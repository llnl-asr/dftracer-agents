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

import glob
import hashlib
import json
import os
from typing import Any, Dict, Iterator, List, Optional

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


def _legacy_files(trace_dir: str) -> List[str]:
    """Traces sitting loose in the trace directory.

    Written either by an older layout or by a process that bypassed the
    wrapper. They are readable, but their lifecycle is unknown, so compaction
    leaves them alone unless explicitly told otherwise.
    """
    return _trace_files(trace_dir)


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


def _clear_index(directory: str) -> None:
    """Drop a directory's ``.dftindex`` store.

    The store is cumulative: once a directory's contents change — files
    compacted away, new ones moved in — a store built earlier keeps reporting
    the old set alongside the new, double-counting events. Removing it after
    every change forces a clean rebuild on the next query.

    Args:
        directory: Directory whose index store should be discarded.
    """
    import shutil

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
    compact_dir = os.path.join(trace_dir, COMPACT_SUBDIR)
    skip_done = prefer_compacted and bool(_trace_files(compact_dir))

    candidates = [compact_dir]
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


def _iter_events(path: str, query: Optional[str] = None) -> Iterator[Dict[str, Any]]:
    """Yield fully-decoded events from one trace file.

    Note:
        Deliberately reads raw lines and decodes them with :mod:`json` rather
        than using ``TraceReader.iter_json``. The native iterator hands back
        nested ``args`` as an opaque ``JsonDictValue`` rather than a ``dict``,
        which is exactly the field this service exists to read.

    Args:
        path: Trace file to read.
        query: Optional native filter over top-level fields (``cat``, ``pid``).

    Yields:
        One decoded event dict per trace line; malformed lines are skipped.
    """
    from dftracer.utils import TraceReader

    reader = TraceReader(path, auto_build_index=True)
    for line in reader.iter_lines(query=query):
        text = bytes(line).strip().rstrip(b",").decode("utf-8", errors="replace")
        if not text or text[0] != "{":
            continue
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict):
            yield event


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

    indexer = Indexer(
        trace_dir,
        index_dir=index_dir,
        require_aggregation=AggregationConfig(time_interval_ms=1000.0),
        force_rebuild=force_rebuild,
    )
    indexer.ensure_indexed()
    return indexer


def _aggregate(indexer, group_by: List[str]) -> List[Dict[str, Any]]:
    """Run one grouped aggregation scan and return it as plain dict rows."""
    import pyarrow as pa

    batches = indexer.iter_arrow_dfanalyzer_all(group_by=group_by)["events"]
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
        rows.extend(_aggregate(indexer, group_by))
    return rows


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
    resolved: Dict[str, str] = {}
    _quiet_native_logs()

    for path in _analysis_files(trace_dir, prefer_compacted):
        for event in _iter_events(path):
            args = event.get("args")
            if not isinstance(args, dict):
                continue

            # dftracer's own file-hash publication.
            if event.get("name") == "FH":
                name = args.get("name")
                value = args.get("value")
                if isinstance(name, str) and isinstance(value, str):
                    resolved.setdefault(value, name)
                continue

            # Harness-emitted events name the file inline.
            fhash = args.get("fhash")
            fname = args.get("fname")
            if isinstance(fhash, str) and isinstance(fname, str):
                resolved.setdefault(fhash, fname)

    return resolved


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
            ``agent_trace_process_groups``, ``agent_trace_events``, and
            ``agent_trace_file_hash``.
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


def run():
    """Run the agent-trace tools as a standalone MCP server."""
    AgentTraceService().agent_subservice.run()


if __name__ == "__main__":
    run()
