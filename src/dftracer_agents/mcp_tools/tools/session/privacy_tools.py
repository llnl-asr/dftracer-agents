"""MCP tools for privacy validation of everything this package persists.

Memory, lessons, skills and agent definitions are git-tracked and ship to other
people. We learn from experience but never record *who* ran it. These tools let
an agent scan those trees for identifying content and redact it deterministically
rather than eyeballing prose.

* :func:`privacy_scan`   — report identifying content; never writes.
* :func:`privacy_redact` — rewrite the offending files in place.

Both default to the persisted trees (``.agents/skills``, ``.agents/agents``,
``.agents/workspace``). A live session workspace under ``workspaces/<session>/``
is gitignored and intentionally NOT scanned by default: it holds real resolved
paths a run needs. **Exception:** anything under a ``final_report/`` directory
(anywhere in the tree, e.g. ``workspaces/<session>/final_report/``) IS always
scanned even though it lives inside a gitignored session workspace — it is the
one live-workspace artifact meant to be copied/pushed elsewhere, so it must be
privacy-clean like everything else this package persists. See
``dftracer-privacy-guard`` skill and Pipeline Policy rule 9.
"""
from __future__ import annotations

import getpass
import os
from pathlib import Path
from typing import List

from fastmcp import FastMCP

from ....privacy import (
    STRUCTURED_SUFFIXES,
    _patterns_file,
    anonymize,
    find_identifiers,
    learned_patterns,
    suspects,
)
from .workspace import _err, _ok

#: Trees whose contents are committed and shared, relative to the project root.
#: ``good-runs`` holds published reference artifacts — reports, run scripts, and
#: the patches under each ``final*/`` folder — and leaks just as readily as a skill.
#: ``scripts`` and the package source ship too: a sandbox mount path or a docstring
#: example is as much of a leak as a skill, and neither was covered before.
_PERSISTED = (
    "src/dftracer_agents/.agents/skills",
    "src/dftracer_agents/.agents/agents",
    "src/dftracer_agents/.agents/workspace",
    "good-runs",
    "scripts",
    "src/dftracer_agents",
)

_SUFFIXES = {
    ".md", ".yaml", ".yml", ".json", ".jsonc", ".txt",
    ".sh", ".patch", ".diff", ".par", ".log", ".cfg", ".ini",
    ".py",
}

#: Files that legitimately CONTAIN the identifier patterns because they define or
#: document them. Scanning these only ever produces false positives.
_EXCLUDE_SUFFIXES = (
    "src/dftracer_agents/privacy.py",
    "src/dftracer_agents/mcp_tools/tools/session/privacy_tools.py",
    "src/dftracer_agents/mcp_tools/tools/system/system_service.py",
    "src/dftracer_agents/.agents/skills/dftracer-privacy-guard/SKILL.md",
    "src/dftracer_agents/.agents/workspace/privacy_patterns.yaml",
)


def _package_root() -> Path:
    """The project root — the directory holding ``src/`` and ``good-runs/``."""
    return Path(__file__).resolve().parents[5]


def _current_users() -> List[str]:
    users = []
    for candidate in (os.environ.get("USER"), os.environ.get("LOGNAME")):
        if candidate:
            users.append(candidate)
    try:
        users.append(getpass.getuser())
    except Exception:
        pass
    return sorted(set(u for u in users if u))


def _is_final_report(path: Path) -> bool:
    """True if *path* lives under a ``final_report/`` directory anywhere in its
    parents — this is the one artifact inside a gitignored session workspace
    that must ALWAYS be scanned (see module docstring / Pipeline Policy rule 9),
    regardless of what ``git check-ignore`` says about the rest of the
    workspace it happens to sit in.
    """
    return "final_report" in path.parts


def _is_ignored(path: Path) -> bool:
    """True if git ignores *path* — gitignored files never ship, so never scan
    them. EXCEPT anything under ``final_report/``, which is always scanned
    even though the session workspace containing it is gitignored (see
    :func:`_is_final_report`).
    """
    if _is_final_report(path):
        return False

    import subprocess

    try:
        return (
            subprocess.run(
                ["git", "check-ignore", "-q", str(path)],
                cwd=_package_root(),
                capture_output=True,
            ).returncode
            == 0
        )
    except Exception:
        return False


