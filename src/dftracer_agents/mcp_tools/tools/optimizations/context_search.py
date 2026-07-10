"""Exhaustive, stack-wide, target-number search for the optimization loop.

Where ``session_optimize_l1_app``/``l2_software``/``l3_filesystem`` each search
only the narrow term list tied to the *currently diagnosed* bottleneck (see
``strategies.py``'s per-metric tables), this module searches every software
layer actually detected in the session — not just the one implicated by the
current diagnosis — and adds a query class that doesn't exist elsewhere in the
pipeline: **benchmark-target search**, hunting for published numbers on what's
achievable at this scale/filesystem (e.g. "IOR Lustre 512 OSTs achieved
bandwidth") so the loop has a concrete number to optimize *toward*.

Context-efficient by construction — the two-step search order:

1. **Local first (free):** ``opt_kb_lookup`` (already measured in a past
   session) + local ``rag_search`` (already-downloaded papers/articles) over
   ``.dftracer_agents/resources/``. No network call, no token cost for a live
   paper's full text.
2. **Remote fan-out only for what step 1 didn't already answer** — via the
   7-source ``academic_service`` search (arXiv/S2/OpenAlex/Crossref/CORE/DBLP/
   web). Every kept hit is saved into the local library
   (``local_library_service``'s ``save_paper``/``save_article`` storage
   convention) so the *next* session's step 1 is free too.

Registered as a plain sync MCP tool (session tools are sync — see
``session/config_search.py``'s ``_run_async`` pattern, reused here) even
though the underlying search functions are async.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP

from ..session.workspace import _load_state, _ok, _err, _ws
from ..papers.academic_service import (
    _arxiv_search, _s2_search, _openalex_search, _crossref_search,
    _dblp_search, _core_search, _web_search_ddg,
    _expand_query_terms, _score_paper_relevance,
    _BOTTLENECK_KEYWORD_EXPANSION, _SYSTEM_KEYWORD_EXPANSION,
)
from ..papers.local_library_service import (
    _resources_dirs, _load_index, _save_index, _slugify, _record_id,
)
from .knowledge_base import _lookup as _kb_lookup_rows


def _run_async(coro):
    """Run an async coroutine from this module's sync MCP tool bodies."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


# ---------------------------------------------------------------------------
# Detected-stack -> components to search
# ---------------------------------------------------------------------------

def _detected_components(state: Dict[str, Any]) -> List[str]:
    """Every software/system layer actually in play for this session.

    Reads the same ``detection.features`` dict ``session_detect`` already
    populates (mpi/hdf5/mpi_impl/ml_frameworks/languages) — no new detection
    logic, this just widens what gets searched from "the current bottleneck's
    metric" to "everything the session already knows it's running."
    """
    features = (state.get("detection") or {}).get("features", {}) or {}
    components: List[str] = ["filesystem"]  # every session has one; always searched

    if features.get("mpi"):
        components.append("mpi")
        impl = (state.get("detection") or {}).get("mpi_impl", {}) or {}
        name = (impl.get("name") or "").lower()
        if "cray" in name:
            components.append("cray-mpich")
        elif "openmpi" in name or "open mpi" in name:
            components.append("openmpi")
        elif "mpich" in name:
            components.append("mpich")
    if features.get("hdf5"):
        components.append("hdf5")
    for fw in features.get("ml_frameworks", []) or []:
        components.append(str(fw).lower())
    if features.get("rocm") and isinstance(features["rocm"], dict) and features["rocm"].get("found"):
        components.append("rocm")
    langs = state.get("detection", {}).get("languages") or []
    for lang in langs:
        if str(lang).lower() == "python":
            components.append("python")

    # Deduplicate, preserve order.
    seen: set = set()
    return [c for c in components if not (c in seen or seen.add(c))]


