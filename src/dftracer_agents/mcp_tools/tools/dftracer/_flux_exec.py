"""Shared helper: run heavy trace-processing commands on allocated compute
resources (via ``flux proxy <jobid> flux run``) instead of the MCP server's
own host, whenever the user already has a live Flux allocation.

Tools that scan/merge/compress/analyze large ``.pfw``/``.pfw.gz`` trace
directories are I/O- and CPU-heavy. The MCP server itself typically runs on
a shared login/service node, which is the wrong place to do that work when
the session already has real compute nodes allocated. This module detects
a live, running allocation owned by the current user and transparently wraps
the subprocess command to execute there instead — falling back to running
locally (unchanged behaviour) whenever no allocation is available, ``flux``
isn't installed, or wrapping fails for any reason. It never blocks or raises
on detection failure.

Agents that already know their session's allocation (e.g. read from session
state after ``flux alloc``) should pass ``allocation_id`` explicitly to
:func:`run_on_allocation` — this is the only way to target a *specific*
allocation when more than one is running under the same user, and skips the
``flux jobs`` auto-detection query entirely.

Opt out for a single call by setting ``DFTRACER_MCP_DISABLE_FLUX_WRAP=1`` in
the environment before invoking the MCP server.
"""

from __future__ import annotations

import functools
import os
import shutil
import subprocess
import time
from typing import List, Optional

_ALLOC_CACHE_TTL_S = 30.0
_alloc_cache: "tuple[float, Optional[str]]" = (0.0, None)


def _disabled() -> bool:
    return os.environ.get("DFTRACER_MCP_DISABLE_FLUX_WRAP", "") == "1"


def _already_inside_allocation() -> bool:
    """True if this process is itself running inside a Flux job shell.

    ``FLUX_JOB_ID`` is set by ``flux run``/``flux shell`` for the launched
    task itself; ``FLUX_URI`` alone is not a reliable signal since a login
    node's default system instance also exports it.
    """
    return bool(os.environ.get("FLUX_JOB_ID"))


@functools.lru_cache(maxsize=1)
def _flux_bin() -> Optional[str]:
    return shutil.which("flux")


def _detect_running_allocation(flux_bin: str) -> Optional[str]:
    """Return the jobid of the current user's most recent RUNNING flux job.

    Best-effort: any error (flux not usable, no jobs, malformed output)
    returns ``None`` so callers fall back to local execution.
    """
    try:
        result = subprocess.run(
            [flux_bin, "jobs", "-a", "--no-header",
             "-o", "{id} {state} {t_remaining}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            return None
        best_id: Optional[str] = None
        best_remaining = -1.0
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            jobid, state = parts[0], parts[1]
            if state.upper() not in ("R", "RUN", "RUNNING"):
                continue
            remaining = 0.0
            if len(parts) >= 3:
                try:
                    remaining = float(parts[2])
                except ValueError:
                    remaining = 0.0
            # Prefer the allocation with the most walltime left, so a
            # long-lived session allocation wins over a job that's about
            # to expire.
            if remaining >= best_remaining:
                best_remaining = remaining
                best_id = jobid
        return best_id
    except Exception:
        return None


def find_active_allocation() -> Optional[str]:
    """Return a usable Flux jobid to proxy into, or ``None``.

    Cached for :data:`_ALLOC_CACHE_TTL_S` seconds so a burst of tool calls
    (e.g. several trace-processing calls in a row) doesn't re-run ``flux
    jobs`` for every single one.
    """
    global _alloc_cache
    if _disabled() or _already_inside_allocation():
        return None
    flux_bin = _flux_bin()
    if not flux_bin:
        return None
    now = time.monotonic()
    cached_at, cached_id = _alloc_cache
    if now - cached_at < _ALLOC_CACHE_TTL_S:
        return cached_id
    jobid = _detect_running_allocation(flux_bin)
    _alloc_cache = (now, jobid)
    return jobid


def wrap_for_allocation(
    cmd: List[str],
    *,
    allocation_id: Optional[str] = None,
    nnodes: int = 1,
    ntasks: int = 1,
) -> List[str]:
    """Wrap *cmd* to run under ``flux proxy <jobid> flux run`` if a live
    allocation is available; otherwise return *cmd* unchanged.

    Args:
        allocation_id: Explicit Flux jobid to proxy into. Callers (agents)
            that already know their session's allocation should pass it
            here — this skips the ``flux jobs`` auto-detection query and
            its cache entirely, and is the only way to target a specific
            allocation when more than one is running under the same user.
            When omitted/``None``, falls back to auto-detecting the most
            recent RUNNING job owned by the current user.

    Only ever prepends to the command — never mutates *cmd*'s own
    arguments — so this is safe to apply unconditionally at each
    subprocess call site for large-trace tools.
    """
    jobid = allocation_id or find_active_allocation()
    if not jobid:
        return cmd
    flux_bin = _flux_bin()
    if not flux_bin:
        return cmd
    return [
        flux_bin, "proxy", jobid,
        flux_bin, "run", "-N", str(nnodes), "-n", str(ntasks), "--exclusive",
        *cmd,
    ]


def run_on_allocation(
    cmd: List[str],
    *,
    allocation_id: Optional[str] = None,
    nnodes: int = 1,
    ntasks: int = 1,
    **subprocess_kwargs,
) -> subprocess.CompletedProcess:
    """``subprocess.run`` *cmd*, transparently wrapped onto an active Flux
    allocation when one is available.

    Args:
        allocation_id: Explicit Flux jobid to proxy into (see
            :func:`wrap_for_allocation`). Pass this whenever the caller
            already knows the session's allocation — e.g. an agent that
            read it from session state — rather than relying on
            auto-detection, which only works when exactly one relevant
            allocation is running under the current user.

    Signature/behaviour otherwise identical to :func:`subprocess.run` — same
    ``capture_output``/``text``/``timeout``/``check`` kwargs are honoured.
    If the wrapped invocation fails to even start (e.g. the allocation died
    between detection and launch), falls back to running *cmd* locally once,
    so a stale/expired allocation never turns a working tool into a hard
    failure.
    """
    wrapped = wrap_for_allocation(
        cmd, allocation_id=allocation_id, nnodes=nnodes, ntasks=ntasks
    )
    if wrapped is cmd:
        return subprocess.run(cmd, **subprocess_kwargs)
    try:
        return subprocess.run(wrapped, **subprocess_kwargs)
    except (FileNotFoundError, OSError):
        return subprocess.run(cmd, **subprocess_kwargs)
