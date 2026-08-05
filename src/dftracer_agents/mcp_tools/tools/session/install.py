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
import re
import shlex
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

    # dftracer's top-level CMakeLists bootstraps its own vendored dependencies
    # (cpp-logger, brahma, gotcha, libuv) via `-DDFTRACER_INSTALL_DEPENDENCIES=ON`.
    # On a first-ever configure of a build directory, that flag ONLY registers
    # dependency-fetch/build targets — it prints "downloading dependencies.
    # Please run make for downloading dependencies and then do reconfigure
    # without dependency flag" and the SAME build produces zero dftracer_core /
    # dftracer_service targets to compile (a `cmake --build` at this point exits
    # 0 almost instantly with no compiler invocations at all — easy to mistake
    # for "nothing needed rebuilding"). The main library/executable targets only
    # appear after a SECOND configure of the SAME build directory with
    # `-DDFTRACER_INSTALL_DEPENDENCIES=OFF` (now that CMake's dependency
    # find_package calls succeed against what pass 1 just installed). This is
    # not a one-off bootstrap needed only on a brand new checkout — it is
    # required on EVERY from-scratch cmake-based dftracer build (confirmed
    # 2026-07-14 rebuilding dftracer for vpic-kokkos after a stale build dir
    # was lost mid-session). Skipping straight to a single configure+build
    # silently produces an install with NO updated dftracer_core/_service
    # binaries, while reporting a clean `cmake --build` exit code.
    # 1a. cmake configure — pass 1 (dependency bootstrap)
    r_cfg1 = _run(
        ["cmake", "-S", str(src), "-B", str(bld)] + cmake_flags +
        ["-DDFTRACER_INSTALL_DEPENDENCIES=ON"],
        timeout=300,
    )
    steps["cmake_configure_pass1_deps"] = r_cfg1
    if not r_cfg1["success"]:
        return {"success": False, "steps": steps, "prefix": str(install_prefix)}

    r_bld1 = _run(
        ["cmake", "--build", str(bld), "--parallel", str(jobs)],
        timeout=1800,
    )
    steps["cmake_build_pass1_deps"] = r_bld1
    if not r_bld1["success"]:
        return {"success": False, "steps": steps, "prefix": str(install_prefix)}

    # 1b. cmake configure — pass 2 (real dftracer_core/_service targets appear
    # now that dependencies are marked installed)
    r_cfg2 = _run(
        ["cmake", "-S", str(src), "-B", str(bld)] + cmake_flags +
        ["-DDFTRACER_INSTALL_DEPENDENCIES=OFF"],
        timeout=300,
    )
    steps["cmake_configure_pass2_main"] = r_cfg2
    if not r_cfg2["success"]:
        return {"success": False, "steps": steps, "prefix": str(install_prefix)}

    # 2. cmake build — pass 2 (actually compiles dftracer_core/_service/etc.)
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


_MODULE_LOAD_RE = re.compile(r"^\s*module\s+load\s+(.+?)\s*(?:#.*)?$", re.MULTILINE)


def _discover_app_module_loads(source_dir: Optional[Path]) -> List[str]:
    """Scan the app's own install/build scripts for ``module load`` lines.

    Apps that already run on a system (e.g. an existing ``venv_*.sh`` or
    ``install.sh`` checked into the repo) usually encode the EXACT compiler/
    MPI/library module combination known to work together — that is a much
    stronger signal than re-deriving a module list from scratch. Returns a
    deduplicated, order-preserving list of module tokens (e.g.
    ``["rocm/6.3.1", "cmake/3.23.1", "gcc/10.3.1"]``) pulled from every
    ``module load ...`` line found in any ``*.sh`` file up to 2 directories
    deep under *source_dir*. Empty list if *source_dir* is unset/missing or
    no such lines are found -- caller should fall back to the system's
    default module list (see ``get_current_system_modules``) in that case.
    """
    if not source_dir or not Path(source_dir).is_dir():
        return []
    seen: Dict[str, None] = {}
    try:
        for script in sorted(Path(source_dir).glob("**/*.sh")):
            try:
                depth = len(script.relative_to(source_dir).parts)
            except ValueError:
                continue
            if depth > 3:
                continue
            try:
                text = script.read_text(errors="ignore")
            except Exception:
                continue
            for m in _MODULE_LOAD_RE.finditer(text):
                for tok in m.group(1).split():
                    tok = tok.strip()
                    if tok and not tok.startswith("$") and not tok.startswith("-"):
                        seen.setdefault(tok, None)
    except Exception:
        return []
    return list(seen.keys())


