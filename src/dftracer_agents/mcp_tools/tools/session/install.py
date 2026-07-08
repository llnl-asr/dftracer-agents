"""
dftracer install helpers — pip installer, cmake builder, and dftracer-utils installer.

This module handles the external installation concerns of the dftracer pipeline:

1. **dftracer via pip** (:func:`_install_dftracer_pip_direct`) — installs
   dftracer directly via ``pip install git+https://github.com/llnl/dftracer.git@<ref>``
   with all setup.py feature env vars (MPI, HDF5, HIP, hwloc, build type, jobs)
   derived from the detected application source and system.  This is the
   standard installation method for all project types.

2. **dftracer via cmake** (:func:`_install_dftracer_cmake`) — clones the
   dftracer repository and builds/installs it directly via cmake configure +
   build + install steps.  Available as a lower-level alternative; prefer pip.

3. **dftracer-utils** (:func:`_install_dftracer_utils`,
   :func:`_dftracer_utils_split`) — installs the ``dftracer-utils`` Python
   package (post-processing tools) from the upstream ``develop`` branch and
   provides a helper that compacts raw trace files via the ``split`` MCP tool.

The split helper (:func:`_dftracer_utils_split`) uses dynamic module loading
(:func:`_load_dftracer_utils_service`) to call the split tool's Python
function directly in-process, avoiding a network round-trip to an MCP server.
It falls back transparently to the ``dftracer_split`` CLI binary when the
service module cannot be loaded.
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from .workspace import _run, _write_artifact_log


_CORE_LIB_NAMES = ("libdftracer_core.so", "libdftracer_core.so.4",
                    "libdftracer_core.dylib")
_CORE_HEADER    = Path("dftracer") / "dftracer.h"


def _find_dftracer_dirs(
    python_exe: Optional[str] = None,
    cmake_prefix: Optional[Path] = None,
) -> Optional[Dict[str, str]]:
    """Locate dftracer include and lib directories, checking every known layout.

    Search order (stops at the first lib dir that contains ``libdftracer_core.so``):

    1. **cmake prefix** — ``<cmake_prefix>/lib/`` and ``<cmake_prefix>/lib64/``
       (produced by a cmake-based dftracer build).
    2. **site-packages pkg dir** — ``<pkg>/lib/`` and ``<pkg>/lib64/``
       (pip wheel layout when the core lib is bundled there).
    3. **site-packages parent** — ``<site-packages>/lib/`` and ``<site-packages>/lib64/``
       (some wheels install the shared lib one level above the package dir).

    The include dir paired with each lib candidate is resolved by looking for
    ``dftracer/dftracer.h`` relative to the same prefix (``<prefix>/include/``).

    Args:
        python_exe: Path to the Python interpreter used to locate the dftracer
            package directory.  Defaults to the current interpreter.
        cmake_prefix: Optional workspace cmake install prefix (``install_ann/``).
            When supplied it is tried first so that cmake-mode installs win.

    Returns:
        Dict with ``include_dir``, ``lib_dir``, and ``lib_name`` on success, or
        ``None`` when no dir containing ``libdftracer_core.so`` is found.
    """
    import json as _json

    def _has_core(d: Path) -> bool:
        return d.is_dir() and any((d / n).exists() for n in _CORE_LIB_NAMES)

    def _include_for(prefix: Path) -> str:
        inc = prefix / "include"
        return str(inc) if (inc / _CORE_HEADER).exists() else str(inc)

    def _lib_name(d: Path) -> str:
        for n in _CORE_LIB_NAMES:
            if (d / n).exists():
                return n
        return "libdftracer_core.so"

    candidates: list[Path] = []

    # 1. cmake prefix (install_ann) — highest priority
    if cmake_prefix:
        candidates += [cmake_prefix / "lib", cmake_prefix / "lib64"]

    # 2. site-packages package dir  (pip wheel: <pkg>/lib, <pkg>/lib64)
    py = python_exe or sys.executable
    script = (
        "import dftracer, os, json; "
        "base = os.path.dirname(os.path.abspath(dftracer.__file__)); "
        "parent = os.path.dirname(base); "
        "print(json.dumps({'pkg': base, 'parent': parent}))"
    )
    try:
        proc = subprocess.run([py, "-c", script],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0 and proc.stdout.strip():
            info = _json.loads(proc.stdout.strip())
            pkg    = Path(info["pkg"])
            parent = Path(info["parent"])
            candidates += [
                pkg    / "lib",  pkg    / "lib64",
                parent / "lib",  parent / "lib64",
            ]
    except Exception:
        pass

    for lib_dir in candidates:
        if _has_core(lib_dir):
            prefix = lib_dir.parent
            return {
                "include_dir": _include_for(prefix),
                "lib_dir":     str(lib_dir),
                "lib_name":    _lib_name(lib_dir),
            }

    # Nothing found — return the cmake prefix dirs anyway so callers can still
    # set RPATH; the build will fail with a clear linker error rather than a
    # silent wrong-path error.
    if cmake_prefix and (cmake_prefix / "lib").is_dir():
        return {
            "include_dir": str(cmake_prefix / "include"),
            "lib_dir":     str(cmake_prefix / "lib"),
            "lib_name":    "libdftracer_core.so",
        }
    return None


# Keep old name as alias so existing callers don't break
_find_dftracer_pip_dirs = _find_dftracer_dirs


#: Absolute path to the ``dftracer_utils_service.py`` module located in the
#: sibling ``dftracer/`` package directory.  Resolved at import time so that
#: :func:`_load_dftracer_utils_service` can use it without re-computing the
#: path on every call.
_UTILS_SERVICE_PATH = Path(__file__).resolve().parent.parent / "dftracer" / "dftracer_utils_service.py"


def _load_dftracer_utils_service():
    """Return the ``dftracer_utils_service`` module, loading it dynamically on first call.

    The module is registered in ``sys.modules`` under the key
    ``"dftracer_agents.mcp_tools.tools.dftracer_utils_service"`` after the
    first successful load, so subsequent calls return the cached module object
    without re-executing the module code.

    Using dynamic loading (rather than a normal import) avoids making
    ``dftracer_utils_service`` a hard dependency of this package: if the file
    does not exist — for example in a minimal install that omits the dftracer
    subpackage — the function returns ``None`` and callers fall back gracefully.

    Returns:
        module or None: The loaded ``dftracer_utils_service`` module, or
            ``None`` if :data:`_UTILS_SERVICE_PATH` does not exist on disk.
    """
    mod_name = "dftracer_agents.mcp_tools.tools.dftracer_utils_service"
    if mod_name in sys.modules:
        return sys.modules[mod_name]
    if not _UTILS_SERVICE_PATH.exists():
        return None
    spec = importlib.util.spec_from_file_location(mod_name, _UTILS_SERVICE_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = mod
    try:
        spec.loader.exec_module(mod)
        return mod
    except Exception:
        # C3: module has relative imports that fail outside the installed package
        # (e.g. "No module named 'dftracer_agents.mcp_service_factory'").
        # Clean up the partial registration and let callers fall back to the binary.
        sys.modules.pop(mod_name, None)
        return None


def _dftracer_info_uncompressed_bytes(file_path: str) -> Optional[int]:
    """Return the uncompressed byte count for a single trace file via dftracer_info.

    Calls ``dftracer_info --files <file> --query summary`` and parses the
    ``Total Uncompressed: ... (<N> bytes)`` line from the output.

    Returns the byte count on success, or ``None`` if the call fails or the
    line cannot be parsed.
    """
    import re as _re
    r = _run(["dftracer_info", "--files", file_path, "--query", "summary"], timeout=60)
    if not r["success"]:
        return None
    m = _re.search(r"Total Uncompressed:.*?\((\d+)\s+bytes\)", r["stdout"])
    if m:
        return int(m.group(1))
    return None


def _dftracer_utils_split(
    directory: str,
    output_dir: str,
    app_name: str = "app",
    chunk_size_mb: int = 512,
) -> Dict[str, Any]:
    """Compact raw dftracer trace files using the ``split`` tool.

    Attempts to invoke the split operation in-process via the
    ``DftracerUtilsService`` MCP service (loaded dynamically by
    :func:`_load_dftracer_utils_service`).  This avoids the overhead of an
    out-of-process call when the service module is available.

    The in-process path introspects the tool object returned by
    ``service.core_subservice.list_tools()`` for a callable attribute
    (trying ``fn``, ``function``, ``callable``, ``handler``, ``_fn`` in
    order) and invokes it with keyword arguments.  Both synchronous and
    ``async`` tool functions are supported.

    If the service module cannot be loaded or any step of the in-process path
    raises an exception, the function falls back to invoking the
    ``dftracer_split`` CLI binary directly via :func:`~workspace._run`.

    Args:
        directory: Path to the directory containing raw ``.pfw`` trace files
            produced by a dftracer-instrumented application run.
        output_dir: Destination directory for the compacted output files.
            Created by the split tool if it does not exist.
        app_name: Application name tag embedded in the output file names.
            Defaults to ``"app"``.
        chunk_size_mb: Target chunk size in MB for each output file.
            Defaults to ``512``.  Larger chunks reduce index overhead
            at the cost of coarser granularity per analysis query.

    Returns:
        Dict[str, Any]: A normalised result dict with keys:

            - ``success`` (bool): ``True`` on success.
            - ``returncode`` (int): Exit code (``0`` on success, non-zero or
              ``-1`` on failure).
            - ``stdout`` (str): Captured output or tool result string.
            - ``stderr`` (str): Error output or exception message.
    """
    mod = _load_dftracer_utils_service()
    if mod is not None:
        try:
            service = mod.DftracerUtilsService()
            tools = asyncio.run(service.core_subservice.list_tools())
            split_tool = next((t for t in tools if t.name == "split"), None)
            if split_tool is not None:
                fn = None
                for attr in ("fn", "function", "callable", "handler", "_fn"):
                    val = getattr(split_tool, attr, None)
                    if callable(val):
                        fn = val
                        break
                if fn is not None:
                    try:
                        kwargs = {"directory": directory, "output_dir": output_dir,
                                  "app_name": app_name, "chunk_size": chunk_size_mb}
                        result = (asyncio.run(fn(**kwargs))
                                  if asyncio.iscoroutinefunction(fn) else fn(**kwargs))
                        return {"success": True, "returncode": 0,
                                "stdout": str(result), "stderr": ""}
                    except subprocess.CalledProcessError as exc:
                        return {"success": False, "returncode": exc.returncode,
                                "stdout": "", "stderr": getattr(exc, "stderr", str(exc))}
        except Exception:
            pass  # fall through to binary fallback

    # Fallback: call binary directly
    return _run(
        ["dftracer_split", "--app-name", app_name, "--directory", directory,
         "--output", output_dir, "--chunk-size", str(chunk_size_mb), "--compress"],
        timeout=600,
    )


def _dftracer_utils_comparator(
    baseline: str,
    variant: str,
    query: str = 'cat == "POSIX" OR cat == "STDIO" OR cat == "C_APP"',
    group_by_dims: str = "cat,name",
    output_format: str = "json",
    threshold_pct: float = 5.0,
) -> Dict[str, Any]:
    """Compare trace metrics between two runs via the DftracerUtilsService comparator tool.

    Invokes ``DftracerUtilsService.analysis_subservice`` comparator in-process
    (same dynamic-loading pattern as :func:`_dftracer_utils_split`).  Falls
    back to the ``dftracer_comparator`` CLI binary if the service cannot be
    loaded.

    Returns:
        Dict[str, Any]: keys ``success``, ``returncode``, ``stdout``, ``stderr``.
    """
    mod = _load_dftracer_utils_service()
    if mod is not None:
        try:
            service = mod.DftracerUtilsService()
            tools = asyncio.run(service.analysis_subservice.list_tools())
            cmp_tool = next((t for t in tools if t.name == "comparator"), None)
            if cmp_tool is not None:
                fn = None
                for attr in ("fn", "function", "callable", "handler", "_fn"):
                    val = getattr(cmp_tool, attr, None)
                    if callable(val):
                        fn = val
                        break
                if fn is not None:
                    try:
                        kwargs = {
                            "baseline": baseline,
                            "variant": variant,
                            "query": query,
                            "group_by_dims": group_by_dims,
                            "output_format": output_format,
                            "threshold_pct": threshold_pct,
                        }
                        result = (
                            asyncio.run(fn(**kwargs))
                            if asyncio.iscoroutinefunction(fn)
                            else fn(**kwargs)
                        )
                        return {"success": True, "returncode": 0,
                                "stdout": str(result), "stderr": ""}
                    except subprocess.CalledProcessError as exc:
                        return {"success": False, "returncode": exc.returncode,
                                "stdout": "", "stderr": getattr(exc, "stderr", str(exc))}
        except Exception:
            pass  # fall through to binary fallback

    # Fallback: call binary directly
    cmd = [
        "dftracer_comparator",
        "--baseline", baseline,
        "--variant", variant,
        "--query", query,
        "--format", output_format,
        "--threshold", str(threshold_pct),
    ]
    if group_by_dims:
        cmd += ["--group-by", group_by_dims]
    return _run(cmd, timeout=120)


def _ensure_session_venv(ws: Path) -> Path:
    """Create an isolated venv at ``<ws>/venv/`` and return its Python executable.

    The venv is created once and reused on subsequent calls.  It is completely
    isolated from the MCP server's own Python environment (no ``--system-site-packages``),
    so every package installed into it — dftracer, dftracer-utils, and any
    project dependencies — is confined to the workspace directory.

    Args:
        ws: Workspace root directory (absolute ``Path``).

    Returns:
        Path to the venv's Python interpreter (``<ws>/venv/bin/python``).

    Raises:
        RuntimeError: If venv creation fails (propagated from ``_run``).
    """
    venv_dir = ws / "venv"
    python = venv_dir / "bin" / "python"
    if not python.exists():
        r = _run(
            [sys.executable, "-m", "venv", "--clear", str(venv_dir)],
            timeout=120,
        )
        if not r["success"]:
            raise RuntimeError(
                f"Failed to create session venv at {venv_dir}: {r['stderr']}"
            )
        # Upgrade pip inside the fresh venv
        _run(
            [str(python), "-m", "pip", "install", "--no-cache-dir",
             "--quiet", "--upgrade", "pip"],
            timeout=120,
        )
    return python


def _install_dftracer_utils(
    pip: Path,
    ws: Optional[Path] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """Install the ``dftracer-utils`` package from its upstream ``develop`` branch.

    Runs ``pip install -v --upgrade git+https://…/dftracer-utils.git@develop``
    using the pip executable at *pip*.  When *ws* is supplied the full verbose
    output is written to ``<ws>/artifacts/06b_session_install_dftracer_utils.log``.

    Args:
        pip: Absolute path to the ``pip`` executable to use.
        ws: Workspace root for artifact logging.  Optional.
        run_id: Session run identifier for the artifact log header.

    Returns:
        Dict[str, Any]: A normalised result dict as returned by
            :func:`~workspace._run`.
    """
    r = _run(
        [str(pip), "install", "-v", "--no-cache-dir", "--upgrade",
         "git+https://github.com/llnl/dftracer-utils.git@develop"],
        timeout=600,
    )
    if ws is not None:
        _write_artifact_log(ws, 6, "session_install_dftracer_utils",
                            {"pip_cmd": str(pip), "pip_install_utils": r}, run_id)
    return r


def _install_dftracer_cmake(
    ws: Path,
    install_prefix: Path,
    dftracer_ref: str = "v2.0.3",
    jobs: int = 4,
    features: Optional[Dict[str, Any]] = None,
    extra_cmake_flags: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Clone dftracer and build/install it directly via cmake.

    Runs three cmake invocations:

    1. ``cmake -S <src> -B <bld> -DCMAKE_INSTALL_PREFIX=<prefix> <flags>``
    2. ``cmake --build <bld> --parallel <jobs>``
    3. ``cmake --install <bld>``

    Feature flags from *features* are translated to cmake ``-D`` flags:

    * ``-DDFTRACER_ENABLE_MPI=ON``   when ``features["mpi"]`` is ``True``
    * ``-DDFTRACER_ENABLE_HDF5=ON``  when ``features["hdf5"]`` is ``True``
    * ``-DHDF5_ROOT=<prefix>``       when ``hdf5_system.prefix`` is known
    * ``-DDFTRACER_ENABLE_PYTHON=OFF``  always — Python bindings are not
      needed for C/C++ annotation and pybind11 adds unnecessary build overhead.

    The dftracer source is cloned once into ``<ws>/dftracer_src/``.
    Subsequent calls reuse the existing clone.  The cmake build tree lives
    at ``<ws>/dftracer_build/``.

    Args:
        ws: Workspace root directory.
        install_prefix: cmake install prefix (``-DCMAKE_INSTALL_PREFIX``).
        dftracer_ref: Git tag or branch to clone.  Defaults to ``"v2.0.3"``.
        jobs: Parallel build jobs.  Defaults to ``4``.
        features: Detected project feature dict from ``_detect_info``.
            Relevant keys: ``"mpi"`` (bool), ``"hdf5"`` (bool),
            ``"hdf5_system"`` (dict with ``"cmake_hint"``).
        extra_cmake_flags: Additional ``-D`` flags appended verbatim after the
            auto-detected flags.  Duplicates are suppressed.

    Returns:
        Dict[str, Any]: Keys ``success`` (bool), ``steps`` (per-step results),
        ``prefix`` (str form of *install_prefix*).
    """
    features = features or {}
    src = ws / "dftracer_src"
    bld = ws / "dftracer_build"
    bld.mkdir(parents=True, exist_ok=True)
    steps: Dict[str, Any] = {}

    # Clone once; reuse on subsequent calls (e.g. retry after a failure)
    if not src.exists():
        r = _run(
            ["git", "clone", "--depth=1", "--branch", dftracer_ref,
             "https://github.com/llnl/dftracer.git", str(src)],
            timeout=600,
        )
        steps["clone"] = r
        if not r["success"]:
            return {"success": False, "steps": steps, "prefix": str(install_prefix)}
    else:
        steps["clone"] = {"status": "reused", "path": str(src)}

    # Base cmake flags — Python/pybind11 disabled for C/C++ projects
    cmake_flags: List[str] = [
        f"-DCMAKE_INSTALL_PREFIX={install_prefix}",
        "-DCMAKE_BUILD_TYPE=RelWithDebInfo",
        "-DDFTRACER_ENABLE_TESTS=OFF",
        "-DDFTRACER_ENABLE_PYTHON=OFF",
    ]

    if features.get("mpi"):
        cmake_flags.append("-DDFTRACER_ENABLE_MPI=ON")
    if features.get("hdf5"):
        cmake_flags.append("-DDFTRACER_ENABLE_HDF5=ON")
        hdf5_hint = (features.get("hdf5_system") or {}).get("cmake_hint", "")
        if hdf5_hint:
            cmake_flags.append(hdf5_hint)

    for flag in (extra_cmake_flags or []):
        if flag not in cmake_flags:
            cmake_flags.append(flag)

    # 1. cmake configure
    r_cfg = _run(
        ["cmake", "-S", str(src), "-B", str(bld)] + cmake_flags,
        timeout=300,
    )
    steps["cmake_configure"] = r_cfg
    if not r_cfg["success"]:
        return {"success": False, "steps": steps, "prefix": str(install_prefix)}

    # 2. cmake build
    r_bld = _run(
        ["cmake", "--build", str(bld), "--parallel", str(jobs)],
        timeout=1800,
    )
    steps["cmake_build"] = r_bld
    if not r_bld["success"]:
        return {"success": False, "steps": steps, "prefix": str(install_prefix)}

    # 3. cmake install
    r_inst = _run(
        ["cmake", "--install", str(bld)],
        timeout=300,
    )
    steps["cmake_install"] = r_inst
    return {
        "success": r_inst["success"],
        "steps": steps,
        "prefix": str(install_prefix),
    }