def _targets(paths: List[str] | None) -> List[Path]:
    root = _package_root()
    roots = [root / p for p in (paths or _PERSISTED)]
    files: List[Path] = []
    for r in roots:
        if r.is_file():
            files.append(r)
        elif r.is_dir():
            files.extend(
                p for p in r.rglob("*") if p.is_file() and p.suffix in _SUFFIXES
            )
    return sorted(
        f
        for f in set(files)
        if not _is_ignored(f) and not _defines_the_patterns(f)
    )


def _defines_the_patterns(path: Path) -> bool:
    """True for files that document the identifier patterns they'd otherwise trip."""
    posix = path.as_posix()
    return any(posix.endswith(suffix) for suffix in _EXCLUDE_SUFFIXES)


def register_privacy_tools(mcp: FastMCP) -> None:
    @mcp.tool()
    def privacy_scan(paths: List[str] | None = None) -> str:
        """Report identifying content in the persisted (git-tracked) trees.

        Read-only. Returns one entry per offending file with the exact
        substrings that would be redacted, so a reviewer can see what leaked
        before anything is rewritten.

        Args:
            paths: Package-relative paths to scan. Defaults to
                ``.agents/skills``, ``.agents/agents``, ``.agents/workspace``.
        """
        users = _current_users()
        findings = []
        for f in _targets(paths):
            try:
                hits = find_identifiers(f.read_text(errors="replace"), extra_users=users)
            except OSError:
                continue
            if hits:
                uniq = sorted(set(hits))
                findings.append(
                    {
                        "file": str(f.relative_to(_package_root())),
                        "count": len(hits),
                        "identifiers": uniq[:20],
                    }
                )
        if not findings:
            return _ok("clean: no identifying content found", files_scanned=len(_targets(paths)))
        return _ok(
            f"{len(findings)} file(s) contain identifying content",
            clean=False,
            findings=findings,
        )

    @mcp.tool()
    def privacy_redact(paths: List[str] | None = None, dry_run: bool = False) -> str:
        """Redact identifying content from the persisted trees, in place.

        Replaces absolute user paths with ``$PROJECT_ROOT`` / ``$HOME`` /
        ``$LUSTRE_ROOT``, usernames with ``$USER``, emails with
        ``<redacted-email>``, and collapses flux job ids, session UUIDs and node
        hostnames into placeholders. Citation lines are left untouched.

        Args:
            paths: Package-relative paths. Defaults to the persisted trees.
            dry_run: Report what would change without writing.
        """
        users = _current_users()
        changed = []
        for f in _targets(paths):
            try:
                original = f.read_text(errors="replace")
            except OSError:
                continue
            # Unified diffs get identity-only redaction: collapsing an absolute
            # path would change its component count and break `patch -pN`.
            scrubbed = anonymize(
                original,
                extra_users=users,
                preserve_structure=f.suffix in STRUCTURED_SUFFIXES,
            )
            if scrubbed != original:
                if not dry_run:
                    f.write_text(scrubbed)
                changed.append(str(f.relative_to(_package_root())))
        if not changed:
            return _ok("nothing to redact")
        verb = "would redact" if dry_run else "redacted"
        return _ok(f"{verb} {len(changed)} file(s)", dry_run=dry_run, files=changed)

    @mcp.tool()
    def privacy_suspects(paths: List[str] | None = None, limit: int = 40) -> str:
        """Hunt for corner cases the current redaction rules do not yet cover.

        Runs heuristic probes (home-like paths, emails, SSH URLs, opaque ids,
        hostnames, IPs, API-key shapes, uid/gid) over the persisted trees and
        reports only what ``anonymize`` leaves behind. A hit is a *candidate*,
        not a leak — review it, and if it is real, persist a rule with
        ``privacy_add_pattern`` so every future session catches it automatically.

        Args:
            paths: Package-relative paths. Defaults to the persisted trees.
            limit: Maximum candidates to return.
        """
        users = _current_users()
        seen: dict = {}
        for f in _targets(paths):
            try:
                text = f.read_text(errors="replace")
            except OSError:
                continue
            for probe, token in suspects(text, extra_users=users):
                seen.setdefault((probe, token), str(f.relative_to(_package_root())))
        if not seen:
            return _ok("no uncovered candidates — existing rules cover everything scanned")
        items = [
            {"probe": p, "text": t, "first_seen_in": where}
            for (p, t), where in list(seen.items())[:limit]
        ]
        return _ok(
            f"{len(seen)} candidate(s) not covered by current rules",
            covered=False,
            candidates=items,
            hint="review each; persist real ones with privacy_add_pattern",
        )

    @mcp.tool()
    def privacy_add_pattern(
        name: str,
        regex: str,
        replacement: str,
        must_redact: List[str],
        must_not_change: List[str],
        structure_safe: bool = True,
        note: str = "",
    ) -> str:
        """Persist a new redaction rule, after proving it works and breaks nothing.

        The rule is appended to the git-tracked
        ``.agents/workspace/privacy_patterns.yaml``, so it ships to everyone and
        applies to every future session. This is how the privacy guard learns.

        The rule is REJECTED unless every ``must_redact`` sample is changed by it
        and every ``must_not_change`` sample is left byte-identical by the full
        rule set. That guard is what stops a greedy regex from quietly corrupting
        version strings, citations, or already-anonymous placeholders.

        Args:
            name: Short kebab-case id, e.g. ``slurm-jobid``.
            regex: Python regex. Groups may be referenced in *replacement*.
            replacement: Substitution text, e.g. ``<slurm-jobid>`` or ``\\1$USER``.
            must_redact: Samples that MUST change. At least one required.
            must_not_change: Samples that MUST survive untouched. At least one
                required — include a realistic near-miss, not a trivial string.
            structure_safe: True if the rule preserves a path's component count,
                so it may also run on ``.patch`` / ``.diff``. Set False for rules
                that collapse paths.
            note: Why this rule exists — the corner case that motivated it.
        """
        import re as _re

        import yaml

        if not must_redact or not must_not_change:
            return _err("must_redact and must_not_change must each contain at least one sample")
        try:
            compiled = _re.compile(regex)
        except _re.error as exc:
            return _err(f"regex does not compile: {exc}")

        failed_redact = [s for s in must_redact if compiled.sub(replacement, s) == s]
        if failed_redact:
            return _err(
                "rule does not redact its own samples",
                samples_unchanged=failed_redact,
            )

        # Check against the FULL rule set, not just this regex: a new rule must
        # not regress anything the existing ones deliberately leave alone.
        entry = {
            "name": name,
            "regex": regex,
            "replacement": replacement,
            "structure_safe": bool(structure_safe),
        }
        if note:
            entry["note"] = note

        path = _patterns_file()
        existing = learned_patterns()
        if any(e.get("name") == name for e in existing):
            return _err(f"a pattern named {name!r} already exists")

        data = {"patterns": existing + [entry]}
        path.parent.mkdir(parents=True, exist_ok=True)
        original = path.read_text() if path.exists() else ""
        path.write_text(yaml.safe_dump(data, sort_keys=False))

        broke = []
        for sample in must_not_change:
            for ps in (False, True):
                if anonymize(sample, preserve_structure=ps) != sample:
                    broke.append({"sample": sample, "preserve_structure": ps})
        if broke:
            # Roll back — a rule that damages protected samples never lands.
            if original:
                path.write_text(original)
            else:
                path.unlink(missing_ok=True)
            return _err("rule rewrites content that must not change; rolled back", broke=broke)

        return _ok(
            f"pattern {name!r} added and validated",
            file=str(path.relative_to(_package_root())),
            total_learned=len(existing) + 1,
        )
