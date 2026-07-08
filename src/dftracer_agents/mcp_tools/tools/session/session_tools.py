"""Session step tools — registers all ``session_*`` MCP tools onto a FastMCP instance.

This module implements the granular, step-by-step tools that an agent uses to drive
the dftracer annotation pipeline one stage at a time.  Each tool corresponds to a
single, well-defined pipeline step so that the agent can inspect intermediate results,
make decisions, and recover from failures before proceeding.

**MCP tool registration pattern**
    All tools are registered inside :func:`register_session_tools`, which is called
    once at server startup with the shared ``FastMCP`` instance.  Each inner function
    is decorated with ``@mcp.tool()``, which causes FastMCP to introspect its
    signature and docstring and expose it as a callable tool over the MCP protocol.
    Inner functions are *not* importable — they exist only as registered tools.

**Tools exposed to the agent**

    Workspace / file I/O
        * ``session_create``        — clone a Git repo into a timestamped workspace
        * ``session_list_files``    — glob files inside a workspace sub-folder
        * ``session_read_file``     — read a file from any workspace sub-folder
        * ``session_write_file``    — write (create or overwrite) a file in the workspace

    Detection & configuration
        * ``session_detect``        — detect language, build tool, and dftracer features
        * ``session_configure``     — configure the *original* build system

    Build & smoke-test (original source)
        * ``session_build_install`` — compile and install the original project
        * ``session_run_smoke_test``— run a smoke test without dftracer

    Annotation workflow
        * ``session_copy_annotated``    — copy source/ → annotated/ to begin instrumentation
        * ``session_patch_build``       — patch the build system to link dftracer
        * ``session_annotation_report`` — coverage report comparing source/ vs annotated/

    dftracer install & annotated build
        * ``session_install_dftracer``       — pip install dftracer with detected features
        * ``session_install_dftracer_utils`` — install dftracer-utils from the develop branch
        * ``session_build_annotated``        — build the annotated source with dftracer linked

    Trace collection & analysis
        * ``session_service_start``      — start dftracer_service background daemon (in dftracer_service.py)
        * ``session_run_with_dftracer``  — run a command with DFTRACER_* env vars set (DFTRACER_DATA_DIR=all)
        * ``session_service_stop``       — stop dftracer_service background daemon (in dftracer_service.py)
        * ``session_split_traces``       — compact raw .pfw traces via dftracer-utils split
        * ``session_analyze_traces``     — summarise traces with dftracer_info

    Configuration discovery (paper-backed)
        * ``session_search_papers_for_config`` — search arXiv + Semantic Scholar
          for benchmark-proven run parameters before production execution

    Session management
        * ``session_status``         — inspect the persisted state of a session
        * ``session_validate_structure``   — read-only check that the workspace
          matches the canonical baseline/annotated/opt<n> layout; refreshes
          ``session.json["paths"]``
        * ``session_reorganize_structure`` — quarantine legacy/drifted paths
          into ``artifacts/legacy/`` and rebuild the canonical skeleton

**Relationship to the broader pipeline**
    :func:`register_session_tools` provides the *individual* building blocks.
    The orchestrating pipeline (``session_run_pipeline`` in ``pipeline_tools.py``)
    calls these same underlying helpers directly rather than going through MCP, but
    an agent can also call every tool here individually for fine-grained control,
    debugging, or partial re-runs.

    Typical ordered usage::

        session_create → session_detect → session_configure → session_build_install
        → session_run_smoke_test → session_copy_annotated → session_patch_build
        (annotation via goose recipe subagents using session_read_file + session_write_file)
        → session_annotation_report  [confirm coverage]
        → session_install_dftracer → session_build_annotated

        Per-run (repeat for baseline / annotated / opt1 / opt2 / …):
        → session_init_run            (create <run>/source,patches,scripts,traces/{raw,compact})
        → session_snapshot_run_source (copy source into <run>/source/, diff → <run>/patches/)
        → session_run_with_dftracer   (run with traces → <run>/traces/raw/)
        → session_split_traces        (compact → <run>/traces/compact/)
        → session_analyze_traces      (dftracer_info on <run>/traces/compact/)

        Query helpers: session_list_runs, session_get_run_paths

    Persistent state for each session is stored in
    ``workspaces/<app>/<timestamp>/session.json`` and updated by ``_save_state``
    after every step.
"""
from __future__ import annotations

import json
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from .workspace import (
    _ws, _load_state, _save_state, _write_artifact_log,
    _ok, _err, _new_run_id, _create_run, _run, _workspaces_root,
    _safe_session_path,
)
from .detection import (
    _detect_info,
    _detect_system_mpi,
    _detect_system_hdf5,
    _mpi_version_compatible,
    _hdf5_version_compatible,
    _MPI_COMPATIBLE_DISPLAY,
    _HDF5_COMPATIBLE_SERIES,
)
from .annotation import (
    _strip_mpi_launcher,
    _generate_annotation_report,
)
from .build import (
    _patch_cmake, _patch_setup_py, _patch_pyproject,
    _patch_autotools_makefile,
)
from .install import (
    _ensure_session_venv, _install_dftracer_pip_direct,
    _dftracer_utils_split, _dftracer_utils_comparator,
    _dftracer_info_uncompressed_bytes, _install_dftracer_utils, _find_dftracer_dirs,
)
from .config_search import search_papers_for_config




# ---------------------------------------------------------------------------
# HPC environment helpers
# ---------------------------------------------------------------------------

def _extract_module_load_lines(source_dir: Path) -> List[str]:
    """Scan install/job scripts for 'ml ...' / 'module load ...' lines.

    Returns a deduplicated list of shell lines (preserving order) that load
    modules, suitable for prepending to any run command so that the same
    environment used by the app's own scripts is reproduced consistently.

    Scans *.sh, *.job, *.slurm, *.lsf, *.bsub files under source_dir.
    Skips comment lines.  Stops collecting once a non-module-load line that
    is not a blank or comment is encountered (avoids pulling in benchmark
    body commands).
    """
    script_extensions = {".sh", ".job", ".slurm", ".lsf", ".bsub"}
    module_re = re.compile(
        r"^\s*(ml\s+\S|module\s+load\s+\S|module\s+add\s+\S)", re.I
    )
    seen: set = set()
    lines: List[str] = []

    script_files = sorted(
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in script_extensions
    )
    for f in script_files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if module_re.match(stripped):
                # Lines are often compound (e.g. "ml load python/3.13.2 &&
                # python3 -m venv .venv && pip install ..."). Only keep the
                # leading module-load segment(s); stop at the first "&&"
                # segment that is not itself a module command, so we never
                # prepend venv-creation / pip-install / activate commands
                # into an unrelated run's preamble.
                segments = [s.strip() for s in stripped.split("&&")]
                kept = []
                for seg in segments:
                    if module_re.match(seg):
                        kept.append(seg)
                    else:
                        break
                module_only = " && ".join(kept)
                if module_only and module_only not in seen:
                    seen.add(module_only)
                    lines.append(module_only)
    return lines


def _build_module_preamble(source_dir: Path) -> str:
    """Return a shell snippet that loads modules extracted from app scripts.

    Returns an empty string if no module-load lines are found.
    The snippet initialises Lmod (sources /etc/profile.d/lmod.sh if present)
    before issuing the module commands so the preamble works even in minimal
    shell environments (e.g. inside flux run).
    """
    lines = _extract_module_load_lines(source_dir)
    if not lines:
        return ""
    parts = [
        "# Auto-detected module loads from app scripts",
        "[ -f /etc/profile.d/lmod.sh ] && source /etc/profile.d/lmod.sh",
    ] + lines
    return "\n".join(parts) + "\n"


def _has_mpi4py_dependency(source_dir: Path) -> bool:
    """Return True if the project declares mpi4py as a dependency."""
    import re as _re
    for fname in ("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt"):
        p = source_dir / fname
        if not p.exists():
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        if _re.search(r"\bmpi4py\b", text, _re.I):
            return True
    return False


# ---------------------------------------------------------------------------
# Module-level implementation functions — exposed so optimization tools in
# optimizations/ can import and call them directly without going through MCP.
# The @mcp.tool() wrappers inside register_session_tools() delegate to these.
# ---------------------------------------------------------------------------

def _session_build_annotated_impl(
    run_id: str,
    jobs: int = 4,
    extra_cmake_flags: str = "",
) -> str:
    """Standalone implementation of session_build_annotated."""
    ws = _ws(run_id)
    ann = ws / "annotated"
    build_ann = ws / "build_ann"
    install_ann = ws / "install_ann"
    build_ann.mkdir(exist_ok=True)
    install_ann.mkdir(exist_ok=True)

    if not ann.exists():
        return _err("annotated/ not found — run session_copy_annotated first")

    state = _load_state(run_id)
    info = state.get("detection") or _detect_info(ws / "source")
    bt = info.get("build_tool", "unknown")
    dft_prefix = state.get("dftracer_install_prefix")

    steps: Dict[str, Any] = {}

    if bt == "cmake":
        flags = [
            f"-DCMAKE_INSTALL_PREFIX={install_ann}",
            "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
        ]
        if dft_prefix:
            flags.append(f"-DCMAKE_PREFIX_PATH={dft_prefix}")
        flags += (extra_cmake_flags.split() if extra_cmake_flags else [])
        r_cfg = _run(
            ["cmake", "-S", str(ann), "-B", str(build_ann)] + flags,
            timeout=300,
        )
        steps["configure"] = r_cfg
        if not r_cfg["success"]:
            return _err("cmake configure failed for annotated source", **r_cfg)

        r_bld = _run(["make", f"-j{jobs}"], cwd=build_ann, timeout=600)
        steps["build"] = r_bld
        if not r_bld["success"]:
            return _err("make failed for annotated source", **r_bld)

        r_ins = _run(["make", "install"], cwd=build_ann, timeout=300)
        steps["install"] = r_ins
        if not r_ins["success"]:
            return _err("make install failed for annotated source", **r_ins)

    elif bt == "autotools":
        for deps_dir in ann.rglob(".deps"):
            if deps_dir.is_dir():
                shutil.rmtree(deps_dir, ignore_errors=True)
        for deps_dir in build_ann.rglob(".deps"):
            if deps_dir.is_dir():
                shutil.rmtree(deps_dir, ignore_errors=True)
        if (ann / "configure.ac").exists() and not (ann / "configure").exists():
            _run(["autoreconf", "-fi"], cwd=ann, timeout=120)

        env: Dict[str, str] = {}
        if dft_prefix:
            pc_path = state.get("dftracer_pkg_config_path", "")
            if not pc_path:
                pc_path = f"{dft_prefix}/lib/pkgconfig"
            env["PKG_CONFIG_PATH"] = pc_path
            env["CPPFLAGS"] = f"-I{dft_prefix}/include"
            env["LDFLAGS"]  = f"-L{dft_prefix}/lib -Wl,-rpath,{dft_prefix}/lib"

        r_cfg = _run(
            [str(ann / "configure"), f"--prefix={install_ann}",
             "--disable-dependency-tracking"],
            cwd=build_ann,
            env=env if env else None,
            timeout=300,
        )
        steps["configure"] = r_cfg
        if not r_cfg["success"]:
            return _err("configure failed for annotated source", **r_cfg)

        r_bld = _run(
            ["make", f"-j{jobs}"],
            cwd=build_ann, env=env if env else None, timeout=600,
        )
        steps["build"] = r_bld
        if not r_bld["success"]:
            return _err("make failed for annotated source", **r_bld)

        r_ins = _run(["make", "install"], cwd=build_ann, timeout=300)
        steps["install"] = r_ins

    elif bt == "python":
        pip = ws / "install" / "bin" / "pip"
        if not pip.exists():
            pip = Path(sys.executable).parent / "pip"
        r_bld = _run([str(pip), "install", "-e", str(ann)], timeout=300)
        steps["pip_install"] = r_bld
        if not r_bld["success"]:
            return _err("pip install failed for annotated source", **r_bld)

    else:
        return _err(f"Unsupported build tool: {bt}")

    _save_state(run_id, {"step": "annotated_built"})
    return _ok("Annotated build succeeded", build_tool=bt, steps=steps)


def _ensure_flux_proxy_wrapper(command: str, ws: Path, script_name: str) -> str:
    """If *command* uses ``flux proxy`` with inline shell, rewrite it as a wrapper script.

    When a command matches ``flux proxy <JOBID> bash -c "..."`` or any form where
    the payload after ``flux proxy <JOBID>`` is not already a path to an existing
    ``.sh`` file, we write the payload into a wrapper script under ``<ws>/tmp/``
    that sources lmod and exports environment variables, then rewrite the command
    to ``flux proxy <JOBID> bash <wrapper_script>``.

    This is necessary because ``flux proxy <JOBID> bash -c "..."`` does not
    propagate ``module load`` / lmod state into subprocesses; a wrapper script
    that sources ``/usr/share/lmod/lmod/init/bash`` is the only reliable method.
    """
    import re as _re
    import shlex as _shlex

    # Match: flux proxy <JOBID> <rest>
    m = _re.match(r'^(flux\s+proxy\s+\S+)\s+(.+)$', command.strip(), _re.DOTALL)
    if not m:
        return command

    proxy_prefix = m.group(1)   # e.g. "flux proxy f3GW7fbdvodR"
    rest = m.group(2).strip()   # e.g. 'bash -c "module load ..."' or 'bash /path/to/script.sh'

    # If rest already references an existing "bash <script.sh>" invocation
    # anywhere in the string (e.g. "flux run ... bash wrapper.sh args..."),
    # no wrapping is needed — the referenced script is expected to handle
    # its own lmod/module initialisation internally.
    bash_script_m = _re.search(r'\bbash\s+(\S+\.sh)\b', rest)
    if bash_script_m:
        script_path = bash_script_m.group(1)
        if Path(script_path).exists():
            return command  # already wrapped

    # Otherwise, the rest is an inline command — write it to a wrapper script.
    tmp_dir = ws / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    wrapper = tmp_dir / script_name
    # Strip outer "bash -c '...'" or 'bash -c "..."' if present.
    inner_m = _re.match(r'^bash\s+-c\s+[\'"](.+)[\'"]$', rest, _re.DOTALL)
    payload = inner_m.group(1) if inner_m else rest
    wrapper.write_text(
        "#!/bin/bash\n"
        "source /usr/share/lmod/lmod/init/bash\n"
        f"{payload}\n"
    )
    wrapper.chmod(0o755)
    return f"{proxy_prefix} bash {wrapper}"