def _install_dftracer_pip_direct(
    dftracer_ref: str = "v2.0.3",
    features: Optional[Dict[str, Any]] = None,
    python_exe: Optional[str] = None,
    jobs: int = 4,
    pip_env_override: Optional[Dict[str, str]] = None,
    ws: Optional[Path] = None,
    run_id: str = "",
) -> Dict[str, Any]:
    """Install dftracer via pip with all setup.py env vars derived from detected features.

    Runs::

        pip install -v --no-cache-dir --upgrade git+https://github.com/llnl/dftracer.git@<ref>

    Environment variables are built from ``features["dftracer_pip_env"]`` (the
    complete dict produced by ``_detect_info``) and supplemented with fallback
    logic for callers that supply a raw ``features`` dict without
    ``dftracer_pip_env``.  The full set of variables passed to ``setup.py``:

    Always set:
      ``DFTRACER_BUILD_TYPE=RelWithDebInfo``
      ``DFTRACER_ENABLE_TESTS=OFF``
      ``DFTRACER_ENABLE_DLIO_BENCHMARK_TESTS=OFF``
      ``DFTRACER_ENABLE_PAPER_TESTS=OFF``
      ``JOBS=<jobs>``
      ``CMAKE_BUILD_PARALLEL_LEVEL=<jobs>``

    Set when detected in source/system:
      ``DFTRACER_ENABLE_MPI=ON``          — MPI headers/calls found in source
      ``DFTRACER_ENABLE_HDF5=ON``         — HDF5 headers/calls found in source or system
      ``HDF5_ROOT=<prefix>``              — system HDF5 prefix (pkg-config / h5cc)
      ``HDF5_DIR=<prefix>``              — same as HDF5_ROOT
      ``DFTRACER_ENABLE_HIP_TRACING=ON``  — HIP GPU headers/calls found in source
      ``DFTRACER_DISABLE_HWLOC=OFF``      — hwloc dev libs found on system

    Args:
        dftracer_ref: Git tag or branch to install.  Defaults to ``"v2.0.3"``.
        features: Detected project feature dict from ``_detect_info``.  Uses
            ``features["dftracer_pip_env"]`` when present; falls back to
            building the env from individual feature flags.
        python_exe: Python interpreter path.  Defaults to ``sys.executable``.
        jobs: Parallel build jobs passed as ``JOBS`` and
            ``CMAKE_BUILD_PARALLEL_LEVEL``.  Defaults to ``4``.
        pip_env_override: Optional dict of additional env vars that are merged
            on top of the computed env (caller-supplied overrides take priority).
        ws: Workspace root for artifact logging.  When supplied the full verbose
            pip output is written to ``<ws>/artifacts/06_session_install_dftracer.log``.
        run_id: Session run identifier for the artifact log header.

    Returns:
        Dict[str, Any]: Keys ``success`` (bool), ``steps`` (pip_install result),
        ``pip_env`` (the env dict actually used, for diagnostics).
    """
    features = features or {}
    py = python_exe or sys.executable

    # Start from the pre-built pip_env if detection produced one
    pip_env: Dict[str, str] = dict(features.get("dftracer_pip_env") or {})

    # System-specific env (e.g. Tuolumne's CCE lib dirs + /usr/lib64 for libdl)
    # is NOT necessarily present in the MCP server process's own environment,
    # so it must be re-applied here or linking dftracer_core against libdl
    # fails with "undefined reference: dlopen (disallowed by
    # --no-allow-shlib-undefined)". See resources/systems.yaml env.LD_LIBRARY_PATH.
    try:
        from ..system.system_service import get_current_system_env
        for _k, _v in get_current_system_env().items():
            pip_env.setdefault(_k, _v)
    except Exception:
        pass

    # Always-on defaults (fill gaps when dftracer_pip_env is absent or partial)
    pip_env.setdefault("DFTRACER_BUILD_TYPE", "RelWithDebInfo")
    pip_env.setdefault("DFTRACER_ENABLE_TESTS", "OFF")
    pip_env.setdefault("DFTRACER_ENABLE_DLIO_BENCHMARK_TESTS", "OFF")
    pip_env.setdefault("DFTRACER_ENABLE_PAPER_TESTS", "OFF")

    # Feature fallbacks (in case caller passed features without dftracer_pip_env)
    if features.get("mpi"):
        pip_env.setdefault("DFTRACER_ENABLE_MPI", "ON")
    if features.get("hdf5"):
        pip_env.setdefault("DFTRACER_ENABLE_HDF5", "ON")
        hdf5_prefix = (features.get("hdf5_system") or {}).get("prefix") or ""
        if hdf5_prefix:
            # Pin the SOURCE HDF5 explicitly so brahma's cmake FindHDF5 does not
            # auto-detect a system HDF5 (e.g. /usr/bin/h5cc -> /usr/lib64/
            # libhdf5.so.103, a serial 1.10 build).  If that happens, brahma links
            # NEEDED libhdf5.so.103 while the app uses the source libhdf5.so.310 and
            # its HDF5 (and often POSIX) interception silently records nothing —
            # only C_APP annotation events appear.  Two failure modes are pinned out
            # here: (1) wrong library soname, (2) wrong (serial /usr/include) header
            # that leaves H5Pset_fapl_mpio undeclared.
            import os as _os
            from pathlib import Path as _P

            pip_env.setdefault("HDF5_ROOT", hdf5_prefix)
            pip_env.setdefault("HDF5_DIR", hdf5_prefix)
            pip_env.setdefault("HDF5_PREFER_PARALLEL", "ON")

            _hbin = _P(hdf5_prefix) / "bin"
            _hlib = _P(hdf5_prefix) / "lib"
            _hinc = _P(hdf5_prefix) / "include"

            # Prefer the PARALLEL compiler wrapper (h5pcc); a parallel-only HDF5 build
            # ships h5pcc but NOT h5cc, so cmake's default `h5cc` probe would fall
            # through to the system one.  Fall back to h5cc if that is all there is.
            _wrapper = ""
            for _cand in ("h5pcc", "h5cc"):
                if (_hbin / _cand).exists():
                    _wrapper = str(_hbin / _cand)
                    break

            if _wrapper:
                # Expose the wrapper under the name `h5cc` on PATH so any FindHDF5
                # that shells out to `h5cc` resolves to the SOURCE build.
                try:
                    import tempfile as _tf
                    _shim = _P(_tf.gettempdir()) / "dftracer-hdf5bin"
                    _shim.mkdir(parents=True, exist_ok=True)
                    for _nm in ("h5cc", "h5pcc"):
                        _lnk = _shim / _nm
                        if _lnk.is_symlink() or _lnk.exists():
                            _lnk.unlink()
                        _lnk.symlink_to(_wrapper)
                    pip_env["PATH"] = str(_shim) + _os.pathsep + pip_env.get(
                        "PATH", _os.environ.get("PATH", "")
                    )
                except Exception:
                    pass
                # And pass it as a first-class cmake arg (see below: SPACE-joined).
                _hdf5_cmake = (
                    f"-DHDF5_C_COMPILER_EXECUTABLE={_wrapper} -DHDF5_PREFER_PARALLEL=ON"
                )
                _existing = pip_env.get("DFTRACER_CMAKE_ARGS", "")
                pip_env["DFTRACER_CMAKE_ARGS"] = (
                    (_existing + " " + _hdf5_cmake).strip() if _existing else _hdf5_cmake
                )

            # Prepend source HDF5 include/lib so the compiler/linker prefer it over
            # any /usr/include or /usr/lib64 HDF5 that would otherwise leak in.
            for _var, _val in (
                ("CMAKE_PREFIX_PATH", hdf5_prefix),
                ("C_INCLUDE_PATH", str(_hinc)),
                ("CPLUS_INCLUDE_PATH", str(_hinc)),
                ("LIBRARY_PATH", str(_hlib)),
                ("LD_LIBRARY_PATH", str(_hlib)),
            ):
                _cur = pip_env.get(_var, _os.environ.get(_var, ""))
                pip_env[_var] = _val + (_os.pathsep + _cur if _cur else "")
    if features.get("hip"):
        pip_env.setdefault("DFTRACER_ENABLE_HIP_TRACING", "ON")
    if features.get("hwloc"):
        pip_env.setdefault("DFTRACER_DISABLE_HWLOC", "OFF")

    # When MPI is enabled, point CC/CXX at the MPI compiler wrappers so that
    # the dftracer C extension and cmake subbuilds pick up the correct MPI ABI.
    # Prefer the wrapper paths already detected (MPICC/MPICXX from detection),
    # then fall back to shutil.which so this works even without a prior detect step.
    if pip_env.get("DFTRACER_ENABLE_MPI") == "ON":
        import shutil as _shutil_cc
        _mpicc = pip_env.get("MPICC") or _shutil_cc.which("mpicc") or ""
        _mpicxx = pip_env.get("MPICXX") or _shutil_cc.which("mpicxx") or ""
        if _mpicc:
            pip_env.setdefault("CC", _mpicc)
        if _mpicxx:
            pip_env.setdefault("CXX", _mpicxx)

        # Pass DFTRACER_MPI_IMPL override via DFTRACER_CMAKE_ARGS so dftracer's
        # dep cmake skips its own probe and forwards the correct impl to brahma.
        # Also pass BRAHMA_MPI_IMPL directly to override brahma's -v parsing.
        # Detect the actual MPI implementation from the compiler path or env.
        _mpi_impl_override = ""
        if "openmpi" in (_mpicc + pip_env.get("MPICC", "")).lower():
            _mpi_impl_override = "OPENMPI"
        if _mpi_impl_override:
            _existing = pip_env.get("DFTRACER_CMAKE_ARGS", "")
            _new_args = f"-DDFTRACER_MPI_IMPL={_mpi_impl_override}"
            pip_env["DFTRACER_CMAKE_ARGS"] = (
                (_existing + " " + _new_args).strip() if _existing else _new_args
            )

    # cmake 4.x removed compatibility with cmake_minimum_required < 3.5.
    # The gotcha dependency (fetched transitively by brahma) ships an old
    # CMakeLists.txt that triggers this.  Setting CMAKE_POLICY_VERSION_MINIMUM
    # as an env var propagates through all cmake ExternalProject sub-invocations
    # (subprocesses inherit it) so gotcha configures successfully under cmake 4.x.
    pip_env.setdefault("CMAKE_POLICY_VERSION_MINIMUM", "3.5")

    # Build parallelism
    pip_env["JOBS"] = str(jobs)
    pip_env["CMAKE_BUILD_PARALLEL_LEVEL"] = str(jobs)

    # Caller overrides win
    if pip_env_override:
        pip_env.update(pip_env_override)

    # cmake's FindMPI (4.x) does NOT check $ENV{MPICC}.  It discovers the MPI
    # compiler with find_program(NAMES mpicc ...) via PATH.  brahma v1.0.6 then
    # detects the MPI implementation by checking whether MPI_C_COMPILER MATCHES
    # "openmpi".  If the canonical /usr/bin/mpicc is a generic wrapper whose path
    # doesn't contain "openmpi", brahma falls back to UNKNOWN and skips all
    # MPI_File_* virtual overrides.
    #
    # Fix: when we know the OpenMPI wrapper (MPICC=.../mpicc.openmpi), create a
    # symlink at /tmp/dftracer-openmpi/bin/mpicc → that wrapper and prepend the
    # directory to PATH.  cmake FindMPI then stores the full path
    # "/tmp/dftracer-openmpi/bin/mpicc" (which MATCHES "openmpi") in
    # MPI_C_COMPILER, and brahma correctly detects OpenMPI.
    mpicc_path = pip_env.get("MPICC", "")
    if mpicc_path and "openmpi" in mpicc_path:
        # cmake's FindMPI derives _MPI_BASE_DIR from mpiexec's location and then
        # searches that dir with NO_DEFAULT_PATH — so patching PATH alone won't
        # work.  And brahma v1.0.6 guards ALL MPI_File_* implementations with a
        # version check (e.g. BRAHMA_MPI_VERSION >= 400106) derived by parsing
        # "mpicc -v" output for "Open MPI) X.Y.Z".  Since /usr/bin/mpicc.openmpi
        # is an opal_wrapper that outputs GCC info on -v/--version, brahma falls
        # back to the MPI standard version (300100) and the guards fail.
        #
        # Fix: create a synthetic MPI_HOME at /tmp/dftracer-openmpi/ and populate
        # it with:
        #   bin/mpicc  — a shell script that emits "mpicc (Open MPI) X.Y.Z" on -v
        #                and delegates all compilation to the real mpicc.openmpi
        #   bin/mpicxx — same for C++
        #   bin/mpiexec — symlink to mpiexec.openmpi
        # Setting MPI_HOME makes cmake's FindMPI search there first (NO_DEFAULT_PATH)
        # so MPI_C_COMPILER = /tmp/dftracer-openmpi/bin/mpicc (path MATCHES "openmpi").
        # brahma then also gets "Open MPI) 4.1.6" from -v, extracts version 400106,
        # and all MPI_File_* GOTCHA hooks are compiled in.
        from pathlib import Path as _Path
        import shutil as _shutil
        import subprocess as _sp

        # Detect installed OpenMPI version string (e.g. "4.1.6")
        try:
            _ompi_ver_out = _sp.run(
                ["ompi_info", "--version"], capture_output=True, text=True, timeout=10
            ).stdout
            import re as _re
            _m = _re.search(r"Open MPI v?(\d+\.\d+\.\d+)", _ompi_ver_out)
            ompi_version = _m.group(1) if _m else "4.1.6"
        except Exception:
            ompi_version = "4.1.6"

        wrapper_dir = _Path("/tmp/dftracer-openmpi/bin")
        wrapper_dir.mkdir(parents=True, exist_ok=True)

        for cc_name, cc_real in [("mpicc", mpicc_path), ("mpicxx", pip_env.get("MPICXX", ""))]:
            if not cc_real:
                continue
            script = wrapper_dir / cc_name
            # Shell wrapper: emit "mpicc/mpicxx (Open MPI) X.Y.Z" on -v/--version
            # so brahma's cmake can extract the vendor version; for all other
            # invocations delegate straight to the real OpenMPI wrapper.
            script.write_text(
                "#!/bin/bash\n"
                f'REAL="{cc_real}"\n'
                f'OMPI_VER="{ompi_version}"\n'
                "if [[ \"$*\" == *\"-v\"* ]] || [[ \"$*\" == *\"--version\"* ]]; then\n"
                f"  echo \"{cc_name} (Open MPI) $OMPI_VER\"\n"
                'fi\n'
                'exec "$REAL" "$@"\n'
            )
            script.chmod(0o755)

        # Add mpiexec symlink so cmake can derive the MPI base directory
        mpiexec_real = _shutil.which("mpiexec.openmpi") or _shutil.which("mpiexec") or ""
        if mpiexec_real:
            link = wrapper_dir / "mpiexec"
            if link.is_symlink():
                link.unlink()
            link.symlink_to(mpiexec_real)

        pip_env["MPI_HOME"] = str(wrapper_dir.parent)

        # Clone dftracer so we can patch dependency/CMakeLists.txt to:
        # 1. Forward MPI_C_COMPILER to brahma so it detects OpenMPI 4.1.6
        # 2. Use brahma's master branch instead of v1.0.6 — v1.0.6 has a bug
        #    where MPI_Errhandler_create declarations collide with OpenMPI 4.1.x's
        #    removal-macro (#define MPI_Errhandler_create(...) static_assert(0,...)).
        #    brahma's master has the deprecated function removed.
        import tempfile as _tempfile, shutil as _shutil2

        # Pre-clean any previously installed dftracer from the session venv so
        # stale headers (old cpplogger #define macros vs new enum API, stale
        # zconf.h referencing missing zlib_name_mangling.h, brahma without MPI)
        # don't get picked up by the new cmake build via CMAKE_PREFIX_PATH.
        _run([py, "-m", "pip", "uninstall", "-y", "dftracer"], timeout=60)
        _dftracer_sp = _Path(py).parent.parent / "lib" / "python3.12" / "site-packages" / "dftracer"
        if _dftracer_sp.exists():
            import shutil as _shutil_sp
            _shutil_sp.rmtree(str(_dftracer_sp), ignore_errors=True)

        clone_dir = _Path(_tempfile.mkdtemp(prefix="dftracer_src_"))
        r_clone = _run(
            ["git", "clone", "--depth=1", "--branch", dftracer_ref,
             "https://github.com/llnl/dftracer.git", str(clone_dir)],
            timeout=600,
        )
        if r_clone["success"]:
            # Patch dftracer's main cmake: the MPI impl compile definition is
            # set via target_compile_definitions(dftracer_core ...) at line 527,
            # but dftracer_core is only created at line 722.  When cmake finds
            # an IMPORTED dftracer_core from a previous install's cmake config
            # (via CMAKE_PREFIX_PATH), the call fails with "not built by this
            # project".  Fix: use add_compile_definitions (directory-scoped,
            # no target needed) so dftracer_core picks it up when it IS built.
            _dftracer_cmake = clone_dir / "CMakeLists.txt"
            if _dftracer_cmake.exists():
                _dc = _dftracer_cmake.read_text()
                _old_tcd = (
                    "                # Set implementation-specific compile definition\n"
                    "                if(NOT DFTRACER_MPI_IMPL_NAME STREQUAL \"UNKNOWN\")\n"
                    "                        target_compile_definitions(${PROJECT_NAME}_core PUBLIC\n"
                    "                                DFTRACER_MPI_IMPL_${DFTRACER_MPI_IMPL_NAME})\n"
                    "                endif()\n"
                )
                _new_tcd = (
                    "                # Set implementation-specific compile definition\n"
                    "                if(NOT DFTRACER_MPI_IMPL_NAME STREQUAL \"UNKNOWN\")\n"
                    "                        add_compile_definitions(\n"
                    "                                DFTRACER_MPI_IMPL_${DFTRACER_MPI_IMPL_NAME})\n"
                    "                endif()\n"
                )
                if _old_tcd in _dc:
                    _dc = _dc.replace(_old_tcd, _new_tcd)
                    _dftracer_cmake.write_text(_dc)

            # dftracer develop's generated src/dftracer/core/brahma/mpi.h was
            # generated against an MPICH-style MPI where handles are plain
            # integers (MPI_Comm = int, MPI_Datatype = int, etc.).  OpenMPI
            # uses opaque pointer types (MPI_Comm = struct ompi_communicator_t*
            # etc.), so every override declaration in that file either:
            #   a) shadows a typedef by declaring a method with the same name
            #      as the type (e.g. "MPI_Comm MPI_Comm(int)") → GCC 13
            #      -Wchanges-meaning error, then "not a type" for every later
            #      use of that type in the class, or
            #   b) has an int/int* parameter where brahma's virtual uses
            #      MPI_Comm/MPI_Comm*, causing hundreds of "does not override"
            #      errors.
            #
            # MPI-IO tracing (MPI_File_*) lives in mpiio.h which was correctly
            # generated for OpenMPI pointer types and compiles cleanly.
            # Replace mpi.h and mpi.cpp with a minimal stub class that:
            #   • compiles with OpenMPI
            #   • satisfies dftracer_main.cpp (get_instance, bind, unbind, finalize)
            #   • leaves mpiio.h fully functional for MPI_File_* tracing
            _mpi_h = clone_dir / "src" / "dftracer" / "core" / "brahma" / "mpi.h"
            _mpi_cpp = clone_dir / "src" / "dftracer" / "core" / "brahma" / "mpi.cpp"
            _MPI_H_STUB = """\
#ifndef DFTRACER_MPI_H
#define DFTRACER_MPI_H

#include <brahma/brahma.h>
#include <dftracer/core/common/constants.h>
#include <dftracer/core/common/logging.h>
#include <dftracer/core/common/typedef.h>

#ifdef BRAHMA_ENABLE_MPI
#include <dftracer/core/df_logger.h>
#include <mpi.h>

namespace brahma {

// Minimal stub: the dftracer-generated mpi.h was built against MPICH integer
// handles and is incompatible with OpenMPI opaque pointer types.
// MPI-IO tracing (MPI_File_*) is handled by MPIIODFTracer (mpiio.h).
class MPIDFTracer : public MPI {
 private:
  static std::shared_ptr<MPIDFTracer> instance;
  static bool stop_trace;
  std::shared_ptr<DFTLogger> logger;

 public:
  MPIDFTracer() : MPI() { logger = DFT_LOGGER_INIT(); }

  virtual ~MPIDFTracer() {}

  static std::shared_ptr<MPIDFTracer> get_instance() {
    if (!stop_trace && instance == nullptr) {
      instance = std::make_shared<MPIDFTracer>();
      MPI::set_instance(instance);
    }
    return instance;
  }

  void finalize() { stop_trace = true; }
};

}  // namespace brahma

#endif  // BRAHMA_ENABLE_MPI

#endif  // DFTRACER_MPI_H
"""
            _MPI_CPP_STUB = """\
#include <dftracer/core/brahma/mpi.h>

#ifdef BRAHMA_ENABLE_MPI
namespace brahma {
std::shared_ptr<MPIDFTracer> MPIDFTracer::instance = nullptr;
bool MPIDFTracer::stop_trace = false;
}  // namespace brahma
#endif  // BRAHMA_ENABLE_MPI
"""
            if _mpi_h.exists():
                _mpi_h.write_text(_MPI_H_STUB)
            if _mpi_cpp.exists():
                _mpi_cpp.write_text(_MPI_CPP_STUB)

            # Pre-clone brahma v1.0.7 (which dftracer develop uses) and patch:
            # 1. mpi.h: fix OpenMPI 4.x C++11 compile errors (MPI_Errhandler_create
            #    macro clash and missing MPI_Handler_function typedef).
            # 2. CMakeLists.txt: wrap the MPI standard version fallback with
            #    "if (NOT BRAHMA_MPI_VERSION)" so that the externally supplied
            #    -DBRAHMA_MPI_VERSION=<num> from BRAHMA_CONFIGURE_ARGS is honoured
            #    instead of being overwritten by the fallback detection.
            brahma_src_dir = _Path(_tempfile.mkdtemp(prefix="brahma_src_"))
            r_brahma = _run(
                ["git", "clone", "--depth=1", "--branch", "v1.0.7",
                 "https://github.com/hariharan-devarajan/brahma.git",
                 str(brahma_src_dir)],
                timeout=300,
            )
            brahma_local_url = None
            if r_brahma["success"]:
                _brahma_mpi_h = (brahma_src_dir / "include" / "brahma"
                                 / "interface" / "mpi.h")
                if _brahma_mpi_h.exists():
                    _bh = _brahma_mpi_h.read_text()
                    # OpenMPI 4.x in C++11 mode sets OMPI_OMIT_MPI1_COMPAT_DECLS=1
                    # and OMPI_REMOVED_USE_STATIC_ASSERT=1, which:
                    #   1. Omits the MPI_Handler_function typedef from <mpi.h>
                    #   2. Omits the MPI_Errhandler_create function declaration
                    #   3. Defines #define MPI_Errhandler_create(...) static_assert(0,...)
                    # Brahma v1.0.7's class body uses both as types → compile error.
                    # Fix:
                    #   a) #undef the static_assert macro so the identifier is free
                    #   b) Provide the missing MPI_Handler_function typedef
                    #   c) Re-declare MPI_Errhandler_create as a plain extern "C"
                    #      so GOTCHA_MACRO_TYPEDEF can use decltype(&fn)
                    # The symbol still exists in libmpi.so so the binding works
                    # at runtime.
                    _bh = _bh.replace(
                        "#include <mpi.h>",
                        "#include <mpi.h>\n"
                        "#ifdef MPI_Errhandler_create\n"
                        "#undef MPI_Errhandler_create\n"
                        "#endif\n"
                        "#ifndef MPI_Handler_function\n"
                        "typedef void (MPI_Handler_function)(MPI_Comm *, int *, ...);\n"
                        "#endif\n"
                        "extern \"C\" int MPI_Errhandler_create"
                        "(MPI_Handler_function *, MPI_Errhandler *);\n",
                    )
                    _brahma_mpi_h.write_text(_bh)

                # Patch brahma's CMakeLists.txt: fix OpenMPI version detection.
                # brahma runs `mpicc -v` to detect OpenMPI impl version, but on
                # Ubuntu mpicc.openmpi -v outputs GCC verbose info, not "Open MPI
                # X.Y.Z". brahma falls back to the MPI standard version (3.1 →
                # 300100). All mpiio.cpp methods are guarded by:
                #   #if (BRAHMA_MPI_IMPL_OPENMPI && BRAHMA_MPI_VERSION >= 400106)
                # so with BRAHMA_MPI_VERSION=300100 every MPI_File_* method is
                # compiled out. Fix: after the fallback, read ompi/version.h
                # directly to get the OpenMPI implementation version (4.1.6 →
                # 400106).
                # Detect OpenMPI implementation version via MPI C API at
                # Python time (reliable: reads OMPI_MAJOR/MINOR/RELEASE_VERSION
                # from the real mpi.h, not from mpicc -v which outputs GCC info).
                # We pass this as -DBRAHMA_MPI_VERSION=N to brahma's cmake so it
                # skips the unreliable mpicc -v detection and uses our value.
                # Brahma's cmake else() block is patched to honour an externally
                # supplied BRAHMA_MPI_VERSION rather than always overwriting it.
                import subprocess as _sp_mpi, re as _re_mpi
                _brahma_mpi_ver = 0
                try:
                    _ompi_out = _sp_mpi.run(
                        ["ompi_info", "--version"],
                        capture_output=True, text=True, timeout=10,
                    ).stdout
                    _m_ompi = _re_mpi.search(
                        r"Open MPI v?(\d+)\.(\d+)\.(\d+)", _ompi_out
                    )
                    if _m_ompi:
                        _brahma_mpi_ver = (
                            int(_m_ompi.group(1)) * 100000
                            + int(_m_ompi.group(2)) * 100
                            + int(_m_ompi.group(3))
                        )
                except Exception:
                    pass

                # Patch brahma v1.0.7's cmake: wrap the MPI standard version
                # fallback with "if (NOT BRAHMA_MPI_VERSION)" so that
                # -DBRAHMA_MPI_VERSION=<num> passed via BRAHMA_CONFIGURE_ARGS
                # is used instead of being overwritten by the fallback.
                # v1.0.7 uses 6-space indent and "MPI standard version (fallback)".
                _brahma_cmake = brahma_src_dir / "CMakeLists.txt"
                if _brahma_cmake.exists():
                    _bc = _brahma_cmake.read_text()
                    _old_fallback = (
                        "      convert_version_to_number"
                        '("${MPI_C_VERSION}" BRAHMA_MPI_VERSION)\n'
                        "      message(STATUS "
                        '"[${PROJECT_NAME}] MPI standard version (fallback):'
                        ' ${MPI_C_VERSION} (${BRAHMA_MPI_VERSION})")\n'
                    )
                    _new_fallback = (
                        "      if(NOT BRAHMA_MPI_VERSION)\n"
                        "        convert_version_to_number"
                        '("${MPI_C_VERSION}" BRAHMA_MPI_VERSION)\n'
                        "        message(STATUS "
                        '"[${PROJECT_NAME}] MPI standard version (fallback):'
                        ' ${MPI_C_VERSION} (${BRAHMA_MPI_VERSION})")\n'
                        "      else()\n"
                        "        message(STATUS "
                        '"[${PROJECT_NAME}] Using provided'
                        ' BRAHMA_MPI_VERSION: ${BRAHMA_MPI_VERSION}")\n'
                        "      endif()\n"
                    )
                    if _old_fallback in _bc:
                        _bc = _bc.replace(_old_fallback, _new_fallback)
                        _brahma_cmake.write_text(_bc)

                _run(["git", "-C", str(brahma_src_dir), "config",
                      "user.email", "build@local"], timeout=10)
                _run(["git", "-C", str(brahma_src_dir), "config",
                      "user.name", "build"], timeout=10)
                _run(["git", "-C", str(brahma_src_dir), "add", "-A"],
                     timeout=10)
                _run(["git", "-C", str(brahma_src_dir), "commit", "-m",
                      "fix: guard MPI version fallback; undef MPI_Errhandler_create"],
                     timeout=15)
                _run(["git", "-C", str(brahma_src_dir), "tag", "v1.0.7-fix"],
                     timeout=10)
                brahma_local_url = f"file://{brahma_src_dir}"

            dep_cmake = clone_dir / "dependency" / "CMakeLists.txt"
            if dep_cmake.exists():
                dc = dep_cmake.read_text()
                # If we have a patched local brahma, redirect dftracer's cmake
                # to use it instead of fetching v1.0.7 from GitHub.
                if brahma_local_url:
                    dc = dc.replace(
                        "https://github.com/hariharan-devarajan/brahma.git v1.0.7",
                        f"{brahma_local_url} v1.0.7-fix",
                    )
                # Inject BRAHMA_MPI_VERSION into BRAHMA_CONFIGURE_ARGS so that
                # brahma's cmake receives the true OpenMPI implementation version
                # (e.g. 400106 for OpenMPI 4.1.6) rather than falling back to the
                # MPI standard version (300100 for MPI 3.1) which mpicc -v yields.
                # brahma's cmake else() block is patched above to honour this value.
                if _brahma_mpi_ver > 0:
                    dc = dc.replace(
                        'dftracer_install_external_project(brahma',
                        f'string(APPEND BRAHMA_CONFIGURE_ARGS '
                        f'";-DBRAHMA_MPI_VERSION={_brahma_mpi_ver}")\n'
                        'dftracer_install_external_project(brahma',
                    )
                dep_cmake.write_text(dc)

            r = _run(
                [py, "-m", "pip", "install", "-v", "--no-cache-dir", "--upgrade",
                 str(clone_dir)],
                env=pip_env,
                timeout=1800,
            )
            _shutil2.rmtree(str(clone_dir), ignore_errors=True)
        else:
            # Clone failed — fall back to direct git+ install (MPI_Errhandler issue
            # will cause build failure if using OPENMPI 4.1.x, but nothing we can do)
            r = _run(
                [py, "-m", "pip", "install", "-v", "--no-cache-dir", "--upgrade",
                 f"git+https://github.com/llnl/dftracer.git@{dftracer_ref}"],
                env=pip_env,
                timeout=900,
            )
    else:
        r = _run(
            [py, "-m", "pip", "install", "-v", "--no-cache-dir", "--upgrade",
             f"git+https://github.com/llnl/dftracer.git@{dftracer_ref}"],
            env=pip_env,
            timeout=900,
        )
    if ws is not None:
        _write_artifact_log(ws, 6, "session_install_dftracer", {
            "python_exe": py,
            "dftracer_ref": dftracer_ref,
            "pip_env": str(pip_env),
            "pip_install": r,
        }, run_id)
    return {"success": r["success"], "steps": {"pip_install": r}, "pip_env": pip_env}


