"""dftracer_session: create and manage a dftracer session workspace (clone or import an app, the canonical
baseline/annotated/opt<n> layout, session.json state, sandboxed file read/write/edit/remove, run paths, status,
system info), and rule deterministically on whether a session is ready for the next stage (dftracer_session_check).

Tools moved unchanged from dftracer-agents (mcp_tools/tools/session/session_tools.py, register_session_tools).
"""
from __future__ import annotations

from datetime import datetime
from datetime import timezone
from pathlib import Path
import shutil
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.session_tools import (  # moved tools use these helpers unchanged
    _CANONICAL_TOP_LEVEL_NAMES,
    _LEGACY_TOP_LEVEL_NAMES,
    _expected_paths,
    _init_structure,
    _session_collect_system_info_impl,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (  # moved tools use these helpers unchanged
    _create_run,
    _err,
    _load_state,
    _ok,
    _run,
    _safe_session_path,
    _save_state,
    _write_artifact_log,
    _ws,
)
import json
from pathlib import Path
import tempfile

from dftracer_agents.mcp_tools.tools.session.workspace import _ws as _cap_ws


def _add(checks, name, ok, detail, fix=""):
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks, **extra):
    status = "inconclusive" if not checks else ("pass" if all(c["ok"] for c in checks) else "fail")
    return {"status": status, "checks": checks, "failed": [c["check"] for c in checks if not c["ok"]], **extra}


def _nonempty_dir(p: Path) -> bool:
    return p.is_dir() and any(p.iterdir())


def _session_check(ws: Path) -> dict:
    """The deterministic ruling on a session workspace (no network, no subprocess)."""
    checks: list = []
    if not ws.is_dir():
        _add(checks, "workspace", False, f"unknown run: no workspace at {ws}",
             "create it with session_create(url, ref) or session_import_local_tree, or pass the run_id it returned")
        return _verdict(checks, workspace=str(ws))
    _add(checks, "workspace", True, str(ws))
    sj = ws / "session.json"
    state = {}
    try:
        state = json.loads(sj.read_text()) if sj.is_file() else {}
    except ValueError as e:
        _add(checks, "session_json", False, f"session.json does not parse: {e}",
             "restore session.json from the last good state or recreate the session; never hand-edit it")
    else:
        _add(checks, "session_json", isinstance(state, dict) and bool(state),
             "session.json present" if state else "session.json missing or empty",
             "session_create / session_import_local_tree write it; a workspace without it was made by hand")
    origin = state.get("url") or state.get("imported_from")
    _add(checks, "origin_recorded", bool(origin), f"origin: {origin}" if origin else "no url or imported_from in session.json",
         "create the session with session_create(url, ref) or session_import_local_tree(path) so the source is reproducible")
    if state.get("url"):
        _add(checks, "ref_recorded", bool(state.get("ref")), f"ref: {state.get('ref')}",
             "pass the exact ref (branch, tag or SHA) to session_create")
    for run in ("baseline", "annotated"):
        src = ws / run / "source"
        _add(checks, f"{run}_source", _nonempty_dir(src), f"{src} {'populated' if _nonempty_dir(src) else 'empty or missing'}",
             "session_init_structure(run_id) builds the skeleton; session_create copies the clone into baseline/ and annotated/ "
             "(a copy that crashed on a dangling symlink is a tool bug: fix the tool, remove the half-made workspace, retry)")
    for d in ("artifacts", "scripts"):
        _add(checks, f"{d}_dir", (ws / d).is_dir(), f"{ws / d}", "session_init_structure(run_id) creates it (logs go to artifacts/)")
    for run in ("annotated",):
        for sub in ("raw", "compact"):
            p = ws / run / "traces" / sub
            _add(checks, f"{run}_traces_{sub}", p.is_dir(), str(p), "session_init_structure(run_id) or session_init_run(run_id, run_name)")
    return _verdict(checks, workspace=str(ws), step=state.get("step"))


