"""Optimization level tools — session_optimize_l1_app, session_optimize_l2_software,
session_optimize_l3_filesystem, session_snapshot_l1_source, session_run_l1_iteration."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ..session.workspace import (
    _ws, _load_state, _save_state, _write_artifact_log, _ok, _err, _run, _workspaces_root,
)
from ..session.install import _dftracer_utils_split, _dftracer_info_uncompressed_bytes
from .strategies import (
    _fetch_arxiv_papers,
    _BUILTIN_REFS,
    _L1_STRATEGIES,
    _L2_STRATEGIES,
    _L3_STRATEGIES,
    _gen_level_proposals,
    _METRIC_SYNONYM_PAIRS,
    _GENERAL_FALLBACK_QUERIES,
)


def register_level_tools(mcp: FastMCP) -> None:
    """Register session_optimize_l1_app, session_optimize_l2_software, session_optimize_l3_filesystem onto *mcp*."""

    @mcp.tool()
    def session_optimize_l1_app(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed application-code optimization proposals (Level 1).

        Searches arXiv for papers on application-level I/O optimization techniques
        that target the detected bottlenecks (buffer coalescing, async I/O, access
        reordering, DataLoader tuning, checkpoint async-write, etc.).

        Every proposal MUST be backed by a verifiable citation (URL).
        Proposals without a citation are silently dropped.

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (list with citation sub-key per entry),
            citation_sources (searched vs built-in counts), unsupported (unmatched metrics).
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx  = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        # ── Targeted arXiv searches per unique bottleneck ────────────────────
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]
        searched: Dict[str, List[Dict[str, Any]]] = {}

        # Merge already-searched papers from the iteration
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        # Domain-specific L1 search queries per bottleneck
        _L1_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["application buffer coalescing small I/O HPC performance",
                               "buffered I/O aggregation parallel file access"],
            "small_read":     ["read buffering application layer HPC I/O optimization"],
            "small_write":    ["write buffering batch I/O application code parallel"],
            "rand_pct":       ["random to sequential I/O reordering application optimization HPC",
                               "index sorting access pattern parallel I/O performance"],
            "seq_pct":        ["sequential access optimization posix_fadvise prefetch HPC"],
            "read_time":      ["async I/O io_uring application code latency HPC",
                               "non-blocking I/O file descriptor pre-open parallel"],
            "write_time":     ["async checkpoint write background thread HPC application",
                               "fallocate write performance parallel storage"],
            "metadata_time":  ["metadata caching application layer stat syscall HPC",
                               "file open reuse epoch training metadata optimization"],
            "fetch_pressure": ["DataLoader parallel I/O workers prefetch deep learning HPC",
                               "data pipeline prefetching GPU training throughput"],
            "epoch_straggler":["sample size bucketing training batch latency optimization",
                               "straggler mitigation deep learning data loading"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric", "")
            if met in searched and searched[met]:
                continue  # already have papers for this metric
            queries = _L1_SEARCH.get(met, [
                f"application code {met} optimization HPC parallel I/O",
                f"{met} I/O performance tuning source code",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L1_STRATEGIES, "L1",
            searched, max_per_level=max_proposals,
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L1 app: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L1",
            iteration=idx,
        )

    @mcp.tool()
    def session_optimize_l2_software(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed software/middleware optimization proposals (Level 2).

        Searches arXiv for papers on MPI-IO collective buffering, ROMIO hints,
        HDF5 chunk/cache tuning, PyTorch DataLoader env-var tuning, NetCDF/PnetCDF
        settings, and other middleware configuration changes that do not require
        source code edits.

        Every proposal is backed by a verifiable citation (URL).

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (each with citation + delivery/env_key),
            citation_sources, unsupported.
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx    = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]

        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        _L2_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["ROMIO collective buffering MPI-IO small request optimization",
                               "MPI-IO cb_buffer_size collective I/O aggregation performance"],
            "rand_pct":       ["ROMIO data sieving non-contiguous MPI-IO access",
                               "MPI-IO hints access pattern optimization parallel"],
            "read_time":      ["ROMIO collective read buffering MPI-IO latency reduction",
                               "HDF5 chunk cache read performance parallel HPC"],
            "write_time":     ["ROMIO collective write MPI-IO throughput hints",
                               "HDF5 collective metadata write parallel performance"],
            "metadata_time":  ["HDF5 metadata cache collective write parallel optimization",
                               "MPI-IO metadata overhead reduction hints"],
            "fetch_pressure": ["PyTorch DataLoader num_workers prefetch_factor throughput",
                               "deep learning data pipeline I/O throughput GPU training"],
            "epoch_straggler":["PyTorch DistributedSampler persistent_workers optimization",
                               "deep learning training epoch tail latency worker tuning"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric","")
            if met in searched and searched[met]:
                continue
            queries = _L2_SEARCH.get(met, [
                f"middleware configuration {met} parallel I/O optimization",
                f"MPI-IO HDF5 {met} tuning HPC performance",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L2_STRATEGIES, "L2",
            searched, max_per_level=max_proposals,
            extra_fields=["delivery", "env_key"],
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L2 software: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L2",
            iteration=idx,
        )

    @mcp.tool()
    def session_optimize_l3_filesystem(
        run_id: str,
        iteration: int = -1,
        metric: str = "time",
        max_proposals: int = 5,
    ) -> str:
        """Generate citation-backed filesystem/OS optimization proposals (Level 3).

        Searches arXiv for papers on Lustre stripe tuning, GPFS configuration,
        Linux kernel readahead (blockdev --setra), vm.dirty page writeback tuning,
        I/O scheduler selection, and NUMA memory binding for I/O-bound workloads.

        Every proposal includes:
        - A verifiable citation URL (arXiv or built-in reference)
        - Required privilege level (no-sudo | sudo | admin-only)
        - Rollback command
        - Side-effect warning

        Args:
            run_id:         Session identifier.
            iteration:      Which optimization iteration to read (-1 = latest).
            metric:         Optimization objective (time | bandwidth | iops | metadata_ops).
            max_proposals:  Maximum total proposals to return (default 5).

        Returns:
            JSON with keys: status, proposals (each with citation + privilege/rollback/side_effect),
            citation_sources, unsupported.
        """
        import json as _json

        state   = _load_state(run_id)
        history = state.get("optimization_history", [])
        if not history:
            return _err("No optimization iterations — run session_optimization_iteration first.")

        idx    = iteration if iteration >= 0 else len(history) - 1
        iter_e = history[idx]
        bottlenecks = iter_e.get("bottlenecks", [])

        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        high_bns = [b for b in bottlenecks if _SEV.get(b.get("severity","trivial"),0) >= 2]

        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit in iter_e.get("literature", []):
            searched[lit["bottleneck"]] = lit.get("papers", [])

        _L3_SEARCH: Dict[str, List[str]] = {
            "small_io":       ["Lustre striping small file I/O optimization HPC",
                               "parallel filesystem stripe configuration performance"],
            "rand_pct":       ["Linux kernel readahead blockdev setra optimization HPC",
                               "sequential I/O readahead tuning parallel workload"],
            "read_time":      ["page cache pressure vfs_cache_pressure HPC read optimization",
                               "Linux kernel I/O performance read cache tuning"],
            "write_time":     ["vm.dirty_ratio dirty page writeback HPC I/O tuning",
                               "Linux kernel write performance dirty page flush optimization"],
            "metadata_time":  ["Lustre metadata striping DNE directory optimization",
                               "parallel filesystem metadata performance tuning HPC"],
            "fetch_pressure": ["NUMA memory binding I/O performance HPC training",
                               "numactl memory locality deep learning GPU I/O throughput"],
        }

        seen_titles: set = set()
        for bn in high_bns:
            met = bn.get("metric","")
            if met in searched and searched[met]:
                continue
            queries = _L3_SEARCH.get(met, [
                f"filesystem OS {met} optimization HPC parallel",
                f"Lustre GPFS {met} tuning performance",
            ])
            for q in queries:
                papers = _fetch_arxiv_papers(q, n=3)
                unique = [p for p in papers
                          if p.get("title","").lower()[:60] not in seen_titles]
                if unique:
                    for p in unique:
                        seen_titles.add(p.get("title","").lower()[:60])
                    searched[met] = unique
                    break

        proposals, cs, cb = _gen_level_proposals(
            bottlenecks, _L3_STRATEGIES, "L3",
            searched, max_per_level=max_proposals,
            extra_fields=["privilege", "rollback", "side_effect"],
        )
        unsupported = [b["metric"] for b in high_bns
                       if not any(p["bottleneck"] == b["metric"] for p in proposals)]

        return _ok(
            f"L3 filesystem: {len(proposals)} citation-backed proposal(s). "
            f"Citations: {cs} arXiv, {cb} built-in. Unsupported: {unsupported or 'none'}.",
            proposals=proposals,
            unsupported=unsupported,
            citation_sources={"searched_papers": cs, "builtin_references": cb},
            level="L3",
            iteration=idx,
        )

    @mcp.tool()
    def session_snapshot_l1_source(
        run_id: str,
        iteration: int,
        label: str = "",
    ) -> str:
        """Snapshot the current annotated source tree before or after an L1 optimization pass.

        Creates a versioned copy of ``<workspace>/annotated/`` at
        ``<workspace>/opt_snapshots/l1_iter_<N>/source/`` so that application-code
        changes made during L1 optimization are permanently recorded and comparable
        across iterations.

        Call this tool:
        * **Before** applying any accepted L1 proposal (``iteration=0`` for the
          pre-optimization baseline, ``iteration=N`` before each subsequent round).
        * The snapshot directory is never overwritten — if it already exists the
          tool returns an error so callers know they need to bump the iteration
          number.

        Directory layout written::

            <workspace>/opt_snapshots/
              l1_iter_0/         ← baseline (before any L1 changes)
                source/          ← copy of annotated/ at this point
                snapshot.json    ← metadata: timestamp, label, session state excerpt
              l1_iter_1/         ← after first accepted proposal batch
                source/
                snapshot.json
              l1_iter_2/ ...

        Side effects:
            * Creates ``<workspace>/opt_snapshots/l1_iter_<N>/source/`` with a
              recursive copy of ``<workspace>/annotated/``.
            * Writes ``snapshot.json`` with timestamp, label, and a summary of
              the current ``session.json`` state.
            * Persists ``{"l1_snapshot_<N>": "<path>"}`` to ``session.json``.

        Args:
            run_id:    Session identifier.
            iteration: Iteration index (0 = baseline, 1 = first proposal batch, …).
            label:     Optional human-readable description of what changed
                       (e.g. ``"applied buffer coalescing"``).

        Returns:
            JSON with keys: ``status``, ``snapshot_dir``, ``iteration``, ``label``,
            ``files_copied``.
        """
        import datetime as _dt

        ws = _ws(run_id)
        ann_dir = ws / "annotated"
        if not ann_dir.exists():
            return _err(
                "annotated/ not found — run session_copy_annotated first.",
                run_id=run_id,
            )

        snap_root = ws / "opt_snapshots" / f"l1_iter_{iteration}"
        snap_src  = snap_root / "source"

        if snap_root.exists():
            return _err(
                f"Snapshot for iteration {iteration} already exists at {snap_root}. "
                "Increment iteration to create a new snapshot.",
                snapshot_dir=str(snap_root),
            )

        shutil.copytree(ann_dir, snap_src, symlinks=True, ignore_dangling_symlinks=True)

        files_copied = sum(1 for _ in snap_src.rglob("*") if _.is_file())

        state = _load_state(run_id)
        snapshot_meta = {
            "iteration":    iteration,
            "label":        label or f"l1_iter_{iteration}",
            "timestamp":    _dt.datetime.utcnow().isoformat() + "Z",
            "source_dir":   str(ann_dir),
            "snapshot_dir": str(snap_src),
            "files_copied": files_copied,
            "session_step": state.get("step", ""),
        }
        (snap_root / "snapshot.json").write_text(json.dumps(snapshot_meta, indent=2))

        _save_state(run_id, {f"l1_snapshot_{iteration}": str(snap_root)})

        return _ok(
            f"Snapshot {iteration} created: {files_copied} file(s) copied to {snap_src}",
            snapshot_dir=str(snap_root),
            source_copy=str(snap_src),
            iteration=iteration,
            label=label or f"l1_iter_{iteration}",
            files_copied=files_copied,
        )

    @mcp.tool()
    def session_run_l1_iteration(
        run_id: str,
        iteration: int,
        command: str,
        subfolder: str = "build_ann",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: str = "",
        app_name: str = "app",
    ) -> str:
        """Run the benchmark with dftracer and collect iteration-specific L1 traces.

        Behaves like ``session_run_with_dftracer`` but writes traces into a
        per-iteration directory so that multiple optimization rounds remain
        independently analyzable:

        * Traces written to  ``<workspace>/traces_opt_l1_iter_<N>/``
        * Split output goes to ``<workspace>/traces_opt_l1_iter_<N>_split/``
        * Persists the run result and trace paths under
          ``l1_iterations[N]`` in ``session.json``.

        Typical L1 iteration workflow::

            session_snapshot_l1_source(run_id, iteration=N)   # snapshot source first
            # ... agent applies accepted proposals to annotated/ ...
            session_build_annotated(run_id)                   # rebuild
            session_run_l1_iteration(run_id, iteration=N, command="./run.sh")
            session_analyze_traces(run_id,
                trace_subdir=f"traces_opt_l1_iter_{N}_split")
            session_diagnose_bottlenecks(run_id)              # compare vs baseline

        Args:
            run_id:     Session identifier.
            iteration:  L1 iteration index (must match a prior ``session_snapshot_l1_source``
                        call — enforced by convention, not hard-checked).
            command:    Shell command to run the benchmark (same as ``session_run_with_dftracer``).
            subfolder:  Working directory relative to workspace root.
                        Defaults to ``"build_ann"``.
            data_dir:   Value for ``DFTRACER_DATA_DIR``.  Defaults to ``"all"``
                        (trace all I/O paths).
            timeout:    Seconds before the subprocess is killed.  Defaults to ``600``.
            env_extra:  Optional JSON object string with extra environment variables
                        to set alongside the DFTRACER_* ones.
            app_name:   Prefix for split output chunk files.  Defaults to ``"app"``.

        Returns:
            JSON with keys: ``status``, ``message``, ``iteration``, ``traces_dir``,
            ``split_dir``, plus the subprocess result fields (``stdout``, ``stderr``,
            ``returncode``).
        """
        ws = _ws(run_id)

        traces_dir = ws / f"traces_opt_l1_iter_{iteration}"
        split_dir  = ws / f"traces_opt_l1_iter_{iteration}_split"
        traces_dir.mkdir(exist_ok=True)
        split_dir.mkdir(exist_ok=True)

        cwd = ws / subfolder
        if not cwd.exists():
            cwd = ws / "build"
        if not cwd.exists():
            cwd = ws / "source"

        run_id_safe = run_id.replace("/", "_")
        log_prefix = str(traces_dir / run_id_safe)
        Path(log_prefix).parent.mkdir(parents=True, exist_ok=True)

        env: Dict[str, str] = {
            "DFTRACER_ENABLE":       "1",
            "DFTRACER_INC_METADATA": "1",
            "DFTRACER_LOG_FILE":     log_prefix,
            "DFTRACER_DATA_DIR":     data_dir,
        }
        if env_extra:
            env.update(json.loads(env_extra))

        r = _run(["/bin/sh", "-c", command], cwd=cwd, env=env, timeout=timeout)

        # Persist run result before attempting split (split failure is non-fatal)
        iter_record: Dict[str, Any] = {
            "iteration":  iteration,
            "command":    command,
            "traces_dir": str(traces_dir),
            "split_dir":  str(split_dir),
            "run_result": r,
        }

        # Auto-split into iter-specific split dir
        trace_files = (
            list(traces_dir.rglob("*.pfw"))
            + list(traces_dir.rglob("*.pfw.gz"))
        )
        split_result: Dict[str, Any] = {}
        _SPLIT_MB = 512
        if not trace_files:
            split_result = {"success": False, "stdout": "No .pfw/.pfw.gz files found in traces dir"}
        elif len(trace_files) == 1:
            uncompressed = _dftracer_info_uncompressed_bytes(str(trace_files[0]))
            if uncompressed is not None and uncompressed <= _SPLIT_MB * 1024 * 1024:
                shutil.copy2(trace_files[0], split_dir / trace_files[0].name)
                split_result = {
                    "success": True,
                    "stdout": f"Single file ({uncompressed / (1024*1024):.1f} MB) copied without splitting",
                }
            else:
                split_result = _dftracer_utils_split(
                    directory=str(traces_dir),
                    output_dir=str(split_dir),
                    app_name=app_name,
                )
        else:
            split_result = _dftracer_utils_split(
                directory=str(traces_dir),
                output_dir=str(split_dir),
                app_name=app_name,
            )

        iter_record["split_result"] = split_result

        # Update session state: append to l1_iterations list
        state = _load_state(run_id)
        iters = state.get("l1_iterations", [])
        # Replace entry if iteration index already recorded
        iters = [e for e in iters if e.get("iteration") != iteration]
        iters.append(iter_record)
        iters.sort(key=lambda x: x.get("iteration", 0))
        _save_state(run_id, {"l1_iterations": iters})
        _write_artifact_log(
            ws, 20 + iteration, "session_run_l1_iteration",
            {"iteration": iteration, "result": r},
            run_id,
        )

        if r["success"]:
            return _ok(
                f"L1 iteration {iteration} run complete. "
                f"Traces: {traces_dir}. Split: {split_dir}.",
                iteration=iteration,
                traces_dir=str(traces_dir),
                split_dir=str(split_dir),
                **r,
            )
        return _err(
            f"L1 iteration {iteration} command failed.",
            iteration=iteration,
            traces_dir=str(traces_dir),
            split_dir=str(split_dir),
            **r,
        )
