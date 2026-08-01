#!/usr/bin/env python3
"""Agent-trace MCP service — deterministic relationships from agent traces.

Agent harnesses (Codex, Claude Code, ...) can be instrumented with dftracer so
that every tool call, file access, and message becomes a trace event.  Once the
trace exists, the interesting questions are *relational*:

* which files did the agent touch, and from which processes?
* which files are shared across processes / turns (the hand-off points)?
* which tools produced or consumed a given file?

Historically those links were inferred by asking an LLM to read the transcript
and guess which files a tool had touched.  They do not need to be guessed, and
they do not need to be re-described by the harness either: when an agent's tool
calls run under dftracer's ``LD_PRELOAD`` interception, the trace already holds
every file each process actually opened, recorded at the syscall level.

Each such event carries an ``fhash`` argument, which dftracer's indexer surfaces
as the ``file_hash`` aggregation column.  Grouping on it yields the
file/process/tool relationships *exactly*, from the index, with no model in the
loop.  The tools here expose that grouping; an LLM is then only asked to narrate
groups it has been handed, never to derive them.

Two hash spaces
---------------
The same file can be recorded under two different hashes, so
:func:`_resolve_file_names` reconciles them and grouping happens on the resolved
path:

* ``POSIX`` events from the preload carry dftracer's own hash, with the path
  published separately in an ``FH`` metadata record.
* ``agent_io`` events, if a harness emits any, carry ``args.fname`` inline
  alongside ``args.fhash``.

Requires ``DFTRACER_INC_METADATA=1`` on the traced process, otherwise the core
drops all ``args`` and no ``fhash`` reaches the trace at all.
"""

from __future__ import annotations

import contextlib
import glob
import hashlib
import json
import logging
import os
import time
from typing import Any, Dict, Iterator, List, Optional, Tuple

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService

# Categories that describe a file interaction. Everything else (messages,
# dftracer's own bookkeeping) is excluded from file grouping by default.
#
# ``agent_io`` events are emitted by the harness when a tool names a file;
# ``POSIX`` events come from dftracer's LD_PRELOAD interception of the real
# syscalls the tool made. Both are included so that a tool call and the actual
# I/O it caused land in the same group.
DEFAULT_FILE_CATEGORIES = ("agent_io", "POSIX")

# dftracer's own instrumentation category; never an agent action.
INTERNAL_CATEGORY = "dftracer"


#: Progress for the trace pipeline. Named so a consumer can raise it on its own
#: — the dashboard turns it on with the rest of its own logging.
_log = logging.getLogger("dftracer_agents.agent_trace")


def _normalize_category(value: Any) -> str:
    """Normalize a category for comparison.

    The aggregation tier lower-cases categories (``POSIX`` becomes ``posix``)
    while raw events preserve the original spelling, so every comparison has to
    be case-insensitive.
    """
    return str(value or "").strip().lower()


def file_hash(path: str) -> str:
    """Return the ``fhash`` an emitter should attach for ``path``.

    Kept here so the emitter and the reader cannot drift: whatever hashes a
    path on the way into the trace must match what resolves it on the way out.

    Args:
        path: Filesystem path. Normalized to an absolute path first, so that
            the same file referenced relatively and absolutely groups together.

    Returns:
        16-character hex digest used as the event's ``fhash`` argument.
    """
    return hashlib.blake2b(os.path.abspath(path).encode("utf-8"), digest_size=8).hexdigest()


#: A traced session's trace directory is split by lifecycle, so that compaction
#: never has to guess whether a file is still being written:
#:
#:   <trace_dir>/live/     an agent is still running and writing here
#:   <trace_dir>/done/     the wrapper moved the agent's traces here when it exited
#:   <trace_dir>/compact/  compacted output, built only from done/
#:
#: Each agent's files are named with its agent id, which is what lets the
#: wrapper move exactly that agent's traces and leave everyone else's alone.
LIVE_SUBDIR = "live"
DONE_SUBDIR = "done"
COMPACT_SUBDIR = "compact"

#: Where a finished session's compacted chunks are recomposed into larger ones.
#:
#: Compaction during a session runs often and on small inputs, because the point
#: is that the dashboard sees a checkpoint soon after the work happens. That
#: leaves a session with many small chunks — good for watching it run, poor for
#: reading it afterwards. Once nothing is writing, those are merged into fewer,
#: larger files here. Still per agent: the agent id lives in the filename and
#: nowhere in the events, so merging across agents would destroy the only link
#: between a trace and the tool call that made it.
FINAL_SUBDIR = "final"

#: Raw traces are moved here once compacted. They are kept rather than deleted
#: so the compaction stays auditable, but they must leave ``done/`` because the
#: index store is per-directory: analysis aggregates whole directories, so a
#: directory holding both a compacted trace and its source would count every
#: event twice.
ARCHIVE_SUBDIR = "archive"

#: Records which done/ traces have already been folded into the compacted copy,
#: so compaction can run repeatedly as agents finish without redoing work or
#: double-counting events.
COMPACT_MANIFEST = "compacted.json"


def _split_binary() -> str:
    """Locate the ``dftracer_split`` executable.

    It ships in the interpreter's ``bin`` directory, which is frequently not on
    ``PATH`` when the caller is a virtualenv Python invoked by absolute path.

    Returns:
        Path to the binary, or the bare name as a last resort.
    """
    import shutil
    import sys

    found = shutil.which("dftracer_split")
    if found:
        return found

    candidates = [os.path.join(os.path.dirname(sys.executable), "dftracer_split")]
    try:
        import dftracer

        candidates.append(
            os.path.join(os.path.dirname(os.path.abspath(dftracer.__file__)), "bin", "dftracer_split")
        )
    except Exception:  # pragma: no cover - dftracer always present in practice
        pass

    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return "dftracer_split"


def _trace_files(trace_dir: str) -> List[str]:
    """List the trace files directly under ``trace_dir``.

    Not recursive, so the lifecycle subdirectories are never mixed together.
    """
    return sorted(
        glob.glob(os.path.join(trace_dir, "*.pfw.gz"))
        + glob.glob(os.path.join(trace_dir, "*.pfw"))
    )


def live_files(trace_dir: str) -> List[str]:
    """Traces belonging to agents that are still running."""
    return _trace_files(os.path.join(trace_dir, LIVE_SUBDIR))


def done_files(trace_dir: str) -> List[str]:
    """Traces belonging to agents that have finished.

    An agent's traces arrive here only once its wrapper has exited, so anything
    in this directory is complete and safe to compact.
    """
    return _trace_files(os.path.join(trace_dir, DONE_SUBDIR))


def compacted_files(trace_dir: str) -> List[str]:
    """Compacted traces built from ``done/``."""
    return _trace_files(os.path.join(trace_dir, COMPACT_SUBDIR))


def final_files(trace_dir: str) -> List[str]:
    """Traces recomposed after the session finished."""
    return _trace_files(os.path.join(trace_dir, FINAL_SUBDIR))


def _legacy_files(trace_dir: str) -> List[str]:
    """Traces sitting loose in the trace directory.

    Written either by an older layout or by a process that bypassed the
    wrapper. They are readable, but their lifecycle is unknown, so compaction
    leaves them alone unless explicitly told otherwise.
    """
    return _trace_files(trace_dir)


def _executable(name: str) -> str:
    """Find a dftracer tool, on PATH or beside this interpreter."""
    import shutil
    import sys

    found = shutil.which(name)
    if found:
        return found
    candidate = os.path.join(os.path.dirname(sys.executable), name)
    return candidate if os.path.isfile(candidate) else name


def _count_binary() -> str:
    """Locate the ``dftracer_event_count`` executable."""
    import shutil
    import sys

    found = shutil.which("dftracer_event_count")
    if found:
        return found
    candidate = os.path.join(os.path.dirname(sys.executable), "dftracer_event_count")
    return candidate if os.path.isfile(candidate) else "dftracer_event_count"


