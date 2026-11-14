"""dftracer_privacy: we learn from experience, we never record who ran it. Scan the persisted (git-tracked) trees of
dftracer-agents and every final_report/ for identifying content (usernames, absolute user paths, emails, flux job ids,
session UUIDs, node hostnames), redact it deterministically, hunt the corner cases the rules miss, and persist new
rules only after they prove they redact their samples and break nothing."""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.privacy_tools import (
    _current_users,
    _package_root,
    _targets,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok
from dftracer_agents.privacy import (
    STRUCTURED_SUFFIXES,
    _patterns_file,
    anonymize,
    find_identifiers,
    learned_patterns,
    suspects,
)

_TEXT_SUFFIXES = {".md", ".yaml", ".yml", ".json", ".jsonc", ".txt", ".sh", ".patch", ".diff", ".par", ".log", ".cfg",
                  ".ini", ".py"}


def _files(paths: List[str]) -> List[Path]:
    out: List[Path] = []
    for p in paths:
        r = Path(p)
        if r.is_file():
            out.append(r)
        elif r.is_dir():
            out.extend(sorted(x for x in r.rglob("*") if x.is_file() and x.suffix in _TEXT_SUFFIXES))
    return out


def _privacy_verdict(paths: List[str], extra_users: List[str] | None = None, allow: List[str] | None = None) -> Dict[str, Any]:
    """One check per file: no identifier find_identifiers knows (config.ini of a final_report is allowed real values)."""
    files = _files(paths)
    if not files:
        return {"status": "inconclusive", "checks": [{"check": "files", "ok": False, "detail": "no file found under the given paths",
                                                      "fix": "pass absolute paths of the files or trees to check"}]}
    users = sorted(set((extra_users or []) + _current_users()))
    allowed = set(allow if allow is not None else ["config.ini"])
    checks = []
    for f in files:
        if f.name in allowed and "final_report" in f.parts:
            continue
        try:
            hits = sorted(set(find_identifiers(f.read_text(errors="replace"), extra_users=users)))
        except OSError as e:
            checks.append({"check": str(f), "ok": False, "detail": f"unreadable: {e}", "fix": "check permissions"})
            continue
        checks.append({"check": str(f), "ok": not hits, "detail": ", ".join(hits[:10]) or "clean",
                       "fix": "" if not hits else ("privacy_redact (patches keep their structure: preserve_structure) then re-check; "
                                                    "a false positive is fixed in privacy.py, never tolerated")})
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "files_scanned": len(checks),
            "checks": [c for c in checks if not c["ok"]] or [{"check": "all", "ok": True, "detail": f"{len(checks)} file(s) clean", "fix": ""}]}


def register(mcp):
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

    @mcp.tool()
    def dftracer_privacy_check(paths: List[str], extra_users: List[str] | None = None) -> dict:
        """Validate deterministically that files are free of identifying content, with the same rules privacy_redact
        applies (privacy.find_identifiers: user paths, usernames, emails, flux job ids, UUIDs, hostnames, learned
        patterns). Takes ABSOLUTE file or directory paths (anywhere, e.g. a session's final_report/ or a capability
        directory), unlike privacy_scan's package-relative ones; config.ini inside a final_report/ is allowed real values.
        Use after privacy_redact, and before persisting any skill, lesson, memory or package. Returns {status:
        pass|fail|inconclusive, files_scanned, checks: [{check (file), ok, detail (identifiers), fix}]}."""
        return _privacy_verdict(list(paths or []), extra_users)

    @mcp.tool()
    def dftracer_privacy_selftest() -> dict:
        """Self-test the privacy validator on fixtures it writes to a temp dir: a note naming a fake user path under a
        /usr/WS<n>/<user> site must fail dftracer_privacy_check with that path found; after anonymize() it must pass; a
        clean file with near-misses (a version tag like torch==2.10.0+rocm710, an anonymous $USER path, a Citation line)
        must pass; redacting a .patch with preserve_structure keeps its line count and per-header '/' count. Use to check
        the validator and the rules. Returns {status: pass|fail, found_fake_path, detail}."""
        fake = "/usr/" + "WS9" + "/someone"
        with tempfile.TemporaryDirectory() as d:
            leak = Path(d) / "note.md"
            leak.write_text(f"Built in {fake}/app with gcc 12.\n")
            v_leak = dftracer_privacy_check([str(leak)])
            found = any(fake in c["detail"] for c in v_leak["checks"])
            leak.write_text(anonymize(leak.read_text()))
            v_red = dftracer_privacy_check([str(leak)])
            clean = Path(d) / "clean.md"
            clean.write_text("pip install torch==2.10.0+rocm710\nOutputs under /p/lustre5/$USER/run\n"
                             "**Citation:** A. Author, B. Author. A paper. 2024.\n")
            v_clean = dftracer_privacy_check([str(clean)])
            patch = f"--- a{fake}/app/x.c\n+++ b{fake}/app/x.c\n@@ -1 +1 @@\n-a\n+b\n"
            red = anonymize(patch, preserve_structure=True)
            hdr = lambda t: [l.count("/") for l in t.splitlines() if l.startswith(("---", "+++"))]
            structure = len(red.splitlines()) == len(patch.splitlines()) and hdr(red) == hdr(patch) and fake not in red
            empty = dftracer_privacy_check([str(Path(d) / "missing")])["status"]
        ok = (v_leak["status"] == "fail" and found and v_red["status"] == "pass" and v_clean["status"] == "pass"
              and structure and empty == "inconclusive")
        return {"status": "pass" if ok else "fail", "found_fake_path": found,
                "detail": f"leak={v_leak['status']} redacted={v_red['status']} near_misses={v_clean['status']} "
                          f"patch_structure_kept={structure} missing={empty}"}
