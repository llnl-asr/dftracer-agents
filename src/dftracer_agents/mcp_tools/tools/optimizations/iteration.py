"""Optimization iteration tools — session_optimization_iteration and session_generate_optimization_proposals."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ..session.workspace import (
    _ws, _load_state, _save_state, _write_artifact_log, _ok, _err, _run, _workspaces_root,
)
from ..session.install import _dftracer_utils_comparator
from ..session.detection import _detect_analyzer_presets
from ..session.session_tools import (
    _session_build_annotated_impl,
    _session_run_with_dftracer_impl,
    _session_split_traces_impl,
    _session_collect_system_info_impl,
    _run_source_dir,
    _run_patches_dir,
    _snapshot_source,
    _generate_patch,
)
from .diagnose import _session_diagnose_bottlenecks_impl


def _load_external_bottlenecks(
    run_id: str, bottlenecks_json: Optional[str]
) -> Optional[List[Dict[str, Any]]]:
    """Load a bottleneck list produced outside ``session_optimization_iteration``.

    Order: an explicit ``bottlenecks_json`` payload, then
    ``<ws>/<run>/analysis/diagnosis.json`` for the usual run names. Returns ``None``
    when nothing usable is found, so the caller can report a precise error.
    """
    if bottlenecks_json:
        try:
            data = json.loads(bottlenecks_json)
        except json.JSONDecodeError:
            return None
        if isinstance(data, dict):
            data = data.get("bottlenecks")
        return data if isinstance(data, list) and data else None

    ws = _ws(run_id)
    for run_name in ("baseline", "opt1", "."):
        cand = ws / run_name / "analysis" / "diagnosis.json"
        if not cand.is_file():
            continue
        try:
            data = json.loads(cand.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict):
            data = data.get("bottlenecks")
        if isinstance(data, list) and data:
            return data
    return None
from .strategies import (
    _fetch_arxiv_papers,
    _BUILTIN_REFS,
    _L1_STRATEGIES,
    _L2_STRATEGIES,
    _L3_STRATEGIES,
    _gen_level_proposals,
    _METRIC_SYNONYM_PAIRS,
    _GENERAL_FALLBACK_QUERIES,
    _build_sys_context,
    _bottleneck_search_queries,
    _category_sort_key,
    _metric_category,
    _DL_ALWAYS_ON_METRICS,
)
from .memory import _memory_retrieve, _memory_write, _memory_reflect


def register_iteration_tools(mcp: FastMCP) -> None:
    """Register session_optimization_iteration and session_generate_optimization_proposals onto *mcp*."""

    @mcp.tool()
    def session_optimization_iteration(
        run_id: str,
        command: str,
        app_name: str = "app",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: Optional[str] = None,
        optimization_applied: str = "",
        rebuild: bool = True,
        max_search_attempts: int = 10,
        papers_per_query: int = 3,
    ) -> str:
        """Run one iteration of the build → profile → diagnose → search optimization loop.

        Each call executes the following pipeline in sequence:

        1. **Build** (optional) — rebuild the annotated binary so any source
           changes applied between calls take effect.  Skip with
           ``rebuild=False`` to re-profile without rebuilding.
        2. **Profile** — run *command* with dftracer tracing enabled.
        3. **Split** — compact raw ``.pfw`` traces.
        4. **Diagnose** — run DFAnalyzer + DFDiagnoser and score bottlenecks.
        5. **System context** — collect or re-read ``system_config.json`` so
           hardware details (CPU arch, filesystem type, network) are available
           to refine search queries.
        6. **Literature search** — bottlenecks are ranked and addressed in the
           canonical order **I/O -> communication -> memory -> compute**
           (severity is only the tiebreaker *within* a category, so a
           critical compute issue never jumps ahead of a medium I/O issue).
           For deep-learning workloads, two additional dimensions are always
           evaluated regardless of severity: (a) application dataloader /
           epoch-time performance, and (b) filesystem bandwidth/utilization
           for the storage the run is on. For the resulting top bottlenecks,
           search arXiv with up to *max_search_attempts* progressively
           fuzzier queries that combine the bottleneck behaviour **and** the
           system hardware context:

           * Attempts 1-2: most specific phrase + system context.
           * Attempts 3-8: synonym phrase pairs with/without system context.
           * Attempts 9-10: broadest fallback ("I/O optimization {sys_context}",
             then generic "parallel I/O performance optimization HPC").

           A bottleneck is marked **unsolved** if no papers are found after all
           *max_search_attempts* queries.  The tool reports what it could not
           find so the agent knows where to ask the user for guidance.

        7. **Compare** — diff the bottleneck severity table against the previous
           iteration (stored in ``session.json`` under ``optimization_history``).

        **Typical agent loop**::

            # Baseline — first iteration
            r0 = session_optimization_iteration(
                run_id, command, optimization_applied="baseline")
            # Agent reads r0.literature and r0.unsolved, applies a source change.
            r1 = session_optimization_iteration(
                run_id, command, optimization_applied="increased write buffer to 4 MiB")
            # r1.delta shows which bottlenecks improved / regressed.
            # Repeat until r1.bottlenecks is empty or delta shows no improvement.

        Args:
            run_id:               Session identifier.
            command:              Benchmark command passed to
                                  ``session_run_with_dftracer``.
            app_name:             Trace split file prefix.
            data_dir:             ``DFTRACER_DATA_DIR`` value (default ``"all"``).
            timeout:              Seconds allowed for profiled run and diagnosis.
            env_extra:            Extra env vars as JSON (same format as
                                  ``session_run_with_dftracer``).
            optimization_applied: Human-readable label for this iteration's change.
                                  Use ``"baseline"`` on the first call.
            rebuild:              If ``True``, rebuild before profiling.
            max_search_attempts:  Maximum arXiv queries per bottleneck (1-10,
                                  default 10).  Stops early when papers are found.
            papers_per_query:     Papers to fetch per query attempt (1-5, default 3).

        Returns:
            JSON string with keys:

            * ``status``          — ``"ok"`` or ``"error"``.
            * ``iteration``       — zero-based iteration index.
            * ``optimization``    — echoed *optimization_applied* label.
            * ``build``           — build step summary (or ``null``).
            * ``profile``         — profiling step summary.
            * ``diagnosis``       — DFDiagnoser summary (severity counts).
            * ``system_context``  — hardware context string used for searches.
            * ``top5_bottlenecks``— top-5 bottlenecks by severity.
            * ``literature``      — list of per-bottleneck search results:
              ``{"bottleneck": …, "queries_tried": N, "papers": [...]}``.
            * ``unsolved``        — bottlenecks for which no papers were found
              after all *max_search_attempts* attempts, with the queries tried.
            * ``delta``           — severity delta vs previous iteration:
              ``{"improved": [...], "regressed": [...], "resolved": [...],
              "new": [...], "unchanged": [...]}``.
            * ``recommendation``  — plain-text guidance for the next step.
        """
        import json as _json
        import shutil as _sh

        state = _load_state(run_id)
        history: list = state.get("optimization_history", [])
        iteration = len(history)
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}

        # ── Step 1: (re)build annotated binary ──────────────────────────────
        build_summary = None
        if rebuild:
            raw = _session_build_annotated_impl(run_id=run_id)
            build_result = _json.loads(raw)
            if build_result.get("status") != "ok":
                return _json.dumps({
                    "status": "error",
                    "message": f"Build failed at iteration {iteration}: "
                               + build_result.get("message", ""),
                    "build": build_result,
                    "iteration": iteration,
                })
            build_summary = {
                "returncode": build_result.get("returncode", 0),
                "duration_s": build_result.get("duration_s"),
            }

        # ── Per-iteration artifact directory ─────────────────────────────────
        ws = _ws(run_id)
        run_name = f"opt{iteration}"
        iter_dir = ws / run_name
        iter_dir.mkdir(exist_ok=True)
        # Canonical run structure: traces/raw, traces/compact, source/, patches/
        iter_traces_dir = iter_dir / "traces" / "raw"
        iter_split_dir  = iter_dir / "traces" / "compact"
        iter_traces_dir.mkdir(parents=True, exist_ok=True)
        iter_split_dir.mkdir(parents=True, exist_ok=True)

        # ── Source snapshot + patch ──────────────────────────────────────────
        # Snapshot current annotated source into opt{N}/source/.
        # Generate a patch vs the previous iteration's source snapshot.
        ann_src = ws / "annotated"
        if ann_src.exists():
            source_dest = _run_source_dir(ws, run_name)
            _snapshot_source(ann_src, source_dest)
            # Determine previous run: opt{N-1}/source/ or annotated/source/
            if iteration == 0:
                prev_src = ws / "annotated" / "source"
                if not prev_src.exists():
                    prev_src = ws / "annotated"
                    # Try the structured annotated run snapshot
                    if (ws / "annotated" / "source").exists():
                        prev_src = ws / "annotated" / "source"
                # For iteration 0, diff vs the annotated run's source snapshot
                prev_src = ws / "annotated" / "source" if (ws / "annotated" / "source").exists() else None
                if prev_src:
                    patch_file = _run_patches_dir(ws, run_name) / "from_annotated.patch"
                    _generate_patch(prev_src, source_dest, patch_file)
                else:
                    patch_file = _run_patches_dir(ws, run_name) / "from_annotated.patch"
                    patch_file.write_text("# no annotated/source/ snapshot available\n")
            else:
                prev_src = ws / f"opt{iteration - 1}" / "source"
                if prev_src.exists():
                    patch_file = _run_patches_dir(ws, run_name) / f"from_opt{iteration - 1}.patch"
                    _generate_patch(prev_src, source_dest, patch_file)

        # ── Step 2: profile run ──────────────────────────────────────────────
        # Traces go directly into ws/<run_name>/traces/raw/ via run_name routing
        raw = _session_run_with_dftracer_impl(
            run_id=run_id,
            command=command,
            subfolder="build_ann",
            data_dir=data_dir,
            timeout=timeout,
            env_extra=env_extra,
            run_name=run_name,
        )
        profile_result = _json.loads(raw)
        if profile_result.get("status") != "ok":
            return _json.dumps({
                "status": "error",
                "message": f"Profile run failed at iteration {iteration}: "
                           + profile_result.get("message", ""),
                "profile": profile_result,
                "iteration": iteration,
            })
        # ── Step 3: split traces into per-iteration compact dir ──────────────
        # Reads from ws/<run_name>/traces/raw/, writes to ws/<run_name>/traces/compact/
        _session_split_traces_impl(run_id=run_id, app_name=app_name, run_name=run_name)
        profile_summary = {
            "returncode": profile_result.get("returncode"),
            "elapsed_s":  profile_result.get("elapsed_s"),
            "trace_files": [str(p) for p in iter_traces_dir.glob("*.pfw.gz")]
                            + [str(p) for p in iter_traces_dir.glob("*.pfw")],
        }

        # ── Step 3b: compare against previous iteration traces ───────────────
        comparison: Dict[str, Any] = {}
        if iteration > 0:
            prev_split = ws / f"opt{iteration - 1}" / "traces" / "compact"
            if prev_split.exists() and any(prev_split.glob("*.pfw.gz")):
                try:
                    cmp_raw = _dftracer_utils_comparator(
                        baseline=str(prev_split),
                        variant=str(iter_split_dir),
                        query='cat == "POSIX" OR cat == "STDIO" OR cat == "C_APP"',
                        group_by_dims="cat,name",
                        output_format="json",
                        threshold_pct=5.0,
                    )
                    cmp_result = (
                        _json.loads(cmp_raw["stdout"])
                        if cmp_raw["success"] and cmp_raw["stdout"].strip()
                        else {"error": cmp_raw["stderr"][:500] or "empty output",
                              "returncode": cmp_raw["returncode"]}
                    )
                    comparison = {
                        "baseline_iter": iteration - 1,
                        "variant_iter":  iteration,
                        "baseline_dir":  str(prev_split),
                        "variant_dir":   str(iter_split_dir),
                        "result":        cmp_result,
                    }
                    # Persist comparison to per-iteration dir
                    (iter_dir / "comparison.json").write_text(
                        _json.dumps(comparison, indent=2)
                    )
                except Exception as _cmp_err:
                    comparison = {"error": str(_cmp_err)}

        # ── Step 4: diagnose bottlenecks ────────────────────────────────────
        # Clear stale checkpoint so each iteration scores only its own traces
        _ckpt = ws / "dfanalyzer_checkpoint"
        if _ckpt.exists():
            import shutil as _sh2
            _sh2.rmtree(str(_ckpt))
        _ckpt.mkdir(exist_ok=True)
        # ML/DL workloads run BOTH dlio (dataloader/compute-specific metrics like
        # data_loader_ops_slope, compute_ops_slope) AND generic (auto-discovered
        # per-category layers, so nothing outside dlio's known layers is
        # silently left undiagnosed); non-DL uses posix alone. Same detection
        # logic as the main pipeline's diagnose step (session/detection.py).
        _frameworks = state.get("frameworks") or state.get("ml_frameworks_list") or []
        _preset = ",".join(_detect_analyzer_presets({"ml_frameworks_list": _frameworks}))
        raw = _session_diagnose_bottlenecks_impl(
            run_id=run_id, timeout=timeout, traces_dir=str(iter_split_dir),
            analyzer_preset=_preset,
        )
        diag_result = _json.loads(raw)
        current_bottlenecks: list = diag_result.get("bottlenecks", [])
        diag_summary = {
            "severity_counts": diag_result.get("severity_counts", {}),
            "bottleneck_count": len(current_bottlenecks),
        }

        # ── Step 5: system context ───────────────────────────────────────────
        sys_cfg_path = ws / "system_config.json"
        if sys_cfg_path.exists():
            try:
                sys_info = _json.loads(sys_cfg_path.read_text())
            except Exception:
                sys_info = {}
        else:
            raw_sys = _session_collect_system_info_impl(run_id=run_id)
            sys_info = _json.loads(raw_sys)
        sys_context = _build_sys_context(sys_info)

        # ── Step 6: literature search — top bottlenecks ─────────────────────
        # Bottlenecks are addressed in the canonical order I/O -> communication
        # -> memory -> compute (severity is only the tiebreaker within a
        # category), so a critical compute issue never jumps ahead of a
        # medium-severity I/O issue.
        ranked = sorted(current_bottlenecks, key=_category_sort_key)
        top5 = ranked[:5]

        # For deep-learning workloads, always evaluate two additional
        # dimensions regardless of severity ranking: (1) application
        # dataloader / epoch-time performance, and (2) filesystem bandwidth /
        # utilization for the storage the run is on.  If a matching
        # bottleneck exists but fell outside the top5, pull it in.
        is_dl_workload = bool(
            state.get("frameworks") or state.get("ml_frameworks_list")
            or any(
                any(dl_met in b.get("metric", "") for dl_met in _DL_ALWAYS_ON_METRICS["dataloader_epoch"])
                for b in current_bottlenecks
            )
        )
        if is_dl_workload:
            top5_metrics = {b.get("metric", "") for b in top5}
            for dim, metric_fragments in _DL_ALWAYS_ON_METRICS.items():
                if any(any(frag in m for frag in metric_fragments) for m in top5_metrics):
                    continue  # already represented
                candidate = next(
                    (b for b in ranked
                     if any(frag in b.get("metric", "") for frag in metric_fragments)),
                    None,
                )
                if candidate is not None:
                    top5.append({**candidate, "dl_dimension": dim})

        max_attempts = max(1, min(10, max_search_attempts))
        n_papers     = max(1, min(5, papers_per_query))
        seen_titles: set = set()
        literature: List[Dict[str, Any]] = []
        unsolved:   List[Dict[str, Any]] = []

        for bn in top5:
            metric      = bn.get("metric", "")
            description = bn.get("description", "")
            severity    = bn.get("severity", "")

            found_papers: List[Dict[str, Any]] = []
            queries_tried: List[str] = []

            # ── Agentic RAG: read Tier-2 project memory before searching ────
            # A citation already validated (improved/resolved a matching
            # bottleneck, here or in a prior session) is used directly,
            # skipping the live arXiv round-trip entirely. A record with
            # only failed attempts (negative confidence) is not surfaced,
            # so the loop naturally re-searches instead of repeating a
            # known-bad fix.
            mem_matches = _memory_retrieve(metric, sys_context, k=1)
            if mem_matches and mem_matches[0].get("successes", 0) > mem_matches[0].get("failures", 0):
                m = mem_matches[0]
                cite = m.get("citation", {})
                found_papers.append({
                    "title":     cite.get("title", m.get("strategy_title", "")),
                    "authors":   cite.get("authors", []),
                    "published": f"{cite.get('year','')}-01-01" if cite.get("year") else "",
                    "abstract":  f"Retrieved from project memory: used {m.get('uses',0)}x, "
                                 f"{m.get('successes',0)} success(es), {m.get('failures',0)} failure(s).",
                    "url":       cite.get("url", ""),
                    "query":     "<memory>",
                    "bottleneck": metric,
                    "source":    "memory",
                })
            else:
                queries = _bottleneck_search_queries(
                    metric=metric,
                    description=description,
                    sys_context=sys_context,
                    max_queries=max_attempts,
                )
                for q in queries:
                    queries_tried.append(q)
                    papers = _fetch_arxiv_papers(q, n=n_papers)
                    for p in papers:
                        title_key = p["title"].lower()[:80]
                        if title_key not in seen_titles:
                            seen_titles.add(title_key)
                            found_papers.append({**p, "query": q, "bottleneck": metric, "source": "arxiv"})
                    if found_papers:
                        break  # stop as soon as at least one paper is found
                # Record the attempt in Tier-2 memory (outcome unknown yet —
                # session_optimization_iteration's next call reflects on it).
                if found_papers:
                    top = found_papers[0]
                    _memory_write(
                        metric=metric,
                        sys_context=sys_context,
                        strategy_title=top.get("title", "")[:120],
                        citation={
                            "authors": top.get("authors", []),
                            "title":   top.get("title", ""),
                            "venue":   f"arXiv {(top.get('published') or '')[:4]}",
                            "year":    (top.get("published") or "")[:4],
                            "url":     top.get("url", ""),
                        },
                        source="searched",
                    )

            entry = {
                "bottleneck":    metric,
                "severity":      severity,
                "description":   description,
                "sys_context":   sys_context,
                "queries_tried": len(queries_tried),
                "last_query":    queries_tried[-1] if queries_tried else "",
                "papers":        found_papers,
            }
            if found_papers:
                literature.append(entry)
            else:
                unsolved.append({
                    "bottleneck":    metric,
                    "severity":      severity,
                    "description":   description,
                    "queries_tried": queries_tried,
                    "message":       (
                        f"No papers found for '{metric}' ({severity}) after "
                        f"{len(queries_tried)} search attempt(s) including "
                        f"system-context '{sys_context}'. "
                        "Manual expert guidance recommended."
                    ),
                })

        # ── Step 7: compare with previous iteration ──────────────────────────
        prev_bottlenecks: list = []
        if history:
            prev_bottlenecks = history[-1].get("bottlenecks", [])

        def _bn_key(b: dict) -> str:
            return f"{b.get('metric', '')}:{b.get('view', '')}"

        prev_keys = {_bn_key(b): b for b in prev_bottlenecks}
        curr_keys = {_bn_key(b): b for b in current_bottlenecks}

        improved   = []
        regressed  = []
        unchanged  = []
        new_issues = []

        for key, cb in curr_keys.items():
            if key not in prev_keys:
                new_issues.append(cb)
            else:
                pb = prev_keys[key]
                cs = _SEV.get(cb.get("severity", "trivial"), 0)
                ps = _SEV.get(pb.get("severity", "trivial"), 0)
                if cs < ps:
                    improved.append({"metric": key, "from": pb["severity"], "to": cb["severity"]})
                elif cs > ps:
                    regressed.append({"metric": key, "from": pb["severity"], "to": cb["severity"]})
                else:
                    unchanged.append(key)

        resolved = [k for k in prev_keys if k not in curr_keys]

        delta = {
            "improved":  improved,
            "regressed": regressed,
            "resolved":  resolved,
            "new":       new_issues,
            "unchanged": unchanged,
        }

        # ── Build recommendation ─────────────────────────────────────────────
        # ── Build per-bottleneck citations list ──────────────────────────────
        # Each entry: {metric, severity, papers: [{title, url, authors, published}]}
        citations: List[Dict[str, Any]] = []
        for lit_entry in literature:
            cite_papers = [
                {
                    "title":     p.get("title", ""),
                    "url":       p.get("url", ""),
                    "authors":   p.get("authors", []),
                    "published": p.get("published", ""),
                    "abstract":  p.get("abstract", "")[:300] + "…" if p.get("abstract") else "",
                    "query":     p.get("query", ""),
                }
                for p in lit_entry.get("papers", [])
            ]
            citations.append({
                "metric":   lit_entry["bottleneck"],
                "severity": lit_entry["severity"],
                "papers":   cite_papers,
            })

        def _cite_str(lit_entry: dict) -> str:
            """One-line citation: 'Title (Authors, Year) <url>'"""
            papers = lit_entry.get("papers", [])
            if not papers:
                return "(no papers found)"
            p = papers[0]
            year  = (p.get("published") or "")[:4]
            auth  = p.get("authors", [])
            first = auth[0].split()[-1] if auth else "Unknown"
            et_al = " et al." if len(auth) > 1 else ""
            return (
                f'"{p["title"][:70]}..." '
                f"- {first}{et_al}, {year} "
                f"<{p.get('url', '')}>"
            )

        n_papers = sum(len(e["papers"]) for e in literature)
        lit_summary = (
            f"{n_papers} paper(s) found across {len(literature)} bottleneck(s)"
        ) if literature else "no papers found"

        if not current_bottlenecks:
            recommendation = "No active bottlenecks — optimization complete."
        elif unsolved:
            names = ", ".join(u["bottleneck"] for u in unsolved)
            recommendation = (
                f"Literature search exhausted for: {names}. "
                "These require manual expert analysis — see 'unsolved' for the "
                "full list of attempted queries. "
            )
            if literature:
                top_lit = literature[0]
                recommendation += (
                    f"For remaining solvable bottlenecks, start with "
                    f"'{top_lit['bottleneck']}': {_cite_str(top_lit)}"
                )
        elif regressed:
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"{len(regressed)} metric(s) regressed — revert last change or "
                "investigate interaction effects.\n"
                f"Evidence ({lit_summary}):\n{cite_lines}"
            )
        elif improved or resolved:
            top = top5[0] if top5 else {}
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"Progress: {len(improved)} improved, {len(resolved)} resolved. "
                f"Top remaining: {top.get('metric','')} ({top.get('severity','')}).\n"
                f"Evidence ({lit_summary}):\n{cite_lines}\n"
                "Apply the suggested optimization and iterate."
            )
        else:
            top = top5[0] if top5 else {}
            cite_lines = "\n".join(f"  • [{c['metric']}] {_cite_str(c)}" for c in citations[:3])
            recommendation = (
                f"No change yet. Top bottleneck: {top.get('metric','')} "
                f"({top.get('severity','')}).\n"
                f"Evidence ({lit_summary}):\n{cite_lines}\n"
                "Apply the technique from the first cited paper and re-run."
            )

        # ── Persist iteration to history ─────────────────────────────────────
        entry = {
            "iteration":       iteration,
            "optimization":    optimization_applied,
            "build":           build_summary,
            "profile":         profile_summary,
            "diagnosis":       diag_summary,
            "system_context":  sys_context,
            "bottlenecks":     current_bottlenecks,
            "top5":            top5,
            "literature":      literature,
            "unsolved":        unsolved,
            "delta":           delta,
            "comparison":      comparison,
        }
        history.append(entry)
        _save_state(run_id, {"optimization_history": history, "step": "optimization_loop"})

        # ── Agentic RAG: reflect — write back whether the *previous*
        # iteration's cited fix actually worked, now that this iteration's
        # delta makes that observable. No-op on the baseline (iteration 0).
        memory_reflection = _memory_reflect(run_id, iteration=iteration) if iteration > 0 else \
            {"written": 0, "detail": "baseline iteration — nothing to reflect on yet"}

        # Save full literature results to workspace for reference
        lit_file = ws / f"optimization_literature_iter{iteration}.json"
        iter_summary = {
            "iteration":    iteration,
            "optimization": optimization_applied,
            "sys_context":  sys_context,
            "literature":   literature,
            "citations":    citations,
            "unsolved":     unsolved,
            "bottlenecks":  current_bottlenecks,
            "delta":        delta,
            "comparison":   comparison,
        }
        lit_file.write_text(_json.dumps(iter_summary, indent=2))
        # Mirror into per-iteration directory for comparison
        (iter_dir / "summary.json").write_text(_json.dumps(iter_summary, indent=2))
        if sys_cfg_path.exists():
            _sh.copy2(str(sys_cfg_path), str(iter_dir / "system_config.json"))

        return _ok(
            f"Iteration {iteration} complete — {len(current_bottlenecks)} active "
            f"bottleneck(s), {len(literature)} solved by literature, "
            f"{len(unsolved)} unsolved. " + recommendation,
            iteration=iteration,
            optimization=optimization_applied,
            build=build_summary,
            profile=profile_summary,
            diagnosis=diag_summary,
            system_context=sys_context,
            top5_bottlenecks=top5,
            literature=literature,
            unsolved=unsolved,
            delta=delta,
            literature_file=str(lit_file),
            iter_dir=str(iter_dir),
            citations=citations,
            recommendation=recommendation,
            memory_reflection=memory_reflection,
        )

    @mcp.tool()
    def session_generate_optimization_proposals(
        run_id: str,
        iteration: int = -1,
        levels: str = "123",
        metric: str = "time",
        max_proposals_per_level: int = 3,
        bottlenecks_json: Optional[str] = None,
    ) -> str:
        """Generate concrete, citation-backed optimization proposals from bottleneck diagnosis.

        Reads the bottleneck list and searched literature from a completed
        ``session_optimization_iteration`` call, maps each bottleneck to a set of
        concrete code / config / system changes using the three-level strategy
        tables (L1 application, L2 software/middleware, L3 system/filesystem),
        and attaches a verifiable citation (URL) to every proposal.

        Bottlenecks are processed in the canonical optimization order
        I/O -> communication -> memory -> compute (severity is the tiebreaker
        within a category), so proposal ``id`` ordering reflects that pipeline.

        Citation priority per proposal:
        1. Papers found by the arXiv / Semantic Scholar search in the latest iteration.
        2. Built-in reference matched to the bottleneck's category: I/O ->
           WisIO (Yildirim et al., ICS 2025) or Drishti (Bez et al., PDSW 2022);
           communication -> Thakur et al. (IJHPCA 2005); memory -> McCalpin
           STREAM (1995); compute -> Williams et al. Roofline (CACM 2009).
           The two always-on DL dimensions use Mohan et al. (VLDB 2021) for
           dataloader/epoch stalls and Lockwood et al. (SC 2018) for
           filesystem bandwidth/utilization.

        A proposal is OMITTED if neither a searched paper nor a built-in reference
        can be matched to the bottleneck + strategy combination.

        Args:
            run_id:                  Session identifier.
            iteration:               Which optimization iteration to read (-1 = latest).
            levels:                  Which levels to generate proposals for.
                                     Any combination of "1", "2", "3" (default "123").
            metric:                  Optimization objective used to rank proposals
                                     (time | bandwidth | iops | metadata_ops).
            max_proposals_per_level: Cap on proposals returned per level (default 3).

        Returns:
            JSON with keys:
            * ``status``     — ``"ok"`` or ``"error"``.
            * ``proposals``  — list of proposal dicts, each containing:
              ``id``, ``level``, ``title``, ``bottleneck``, ``severity``,
              ``change``, ``expected_delta``, ``risk``, ``citation``
              (sub-keys: authors, title, venue, year, url, finding),
              and for L2: ``delivery``, ``env_key``;
              for L3: ``privilege``, ``rollback``, ``side_effect``.
            * ``unsupported`` — bottlenecks for which no strategy was found.
            * ``citation_sources`` — breakdown of how many proposals used searched
              papers vs built-in references.

        Args:
            bottlenecks_json: Optional JSON list of bottleneck dicts, used when the
                run was launched externally (e.g. a `flux batch` job) and so has no
                ``optimization_history``. Falls back to
                ``<ws>/<run>/analysis/diagnosis.json`` when omitted.
        """
        import json as _json

        state = _load_state(run_id)
        history: list = state.get("optimization_history", [])

        if not history:
            # session_optimization_iteration assumes a locally-launched run. Runs
            # submitted externally (e.g. a two-phase `flux batch` job on 8 nodes)
            # never populate optimization_history, yet they have a perfectly good
            # diagnosis. Accept one directly rather than dead-ending the optimizer.
            fallback = _load_external_bottlenecks(run_id, bottlenecks_json)
            if fallback is None:
                return _err(
                    "No optimization iterations found. Either run "
                    "session_optimization_iteration first, or pass bottlenecks_json "
                    "(a JSON list of {bottleneck, severity, category, ...}), or write "
                    "<ws>/baseline/analysis/diagnosis.json."
                )
            bottlenecks, literature = fallback, []
        else:
            idx = iteration if iteration >= 0 else len(history) - 1
            if idx >= len(history):
                return _err(f"Iteration {iteration} not found (history has {len(history)} entries).")

            iter_entry  = history[idx]
            bottlenecks = iter_entry.get("bottlenecks", [])
            literature  = iter_entry.get("literature", [])

        # Build metric → papers lookup from the iteration's existing literature search
        searched: Dict[str, List[Dict[str, Any]]] = {}
        for lit_entry in literature:
            m = lit_entry.get("bottleneck", "")
            searched[m] = lit_entry.get("papers", [])

        want_l1 = "1" in levels
        want_l2 = "2" in levels
        want_l3 = "3" in levels

        all_proposals: List[Dict[str, Any]] = []
        all_unsupported: List[str] = []
        total_cs = 0
        total_cb = 0

        if want_l1:
            p1, cs1, cb1 = _gen_level_proposals(
                bottlenecks, _L1_STRATEGIES, "L1", searched,
                max_per_level=max_proposals_per_level,
            )
            all_proposals.extend(p1)
            total_cs += cs1
            total_cb += cb1

        if want_l2:
            p2, cs2, cb2 = _gen_level_proposals(
                bottlenecks, _L2_STRATEGIES, "L2", searched,
                max_per_level=max_proposals_per_level,
                extra_fields=["delivery", "env_key"],
            )
            all_proposals.extend(p2)
            total_cs += cs2
            total_cb += cb2

        if want_l3:
            p3, cs3, cb3 = _gen_level_proposals(
                bottlenecks, _L3_STRATEGIES, "L3", searched,
                max_per_level=max_proposals_per_level,
                extra_fields=["privilege", "rollback", "side_effect"],
            )
            all_proposals.extend(p3)
            total_cs += cs3
            total_cb += cb3

        # Collect bottlenecks not covered by any level
        _SEV = {"trivial": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
        covered = {p["bottleneck"] for p in all_proposals}
        for bn in bottlenecks:
            met = bn.get("metric", "")
            if _SEV.get(bn.get("severity", "trivial"), 0) >= 2 and met not in covered:
                all_unsupported.append(met)

        total = len(all_proposals)
        return _ok(
            f"{total} citation-backed proposal(s) across levels '{levels}' "
            f"for run {run_id} iteration {idx}. "
            f"Citations: {total_cs} from arXiv search, {total_cb} from built-in references. "
            f"Unsupported bottlenecks: {all_unsupported or 'none'}.",
            proposals=all_proposals,
            unsupported=all_unsupported,
            citation_sources={
                "searched_papers": total_cs,
                "builtin_references": total_cb,
            },
            iteration=idx,
        )