#: Per-component query templates. ``{system}`` is filled from the tool's
#: ``system`` arg. Two classes per component: an "opportunity" query (what can
#: be tuned/optimized) and a "target" query (published achievable numbers —
#: the benchmark-target search this module adds that nothing else in the
#: pipeline does).
_COMPONENT_QUERIES: Dict[str, Dict[str, List[str]]] = {
    "filesystem": {
        "opportunity": ["Lustre parallel filesystem I/O optimization stripe tuning",
                         "parallel filesystem metadata small file performance optimization"],
        "target": ["Lustre {system} achieved aggregate bandwidth published benchmark",
                    "IOR benchmark Lustre stripe count achieved bandwidth GB/s scale"],
    },
    "mpi": {
        "opportunity": ["MPI-IO collective buffering ROMIO hints optimization"],
        "target": ["MPI-IO collective I/O achieved bandwidth published benchmark HPC"],
    },
    "cray-mpich": {
        "opportunity": ["Cray MPICH MPI-IO tuning environment variables HPC"],
        "target": ["Cray MPICH collective I/O achieved bandwidth Cray system benchmark"],
    },
    "openmpi": {
        "opportunity": ["OpenMPI ROMIO MPI-IO hints tuning collective buffering"],
        "target": ["OpenMPI MPI-IO achieved bandwidth published benchmark HPC"],
    },
    "mpich": {
        "opportunity": ["MPICH ROMIO ADIO hints tuning collective I/O"],
        "target": ["MPICH MPI-IO achieved bandwidth published benchmark"],
    },
    "hdf5": {
        "opportunity": ["HDF5 parallel I/O tuning chunking collective metadata cache"],
        "target": ["HDF5 parallel I/O achieved bandwidth published benchmark Lustre"],
    },
    "rocm": {
        "opportunity": ["ROCm GPU data pipeline I/O overlap optimization HPC"],
        "target": ["ROCm GPU training throughput achieved published benchmark"],
    },
    "python": {
        "opportunity": ["Python I/O overhead reduction data loading optimization HPC"],
        "target": [],
    },
    "pytorch": {
        "opportunity": ["PyTorch DataLoader num_workers prefetch_factor I/O optimization"],
        "target": ["PyTorch DataLoader achieved throughput published benchmark deep learning"],
    },
    "tensorflow": {
        "opportunity": ["TensorFlow tf.data pipeline I/O optimization prefetch"],
        "target": ["TensorFlow tf.data achieved throughput published benchmark"],
    },
}

_DEFAULT_QUERIES = {
    "opportunity": ["{component} I/O performance optimization HPC"],
    "target": ["{component} achieved bandwidth published benchmark HPC"],
}


# ---------------------------------------------------------------------------
# Local-first lookup
# ---------------------------------------------------------------------------

def _local_hits_for(component: str, system: str, workload: str) -> Tuple[List[Dict], List[Dict]]:
    """Return (kb_rows, rag_results) already known locally for *component* — free, no network."""
    kb_rows = _kb_lookup_rows(system, workload, component, "", "", 10)

    from ..papers import rag_service  # lazy: see local_library_service for the same
                                       # rationale (avoids a heavier import chain at module
                                       # load time for a dependency this tool may not use)
    entries = _load_index()
    rag = rag_service.rag_search(f"{component} optimization", entries, "", component, top_k=5)
    return kb_rows, rag.get("results", [])


# ---------------------------------------------------------------------------
# Remote fan-out (only for what local didn't answer)
# ---------------------------------------------------------------------------

async def _remote_search_one(query: str, max_each: int = 3) -> List[Dict[str, Any]]:
    results = await asyncio.gather(
        _arxiv_search(query, max_each), _s2_search(query, max_each),
        _openalex_search(query, max_each), _crossref_search(query, max_each),
        _dblp_search(query, max_each), _core_search(query, max_each),
        return_exceptions=True,
    )
    papers: List[Dict[str, Any]] = []
    for r in results:
        if isinstance(r, list):
            papers.extend(r)
    return papers


