"""
DFTracer skill discovery and loading service.

Skills are Markdown playbooks bundled in the repo under ``.agents/skills/<name>/SKILL.md``.
Claude Code discovers these natively, but other MCP harnesses (Goose, opencode,
custom agents) have no built-in way to enumerate or read them.  This service
exposes them over MCP so *any* harness can list the available skills, search
them by keyword, and pull a specific skill's full text into its context on
demand.

Registers three FastMCP tools against a ``DFTracerSkills`` sub-server:

- ``skill_list``   — enumerate all skills with their name + description.
- ``skill_search`` — keyword-rank skills by name/description/body relevance.
- ``skill_load``   — return the full Markdown of one (or several) skills.

The skill directory is resolved at call time (never cached) so newly added or
edited skills are always visible without restarting the server.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory


# ---------------------------------------------------------------------------
# Skill directory resolution
# ---------------------------------------------------------------------------

def _candidate_skill_dirs() -> List[Path]:
    """Return candidate ``.agents/skills`` directories in priority order.

    Works in both editable/dev checkouts (skills at the repo root) and installed
    packages (skills symlinked/copied inside the package tree).  The first
    existing directory wins.
    """
    here = Path(__file__).resolve()
    # here = <root>/src/dftracer_agents/mcp_tools/tools/dftracer/skills_service.py
    pkg_root = here.parents[3]          # <root>/src/dftracer_agents
    repo_root = here.parents[5]         # <root>  (dev checkout, one above src/)
    candidates = [
        repo_root / ".agents" / "skills",       # dev checkout (canonical source)
        pkg_root / ".agents" / "skills",         # installed package (bundled data)
    ]
    # Also honour an installed dftracer_agents package if importable.
    try:  # pragma: no cover - depends on install layout
        import importlib.util

        spec = importlib.util.find_spec("dftracer_agents")
        if spec and spec.origin:
            candidates.append(Path(spec.origin).parent / ".agents" / "skills")
    except Exception:
        pass
    return candidates


def _skills_dir() -> Path:
    """Resolve the active skills directory, raising if none is found."""
    for cand in _candidate_skill_dirs():
        if cand.is_dir():
            return cand
    raise FileNotFoundError(
        "No .agents/skills directory found. Looked in: "
        + ", ".join(str(c) for c in _candidate_skill_dirs())
    )


# ---------------------------------------------------------------------------
# Frontmatter parsing
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, str], str]:
    """Split a SKILL.md into (frontmatter dict, body).

    Only the flat ``key: value`` pairs used by dftracer skills are parsed
    (``name`` and ``description``).  If no frontmatter is present, an empty
    dict and the original text are returned.
    """
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta: Dict[str, str] = {}
    for line in m.group(1).splitlines():
        if ":" in line and not line.lstrip().startswith("#"):
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip()
    return meta, text[m.end():]


_SECTION_RE = re.compile(r"^## +(.+?)\s*$", re.MULTILINE)
_WIKILINK_RE = re.compile(r"\[\[([\w-]+)\]\]")


def _split_sections(body: str) -> Dict[str, str]:
    """Split a skill body into ``## `` sections, keyed by header text (as written).

    Each value includes its own header line up to (not including) the next
    ``## `` header or end of file. Content before the first ``## `` header (if
    any) is not included — callers wanting that should just use the full body.
    """
    sections: Dict[str, str] = {}
    matches = list(_SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[m.group(1).strip()] = body[start:end].rstrip() + "\n"
    return sections


def _match_section(section: str, sections: Dict[str, str]) -> Optional[str]:
    """Resolve *section* against section header keys, preferring precise matches.

    Tries, in order: exact case-insensitive equality, case-insensitive prefix
    match, then case-insensitive substring match. This avoids a short query
    like "CRITICAL" incorrectly matching an unrelated header that merely
    contains the word in passing (e.g. "...Critical Rules...") ahead of the
    actual "## CRITICAL — ..." section.
    """
    target = section.lower()
    for key in sections:
        if key.lower() == target:
            return key
    for key in sections:
        if key.lower().startswith(target):
            return key
    for key in sections:
        if target in key.lower():
            return key
    return None


def _extract_wikilinks(text: str) -> List[str]:
    """Return deduped ``[[skill-name]]`` cross-references found in *text*, in order."""
    seen: List[str] = []
    for name in _WIKILINK_RE.findall(text):
        if name not in seen:
            seen.append(name)
    return seen


def _iter_skills() -> List[Dict[str, object]]:
    """Return metadata for every skill found under the active skills dir.

    Each entry: ``{name, description, path (str), body (str)}``.  ``name`` falls
    back to the directory name when frontmatter omits it.
    """
    root = _skills_dir()
    skills: List[Dict[str, object]] = []
    for skill_md in sorted(root.glob("*/SKILL.md")):
        try:
            text = skill_md.read_text(encoding="utf-8")
        except OSError:
            continue
        meta, body = _parse_frontmatter(text)
        skills.append({
            "name": meta.get("name") or skill_md.parent.name,
            "description": meta.get("description", ""),
            "path": str(skill_md),
            "body": body,
            "full": text,
        })
    return skills


# ---------------------------------------------------------------------------
# Search ranking
# ---------------------------------------------------------------------------

def _score(query_terms: List[str], skill: Dict[str, object]) -> int:
    """Weighted keyword score: name matches > description > body."""
    name = str(skill["name"]).lower()
    desc = str(skill["description"]).lower()
    body = str(skill["body"]).lower()
    score = 0
    for term in query_terms:
        if term in name:
            score += 10
        if term in desc:
            score += 4
        score += body.count(term)
    return score


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class DFTracerSkillsService(MCPService):
    """MCP service exposing the bundled dftracer skills to any harness.

    Attributes:
        skills_subservice (FastMCP): Internal ``FastMCP`` server named
            ``"DFTracerSkills"`` owning the three registered tools.  Exposed so
            ``mcp_server.py`` can mount its tools on the combined server.
    """

    def __init__(self) -> None:
        self.skills_subservice = FastMCP("DFTracerSkills")
        _register_skill_tools(self.skills_subservice)

    def execute(self, data: dict) -> Optional[str]:
        return "Use skill_list / skill_search / skill_load to discover and load dftracer skills."

    @property
    def name(self) -> str:
        return "dftracer-skills"


def _register_skill_tools(mcp: FastMCP) -> None:
    """Register ``skill_list``, ``skill_search``, and ``skill_load``."""

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
    def memory_write(
        name: str,
        description: str,
        body: str,
        type: str = "project",
    ) -> str:
        """Create or update a persistent memory, and keep the MEMORY.md index current.

        Use this at the END of every session to record cross-session facts:
        ongoing work and its state (``type=project``), user guidance on how to
        work (``type=feedback``), or external pointers (``type=reference``).
        Works identically in every harness — this tool, not any harness-native
        memory feature, is the portable write path.

        The content is anonymized deterministically before writing (usernames,
        absolute user paths, job ids, hostnames -> placeholders) because this
        store is git-tracked and ships to other people. ``type=user`` is
        rejected for the same reason. If ``name`` already exists the file is
        overwritten (update semantics).

        Args:
            name: kebab-case slug, e.g. "project-harness-agent-templates".
            description: one-line summary for the MEMORY.md index.
            body: the fact. For feedback/project include **Why:** and
                  **How to apply:** lines. Link related memories with [[name]].
            type: "project" | "feedback" | "reference".

        Returns JSON: {status, file, redacted: [...], index_updated}.
        """
        from dftracer_agents.bootstrap import bundled_memory_dir
        from dftracer_agents.privacy import anonymize, find_identifiers

        if type not in ("project", "feedback", "reference"):
            return json.dumps({
                "status": "error",
                "error": f"type={type!r} not allowed; memory is anonymous and git-tracked "
                         "(no 'user' profiles). Use project|feedback|reference.",
            })
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
        if not slug:
            return json.dumps({"status": "error", "error": "name must contain letters/digits"})

        text = (
            f"---\nname: {slug}\ndescription: {description}\n"
            f"metadata:\n  type: {type}\n---\n\n{body.rstrip()}\n"
        )
        # Bare usernames outside a path are only caught when passed explicitly.
        import getpass
        extra_users = [getpass.getuser()]
        redacted = find_identifiers(text, extra_users=extra_users)
        text = anonymize(text, extra_users=extra_users)

        mem_dir = bundled_memory_dir()
        dest = mem_dir / f"{slug}.md"
        existed = dest.exists()
        dest.write_text(text)

        index = mem_dir / "MEMORY.md"
        line = f"- [{slug}]({slug}.md) — {anonymize(description)}"
        lines = index.read_text().splitlines() if index.exists() else ["# Memory Index", ""]
        lines = [l for l in lines if f"]({slug}.md)" not in l]
        lines.append(line)
        index.write_text("\n".join(lines) + "\n")

        # The knowledge graph rebuilds on a content hash, so a memory written
        # mid-session stays invisible to graph_query(mode="docs") until the next
        # sweep. Marking dirty here is what makes a just-recorded lesson
        # retrievable in the same session. (memory_recall reads the store
        # directly and is unaffected, but the graph path must not go stale.)
        try:
            from dftracer_agents.mcp_tools.tools.session.code_graph import mark_graph_dirty
            mark_graph_dirty(f"memory_write:{slug}")
        except Exception:
            pass  # retrieval degrades to the next hash sweep; never fail a write

        return json.dumps({
            "status": "ok",
            "file": str(dest),
            "action": "updated" if existed else "created",
            "redacted": redacted,
            "index_updated": True,
        }, indent=2)


MCPServiceFactory.register("dftracer-skills", DFTracerSkillsService())