def _count_events(directory: str) -> Optional[int]:
    """Count the events in a directory of traces.

    Used to prove compaction is lossless: the compacted output must hold exactly
    as many events as the raw traces it replaced.

    Args:
        directory: Directory of ``.pfw``/``.pfw.gz`` files.

    Returns:
        The event count, or ``None`` when it could not be determined. The
        counter reports gzip estimates with a leading ``~``, which is stripped.
    """
    import subprocess

    try:
        result = subprocess.run(
            [_count_binary(), "-d", directory], capture_output=True, text=True, timeout=300
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    for line in reversed(result.stdout.strip().splitlines()):
        token = line.strip().lstrip("~").replace(",", "")
        if token.isdigit():
            return int(token)
    return None


#: What ``dftracer_info`` reports, and the key each line becomes.
_INFO_FIELDS = {
    "Total Files": "files",
    "Total Lines": "lines",
    "Valid Events": "events",
    "Total Uncompressed": "uncompressed_bytes",
}


def trace_info(directory: str) -> Dict[str, int]:
    """Summarize a directory of traces: how many events, and how big uncompressed.

    Traces are gzipped, so their size on disk says little about the work behind
    them — 14 MB on disk is 315 MB of events. ``dftracer_info`` reads the gzip
    trailers and the index rather than the events, so it answers for nearly two
    million events in a couple of seconds.

    Args:
        directory: Directory of ``.pfw``/``.pfw.gz`` files.

    Returns:
        ``files``, ``lines``, ``events`` and ``uncompressed_bytes``; missing
        keys mean the tool could not answer, which is not an error here.
    """
    import re
    import subprocess

    from .dftracer_utils_service import info_command

    # The shared builder knows the flags; it names the binary bare, because the
    # MCP tool runs it through a shell that has the environment. Called
    # directly there is no such shell, so the path is resolved here.
    command = info_command(directory=directory)
    command[0] = _executable(command[0])

    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError):
        return {}
    if result.returncode != 0:
        return {}

    found: Dict[str, int] = {}
    for line in result.stdout.splitlines():
        label, _, value = line.partition(":")
        key = _INFO_FIELDS.get(label.strip())
        if not key or not value.strip():
            continue
        # Sizes are printed as "315.12 MB (330423609 bytes)"; the exact figure
        # is the one in parentheses.
        exact = re.search(r"\((\d+) bytes\)", value)
        if exact:
            found[key] = int(exact.group(1))
            continue
        digits = value.strip().replace(",", "")
        if digits.isdigit():
            found[key] = int(digits)
    return found


def _clear_index(directory: str) -> None:
    """Drop a directory's ``.dftindex`` store.

    The store is cumulative: once a directory's contents change — files
    compacted away, new ones moved in — a store built earlier keeps reporting
    the old set alongside the new, double-counting events. Removing it after
    every change forces a clean rebuild on the next query.

    Held under the same cross-process lock readers take, so a store is never
    removed while one is opening it.

    Args:
        directory: Directory whose index store should be discarded.
    """
    import shutil

    with _index_guard(directory):
        shutil.rmtree(os.path.join(directory, ".dftindex"), ignore_errors=True)


def _clear_session_indexes(trace_dir: str) -> None:
    """Drop the index stores of every lifecycle directory in a session."""
    for name in ("", COMPACT_SUBDIR, DONE_SUBDIR, LIVE_SUBDIR, ARCHIVE_SUBDIR):
        _clear_index(os.path.join(trace_dir, name) if name else trace_dir)


def _manifest_path(trace_dir: str) -> str:
    """Path of the compaction manifest for ``trace_dir``."""
    return os.path.join(trace_dir, COMPACT_SUBDIR, COMPACT_MANIFEST)


def _read_manifest(trace_dir: str) -> Dict[str, Any]:
    """Read the compaction manifest, tolerating absence or corruption."""
    path = _manifest_path(trace_dir)
    if not os.path.exists(path):
        return {"sources": [], "batches": []}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {"sources": [], "batches": []}
    if not isinstance(data, dict):
        return {"sources": [], "batches": []}
    data.setdefault("sources", [])
    data.setdefault("batches", [])
    return data


def _write_manifest(trace_dir: str, manifest: Dict[str, Any]) -> None:
    """Write the compaction manifest atomically."""
    path = _manifest_path(trace_dir)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp = f"{path}.tmp"
    with open(temp, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)
    os.replace(temp, path)


def _pending_files(trace_dir: str) -> List[str]:
    """Return finished traces not yet folded into the compacted copy.

    Compaction moves what it consumes into ``archive/``, so everything left in
    ``done/`` is by definition still pending.
    """
    return done_files(trace_dir)


