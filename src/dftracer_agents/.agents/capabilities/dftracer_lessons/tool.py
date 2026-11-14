"""dftracer_lessons: the lessons-learned process of dftracer-agents. Sync workspace-staged annotation lessons back to the
source repo (preview, then a confirmed pull request), and validate any proposed lesson before it is persisted: the
symptom -> root cause -> fix shape, no identifying content, and not a duplicate of what is already recorded."""
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.session.annotation_ai import _LESSONS_ANCHOR, _LESSONS_REL
from dftracer_agents.mcp_tools.tools.session.lessons_sync import (
    _BASE_BRANCH,
    _clone_fresh,
    _create_pull_request,
    _entry_context,
    _find_local_git_root,
    extract_new_entries,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _workspaces_root
from dftracer_agents.privacy import anonymize, find_identifiers

_BULLET = re.compile(r"^- \[L-[0-9a-f]{8}\] .+ -> .+ -> .+ \(\d{4}-\d{2}-\d{2}\)\s*$")
_BLOCK_FIELDS = ("context", "error", "root_cause", "fix", "tags")


def _lesson_verdict(entry: str, existing: str = "") -> Dict[str, Any]:
    """Rule on one proposed lesson: a capability LESSONS.md bullet, or a ---delimited lessons/pitfalls block."""
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    text = entry.strip()
    if not text:
        return {"status": "inconclusive", "checks": [{"check": "entry", "ok": False, "detail": "empty entry",
                                                      "fix": "pass the lesson text you intend to persist"}]}
    if text.startswith("- [L-"):
        add("shape", bool(_BULLET.match(text)), "LESSONS.md bullet" if _BULLET.match(text) else "bullet is not '- [L-xxxxxxxx] symptom -> root cause -> fix (YYYY-MM-DD)'",
            "write one line: - [L-<8 hex>] <symptom> -> <root cause> -> <fix> (<date>)")
    else:
        body = text.strip("-\n ")
        have = {f for f in _BLOCK_FIELDS if re.search(rf"(?m)^{f}:\s*\S", body)}
        if re.search(r"(?m)^fix:\s*\|?\s*$", body) and re.search(r"(?m)^fix:\s*\|\s*\n\s+\S", body):
            have.add("fix")
        if re.search(r"(?m)^error:\s*\|\s*\n\s+\S", body):
            have.add("error")
        need = [f for f in _BLOCK_FIELDS if f not in have]
        add("shape", not need, "missing: " + ", ".join(need) if need else "context/error/root_cause/fix/tags present",
            "use the block format: context, error (exact message), root_cause, fix (exact steps), tags; date for the lessons cache")
    hits = sorted(set(find_identifiers(text)))
    add("privacy", not hits, ", ".join(hits[:6]) or "no identifying content",
        "redact before you persist: $USER, $PROJECT_ROOT, $LUSTRE_ROOT, $HOME, <flux-jobid>, <uuid>, <system><node>")
    ctx = _entry_context(text.strip("-\n "))
    dup = bool(existing) and ((ctx and ctx in existing) or (not ctx and text in existing))
    add("new", not dup, "already recorded" if dup else "not yet recorded",
        "do not repeat a known lesson: refine the existing entry instead")
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
    @mcp.tool()
    def session_lessons_sync_preview(run_id: str = "") -> str:
        """Preview the lessons that would be synced back to the source repo.

        Diffs the workspace-staged ``dftracer-annotation-lessons/SKILL.md``
        (written by ``session_ml_append_lesson``) against the source repo's
        current copy of the same file. Read-only — no git or network calls.
        Always call this before ``session_lessons_sync_pr`` to review what
        would be added.

        Args:
            run_id: Optional session identifier, used only to locate the
                workspaces root when called from inside a session context.

        Returns:
            JSON with ``status``, ``new_entry_count``, ``new_entries`` (list
            of the raw entry blocks that would be added), and ``source_repo``
            (the local git checkout or clone that would be used).
        """
        ws_root = _workspaces_root()
        staged_path = ws_root / _LESSONS_REL
        if not staged_path.exists():
            return _ok("No staged lessons file found — nothing to sync", new_entry_count=0, new_entries=[])
        # Lessons leave this machine here. Redact identity and machine-local
        # paths at the read boundary so nothing unredacted can reach a PR.
        staged_text = anonymize(staged_path.read_text(errors="replace"))

        repo_root = _find_local_git_root()
        cleanup_dir: Optional[Path] = None
        if repo_root is None:
            repo_root = _clone_fresh()
            cleanup_dir = repo_root
        try:
            source_path = repo_root / "src" / "dftracer_agents" / _LESSONS_REL
            source_text = source_path.read_text(errors="replace") if source_path.exists() else ""
            new_entries = extract_new_entries(staged_text, source_text)
            return _ok(
                f"{len(new_entries)} new lesson entr{'y' if len(new_entries) == 1 else 'ies'} would be synced",
                new_entry_count=len(new_entries),
                new_entries=new_entries,
                source_repo=str(repo_root),
                used_fresh_clone=cleanup_dir is not None,
            )
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    @mcp.tool()
    def session_lessons_sync_pr(run_id: str = "", confirm: bool = False) -> str:
        """Open a PR against llnl/dftracer-agents with new staged lesson entries.

        Creates a branch, commits the new entries into
        ``src/dftracer_agents/.agents/skills/dftracer-annotation-lessons/SKILL.md``,
        pushes it, and opens a pull request via the GitHub REST API. Operates
        on the local dev checkout if one is found (editable install); clones
        the repo fresh into a temp directory otherwise.

        This is a **visible, hard-to-reverse action against a real shared
        repository** — it never runs automatically. Always call
        ``session_lessons_sync_preview`` first to review the diff, and only
        call this with ``confirm=True`` after the user has approved it.

        Args:
            run_id: Optional session identifier, used only to locate the
                workspaces root when called from inside a session context.
            confirm: Must be explicitly ``True`` — the call is rejected
                otherwise, with zero git/network side effects.

        Returns:
            JSON with ``status``, and on success ``pr_url``, ``branch``,
            ``entry_count``; on failure an explanatory ``message``.
        """
        if not confirm:
            return _err(
                "confirm must be explicitly set to True — call "
                "session_lessons_sync_preview first to review the diff"
            )

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
        if not token:
            return _err("GITHUB_TOKEN or GH_TOKEN environment variable is not set")

        ws_root = _workspaces_root()
        staged_path = ws_root / _LESSONS_REL
        if not staged_path.exists():
            return _ok("No staged lessons file found — nothing to sync", entry_count=0)
        # Lessons leave this machine here. Redact identity and machine-local
        # paths at the read boundary so nothing unredacted can reach a PR.
        staged_text = anonymize(staged_path.read_text(errors="replace"))

        repo_root = _find_local_git_root()
        cleanup_dir: Optional[Path] = None
        if repo_root is None:
            repo_root = _clone_fresh()
            cleanup_dir = repo_root

        try:
            if cleanup_dir is None:
                # Operating on the user's own local dev checkout — never sweep
                # unrelated uncommitted work into this commit.
                status = subprocess.run(
                    ["git", "status", "--porcelain"], cwd=repo_root,
                    check=True, capture_output=True, text=True,
                )
                dirty = [
                    line for line in status.stdout.splitlines()
                    if not line.strip().endswith(str(Path("src") / "dftracer_agents" / _LESSONS_REL))
                ]
                if dirty:
                    return _err(
                        "Local dev checkout has unrelated uncommitted changes — "
                        "commit or stash them before syncing lessons, to avoid "
                        "sweeping unrelated work into this PR",
                        dirty_paths=[line[3:] for line in dirty],
                    )

            source_rel = Path("src") / "dftracer_agents" / _LESSONS_REL
            source_path = repo_root / source_rel
            source_text = source_path.read_text(errors="replace") if source_path.exists() else ""
            new_entries = extract_new_entries(staged_text, source_text)
            if not new_entries:
                return _ok("No new lesson entries to sync", entry_count=0)

            appended_block = "\n\n---\n" + "\n---\n".join(new_entries) + "\n"
            if _LESSONS_ANCHOR in source_text:
                new_source_text = source_text.replace(
                    _LESSONS_ANCHOR, _LESSONS_ANCHOR + appended_block, 1
                )
            else:
                new_source_text = source_text + f"\n{_LESSONS_ANCHOR}\n" + appended_block
            source_path.parent.mkdir(parents=True, exist_ok=True)
            source_path.write_text(new_source_text)

            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            branch = f"lessons-sync/{ts}"

            def _git(*args: str) -> None:
                subprocess.run(
                    ["git", *args], cwd=repo_root, check=True,
                    capture_output=True, text=True,
                )

            _git("checkout", "-b", branch)
            _git("add", str(source_rel))
            _git(
                "commit", "-m",
                f"Sync {len(new_entries)} annotation lesson(s) from session {run_id or '(unspecified)'}",
            )
            _git("push", "origin", branch)

            title = f"Sync {len(new_entries)} annotation lesson(s) from a pipeline session"
            body = (
                f"Automated sync of {len(new_entries)} new entr"
                f"{'y' if len(new_entries) == 1 else 'ies'} appended to "
                f"`dftracer-annotation-lessons/SKILL.md` during session `{run_id or '(unspecified)'}`.\n\n"
                "Generated by `session_lessons_sync_pr` — please review before merging."
            )
            pr = _create_pull_request(token, base=_BASE_BRANCH, head=branch, title=title, body=body)

            return _ok(
                f"Opened PR with {len(new_entries)} new lesson entr{'y' if len(new_entries) == 1 else 'ies'}",
                pr_url=pr.get("html_url"),
                branch=branch,
                entry_count=len(new_entries),
            )
        finally:
            if cleanup_dir is not None:
                shutil.rmtree(cleanup_dir, ignore_errors=True)

    @mcp.tool()
    def dftracer_lessons_check(entry: str, existing_text: str = "") -> dict:
        """Validate one proposed lesson before it is persisted (the confirmation gate's deterministic half). Accepts a
        capability LESSONS.md bullet ('- [L-xxxxxxxx] symptom -> root cause -> fix (YYYY-MM-DD)') or a ---delimited
        lessons/pitfalls block (context, error, root_cause, fix, tags). Checks: shape; no identifying content (user paths,
        usernames, emails, job ids, UUIDs, hostnames); not already recorded in existing_text (same context: line or same
        text). Use before proposing or persisting any lesson. Returns {status: pass|fail|inconclusive, checks: [{check,
        ok, detail, fix}]}."""
        return _lesson_verdict(entry, existing_text)

    @mcp.tool()
    def dftracer_lessons_selftest() -> dict:
        """Self-test dftracer_lessons_check: a well-formed pitfall block and a well-formed bullet pass; a block without
        fix fails on shape; a block naming a fake user path fails on privacy; a block whose context is already recorded
        fails on new; extract_new_entries keeps only the unrecorded entry. Use to check the validator. Returns {status:
        pass|fail, detail}."""
        block = ("---\ncontext: Linker fails after annotation\nerror: |\n  undefined reference to 'dftracer_init'\n"
                 "root_cause: libdftracer_core not on the link line\nfix: |\n  add -ldftracer_core and an rpath\n"
                 "tags: [c, annotation, linker]\n---")
        bullet = "- [L-0123abcd] linker error after annotation -> core not linked -> add -ldftracer_core and rpath (2026-07-30)"
        nofix = block.replace("fix: |\n  add -ldftracer_core and an rpath\n", "")
        leak = block.replace("libdftracer_core not on the link line", "lib in " + "/usr/" + "WS9" + "/someone/lib")
        r = {
            "block": _lesson_verdict(block)["status"],
            "bullet": _lesson_verdict(bullet)["status"],
            "nofix": [c["check"] for c in _lesson_verdict(nofix)["checks"] if not c["ok"]],
            "leak": [c["check"] for c in _lesson_verdict(leak)["checks"] if not c["ok"]],
            "dup": [c["check"] for c in _lesson_verdict(block, "context: Linker fails after annotation")["checks"] if not c["ok"]],
        }
        anchor = _LESSONS_ANCHOR
        staged = f"head\n{anchor}\n---\ncontext: old one\nfix: x\n---\ncontext: new one\nfix: y\n"
        new = extract_new_entries(staged, f"head\n{anchor}\n---\ncontext: old one\nfix: x\n")
        r["extract"] = len(new) == 1 and "new one" in new[0]
        ok = (r["block"] == "pass" and r["bullet"] == "pass" and r["nofix"] == ["shape"] and r["leak"] == ["privacy"]
              and r["dup"] == ["new"] and r["extract"])
        return {"status": "pass" if ok else "fail", "detail": str(r)}
