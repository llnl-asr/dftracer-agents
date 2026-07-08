"""
Source tree detection helpers — language, build tool, features, and dftracer pip env vars.

This module implements heuristic analysis of an application's source tree so
that downstream pipeline stages (build, install, run) can make informed
decisions without requiring the user to supply build flags manually.  The
analysis operates entirely at the filesystem and text level — no build system
is invoked during detection.

Primary entry points:

- :func:`_detect_system_hdf5` — probes the host system for an existing HDF5
  installation using ``pkg-config``, compiler wrappers, and header path scans.
- :func:`_detect_system_hwloc` — probes the host system for hwloc dev libs.
- :func:`_detect_info` — scans the source tree and returns a comprehensive
  dict covering detected languages, build tool, optional features, the complete
  ``dftracer_pip_env`` dict ready to pass to ``pip install``, and the first few
  kilobytes of the project README.

``dftracer_pip_env`` maps directly to the environment variables read by
dftracer's ``setup.py``.  All options supported by setup.py are covered:

  DFTRACER_ENABLE_MPI              ON if MPI detected in source
  DFTRACER_ENABLE_HDF5             ON if HDF5 detected in source or system
  HDF5_ROOT / HDF5_DIR             set when system HDF5 prefix is known
  DFTRACER_ENABLE_HIP_TRACING      ON if HIP GPU code detected in source
  DFTRACER_DISABLE_HWLOC           OFF if hwloc dev libs found; absent otherwise
  DFTRACER_BUILD_TYPE              RelWithDebInfo (always)
  DFTRACER_ENABLE_TESTS            OFF (always)
  DFTRACER_ENABLE_DLIO_BENCHMARK_TESTS  OFF (always)
  DFTRACER_ENABLE_PAPER_TESTS      OFF (always)
  CMAKE_ARGS                       explicit -D flags for MPI and HDF5 to prevent
                                   cmake auto-detection failures; includes
                                   -DMPI_C_COMPILER, -DMPI_CXX_COMPILER, and
                                   -DHDF5_ROOT pointing to the correct (parallel)
                                   HDF5 variant when both MPI and HDF5 are present
  JOBS / CMAKE_BUILD_PARALLEL_LEVEL    set at install time from jobs param

Detection is intentionally conservative: a feature is reported as present only
when a concrete indicator is found (a header include pattern, an API call
pattern, or a pkg-config entry).

Runtime constraints: :func:`_detect_info` reads up to 5 MB of combined source
text to bound execution time on large repositories.  External tool invocations
each carry a 10-second timeout so the detection phase cannot block the pipeline.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


#: Filesystem paths searched for the ``hdf5.h`` header when no pkg-config or
#: compiler wrapper is available.  Ordered from the most common Linux
#: distribution install location to less-common prefix installs.
_HDF5_HEADER_SEARCH = [
    "/usr/include/hdf5",
    "/usr/include/hdf5/serial",
    "/usr/local/include",
    "/usr/local/hdf5/include",
    "/opt/hdf5/include",
]

#: Filesystem paths searched for HDF5 shared/static libraries.  Used
#: indirectly by :func:`_detect_system_hdf5` to infer installation prefix when
#: compiler wrappers are unavailable.
_HDF5_LIB_SEARCH = [
    "/usr/lib",
    "/usr/lib/x86_64-linux-gnu/hdf5/serial",
    "/usr/lib/x86_64-linux-gnu",
    "/usr/local/lib",
    "/usr/local/hdf5/lib",
    "/opt/hdf5/lib",
]

#: dftracer-compatible HDF5 (major, minor) series.
#: Any patch within these series is accepted; the specific recommended release
#: for each series is listed in :data:`_HDF5_RECOMMENDED_VERSIONS`.
_HDF5_COMPATIBLE_SERIES: set = {(1, 8), (1, 10), (1, 12), (1, 14)}

#: Recommended specific version string for each compatible HDF5 series.
#: Use these when building HDF5 from source; prefer the highest series (1.14.x).
_HDF5_RECOMMENDED_VERSIONS: Dict[tuple, str] = {
    (1, 8):  "1.8.23",
    (1, 10): "1.10.5",
    (1, 12): "1.12.3",
    (1, 14): "1.14.5",
}

#: Preferred default when no compatible system HDF5 is found.
_HDF5_DEFAULT_VERSION = "1.14.5"


def _hdf5_version_compatible(version: Optional[str]) -> bool:
    """Return True if *version* belongs to a dftracer-compatible HDF5 series.

    Accepts dotted strings such as ``"1.14.3"`` or ``"1.12.2"``.  Returns
    ``False`` when *version* is ``None`` or cannot be parsed.
    """
    if not version:
        return False
    parts = version.split(".")
    try:
        series = (int(parts[0]), int(parts[1]))
    except (IndexError, ValueError):
        return False
    return series in _HDF5_COMPATIBLE_SERIES


def _hdf5_from_prefix(prefix: str) -> Dict[str, Any]:
    """Build an HDF5 detection result from an explicit install *prefix*.

    Used when the caller already knows the HDF5 install to use (e.g. a
    source-built HDF5 in the session workspace). Probes ``<prefix>/bin/h5pcc``
    or ``h5cc -showconfig`` for the version and parallel flag, falling back to
    reading the version out of the installed headers. This is authoritative —
    it always wins over the system scan — so dftracer links the SAME HDF5 the
    application was built against instead of a stray ``/usr`` HDF5.
    """
    p = Path(prefix)
    version: Optional[str] = None
    parallel = False
    for wrapper in ("h5pcc", "h5cc"):
        w = p / "bin" / wrapper
        if w.exists():
            parallel = parallel or wrapper == "h5pcc"
            try:
                r = subprocess.run([str(w), "-showconfig"],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    m = re.search(r"HDF5 Version:\s+(\S+)", r.stdout)
                    if m:
                        version = m.group(1)
                    if re.search(r"Parallel HDF5:\s+yes", r.stdout, re.I):
                        parallel = True
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass
    if version is None:
        inc = _find_hdf5_include_under(prefix)
        version = _read_hdf5_version_from_header(inc) if inc else None
    series = tuple(int(x) for x in version.split(".")[:2]) if version else None
    return {
        "found": True,
        "version": version,
        "prefix": prefix,
        "parallel": parallel,
        "cmake_hint": f"-DHDF5_ROOT={prefix}",
        "source": f"prefix:{prefix}",
        "compatible": _hdf5_version_compatible(version),
        "recommended": _HDF5_RECOMMENDED_VERSIONS.get(series) if series else _HDF5_DEFAULT_VERSION,
    }


def _detect_system_hdf5(prefix: Optional[str] = None) -> Dict[str, Any]:
    """Probe the host system for an HDF5 installation.

    When *prefix* is provided and exists, that install is used authoritatively
    (via :func:`_hdf5_from_prefix`) instead of scanning the system — this is how
    a caller pins a source-built HDF5 so dftracer links against it.

    Detection is attempted via four strategies in order of decreasing
    reliability:

    1. ``pkg-config --modversion hdf5`` (and ``hdf5-serial`` as a fallback
       package name) — most reliable on Debian/Ubuntu-family systems.
    2. ``h5cc -showconfig`` / ``h5pcc -showconfig`` — HDF5-bundled C/C++
       compiler wrappers that expose version and prefix information.
    3. ``h5dump --version`` — available even when only the HDF5 tools package
       is installed; yields a version string but no prefix.
    4. Header file scan across :data:`_HDF5_HEADER_SEARCH` paths — last resort
       when no HDF5 tooling is on ``PATH``; yields a prefix but no version.

    Each strategy is attempted with a 10-second timeout.  ``FileNotFoundError``
    and ``subprocess.TimeoutExpired`` are silently swallowed so that the
    function always returns a result.

    Returns:
        Dict[str, Any]: A dict with the following keys:

            - ``found`` (bool): ``True`` if any HDF5 indicator was detected.
            - ``version`` (str or None): Dotted version string such as
              ``"1.12.2"``, or ``None`` when not determinable.
            - ``prefix`` (str or None): Installation prefix directory (e.g.
              ``"/usr/local/hdf5"``), or ``None`` when not determinable.
            - ``cmake_hint`` (str or None): A ``-DHDF5_ROOT=<prefix>`` string
              suitable for passing to CMake, or ``None`` when no prefix was
              found.
            - ``source`` (str or None): Human-readable label identifying which
              detection strategy succeeded (e.g. ``"pkg-config:hdf5"``,
              ``"h5cc"``, ``"h5dump"``, or ``"header:/usr/…/hdf5.h"``).
            - ``compatible`` (bool): ``True`` if the detected version belongs
              to a dftracer-compatible HDF5 series (1.8.x, 1.10.x, 1.12.x,
              or 1.14.x).  Always ``False`` when ``version`` is ``None``.
            - ``recommended`` (str or None): The preferred specific release for
              the detected series (e.g. ``"1.14.5"``), or ``None`` when the
              version could not be determined.
    """
    # 0. Explicit caller-provided prefix wins over any system scan.
    if prefix and Path(prefix).exists():
        return _hdf5_from_prefix(prefix)

    # 1. pkg-config
    for pkg in ("hdf5", "hdf5-serial"):
        try:
            r = subprocess.run(
                ["pkg-config", "--modversion", pkg],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                version = r.stdout.strip()
                prefix_r = subprocess.run(
                    ["pkg-config", "--variable=prefix", pkg],
                    capture_output=True, text=True, timeout=10,
                )
                prefix = prefix_r.stdout.strip() if prefix_r.returncode == 0 else None
                compat = _hdf5_version_compatible(version)
                series = tuple(int(x) for x in version.split(".")[:2]) if version else None
                return {
                    "found": True, "version": version,
                    "prefix": prefix,
                    "cmake_hint": f"-DHDF5_ROOT={prefix}" if prefix else None,
                    "source": f"pkg-config:{pkg}",
                    "compatible": compat,
                    "recommended": _HDF5_RECOMMENDED_VERSIONS.get(series) if series else None,
                }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 2. h5cc / h5pcc wrapper
    for wrapper in ("h5cc", "h5pcc"):
        if shutil.which(wrapper):
            try:
                r = subprocess.run(
                    [wrapper, "-showconfig"],
                    capture_output=True, text=True, timeout=10,
                )
                if r.returncode == 0:
                    m = re.search(r"HDF5 Version:\s+(\S+)", r.stdout)
                    version = m.group(1) if m else None
                    mp = re.search(r"Installation point:\s+(\S+)", r.stdout)
                    prefix = mp.group(1) if mp else None
                    compat = _hdf5_version_compatible(version)
                    series = tuple(int(x) for x in version.split(".")[:2]) if version else None
                    return {
                        "found": True, "version": version,
                        "prefix": prefix,
                        "cmake_hint": f"-DHDF5_ROOT={prefix}" if prefix else None,
                        "source": wrapper,
                        "compatible": compat,
                        "recommended": _HDF5_RECOMMENDED_VERSIONS.get(series) if series else None,
                    }
            except subprocess.TimeoutExpired:
                pass

    # 3. h5dump version string
    if shutil.which("h5dump"):
        try:
            r = subprocess.run(
                ["h5dump", "--version"],
                capture_output=True, text=True, timeout=10,
            )
            m = re.search(r"(\d+\.\d+\.\d+)", r.stdout + r.stderr)
            if m:
                version = m.group(1)
                compat = _hdf5_version_compatible(version)
                series = tuple(int(x) for x in version.split(".")[:2])
                return {
                    "found": True, "version": version,
                    "prefix": None, "cmake_hint": None,
                    "source": "h5dump",
                    "compatible": compat,
                    "recommended": _HDF5_RECOMMENDED_VERSIONS.get(series),
                }
        except subprocess.TimeoutExpired:
            pass

    # 4. Header scan — version unknown; mark as not yet verified compatible
    for inc in _HDF5_HEADER_SEARCH:
        h = Path(inc) / "hdf5.h"
        if h.exists():
            prefix = str(Path(inc).parent) if Path(inc).name == "include" else None
            return {
                "found": True, "version": None,
                "prefix": prefix,
                "cmake_hint": f"-DHDF5_ROOT={prefix}" if prefix else None,
                "source": f"header:{h}",
                "compatible": False,
                "recommended": _HDF5_DEFAULT_VERSION,
            }

    return {"found": False, "version": None, "prefix": None,
            "cmake_hint": None, "source": None,
            "compatible": False, "recommended": _HDF5_DEFAULT_VERSION}


# ---------------------------------------------------------------------------
# MPI compatibility constants (derived from dftracer brahma/mpi.cpp)
# BRAHMA_MPI_VERSION encoding: MAJOR * 100000 + MINOR * 100 + PATCH
# ---------------------------------------------------------------------------

#: Compatible MPI version ranges per implementation.
#: Each entry is a list of ``(min_inclusive, max_exclusive)`` BRAHMA_MPI_VERSION ints.
_MPI_COMPATIBLE_RANGES: Dict[str, list] = {
    "openmpi":   [(400106, 400200), (500006, 500100)],
    "mpich":     [(300403, 300500), (400203, 400300)],
    "craympich": [(800108, 800200), (900001, 900200)],
}

#: Human-readable description of compatible ranges for each implementation.
_MPI_COMPATIBLE_DISPLAY: Dict[str, str] = {
    "openmpi":   "4.1.6 – 4.1.x  or  5.0.6 – 5.0.x",
    "mpich":     "3.4.3 – 3.4.x  or  4.2.3 – 4.2.x",
    "craympich": "8.1.8 – 8.1.x  or  9.0.1 – 9.1.x",
}

#: GitHub issue URL for requesting new MPI version support.
_DFTRACER_ISSUES_URL = "https://github.com/llnl/dftracer/issues"


def _mpi_to_brahma_int(major: int, minor: int, patch: int) -> int:
    """Convert a (major, minor, patch) MPI version tuple to a BRAHMA_MPI_VERSION int."""
    return major * 100000 + minor * 100 + patch


def _mpi_version_compatible(impl: str, version_str: str) -> bool:
    """Return True if *version_str* of *impl* falls within a dftracer-compatible range.

    Args:
        impl:        Normalised implementation key: ``"openmpi"``, ``"mpich"``,
                     or ``"craympich"``.  Unknown impls always return ``False``.
        version_str: Dotted version string, e.g. ``"4.1.6"`` or ``"5.0.7"``.

    Returns:
        True when the version is within at least one compatible range; False otherwise.
    """
    ranges = _MPI_COMPATIBLE_RANGES.get(impl)
    if not ranges or not version_str:
        return False
    parts = version_str.split(".")
    try:
        vint = _mpi_to_brahma_int(int(parts[0]), int(parts[1]), int(parts[2]))
    except (IndexError, ValueError):
        return False
    return any(lo <= vint < hi for lo, hi in ranges)


def _detect_system_mpi() -> Dict[str, Any]:
    """Detect the system MPI implementation, version, and dftracer compatibility.

    Probes in order:
    1. ``mpiexec --version`` / ``mpirun --version`` — available on most MPI installs.
    2. ``ompi_info --version`` — OpenMPI-specific fallback.
    3. ``mpichversion`` — MPICH-specific fallback.
    4. ``mpicc --show`` / ``--showme`` — last resort compiler-wrapper probe.

    Returns:
        Dict[str, Any] with keys:

        - ``found`` (bool): True if any MPI was detected.
        - ``impl`` (str or None): Normalised implementation key —
          ``"openmpi"``, ``"mpich"``, ``"craympich"``, or ``"unknown"``.
        - ``impl_display`` (str or None): Human-readable implementation name.
        - ``version`` (str or None): Dotted version string, e.g. ``"4.1.6"``.
        - ``compatible`` (bool): True if the version is dftracer-compatible.
        - ``compatible_versions`` (str or None): Human-readable range string
          for the detected impl, e.g. ``"4.1.6 – 4.1.x  or  5.0.6 – 5.0.x"``.
        - ``source`` (str or None): Which probe succeeded.
    """
    impl: Optional[str] = None
    version_str: Optional[str] = None
    source: Optional[str] = None

    # 1. mpiexec / mpirun --version
    for cmd in ("mpiexec", "mpirun"):
        if not shutil.which(cmd):
            continue
        try:
            r = subprocess.run([cmd, "--version"], capture_output=True, text=True, timeout=10)
            output = r.stdout + r.stderr
            m = re.search(r"Open MPI[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
            if m:
                impl, version_str, source = "openmpi", m.group(1), cmd
                break
            m = re.search(r"Cray MPICH[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
            if m:
                impl, version_str, source = "craympich", m.group(1), cmd
                break
            m = re.search(r"MPICH[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
            if m:
                impl, version_str, source = "mpich", m.group(1), cmd
                break
            m = re.search(r"Intel.{0,20}MPI[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
            if m:
                impl, version_str, source = "intelmpi", m.group(1), cmd
                break
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 2. ompi_info for OpenMPI
    if not impl and shutil.which("ompi_info"):
        try:
            r = subprocess.run(["ompi_info", "--version"], capture_output=True, text=True, timeout=10)
            m = re.search(r"Open MPI[^0-9]*(\d+\.\d+\.\d+)", r.stdout + r.stderr, re.I)
            if m:
                impl, version_str, source = "openmpi", m.group(1), "ompi_info"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 3. mpichversion
    if not impl and shutil.which("mpichversion"):
        try:
            r = subprocess.run(["mpichversion"], capture_output=True, text=True, timeout=10)
            m = re.search(r"MPICH\s+Version:\s+(\d+\.\d+\.\d+)", r.stdout + r.stderr, re.I)
            if m:
                impl, version_str, source = "mpich", m.group(1), "mpichversion"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    # 4. mpicc compiler wrapper
    if not impl and shutil.which("mpicc"):
        for flag in ("--showme:version", "--version", "-v"):
            try:
                r = subprocess.run(["mpicc", flag], capture_output=True, text=True, timeout=10)
                output = r.stdout + r.stderr
                m = re.search(r"Open MPI[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
                if m:
                    impl, version_str, source = "openmpi", m.group(1), f"mpicc {flag}"
                    break
                m = re.search(r"MPICH[^0-9]*(\d+\.\d+\.\d+)", output, re.I)
                if m:
                    impl, version_str, source = "mpich", m.group(1), f"mpicc {flag}"
                    break
            except (FileNotFoundError, subprocess.TimeoutExpired):
                pass

    if not impl:
        return {"found": False, "impl": None, "impl_display": None, "version": None,
                "compatible": False, "compatible_versions": None, "source": None}

    _DISPLAY = {"openmpi": "Open MPI", "mpich": "MPICH", "craympich": "Cray MPICH",
                "intelmpi": "Intel MPI", "unknown": "Unknown MPI"}
    compat = _mpi_version_compatible(impl, version_str or "")

    return {
        "found": True,
        "impl":             impl,
        "impl_display":     _DISPLAY.get(impl, impl),
        "version":          version_str,
        "compatible":       compat,
        "compatible_versions": _MPI_COMPATIBLE_DISPLAY.get(impl),
        "source":           source,
    }


# ---------------------------------------------------------------------------
# MPI implementation detection via compiled C probe
# ---------------------------------------------------------------------------

#: Small C program that calls MPI_Get_library_version() to get the vendor
#: string. This is the most reliable way to detect OpenMPI vs MPICH vs
#: MVAPICH2 vs Cray-MPICH — string matching on mpirun output can be fooled
#: by wrapper scripts, but the MPI ABI itself always tells the truth.
_MPI_VERSION_C_SRC = r"""
#include <stdio.h>
#include <mpi.h>
int main(int argc, char** argv) {
    char version[MPI_MAX_LIBRARY_VERSION_STRING];
    int len = 0;
    MPI_Init(&argc, &argv);
    MPI_Get_library_version(version, &len);
    MPI_Finalize();
    if (len > 0) {
        printf("%s\n", version);
    }
    return 0;
}
"""

#: Candidate mpicc wrapper names to search for MPI C compiler, ordered from
#: most specific (implementation-branded) to least specific (plain ``mpicc``).
_MPICC_CANDIDATES: List[str] = [
    "mpicc.openmpi",   # Debian/Ubuntu OpenMPI
    "mpicc.mpich",     # Debian/Ubuntu MPICH
    "mpicc.mpich3",    # older Debian MPICH
    "mpicc",           # generic / Cray / MVAPICH / PATH-resolved
]

#: Corresponding C++ compiler wrappers in the same priority order.
_MPICXX_CANDIDATES: List[str] = [
    "mpicxx.openmpi",
    "mpic++.openmpi",
    "mpicxx.mpich",
    "mpicxx.mpich3",
    "mpicxx",
    "mpic++",
]


def _probe_mpi_via_c(mpicc: str) -> Optional[Dict[str, Any]]:
    """Compile and run a small C program that calls ``MPI_Get_library_version``.

    Returns a dict with ``impl``, ``version``, and ``version_str`` if
    successful, or ``None`` if compilation or execution fails.

    Args:
        mpicc: Path to the MPI C compiler wrapper (e.g. ``/usr/bin/mpicc``).

    Returns:
        Dict with keys ``impl`` (``"openmpi"``, ``"mpich"``, ``"mvapich"``,
        ``"craympich"``, or ``"unknown"``), ``version`` (dotted string), and
        ``brahma_int`` (BRAHMA_MPI_VERSION integer), or ``None``.
    """
    try:
        with tempfile.TemporaryDirectory(prefix="dftracer_mpi_probe_") as tmpdir:
            src = os.path.join(tmpdir, "mpi_version.c")
            exe = os.path.join(tmpdir, "mpi_version")
            with open(src, "w") as f:
                f.write(_MPI_VERSION_C_SRC)

            r = subprocess.run(
                [mpicc, src, "-o", exe],
                capture_output=True, text=True, timeout=30,
            )
            if r.returncode != 0:
                return None

            # Run with a single process; MPI_Init is needed but we do not
            # spawn real workers — any single-process MPI runtime handles this.
            r2 = subprocess.run(
                [exe],
                capture_output=True, text=True, timeout=15,
            )
            if r2.returncode != 0:
                return None

            output = r2.stdout.strip()
            if not output:
                return None

            return _parse_mpi_library_version(output)

    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None


def _parse_mpi_library_version(output: str) -> Optional[Dict[str, Any]]:
    """Parse the string returned by ``MPI_Get_library_version``.

    Handles the most common vendor strings:

    - ``Open MPI v4.1.6``       → openmpi  4.1.6
    - ``MVAPICH2 2.3.6``        → mvapich  2.3.6
    - ``MPICH Version:  3.4.3`` → mpich    3.4.3
    - ``MPI VERSION = Cray MPICH version 8.1.28`` → craympich 8.1.28

    Returns ``None`` if no recognisable pattern is found.
    """
    patterns = [
        (r"Open MPI[^\d]*(\d+\.\d+\.\d+)", "openmpi"),
        (r"MVAPICH2?[^\d]*(\d+\.\d+\.\d+)", "mvapich"),
        (r"Cray MPICH[^\d]*(\d+\.\d+\.\d+)", "craympich"),
        (r"MPICH[^\d]*(\d+\.\d+\.\d+)", "mpich"),
    ]
    for pattern, impl in patterns:
        m = re.search(pattern, output, re.I)
        if m:
            version_str = m.group(1)
            try:
                major, minor, patch = (int(x) for x in version_str.split("."))
                brahma_int = _mpi_to_brahma_int(major, minor, patch)
            except (ValueError, AttributeError):
                brahma_int = 0
            return {"impl": impl, "version": version_str, "brahma_int": brahma_int}
    return None


def _find_mpi_compilers() -> Tuple[Optional[str], Optional[str]]:
    """Locate the best available MPI C and C++ compiler wrappers.

    Tries implementation-branded names first (``mpicc.openmpi``, etc.) so
    that on systems with multiple MPI installs the dominant one wins.

    Returns:
        Tuple ``(mpicc_path, mpicxx_path)`` where each element is either a
        resolved absolute path or ``None`` if nothing was found.
    """
    mpicc = next((shutil.which(c) for c in _MPICC_CANDIDATES if shutil.which(c)), None)
    mpicxx = next((shutil.which(c) for c in _MPICXX_CANDIDATES if shutil.which(c)), None)
    return mpicc, mpicxx


def _detect_mpi_impl(
    mpicc_override: Optional[str] = None,
    mpicxx_override: Optional[str] = None,
) -> Dict[str, Any]:
    """Detect MPI implementation, version, and compiler wrappers.

    When *mpicc_override* / *mpicxx_override* are provided (e.g. the exact MPI
    wrappers the application was built with), they are used verbatim instead of
    searching ``PATH`` — the C probe still compiles against them to read the
    true vendor/version, so the detected values match the app's MPI.

    Uses :func:`_probe_mpi_via_c` (``MPI_Get_library_version``) as the primary
    strategy and falls back to the text-based :func:`_detect_system_mpi` probes.

    Returns a dict with keys:

    - ``found`` (bool)
    - ``impl`` (str or None): ``"openmpi"``, ``"mpich"``, ``"mvapich"``,
      ``"craympich"``, or ``"unknown"``
    - ``version`` (str or None): dotted version string
    - ``brahma_int`` (int): BRAHMA_MPI_VERSION integer (0 if unknown)
    - ``compatible`` (bool)
    - ``mpicc`` (str or None): path to MPI C compiler wrapper
    - ``mpicxx`` (str or None): path to MPI C++ compiler wrapper
    - ``cmake_flags`` (List[str]): ``-D`` flags for cmake FindMPI
    """
    mpicc, mpicxx = _find_mpi_compilers()
    # Caller-supplied wrappers (the app's actual MPI) take priority.
    if mpicc_override and Path(mpicc_override).exists():
        mpicc = mpicc_override
    if mpicxx_override and Path(mpicxx_override).exists():
        mpicxx = mpicxx_override

    # Primary: compile and run a C probe (most accurate)
    probe: Optional[Dict[str, Any]] = None
    if mpicc:
        probe = _probe_mpi_via_c(mpicc)

    # Fallback: text-based detection
    if probe is None:
        text_info = _detect_system_mpi()
        if text_info["found"]:
            version_str = text_info.get("version") or ""
            impl = text_info.get("impl") or "unknown"
            try:
                major, minor, patch = (int(x) for x in version_str.split("."))
                brahma_int = _mpi_to_brahma_int(major, minor, patch)
            except (ValueError, AttributeError):
                brahma_int = 0
            probe = {"impl": impl, "version": version_str, "brahma_int": brahma_int}

    if probe is None or not mpicc:
        return {
            "found": bool(mpicc or probe),
            "impl": probe.get("impl") if probe else None,
            "version": probe.get("version") if probe else None,
            "brahma_int": probe.get("brahma_int", 0) if probe else 0,
            "compatible": False,
            "mpicc": mpicc,
            "mpicxx": mpicxx,
            "cmake_flags": [],
        }

    impl = probe.get("impl") or "unknown"
    version = probe.get("version")
    brahma_int = probe.get("brahma_int", 0)
    compat = _mpi_version_compatible(impl, version or "")

    cmake_flags: List[str] = []
    if mpicc:
        cmake_flags.append(f"-DMPI_C_COMPILER={mpicc}")
    if mpicxx:
        cmake_flags.append(f"-DMPI_CXX_COMPILER={mpicxx}")

    return {
        "found": True,
        "impl": impl,
        "version": version,
        "brahma_int": brahma_int,
        "compatible": compat,
        "mpicc": mpicc,
        "mpicxx": mpicxx,
        "cmake_flags": cmake_flags,
    }


#: Architecture-specific parallel HDF5 library paths searched when both MPI
#: and HDF5 are required. Key is arch substring, value is path pattern list.
_PARALLEL_HDF5_SEARCH: List[str] = [
    "/usr/lib/x86_64-linux-gnu/hdf5/openmpi",
    "/usr/lib/aarch64-linux-gnu/hdf5/openmpi",
    "/usr/lib/arm-linux-gnueabihf/hdf5/openmpi",
    "/usr/lib/powerpc64le-linux-gnu/hdf5/openmpi",
    "/usr/local/hdf5-parallel",
    "/opt/hdf5-parallel",
]


def _detect_parallel_hdf5() -> Optional[Dict[str, Any]]:
    """Detect a parallel (MPI-enabled) HDF5 installation.

    Prefers architecture-specific parallel HDF5 directories (e.g.
    ``/usr/lib/aarch64-linux-gnu/hdf5/openmpi``) over the generic install
    prefix reported by ``h5pcc``, because cmake's ``HDF5_ROOT`` works best
    when it points to a directory that contains both ``include/hdf5.h`` and
    ``libhdf5.so`` directly — the arch-specific paths satisfy that requirement
    while the generic ``/usr`` prefix may not.

    Falls back to ``h5pcc -showconfig`` when no arch-specific path is found.

    Returns a dict with ``prefix``, ``version``, ``include_dir``, and
    ``lib_dir`` if found, or ``None``.
    """
    # 1. Architecture-specific parallel HDF5 directories (preferred for cmake)
    for candidate in _PARALLEL_HDF5_SEARCH:
        p = Path(candidate)
        if p.exists() and (p / "libhdf5.so").exists():
            inc_link = p / "include"
            # The include/ dir may be a symlink to the real header directory
            inc = str(inc_link) if inc_link.exists() else None
            if inc:
                # Resolve symlink to get the real path for the header read
                real_inc = str(inc_link.resolve())
                if not Path(real_inc, "hdf5.h").exists():
                    inc = None
            if not inc:
                inc = _find_hdf5_include_under("/usr")
            version = _read_hdf5_version_from_header(inc) if inc else None
            return {
                "prefix": str(p),
                "version": version,
                "include_dir": inc,
                "lib_dir": str(p),
            }

    # 2. h5pcc wrapper fallback
    if shutil.which("h5pcc"):
        try:
            r = subprocess.run(
                ["h5pcc", "-showconfig"],
                capture_output=True, text=True, timeout=10,
            )
            if r.returncode == 0:
                m_ver = re.search(r"HDF5 Version:\s+(\S+)", r.stdout)
                m_pfx = re.search(r"Installation point:\s+(\S+)", r.stdout)
                version = m_ver.group(1) if m_ver else None
                prefix = m_pfx.group(1) if m_pfx else None
                if prefix:
                    inc = _find_hdf5_include_under(prefix)
                    return {
                        "prefix": prefix, "version": version,
                        "include_dir": inc,
                        "lib_dir": os.path.join(prefix, "lib"),
                    }
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return None


def _find_hdf5_include_under(prefix: str) -> Optional[str]:
    """Return the HDF5 include directory under *prefix* or its standard variants."""
    candidates = [
        os.path.join(prefix, "include", "hdf5", "openmpi"),
        os.path.join(prefix, "include", "hdf5", "serial"),
        os.path.join(prefix, "include", "hdf5"),
        os.path.join(prefix, "include"),
    ]
    for c in candidates:
        if Path(c, "hdf5.h").exists():
            return c
    return None


def _read_hdf5_version_from_header(include_dir: str) -> Optional[str]:
    """Extract the HDF5 version string from ``H5public.h``."""
    h5pub = Path(include_dir) / "H5public.h"
    if not h5pub.exists():
        return None
    try:
        text = h5pub.read_text(errors="ignore")
        major = re.search(r"#define\s+H5_VERS_MAJOR\s+(\d+)", text)
        minor = re.search(r"#define\s+H5_VERS_MINOR\s+(\d+)", text)
        release = re.search(r"#define\s+H5_VERS_RELEASE\s+(\d+)", text)
        if major and minor and release:
            return f"{major.group(1)}.{minor.group(1)}.{release.group(1)}"
    except OSError:
        pass
    return None


#: Header search paths for hwloc (hardware locality library).
_HWLOC_HEADER_SEARCH = [
    "/usr/include/hwloc.h",
    "/usr/local/include/hwloc.h",
    "/opt/hwloc/include/hwloc.h",
]


def _detect_system_hwloc() -> bool:
    """Return True if hwloc development libraries are installed on the system.

    Tries, in order:

    1. ``pkg-config --exists hwloc`` — most reliable on Debian/Ubuntu systems.
    2. Header scan across :data:`_HWLOC_HEADER_SEARCH`.

    Both probes carry a 10-second timeout.  Returns ``False`` if hwloc is not
    found rather than raising.
    """
    try:
        r = subprocess.run(
            ["pkg-config", "--exists", "hwloc"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    return any(Path(p).exists() for p in _HWLOC_HEADER_SEARCH)


def _read_rocm_version(rocm_path: str) -> Optional[str]:
    """Read ROCm version from .info/version file under rocm_path."""
    for vfile in (".info/version", "version", "lib/rocm_version.h"):
        p = Path(rocm_path) / vfile
        if p.exists():
            try:
                text = p.read_text(errors="ignore").strip()
                m = re.search(r"(\d+\.\d+[\.\d]*)", text)
                if m:
                    return m.group(1)
            except OSError:
                pass
    return None


def _detect_rocm_from_scripts(source_dir: Path) -> Dict[str, Any]:
    """Scan app install/job scripts for 'module load rocm/X.Y.Z' patterns.

    Covers HPC systems (like Tuolumne) where ROCm is environment-module managed
    and not present at standard /opt/rocm paths until the module is loaded.
    Looks in *.sh, *.job, *.slurm, *.lsf, *.bsub files under the source tree.

    Returns dict with keys: found, path, version, source
    """
    script_extensions = {".sh", ".job", ".slurm", ".lsf", ".bsub"}
    # ml / module load rocm/X.Y.Z
    rocm_module_re = re.compile(
        r"\bml\b.*\brocm/([\d]+\.[\d]+\.[\d]+)\b"
        r"|\bmodule\s+load\b.*\brocm/([\d]+\.[\d]+\.[\d]+)\b",
        re.I,
    )
    # Also match bare rocm/X.Y.Z anywhere in a line (e.g. job submission comments)
    rocm_version_re = re.compile(r"\brocm/([\d]+\.[\d]+\.[\d]+)\b", re.I)

    best_version: Optional[str] = None
    best_source: Optional[str] = None

    script_files = [
        f for f in source_dir.rglob("*")
        if f.is_file() and f.suffix.lower() in script_extensions
    ]
    # Also look one level up (repo root scripts/ dir)
    for f in script_files:
        try:
            text = f.read_text(errors="ignore")
        except OSError:
            continue
        # Prefer explicit module-load lines
        m = rocm_module_re.search(text)
        if m:
            version = m.group(1) or m.group(2)
            best_version = version
            best_source = f"script:{f.name}"
            break
        # Fall back to any rocm/X.Y.Z mention
        m2 = rocm_version_re.search(text)
        if m2 and not best_version:
            best_version = m2.group(1)
            best_source = f"script:{f.name}"

    if not best_version:
        return {"found": False, "path": None, "version": None, "source": None}

    # Build the expected /opt/rocm-X.Y.Z path and check if it exists
    candidate_path = f"/opt/rocm-{best_version}"
    rocm_path = candidate_path if Path(candidate_path).exists() else None
    # If versioned path missing, try plain /opt/rocm
    if not rocm_path and Path("/opt/rocm").exists():
        rocm_path = "/opt/rocm"

    return {
        "found": True,
        "path": rocm_path,
        "version": best_version,
        "source": best_source,
    }


def _detect_rocm(source_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Detect ROCm (AMD GPU) stack on the system.

    Checks (in order):
    1. App install/job scripts for 'module load rocm/X.Y.Z' (HPC module systems)
    2. ROCM_PATH / ROCM_HOME / HIP_PATH environment variables
    3. /opt/rocm-X.Y.Z and /opt/rocm directory existence
    4. hipcc compiler on PATH
    5. rocm-smi / rocminfo tool on PATH

    Pass source_dir to enable script scanning (step 1), which is the most
    reliable strategy on HPC systems like Tuolumne where ROCm is module-managed.

    Returns dict with keys:
        found (bool), path (str or None), version (str or None), source (str or None)
    """
    import os as _os

    # 1. App scripts (most reliable on HPC module-managed systems)
    if source_dir is not None:
        script_result = _detect_rocm_from_scripts(source_dir)
        if script_result["found"]:
            return script_result

    # 2. Environment variables
    for env_var in ("ROCM_PATH", "ROCM_HOME", "HIP_PATH"):
        val = _os.environ.get(env_var)
        if val and Path(val).exists():
            version = _read_rocm_version(val)
            return {"found": True, "path": val, "version": version, "source": f"env:{env_var}"}

    # 3. Versioned and default /opt/rocm paths
    rocm_candidates = ["/opt/rocm"]
    # Also scan /opt/ for any rocm-X.Y.Z directories
    opt = Path("/opt")
    if opt.exists():
        rocm_candidates += sorted(
            str(p) for p in opt.iterdir()
            if p.is_dir() and p.name.startswith("rocm-")
        )
    for candidate in rocm_candidates:
        if Path(candidate).exists():
            version = _read_rocm_version(candidate)
            return {"found": True, "path": candidate, "version": version, "source": f"path:{candidate}"}

    # 4. hipcc on PATH
    hipcc = shutil.which("hipcc")
    if hipcc:
        rocm_root = str(Path(hipcc).parent.parent)
        version = _read_rocm_version(rocm_root)
        return {"found": True, "path": rocm_root, "version": version, "source": "hipcc"}

    # 5. rocm-smi / rocminfo
    if shutil.which("rocm-smi") or shutil.which("rocminfo"):
        return {"found": True, "path": None, "version": None, "source": "rocm-smi"}

    return {"found": False, "path": None, "version": None, "source": None}


