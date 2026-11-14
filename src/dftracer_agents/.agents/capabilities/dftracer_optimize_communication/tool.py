"""dftracer_optimize_communication: the communication (MPI/collectives/network) component of the dftracer optimizer as data: the exhaustive dimension checklist and built-in L1/L2/L3 strategies (opt_communication_checklist), and a deterministic validator of a citation-backed findings table (dftracer_optimize_communication_check)."""
from __future__ import annotations



import json
import re
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.optimizations.strategies import _BUILTIN_REFS

# --------------------------------------------------------------------------- validator helpers (deterministic, offline)
_LEVELS = ("L1", "L2", "L3")
_SCOPE_LEVEL = {"workload": "L1", "software": "L2", "system": "L3"}
_ARXIV_RX = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
_DOI_RX = re.compile(r"\b10\.\d{4,9}/\S+")
_URL_RX = re.compile(r"https?://[^\s/]+\.[^\s]+")
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
# "Doing less" is not optimizing: fewer epochs/steps, a smaller input, skipped checkpoints or output.
_DO_LESS_RX = re.compile(
    r"\b(?:reduce|reducing|fewer|cut|cutting|skip|skipping|drop|dropping|disable|disabling|remove|removing|shrink|smaller|lower|lowering)\b"
    r"[^.;\n]{0,40}?\b(?:epochs?|iterations?|steps?|timesteps?|dataset|samples|problem size|input size|checkpoints?|"
    r"output files?|outputs?|validation|tolerance|resolution)\b", re.I)


def _add(checks: List[dict], name: str, ok: bool, detail: str, fix: str = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks: List[dict], **extra) -> str:
    if not checks:
        return json.dumps({"status": "inconclusive", "checks": [], "reason": "nothing to check: give at least one input", **extra})
    failed = [c["check"] for c in checks if not c["ok"]]
    return json.dumps({"status": "fail" if failed else "pass", "checks": checks, "failed": failed, **extra}, default=str)