async def _remote_search_components(
    components: List[str], system: str, query_class: str, max_each: int = 3,
) -> Dict[str, List[Dict[str, Any]]]:
    """Run every component's *query_class* queries in parallel; return {component: [papers]}."""
    tasks = []
    task_components = []
    for component in components:
        templates = _COMPONENT_QUERIES.get(component, _DEFAULT_QUERIES)
        for q in templates.get(query_class, []):
            query = q.format(system=system or "HPC system", component=component)
            tasks.append(_remote_search_one(query, max_each))
            task_components.append(component)
    if not tasks:
        return {}
    results = await asyncio.gather(*tasks, return_exceptions=True)
    by_component: Dict[str, List[Dict[str, Any]]] = {}
    for component, r in zip(task_components, results):
        if isinstance(r, list):
            by_component.setdefault(component, []).extend(r)
    return by_component


def _save_hit_to_library(paper: Dict[str, Any], query: str) -> Optional[str]:
    """Persist a remote search hit into the local library as a text-only entry (no PDF fetch —
    this tool prioritizes speed over archiving; use save_paper explicitly for full-text caching).

    Returns the stored entry id, or None if it had no usable title/url.
    """
    title = paper.get("title") or ""
    url = paper.get("url") or paper.get("pdf_url") or paper.get("abs_url") or ""
    if not title or not url:
        return None
    dirs = _resources_dirs()
    rec_id = _record_id(url)
    entries = _load_index()
    if any(e["id"] == rec_id for e in entries):
        return rec_id  # already saved — nothing to do

    filename = f"{_slugify(title)}-{rec_id}.md"
    dest = dirs["articles"] / filename
    body = f"# {title}\n\nSource: {url}\n\n{paper.get('abstract', '')}"
    dest.write_text(body)

    record = {
        "id": rec_id, "type": "article",
        "filename": str(dest.relative_to(dirs["root"])),
        "title": title, "authors": ", ".join(paper.get("authors", []) or []),
        "year": paper.get("year"), "source": paper.get("source", ""),
        "query": query, "abstract": paper.get("abstract", ""), "doi": "",
        "url": url, "added": datetime.now(timezone.utc).isoformat(),
        "text_extracted": True, "text_len": len(body),
    }
    entries.append(record)
    _save_index(entries)
    return rec_id


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

