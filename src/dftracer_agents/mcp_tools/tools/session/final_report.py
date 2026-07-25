"""Assemble a self-contained ``final_report/`` deliverable for a session.

A dftracer session accumulates its evidence in many places: annotated sources,
per-iteration run directories, wrapper scripts under ``tmp/``, logs under
``artifacts/``, and a living ``pipeline_plan.md``. Reproducing the study later
(or handing it to a colleague) means re-deriving all of that by hand.

``session_final_report`` collapses it into one folder:

.. code-block:: text

    final_report/
      README.md               ← how to reproduce the session manually
      REPORT.md               ← what was done, results, root cause
      REPORT.pdf              ← same content, rendered (pandoc, else a pure-
                                Python markdown-it-py+xhtml2pdf fallback with
                                no sudo required) — generated automatically
                                every call, never left to agent improvisation
      CONVERSATION.md         ← narrative walkthrough of how we got there
      PERFORMANCE.md          ← what the AGENT PIPELINE cost: per-step time,
                                retries, tokens, USD (from performance/)
      performance/
        performance_report.md     ← the same report, with its supporting data
        summary.json              ← whole-run profile snapshot
        steps/<n>-<step>.json     ← one file per pipeline step
        mlflow.json               ← experiment / parent-run / UI deep link
      plan/
        pipeline_plan.md          ← the final plan that was executed
        pipeline_plan_changelog.md
        plan_evolution.diff       ← how the plan changed vs its first version
      patches/
        annotated.patch           ← baseline/source  → annotated/source
        opt<n>.patch              ← previous run     → opt<n> (source + config)
        opt<n>.config.diff        ← flash.par / run-script deltas
      scripts/
        install.sh                ← rebuild deps + app for every case
        run_<case>.sh             ← one runner per case (baseline, opt1..optN)
        run_all.sh                ← run every case in order
        render_pdf.sh             ← regenerate REPORT.pdf from REPORT.md standalone
        readme_smoke_test.sh      ← static check: does README.md actually name every
                                    script, explain config.ini/WORKSPACE_ROOT, give a
                                    real runnable command, and point back to REPORT.md?

    Raw run/build logs are intentionally NOT included — they live in the
    session workspace's ``artifacts/`` dir, not in this reproducible package.

Everything written is derived from what is already on disk — the tool never
re-runs the application.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import sys

from fastmcp import FastMCP

from .workspace import _ws, _ok, _err, _load_state


#: Run directories that participate in the optimization ladder, in order.
#: ``baseline`` is the reference; ``annotated`` is the instrumented build.
_LADDER_PREFIXES = ("baseline", "annotated", "opt")


def _discover_runs(ws: Path) -> List[str]:
    """Return run names in incremental (chronological) ladder order.

    Two sources are merged, oldest-first:

    1. Fixed-name run directories: ``baseline``, ``annotated``, ``opt1..optN``
       (``opt<n>`` sorted numerically so ``opt10`` follows ``opt9``, not ``opt1``).
    2. Free-form optimization-variant wrapper scripts under ``tmp/`` that do NOT
       follow the fixed naming convention — e.g. a component optimizer subagent's
       own descriptive variants (``io_v0_baseline``, ``io_v1_collmeta``,
       ``comm_ab``, ``align1m``, ...). These are real measured optimization
       attempts and must not be silently dropped from the final report just
       because they weren't written into a top-level ``opt<n>/`` directory.
       Discovered from ``tmp/*.sh`` wrapper scripts, ordered by mtime so the
       report reflects the actual sequence the variants were tried in.

    The two lists are concatenated (fixed ladder first, then free-form variants
    in the order they were run) so ``run_all.sh`` reproduces every measured
    optimization this session made, not just the ones matching the ladder
    convention.
    """
    names = [d.name for d in ws.iterdir() if d.is_dir()]
    ordered: List[str] = []
    for fixed in ("baseline", "annotated"):
        if fixed in names:
            ordered.append(fixed)
    opts = [n for n in names if n.startswith("opt") and n[3:].isdigit()]
    ordered += sorted(opts, key=lambda n: int(n[3:]))

    tmp = ws / "tmp"
    if tmp.is_dir():
        fixed_and_opt = set(ordered)
        seen: set = set()
        variant_scripts = sorted(
            (p for p in tmp.glob("*.sh") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
        )
        for script in variant_scripts:
            stem = script.stem
            # Skip anything that's just a fixed-ladder name in disguise
            # (e.g. tmp/baseline_run.sh) — already covered above.
            if any(stem == f or stem.startswith(f"{f}_") or stem.endswith(f"_{f}")
                   for f in fixed_and_opt):
                continue
            if stem in seen:
                continue
            seen.add(stem)
            ordered.append(stem)
    return ordered


def _diff_files(old: Path, new: Path, out: Path) -> int:
    """Write a unified diff of two individual files to *out*. Returns line count.

    Companion to ``_diff_trees`` for apps that keep one shared annotated
    script with suffixed variants (``molformer_train_opt1.py`` next to
    ``molformer_train.py``) instead of a full per-run ``source/`` tree copy —
    common for single-file Python ML training scripts. Same stdout-not-exit-
    code convention as ``_diff_trees``.
    """
    if not old.is_file() or not new.is_file():
        return 0
    r = subprocess.run(
        ["diff", "-u", str(old), str(new)], capture_output=True, text=True,
    )
    if not r.stdout:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(r.stdout)
    return len(r.stdout.splitlines())


def _variant_source_files(ws: Path) -> Dict[str, Path]:
    """Find single-file annotated-source variants under ``<ws>/annotated/src``.

    Returns a map of ``opt<N>`` (or any other suffix) -> the variant file's
    path, plus a ``"base"`` entry for the un-suffixed original, when the
    directory holds a base script (e.g. ``app.py``) alongside suffixed
    siblings (``app_opt1.py``, ``app_opt2.py``) instead of one full
    per-run ``source/`` tree copy per optimization iteration.
    """
    src_dir = ws / "annotated" / "src"
    if not src_dir.is_dir():
        return {}
    files = [p for p in src_dir.iterdir() if p.is_file()]
    result: Dict[str, Path] = {}
    for p in files:
        stem, suffix = p.stem, p.suffix
        for other in files:
            if other is p:
                continue
            other_stem = other.stem
            if other_stem.startswith(stem + "_") and other.suffix == suffix:
                variant = other_stem[len(stem) + 1:]
                if variant.startswith("opt") or variant.startswith("v"):
                    result.setdefault("base", p)
                    result[variant] = other
    return result


def _diff_trees(old: Path, new: Path, out: Path) -> int:
    """Write a unified diff of two directory trees to *out*. Returns line count.

    ``--no-dereference`` is required: source trees such as Flash-X's contain
    dangling symlinks (shallow-clone submodules), and without it ``diff`` tries
    to follow them, prints "No such file or directory" to stderr, and exits 2.
    Exit code 2 does NOT mean the diff is invalid — a full, correct patch is
    still produced on stdout — so we key off the presence of output, not the
    exit code, and never discard a non-empty diff.

    Binary files and build artefacts are excluded so the patch stays reviewable.
    """
    if not old.is_dir() or not new.is_dir():
        return 0
    excludes = [
        "--exclude=*.o", "--exclude=*.mod", "--exclude=*.a", "--exclude=*.so",
        "--exclude=.git", "--exclude=object", "--exclude=*.pfw",
        "--exclude=*.pfw.gz", "--exclude=*.h5", "--exclude=*.log",
    ]
    r = subprocess.run(
        ["diff", "-ruN", "--no-dereference", *excludes, str(old), str(new)],
        capture_output=True, text=True,
    )
    # diff: 0 = identical, 1 = differences found, 2 = trouble (e.g. an
    # unreadable path) — but stdout may still hold a complete, valid patch.
    if not r.stdout:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(r.stdout)
    return len(r.stdout.splitlines())


def _anonymize_script(path: Path, ws: Path) -> None:
    """Replace every literal occurrence of the workspace's absolute path with
    ``$WS`` (sourced from ``config.ini`` by ``lib_load_config.sh``).

    This is what makes a collected script safe to keep in a git-tracked
    ``final_report/`` folder: the workspace path is a real, session-specific
    absolute filesystem path (and its final path component is often a
    timestamp, its parents may contain a username) — exactly the kind of PII
    the project's privacy policy forbids persisting. Every script MUST be
    anonymized this way; never ship a copied wrapper script unmodified.
    """
    text = path.read_text()
    ws_str = str(ws)
    if ws_str in text:
        text = text.replace(ws_str, "${WS}")
    # A script that used $WS/$WORKSPACE_ROOT already (from an earlier,
    # config-aware wrapper) needs the loader sourced; a raw tmp/-collected
    # script needs it prepended so ${WS} actually resolves.
    if "lib_load_config.sh" not in text:
        lines = text.splitlines(keepends=True)
        shebang = lines[0] if lines and lines[0].startswith("#!") else "#!/bin/bash\n"
        rest = lines[1:] if lines and lines[0].startswith("#!") else lines
        loader = (
            'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"\n'
            'source "$HERE/lib_load_config.sh"\n\n'
        )
        text = shebang + loader + "".join(rest)
    path.write_text(text)


def _anonymize_path_refs(path: Path, ws: Path) -> None:
    """Strip the workspace's absolute path out of a plain text file, in place.

    Diff/patch files written by ``_diff_trees``/``_diff_files`` embed the
    full absolute paths being compared (``diff -ruN <old> <new>`` puts them in
    the ``---``/``+++`` header lines) — the same PII class ``_anonymize_script``
    strips from collected wrapper scripts, but patches were previously written
    to ``final_report/patches/`` unmodified. Best-effort and silent on any
    read/decode failure (e.g. a stray binary diff) since this is a privacy
    scrub, not a build step — ``privacy_scan``/``privacy_redact`` remain the
    authoritative, deterministic check before a session is considered done.
    """
    try:
        text = path.read_text(errors="ignore")
    except OSError:
        return
    ws_str = str(ws)
    if ws_str in text:
        path.write_text(text.replace(ws_str, "$WS"))


def _collect_scripts(ws: Path, dest: Path, runs: List[str]) -> List[str]:
    """Copy each run's wrapper script into ``final_report/scripts/``, anonymized.

    Wrapper scripts are written to ``<ws>/tmp/`` by the tracer/optimizer steps
    (a wrapper is required because ``flux run`` does not accept ``-x VAR`` and
    env does not cross the ``flux proxy`` boundary). Anything matching the run
    name is collected, then every absolute-path/PII occurrence is stripped via
    ``_anonymize_script`` so the copy is safe to keep git-tracked.
    """
    dest.mkdir(parents=True, exist_ok=True)
    copied: List[str] = []
    tmp = ws / "tmp"
    if not tmp.is_dir():
        return copied
    for run in runs:
        for cand in sorted(tmp.glob(f"*{run}*.sh")):
            target = dest / f"run_{run}.sh"
            shutil.copy2(cand, target)
            target.chmod(0o755)
            _anonymize_script(target, ws)
            copied.append(target.name)
            break
    return copied


def _write_config_ini(dest: Path, state: Dict[str, Any]) -> None:
    """Emit ``config.ini``: the ONLY place a real path/session value should
    ever need to go. Every generated script sources it (via
    ``lib_load_config.sh``) instead of embedding an absolute path directly —
    this is what keeps the rest of ``final_report/`` git-safe and reusable.

    Flat ``KEY=VALUE`` (no ``[sections]``) so ``lib_load_config.sh`` can
    source it directly with ``set -a; source config.ini; set +a``.
    """
    app_name = state.get("app_name", "")
    app_url = state.get("url") or state.get("app", "")
    app_ref = state.get("ref", "main")
    lines = [
        "# Reproducibility config for this session's final_report/.",
        "# ---------------------------------------------------------------",
        "# This file is the ONLY place a real filesystem path or",
        "# session-specific value should ever go. Every script under",
        "# scripts/ sources this file (via scripts/lib_load_config.sh)",
        "# instead of hardcoding paths, so the scripts themselves never",
        "# contain a username or absolute user path (project privacy",
        "# policy: git-tracked deliverables must never embed PII).",
        "#",
        "# HOW TO USE: set WORKSPACE_ROOT below to wherever this session's",
        "# workspace lives on your system, then run scripts/run_all.sh",
        "# <flux-alloc-id> (or scripts/install.sh first, for a from-scratch",
        "# reproduction).",
        "",
        "# --- Required: set this before running anything -------------------",
        "WORKSPACE_ROOT=/path/to/your/session/workspace",
        "",
        "# --- App -----------------------------------------------------------",
        f"APP_NAME={app_name}",
        f"APP_REPO_URL={app_url}",
        f"APP_REF={app_ref}",
        "",
        "# --- Compiler selection (override if not using system `cc`/`CC`) ---",
        "CC_CMD=cc",
        "CXX_CMD=CC",
        "",
    ]
    p = dest / "config.ini"
    p.write_text("\n".join(lines) + "\n")


def _write_lib_load_config(dest: Path) -> None:
    """Emit the generic config loader every other script sources.

    Deliberately app-agnostic: it only sets ``WS``/``CC``/``CXX`` and
    validates ``WORKSPACE_ROOT`` was actually edited. Anything app-specific
    (module loads, build flags) belongs in ``install.sh``/the run scripts
    themselves, which can read additional keys out of the same config.ini.
    """
    body = """#!/bin/bash
# Source this from every other script: loads config.ini and validates
# WORKSPACE_ROOT was actually set. No script other than this one should
# ever contain a hardcoded absolute path -- everything comes from config.ini.
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="${CONFIG_FILE:-$HERE/../config.ini}"

if [ ! -f "$CONFIG_FILE" ]; then
  echo "ERROR: config file not found: $CONFIG_FILE" >&2
  exit 1
fi

set -a
# shellcheck disable=SC1090
source "$CONFIG_FILE"
set +a

if [ -z "$WORKSPACE_ROOT" ] || [ "$WORKSPACE_ROOT" = "/path/to/your/session/workspace" ]; then
  echo "ERROR: set WORKSPACE_ROOT in $CONFIG_FILE to your actual session workspace path before running." >&2
  exit 1
fi

WS="$WORKSPACE_ROOT"
export WS
export CC="${CC_CMD:-cc}"
export CXX="${CXX_CMD:-CC}"

# OUTPUT_ROOT: where run scripts write their own dataset/traces output.
# Defaults to WS itself; override (e.g. to a scratch validation directory,
# kept separate from the original session's own run data) via config.ini or
# the environment before sourcing this file.
OUTPUT_ROOT="${OUTPUT_ROOT:-$WS}"
mkdir -p "$OUTPUT_ROOT"
export OUTPUT_ROOT
"""
    p = dest / "lib_load_config.sh"
    p.write_text(body)
    p.chmod(0o755)


def _write_run_all(dest: Path, scripts: List[str], alloc_hint: str,
                   params_root: Optional[Path] = None) -> None:
    """Emit ``run_all.sh`` driving every collected case in ladder order.

    Each case's parameter file is staged into the app's build dir first (app
    frameworks that read a fixed filename from cwd, like Flash-X's
    ``flash.par``, silently run the *last* iteration's configuration
    otherwise). All paths come from ``config.ini`` via ``lib_load_config.sh``
    — no absolute path is embedded here.
    """
    lines = [
        "#!/bin/bash",
        "# Run every case in the optimization ladder, in order.",
        "# Pass the active Flux allocation id as $1 (see `flux jobs`).",
        "#",
        "# NOTE: a bare `flux run` queues a NEW job instead of using your",
        "# allocation -- always go through `flux proxy <alloc>`.",
        "set -e",
        f'ALLOC="${{1:-{alloc_hint}}}"',
        'if [ -z "$ALLOC" ]; then echo "usage: $0 <flux_alloc_id>"; exit 1; fi',
        'HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'source "$HERE/lib_load_config.sh"',
        'OBJ="$WS/annotated/source/object"',
        "",
    ]
    for s in scripts:
        case = s[len("run_"):-len(".sh")]
        lines.append(f'echo "=== {case} ==="')
        if params_root and (params_root / case).is_dir():
            for pf in sorted((params_root / case).iterdir()):
                if pf.is_file():
                    lines.append(
                        f'cp "$HERE/../params/{case}/{pf.name}" "$OBJ/{pf.name}"'
                        f'   # this case\'s config; app reads it from cwd'
                    )
        lines += [
            f'flux proxy "$ALLOC" bash "$HERE/{s}"',
            "",
        ]
    p = dest / "run_all.sh"
    p.write_text("\n".join(lines) + "\n")
    p.chmod(0o755)


_README_SMOKE_TEST_SCRIPT = r"""#!/bin/bash
# Static smoke test: does final_report/README.md actually contain everything
# needed to reproduce this session from this folder alone -- every script
# under scripts/ is named, config.ini/WORKSPACE_ROOT is explained, and there
# is at least one real, runnable command block. This does NOT execute the
# app (that is scripts/run_all.sh's job, and the "self-contained validation"
# step in session_final_report) -- it only checks the README's INSTRUCTIONS
# are complete enough for a reader who has never seen this session to follow
# them, so a thin or stale README fails fast instead of only being noticed
# by a human mid-reproduction.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FINAL="$HERE/.."
README="$FINAL/README.md"
FAIL=0

fail() { echo "MISSING: $1" >&2; FAIL=1; }

if [ ! -f "$README" ]; then
  echo "MISSING: README.md does not exist" >&2
  exit 1
fi

grep -qi "WORKSPACE_ROOT" "$README"  || fail "no mention of WORKSPACE_ROOT (reader won't know to set it)"
grep -qi "config.ini"     "$README"  || fail "no mention of config.ini (reader won't know it's the one place to set a real path)"

# Every script this session actually produced must be named somewhere in the
# README, or a reader has no way to know it exists / when to run it.
for s in "$HERE"/*.sh; do
  name="$(basename "$s")"
  [ "$name" = "readme_smoke_test.sh" ] && continue
  grep -q -- "$name" "$README" || fail "scripts/$name exists but is never mentioned in README.md"
done

# At least one real, copy-pasteable command (not just prose describing what
# to do) -- a fenced code block containing a shell invocation.
if ! grep -qE '^\s*(bash |\./|flux |cd )' "$README"; then
  fail "no literal runnable command (e.g. 'bash scripts/install.sh', './scripts/run_all.sh <alloc-id>') found in README.md"
fi

# A verification step: the reader needs to know what "reproduced" means --
# some pointer back to the numbers in REPORT.md to compare against.
grep -qi "REPORT.md" "$README" || fail "no pointer back to REPORT.md to compare reproduced numbers against"

if [ "$FAIL" -ne 0 ]; then
  echo "README smoke test: FAILED -- see MISSING lines above" >&2
  exit 1
fi
echo "README smoke test: PASSED -- every script is named, config.ini/WORKSPACE_ROOT explained, a runnable command and a verification pointer are both present."
"""


def _write_readme_smoke_test(scripts_dir: Path) -> Path:
    """Persist the standalone README completeness smoke test into ``scripts/``.

    Written every call (not just when it fails) so a reader who copies only
    `final_report/` elsewhere can re-run it themselves after editing the
    README, without this MCP tool.
    """
    p = scripts_dir / "readme_smoke_test.sh"
    p.write_text(_README_SMOKE_TEST_SCRIPT)
    p.chmod(0o755)
    return p


def _run_readme_smoke_test(final: Path) -> Dict[str, Any]:
    """Execute the README smoke test and report pass/fail + what's missing.

    Static/mechanical, not a full reproduction run (that's the separate
    ``validated``/``run_all.sh`` flow) -- this only checks that the
    INSTRUCTIONS are complete, which is cheap enough to run every call and
    catches a stale/thin README before a human ever tries to follow it.
    """
    script = final / "scripts" / "readme_smoke_test.sh"
    if not script.is_file():
        return {"ok": False, "reason": "readme_smoke_test.sh was not written"}
    r = subprocess.run(
        ["bash", str(script)], capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        missing = [
            line.split("MISSING: ", 1)[1]
            for line in (r.stderr or "").splitlines()
            if line.startswith("MISSING: ")
        ]
        return {"ok": False, "missing": missing or [(r.stderr or r.stdout).strip()]}
    return {"ok": True, "output": r.stdout.strip()}


def _write_install(dest: Path, state: Dict[str, Any]) -> None:
    """Emit ``install.sh`` reconstructing dependencies + the app build.

    App build systems vary too much to auto-derive generically (CMake vs.
    Make vs. a framework-specific ``setup`` script), so this always emits a
    templated skeleton that reads paths from config.ini and points the
    reader at the exact patches to re-apply — NOT a guess at build commands
    for an app this tool has no reliable way to introspect. If the session's
    own build step recorded exact commands (state["build_commands"]), those
    are inlined instead of the placeholder.
    """
    app_name = state.get("app_name", "the application")
    dft = state.get("dftracer_install_prefix", "<dftracer_prefix>")
    build_cmds = state.get("build_commands", "")
    build_block = (
        build_cmds if build_cmds else
        f'# TODO: fill in the exact build commands for {app_name}.\n'
        f'# See ../patches/annotated.patch for the source-level changes and\n'
        f'# ../plan/pipeline_plan.md for the build flags this session used.\n'
        f'echo "EDIT scripts/install.sh with this app\'s real build commands" >&2\n'
        f'exit 1'
    )
    body = f"""#!/bin/bash
# Rebuild the session's dependencies and application from scratch.
# Edit ../config.ini (WORKSPACE_ROOT) before running this.
set -e
HERE="$(cd "$(dirname "${{BASH_SOURCE[0]}}")" && pwd)"
source "$HERE/lib_load_config.sh"

# dftracer install prefix used by this session (for reference only --
# re-install from source into $WS, never reuse a path from another machine):
#   {dft}

{build_block}
"""
    p = dest / "install.sh"
    p.write_text(body)
    p.chmod(0o755)


def _plan_evolution(ws: Path, out: Path) -> None:
    """Diff the plan's first recorded version against its final version.

    The changelog holds the history; if git tracks the plan we diff against its
    first blob, otherwise we simply note that no earlier version was recoverable.
    """
    plan = ws / "pipeline_plan.md"
    if not plan.exists():
        return
    r = subprocess.run(
        ["git", "log", "--diff-filter=A", "--format=%H", "--", str(plan)],
        cwd=str(ws), capture_output=True, text=True,
    )
    first = r.stdout.strip().splitlines()
    if r.returncode == 0 and first:
        d = subprocess.run(
            ["git", "diff", f"{first[-1]}", "--", str(plan)],
            cwd=str(ws), capture_output=True, text=True,
        )
        if d.stdout:
            out.write_text(d.stdout)
            return
    out.write_text(
        "# Plan evolution\n\n"
        "The plan is not tracked in git for this session, so no earlier\n"
        "revision could be diffed. See `pipeline_plan_changelog.md` for the\n"
        "dated history of what each step changed and why.\n"
    )


_RENDER_PDF_SCRIPT = r"""#!/bin/bash
# Render final_report/REPORT.md -> final_report/REPORT.pdf.
# Saved here (not just run ad hoc) so the PDF is reproducible without this
# tool: `bash scripts/render_pdf.sh` from inside final_report/ regenerates it
# from whatever REPORT.md currently contains.
set -e
cd "$(dirname "$0")/.."   # final_report/
SRC="REPORT.md"
DST="REPORT.pdf"

if command -v pandoc >/dev/null 2>&1; then
    pandoc "$SRC" -o "$DST" --pdf-engine=xelatex 2>/dev/null \
        || pandoc "$SRC" -o "$DST"
    exit $?
fi

# No pandoc (common on locked-down HPC login/compute nodes, no sudo): pure
# Python fallback, installed locally with no privilege escalation.
PYBIN="${PYTHON_BIN:-python3}"
"$PYBIN" - "$SRC" "$DST" <<'PYEOF'
import subprocess, sys, os

def ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
    except ImportError:
        # --user fails inside a venv ("user site-packages are not visible");
        # a venv's own pip already installs to its own site-packages without
        # it, so only pass --user when NOT running inside a venv.
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        cmd = [sys.executable, "-m", "pip", "install", "-q"]
        if not in_venv:
            cmd.append("--user")
        cmd.append(pkg)
        subprocess.run(cmd, check=True)

ensure("markdown-it-py", "markdown_it")
ensure("xhtml2pdf")

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

src, dst = sys.argv[1], sys.argv[2]
md_text = open(src, encoding="utf-8").read()
html_body = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(md_text)
STYLE = (
    "body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; } "
    "table { border-collapse: collapse; width: 100%; margin: 8px 0; } "
    "th, td { border: 1px solid #999; padding: 4px 6px; font-size: 9pt; } "
    "th { background: #eee; } "
    "pre { background: #f5f5f5; padding: 6px; font-size: 8pt; white-space: pre-wrap; } "
    "code { font-family: monospace; } "
    "h1, h2, h3 { color: #222; }"
)
html = (
    "<html><head><meta charset=\"utf-8\"><style>" + STYLE + "</style></head>"
    "<body>" + html_body + "</body></html>"
)

with open(dst, "wb") as fh:
    result = pisa.CreatePDF(html, dest=fh)
if result.err:
    sys.exit(f"xhtml2pdf reported {result.err} error(s)")
PYEOF
"""


def _render_pdf(final: Path) -> Dict[str, Any]:
    """Render ``REPORT.md`` -> ``REPORT.pdf`` and save the script that did it.

    Tries system/module ``pandoc`` first (best typography), falls back to a
    pure-Python ``markdown-it-py`` + ``xhtml2pdf`` chain (no sudo, works on
    locked-down HPC nodes) if pandoc is unavailable. Never raises — a failed
    PDF render is reported in the return dict, not a tool-call exception,
    since the markdown REPORT.md is still the authoritative deliverable.
    """
    report_md_path = final / "REPORT.md"
    if not report_md_path.exists():
        return {"generated": False, "reason": "REPORT.md not written yet"}

    scripts_dir = final / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    script_path = scripts_dir / "render_pdf.sh"
    script_path.write_text(_RENDER_PDF_SCRIPT)
    script_path.chmod(0o755)

    pdf_path = final / "REPORT.pdf"
    try:
        r = subprocess.run(
            ["bash", str(script_path)],
            cwd=str(final),
            capture_output=True,
            text=True,
            timeout=180,
            env={**__import__("os").environ, "PYTHON_BIN": sys.executable},
        )
    except Exception as e:  # noqa: BLE001 - PDF rendering is best-effort
        return {"generated": False, "reason": f"render_pdf.sh raised: {e}"}

    if r.returncode != 0:
        return {
            "generated": False,
            "reason": (r.stderr or r.stdout or "unknown error").strip()[-2000:],
            "script": str(script_path),
        }
    if not pdf_path.exists() or pdf_path.read_bytes()[:5] != b"%PDF-":
        return {
            "generated": False,
            "reason": "render_pdf.sh exited 0 but no valid %PDF- file was produced",
            "script": str(script_path),
        }
    return {
        "generated": True,
        "path": str(pdf_path),
        "size_bytes": pdf_path.stat().st_size,
        "script": str(script_path),
    }


_REQUIRED_REPORT_SECTIONS = [
    "Executive Summary",
    "Baseline Detail",
    "Per-Agent Contributions",
    "Tool Findings vs Manual Analysis",
    "Bottleneck Diagnosis",
    "Optimization Proposals",
    "Optimization Ledger",
    "N-Way Comparator",
    "Iteration Results",
    "Lessons Learned",
    "Recommendations",
    "Remaining Work",
]


def _validate_report_completeness(
    final: Path, runs: List[str], patches: Dict[str, int], pdf_result: Dict[str, Any],
) -> Dict[str, Any]:
    """Check ``REPORT.md`` against the mandatory structure, not just that it exists.

    A ``session_final_report`` call can succeed mechanically (files written,
    PDF rendered) while the report content itself is thin — missing sections,
    no code snippets, no per-optimization "what's next". This is a deterministic
    proxy for that: it can't judge prose quality, but it can catch the concrete,
    checkable gaps (a missing section header, zero code fences for a session
    that ran opt<N> variants, an empty ``patches`` dict when opt runs exist)
    that made this exact report's ``session_final_report`` output look "ok"
    while the earlier CLAUDE.md-mandated per-optimization detail was still
    missing. Returns ``{"ok": bool, "missing": [...], "warnings": [...]}`` so
    the calling agent (or a future one resuming this session) knows exactly
    what to fix and re-call the tool, rather than shipping a report that only
    *looks* validated because the mechanical parts succeeded.
    """
    missing: List[str] = []
    warnings: List[str] = []

    report_path = final / "REPORT.md"
    text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    if not text:
        return {"ok": False, "missing": ["REPORT.md is empty or missing"], "warnings": []}

    for section in _REQUIRED_REPORT_SECTIONS:
        if section.lower() not in text.lower():
            missing.append(f"REPORT.md is missing the required '{section}' section")

    opt_runs = [r for r in runs if r.startswith("opt")]
    if opt_runs:
        n_code_fences = text.count("```")
        # Each optimization's code/config snippet should be its own fenced
        # block; a report with fewer fenced blocks than optimizations almost
        # certainly fell back to naming files instead of showing the diff.
        if n_code_fences < len(opt_runs) * 2:  # each fence pair = one block
            warnings.append(
                f"only {n_code_fences // 2} code block(s) in REPORT.md for "
                f"{len(opt_runs)} optimization run(s) — every optimization "
                "should carry its own code/config snippet (Pipeline Policy "
                "rule: per-optimization triple)"
            )
        if "what's next" not in text.lower() and "what next" not in text.lower():
            warnings.append(
                "no 'what's next' language found in REPORT.md — every "
                "optimization (applied or rejected) needs its own next-step line"
            )
        if not patches:
            missing.append(
                f"patches/ is empty but {len(opt_runs)} opt run(s) exist — "
                "either the app kept no per-run source/ snapshot and no "
                "annotated/src/<name>_opt<N> variant files were found, or "
                "session_capture_run_record was never called for these runs"
            )

    if not pdf_result.get("generated"):
        missing.append(
            f"REPORT.pdf was not generated: {pdf_result.get('reason', 'unknown reason')}"
        )

    return {"ok": not missing, "missing": missing, "warnings": warnings}


def _session_final_report_impl(
    run_id: str,
    report_md: str = "",
    conversation_md: str = "",
    readme_md: str = "",
    alloc_hint: str = "",
    validated: bool = False,
    validation_notes: str = "",
) -> str:
    """Standalone implementation of ``session_final_report`` (see module docstring).

    The three narrative documents are supplied by the caller (the agent knows
    what happened; the tool does not invent findings). Everything else —
    patches, scripts, plan, logs — is derived mechanically from the workspace.
    """
    ws = _ws(run_id)
    if not ws.is_dir():
        return _err(f"workspace not found for run_id: {run_id}")

    final = ws / "final_report"
    final.mkdir(parents=True, exist_ok=True)
    state = _load_state(run_id)
    runs = _discover_runs(ws)

    # ---- plan -----------------------------------------------------------
    plan_dir = final / "plan"
    plan_dir.mkdir(exist_ok=True)
    for name in ("pipeline_plan.md", "pipeline_plan_changelog.md"):
        src = ws / name
        if src.exists():
            shutil.copy2(src, plan_dir / name)
    _plan_evolution(ws, plan_dir / "plan_evolution.diff")

    # ---- patches --------------------------------------------------------
    patch_dir = final / "patches"
    patch_dir.mkdir(exist_ok=True)
    patches: Dict[str, int] = {}

    base_src = ws / "baseline" / "source"
    ann_src = ws / "annotated" / "source"
    variants = _variant_source_files(ws)
    if base_src.is_dir() and ann_src.is_dir():
        patches["annotated.patch"] = _diff_trees(
            base_src, ann_src, patch_dir / "annotated.patch")
        # The build-system selection (e.g. serial vs parallel HDF5 IO unit) is
        # invisible to a source diff but is often the decisive change.
        n = _build_config_diff(base_src, ann_src, patch_dir / "build_config.diff")
        if n:
            patches["build_config.diff"] = n
    elif "base" in variants:
        # Single-file app: no per-run source/ tree, but annotated/src holds the
        # base script directly (already the annotated version at this point in
        # the pipeline) — nothing to diff against a pre-annotation baseline
        # copy, so this is intentionally a no-op; the opt<N> variants below are
        # what the report actually needs.
        pass

    # Optimization iterations often re-use the annotated tree and differ only in
    # build flags / parameter file / run script, so capture BOTH a source diff
    # (when the run kept its own source snapshot) and a config diff.
    prev_src = ann_src
    prev_run = "annotated"
    for run in [r for r in runs if r.startswith("opt")]:
        rsrc = ws / run / "source"
        variant_key = run[len("opt"):] if run.startswith("opt") else None
        variant_file = variants.get(f"opt{variant_key}") if variant_key else None
        if rsrc.is_dir():
            patches[f"{run}.patch"] = _diff_trees(
                prev_src, rsrc, patch_dir / f"{run}.patch")
            prev_src = rsrc
        elif variant_file and "base" in variants:
            # Single-file variant pattern (e.g. train_opt1.py next to
            # train.py) — diff each variant against the shared base script
            # rather than a per-run source/ tree copy.
            n = _diff_files(
                variants["base"], variant_file, patch_dir / f"{run}.patch")
            if n:
                patches[f"{run}.patch"] = n

        # Preferred: the record captured by session_capture_run_record at the end
        # of the step. It survives the next iteration overwriting build config,
        # flash.par, and the run wrapper in place. Sessions may name this file
        # after any prior run (not necessarily the immediately-preceding one —
        # e.g. every opt<N> diffed straight against "baseline"), so fall back to
        # whatever from_*.record.diff is actually present rather than requiring
        # an exact from_<prev_run> match.
        rec_diff = ws / run / "patches" / f"from_{prev_run}.record.diff"
        if not rec_diff.is_file():
            candidates = sorted((ws / run / "patches").glob("from_*.record.diff")) \
                if (ws / run / "patches").is_dir() else []
            rec_diff = candidates[0] if candidates else rec_diff
        if rec_diff.is_file():
            dst = patch_dir / f"{run}.record.diff"
            shutil.copy2(rec_diff, dst)
            patches[f"{run}.record.diff"] = len(dst.read_text().splitlines())
        else:
            # Fallback for sessions that predate run records: reconstruct what we
            # can from the wrapper scripts still lying around in tmp/.
            cfg = _config_diff(ws, run, patch_dir / f"{run}.config.diff")
            if cfg:
                patches[f"{run}.config.diff"] = cfg

        prev_run = run

    # Every patch file embeds the workspace's real absolute path in its diff
    # headers — strip it now, once, rather than relying solely on the
    # end-of-session privacy_scan/privacy_redact pass to catch it (defense in
    # depth: this class of PII is fully mechanical to remove at the source).
    for p in patch_dir.glob("*"):
        if p.is_file():
            _anonymize_path_refs(p, ws)

    # ---- per-run parameter files, so each case can actually be re-run ----
    params_root = final / "params"
    for run in runs:
        rec_params = ws / run / "record" / "params"
        if not rec_params.is_dir():
            continue
        dst = params_root / run
        dst.mkdir(parents=True, exist_ok=True)
        for p in rec_params.iterdir():
            if p.is_file():
                shutil.copy2(p, dst / p.name)

    # ---- scripts --------------------------------------------------------
    scripts_dir = final / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    _write_config_ini(final, state)
    _write_lib_load_config(scripts_dir)
    collected = _collect_scripts(ws, scripts_dir, runs)
    _write_install(scripts_dir, state)
    _write_run_all(scripts_dir, collected, alloc_hint,
                   params_root=params_root if params_root.is_dir() else None)
    _write_readme_smoke_test(scripts_dir)

    # Raw run/build logs are intentionally NOT copied into final_report/ — they
    # are workspace-local debugging exhaust, not part of the reproducible
    # deliverable (the scripts + patches + REPORT.md numbers are). Anyone who
    # needs the raw logs still has the session workspace's artifacts/ dir.
    n_logs = 0

    # ---- pipeline profile (what the agents cost to produce all of the above) --
    performance = _collect_performance(ws, final)

    # ---- narrative documents -------------------------------------------
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    hdr = f"<!-- generated by session_final_report for {run_id} at {stamp} -->\n\n"
    val_line = (
        f"**Self-contained validation: PASSED** ({validation_notes or 'reproduced via scripts/ + config.ini'})\n\n"
        if validated else
        "**Self-contained validation: NOT YET RUN.** Before considering this report "
        "final, copy config.ini + scripts/ to a scratch location (or point "
        "OUTPUT_ROOT at an isolated subdirectory), fill in WORKSPACE_ROOT, and "
        "run scripts/run_all.sh <alloc-id> end to end — then re-call "
        "session_final_report with validated=True.\n\n"
    )
    (final / "REPORT.md").write_text(hdr + val_line + (report_md or "# Report\n\n(not supplied)\n"))
    (final / "CONVERSATION.md").write_text(
        hdr + (conversation_md or "# Conversational report\n\n(not supplied)\n"))
    (final / "README.md").write_text(
        hdr + (readme_md or "# Reproducing this session\n\n(not supplied)\n"))

    # ---- PDF rendering (MANDATORY, deterministic — not left to agent recipe) --
    pdf_result = _render_pdf(final)

    # ---- report-completeness validation (MANDATORY, deterministic) ------
    # Checked, not assumed: a mechanically "ok" assembly can still ship a thin
    # REPORT.md missing required sections/snippets/patches. See
    # _validate_report_completeness's docstring for why this exists.
    completeness = _validate_report_completeness(final, runs, patches, pdf_result)

    # ---- README reproducibility smoke test (MANDATORY, deterministic) ---
    # A README that "exists" can still be useless to a reader who has never
    # seen this session -- this actually checks the instructions are complete
    # (every script named, config.ini/WORKSPACE_ROOT explained, a real
    # runnable command present, a pointer back to REPORT.md to verify
    # against), not just that the file is non-empty.
    readme_check = _run_readme_smoke_test(final)

    return _ok(
        "final_report assembled",
        final_report_dir=str(final),
        runs=runs,
        patches=patches,
        scripts=collected + [
            "install.sh", "run_all.sh", "lib_load_config.sh",
            "render_pdf.sh", "readme_smoke_test.sh",
        ],
        config="config.ini",
        logs_copied=n_logs,
        performance=performance or "no performance/ dir (pipeline was not profiled)",
        validated=validated,
        pdf=pdf_result,
        completeness=completeness,
        readme_check=readme_check,
        reminder=(
            "REPORT.md is incomplete — see the completeness field's 'missing' "
            "list, fix REPORT.md/patches, and re-call session_final_report "
            "before calling this session done." if not completeness["ok"] else
            "README.md is missing reproduction instructions — see the "
            "readme_check field's 'missing' list, fix README.md, and re-call "
            "session_final_report before calling this session done." if not readme_check["ok"] else
            None if validated else
            "Run a self-contained validation (config.ini + scripts/run_all.sh, "
            "output isolated from the original run data) before calling this "
            "done; re-call with validated=True once it passes."
        ),
    )


def _collect_performance(ws: Path, final: Path) -> Dict[str, Any]:
    """Fold the pipeline profile into the deliverable.

    ``performance/`` records what the *agent pipeline* cost to produce this
    session — per-step wall/exec time, tries and retries, tokens, USD — which is
    exactly the part a reader cannot reconstruct from the patches and logs.

    The raw OTLP event log (``otlp/``) is deliberately left behind: it is large,
    append-only, and already summarised by everything else here.

    The report is re-rendered from ``summary.json`` rather than copied, so a
    ``performance_report.md`` left stale by a collector that died mid-run cannot
    ship inside the final deliverable.
    """
    src = ws / "performance"
    if not src.is_dir():
        return {}

    dst = final / "performance"
    dst.mkdir(parents=True, exist_ok=True)
    out: Dict[str, Any] = {}

    steps_src = src / "steps"
    if steps_src.is_dir():
        steps_dst = dst / "steps"
        steps_dst.mkdir(exist_ok=True)
        n = 0
        for p in sorted(steps_src.glob("*.json")):
            shutil.copy2(p, steps_dst / p.name)
            n += 1
        out["steps"] = n

    for name in ("summary.json", "mlflow.json"):
        p = src / name
        if p.is_file():
            shutil.copy2(p, dst / name)

    summary = src / "summary.json"
    report = dst / "performance_report.md"
    if summary.is_file():
        try:
            from ....profiling.mlflow_sink import write_performance_report
            snap = json.loads(summary.read_text())
            url = ""
            pointer = src / "mlflow.json"
            if pointer.is_file():
                url = json.loads(pointer.read_text()).get("ui_url", "")
            write_performance_report(snap, dst, url)
            out["cost_usd"] = round(snap["totals"]["cost_usd"], 4)
            out["retries"] = snap["attempts"]["retries"]
        except Exception:
            # A malformed snapshot must not sink the whole deliverable.
            fallback = src / "performance_report.md"
            if fallback.is_file():
                shutil.copy2(fallback, report)
    elif (src / "performance_report.md").is_file():
        shutil.copy2(src / "performance_report.md", report)

    # Surface the report at the top level too: it is a headline document, not a
    # supporting artefact, and readers should not have to go looking for it.
    if report.is_file():
        shutil.copy2(report, final / "PERFORMANCE.md")
        out["report"] = str(final / "PERFORMANCE.md")
    return out


#: Build-configuration files worth diffing between runs. For Make-based apps
#: (Flash-X) the decisive optimization is recorded here, not in the source tree:
#: e.g. ``object/Units`` flips ``IO/IOMain/hdf5/serial/PM`` ->
#: ``.../parallel/PM`` and ``object/setup_call`` gains ``+parallelIO``.
_BUILD_CONFIG_FILES = ("object/setup_call", "object/Units", "object/Makefile.h")


def _build_config_diff(old_src: Path, new_src: Path, out: Path) -> int:
    """Diff the build configuration of two run source trees.

    A source-tree diff is blind to build-system selection: rebuilding with a
    different setup shortcut changes which units are compiled, not the sources.
    Capturing ``setup_call`` / ``Units`` / ``Makefile.h`` makes that visible.
    """
    chunks: List[str] = []
    for rel in _BUILD_CONFIG_FILES:
        a, b = old_src / rel, new_src / rel
        if not (a.exists() and b.exists()):
            continue
        r = subprocess.run(["diff", "-u", "--no-dereference", str(a), str(b)],
                           capture_output=True, text=True)
        if r.stdout:
            chunks.append(f"# {rel}\n{r.stdout}")
    if not chunks:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(chunks)
    out.write_text(body)
    return len(body.splitlines())


def _config_diff(ws: Path, run: str, out: Path) -> int:
    """Diff a run's parameter file and wrapper script against the baseline's.

    Optimization iterations on Make-based apps (Flash-X) frequently change only
    ``flash.par`` and the run wrapper, so a source-tree diff would be empty and
    hide the actual change. Returns the number of diff lines written.
    """
    tmp = ws / "tmp"
    chunks: List[str] = []
    base_sh = next(iter(sorted(tmp.glob("*baseline*.sh"))), None)
    run_sh = next(iter(sorted(tmp.glob(f"*{run}*.sh"))), None)
    if base_sh and run_sh:
        r = subprocess.run(["diff", "-u", "--no-dereference", str(base_sh), str(run_sh)],
                           capture_output=True, text=True)
        # Key off output, not exit status (see _diff_trees).
        if r.stdout:
            chunks.append(f"# run wrapper: {base_sh.name} -> {run_sh.name}\n{r.stdout}")
    if not chunks:
        return 0
    out.parent.mkdir(parents=True, exist_ok=True)
    body = "\n".join(chunks)
    out.write_text(body)
    return len(body.splitlines())


def _session_capture_run_record_impl(
    run_id: str,
    run_name: str,
    prev_run_name: str = "",
    source_path: str = "",
    run_script: str = "",
    run_log: str = "",
    param_files: str = "",
    notes: str = "",
) -> str:
    """Standalone implementation of ``session_capture_run_record``.

    Persists, under ``<ws>/<run_name>/record/``, everything the final report
    will later need but which is otherwise lost when the next iteration
    overwrites it in place:

    * ``build_config/`` — ``object/setup_call``, ``object/Units``,
      ``object/Makefile.h``. The decisive optimization on Make-based apps lives
      here (serial vs parallel IO unit), and a source diff cannot see it.
    * ``params/`` — the run's parameter file(s). Apps like Flash-X always read a
      fixed filename (``flash.par``) from cwd, so each iteration overwrites it;
      without a snapshot only the last iteration's config survives.
    * ``scripts/run.sh`` — the exact wrapper used for this run.
    * ``meta.json`` — run name, timestamps, notes, and the captured file list.

    The run log is copied into ``artifacts/<run_name>_run.log`` so that every log
    lives under ``artifacts/`` rather than scattered across ``tmp/``.

    When *prev_run_name* is given, a ``patches/from_<prev>.record.diff`` is
    written capturing the build-config / params / script deltas — i.e. exactly
    what changed between the two iterations.
    """
    ws = _ws(run_id)
    if not ws.is_dir():
        return _err(f"workspace not found for run_id: {run_id}")

    run_dir = ws / run_name
    record = run_dir / "record"
    (record / "build_config").mkdir(parents=True, exist_ok=True)
    (record / "params").mkdir(parents=True, exist_ok=True)
    (run_dir / "scripts").mkdir(parents=True, exist_ok=True)
    artifacts = ws / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)

    captured: Dict[str, List[str]] = {"build_config": [], "params": [], "scripts": [], "logs": []}

    # Where is this run's built source tree?
    src = Path(source_path) if source_path else (ws / "annotated" / "source")

    for rel in _BUILD_CONFIG_FILES:
        f = src / rel
        if f.is_file():
            dst = record / "build_config" / Path(rel).name
            shutil.copy2(f, dst)
            captured["build_config"].append(dst.name)

    # Parameter files: explicit list wins, else the app's cwd-resident par file.
    wanted = [p.strip() for p in param_files.split(",") if p.strip()]
    if not wanted:
        guess = src / "object" / "flash.par"
        if guess.is_file():
            wanted = [str(guess)]
    for p in wanted:
        f = Path(p)
        if not f.is_absolute():
            f = src / "object" / p
        if f.is_file():
            shutil.copy2(f, record / "params" / f.name)
            captured["params"].append(f.name)

    if run_script:
        s = Path(run_script)
        if s.is_file():
            dst = run_dir / "scripts" / "run.sh"
            shutil.copy2(s, dst)
            dst.chmod(0o755)
            captured["scripts"].append("run.sh")

    # All logs belong under artifacts/, never tmp/ or the terminal only.
    if run_log:
        lg = Path(run_log)
        if lg.is_file():
            dst = artifacts / f"{run_name}_run.log"
            if lg.resolve() != dst.resolve():
                shutil.copy2(lg, dst)
            captured["logs"].append(dst.name)

    meta = {
        "run_name": run_name,
        "prev_run_name": prev_run_name or None,
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "source_path": str(src),
        "notes": notes,
        "captured": captured,
    }
    import json as _json
    (record / "meta.json").write_text(_json.dumps(meta, indent=2) + "\n")

    # Delta vs the previous iteration's record.
    patch_lines = 0
    if prev_run_name:
        prev = ws / prev_run_name / "record"
        if prev.is_dir():
            out = run_dir / "patches" / f"from_{prev_run_name}.record.diff"
            out.parent.mkdir(parents=True, exist_ok=True)
            r = subprocess.run(
                ["diff", "-ruN", "--no-dereference", str(prev), str(record)],
                capture_output=True, text=True,
            )
            if r.stdout:
                out.write_text(r.stdout)
                patch_lines = len(r.stdout.splitlines())

    return _ok(
        f"captured run record for {run_name}",
        record_dir=str(record),
        captured=captured,
        record_patch_lines=patch_lines,
    )


def register_final_report_tools(mcp: FastMCP) -> None:
    """Register the ``session_final_report`` MCP tool."""

    @mcp.tool()
    def session_capture_run_record(
        run_id: str,
        run_name: str,
        prev_run_name: str = "",
        source_path: str = "",
        run_script: str = "",
        run_log: str = "",
        param_files: str = "",
        notes: str = "",
    ) -> str:
        """Capture everything needed to reconstruct a run, before it is overwritten.

        **Call this at the END of every run-producing step** (``annotated``,
        ``baseline``, each ``opt<n>``). Iterations overwrite build config, the
        parameter file, and the run wrapper in place, so the information is gone
        by the time ``session_final_report`` runs unless it was captured here.

        Writes ``<run_name>/record/{build_config,params,meta.json}``,
        ``<run_name>/scripts/run.sh``, copies the log to
        ``artifacts/<run_name>_run.log``, and (with *prev_run_name*) a
        ``patches/from_<prev>.record.diff`` showing the iteration's actual delta.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label (``"annotated"``, ``"baseline"``, ``"opt1"``, ...).
            prev_run_name: Previous run to diff this record against.
            source_path: Source tree for this run. Defaults to ``annotated/source``.
            run_script: Path to the wrapper script used to launch the run.
            run_log: Path to the run's stdout/stderr log; copied into ``artifacts/``.
            param_files: Comma-separated parameter files (absolute, or relative to
                ``<source>/object/``). Defaults to ``object/flash.par`` if present.
            notes: Free-text description of what this iteration changed and why.

        Returns:
            JSON with ``record_dir``, the ``captured`` file lists, and
            ``record_patch_lines``.
        """
        return _session_capture_run_record_impl(
            run_id=run_id, run_name=run_name, prev_run_name=prev_run_name,
            source_path=source_path, run_script=run_script, run_log=run_log,
            param_files=param_files, notes=notes,
        )

    @mcp.tool()
    def session_final_report(
        run_id: str,
        report_md: str = "",
        conversation_md: str = "",
        readme_md: str = "",
        alloc_hint: str = "",
        validated: bool = False,
        validation_notes: str = "",
    ) -> str:
        """Assemble a self-contained, reproducible ``final_report/`` folder.

        Collects, from what is already on disk (never re-running the app):

        * ``config.ini`` — the ONLY place a real path/session value goes
          (``WORKSPACE_ROOT`` placeholder + app/compiler settings). Every
          script sources it via ``scripts/lib_load_config.sh`` instead of
          hardcoding a path, so nothing else here can leak a username or
          absolute user path (git-tracked deliverables must never embed PII).
        * ``scripts/`` — ``lib_load_config.sh`` (generic config loader),
          ``install.sh`` (rebuild deps + app), one ``run_<case>.sh`` per case
          (collected from ``tmp/`` and automatically re-pathed to use
          ``$WS``/``config.ini`` instead of the literal absolute path — see
          ``_anonymize_script``), and ``run_all.sh`` to drive them in order.
        * ``patches/`` — ``annotated.patch`` (baseline→annotated source) and,
          per optimization iteration, ``opt<n>.patch`` (source delta) plus
          ``opt<n>.config.diff`` (parameter-file / run-wrapper delta).
        * ``plan/`` — the final ``pipeline_plan.md`` that was executed, its
          changelog, and ``plan_evolution.diff`` vs the first tracked revision.
        * ``REPORT.md``, ``CONVERSATION.md``, ``README.md`` — narrative docs.
          Raw run/build logs are NOT copied in — they stay in the session
          workspace's ``artifacts/`` dir, out of the reproducible package.

        The three narrative documents are passed in by the caller: the agent
        knows what was found and why, and this tool must not invent results.

        **Mandatory before considering a session's final_report/ done:** run a
        self-contained validation — point ``OUTPUT_ROOT`` (in config.ini or
        the environment) at a scratch directory isolated from the session's
        own run data, fill in ``WORKSPACE_ROOT``, and execute
        ``scripts/run_all.sh <alloc-id>`` end to end using ONLY what's in
        ``final_report/``. Once it reproduces the reported results, re-call
        this tool with ``validated=True`` and a short ``validation_notes``
        summary — this stamps REPORT.md with a pass/fail marker instead of
        leaving reproducibility as an unverified claim.

        **``report_md`` detail bar (MANDATORY, not best-effort):** a short prose
        summary is not sufficient even for a session with many steps. Every
        baseline run gets its own row/section (not folded into one line), and
        every optimization considered — applied, applied-then-reverted, and
        not-applicable-with-reason — gets a row in an Optimization Ledger
        table (dimension | level | change | applied? | measured delta |
        replicates/significance | verdict). A rejected optimization (e.g. one
        that was tried and measured to make things worse) is exactly as
        reportable as a winning one — omitting it hides real work and risks
        someone re-trying it blind later. See the ``dftracer-report`` agent's
        "Report Structure" section for the full 10-section template this
        should follow.

        Args:
            run_id: Session identifier returned by ``session_create``.
            report_md: Markdown body for ``REPORT.md`` (what was done, results —
                see the detail-bar note above; follow the full structured
                template, not a condensed summary).
            conversation_md: Markdown body for ``CONVERSATION.md`` (narrative).
            readme_md: Markdown body for ``README.md`` — must give a reader who
                has ONLY this ``final_report/`` folder (no session workspace,
                no conversation history) exact, copy-pasteable commands: copy
                the folder, edit ``config.ini``'s ``WORKSPACE_ROOT``, the exact
                ``bash scripts/install.sh`` / ``bash scripts/run_all.sh
                <alloc-id>`` invocations, every script by name and what it
                does, and a pointer back to the ``REPORT.md`` numbers to
                compare against. ``readme_check`` (see Returns) fails if this
                is thin.
            alloc_hint: Default Flux allocation id baked into ``run_all.sh``.
            validated: Set True only after the self-contained validation above
                has actually been run and passed.
            validation_notes: One-line summary of what the validation run
                measured (e.g. "124.2s vs 124.0-124.6s expected band").

        Returns:
            JSON with ``status``, ``final_report_dir``, the ``runs`` discovered,
            a ``patches`` map of filename→line count, the ``scripts``/``config``
            written, ``logs_copied``, ``validated``, ``pdf`` (PDF render result),
            ``completeness`` (``{ok, missing, warnings}`` — REPORT.md structure
            check), ``readme_check`` (``{ok, missing}`` — the README smoke test:
            does it name every script, explain config.ini/WORKSPACE_ROOT, give
            a real runnable command, and point back to REPORT.md?), and (if
            anything above is not yet true) a ``reminder`` naming exactly what
            to fix before calling this session done.
        """
        return _session_final_report_impl(
            run_id=run_id,
            report_md=report_md,
            conversation_md=conversation_md,
            readme_md=readme_md,
            alloc_hint=alloc_hint,
            validated=validated,
            validation_notes=validation_notes,
        )
