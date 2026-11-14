"""dftracer_install: install dftracer into a session (the pre-release with dftracer-modules, then dftracer-compose
links only the layers the session asked for against the application's own MPI/HDF5/ROCm; or a pip/source build),
search or retire prebuilt wheels, install dftracer-utils, generate a dftracer.pc for autotools projects, and rule
deterministically on the result (dftracer_install_check).

Tools moved unchanged from dftracer-agents (session_tools.register_session_tools and
dftracer_service.register_install_session_tools).
"""
from __future__ import annotations

from pathlib import Path
import json
import sys
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.detection import (  # moved tools use these helpers unchanged
    _HDF5_COMPATIBLE_SERIES,
    _MPI_COMPATIBLE_DISPLAY,
    _detect_info,
    _detect_system_hdf5,
    _detect_system_mpi,
    _hdf5_version_compatible,
)
from dftracer_agents.mcp_tools.tools.session.install import (  # moved tools use these helpers unchanged
    _ensure_session_venv,
    _find_dftracer_dirs,
    _install_dftracer_pip_direct,
    _install_dftracer_utils,
)
from dftracer_agents.mcp_tools.tools.session.session_tools import (  # moved tools use these helpers unchanged
    _trim_steps,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _err,
    _load_state,
    _ok,
    _save_state,
    _ws,
)
import json
import subprocess
from pathlib import Path
import tempfile

from dftracer_agents.mcp_tools.tools.session.workspace import _ws as _cap_ws

_LAYERS = ("mpi", "hdf5", "papi", "hip", "hwloc", "cuda")


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _site_packages(ws: Path) -> list:
    return sorted(p for p in (ws / "venv").glob("lib*/python*/site-packages") if p.is_dir())


def _install_check(ws: Path, require: str = "", run_import: bool = False) -> dict:
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}", "pass the run_id session_create returned")
        return _verdict(checks, workspace=str(ws))
    try:
        state = json.loads((ws / "session.json").read_text())
    except (OSError, ValueError):
        state = {}
    build = state.get("dftracer_build") or {}
    lib_dir = state.get("dftracer_pip_lib_dir") or ""
    _add(checks, "install_recorded", bool(state.get("dftracer_install_prefix")) and bool(lib_dir),
         f"mode={build.get('mode')} version={build.get('version')} lib_dir={lib_dir or '-'}",
         "run session_install_dftracer(run_id) (after the app built: session_build_install) and read its error")
    if state.get("dftracer_install_prefix") and not state.get("app_built"):
        _add(checks, "app_built_first", False, "dftracer is installed but session.json has no app_built",
             "build the ORIGINAL app first (session_build_install) so dftracer composes against the MPI/HDF5 it resolved")
    core = sorted(p.name for p in Path(lib_dir).glob("libdftracer_core*.so*")) if lib_dir and Path(lib_dir).is_dir() else []
    _add(checks, "core_library", bool(core), f"{lib_dir}: {core[:3]}",
         "the venv has no libdftracer_core: reinstall with session_install_dftracer (pip --pre dftracer[modules] dftracer-modules)")
    sps = _site_packages(ws)
    if sps:
        copies = sorted(str(p.relative_to(sps[0])) for p in sps[0].rglob("libdftracer_core*.so*")
                        if p.is_file() and not p.is_symlink() and "_dbg" not in p.name and "compose" not in p.parts)
        _add(checks, "one_core", len(copies) == 1, f"{len(copies)} real libdftracer_core file(s): {copies[:4]}",
             "two cores means a dftracer older than the pre-release (dftracer.libs copy) or an in-place source build over a "
             "wheel: reinstall the pre-release fresh into the SESSION venv and compose; never patch the shared venv")
    inc = state.get("dftracer_pip_include_dir") or ""
    hdr = Path(inc) / "dftracer" / "dftracer.h" if inc else None
    _add(checks, "header", bool(hdr and hdr.is_file()), str(hdr) if hdr else "no include dir recorded",
         "dftracer/dftracer.h must be in the venv's include dir; reinstall dftracer (not dftracer-utils first: RULE 3 order)")
    want = [m for m in _LAYERS if m in {x.strip() for x in require.split(",") if x.strip()}] or \
        [m for m in _LAYERS if build.get(m)]
    comp = []
    for sp in sps:
        comp = sorted(sp.glob("dftracer/lib*/dftracer/composition.txt"))
        if comp:
            break
    if build.get("mode") == "compose" or want or require:
        status = {}
        if comp:
            for ln in comp[0].read_text().splitlines():
                parts = ln.split()
                if len(parts) == 3:
                    status[parts[0]] = parts[2]
        _add(checks, "composed", bool(comp), f"linked: {sorted(m for m, s in status.items() if s == 'linked')}" if comp
             else "no dftracer/lib*/dftracer/composition.txt in the venv",
             "re-run scripts/compose_dftracer.sh (dftracer-compose --enable <layers>) in the session env.sh, cmake>=3.24 first on PATH")
        for m in want:
            s = status.get(m, "missing")
            _add(checks, f"linked_{m}", s == "linked", f"{m}: {s}",
                 f"{m} is '{s}': load the module the APPLICATION uses for {m} in scripts/env.sh and re-run "
                 "scripts/compose_dftracer.sh; 'absent' means this machine has no such library (ask whether to drop the layer)")
    pc = state.get("dftracer_pc_file")
    if pc:
        txt = Path(pc).read_text() if Path(pc).is_file() else ""
        _add(checks, "pkgconfig", "-ldftracer_core" in txt, f"{pc}", "re-run session_generate_dftracer_pc(run_id)")
    if run_import:
        py = ws / "venv" / "bin" / "python"
        r = subprocess.run([str(py), "-c", "import dftracer.dftracer"], capture_output=True, text=True, timeout=60) \
            if py.exists() else None
        _add(checks, "imports", bool(r) and r.returncode == 0,
             (r.stderr.strip()[-300:] or "import dftracer.dftracer ok") if r else f"no {py}",
             "an ImportError here is swallowed by pydftracer at run time (empty trace): a GLIBCXX error needs a newer "
             "libstdc++ preload or fewer composed layers; a wrong python means install env != run env (RULE 0)")
    return _verdict(checks, workspace=str(ws), asked=want)


