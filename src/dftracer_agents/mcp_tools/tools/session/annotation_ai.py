"""AI/ML-aware dftracer source annotation and file-discovery tools.

Registers four MCP tools:

* ``find_source_files``      — recursively list C / C++ / Python source files
                               in any folder on disk (no session required)
* ``python_annotate_ai_file``— annotate a Python file with AI/ML-region-aware
                               dftracer decorators (``dft_ai.pipeline.train`` etc.)
                               and optionally wrap epoch / fetch for-loops with
                               ``dft_ai.pipeline.epoch.iter()`` /
                               ``dft_ai.dataloader.fetch.iter()``
* ``python_write_ai_file``   — flush the in-memory AI-annotated buffer to disk

AI/ML dftracer annotation API
-------------------------------
The annotation mirrors the pattern used by dlio_benchmark (master branch):

  from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai

  _dlp     = DFTracerFn("<category>")       # per-module generic logger
  _dft_log = dftracer.initialize_log(...)   # entry-point files only

  # Function-level decorators (AI/ML regions)
  @dft_ai                            # top-level run() / main() / __call__()
  @dft_ai.pipeline.train             # training loop functions
  @dft_ai.pipeline.evaluate          # evaluation / validation loop functions
  @dft_ai.pipeline.test              # test loop functions
  @dft_ai.compute                    # generic compute step (model(...))
  @dft_ai.compute.forward            # forward pass
  @dft_ai.compute.backward           # backward / gradient step
  @dft_ai.compute.step               # optimizer step
  @dft_ai.data.preprocess            # preprocessing / transform / augment
  @dft_ai.data.item                  # per-item read (__getitem__, read_index)
  @dft_ai.dataloader.fetch           # batch-level data fetching / next()
  @dft_ai.checkpoint.capture         # checkpoint save
  @dft_ai.checkpoint.restart         # checkpoint load / restore
  @dft_ai.device.transfer            # host↔device transfer
  @_dlp.log_init                     # __init__ methods
  # @staticmethod -> contextual region, never @log_static:
  #     with DFTracerFn("<cat>", name="f"): ...
  @_dlp.log                          # everything else

  # Loop-level iterator wrappers
  for epoch in dft_ai.pipeline.epoch.iter(range(1, epochs+1)):
  for batch in dft_ai.dataloader.fetch.iter(dataloader):

Line-shift safety
-----------------
All decorator insertions are computed from the original AST parse, sorted
highest-line-first, and applied in a single pass before writing, so early
insertions never shift indices used for later ones.

Loop wrapping is done in-place (same line substitution), so it does not shift
any line numbers and can be applied before or after decorator insertion.
"""
from __future__ import annotations

import ast
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP

from .workspace import _ws, _ok, _err
from .annotation_python import (
    _extract_functions_from_ast,
    _last_import_idx,
    _find_return_lines,
    _indent_of,
)

# ── Language extension map ─────────────────────────────────────────────────────
_LANG_EXTS: Dict[str, List[str]] = {
    "c":      [".c"],
    "cpp":    [".cpp", ".cxx", ".cc", ".C"],
    "c++":    [".cpp", ".cxx", ".cc", ".C"],
    "python": [".py"],
    "py":     [".py"],
    "all":    [".c", ".cpp", ".cxx", ".cc", ".C", ".py"],
}

# ── AI/ML decorator patterns — ordered, first match wins ──────────────────────
# Pattern matched against fn["name"].lower().
_AI_REGION_PATTERNS: List[Tuple[str, str, str]] = [
    # (name_regex,                                    dft_ai decorator,               label)
    (r'^run$|^main$|^__call__$',                     '@dft_ai',                       'runner'),
    (r'\btrain\b|\btraining\b|^fit$|^_fit$',         '@dft_ai.pipeline.train',        'train'),
    (r'\beval\b|\bevaluat|\bvalidat',                '@dft_ai.pipeline.evaluate',     'evaluate'),
    (r'\btest\b|\btesting\b',                        '@dft_ai.pipeline.test',         'test'),
    (r'^forward$',                                   '@dft_ai.compute.forward',       'forward'),
    (r'^backward$',                                  '@dft_ai.compute.backward',      'backward'),
    (r'^optimizer_step$|^optim_step$',               '@dft_ai.compute.step',          'optim-step'),
    (r'\bcompute\b|\btrain_step\b|\beval_step\b',    '@dft_ai.compute',               'compute'),
    (r'\bpreprocess\b|\btransform\b|\baugment\b|\bcollate\b', '@dft_ai.data.preprocess', 'preprocess'),
    (r'^__getitem__$|read_index|\bload_item\b|\bget_item\b',  '@dft_ai.data.item',    'item'),
    (r'\bfetch\b|\bload_batch\b|\bnext\b',           '@dft_ai.dataloader.fetch',      'fetch'),
    (r'save_checkpoint|save_ckpt|write_ckpt',        '@dft_ai.checkpoint.capture',    'ckpt-save'),
    (r'load_checkpoint|load_ckpt|restore_ckpt',      '@dft_ai.checkpoint.restart',    'ckpt-load'),
    (r'to_device|device_transfer|\btransfer\b',      '@dft_ai.device.transfer',       'device'),
]

# ── Loop iterator wrapping patterns ───────────────────────────────────────────
_EPOCH_VAR_RE = re.compile(r'\bepoch\b', re.IGNORECASE)
_FETCH_VAR_RE = re.compile(r'\b(batch|sample|item|data|loader_item|minibatch)\b', re.IGNORECASE)

# ── Module-level in-memory file cache ─────────────────────────────────────────
_AI_FILE_CACHE: Dict[tuple, List[str]] = {}

_AI_IMPORT = "from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai"
_DFT_FINI  = "_dft_log.finalize()"

#: Canonical path to the annotation lessons LOG file — relative to the workspaces
#: root. This constant is used by session_ml_append_lesson so every tool call lands
#: in the same file regardless of the active session. Folded into the single
#: dftracer-annotation-lessons skill (shared by C/C++/Python and ML annotation
#: sessions alike) rather than a separate, never-packaged dftracer-ml-annotation-lessons
#: skill. The accumulating log lives in a sibling file, LESSONS_LOG.md, kept separate
#: from SKILL.md so that file stays compact regardless of how many sessions have
#: contributed entries — the anchor text below must match the one actually present
#: in .agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md exactly (literal
#: string search). session_lessons_sync_pr (lessons_sync.py) imports these same
#: constants, so this is the only place the path/anchor need to change.
_LESSONS_REL = Path(".agents") / "skills" / "dftracer-annotation-lessons" / "LESSONS_LOG.md"
_LESSONS_ANCHOR = "<!-- New entries are appended below this line by the pipeline recipe -->"

#: Canonical directory containing local research papers relevant to ML/DL
#: I/O optimization. Path is resolved relative to the project root (one level
#: above the workspaces root) at call time. Same hidden cache directory
#: ``local_library_service.py``'s ``save_paper``/``rag_search`` use.
_LOCAL_PAPERS_SUBDIR = ".dftracer_agents/resources/papers"