def _modules_available(modules: List[str]) -> List[str]:
    """Return the subset of *modules* that ``module avail`` finds on THIS system.

    App-discovered modules (see ``_discover_app_module_loads``) may come from
    a checked-in script written for a different/older system than the one
    this session is actually running on (e.g. 1000genome-workflow's
    ``env.sh``) -- loading a stale/incompatible module name lets Lmod
    auto-replace it with an unrelated default, silently changing the
    compiler/MPI combination the rest of the session assumes. Validate before
    using rather than trusting the app script blindly.

    Best-effort: if the ``module`` command/subprocess itself is unavailable
    or fails, fail OPEN (return *modules* unchanged) rather than blocking a
    build on a broken probe -- this is a guard against a bad *module name*,
    not a general module-command health check.
    """
    if not modules:
        return modules
    try:
        import subprocess as _sp
        cmd = "module avail -t " + " ".join(shlex.quote(m) for m in modules) + " 2>&1"
        proc = _sp.run(["bash", "-lc", cmd], capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "") + (proc.stderr or "")
    except Exception:
        return modules
    if not out.strip():
        # `module` command produced nothing at all (e.g. not installed on
        # this system) -- can't verify either way, fail open.
        return modules
    ok = []
    for m in modules:
        base = m.split("/")[0]
        if m in out or base in out:
            ok.append(m)
    return ok


def _ensure_session_env_script(ws: Path, source_dir: Optional[Path] = None) -> Path:
    """Create (once) and return the session's canonical ``env.sh``.

    Written the FIRST time any step needs it (normally ``session_detect``,
    the earliest point ``source/`` exists to scan) and then REUSED by every
    later build/install/run step (dftracer install, app build, smoke test,
    trace runs) -- so they all `module load` the exact same modules instead
    of each step re-deriving (and potentially disagreeing on) its own list.

    Module source priority:
      1. The app's OWN install/build scripts under *source_dir* (a
         ``module load`` line in a checked-in ``venv_*.sh``/``install.sh``
         encodes a combination already known to work for this specific app
         -- a much stronger signal than guessing from scratch).
      2. The system's default module list (``get_current_system_modules``).

    Idempotent: if ``<ws>/scripts/env.sh`` already exists this is a no-op
    that just returns its path -- callers wanting to force a refresh (e.g.
    the app's scripts changed) must remove it first via
    ``session_remove_path``.

    Lives under ``<ws>/scripts/`` (not the workspace root) -- every script
    the session produces, including this one and any manual/ad-hoc script
    run outside the normal step pipeline, belongs in that one directory.
    """
    ws = Path(ws)
    scripts_dir = ws / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    env_script = scripts_dir / "env.sh"
    if env_script.exists():
        return env_script

    modules = _discover_app_module_loads(source_dir)
    if modules:
        # Guard against a stale/incompatible module list checked into the
        # app's own scripts (written for a different system) -- validate
        # each token exists on THIS system before trusting it, or Lmod will
        # silently auto-replace an unavailable module with an unrelated
        # default (root cause of the 1000genome-workflow env.sh incident).
        modules = _modules_available(modules)
    module_source = "app install scripts" if modules else "system default (systems.yaml)"
    if not modules:
        try:
            from ..system.system_service import get_current_system_modules
            modules = get_current_system_modules()
        except Exception:
            modules = []

    # Neither an app's own scripts (a fresh clone has none yet) nor the
    # system default module list (systems.yaml's Tuolumne entry has no ROCm
    # module -- most sessions don't need one) load ROCm. If this session's
    # session_detect already determined the app genuinely needs HIP
    # (detection.hip_tracing_needed), append the resolved rocm/X.Y.Z module
    # so every later build/install/run step that sources this SAME env.sh
    # actually has hipcc/ROCm's CMake config on PATH. Without this, a
    # DFTRACER_ENABLE_HIP_TRACING=ON install (or any HIP compilation unit in
    # the app's own build) fails deep inside cmake/ninja with a confusing
    # unrelated error (e.g. "'stdlib.h' file not found" from a mis-configured
    # HIP compiler frontend that never got ROCM_PATH/PATH set up) -- confirmed
    # on RAJAPerf 2026-08-05, where session_configure had its own ad-hoc
    # module line but session_install_dftracer's cached env.sh predated that
    # fix and never picked up rocm/7.2.1. Best-effort: any failure reading
    # session.json just skips this (module list falls back to whatever was
    # already computed above).
    try:
        import json as _json
        _state_file = ws / "session.json"
        if _state_file.exists():
            _state = _json.loads(_state_file.read_text())
            _det = _state.get("detection") or {}
            if _det.get("hip_tracing_needed"):
                _rocm_module = (_det.get("rocm_info") or {}).get("module")
                if _rocm_module and not any(
                    m == _rocm_module or m.split("/")[0] == "rocm" for m in modules
                ):
                    modules = list(modules) + [_rocm_module]
    except Exception:
        pass

    sys_env: Dict[str, str] = {}
    try:
        from ..system.system_service import get_current_system_env
        sys_env = get_current_system_env()
    except Exception:
        pass

    lines = [
        "#!/bin/bash",
        "# Auto-generated ONCE by dftracer-agents (session_detect / first",
        "# install step) -- every build/install/run step in this session",
        "# sources this SAME file so they share one consistent environment.",
        f"# Module source: {module_source}",
        "# To refresh: session_remove_path(run_id, 'env.sh') then re-run detection.",
    ]
    if modules:
        lines.append(f"module load {' '.join(shlex.quote(m) for m in modules)}")
    else:
        lines.append("# no modules discovered from app scripts or systems.yaml -- add manually if needed")
    for k, v in sys_env.items():
        lines.append(f"export {k}={shlex.quote(str(v))}")
    env_script.write_text("\n".join(lines) + "\n")
    env_script.chmod(0o755)
    return env_script


