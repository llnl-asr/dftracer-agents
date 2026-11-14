"""dftracer_build: detect an application's build system and features, configure and build the ORIGINAL source
(cmake, autotools, meson, pip), copy and patch the annotated tree so it links dftracer, build the annotated tree, report
annotation coverage, and rule deterministically on either build (dftracer_build_check).

Tools moved unchanged from dftracer-agents (mcp_tools/tools/session/session_tools.py, register_session_tools).
"""
from __future__ import annotations

import shutil
import sys
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.annotation import (  # moved tools use these helpers unchanged
    _generate_annotation_report,
)
from dftracer_agents.mcp_tools.tools.session.build import (  # moved tools use these helpers unchanged
    _patch_autotools_makefile,
    _patch_cmake,
    _patch_pyproject,
    _patch_setup_py,
)
from dftracer_agents.mcp_tools.tools.session.detection import (  # moved tools use these helpers unchanged
    _detect_info,
)
from dftracer_agents.mcp_tools.tools.session.install import (  # moved tools use these helpers unchanged
    _ensure_session_env_script,
    _find_dftracer_dirs,
)
from dftracer_agents.mcp_tools.tools.session.session_tools import (  # moved tools use these helpers unchanged
    _has_mpi4py_dependency,
    _session_build_annotated_impl,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _autoreconf_bootstrap,
    _err,
    _load_state,
    _ok,
    _run,
    _save_state,
    _write_artifact_log,
    _ws,
)
import json
import os
import tempfile
from pathlib import Path

from dftracer_agents.mcp_tools.tools.session.workspace import _ws as _cap_ws

_C_EXT = {".c", ".cc", ".cpp", ".cxx", ".C"}


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _nonempty_dir(p: Path) -> bool:
    return p.is_dir() and any(p.iterdir())


def _links_dftracer(roots, limit: int = 600) -> list:
    """Executables / shared objects under *roots* whose bytes name libdftracer_core (DT_NEEDED or rpath)."""
    hits, seen = [], 0
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*"):
            if seen >= limit or len(hits) >= 5:
                return hits
            if not p.is_file() or p.is_symlink() or p.stat().st_size > (256 << 20):
                continue
            if not (os.access(p, os.X_OK) or ".so" in p.name):
                continue
            seen += 1
            with open(p, "rb") as fh:
                head = fh.read(4)
                if head != b"\x7fELF":
                    continue
                if b"libdftracer_core" in fh.read():
                    hits.append(str(p))
    return hits