def register_context_search_tools(mcp: FastMCP) -> None:
    """Register ``session_search_optimization_context`` onto *mcp*."""

    @mcp.tool()
    def session_search_optimization_context(
        run_id: str,
        system: str = "",
        workload: str = "",
        metric_scope: str = "",
        max_remote_per_query: int = 3,
    ) -> str:
        """Exhaustive, stack-wide literature/docs/benchmark search — beyond the current diagnosis.

        Searches every software/system layer actually detected in this session
        (not just the metric tied to the current bottleneck), local-first
        (`opt_kb_lookup` + local `rag_search`, free) then remote fan-out across
        7 paper sources only for what's not already known locally. Adds a query
        class nothing else in the pipeline has: benchmark-target search — hunts
        for published achievable bandwidth/throughput numbers at this scale so
        the loop has a concrete target, not just a direction.

        Call this BEFORE `session_optimize_l1_app`/`l2_software`/`l3_filesystem`
        — it's step "0.5" of the optimization loop, after `opt_kb_lookup`
        (step 1) and before proposal generation.

        Args:
            run_id:       Session identifier — reads the detected stack from
                this session's `session.json` (mpi/hdf5/frameworks/languages).
            system:       System name for KB lookup + benchmark-target queries
                (e.g. "tuolumne"). Optional but sharpens target-number search.
            workload:     Application name for KB lookup (e.g. "flash_x").
            metric_scope: Optional filter — "app" or "system" — to only search
                query classes relevant to one axis (see `opt_kb_record`'s
                `metric_scope`). Blank searches both.
            max_remote_per_query: Papers to fetch per source per query (default 3).

        Returns:
            JSON written to `<workspace>/context_opportunities.json` and
            returned inline: `components_searched`, `opportunities` (candidate
            optimizations beyond the current bottleneck, each tagged
            `component`/`metric_scope`/`query_class`/citation), and
            `benchmark_targets` (published numbers with citations — read the
            `snippet` to extract the actual value; this tool does not attempt
            to parse numbers out of free text, that's a judgment call for the
            calling agent).
        """
        state = _load_state(run_id)
        if not state:
            return _err(f"No session found for run_id={run_id!r}")

        components = _detected_components(state)
        want_classes = [metric_scope] if metric_scope in ("app", "system") else None

        opportunities: List[Dict[str, Any]] = []
        benchmark_targets: List[Dict[str, Any]] = []
        components_needing_remote: List[str] = []

        # ── Step 1: local first (free) ───────────────────────────────────
        for component in components:
            kb_rows, rag_results = _local_hits_for(component, system, workload)
            if kb_rows or rag_results:
                for r in rag_results:
                    opportunities.append({
                        "component": component, "metric_scope": "app",
                        "query_class": "opportunity", "source": "local:" + r.get("source", ""),
                        "title": r.get("title"), "url": r.get("url"),
                        "score": r.get("combined_score"), "chunk": r.get("chunk"),
                        "citation": r.get("url") or f"session:{run_id}",
                    })
            # Even with local hits, still worth a remote check for THIS
            # session's specific scale/system if nothing local mentions it —
            # local coverage of opportunity queries does not imply target
            # numbers exist locally too, so always attempt remote for target
            # class unless explicitly filtered out below.
            if not rag_results:
                components_needing_remote.append(component)

        # ── Step 2: remote fan-out only for what step 1 didn't answer ────
        query_classes = want_classes or ["opportunity", "target"]
        remote_components = components_needing_remote or components  # always try target queries
        for query_class in query_classes:
            if query_class == "opportunity" and not components_needing_remote:
                continue  # already covered locally
            by_component = _run_async(
                _remote_search_components(remote_components, system, query_class, max_remote_per_query)
            )
            for component, papers in by_component.items():
                query_terms = _expand_query_terms(component, _BOTTLENECK_KEYWORD_EXPANSION)
                boost_terms = _expand_query_terms(system, _SYSTEM_KEYWORD_EXPANSION) if system else []
                for paper in papers:
                    score, matched = _score_paper_relevance(paper, query_terms, boost_terms)
                    if score <= 0:
                        continue
                    saved_id = _save_hit_to_library(paper, f"{component} {query_class}")
                    row = {
                        "component": component,
                        "metric_scope": "system" if query_class == "target" else "app",
                        "query_class": query_class,
                        "source": paper.get("source", ""),
                        "title": paper.get("title"), "url": paper.get("url") or paper.get("pdf_url"),
                        "authors": paper.get("authors"), "year": paper.get("year"),
                        "score": score, "snippet": (paper.get("abstract") or "")[:400],
                        "citation": paper.get("url") or paper.get("pdf_url") or "",
                        "saved_to_library": saved_id,
                    }
                    if query_class == "target":
                        benchmark_targets.append(row)
                    else:
                        opportunities.append(row)

        opportunities.sort(key=lambda r: r.get("score") or 0, reverse=True)
        benchmark_targets.sort(key=lambda r: r.get("score") or 0, reverse=True)

        result = {
            "run_id": run_id,
            "components_searched": components,
            "remote_fetched_for": remote_components if query_classes else [],
            "opportunities": opportunities[:30],
            "benchmark_targets": benchmark_targets[:15],
        }
        out_path = _ws(run_id) / "context_opportunities.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))

        return _ok(
            f"{len(components)} components searched, {len(opportunities)} opportunities, "
            f"{len(benchmark_targets)} benchmark targets found -> {out_path}",
            **result,
        )