def _parse_json_arg(text: str):
    """JSON text, or the path of a JSON file. Raises ValueError with a readable message."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty input")
    if t[0] not in "[{\"" and Path(t).is_file():
        t = Path(t).read_text()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from None


def _rows(data, keys=("rows", "proposals", "accepted", "findings", "papers", "citations", "entries")) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _citation_problem(cit) -> str:
    """'' when the citation is usable, else why it is not."""
    if isinstance(cit, dict):
        if not str(cit.get("title") or "").strip():
            return "citation object has no title"
        if not (cit.get("url") or cit.get("doi") or (cit.get("venue") and cit.get("year"))):
            return "citation object needs url, doi, or venue and year"
        return ""
    c = str(cit or "").strip()
    if not c:
        return "no citation"
    if c.lower().startswith("session:"):
        return "" if len(c) > len("session:") else "session: citation without a run id"
    if c.lower() in _BUILTIN_REFS:
        return ""
    if _URL_RX.search(c) or _DOI_RX.search(c) or _ARXIV_RX.search(c):
        return ""
    if _YEAR_RX.search(c) and len(c) >= 15:
        return ""
    return "not a URL, DOI, arXiv id, built-in reference key or 'Author et al., Venue Year'"


def _level_of(row: dict) -> str:
    lv = str(row.get("level") or row.get("scope") or "").strip()
    if lv.upper() in _LEVELS:
        return lv.upper()
    return _SCOPE_LEVEL.get(lv.lower(), "")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "change", "strategy", "description"))

# --------------------------------------------------------------------------- the communication dimension checklist (dftracer-communication-optimization)
_COMPONENT = 'communication'
_DIMENSIONS = [(1, 'L1', 'overlap communication with compute', 'non-blocking collectives launched early, gradient-ready hooks'), (2, 'L1', 'message aggregation', 'batch small messages without changing the logical topology'), (3, 'L2', 'collective algorithm selection', 'topology-aware algorithm choice, same data communicated'), (4, 'L2', 'eager/rendezvous protocol tuning', 'eager limit / rendezvous threshold'), (5, 'L2', 'transport/provider selection', 'libfabric/UCX provider, RDMA vs TCP: check what the fabric has'), (6, 'L3', 'topology-aware rank placement', '--map-by, rankfiles, co-locate communicating pairs'), (7, 'L3', 'network/interconnect tuning', 'NIC binding, QP/RDMA depth, congestion control: often admin-only'), (8, 'L3', 'collective-buffer sizing at the system level', 'cb_nodes/CRAY_CB_NODES_MULTIPLIER (Cray MPICH ignores cb_nodes)'), (9, '', 'compute/communication overlap', 'shared with the compute checklist: record once, cross-reference')]
_RULES = [('\\b(?:change|restructure|rewrite|replace)\\b[^.;]{0,40}\\b(?:communication pattern|topology|decomposition)\\b', "never change the app's actual communication pattern", 'keep what is communicated; tune algorithm, protocol, transport or placement instead'), ('\\bcb_nodes\\b(?![^.;]{0,60}CRAY_CB_NODES_MULTIPLIER)', 'Cray MPICH ignores cb_nodes directly', 'on Cray MPICH set CRAY_CB_NODES_MULTIPLIER (see software-mpi) or mark the row not applicable')]
_RULE_BYPASS = ''
_VERDICTS = {"applied": "Applied & measured", "applicable": "Applicable, not measured", "not_applicable": "Not applicable"}
_VERDICT_ALIASES = {"applied & measured": "applied", "applied": "applied", "applicable, not measured": "applicable",
                    "applicable": "applicable", "not applicable": "not_applicable", "not_applicable": "not_applicable",
                    "n/a": "not_applicable", "na": "not_applicable"}


def _strategies_for_component():
    from dftracer_agents.mcp_tools.tools.optimizations.strategies import (
        _L1_STRATEGIES, _L2_STRATEGIES, _L3_STRATEGIES, _metric_category, _CATEGORY_DEFAULT_CITE)
    out = {}
    for lv, table in (("L1", _L1_STRATEGIES), ("L2", _L2_STRATEGIES), ("L3", _L3_STRATEGIES)):
        for metric, strategies in table.items():
            if _metric_category(metric) == _COMPONENT:
                out.setdefault(metric, {})[lv] = [{k: s.get(k) for k in ("title", "change", "expected_delta", "risk", "builtin_cite")}
                                                   for s in strategies]
    return out, _CATEGORY_DEFAULT_CITE.get(_COMPONENT, "")


def _checklist(bottlenecks_json: str = "") -> dict:
    from dftracer_agents.mcp_tools.tools.optimizations.strategies import _metric_category, _BUILTIN_REFS
    strategies, cite = _strategies_for_component()
    mine, other = [], []
    if bottlenecks_json.strip():
        rows = _rows(_parse_json_arg(bottlenecks_json), keys=("bottlenecks", "rows"))
        for b in rows:
            metric = str((b or {}).get("metric", "")) if isinstance(b, dict) else str(b)
            (mine if _metric_category(metric) == _COMPONENT else other).append(b)
    return {
        "status": "ok", "component": _COMPONENT,
        "dimensions": [{"dimension": n, "layer": lv or "any", "name": name, "what": what} for n, lv, name, what in _DIMENSIONS],
        "verdicts": _VERDICTS,
        "row_fields": ["dimension", "verdict", "level", "change", "citation", "reason", "app_impact_pct", "system_impact_pct"],
        "strategies": strategies,
        "default_citation": {"key": cite, **(_BUILTIN_REFS.get(cite) or {})},
        "bottlenecks": mine, "other_component_bottlenecks": len(other),
    }


def _check_findings(findings_json: str) -> str:
    checks = []
    try:
        rows = _rows(_parse_json_arg(findings_json))
    except ValueError as e:
        _add(checks, "findings_parse", False, str(e), "pass a JSON list of rows (or {\"rows\": [...]}) as opt_communication_checklist row_fields name them")
        return _verdict(checks, component=_COMPONENT)
    rows = [r for r in rows if isinstance(r, dict)]
    _add(checks, "findings_present", bool(rows), f"{len(rows)} row(s)", "record one row per checklist dimension")
    dims = {n: (lv, name) for n, lv, name, _ in _DIMENSIONS}
    seen, bad_verdict, bad_level, layer_mismatch, uncited, no_reason, no_impact, do_less, rule_hits = set(), [], [], [], [], [], [], [], []
    for i, r in enumerate(rows, 1):
        try:
            d = int(r.get("dimension"))
        except (TypeError, ValueError):
            d = None
        v = _VERDICT_ALIASES.get(str(r.get("verdict", "")).strip().lower())
        if d in dims:
            seen.add(d)
        if not v:
            bad_verdict.append(i)
            continue
        lv = _level_of(r)
        if v != "not_applicable":
            if not lv:
                bad_level.append(i)
            elif d in dims and dims[d][0] and dims[d][0] != lv:
                layer_mismatch.append(f"row {i}: dimension {d} is {dims[d][0]}, row says {lv}")
            p = _citation_problem(r.get("citation"))
            if p:
                uncited.append(f"row {i}: {p}")
            text = _row_text(r)
            if _DO_LESS_RX.search(text):
                do_less.append(f"row {i}: {_DO_LESS_RX.search(text).group(0)!r}")
            for rx, msg, _fix in _RULES:
                if re.search(rx, text, re.I) and not (_RULE_BYPASS and r.get(_RULE_BYPASS) is True):
                    rule_hits.append(f"row {i}: {msg}")
        if v in ("applicable", "not_applicable") and not str(r.get("reason") or "").strip():
            no_reason.append(i)
        if v == "applied" and not (_num(r.get("app_impact_pct")) and _num(r.get("system_impact_pct"))):
            no_impact.append(i)
    missing = [n for n in dims if n not in seen]
    _add(checks, "every_dimension_has_a_verdict", not missing,
         f"missing dimensions {missing}" if missing else f"all {len(dims)} dimensions recorded",
         "add a row for each missing dimension: applied, applicable (with reason) or not_applicable (with reason, after the literature search)")
    _add(checks, "verdicts_valid", not bad_verdict, f"rows with an unknown verdict: {bad_verdict}" if bad_verdict else "ok",
         "verdict is applied, applicable or not_applicable")
    _add(checks, "level_is_L1_L2_L3", not bad_level, f"rows without a level L1/L2/L3: {bad_level}" if bad_level else "ok",
         "set level to L1 (application), L2 (software/middleware) or L3 (system)")
    _add(checks, "level_matches_dimension", not layer_mismatch, "; ".join(layer_mismatch) or "ok",
         "move the row to the dimension of its layer, or correct its level")
    _add(checks, "citation_backed", not uncited, "; ".join(uncited) or "every applied/applicable row is cited",
         "cite a paper (arXiv/DOI) > docs > web, or session:<run_id> for an in-house measurement")
    _add(checks, "reason_for_not_applied", not no_reason, f"rows without a reason: {no_reason}" if no_reason else "ok",
         "give the concrete technical reason (wrong FS type, admin-only, structurally inert, no literature found after searching)")
    _add(checks, "impact_fields", not no_impact, f"applied rows without numeric app_impact_pct/system_impact_pct: {no_impact}" if no_impact else "ok",
         "fill app_impact_pct and system_impact_pct (0 when not applicable, never missing)")
    _add(checks, "never_do_less", not do_less, "; ".join(do_less) or "ok",
         "doing less (fewer epochs/steps, smaller input, skipped output) is not an optimization: drop the row or replace it with a lever that keeps the work")
    _add(checks, "component_rules", not rule_hits, "; ".join(rule_hits) or "ok",
         "; ".join(sorted({f"{m}: {f}" for _rx, m, f in _RULES})))
    return _verdict(checks, component=_COMPONENT, dimensions=len(dims))


def register(mcp):
    """Register this capability's tools on *mcp*."""





    @mcp.tool()
    def opt_communication_checklist(bottlenecks_json: str = "") -> str:
        """Render the communication optimization dimension checklist (the dftracer-communication-optimization skill as data) to walk before proposing anything.

        Use at the start of every communication optimization pass: every dimension must get a verdict (applied, applicable,
        not_applicable), never only the top bottleneck's obvious fix. Pass the diagnoser's bottleneck list
        (JSON list, or {"bottlenecks": [...]}, or a diagnosis.json path) to also get the bottlenecks that belong to
        the communication component.

        Returns JSON with keys: status, component, dimensions (dimension, layer, name, what), verdicts, row_fields
        (what each findings row for dftracer_optimize_communication_check carries), strategies (built-in L1/L2/L3 strategies per
        communication metric), default_citation, bottlenecks, other_component_bottlenecks.
        """
        try:
            return json.dumps(_checklist(bottlenecks_json), default=str)
        except ValueError as e:
            return json.dumps({"status": "error", "error": f"bottlenecks_json: {e}"})

    @mcp.tool()
    def dftracer_optimize_communication_check(findings_json: str) -> str:
        """Deterministically validate a communication optimization findings table against the communication dimension checklist.

        Use after walking opt_communication_checklist and before handing the findings to the optimizer orchestrator.
        findings_json is a JSON list of rows (or {"rows": [...]}, or a path to such a file), each with dimension,
        verdict (applied | applicable | not_applicable), level (L1/L2/L3), change, citation, reason,
        app_impact_pct, system_impact_pct.

        Returns JSON with keys: status ("pass" | "fail" | "inconclusive"), checks (check, ok, detail, fix),
        failed, component, dimensions. Checks: every dimension has a verdict, verdicts are valid, applied or applicable
        rows carry level L1/L2/L3 matching the dimension and a usable citation, not-applied rows give a reason,
        applied rows carry numeric impact fields, no row is "doing less", and the communication-specific rules hold.
        """
        return _check_findings(findings_json)