def _build_check(ws: Path, stage: str) -> dict:
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}", "pass the run_id session_create returned")
        return _verdict(checks, workspace=str(ws))
    if stage not in ("original", "annotated"):
        _add(checks, "stage", False, f"unknown stage {stage!r}", "stage is 'original' or 'annotated'")
        return _verdict(checks, workspace=str(ws))
    try:
        state = json.loads((ws / "session.json").read_text())
    except (OSError, ValueError):
        state = {}
    bt = (state.get("detection") or {}).get("build_tool") or state.get("build_tool") or "unknown"
    _add(checks, "detected", bt != "unknown", f"build_tool={bt}",
         "run session_detect(run_id) (pin hdf5_prefix/mpicc/mpicxx when the app uses a session-built HDF5 or a specific MPI)")
    logs = sorted(p.name for p in (ws / "artifacts").glob("*")) if (ws / "artifacts").is_dir() else []
    if stage == "original":
        _add(checks, "app_built", state.get("app_built") is True, f"app_built={state.get('app_built')}",
             "run session_configure then session_build_install; read artifacts/*session_build_install* for the FIRST compiler error")
        _add(checks, "install_tree", _nonempty_dir(ws / "install"), str(ws / "install"),
             "session_build_install installs into <ws>/install (pip projects install there during session_configure)")
        _add(checks, "build_log", any("build_install" in n for n in logs), "artifacts build_install log",
             "logs go to artifacts/: re-run session_build_install so its log is written")
        return _verdict(checks, workspace=str(ws), build_tool=bt, stage=stage)
    _add(checks, "dftracer_installed", bool(state.get("dftracer_install_prefix")),
         f"dftracer_install_prefix={state.get('dftracer_install_prefix')}", "run session_install_dftracer before building the annotated tree")
    src = ws / "annotated" / "source" if (ws / "annotated" / "source").is_dir() else ws / "annotated"
    files = [p for p in src.rglob("*") if p.is_file() and p.suffix in (_C_EXT | {".py"})] if src.is_dir() else []
    marked = [p for p in files if "DFTRACER_C" in p.read_text(errors="replace") or "dftracer" in p.read_text(errors="replace")]
    _add(checks, "annotated_source", bool(marked), f"{len(marked)}/{len(files)} source files carry dftracer annotations",
         "annotate first (annotation stage) and validate_annotations; the build stage never annotates by hand")
    if bt == "python":
        return _verdict(checks, workspace=str(ws), build_tool=bt, stage=stage)
    _add(checks, "annotated_built", _nonempty_dir(ws / "install_ann") or _nonempty_dir(ws / "build_ann"),
         "install_ann/ or build_ann/ populated",
         "run session_build_annotated(run_id, extra_cmake_flags=<same as the original build>) (autotools: custom_build_cmd)")
    hits = _links_dftracer([ws / "install_ann" / "bin", ws / "install_ann" / "lib", ws / "install_ann" / "lib64", ws / "build_ann"])
    _add(checks, "links_dftracer", bool(hits), f"ELF objects naming libdftracer_core: {hits[:3]}",
         "the annotated binary does not link dftracer: session_patch_build (cmake/autotools/pyproject) or "
         "session_generate_dftracer_pc + PKG_CONFIG_PATH for autotools, then rebuild; add -Wl,-rpath to the dftracer lib dir")
    return _verdict(checks, workspace=str(ws), build_tool=bt, stage=stage)