def _detect_ml_frameworks(all_text: str) -> Dict[str, Any]:
    """Detect ML/AI frameworks from source text patterns.

    Returns dict with bool flag per framework and a list of detected frameworks.
    """
    frameworks: List[str] = []
    details: Dict[str, bool] = {}

    patterns = [
        ("pytorch",     r"import torch\b|from torch\b|torch\.nn|torch\.optim|torch\.cuda|torch\.distributed"),
        ("tensorflow",  r"import tensorflow|from tensorflow|tf\.keras|tf\.data|tf\.distribute"),
        ("jax",         r"import jax\b|from jax\b|jax\.numpy|jax\.grad|jax\.jit"),
        ("keras",       r"import keras\b|from keras\b|keras\.layers|keras\.Model"),
        ("flax",        r"import flax\b|from flax\b|flax\.linen"),
        ("mxnet",       r"import mxnet|from mxnet\b|mx\.nd\b"),
        ("horovod",     r"import horovod|from horovod\b|hvd\.init\(\)"),
        ("deepspeed",   r"import deepspeed\b|from deepspeed\b|deepspeed\.initialize"),
        ("megatron",    r"import megatron|from megatron\b"),
        ("fsdp",        r"FullyShardedDataParallel|from torch\.distributed\.fsdp"),
        ("dali",        r"nvidia\.dali|from nvidia\.dali\b"),
        ("dlio",        r"from dlio_benchmark|import dlio_benchmark|dlio_benchmark"),
        ("lightning",   r"import lightning|from lightning\b|pytorch_lightning|from pytorch_lightning"),
    ]

    for name, pattern in patterns:
        found = bool(re.search(pattern, all_text, re.I | re.MULTILINE))
        details[name] = found
        if found:
            frameworks.append(name)

    return {"frameworks": frameworks, "details": details}


