"""dftracer_pipeline: the end-to-end dftracer annotation + trace pipeline (session_run_pipeline), the plan the
dftracer-pipeline-planner writes before any stage runs, and the routing of each stage to one executor agent.

Predictors: session_run_pipeline (runs steps 1-8, pauses for the annotation report, resumes 8.5-13) and
dftracer_pipeline_plan_template (renders the sectioned pipeline_plan.md skeleton, one `## STEP N: <agent>` per
stage, from the canonical stage table). Validator: dftracer_pipeline_check rules deterministically on a written
plan (every stage numbered in order with a known executor agent, report then privacy-guard last, DISPATCH ORDER
equal to the step headings, nothing left unfilled) and on a session_run_pipeline result (status, failed step,
step timing order).
"""
from __future__ import annotations

import difflib
import json
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.workspace import (
    _ws, _load_state, _save_state, _write_artifact_log,
    _ok, _err, _new_run_id, _create_run, _run, _workspaces_root, _derive_app_name,
)
from dftracer_agents.mcp_tools.tools.session.detection import _detect_info, _detect_analyzer_preset
from dftracer_agents.mcp_tools.tools.session.annotation import (
    _annotate_c_source, _annotate_python_source,
    _fix_dftracer_annotation_errors,
    _strip_mpi_launcher,
    _generate_annotation_report,
)
from dftracer_agents.mcp_tools.tools.session.build import (
    _patch_cmake, _patch_setup_py, _patch_pyproject,
    _patch_autotools_makefile, _find_c_entry_points,
    _find_python_entry_points, _guess_smoke_test,
)
from dftracer_agents.mcp_tools.tools.session.install import (
    _ensure_session_venv, _install_dftracer_pip_direct, _dftracer_utils_split,
)
from dftracer_agents.mcp_tools.tools.session.pipeline_tools import _timed_step, _finish_step


# --------------------------------------------------------------------------------------------- plan model
# The canonical stage table of the dftracer-pipeline-planner (perf mode) and of provenance mode. Each executor
# agent has the rank(s) it may take; a plan's steps must be in non-decreasing rank order.
PERF_STAGES = [
    ("System detection: launcher, filesystem, modules, allocation facts", "dftracer-system-detect", "level_1",
     "system name, queue/launcher, module state, filesystem assumptions"),
    ("Session setup: clone, detect, configure workspace", "dftracer-session-setup", "level_1",
     "repo URL, ref, build tool, HDF5/MPI needs, canonical paths"),
    ("Build original app", "dftracer-build-app", "level_1",
     "configure/build flags, failure summary, build artifacts"),
    ("Install/build dftracer - ONLY after the app build succeeded, against the dependencies it resolved",
     "dftracer-build-dftracer", "level_1",
     "the app build's compiler/MPI/venv/HDF5/ROCm versions; only the tracing layers the user asked for; install path"),
    ("Annotation scoping + annotation + validation", "dftracer-annotator", "level_2",
     "smoke-test command to scope files; language; exclude patterns; hot-loop functions to exclude"),
    ("Build annotated + smoke test", "dftracer-build-smoke", "level_3",
     "subfolder, DFTRACER_INIT mode, smoke command"),
    ("Best-case trace run + split", "dftracer-tracer", "level_1",
     "run command, data_dir, env_extra, allocation shape, run_name"),
    ("Analyze traces", "dftracer-analyzer", "level_3", "preset (posix vs dlio), which views, checkpoint dir"),
    ("Diagnose bottlenecks", "dftracer-diagnoser", "level_3", "checkpoint dir, ranked bottleneck list"),
    ("Optimization loop (L1/L2/L3 + proposals + compare)", "dftracer-optimizer", "level_4",
     "metric objective, max iterations, termination criteria"),
    ("Final report assembly + validation (MANDATORY)", "dftracer-report", "level_2",
     "run_id; session_final_report must return pdf.generated, completeness.ok and readme_check.ok all true"),
    ("Privacy scan/redact of everything persisted (MANDATORY, last)", "dftracer-privacy-guard", "level_1",
     "run_id; privacy_scan must include final_report/ and report clean"),
]
PROV_STAGES = [
    ("System detection", "dftracer-system-detect", "level_1", "system name, launcher, modules, filesystem"),
    ("Session setup", "dftracer-session-setup", "level_1", "repo URL, ref, build tool, canonical paths"),
    ("Build original app", "dftracer-build-app", "level_1", "configure/build flags, build artifacts"),
    ("Plain run of the original app (tracing disabled); outputs in <WS>/dataset/<run>/", "dftracer-tracer",
     "level_1", "run command + output dir"),
    ("Discover artifacts + data touch-points; write provenance/discovery.md", "dftracer-provenance-discover",
     "level_2", "returns questions for the user"),
    ("USER DECISION (pipeline pauses): artifact(s), entity types, granularity -> provenance_write_spec",
     "main-thread", "level_2", "AskUserQuestion; never skip"),
    ("Install dftracer (data-centric: no PAPI/variorum/GPU tracing)", "dftracer-build-dftracer", "level_1",
     "install path"),
    ("Trace lineage in code + annotate <WS>/provenance/source; provenance_validate passes",
     "dftracer-provenance-annotate", "level_2", "spec.yaml"),
    ("Build provenance tree", "dftracer-build-smoke", "level_3", "subfolder provenance"),
    ("Traced run with DFTRACER_INC_METADATA=1, traces -> <WS>/provenance/traces/", "dftracer-tracer", "level_1",
     "no service daemon"),
    ("Graph + health gate; loop to annotate on gaps", "dftracer-provenance-graph", "level_2", "health gate"),
    ("Final report", "dftracer-report", "level_2", "run_id"),
    ("Privacy scan/redact (last)", "dftracer-privacy-guard", "level_1", "run_id"),
]
_RANKS_PERF = {
    "dftracer-system-detect": [0], "dftracer-session-setup": [1], "dftracer-build-app": [2],
    "dftracer-build-system-cmake": [2], "dftracer-build-system-autotools": [2], "dftracer-build-system-meson": [2],
    "dftracer-build-system-pip": [2], "dftracer-build-dftracer": [3], "dftracer-annotator": [4],
    "dftracer-annotate-c": [4], "dftracer-annotate-cpp": [4], "dftracer-annotate-python": [4],
    "dftracer-validate-c": [4.5], "dftracer-validate-cpp": [4.5], "dftracer-validate-python": [4.5],
    "dftracer-build-smoke": [5], "dftracer-tracer": [6], "dftracer-analyzer": [7], "dftracer-diagnoser": [7.5],
    "dftracer-optimizer": [8], "dftracer-optimizer-io": [8], "dftracer-optimizer-compute": [8],
    "dftracer-optimizer-communication": [8], "dftracer-optimizer-memory": [8],
    "dftracer-report": [9], "dftracer-privacy-guard": [10],
}
_RANKS_PROV = {
    "dftracer-system-detect": [0], "dftracer-session-setup": [1], "dftracer-build-app": [2],
    "dftracer-build-system-cmake": [2], "dftracer-build-system-autotools": [2], "dftracer-build-system-meson": [2],
    "dftracer-build-system-pip": [2], "dftracer-tracer": [3, 8.5], "dftracer-provenance-discover": [4],
    "main-thread": [5], "dftracer-build-dftracer": [6], "dftracer-provenance-annotate": [7],
    "dftracer-build-smoke": [8], "dftracer-provenance-graph": [9], "dftracer-report": [10],
    "dftracer-privacy-guard": [11],
}
STEP_LABELS = ["step_1_clone", "step_2_detect", "step_3_configure", "step_4_build_install", "step_5_smoke_test",
               "step_6_copy_annotated", "step_7_patch_build", "step_8_annotate", "step_8_5_install_dftracer",
               "step_9_10_build_and_run", "step_11_split_traces", "step_12_analyze_traces", "step_13_diagnose"]