def register(mcp):
    """Register the dftracer_build tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

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
                  ``"make"``, ``"python"``, ``"python-pip"``, ``"anaconda"``,
                  ``"meson"``, or ``"unknown"``.
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

        # Create the session's ONE canonical env.sh here -- the earliest
        # point source/ exists to scan for the app's own module-load
        # scripts. Every later build/install/run step (dftracer install,
        # app build, smoke test, trace runs) sources this SAME file instead
        # of each re-deriving its own module list, so they can never
        # disagree with each other.
        try:
            _ensure_session_env_script(_ws(run_id), src)
        except Exception:
            pass

        return _ok("Detection complete", **info)

    @mcp.tool()
    def session_configure(
        run_id: str,
        extra_cmake_flags: str = "",
        extra_configure_flags: str = "",
        extra_pip_flags: str = "",
        extra_meson_flags: str = "",
    ) -> str:
        """Configure the build system for the *original* cloned source.

        Runs the appropriate configuration command based on the build tool
        detected by ``session_detect`` (or re-detected inline if the detection
        step was skipped):

        * **cmake** — ``cmake -S source -B build -DCMAKE_INSTALL_PREFIX=install
          -DCMAKE_BUILD_TYPE=RelWithDebInfo [extra_cmake_flags]``
        * **autotools** — ``autoreconf -fi`` (if ``configure`` does not exist),
          then ``./configure --prefix=<install> [extra_configure_flags]``
        * **meson** — ``meson setup build source -Dprefix=<install>
          [extra_meson_flags]`` (project-specific ``option()``s from
          ``meson.options``/``meson_options.txt`` go in ``extra_meson_flags``,
          e.g. ``"-Duse_system_flux=true"`` — always read that file first, see
          [[software-meson]]).
        * **python** — ``python3 -m venv install/`` followed by
          ``pip install -e source/ [extra_pip_flags]``. If ``pyproject.toml``
          declares ``build-backend = "mesonpy"``, project-specific meson
          options must be forwarded via
          ``extra_pip_flags='--config-settings=setup-args="-Doption=value"'``
          (see [[software-pip]]) — this branch does not otherwise know about
          meson.options.
        * **python-pip** — ``python3 -m venv install/`` only. This covers pure
          Python application trees with no packaging metadata at the repo root;
          app-specific dependencies are installed later into the same venv.
        * **anaconda** — treated like ``python-pip`` at the session-tooling
          level: create the session venv and leave dependency installation to
          the workload-specific step that reads the environment files.

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
            extra_meson_flags: Space-separated additional ``-D`` flags appended
                to ``meson setup`` (e.g. ``"-Duse_system_flux=true"``).
                Ignored for non-meson projects.  Defaults to ``""``.

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
            # Bootstrap if needed. Some projects (e.g. IOR) keep their
            # custom m4 macros (X_AC_META, ...) in config/ rather than the
            # default aclocal search path, so autoreconf must be told about
            # it explicitly or it fails silently and configure is never
            # generated (session_configure then errors with a confusing
            # "No such file or directory: '.../configure'").
            if (src / "configure.ac").exists() and not (src / "configure").exists():
                r_boot = _autoreconf_bootstrap(src)
                if not r_boot["success"]:
                    return _err("autoreconf bootstrap failed", **r_boot)
            flags = [
                f"--prefix={install}",
                "--disable-dependency-tracking",  # avoids config.status .deps failures
            ] + (extra_configure_flags.split() if extra_configure_flags else [])
            r = _run([str(src / "configure")] + flags, cwd=build, timeout=300)
        elif bt == "meson":
            flags = [f"-Dprefix={install}"] + (
                extra_meson_flags.split() if extra_meson_flags else []
            )
            # Re-running `meson setup` on an existing builddir reconfigures in
            # place; --reconfigure makes that safe on a re-run of this tool.
            if (build / "meson-info").exists():
                flags = ["--reconfigure"] + flags
            r = _run(["meson", "setup", str(build), str(src)] + flags, timeout=300)
        elif bt in {"python", "python-pip", "anaconda"}:
            venv_r = _run([sys.executable, "-m", "venv", str(install)], timeout=60)
            if not venv_r["success"]:
                return _err("venv creation failed", **venv_r)
            pip = install / "bin" / "pip"
            pip_env: Dict[str, str] = {}
            # mpi4py must be compiled against the system MPI (not a wheel) so
            # that it uses the same ABI as the rest of the MPI stack.
            if bt == "python":
                flags = ["install", "-e", str(src)] + (
                    extra_pip_flags.split() if extra_pip_flags else []
                )
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
                # Pure Python application trees and conda-described repos may
                # have no installable package metadata at the repo root. Create
                # the shared session venv here and let the workload-specific step
                # install explicit dependencies into it.
                r = {
                    "success": True,
                    "returncode": 0,
                    "stdout": f"Created session venv for build tool {bt}; no package install at configure stage.",
                    "stderr": "",
                }
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
        * **meson** — ``meson compile -C build -j<jobs>`` followed by
          ``meson install -C build``.
        * **python** — no-op; installation was already performed by
          ``session_configure`` (``pip install -e``).
        * **python-pip / anaconda** — no-op; ``session_configure`` created the
          shared session venv and the workload-specific dependency step will
          populate it.

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
            _save_state(run_id, {"step": "installed", "app_built": True})
            return _ok("Build and install succeeded", make=r, install=r2)

        if bt == "meson":
            r = _run(["meson", "compile", "-C", str(build), f"-j{jobs}"], timeout=600)
            if not r["success"]:
                _write_artifact_log(_ws(run_id), 4, "session_build_install", {"build": r}, run_id)
                return _err("meson compile failed", **r)
            r2 = _run(["meson", "install", "-C", str(build)], timeout=300)
            _write_artifact_log(_ws(run_id), 4, "session_build_install", {"build": r, "install": r2}, run_id)
            if not r2["success"]:
                return _err("meson install failed", **r2)
            _save_state(run_id, {"step": "installed", "app_built": True})
            return _ok("Build and install succeeded", build=r, install=r2)

        if bt == "python":
            _save_state(run_id, {"step": "installed", "app_built": True})
            _write_artifact_log(_ws(run_id), 4, "session_build_install", {"status": "python project installed via pip"}, run_id)
            return _ok("Python project installed via session_configure (pip install -e)")

        if bt in {"python-pip", "anaconda"}:
            _save_state(run_id, {"step": "installed", "app_built": True})
            _write_artifact_log(_ws(run_id), 4, "session_build_install", {"status": f"{bt} session venv prepared"}, run_id)
            return _ok(f"{bt} session venv prepared via session_configure")

        return _err(f"Unknown build tool: {bt}")

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
    def session_build_annotated(
        run_id: str,
        jobs: int = 4,
        extra_cmake_flags: str = "",
        custom_build_cmd: str = "",
        build_subdir: str = "",
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
            custom_build_cmd=custom_build_cmd,
            build_subdir=build_subdir,
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
    def dftracer_build_check(run_id: str = "", workspace: str = "", stage: str = "original") -> dict:
        """Validator for the build stage: rule deterministically on a session build. stage='original' checks
        session_detect recorded a build tool, session_build_install recorded app_built, <ws>/install is populated and the
        build log is in artifacts/. stage='annotated' checks dftracer was installed first, the annotated source carries
        dftracer annotations, install_ann/ or build_ann/ is populated and at least one ELF executable or shared object
        there names libdftracer_core (so the binary really links dftracer). Reads files only. Args: run_id or workspace
        (a path, overrides run_id), stage. Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}],
        failed, workspace, build_tool, stage}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _build_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id), stage)

    @mcp.tool()
    def dftracer_build_selftest() -> dict:
        """Self-test of dftracer_build_check on fixtures in a temporary directory: a built original app passes; an
        annotated build whose binary names libdftracer_core passes; one whose binary does not fails links_dftracer; an
        unknown stage and a missing workspace fail. Returns {status: pass|fail, cases: [...]}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "app" / "20260101_000000"
            for p in ("install/bin", "artifacts", "annotated/source", "install_ann/bin"):
                (ws / p).mkdir(parents=True)
            (ws / "install/bin/app").write_text("x")
            (ws / "artifacts/04_session_build_install.log").write_text("ok")
            (ws / "annotated/source/io.c").write_text("#include <dftracer/dftracer.h>\nvoid f(){DFTRACER_C_FUNCTION_START();}\n")
            (ws / "session.json").write_text(json.dumps({"detection": {"build_tool": "cmake"}, "app_built": True,
                                                         "dftracer_install_prefix": "/x"}))
            cases.append({"case": "original", "want": "pass", "got": _build_check(ws, "original")["status"]})
            exe = ws / "install_ann/bin/app"
            exe.write_bytes(b"\x7fELF....NEEDED libdftracer_core.so ....")
            exe.chmod(0o755)
            cases.append({"case": "annotated_linked", "want": "pass", "got": _build_check(ws, "annotated")["status"]})
            exe.write_bytes(b"\x7fELF....NEEDED libc.so.6 ....")
            cases.append({"case": "annotated_unlinked", "want": ["links_dftracer"], "got": _build_check(ws, "annotated")["failed"]})
            cases.append({"case": "bad_stage", "want": ["stage"], "got": _build_check(ws, "x")["failed"]})
            r = _build_check(Path(d) / "nope", "original")
            cases.append({"case": "missing", "want": "unknown run", "got": r["checks"][0]["detail"][:11]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