#: ML/DL framework names that indicate a deep-learning training workload.
#: When any of these are detected, dfanalyzer should use ``analyzer/preset=dlio``
#: instead of the generic ``posix`` preset.  The ``dlio`` preset understands
#: epoch/fetch_data/data_loader/checkpoint/compute layers natively and produces
#: semantically richer bottleneck labels for DL workloads.
_DL_FRAMEWORK_NAMES = frozenset({
    "pytorch", "tensorflow", "jax", "keras", "flax", "mxnet",
    "horovod", "deepspeed", "megatron", "fsdp", "dali", "dlio", "lightning",
})


def _detect_analyzer_preset(detect_info: Dict[str, Any]) -> str:
    """Return the dfanalyzer preset name appropriate for the detected workload.

    Uses the ML framework list from ``_detect_info`` output.  If any known
    deep-learning framework is present the ``dlio`` preset is returned;
    otherwise ``posix`` is returned for generic POSIX I/O workloads.

    Args:
        detect_info: Dict returned by :func:`_detect_info`.

    Returns:
        ``"dlio"`` for deep-learning workloads, ``"posix"`` otherwise.
    """
    frameworks = set(detect_info.get("ml_frameworks_list", []))
    if frameworks & _DL_FRAMEWORK_NAMES:
        return "dlio"
    return "posix"