def register(mcp):
    """Register the dftracer_install tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

    @mcp.tool()
    def session_install_dftracer(
        run_id: str,
        dftracer_ref: str = "develop",
        jobs: int = 4,
        venv_path: str = "",
        hdf5: Optional[bool] = None,
        dftracer_repo: str = "",
        papi: Optional[bool] = None,
        hip: Optional[bool] = None,
        rocm_path: str = "",
        variorum: Optional[bool] = None,
        variorum_build: str = "",
        mpi: Optional[bool] = None,
        pip_index_url: str = "https://pypi.org/simple",
        app_built_override: bool = False,
        prebuilt: str = "compose",
    ) -> str:
        """Install dftracer via pip for all project types, then locate dirs in site-packages.

        COMPOSE (``prebuilt="compose"``, the default): install the dftracer PRERELEASE with ``dftracer-modules`` (its
        optional modules, prebuilt for many MPI, HDF5, PAPI, hwloc, ROCm and CUDA versions) from the package index -
        binary wheels only, seconds - then run ``dftracer-compose`` inside the session's ``env.sh`` (the application's
        modules loaded) to link the modules detection asks for (``mpi``, ``hdf5``, ``papi``, ``hip``, ``hwloc``; the
        arguments override detection) and leave the others out. A module this machine needs whose variant is not
        prebuilt is built from the sources the wheel ships, by dftracer-compose itself, so the result has the same
        features a source build would. The result's ``compose`` says what was linked, what the machine does not have
        (``absent``) and what failed to build (``missing``: the tool fails then); variorum needs no compose (the
        service loads Variorum 0.8 when it starts). ``scripts/compose_dftracer.sh`` re-runs the step.

        PREBUILT WHEELS (``prebuilt``): dftracer builds from source take minutes, so every successful build is packed
        into a relocatable wheel and indexed (SQLite) by its exact combination - system, Python, MPI, HDF5,
        compilers/modules, feature flags, dftracer repo/ref/commit and source patches - in ``$DFTRACER_WHEELHOUSE``.
        ``auto`` (default): install the matching wheel when one exists, else build and store it; ``refresh``: build
        fresh and replace the stored wheel; ``off``: build, store nothing. The result's ``wheelhouse.message`` says
        which happened - tell the user. Search the index with ``dftracer_wheelhouse``.

        DEMO MODE (``prebuilt="prerelease"``): install the ALREADY-COMPILED dftracer prerelease wheel from the package
        index (``pip install --pre --only-binary=:all: dftracer``) - takes seconds and compiles NOTHING (no sdist, no
        ``git+``, no wheelhouse build, no cmake). The public wheels are built WITHOUT MPI, HDF5, PAPI, HIP and variorum,
        so those are forced off (``mpi``/``hdf5``/``papi``/``hip``/``variorum`` arguments are ignored) and recorded in
        session state (``dftracer_build``), so ``session_build_annotated`` links only the plain core; POSIX/STDIO and
        annotation tracing work. ``dftracer_repo`` is ignored; ``dftracer_ref`` pins a version only when it looks like
        one (e.g. ``"2.2.0.post39.dev0"``), otherwise the newest prerelease is used. When no binary wheel matches this
        Python/platform it fails fast (status error, what was tried, what the index offers) and never falls back to
        compiling. The result's ``prerelease.message`` says this is the prebuilt prerelease without MPI/HDF5 tracing -
        tell the user. Include/lib dirs are discovered and persisted exactly as in the normal path.

        Installs ``pip install git+<dftracer_repo>@<ref>`` (defaulting to
        ``https://github.com/llnl-asr/dftracer.git``, ref
        ``develop``) regardless of whether the
        project is C/C++ or Python.  Feature flags detected from the application
        source are forwarded as environment variables so the dftracer wheel's C
        extension is built with the correct support compiled in:

        * ``DFTRACER_ENABLE_MPI=ON``   — when MPI is detected in the project
        * ``DFTRACER_ENABLE_HDF5=ON``  — when HDF5 is detected in the project
        * ``HDF5_ROOT=<prefix>``       — when the system HDF5 prefix is known
        * ``HDF5_DIR=<prefix>``        — same as ``HDF5_ROOT`` for cmake backends
        * ``DFTRACER_ENABLE_PAPI_TRACING=ON`` — when ``papi=True``

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
            venv_path: Directory to create/reuse as the install venv, relative
                to the session workspace root or absolute. A venv is ALWAYS
                required — dftracer is never installed into the MCP server's
                own interpreter or any interpreter outside the session
                workspace. Defaults to ``ws/install`` for Python (ML/AI)
                projects (shared with the app, so ``import dftracer`` resolves
                without path surgery) or ``ws/venv`` for C/C++ projects. Pass
                this explicitly when a later step (e.g. ``dftracer-build-app``)
                already created the app's venv at a non-default path — dftracer
                MUST land in that same venv, never a second parallel one.
            hdf5: Explicit override for HDF5 support, bypassing auto-detection.
                Defaults to ``None``, which preserves the existing behavior of
                trusting whatever ``session_detect`` finds on the system (auto
                re-detected fresh on every call). Pass ``False`` to force
                ``DFTRACER_ENABLE_HDF5=OFF`` and skip HDF5 auto-detection
                entirely — e.g. for a pure-Python/no-HDF5 app where the system
                happens to have an HDF5 module that would otherwise be
                auto-enabled. Pass ``True`` to force HDF5 on even if detection
                did not find it in the app source (still requires a usable
                system HDF5 to build against).
            dftracer_repo: Git URL to install dftracer FROM. Defaults to empty,
                which means LC's GitLab over SSH
                (``https://github.com/llnl-asr/dftracer.git``).
                That is the canonical readable remote: the GitHub copy is
                PRIVATE and anonymous HTTPS fails from every LC machine. GitLab
                is reachable only from inside the LC network. ``dftracer_ref`` is
                resolved against THIS repo, so a ref that exists only on the
                GitLab remote requires setting both. Pass ``"pypi-pre"`` to
                build from the PyPI PRERELEASE source distribution (or
                ``"pypi"`` for the latest release sdist) instead of any git
                remote; the sdist is extracted to ``<WS>/tmp/dftracer_sdist``
                and the same feature env vars (MPI etc.) are applied before
                ``pip install``.
            app_built_override: Skip the app-built-first gate. Only for apps
                built outside ``session_build_install`` (hand-written build
                configs); the app must still already be built.
            pip_index_url: The single pip index used for the dftracer build and
                its build dependencies (``PIP_INDEX_URL``; extra indexes from
                user pip config are cleared, since an unresolvable extra index
                makes every build dep retry 5x and the install hangs). Pass
                ``""`` to keep the user's pip config unchanged.
            papi: Explicit override for PAPI hardware-counter tracing
                (``DFTRACER_ENABLE_PAPI_TRACING``). Defaults to ``None``
                (leave off). Pass ``True`` to compile PAPI counter sampling in;
                this requires a PAPI development install and is only present on
                dftracer builds whose source actually has the PAPI support (the
                ``feature/papi-counter-tracing`` branch and its successors) —
                on a build without it the flag is silently ignored by cmake, so
                ALWAYS verify ``DFTRACER_PAPI_TRACING_ENABLE`` in the installed
                ``dftracer_config.hpp`` rather than trusting a green install.
            variorum: Enable Variorum node-level POWER tracing
                (``DFTRACER_ENABLE_VARIORUM``). Defaults to ``None``, which
                means "ask the node": ``system_service.probe_power`` looks for a
                power domain variorum can be built against (rocm_smi for GPU
                package power, RAPL/MSR for CPU, Cray pm_counters) and turns
                power collection ON when one exists, since it is sampled in a
                separate service process and costs the application nothing.
                Pass ``False`` to force it off, ``True`` to force it on where
                the probe found nothing.
                Power is node-wide, so it is sampled by ``dftracer_service`` and
                appears in the per-node service traces as ``type 13`` counter
                records with the power family (``cpu``/``gpu``/``memory``/
                ``network``/``node``) as ``cat`` -- NOT in the per-rank app
                traces. Which domains are compiled in is decided from the build
                machine: the AMD GPU domain needs ``librocm_smi64`` reachable via
                ``ROCM_PATH``, the AMD CPU domain needs E-SMI. A domain whose
                vendor library is absent is silently omitted, so verify the
                build log line ``variorum power domains to build: ...``.
            variorum_build: When to build variorum from source --
                ``AUTO`` (reuse an installed one), ``ALWAYS``, or ``NEVER``.
                Defaults to ``ALWAYS``, because a distribution variorum is
                usually built for none of an accelerator node's power domains
                and then fails at run time with
                ``_ERROR_VARIORUM_UNSUPPORTED_PLATFORM``, reporting no power at
                all with no build-time warning.
            mpi: Explicit override for MPI tracing (``DFTRACER_ENABLE_MPI``),
                bypassing auto-detection. Defaults to ``None``, which auto-
                disables MPI tracing when ``session_detect``'s MPI compat
                check reports ``compatible: False`` (e.g. mvapich2 2.3.7's
                brahma_int is not in dftracer's supported range and fails
                to compile brahma's MPI interception wrapper with
                ``error: member function declared with "override" does not
                override a base class member``) instead of building anyway
                and failing. Pass ``True`` to force MPI tracing on despite
                an incompatible version (not recommended — it will not
                build), or ``False`` to explicitly skip it regardless of
                compatibility (e.g. a POSIX-only run that has no use for
                MPI-IO interception).
            hip: Explicit override for HIP/GPU tracing
                (``DFTRACER_ENABLE_HIP_TRACING``), bypassing auto-detection.
                Defaults to ``None`` (trust detection). Detection only looks for
                HIP in the APP's own source, which produces a false negative for
                apps that reach the GPU through a library abstraction
                (MFEM's ``MFEM_FORALL``, RAJA, Kokkos): the app source has no
                ``hip`` token but the run genuinely launches HIP kernels worth
                tracing. Pass ``True`` for those. Pass ``False`` to force it off
                on a GPU-capable system where the app is CPU-only (the inverse
                false positive).
            rocm_path: Explicit ROCm install prefix to build HIP tracing
                against, overriding the auto-detected one. Detection resolves
                ROCm from ``module avail``, which returns the NEWEST available
                version -- not necessarily the one the application was compiled
                with. dftracer's HIP tracing must be built against the SAME
                ROCm/rocprofiler-sdk the app uses, or tracing silently collects
                nothing (or crashes on a version-skewed rocprofiler ABI). Set
                this whenever the app pins a specific ROCm.

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
                * ``prerelease`` — only for ``prebuilt="prerelease"``: ``{version, wheel, tags, pip_cmd, seconds,
                  compiled: false, excluded_features, message}``.
        """
        _prebuilt_mode = (prebuilt or "compose").strip().lower()
        if _prebuilt_mode not in ("compose", "auto", "refresh", "off", "prerelease"):
            return _err(f"prebuilt must be one of compose, auto, refresh, off, prerelease (got {prebuilt!r})")
        _prerelease = _prebuilt_mode == "prerelease"
        _compose = _prebuilt_mode == "compose"
        if _prerelease:
            # The public prerelease wheels are compiled without these; forcing them
            # off here makes every feature block below (detection overrides, power
            # probe, compat checks, features_enabled) agree with what is installed.
            mpi = hdf5 = papi = hip = variorum = False
            variorum_build = ""
        ws = _ws(run_id)
        state = _load_state(run_id)
        # ORDER GATE: the ORIGINAL app must be built first. dftracer has to be
        # compiled against the dependency stack the app build actually resolved
        # (compiler, MPI, HDF5/ROCm versions, venv); installing it first bakes
        # in guessed dependencies that the app build later contradicts.
        if not (state.get("app_built") or app_built_override):
            return json.dumps({
                "status": "error",
                "message": (
                    "Build the original app first (session_build_install), then "
                    "install dftracer against the dependencies that build used. "
                    "If the app was built outside session_build_install (e.g. a "
                    "hand-written Make.mach), pass app_built_override=True."
                ),
            }, indent=2)
        # ALWAYS re-run detection fresh before installing, rather than trusting
        # a possibly-stale stored `detection` block. HDF5/MPI may have been
        # rebuilt (new version, moved prefix) since the last session_detect
        # call, and installing dftracer against a stale detection silently
        # rebuilds against the WRONG library -- the exact class of bug behind
        # today's HDF5-async and MPI-IO-interception incidents. Detection is
        # cheap; a wrong install is expensive to diagnose after the fact.
        #
        # Preserve any explicit hdf5_prefix/mpicc/mpicxx pins from the PRIOR
        # detection so this refresh doesn't regress to auto-probing /usr --
        # a prior session_detect call may have been given explicit overrides
        # (e.g. a source-built HDF5 in the workspace) that must carry forward.
        _prev = state.get("detection") or {}
        _prev_hdf5_prefix = ((_prev.get("features") or {}).get("hdf5_system") or {}).get("prefix")
        _prev_mpicc = (_prev.get("mpi_impl") or {}).get("mpicc")
        _prev_mpicxx = (_prev.get("mpi_impl") or {}).get("mpicxx")
        info = _detect_info(
            ws / "source",
            hdf5_prefix=_prev_hdf5_prefix,
            mpicc=_prev_mpicc,
            mpicxx=_prev_mpicxx,
        )
        _save_state(run_id, {"detection": info})
        features = info.get("features", {})
        bt = info.get("build_tool", state.get("build_tool", "unknown"))

        # Explicit hdf5 override (caller-specified) short-circuits
        # auto-detection instead of being re-discovered/overridden by it —
        # e.g. a pure-Python app with no HDF5 usage on a system where HDF5
        # happens to be present/auto-detected. `hdf5=None` (default)
        # preserves exact prior auto-detect behavior.
        if hdf5 is not None:
            features = dict(features)
            features["hdf5"] = bool(hdf5)
            if not hdf5:
                features["hdf5_system"] = {}

        hdf5_override_applied = hdf5 is not None

        # PAPI is never auto-detected from app source: unlike MPI/HDF5/HIP it is
        # not something the application itself calls, it is a tracing backend the
        # OPERATOR asks for. So it is opt-in via this argument only.
        if papi is not None:
            features = dict(features)
            features["papi"] = bool(papi)

        # Variorum node power. Not detectable from app source -- it is node-level
        # telemetry collected by dftracer_service rather than by the traced
        # process -- but it IS detectable from the node: system_detect's power
        # probe reports whether a domain exists that variorum can be built
        # against. Since it costs the application nothing (separate sampler,
        # separate process), default it ON wherever the node can measure power,
        # instead of leaving free data on the floor. An explicit argument still
        # wins, in either direction.
        if variorum is not None:
            features = dict(features)
            features["variorum"] = bool(variorum)
        else:
            try:
                from dftracer_agents.mcp_tools.tools.system.system_service import probe_power
                _power = probe_power()
            except Exception:
                _power = {"can_enable": False}
            if _power.get("can_enable"):
                features = dict(features)
                features["variorum"] = True
                features.setdefault("variorum_source", "system power probe")
                # The app-matched ROCm (resolved above) wins over the probe's
                # newest-on-the-node guess: two rocm_smi ABIs in one process is
                # a heap corruption, not a warning.
                if not (features.get("rocm") or {}).get("path"):
                    _probe_rocm = (_power.get("features") or {}).get("rocm_path_for_variorum")
                    if _probe_rocm:
                        features["rocm"] = {**(features.get("rocm") or {}),
                                            "found": True,
                                            "path": _probe_rocm,
                                            "source": "system power probe"}
        if variorum_build:
            features = dict(features)
            features["variorum_build"] = variorum_build

        # HIP override: detection greps the app's own source for HIP, so it
        # false-negatives on apps that reach the GPU only through MFEM/RAJA/
        # Kokkos abstractions. Honour the caller instead of re-deriving.
        if hip is not None:
            features = dict(features)
            features["hip"] = bool(hip)

        # ROCm prefix override: detection takes the newest module available,
        # which is frequently NOT the ROCm the app was built against. HIP
        # tracing must match the app's rocprofiler-sdk exactly.
        if rocm_path:
            features = dict(features)
            _rocm_info = dict(features.get("rocm") or {})
            _rocm_info.update({
                "found": True,
                "path": rocm_path,
                "source": "caller override",
            })
            features["rocm"] = _rocm_info

        features_enabled = []
        compat_warnings: list = []

        # Explicit mpi override (caller-specified) short-circuits
        # auto-detection, same pattern as hdf5/hip above. When left as the
        # default (None), auto-disable MPI tracing if session_detect's
        # compat probe already flagged this MPI as unsupported -- building
        # anyway fails deterministically rather than just degrading, e.g.
        # mvapich2 2.3.7 on corona: brahma's MPI wrapper class declares
        # `override` methods against signatures that don't match this
        # version's actual mpi.h, producing dozens of "does not override a
        # base class member" compile errors and failing the whole wheel
        # build (verified 2026-09-11, run ior/20260911_042538).
        if mpi is not None:
            features = dict(features)
            features["mpi"] = bool(mpi)
        else:
            _mpi_info_pre = info.get("mpi_impl") or {}
            if features.get("mpi") and _mpi_info_pre.get("found") and not _mpi_info_pre.get("compatible", True):
                features = dict(features)
                features["mpi"] = False
                compat_warnings.append(
                    f"MPI tracing AUTO-DISABLED: detected "
                    f"{_mpi_info_pre.get('impl_display', _mpi_info_pre.get('impl', 'unknown'))} "
                    f"{_mpi_info_pre.get('version', 'unknown')} is not in a dftracer-compatible "
                    f"range and fails to compile brahma's MPI interception wrapper. "
                    f"Pass mpi=True to force it on anyway (it will not build), or mpi=False "
                    f"to silence this note."
                )

        # --- MPI version check ---
        if features.get("mpi"):
            features_enabled.append("mpi")
            # Prefer the already-computed mpi_impl from session_detect (session
            # state) over a fresh system probe -- session_detect is the single
            # source of truth (it may have been given explicit mpicc/mpicxx
            # overrides pinning the exact MPI build to use); re-probing here
            # can disagree with it (e.g. misclassify craympich as generic
            # mpich) and produce a false compatibility warning even when the
            # session is already using a fully supported version.
            mpi_info = info.get("mpi_impl") or {}
            if not mpi_info.get("found"):
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
                        f"Report unsupported versions at https://github.com/llnl-asr/dftracer/issues"
                    )
            else:
                compat_warnings.append(
                    "MPI detected in source but MPI runtime could not be probed — "
                    "MPI-IO tracing may not work."
                )

        # --- HDF5 version check ---
        # (features["hdf5"] is already forced False above when the caller
        # passed hdf5=False, so this naturally skips probing/reporting on a
        # feature that was just hard-disabled.)
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
        # PAPI was missing from this list even when DFTRACER_ENABLE_PAPI_TRACING
        # was forwarded and the build genuinely compiled it in -- the returned
        # `features=[...]` message read as "PAPI is off" while the installed
        # dftracer_config.hpp said DFTRACER_PAPI_TRACING_ENABLE 1.  Reporting
        # only; the flag itself was always passed through correctly.
        if features.get("papi"):
            features_enabled.append("papi")
        # Same omission as papi above: variorum compiled in (DFTRACER_VARIORUM_ENABLE 1
        # in the installed dftracer_config.hpp, libvariorum.so linked into the service)
        # while the returned `features=[...]` message read as though it were off.
        if features.get("variorum"):
            features_enabled.append("variorum")
        if features.get("hwloc"):
            features_enabled.append("hwloc")

        # A venv is ALWAYS required — dftracer is never installed into the MCP
        # server's own interpreter or any interpreter outside the session
        # workspace. `venv_path` makes that explicit in the tool's signature
        # so callers can't accidentally omit it; when unset, resolve the
        # historical default per project type:
        #
        # For Python (ML/AI) projects, dftracer and the app MUST share the same
        # venv (ws/install/ by default) so `import dftracer` resolves at
        # runtime without any path surgery. Never create a parallel ws/venv/
        # for Python apps — it causes import errors when the app venv is
        # active but dftracer lives in a different site-packages.
        #
        # For C/C++ projects, create an isolated session venv (ws/venv/ by
        # default) that is separate from the MCP server's own Python
        # environment.
        if venv_path:
            _venv_dir = Path(venv_path)
            if not _venv_dir.is_absolute():
                _venv_dir = ws / _venv_dir
        elif bt == "python":
            _venv_dir = ws / "install"
        else:
            _venv_dir = ws / "venv"

        app_venv_python = _venv_dir / "bin" / "python"
        if not app_venv_python.exists():
            try:
                import subprocess as _sp
                _sp.run(
                    [sys.executable, "-m", "venv", str(_venv_dir)],
                    check=True, timeout=60,
                )
            except Exception as exc:
                return _err(f"Failed to create install venv at {_venv_dir}: {exc}")
        venv_python_str = str(app_venv_python)

        # Validate: `venv_python_str` MUST be the interpreter PATH inside
        # `_venv_dir` (a real venv, with its own pyvenv.cfg) — never silently
        # fall through to a bare system/module python. This is the guard for
        # the 2026-07-20 pecan_milan incident where dftracer's cmake configure
        # step picked up the system Anaconda python instead of the session
        # venv (see the VIRTUAL_ENV/PATH fix in _install_dftracer_pip_direct);
        # catching a bad interpreter HERE, before any pip/cmake subprocess
        # runs, turns that failure mode into an immediate, clear error.
        #
        # IMPORTANT: do NOT `.resolve()` the python path before checking
        # containment — `<venv>/bin/python` is BY DESIGN a symlink out to the
        # base interpreter (that's how venvs work), so resolving it always
        # points outside the venv dir. Check containment on the unresolved
        # `bin/python` path instead; only resolve `_venv_dir` itself (to
        # normalize `..`/relative segments), not the interpreter symlink.
        _resolved_venv_dir = _venv_dir.resolve()
        _pyvenv_cfg = _resolved_venv_dir / "pyvenv.cfg"
        if not _pyvenv_cfg.exists():
            return _err(
                f"Install venv validation failed: {_pyvenv_cfg} does not exist — "
                f"{_venv_dir} is not a real venv. Refusing to install dftracer "
                f"outside a session-local venv."
            )
        try:
            Path(venv_python_str).absolute().relative_to(_resolved_venv_dir)
        except ValueError:
            return _err(
                f"Install venv validation failed: {venv_python_str} is NOT a "
                f"path inside the install venv {_resolved_venv_dir}. Refusing "
                f"to install dftracer into a system/module interpreter."
            )
        _save_state(run_id, {"session_venv_python": venv_python_str, "session_venv_dir": str(_venv_dir)})

        # Reconcile detection's PRE-COMPUTED pip env with the caller's explicit
        # overrides. This is required, not cosmetic: _install_dftracer_pip_direct
        # merges `dftracer_pip_env` with setdefault() BEFORE it applies the
        # per-feature blocks, so a detection-supplied "DFTRACER_ENABLE_HDF5=ON"
        # wins and the later `setdefault(..., "OFF")` is a silent no-op. Without
        # this, hdf5=False did not actually disable HDF5 -- and the same value is
        # ALSO embedded in the DFTRACER_CMAKE_ARGS string, which is passed
        # straight through to cmake and would override the env var regardless.
        _pip_env_in = dict(info.get("dftracer_pip_env") or {})

        def _force_flag(env_key: str, cmake_key: str, on: bool) -> None:
            env_val = "ON" if on else "OFF"
            _pip_env_in[env_key] = env_val
            args = _pip_env_in.get("DFTRACER_CMAKE_ARGS", "")
            if args:
                kept = [
                    tok for tok in args.split()
                    if not tok.startswith(f"-D{cmake_key}=")
                ]
                kept.append(f"-D{cmake_key}={env_val}")
                _pip_env_in["DFTRACER_CMAKE_ARGS"] = " ".join(kept)

        if hdf5 is not None:
            _force_flag("DFTRACER_ENABLE_HDF5", "DFTRACER_ENABLE_HDF5", bool(hdf5))
            if not hdf5:
                # Drop the HDF5 prefix hints too, so cmake's find_package(HDF5)
                # cannot re-enable it off a system copy the app never uses.
                for _hk in ("HDF5_ROOT", "HDF5_DIR"):
                    _pip_env_in.pop(_hk, None)
                _args = _pip_env_in.get("DFTRACER_CMAKE_ARGS", "")
                if _args:
                    _pip_env_in["DFTRACER_CMAKE_ARGS"] = " ".join(
                        tok for tok in _args.split()
                        if not tok.startswith(("-DHDF5_ROOT=", "-DHDF5_PREFER_PARALLEL="))
                    )
        if papi is not None:
            _force_flag(
                "DFTRACER_ENABLE_PAPI_TRACING", "DFTRACER_ENABLE_PAPI_TRACING", bool(papi)
            )
        if hip is not None:
            _force_flag(
                "DFTRACER_ENABLE_HIP_TRACING", "DFTRACER_ENABLE_HIP_TRACING", bool(hip)
            )
        if rocm_path:
            _pip_env_in["ROCM_PATH"] = rocm_path
            _pip_env_in["HIP_PATH"] = rocm_path
        if variorum is not None:
            _force_flag(
                "DFTRACER_ENABLE_VARIORUM", "DFTRACER_ENABLE_VARIORUM", bool(variorum)
            )
            if variorum:
                _pip_env_in["DFTRACER_BUILD_VARIORUM"] = (
                    variorum_build or "ALWAYS"
                ).upper()

        _compose_info: Dict[str, Any] = {}
        if _compose:
            # The prerelease with its prebuilt modules, then dftracer-compose links what the session asked for.
            from dftracer_agents.mcp_tools.tools.session.install import _install_dftracer_prerelease_wheel, _compose_dftracer, _ensure_session_env_script
            result = _install_dftracer_prerelease_wheel(
                python_exe=venv_python_str,
                ws=ws,
                run_id=run_id,
                pip_index_url=pip_index_url,
                dftracer_ref=dftracer_ref,
                with_modules=True,
            )
            if not result["success"]:
                return _err(
                    "dftracer prerelease (with dftracer-modules) install failed: " + result.get("error", ""),
                    prerelease=result.get("prerelease"),
                    venv=str(Path(venv_python_str).parent.parent),
                    steps=_trim_steps(result["steps"]),
                    full_log="artifacts/06_dftracer_prerelease_probe.log, artifacts/06_session_install_dftracer.log",
                )
            _want = [m for m in ("mpi", "hdf5", "papi", "hip", "hwloc", "cuda") if features.get(m)]
            _compose_info = _compose_dftracer(
                venv_python_str, ws, _want, jobs=jobs,
                env_script=_ensure_session_env_script(ws, ws / "source"),
            )
            result["steps"]["compose"] = {k: _compose_info.get(k) for k in ("success", "stdout", "stderr")}
            if not _compose_info["success"] or _compose_info["missing"]:
                return _err(
                    "dftracer-compose did not link "
                    + (", ".join(_compose_info["missing"]) or "the modules")
                    + ": " + (_compose_info.get("error") or "a module this machine has did not build; see the log"),
                    compose={k: _compose_info.get(k) for k in ("asked", "linked", "absent", "missing", "composition", "script")},
                    venv=str(Path(venv_python_str).parent.parent),
                    full_log="artifacts/06c_compose_dftracer.log",
                )
            # What the installed dftracer has: the modules linked, plus variorum (read at run time, no compose).
            features_enabled = [f for f in features_enabled
                                if f.split("=")[0] in _compose_info["linked"] or f.split("=")[0] == "variorum"]
            for _m in ("mpi", "hdf5", "papi", "hip", "hwloc", "cuda"):
                features[_m] = _m in _compose_info["linked"]
        elif _prerelease:
            # Only what the public wheel really has: no MPI/HDF5/PAPI/HIP/variorum,
            # and its libdftracer_core links no hwloc either (checked with ldd).
            features_enabled = []
            from dftracer_agents.mcp_tools.tools.session.install import _install_dftracer_prerelease_wheel
            result = _install_dftracer_prerelease_wheel(
                python_exe=venv_python_str,
                ws=ws,
                run_id=run_id,
                pip_index_url=pip_index_url,
                dftracer_ref=dftracer_ref,
            )
            if not result["success"]:
                return _err(
                    "prebuilt dftracer prerelease install failed (nothing was compiled; no source fallback): "
                    + result.get("error", ""),
                    prerelease=result.get("prerelease"),
                    venv=str(Path(venv_python_str).parent.parent),
                    steps=_trim_steps(result["steps"]),
                    full_log="artifacts/06_dftracer_prerelease_probe.log, artifacts/06_session_install_dftracer.log",
                )
        else:
            result = _install_dftracer_pip_direct(
                dftracer_ref=dftracer_ref,
                dftracer_repo=dftracer_repo,
                pip_index_url=pip_index_url,
                features={**features, "dftracer_pip_env": _pip_env_in},
                python_exe=venv_python_str,
                jobs=jobs,
                ws=ws,
                run_id=run_id,
                prebuilt=_prebuilt_mode,
            )
        if not result["success"]:
            return _err(
                "dftracer pip install failed",
                features_enabled=features_enabled,
                ref=dftracer_ref,
                venv=str(Path(venv_python_str).parent.parent),
                steps=_trim_steps(result["steps"]),
                full_log="artifacts/ (session_install_dftracer step log)",
            )

        # Locate include/lib dirs inside the venv's site-packages
        dirs = _find_dftracer_dirs(python_exe=venv_python_str) or {}

        install_dir = ws / "install_ann"
        install_dir.mkdir(exist_ok=True)
        _pre_info = result.get("prerelease") or {}
        if _pre_info.get("version"):
            dftracer_ref = _pre_info["version"]
        _save_state(run_id, {
            "dftracer_install_prefix":  dirs.get("lib_dir", str(install_dir)),
            "dftracer_pip_include_dir": dirs.get("include_dir", ""),
            "dftracer_pip_lib_dir":     dirs.get("lib_dir", ""),
            # What the INSTALLED dftracer was built with (detection.features
            # still describes the APP, e.g. it may use MPI while this dftracer
            # has no MPI tracing).
            "dftracer_features_enabled": features_enabled,
            "dftracer_build": {
                "mode": _prebuilt_mode,
                "version": dftracer_ref,
                **{f: bool(features.get(f)) for f in ("mpi", "hdf5", "papi", "hip", "variorum")},
            },
        })
        _wh_info = result.get("wheelhouse") or {}
        msg = f"dftracer installed via pip into session venv (features={features_enabled})"
        if _compose_info:
            msg += (f"\n\nComposed: linked {', '.join(_compose_info['linked']) or 'no optional module'}"
                    + (f"; not on this machine: {', '.join(_compose_info['absent'])}" if _compose_info["absent"] else "")
                    + f" (re-run: {_compose_info.get('script')}).")
        if _pre_info.get("message"):
            msg += f"\n\nPrerelease: {_pre_info['message']}."
        if not dirs.get("include_dir") or not dirs.get("lib_dir"):
            msg += "\n\nWARNING: dftracer include/lib dirs not found in the venv."
        if _wh_info.get("message"):
            msg += f"\n\nWheelhouse: {_wh_info['message']}."
        if compat_warnings:
            msg += "\n\nCOMPATIBILITY WARNINGS:\n" + "\n".join(
                f"  • {w}" for w in compat_warnings
            )
        return _ok(
            msg,
            features_enabled=features_enabled,
            compat_warnings=compat_warnings,
            ref=dftracer_ref,
            venv=str(Path(venv_python_str).parent.parent),
            include_dir=dirs.get("include_dir", "(not found)"),
            lib_dir=dirs.get("lib_dir", "(not found)"),
            lib_name=dirs.get("lib_name", "libdftracer_core.so"),
            steps=_trim_steps(result["steps"]),
            full_log="artifacts/ (session_install_dftracer step log)",
            wheelhouse={k: v for k, v in _wh_info.items() if k != "stored"} | (
                {"stored": {k: v for k, v in (_wh_info.get("stored") or {}).items() if k != "message"}}
                if _wh_info.get("stored") else {}),
            **({"prerelease": _pre_info} if _pre_info else {}),
            **({"compose": {k: _compose_info.get(k) for k in ("asked", "linked", "absent", "composition", "script")}}
               if _compose_info else {}),
        )

    @mcp.tool()
    def dftracer_wheelhouse(
        action: str = "search",
        text: str = "",
        system: str = "",
        python_tag: str = "",
        ref: str = "",
        commit: str = "",
        include_broken: bool = False,
        key: str = "",
        limit: int = 50,
    ) -> str:
        """Search or manage the prebuilt dftracer wheels (one per exact build combination).

        Use to see which dftracer builds already exist for a system before installing, or to retire a bad wheel.
        action: "search" (default) lists wheels matching the filters - text: substring over MPI / HDF5 / compilers /
        features / CMake args / modules / version / repo; system (e.g. tuolumne), python_tag (e.g. cp313), ref,
        commit prefix; include_broken also lists wheels that failed an install check. "retire" marks the wheel
        ``key`` broken so it is never installed again (the next install builds fresh and stores a new one).
        Returns JSON {status, wheelhouse, index, count, wheels:[{system, python_tag, dftracer_ref, dftracer_commit,
        mpi, hdf5, compilers, features_json, wheel_path, used_count, build_seconds, key, ...}], totals}.
        """
        from dftracer_agents.mcp_tools.tools.session import wheelhouse as _wh
        if action == "retire":
            if not key:
                return _err("retire needs the wheel's key")
            _wh.mark(key, "broken", "retired by request")
            return _ok("wheel retired", key=key)
        if action != "search":
            return _err("action must be search or retire")
        return json.dumps(_wh.search(text, system, python_tag, ref, commit, include_broken, limit), default=str)

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
    def session_generate_dftracer_pc(run_id: str) -> str:
        """Generate a pkg-config ``.pc`` file for dftracer and save it under install_ann/.

        dftracer is a CMake-only project and does not always install a
        ``dftracer.pc`` file.  This tool locates ``libdftracer_core.so`` —
        first in ``<workspace>/install_ann/lib[64]/``, then in the pip-installed
        site-packages layout — and writes a conforming ``dftracer.pc`` to
        ``<workspace>/install_ann/lib/pkgconfig/dftracer.pc``.

        After this tool runs, ``pkg-config --cflags dftracer`` and
        ``pkg-config --libs dftracer`` work correctly when
        ``PKG_CONFIG_PATH`` includes the returned *pkg_config_path*.
        ``session_build_annotated`` sets ``PKG_CONFIG_PATH`` automatically, so
        the typical call order for autotools projects is::

            session_install_dftracer → session_generate_dftracer_pc
              → session_patch_build → session_build_annotated

        Side effects:
            * Creates ``<workspace>/install_ann/lib/pkgconfig/`` if absent.
            * Writes ``dftracer.pc`` (overwriting any existing file).
            * Persists ``{"dftracer_pc_file": …, "dftracer_pkg_config_path": …}``
              to ``session.json``.

        Args:
            run_id: Session identifier returned by ``session_create``.

        Returns:
            JSON string with keys:
                * ``status``           — ``"ok"`` or ``"error"``.
                * ``message``          — path of the written file.
                * ``pc_file``          — absolute path to the generated ``.pc``.
                * ``pkg_config_path``  — directory to add to ``PKG_CONFIG_PATH``.
                * ``lib_dir``          — directory containing ``libdftracer_core.so``.
                * ``include_dir``      — directory containing ``dftracer/dftracer.h``.
        """
        ws = _ws(run_id)
        install_ann = ws / "install_ann"

        dirs = _find_dftracer_dirs(
            python_exe=sys.executable,
            cmake_prefix=install_ann if install_ann.exists() else None,
        )
        if not dirs:
            return _err(
                "libdftracer_core.so not found in install_ann/ or site-packages. "
                "Run session_install_dftracer first."
            )

        lib_dir     = Path(dirs["lib_dir"])
        include_dir = Path(dirs["include_dir"])
        prefix      = lib_dir.parent

        version = "2.0.3"
        for f in lib_dir.glob("libdftracer_core.so.*"):
            parts = f.name.split(".")
            if len(parts) >= 4:
                version = ".".join(parts[3:])
                break

        pc_content = (
            f"prefix={prefix}\n"
            f"exec_prefix=${{prefix}}\n"
            f"libdir=${{exec_prefix}}/lib\n"
            f"includedir=${{prefix}}/include\n"
            f"\n"
            f"Name: dftracer\n"
            f"Description: DFTracer I/O tracing library\n"
            f"Version: {version}\n"
            f"Libs: -L${{libdir}} -ldftracer_core -Wl,-rpath,${{libdir}}\n"
            f"Cflags: -I${{includedir}}\n"
        )

        pc_dir  = install_ann / "lib" / "pkgconfig"
        pc_dir.mkdir(parents=True, exist_ok=True)
        pc_file = pc_dir / "dftracer.pc"
        pc_file.write_text(pc_content)

        _save_state(run_id, {
            "dftracer_pc_file":          str(pc_file),
            "dftracer_pkg_config_path":  str(pc_dir),
        })
        return _ok(
            f"Generated {pc_file}",
            pc_file=str(pc_file),
            pkg_config_path=str(pc_dir),
            lib_dir=str(lib_dir),
            include_dir=str(include_dir),
            version=version,
            hint=(
                f"Export: PKG_CONFIG_PATH={pc_dir}:$PKG_CONFIG_PATH "
                f"before calling ./configure or make"
            ),
        )

    @mcp.tool()
    def dftracer_install_check(run_id: str = "", workspace: str = "", require: str = "", run_import: bool = False) -> dict:
        """Validator for the install stage: rule deterministically on what session_install_dftracer produced. Checks
        session.json recorded the install (prefix, lib dir, mode, version), the app was built first, libdftracer_core is
        in the recorded lib dir, the session venv holds exactly ONE real libdftracer_core (compose-tree archives do not
        count), dftracer/dftracer.h is present, the compose result (dftracer/lib*/dftracer/composition.txt) LINKED every
        layer asked for, the generated dftracer.pc (if any) links -ldftracer_core, and optionally (run_import=True) that
        the venv python imports dftracer.dftracer. Args: run_id or workspace (path, overrides run_id); require: comma
        list of layers that must be linked (mpi,hdf5,papi,hip,hwloc,cuda; default: those recorded in dftracer_build);
        run_import. Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], failed, workspace, asked}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _install_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id), require, run_import)

    @mcp.tool()
    def dftracer_install_selftest() -> dict:
        """Self-test of dftracer_install_check on a fake session venv in a temporary directory: a composed venv with one
        core and MPI linked must pass; asking for hdf5 (absent) must fail linked_hdf5; a second core copy must fail
        one_core; a missing workspace must fail as 'unknown run'. Returns {status: pass|fail, cases: [...]}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / "app" / "20260101_000000"
            sp = ws / "venv" / "lib" / "python3.12" / "site-packages"
            lib = sp / "dftracer" / "lib64"
            inc = sp / "dftracer" / "include" / "dftracer"
            (lib / "dftracer").mkdir(parents=True)
            inc.mkdir(parents=True)
            (inc / "dftracer.h").write_text("/* h */\n")
            (lib / "libdftracer_core.so.4.3.0").write_text("ELF")
            (lib / "libdftracer_core.so").symlink_to("libdftracer_core.so.4.3.0")
            (lib / "dftracer" / "compose").mkdir()
            (lib / "dftracer" / "compose" / "libdftracer_core_base.a").write_text("ar")
            (lib / "dftracer" / "composition.txt").write_text("mpi cray-mpich-9 linked\nhdf5 - absent\npapi - disabled\n")
            (ws / "session.json").write_text(json.dumps({
                "app_built": True, "dftracer_install_prefix": str(lib), "dftracer_pip_lib_dir": str(lib),
                "dftracer_pip_include_dir": str(sp / "dftracer" / "include"),
                "dftracer_build": {"mode": "compose", "version": "2.2.0", "mpi": True, "hdf5": False}}))
            cases.append({"case": "composed_mpi", "want": "pass", "got": _install_check(ws)["status"]})
            cases.append({"case": "hdf5_absent", "want": ["linked_hdf5"], "got": _install_check(ws, "mpi,hdf5")["failed"]})
            (sp / "dftracer.libs").mkdir()
            (sp / "dftracer.libs" / "libdftracer_core-abc.so.4").write_text("ELF")
            cases.append({"case": "two_cores", "want": ["one_core"], "got": _install_check(ws)["failed"]})
            r = _install_check(Path(d) / "nope")
            cases.append({"case": "missing", "want": "unknown run", "got": r["checks"][0]["detail"][:11]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