_STEP_FIX = {
    "1": "git clone failed: check the URL and ref (the tool retries without --branch, then checks out ref); a private "
         "repo needs an SSH key or credential helper.",
    "3": "configure failed: read artifacts/03_session_configure.log; pass the missing -D flags in extra_cmake_flags, "
         "or fix the autotools prerequisites (AC_PREREQ newer than the system autoconf).",
    "4": "make or make install failed: read the FIRST 'error:' in artifacts/04_session_build_install.log; fix the "
         "flags or the toolchain modules, then call again.",
    "8.5": "the dftracer prerelease install or dftracer-compose failed: read artifacts/06c_compose_dftracer.log; "
           "load the application's modules (same MPI/HDF5 as the app build) and retry; never install dftracer on "
           "guessed dependencies.",
    "9": "the annotated build failed after the automatic fix loop: correct the annotation in annotated/ with the "
         "annotation tools (clang_annotate_file with comp_overrides / exclusion), then session_build_annotated.",
    "10": "the smoke test under dftracer failed: if the error names DFTRACER symbols re-annotate and retry; "
          "otherwise it is a non-annotation failure - ask the user before continuing.",
}


def _fill(text: str) -> str:
    return f"<fill: {text}>"


def render_plan(run_id: str, mode: str = "perf", app: str = "", system: str = "", smoke_command: str = "",
                skip: Optional[List[str]] = None) -> Dict[str, Any]:
    """Render the pipeline_plan.md skeleton: Overview, one `## STEP N: <agent>` per stage, DISPATCH ORDER."""
    stages = PROV_STAGES if mode == "provenance" else PERF_STAGES
    skip = set(skip or [])
    for must in ("dftracer-report", "dftracer-privacy-guard"):
        skip.discard(must)
    rows = [s for s in stages if s[1] not in skip]
    lines = [f"# DFTracer Pipeline Plan — {run_id}", "", "## Overview", "",
             f"- run_id: {run_id}", f"- mode: {mode}",
             f"- app: {app or _fill('app name, URL and ref')}",
             f"- system: {system or _fill('system facts from system_detect: launcher, modules, filesystem')}",
             f"- smoke command: {smoke_command or _fill('the smoke-test command')}",
             f"- canonical paths: {_fill('from session_get_run_paths(run_id), never hand-built')}",
             f"- workloads/configs in focus: {_fill('the user answer to which workloads/configs to focus on')}",
             ""]
    order = []
    for n, (stage, agent, level, notes) in enumerate(rows):
        order.append(agent)
        lines += [f"## STEP {n}: {agent}", "", f"Stage: {stage}", f"Model level: {level}",
                  f"Inputs: {_fill(notes)}", f"Expected artifact: {_fill('what this step returns to the next')}",
                  f"Gotchas: {_fill('pitfalls from the skills for this app/system')}", ""]
    lines += ["## DISPATCH ORDER", "", ", ".join(order), ""]
    return {"status": "ok", "plan_md": "\n".join(lines), "dispatch_order": order, "mode": mode,
            "steps": len(order)}