def _mpi_prefix_to_wrappers(mpi_prefix: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Return ``(mpicc, mpicxx)`` under ``<mpi_prefix>/bin`` if they exist."""
    if not mpi_prefix:
        return None, None
    b = Path(mpi_prefix) / "bin"
    mpicc = next((str(b / n) for n in ("mpicc", "mpcc") if (b / n).exists()), None)
    mpicxx = next((str(b / n) for n in ("mpicxx", "mpic++", "mpiCC") if (b / n).exists()), None)
    return mpicc, mpicxx


def _detect_info(
    source_dir: Path,
    hdf5_prefix: Optional[str] = None,
    mpi_prefix: Optional[str] = None,
    mpicc: Optional[str] = None,
    mpicxx: Optional[str] = None,
) -> Dict[str, Any]:
    """Scan a source tree and return a comprehensive analysis dict.

    *hdf5_prefix* pins the HDF5 install to probe (e.g. a source-built HDF5 in
    the session workspace) so dftracer links the SAME HDF5 as the app rather
    than a stray system one. *mpi_prefix* (or explicit *mpicc*/*mpicxx*) pins
    the MPI wrappers the app was built with; the C probe compiles against them
    to read the true vendor/version.

    Walks *source_dir* recursively to collect file names and suffixes, then
    uses them to infer:

    - **Languages** present (C, C++, Python, Fortran) from file extensions.
    - **Build tool** (CMake, Autotools, Meson, Python packaging, or bare Make)
      from the presence of canonical build-system files.
    - **Optional features** (MPI, HDF5, POSIX I/O, OpenMP) from regex patterns
      in source text, capped at 5 MB of combined text to bound scan time.
    - **dftracer CMake flags** recommended for the detected feature set,
      consistent with dftracer's cmake build flags.
    - **Key files** (build descriptors, READMEs, INSTALL guides) present at
      the tree root.
    - **README excerpt** — the first 6 000 characters of whichever README
      variant is found first, for display to the user.

    Note:
        Autotools projects may contain Python helper scripts in their tree.
        These are filtered out of the ``languages`` list for Autotools projects
        because they are build infrastructure, not project source language.
        Python packaging projects (``setup.py``, ``pyproject.toml``) that also
        contain C/C++ extension code keep all detected languages.

    Args:
        source_dir: Absolute path to the root of the application source tree.
            The directory must already exist and be readable.

    Returns:
        Dict[str, Any]: A dict with the following keys:

            - ``languages`` (List[str]): Detected source languages, e.g.
              ``["c", "cpp", "python"]``.  Order reflects discovery order, not
              priority.
            - ``build_tool`` (str): One of ``"cmake"``, ``"autotools"``,
              ``"meson"``, ``"python"``, ``"make"``, or ``"unknown"``.
            - ``features`` (Dict[str, Any]): Feature flags and HDF5 probe
              results.  Keys: ``"mpi"`` (bool), ``"python"`` (bool),
              ``"hdf5"`` (bool), ``"hdf5_in_source"`` (bool),
              ``"hdf5_system"`` (dict from :func:`_detect_system_hdf5`),
              ``"posix_io"`` (bool), ``"openmp"`` (bool).
            - ``dftracer_cmake_flags`` (List[str]): CMake ``-D`` flags to pass
              when building dftracer for this project.  Always contains
              ``"-DDFTRACER_ENABLE_TESTS=OFF"``; additional flags are appended
              based on detected features.
            - ``key_files`` (List[str]): Sorted list of notable file names
              found at the source tree root (build descriptors and docs).
            - ``readme_excerpt`` (str or None): First 6 000 characters of the
              README, or ``None`` if no README was found.
    """
    files = [f for f in source_dir.rglob("*") if f.is_file()]
    names = {f.name for f in files}
    suffixes = {f.suffix.lower() for f in files}

    languages: List[str] = []
    if suffixes & {".c", ".h"}:
        languages.append("c")
    if suffixes & {".cpp", ".cxx", ".cc", ".hpp", ".hxx"}:
        languages.append("cpp")
    if ".py" in suffixes:
        languages.append("python")
    if suffixes & {".f90", ".f95", ".f03", ".f", ".for", ".f77"}:
        languages.append("fortran")

    if "CMakeLists.txt" in names:
        build_tool = "cmake"
    elif "configure.ac" in names or "configure.in" in names:
        build_tool = "autotools"
    elif "meson.build" in names:
        build_tool = "meson"
    elif names & {"setup.py", "pyproject.toml", "setup.cfg"}:
        build_tool = "python"
    elif names & {"Makefile", "makefile", "GNUmakefile"}:
        build_tool = "make"
    else:
        build_tool = "unknown"

    # Autotools is a C/C++ build system; .py files are build scripts, not project sources.
    # Python projects (setup.py/pyproject.toml) can contain C/C++ extensions — keep all.
    if build_tool == "autotools":
        languages = [l for l in languages if l != "python"]

    # Source scan for feature detection (cap at 5 MB to avoid huge repos)
    all_text = ""
    scannable = {".c", ".h", ".cpp", ".cxx", ".cc", ".hpp", ".py", ".f90", ".f"}
    for f in files:
        if f.suffix.lower() in scannable:
            try:
                all_text += f.read_text(errors="ignore")
                if len(all_text) > 5_000_000:
                    break
            except OSError:
                pass

    hdf5_in_source = bool(re.search(r"hdf5\.h|H5Fopen|H5Fcreate|H5Dread|H5Dwrite|h5py", all_text, re.I))
    hdf5_system = _detect_system_hdf5(hdf5_prefix)
    hwloc_found = _detect_system_hwloc()

    features = {
        "mpi":      bool(re.search(r"mpi\.h|MPI_Init|MPI_Comm|mpi4py", all_text, re.I)),
        "python":   "python" in languages,
        "hdf5":     hdf5_in_source or hdf5_system["found"],
        "hdf5_in_source": hdf5_in_source,
        "hdf5_system":    hdf5_system,
        "hip":      bool(re.search(
            r"hip/hip_runtime\.h|hipMalloc|hipMemcpy|hipLaunchKernelGGL|#include\s+[<\"]hip/",
            all_text, re.I,
        )),
        "hwloc":    hwloc_found,
        "posix_io": bool(re.search(r"\bopen\s*\(|\bfopen\s*\(|\bread\s*\(|\bwrite\s*\(", all_text)),
        "openmp":   bool(re.search(r"omp\.h|#pragma omp|import openmp", all_text, re.I)),
    }

    # Detect ROCm: scan app scripts first (HPC module-managed), then system paths
    rocm_info = _detect_rocm(source_dir=source_dir)
    ml_frameworks = _detect_ml_frameworks(all_text)

    # HIP tracing: enable when either HIP source code patterns found OR ROCm stack present
    hip_tracing_needed = features["hip"] or rocm_info["found"]
    features["rocm"] = rocm_info
    features["ml_frameworks"] = ml_frameworks["frameworks"]
    features["ml_framework_details"] = ml_frameworks["details"]
    features["hip_tracing_needed"] = hip_tracing_needed

    # Detect MPI implementation via compiled C probe so we get the exact
    # vendor string and can pass explicit cmake compiler hints.
    mpi_impl_info: Dict[str, Any] = {}
    if features["mpi"]:
        _mpicc_ov = mpicc or _mpi_prefix_to_wrappers(mpi_prefix)[0]
        _mpicxx_ov = mpicxx or _mpi_prefix_to_wrappers(mpi_prefix)[1]
        mpi_impl_info = _detect_mpi_impl(_mpicc_ov, _mpicxx_ov)

    # For MPI+HDF5 combinations prefer the parallel (MPI-enabled) HDF5 build —
    # unless the caller pinned an explicit hdf5_prefix, which always wins.
    parallel_hdf5: Optional[Dict[str, Any]] = None
    if features["mpi"] and features["hdf5"] and not hdf5_prefix:
        parallel_hdf5 = _detect_parallel_hdf5()

    # Resolve the HDF5 root to use:
    # explicit hdf5_prefix > parallel variant > serial system prefix
    hdf5_root: Optional[str] = None
    if hdf5_prefix:
        hdf5_root = hdf5_prefix
    elif parallel_hdf5:
        hdf5_root = parallel_hdf5["prefix"]
    elif hdf5_system.get("prefix"):
        hdf5_root = hdf5_system["prefix"]

    # Complete pip env dict covering every setup.py-supported env var.
    # JOBS / CMAKE_BUILD_PARALLEL_LEVEL are set at install time (jobs param).
    dftracer_pip_env: Dict[str, str] = {
        "DFTRACER_BUILD_TYPE":                   "RelWithDebInfo",
        "DFTRACER_ENABLE_TESTS":                 "OFF",
        "DFTRACER_ENABLE_DLIO_BENCHMARK_TESTS":  "OFF",
        "DFTRACER_ENABLE_PAPER_TESTS":           "OFF",
    }
    if features["mpi"]:
        dftracer_pip_env["DFTRACER_ENABLE_MPI"] = "ON"
        # MPICC/MPICXX env vars tell cmake's FindMPI which wrapper to interrogate.
        # brahma v1.0.6 detects the MPI implementation by checking whether
        # MPI_C_COMPILER's path contains "openmpi" / "mpich" etc.  Using the
        # implementation-branded wrapper (mpicc.openmpi) ensures that check passes
        # and BRAHMA_MPI_IMPL_OPENMPI gets defined, which gates the MPI_File_*
        # virtual function overrides.
        mpicc_path = mpi_impl_info.get("mpicc")
        mpicxx_path = mpi_impl_info.get("mpicxx")
        if mpicc_path:
            dftracer_pip_env["MPICC"] = mpicc_path
        if mpicxx_path:
            dftracer_pip_env["MPICXX"] = mpicxx_path
    if features["hdf5"]:
        dftracer_pip_env["DFTRACER_ENABLE_HDF5"] = "ON"
        if hdf5_root:
            dftracer_pip_env["HDF5_ROOT"] = hdf5_root
            dftracer_pip_env["HDF5_DIR"] = hdf5_root
    if hip_tracing_needed:
        dftracer_pip_env["DFTRACER_ENABLE_HIP_TRACING"] = "ON"
    if hwloc_found:
        dftracer_pip_env["DFTRACER_DISABLE_HWLOC"] = "OFF"

    # Build CMAKE_ARGS with explicit compiler and path hints so that cmake's
    # FindMPI and FindHDF5 don't auto-detect the wrong variant (e.g. picking
    # the MPI standard version instead of the vendor version for brahma).
    cmake_args_parts: List[str] = ["-DDFTRACER_ENABLE_TESTS=OFF"]
    if features["python"]:
        cmake_args_parts.append("-DDFTRACER_ENABLE_PYTHON=ON")
    if features["mpi"]:
        cmake_args_parts.append("-DDFTRACER_ENABLE_MPI=ON")
        for flag in mpi_impl_info.get("cmake_flags", []):
            cmake_args_parts.append(flag)
    if features["hdf5"]:
        cmake_args_parts.append("-DDFTRACER_ENABLE_HDF5=ON")
        if hdf5_root:
            cmake_args_parts.append(f"-DHDF5_ROOT={hdf5_root}")
            # Prefer parallel HDF5 so brahma compiles against the parallel headers
            # (H5FDmpio / H5Pset_fapl_mpio) rather than a serial /usr/include one.
            cmake_args_parts.append("-DHDF5_PREFER_PARALLEL=ON")
    if hip_tracing_needed:
        cmake_args_parts.append("-DDFTRACER_ENABLE_HIP_TRACING=ON")
    if hwloc_found:
        cmake_args_parts.append("-DDFTRACER_DISABLE_HWLOC=OFF")

    # Set DFTRACER_CMAKE_ARGS env var — consumed by dftracer's setup.py.
    # MUST be SPACE-separated: the default install ref (develop) splits this on
    # whitespace and passes each token as its own cmake -D.  A ";"-joined string is
    # passed as ONE argument, so cmake parses e.g.
    #   -DDFTRACER_ENABLE_TESTS=OFF;-DDFTRACER_ENABLE_HDF5=ON;...
    # as DFTRACER_ENABLE_TESTS="OFF;-D...", a non-false list that RE-ENABLES tests
    # (and builds the HDF5-MPI test that fails to compile).  Space-join keeps each
    # -D clean.  Critical flags (HDF5_ROOT, MPICC/MPICXX, ENABLE_* ) are ALSO passed
    # as standalone env vars in install.py, so they hold even if a given dftracer
    # version parses DFTRACER_CMAKE_ARGS differently.
    dftracer_pip_env["DFTRACER_CMAKE_ARGS"] = " ".join(cmake_args_parts)

    # Map features → dftracer cmake flags (for cmake-based installs)
    dftracer_cmake_flags: List[str] = list(cmake_args_parts)

    key_files = sorted(n for n in names if n in {
        "CMakeLists.txt", "configure.ac", "setup.py", "pyproject.toml",
        "Makefile", "README.md", "README.rst", "README.txt", "INSTALL.md",
        "INSTALL", "meson.build",
    })

    # Look for readme
    readme_content = None
    for rname in ["README.md", "README.rst", "README.txt", "README", "INSTALL.md"]:
        rp = source_dir / rname
        if rp.exists():
            readme_content = rp.read_text(errors="ignore")[:6000]
            break

    return {
        "languages": languages,
        "build_tool": build_tool,
        "features": features,
        "mpi_impl": mpi_impl_info if features["mpi"] else {},
        "parallel_hdf5": parallel_hdf5,
        "dftracer_pip_env": dftracer_pip_env,
        "dftracer_cmake_flags": dftracer_cmake_flags,
        "key_files": key_files,
        "readme_excerpt": readme_content,
        "rocm_info": rocm_info,
        "ml_frameworks_list": ml_frameworks["frameworks"],
        "hip_tracing_needed": hip_tracing_needed,
    }
