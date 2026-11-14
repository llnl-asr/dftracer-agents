"""dftracer_profiling: the dftracer pipeline profiles itself. Bind the MLflow profile to a session, bracket every step
(and every retry of it) with begin/end, read running totals, write the performance report to
<workspace>/performance/, and verify that report deterministically (every step closed, no laundered failure, nothing
superseded, the report and its data present). When the collector is down every tool says profiling: disabled."""
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.profiling import _disabled, _off
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _perf_dir, _ws
from dftracer_agents.profiling import client

_OK = {"ok", "success", "succeeded", "completed", "done"}


def _profile_verdict(perf: Path) -> Dict[str, Any]:
    """Rule on a session's performance/ directory as {check, ok, detail, fix} items."""
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    rep = perf / "performance_report.md"
    add("report", rep.is_file() and rep.stat().st_size > 0, "performance_report.md present" if rep.is_file() else "no performance_report.md",
        "call profile_report() a few seconds after the last step ends (the collector must be running)")
    summary = {}
    sp = perf / "summary.json"
    try:
        summary = json.loads(sp.read_text()) if sp.is_file() else {}
        add("summary", bool(summary), "summary.json parses" if summary else "no summary.json", "profile_report(flush=True) writes summary.json")
    except ValueError as e:
        add("summary", False, f"summary.json is not JSON: {e}"[:200], "re-run profile_report(flush=True)")
    steps = []
    bad_files = []
    for f in sorted((perf / "steps").glob("*.json")) if (perf / "steps").is_dir() else []:
        try:
            steps.append(json.loads(f.read_text()))
        except ValueError:
            bad_files.append(f.name)
    if not steps:
        steps = [s for s in summary.get("steps") or [] if s.get("key") != "main"]
    add("steps", bool(steps) and not bad_files, f"{len(steps)} step(s)" + (f", unreadable: {bad_files}" if bad_files else ""),
        "bracket every step with profile_step_begin/profile_step_end so steps/<n>-<step>.json exist")
    open_ = [s.get("key") for s in steps for a in s.get("attempts") or [] if a.get("end_ns") is None]
    add("closed", not open_, f"open: {open_}" if open_ else "every attempt closed",
        "close each step yourself with profile_step_end(step=<same string>, status=<real outcome>)")
    sup = sorted({s.get("key") for s in steps for a in s.get("attempts") or [] if a.get("status") == "superseded"})
    add("not_superseded", not sup, f"force-closed as superseded: {sup}" if sup else "no attempt superseded",
        "a step was left open when the next began (or parallel subagents shared the single open-step slot): end every step; "
        "for parallel fan-out the orchestrator brackets the whole fan-out once")
    laundered = sorted({s.get("key") for s in steps for a in s.get("attempts") or []
                        if str(a.get("status", "")).lower() in _OK and str(a.get("error") or "").strip()})
    add("honest_status", not laundered, f"ok with an error text: {laundered}" if laundered else "no ok attempt carries an error",
        "end a failed attempt with status failed/timeout/lint_error and the error, then reopen with the same step string")
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
    @mcp.tool()
    def profile_bind(run_id: str, app: str = "", system: str = "") -> str:
        """Attach the pipeline profile to a dftracer session. Call once, right
        after ``session_create`` or ``session_status`` resolves the run.

        Telemetry captured before this call is kept and attributed to the
        session — it is the planning and routing cost that led up to it, and it
        is part of what the run cost. Binding also creates the MLflow parent run
        and the session's ``performance/`` directory.

        Args:
            run_id: The dftracer session id, e.g. ``"ior/20260708_140322"``.
            app: Application name, recorded as an MLflow tag.
            system: System name (``tuolumne``, ...), recorded as an MLflow tag.

        Returns:
            JSON with the bound ``run_id``, the ``performance_dir`` where the
            report will be written, and the MLflow parent run id.
        """
        if not _ws(run_id).exists():
            return _err(f"No session workspace for {run_id!r}", run_id=run_id)
        tags = {k: v for k, v in (("app", app), ("system", system)) if v}
        res = client.bind(run_id, tags)
        if _disabled(res):
            return _off(f"Collector not running; {run_id} will not be profiled")
        if res.get("error"):
            return _err(res["error"], run_id=run_id)
        return _ok(f"Profile bound to {run_id}", **res)

    @mcp.tool()
    def profile_step_begin(step: str, agent: str = "", notes: str = "") -> str:
        """Open a pipeline step, starting its execution clock.

        **Calling this again with the same ``step`` records a RETRY** — the step
        gains a second attempt rather than the profile gaining a duplicate step.
        That is how the report distinguishes "the annotator took 400 s" from "the
        annotator took 400 s across three tries, two of which failed lint".

        Any other step still open is closed as ``superseded``, so a step agent
        that forgets ``profile_step_end`` cannot absorb the next step's cost.

        Args:
            step: Stable step identity. Use the plan's own heading verbatim,
                e.g. ``"STEP 3: dftracer-annotator"`` — the same string on a
                retry, or the retry will be recorded as a new step.
            agent: Subagent executing the step, e.g. ``"dftracer-annotator"``.
            notes: Free text (the smoke command, the file count) stored as an
                MLflow param on the step's run.

        Returns:
            JSON with ``step``, ``index``, and ``attempt`` (1 on the first try).
        """
        if not step.strip():
            return _err("step is required")
        res = client.step_begin(step, agent, notes)
        if _disabled(res):
            return _off(f"Collector not running; step {step!r} not timed")
        if res.get("error"):
            return _err(res["error"], step=step)
        n = res.get("attempt", 1)
        label = f"attempt {n}" if n > 1 else "first attempt"
        return _ok(f"Step {step!r} started ({label})", **res)

    @mcp.tool()
    def profile_step_end(status: str = "ok", step: str = "", error: str = "") -> str:
        """Close a step's current attempt and stop its execution clock.

        Args:
            status: Outcome of this attempt. ``ok`` (also ``success``,
                ``completed``) counts as a success; anything else — ``failed``,
                ``timeout``, ``lint_error`` — counts as a failed attempt and is
                surfaced in the report's Rework section. A step that will be
                retried should be ended with the reason it failed, then reopened
                with ``profile_step_begin`` using the same ``step``.
            step: Which step to close. Defaults to the most recent open one.
            error: Short failure reason. Shown verbatim in the report.

        Returns:
            JSON with the step's final ``status``, ``tries``, ``retries`` and
            ``exec_s`` (seconds spent inside attempts).
        """
        res = client.step_end(status, step, error)
        if _disabled(res):
            return _off("Collector not running; nothing to close")
        if res.get("error"):
            return _err(res["error"], step=step, status=status)
        return _ok(f"Step {res['step']!r} ended: {res['status']} "
                   f"({res['tries']} tries, {res['exec_s']}s)", **res)

    @mcp.tool()
    def profile_status() -> str:
        """Running totals: cost, tokens, per-step timing, tries and retries.

        Cheap — served from the collector's memory, no MLflow round-trip. Use it
        mid-pipeline to see what the run has cost so far and which steps are
        burning it.

        Returns:
            JSON with ``totals``, ``attempts``, a compact ``steps`` list, and the
            MLflow parent run id.
        """
        res = client.status()
        if _disabled(res):
            return _off("Collector not running")
        if res.get("error"):
            return _err(res["error"])
        t = res.get("totals", {})
        return _ok(f"${t.get('cost_usd', 0):.4f} · {t.get('tokens_total', 0):,} tokens · "
                   f"{res.get('attempts', {}).get('steps', 0)} steps", **res)

    @mcp.tool()
    def profile_report(run_id: str = "", flush: bool = True) -> str:
        """Write the final performance report and return it.

        Flushes the profile, so the report reflects every step that has ended.
        Events still buffered inside Claude Code (up to
        ``OTEL_LOGS_EXPORT_INTERVAL``, 5 s by default) may not be included; call
        this a few seconds after the last step ends for an exact total.

        Writes ``<workspace>/performance/performance_report.md``,
        ``summary.json``, and ``steps/<n>-<step>.json``.

        Args:
            run_id: Session to report on. Defaults to the bound one; passing a
                different one only changes where this tool *looks* for the file,
                it does not rebind the collector.
            flush: Force a flush first. Set False to read the last written report
                without touching MLflow.

        Returns:
            JSON with ``report_path`` and the report's markdown in ``report``.
        """
        if flush:
            res = client.flush()
            if _disabled(res):
                return _off("Collector not running; no report written")
            if res.get("error"):
                return _err(res["error"])

        rid = run_id or (client.status().get("run_id") or "")
        if not rid:
            return _err("No run_id bound and none given; call profile_bind first")

        path = _perf_dir(rid) / "performance_report.md"
        if not path.exists():
            return _err(f"No report at {path}", run_id=rid)
        return _ok(f"Performance report for {rid}", run_id=rid,
                   report_path=str(path), report=path.read_text())

    @mcp.tool()
    def dftracer_profiling_check(run_id: str = "", perf_dir: str = "") -> dict:
        """Validate a session's pipeline profile deterministically from what profile_report wrote. Give run_id (reads
        <workspace>/performance) or an absolute perf_dir. Checks: performance_report.md present; summary.json parses;
        step files present and readable; every attempt closed; no attempt force-closed as superseded (a step left open,
        or parallel subagents sharing the single open-step slot); no attempt marked ok while carrying an error text. Use
        after profile_report at the end of a pipeline. Returns {status: pass|fail|inconclusive, checks: [{check, ok,
        detail, fix}]}."""
        if run_id.strip():
            perf = _perf_dir(run_id)
        elif perf_dir:
            perf = Path(perf_dir)
        else:
            return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "give run_id or perf_dir",
                                                          "fix": "pass the session run_id"}]}
        if not perf.is_dir():
            return {"status": "inconclusive", "checks": [{"check": "performance_dir", "ok": False,
                                                          "detail": "unknown run or no performance/ directory",
                                                          "fix": "profile_bind(run_id) right after session_create, then profile_report() at the end"}]}
        return _profile_verdict(perf)

    @mcp.tool()
    def dftracer_profiling_selftest() -> dict:
        """Self-test dftracer_profiling_check on a performance/ fixture it writes to a temp dir: a report, summary.json
        and two closed step files must pass; an attempt superseded by the next step must fail not_superseded; an ok
        attempt carrying an error must fail honest_status; an open attempt must fail closed. Use to check the validator.
        Returns {status: pass|fail, detail}."""
        def step(i, key, attempts):
            return {"run_id": "app/1", "key": key, "index": i, "status": attempts[-1]["status"], "tries": len(attempts),
                    "attempts": attempts}

        good = [step(1, "STEP 1: setup", [{"number": 1, "status": "ok", "error": "", "start_ns": 1, "end_ns": 2}]),
                step(2, "STEP 2: build", [{"number": 1, "status": "failed", "error": "make: 2", "start_ns": 3, "end_ns": 4},
                                          {"number": 2, "status": "ok", "error": "", "start_ns": 5, "end_ns": 6}])]
        out = {}
        with tempfile.TemporaryDirectory() as d:
            for label, steps in (("good", good),
                                 ("superseded", [step(1, "STEP 1: setup", [{"number": 1, "status": "superseded", "error": "", "start_ns": 1, "end_ns": 2}])]),
                                 ("laundered", [step(1, "STEP 1: setup", [{"number": 1, "status": "ok", "error": "lint: 4 files", "start_ns": 1, "end_ns": 2}])]),
                                 ("open", [step(1, "STEP 1: setup", [{"number": 1, "status": "running", "error": "", "start_ns": 1, "end_ns": None}])])):
                perf = Path(d) / label
                (perf / "steps").mkdir(parents=True)
                (perf / "performance_report.md").write_text("# Performance\n")
                (perf / "summary.json").write_text(json.dumps({"run_id": "app/1", "steps": steps, "totals": {}}))
                for s in steps:
                    (perf / "steps" / f"{s['index']:02d}-s.json").write_text(json.dumps(s))
                v = dftracer_profiling_check(perf_dir=str(perf))
                out[label] = [c["check"] for c in v["checks"] if not c["ok"]]
        ok = out == {"good": [], "superseded": ["not_superseded"], "laundered": ["honest_status"], "open": ["closed"]}
        return {"status": "pass" if ok else "fail", "detail": json.dumps(out)}