def _run_dir(ws: Path, run_name: str) -> Path:
    """Return ``<ws>/<run_name>/`` creating it if needed.

    The per-run directory layout is:

    .. code-block:: text

        <run_name>/
          source/          ← snapshot of the source tree for this run
          patches/         ← diffs vs previous run's source snapshot
          scripts/         ← launch scripts used for this run
          traces/
            raw/           ← DFTRACER_LOG_FILE prefix; raw .pfw.gz files
            compact/       ← compacted output from session_split_traces

    *run_name* is one of: ``"baseline"`` (original, unannotated app),
    ``"annotated"`` (dftracer-instrumented baseline), ``"opt1"`` / ``"opt2"``
    / … (successive optimisation iterations).
    """
    d = ws / run_name
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_source_dir(ws: Path, run_name: str) -> Path:
    """Return and create ``<ws>/<run_name>/source/``."""
    d = _run_dir(ws, run_name) / "source"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_patches_dir(ws: Path, run_name: str) -> Path:
    """Return and create ``<ws>/<run_name>/patches/``."""
    d = _run_dir(ws, run_name) / "patches"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_traces_raw(ws: Path, run_name: str) -> Path:
    """Return and create ``<ws>/<run_name>/traces/raw/``."""
    d = _run_dir(ws, run_name) / "traces" / "raw"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_traces_compact(ws: Path, run_name: str) -> Path:
    """Return and create ``<ws>/<run_name>/traces/compact/``."""
    d = _run_dir(ws, run_name) / "traces" / "compact"
    d.mkdir(parents=True, exist_ok=True)
    return d


_SCRIPT_STUB = "#!/usr/bin/env bash\n"


def _ensure_script_stub(scripts_dir: Path, name: str) -> None:
    """Create ``<scripts_dir>/<name>`` with an executable stub if it doesn't exist."""
    p = scripts_dir / name
    if not p.exists():
        p.write_text(_SCRIPT_STUB)
        p.chmod(0o755)


def _init_structure(ws: Path, dataset_path: Optional[str] = None) -> Dict[str, Any]:
    """Build the canonical session directory skeleton under *ws*, idempotently.

    Layout created (existing paths/files are left untouched)::

        baseline/source/, baseline/scripts/{compile.sh,run.sh}
        annotated/source/, annotated/scripts/{compile.sh,run.sh},
                  annotated/traces/{raw,compact}/, annotated/analysis-diagnostics/
        artifacts/
        tmp/
        dataset/            ← symlink to dataset_path if given, else empty dir
        session_report.md   ← skeleton header, not overwritten if present

    ``opt<n>/`` run directories are intentionally *not* pre-created here — they
    are created lazily and on-demand by ``_run_dir(ws, f"opt{n}")`` when the
    optimization loop actually starts a new iteration, using the same
    ``source/patches/scripts/traces/{raw,compact}`` sub-layout as ``annotated/``.

    Args:
        ws: Session workspace root (created if missing).
        dataset_path: Absolute path to a dataset directory (typically on a
            parallel filesystem such as Lustre) to symlink as ``dataset/``.
            When omitted or when the symlink cannot be created, ``dataset/``
            is created as a plain empty directory instead.

    Returns:
        Dict[str, Any]: Paths and flags describing what was created, suitable
            for merging into an MCP tool's JSON response.
    """
    ws.mkdir(parents=True, exist_ok=True)

    baseline_source = _run_source_dir(ws, "baseline")
    baseline_scripts = _run_dir(ws, "baseline") / "scripts"
    baseline_scripts.mkdir(parents=True, exist_ok=True)
    _ensure_script_stub(baseline_scripts, "compile.sh")
    _ensure_script_stub(baseline_scripts, "run.sh")

    annotated_source = _run_source_dir(ws, "annotated")
    annotated_scripts = _run_dir(ws, "annotated") / "scripts"
    annotated_scripts.mkdir(parents=True, exist_ok=True)
    _ensure_script_stub(annotated_scripts, "compile.sh")
    _ensure_script_stub(annotated_scripts, "run.sh")
    _run_traces_raw(ws, "annotated")
    _run_traces_compact(ws, "annotated")
    (_run_dir(ws, "annotated") / "analysis-diagnostics").mkdir(parents=True, exist_ok=True)

    artifacts = ws / "artifacts"
    artifacts.mkdir(exist_ok=True)

    tmp = ws / "tmp"
    tmp.mkdir(exist_ok=True)

    dataset = ws / "dataset"
    dataset_kind = "none"
    if dataset_path:
        if dataset.is_symlink() or dataset.exists():
            dataset_kind = "existing"
        else:
            try:
                dataset.symlink_to(Path(dataset_path).resolve())
                dataset_kind = "symlink"
            except OSError:
                dataset.mkdir(exist_ok=True)
                dataset_kind = "dir_fallback"
    elif not dataset.exists():
        dataset.mkdir(exist_ok=True)
        dataset_kind = "dir"
    else:
        dataset_kind = "existing"

    report = ws / "session_report.md"
    if not report.exists():
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        report.write_text(f"# Session Report: {ws.name}\n\n_Generated {ts}_\n")

    return {
        "baseline": str(_run_dir(ws, "baseline")),
        "annotated": str(_run_dir(ws, "annotated")),
        "artifacts": str(artifacts),
        "tmp": str(tmp),
        "dataset": str(dataset),
        "dataset_kind": dataset_kind,
        "session_report": str(report),
    }


_LEGACY_TOP_LEVEL_NAMES = (
    "source", "build", "install", "build_ann", "install_ann", "venv",
    "dftracer_src", "dftracer_build", "traces", "traces_split",
    "annotation_logs", "dfanalyzer_checkpoint", "diagnosis", "diagnosis.json",
    "step_timings.json",
)
"""Top-level names used by the older, pre-refactor flat pipeline layout
(``pipeline_tools.py`` / ``annotation.py`` / ``install.py``).  None of these
belong in the canonical ``baseline/annotated/opt<n>`` run-scoped tree built by
``_init_structure`` — their presence indicates structure drift that
``session_reorganize_structure`` should quarantine under ``artifacts/legacy/``.
"""

_CANONICAL_TOP_LEVEL_NAMES = (
    "baseline", "annotated", "artifacts", "tmp", "dataset",
    "session_report.md", "session.json",
)


def _opt_run_names(ws: Path) -> List[str]:
    """Return sorted ``opt<n>`` run directory names that already exist under *ws*."""
    if not ws.is_dir():
        return []
    return sorted(
        (p.name for p in ws.iterdir() if p.is_dir() and re.fullmatch(r"opt\d+", p.name)),
        key=lambda n: int(n[3:]),
    )


def _expected_paths(ws: Path) -> Dict[str, Any]:
    """Compute the full canonical path map for *ws*, without creating anything.

    Includes ``baseline``, ``annotated``, and every ``opt<n>`` run directory
    that already exists on disk, each with its ``source/patches/scripts/
    traces/{raw,compact}`` sub-paths, plus the session-level singletons
    (``artifacts``, ``tmp``, ``dataset``, ``session_report``, ``session_json``).

    This is the single source of truth persisted into ``session.json["paths"]``
    by ``session_validate_structure`` and ``session_reorganize_structure`` so
    that any MCP tool can look up "where does this belong" without re-deriving
    the layout itself.
    """
    def _run_paths(run_name: str) -> Dict[str, str]:
        run_d = ws / run_name
        return {
            "run_dir": str(run_d),
            "source_dir": str(run_d / "source"),
            "patches_dir": str(run_d / "patches"),
            "scripts_dir": str(run_d / "scripts"),
            "traces_raw": str(run_d / "traces" / "raw"),
            "traces_compact": str(run_d / "traces" / "compact"),
        }

    runs = {"baseline": _run_paths("baseline"), "annotated": _run_paths("annotated")}
    for opt_name in _opt_run_names(ws):
        runs[opt_name] = _run_paths(opt_name)

    return {
        "runs": runs,
        "artifacts": str(ws / "artifacts"),
        "tmp": str(ws / "tmp"),
        "dataset": str(ws / "dataset"),
        "session_report": str(ws / "session_report.md"),
        "session_json": str(ws / "session.json"),
    }