def register(mcp):
    """Register the dftracer_session tools on *mcp*: the dftracer-agents tools moved here unchanged, then the validator."""

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
                # `--branch` only accepts a branch or tag name, so it fails for a
                # raw commit SHA. Retry with a plain clone, then check out the ref.
                shutil.rmtree(src, ignore_errors=True)
                src.mkdir(exist_ok=True)
                r2 = _run(["git", "clone", "--depth", "1", url, str(src)], timeout=300)
                if not r2["success"]:
                    return _err("git clone failed", clone_stderr=r2["stderr"])
                # A --depth 1 clone contains ONLY the default branch tip, so a
                # commit SHA is almost never present and `git checkout <sha>`
                # fails. This used to be fire-and-forget: the failure was
                # swallowed and the session silently continued on the default
                # branch's HEAD while reporting status "ok" for the requested
                # ref -- i.e. every downstream build/trace ran against the wrong
                # source. Deepen and verify instead of assuming.
                co = _run(["git", "checkout", "--detach", ref], cwd=src)
                if not co["success"]:
                    unshallow = _run(
                        ["git", "fetch", "--unshallow"], cwd=src, timeout=900
                    )
                    if not unshallow["success"]:
                        # Not all remotes/refs support --unshallow (already
                        # complete, or a partial-clone filter); a full fetch of
                        # all refs is the next-best deepening step.
                        _run(["git", "fetch", "--tags", "origin"], cwd=src, timeout=900)
                    co = _run(["git", "checkout", "--detach", ref], cwd=src)
                    if not co["success"]:
                        return _err(
                            f"git checkout of ref {ref!r} failed even after "
                            f"deepening the clone -- refusing to continue on the "
                            f"wrong commit",
                            checkout_stderr=co.get("stderr", ""),
                        )

            # Verify HEAD really is the requested ref. `git clone --branch` can
            # succeed while a later local step moves HEAD, and the SHA path above
            # is easy to regress, so confirm rather than trust.
            head = _run(["git", "rev-parse", "HEAD"], cwd=src)
            head_sha = (head.get("stdout") or "").strip()
            if head_sha and not already_cloned:
                # `ref` may be a branch, a tag, an abbreviated SHA, or a full SHA;
                # resolving it locally normalizes all four to a full SHA.
                want = _run(["git", "rev-parse", f"{ref}^{{commit}}"], cwd=src)
                want_sha = (want.get("stdout") or "").strip()
                if want_sha and want_sha != head_sha:
                    return _err(
                        f"clone is on {head_sha[:12]} but ref {ref!r} resolves to "
                        f"{want_sha[:12]} -- refusing to continue on the wrong commit"
                    )

            # No-op when the repo has no .gitmodules; required for repos that
            # vendor dependencies as submodules (e.g. LLNL/RAJAPerf pulling in
            # RAJA/BLT/camp) — otherwise their CMake configure fails with
            # "submodule is not present".
            _run(
                ["git", "submodule", "update", "--init", "--recursive", "--depth", "1"],
                cwd=src,
                timeout=600,
            )

        def _ignore_git_pack_special_files(directory, files):
            # Skip .git/objects/pack/*.keep entries shutil.copytree would
            # otherwise try to stat/copy but that don't exist on disk in some
            # shallow/recursive submodule clones (e.g. LLNL/RAJAPerf).
            ignored = set()
            for f in files:
                if f.endswith(".keep") and not (Path(directory) / f).exists():
                    ignored.add(f)
            return ignored

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
                    ignore=_ignore_git_pack_special_files,
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
    def session_import_local_tree(
        run_id: str,
        local_path: str,
        include_git: bool = False,
        overwrite: bool = False,
    ) -> str:
        """Import a local source tree into an existing session workspace.

        This is the non-git counterpart to ``session_create``.

        Use this when you already have a prepared checkout on the filesystem
        (possibly with uncommitted changes) and want to seed a dftracer session
        without re-cloning.

        What it does:
        - Copies ``local_path`` into ``<ws>/source/``.
        - Copies that imported tree into ``baseline/source/`` and
          ``annotated/source/`` (so annotation can proceed without touching the
          imported original).

        Safety / defaults:
        - By default, ``.git`` is excluded (``include_git=False``) to avoid
          copying large git object stores.
        - Common build/bytecode caches are excluded.
        - If any destination directory is non-empty and ``overwrite=False``,
          the tool returns an error to prevent accidental clobbering.

        Args:
            run_id: Existing session identifier (from ``pipeline_create_run`` or
                ``session_create``).
            local_path: Absolute path to a directory tree to copy.
            include_git: If True, include ``.git/`` in the import.
            overwrite: If True, delete any existing ``source/`` and
                ``baseline/source`` and ``annotated/source`` contents first.

        Returns:
            JSON string with ``status`` and a short summary of what was copied.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Unknown run_id: {run_id}")

        src_in = Path(local_path)
        if not src_in.is_absolute():
            return _err("local_path must be absolute", local_path=local_path)
        if not src_in.exists() or not src_in.is_dir():
            return _err("local_path is not an existing directory", local_path=local_path)

        structure = _init_structure(ws, dataset_path=None)

        def _is_non_empty_dir(p: Path) -> bool:
            return p.exists() and p.is_dir() and any(p.iterdir())

        def _maybe_clear_dir(p: Path) -> None:
            if p.exists():
                shutil.rmtree(p, ignore_errors=True)
            p.mkdir(parents=True, exist_ok=True)

        def _ignore(directory: str, names: list[str]) -> set[str]:
            ignore_names: set[str] = {
                "__pycache__",
                ".pytest_cache",
                ".mypy_cache",
                ".ruff_cache",
                ".tox",
                "dist",
                "build",
                ".eggs",
            }
            if not include_git:
                ignore_names.add(".git")
            ignored = {n for n in names if n in ignore_names}
            ignored |= {n for n in names if n.endswith(".pyc") or n.endswith(".pyo")}
            return ignored

        dest_source = ws / "source"
        dest_baseline = Path(structure["baseline"]) / "source"
        dest_annotated = Path(structure["annotated"]) / "source"

        for dest in (dest_source, dest_baseline, dest_annotated):
            if _is_non_empty_dir(dest) and not overwrite:
                return _err(
                    "Destination is non-empty; pass overwrite=True to replace it",
                    destination=str(dest),
                )

        if overwrite:
            _maybe_clear_dir(dest_source)
            _maybe_clear_dir(dest_baseline)
            _maybe_clear_dir(dest_annotated)

        shutil.copytree(
            src_in,
            dest_source,
            dirs_exist_ok=True,
            symlinks=True,
            ignore_dangling_symlinks=True,
            ignore=_ignore,
        )

        for target in (dest_baseline, dest_annotated):
            shutil.copytree(
                dest_source,
                target,
                dirs_exist_ok=True,
                symlinks=True,
                ignore_dangling_symlinks=True,
                ignore=_ignore,
            )

        _save_state(run_id, {
            "step": "imported",
            "imported_from": str(src_in),
            "import_include_git": include_git,
        })
        _write_artifact_log(ws, 1, "session_import_local_tree", {
            "run_id": run_id,
            "local_path": str(src_in),
            "include_git": include_git,
            "overwrite": overwrite,
            "dest_source": str(dest_source),
            "dest_baseline": str(dest_baseline),
            "dest_annotated": str(dest_annotated),
        }, run_id)

        return _ok(
            "Imported local tree",
            run_id=run_id,
            local_path=str(src_in),
            include_git=include_git,
            overwrite=overwrite,
            source=str(dest_source),
            baseline_source=str(dest_baseline),
            annotated_source=str(dest_annotated),
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

        # Two things this must NOT do, both of which it used to.
        #
        # Follow symlinks: a session's `dataset/` is a symlink to the dataset
        # on the parallel filesystem. A recursive pattern walked into it, and
        # that tree is large enough that a depth-2 `find` exceeds 40 seconds --
        # measured 14.3s average per call, the slowest tool in the pipeline.
        #
        # Walk to completion before truncating: the old code built the whole
        # match list and then took [:max_results], so the limit bounded the
        # ANSWER but not the work. Stopping early is what makes it a limit.
        matched: list[str] = []
        truncated = False
        skipped_links: list[str] = []
        for path in base.glob(pattern):
            if len(matched) >= max_results:
                truncated = True
                break
            try:
                if path.is_symlink():
                    # Report it rather than silently omitting it: a caller
                    # looking for dataset/ needs to know why it is not here.
                    skipped_links.append(str(path.relative_to(base)))
                    continue
                if path.is_file():
                    matched.append(str(path.relative_to(base)))
            except OSError:
                continue        # unreadable entry: skip, do not fail the call

        return _ok(f"{len(matched)} files found", files=matched,
                   truncated=truncated, skipped_symlinks=skipped_links)

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
    def session_edit_file(
        run_id: str,
        filepath: str,
        old_string: str,
        new_string: str,
        subfolder: str = "annotated",
        replace_all: bool = False,
    ) -> str:
        """Apply a targeted find-and-replace edit to a file already in the workspace.

        Companion to ``session_write_file`` for agents whose toolset lacks a
        native ``Edit``/patch tool: ``session_write_file`` only overwrites a
        file in full, which is wasteful and risky for a small change to a large
        file (e.g. splicing one section into a multi-thousand-line
        ``pipeline_plan.md``). This tool reads the file, performs an exact
        string substitution, and writes the result back — no full-file
        round-trip through the caller required.

        Matching is exact (no regex). Exactly one occurrence of *old_string*
        must exist unless *replace_all* is set — this mirrors the safety
        behavior of the CLI ``Edit`` tool so a caller cannot silently mutate
        the wrong occurrence of a common string. Provide enough surrounding
        context in *old_string* to make it unique rather than setting
        *replace_all* on a string that also occurs where it shouldn't.

        Side effects:
            * Overwrites ``<workspace>/<subfolder>/<filepath>`` on disk with the
              substituted content.

        Args:
            run_id: Session identifier returned by ``session_create``.
            filepath: Path to the file relative to *subfolder* (e.g.
                ``"pipeline_plan.md"``). The file must already exist.
            old_string: Exact text to locate and replace. Must be non-empty and
                must not equal *new_string*.
            new_string: Replacement text.
            subfolder: Workspace sub-folder containing the file. Defaults to
                ``"annotated"`` for consistency with ``session_write_file``;
                pass ``"."`` for session-root files like ``pipeline_plan.md``.
            replace_all: If ``True``, replace every occurrence of *old_string*
                instead of requiring exactly one. Defaults to ``False``.

        Returns:
            JSON string with keys:
                * ``status`` (``"ok"`` or ``"error"``).
                * ``message`` — on success, ``"Replaced <N> occurrence(s) in
                  <subfolder>/<filepath>"``; on error, why the edit was rejected
                  (file not found, *old_string* not found, *old_string* found
                  more than once with ``replace_all=False``, or *old_string*
                  equal to *new_string*).
                * ``occurrences`` — number of replacements made (only on success).

        Raises:
            Returns ``{"status": "error"}`` — never raises — when the file does
            not exist, *old_string* is empty, *old_string* equals *new_string*,
            *old_string* is not found, or *old_string* occurs more than once
            while *replace_all* is ``False``.
        """
        if not old_string:
            return _err("old_string must be non-empty")
        if old_string == new_string:
            return _err("old_string and new_string must differ")
        try:
            p = _safe_session_path(_ws(run_id), f"{subfolder}/{filepath}")
        except ValueError as exc:
            return _err(str(exc))
        if not p.exists():
            return _err(f"File not found: {subfolder}/{filepath}")
        content = p.read_text(errors="replace")
        count = content.count(old_string)
        if count == 0:
            return _err(f"old_string not found in {subfolder}/{filepath}")
        if count > 1 and not replace_all:
            return _err(
                f"old_string found {count} times in {subfolder}/{filepath} — "
                "pass replace_all=True or make old_string unique with more "
                "surrounding context"
            )
        new_content = content.replace(old_string, new_string, -1 if replace_all else 1)
        p.write_text(new_content)
        replaced = count if replace_all else 1
        return _ok(
            f"Replaced {replaced} occurrence(s) in {subfolder}/{filepath}",
            occurrences=replaced,
        )

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

    @mcp.tool()
    def dftracer_session_check(run_id: str = "", workspace: str = "") -> dict:
        """Validator for the session stage: rule deterministically on whether a dftracer session workspace is complete
        and ready for detect/build. Checks the workspace exists (a missing one is reported as 'unknown run'),
        session.json parses and records the origin (url+ref or imported_from), baseline/source and annotated/source are
        populated, artifacts/ and scripts/ exist, and annotated/traces/{raw,compact} exist. Reads files only; never runs
        anything. Args: run_id (as returned by session_create) or workspace (a path, overrides run_id).
        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], failed: [names], workspace, step}."""
        if not run_id and not workspace:
            return _verdict([], error="give run_id or workspace")
        return _session_check(Path(workspace).expanduser() if workspace else _cap_ws(run_id))

    @mcp.tool()
    def dftracer_session_selftest() -> dict:
        """Self-test of dftracer_session_check on fixtures written to a temporary directory: a complete session must pass,
        one with an empty annotated/source and no session.json must fail on exactly those checks, and a missing
        workspace must fail as 'unknown run'. Use after editing the validator. Returns {status: pass|fail, cases: [...]}."""
        cases = []
        with tempfile.TemporaryDirectory() as d:
            good = Path(d) / "app" / "20260101_000000"
            for p in ("baseline/source", "annotated/source", "artifacts", "scripts", "annotated/traces/raw", "annotated/traces/compact"):
                (good / p).mkdir(parents=True, exist_ok=True)
            (good / "baseline/source/main.c").write_text("int main(){return 0;}\n")
            (good / "annotated/source/main.c").write_text("int main(){return 0;}\n")
            (good / "session.json").write_text(json.dumps({"url": "https://example.org/app.git", "ref": "v1", "step": "cloned"}))
            r = _session_check(good)
            cases.append({"case": "complete", "want": "pass", "got": r["status"]})
            (good / "annotated/source/main.c").unlink()
            (good / "session.json").unlink()
            r = _session_check(good)
            cases.append({"case": "broken", "want": ["session_json", "origin_recorded", "annotated_source"], "got": r["failed"]})
            r = _session_check(Path(d) / "nope")
            cases.append({"case": "missing", "want": "unknown run", "got": r["checks"][0]["detail"][:11]})
        ok = all(c["want"] == c["got"] for c in cases)
        return {"status": "pass" if ok else "fail", "cases": cases}
