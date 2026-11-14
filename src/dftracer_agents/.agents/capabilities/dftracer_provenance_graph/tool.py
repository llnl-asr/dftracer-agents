"""dftracer_provenance_graph: provenance mode, graph stage. Build the provenance graph from the provenance traces (through
dftracer-utils' dftracer_view), write graph.json / graph.dot, and apply the health gate: events present, no dangling
hashes, every type described, every artifact type present, produced and rooted in a spec input, nothing truncated."""
import json
import shutil
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.provenance import (
    PROV_RUN,
    SPEC_REL,
    _load_spec,
    build_graph,
    graph_to_dot,
    view_prov_events,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _safe_session_path, _ws


def _gate(g: Dict[str, Any], spec: Dict[str, Any]) -> List[Dict[str, Any]]:
    """The provenance graph health gate as named checks, each with its repair."""
    s = g.get("summary") or {}
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    add("prov_events", (s.get("prov_events") or 0) > 0, f"{s.get('prov_events', 0)} provenance events",
        "the run lacked DFTRACER_INC_METADATA=1, dftracer was never initialized, or the activities never ran")
    add("dangling", (s.get("dangling_hashes") or 0) == 0, f"{s.get('dangling_hashes', 0)} dangling hashes {list(g.get('dangling') or [])[:5]}",
        "a process's trace is missing (fewer than 5 events: it was deleted; os._exit child without finalize) or metadata was disabled")
    und = list(s.get("types_undescribed") or [])
    add("types_described", not und, f"undescribed: {und}" if und else f"{s.get('types_described', 0)} types described",
        "the type registry was not installed or is stale: re-run provenance_install_helpers (descriptions come from the spec)")
    miss = list(s.get("artifact_types_missing") or [])
    add("artifacts_present", not miss, f"missing artifact types: {miss}" if miss else "every artifact type recorded",
        "annotation gap at the artifact writer: insert generated(<artifact>) after the write succeeds")
    add("artifacts_produced", (s.get("artifacts_without_producer") or 0) == 0, f"{s.get('artifacts_without_producer', 0)} artifact instance(s) without producer",
        "the artifact's writer activity is missing or records it as used, not generated")
    add("not_truncated", (s.get("truncated_activities") or 0) == 0, f"{s.get('truncated_activities', 0)} truncated activities",
        "list chunks were lost (PROV_CONT): keep entity lists per activity small, or check the trace for missing events")
    lineage = g.get("lineage") or {}
    arts = list((spec or {}).get("scientific_artifacts") or [])
    if arts:
        add("lineage", bool(lineage), f"{len(lineage)} artifact instance(s) with lineage",
            "no artifact entity reached the graph: pass artifact_types or fix the spec's scientific_artifacts")
    roles = {t.get("name"): t.get("role") for t in (spec or {}).get("entity_types") or [] if isinstance(t, dict)}
    if roles:
        ents = g.get("entities") or {}
        bad = sorted({(ents.get(r) or {}).get("type", r) for l in lineage.values() for r in l.get("roots") or []
                      if roles.get((ents.get(r) or {}).get("type")) != "input"})
        add("roots_are_inputs", not bad, f"roots that are not spec inputs: {bad}" if bad else "every root is a spec input",
            "a producer upstream is unannotated: insert the activity that generates these types, or agree with the user that they are inputs")
    return checks


def _event(rec: Dict[str, Any]) -> str:
    return json.dumps(rec, separators=(",", ":"))


def register(mcp):
    @mcp.tool()
    def provenance_extract_graph(run_id: str, trace_dir: str = f"{PROV_RUN}/traces",
                                 artifact_types: str = "", out_dir: str = f"{PROV_RUN}/graph",
                                 use_viewer: bool = True, viewer_bin: str = "") -> str:
        """Build the provenance graph from traces and check lineage completeness.

        Reads ``prov_entity:<hash>`` metadata events and ``PROV``/``PROV_CONT``
        activity events from every ``*.pfw``/``*.pfw.gz`` under ``trace_dir``
        (all ranks/processes are merged; hashes join across them). Writes
        ``graph.json`` (entities, activities, edges, lineage) and ``graph.dot``
        to ``out_dir`` and returns the summary.

        Health signals to act on:
          * ``prov_events == 0`` -> args dropped: run lacked ``DFTRACER_INC_METADATA=1``
            or activities never executed.
          * ``dangling_hashes`` -> an edge references an entity with no metadata
            event (metadata disabled, or a different process emitted it and its trace is missing).
          * ``isolated_entities`` -> registered but never used/generated.
          * ``artifacts_without_producer`` / ``artifact_types_missing`` -> the
            scientific artifact's lineage is not captured -- annotation gap.
          * ``truncated_activities`` -> list chunks lost.

        Args:
            artifact_types: comma-separated; defaults to spec.scientific_artifacts.
            use_viewer: extract events with dftracer-utils' ``dftracer_view`` (indexed,
                query-driven; scales to large traces). Falls back to a direct scan
                when the viewer is not found.
            viewer_bin: path to ``dftracer_view`` (e.g. ``<venv>/bin/dftracer_view``);
                default: first on PATH.
        """
        ws = _ws(run_id)
        try:
            tdir = _safe_session_path(ws, trace_dir)
            odir = _safe_session_path(ws, out_dir)
        except ValueError as e:
            return _err(str(e))
        if not tdir.exists():
            return _err(f"{tdir} does not exist")
        arts = [x.strip() for x in artifact_types.split(",") if x.strip()] or \
            list(_load_spec(ws).get("scientific_artifacts") or [])
        view = None
        exe = viewer_bin or shutil.which("dftracer_view") or ""
        if exe and use_viewer:
            view = view_prov_events(tdir, odir / "prov_events.jsonl", exe, odir / "index")
            if view["returncode"] != 0:
                return _err("dftracer_view failed", view=view)
            g = build_graph(odir / "prov_events.jsonl", arts)
        else:
            g = build_graph(tdir, arts)
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "graph.json").write_text(json.dumps(g, indent=1, default=str))
        (odir / "graph.dot").write_text(graph_to_dot(g))
        s = g["summary"]
        healthy = (s["prov_events"] > 0 and s["dangling_hashes"] == 0 and not s["artifact_types_missing"]
                   and s["artifacts_without_producer"] == 0 and s["truncated_activities"] == 0)
        sample = dict(list(g["lineage"].items())[:10])
        return _ok("graph built", run_id=run_id, healthy=healthy, artifact_types=arts,
                   extraction=("dftracer_view" if view else "direct-scan"), view=view,
                   graph_json=str((odir / "graph.json").relative_to(ws)),
                   graph_dot=str((odir / "graph.dot").relative_to(ws)),
                   summary=s, dangling=g["dangling"][:10], isolated=g["isolated"][:10],
                   lineage_sample=sample)

    @mcp.tool()
    def dftracer_provenance_graph_check(run_id: str = "", graph_json: str = "", spec_path: str = "") -> dict:
        """Apply the provenance graph health gate deterministically to a graph.json written by provenance_extract_graph.
        Give run_id (reads <WS>/provenance/graph/graph.json and <WS>/provenance/spec.yaml) or an absolute graph_json and
        optional spec_path. Checks: prov_events > 0, no dangling hashes, no undescribed type, no missing artifact type, no
        artifact instance without a producer, no truncated activity, artifact lineage present, and every lineage root is a
        spec input. Use after provenance_extract_graph, before reporting healthy. Returns {status: pass|fail|inconclusive,
        checks: [{check, ok, detail, fix}], summary}."""
        import yaml
        if run_id.strip():
            ws = _ws(run_id)
            gp, sp = ws / PROV_RUN / "graph" / "graph.json", ws / SPEC_REL
        elif graph_json:
            gp, sp = Path(graph_json), Path(spec_path) if spec_path else None
        else:
            return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "give run_id or graph_json",
                                                          "fix": "pass the session run_id after provenance_extract_graph"}]}
        if not gp.is_file():
            return {"status": "inconclusive", "checks": [{"check": "graph_present", "ok": False,
                                                          "detail": f"unknown run or no graph.json ({gp.name} missing)",
                                                          "fix": "run provenance_extract_graph(run_id) first"}]}
        try:
            g = json.loads(gp.read_text())
        except ValueError as e:
            return {"status": "fail", "checks": [{"check": "graph_json", "ok": False, "detail": f"not JSON: {e}"[:200],
                                                  "fix": "re-run provenance_extract_graph"}]}
        spec = {}
        if sp is not None and sp.is_file():
            spec = yaml.safe_load(sp.read_text()) or {}
        checks = _gate(g, spec)
        return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks, "summary": g.get("summary")}

    @mcp.tool()
    def dftracer_provenance_graph_selftest() -> dict:
        """Self-test the health gate on a tiny fixture it writes to a temp dir: provenance records (EH entities, ET type
        descriptions, one activity using a deck and generating a field) go through the real build_graph; the healthy graph
        must pass dftracer_provenance_graph_check, the same records without the deck's EH must fail on dangling, and a
        missing graph is inconclusive. Use to check the validator itself. Returns {status: pass|fail, detail}."""
        import yaml
        spec = {"scientific_artifacts": ["field"],
                "entity_types": [{"name": "deck", "role": "input"}, {"name": "field", "role": "output"}]}
        recs = [
            {"name": "EH", "cat": "dftracer", "pid": 1, "args": {"id": "a1", "type": "deck", "store": "3", "uri": "deck.in"}},
            {"name": "EH", "cat": "dftracer", "pid": 1, "args": {"id": "b2", "type": "field", "store": "3", "uri": "field.h5"}},
            {"name": "ET", "cat": "dftracer", "pid": 1, "args": {"type": "deck", "role": "1", "description": "The input deck."}},
            {"name": "ET", "cat": "dftracer", "pid": 1, "args": {"type": "field", "role": "2", "description": "The field snapshot."}},
            {"name": "simulate", "cat": "PROV", "pid": 1, "id": 7, "ts": 10, "dur": 5,
             "args": {"activity": "simulate", "relations": {"used": ["a1"], "generated": ["b2"]}}},
        ]
        with tempfile.TemporaryDirectory() as d:
            sp = Path(d) / "spec.yaml"
            sp.write_text(yaml.safe_dump(spec))
            out = {}
            for label, rows in (("healthy", recs), ("dangling", recs[1:])):
                ev = Path(d) / f"{label}.jsonl"
                ev.write_text("\n".join(_event(r) for r in rows) + "\n")
                gp = Path(d) / f"{label}_graph.json"
                gp.write_text(json.dumps(build_graph(ev, ["field"]), default=str))
                v = dftracer_provenance_graph_check(graph_json=str(gp), spec_path=str(sp))
                out[label] = (v["status"], [c["check"] for c in v["checks"] if not c["ok"]])
            missing = dftracer_provenance_graph_check(graph_json=str(Path(d) / "none.json"))["status"]
        ok = out["healthy"][0] == "pass" and out["dangling"][0] == "fail" and "dangling" in out["dangling"][1] and missing == "inconclusive"
        return {"status": "pass" if ok else "fail", "detail": f"healthy={out['healthy']} dangling={out['dangling']} missing={missing}"}