def _chk(out: list, check: str, ok: bool, detail: str, fix: str = "") -> None:
    out.append({"check": check, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def check_plan(plan_md: str, run_id: str = "", mode: str = "auto") -> List[dict]:
    out: List[dict] = []
    text = plan_md or ""
    m = re.search(r"^# DFTracer Pipeline Plan\s*[—\-:]+\s*(\S+)", text, re.M)
    _chk(out, "plan_header", bool(m), m.group(0) if m else "no '# DFTracer Pipeline Plan — <run_id>' header",
         "start the plan with '# DFTracer Pipeline Plan — <run_id>' (dftracer_pipeline_plan_template renders it)")
    if run_id and m:
        _chk(out, "plan_run_id", m.group(1) == run_id, f"plan run_id {m.group(1)!r}, expected {run_id!r}",
             "write the plan for the session you were given; get run_id from session_status, never invent one")
    _chk(out, "overview", bool(re.search(r"^## Overview\s*$", text, re.M)), "## Overview section",
         "add '## Overview' with system facts, app, run scale, run_id and canonical paths")
    steps = [(int(a), b.strip()) for a, b in re.findall(r"^## STEP (\d+):\s*([A-Za-z0-9_.-]+)\s*$", text, re.M)]
    _chk(out, "has_steps", bool(steps), f"{len(steps)} '## STEP N: <agent>' heading(s)",
         "write one '## STEP N: <executor-agent>' section per stage")
    if not steps:
        return out
    if mode == "auto":
        mode = "provenance" if any(a.startswith("dftracer-provenance") for _, a in steps) else "perf"
    nums = [n for n, _ in steps]
    contiguous = nums[0] in (0, 1) and all(b == a + 1 for a, b in zip(nums, nums[1:]))
    _chk(out, "step_numbers", contiguous, f"step numbers {nums}",
         "number steps contiguously from 0 or 1 in execution order")
    ranks = _RANKS_PROV if mode == "provenance" else _RANKS_PERF
    unknown = [a for _, a in steps if a not in ranks]
    _chk(out, "known_executors", not unknown, f"unknown executor agent(s): {unknown}" if unknown else
         "every step names a known executor agent",
         "use the exact executor subagent name from the stage table (e.g. dftracer-build-app, dftracer-tracer)")
    cur, bad = -1.0, []
    for n, a in steps:
        if a not in ranks:
            continue
        nxt = [r for r in ranks[a] if r >= cur]
        if not nxt:
            bad.append(f"STEP {n}: {a}")
        else:
            cur = min(nxt)
    _chk(out, "stage_order", not bad, f"out of stage order: {bad}" if bad else f"stages in {mode} order",
         "reorder: system-detect, session-setup, build-app, build-dftracer (only after the app build), annotate, "
         "build-smoke, tracer, analyzer, diagnoser, optimizer, report, privacy-guard")
    agents = [a for _, a in steps]
    tail_ok = len(agents) >= 2 and agents[-2:] == ["dftracer-report", "dftracer-privacy-guard"]
    _chk(out, "report_then_privacy_last", tail_ok, f"last steps {agents[-2:]}",
         "stages dftracer-report then dftracer-privacy-guard are ALWAYS the last two steps, in every plan")
    if mode == "provenance":
        perf_only = [a for a in agents if a in ("dftracer-analyzer", "dftracer-diagnoser", "dftracer-optimizer")]
        _chk(out, "provenance_no_perf_stages", not perf_only, f"perf stages in a provenance plan: {perf_only}",
             "provenance mode replaces analyze/diagnose/optimize; drop them")
        _chk(out, "provenance_user_gate", "provenance_write_spec" in text,
             "user decision gate (provenance_write_spec) " + ("present" if "provenance_write_spec" in text else "missing"),
             "add the USER DECISION step: the pipeline pauses for the user to choose artifacts/entities, then "
             "provenance_write_spec")
    # sections: non-empty and filled
    bodies = re.split(r"^## ", text, flags=re.M)
    empty, unfilled = [], []
    for b in bodies[1:]:
        head, _, body = b.partition("\n")
        if not head.startswith("STEP "):
            continue
        if not body.strip():
            empty.append(head.strip())
        if re.search(r"<fill:|<self-contained instructions", body):
            unfilled.append(head.strip())
    _chk(out, "steps_self_contained", not empty, f"empty step sections: {empty}" if empty else
         "every step section has instructions",
         "each step agent starts from a COLD context: give tools, exact inputs, commands, expected artifacts")
    unfilled_ov = bool(re.search(r"<fill:", text.split("## STEP", 1)[0]))
    _chk(out, "no_unfilled_placeholders", not unfilled and not unfilled_ov,
         f"unfilled placeholders in: {unfilled + (['Overview'] if unfilled_ov else [])}" if (unfilled or unfilled_ov)
         else "no placeholders left",
         "replace every '<fill: ...>' with the real value (paths from session_get_run_paths, flags, commands)")
    dm = re.search(r"DISPATCH ORDER:?\s*(?:\n+)?(.*?)(?=^## |\Z)", text, re.M | re.S)
    disp = re.findall(r"(dftracer-[a-z0-9-]+|main-thread)", dm.group(1)) if dm else []
    _chk(out, "dispatch_order", bool(dm) and disp == agents,
         f"DISPATCH ORDER {disp} vs steps {agents}" if dm else "no DISPATCH ORDER",
         "end the plan with '## DISPATCH ORDER' listing the step agents in exactly the step order")
    return out


def check_result(result: Any) -> List[dict]:
    out: List[dict] = []
    raw = result if isinstance(result, str) else json.dumps(result)
    if "name 'smoke_cmd' is not defined" in raw or "name 'src' is not defined" in raw:
        _chk(out, "resume_path", False, "session_run_pipeline resume raised NameError (smoke_cmd/src)",
             "known defect of the resume path (annotation_confirmed=True): it uses smoke_cmd and src that only the "
             "first call defines. Continue with the stage tools (session_install_dftracer, session_build_annotated, "
             "session_run_with_dftracer, session_split_traces) or repair the tool; never hand-roll the steps")
        return out
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        _chk(out, "result_json", False, "result is not JSON", "pass the tool's JSON result verbatim")
        return out
    if not isinstance(data, dict):
        _chk(out, "result_json", False, "result is not a JSON object", "pass the tool's JSON result verbatim")
        return out
    st = data.get("status")
    _chk(out, "result_status", st == "ok", f"status={st!r}: {str(data.get('message', ''))[:160]}",
         _STEP_FIX.get(str(data.get("step")), "Workspace not found -> wrong run_id: use the run_id the first call "
                       "returned" if "Workspace not found" in str(data.get("message")) else
                       "read the message and the step's artifact log, fix the input, call again"))
    if st != "ok":
        return out
    _chk(out, "run_id", bool(data.get("run_id")), f"run_id={data.get('run_id')!r}",
         "the result must carry run_id; resume with annotation_confirmed=True and that run_id")
    if data.get("awaiting_confirmation"):
        rep = data.get("annotation_report") or {}
        _chk(out, "annotation_report", bool(rep), "annotation report present" if rep else "no annotation_report",
             "show the annotation report to the user and wait for confirmation before resuming")
    timings = data.get("step_timings") or []
    labels = [t.get("step_label") or t.get("step") for t in timings if isinstance(t, dict)]
    idx = [STEP_LABELS.index(l) for l in labels if l in STEP_LABELS]
    _chk(out, "step_order", idx == sorted(idx) and len(idx) == len(labels),
         f"step timings {labels}", "steps ran out of order or with unknown labels: rerun from a clean session")
    neg = [l for t, l in zip(timings, labels) if isinstance(t, dict) and (t.get("duration_s") or 0) < 0]
    _chk(out, "step_durations", not neg, f"negative durations: {neg}" if neg else "durations >= 0",
         "a step's duration is negative: the clock or the timing record is broken; rerun the step")
    return out


def verdict(checks: List[dict]) -> Dict[str, Any]:
    if not checks:
        return {"status": "inconclusive", "checks": [], "why": "nothing to check: pass plan_md and/or result"}
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
    @mcp.tool()
    def session_run_pipeline(
        url: str,
        ref: str = "main",
        smoke_test_command: Optional[str] = None,
        extra_cmake_flags: str = "",
        jobs: int = 4,
        run_id: Optional[str] = None,
        skip_annotation: bool = False,
        annotation_confirmed: bool = False,
        dftracer_ref: str = "develop",
    ) -> str:
        """
        Full dftracer annotation + smoke-test pipeline.

        Executes all steps in sequence and pauses after step 8 to show the
        user an annotation coverage report.  The user must confirm before the
        pipeline continues to build, run, and analyse.

        Normal flow:
          1.   Create session and clone source (run_id derived from url)
          2.   Detect language, build tool, and dftracer features
          3.   Configure build
          4.   Build and install
          5.   Run smoke test (with auto-detected command if not provided)
          6.   Copy source to annotated/
          7.   Patch build system for dftracer
          8.   Auto-annotate C/C++ and Python source
          >>>  PAUSE — show annotation report, ask user to confirm <<<
          8.5. Install dftracer into install_ann/ (C/C++) or venv (Python)
          9.   Build annotated source with dftracer (CMAKE_PREFIX_PATH set)
          10.  Run smoke test with dftracer (traces collected)
          11.  dftracer_split — compact traces
          12.  dftracer_info  — summarise traces

        To resume after confirming the annotation report:
          Call this tool again with the same run_id and annotation_confirmed=True.
          Steps 1–8 are skipped; the pipeline resumes from step 8.5.

        If a step fails the pipeline stops and reports which step failed.

        Args:
            url:                  Git URL to clone (also used to derive app name
                                  and run_id when annotation_confirmed=True).
            ref:                  Branch, tag, or commit (default: main).
            smoke_test_command:   Shell command to verify the build.
            extra_cmake_flags:    Extra cmake -D flags for both builds.
            jobs:                 Parallel make jobs.
            run_id:               Fixed RUN-ID.  Required when
                                  annotation_confirmed=True to identify the run.
            skip_annotation:      If True, stop after step 5 (original smoke test).
            annotation_confirmed: If True and run_id is set, skip steps 1–8 and
                                  resume from step 8.5 (dftracer install + build).
            dftracer_ref:         dftracer git tag/branch to install (default: v2.0.3).
        """
        report: Dict[str, Any] = {}
        step_timings: List[Dict[str, Any]] = []

        # ── Normal path (steps 1–8): clone, build, annotate, then PAUSE ───────
        if not (annotation_confirmed and run_id):
            rid, ws = _create_run(url, run_id)
            src = ws / "source"
            src.mkdir(exist_ok=True)

            # Step 1: clone
            _t1 = _timed_step("step_1_clone")
            clone_r = _run(
                ["git", "clone", "--depth", "1", "--branch", ref, url, str(src)],
                timeout=300,
            )
            if not clone_r["success"]:
                shutil.rmtree(src, ignore_errors=True)
                src.mkdir(exist_ok=True)
                clone_r = _run(["git", "clone", "--depth", "1", url, str(src)], timeout=300)
                if not clone_r["success"]:
                    return _err("Step 1 failed: git clone", step=1, **clone_r)
                _run(["git", "checkout", ref], cwd=src)
            _finish_step(_t1)
            step_timings.append(_t1)
            report["step_1_clone"] = {"status": "ok", "run_id": rid, **{k: v for k, v in _t1.items() if k != "step_label"}}
            _write_artifact_log(ws, 1, "session_create",
                                {"clone": clone_r, "run_id": rid, "url": url, "ref": ref}, rid)

            # Step 2: detect
            _t2 = _timed_step("step_2_detect")
            info = _detect_info(src)
            bt = info["build_tool"]
            _save_state(rid, {"run_id": rid, "url": url, "ref": ref,
                               "workspace": str(ws), "detection": info})
            _finish_step(_t2)
            step_timings.append(_t2)
            report["step_2_detect"] = {
                "status": "ok",
                "languages": info["languages"],
                "build_tool": bt,
                "features": info["features"],
                "dftracer_cmake_flags": info["dftracer_cmake_flags"],
                **{k: v for k, v in _t2.items() if k != "step_label"},
            }
            _write_artifact_log(ws, 2, "session_detect", report["step_2_detect"], rid)

            # Step 3: configure
            _t3 = _timed_step("step_3_configure")
            build = ws / "build"
            install = ws / "install"
            build.mkdir(exist_ok=True)
            install.mkdir(exist_ok=True)

            cmake_flags = [
                f"-DCMAKE_INSTALL_PREFIX={install}",
                "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
            ] + (extra_cmake_flags.split() if extra_cmake_flags else [])

            if bt == "cmake":
                cfg_r = _run(["cmake", "-S", str(src), "-B", str(build)] + cmake_flags, timeout=300)
            elif bt == "autotools":
                if (src / "configure.ac").exists() and not (src / "configure").exists():
                    _run(["autoreconf", "-fi"], cwd=src, timeout=120)
                cfg_r = _run([str(src / "configure"), f"--prefix={install}"], cwd=build, timeout=300)
            elif bt == "python":
                cfg_r = _run([sys.executable, "-m", "venv", str(install)], timeout=60)
                if cfg_r["success"]:
                    pip = install / "bin" / "pip"
                    cfg_r = _run([str(pip), "install", "-e", str(src)], timeout=300)
            else:
                cfg_r = {"success": False, "returncode": -1,
                          "stdout": "", "stderr": f"Unknown build tool: {bt}"}

            _finish_step(_t3)
            step_timings.append(_t3)
            report["step_3_configure"] = {**cfg_r, **{k: v for k, v in _t3.items() if k != "step_label"}}
            _write_artifact_log(ws, 3, "session_configure",
                                {"configure": cfg_r, "build_tool": bt}, rid)
            if not cfg_r["success"]:
                return _err("Step 3 failed: configure", step=3, report=report)

            # Step 4: build + install
            _t4 = _timed_step("step_4_build_install")
            if bt in {"cmake", "autotools", "make"}:
                bld_r = _run(["make", f"-j{jobs}"], cwd=build, timeout=600)
                report["step_4_build"] = bld_r
                if not bld_r["success"]:
                    _finish_step(_t4)
                    step_timings.append(_t4)
                    return _err("Step 4 failed: make", step=4, report=report)
                ins_r = _run(["make", "install"], cwd=build, timeout=300)
                report["step_4_install"] = ins_r
                if not ins_r["success"]:
                    _finish_step(_t4)
                    step_timings.append(_t4)
                    return _err("Step 4 failed: make install", step=4, report=report)
            else:
                report["step_4_build"] = {"status": "skipped (python)"}
            _finish_step(_t4)
            step_timings.append(_t4)
            report["step_4_timing"] = {k: v for k, v in _t4.items() if k != "step_label"}
            _write_artifact_log(ws, 4, "session_build_install", {
                k: v for k, v in report.items() if k.startswith("step_4")
            }, rid)

            # Step 5: original smoke test
            _t5 = _timed_step("step_5_smoke_test")
            smoke_cmd = smoke_test_command or _guess_smoke_test(src, bt, install)
            if smoke_cmd:
                sm_r = _run(["/bin/sh", "-c", smoke_cmd], cwd=build, timeout=300)
                report["step_5_smoke_test"] = {**sm_r, "command": smoke_cmd}
                if not sm_r["success"]:
                    report["step_5_smoke_test"]["warning"] = (
                        "Original smoke test failed — continuing to annotation phase"
                    )
            else:
                report["step_5_smoke_test"] = {"status": "no smoke test detected"}
            _finish_step(_t5)
            step_timings.append(_t5)
            report["step_5_smoke_test"].update({k: v for k, v in _t5.items() if k != "step_label"})
            _write_artifact_log(ws, 5, "session_run_smoke_test",
                                report["step_5_smoke_test"], rid)
            _save_state(rid, {"step": "original_build_done", "detection": info})

            if skip_annotation:
                return _ok(
                    "Pipeline complete (annotation skipped)",
                    run_id=rid, workspace=str(ws), report=report,
                )

            # Step 6: copy to annotated/
            _t6 = _timed_step("step_6_copy_annotated")
            ann = ws / "annotated"
            if ann.exists():
                shutil.rmtree(ann)
            shutil.copytree(src, ann, symlinks=True, ignore_dangling_symlinks=True)
            _finish_step(_t6)
            step_timings.append(_t6)
            report["step_6_copy_annotated"] = {"status": "ok", "path": str(ann), **{k: v for k, v in _t6.items() if k != "step_label"}}
            _write_artifact_log(ws, 6, "session_copy_annotated",
                                report["step_6_copy_annotated"], rid)

            # Step 7: patch build system
            _t7 = _timed_step("step_7_patch_build")
            patched: List[str] = []
            if bt == "cmake":
                cml = ann / "CMakeLists.txt"
                if cml.exists():
                    cml.write_text(_patch_cmake(cml))
                    patched.append("CMakeLists.txt")
            elif bt == "autotools":
                for mf in ann.glob("Makefile*"):
                    mf.write_text(_patch_autotools_makefile(mf))
                    patched.append(mf.name)
            elif bt == "python":
                for pname, pfn in (("setup.py", _patch_setup_py),
                                   ("pyproject.toml", _patch_pyproject)):
                    pp = ann / pname
                    if pp.exists():
                        pp.write_text(pfn(pp))
                        patched.append(pname)
            _finish_step(_t7)
            step_timings.append(_t7)
            report["step_7_patch_build"] = {"status": "ok", "patched": patched, **{k: v for k, v in _t7.items() if k != "step_label"}}
            _write_artifact_log(ws, 7, "session_patch_build",
                                report["step_7_patch_build"], rid)

            # Step 8: auto-annotate source
            _t8 = _timed_step("step_8_annotate")
            c_entries = {str(p) for p in _find_c_entry_points(ann)}
            py_entries = {str(p) for p in _find_python_entry_points(ann)}
            annotated: List[str] = []
            langs = info.get("languages", [])

            if "c" in langs or "cpp" in langs:
                for ext in ("*.c", "*.h", "*.cpp", "*.cxx", "*.cc", "*.hpp"):
                    for f in ann.rglob(ext):
                        try:
                            old = f.read_text(errors="ignore")
                            new = _annotate_c_source(old, f, is_entry=str(f) in c_entries)
                            if new != old:
                                f.write_text(new)
                                annotated.append(str(f.relative_to(ann)))
                        except OSError:
                            pass

            if "python" in langs:
                for f in ann.rglob("*.py"):
                    try:
                        old = f.read_text(errors="ignore")
                        new = _annotate_python_source(old, is_entry=str(f) in py_entries)
                        if new != old:
                            f.write_text(new)
                            annotated.append(str(f.relative_to(ann)))
                    except OSError:
                        pass

            ann_patch = ws / "annotation.patch"
            ann_patch_chunks: List[str] = []
            for f_rel in annotated:
                src_f = ws / "source" / f_rel
                ann_f = ann / f_rel
                if src_f.exists() and ann_f.exists():
                    ann_patch_chunks.append("".join(difflib.unified_diff(
                        src_f.read_text(errors="ignore").splitlines(keepends=True),
                        ann_f.read_text(errors="ignore").splitlines(keepends=True),
                        fromfile=f"a/{f_rel}", tofile=f"b/{f_rel}",
                    )))
            ann_patch.write_text("".join(ann_patch_chunks))

            _finish_step(_t8)
            step_timings.append(_t8)
            report["step_8_annotate"] = {
                "status": "ok",
                "files_annotated": len(annotated),
                "annotated": annotated,
                "patch_file": str(ann_patch),
                **{k: v for k, v in _t8.items() if k != "step_label"},
            }
            _write_artifact_log(ws, 8, "session_annotate_source",
                                report["step_8_annotate"], rid)
            _save_state(rid, {"step": "annotation_done", "step_timings_phase1": step_timings})

            # ── PAUSE: show coverage report and ask user to confirm ────────────
            ann_report = _generate_annotation_report(ws, rid)
            s = ann_report.get("summary", {})
            annotatable = s.get("total_functions", 0) - s.get("skipped", 0)
            return _ok(
                f"Step 8 complete — annotation done. "
                f"{s.get('annotated', '?')}/{annotatable} functions annotated "
                f"({s.get('coverage_pct', '?')}% coverage, "
                f"{s.get('relevant_files', '?')} file(s) modified). "
                f"Review the annotation_report below. "
                f"If satisfied, call session_run_pipeline again with "
                f"annotation_confirmed=True and run_id='{rid}' to continue "
                f"to steps 8.5–12 (dftracer install, build, trace, analyse). "
                f"To add missing annotations first, use session_read_file / "
                f"session_write_file on the run_id='{rid}' workspace, then confirm.",
                awaiting_confirmation=True,
                annotation_report=ann_report,
                run_id=rid,
                workspace=str(ws),
                step_reports=report,
                step_timings=step_timings,
            )
        # ── end of normal path (always returns above) ─────────────────────────

        # ── Resume path: annotation_confirmed=True — load state, run 8.5–12 ───
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Workspace not found for run_id: {run_id}")
        state = _load_state(run_id)
        rid = run_id
        info = state.get("detection", {})
        bt = info.get("build_tool", "unknown")
        ann = ws / "annotated"
        install = ws / "install"
        if not ann.exists():
            return _err(
                "annotated/ directory not found — complete steps 1–8 first "
                "(call session_run_pipeline without annotation_confirmed=True)",
                run_id=rid,
            )
        report["resumed_from"] = "step_8_5"
        step_timings = list(_load_state(run_id).get("step_timings_phase1", []))

        # --- Step 8.5: create session venv + install dftracer ---
        _t85 = _timed_step("step_8_5_install_dftracer")
        build_ann = ws / "build_ann"
        install_ann = ws / "install_ann"
        build_ann.mkdir(exist_ok=True)
        install_ann.mkdir(exist_ok=True)

        # Create an isolated venv inside the workspace so dftracer and its
        # dependencies do not pollute or reuse the MCP server's environment.
        try:
            venv_python = _ensure_session_venv(ws)
        except RuntimeError as exc:
            _finish_step(_t85)
            return _err(f"Step 8.5 failed: session venv creation: {exc}",
                        step="8.5", report=report)
        _save_state(rid, {"session_venv_python": str(venv_python)})

        # The dftracer prerelease with its prebuilt modules, then dftracer-compose links the ones detection
        # asks for (with the application's modules loaded), building from source only a variant that is not
        # prebuilt -- as session_install_dftracer does by default.
        from dftracer_agents.mcp_tools.tools.session.install import _install_dftracer_prerelease_wheel, _compose_dftracer, _ensure_session_env_script
        _feats = info.get("features", {}) or {}
        dft_r = _install_dftracer_prerelease_wheel(
            python_exe=str(venv_python), ws=ws, run_id=rid, dftracer_ref=dftracer_ref, with_modules=True)
        _composed: Dict[str, Any] = {}
        if dft_r["success"]:
            _composed = _compose_dftracer(
                str(venv_python), ws, [m for m in ("mpi", "hdf5", "papi", "hip", "hwloc", "cuda") if _feats.get(m)],
                jobs=jobs, env_script=_ensure_session_env_script(ws, ws / "source"))
            dft_r["steps"]["compose"] = {k: _composed.get(k) for k in ("success", "stdout", "stderr")}
            dft_r["success"] = bool(_composed.get("success")) and not _composed.get("missing")
        _finish_step(_t85)
        step_timings.append(_t85)
        report["step_8_5_install_dftracer"] = {
            "ref": dftracer_ref,
            "venv": str(ws / "venv"),
            "steps": dft_r["steps"],
            "success": dft_r["success"],
            "compose": {k: _composed.get(k) for k in ("asked", "linked", "absent", "missing", "script")},
            **{k: v for k, v in _t85.items() if k != "step_label"},
        }
        _write_artifact_log(ws, 9, "session_install_dftracer", report["step_8_5_install_dftracer"], rid)
        if not dft_r["success"]:
            return _err("Step 8.5 failed: dftracer prerelease install or dftracer-compose "
                        f"({(_composed or {}).get('error') or (dft_r.get('error') or 'see artifacts/06c_compose_dftracer.log')})",
                        step="8.5", report=report)
        dft_prefix = str(install_ann)

        _save_state(rid, {"dftracer_install_prefix": dft_prefix})

        # --- Steps 9-10: build annotated + run with dftracer (retry loop) ---
        # Automatically fixes dftracer annotation errors and retries up to
        # MAX_ANNOTATION_RETRIES times before giving up.
        _t910 = _timed_step("step_9_10_build_and_run")
        traces_dir = ws / "traces"
        traces_dir.mkdir(exist_ok=True)
        dftracer_env = {
            "DFTRACER_ENABLE": "1",
            "DFTRACER_INC_METADATA": "1",
            "DFTRACER_LOG_FILE": str(traces_dir / "trace"),
            "DFTRACER_DATA_DIR": str(src),
            "DFTRACER_INIT": "FUNCTION",
        }
        ann_smoke_cwd = build_ann if build_ann.exists() else ann
        MAX_ANNOTATION_RETRIES = 3
        build_ok = False
        run_ok = not bool(smoke_cmd)  # trivially ok if there is no smoke command

        for attempt in range(1, MAX_ANNOTATION_RETRIES + 1):
            sfx = f"_attempt{attempt}"
            build_step: Dict[str, Any] = {"attempt": attempt}

            # Wipe the build dir on retries to avoid stale object files
            if attempt > 1:
                shutil.rmtree(build_ann, ignore_errors=True)
                build_ann.mkdir(exist_ok=True)

            # ---- build ----
            if bt == "cmake":
                ann_flags = [
                    f"-DCMAKE_INSTALL_PREFIX={install_ann}",
                    "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
                ]
                if dft_prefix:
                    ann_flags.append(f"-DCMAKE_PREFIX_PATH={dft_prefix}")
                ann_flags += (extra_cmake_flags.split() if extra_cmake_flags else [])
                r_ac = _run(["cmake", "-S", str(ann), "-B", str(build_ann)] + ann_flags, timeout=300)
                build_step["configure"] = r_ac
                if r_ac["success"]:
                    r_ab = _run(["make", f"-j{jobs}"], cwd=build_ann, timeout=600)
                    build_step["build"] = r_ab
                    if r_ab["success"]:
                        _run(["make", "install"], cwd=build_ann, timeout=300)
                        build_ok = True

            elif bt == "autotools":
                if (ann / "configure.ac").exists() and not (ann / "configure").exists():
                    _run(["autoreconf", "-fi"], cwd=ann, timeout=120)
                env_ann: Dict[str, str] = {}
                if dft_prefix:
                    env_ann["PKG_CONFIG_PATH"] = f"{dft_prefix}/lib/pkgconfig"
                    env_ann["CPPFLAGS"] = f"-I{dft_prefix}/include"
                    env_ann["LDFLAGS"] = f"-L{dft_prefix}/lib -Wl,-rpath,{dft_prefix}/lib"
                r_ac = _run(
                    [str(ann / "configure"), f"--prefix={install_ann}"],
                    cwd=build_ann, env=env_ann or None, timeout=300,
                )
                build_step["configure"] = r_ac
                if r_ac["success"]:
                    r_ab = _run(["make", f"-j{jobs}"], cwd=build_ann,
                                env=env_ann or None, timeout=600)
                    build_step["build"] = r_ab
                    if r_ab["success"]:
                        _run(["make", "install"], cwd=build_ann, timeout=300)
                        build_ok = True

            elif bt == "python":
                pip = ws / "install" / "bin" / "pip"
                if not pip.exists():
                    pip = Path(sys.executable).parent / "pip"
                r_ab = _run([str(pip), "install", "-e", str(ann)], timeout=300)
                build_step["build"] = r_ab
                build_ok = r_ab["success"]

            report[f"step_9{sfx}"] = build_step
            _write_artifact_log(ws, 10, f"session_build_annotated{sfx}", build_step, rid)

            if not build_ok:
                # Collect all stderr from this attempt
                build_stderr = "\n".join(
                    v.get("stderr", "") for v in build_step.values()
                    if isinstance(v, dict)
                )
                fixed = _fix_dftracer_annotation_errors(ann, build_stderr)
                report[f"step_9_fix{sfx}"] = {
                    "fixed_files": fixed,
                    "stderr_excerpt": build_stderr[:500],
                }
                if fixed and attempt < MAX_ANNOTATION_RETRIES:
                    # Write fix log and try again
                    _write_artifact_log(ws, 10, f"session_build_fix{sfx}",
                                        report[f"step_9_fix{sfx}"], rid)
                    continue
                return _err(
                    f"Step 9 failed after {attempt} attempt(s): build annotated",
                    step=9, attempts=attempt,
                    fixed_files=fixed,
                    hint=(
                        "Automated fix was unable to resolve all errors. "
                        "Use session_read_file + session_write_file to manually "
                        "correct the annotation in annotated/, then call "
                        "session_build_annotated to rebuild."
                    ),
                    report=report,
                )

            # ---- run with dftracer ----
            if smoke_cmd:
                # Clear stale traces from previous attempts
                for tf in traces_dir.glob("*.pfw*"):
                    tf.unlink()

                sm2_r = _run(
                    ["/bin/sh", "-c", smoke_cmd],
                    cwd=ann_smoke_cwd, env=dftracer_env, timeout=300,
                )
                report[f"step_10{sfx}"] = {**sm2_r, "command": smoke_cmd, "attempt": attempt}
                _write_artifact_log(ws, 11, f"session_run_with_dftracer{sfx}",
                                    report[f"step_10{sfx}"], rid)
                run_ok = sm2_r["success"]

                if run_ok:
                    break  # both build and run succeeded

                # Run failed — try annotation fix and rebuild
                run_stderr = sm2_r.get("stderr", "")
                fixed = _fix_dftracer_annotation_errors(ann, run_stderr)
                report[f"step_10_fix{sfx}"] = {
                    "fixed_files": fixed,
                    "stderr_excerpt": run_stderr[:500],
                }
                if fixed and attempt < MAX_ANNOTATION_RETRIES:
                    _write_artifact_log(ws, 11, f"session_run_fix{sfx}",
                                        report[f"step_10_fix{sfx}"], rid)
                    build_ok = False  # force rebuild on next iteration
                    continue
                # No fixable dftracer issue — stop retrying
                return _err(
                    f"Step 10 failed after {attempt} attempt(s): smoke test with dftracer",
                    step=10, attempts=attempt,
                    hint=(
                        "Use session_read_file + session_write_file to manually "
                        "fix annotation in annotated/, then call "
                        "session_build_annotated and session_run_with_dftracer."
                    ),
                    report=report,
                )
            else:
                report["step_10_smoke_with_dftracer"] = {"status": "no smoke command provided", "attempt": attempt}
                _write_artifact_log(ws, 11, f"session_run_with_dftracer{sfx}",
                                    report["step_10_smoke_with_dftracer"], rid)
                break  # no smoke test — build alone is enough

        # Promote the last successful attempt's data to canonical report keys
        for k in list(report.keys()):
            if k.startswith("step_9_attempt") or k.startswith("step_10_attempt"):
                base = "step_9_build_ann" if "step_9" in k else "step_10_smoke_with_dftracer"
                report.setdefault(base, report[k])

        _finish_step(_t910)
        step_timings.append(_t910)

        _save_state(rid, {"step": "annotated_built"})
        # Write final canonical logs with fixed step numbers
        _write_artifact_log(ws, 10, "session_build_annotated",
                             report.get("step_9_build_ann", {}), rid)
        _write_artifact_log(ws, 11, "session_run_with_dftracer",
                             report.get("step_10_smoke_with_dftracer", {}), rid)

        # --- Step 11: split traces (via dftracer-utils MCP service) ---
        _t11 = _timed_step("step_11_split_traces")
        traces_split = ws / "traces_split"
        traces_split.mkdir(exist_ok=True)
        trace_files = list(traces_dir.glob("*.pfw")) + list(traces_dir.glob("*.pfw.gz"))

        if trace_files:
            sp_r = _dftracer_utils_split(
                directory=str(traces_dir),
                output_dir=str(traces_split),
                app_name=rid,
            )
            report["step_11_split"] = sp_r
            if not sp_r["success"]:
                report["step_11_split"]["warning"] = "dftracer_split (utils service) failed — proceeding"
        else:
            report["step_11_split"] = {"status": "no trace files found"}
            traces_split = traces_dir  # fall back
        _finish_step(_t11)
        step_timings.append(_t11)
        report["step_11_split"].update({k: v for k, v in _t11.items() if k != "step_label"})
        _write_artifact_log(ws, 12, "session_split_traces", report["step_11_split"], rid)

        # --- Step 12: analyze traces ---
        _t12 = _timed_step("step_12_analyze_traces")
        idx_dir = traces_split / "idx"
        idx_dir.mkdir(exist_ok=True)
        an_r = _run(
            [
                "dftracer_info",
                "-d", str(traces_split),
                "--query", "summary",
                "--index-dir", str(idx_dir),
            ],
            timeout=600,
        )
        _finish_step(_t12)
        step_timings.append(_t12)
        report["step_12_analyze"] = {**an_r, **{k: v for k, v in _t12.items() if k != "step_label"}}
        _write_artifact_log(ws, 13, "session_analyze_traces", an_r, rid)

        # --- Step 13: diagnose bottlenecks (dfanalyzer checkpoint → dfdiagnoser) ---
        _t13 = _timed_step("step_13_diagnose")
        checkpoint_dir = ws / "dfanalyzer_checkpoint"
        diagnosis_dir  = ws / "diagnosis"
        scored_dir     = diagnosis_dir / "scored"
        checkpoint_dir.mkdir(exist_ok=True)
        diagnosis_dir.mkdir(exist_ok=True)
        scored_dir.mkdir(exist_ok=True)

        diag_phases: Dict[str, Any] = {}

        # Choose dfanalyzer preset: dlio for deep-learning workloads, posix otherwise.
        analyzer_preset = _detect_analyzer_preset(info)

        # Phase 13a: dfanalyzer with checkpoint output
        ana13_r = _run(
            [
                "dfanalyzer",
                f"trace_path={traces_split}",
                "analyzer.checkpoint=True",
                f"analyzer.checkpoint_dir={checkpoint_dir}",
                f"analyzer/preset={analyzer_preset}",
                "view_types=[time_range]",
            ],
            timeout=600,
        )
        diag_phases["dfanalyzer"] = ana13_r
        if not ana13_r["success"]:
            report["step_13_diagnose"] = {
                "status": "warning",
                "message": "dfanalyzer failed — bottleneck diagnosis skipped",
                "stderr": ana13_r.get("stderr", ""),
                "hint": "Install dfanalyzer: pip install dfanalyzer-utils",
                "phases": diag_phases,
            }
        else:
            flat_views = list(checkpoint_dir.glob("_flat_view_*.parquet"))
            if not flat_views:
                report["step_13_diagnose"] = {
                    "status": "warning",
                    "message": "dfanalyzer produced no _flat_view_*.parquet — bottleneck diagnosis skipped",
                    "phases": diag_phases,
                }
            else:
                # Phase 13b: dfdiagnoser (Python API → CLI fallback)
                diag_r: Optional[Dict[str, Any]] = None
                try:
                    from dfdiagnoser.diagnoser import Diagnoser   # type: ignore
                    from dfdiagnoser.output import FileOutput     # type: ignore
                    _dg = Diagnoser()
                    _res = _dg.diagnose_checkpoint(checkpoint_dir=str(checkpoint_dir))
                    FileOutput(output_dir=str(scored_dir), output_format="json").handle_result(_res)
                    diag_r = {
                        "returncode": 0,
                        "stdout": f"Scored {len(_res.scored_flat_views)} view(s) via Python API",
                        "stderr": "",
                        "success": True,
                    }
                except ImportError:
                    diag_r = _run(
                        [
                            "dfdiagnoser",
                            "input=checkpoint",
                            f"input.checkpoint_dir={checkpoint_dir}",
                            "output=file",
                            f"output.output_dir={scored_dir}",
                            "output.output_format=json",
                        ],
                        timeout=600,
                    )
                    if not diag_r["success"] and "not found" in diag_r.get("stderr", "").lower():
                        diag_r["stderr"] += " — install with: pip install dfdiagnoser"
                except Exception as exc:
                    diag_r = {"returncode": -1, "stdout": "", "stderr": str(exc), "success": False}
                diag_phases["dfdiagnoser"] = diag_r

                # Parse scored outputs
                _score_labels = {1: "trivial", 2: "low", 3: "medium", 4: "high", 5: "critical"}
                severity_counts: Dict[str, int] = {
                    "critical": 0, "high": 0, "medium": 0, "low": 0, "trivial": 0
                }
                bottlenecks: List[Dict[str, Any]] = []
                for _sp in sorted(scored_dir.glob("*_scored.json")):
                    try:
                        with open(_sp) as _f:
                            _rows = json.load(_f)
                        _vname = _sp.stem
                        for _rk, _row in (_rows.items() if isinstance(_rows, dict) else []):
                            for _col, _val in _row.items():
                                if not _col.endswith("_score") or _val is None:
                                    continue
                                _metric = _col[:-6]
                                try:
                                    _score = int(_val)
                                except (TypeError, ValueError):
                                    continue
                                _label = _score_labels.get(_score, "unknown")
                                if _label in severity_counts:
                                    severity_counts[_label] += 1
                                if _score >= 4:
                                    bottlenecks.append({
                                        "view":     _vname,
                                        "scope":    str(_rk),
                                        "metric":   _metric,
                                        "score":    _score,
                                        "severity": _label,
                                        "value":    _row.get(_metric),
                                    })
                    except Exception:
                        pass
                bottlenecks.sort(key=lambda x: x["score"], reverse=True)

                diagnosis_summary = {
                    "run_id":          rid,
                    "checkpoint_dir":  str(checkpoint_dir),
                    "diagnosis_dir":   str(diagnosis_dir),
                    "severity_counts": severity_counts,
                    "bottlenecks":     bottlenecks[:50],
                    "phases":          diag_phases,
                }
                (ws / "diagnosis.json").write_text(json.dumps(diagnosis_summary, indent=2))

                total_issues  = sum(severity_counts.values())
                critical_high = severity_counts["critical"] + severity_counts["high"]
                report["step_13_diagnose"] = {
                    "status":          "ok",
                    "total_scored":    total_issues,
                    "high_critical":   critical_high,
                    "severity_counts": severity_counts,
                    "bottlenecks":     bottlenecks[:10],
                    "phases":          diag_phases,
                }
                _write_artifact_log(ws, 14, "session_diagnose_bottlenecks", {
                    "total_metrics_scored": total_issues,
                    "high_critical":        critical_high,
                    "severity_counts":      severity_counts,
                }, rid)

        _finish_step(_t13)
        step_timings.append(_t13)
        if "step_13_diagnose" in report:
            report["step_13_diagnose"].update({k: v for k, v in _t13.items() if k != "step_label"})

        # Build a compact timing summary and persist it
        timing_summary = [
            {"step": t["step_label"], "started_at": t.get("started_at"), "ended_at": t.get("ended_at"), "duration_s": t.get("duration_s")}
            for t in step_timings
        ]
        (ws / "step_timings.json").write_text(json.dumps(timing_summary, indent=2))
        _save_state(rid, {
            "step": "pipeline_complete",
            "traces": str(traces_dir),
            "traces_split": str(traces_split),
            "step_timings": timing_summary,
        })

        return _ok(
            "Pipeline complete",
            run_id=rid,
            workspace=str(ws),
            report=report,
            step_timings=timing_summary,
        )

    @mcp.tool()
    def dftracer_pipeline_plan_template(run_id: str, mode: str = "perf", app: str = "", system: str = "",
                                        smoke_command: str = "", skip: Optional[List[str]] = None) -> dict:
        """Render the sectioned pipeline_plan.md skeleton the dftracer-pipeline-planner writes into a session
        (session_write_file(run_id, subfolder=".", filepath="pipeline_plan.md")): '# DFTracer Pipeline Plan -
        <run_id>', '## Overview', one '## STEP N: <executor-agent>' per stage of the canonical stage table, and
        '## DISPATCH ORDER'. Use it at the start of every full pipeline request, after the session exists; then
        replace every '<fill: ...>' with real values and validate with dftracer_pipeline_check.
        Args: run_id (the existing session), mode 'perf' (default) or 'provenance', app, system, smoke_command
        (pre-fill the Overview), skip (executor agents to leave out, e.g. ['dftracer-optimizer'];
        dftracer-report and dftracer-privacy-guard can never be skipped).
        Returns {status, plan_md, dispatch_order, mode, steps}."""
        if mode not in ("perf", "provenance"):
            return {"status": "error", "message": f"unknown mode {mode!r}: use 'perf' or 'provenance'"}
        if not run_id or not str(run_id).strip():
            return {"status": "error", "message": "run_id is required: the session must exist before planning"}
        return render_plan(run_id, mode, app, system, smoke_command, skip)

    @mcp.tool()
    def dftracer_pipeline_check(plan_md: str = "", result: str = "", run_id: str = "", mode: str = "auto") -> dict:
        """Deterministic validator of the dftracer pipeline. Use it after writing pipeline_plan.md and after every
        session_run_pipeline call. On plan_md (the plan text) it checks: the '# DFTracer Pipeline Plan - <run_id>'
        header (and that it names run_id when given), '## Overview', '## STEP N: <agent>' headings numbered
        contiguously, every agent a known executor, steps in stage order (system-detect, session-setup, build-app,
        build-dftracer only after the app build, annotate, build-smoke, tracer, analyzer, diagnoser, optimizer),
        dftracer-report then dftracer-privacy-guard last, provenance mode without analyze/optimize stages and with
        the provenance_write_spec user gate, no empty or '<fill:' sections, and DISPATCH ORDER equal to the steps.
        On result (session_run_pipeline's JSON) it checks status, run_id, the annotation report when awaiting
        confirmation, step timing order and durations, and recognises the resume-path NameError.
        mode: 'auto' (provenance when a provenance agent appears), 'perf' or 'provenance'.
        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}."""
        checks: List[dict] = []
        if plan_md:
            checks += check_plan(plan_md, run_id, mode)
        if result:
            checks += check_result(result)
        return verdict(checks)