#: Metadata for the local research papers — title, filename, key concepts,
#: and which bottleneck categories they address.  Used by
#: ``session_search_local_papers`` to return targeted results without
#: requiring PDF parsing at query time.
_LOCAL_PAPER_INDEX: List[Dict[str, Any]] = [
    {
        "filename": "HPDC26_GLANCED_IO.pdf",
        "title": "GLANCED-IO: Taming I/O Optimization for Deep Learning at Scale",
        "venue": "HPDC '26",
        "authors": "Sinurat et al. (Argonne, U.Chicago, LLNL)",
        "year": 2026,
        "bottlenecks": ["data_loading", "prefetch", "num_workers", "transfer_size",
                        "pfs_striping", "dataset_access_pattern", "cross_layer",
                        "compute_io_overlap", "pipeline_stall"],
        "frameworks": ["pytorch", "tensorflow", "dlio"],
        "key_concepts": [
            "Cross-layer I/O optimization (app + system layers jointly)",
            "Performance Evaluator: multi-objective score = C*throughput + (1-C)*bandwidth",
            "Proxy Generator: GPU-free DLIO proxy mirroring original app I/O behavior",
            "Subset Selector: halving strategy for representative data subset",
            "Guided Optimizer: OFAT greedy — tunes one parameter at a time",
            "Compute/IO overlap is essential: dataloader must prefetch next batch while GPU runs",
            "No overlap (sequential load→compute→load) = pipeline stall = biggest fixable bottleneck",
            "Overlap preserved by: num_workers>0, prefetch_factor>1, persistent_workers=True",
            "Proxy Generator validates overlap is preserved: 3 fidelity criteria including overlap",
            "Blocking synchronization (MPI.Barrier) after each step kills overlap in distributed DL",
            "Parameter ordering (portable→hard): num_workers → prefetch_factor → "
            "dataset_access_pattern → PFS_striping → transfer_size",
            "Siloed optimization misses 2.4x throughput or degrades bandwidth 3.1x",
            "93% fidelity preserved when optimizing proxies vs full pipeline",
            "1.57x better performance than state-of-art with 2.3x fewer config evaluations",
        ],
        "optimization_rules": [
            "L1-ML-1: Tune num_workers first — most portable, highest impact",
            "OVERLAP-1: Verify dataloader and compute events OVERLAP in dftracer timeline — "
            "sequential load→compute→load means num_workers=0 or prefetch_factor=0",
            "OVERLAP-2: Fix: set num_workers≥4, prefetch_factor≥2, persistent_workers=True; "
            "use non_blocking=True for tensor.to(device) to allow async H2D transfer",
            "OVERLAP-3: In distributed DL, check for MPI.Barrier after each step — "
            "it serializes all ranks and kills dataloader overlap",
            "OVERLAP-4: DLIO benchmark is the canonical correct-overlap reference — "
            "its DataLoader config always preserves compute/IO overlap",
            "L1-ML-1: Tune num_workers first — most portable, highest impact",
            "L1-ML-2: Tune prefetch_factor second — controls I/O concurrency vs CPU contention",
            "L1-ML-3: Check dataset_access_pattern (sequential vs random) — affects PFS efficiency",
            "L2-ML-1: Disable PyTorch default collation when each sample is already a complete batch",
            "L3-ML-1: Tune PFS striping (stripe_count, stripe_size) — system-level, less portable",
            "L3-ML-2: Tune transfer_size to align I/O operations with storage hardware stripe width",
            "CROSS: Always optimize throughput AND I/O bandwidth together — siloed tuning leaves "
            "up to 2.4x performance on the table",
        ],
        "abstract_excerpt": (
            "GLANCED-IO is a cross-layer I/O optimization framework that optimizes DL pipelines "
            "with high-fidelity approximation and efficient configuration space exploration. "
            "Independently optimizing either application or system configurations leaves up to 2.4x "
            "performance on the table. GLANCED-IO's OFAT-guided greedy exploration achieves results "
            "comparable to more-expensive autotuning while removing pre-training requirements."
        ),
    },
    {
        "filename": "Cladia_IPDPS_26-2.pdf",
        "title": "Cladia: Cross-Layer Diagnosis of Deep Learning I/O Bottlenecks with Uncertainty-Aware AI",
        "venue": "IPDPS '26",
        "authors": "Anonymous (submitted)",
        "year": 2026,
        "bottlenecks": ["cross_layer", "python_overhead", "framework_overhead",
                        "checkpoint", "data_format", "root_cause"],
        "frameworks": ["pytorch", "tensorflow"],
        "key_concepts": [
            "Hierarchical I/O dependency graph from raw DFTracer traces",
            "Uncertainty-aware diagnostic model using quantile regression (98% accuracy)",
            "Layer-aware explainability with SHAP values — traces bottleneck to root-cause layer",
            "Cross-layer overhead metric: higher POSIX BW can be negated by Python API overhead",
            "NPZ vs HDF5: similar GPU compute times mask 5x Python API overhead difference",
            "Single-layer diagnosis misdiagnoses bottlenecks — fixes improve speedup 30% less",
            "2.91x reduction in framework-level checkpointing bottleneck via targeted fix",
            "3.4x improvement over mean-based diagnostic models via uncertainty quantification",
        ],
        "optimization_rules": [
            "DIAG-1: Never diagnose from a single layer — always use cross-layer view",
            "DIAG-2: High POSIX bandwidth does not mean no bottleneck — check Python API overhead",
            "DIAG-3: Similar compute times in two configs does not mean equivalent I/O — "
            "check Framework Read I/O Time and Python Read I/O Time separately",
            "DIAG-4: Use quantile regression not mean — performance variability is the signal",
            "DIAG-5: Trace bottleneck backward through layers with SHAP to find root-cause layer",
            "DIAG-6: Checkpoint bottlenecks (2.91x) are a high-priority fix target in DL workloads",
        ],
        "abstract_excerpt": (
            "Cladia is an explainable, AI-powered diagnostic framework for cross-layer diagnosis "
            "of DL I/O bottlenecks. It transforms raw trace data into a hierarchical I/O dependency "
            "graph, uses quantile regression to capture performance variability (98% accuracy, "
            "3.4x improvement over mean-based), and provides layer-aware explainability via SHAP "
            "values to trace bottlenecks to root-cause layers."
        ),
    },
    {
        "filename": "LiveFlow_SC_26__Copy_.pdf",
        "title": "LiveFlow: Online Data Movement Optimization for Distributed Deep Learning on HPC Systems",
        "venue": "SC '26",
        "authors": "Yildirim (Illinois Institute of Technology)",
        "year": 2026,
        "bottlenecks": ["distributed", "synchronization", "data_loading", "critical_path",
                        "input_pipeline", "communication"],
        "frameworks": ["pytorch", "ddp", "horovod"],
        "key_concepts": [
            "Online controller — optimizes during training, not post-hoc",
            "Cross-layer visibility: correlates training-step and framework-level events",
            "Bounded decision windows: avoids 9% slowdown from over-frequent changes",
            "Hierarchical coordination: job-level when a node falls behind",
            "Distinguishes exposure from cause: busy I/O can be hidden behind compute",
            "Input path: worker parallelism, prefetch depth; Sync path: DDP bucketization",
            "12x fewer control actions than single-domain controller",
            "4.6x end-to-end speedup on diverse workloads",
            "HPC constraint: optimization must happen within a job, not across jobs",
        ],
        "optimization_rules": [
            "LIVE-1: Check if I/O is on the critical path before tuning — busy I/O hidden behind "
            "compute is not a bottleneck worth fixing",
            "LIVE-2: For distributed training, optimize communication (DDP bucketization) before "
            "input pipeline when sync path dominates the critical path",
            "LIVE-3: Use bounded windows (multiple steps) before acting on a knob change — "
            "immediate post-change behavior is misleading due to caching and warmup",
            "LIVE-4: Job-level coordination is needed when load imbalance is the bottleneck — "
            "per-node tuning alone cannot fix a job-wide stall",
            "LIVE-5: Input path optimization: num_workers and prefetch depth are the primary levers",
        ],
        "abstract_excerpt": (
            "LiveFlow is an online data-movement optimizer for distributed deep learning on HPC "
            "that combines streaming cross-layer analysis, bounded decision windows, and hierarchical "
            "coordination. It achieves up to 4.6x end-to-end speedup with 12x fewer control actions "
            "than a controller limited to one domain, and adds less than 4% time and 3% memory overhead."
        ),
    },
    {
        "filename": "SSDBM26_HORATIO-2.pdf",
        "title": "HORATIO: Bridging Management and Analysis of Traces at Scale",
        "venue": "SSDBM '26",
        "authors": "Sinurat, Nixon, Gunawi, Dryden, Devarajan (U.Chicago, LLNL)",
        "year": 2026,
        "bottlenecks": ["trace_management", "trace_analysis", "scale", "query_performance"],
        "frameworks": ["dftracer"],
        "key_concepts": [
            "Raw trace management: index + analyze + cluster without format conversion",
            "Indexer: RocksDB-backed index + per-chunk bloom filters (75x faster selective queries)",
            "Analyzer: native C++ pipeline with zero-copy Python exchange (80-83x over Dask)",
            "Assembler: lossless trace clustering, 1.8-5.5x speedup, preserves raw format",
            "Executor: coroutine-backed parallel execution across fragmented trace files",
            "232 M events/s cross-query mean throughput — highest among state-of-the-art",
            "Scales to 16x32 nodes on 2.2 TB uncompressed traces",
            "MPI-based mode: 230x speedup on h5bench preset with cluster boundary alignment",
            "Avoids dual retention (raw + Parquet) — 1.01x raw storage overhead",
        ],
        "optimization_rules": [
            "TRACE-1: Use HORATIO for trace pipelines > 10 GB — DFAnalyzer OOMs at terabyte scale",
            "TRACE-2: Enable per-chunk bloom filters for selective event queries on large traces",
            "TRACE-3: Lossless clustering (Assembler) before analysis reduces query fan-out 1.8-5.5x",
            "TRACE-4: For MPI workloads, align trace clustering to cluster boundary for 230x speedup",
        ],
        "abstract_excerpt": (
            "HORATIO is a raw trace management framework that indexes, analyzes, and physically "
            "clusters raw DFTracer traces. It delivers selective queries up to 75x faster than "
            "naive Parquet at ~1.01x raw storage, with 80-83x end-to-end pipeline speedup over "
            "the original Dask-based pipeline."
        ),
    },
]