def _snapshot_source(src: Path, dest: Path) -> Dict[str, Any]:
    """Copy *src* tree into *dest*, overwriting existing files.

    Uses ``rsync`` when available for speed; falls back to ``shutil.copytree``
    otherwise.  Returns a dict with ``file_count`` and ``success``.
    """
    import subprocess as _sp
    dest.mkdir(parents=True, exist_ok=True)
    try:
        r = _sp.run(
            ["rsync", "-a", "--delete", f"{src}/", f"{dest}/"],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode == 0:
            count = sum(1 for _ in dest.rglob("*") if _.is_file())
            return {"success": True, "file_count": count, "tool": "rsync"}
    except (FileNotFoundError, _sp.TimeoutExpired):
        pass
    # Fallback: shutil. Preserve symlinks (rsync -a already does) and tolerate
    # dangling ones so repos like Flash-X (with links to not-yet-generated
    # targets) snapshot without crashing.
    import shutil as _sh
    _sh.rmtree(str(dest), ignore_errors=True)
    _sh.copytree(str(src), str(dest), symlinks=True, ignore_dangling_symlinks=True)
    count = sum(1 for _ in dest.rglob("*") if _.is_file())
    return {"success": True, "file_count": count, "tool": "shutil"}


def _generate_patch(from_dir: Path, to_dir: Path, patch_file: Path) -> str:
    """Generate a unified diff between *from_dir* and *to_dir*, write to *patch_file*.

    Excludes ``.git/`` metadata.  Returns ``"ok"`` on success, or an error string.
    """
    import subprocess as _sp
    try:
        patch_file.parent.mkdir(parents=True, exist_ok=True)
        # Find changed files (excluding .git) then produce unified diff per file
        changed = _sp.run(
            ["diff", "-rq", "--exclude=.git", str(from_dir), str(to_dir)],
            capture_output=True, text=True, timeout=30,
        )
        lines: list[str] = []
        for line in changed.stdout.splitlines():
            parts = line.split()
            # "Files <f1> and <f2> differ" → two path tokens
            if len(parts) >= 4 and parts[0] == "Files" and parts[2] == "and":
                f1, f2 = parts[1], parts[3]
                d = _sp.run(["diff", "-u", f1, f2], capture_output=True, text=True, timeout=10)
                lines.append(d.stdout)
        content = "".join(lines) if lines else "# no differences\n"
        patch_file.write_text(content)
        return "ok"
    except Exception as e:
        return str(e)


def _session_run_with_dftracer_impl(
    run_id: str,
    command: str,
    subfolder: str = "build_ann",
    data_dir: str = "all",
    timeout: int = 600,
    env_extra: Optional[str] = None,
    run_name: str = "baseline",
    allocation_id: Optional[str] = None,
    nnodes: Optional[int] = None,
    ntasks: Optional[int] = None,
) -> str:
    """Standalone implementation of session_run_with_dftracer."""
    ws = _ws(run_id)

    # Traces always go into the session workspace under <run_name>/traces/raw/.
    # App I/O data (datasets, checkpoints, run outputs) goes to Lustre — traces
    # do NOT go to Lustre because the MCP analysis tools (session_split_traces,
    # session_analyze_traces, session_optimization_iteration) read from the
    # workspace directory, not from Lustre.
    #
    # Clear any stale files left behind by a previous attempt for this same
    # run_name (e.g. an orphaned flux job whose Python-side subprocess.run
    # was killed by a timeout but which kept running on the cluster and wrote
    # trace files after the fact) — otherwise a later, successful attempt
    # would silently read a mix of old and new trace data.
    import shutil as _sh_traces
    _sh_traces.rmtree(str(_run_dir(ws, run_name) / "traces" / "raw"), ignore_errors=True)
    traces_dir = _run_traces_raw(ws, run_name)

    cwd = ws / subfolder
    if not cwd.exists():
        cwd = ws / "build"
    if not cwd.exists():
        cwd = ws / "source"

    # Use <run_name>-<attempt> as the trace file prefix so files are named
    # <run_name>-<attempt>-<hash>-.pfw.gz. The attempt suffix is unique per
    # call (not just per run_name) so that an orphaned process from a killed
    # attempt can never write into the same file prefix a later attempt uses,
    # even if it isn't actually terminated by the timeout.
    import uuid as _uuid_traces
    attempt_suffix = _uuid_traces.uuid4().hex[:8]
    log_file_prefix = str(traces_dir / f"{run_name}-{attempt_suffix}")

    env: Dict[str, str] = {
        "DFTRACER_ENABLE": "1",
        "DFTRACER_INC_METADATA": "1",
        "DFTRACER_LOG_FILE": log_file_prefix,
        "DFTRACER_DATA_DIR": data_dir,
    }
    if env_extra:
        import json as _json
        env.update(_json.loads(env_extra))

    # For flux proxy commands, always use a wrapper script — inline bash -c
    # does not propagate module loads reliably inside flux proxy subprocesses.
    command = _ensure_flux_proxy_wrapper(command, ws, f"run_with_dftracer_{run_name}.sh")

    # Allocation-aware run: wrap with flux proxy + flux run using all nodes
    if allocation_id:
        _nnodes = nnodes if nnodes else 1
        _ntasks = ntasks if ntasks else _nnodes
        flux_flags = f"-N {_nnodes} -n {_ntasks} --exclusive"
        # Forward all DFTRACER env vars and LD_LIBRARY_PATH/LD_PRELOAD to ranks
        for key in list(env.keys()):
            flux_flags += f" -x {key}"
        command = f"flux proxy {allocation_id} flux run {flux_flags} {command}"

    # Prepend module-load preamble from the app's own scripts for ABI consistency.
    preamble = _build_module_preamble(ws / "source")
    run_command = (preamble + command) if preamble else command

    r = _run(["/bin/bash", "-c", run_command], cwd=cwd, env=env, timeout=timeout)
    _save_state(run_id, {
        "step": "ran_with_dftracer",
        "dftracer_run": {"command": command, "run_name": run_name, **r},
        f"last_run_{run_name}": {"command": command, **r},
    })
    _write_artifact_log(ws, 11, "session_run_with_dftracer", {"command": command, "run_name": run_name, "result": r, "traces_dir": str(traces_dir)}, run_id)
    if r["success"]:
        return _ok("Command completed with dftracer", run_name=run_name, traces_dir=str(traces_dir), **r)
    return _err("Command failed with dftracer", run_name=run_name, traces_dir=str(traces_dir), **r)


def _session_split_traces_impl(
    run_id: str,
    app_name: str = "app",
    run_name: str = "baseline",
) -> str:
    """Standalone implementation of session_split_traces."""
    ws = _ws(run_id)
    traces_in = _run_traces_raw(ws, run_name)
    traces_out = _run_traces_compact(ws, run_name)

    if not traces_in.exists():
        return _err(f"{run_name}/traces/raw/ not found in session {run_id} — run session_run_with_dftracer first")

    trace_files = list(traces_in.rglob("*.pfw")) + list(traces_in.rglob("*.pfw.gz"))
    if not trace_files:
        return _err(f"No .pfw or .pfw.gz files found in {traces_in}")

    _SPLIT_CHUNK_MB = 512  # default chunk size — 512 MB balances index overhead vs granularity
    if len(trace_files) == 1:
        uncompressed = _dftracer_info_uncompressed_bytes(str(trace_files[0]))
        if uncompressed is not None and uncompressed <= _SPLIT_CHUNK_MB * 1024 * 1024:
            import shutil as _shutil
            dest = traces_out / trace_files[0].name
            _shutil.copy2(trace_files[0], dest)
            r = {"success": True, "returncode": 0,
                 "stdout": (f"skipped split: single file {trace_files[0].name} "
                            f"uncompressed {uncompressed / (1024*1024):.1f} MB ≤ {_SPLIT_CHUNK_MB} MB chunk size"),
                 "stderr": ""}
            _save_state(run_id, {"step": f"traces_split_{run_name}", "split_result": r})
            _write_artifact_log(ws, 12, "session_split_traces", r, run_id)
            return _ok(
                f"Traces copied without splitting (single file {uncompressed / (1024*1024):.1f} MB uncompressed ≤ {_SPLIT_CHUNK_MB} MB)",
                run_name=run_name, output=str(traces_out), **r,
            )

    r = _dftracer_utils_split(
        directory=str(traces_in),
        output_dir=str(traces_out),
        app_name=app_name,
        chunk_size_mb=_SPLIT_CHUNK_MB,
    )
    _save_state(run_id, {"step": f"traces_split_{run_name}", "split_result": r})
    _write_artifact_log(ws, 12, "session_split_traces", r, run_id)
    if r["success"]:
        return _ok("Traces split successfully", run_name=run_name, output=str(traces_out), **r)
    return _err("dftracer_split failed", **r)


def _session_validate_traces_impl(
    run_id: str,
    run_name: str = "baseline",
    traces_dir: Optional[str] = None,
) -> str:
    """Standalone implementation of session_validate_traces.

    Validates ``.pfw``/``.pfw.gz`` trace files using dftracer-utils' own
    indexing/parsing pipeline (``dftracer_event_count``), rather than any
    ad hoc JSON check: a file that fails to parse or index is reported by
    the tool itself as ``failed`` in its ``indexed=.. skipped=.. failed=..``
    summary line, so this reuses the exact mechanism dftracer-utils uses to
    detect truncated/corrupted trace files (e.g. a worker killed mid-write).
    """
    ws = _ws(run_id)
    if not ws.exists():
        return _err(f"Session {run_id} not found")

    if traces_dir is not None:
        d = Path(traces_dir)
    else:
        compact = _run_traces_compact(ws, run_name)
        raw = _run_traces_raw(ws, run_name)
        has_compact = any(compact.glob("*.pfw*"))
        d = compact if has_compact else raw

    if not d.exists():
        return _err(f"Traces directory not found: {d}")

    trace_files = list(d.glob("*.pfw")) + list(d.glob("*.pfw.gz"))
    if not trace_files:
        return _err(f"No .pfw or .pfw.gz files found in {d}")

    # Use a run-scoped index dir so --force always rebuilds the index fresh
    # (a shared/default index-dir would let cached results from a prior,
    # possibly corrupted, run mask the summary line this parses below).
    index_dir = _run_dir(ws, run_name) / "tmp" / "validate_index"
    index_dir.mkdir(parents=True, exist_ok=True)
    r = _run(
        ["dftracer_event_count", "--directory", str(d),
         "--index-dir", str(index_dir), "--force"],
        timeout=300,
    )
    if not r["success"]:
        return _err("dftracer_event_count failed", **r)

    log = r["stderr"] + "\n" + r["stdout"]
    m = re.search(
        r"indexed=(\d+)\s+skipped=(\d+)\s+failed=(\d+)", log
    )
    indexed = skipped = failed = None
    if m:
        indexed, skipped, failed = (int(g) for g in m.groups())

    valid_events = None
    stdout_lines = [l.strip() for l in r["stdout"].splitlines() if l.strip()]
    if stdout_lines and stdout_lines[-1].isdigit():
        valid_events = int(stdout_lines[-1])

    result = {
        "directory": str(d),
        "trace_file_count": len(trace_files),
        "indexed": indexed,
        "skipped": skipped,
        "failed": failed,
        "valid_events": valid_events,
        "stderr_tail": r["stderr"][-2000:],
    }
    _save_state(run_id, {"step": f"traces_validated_{run_name}", "validate_result": result})
    _write_artifact_log(ws, 12, "session_validate_traces", result, run_id)

    if failed is not None and failed > 0:
        return _err(
            f"{failed} of {indexed + skipped + failed} trace file(s) failed "
            f"dftracer_event_count validation (corrupted/truncated)",
            **result,
        )
    return _ok(
        f"All {trace_files and (indexed if indexed is not None else len(trace_files))} "
        f"trace file(s) validated cleanly ({valid_events if valid_events is not None else '?'} valid events)",
        **result,
    )


def _session_collect_system_info_impl(run_id: str) -> str:
    """Standalone implementation of session_collect_system_info."""
    import json as _json

    ws = _ws(run_id)
    if not ws.exists():
        return _err(f"Session {run_id} not found")

    def _human(kb: int) -> str:
        n = kb * 1024
        for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n //= 1024
        return f"{n:.1f} PiB"

    def _cmd(args: List[str], t: int = 10) -> str:
        r = _run(args, timeout=t)
        return r["stdout"] if r["success"] else ""

    # ── CPU ──────────────────────────────────────────────────────────
    cpu: Dict[str, Any] = {}
    lscpu_raw = _cmd(["lscpu", "--json"])
    if lscpu_raw:
        try:
            fields = {
                item["field"].rstrip(":"): item["data"]
                for item in _json.loads(lscpu_raw).get("lscpu", [])
            }
            cpu = {
                "architecture":    fields.get("Architecture"),
                "model_name":      fields.get("Model name"),
                "vendor":          fields.get("Vendor ID"),
                "sockets":         fields.get("Socket(s)"),
                "cores_per_socket":fields.get("Core(s) per socket"),
                "threads_per_core":fields.get("Thread(s) per core"),
                "logical_cpus":    fields.get("CPU(s)"),
                "min_mhz":         fields.get("CPU min MHz"),
                "max_mhz":         fields.get("CPU max MHz"),
                "numa_nodes":      fields.get("NUMA node(s)"),
                "l1d_cache":       fields.get("L1d cache"),
                "l1i_cache":       fields.get("L1i cache"),
                "l2_cache":        fields.get("L2 cache"),
                "l3_cache":        fields.get("L3 cache"),
            }
        except Exception:
            pass
    if not cpu:
        cpuinfo = _cmd(["cat", "/proc/cpuinfo"])
        models = [l.split(":", 1)[1].strip() for l in cpuinfo.splitlines()
                  if l.startswith("model name")]
        mhz = [l.split(":", 1)[1].strip() for l in cpuinfo.splitlines()
               if l.startswith("cpu MHz")]
        cpu = {
            "model_name":   models[0] if models else None,
            "logical_cpus": str(len(models)),
            "mhz_samples":  mhz[:8],
        }

    # ── Memory ───────────────────────────────────────────────────────
    memory: Dict[str, Any] = {}
    for line in _cmd(["cat", "/proc/meminfo"]).splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        try:
            val_kb = int(parts[1])
        except ValueError:
            continue
        if key == "MemTotal":
            memory["total_kb"] = val_kb
            memory["total"] = _human(val_kb)
        elif key == "MemAvailable":
            memory["available_kb"] = val_kb
            memory["available"] = _human(val_kb)
        elif key == "MemFree":
            memory["free_kb"] = val_kb
        elif key == "Buffers":
            memory["buffers_kb"] = val_kb
        elif key == "Cached":
            memory["cached_kb"] = val_kb
        elif key == "SwapTotal":
            memory["swap_total_kb"] = val_kb
            memory["swap_total"] = _human(val_kb)
        elif key == "SwapFree":
            memory["swap_free_kb"] = val_kb

    # ── Network ──────────────────────────────────────────────────────
    interfaces: List[Dict[str, Any]] = []
    link_json = _cmd(["ip", "-j", "link", "show"])
    if link_json:
        try:
            for iface in _json.loads(link_json):
                entry: Dict[str, Any] = {
                    "name":  iface.get("ifname"),
                    "type":  iface.get("link_type"),
                    "flags": iface.get("flags", []),
                    "mtu":   iface.get("mtu"),
                    "state": iface.get("operstate"),
                    "mac":   iface.get("address"),
                }
                eth = _cmd(["ethtool", iface.get("ifname", "")], t=5)
                for eth_line in eth.splitlines():
                    if "Speed:" in eth_line:
                        entry["speed"] = eth_line.split(":", 1)[1].strip()
                    elif "Duplex:" in eth_line:
                        entry["duplex"] = eth_line.split(":", 1)[1].strip()
                    elif "Port:" in eth_line:
                        entry["port_type"] = eth_line.split(":", 1)[1].strip()
                interfaces.append(entry)
        except Exception:
            pass
    if not interfaces:
        for line in _cmd(["cat", "/proc/net/dev"]).splitlines()[2:]:
            if ":" in line:
                interfaces.append({"name": line.split(":")[0].strip()})

    # ── Filesystems ───────────────────────────────────────────────────
    mounts: List[Dict[str, Any]] = []
    for line in _cmd(["df", "-Th"]).splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 7:
            mounts.append({
                "filesystem": parts[0],
                "type":       parts[1],
                "size":       parts[2],
                "used":       parts[3],
                "avail":      parts[4],
                "use_pct":    parts[5],
                "mount":      parts[6],
            })

    # ── Host / OS ────────────────────────────────────────────────────
    hostname = _cmd(["hostname", "-f"]).strip() or _cmd(["hostname"]).strip()
    kernel   = _cmd(["uname", "-r"]).strip()
    os_release: Dict[str, str] = {}
    for line in _cmd(["cat", "/etc/os-release"]).splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os_release[k] = v.strip('"')

    info: Dict[str, Any] = {
        "hostname":    hostname,
        "kernel":      kernel,
        "os_release":  os_release,
        "cpu":         cpu,
        "memory":      memory,
        "network":     {"interfaces": interfaces},
        "filesystems": mounts,
    }

    config_path = ws / "system_config.json"
    config_path.write_text(_json.dumps(info, indent=2))

    _save_state(run_id, {
        "step": "system_info_collected",
        "system_config": str(config_path),
    })
    _write_artifact_log(ws, 14, "session_collect_system_info", {
        "hostname":    hostname,
        "cpu_model":   cpu.get("model_name"),
        "memory_total":memory.get("total"),
        "interfaces":  len(interfaces),
        "mounts":      len(mounts),
    }, run_id)

    return _ok(
        "System configuration collected",
        config_file=str(config_path),
        hostname=hostname,
        cpu=cpu,
        memory_total=memory.get("total"),
        network_interfaces=len(interfaces),
        filesystem_mounts=len(mounts),
    )


def register_session_tools(mcp: FastMCP) -> None:  # noqa: C901  (long but intentional)
    """Register all ``session_*`` MCP tools onto *mcp*.

    This function is called once at MCP server startup.  Each nested function
    decorated with ``@mcp.tool()`` becomes a separately callable tool in the
    agent's tool palette.  The nesting pattern gives every inner function access
    to *mcp* via closure without polluting the module namespace.

    Registered tools (in pipeline order):
        ``session_create``, ``session_detect``, ``session_list_files``,
        ``session_read_file``, ``session_write_file``, ``session_configure``,
        ``session_build_install``, ``session_run_smoke_test``,
        ``session_copy_annotated``, ``session_patch_build``,
        ``session_annotation_report``,
        ``session_install_dftracer``,
        ``session_install_dftracer_utils``, ``session_build_annotated``,
        ``session_init_run``, ``session_snapshot_run_source``,
        ``session_get_run_paths``, ``session_list_runs``,
        ``session_run_with_dftracer``, ``session_split_traces``,
        ``session_analyze_traces``, ``session_status``,
        ``session_collect_system_info``, ``session_generate_dftracer_pc``,
        ``session_search_papers_for_config``.

    Optimization tools (diagnose, search, iteration, proposals, levels) are
    registered separately via
    :func:`~tools.optimizations.register_optimization_tools`.

    Args:
        mcp: The shared ``FastMCP`` server instance onto which tools are
            registered via ``@mcp.tool()`` decorators.
    """

    @mcp.tool()
    def session_create(
        url: str,
        ref: str = "main",
        run_id: Optional[str] = None,
        dataset_path: Optional[str] = None,
    ) -> str:
        """Clone a Git repository into a new, isolated session workspace.

        Creates a timestamped workspace directory under
        ``workspaces/<app_name>/<YYYYMMDD_HHMMSS>/`` (where *app_name* is
        derived from *url*), clones *url* at *ref* into the ``source/``
        sub-folder, builds the canonical session directory skeleton (see
        ``session_init_structure``), and persists initial session state to
        ``session.json``.

        A shallow clone (``--depth 1``) is attempted first for speed.  If the
        branch/tag does not exist on the remote the tool retries with a bare
        clone followed by ``git checkout <ref>``.

        Side effects:
            * Creates ``workspaces/<app>/<ts>/source/`` on disk (the raw clone).
            * Creates ``baseline/source/`` and ``annotated/source/`` as copies
              of the clone, plus ``baseline/scripts/``, ``annotated/scripts/``,
              ``annotated/traces/{raw,compact}/``, ``artifacts/``, ``tmp/``,
              ``dataset/`` (symlinked to *dataset_path* if given), and
              ``session_report.md`` — see ``session_init_structure``.
            * Writes ``workspaces/<app>/.current_run`` pointer so that
              ``pipeline_get_run_id`` can recall this session.
            * Persists ``{"url", "ref", "step": "cloned"}`` to ``session.json``.

        Args:
            url: Git URL to clone (https or ssh).
            ref: Branch, tag, or commit SHA to checkout (default: ``"main"``).
            run_id: Optional fixed RUN-ID string.  A UUID-based ID is generated
                when omitted.
            dataset_path: Optional absolute path (typically on a parallel
                filesystem such as Lustre) to symlink as ``dataset/`` in the
                workspace.  When omitted, ``dataset/`` is created as a plain
                empty directory.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — human-readable confirmation.
                * ``run_id`` — the session identifier for all subsequent calls.
                * ``workspace`` — absolute path to the session root directory.
                * ``source`` — absolute path to the cloned source tree.
                * ``baseline``, ``annotated``, ``artifacts``, ``tmp``, ``dataset``,
                  ``session_report`` — paths created by ``session_init_structure``.

        Raises:
            Returns ``{"status": "error"}`` JSON when both clone attempts fail,
            with ``clone_stderr`` carrying the git error output.

        Note:
            This must be the first tool called in a new annotation session.
            All other ``session_*`` tools require a valid *run_id* produced here.
        """
        # _create_run derives app name from the URL and creates
        # workspaces/<app>/<uid>/ for a NEW session. A supplied run_id is a
        # resume handle: it must reference an existing session or _create_run
        # raises FileNotFoundError (we never invent a new run under that name).
        try:
            rid, ws = _create_run(url, run_id)
        except FileNotFoundError as e:
            return _err(str(e))

        src = ws / "source"
        src.mkdir(exist_ok=True)

        # Resume: if source/ is already populated (existing session), skip the
        # clone entirely — re-cloning into a non-empty dir would fail and would
        # clobber any local state.
        already_cloned = any(src.iterdir())
        if not already_cloned:
            clone_result = _run(
                ["git", "clone", "--depth", "1", "--branch", ref, url, str(src)],
                timeout=300,
            )
            if not clone_result["success"]:
                # Retry without --branch (bare clone then checkout)
                shutil.rmtree(src, ignore_errors=True)
                src.mkdir(exist_ok=True)
                r2 = _run(["git", "clone", "--depth", "1", url, str(src)], timeout=300)
                if not r2["success"]:
                    return _err("git clone failed", clone_stderr=r2["stderr"])
                _run(["git", "checkout", ref], cwd=src)

        structure = _init_structure(ws, dataset_path)
        for target_name in ("baseline", "annotated"):
            target_source = Path(structure[target_name]) / "source"
            if not any(target_source.iterdir()):
                # symlinks=True preserves repo symlinks as symlinks (matching the
                # clone) instead of dereferencing them; ignore_dangling_symlinks
                # keeps the copy from crashing on links whose target is absent in
                # a shallow clone (e.g. Flash-X's physics/.../StirMain/TurbGen.h).
                shutil.copytree(
                    src, target_source, dirs_exist_ok=True,
                    symlinks=True, ignore_dangling_symlinks=True,
                )

        _save_state(rid, {
            "url": url,
            "ref": ref,
            "step": "cloned",
            "structure_dataset_path": dataset_path,
        })
        return _ok(
            f"Session {rid} created",
            run_id=rid,
            workspace=str(ws),
            source=str(src),
            **structure,
        )

    @mcp.tool()
    def session_init_structure(
        run_id: str,
        dataset_path: Optional[str] = None,
    ) -> str:
        """Build (or repair) the canonical session directory skeleton.

        Normally invoked automatically by ``session_create``; exposed as a
        standalone tool so callers can re-run it idempotently — e.g. to attach
        a ``dataset_path`` symlink after the fact, or to restore directories
        removed via ``session_remove_path``.

        Directory layout created (existing paths/files are left untouched)::

            baseline/source/, baseline/scripts/{compile.sh,run.sh}
            annotated/source/, annotated/scripts/{compile.sh,run.sh},
                      annotated/traces/{raw,compact}/, annotated/analysis-diagnostics/
            artifacts/
            tmp/
            dataset/            ← symlink to dataset_path if given, else empty dir
            session_report.md   ← skeleton header, not overwritten if present

        ``opt<n>/`` run directories are intentionally not pre-created — they
        are created lazily by the optimization loop using the same
        ``source/patches/scripts/traces/{raw,compact}`` sub-layout as ``annotated/``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            dataset_path: Optional absolute path to symlink as ``dataset/``.
                Ignored if ``dataset/`` already exists (file, dir, or symlink).

        Returns:
            JSON string with keys: ``status``, ``message``, ``baseline``,
            ``annotated``, ``artifacts``, ``tmp``, ``dataset``, ``dataset_kind``,
            ``session_report``.
        """
        ws = _ws(run_id)
        structure = _init_structure(ws, dataset_path)
        _save_state(run_id, {
            "structure_dataset_path": dataset_path,
            "paths": _expected_paths(ws),
        })
        return _ok(f"Session structure ready for {run_id}", **structure)

    @mcp.tool()
    def session_detect(
        run_id: str,
        hdf5_prefix: Optional[str] = None,
        mpi_prefix: Optional[str] = None,
        mpicc: Optional[str] = None,
        mpicxx: Optional[str] = None,
    ) -> str:
        """Detect the programming language, build tool, and dftracer feature flags.

        Optional overrides let the caller pin the SAME libraries the application
        was built with, so dftracer links against them (not a stray system copy):

        * ``hdf5_prefix`` — install prefix of the HDF5 the app uses (e.g. a
          source-built HDF5 in the workspace like ``<WS>/hdf5_1.14``). Detection
          probes ``<prefix>/bin/h5pcc``/``h5cc`` for the version + parallel flag
          instead of scanning ``/usr``.
        * ``mpi_prefix`` — MPI install prefix; its ``bin/mpicc``/``mpicxx`` are
          used for the compile-based version probe.
        * ``mpicc`` / ``mpicxx`` — explicit wrapper paths (override ``mpi_prefix``).

        Analyses the cloned ``source/`` tree to determine how the project is
        built, which languages it uses, and which optional dftracer features
        (MPI, HDF5, Python bindings) are appropriate.  The detection results
        guide every downstream step — ``session_configure``, ``session_patch_build``,
        and ``session_install_dftracer`` all read the
        ``detection`` key written by this tool.

        Side effects:
            * Persists ``{"detection": <info>, "step": "detected"}`` to
              ``session.json`` via ``_save_state``.
            * Writes an artifact log entry at step 2
              (``<workspace>/annotation_logs/02_session_detect.json``).

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"Detection complete"``.
                * ``languages`` — list of detected languages (e.g. ``["c", "cpp"]``).
                * ``build_tool`` — one of ``"cmake"``, ``"autotools"``,
                  ``"make"``, ``"python"``, or ``"unknown"``.
                * ``features`` — dict of detected optional features
                  (``{"mpi": bool, "hdf5": bool, "python": bool, ...}``).
                * ``dftracer_cmake_flags`` — recommended ``-D`` flags for cmake.
                * ``dftracer_pip_env`` — complete env var dict for pip install.
                * Additional keys from ``_detect_info`` (readme excerpt, key files, etc.).

        Raises:
            Returns ``{"status": "error"}`` when ``source/`` does not exist
            (i.e. ``session_create`` has not been run for this *run_id*).

        Note:
            Must be called after ``session_create`` and before ``session_configure``.
        """
        src = _ws(run_id) / "source"
        if not src.exists():
            return _err("source/ not found — run session_create first")

        info = _detect_info(src, hdf5_prefix=hdf5_prefix, mpi_prefix=mpi_prefix,
                            mpicc=mpicc, mpicxx=mpicxx)
        # Persist the overrides so re-detection (configure/install) stays pinned.
        overrides = {k: v for k, v in {
            "hdf5_prefix": hdf5_prefix, "mpi_prefix": mpi_prefix,
            "mpicc": mpicc, "mpicxx": mpicxx,
        }.items() if v}
        _save_state(run_id, {"detection": info, "step": "detected",
                             **({"detect_overrides": overrides} if overrides else {})})
        _write_artifact_log(_ws(run_id), 2, "session_detect", info, run_id)
        return _ok("Detection complete", **info)

    @mcp.tool()
    def session_list_files(
        run_id: str,
        subfolder: str = "source",
        pattern: str = "**/*",
        max_results: int = 100,
    ) -> str:
        """List files inside a workspace sub-folder using a glob pattern.

        Useful for exploring the directory layout of ``source/``, ``annotated/``,
        ``build/``, ``install/``, or any other sub-folder that exists in the
        session workspace.

        Args:
            run_id: Session identifier returned by ``session_create``.
            subfolder: Sub-folder to list relative to the workspace root.
                Common values: ``"source"``, ``"annotated"``, ``"build"``,
                ``"install"``, ``"traces"``.  Defaults to ``"source"``.
            pattern: ``pathlib.Path.glob``-compatible pattern relative to
                *subfolder*.  Defaults to ``"**/*"`` (all files recursively).
            max_results: Maximum number of file paths to return.  Paths are
                returned in filesystem order; results are truncated after this
                count.  Defaults to ``100``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"<N> files found"``.
                * ``files`` — list of paths relative to *subfolder* (strings).

        Raises:
            Returns ``{"status": "error"}`` when *subfolder* does not exist in
            the session workspace.
        """
        base = _ws(run_id) / subfolder
        if not base.exists():
            return _err(f"{subfolder}/ does not exist in session {run_id}")
        paths = [
            str(p.relative_to(base))
            for p in base.glob(pattern)
            if p.is_file()
        ][:max_results]
        return _ok(f"{len(paths)} files found", files=paths)

    @mcp.tool()
    def session_read_file(
        run_id: str,
        filepath: str,
        subfolder: str = "source",
        max_bytes: int = 32768,
    ) -> str:
        """Read a file from the workspace for inspection or annotation.

        The agent typically reads files from ``source/`` to understand the
        original code, and from ``annotated/`` to verify or correct
        instrumentation applied by goose recipe subagents or by hand.

        Args:
            run_id: Session identifier returned by ``session_create``.
            filepath: Path to the file relative to *subfolder* (e.g.
                ``"src/main.c"`` or ``"CMakeLists.txt"``).
            subfolder: Workspace sub-folder containing the file.  Defaults to
                ``"source"``.  Use ``"annotated"`` to read the instrumented copy.
            max_bytes: Maximum number of bytes to return.  Content is truncated
                to this limit; the ``truncated`` field in the response indicates
                whether truncation occurred.  Defaults to ``32768`` (32 KiB).

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"File read"``.
                * ``filepath`` — the *filepath* argument as passed.
                * ``subfolder`` — the *subfolder* argument as passed.
                * ``content`` — file text (UTF-8, with replacement characters for
                  undecodable bytes), truncated to *max_bytes*.
                * ``truncated`` — ``true`` if the file was larger than *max_bytes*.

        Raises:
            Returns ``{"status": "error"}`` when the file does not exist.
        """
        try:
            p = _safe_session_path(_ws(run_id), f"{subfolder}/{filepath}")
        except ValueError as exc:
            return _err(str(exc))
        if not p.exists():
            return _err(f"File not found: {subfolder}/{filepath}")
        content = p.read_text(errors="replace")[:max_bytes]
        truncated = len(content) == max_bytes
        return _ok(
            "File read",
            filepath=filepath,
            subfolder=subfolder,
            content=content,
            truncated=truncated,
        )

    @mcp.tool()
    def session_write_file(
        run_id: str,
        filepath: str,
        content: str,
        subfolder: str = "annotated",
    ) -> str:
        """Write (create or overwrite) a file inside the workspace.

        The primary use-case is applying LLM-generated dftracer annotations to
        files in the ``annotated/`` sub-folder.  The agent reads a file with
        ``session_read_file``, adds ``DFTRACER_C_FUNCTION_START`` /
        ``DFTRACER_C_FUNCTION_END`` macros (or ``@dft_fn`` for Python), and
        writes the result back with this tool.

        Intermediate directories are created automatically if they do not exist.

        Side effects:
            * Creates or overwrites
              ``<workspace>/<subfolder>/<filepath>`` on disk.

        Args:
            run_id: Session identifier returned by ``session_create``.
            filepath: Destination path relative to *subfolder* (e.g.
                ``"src/main.c"``).  Parent directories are created as needed.
            content: Complete file content to write (UTF-8 string).  The existing
                file, if any, is replaced in full — partial updates are not
                supported.
            subfolder: Workspace sub-folder to write into.  Defaults to
                ``"annotated"``.  Use ``"source"`` with caution as overwriting
                the original makes it impossible to diff changes later.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"Wrote <N> bytes to <subfolder>/<filepath>"``.
        """
        try:
            p = _safe_session_path(_ws(run_id), f"{subfolder}/{filepath}")
        except ValueError as exc:
            return _err(str(exc))
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return _ok(f"Wrote {len(content)} bytes to {subfolder}/{filepath}")

    @mcp.tool()
    def session_remove_path(
        run_id: str,
        relpath: str,
        recursive: bool = False,
    ) -> str:
        """Remove a file, directory, or symlink inside the session workspace.

        Strictly sandboxed to the calling session's workspace: *relpath* is
        resolved and validated with :func:`_safe_session_path`, which rejects
        ``..``-traversal, absolute-path injection, and any resolved path that
        falls outside ``<workspace>/``.  The workspace root itself and
        ``session.json`` can never be removed through this tool.

        Symlinks (e.g. the ``dataset`` link created by ``session_init_structure``)
        are unlinked directly — the link node is removed, its target is never
        followed or touched.

        Args:
            run_id: Session identifier returned by ``session_create``.
            relpath: Path to remove, relative to the session workspace root
                (e.g. ``"tmp/scratch.bin"`` or ``"opt3"``).
            recursive: Must be ``True`` to remove a non-empty directory.
                Defaults to ``False`` as a safety guard against accidental
                large deletions.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — human-readable summary.
                * ``removed`` — resolved path that was removed (on success).
                * ``kind`` — one of ``"file"``, ``"dir"``, ``"symlink"``.

        Raises:
            Returns ``{"status": "error"}`` when *relpath* escapes the
            workspace, targets the workspace root or ``session.json``, does
            not exist, or is a non-empty directory with ``recursive=False``.
        """
        ws = _ws(run_id)
        try:
            p = _safe_session_path(ws, relpath)
        except ValueError as exc:
            return _err(str(exc))

        if p.name == "session.json" and p.parent == ws.resolve():
            return _err("refusing to remove session.json")

        if not p.exists() and not p.is_symlink():
            return _err(f"path does not exist: {relpath}")

        if p.is_symlink():
            p.unlink()
            return _ok(f"Removed symlink {relpath}", removed=str(p), kind="symlink")

        if p.is_dir():
            if any(p.iterdir()) and not recursive:
                return _err(
                    f"{relpath} is a non-empty directory — pass recursive=True to remove it",
                    removed=str(p),
                )
            shutil.rmtree(p)
            return _ok(f"Removed directory {relpath}", removed=str(p), kind="dir")

        p.unlink()
        return _ok(f"Removed file {relpath}", removed=str(p), kind="file")

    @mcp.tool()
    def session_configure(
        run_id: str,
        extra_cmake_flags: str = "",
        extra_configure_flags: str = "",
        extra_pip_flags: str = "",
    ) -> str:
        """Configure the build system for the *original* cloned source.

        Runs the appropriate configuration command based on the build tool
        detected by ``session_detect`` (or re-detected inline if the detection
        step was skipped):

        * **cmake** — ``cmake -S source -B build -DCMAKE_INSTALL_PREFIX=install
          -DCMAKE_BUILD_TYPE=RelWithDebInfo [extra_cmake_flags]``
        * **autotools** — ``autoreconf -fi`` (if ``configure`` does not exist),
          then ``./configure --prefix=<install> [extra_configure_flags]``
        * **python** — ``python3 -m venv install/`` followed by
          ``pip install -e source/ [extra_pip_flags]``

        Side effects:
            * Creates ``<workspace>/build/`` and ``<workspace>/install/``.
            * For Python projects, creates a virtualenv at ``<workspace>/install/``.
            * Persists ``{"step": "configured", "build_tool": <bt>}`` to
              ``session.json``.
            * Writes an artifact log at step 3.

        Args:
            run_id: Session identifier returned by ``session_create``.
            extra_cmake_flags: Space-separated additional ``-D`` flags appended
                to the cmake invocation (e.g. ``"-DENABLE_TESTS=OFF"``).
                Ignored for non-cmake projects.  Defaults to ``""``.
            extra_configure_flags: Space-separated flags appended to
                ``./configure``.  Ignored for non-autotools projects.
                Defaults to ``""``.
            extra_pip_flags: Space-separated flags appended to
                ``pip install -e``.  Ignored for non-Python projects.
                Defaults to ``""``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"Configure succeeded"`` or error description.
                * ``build_tool`` — detected build system string.
                * ``stdout``, ``stderr``, ``returncode`` — subprocess output.

        Raises:
            Returns ``{"status": "error"}`` for unsupported build tools or when
            the configuration command exits non-zero.

        Note:
            Must be called after ``session_detect`` so that ``session.json``
            contains the ``detection`` key.  If ``session_detect`` was not called,
            detection is re-run inline.
        """
        ws = _ws(run_id)
        src = ws / "source"
        build = ws / "build"
        install = ws / "install"
        build.mkdir(exist_ok=True)
        install.mkdir(exist_ok=True)

        state = _load_state(run_id)
        info = state.get("detection") or _detect_info(src)
        bt = info.get("build_tool", "unknown")

        if bt == "cmake":
            flags = [
                f"-DCMAKE_INSTALL_PREFIX={install}",
                "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
            ] + (extra_cmake_flags.split() if extra_cmake_flags else [])
            r = _run(
                ["cmake", "-S", str(src), "-B", str(build)] + flags,
                timeout=300,
            )
        elif bt == "autotools":
            # Clean stale .deps dirs — these cause config.status to fail
            for deps_dir in src.rglob(".deps"):
                if deps_dir.is_dir():
                    shutil.rmtree(deps_dir, ignore_errors=True)
            for deps_dir in build.rglob(".deps"):
                if deps_dir.is_dir():
                    shutil.rmtree(deps_dir, ignore_errors=True)
            # Bootstrap if needed
            if (src / "configure.ac").exists() and not (src / "configure").exists():
                _run(["autoreconf", "-fi"], cwd=src, timeout=120)
            flags = [
                f"--prefix={install}",
                "--disable-dependency-tracking",  # avoids config.status .deps failures
            ] + (extra_configure_flags.split() if extra_configure_flags else [])
            r = _run([str(src / "configure")] + flags, cwd=build, timeout=300)
        elif bt == "python":
            venv_r = _run([sys.executable, "-m", "venv", str(install)], timeout=60)
            if not venv_r["success"]:
                return _err("venv creation failed", **venv_r)
            pip = install / "bin" / "pip"
            flags = ["install", "-e", str(src)] + (
                extra_pip_flags.split() if extra_pip_flags else []
            )
            # mpi4py must be compiled against the system MPI (not a wheel) so
            # that it uses the same ABI as the rest of the MPI stack.
            pip_env: Dict[str, str] = {}
            if _has_mpi4py_dependency(src):
                if "--no-binary=mpi4py" not in flags:
                    flags.insert(flags.index(str(src)) + 1, "--no-binary=mpi4py")
                # Point CC/CXX at MPI wrappers for consistent compilation.
                import shutil as _shutil
                mpicc = info.get("mpi_impl", {}).get("mpicc") or _shutil.which("mpicc") or ""
                mpicxx = info.get("mpi_impl", {}).get("mpicxx") or _shutil.which("mpicxx") or _shutil.which("mpic++") or ""
                if mpicc:
                    pip_env["CC"] = mpicc
                if mpicxx:
                    pip_env["CXX"] = mpicxx
            r = _run([str(pip)] + flags, env=pip_env if pip_env else None, timeout=300)
        else:
            return _err(f"Unsupported build tool: {bt}")

        _save_state(run_id, {"step": "configured", "build_tool": bt})
        _write_artifact_log(_ws(run_id), 3, "session_configure", {"build_tool": bt, "result": r}, run_id)
        if not r["success"]:
            return _err("Configure failed", **r)
        return _ok("Configure succeeded", build_tool=bt, **r)

    @mcp.tool()
    def session_build_install(
        run_id: str,
        jobs: int = 4,
    ) -> str:
        """Compile and install the original project after ``session_configure``.

        Runs the appropriate build command based on the ``build_tool`` persisted
        by ``session_configure``:

        * **cmake / autotools / make** — ``make -j<jobs>`` followed by
          ``make install``.  Both commands run in ``<workspace>/build/``.
        * **python** — no-op; installation was already performed by
          ``session_configure`` (``pip install -e``).

        Side effects:
            * Populates ``<workspace>/install/`` with installed binaries/libraries.
            * Persists ``{"step": "installed"}`` to ``session.json``.
            * Writes an artifact log at step 4.

        Args:
            run_id: Session identifier returned by ``session_create``.
            jobs: Number of parallel ``make`` jobs.  Defaults to ``4``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — description of the outcome.
                * For cmake/autotools: ``make`` and ``install`` sub-dicts each
                  containing ``stdout``, ``stderr``, ``returncode``.

        Raises:
            Returns ``{"status": "error"}`` when ``make`` or ``make install``
            exits non-zero, or when the ``build_tool`` stored in state is
            unrecognised (``"unknown"``).

        Note:
            Must be called after ``session_configure``.
        """
        ws = _ws(run_id)
        build = ws / "build"
        install = ws / "install"
        state = _load_state(run_id)
        bt = state.get("build_tool", "unknown")

        if bt in {"cmake", "autotools", "make"}:
            r = _run(["make", f"-j{jobs}"], cwd=build, timeout=600)
            if not r["success"]:
                _write_artifact_log(_ws(run_id), 4, "session_build_install", {"build": r}, run_id)
                return _err("make failed", **r)
            r2 = _run(["make", "install"], cwd=build, timeout=300)
            _write_artifact_log(_ws(run_id), 4, "session_build_install", {"build": r, "install": r2}, run_id)
            if not r2["success"]:
                return _err("make install failed", **r2)
            _save_state(run_id, {"step": "installed"})
            return _ok("Build and install succeeded", make=r, install=r2)

        if bt == "python":
            _save_state(run_id, {"step": "installed"})
            _write_artifact_log(_ws(run_id), 4, "session_build_install", {"status": "python project installed via pip"}, run_id)
            return _ok("Python project installed via session_configure (pip install -e)")

        return _err(f"Unknown build tool: {bt}")

    @mcp.tool()
    def session_run_smoke_test(
        run_id: str,
        command: str,
        subfolder: str = "build",
        env_extra: Optional[str] = None,
        timeout: int = 300,
    ) -> str:
        """Run a smoke test command inside the workspace as a single process.

        Executes *command* without MPI or any parallel launcher to verify that
        the original build is functional before annotation begins.  Any MPI /
        parallel launcher prefix (``mpirun``, ``mpiexec``, ``srun``, ``jsrun``,
        ``aprun``, ``flux run``) is automatically stripped from *command* so
        the binary runs directly.

        This stripping is intentional: smoke tests must be deterministic and
        reproducible without a cluster scheduler or MPI runtime.  The stripped
        command and a boolean ``mpi_launcher_stripped`` flag are both included
        in the response so the agent can audit what was actually executed.

        Side effects:
            * Persists ``{"last_smoke_test": {command, ...subprocess result}}``
              to ``session.json``.
            * Writes an artifact log at step 5.

        Args:
            run_id: Session identifier returned by ``session_create``.
            command: Shell command to execute (passed to ``/bin/sh -c``).
                MPI launchers are stripped automatically — pass the original
                command unchanged; this tool will sanitise it.
            subfolder: Working-directory sub-folder relative to the workspace
                root.  Defaults to ``"build"``.  Falls back to ``"source"``
                if *subfolder* does not exist.
            env_extra: Optional JSON object string (``'{"VAR": "val"}'``) of
                additional environment variables merged into the subprocess
                environment.  Defaults to ``None`` (no extra variables).
            timeout: Seconds before the subprocess is killed.
                Defaults to ``300``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"Smoke test passed"`` or ``"Smoke test failed"``.
                * ``command_run`` — the sanitised command that was actually executed.
                * ``mpi_launcher_stripped`` — ``true`` if a launcher prefix was removed.
                * ``stdout``, ``stderr``, ``returncode`` — subprocess output.

        Raises:
            Returns ``{"status": "error"}`` when the command exits non-zero.

        Note:
            Must be called after ``session_build_install``.  A failure here does
            not block the annotation phase — ``session_run_pipeline`` treats a
            failed smoke test as a warning and continues.
        """
        cwd = _ws(run_id) / subfolder
        if not cwd.exists():
            cwd = _ws(run_id) / "source"

        env = {}
        if env_extra:
            env = json.loads(env_extra)

        safe_command, stripped = _strip_mpi_launcher(command)

        # For flux proxy commands, always use a wrapper script — inline bash -c
        # does not propagate module loads reliably inside flux proxy subprocesses.
        safe_command = _ensure_flux_proxy_wrapper(safe_command, _ws(run_id), "run_smoke_test.sh")

        # Prepend module-load preamble extracted from the app's own scripts so
        # the run environment is consistent with what the app's job scripts use.
        preamble = _build_module_preamble(_ws(run_id) / "source")
        if preamble:
            safe_command = preamble + safe_command

        r = _run(["/bin/bash", "-c", safe_command], cwd=cwd, env=env, timeout=timeout)
        _save_state(run_id, {"last_smoke_test": {"command": safe_command, **r}})
        _write_artifact_log(_ws(run_id), 5, "session_run_smoke_test", {
            "command_original": command,
            "command_run": safe_command,
            "mpi_launcher_stripped": stripped,
            "result": r,
        }, run_id)
        if r["success"]:
            return _ok(
                "Smoke test passed",
                command_run=safe_command,
                mpi_launcher_stripped=stripped,
                **r,
            )
        return _err(
            "Smoke test failed",
            command_run=safe_command,
            mpi_launcher_stripped=stripped,
            **r,
        )

    @mcp.tool()
    def session_wait_flux_job(
        alloc_id: str,
        job_id: str,
        poll_interval: int = 30,
        timeout: int = 7200,
        log_file: Optional[str] = None,
    ) -> str:
        """Wait for a Flux job (inside an allocation) to complete, polling until done.

        Uses ``flux proxy <alloc_id> flux jobs -a`` to poll the job state every
        *poll_interval* seconds until the job reaches a terminal state (CD, CA, F)
        or *timeout* seconds elapse.

        After the job finishes, optionally tails the last 50 lines of *log_file*
        (if provided) so the caller can see the final output without reading the
        whole file.

        Args:
            alloc_id: The allocation JOBID to proxy into (e.g. ``"f3Gb7i5BCZsM"``).
            job_id: The inner job JOBID to wait for (e.g. ``"f6H3GwWJpo"``).
            poll_interval: Seconds between status polls. Defaults to ``30``.
            timeout: Maximum seconds to wait. Defaults to ``7200`` (2 hours).
            log_file: Optional absolute path to a log file whose last 50 lines
                are returned in the response after the job finishes.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``, ``"error"``, or ``"timeout"``).
                * ``message`` — human-readable summary.
                * ``job_state`` — final flux job state (``"CD"``, ``"CA"``, ``"F"``).
                * ``elapsed`` — seconds waited.
                * ``log_tail`` — last 50 lines of *log_file* (if provided and exists).
        """
        import time as _time
        import subprocess as _sp

        start = _time.time()
        terminal = {"CD", "CA", "F"}
        job_state = None
        elapsed = 0

        while elapsed < timeout:
            try:
                result = _sp.run(
                    ["flux", "proxy", alloc_id, "flux", "jobs", "-a"],
                    capture_output=True, text=True, timeout=30,
                )
                for line in result.stdout.splitlines():
                    parts = line.split()
                    if parts and parts[0] == job_id:
                        job_state = parts[3] if len(parts) > 3 else None
                        break
            except Exception:
                pass

            if job_state in terminal:
                break

            _time.sleep(poll_interval)
            elapsed = int(_time.time() - start)

        elapsed = int(_time.time() - start)

        log_tail = ""
        if log_file:
            try:
                with open(log_file, "r") as fh:
                    lines = fh.readlines()
                    log_tail = "".join(lines[-50:])
            except Exception:
                log_tail = "(could not read log file)"

        if elapsed >= timeout and job_state not in terminal:
            return json.dumps({
                "status": "timeout",
                "message": f"Job {job_id} did not finish within {timeout}s",
                "job_state": job_state,
                "elapsed": elapsed,
                "log_tail": log_tail,
            })

        ok = job_state == "CD"
        return json.dumps({
            "status": "ok" if ok else "error",
            "message": f"Job {job_id} finished with state {job_state} after {elapsed}s",
            "job_state": job_state,
            "elapsed": elapsed,
            "log_tail": log_tail,
        })

    @mcp.tool()
    def session_copy_annotated(run_id: str) -> str:
        """Copy the original source tree to ``annotated/`` ready for instrumentation.

        Performs a full recursive copy of ``<workspace>/source/`` to
        ``<workspace>/annotated/``.  If ``annotated/`` already exists (e.g.
        from a previous attempt) it is deleted first so the copy starts clean.

        The agent subsequently uses ``session_read_file`` /
        ``session_write_file`` on *subfolder* ``"annotated"`` to apply
        dftracer instrumentation macros without touching the pristine
        ``source/`` tree.

        Side effects:
            * Removes any pre-existing ``<workspace>/annotated/`` directory.
            * Creates a fresh ``<workspace>/annotated/`` that is an exact
              copy of ``<workspace>/source/``.
            * Persists ``{"step": "annotated_copy_created"}`` to
              ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"Copied source to <path>"``.

        Raises:
            Returns ``{"status": "error"}`` when ``source/`` does not exist.

        Note:
            Must be called after ``session_create``.  Typically called after
            ``session_run_smoke_test`` to confirm the original build is working
            before beginning instrumentation.
        """
        ws = _ws(run_id)
        src = ws / "source"
        dst = ws / "annotated"
        if not src.exists():
            return _err("source/ not found — run session_create first")
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst, symlinks=True, ignore_dangling_symlinks=True)
        _save_state(run_id, {"step": "annotated_copy_created"})
        return _ok(f"Copied source to {dst}")

    @mcp.tool()
    def session_patch_build(run_id: str) -> str:
        """Patch the build system in ``annotated/`` to link dftracer.

        Modifies the build files inside ``<workspace>/annotated/`` so that the
        project is compiled and linked against the dftracer library.  The exact
        changes depend on the build tool detected by ``session_detect``:

        * **cmake** — injects ``find_package(dftracer REQUIRED)`` and
          ``target_link_libraries(... dftracer::dftracer)`` into
          ``CMakeLists.txt`` (root and one level of sub-projects).
        * **autotools** — prepends hardcoded dftracer include/lib flags to
          ``Makefile.am`` / ``Makefile.in`` / ``Makefile`` files found in
          ``annotated/``, and appends ``-ldftracer_core`` to the ``LIBS``
          assignment in ``.in`` and generated ``Makefile`` files so the library
          is linked *after* object files (required for correct symbol resolution).
        * **python** — adds ``"dftracer"`` to ``install_requires`` in
          ``setup.py`` and/or the ``dependencies`` table in
          ``pyproject.toml``.

        Side effects:
            * Overwrites one or more build files inside
              ``<workspace>/annotated/`` in place.
            * Persists ``{"step": "build_patched"}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"Patched <N> build file(s)"``.
                * ``patched`` — list of build file paths (relative to
                  ``annotated/``) that were modified.
                * ``build_tool`` — the detected build system string.

        Raises:
            Returns ``{"status": "error"}`` when ``annotated/`` does not exist.

        Note:
            Must be called after ``session_copy_annotated``.
        """
        ws = _ws(run_id)
        ann = ws / "annotated"
        if not ann.exists():
            return _err("annotated/ not found — run session_copy_annotated first")

        patched: List[str] = []
        state = _load_state(run_id)
        info = state.get("detection") or _detect_info(ws / "source")
        bt = info.get("build_tool", "unknown")

        # Paths saved by session_install_dftracer; rediscover if not yet set.
        pip_inc = state.get("dftracer_pip_include_dir", "")
        pip_lib = state.get("dftracer_pip_lib_dir", "")
        if not pip_inc:
            cmake_prefix = ws / "install_ann"
            dirs = _find_dftracer_dirs(cmake_prefix=cmake_prefix if cmake_prefix.exists() else None)
            if dirs:
                pip_inc = dirs.get("include_dir", "")
                pip_lib = dirs.get("lib_dir", "")

        if bt == "cmake":
            cml = ann / "CMakeLists.txt"
            if cml.exists():
                cml.write_text(_patch_cmake(cml, pip_inc, pip_lib))
                patched.append("CMakeLists.txt")
            # Recurse one level for sub-projects
            for sub in ann.iterdir():
                if sub.is_dir():
                    scml = sub / "CMakeLists.txt"
                    if scml.exists():
                        scml.write_text(_patch_cmake(scml, pip_inc, pip_lib))
                        patched.append(str(scml.relative_to(ann)))
            # Also check annotated/src/ for deeper sub-trees
            for src_sub in ann.rglob("CMakeLists.txt"):
                rel = str(src_sub.relative_to(ann))
                if rel == "CMakeLists.txt" or rel in patched:
                    continue
                src_sub.write_text(_patch_cmake(src_sub, pip_inc, pip_lib))
                patched.append(rel)

        elif bt in ("autotools", "make"):
            # Patch all Makefiles found under annotated/ (including src/)
            for mf in ann.rglob("Makefile*"):
                new_content = _patch_autotools_makefile(mf, pip_inc, pip_lib)
                if new_content != mf.read_text():
                    mf.write_text(new_content)
                    patched.append(str(mf.relative_to(ann)))

        elif bt == "python":
            for name, fn in (
                ("setup.py", _patch_setup_py),
                ("pyproject.toml", _patch_pyproject),
            ):
                p = ann / name
                if p.exists():
                    p.write_text(fn(p))
                    patched.append(name)

        _save_state(run_id, {"step": "build_patched"})
        return _ok(
            f"Patched {len(patched)} build file(s)",
            patched=patched,
            build_tool=bt,
            pip_include_dir=pip_inc or "(none)",
            pip_lib_dir=pip_lib or "(none)",
        )

    @mcp.tool()
    def session_annotation_report(run_id: str) -> str:
        """Show a coverage report comparing ``source/`` against ``annotated/``.

        Generates a structured report that lets the agent (and user) verify
        annotation completeness before committing to the annotated build.  The
        report is produced in four steps:

        1. Diff ``source/`` and ``annotated/`` to find files that were changed.
        2. Detect all C/C++ function definitions in each relevant source file
           using a regex scanner.
        3. Check which functions carry ``DFTRACER_C_FUNCTION_START`` (C/C++) or
           ``@dft_fn`` (Python) in the annotated copy.
        4. Cross-reference ``annotation_logs/annotation_status.md`` for any
           recorded per-function status and reason.

        The response message also instructs the agent to call
        ``session_run_pipeline`` with ``annotation_confirmed=True`` and the
        same *run_id* once the coverage is satisfactory.

        Side effects:
            None — this is a read-only inspection tool.

        Args:
            run_id: Session identifier returned by ``session_create`` or
                ``pipeline_create_run``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — summary of annotated/total functions and
                  a prompt to confirm and continue the pipeline.
                * ``summary`` — dict with aggregate metrics:
                    * ``total_files`` — number of source files examined.
                    * ``relevant_files`` — files with at least one annotation
                      change.
                    * ``total_functions`` — total function definitions found.
                    * ``annotated`` — functions with dftracer markers present.
                    * ``skipped`` — functions explicitly recorded as skipped.
                    * ``failed`` — functions where annotation was attempted but
                      produced errors.
                    * ``coverage_pct`` — ``annotated / (total - skipped) * 100``.
                * ``files`` — list of per-file dicts, each containing:
                    * ``file`` — path relative to ``annotated/``.
                    * ``total_functions``, ``annotated``, ``skipped``, ``failed``,
                      ``pending``, ``not_annotated`` — per-file counts.
                    * ``functions`` — list of per-function status dicts with
                      ``name``, ``status`` (``annotated`` | ``skipped`` |
                      ``failed`` | ``pending`` | ``not_annotated``), and
                      ``reason`` where applicable.

        Raises:
            Returns ``{"status": "error"}`` when the workspace does not exist,
            or when ``_generate_annotation_report`` encounters an internal error
            (e.g. neither ``source/`` nor ``annotated/`` is present).

        Note:
            Call after manual annotation is complete.  To continue the pipeline
            after reviewing this report, call ``session_run_pipeline`` with
            ``annotation_confirmed=True`` and ``run_id=<run_id>``.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Workspace not found for run_id: {run_id}")

        rpt = _generate_annotation_report(ws, run_id)

        if "error" in rpt:
            return _err(rpt["error"], run_id=run_id)

        s = rpt["summary"]
        return _ok(
            f"Annotation report: {s['annotated']}/{s['total_functions'] - s['skipped']} "
            f"functions annotated ({s['coverage_pct']}% coverage). "
            f"Review the report, then call session_run_pipeline with "
            f"annotation_confirmed=True and run_id='{run_id}' to continue.",
            **rpt,
        )

    @mcp.tool()
    def session_install_dftracer(
        run_id: str,
        dftracer_ref: str = "develop",
        jobs: int = 4,
    ) -> str:
        """Install dftracer via pip for all project types, then locate dirs in site-packages.

        Always uses ``pip install git+https://github.com/llnl/dftracer.git@<ref>``
        regardless of whether the project is C/C++ or Python.  Feature flags
        detected from the application source are forwarded as environment
        variables so the dftracer wheel's C extension is built with the correct
        support compiled in:

        * ``DFTRACER_ENABLE_MPI=ON``   — when MPI is detected in the project
        * ``DFTRACER_ENABLE_HDF5=ON``  — when HDF5 is detected in the project
        * ``HDF5_ROOT=<prefix>``       — when the system HDF5 prefix is known
        * ``HDF5_DIR=<prefix>``        — same as ``HDF5_ROOT`` for cmake backends

        After a successful install, include and lib directories are discovered
        by probing the dftracer package inside site-packages (no cmake prefix
        assumed).  The resolved paths are stored in session state so that
        ``session_build_annotated`` can pass them as ``CMAKE_PREFIX_PATH`` /
        ``pkg-config`` search paths.

        Feature detection reads ``session_detect`` results from session state,
        or re-runs detection inline if not yet stored.

        Side effects:
            * Installs dftracer into the current Python environment's site-packages.
            * Persists ``dftracer_install_prefix``, ``dftracer_pip_include_dir``,
              and ``dftracer_pip_lib_dir`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            dftracer_ref: Git tag or branch of dftracer to install.
                Defaults to ``"develop"``.
            jobs: Unused (kept for API compatibility).  Defaults to ``4``.

        Returns:
            JSON string with keys:
                * ``status`` — ``"ok"`` or ``"error"``.
                * ``message`` — outcome description.
                * ``features_enabled`` — list of detected features that were
                  activated (e.g. ``["mpi", "hdf5=1.14.3"]``).
                * ``ref`` — the dftracer ref that was installed.
                * ``include_dir`` — resolved dftracer include path in site-packages.
                * ``lib_dir`` — resolved dftracer lib path in site-packages.
                * ``lib_name`` — shared library filename found.
                * ``steps`` — ``{"pip_install": <run result>}``.
        """
        ws = _ws(run_id)
        state = _load_state(run_id)
        info = state.get("detection") or _detect_info(ws / "source")
        features = info.get("features", {})
        bt = info.get("build_tool", state.get("build_tool", "unknown"))

        features_enabled = []
        compat_warnings: list = []

        # --- MPI version check ---
        if features.get("mpi"):
            features_enabled.append("mpi")
            mpi_info = _detect_system_mpi()
            if mpi_info["found"]:
                impl = mpi_info.get("impl", "unknown")
                ver  = mpi_info.get("version", "unknown")
                compat = mpi_info.get("compatible", False)
                if not compat:
                    compat_range = _MPI_COMPATIBLE_DISPLAY.get(impl, "see dftracer docs")
                    compat_warnings.append(
                        f"MPI-IO tracing DISABLED: detected {mpi_info.get('impl_display', impl)} "
                        f"{ver} is not in a dftracer-compatible range "
                        f"({compat_range}). "
                        f"Upgrade or downgrade your MPI installation to enable MPI-IO event capture. "
                        f"Report unsupported versions at https://github.com/llnl/dftracer/issues"
                    )
            else:
                compat_warnings.append(
                    "MPI detected in source but MPI runtime could not be probed — "
                    "MPI-IO tracing may not work."
                )

        # --- HDF5 version check ---
        if features.get("hdf5"):
            hdf5_sys = features.get("hdf5_system") or _detect_system_hdf5()
            hdf5_ver = hdf5_sys.get("version", "")
            features_enabled.append(f"hdf5{('=' + hdf5_ver) if hdf5_ver else ''}")
            if hdf5_ver and not _hdf5_version_compatible(hdf5_ver):
                compat_series = ", ".join(
                    f"1.{m}" for (_, m) in sorted(_HDF5_COMPATIBLE_SERIES)
                )
                compat_warnings.append(
                    f"HDF5 tracing may be DEGRADED: detected HDF5 {hdf5_ver} is not in a "
                    f"dftracer-compatible series (1.{{{compat_series}}}). "
                    f"Compatible series: {compat_series}."
                )

        if features.get("hip"):
            features_enabled.append("hip")
        if features.get("hwloc"):
            features_enabled.append("hwloc")

        # For Python (ML/AI) projects, dftracer and the app MUST share the same
        # venv (ws/install/) so `import dftracer` resolves at runtime without
        # any path surgery.  Never create a parallel ws/venv/ for Python apps —
        # it causes import errors when the app venv is active but dftracer lives
        # in a different site-packages.
        #
        # For C/C++ projects, create an isolated session venv (ws/venv/) that is
        # separate from the MCP server's own Python environment.
        app_venv_python = ws / "install" / "bin" / "python"
        if bt == "python":
            if not app_venv_python.exists():
                # App venv not yet created — create it now so dftracer and the
                # app land in the same environment from the start.
                try:
                    import subprocess as _sp
                    _sp.run(
                        [sys.executable, "-m", "venv", str(ws / "install")],
                        check=True, timeout=60,
                    )
                except Exception as exc:
                    return _err(f"Failed to create app venv at ws/install/: {exc}")
            venv_python_str = str(app_venv_python)
        else:
            try:
                venv_python = _ensure_session_venv(ws)
            except RuntimeError as exc:
                return _err(f"session venv creation failed: {exc}")
            venv_python_str = str(venv_python)
        _save_state(run_id, {"session_venv_python": venv_python_str})

        result = _install_dftracer_pip_direct(
            dftracer_ref=dftracer_ref,
            features={**features, "dftracer_pip_env": info.get("dftracer_pip_env", {})},
            python_exe=venv_python_str,
            jobs=jobs,
            ws=ws,
            run_id=run_id,
        )
        if not result["success"]:
            return _err(
                "dftracer pip install failed",
                features_enabled=features_enabled,
                ref=dftracer_ref,
                venv=str(ws / "venv"),
                steps=result["steps"],
            )

        # Locate include/lib dirs inside the venv's site-packages
        dirs = _find_dftracer_dirs(python_exe=venv_python_str) or {}

        install_dir = ws / "install_ann"
        install_dir.mkdir(exist_ok=True)
        _save_state(run_id, {
            "dftracer_install_prefix":  dirs.get("lib_dir", str(install_dir)),
            "dftracer_pip_include_dir": dirs.get("include_dir", ""),
            "dftracer_pip_lib_dir":     dirs.get("lib_dir", ""),
        })
        msg = f"dftracer installed via pip into session venv (features={features_enabled})"
        if compat_warnings:
            msg += "\n\nCOMPATIBILITY WARNINGS:\n" + "\n".join(
                f"  • {w}" for w in compat_warnings
            )
        return _ok(
            msg,
            features_enabled=features_enabled,
            compat_warnings=compat_warnings,
            ref=dftracer_ref,
            venv=str(ws / "venv"),
            include_dir=dirs.get("include_dir", "(not found)"),
            lib_dir=dirs.get("lib_dir", "(not found)"),
            lib_name=dirs.get("lib_name", "libdftracer_core.so"),
            steps=result["steps"],
        )

    @mcp.tool()
    def session_install_dftracer_utils(
        run_id: str,
    ) -> str:
        """Install ``dftracer-utils`` from the ``develop`` branch into the session environment.

        ``dftracer-utils`` provides the ``dftracer_split`` binary consumed by
        ``session_split_traces`` to compact raw ``.pfw`` trace files, as well as
        ``dftracer_info``, ``dftracer_merge``, and other trace analysis tools.

        The package is installed using ``pip install --upgrade`` so that the
        latest snapshot from the ``develop`` branch is always fetched, regardless
        of what PyPI currently has.

        Pip resolution order:

        1. ``<workspace>/install/bin/pip`` — the session virtualenv's pip.
        2. ``<server_python_dir>/pip`` — the pip adjacent to the MCP server's
           own Python interpreter.
        3. ``pip3`` — system fallback.

        Side effects:
            * Installs ``dftracer-utils`` (develop snapshot) into the resolved
              pip environment.
            * Persists ``{"dftracer_utils_installed": bool}`` to
              ``session.json``.

        Args:
            run_id: Session identifier (used only for workspace path resolution
                and state tracking; does not affect the install target).

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"dftracer-utils installed from develop"`` or
                  ``"dftracer-utils install failed"``.
                * Additional keys from the pip subprocess result
                  (``stdout``, ``stderr``, ``returncode``).

        Raises:
            Returns ``{"status": "error"}`` when pip exits non-zero.

        Note:
            Call once per session before ``session_split_traces`` if you want to
            guarantee the ``develop``-branch version of ``dftracer-utils`` is
            active.  Call this after ``session_install_dftracer`` to ensure the
            latest ``develop``-branch version of ``dftracer-utils`` is installed.
        """
        ws = _ws(run_id)
        state = _load_state(run_id)

        # Use the session venv created by session_install_dftracer; create it
        # now if this tool is called standalone before session_install_dftracer.
        venv_python_str = state.get("session_venv_python", "")
        if venv_python_str and Path(venv_python_str).exists():
            pip = Path(venv_python_str).parent / "pip"
        else:
            try:
                venv_python = _ensure_session_venv(ws)
                _save_state(run_id, {"session_venv_python": str(venv_python)})
                pip = venv_python.parent / "pip"
            except RuntimeError:
                pip = Path(sys.executable).parent / "pip"
        if not pip.exists():
            pip = Path(sys.executable).parent / "pip3"

        r = _install_dftracer_utils(pip, ws=_ws(run_id), run_id=run_id)
        _save_state(run_id, {"dftracer_utils_installed": r["success"]})
        if r["success"]:
            return _ok("dftracer-utils installed from develop", **r)
        return _err("dftracer-utils install failed", **r)

    @mcp.tool()
    def session_build_annotated(
        run_id: str,
        jobs: int = 4,
        extra_cmake_flags: str = "",
    ) -> str:
        """Configure and build the annotated source with dftracer linked.

        Mirrors the configure → build → install sequence of
        ``session_configure`` + ``session_build_install`` but targets the
        ``annotated/`` source tree and uses separate output directories
        (``build_ann/`` and ``install_ann/``) to preserve the original build
        for comparison.

        If ``session_install_dftracer`` was called first, the dftracer install
        prefix recorded in session state is automatically injected:

        * **cmake** — ``-DCMAKE_PREFIX_PATH=<prefix>`` appended to cmake flags.
        * **autotools** — ``PKG_CONFIG_PATH``, ``CPPFLAGS``, and ``LDFLAGS``
          environment variables set to point at ``<prefix>/lib/pkgconfig``,
          ``<prefix>/include``, and ``<prefix>/lib`` respectively.
        * **python** — dftracer is already in the venv; no extra flags needed.

        Side effects:
            * Creates ``<workspace>/build_ann/`` and ``<workspace>/install_ann/``.
            * For cmake/autotools: runs configure, ``make -j<jobs>``, and
              ``make install`` inside ``build_ann/``.
            * For Python: runs ``pip install -e annotated/``.
            * Persists ``{"step": "annotated_built"}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            jobs: Parallel make jobs.  Defaults to ``4``.
            extra_cmake_flags: Space-separated additional ``-D`` flags appended
                to the cmake invocation.  Defaults to ``""``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"Annotated build succeeded"`` or error
                  description.
                * ``build_tool`` — the detected build system string.
                * ``steps`` — dict of step results keyed by stage name
                  (``"configure"``, ``"build"``, ``"install"``, or
                  ``"pip_install"``), each a subprocess result dict.

        Raises:
            Returns ``{"status": "error"}`` when any build stage exits
            non-zero, or when ``annotated/`` does not exist.

        Note:
            Must be called after ``session_install_dftracer``.
        """
        return _session_build_annotated_impl(
            run_id=run_id,
            jobs=jobs,
            extra_cmake_flags=extra_cmake_flags,
        )

    @mcp.tool()
    @mcp.tool()
    def session_init_run(
        run_id: str,
        run_name: str,
    ) -> str:
        """Create the directory structure for a named profiling/optimization run.

        Every profiling iteration (baseline, opt1, opt2, …) lives under its own
        sub-directory inside the session workspace:

        .. code-block:: text

            <workspace>/
              <run_name>/
                traces/raw/      ← DFTRACER_LOG_FILE prefix goes here
                traces/compact/  ← session_split_traces output
                scripts/         ← launch scripts for this run
                source/          ← optional source snapshot

        Call this before ``session_run_with_dftracer`` to obtain the canonical
        paths for the run.  Calling it on an already-existing run is safe
        (directories are created only if absent).

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Short name for this run iteration, e.g. ``"baseline"``,
                ``"opt1"``, ``"opt2"``.  Used as the directory prefix.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — confirmation.
                * ``run_name`` — the *run_name* argument.
                * ``run_dir`` — ``<workspace>/<run_name>/``.
                * ``traces_raw`` — ``<workspace>/<run_name>/traces/raw/``.
                * ``traces_compact`` — ``<workspace>/<run_name>/traces/compact/``.
                * ``scripts_dir`` — ``<workspace>/<run_name>/scripts/``.
                * ``dftracer_log_prefix`` — value to set for ``DFTRACER_LOG_FILE``.
        """
        ws = _ws(run_id)
        run_d = _run_dir(ws, run_name)
        raw = _run_traces_raw(ws, run_name)
        compact = _run_traces_compact(ws, run_name)
        source = _run_source_dir(ws, run_name)
        patches = _run_patches_dir(ws, run_name)
        scripts = run_d / "scripts"
        scripts.mkdir(parents=True, exist_ok=True)
        log_prefix = str(raw / run_name)
        return _ok(
            f"Run directory initialised: {run_d}",
            run_name=run_name,
            run_dir=str(run_d),
            source_dir=str(source),
            patches_dir=str(patches),
            traces_raw=str(raw),
            traces_compact=str(compact),
            scripts_dir=str(scripts),
            dftracer_log_prefix=log_prefix,
        )

    @mcp.tool()
    def session_get_run_paths(
        run_id: str,
        run_name: str,
    ) -> str:
        """Return the canonical paths for a named run without creating anything.

        Useful for querying where a run's traces/scripts live without
        side-effecting the workspace.  Directories are NOT created.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run name (e.g. ``"baseline"``, ``"opt1"``).

        Returns:
            JSON with ``run_dir``, ``traces_raw``, ``traces_compact``,
            ``scripts_dir``, ``dftracer_log_prefix``, and ``exists``
            (``true`` if ``<workspace>/<run_name>/`` exists on disk).
        """
        ws = _ws(run_id)
        run_d = ws / run_name
        raw = run_d / "traces" / "raw"
        compact = run_d / "traces" / "compact"
        source = run_d / "source"
        patches = run_d / "patches"
        scripts = run_d / "scripts"
        return _ok(
            f"Paths for run {run_name!r}",
            run_name=run_name,
            run_dir=str(run_d),
            source_dir=str(source),
            patches_dir=str(patches),
            traces_raw=str(raw),
            traces_compact=str(compact),
            scripts_dir=str(scripts),
            dftracer_log_prefix=str(raw / run_name),
            exists=run_d.exists(),
        )

    @mcp.tool()
    def session_list_runs(run_id: str) -> str:
        """List all named runs (baseline, opt1, opt2, …) in the session workspace.

        A directory is counted as a *run* when it contains a ``traces/``
        sub-folder.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON with ``runs`` (list of run names) and per-run summary
            (``has_raw``, ``has_compact``, ``raw_count``, ``compact_count``).
        """
        ws = _ws(run_id)
        runs = []
        for child in sorted(ws.iterdir()):
            if not child.is_dir():
                continue
            has_traces = (child / "traces").exists()
            has_source = (child / "source").exists()
            has_patches = (child / "patches").exists()
            if not (has_traces or has_source):
                continue
            raw = child / "traces" / "raw"
            compact = child / "traces" / "compact"
            patches = child / "patches"
            source = child / "source"
            raw_count = len(list(raw.rglob("*.pfw*"))) if raw.exists() else 0
            compact_count = len(list(compact.rglob("*.pfw*"))) if compact.exists() else 0
            patch_files = sorted(p.name for p in patches.glob("*.patch")) if has_patches else []
            source_file_count = sum(1 for _ in source.rglob("*") if _.is_file()) if has_source else 0
            runs.append({
                "name": child.name,
                "has_source": has_source,
                "source_file_count": source_file_count,
                "patches": patch_files,
                "has_raw": raw.exists(),
                "has_compact": compact.exists(),
                "raw_count": raw_count,
                "compact_count": compact_count,
            })
        return _ok(f"Found {len(runs)} run(s)", runs=runs)

    @mcp.tool()
    def session_validate_structure(run_id: str) -> str:
        """Validate that the session workspace matches the canonical directory structure.

        **Every stage of the pipeline (annotation, build, optimization, trace
        collection) must write into the paths this tool reports — never a
        path a step invents on its own.** Call this before annotation and
        before/after every optimization iteration to catch drift early; call
        ``session_reorganize_structure`` if it reports anything not clean.

        Checks, read-only (nothing is created or moved):
          - ``baseline/``, ``annotated/``, and every existing ``opt<n>/`` each
            have ``source/``, ``scripts/compile.sh``, ``scripts/run.sh``
          - ``annotated/`` additionally has ``traces/raw/``, ``traces/compact/``,
            ``analysis-diagnostics/`` (pre-created by ``session_init_structure``;
            ``baseline/`` and ``opt<n>/`` traces are lazy — created on first
            ``session_run_with_dftracer`` for that run, so absence there is not
            flagged as drift)
          - session-level singletons: ``artifacts/``, ``tmp/``, ``dataset/``,
            ``session_report.md``
          - no legacy flat-layout paths (``source/``, ``build/``, ``install/``,
            ``build_ann/``, ``venv/``, ``traces/`` at workspace root, etc.) or
            other unrecognised top-level entries are present

        Note: ``patches/`` is created lazily by ``session_snapshot_run_source``
        for whichever run it's called on, so it is intentionally not checked.

        As a side effect, writes the freshly computed canonical path map into
        ``session.json["paths"]`` so any tool can look up exact paths without
        re-deriving the layout itself.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON with ``clean`` (bool), ``missing`` (canonical paths absent),
            ``legacy_drift`` (pre-refactor flat-layout paths found — pass these
            to ``session_reorganize_structure``), ``unexpected_paths`` (anything
            else unrecognised at workspace root), and ``paths`` (the canonical
            map now persisted to ``session.json``).
        """
        ws = _ws(run_id)
        if not ws.is_dir():
            return _err(f"Workspace not found for {run_id}", run_id=run_id)

        expected = _expected_paths(ws)
        missing: List[str] = []

        def _check(label: str, path_str: str) -> None:
            if not Path(path_str).exists():
                missing.append(label)

        # `patches/` is created lazily on first snapshot (session_snapshot_run_source)
        # for every run, and `traces/` is only pre-created by _init_structure for
        # `annotated/` — baseline never runs with dftracer so it has no traces.
        # Neither is a hard requirement here; opt<n> runs likewise only get
        # traces/patches once their optimization iteration actually executes.
        for run_name, run_paths in expected["runs"].items():
            _check(f"{run_name}/source", run_paths["source_dir"])
            _check(f"{run_name}/scripts", run_paths["scripts_dir"])
            if (Path(run_paths["scripts_dir"]).is_dir()):
                _check(f"{run_name}/scripts/compile.sh", str(Path(run_paths["scripts_dir"]) / "compile.sh"))
                _check(f"{run_name}/scripts/run.sh", str(Path(run_paths["scripts_dir"]) / "run.sh"))
            if run_name == "annotated":
                _check(f"{run_name}/traces/raw", run_paths["traces_raw"])
                _check(f"{run_name}/traces/compact", run_paths["traces_compact"])
        _check("annotated/analysis-diagnostics", str(ws / "annotated" / "analysis-diagnostics"))
        _check("artifacts", expected["artifacts"])
        _check("tmp", expected["tmp"])
        _check("dataset", expected["dataset"])
        _check("session_report.md", expected["session_report"])

        legacy_drift = sorted(
            name for name in _LEGACY_TOP_LEVEL_NAMES if (ws / name).exists()
        )
        known_top_level = set(_CANONICAL_TOP_LEVEL_NAMES) | set(expected["runs"].keys())
        unexpected_paths = sorted(
            p.name for p in ws.iterdir()
            if p.name not in known_top_level and p.name not in _LEGACY_TOP_LEVEL_NAMES
        )

        _save_state(run_id, {
            "paths": expected,
            "structure_validated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        })

        clean = not missing and not legacy_drift and not unexpected_paths
        msg = (
            f"Session structure is clean for {run_id}"
            if clean else
            f"Session structure has drift for {run_id}: "
            f"{len(missing)} missing, {len(legacy_drift)} legacy, {len(unexpected_paths)} unexpected"
        )
        return _ok(
            msg,
            clean=clean,
            missing=missing,
            legacy_drift=legacy_drift,
            unexpected_paths=unexpected_paths,
            paths=expected,
        )

    @mcp.tool()
    def session_reorganize_structure(run_id: str, dry_run: bool = True) -> str:
        """Repair the session workspace to match the canonical directory structure.

        Run this whenever ``session_validate_structure`` reports drift — e.g.
        after an older tool wrote to a legacy flat path (``ws/source``,
        ``ws/build_ann``, ``ws/traces``, ``ws/venv``, ...) instead of the
        canonical ``baseline/annotated/opt<n>`` run-scoped tree.

        Two actions are taken:
          1. ``session_init_structure``'s skeleton builder is invoked
             (idempotent — never overwrites existing files).
          2. Every legacy top-level path found (see
             ``session_validate_structure``'s ``legacy_drift`` list) is moved
             into ``artifacts/legacy/<name>/`` — never deleted, never merged
             into a run directory by guessing which run it belongs to, since
             that mapping is ambiguous for pre-refactor layouts. Move the
             content into the correct run manually if you can confirm which
             run it came from, then re-run this tool.

        Args:
            run_id: Session identifier returned by ``session_create``.
            dry_run: If True (default), report the actions that *would* be
                taken without moving anything. Set False to actually move
                legacy paths into quarantine.

        Returns:
            JSON with ``dry_run`` (bool), ``actions`` (list of
            ``{path, action}`` where action is one of ``"planned_move"``,
            ``"moved"``, ``"skipped_exists"``), and the refreshed ``paths``
            map (also persisted to ``session.json["paths"]``).
        """
        ws = _ws(run_id)
        if not ws.is_dir():
            return _err(f"Workspace not found for {run_id}", run_id=run_id)

        if not dry_run:
            _init_structure(ws, None)

        legacy_root = ws / "artifacts" / "legacy"
        actions: List[Dict[str, str]] = []
        for name in _LEGACY_TOP_LEVEL_NAMES:
            src = ws / name
            if not src.exists():
                continue
            dest = legacy_root / name
            if dest.exists():
                actions.append({"path": name, "action": "skipped_exists", "dest": str(dest)})
                continue
            if dry_run:
                actions.append({"path": name, "action": "planned_move", "dest": str(dest)})
            else:
                legacy_root.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dest))
                actions.append({"path": name, "action": "moved", "dest": str(dest)})

        expected = _expected_paths(ws)
        if not dry_run:
            _save_state(run_id, {
                "paths": expected,
                "structure_reorganized_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            })

        msg = (
            f"Dry run: {len(actions)} action(s) planned for {run_id}"
            if dry_run else
            f"Reorganized {run_id}: {len(actions)} legacy path(s) handled"
        )
        return _ok(msg, dry_run=dry_run, actions=actions, paths=expected)

    @mcp.tool()
    def session_snapshot_run_source(
        run_id: str,
        run_name: str,
        source_path: Optional[str] = None,
        prev_run_name: Optional[str] = None,
    ) -> str:
        """Snapshot the current source tree into ``<run_name>/source/`` and generate a patch.

        Captures the state of the source code for a named run iteration so that
        every run has a reproducible record of exactly which code produced its
        traces.  Optionally generates a unified diff (patch) vs the previous run's
        snapshot.

        The canonical pipeline order is::

            baseline   → snapshot ws/source/   (no patch — this is the origin)
            annotated  → snapshot ws/annotated/ + patch vs baseline/source/
            opt1       → snapshot ws/annotated/ (after applying opt) + patch vs annotated/source/
            opt2       → snapshot ws/annotated/ (after applying opt2) + patch vs opt1/source/

        Side effects:
            * Copies *source_path* (or ``<workspace>/annotated/`` for annotated /
              opt runs, ``<workspace>/source/`` for baseline) into
              ``<workspace>/<run_name>/source/`` using rsync (shutil fallback).
            * If *prev_run_name* is given, writes a unified diff to
              ``<workspace>/<run_name>/patches/from_<prev_run_name>.patch``.
            * Persists ``{"snapshot_<run_name>": {...}}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label (e.g. ``"baseline"``, ``"annotated"``, ``"opt1"``).
            source_path: Absolute path of the source tree to snapshot.
                Defaults to ``<workspace>/annotated/`` for non-baseline runs,
                or ``<workspace>/source/`` for ``"baseline"``.
            prev_run_name: If given, generates a patch from
                ``<workspace>/<prev_run_name>/source/`` to the new snapshot.

        Returns:
            JSON with ``source_dir``, ``file_count``, and (if *prev_run_name*)
            ``patch_file``.
        """
        ws = _ws(run_id)

        # Resolve default source path
        if source_path:
            src = Path(source_path)
        elif run_name == "baseline":
            src = ws / "source"
        else:
            src = ws / "annotated"
            if not src.exists():
                src = ws / "source"

        if not src.exists():
            return _err(f"Source path not found: {src}")

        dest = _run_source_dir(ws, run_name)
        snap = _snapshot_source(src, dest)
        if not snap["success"]:
            return _err(f"Snapshot failed for {run_name}", source=str(src))

        result: Dict[str, Any] = {
            "source_dir": str(dest),
            "source_from": str(src),
            "file_count": snap["file_count"],
            "tool": snap.get("tool"),
        }

        # Generate patch vs previous run
        if prev_run_name:
            prev_source = ws / prev_run_name / "source"
            if prev_source.exists():
                patch_file = _run_patches_dir(ws, run_name) / f"from_{prev_run_name}.patch"
                patch_status = _generate_patch(prev_source, dest, patch_file)
                result["patch_file"] = str(patch_file)
                result["patch_status"] = patch_status
            else:
                result["patch_file"] = None
                result["patch_status"] = f"skipped: {prev_run_name}/source/ not found"

        _save_state(run_id, {f"snapshot_{run_name}": result})
        return _ok(f"Snapshotted {src.name} → {run_name}/source/ ({snap['file_count']} files)", **result)

    @mcp.tool()
    def session_run_with_dftracer(
        run_id: str,
        command: str,
        subfolder: str = "build_ann",
        data_dir: str = "all",
        timeout: int = 600,
        env_extra: Optional[str] = None,
        run_name: str = "baseline",
        allocation_id: Optional[str] = None,
        nnodes: Optional[int] = None,
        ntasks: Optional[int] = None,
    ) -> str:
        """Run a command with dftracer environment variables set to capture traces.

        Executes *command* inside the workspace with all ``DFTRACER_*`` env
        vars configured.  Trace files are written to
        ``<workspace>/<run_name>/traces/raw/`` and consumed by
        ``session_split_traces``.

        The following variables are always set:

        * ``DFTRACER_ENABLE=1``         — activates tracing.
        * ``DFTRACER_INC_METADATA=1``   — records process/thread metadata.
        * ``DFTRACER_LOG_FILE=<workspace>/<run_name>/traces/raw/<run_name>``
          — trace file prefix; dftracer appends ``.<pid>.pfw``.
        * ``DFTRACER_DATA_DIR=all``     — captures I/O on *any* file path.
          Pass an explicit path via *data_dir* only to restrict monitoring
          to a subtree.

        ``DFTRACER_INIT`` is intentionally **not** set here.  Pass it via
        *env_extra* only when the annotated source has no explicit
        ``DFTRACER_C_INIT`` / ``DFTRACER_CPP_INIT`` calls.

        Additional variables can be merged/overridden via *env_extra*.

        **Allocation-aware runs:** For production-scale runs, provide *allocation_id*,
        *nnodes*, and *ntasks* to wrap the command with ``flux proxy <alloc> flux run``
        using all nodes in the allocation. The tracer agent must verify the allocation
        is active before running.

        Side effects:
            * Creates ``<workspace>/<run_name>/traces/raw/`` if absent.
            * Writes trace files inside that directory.
            * Persists ``{"step": "ran_with_dftracer", ...}`` to ``session.json``.
            * Writes an artifact log at step 11.

        Args:
            run_id: Session identifier returned by ``session_create``.
            command: Shell command to run (via ``/bin/sh -c``).
            subfolder: Working-directory sub-folder relative to the workspace
                root.  Defaults to ``"build_ann"``.  Falls back to ``"build"``
                then ``"source"`` if absent.
            data_dir: Value passed to ``DFTRACER_DATA_DIR``.  Defaults to
                ``"all"``.
            timeout: Seconds before the subprocess is killed (default: 600).
            env_extra: Optional JSON object string of additional env vars.
            run_name: Named run label — e.g. ``"baseline"``, ``"opt1"``.
                Traces are stored under ``<workspace>/<run_name>/traces/raw/``.
                Defaults to ``"baseline"``.
            allocation_id: Optional Flux allocation ID (e.g. ``"f3Junw1CTMif"``).
                When provided, the command is wrapped with
                ``flux proxy <alloc_id> flux run -N <nnodes> -n <ntasks> --exclusive``.
            nnodes: Number of nodes to use (required when allocation_id is set).
            ntasks: Number of MPI tasks (required when allocation_id is set).

        Returns:
            JSON string with keys ``status``, ``message``, ``run_name``,
            ``traces_dir``, ``stdout``, ``stderr``, ``returncode``.

        Note:
            Must be called after ``session_build_annotated``.  Follow with
            ``session_split_traces`` (same *run_name*) to compact the raw files.
        """
        return _session_run_with_dftracer_impl(
            run_id=run_id,
            command=command,
            subfolder=subfolder,
            data_dir=data_dir,
            timeout=timeout,
            env_extra=env_extra,
            run_name=run_name,
            allocation_id=allocation_id,
            nnodes=nnodes,
            ntasks=ntasks,
        )

    @mcp.tool()
    def session_analyze_traces(
        run_id: str,
        run_name: str = "baseline",
        query_type: str = "summary",
        index_dir: Optional[str] = None,
        extra_flags: str = "",
    ) -> str:
        """Summarise dftracer traces using ``dftracer_info`` (dfanalyzer).

        Invokes ``dftracer_info`` against the compacted trace directory
        (``<workspace>/<run_name>/traces/compact/``) to produce a summary of
        I/O behaviour — function call counts, time in I/O, per-file breakdowns,
        and process metadata.  The index directory caches parsed data for faster
        repeated queries.

        Side effects:
            * Creates *index_dir* (or ``<compact>/idx/``) if absent.
            * Persists ``{"step": "traces_analyzed_<run_name>", ...}`` to
              ``session.json``.
            * Writes an artifact log at step 13.

        Args:
            run_id: Session identifier returned by ``session_create``.
            run_name: Run label whose compact traces to analyse (default
                ``"baseline"``).  Maps to
                ``<workspace>/<run_name>/traces/compact/``.
            query_type: Value passed to ``dftracer_info --query``.  Common
                values: ``"summary"`` (default), ``"function"``, ``"file"``.
            index_dir: Absolute path to the index directory.  Defaults to
                ``<compact>/idx/``.
            extra_flags: Additional space-separated flags for ``dftracer_info``.

        Returns:
            JSON string with keys ``status``, ``message``, ``run_name``,
            ``stdout``, ``stderr``, ``returncode``.

        Raises:
            Returns ``{"status": "error"}`` when the compact directory does not
            exist or when ``dftracer_info`` exits non-zero.

        Note:
            Must be called after ``session_split_traces`` with the same
            *run_name*.
        """
        ws = _ws(run_id)
        traces = ws / run_name / "traces" / "compact"
        if not traces.exists():
            # Legacy fallback for sessions predating the structured layout
            legacy = ws / "traces_split"
            if legacy.exists():
                traces = legacy
            else:
                return _err(f"{run_name}/traces/compact/ not found — run session_split_traces first")

        idx = Path(index_dir) if index_dir else traces / "idx"
        idx.mkdir(parents=True, exist_ok=True)

        flags = extra_flags.split() if extra_flags else []
        r = _run(
            [
                "dftracer_info",
                "-d", str(traces),
                "--query", query_type,
                "--index-dir", str(idx),
            ] + flags,
            timeout=600,
        )
        _save_state(run_id, {f"step_analyzed_{run_name}": True, "analysis_result": r})
        _write_artifact_log(_ws(run_id), 13, "session_analyze_traces", {"run_name": run_name, **r}, run_id)
        if r["success"]:
            return _ok("Analysis complete", run_name=run_name, **r)
        return _err("dftracer_info failed", run_name=run_name, **r)

    @mcp.tool()
    def session_search_papers_for_config(
        run_id: str,
        app_name: str,
        problem_name: Optional[str] = None,
        max_results_each: int = 3,
    ) -> str:
        """Search academic literature AND GitHub repos for application-specific run configuration.

        Before executing a production-scale run, agents MUST call this tool to
        discover benchmark-proven parameter values (grid size, checkpoint
        intervals, refinement levels, etc.) from published papers AND the app's
        official GitHub repository.  The results are persisted to ``session.json``
        so downstream steps (planner, tracer, optimizer) can read them without
        re-searching.

        Workflow:
          1. Build targeted queries from *app_name* + *problem_name*.
          2. Search arXiv + Semantic Scholar in parallel.
          3. Search the app's official GitHub repository for benchmark parameter files.
          4. Extract known parameter patterns from paper titles/abstracts AND repo files.
          5. Persist findings and return structured recommendations.

        Side effects:
            * Writes ``<workspace>/artifacts/paper_config_search.json``.
            * Persists ``{"paper_config_search": {...}}`` to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            app_name: Application name (e.g. ``"Flash-X"``, ``"IOR"``, ``"h5bench"``).
            problem_name: Optional problem / benchmark name (e.g. ``"Sedov"``,
                ``"weak"``, ``"strong"``).
            max_results_each: Papers to fetch per source (default 3).

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — human-readable summary.
                * ``queries`` — list of search strings used.
                * ``paper_count`` — total unique papers found.
                * ``papers`` — list of paper dicts (title, authors, year, url, pdf_url).
                * ``github_files`` — list of benchmark parameter files found in the app's repo.
                * ``github_params`` — dict of parameters extracted from repo files.
                * ``extracted_params`` — combined dict of all parameters found (papers + GitHub).
                * ``recommendations`` — human-readable bullet list.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Session {run_id} not found")

        result = search_papers_for_config(
            app_name=app_name,
            problem_name=problem_name,
            max_results_each=max_results_each,
        )

        # Persist to artifact file
        artifact = ws / "artifacts" / "paper_config_search.json"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text(json.dumps(result, indent=2, default=str))

        _save_state(run_id, {"paper_config_search": result})
        _write_artifact_log(
            ws, 15, "session_search_papers_for_config",
            {"app_name": app_name, "problem_name": problem_name, **result},
            run_id,
        )
        total_sources = result.get("paper_count", 0) + result.get("github_file_count", 0)
        return _ok(
            f"Found {result.get('paper_count', 0)} papers and {result.get('github_file_count', 0)} repo files for {app_name} (total sources: {total_sources})",
            **result,
        )

    @mcp.tool()
    def session_status(run_id: str) -> str:
        """Return the current persisted state of a session.

        Reads ``<workspace>/session.json`` and lists all sub-directories that
        exist in the workspace root.  Useful for understanding how far a
        session has progressed, what build tool was detected, and which
        workspace sub-folders are present (``source``, ``annotated``,
        ``build``, ``install``, ``traces``, etc.).

        Side effects:
            None — this is a read-only inspection tool.

        Args:
            run_id: Session identifier returned by ``session_create`` or
                ``pipeline_create_run``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"``).
                * ``message`` — ``"Session status"``.
                * ``workspace`` — absolute path to the session workspace.
                * ``subdirs`` — list of sub-directory names present in the
                  workspace root.
                * All keys stored in ``session.json`` (e.g. ``step``,
                  ``url``, ``ref``, ``build_tool``, ``detection``,
                  ``dftracer_install_prefix``, etc.).

        Raises:
            Returns ``{"status": "error"}`` when no workspace directory exists
            for *run_id*.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Session {run_id} not found")
        state = _load_state(run_id)
        subdirs = [d.name for d in ws.iterdir() if d.is_dir()]
        # Drop keys that we pass explicitly to avoid duplicate-keyword errors
        extra = {k: v for k, v in state.items() if k not in {"workspace"}}
        return _ok("Session status", workspace=str(ws), subdirs=subdirs, **extra)

    @mcp.tool()
    def session_collect_system_info(run_id: str) -> str:
        """Collect a system configuration snapshot for the current node.

        Gathers CPU, memory, network, and filesystem information from the
        running host and saves the result to
        ``<workspace>/system_config.json``.  This snapshot is typically
        captured immediately after the dftracer trace run so that analysis
        tools can correlate I/O behaviour with the hardware environment in
        which the benchmark was executed.

        Information collected:

        **CPU**
            Architecture, model name, socket/core/thread counts, min/max
            frequency (MHz), NUMA topology, L1/L2/L3 cache sizes.
            Source: ``lscpu --json`` with ``/proc/cpuinfo`` as fallback.

        **Memory**
            Total, available, and swap capacity; buffer and cache sizes.
            Source: ``/proc/meminfo``.

        **Network**
            Per-interface name, link type, MTU, operational state, MAC
            address, and (where ``ethtool`` is available) negotiated speed
            and duplex.
            Source: ``ip -j link show`` with ``/proc/net/dev`` as fallback.

        **Filesystems**
            All mounted filesystems: device, type, total/used/available
            capacity, use percentage, and mount point.
            Source: ``df -Th``.

        **Host / OS**
            Fully-qualified hostname, kernel release, and ``/etc/os-release``
            fields (``NAME``, ``VERSION``, ``ID``, etc.).

        Missing commands (e.g. ``ethtool``, ``lscpu``) are silently skipped
        rather than failing the tool — every section degrades independently.

        Side effects:
            * Writes ``<workspace>/system_config.json``.
            * Persists ``{"step": "system_info_collected", "system_config":
              "<path>"}`` to ``session.json``.
            * Writes an artifact log at step 14.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — ``"System configuration collected"``.
                * ``config_file`` — absolute path to ``system_config.json``.
                * ``hostname`` — fully-qualified hostname.
                * ``cpu`` — CPU info dict.
                * ``memory_total`` — human-readable total RAM string.
                * ``network_interfaces`` — count of network interfaces found.
                * ``filesystem_mounts`` — count of mounted filesystems found.

        Raises:
            Returns ``{"status": "error"}`` when the session workspace does
            not exist.
        """
        return _session_collect_system_info_impl(run_id=run_id)


