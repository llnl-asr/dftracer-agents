"""dftracer_skills: the dftracer-agents knowledge surface for any harness. List, search and load the bundled skills
(whole, one section, or a sibling file), re-render agents, permissions and hooks from their canonical templates, and
list/recall/read the git-tracked project memory; validate a skill or memory file before it is persisted."""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from dftracer_agents.mcp_tools.tools.dftracer.skills_service import (
    _extract_wikilinks,
    _iter_skills,
    _match_section,
    _parse_frontmatter,
    _score,
    _split_sections,
)
from dftracer_agents.privacy import find_identifiers


def _skill_verdict(text: str, dirname: str = "", kind: str = "skill", known: Optional[Set[str]] = None) -> Dict[str, Any]:
    """Rule on one SKILL.md or memory file: frontmatter, name, description, sections, links, privacy, memory type."""
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    meta, body = _parse_frontmatter(text)
    add("frontmatter", bool(meta), "frontmatter parsed" if meta else "no ---frontmatter--- block",
        "start the file with ---, name: and description: lines, then ---")
    name = meta.get("name", "")
    add("name", bool(name) and (not dirname or name == dirname or kind == "memory"),
        f"name {name!r}" + (f" vs directory {dirname!r}" if dirname else ""),
        "the frontmatter name must equal the skill directory name")
    add("description", len(meta.get("description", "").strip(">| ")) >= 20 or bool(re.search(r"(?m)^description:\s*[>|]\s*$", text)),
        "description present" if meta.get("description") else "no description",
        "write a description that says what the skill covers and when to load it (it decides when the skill is loaded)")
    if kind == "skill":
        secs = _split_sections(body)
        add("sections", bool(secs), f"{len(secs)} '## ' section(s)", "structure the body in '## ' sections so skill_load(section=...) can fetch one")
        if known is not None:
            bad = [l for l in _extract_wikilinks(text) if l not in known]
            add("links", not bad, f"unresolved [[links]]: {bad}" if bad else "every [[link]] names a known skill",
                "fix the link to an existing skill name (skill_list), or create that skill")
    else:
        mt = re.search(r"(?m)^\s*type:\s*(\S+)", text)
        t = mt.group(1) if mt else ""
        add("memory_type", t in ("project", "feedback", "reference"), f"type {t!r}",
            "memory is anonymous and git-tracked: type project|feedback|reference, never user")
    hits = sorted(set(find_identifiers(text)))
    add("privacy", not hits, ", ".join(hits[:6]) or "no identifying content",
        "redact before you persist ($USER, $PROJECT_ROOT, $LUSTRE_ROOT, $HOME, <flux-jobid>, <uuid>, <system><node>)")
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
    @mcp.tool()
    def skill_list() -> str:
        """List every available dftracer skill with its name and description.

        Skills are Markdown playbooks covering annotation, build/run,
        optimization, system detection, and workloads. Call this first to see
        what's available, then ``skill_load`` the ones relevant to the task.

        Returns JSON: {status, count, skills: [{name, description}]}.
        """
        try:
            skills = _iter_skills()
        except FileNotFoundError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        return json.dumps({
            "status": "ok",
            "count": len(skills),
            "skills": [
                {"name": s["name"], "description": s["description"]}
                for s in skills
            ],
            "usage": "Call skill_load(name=...) to pull a skill's full text into context.",
        }, indent=2)

    @mcp.tool()
    def skill_search(query: str, max_results: int = 5) -> str:
        """Rank skills by relevance to a free-text query.

        Matches query terms against skill name (highest weight), description,
        and body text. Use this when you know the task ("annotate C MPI code",
        "run with LD_PRELOAD", "tuolumne modules") but not the exact skill name.

        Args:
            query:       Free-text search, e.g. "annotate python ml workload".
            max_results: Maximum number of skills to return (default 5).

        Returns JSON: {status, query, matches: [{name, description, score}]}.
        """
        try:
            skills = _iter_skills()
        except FileNotFoundError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        terms = [t for t in re.split(r"\W+", query.lower()) if t]
        scored = [
            (s, _score(terms, s)) for s in skills
        ]
        scored = [pair for pair in scored if pair[1] > 0]
        scored.sort(key=lambda pair: pair[1], reverse=True)
        matches = [
            {"name": s["name"], "description": s["description"], "score": score}
            for s, score in scored[: max(1, max_results)]
        ]
        return json.dumps({
            "status": "ok",
            "query": query,
            "matches": matches,
            "usage": "Call skill_load(name=...) on the best match to load it.",
        }, indent=2)

    @mcp.tool()
    def skill_load(
        name: str,
        section: Optional[str] = None,
        file: str = "SKILL.md",
    ) -> str:
        """Return Markdown text from one or more skills — full file, one section, or a sibling file.

        Load a skill into context before acting on the task it covers. Accepts a
        single skill name or a comma-separated list to load several at once.
        Names match either the frontmatter ``name`` or the skill directory name.

        By default returns the entire ``SKILL.md``. Pass ``section`` to fetch
        only one ``## `` section instead (case-insensitive substring match
        against header text) — use this for large skills so you don't pull the
        whole file into context just to read one part. Pass ``file`` to load a
        sibling reference file within the skill's own directory instead of
        ``SKILL.md`` (e.g. an externalized troubleshooting doc or a lessons log
        — see that skill's ``SKILL.md`` for pointers to what sibling files
        exist and when to load them); sibling files have no frontmatter and are
        returned as-is, with ``section`` still applying if given.

        Every response includes ``related_skills`` — the ``[[skill-name]]``
        cross-references found in whatever text was actually returned. These
        are names only (never their content) so you know what to load next
        without grepping for brackets yourself.

        Args:
            name: Skill name, or comma-separated names, e.g.
                  "dftracer-annotate-c" or "dftracer-cheatsheet,dftracer-annotate-c".
            section: Optional ``## `` header text (or substring) to return only
                     that section. If no section matches, the response reports
                     ``available_sections`` instead of guessing.
            file: Filename within the skill's directory to load (default
                  "SKILL.md"). Use this to load sibling reference/log files.

        Returns JSON: {status, loaded: [{name, content, related_skills}], missing: [...]}.
        """
        try:
            skills = _iter_skills()
        except FileNotFoundError as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        by_name = {str(s["name"]).lower(): s for s in skills}
        requested = [n.strip() for n in name.split(",") if n.strip()]
        loaded, missing = [], []
        for req in requested:
            hit = by_name.get(req.lower())
            if not hit:
                missing.append(req)
                continue

            if file != "SKILL.md":
                sibling = Path(str(hit["path"])).parent / file
                if not sibling.is_file():
                    missing.append(f"{req} (file={file!r} not found)")
                    continue
                text = sibling.read_text(encoding="utf-8", errors="replace")
            else:
                text = str(hit["full"])

            entry: Dict[str, object] = {"name": hit["name"]}
            if section:
                sections = _split_sections(text)
                match_key = _match_section(section, sections)
                if match_key is None:
                    entry["error"] = f"No section matching {section!r}"
                    entry["available_sections"] = sorted(sections.keys())
                    loaded.append(entry)
                    continue
                text = sections[match_key]
                entry["section"] = match_key

            entry["content"] = text
            entry["related_skills"] = _extract_wikilinks(text)
            loaded.append(entry)

        result: Dict[str, object] = {"status": "ok", "loaded": loaded}
        if missing:
            result["missing"] = missing
            result["available"] = sorted(by_name.keys())
            if not loaded:
                result["status"] = "not_found"
        return json.dumps(result, indent=2)

    @mcp.tool()
    def agents_sync(target_root: Optional[str] = None) -> str:
        """Re-render every harness agent copy from the YAML agent templates.

        The canonical agent definitions are harness-neutral YAML templates
        under ``src/dftracer_agents/.agents/agents/*.yaml`` (shared prose in
        ``common-sections.yaml``). The files each harness actually reads —
        ``.claude/agents/*.md``, ``.opencode/agents/*.md``,
        ``.github/agents/*.agent.md``, ``.codex/agents/*.toml`` — are
        generated build artifacts.

        Call this after ANY self-learning edit to a template (or to
        ``common-sections.yaml``) so all four harnesses pick up the change.
        It is one-way and lossless: templates are the only source of truth,
        rendered copies are never merged back.

        Args:
            target_root: Workspace root to render into (default: the active
                project root).

        Returns JSON: {status, target, summary, changed: [...], conflicts: [...]}.
        """
        from dftracer_agents.agents import sync_agents

        try:
            result = sync_agents(
                target_root=Path(target_root) if target_root else None
            )
        except Exception as exc:  # surface template errors to the caller
            return json.dumps({"status": "error", "error": str(exc)})
        return json.dumps({
            "status": "ok" if not result["conflicts"] else "partial",
            "target": result["target"],
            "summary": result["summary"],
            "changed": result["changed"],
            "conflicts": result["conflicts"],
        }, indent=2)

    @mcp.tool()
    def permissions_sync() -> str:
        """Re-render the packaged permission templates from the canonical policy.

        The canonical policy is
        ``src/dftracer_agents/.agents/workspace/permissions.yaml`` (the same
        workspace/project-root/outside tiers documented in CLAUDE.md's
        Permission Tiers table). This regenerates the ``permissions`` key in
        the packaged ``.claude/settings.json`` and the ``permission`` key in
        the packaged ``.opencode/opencode.jsonc`` — both symlinked into every
        install target by ``dftracer-bootstrap-workspace``. Copilot has no
        equivalent path-scoped mechanism (only the per-agent tools:
        allowlist ``agents_sync`` already renders) and Codex intentionally
        can't accept sandbox/approval settings from a repo-tracked file, so
        neither is touched here — see ``permissions.py`` for why.

        Call this after editing ``permissions.yaml`` so both harnesses pick
        up the change.

        Returns JSON: {claude, opencode, mcp_tool_count, changed, summary}.
        """
        from dftracer_agents.permissions import sync_permissions

        try:
            result = sync_permissions()
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def hooks_sync(target_root: str = "") -> str:
        """Re-render the prompt-injection hooks for every harness.

        Companion to ``permissions_sync`` / ``agents_sync``. Writes the shared
        hook script plus each harness's wiring — Claude ``UserPromptSubmit``,
        Codex ``.codex/hooks.json``, and the OpenCode ``chat.message`` plugin —
        and installs the scripts into ``target_root`` as well, since a hook
        command is a relative path resolved against the project the harness
        runs in, not against this package.

        Call this after editing ``hooks.py``. Copilot has no hook mechanism and
        is reported as ``not_applicable``; its tool calls are still covered by
        the MCP context middleware.

        Returns JSON: {status, claude, codex, opencode, copilot, changed,
        target_scripts, advisories, summary}.
        """
        from pathlib import Path as _Path
        from dftracer_agents.hooks import ensure_hooks_setup
        from dftracer_agents.skills import resolve_default_target

        root = (_Path(target_root).expanduser().resolve() if target_root
                else resolve_default_target())
        try:
            return json.dumps(ensure_hooks_setup(target_root=root), indent=2)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)})

    @mcp.tool()
    def memory_list() -> str:
        """List the project's persistent memories (name + one-line description).

        Memory is the git-tracked, cross-session store at
        ``src/dftracer_agents/.agents/workspace/memory/`` — one markdown file
        per fact, indexed by ``MEMORY.md``. It works identically in every
        harness (Claude Code, OpenCode, Copilot): call this at session start
        to see what past sessions learned, then ``memory_read`` the relevant
        ones.

        Returns JSON: {status, count, memories: [{name, description, type, file}]}.
        """
        from dftracer_agents.bootstrap import bundled_memory_dir

        memories = []
        for path in sorted(bundled_memory_dir().glob("*.md")):
            if path.name == "MEMORY.md":
                continue
            entry: Dict[str, object] = {"file": path.name}
            m = re.match(r"^---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
            if m:
                for key in ("name", "description"):
                    km = re.search(rf"(?m)^{key}:\s*(.+)$", m.group(1))
                    if km:
                        entry[key] = km.group(1).strip()
                tm = re.search(r"(?m)^\s*type:\s*(.+)$", m.group(1))
                if tm:
                    entry["type"] = tm.group(1).strip()
            memories.append(entry)
        return json.dumps({
            "status": "ok",
            "count": len(memories),
            "memories": memories,
            "usage": "memory_read(name=...) for full text; memory_write(...) to record.",
        }, indent=2)

    @mcp.tool()
    def memory_recall(
        query: str,
        k: int = 5,
        types: str = "",
        expand_links: bool = True,
    ) -> str:
        """Search memory by relevance instead of listing all of it.

        ``memory_list`` dumps every memory's name and description and leaves the
        picking to you; this ranks them against *query* by matching the name,
        the description AND the body, then walks one hop of the ``[[wikilink]]``
        graph so a memory that names the right neighbour pulls it in too.

        Use it whenever you are about to do something the project may already
        have a lesson about — a build, a run, a site-specific step, an
        optimization — rather than reading memories at random.

        Args:
            query: free text; a task description or an error message both work.
            k: how many directly-matched memories to keep before link expansion.
            types: comma-separated filter, e.g. "feedback" or "project,feedback".
            expand_links: include 1-hop [[wikilink]] neighbours of the hits.

        Returns JSON: {status, count, results: [{name, description, type,
        score, via}], digest, usage}. ``via`` is "match" for a direct hit or the
        linking memory's name. Bodies are not included — ``memory_read`` those
        that matter.
        """
        from dftracer_agents.memory_graph import recall, render_digest

        wanted = [t.strip() for t in types.split(",") if t.strip()] or None
        try:
            results = recall(query, k=k, types=wanted, expand_links=expand_links)
        except Exception as exc:
            return json.dumps({"status": "error", "error": str(exc)})
        return json.dumps({
            "status": "ok" if results else "no_match",
            "count": len(results),
            "results": results,
            "digest": render_digest(results, budget_chars=2000),
            "usage": "memory_read(name=...) for the full text of any of these.",
        }, indent=2)

    @mcp.tool()
    def memory_read(name: str) -> str:
        """Return the full text of one or more memories (comma-separated names).

        ``name`` matches the frontmatter ``name`` or the filename stem.

        Returns JSON: {status, loaded: [{name, content}], missing: [...]}.
        """
        from dftracer_agents.bootstrap import bundled_memory_dir

        mem_dir = bundled_memory_dir()
        by_key: Dict[str, Path] = {}
        for path in mem_dir.glob("*.md"):
            if path.name == "MEMORY.md":
                continue
            by_key[path.stem.lower()] = path
            m = re.search(r"(?m)^name:\s*(.+)$", path.read_text()[:500])
            if m:
                by_key[m.group(1).strip().lower()] = path
        loaded, missing = [], []
        for req in [n.strip() for n in name.split(",") if n.strip()]:
            hit = by_key.get(req.lower())
            if hit:
                loaded.append({"name": req, "content": hit.read_text()})
            else:
                missing.append(req)
        result: Dict[str, object] = {"status": "ok" if loaded else "not_found", "loaded": loaded}
        if missing:
            result["missing"] = missing
            result["available"] = sorted({p.stem for p in mem_dir.glob("*.md")} - {"MEMORY"})
        return json.dumps(result, indent=2)

    @mcp.tool()
    def dftracer_skills_check(path: str = "", text: str = "", kind: str = "skill") -> dict:
        """Validate a dftracer-agents skill (kind='skill') or memory file (kind='memory') deterministically before it is
        persisted. Give an absolute path (a SKILL.md, a skill directory, or a memory .md) or the text itself. Checks:
        frontmatter parses; name present and equal to the skill directory; a description; '## ' sections; every
        [[wikilink]] names a skill that exists (skill_list); memory type is project|feedback|reference (never user); no
        identifying content. Use after editing a skill or before memory_write. Returns {status: pass|fail|inconclusive,
        checks: [{check, ok, detail, fix}]}."""
        dirname = ""
        if path:
            p = Path(path)
            if p.is_dir():
                p = p / "SKILL.md"
            if not p.is_file():
                return {"status": "inconclusive", "checks": [{"check": "file", "ok": False, "detail": "no such file",
                                                              "fix": "pass an absolute path to a SKILL.md, a skill directory or a memory file"}]}
            text = p.read_text(errors="replace")
            dirname = p.parent.name if kind == "skill" else ""
        if not text.strip():
            return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "nothing to check",
                                                          "fix": "pass path or text"}]}
        try:
            known = {str(s["name"]) for s in _iter_skills()} | {Path(str(s["path"])).parent.name for s in _iter_skills()}
        except FileNotFoundError:
            known = None
        return _skill_verdict(text, dirname, kind, known)

    @mcp.tool()
    def dftracer_skills_selftest() -> dict:
        """Self-test the skill/memory validator on fixtures: a well-formed skill whose links are known passes; a name that
        differs from its directory fails on name; an unknown [[link]] fails on links; a skill naming a fake user path fails
        on privacy; a memory of type user fails on memory_type; the section splitter and matcher find '## Rules' by prefix.
        Use to check the validator. Returns {status: pass|fail, detail}."""
        good = ("---\nname: demo-skill\ndescription: Demo skill used to test the validator of dftracer skills.\n---\n\n"
                "# Demo\n\n## Rules\nSee [[other-skill]].\n\n## Corner cases\nnone\n")
        known = {"demo-skill", "other-skill"}
        failed = lambda v: [c["check"] for c in v["checks"] if not c["ok"]]
        r = {
            "good": _skill_verdict(good, "demo-skill", "skill", known)["status"],
            "name": failed(_skill_verdict(good, "another-dir", "skill", known)),
            "links": failed(_skill_verdict(good.replace("[[other-skill]]", "[[missing-skill]]"), "demo-skill", "skill", known)),
            "privacy": failed(_skill_verdict(good.replace("none", "built in " + "/usr/" + "WS9" + "/someone/x"), "demo-skill", "skill", known)),
            "memory": failed(_skill_verdict("---\nname: m\ndescription: a memory about the build of the app here\nmetadata:\n  type: user\n---\n\nbody\n", "", "memory")),
        }
        secs = _split_sections(_parse_frontmatter(good)[1])
        r["section"] = _match_section("rul", secs)
        ok = (r["good"] == "pass" and r["name"] == ["name"] and r["links"] == ["links"] and r["privacy"] == ["privacy"]
              and r["memory"] == ["memory_type"] and r["section"] == "Rules")
        return {"status": "pass" if ok else "fail", "detail": json.dumps(r)}