def _agent_id_for(path: str) -> str:
    """Recover the agent id a trace file belongs to.

    The wrapper names each agent's log file after its agent id, and dftracer
    appends its own per-process suffix, so the id is the leading segment.

    Args:
        path: Trace file path.

    Returns:
        The agent id, or the bare filename when it carries no suffix.
    """
    name = os.path.basename(path)
    for suffix in (".pfw.gz", ".pfw"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
            break
    head = name.split("-", 1)[0] if "-" in name else name
    # Compaction names its output "<agent>.<batch>", so drop a trailing batch
    # number; otherwise a compacted trace reports a different agent than the raw
    # one it was built from, and attribution silently breaks after compaction.
    agent, _, batch = head.rpartition(".")
    return agent if agent and batch.isdigit() else head


def _analysis_dirs(trace_dir: str, prefer_compacted: bool = False) -> List[str]:
    """Return the directories analysis should aggregate over.

    A session's events are spread across three places as compaction proceeds:

    * ``compact/`` — finished agents already folded together,
    * ``done/`` — finished agents not yet compacted,
    * ``live/`` — agents still running, included so live analysis is current,

    plus any legacy traces sitting loose in ``trace_dir``. Compaction moves the
    traces it consumes into ``archive/`` (never read here), which is what keeps
    these sets disjoint and every event counted exactly once.

    Directories rather than files, because dftracer's index store is per
    directory and its aggregation reads the whole store regardless of which
    files were requested.

    Args:
        trace_dir: Directory holding a session's traces.
        prefer_compacted: Skip ``done/`` and read the compacted copy instead.
            Set this when a compactor is running continuously: ``done/`` is then
            a staging area being emptied in the background, and reading it races
            with the move into ``archive/``. The cost is freshness — a call that
            has finished but not yet been compacted is not visible until it is.

    Returns:
        Existing directories that contain traces.
    """
    final_dir = os.path.join(trace_dir, FINAL_SUBDIR)
    compact_dir = os.path.join(trace_dir, COMPACT_SUBDIR)
    skip_done = prefer_compacted and bool(_trace_files(compact_dir) or _trace_files(final_dir))

    # final/ first: it holds what compaction already consumed, so both being
    # read would count those events twice. Finalization archives what it
    # consumes, which is what keeps the two disjoint.
    candidates = [final_dir, compact_dir]
    if not skip_done:
        candidates.append(os.path.join(trace_dir, DONE_SUBDIR))
    candidates.extend([os.path.join(trace_dir, LIVE_SUBDIR), trace_dir])
    return [directory for directory in candidates if _trace_files(directory)]


def _analysis_files(trace_dir: str, prefer_compacted: bool = False) -> List[str]:
    """Return every trace file analysis reads, across all lifecycle stages."""
    return [
        path
        for directory in _analysis_dirs(trace_dir, prefer_compacted)
        for path in _trace_files(directory)
    ]


def _finalize_impl(
    trace_dir: str,
    chunk_size_mb: int = 64,
    force: bool = False,
) -> Dict[str, Any]:
    """Recompose a finished session's compacted chunks into larger ones.

    The second tier of compaction. The first runs while the session does, often
    and on small inputs, so the dashboard sees a checkpoint soon after the work
    happens — which leaves many small chunks. This merges them once nothing is
    writing.

    Per agent, like the first tier: the agent id is in the filename and nowhere
    in the events, so merging across agents would destroy the link between a
    trace and the tool call that made it.

    Runs while the session does. It consumes only the chunks it listed when it
    started, and the caller holds the compaction lock, so a compactor writing
    new ones alongside is not a race — which is what lets the second tier keep
    up rather than waiting for the end.

    Args:
        trace_dir: Directory holding the session's traces.
        chunk_size_mb: Target size of each recomposed chunk.
        force: Unused; kept so callers written against the earlier signature
            still work.

    Returns:
        What was consumed and produced, or why it was skipped.

    Raises:
        RuntimeError: When ``dftracer_split`` fails.
    """
    import shutil
    import subprocess
    import tempfile

    # Snapshot first. Anything the compactor adds after this belongs to the
    # next round, and will not be archived by this one.
    sources = compacted_files(trace_dir)
    if not sources:
        return {"trace_dir": trace_dir, "skipped": True, "reason": "nothing compacted to recompose"}

    # Counted before the indexes are dropped and before anything moves, so the
    # comparison at the end is against what actually went in.
    events_in = _count_events(os.path.join(trace_dir, COMPACT_SUBDIR))

    _clear_session_indexes(trace_dir)
    output_dir = os.path.join(trace_dir, FINAL_SUBDIR)

    by_agent: Dict[str, List[str]] = {}
    for path in sources:
        by_agent.setdefault(_agent_id_for(path), []).append(path)
    produced: List[str] = []
    for agent, paths in sorted(by_agent.items()):
        with tempfile.TemporaryDirectory(prefix="dft-final-") as staging:
            for path in paths:
                target = os.path.join(staging, os.path.basename(path))
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copy2(path, target)
            _clear_index(staging)

            command = [
                _split_binary(),
                "-d", staging,
                "-o", output_dir,
                "-n", agent,
                "-s", str(int(chunk_size_mb)),
                "--compress",
                "-f",
            ]
            result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(
                f"dftracer_split failed finalizing agent {agent!r} ({result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
        produced.append(agent)

    # Retire the sources, or their events would be counted twice: final/ and
    # compact/ are both read, and finalization is what makes them disjoint.
    archive_dir = os.path.join(trace_dir, ARCHIVE_SUBDIR)
    os.makedirs(archive_dir, exist_ok=True)
    archived = 0
    for path in sources:
        try:
            shutil.move(path, os.path.join(archive_dir, os.path.basename(path)))
            archived += 1
        except OSError:
            continue

    events_out = _count_events(output_dir)
    _clear_session_indexes(trace_dir)

    return {
        "trace_dir": trace_dir,
        "output_dir": output_dir,
        "agents": sorted(produced),
        "consumed": len(sources),
        "archived": archived,
        "final_files": len(final_files(trace_dir)),
        "events_in": events_in,
        "events_out": events_out,
        # Splitting rewrites each chunk with its own bookkeeping records, so the
        # output legitimately carries a few more. Losing events is the failure
        # worth catching — and a count that could not be taken is unknown, not
        # a pass: treating a failed count as zero made every run "verified".
        "verified": (
            None if events_in is None or events_out is None else events_out >= events_in
        ),
        "skipped": False,
    }


def _compact_impl(
    trace_dir: str,
    app_name: str = "agent",
    chunk_size_mb: int = 4,
    force: bool = False,
    include_legacy: bool = False,
) -> Dict[str, Any]:
    """Compact the traces of agents that have finished.

    Only ``done/`` is compacted. An agent's traces are moved there by its
    wrapper as the agent exits, so membership of that directory — not a
    timestamp heuristic — is what proves a trace is complete. Agents still
    running keep writing into ``live/`` and are untouched, which makes this safe
    to run repeatedly during a session.

    Args:
        trace_dir: Directory holding the session's traces.
        app_name: Prefix for the output files.
        chunk_size_mb: Target size of each output chunk.
        force: Discard the existing compacted copy and rebuild from all of
            ``done/``.
        include_legacy: Also compact traces sitting loose in ``trace_dir``,
            whose lifecycle is unknown. Only safe when nothing is writing them.

    Returns:
        What was consumed, what is still running, and the resulting file counts.

    Raises:
        RuntimeError: When ``dftracer_split`` fails.
    """
    import shutil
    import subprocess
    import tempfile

    # Always reindex around compaction. The index store is cumulative, so one
    # built before files moved between lifecycle directories would describe a
    # set that no longer exists.
    _clear_session_indexes(trace_dir)

    output_dir = os.path.join(trace_dir, COMPACT_SUBDIR)
    if force:
        shutil.rmtree(output_dir, ignore_errors=True)

    manifest = {"sources": [], "batches": []} if force else _read_manifest(trace_dir)
    consumed = set(manifest.get("sources", []))

    ready = _pending_files(trace_dir)
    if force:
        # Rebuilding from scratch has to pull back everything already retired,
        # or the rebuilt copy would be missing earlier batches.
        ready = ready + _trace_files(os.path.join(trace_dir, ARCHIVE_SUBDIR))
    if include_legacy:
        ready = ready + _legacy_files(trace_dir)

    running = live_files(trace_dir)
    if not ready:
        return {
            "trace_dir": trace_dir,
            "output_dir": output_dir,
            "consumed": 0,
            "still_running": len(running),
            "compacted_files": len(compacted_files(trace_dir)),
            "skipped": True,
            "reason": (
                f"no finished traces to compact ({len(running)} agent trace file(s) still live)"
                if running
                else "no finished traces to compact"
            ),
        }

    # Compact per agent, not per batch. The agent id lives in the trace
    # filename and nowhere inside the events, so merging several agents into one
    # output file would destroy the only link between a trace and the tool call
    # that produced it.
    by_agent: Dict[str, List[str]] = {}
    for path in ready:
        by_agent.setdefault(_agent_id_for(path), []).append(path)

    batch = len(manifest["batches"])
    events_in = 0
    produced_names: List[str] = []
    for agent, paths in sorted(by_agent.items()):
        # Each round gets its own suffix so a later compaction of the same
        # agent cannot overwrite what an earlier one produced.
        output_name = f"{agent}.{batch:04d}"
        with tempfile.TemporaryDirectory(prefix="dft-compact-") as staging:
            for path in paths:
                target = os.path.join(staging, os.path.basename(path))
                try:
                    os.link(path, target)
                except OSError:
                    shutil.copy2(path, target)

            staged = _count_events(staging)
            if staged:
                events_in += staged
            _clear_index(staging)

            command = [
                _split_binary(),
                "-d", staging,
                "-o", output_dir,
                "-n", output_name,
                "-s", str(int(chunk_size_mb)),
                "--compress",
                "-f",
            ]
            result = subprocess.run(command, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(
                f"dftracer_split failed for agent {agent!r} ({result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )
        produced_names.append(output_name)

    batch_name = ", ".join(produced_names)

    input_bytes = sum(os.path.getsize(path) for path in ready if os.path.exists(path))

    # Retire the consumed sources out of done/. Their events now live in the
    # compacted output, and leaving them in a directory analysis reads would
    # count every one of those events a second time.
    archive_dir = os.path.join(trace_dir, ARCHIVE_SUBDIR)
    os.makedirs(archive_dir, exist_ok=True)
    archived = 0
    for path in ready:
        try:
            shutil.move(path, os.path.join(archive_dir, os.path.basename(path)))
            archived += 1
        except OSError:
            continue

    consumed.update(os.path.basename(path) for path in ready)
    manifest["sources"] = sorted(consumed)
    manifest["batches"].append(
        {"name": batch_name, "agents": sorted(by_agent), "inputs": len(ready), "archived": archived}
    )
    _write_manifest(trace_dir, manifest)

    produced = compacted_files(trace_dir)

    # Compaction must conserve events exactly. The output accumulates across
    # batches, so the running total is what the check compares against.
    events_out = _count_events(output_dir)
    expected = manifest.get("events", 0) + events_in
    manifest["events"] = expected
    _write_manifest(trace_dir, manifest)
    # Splitting rewrites each chunk with its own session bookkeeping records, so
    # the output legitimately carries a few more events than went in. Losing
    # events is the failure worth catching, not gaining a handful.
    verified = None if events_out is None else events_out >= expected

    # Every lifecycle directory changed shape, and event counting above left
    # behind index stores with no aggregation tier. Both make the cached stores
    # wrong for the next query, so they are dropped last of all.
    _clear_session_indexes(trace_dir)

    return {
        "trace_dir": trace_dir,
        "output_dir": output_dir,
        "batch": batch_name,
        "events_in": events_in,
        "events_out": events_out,
        "events_expected_min": expected,
        "events_verified": verified,
        "consumed": len(ready),
        "archived": archived,
        "archive_dir": archive_dir,
        "still_running": len(running),
        "compacted_files": len(produced),
        "input_bytes": input_bytes,
        "output_bytes": sum(os.path.getsize(path) for path in produced),
        "skipped": False,
    }


def _quiet_native_logs() -> None:
    """Silence the native indexer's INFO chatter.

    The C++ layer logs progress to stderr on every index resolve. Harmless for
    a CLI, but it swamps MCP client logs, so drop it to warnings and above.
    """
    try:
        from dftracer.utils import set_log_level

        set_log_level("WARN")
    except Exception:  # pragma: no cover - older utils builds lack the setter
        pass


#: Rows per Arrow batch when streaming raw events. Large enough that the
#: per-batch overhead disappears, small enough that one batch is not a session.
STREAM_BATCH_SIZE = 20000


def _iter_events(path: str, query: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Yield fully-decoded events from one trace file.

    Reads through ``TraceViewer``, the view interface, which replaced
    ``TraceReader`` — its ``stream`` hands back Arrow batches of whole events
    with ``args`` as a JSON string, which is the field this service exists to
    read.

    Note:
        The query is a chunk-level filter, so what comes back is a superset of
        what matches. Callers still have to check the events they care about;
        the filter is there to avoid decoding the ones they do not.

    Args:
        path: Trace file to read.
        query: Optional native filter over top-level fields (``name``, ``cat``,
            ``pid``).

    Yields:
        One event dict per record, with ``args`` decoded into a mapping.
    """
    import pyarrow as pa
    from dftracer.utils import TraceViewer

    viewer = TraceViewer(path)
    if query:
        viewer = viewer.filter(query)

    for capsule in viewer.stream(batch_size=STREAM_BATCH_SIZE):
        batch = pa.record_batch(capsule)
        columns = {name: batch.column(name).to_pylist() for name in batch.schema.names}
        for index in range(batch.num_rows):
            event = {name: values[index] for name, values in columns.items()}
            # args arrives as a JSON string; every caller here reads it as a
            # mapping, which is the whole reason this function exists.
            raw_args = event.get("args")
            if isinstance(raw_args, str):
                try:
                    event["args"] = json.loads(raw_args)
                except json.JSONDecodeError:
                    event["args"] = {}
            yield event


#: One lock per index store, so concurrent readers within a process do not open
#: the same RocksDB for writing at the same moment.
_INDEX_LOCKS: Dict[str, "threading.Lock"] = {}
_INDEX_LOCKS_GUARD = None

#: How many times to wait for another process to finish with an index store.
#: Compaction deletes and rebuilds it, which takes seconds; a reader that
#: arrives mid-rebuild should wait rather than fail.
INDEX_RETRIES = 8
INDEX_RETRY_SECONDS = 0.5


def _index_lock(trace_dir: str):
    """The in-process lock guarding one directory's index store."""
    import threading

    global _INDEX_LOCKS_GUARD
    if _INDEX_LOCKS_GUARD is None:
        _INDEX_LOCKS_GUARD = threading.Lock()
    with _INDEX_LOCKS_GUARD:
        return _INDEX_LOCKS.setdefault(os.path.abspath(trace_dir), threading.Lock())


@contextlib.contextmanager
def _index_guard(directory: str):
    """Hold the cross-process lock on a directory's index store.

    A thread lock is not enough. The compactor runs in its own process — the
    hook spawns it — and compaction *deletes* index stores so the next query
    rebuilds them from the directory's new contents. A reader opening the store
    at that moment gets "Failed to open RocksDB", which is not a race between
    threads but between processes.

    The lock file sits beside the store rather than inside it, so clearing the
    store does not remove the thing being held.

    Args:
        directory: Directory whose index store is being touched.
    """
    import fcntl

    os.makedirs(directory, exist_ok=True)
    path = os.path.join(directory, ".dftindex.lock")
    with _index_lock(directory):
        handle = open(path, "a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()


def _open_indexer(trace_dir: str, force_rebuild: bool = False, index_dir: str = ""):
    """Build (or reuse) the aggregation index for a session's traces.

    Args:
        trace_dir: Directory holding a session's traces.
        force_rebuild: Rebuild the index even when a cached one is present.
        index_dir: Where to place the ``.dftindex`` store. Defaults to
            alongside the traces.

    Returns:
        An ``Indexer`` whose aggregation tier is ready to query.

    Raises:
        FileNotFoundError: When the session has no trace files.
    """
    from dftracer.utils import AggregationConfig, Indexer

    _quiet_native_logs()
    files = _trace_files(trace_dir)
    if not files:
        raise FileNotFoundError(f"No .pfw/.pfw.gz trace files found under: {trace_dir}")

    began = time.monotonic()
    # One indexer per directory at a time, across processes. A live session has
    # several readers — the graph build, the file grouping, the watcher — and a
    # compactor in a process of its own that deletes the store outright.
    with _index_guard(index_dir or trace_dir):
        indexer = Indexer(
            trace_dir,
            index_dir=index_dir,
            require_aggregation=AggregationConfig(time_interval_ms=1000.0),
            force_rebuild=force_rebuild,
        )
        # Always, even when a store is already there. `CURRENT` proves an index
        # exists, not that it has the aggregation tier this reads through — the
        # event counter and the info tool both leave a store without one, and
        # skipping on that evidence fails later with "No aggregation config
        # found". ensure_indexed is cheap when the index is current: the
        # resolver reports every file cached and does no work.
        #
        # Retried, because holding the lock does not help against a process that
        # deleted the store a moment before this one looked: the failure is a
        # rebuild in progress, and waiting is the right answer to it.
        for attempt in range(INDEX_RETRIES):
            try:
                indexer.ensure_indexed()
                break
            except Exception as exc:  # noqa: BLE001 - retried, then re-raised.
                if attempt == INDEX_RETRIES - 1 or "RocksDB" not in str(exc):
                    raise
                _log.info(
                    "index busy (%s), retrying in %.1fs",
                    str(exc).split(":")[0][:60],
                    INDEX_RETRY_SECONDS,
                )
                time.sleep(INDEX_RETRY_SECONDS)
    _log.info(
        "index ready: %d trace file(s) in %s (%.2fs%s)",
        len(files),
        os.path.basename(trace_dir.rstrip("/")) or trace_dir,
        time.monotonic() - began,
        ", rebuilt" if force_rebuild else "",
    )
    return indexer


def _aggregate(indexer, group_by: List[str], query: str = "") -> List[Dict[str, Any]]:
    """Run one grouped aggregation scan and return it as plain dict rows.

    Args:
        indexer: An indexer whose aggregation tier is ready.
        group_by: Columns to collapse the scan onto.
        query: Optional native filter, applied during the scan rather than
            after it. This is what makes a filtered view cheap: the events that
            would be thrown away are never decoded.
    """
    import pyarrow as pa

    kwargs: Dict[str, Any] = {"group_by": group_by}
    if query:
        kwargs["query"] = query
    batches = indexer.iter_arrow_dfanalyzer_all(**kwargs)["events"]
    records = [pa.record_batch(capsule) for capsule in batches]
    if not records:
        return []
    return pa.Table.from_batches(records).to_pylist()


def _aggregate_session(
    trace_dir: str,
    group_by: List[str],
    force_rebuild: bool = False,
    index_dir: str = "",
    prefer_compacted: bool = False,
    query: str = "",
) -> List[Dict[str, Any]]:
    """Aggregate a session across every lifecycle directory that holds traces.

    Each directory is indexed and scanned on its own. dftracer's aggregation
    reads a directory's whole index store regardless of which files were asked
    for, so a single mixed scan would double-count anything present as both a
    compacted trace and its source.

    Args:
        trace_dir: Directory holding a session's traces.
        group_by: Aggregation grouping columns.
        force_rebuild: Rebuild each index rather than reusing a cached one.
        index_dir: Override the ``.dftindex`` location.
        prefer_compacted: Read the compacted copy instead of ``done/``.
        query: Optional native filter applied during each scan.

    Returns:
        The concatenated rows from every directory.

    Raises:
        FileNotFoundError: When the session has no trace files at all.
    """
    directories = _analysis_dirs(trace_dir, prefer_compacted)
    if not directories:
        raise FileNotFoundError(f"No .pfw/.pfw.gz trace files found under: {trace_dir}")

    rows: List[Dict[str, Any]] = []
    for directory in directories:
        indexer = _open_indexer(directory, force_rebuild=force_rebuild, index_dir=index_dir)
        rows.extend(_aggregate(indexer, group_by, query=query))
    return rows


#: Resolved name tables, keyed by the trace files and their modification
#: times. Naming every file means a pass over every trace, and a view that is
#: rebuilt every time a session changes would otherwise pay for it each time.
_NAME_CACHE: Dict[tuple, Dict[str, str]] = {}
_NAME_CACHE_LIMIT = 32


def _mtime(path: str) -> float:
    """Modification time, or 0 when the file has gone."""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


#: The events that name a file, in either hash space: dftracer's own ``FH``
#: records, and the harness's ``agent_io`` events which carry the path inline.
#: Asked for in one query because each one costs a scan.
_NAMING_EVENTS = '(name == "FH" or cat == "agent_io")'


def _resolve_file_names(trace_dir: str, prefer_compacted: bool = False) -> Dict[str, str]:
    """Map every ``fhash`` in a trace back to the path it stands for.

    Two independent hash spaces have to be reconciled, because the same file
    can be recorded twice with different hashes:

    * Harness-emitted ``agent_io`` events carry ``args.fname`` alongside
      ``args.fhash``, hashed by the emitter.
    * ``POSIX`` events captured via LD_PRELOAD carry only ``args.fhash``,
      hashed by dftracer itself; the path is published separately in an ``FH``
      metadata record whose ``args.name`` is the path and ``args.value`` is
      the hash.

    Reading both is what lets a tool call and the syscalls it caused be grouped
    as one file rather than two.

    Args:
        trace_dir: Directory holding the trace files.

    Returns:
        Mapping of ``fhash`` to path, covering both hash spaces.
    """
    files = _analysis_files(trace_dir, prefer_compacted)
    # A trace file's contents are fixed once written, so the names in it can
    # only be read once. Keyed by what was read and when, because a live
    # session grows a file at a time.
    key = tuple(sorted((f, _mtime(f)) for f in files))
    cached = _NAME_CACHE.get(key)
    if cached is not None:
        # A copy: callers merge their own findings into what they get back, and
        # a cache handed out by reference would accumulate them.
        return dict(cached)

    resolved: Dict[str, str] = {}
    _quiet_native_logs()

    for path in files:
        # One pass, filtered natively. A session with half a million events
        # names its files in ten thousand of them, and decoding the other four
        # hundred and ninety thousand to find that out is most of what this
        # used to cost. Both hash spaces are asked for together, because a
        # second query costs another scan of the same file.
        for event in _iter_events(path, query=_NAMING_EVENTS):
            args = event.get("args")
            if not isinstance(args, dict):
                continue

            if event.get("name") == "FH":
                name = args.get("name")
                value = args.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    resolved.setdefault(value, name)
                continue

            fhash = args.get("fhash")
            fname = args.get("fname")
            if isinstance(fhash, str) and isinstance(fname, str):
                resolved.setdefault(fhash, fname)

    if len(_NAME_CACHE) >= _NAME_CACHE_LIMIT:
        _NAME_CACHE.clear()
    _NAME_CACHE[key] = dict(resolved)
    return resolved


class _PathRules:
    """Longest-prefix include/exclude rules over paths, as a component trie.

    A traced agent touches thousands of files it never meant to: the shell
    searches PATH, the interpreter walks sys.path, the module system reads a
    hundred Lua files. Deciding which of those matter is what lets the scan
    that follows skip almost everything.

    Rules are path prefixes, resolved by longest match — the most specific one
    covering a path decides it. Two are special: ``*`` matches everything and
    ``.`` is the project directory. Exclusion is checked first, so a path named
    by both at the same depth is out, and a path no rule mentions is kept.

    A rule's last component matches by string prefix, so ``/usr/lib`` covers
    ``/usr/lib64`` — which is how a path prefix normally reads. The consequence
    is worth knowing: ``runs`` also covers ``runsheet.csv``.

    Args:
        include: Prefixes to keep.
        exclude: Prefixes to drop.
        project_root: What ``.`` and a relative prefix resolve against.
    """

    def __init__(
        self,
        include: Optional[List[str]] = None,
        exclude: Optional[List[str]] = None,
        project_root: str = "",
    ):
        self.project_root = os.path.abspath(project_root) if project_root else ""
        self._root: Dict[str, Any] = {"children": {}, "prefixes": {}, "keep": None}
        self._configured = False
        for prefix in exclude or []:
            self._add(prefix, keep=False)
        for prefix in include or []:
            self._add(prefix, keep=True)

    def allows(self, path: str) -> bool:
        """Whether a path survives the rules."""
        if not self._configured:
            return True
        node = self._root
        decision = node["keep"]
        for part in self._components(path):
            by_prefix = _longest_prefix(node["prefixes"], part)
            if by_prefix is not None:
                decision = by_prefix
            node = node["children"].get(part)
            if node is None:
                break
            if node["keep"] is not None:
                decision = node["keep"]
        return True if decision is None else decision

    def _add(self, prefix: str, keep: bool) -> None:
        parts = self._rule_components(prefix)
        if parts is None:
            return
        self._configured = True
        node = self._root
        if not parts:
            # `*`: the root, which every path passes through.
            if node["keep"] is None or keep is False:
                node["keep"] = keep
            return

        empty = {"children": {}, "prefixes": {}, "keep": None}
        for part in parts[:-1]:
            node = node["children"].setdefault(part, dict(empty, children={}, prefixes={}))
        last = parts[-1]
        # Exclusion wins a tie, so an inclusion never overwrites one.
        if last not in node["prefixes"] or keep is False:
            node["prefixes"][last] = keep
        node["children"].setdefault(last, {"children": {}, "prefixes": {}, "keep": None})

    def _rule_components(self, prefix: str) -> Optional[List[str]]:
        text = str(prefix or "").strip().rstrip("/")
        if not text:
            return None
        if text == "*":
            return []
        if text in (".", "./"):
            return self._components(self.project_root) if self.project_root else None
        return self._components(text)

    def _components(self, path: str) -> List[str]:
        text = str(path or "")
        if not text:
            return []
        if not text.startswith("/") and self.project_root:
            text = f"{self.project_root}/{text}"
        return [part for part in text.split("/") if part and part != "."]


def _longest_prefix(prefixes: Dict[str, bool], component: str) -> Optional[bool]:
    """What a node's prefix rules say about one path component.

    The longest matching prefix decides; exclusion wins a tie at equal length.
    """
    best: Optional[Any] = None
    for prefix, keep in prefixes.items():
        if not component.startswith(prefix):
            continue
        if best is None or len(prefix) > best[0] or (len(prefix) == best[0] and not keep):
            best = (len(prefix), keep)
    return None if best is None else best[1]


def is_command_line(name: str) -> bool:
    """Whether a hashed name is a command a process ran, not a file it opened.

    dftracer hashes the argument of `popen`-style calls into the same table it
    hashes paths into, so `ls -F /opt/cray/pe/libsci/22.11.1.2` arrives looking
    like a filename. On one real session those outnumbered the real files: 105
    of the 153 that survived the path rules.

    A command is told from a path by its first word: `ls`, `readlink`, `cd` and
    `source` are bare names, while a path that happens to contain a space still
    starts with `/`. That keeps `/home/me/My Notes/a.txt` a file.

    Args:
        name: The hashed name to judge.

    Returns:
        True when the name reads as a command line rather than a path.
    """
    text = str(name or "").strip()
    if not text or text.startswith("/"):
        return False
    head, sep, _ = text.partition(" ")
    return bool(sep) and "/" not in head


def _process_cwds(trace_dir: str, prefer_compacted: bool = False) -> Dict[int, str]:
    """Read the directory each process started in, keyed by pid.

    dftracer records it in every process's ``start`` event, as a hash into the
    same table file paths are hashed into. Pushing ``name == "start"`` down to
    the reader means only those events are decoded rather than all of them.

    Args:
        trace_dir: Directory holding a session's traces.
        prefer_compacted: Read the compacted copy instead of ``done/``.

    Returns:
        Mapping of pid to the working-directory hash it started in.
    """
    cwds: Dict[int, str] = {}
    for path in _analysis_files(trace_dir, prefer_compacted):
        for event in _iter_events(path, query='name == "start"'):
            args = event.get("args")
            pid = event.get("pid")
            if isinstance(args, dict) and isinstance(pid, int):
                cwd = args.get("cwd")
                if isinstance(cwd, str):
                    cwds[pid] = cwd
    return cwds


def _file_names(trace_dir: str, prefer_compacted: bool = False) -> Dict[str, str]:
    """Map every file hash to the path it stands for, straight from the index.

    The index already holds this table — it is built from the ``FH`` metadata
    records as the traces are ingested — so asking for it costs a lookup rather
    than a pass over the events. On one real session that is 10,715 names in
    0.07s against 2.4s to read the same records back out of the traces.

    Falls back to reading the records when the index has no table, which is what
    an older dftracer-utils produces: before the ``ph``-as-integer fix the
    metadata visitor never fired, so the table was silently empty.

    Args:
        trace_dir: Directory holding a session's traces.
        prefer_compacted: Read the compacted copy instead of ``done/``.

    Returns:
        Mapping of file hash to path, as the trace recorded it — which for a
        relative path is relative to whichever process opened it.
    """
    names: Dict[str, str] = {}
    for directory in _analysis_dirs(trace_dir, prefer_compacted):
        try:
            indexer = _open_indexer(directory)
            table = indexer.get_hash_table("file")
        except Exception:  # noqa: BLE001 - fall back to reading the records.
            table = {}
        for file_hash, name in table.items():
            names.setdefault(file_hash, name)

    if names:
        return names
    return _resolve_file_names(trace_dir, prefer_compacted)


#: What a filtered activity scan groups by, and what it measures — the view
#: interface's own column and metric names.
ACTIVITY_GROUP_BY = ("fhash", "pid", "name")
ACTIVITY_METRICS = ("count", "sum:dur", "sum:size", "min:ts", "max:te")


def _view_activity(
    files: List[str],
    index_path: str,
    query: str,
    group_by: Tuple[str, ...] = ACTIVITY_GROUP_BY,
    metrics: Tuple[str, ...] = ACTIVITY_METRICS,
) -> List[Dict[str, Any]]:
    """Aggregate one directory's events, filtered to the files that matter.

    The order is the point: the filter narrows the scan to the handful of files
    the rules kept, and the grouping runs over what survives. ``TraceViewer`` is
    lazy, so both are handed to the C++ side and executed in one pass — events
    for files nobody asked about are never decoded.

    Args:
        files: Trace files to read.
        index_path: The ``.dftindex`` store to read them through.
        query: Native filter, normally ``fhash in [...]``.
        group_by: Columns to collapse onto.
        metrics: Aggregates to compute.

    Returns:
        One dict per group.
    """
    import pyarrow as pa
    from dftracer.utils import TraceViewer

    began = time.monotonic()
    viewer = TraceViewer(files, index_path=index_path)
    if query:
        viewer = viewer.filter(query)
    rows = pa.table(viewer.group_by(*group_by).agg(*metrics).collect()).to_pylist()
    _log.info(
        "group by %s: %d row(s) from %d file(s) in %.2fs (%s)",
        "/".join(group_by),
        len(rows),
        len(files),
        time.monotonic() - began,
        "filtered" if query else "unfiltered",
    )
    return rows


def _kept_hashes(
    names: Dict[str, str],
    rules: _PathRules,
    cwds: Optional[Dict[int, str]] = None,
) -> Dict[str, str]:
    """Apply the rules to every file the session touched.

    A path recorded relative to a process's working directory means a different
    file depending on which process opened it, and a hash alone does not say
    which. So a relative path is kept if it survives the rules under *any*
    working directory seen in the session: this stage decides what to scan, and
    scanning something that is later dropped costs less than dropping something
    that mattered.

    Args:
        names: Hash to path, as recorded.
        rules: The include/exclude rules.
        cwds: Working-directory hashes per pid, to resolve relative paths.

    Returns:
        The subset of ``names`` worth scanning.
    """
    directories = []
    if cwds:
        for cwd_hash in set(cwds.values()):
            directory = names.get(cwd_hash)
            if isinstance(directory, str) and directory.startswith("/"):
                directories.append(directory)

    kept: Dict[str, str] = {}
    for file_hash, name in names.items():
        if is_command_line(name):
            continue
        if name.startswith("/"):
            if rules.allows(name):
                kept[file_hash] = name
            continue
        candidates = [f"{directory.rstrip('/')}/{name}" for directory in directories] or [name]
        if any(rules.allows(candidate) for candidate in candidates):
            kept[file_hash] = name
    return kept


#: Where naming the files stops paying for itself. The filter is a literal list
#: in the query string, so it costs about twenty bytes per file to state and
#: the scan it saves shrinks as the list grows. Measured on a session of 80,169
#: grouped rows, against 0.90s for no query at all:
#:
#:     1,000 hashes -> 10,315 rows in 0.39s
#:     4,000 hashes -> 25,846 rows in 0.99s
#:     7,080 hashes -> 37,872 rows in 1.37s
#:
#: So a filter is worth stating while it names a few thousand files, and past
#: that the unfiltered scan is faster than parsing the query. A rule set that
#: keeps most of a session has nothing to push down, and saying so plainly beats
#: pushing down a filter that costs more than it saves.
MAX_QUERY_HASHES = 4000


def _fhash_query(hashes: List[str]) -> str:
    """Build the native filter that keeps only these files' events.

    Returns:
        A query string, or "" when there is nothing to filter by — which the
        callers treat as "scan everything" rather than "scan nothing".
    """
    selected = [h for h in hashes if isinstance(h, str) and h][:MAX_QUERY_HASHES]
    if not selected:
        return ""
    return "fhash in [%s]" % ", ".join('"%s"' % h for h in selected)


def _int(value: Any) -> int:
    """Coerce an aggregation cell to int, treating nulls/NaN as 0."""
    if value is None:
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number


def _float(value: Any) -> float:
    """Coerce an aggregation cell to float, treating nulls/NaN as 0.0."""
    if value is None:
        return 0.0
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return 0.0 if number != number else number  # NaN check


def _file_groups_impl(
    trace_dir: str,
    categories: Optional[List[str]] = None,
    resolve_names: bool = True,
    force_rebuild: bool = False,
    index_dir: str = "",
    prefer_compacted: bool = False,
) -> Dict[str, Any]:
    """Group every file-touching event by ``file_hash``.

    This is the deterministic replacement for LLM-inferred file relationships.

    Args:
        trace_dir: Directory holding the agent's ``.pfw``/``.pfw.gz`` traces.
        categories: Event categories to treat as file interactions. Defaults to
            ``["agent_io"]``. Pass ``[]`` to include every non-dftracer category.
        resolve_names: Recover the path behind each hash by scanning the trace.
            Turn off for a faster, hash-only answer on very large traces.
        force_rebuild: Rebuild the index rather than reusing a cached one.
        index_dir: Override the ``.dftindex`` location.

    Returns:
        Dict with ``files`` (one entry per distinct file, each listing the
        processes and operations that touched it), ``shared_files`` (the subset
        touched by more than one process — the cross-process hand-off points),
        and a ``summary``.
    """
    wanted_categories = {
        _normalize_category(c)
        for c in (DEFAULT_FILE_CATEGORIES if categories is None else categories)
        if c
    }

    rows = _aggregate_session(
        trace_dir,
        ["file_hash", "cat", "func_name", "pid"],
        force_rebuild=force_rebuild,
        index_dir=index_dir,
        prefer_compacted=prefer_compacted,
    )

    # Group on the resolved path when one is known, so that the harness's hash
    # of a file and dftracer's own hash of the same file collapse into a single
    # entry. Unresolvable hashes group under themselves.
    names = _resolve_file_names(trace_dir, prefer_compacted) if resolve_names else {}

    files: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        fhash = row.get("file_hash")
        category = _normalize_category(row.get("cat"))
        if not fhash or category == INTERNAL_CATEGORY:
            continue
        if wanted_categories and category not in wanted_categories:
            continue

        name = names.get(fhash)
        key = name or fhash
        entry = files.setdefault(
            key,
            {
                "file_hash": fhash,
                "file_hashes": set(),
                "file_name": name,
                "processes": set(),
                "operations": [],
                "categories": set(),
                "total_events": 0,
                "total_time": 0.0,
                "time_start": None,
                "time_end": None,
            },
        )
        entry["file_hashes"].add(fhash)
        pid = _int(row.get("pid"))
        count = _int(row.get("count"))
        start = _int(row.get("time_start"))
        end = _int(row.get("time_end"))

        entry["processes"].add(pid)
        entry["categories"].add(category)
        entry["total_events"] += count
        entry["total_time"] += _float(row.get("time"))
        entry["operations"].append(
            {
                "operation": row.get("func_name"),
                "category": category,
                "pid": pid,
                "count": count,
                "time": _float(row.get("time")),
                "time_start": start,
                "time_end": end,
            }
        )
        if start and (entry["time_start"] is None or start < entry["time_start"]):
            entry["time_start"] = start
        if end and (entry["time_end"] is None or end > entry["time_end"]):
            entry["time_end"] = end

    results: List[Dict[str, Any]] = []
    for entry in files.values():
        processes = sorted(entry["processes"])
        operations = sorted(entry["operations"], key=lambda op: (op["time_start"], op["pid"]))
        results.append(
            {
                "file_hash": entry["file_hash"],
                "file_hashes": sorted(entry["file_hashes"]),
                "file_name": entry["file_name"],
                "processes": processes,
                "num_processes": len(processes),
                "shared": len(processes) > 1,
                "categories": sorted(entry["categories"]),
                "operations": operations,
                "operation_names": sorted({op["operation"] for op in operations if op["operation"]}),
                "total_events": entry["total_events"],
                "total_time": entry["total_time"],
                "time_start": entry["time_start"],
                "time_end": entry["time_end"],
            }
        )

    results.sort(key=lambda item: (-item["num_processes"], -item["total_events"]))
    shared = [item for item in results if item["shared"]]

    return {
        "trace_dir": trace_dir,
        "trace_files": _analysis_files(trace_dir, prefer_compacted),
        "files": results,
        "shared_files": shared,
        "summary": {
            "num_files": len(results),
            "num_shared_files": len(shared),
            "num_processes": len({pid for item in results for pid in item["processes"]}),
            "total_events": sum(item["total_events"] for item in results),
            "categories": sorted({c for item in results for c in item["categories"]}),
        },
    }


def _filtered_activity_impl(
    trace_dir: str,
    include_paths: Optional[List[str]] = None,
    exclude_paths: Optional[List[str]] = None,
    project_root: str = "",
    prefer_compacted: bool = False,
    force_rebuild: bool = False,
    index_dir: str = "",
) -> Dict[str, Any]:
    """Report what a session did to the files that matter, and skip the rest.

    Three steps, in this order, because each one makes the next cheaper:

    1. **Name every file.** Only the ``FH`` records are decoded — the reader
       evaluates ``name == "FH"`` natively — so ten thousand names come back
       without touching the half-million events around them.
    2. **Match the prefix rules.** Applied to the names, which number in the
       thousands, rather than to the events, which number in the hundreds of
       thousands.
    3. **Scan only what survived.** The kept hashes become a native ``fhash in
       [...]`` filter on the aggregation, so events for files nobody asked
       about are never decoded at all.

    Measured on a real session: 80,169 grouped rows in 1.12s unfiltered against
    296 rows in 0.17s for the twenty files that mattered.

    Args:
        trace_dir: Directory holding a session's traces.
        include_paths: Path prefixes to keep. ``*`` is everything, ``.`` the
            project directory.
        exclude_paths: Path prefixes to drop, checked first.
        project_root: What ``.`` and a relative prefix resolve against.
        prefer_compacted: Read the compacted copy instead of ``done/``.
        force_rebuild: Rebuild the index rather than reusing a cached one.
        index_dir: Override the ``.dftindex`` location.

    Returns:
        ``rows`` of per-file, per-process, per-operation aggregates; ``names``
        mapping the hashes in them to paths; ``cwds`` giving each process's
        working directory so a relative path can be resolved exactly; and
        ``filtered`` reporting how much the rules removed.
    """
    began = time.monotonic()
    rules = _PathRules(include_paths, exclude_paths, project_root)
    names = _file_names(trace_dir, prefer_compacted)
    cwds = _process_cwds(trace_dir, prefer_compacted)
    _log.info(
        "named %d file(s) across %d process(es) (%.2fs)",
        len(names),
        len(cwds),
        time.monotonic() - began,
    )

    began = time.monotonic()
    kept = _kept_hashes(names, rules, cwds)
    _log.info(
        "path rules: %d file(s) kept of %d (%.2fs)",
        len(kept),
        len(names),
        time.monotonic() - began,
    )

    # A filter naming more files than this costs more to parse than the scan it
    # saves, so the scan runs unfiltered and the caller is told why.
    too_broad = len(kept) > MAX_QUERY_HASHES
    query = "" if too_broad else _fhash_query(list(kept))

    rows: List[Dict[str, Any]] = []
    for directory in _analysis_dirs(trace_dir, prefer_compacted):
        # Each directory is indexed and scanned on its own: the index store
        # covers a whole directory regardless of which files were asked for, so
        # one mixed scan would double-count a trace present as both a compacted
        # file and its source.
        _open_indexer(directory, force_rebuild=force_rebuild, index_dir=index_dir)
        store = index_dir or os.path.join(directory, ".dftindex")
        rows.extend(_view_activity(_trace_files(directory), store, query))

    return {
        "trace_dir": trace_dir,
        "rows": rows,
        "names": {h: names[h] for h in kept},
        "cwds": {str(pid): names.get(cwd_hash, "") for pid, cwd_hash in cwds.items()},
        "filtered": {
            "files_total": len(names),
            "files_kept": len(kept),
            "rows": len(rows),
            "query_applied": bool(query),
            "query_skipped_as_too_broad": too_broad,
            "max_query_hashes": MAX_QUERY_HASHES,
        },
    }


def _process_groups_impl(
    trace_dir: str,
    force_rebuild: bool = False,
    index_dir: str = "",
    prefer_compacted: bool = False,
) -> Dict[str, Any]:
    """Summarize activity per process, and link processes via shared files.

    The inverse view of :func:`_file_groups_impl`: instead of "who touched this
    file", it answers "what did this process do, and which processes does it
    share files with".

    Args:
        trace_dir: Directory holding the agent's traces.
        force_rebuild: Rebuild the index rather than reusing a cached one.
        index_dir: Override the ``.dftindex`` location.

    Returns:
        Dict with ``processes`` (per-pid operation breakdown) and ``links``
        (pid pairs joined by at least one shared file, with the file hashes).
    """
    rows = _aggregate_session(
        trace_dir,
        ["pid", "cat", "func_name", "file_hash"],
        force_rebuild=force_rebuild,
        index_dir=index_dir,
        prefer_compacted=prefer_compacted,
    )

    processes: Dict[int, Dict[str, Any]] = {}
    pids_by_file: Dict[str, set[int]] = {}
    for row in rows:
        category = _normalize_category(row.get("cat"))
        if category == INTERNAL_CATEGORY:
            continue
        pid = _int(row.get("pid"))
        entry = processes.setdefault(
            pid,
            {"pid": pid, "operations": [], "files": set(), "total_events": 0, "categories": set()},
        )
        count = _int(row.get("count"))
        entry["total_events"] += count
        entry["categories"].add(category)
        entry["operations"].append(
            {
                "operation": row.get("func_name"),
                "category": category,
                "count": count,
                "time": _float(row.get("time")),
            }
        )
        fhash = row.get("file_hash")
        if fhash:
            entry["files"].add(fhash)
            pids_by_file.setdefault(fhash, set()).add(pid)

    links: Dict[tuple[int, int], List[str]] = {}
    for fhash, pids in pids_by_file.items():
        ordered = sorted(pids)
        for i, left in enumerate(ordered):
            for right in ordered[i + 1 :]:
                links.setdefault((left, right), []).append(fhash)

    return {
        "trace_dir": trace_dir,
        "processes": [
            {
                "pid": entry["pid"],
                "categories": sorted(entry["categories"]),
                "operations": sorted(entry["operations"], key=lambda op: -op["count"]),
                "files": sorted(entry["files"]),
                "num_files": len(entry["files"]),
                "total_events": entry["total_events"],
            }
            for entry in sorted(processes.values(), key=lambda e: e["pid"])
        ],
        "links": [
            {"pids": list(pair), "shared_file_hashes": sorted(hashes), "num_shared_files": len(hashes)}
            for pair, hashes in sorted(links.items(), key=lambda item: -len(item[1]))
        ],
    }


def _summary_impl(
    trace_dir: str,
    force_rebuild: bool = False,
    index_dir: str = "",
    prefer_compacted: bool = False,
) -> Dict[str, Any]:
    """Return a category/operation breakdown of an agent trace.

    Args:
        trace_dir: Directory holding the agent's traces.
        force_rebuild: Rebuild the index rather than reusing a cached one.
        index_dir: Override the ``.dftindex`` location.

    Returns:
        Dict with per-category and per-operation counts plus the trace's wall
        clock span, suitable as a cheap first look before drilling in.
    """
    rows = _aggregate_session(
        trace_dir,
        ["cat", "func_name", "pid"],
        force_rebuild=force_rebuild,
        index_dir=index_dir,
        prefer_compacted=prefer_compacted,
    )

    categories: Dict[str, Dict[str, Any]] = {}
    operations: Dict[str, Dict[str, Any]] = {}
    pids: set[int] = set()
    start: Optional[int] = None
    end: Optional[int] = None

    for row in rows:
        category = _normalize_category(row.get("cat"))
        if category == INTERNAL_CATEGORY:
            continue
        count = _int(row.get("count"))
        duration = _float(row.get("time"))
        pids.add(_int(row.get("pid")))

        bucket = categories.setdefault(category, {"category": category, "count": 0, "time": 0.0})
        bucket["count"] += count
        bucket["time"] += duration

        name = row.get("func_name")
        op = operations.setdefault(
            f"{category}:{name}",
            {"category": category, "operation": name, "count": 0, "time": 0.0},
        )
        op["count"] += count
        op["time"] += duration

        row_start = _int(row.get("time_start"))
        row_end = _int(row.get("time_end"))
        if row_start and (start is None or row_start < start):
            start = row_start
        if row_end and (end is None or row_end > end):
            end = row_end

    return {
        "trace_dir": trace_dir,
        "trace_files": _analysis_files(trace_dir, prefer_compacted),
        "num_processes": len(pids),
        "processes": sorted(pids),
        "time_start": start,
        "time_end": end,
        "duration_us": (end - start) if (start and end) else 0,
        "categories": sorted(categories.values(), key=lambda item: -item["count"]),
        "operations": sorted(operations.values(), key=lambda item: -item["count"]),
    }


def _events_impl(
    trace_dir: str,
    category: str = "",
    pid: int = 0,
    operation: str = "",
    file_hash_filter: str = "",
    limit: int = 200,
    prefer_compacted: bool = False,
) -> Dict[str, Any]:
    """Read individual agent events out of the trace.

    Grouping answers "what relates to what"; this answers "show me the actual
    events" for a group the caller has already narrowed down.

    Note:
        The native reader's query language filters top-level fields only, so
        ``cat``/``pid`` are pushed down to it while ``operation`` and
        ``file_hash_filter`` are applied in Python over the returned rows.

    Args:
        trace_dir: Directory holding the agent's traces.
        category: Restrict to one event category (e.g. ``agent_io``).
        pid: Restrict to one process id. 0 means all.
        operation: Restrict to one operation/tool name (event ``name``).
        file_hash_filter: Restrict to events touching this ``fhash``.
        limit: Maximum number of events to return.

    Returns:
        Dict with the matching ``events`` and whether the ``limit`` truncated
        the result.
    """
    _quiet_native_logs()
    clauses = []
    if category:
        clauses.append(f'cat == "{category}"')
    if pid:
        clauses.append(f"pid == {pid}")
    query = " and ".join(clauses) if clauses else None

    events: List[Dict[str, Any]] = []
    truncated = False
    for path in _analysis_files(trace_dir, prefer_compacted):
        if truncated:
            break
        for event in _iter_events(path, query=query):
            if _normalize_category(event.get("cat")) == INTERNAL_CATEGORY:
                continue
            args = event.get("args") if isinstance(event.get("args"), dict) else {}
            if operation and event.get("name") != operation:
                continue
            if file_hash_filter and args.get("fhash") != file_hash_filter:
                continue
            events.append(
                {
                    "name": event.get("name"),
                    "cat": event.get("cat"),
                    "pid": event.get("pid"),
                    "tid": event.get("tid"),
                    "ts": event.get("ts"),
                    "dur": event.get("dur"),
                    "args": args,
                }
            )
            if len(events) >= limit:
                truncated = True
                break

    events.sort(key=lambda item: item.get("ts") or 0)
    return {
        "trace_dir": trace_dir,
        "filters": {
            "category": category,
            "pid": pid,
            "operation": operation,
            "file_hash": file_hash_filter,
        },
        "count": len(events),
        "truncated": truncated,
        "events": events,
    }


class AgentTraceService(MCPService):
    """MCP service exposing deterministic relationships from agent traces.

    Attributes:
        agent_subservice (FastMCP): Sub-server named ``"DFTracerAgentTrace"``
            hosting ``agent_trace_summary``, ``agent_trace_file_groups``,
            ``agent_trace_process_groups``, ``agent_trace_events``,
            ``agent_trace_file_hash``, and ``agent_trace_filtered_activity``.
    """

    def __init__(self):
        self.agent_subservice = FastMCP("DFTracerAgentTrace")
        self._register_tools()

    @property
    def name(self) -> str:
        """Return the service name."""
        return "agent_trace"

    def execute(self, data: dict) -> Optional[str]:
        """Unused hook required by :class:`MCPService`; tools are served by FastMCP."""
        return None

    def _register_tools(self):
        """Register the agent-trace tools on ``agent_subservice``."""

        @self.agent_subservice.tool()
        def agent_trace_summary(
            trace_dir: str,
            force_rebuild: bool = False,
            index_dir: str = "",
        ) -> dict:
            """Summarize an agent trace: categories, operations, processes, span.

            Start here to see what a traced agent session contains before
            drilling into file or process relationships.

            Args:
                trace_dir: Directory holding the agent's .pfw/.pfw.gz traces.
                force_rebuild: Rebuild the index instead of reusing a cached one.
                index_dir: Override the .dftindex location.

            Returns:
                Per-category and per-operation counts, process list, and the
                trace's wall-clock span.
            """
            return _summary_impl(trace_dir, force_rebuild=force_rebuild, index_dir=index_dir)

        @self.agent_subservice.tool()
        def agent_trace_file_groups(
            trace_dir: str,
            categories: Optional[List[str]] = None,
            resolve_names: bool = True,
            force_rebuild: bool = False,
            index_dir: str = "",
        ) -> dict:
            """Group agent events by the file they touched, via the trace index.

            Use this INSTEAD of asking a model which files were accessed by
            which processes. The grouping is computed from the index's
            ``file_hash`` column, so it is exact. Feed the result to a model
            only to narrate it.

            Args:
                trace_dir: Directory holding the agent's .pfw/.pfw.gz traces.
                categories: Event categories counted as file interactions.
                    Defaults to ["agent_io"]; pass [] for all agent categories.
                resolve_names: Recover each file's path from the trace. Disable
                    for a faster hash-only answer on very large traces.
                force_rebuild: Rebuild the index instead of reusing a cached one.
                index_dir: Override the .dftindex location.

            Returns:
                ``files`` (each with its processes and operations),
                ``shared_files`` (touched by more than one process — the
                cross-process hand-off points), and a summary.
            """
            return _file_groups_impl(
                trace_dir,
                categories=categories,
                resolve_names=resolve_names,
                force_rebuild=force_rebuild,
                index_dir=index_dir,
            )

        @self.agent_subservice.tool()
        def agent_trace_process_groups(
            trace_dir: str,
            force_rebuild: bool = False,
            index_dir: str = "",
        ) -> dict:
            """Summarize each process and link processes that share files.

            The inverse of ``agent_trace_file_groups``: what each process did,
            and which process pairs are connected by a shared file.

            Args:
                trace_dir: Directory holding the agent's .pfw/.pfw.gz traces.
                force_rebuild: Rebuild the index instead of reusing a cached one.
                index_dir: Override the .dftindex location.

            Returns:
                ``processes`` (per-pid operation breakdown) and ``links`` (pid
                pairs with the file hashes they share).
            """
            return _process_groups_impl(
                trace_dir, force_rebuild=force_rebuild, index_dir=index_dir
            )

        @self.agent_subservice.tool()
        def agent_trace_events(
            trace_dir: str,
            category: str = "",
            pid: int = 0,
            operation: str = "",
            file_hash: str = "",
            limit: int = 200,
        ) -> dict:
            """Read individual agent events, optionally filtered.

            Use after grouping, to inspect the concrete events behind a file or
            process of interest.

            Args:
                trace_dir: Directory holding the agent's .pfw/.pfw.gz traces.
                category: Restrict to one event category (e.g. "agent_io").
                pid: Restrict to one process id; 0 means all.
                operation: Restrict to one operation/tool name.
                file_hash: Restrict to events touching this file hash.
                limit: Maximum number of events to return.

            Returns:
                The matching events and whether the limit truncated them.
            """
            return _events_impl(
                trace_dir,
                category=category,
                pid=pid,
                operation=operation,
                file_hash_filter=file_hash,
                limit=limit,
            )

        @self.agent_subservice.tool()
        def agent_trace_compact(
            trace_dir: str,
            app_name: str = "agent",
            chunk_size_mb: int = 4,
            force: bool = False,
            include_legacy: bool = False,
        ) -> dict:
            """Compact the traces of agents that have finished.

            A traced agent leaves one trace file per process, and a single shell
            command forks enough times to produce dozens; compacting them makes
            every later query far faster. Safe to run during a session and to
            re-run as more agents finish: only the `done/` directory is
            compacted, and agents still writing into `live/` are untouched.

            Once compacted, the other agent_trace_* tools read the compacted
            copy plus anything newer, automatically.

            Args:
                trace_dir: Directory holding the session's traces.
                app_name: Prefix for the output files.
                chunk_size_mb: Target size of each output chunk.
                force: Discard the compacted copy and rebuild from all of done/.
                include_legacy: Also compact traces sitting loose in trace_dir,
                    whose lifecycle is unknown. Only safe when nothing is
                    writing them.

            Returns:
                What was consumed, how many agents are still running, and the
                resulting file and byte counts.
            """
            return _compact_impl(
                trace_dir,
                app_name=app_name,
                chunk_size_mb=chunk_size_mb,
                force=force,
                include_legacy=include_legacy,
            )

        @self.agent_subservice.tool()
        def agent_trace_status(trace_dir: str) -> dict:
            """Report which agents are still running and what is compacted.

            Use before ``agent_trace_compact`` to see whether there is anything
            worth compacting, or to check whether a session has finished.

            Args:
                trace_dir: Directory holding the session's traces.

            Returns:
                Counts of live, finished, pending, and compacted traces, plus
                the agent ids currently running.
            """
            running = live_files(trace_dir)
            pending = _pending_files(trace_dir)
            legacy = _legacy_files(trace_dir)
            return {
                "trace_dir": trace_dir,
                "live_files": len(running),
                "live_agents": sorted({_agent_id_for(path) for path in running}),
                "done_files": len(done_files(trace_dir)),
                "pending_files": len(pending),
                "compacted_files": len(compacted_files(trace_dir)),
                "legacy_files": len(legacy),
                "analysis_reads_files": len(_analysis_files(trace_dir)),
                "session_finished": not running,
                "worth_compacting": bool(pending),
            }

        @self.agent_subservice.tool()
        def agent_trace_file_hash(path: str) -> dict:
            """Compute the fhash an agent emitter attaches for a given path.

            Lets a caller go from a known filename to the hash used by
            ``agent_trace_file_groups`` / ``agent_trace_events``.

            Args:
                path: Filesystem path; normalized to absolute before hashing.

            Returns:
                The absolute path and its file hash.
            """
            return {"path": os.path.abspath(path), "file_hash": file_hash(path)}

        @self.agent_subservice.tool()
        def agent_trace_filtered_activity(
            trace_dir: str,
            include_paths: Optional[List[str]] = None,
            exclude_paths: Optional[List[str]] = None,
            project_root: str = "",
            prefer_compacted: bool = False,
            force_rebuild: bool = False,
            index_dir: str = "",
        ) -> dict:
            """Report a session's file activity, scanning only the files that matter.

            Use this INSTEAD of reading a session's events and discarding most
            of them. A traced agent touches thousands of files it never meant
            to — the shell searching PATH, the interpreter walking sys.path,
            the module system reading Lua — and the events for those are never
            decoded here.

            The order is what makes it cheap: name every file first (only the
            ``FH`` records are read), match the path rules against those names,
            then push the survivors into the scan as a native ``fhash in [...]``
            filter.

            Args:
                trace_dir: Directory holding the agent's .pfw/.pfw.gz traces.
                include_paths: Path prefixes to keep. ``*`` is everything and
                    ``.`` is the project directory; longest match wins.
                exclude_paths: Path prefixes to drop, checked first.
                project_root: What ``.`` and a relative prefix resolve against.
                prefer_compacted: Read the compacted copy instead of done/.
                force_rebuild: Rebuild the index rather than reusing a cached one.
                index_dir: Override the .dftindex location.

            Returns:
                Per-file, per-process, per-operation aggregates for the kept
                files; the paths their hashes stand for; each process's working
                directory, so a relative path can be resolved exactly; and what
                the rules removed.
            """
            return _filtered_activity_impl(
                trace_dir,
                include_paths=include_paths,
                exclude_paths=exclude_paths,
                project_root=project_root,
                prefer_compacted=prefer_compacted,
                force_rebuild=force_rebuild,
                index_dir=index_dir,
            )


def run():
    """Run the agent-trace tools as a standalone MCP server."""
    AgentTraceService().agent_subservice.run()


if __name__ == "__main__":
    run()