def _run_pip_via_module_script(
    py: str,
    pip_args: List[str],
    pip_env: Dict[str, str],
    modules: List[str],
    ws: Optional[Path] = None,
    timeout: int = 900,
    env_script: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run ``<py> -m pip <pip_args>`` inside a real ``module load``'d shell.

    Hand-assembling individual env vars (CC, CXX, LD_LIBRARY_PATH, ...) is
    NOT equivalent to actually running ``module load`` -- Cray's compiler
    driver scripts additionally consult PE_ENV/CRAY_* variables that
    ``module load`` sets internally to pick the correct companion GNU
    toolchain. Skipping the real module load lets the driver silently fall
    back to whatever stray GCC it finds on PATH (root-caused 2026-07-20 on a
    pecan_milan session: Cray Clang picked up an unrelated
    ``/opt/rh/gcc-toolset-13`` and failed with "stdlib.h file not found"
    building dftracer's vendored cpp-logger). Writing a real ``module
    load ...`` line into a login-shell script (``bash -l``, so
    ``/etc/profile.d/*.sh`` initializes the ``module`` function) and running
    the pip command AFTER it is the fix -- not more env var guessing.

    When *env_script* is given (the session's canonical ``env.sh`` from
    ``_ensure_session_env_script``), it is sourced instead of inlining a
    fresh ``module load`` line -- this is what keeps every step in the
    session using the SAME modules rather than each one re-deriving its own.
    *modules* is only used as a fallback when no *env_script* is available.

    The explicit ``pip_env`` overrides (CC/CXX/HDF5_ROOT/DFTRACER_* etc.)
    are still exported on top of the module-load'd environment, so any
    caller-computed values win over whatever the modules alone would set.
    """
    lines = ["#!/bin/bash", "set -e"]
    if env_script is not None and Path(env_script).exists():
        lines.append(f"source {shlex.quote(str(env_script))}")
    elif modules:
        lines.append(f"module load {' '.join(shlex.quote(m) for m in modules)}")
    else:
        lines.append("# no modules discovered from app install scripts or systems.yaml")
    lines.append('echo "--- module list after load ---" 1>&2')
    lines.append("module list 2>&1 1>&2 || true")
    for k, v in pip_env.items():
        lines.append(f"export {k}={shlex.quote(str(v))}")
    lines.append(f"exec {shlex.quote(py)} -m pip {' '.join(shlex.quote(a) for a in pip_args)}")
    script_body = "\n".join(lines) + "\n"

    if ws is not None:
        # Every script the session produces lives under scripts/, not tmp/
        # or the workspace root -- this is the session's persistent record
        # of exactly how dftracer was built, reusable for a manual rebuild.
        scripts_dir = Path(ws) / "scripts"
        try:
            scripts_dir.mkdir(parents=True, exist_ok=True)
            script_path = scripts_dir / "install_dftracer.sh"
            script_path.write_text(script_body)
            script_path.chmod(0o755)
            return _run(["bash", "-l", str(script_path)], timeout=timeout)
        except Exception:
            pass
    return _run(["bash", "-lc", script_body], timeout=timeout)


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

    # Never silently fall back to a bare system/module interpreter here: `py`
    # MUST be a path inside a real venv (pyvenv.cfg present in its
    # grandparent dir). session_install_dftracer validates this before
    # calling in, but this function is also callable directly, so re-validate
    # at the point the actual pip/cmake subprocess is about to run — this is
    # exactly the class of bug (dftracer's cmake configure picking up system
    # Anaconda instead of the session venv) that caused the 2026-07-20
    # pecan_milan install failure.
    #
    # IMPORTANT: do NOT `.resolve()` `py` here — `<venv>/bin/python` is BY
    # DESIGN a symlink out to the base interpreter, so resolving it walks
    # straight back to the system Python this check exists to reject. Use
    # `.absolute()` (normalizes `..`/relative segments without following
    # symlinks) to find the venv root instead.
    _venv_root = str(Path(py).absolute().parent.parent)
    if not (Path(_venv_root) / "pyvenv.cfg").exists():
        raise RuntimeError(
            f"_install_dftracer_pip_direct: python_exe={py!r} does not resolve "
            f"inside a real venv (no pyvenv.cfg at {_venv_root}). Refusing to "
            f"install dftracer outside a session-local venv -- pass an "
            f"explicit python_exe pointing into one."
        )

    # Make the target venv look "activated" to the pip subprocess.  `py` here
    # is only ever invoked as `<py> -m pip install ...`, so pip itself always
    # installs into the right site-packages -- but dftracer's own cmake
    # configure step (invoked by the build backend as a SEPARATE process, not
    # via `python -c`) runs CMake's `find_package(Python3)`, which searches
    # PATH for a `python3`/`python` binary and has no idea `py` was passed to
    # pip. Without VIRTUAL_ENV set and the venv's bin/ prepended to PATH, it
    # silently finds whatever python the loaded system module put on PATH
    # first (e.g. Tuolumne's anaconda-based `python/3.13.2` module) and
    # computes CMAKE_INSTALL_PREFIX from THAT interpreter's site-packages --
    # which is read-only system Anaconda, not the session venv. Root-caused
    # 2026-07-20 on a pecan_milan session: `pip install` via the venv's own
    # python still failed with "file cannot create directory
    # .../anaconda3-2025.3.1/lib/python3.13/site-packages/dftracer/... Maybe
    # need administrative privileges" even with `--no-cache-dir --upgrade`
    # and an explicit venv `py`. Do NOT reach for `--no-build-isolation` to
    # fix this (see the explicit comment further down in this function) --
    # that's an unrelated isolation knob and does not fix Python discovery.
    pip_env: Dict[str, str] = {}
    pip_env["VIRTUAL_ENV"] = _venv_root
    pip_env["PATH"] = str(Path(py).absolute().parent) + os.pathsep + os.environ.get("PATH", "")

    # Use the session's ONE canonical env.sh (module load list) so this
    # install step loads the exact same modules every other step in the
    # session does -- created once (normally by session_detect) and reused,
    # never re-derived independently here. `_install_modules` is only kept
    # as a fallback for the (should-not-happen) case where `ws` is None.
    _install_env_script: Optional[Path] = None
    _install_modules: List[str] = []
    if ws is not None:
        _install_env_script = _ensure_session_env_script(Path(ws), Path(ws) / "source")
    else:
        _install_modules = _discover_app_module_loads(None)
        if not _install_modules:
            try:
                from ..system.system_service import get_current_system_modules
                _install_modules = get_current_system_modules()
            except Exception:
                _install_modules = []

    # Always clear any stale dftracer install (site-packages + pip's wheel
    # cache for dftracer) before rebuilding.  A stale compiled .so can carry
    # over an old brahma wrapper shape or headers baked in from a prior HDF5/
    # MPI combo, which silently survives a plain `pip install --upgrade` and
    # makes rebuild iterations non-deterministic.  See workload-h5bench skill
    # (2026-07-10) for the incident this fixes.
    try:
        import glob as _glob
        import shutil as _shutil_clean
        _site_glob = str(Path(py).parent.parent / "lib" / "python*" / "site-packages" / "dftracer*")
        for _stale in _glob.glob(_site_glob):
            _shutil_clean.rmtree(_stale, ignore_errors=True)
        _run([py, "-m", "pip", "cache", "remove", "dftracer"], timeout=30)
    except Exception:
        pass

    # Merge in the pre-built pip_env if detection produced one (VIRTUAL_ENV/PATH
    # set above take priority over anything detection computed, since those
    # are specific to the venv we were explicitly told to install into).
    for _k, _v in (features.get("dftracer_pip_env") or {}).items():
        pip_env.setdefault(_k, _v)

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
    # Brahma's HDF5 async wrapper signature depends on the exact HDF5 version
    # compiled against (HDF5 >= 1.13 macro-expands H5*_async(...) calls to
    # prepend app_file/app_func/app_line, changing the real argument count).
    # Without an explicit version forwarded to brahma's cmake, its dependency
    # build silently assumes a default and the wrapper shape can mismatch,
    # corrupting every hid_t argument at runtime (exit 0, no output file,
    # HDF5-DIAG errors) -- confirmed 2026-07-10 on HDF5 1.14.5. Mirrors the
    # same brahma_int / BRAHMA_MPI_VERSION forwarding pattern used for MPI
    # below. dftracer only supports one exact patch version per HDF5
    # major.minor line: 1.8.23, 1.10.5, 1.12.3, 1.14.5 (see software-hdf5
    # skill) -- session_detect should already have validated the detected
    # hdf5_system.version against that list.
    _brahma_hdf5_ver = 0
    _hdf5_version_str = (features.get("hdf5_system") or {}).get("version") or ""
    if _hdf5_version_str:
        try:
            _hparts = [int(p) for p in _hdf5_version_str.split(".")[:3]]
            while len(_hparts) < 3:
                _hparts.append(0)
            _brahma_hdf5_ver = _hparts[0] * 100000 + _hparts[1] * 1000 + _hparts[2]
        except (ValueError, IndexError):
            _brahma_hdf5_ver = 0

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
                # HDF5_ROOT/CMAKE_PREFIX_PATH are passed EXPLICITLY as -D args (not
                # left as env-var-only hints) so dftracer's find_package(HDF5) is
                # forced to this exact source build and never falls through to its
                # own auto-detection/system-probing path -- same "detect once,
                # never let dftracer/brahma auto-detect" principle used for
                # DFTRACER_MPI_IMPL below.
                _hdf5_cmake = (
                    f"-DHDF5_C_COMPILER_EXECUTABLE={_wrapper} -DHDF5_PREFER_PARALLEL=ON "
                    f"-DHDF5_ROOT={hdf5_prefix} -DCMAKE_PREFIX_PATH={hdf5_prefix} "
                    f"-DHDF5_NO_FIND_PACKAGE_CONFIG_FILE=ON"
                )
                _existing = pip_env.get("DFTRACER_CMAKE_ARGS", "")
                pip_env["DFTRACER_CMAKE_ARGS"] = (
                    (_existing + " " + _hdf5_cmake).strip() if _existing else _hdf5_cmake
                )

            # Prepend source HDF5 include/lib so the compiler/linker prefer it over
            # any /usr/include or /usr/lib64 HDF5 that would otherwise leak in.
            #
            # NEVER use the C_INCLUDE_PATH/CPLUS_INCLUDE_PATH env vars for this
            # on a Cray-clang toolchain: they are NOT a safe "extra -I" —
            # setting either one (confirmed with JUST CPLUS_INCLUDE_PATH=
            # /usr/include, nothing else) breaks Cray clang's own internal
            # GCC-toolchain auto-detection's `#include_next <stdlib.h>` chain
            # (it auto-selects a headers-only GCC toolset, e.g.
            # /opt/rh/gcc-toolset-13, for libstdc++ and expects to fall through
            # to the system libc's stdlib.h next -- the CPATH-family env vars
            # get spliced into that internal search chain ahead of the
            # implicit /usr/include fallback, so cstdlib's #include_next
            # resolves nowhere) and produces `fatal error: 'stdlib.h' file not
            # found` for EVERY C++ translation unit in the build, not just the
            # ones that need HDF5 headers -- confirmed to take down dftracer's
            # unrelated vendored cpp-logger dependency this way (RAJAPerf
            # 2026-08-05; same symptom seen 2026-07-20 on pecan_milan).
            # CFLAGS/CXXFLAGS `-I<dir>` achieves the same "prefer this HDF5"
            # goal without touching the compiler's internal system-header
            # resolution -- verified directly to compile clean where the env
            # var version failed identically.
            for _var, _val in (
                ("CMAKE_PREFIX_PATH", hdf5_prefix),
                ("LIBRARY_PATH", str(_hlib)),
                ("LD_LIBRARY_PATH", str(_hlib)),
            ):
                _cur = pip_env.get(_var, _os.environ.get(_var, ""))
                pip_env[_var] = _val + (_os.pathsep + _cur if _cur else "")
            for _var in ("CFLAGS", "CXXFLAGS"):
                _cur_flags = pip_env.get(_var, _os.environ.get(_var, ""))
                _inc_flag = f"-I{_hinc}"
                pip_env[_var] = (
                    f"{_inc_flag} {_cur_flags}".strip() if _cur_flags else _inc_flag
                )
    elif features.get("hdf5") is False:
        # Explicit off (either "not detected" or a caller override via
        # session_install_dftracer(hdf5=False)) — force it rather than
        # relying on the dftracer build's own default, so a system HDF5
        # cannot get silently re-enabled by auto-detection.
        pip_env.setdefault("DFTRACER_ENABLE_HDF5", "OFF")
    if features.get("hip"):
        pip_env.setdefault("DFTRACER_ENABLE_HIP_TRACING", "ON")
        # find_package(rocprofiler-sdk) needs the ROCm prefix reachable via
        # CMAKE_PREFIX_PATH (ROCM_PATH/HIP_PATH are set separately above from
        # dftracer_pip_env, in case dftracer's CMakeLists.txt reads those
        # instead) — without one of these, HIP tracing SILENTLY compiles out
        # with no build error at all (see software-rocm skill). Append
        # (never overwrite) since HDF5's own prefix may already be first in
        # CMAKE_PREFIX_PATH — CMake's list-valued CMAKE_PREFIX_PATH searches
        # every entry, order does not matter for find_package to succeed.
        _rocm_prefix = (features.get("rocm") or {}).get("path")
        if _rocm_prefix:
            _cur_cpp = pip_env.get("CMAKE_PREFIX_PATH", _os.environ.get("CMAKE_PREFIX_PATH", ""))
            pip_env["CMAKE_PREFIX_PATH"] = _rocm_prefix + (_os.pathsep + _cur_cpp if _cur_cpp else "")
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
        # Without this flag, brahma's cmake does not generate #define BRAHMA_MPI_IMPL_CRAYMPICH
        # (etc.) in the generated brahma_config.hpp header file, causing all MPI-IO functions
        # to be excluded by #if defined(BRAHMA_MPI_IMPL_CRAYMPICH) preprocessor gates.
        # Strategy: prefer session_detect's stored mpi_impl.impl, fall back to path-based
        # detection for all major MPI implementations (not just OpenMPI).

        _mpi_impl_override = ""

        # Option 1: Read from session state (session_detect already did this work)
        mpi_impl_info = features.get("mpi_impl", {})
        if isinstance(mpi_impl_info, dict) and mpi_impl_info.get("impl"):
            _impl_name = mpi_impl_info.get("impl", "").upper()
            # Map detected impl name to CMAKE flag
            _impl_map = {
                "OPENMPI": "OPENMPI",
                "MPICH": "MPICH",
                "CRAYMPICH": "CRAYMPICH",
                "MVAPICH": "MVAPICH",
                "INTELMPI": "INTELMPI",
            }
            _mpi_impl_override = _impl_map.get(_impl_name, "")

        # Option 2: Fall back to path-based detection for all MPI implementations
        # (in case session_detect was not run or mpi_impl is missing from session state)
        if not _mpi_impl_override:
            _mpicc_str = (_mpicc + pip_env.get("MPICC", "")).lower()
            if "openmpi" in _mpicc_str:
                _mpi_impl_override = "OPENMPI"
            elif "craympich" in _mpicc_str or "cray" in _mpicc_str:
                _mpi_impl_override = "CRAYMPICH"
            elif "mvapich" in _mpicc_str:
                _mpi_impl_override = "MVAPICH"
            elif "impi" in _mpicc_str or "intel" in _mpicc_str:
                _mpi_impl_override = "INTELMPI"
            elif "mpich" in _mpicc_str:
                # Generic MPICH (catches plain mpich without cray/mvapich prefix)
                _mpi_impl_override = "MPICH"

        if _mpi_impl_override:
            _existing = pip_env.get("DFTRACER_CMAKE_ARGS", "")
            _new_args = f"-DDFTRACER_MPI_IMPL={_mpi_impl_override}"
            pip_env["DFTRACER_CMAKE_ARGS"] = (
                (_existing + " " + _new_args).strip() if _existing else _new_args
            )

        # Also pass BRAHMA_MPI_VERSION (numeric version: major*100000 + minor*100 + patch)
        # so brahma's cmake uses the correct implementation version instead of falling back
        # to the MPI standard version (e.g., 300100 for MPI 3.1) which is outside brahma's
        # supported ranges for all implementations. Without this, brahma's preprocessor gates
        # (#if BRAHMA_MPI_IMPL_CRAYMPICH && BRAHMA_MPI_VERSION >= 900001) exclude all
        # MPI-IO interception functions.
        _brahma_mpi_ver = 0

        # Option 1: Read from session state (session_detect already computed this)
        if isinstance(mpi_impl_info, dict) and mpi_impl_info.get("brahma_int"):
            _brahma_mpi_ver = mpi_impl_info.get("brahma_int", 0)
            if isinstance(_brahma_mpi_ver, str):
                try:
                    _brahma_mpi_ver = int(_brahma_mpi_ver)
                except (ValueError, TypeError):
                    _brahma_mpi_ver = 0

        # Option 2: Fall back to computing the version for each MPI implementation
        # via the compiler wrapper (-v output) if not found in session state
        if _brahma_mpi_ver == 0 and _mpi_impl_override:
            import subprocess as _sp_mpi_ver, re as _re_mpi_ver
            try:
                _mpicc_exe = pip_env.get("MPICC") or _mpicc or ""
                if not _mpicc_exe:
                    # Try to find it in PATH
                    import shutil as _shutil_find_mpi
                    _mpicc_exe = _shutil_find_mpi.which("mpicc") or ""

                if _mpicc_exe:
                    _ver_out = _sp_mpi_ver.run(
                        [_mpicc_exe, "-v"],
                        capture_output=True, text=True, timeout=10
                    ).stderr + _sp_mpi_ver.run(
                        [_mpicc_exe, "--version"],
                        capture_output=True, text=True, timeout=10
                    ).stdout

                    # Parse version for each known MPI implementation
                    if _mpi_impl_override == "OPENMPI":
                        # OpenMPI: "Open MPI vX.Y.Z" or "Open MPI X.Y.Z"
                        _m = _re_mpi_ver.search(r"Open MPI v?(\d+)\.(\d+)\.(\d+)", _ver_out)
                        if _m:
                            _brahma_mpi_ver = (
                                int(_m.group(1)) * 100000
                                + int(_m.group(2)) * 100
                                + int(_m.group(3))
                            )
                    elif _mpi_impl_override == "CRAYMPICH":
                        # Cray MPICH: "Cray MPICH version X.Y.Z" or similar
                        _m = _re_mpi_ver.search(r"version\s+(\d+)\.(\d+)\.(\d+)", _ver_out, _re_mpi_ver.IGNORECASE)
                        if _m:
                            _brahma_mpi_ver = (
                                int(_m.group(1)) * 100000
                                + int(_m.group(2)) * 100
                                + int(_m.group(3))
                            )
                    elif _mpi_impl_override == "MVAPICH":
                        # MVAPICH: "MVAPICH2 vX.Y.Z" or "MVAPICH vX.Y"
                        _m = _re_mpi_ver.search(r"MVAPICH\d?\s+v?(\d+)\.(\d+)(?:\.(\d+))?", _ver_out, _re_mpi_ver.IGNORECASE)
                        if _m:
                            _brahma_mpi_ver = (
                                int(_m.group(1)) * 100000
                                + int(_m.group(2)) * 100
                                + (int(_m.group(3)) if _m.group(3) else 0)
                            )
                    elif _mpi_impl_override == "INTELMPI":
                        # Intel MPI: "Intel(R) MPI Library X.Y" or similar
                        _m = _re_mpi_ver.search(r"Intel.*MPI.*(\d+)\.(\d+)(?:\.(\d+))?", _ver_out, _re_mpi_ver.IGNORECASE)
                        if _m:
                            _brahma_mpi_ver = (
                                int(_m.group(1)) * 100000
                                + int(_m.group(2)) * 100
                                + (int(_m.group(3)) if _m.group(3) else 0)
                            )
                    elif _mpi_impl_override == "MPICH":
                        # MPICH: "MPICH vX.Y.Z" or "mpich version X.Y"
                        _m = _re_mpi_ver.search(r"MPICH\s+v?(\d+)\.(\d+)(?:\.(\d+))?", _ver_out, _re_mpi_ver.IGNORECASE)
                        if _m:
                            _brahma_mpi_ver = (
                                int(_m.group(1)) * 100000
                                + int(_m.group(2)) * 100
                                + (int(_m.group(3)) if _m.group(3) else 0)
                            )
            except Exception:
                # If probe fails, _brahma_mpi_ver remains 0 and brahma will fall back
                # to its own detection (which may not work correctly for non-OpenMPI)
                pass

        # Pass the brahma version via cmake args if we determined it
        if _brahma_mpi_ver > 0:
            _existing = pip_env.get("DFTRACER_CMAKE_ARGS", "")
            _new_args = f"-DBRAHMA_MPI_VERSION={_brahma_mpi_ver}"
            pip_env["DFTRACER_CMAKE_ARGS"] = (
                (_existing + " " + _new_args).strip() if _existing else _new_args
            )

    # cmake 4.x removed compatibility with cmake_minimum_required < 3.5.
    # The gotcha dependency (fetched transitively by brahma) ships an old
    # CMakeLists.txt that triggers this.  Setting CMAKE_POLICY_VERSION_MINIMUM
    # as an env var propagates through all cmake ExternalProject sub-invocations
    # (subprocesses inherit it) so gotcha configures successfully under cmake 4.x.
    pip_env.setdefault("CMAKE_POLICY_VERSION_MINIMUM", "3.5")

    # Pin CMake's FindPython3 to the exact interpreter `py` resolves to.
    # VIRTUAL_ENV/PATH above make discovery correct in the common case, but
    # CMake's Python3_FIND_VIRTUALENV heuristic isn't guaranteed on every
    # cmake version -- passing -DPython3_EXECUTABLE/-DPython3_ROOT_DIR is the
    # authoritative override so CMAKE_INSTALL_PREFIX can never resolve back to
    # a system Python's (read-only) site-packages. See the VIRTUAL_ENV/PATH
    # comment near the top of this function for the failure this prevents.
    _existing_cmake_args = pip_env.get("DFTRACER_CMAKE_ARGS", "")
    pip_env["DFTRACER_CMAKE_ARGS"] = (
        f"-DPython3_EXECUTABLE={py} -DPython3_ROOT_DIR={_venv_root} "
        + _existing_cmake_args
    ).strip()

    # Tuolumne (Cray PE / lld): linking any binary/shared-lib that references
    # dlopen (e.g. dftracer_core's test_cpp, dftracer_service) fails with
    # "ld.lld: error: undefined reference: dlopen (disallowed by
    # --no-allow-shlib-undefined)" unless -ldl is explicitly passed at link
    # time -- LD_LIBRARY_PATH alone (already set above) only helps runtime
    # resolution, not the link-time symbol check. See resources/systems.yaml
    # Tuolumne notes; root-caused again 2026-07-20 on a pecan_milan session
    # building dftracer_service specifically.
    pip_env.setdefault("LDFLAGS", "-ldl")

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
                # NOTE: dftracer's actual GIT_TAG for brahma is "v1.0.10", not
                # "v1.0.7" -- the v1.0.7 string below never matched, so this
                # redirect was silently a no-op. Kept for compatibility with
                # any local checkout tagged v1.0.7-fix, but the real target is
                # GIT_TAG v1.0.10 (see the second replace below).
                if brahma_local_url:
                    dc = dc.replace(
                        "https://github.com/hariharan-devarajan/brahma.git v1.0.7",
                        f"{brahma_local_url} v1.0.7-fix",
                    )
                # Locally-patched brahma checkout that fixes the *_async HDF5
                # wrapper signature bug (H5_DOXYGEN codegen mismatch -- see
                # $HOME/dftracer-project/brahma commit
                # 8d2775c "Fix HDF5 *_async wrapper signatures"). Redirect
                # dftracer's actual GIT_REPOSITORY/GIT_TAG (v1.0.10) to this
                # checkout when it exists, so every session picks up the fix
                # until it lands upstream.
                # Re-enabled (2026-07-10): confirmed the h5bench shim fix
                # ALONE (with stock unpatched brahma v1.0.10) does NOT
                # resolve the HDF5-DIAG errors -- identical error cascade
                # reproduced. Both fixes are required together: the shim
                # fix ensures h5bench calls the REAL HDF5 *_async symbols,
                # and this local brahma patch ensures those real calls are
                # intercepted with the correct (non-corrupting) signature.
                _USE_LOCAL_BRAHMA_FIX = True
                _local_brahma_fix_dir = _Path(
                    "$HOME/dftracer-project/brahma"
                )
                if _USE_LOCAL_BRAHMA_FIX and _local_brahma_fix_dir.is_dir():
                    dc = dc.replace(
                        "GIT_REPOSITORY https://github.com/hariharan-devarajan/brahma.git\n"
                        "    GIT_TAG v1.0.10",
                        f"GIT_REPOSITORY file://{_local_brahma_fix_dir}\n"
                        "    GIT_TAG bugfix/hdf5_async",
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
                # NOTE: unlike MPI, HDF5 does NOT need a manually-injected
                # BRAHMA_HDF5_VERSION flag -- dftracer's own dependency/CMakeLists.txt
                # (github.com/llnl/dftracer, develop, dependency/CMakeLists.txt)
                # already computes `_dftracer_dep_hdf5_version` internally via
                # find_package(HDF5) using the identical major*100000+minor*100+patch
                # formula (confirmed: 1.14.5 -> 101405) and forwards HDF5_ROOT /
                # CMAKE_PREFIX_PATH to that find_package call itself. The ONLY
                # requirement is that HDF5_ROOT/CMAKE_PREFIX_PATH (set above from
                # hdf5_system.prefix) actually reach dftracer's own top-level cmake
                # configure step. Build isolation stays ON (pip's default) -- do
                # NOT pass --no-build-isolation, it conflicts with the parent
                # project's own venv/site-packages by exposing them to the build.
                # PEP 517 isolation only isolates the installed *package* set; it
                # still inherits the env dict passed via `env=pip_env` below, which
                # already carries HDF5_ROOT/CMAKE_PREFIX_PATH/DFTRACER_CMAKE_ARGS
                # explicitly. Do not text-patch dependency/CMakeLists.txt for
                # HDF5; that duplicates logic dftracer already has and can drift
                # out of sync with upstream's actual variable names.
                dep_cmake.write_text(dc)

            r = _run_pip_via_module_script(
                py,
                ["install", "-v", "--no-cache-dir", "--upgrade", str(clone_dir)],
                pip_env,
                _install_modules,
                ws=ws,
                timeout=1800,
                env_script=_install_env_script,
            )
            _shutil2.rmtree(str(clone_dir), ignore_errors=True)
        else:
            # Clone failed — fall back to direct git+ install (MPI_Errhandler issue
            # will cause build failure if using OPENMPI 4.1.x, but nothing we can do)
            r = _run_pip_via_module_script(
                py,
                ["install", "-v", "--no-cache-dir", "--upgrade",
                 f"git+https://github.com/llnl/dftracer.git@{dftracer_ref}"],
                pip_env,
                _install_modules,
                ws=ws,
                timeout=900,
                env_script=_install_env_script,
            )
    else:
        r = _run_pip_via_module_script(
            py,
            ["install", "-v", "--no-cache-dir", "--upgrade",
             f"git+https://github.com/llnl/dftracer.git@{dftracer_ref}"],
            pip_env,
            _install_modules,
            ws=ws,
            timeout=900,
            env_script=_install_env_script,
        )
    if ws is not None:
        _write_artifact_log(ws, 6, "session_install_dftracer", {
            "python_exe": py,
            "dftracer_ref": dftracer_ref,
            "pip_env": str(pip_env),
            "pip_install": r,
        }, run_id)
    return {"success": r["success"], "steps": {"pip_install": r}, "pip_env": pip_env}