def _detect_ai_decorator(fn: dict) -> str:
    """Return the best @dft_ai.* decorator string, or '' to use @_dlp.log."""
    name = fn["name"].lower()
    for pattern, decorator, _ in _AI_REGION_PATTERNS:
        if re.search(pattern, name):
            return decorator
    return ""


def _wrap_for_loops(source: str, lines: List[str]) -> Tuple[List[str], List[dict]]:
    """Wrap epoch / fetch for-loop iterators with dft_ai context managers.

    Uses ``ast.get_source_segment`` to extract the exact iterator expression and
    replaces it in-place on the same source line.  No line indices shift.

    Returns (modified_lines, list_of_wrap_records).
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return lines, []

    replacements: List[Tuple[int, str, str, str]] = []  # (line_idx, old, new, kind)

    for node in ast.walk(tree):
        if not isinstance(node, ast.For):
            continue

        target = node.target
        if isinstance(target, ast.Name):
            var_name = target.id
        elif isinstance(target, ast.Tuple):
            var_name = " ".join(
                e.id for e in target.elts if isinstance(e, ast.Name)
            )
        else:
            continue

        iter_src = ast.get_source_segment(source, node.iter)
        if not iter_src:
            continue

        line_idx = node.lineno - 1  # 0-based

        if _EPOCH_VAR_RE.search(var_name):
            wrapped = f"dft_ai.pipeline.epoch.iter({iter_src})"
            replacements.append((line_idx, iter_src, wrapped, "epoch"))
        elif _FETCH_VAR_RE.search(var_name):
            wrapped = f"dft_ai.dataloader.fetch.iter({iter_src})"
            replacements.append((line_idx, iter_src, wrapped, "fetch"))

    result = list(lines)
    wraps: List[dict] = []
    seen_lines: set = set()

    for line_idx, old_text, new_text, kind in replacements:
        if line_idx in seen_lines:
            continue
        line = result[line_idx]
        # Skip if already wrapped (idempotency guard)
        if "dft_ai" in line and ".iter(" in line:
            continue
        if old_text in line:
            result[line_idx] = line.replace(old_text, new_text, 1)
            seen_lines.add(line_idx)
            wraps.append({"line": line_idx + 1, "kind": kind,
                          "original": old_text, "wrapped": new_text})

    return result, wraps



# ---------------------------------------------------------------------------
# Module-level implementation, so orchestrators (ml_pipeline) can drive the
# same annotation logic without going through the MCP tool wrapper.
# ---------------------------------------------------------------------------


def _ai_context_expr(ai_dec: str, cat: str, name: str) -> str:
    """Return the `with` context expression for a static method.

    A @staticmethod cannot take the AI decorator (it fights @staticmethod
    ordering), but the AI API objects are context managers too. So a semantic
    static method keeps its AI semantics as a region — ``@dft_ai.compute.backward``
    becomes ``with dft_ai.compute.backward():`` — instead of degrading to a plain
    DFTracerFn region, which would erase the AI category from the trace.
    """
    if ai_dec:
        expr = ai_dec.lstrip("@")
        if expr == "dft_ai":                    # bare object decorator
            return f'DFTracerFn("{cat}", name="{name}")'
        return f"{expr}()"
    return f'DFTracerFn("{cat}", name="{name}")'


def _ai_summary(filepath, insertions, ai_count, wrap_count, fn_count,
                regions, skipped_static, skipped_cheap=()) -> str:
    """Human-readable summary naming EVERY instrumentation form applied.

    Callers (and the validator) need to distinguish decorator-annotated functions
    from contextual ``with``-region ones, and to see any static method that was
    deliberately left alone — otherwise a silent skip looks like full coverage.
    """
    parts = [
        f"AI-annotated {filepath}: {len(insertions)} decorator insertion(s)",
        f"{ai_count} AI/ML region(s)",
        f"{len(regions)} contextual `with` region(s) for @staticmethod",
        f"{wrap_count} loop wrap(s)",
        f"{fn_count} function(s) total",
    ]
    if skipped_cheap:
        parts.append(f"{len(skipped_cheap)} skipped by cost gate")
    msg = ", ".join(parts) + "."
    if skipped_static:
        msg += (f" SKIPPED {len(skipped_static)} static method(s) whose body spans a "
                f"multi-line string — annotate by hand: {', '.join(skipped_static)}.")
    return msg


def _python_annotate_ai_file_impl(
    run_id: str,
    filepath: str,
    category: str = "",
    is_entry: bool = False,
    logfile: str = "None",
    data_dir: str = "None",
    process_id: int = -1,
    annotate_loops: bool = True,
    annotate_nested: bool = True,
    annotated_dir: str = "annotated",
    only_functions: str = "",
) -> str:
    """Annotate a Python file with AI/ML-region-aware dftracer decorators.

    Uses the full ``dft_ai`` API from ``dftracer.python`` to insert
    semantically-correct region decorators based on function name patterns.
    Matches the annotation style used in dlio_benchmark (master branch).

    **Decorator selection rules** (first match wins):

    +----------------------------------------------+-------------------------------+
    | Function name matches (case-insensitive)     | Decorator inserted            |
    +==============================================+===============================+
    | ``run``, ``main``, ``__call__``              | ``@dft_ai``                   |
    +----------------------------------------------+-------------------------------+
    | ``train``, ``training``, ``fit``             | ``@dft_ai.pipeline.train``    |
    +----------------------------------------------+-------------------------------+
    | ``eval``, ``evaluate``, ``validate``         | ``@dft_ai.pipeline.evaluate`` |
    +----------------------------------------------+-------------------------------+
    | ``test``, ``testing``                        | ``@dft_ai.pipeline.test``     |
    +----------------------------------------------+-------------------------------+
    | ``forward``                                  | ``@dft_ai.compute.forward``   |
    +----------------------------------------------+-------------------------------+
    | ``backward``                                 | ``@dft_ai.compute.backward``  |
    +----------------------------------------------+-------------------------------+
    | ``optimizer_step``, ``optim_step``           | ``@dft_ai.compute.step``      |
    +----------------------------------------------+-------------------------------+
    | ``compute``, ``train_step``, ``eval_step``   | ``@dft_ai.compute``           |
    +----------------------------------------------+-------------------------------+
    | ``preprocess``, ``transform``, ``augment``,  | ``@dft_ai.data.preprocess``   |
    | ``collate``                                  |                               |
    +----------------------------------------------+-------------------------------+
    | ``__getitem__``, ``read_index``, ``get_item``| ``@dft_ai.data.item``         |
    +----------------------------------------------+-------------------------------+
    | ``fetch``, ``load_batch``, ``next``          | ``@dft_ai.dataloader.fetch``  |
    +----------------------------------------------+-------------------------------+
    | ``save_checkpoint``, ``save_ckpt``           | ``@dft_ai.checkpoint.capture``|
    +----------------------------------------------+-------------------------------+
    | ``load_checkpoint``, ``load_ckpt``, etc.     | ``@dft_ai.checkpoint.restart``|
    +----------------------------------------------+-------------------------------+
    | ``to_device``, ``transfer``                  | ``@dft_ai.device.transfer``   |
    +----------------------------------------------+-------------------------------+
    | ``__init__``                                 | ``@_dlp.log_init``            |
    +----------------------------------------------+-------------------------------+
    | ``@staticmethod`` methods                    | ``with DFTracerFn(...)`` region |
    +----------------------------------------------+-------------------------------+
    | *(everything else)*                          | ``@_dlp.log``                 |
    +----------------------------------------------+-------------------------------+

    **Loop iterator wrapping** (when ``annotate_loops=True``):

    * ``for epoch in <X>:``  →
      ``for epoch in dft_ai.pipeline.epoch.iter(<X>):``
    * ``for batch/sample/item in <X>:``  →
      ``for batch in dft_ai.dataloader.fetch.iter(<X>):``

    The injected import block is::

        from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai
        _dlp     = DFTracerFn("<category>")
        _dft_log = dftracer.initialize_log(...)   # entry-point files only

    All operations use the bottom-to-top insertion strategy: indices are
    computed from the original file, sorted highest-first, and applied in
    one pass before writing.  Loop wrapping is done as in-place line
    substitution (no index shift).

    Operation is idempotent: if the dftracer import is already present the
    file is returned unchanged.

    Args:
        run_id:          Session identifier from ``session_create``.
        filepath:        Path relative to the ``annotated/`` subfolder.
        category:        Category string for ``DFTracerFn("<category>")``.
                         Defaults to the filename stem.
        is_entry:        ``True`` for the program entry point (adds
                         ``initialize_log`` and ``finalize()``).
        logfile:         ``logfile`` arg for ``initialize_log``.
        data_dir:        ``data_dir`` arg for ``initialize_log``.
        process_id:      ``process_id`` arg for ``initialize_log``.
        annotate_loops:  When ``True`` (default), wrap epoch and fetch
                         for-loop iterators with ``dft_ai.*.iter()``.
        annotate_nested: When ``True`` (default), annotate nested functions
                         and class methods.

    Returns:
        JSON with ``status``, ``message``, ``filepath``, ``insertions``,
        ``functions``, ``ai_functions``, ``loop_wraps``, ``total_lines``,
        ``already_annotated``.
    """
    ws = _ws(run_id)
    abs_path = ws / annotated_dir / filepath

    cache_key = (run_id, filepath)
    if cache_key in _AI_FILE_CACHE:
        lines = list(_AI_FILE_CACHE[cache_key])
    elif abs_path.exists():
        lines = abs_path.read_text(errors="replace").splitlines()
    else:
        return _err(f"File not found in annotated/: {filepath}")

    text = "\n".join(lines)

    # Idempotency guard
    if "from dftracer.python import" in text and "dft_ai" in text:
        return _ok(
            f"{filepath} is already AI-annotated — skipped.",
            filepath=filepath,
            insertions=0,
            functions=0,
            ai_functions=0,
            loop_wraps=0,
            total_lines=len(lines),
            already_annotated=True,
        )

    cat = category or Path(filepath).stem

    # ── Step 1: parse function map ─────────────────────────────────────
    all_fns = _extract_functions_from_ast(text)
    if not annotate_nested:
        all_fns = [f for f in all_fns if "." not in f["qualname"]]

    # ── Step 2: build import/init block ───────────────────────────────
    import_idx = _last_import_idx(lines)

    init_lines: List[str] = [
        _AI_IMPORT,
        f'_dlp = DFTracerFn("{cat}")',
    ]
    if is_entry:
        init_lines.append(
            f"_dft_log = dftracer.initialize_log("
            f"logfile={logfile}, data_dir={data_dir}, process_id={process_id})"
        )

    # ── Step 3: build per-function decorator insertions ───────────────
    insertions: List[tuple] = []

    # Insertions are (index, priority, text). At the SAME index, lower priority is
    # applied first; since each insert pushes previously-inserted text down, the
    # LAST thing applied ends up topmost. So decorators (prio 0) must be applied
    # before the init block (prio 1), otherwise a decorator for a function that
    # starts right after the imports is emitted ABOVE the import it depends on —
    # producing `@dft_ai` before `from dftracer... import ... as dft_ai`, which is
    # a SyntaxError.
    _PRIO_DEC, _PRIO_INIT = 0, 1

    # Init block — reversed so after sort+insert they land in order
    for ln in reversed(init_lines):
        insertions.append((import_idx, _PRIO_INIT, ln))

    ai_count = 0
    decorator_map: List[dict] = []

    # Static methods get a contextual `with` region instead of @log_static: a
    # decorator has to fight @staticmethod ordering, whereas dft_fn is a context
    # manager (__enter__/__exit__). Collected here, applied after the insertions.
    from .annotation_python import _multiline_string_rows
    regions: List[dict] = []
    skipped_static: List[str] = []
    multiline_str_rows = _multiline_string_rows(text)

    # Cost gate. `only_functions` is the allow-list from the AI/ML cost estimator.
    # Semantic functions (those _detect_ai_decorator recognises: data item,
    # checkpoint, training step, comm, ...) are ALWAYS annotated regardless — they
    # are instrumented for what they mean, not what they cost. Everything else is
    # annotated only when the estimator selected it, so a file does not end up with
    # a decorator on every getter. An empty allow-list keeps the old behaviour.
    _wanted = {n.strip() for n in only_functions.split(",") if n.strip()}
    _names = [f["name"] for f in all_fns]

    def _selected(fn: dict) -> bool:
        if not _wanted:
            return True
        if fn["qualname"] in _wanted:
            return True
        # A bare name is honoured only when unambiguous in this file.
        return fn["name"] in _wanted and _names.count(fn["name"]) == 1

    skipped_cheap: List[str] = []

    for fn in all_fns:
        dec_idx = fn["decorator_insert_line"] - 1
        indent  = " " * fn["col_offset"]

        ai_dec_pre = _detect_ai_decorator(fn)
        is_semantic = bool(ai_dec_pre) or fn["is_entry_point"]

        if not is_semantic and not _selected(fn):
            skipped_cheap.append(fn["qualname"])
            continue

        # Determine decorator
        if fn["is_init"]:
            dec = f"{indent}@_dlp.log_init"
            label = "log_init"
        elif fn["has_staticmethod"]:
            # Re-indenting a body that spans a multi-line string would rewrite the
            # literal's contents, so leave those alone and report them instead.
            if multiline_str_rows.intersection(
                    range(fn["body_first_line"], fn["end_line"] + 1)):
                skipped_static.append(fn["qualname"])
            else:
                fn = dict(fn)
                fn["_ctx"] = _ai_context_expr(ai_dec_pre, cat, fn["name"])
                if ai_dec_pre:
                    ai_count += 1
                regions.append(fn)
            continue
        else:
            ai_dec = ai_dec_pre
            if ai_dec:
                dec = f"{indent}{ai_dec}"
                label = ai_dec
                ai_count += 1
            else:
                dec = f"{indent}@_dlp.log"
                label = "log"

        insertions.append((dec_idx, _PRIO_DEC, dec))
        decorator_map.append({
            "function": fn["qualname"],
            "decorator": label,
            "line": fn["decorator_insert_line"],
        })

    # Entry-point finalize
    if is_entry:
        main_fn = next((f for f in all_fns if f["is_entry_point"]), None)
        if main_fn:
            body_ind  = " " * main_fn.get("body_col_offset", 4)
            fini_line = f"{body_ind}{_DFT_FINI}"
            returns   = main_fn.get("return_lines", [])
            if returns:
                for ret_line in returns:
                    ind = _indent_of(lines, ret_line)
                    insertions.append((ret_line - 1, _PRIO_DEC, f"{ind}{_DFT_FINI}"))
            else:
                # end_line is the 1-based LAST line of main(); its 0-based index is
                # end_line-1. Insert AFTER it (index end_line), otherwise finalize()
                # lands before the final statement — and for a one-statement main()
                # it becomes the FIRST statement, finalizing the log before any work.
                insertions.append((main_fn["end_line"], _PRIO_DEC, fini_line))
        else:
            insertions.append((len(lines), _PRIO_DEC, _DFT_FINI))

    # ── Step 4: sort highest-first, apply in one pass ─────────────────
    insertions.sort(key=lambda x: (-x[0], x[1]))
    for idx, _prio, txt in insertions:
        lines.insert(idx, txt)

    # Contextual `with` regions for @staticmethod, computed from a FRESH parse so
    # the line numbers reflect the decorator insertions above. Bottom-up so
    # earlier functions keep their positions. The `with` goes AFTER a leading
    # docstring, otherwise the docstring becomes a dead expression inside it.
    if regions:
        want = {f["qualname"] for f in regions}
        ctx_by_qual = {f["qualname"]: f["_ctx"] for f in regions}
        try:
            fresh = _extract_functions_from_ast("\n".join(lines))
        except Exception:
            fresh = []
        for fn in sorted((f for f in fresh if f["qualname"] in want),
                         key=lambda f: -f["body_first_line"]):
            b0 = fn["body_first_line"] - 1
            e0 = fn["end_line"] - 1
            body_ind = " " * fn["body_col_offset"]
            doc = lines[b0].strip()
            if len(doc) > 5 and (
                (doc.startswith('"""') and doc.endswith('"""'))
                or (doc.startswith("'''") and doc.endswith("'''"))
            ):
                b0 += 1
            for i in range(b0, min(e0, len(lines) - 1) + 1):
                if lines[i].strip():
                    lines[i] = "    " + lines[i]
            ctx = ctx_by_qual.get(fn["qualname"],
                                  f'DFTracerFn("{cat}", name="{fn["name"]}")')
            lines.insert(b0, f"{body_ind}with {ctx}:")
            decorator_map.append({"function": fn["qualname"],
                                  "decorator": f"with {ctx}",
                                  "line": fn["body_first_line"]})

    # ── Step 5: wrap for-loops (in-place substitution, no index shift) ─
    wrap_count = 0
    wrap_records: List[dict] = []
    if annotate_loops:
        new_text = "\n".join(lines)
        lines, wrap_records = _wrap_for_loops(new_text, lines)
        wrap_count = len(wrap_records)

    # ── Step 6: write once ────────────────────────────────────────────
    _AI_FILE_CACHE[cache_key] = list(lines)
    abs_path.write_text("\n".join(lines) + "\n")

    fn_count = len(all_fns)
    total_insertions = len(insertions) + wrap_count
    return _ok(
        _ai_summary(filepath, insertions, ai_count, wrap_count, fn_count,
                    regions, skipped_static, skipped_cheap),
        filepath=filepath,
        insertions=len(insertions),
        functions=fn_count,
        ai_functions=ai_count,
        # Every instrumentation form this file produced, so callers can audit
        # coverage without re-reading the source:
        decorators=len(decorator_map) - len(regions),
        with_regions=len(regions),
        with_region_functions=[f["qualname"] for f in regions],
        skipped_static=skipped_static,
        skipped_cheap=skipped_cheap,
        loop_wraps=wrap_count,
        loop_wrap_details=wrap_records,
        decorator_map=decorator_map,
        total_lines=len(lines),
        already_annotated=False,
    )


def register_ai_tools(mcp: FastMCP) -> None:
    """Register AI/ML annotation and file-discovery tools on *mcp*."""

    @mcp.tool()
    def find_source_files(
        folder: str,
        language: str,
        run_id: str = "",
        recursive: bool = True,
        exclude_patterns: List[str] = None,
    ) -> str:
        """Recursively list source files of the given language in a folder.

        Works without a session (pass an absolute *folder* path) or within a
        session workspace (pass *run_id* and a path relative to the session root).

        Supported languages / extensions:

        +----------+----------------------------------+
        | language | extensions                       |
        +==========+==================================+
        | c        | .c                               |
        +----------+----------------------------------+
        | cpp      | .cpp .cxx .cc .C                 |
        +----------+----------------------------------+
        | python   | .py                              |
        +----------+----------------------------------+
        | all      | all of the above                 |
        +----------+----------------------------------+

        Args:
            folder:           Absolute path to the directory to search, OR a path
                              relative to the session workspace root when *run_id*
                              is provided.
            language:         One of ``"c"``, ``"cpp"``, ``"c++"``, ``"python"``,
                              ``"py"``, ``"all"`` (case-insensitive).
            run_id:           Optional session identifier (from ``session_create``).
                              When supplied, *folder* is resolved relative to the
                              session workspace directory.
            recursive:        If ``True`` (default) search sub-directories.
            exclude_patterns: Optional list of glob patterns to exclude
                              (e.g. ``["**/test*", "**/__pycache__/**"]``).

        Returns:
            JSON with ``status``, ``message``, ``files`` (list of dicts with
            ``path``, ``size_bytes``, ``language``), ``count``, ``language``,
            ``folder``.
        """
        lang_key = language.strip().lower()
        if lang_key not in _LANG_EXTS:
            return _err(
                f"Unknown language '{language}'. "
                f"Supported: {sorted(_LANG_EXTS)}",
                supported=sorted(_LANG_EXTS),
            )
        exts = set(_LANG_EXTS[lang_key])

        # Resolve the folder
        if run_id:
            ws = _ws(run_id)
            base = ws / folder
        else:
            base = Path(folder)

        if not base.exists():
            return _err(f"Folder does not exist: {base}", folder=str(base))
        if not base.is_dir():
            return _err(f"Path is not a directory: {base}", folder=str(base))

        exclude = exclude_patterns or []

        # Walk
        glob_fn = base.rglob if recursive else base.glob
        found: List[dict] = []
        for p in sorted(glob_fn("*")):
            if not p.is_file():
                continue
            if p.suffix not in exts:
                continue
            # Apply exclusion patterns
            rel = p.relative_to(base)
            if any(rel.match(pat) for pat in exclude):
                continue
            # Determine language label
            if p.suffix == ".py":
                file_lang = "python"
            elif p.suffix == ".c":
                file_lang = "c"
            else:
                file_lang = "cpp"
            found.append({
                "path": str(p),
                "relative_path": str(rel),
                "size_bytes": p.stat().st_size,
                "language": file_lang,
            })

        return _ok(
            f"Found {len(found)} {language} file(s) in {base}.",
            files=found,
            count=len(found),
            language=language,
            folder=str(base),
        )

    @mcp.tool()
    def python_annotate_ai_file(
        run_id: str,
        filepath: str,
        category: str = "",
        is_entry: bool = False,
        logfile: str = "None",
        data_dir: str = "None",
        process_id: int = -1,
        annotate_loops: bool = True,
        annotate_nested: bool = True,
    ) -> str:
        """Annotate a Python file with dftracer AI/ML region decorators.

        Delegates to :func:`_python_annotate_ai_file_impl`. See that function for
        the full behaviour (decorator selection, loop wrapping, import injection,
        idempotency).
        """
        return _python_annotate_ai_file_impl(
            run_id=run_id, filepath=filepath, category=category,
            is_entry=is_entry, logfile=logfile, data_dir=data_dir,
            process_id=process_id, annotate_loops=annotate_loops,
            annotate_nested=annotate_nested,
        )

    @mcp.tool()
    def python_write_ai_file(run_id: str, filepath: str) -> str:
        """Flush the in-memory AI-annotated Python file buffer to disk.

        Call this after one or more in-memory AI annotation operations if you
        want to do the disk write separately.  ``python_annotate_ai_file``
        already writes to disk automatically, so this tool is for workflows
        that update the cache further before committing.

        Args:
            run_id:   Session identifier from ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON with ``status``, ``message``, ``filepath``, ``total_lines``.
        """
        cache_key = (run_id, filepath)
        if cache_key not in _AI_FILE_CACHE:
            return _err(
                f"No in-memory AI state for {filepath} — "
                "call python_annotate_ai_file first.",
                filepath=filepath,
            )
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        lines = _AI_FILE_CACHE[cache_key]
        abs_path.write_text("\n".join(lines) + "\n")
        del _AI_FILE_CACHE[cache_key]
        return _ok(
            f"Wrote {len(lines)} lines to {filepath}.",
            filepath=filepath,
            total_lines=len(lines),
        )

    @mcp.tool()
    def dftracer_get_ai_annotation(
        function_name: str = "",
        code_snippet: str = "",
        context: str = "",
        phase: str = "",
    ) -> str:
        """Look up the correct dftracer AI/ML annotation for a given function or code pattern.

        Uses the official AI/DL logging conventions from pydftracer to map
        function names, code patterns, and AI/ML phases to the correct
        dft_ai.* decorator or context manager.

        AI/DL Logging Conventions (six categories):

        COMPUTE:
          dft_ai.compute.forward    — forward pass (model(x), __call__, predict)
          dft_ai.compute.backward   — backward pass / gradient computation (loss.backward)
          dft_ai.compute.step       — optimizer step (optimizer.step, parameter update)
          dft_ai.compute            — generic compute / train_step / eval_step

        DATA:
          dft_ai.data.item          — per-item read (__getitem__, read_index, load_sample)
          dft_ai.data.preprocess    — dataset-level preprocessing, transform, augment, collate

        DATALOADER:
          dft_ai.dataloader.fetch   — batch fetching, next(iter), __next__, load_batch
          dft_ai.dataloader.fetch.iter(iterable)  — wraps a for-loop over batches

        COMM (distributed):
          dft_ai.comm.all_reduce    — all-reduce gradient synchronization
          dft_ai.comm.barrier       — synchronization barrier
          dft_ai.comm.broadcast     — one-to-many broadcast
          dft_ai.comm.reduce        — many-to-one reduce
          dft_ai.comm.all_gather    — all-gather
          dft_ai.comm.scatter       — scatter
          dft_ai.comm.send          — point-to-point send
          dft_ai.comm.receive       — point-to-point receive

        DEVICE:
          dft_ai.device.transfer    — host-to-device or device-to-host memory transfer
                                      (.cuda(), .to(device), .cpu(), torch.Tensor.to)

        CHECKPOINT:
          dft_ai.checkpoint.capture — model checkpoint save (save_checkpoint, torch.save)
          dft_ai.checkpoint.restart — model checkpoint load (load_checkpoint, torch.load)

        PIPELINE:
          dft_ai.pipeline.train     — training loop function
          dft_ai.pipeline.evaluate  — evaluation / validation loop
          dft_ai.pipeline.test      — test loop
          dft_ai                    — top-level run() / main() / __call__()
          dft_ai.pipeline.epoch.iter(range(epochs))  — wraps epoch for-loop

        GENERIC (for expensive non-AI regions via dft_fn):
          @_dlp.log_init            — __init__ methods
          with DFTracerFn(...)      — @staticmethod methods (contextual region)
          @_dlp.log                 — any other expensive function

        API STYLES:
          Decorator:        @dft_ai.compute.forward
          Context manager:  with dft_ai.compute.forward():
          Iterator wrapper: for batch in dft_ai.dataloader.fetch.iter(loader):
          Start/stop:       dft_ai.compute.step.start() / dft_ai.compute.step.stop()
          Metadata update:  ai.update(step=step, epoch=epoch)

        Args:
            function_name:  Name of the function to annotate (e.g. "forward", "train_epoch").
            code_snippet:   Short code snippet showing the function body or call pattern.
            context:        Free-text description of what this function does in the pipeline
                            (e.g. "reads a batch from disk", "runs optimizer step").
            phase:          Explicit phase hint: "compute", "data", "dataloader", "comm",
                            "device", "checkpoint", "pipeline", or "" for auto-detect.

        Returns:
            JSON with:
                decorator       — the recommended @dft_ai.* decorator string
                iterator_wrap   — recommended .iter() wrapper if the fn is a loop, or ""
                api_style       — "decorator" | "context_manager" | "iterator" | "start_stop"
                category        — category name (compute/data/dataloader/comm/device/checkpoint/pipeline)
                rationale       — why this annotation was chosen
                example         — short code example showing correct usage
                alternatives    — list of alternative annotations that could also apply
        """
        import re as _re

        name_lower = (function_name or "").lower()
        snippet_lower = (code_snippet or "").lower()
        context_lower = (context or "").lower()
        combined = f"{name_lower} {snippet_lower} {context_lower}"
        phase_lower = (phase or "").lower()

        # Build lookup table: (regex_for_combined, decorator, category, rationale, example, api_style)
        _LOOKUP = [
            # ── Checkpoint ──────────────────────────────────────────────────────
            (r'save_checkpoint|save_ckpt|write_ckpt|torch\.save|checkpoint.*save|ckpt.*write',
             "@dft_ai.checkpoint.capture", "checkpoint",
             "Function saves model state to disk (checkpoint capture).",
             "@dft_ai.checkpoint.capture\ndef save_checkpoint(model, path):\n    torch.save(model.state_dict(), path)",
             "decorator"),
            (r'load_checkpoint|load_ckpt|restore_ckpt|torch\.load|checkpoint.*load|ckpt.*read|ckpt.*restore',
             "@dft_ai.checkpoint.restart", "checkpoint",
             "Function loads model state from disk (checkpoint restart).",
             "@dft_ai.checkpoint.restart\ndef load_checkpoint(model, path):\n    model.load_state_dict(torch.load(path))",
             "decorator"),
            # ── Distributed communication ────────────────────────────────────────
            (r'all.?reduc|allreduc',
             "@dft_ai.comm.all_reduce", "comm",
             "All-reduce gradient synchronization across distributed workers.",
             "with dft_ai.comm.all_reduce():\n    dist.all_reduce(tensor)",
             "context_manager"),
            (r'\bbarrier\b',
             "@dft_ai.comm.barrier", "comm",
             "Synchronization barrier across distributed workers.",
             "with dft_ai.comm.barrier():\n    dist.barrier()",
             "context_manager"),
            (r'\ball.?gather\b',
             "@dft_ai.comm.all_gather", "comm",
             "All-gather communication (many-to-many).",
             "with dft_ai.comm.all_gather():\n    dist.all_gather(output_list, tensor)",
             "context_manager"),
            (r'\bscatter\b',
             "@dft_ai.comm.scatter", "comm",
             "Scatter communication (one-to-many).",
             "with dft_ai.comm.scatter():\n    dist.scatter(tensor, scatter_list)",
             "context_manager"),
            (r'\breduce.scatter\b|reduce_scatter',
             "@dft_ai.comm.reduce_scatter", "comm",
             "Reduce-scatter communication (many-to-many).",
             "with dft_ai.comm.reduce_scatter():\n    dist.reduce_scatter(output, input_list)",
             "context_manager"),
            (r'\bbroadcast\b',
             "@dft_ai.comm.broadcast", "comm",
             "Broadcast communication (one-to-many).",
             "with dft_ai.comm.broadcast():\n    dist.broadcast(tensor, src=0)",
             "context_manager"),
            (r'\breduce\b(?!.*scatter)',
             "@dft_ai.comm.reduce", "comm",
             "Reduce communication (many-to-one).",
             "with dft_ai.comm.reduce():\n    dist.reduce(tensor, dst=0)",
             "context_manager"),
            (r'all.?to.?all',
             "@dft_ai.comm.all_to_all", "comm",
             "All-to-all communication (many-to-many).",
             "with dft_ai.comm.all_to_all():\n    dist.all_to_all(output_list, input_list)",
             "context_manager"),
            (r'\bsend\b',
             "@dft_ai.comm.send", "comm",
             "Point-to-point send to another rank.",
             "with dft_ai.comm.send():\n    dist.send(tensor, dst=rank)",
             "context_manager"),
            (r'\breceive\b|\brecv\b',
             "@dft_ai.comm.receive", "comm",
             "Point-to-point receive from another rank.",
             "with dft_ai.comm.receive():\n    dist.recv(tensor, src=rank)",
             "context_manager"),
            # ── Device transfer ──────────────────────────────────────────────────
            (r'to.?device|\.cuda\(\)|\.to\s*\(\s*device|device.*transfer|host.to.device|\.cpu\(\)',
             "@dft_ai.device.transfer", "device",
             "Host-to-device or device-to-host memory transfer.",
             "@dft_ai.device.transfer\ndef transfer(batch, device):\n    return batch.to(device)",
             "decorator"),
            # ── Compute: optimizer step ──────────────────────────────────────────
            (r'optimizer.*step|optim.*step|param.*update|^step$',
             "@dft_ai.compute.step", "compute",
             "Optimizer parameter update step.",
             "dft_ai.compute.step.start()\noptimizer.step()\ndft_ai.compute.step.stop()",
             "start_stop"),
            # ── Compute: forward pass ────────────────────────────────────────────
            (r'^forward$|model\(|predict\b|__call__.*model|inference\b',
             "@dft_ai.compute.forward", "compute",
             "Forward pass through the model.",
             "@dft_ai.compute.forward\ndef forward(model, x):\n    return model(x)",
             "decorator"),
            # ── Compute: backward pass ───────────────────────────────────────────
            (r'backward|\.backward\(\)|grad.*comput|loss.*back',
             "@dft_ai.compute.backward", "compute",
             "Backward pass / gradient computation.",
             "@dft_ai.compute.backward\ndef backward(loss):\n    loss.backward()",
             "decorator"),
            # ── Compute: generic ─────────────────────────────────────────────────
            (r'train.?step|eval.?step|\bcompute\b|run.?step',
             "@dft_ai.compute", "compute",
             "Generic compute step (combined forward+backward or custom).",
             "@dft_ai.compute\ndef train_step(model, batch):\n    ...",
             "decorator"),
            # ── Data: per-item ────────────────────────────────────────────────────
            (r'__getitem__|read.?index|load.?sample|load.?item|get.?item|read.?file\b',
             "@dft_ai.data.item", "data",
             "Per-item data loading from storage (__getitem__ or equivalent).",
             "@dft_ai.data.item\ndef __getitem__(self, idx):\n    return np.load(self.files[idx])",
             "decorator"),
            # ── Data: preprocessing ───────────────────────────────────────────────
            (r'preprocess|transform|augment|collate|normalize|resize|tokenize',
             "@dft_ai.data.preprocess", "data",
             "Data preprocessing, transformation, or augmentation.",
             "@dft_ai.data.preprocess.derive(name='augment')\ndef augment(sample):\n    ...",
             "decorator"),
            # ── Dataloader: fetch ─────────────────────────────────────────────────
            (r'fetch.?batch|load.?batch|next\(iter|__next__|data.?loader.*iter|batch.*fetch',
             "@dft_ai.dataloader.fetch", "dataloader",
             "Fetch a batch from the dataloader.",
             "@dft_ai.dataloader.fetch\ndef fetch_batch(loader):\n    return next(iter(loader))",
             "decorator"),
            # ── Pipeline: epoch iteration ─────────────────────────────────────────
            (r'for.*epoch\b',
             "dft_ai.pipeline.epoch.iter(...)", "pipeline",
             "Epoch-level iteration — wrap the epoch for-loop iterator.",
             "for epoch in dft_ai.pipeline.epoch.iter(range(num_epochs)):\n    ...",
             "iterator"),
            # ── Pipeline: training loop ───────────────────────────────────────────
            (r'\btrain\b|\btraining\b|^fit$|^_fit$|train.?loop|run.?train',
             "@dft_ai.pipeline.train", "pipeline",
             "Training loop function (outer training routine).",
             "@dft_ai.pipeline.train\ndef train(model, loader, epochs):\n    ...",
             "decorator"),
            # ── Pipeline: evaluation/validation ──────────────────────────────────
            (r'\beval\b|\bevaluat|\bvalidat|val.?loop|run.?eval',
             "@dft_ai.pipeline.evaluate", "pipeline",
             "Evaluation or validation loop.",
             "@dft_ai.pipeline.evaluate\ndef evaluate(model, loader):\n    ...",
             "decorator"),
            # ── Pipeline: test ────────────────────────────────────────────────────
            (r'\btest\b|\btesting\b|run.?test|test.?loop',
             "@dft_ai.pipeline.test", "pipeline",
             "Test loop function.",
             "@dft_ai.pipeline.test\ndef test(model, test_loader):\n    ...",
             "decorator"),
            # ── Top-level runner ──────────────────────────────────────────────────
            (r'^run$|^main$|^__call__$|^run_training$',
             "@dft_ai", "pipeline",
             "Top-level entry point for the ML pipeline.",
             "@dft_ai\ndef run(config):\n    ...",
             "decorator"),
        ]

        # Apply phase filter first
        phase_category_map = {
            "compute": ["compute"],
            "data": ["data"],
            "dataloader": ["dataloader"],
            "comm": ["comm"],
            "device": ["device"],
            "checkpoint": ["checkpoint"],
            "pipeline": ["pipeline"],
        }
        phase_filter = phase_category_map.get(phase_lower, [])

        best_decorator = ""
        best_category = ""
        best_rationale = ""
        best_example = ""
        best_api_style = "decorator"
        alternatives = []

        for pattern, decorator, category, rationale, example, api_style in _LOOKUP:
            if phase_filter and category not in phase_filter:
                alternatives_check = False
            else:
                alternatives_check = True

            if _re.search(pattern, combined, _re.I):
                if not best_decorator and alternatives_check:
                    best_decorator = decorator
                    best_category = category
                    best_rationale = rationale
                    best_example = example
                    best_api_style = api_style
                elif decorator != best_decorator:
                    alternatives.append({"decorator": decorator, "category": category})

        # Default fallback
        if not best_decorator:
            if phase_lower == "compute":
                best_decorator = "@dft_ai.compute"
                best_category = "compute"
                best_rationale = "No specific compute pattern matched; use generic compute decorator."
                best_example = "@dft_ai.compute\ndef my_compute_fn(...):\n    ..."
                best_api_style = "decorator"
            elif phase_lower == "data":
                best_decorator = "@dft_ai.data.item"
                best_category = "data"
                best_rationale = "No specific data pattern matched; use data.item for individual reads."
                best_example = "@dft_ai.data.item\ndef read(self, idx):\n    ..."
                best_api_style = "decorator"
            else:
                best_decorator = "@_dlp.log"
                best_category = "generic"
                best_rationale = "No AI/ML region pattern matched; use generic dft_fn log decorator."
                best_example = "_dlp = DFTracerFn('mymodule')\n@_dlp.log\ndef my_fn(...):\n    ..."
                best_api_style = "decorator"

        iterator_wrap = ""
        if best_api_style == "iterator":
            iterator_wrap = best_decorator
            best_decorator = ""

        return _ok(
            f"Annotation for '{function_name or context}': {best_decorator or iterator_wrap}",
            decorator=best_decorator,
            iterator_wrap=iterator_wrap,
            api_style=best_api_style,
            category=best_category,
            rationale=best_rationale,
            example=best_example,
            alternatives=alternatives[:5],
        )

    @mcp.tool()
    def session_detect_ml_workload(
        run_id: str = "",
        source_folder: str = "",
    ) -> str:
        """Detect ML/AI workload characteristics from a source tree.

        Scans for:
        - AI/ML frameworks (PyTorch, TensorFlow, JAX, Keras, Horovod, DeepSpeed, etc.)
        - ROCm/HIP GPU stack (for AMD GPU profiling)
        - CUDA GPU stack
        - Distributed training patterns
        - Data pipeline patterns (DataLoader, HDF5, tfrecord, DALI)
        - HIP profiling requirement (ROCm detected or HIP source code patterns)

        Use this tool BEFORE session_install_dftracer to ensure dftracer is built
        with the correct HIP tracing support when an AMD/ROCm workload is detected.

        Args:
            run_id:        Session identifier (from session_create). When supplied,
                           reads or re-runs detection from the session workspace.
            source_folder: Absolute path to source folder. Used when run_id is empty.

        Returns:
            JSON with:
                frameworks          — list of detected ML frameworks
                rocm_found          — bool: ROCm stack detected on system
                hip_source          — bool: HIP code patterns in source
                hip_tracing_needed  — bool: dftracer should be built with HIP tracing
                rocm_info           — ROCm detection details (path, version, source)
                cuda_found          — bool: CUDA patterns detected
                distributed         — bool: distributed training patterns detected
                has_dataloader      — bool: DataLoader / data pipeline patterns
                annotation_strategy — recommended annotation approach
                install_flags       — dict of env vars to set for session_install_dftracer
                capabilities        — list of capability strings describing this workload
        """
        import re as _re
        from .detection import _detect_info, _detect_rocm, _detect_ml_frameworks

        if run_id:
            ws = _ws(run_id)
            src = ws / "source"
            if not src.exists():
                src = ws / "annotated"
            if not src.exists():
                return _err(f"No source or annotated directory in workspace for run_id={run_id}")
        elif source_folder:
            src = Path(source_folder)
            if not src.exists():
                return _err(f"Source folder does not exist: {source_folder}")
        else:
            return _err("Provide either run_id or source_folder.")

        info = _detect_info(src)
        features = info.get("features", {})
        rocm_info = features.get("rocm", _detect_rocm())
        ml_frameworks = features.get("ml_frameworks", [])
        hip_tracing_needed = features.get("hip_tracing_needed", rocm_info.get("found", False))

        # Scan source text for CUDA and distributed patterns
        all_text = ""
        for ext in (".py", ".cpp", ".cu", ".cxx", ".cc", ".c", ".h"):
            for f in sorted(src.rglob(f"*{ext}"))[:200]:
                try:
                    all_text += f.read_text(errors="ignore")
                    if len(all_text) > 3_000_000:
                        break
                except OSError:
                    pass

        cuda_found = bool(_re.search(r"\.cuda\(\)|cuda\.is_available|torch\.cuda|cudaMalloc|#include\s*[<\"]cuda", all_text, _re.I))
        distributed = bool(_re.search(
            r"dist\.init_process_group|torch\.distributed|horovod|hvd\.init|MPI_Init|DeepSpeed|FSDP|DistributedDataParallel",
            all_text, _re.I
        ))
        has_dataloader = bool(_re.search(
            r"DataLoader|tf\.data|torch\.utils\.data|nvidia\.dali|h5py\.|hdf5|tfrecord|numpy\.load",
            all_text, _re.I
        ))

        # Build annotation strategy
        strategy_parts = []
        if ml_frameworks:
            strategy_parts.append(f"Detected frameworks: {', '.join(ml_frameworks)}")
        if hip_tracing_needed:
            strategy_parts.append("HIP tracing REQUIRED — build dftracer with DFTRACER_ENABLE_HIP_TRACING=ON")
        if distributed:
            strategy_parts.append("Distributed training detected — annotate comm.* regions")
        if has_dataloader:
            strategy_parts.append("Data pipeline detected — annotate data.item and dataloader.fetch regions")

        install_flags = {}
        if hip_tracing_needed:
            install_flags["DFTRACER_ENABLE_HIP_TRACING"] = "ON"
        if features.get("mpi"):
            install_flags["DFTRACER_ENABLE_MPI"] = "ON"
        if features.get("hdf5"):
            install_flags["DFTRACER_ENABLE_HDF5"] = "ON"

        capabilities = []
        for fw in ml_frameworks:
            capabilities.append(f"framework:{fw}")
        if hip_tracing_needed:
            capabilities.append("hip_tracing")
        if rocm_info.get("found"):
            capabilities.append(f"rocm:{rocm_info.get('version', 'unknown')}")
        if cuda_found:
            capabilities.append("cuda")
        if distributed:
            capabilities.append("distributed")
        if has_dataloader:
            capabilities.append("data_pipeline")

        annotation_strategy = " | ".join(strategy_parts) if strategy_parts else "Standard Python annotation with generic dft_fn logging"

        return _ok(
            f"ML workload detected: frameworks={ml_frameworks}, hip_tracing={hip_tracing_needed}",
            frameworks=ml_frameworks,
            rocm_found=rocm_info.get("found", False),
            hip_source=features.get("hip", False),
            hip_tracing_needed=hip_tracing_needed,
            rocm_info=rocm_info,
            cuda_found=cuda_found,
            distributed=distributed,
            has_dataloader=has_dataloader,
            annotation_strategy=annotation_strategy,
            install_flags=install_flags,
            capabilities=capabilities,
            build_tool=info.get("build_tool"),
            languages=info.get("languages", []),
        )

    @mcp.tool()
    def session_search_local_papers(
        query: str,
        bottleneck: str = "",
        framework: str = "",
        top_k: int = 4,
    ) -> str:
        """Search the local ML/AI optimization paper library for relevant citations.

        Searches the pre-indexed local paper collection at
        ``resources/papers/`` — a curated set of targeted ML/AI I/O
        optimization papers that complement online search results.

        Current library (4 papers):

        * **GLANCED-IO** (HPDC '26) — Cross-layer DL I/O optimization;
          parameter ordering (num_workers → prefetch → access_pattern →
          PFS_striping → transfer_size); OFAT greedy autotuner.
        * **Cladia** (IPDPS '26) — Cross-layer diagnosis of DL I/O
          bottlenecks; uncertainty-aware AI; SHAP-based root-cause attribution.
        * **LiveFlow** (SC '26) — Online data movement optimization for
          distributed DL; bounded decision windows; hierarchical coordination.
        * **HORATIO** (SSDBM '26) — Raw trace management at scale; 75x
          faster selective queries; 80-83x analysis speedup over Dask.

        Each result includes:

        * Full citation metadata (title, authors, venue, year)
        * Matching bottleneck categories and frameworks
        * Key concepts extracted from the paper
        * Concrete optimization rules derived from paper findings
        * Path to the local PDF for deeper reading

        Use this tool **before** generating optimization proposals so that
        every proposed optimization is backed by a local paper citation.

        Args:
            query:      Free-text query describing the bottleneck or optimization
                        goal (e.g. ``"prefetch depth data loading stall"``).
            bottleneck: Optional filter by bottleneck category — one of:
                        ``data_loading``, ``prefetch``, ``num_workers``,
                        ``transfer_size``, ``pfs_striping``,
                        ``dataset_access_pattern``, ``cross_layer``,
                        ``checkpoint``, ``distributed``, ``synchronization``,
                        ``critical_path``, ``python_overhead``,
                        ``framework_overhead``, ``trace_management``.
            framework:  Optional filter by ML framework (e.g. ``"pytorch"``).
            top_k:      Maximum number of papers to return (default: 4 = all).

        Returns:
            JSON with ``papers`` (list of match dicts), ``count``, ``query``,
            and ``papers_dir`` (absolute path to local PDF directory).
        """
        from .workspace import _workspaces_root

        # Resolve papers directory relative to workspaces root's parent (project root)
        ws_root = _workspaces_root()
        project_root = ws_root.parent
        papers_dir = project_root / _LOCAL_PAPERS_SUBDIR
        if not papers_dir.exists():
            # Fall back: search sibling directories
            for candidate in [ws_root / _LOCAL_PAPERS_SUBDIR,
                               Path(__file__).resolve().parents[5] / _LOCAL_PAPERS_SUBDIR]:
                if candidate.exists():
                    papers_dir = candidate
                    break

        query_lower = query.lower()
        bottleneck_lower = bottleneck.lower()
        framework_lower = framework.lower()

        scored: List[Dict[str, Any]] = []
        for paper in _LOCAL_PAPER_INDEX:
            score = 0

            # Bottleneck filter
            if bottleneck_lower and bottleneck_lower not in paper["bottlenecks"]:
                # Allow partial match
                if not any(bottleneck_lower in b for b in paper["bottlenecks"]):
                    continue

            # Framework filter
            if framework_lower and framework_lower not in paper.get("frameworks", []):
                continue

            # Score by query term matches
            combined_text = (
                paper["title"].lower() + " " +
                " ".join(paper["bottlenecks"]) + " " +
                " ".join(paper.get("frameworks", [])) + " " +
                " ".join(paper["key_concepts"]).lower() + " " +
                " ".join(paper["optimization_rules"]).lower() + " " +
                paper["abstract_excerpt"].lower()
            )
            for term in query_lower.split():
                if len(term) > 2 and term in combined_text:
                    score += 1

            # Recency bonus (all 2026 papers get +2)
            score += max(0, paper["year"] - 2023)

            scored.append({**paper, "_score": score})

        # Sort by score descending
        scored.sort(key=lambda p: -p["_score"])
        results = scored[:top_k]

        # Build clean output (remove internal score key, add PDF path)
        output_papers = []
        for p in results:
            pdf_path = papers_dir / p["filename"]
            out = {k: v for k, v in p.items() if not k.startswith("_")}
            out["pdf_path"] = str(pdf_path) if pdf_path.exists() else f"(not found: {pdf_path})"
            out["relevance_score"] = p["_score"]
            output_papers.append(out)

        return _ok(
            f"Found {len(output_papers)} local paper(s) for query: '{query}'",
            papers=output_papers,
            count=len(output_papers),
            query=query,
            papers_dir=str(papers_dir),
        )

    @mcp.tool()
    def session_ml_append_lesson(
        app: str,
        context: str,
        error: str,
        root_cause: str,
        fix: str,
        phase: str = "",
        framework: Optional[List[str]] = None,
        annotation_rule: str = "",
        tags: Optional[List[str]] = None,
        run_id: str = "",
    ) -> str:
        """Append a new lesson to the shared annotation lessons file.

        Called automatically at the end of every ``dftracer-ml-annotate`` session
        to record pitfalls, framework-specific quirks, and new annotation rules.
        The lessons accumulate as institutional memory and are read at the start
        of every future session to prevent repeating mistakes.

        This is a **workspace-local staging copy** — it lives at a fixed path
        relative to the workspaces root::

            workspaces/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md

        It is not the source-repo skill file itself. Use
        ``session_lessons_sync_preview`` / ``session_lessons_sync_pr`` to merge
        these staged entries back into the real
        ``dftracer-annotation-lessons/LESSONS_LOG.md`` in the source repo via a PR.

        Entries are appended after the anchor comment line in the file.  If the
        file does not yet exist it is created with the anchor in place.

        Duplicate detection: if an entry with an identical ``context`` string
        already exists in the file, the new entry is skipped and
        ``already_recorded=True`` is returned.

        Args:
            app:             Git URL or short name of the application.
            context:         One-line description of what was being attempted
                             when the issue occurred.
            error:           Verbatim error excerpt (compiler, runtime, or
                             import error message).
            root_cause:      Why the error happened — one concise paragraph.
            fix:             Exact steps, code, or rule that resolved the issue.
            phase:           Pipeline phase where the issue occurred — one of:
                             ``compute``, ``data``, ``dataloader``, ``comm``,
                             ``device``, ``checkpoint``, ``pipeline``,
                             ``install``, or ``run``.
            framework:       List of ML framework names affected
                             (e.g. ``["pytorch", "horovod"]``).
            annotation_rule: New or updated standing rule derived from this
                             lesson (e.g. ``"ML-R17: always call …"``), or
                             empty string if no new rule applies.
            tags:            Additional keyword tags for search.
            run_id:          Optional session identifier — used only to locate
                             the workspaces root when the tool is called from
                             inside a session context.

        Returns:
            JSON with ``status``, ``message``, ``lessons_file``,
            ``already_recorded``, ``entry_date``.
        """
        from .workspace import _workspaces_root

        ws_root = _workspaces_root()
        lessons_path = ws_root / _LESSONS_REL
        lessons_path.parent.mkdir(parents=True, exist_ok=True)

        # Read or create the file
        if lessons_path.exists():
            text = lessons_path.read_text(errors="replace")
        else:
            text = (
                "# dftracer Annotation Lessons\n\n"
                f"{_LESSONS_ANCHOR}\n"
            )

        # Duplicate check on context
        if context.strip() and context.strip() in text:
            return _ok(
                f"Lesson already recorded (context matches): {context[:80]}",
                lessons_file=str(lessons_path),
                already_recorded=True,
                entry_date="",
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fw_str = str(framework or [])
        tag_str = str(tags or ([phase] if phase else []))

        # Build YAML-like block
        entry_lines = [
            "",
            "---",
            f"date: {today}",
            f"app: {app}",
            f"framework: {fw_str}",
            f"context: {context}",
            f"phase: {phase}",
            "error: |",
        ]
        for line in error.splitlines():
            entry_lines.append(f"  {line}")
        entry_lines.append(f"root_cause: {root_cause}")
        entry_lines.append("fix: |")
        for line in fix.splitlines():
            entry_lines.append(f"  {line}")
        if annotation_rule:
            entry_lines.append(f"annotation_rule: {annotation_rule}")
        entry_lines.append(f"tags: {tag_str}")
        entry_lines.append("")

        entry_block = "\n".join(entry_lines)

        if _LESSONS_ANCHOR in text:
            new_text = text.replace(
                _LESSONS_ANCHOR,
                _LESSONS_ANCHOR + entry_block,
                1,
            )
        else:
            new_text = text + entry_block

        lessons_path.write_text(new_text)

        return _ok(
            f"Lesson appended to {lessons_path.name}: {context[:80]}",
            lessons_file=str(lessons_path),
            already_recorded=False,
            entry_date=today,
        )
