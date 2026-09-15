"""Knowledge-graph access for agents: locate code instead of reading it.

The dominant token cost in this pipeline is *input* — source an agent reads to
orient itself. ``graphify`` builds a tree-sitter graph over C/C++/Fortran/Python
plus markdown headings, so an agent can ask "where is X" and get
``file:line`` back for ~200 tokens instead of ~30,000.

Two graphs, two lifetimes:

* **knowledge graph** — this package's own code + its skills and agent
  definitions. Answers "where is the cost gate enforced", "what breaks if I
  change ``recommend()``".
* **session graph** — the target application being annotated/optimized, built
  inside that session's workspace. Answers the same questions about Flash-X or
  ScaFFold without reading 2600 files.

Deployment constraints this module works around (all measured, none guessable):

1. ``graphify`` writes its output to ``<scanned_path>/graphify-out`` and offers
   no ``--out`` flag. Scanning the installed package would therefore drop build
   artefacts inside ``site-packages``, which ``pip uninstall`` never removes. So
   we scan a **staged copy** under ``<workspaces_root>/_graph/`` instead.
2. ``graphify`` does **not** follow symlinked directories — a staged symlink
   tree yields "No code files found". The stage must contain real files.
3. A markdown-only tree also yields no graph; markdown is only extracted when
   code files are present in the same tree. So the stage carries both.
5. ``graphify`` honours ``.gitignore``. The cache lives under ``workspaces/``,
   which this repo gitignores, so scanning it reports "No code files found". A
   ``.graphifyignore`` at the scan root re-includes it (last-match-wins, same
   semantics as gitignore); ``_ensure_graphifyignore`` maintains that file.
4. Skills are self-updating: agents record lessons into them at runtime. The
   graph therefore rots. Freshness is keyed on a content hash of the staged
   inputs, plus a dirty flag any writer can set.

A rebuild costs ~4 s with no LLM and no API key, so the hash check is the
expensive-looking part that is actually free.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP

from .workspace import _workspaces_root, _ws, _ok, _err


#: Files worth graphing. Everything else is noise or unparseable.
_SRC_SUFFIXES = (".py", ".md", ".c", ".h", ".cpp", ".hpp", ".cc", ".F90", ".f90")

#: Never staged: build artefacts, caches, and graphify's own output.
_SKIP_DIRS = {"__pycache__", ".git", "graphify-out", "build", "dist",
              ".venv", "site-packages", ".pytest_cache", "node_modules"}



#: Negations that re-include the graph cache. ``graphify`` merges ``.gitignore``
#: and ``.graphifyignore`` per directory, last match wins — so these override the
#: repo's ``workspaces/`` ignore. Without them the stage graphs to zero nodes.
_GRAPHIFYIGNORE_MARK = "# dftracer-agents: re-include the knowledge-graph stage"
_GRAPHIFYIGNORE_BODY = f"""{_GRAPHIFYIGNORE_MARK}
# graphify honours .gitignore; the stage lives under a gitignored workspaces/
# directory and would otherwise report "No code files found".
#
# `!workspaces/` re-includes the DIRECTORY so traversal can descend into it.
# On its own it also re-includes everything under it -- every session, every
# trace, every copied source tree. That is ~88 GB on a working checkout, and a
# single graph_query over it was measured at 179 SECONDS, three quarters of a
# pipeline step's tool time. So exclude the contents, then re-include only the
# stage.
!workspaces/
workspaces/*
!workspaces/_graph/
!workspaces/_graph/**
"""


def _ensure_graphifyignore(scan_root: Path) -> None:
    """Make sure the cache is visible to graphify from *scan_root* upwards.

    Idempotent and additive: an existing ``.graphifyignore`` is appended to, never
    replaced, so a project's own rules survive.
    """
    f = scan_root / ".graphifyignore"
    try:
        existing = f.read_text() if f.exists() else ""
        if _GRAPHIFYIGNORE_MARK in existing:
            return
        f.write_text((existing.rstrip() + "\n\n" if existing.strip() else "")
                     + _GRAPHIFYIGNORE_BODY)
    except OSError:
        pass          # best effort: a read-only root just means a smaller graph



#: Matches a source path in graphify output: `src=a/b.py`, `Source: a/b.py L12`,
#: `- fn() [calls] a/b.py:L5`.
_PATH_RE = re.compile(r"(?<![\w/.])((?:[\w.\-]+/)*[\w.\-]+\.(?:py|md|c|h|cpp|hpp|cc|F90|f90))")


def _rewrite_paths(out: str, base: Path) -> str:
    """Rewrite stage-relative paths back to real, openable paths.

    The graph is built over a staged COPY, so every ``source_file`` is relative to
    the stage. Handing an agent ``mcp_tools/tools/session/x.py`` sends it to a file
    that does not exist. Map each path back onto *base* when it resolves there;
    leave it alone otherwise (never invent a path).
    """
    def sub(m: "re.Match") -> str:
        rel = m.group(1)
        cand = base / rel
        return str(cand) if cand.exists() else rel
    return _PATH_RE.sub(sub, out)


def _graph_root() -> Path:
    """Cache root: a sibling of the existing ``_memory/`` store."""
    p = _workspaces_root() / "_graph"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _pkg_root() -> Path:
    """The installed ``dftracer_agents`` package directory.

    Resolved from this module's own location, so it is correct for BOTH an
    editable install (``<repo>/src/dftracer_agents``) and a wheel install
    (``<venv>/lib/pythonX.Y/site-packages/dftracer_agents``). We only ever READ
    from it — all build artefacts go to the writable cache — so a read-only
    site-packages is fine.
    """
    return Path(__file__).resolve().parents[3]


def _iter_sources(root: Path):
    for f in root.rglob("*"):
        if not f.is_file() or f.suffix not in _SRC_SUFFIXES:
            continue
        if any(part in _SKIP_DIRS for part in f.relative_to(root).parts):
            continue
        yield f


def _content_hash(root: Path) -> str:
    """Hash path + content of every graphable file.

    Content, not mtime: a `git checkout` or a reinstall rewrites mtimes without
    changing anything, and we do not want a spurious 4 s rebuild for that.
    """
    h = hashlib.sha256()
    for f in sorted(_iter_sources(root)):
        h.update(str(f.relative_to(root)).encode())
        try:
            h.update(f.read_bytes())
        except OSError:
            continue
    return h.hexdigest()


def _stage(src: Path, stage: Path) -> int:
    """Copy graphable files into *stage* (real files — symlinks are not followed)."""
    if stage.exists():
        shutil.rmtree(stage)
    n = 0
    for f in _iter_sources(src):
        dest = stage / f.relative_to(src)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        n += 1
    return n


def _graphify(*args: str, timeout: int = 900) -> subprocess.CompletedProcess:
    return subprocess.run(["graphify", *args], capture_output=True, text=True,
                          timeout=timeout)


def _node_count(graph_json: Path) -> int:
    try:
        return len(json.loads(graph_json.read_text()).get("nodes", []))
    except Exception:
        return 0


# --------------------------------------------------------------------------
# Freshness
# --------------------------------------------------------------------------

def _state_path() -> Path:
    return _graph_root() / "state.json"


def _dirty_path() -> Path:
    return _graph_root() / "dirty"


def mark_graph_dirty(reason: str = "") -> None:
    """Flag the knowledge graph stale. Call after writing a skill or lesson.

    Cheap and idempotent. The next ``graph_ensure``/``graph_query`` rebuilds.
    Hash-checking already catches edits made through the filesystem; this exists
    so an in-process writer does not have to wait for the next hash sweep.
    """
    try:
        _dirty_path().write_text(f"{time.time()}\n{reason}\n")
    except OSError:
        pass


def _ensure_knowledge_graph(force: bool = False) -> Dict[str, Any]:
    """Rebuild the knowledge graph iff its inputs changed (or it is dirty)."""
    root = _graph_root()
    stage = root / "stage"
    graph_json = stage / "graphify-out" / "graph.json"

    pkg = _pkg_root()
    digest = _content_hash(pkg)

    state: Dict[str, Any] = {}
    if _state_path().exists():
        try:
            state = json.loads(_state_path().read_text())
        except Exception:
            state = {}

    dirty = _dirty_path().exists()
    fresh = (not force and not dirty
             and state.get("hash") == digest and graph_json.exists())
    if fresh:
        return {"rebuilt": False, "reason": "up to date",
                "graph": str(graph_json), "nodes": state.get("nodes", 0)}

    staged = _stage(pkg, stage)
    # The cache sits inside a (usually gitignored) workspaces/ tree. The ignore
    # rule lives beside that directory, so the negation must go there too — not in
    # the process cwd, which need not be the project root.
    _ensure_graphifyignore(_workspaces_root().parent)
    r = _graphify("update", str(stage), "--no-cluster")
    if not graph_json.exists():
        return {"rebuilt": False, "error": "graphify produced no graph",
                "staged_files": staged,
                "stderr": (r.stderr or r.stdout)[-400:]}

    nodes = _node_count(graph_json)
    _state_path().write_text(json.dumps(
        {"hash": digest, "nodes": nodes, "staged_files": staged,
         "built_at": time.strftime("%Y-%m-%dT%H:%M:%S")}, indent=2) + "\n")
    _dirty_path().unlink(missing_ok=True)
    return {"rebuilt": True,
            "reason": "forced" if force else ("dirty flag" if dirty else "inputs changed"),
            "graph": str(graph_json), "nodes": nodes, "staged_files": staged}


def _ensure_session_graph(run_id: str, force: bool = False) -> Dict[str, Any]:
    """Build the target application's graph inside its own workspace.

    The app tree is already writable and session-scoped, so it is scanned in
    place — no staging needed.
    """
    ws = _ws(run_id)
    src = ws / "annotated"
    if not src.is_dir():
        src = ws / "source"
    if not src.is_dir():
        return {"rebuilt": False, "error": f"no annotated/ or source/ tree for {run_id}"}

    graph_json = src / "graphify-out" / "graph.json"
    if graph_json.exists() and not force:
        return {"rebuilt": False, "reason": "exists (pass force=True to refresh)",
                "graph": str(graph_json), "nodes": _node_count(graph_json)}

    r = _graphify("update", str(src), "--no-cluster")
    if not graph_json.exists():
        return {"rebuilt": False, "error": "graphify produced no graph",
                "stderr": (r.stderr or r.stdout)[-400:]}
    return {"rebuilt": True, "graph": str(graph_json), "nodes": _node_count(graph_json)}


def _resolve(run_id: str, force: bool) -> Dict[str, Any]:
    return (_ensure_session_graph(run_id, force) if run_id
            else _ensure_knowledge_graph(force))


_LOC_RE = re.compile(r"L(\d+)")


def _content_root(graph: str) -> Path:
    """Directory the graph's ``source_file`` paths are relative to.

    Both graphs are built over a copy (the stage, or the session's ``annotated``
    tree), so line numbers only line up against that copy — never the pkg root.
    """
    return Path(graph).parents[1]


def _load_nodes(graph: str) -> List[Dict[str, Any]]:
    with open(graph, encoding="utf-8") as fh:
        return json.load(fh).get("nodes", [])


def _node_line(node: Dict[str, Any]) -> int:
    m = _LOC_RE.search(str(node.get("source_location") or ""))
    return int(m.group(1)) if m else 1


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _header_end(lines: List[str], start: int) -> int:
    """0-indexed line closing the definition header at 1-indexed *start*.

    A multi-line signature ends at the ``:`` that closes it at bracket depth 0 —
    not at the first dedent, since ``) -> bool:`` sits at the same indent as its
    own ``def``. Scan at most 40 lines; a header longer than that is not a header.
    """
    depth = 0
    for i in range(start - 1, min(start + 39, len(lines))):
        for ch in lines[i]:
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
        if depth <= 0 and lines[i].rstrip().endswith(":"):
            return i
    return start - 1


def _code_end(lines: List[str], start: int) -> int:
    """End of the definition beginning at 1-indexed *start*, by dedent.

    The next graph node in the file is NOT a usable end for code: graphify emits
    nodes for nested defs too, so a method containing an inner function would
    truncate to its signature. Scan past the header, then take the first non-blank
    line indented no deeper than the definition itself.
    """
    base = _indent(lines[start - 1])
    for i in range(_header_end(lines, start) + 1, len(lines)):
        line = lines[i]
        if line.strip() and _indent(line) <= base:
            return i  # 1-indexed line i+1 is outside; body ends at line i
    return -1


def _doc_end(nodes: List[Dict[str, Any]], node: Dict[str, Any]) -> int:
    """End of a markdown section: the line before the next heading in the file.

    graphify emits one node per heading with a start line and no end, so the
    next heading is exactly where this section stops.
    """
    src, start = node.get("source_file"), _node_line(node)
    later = [
        _node_line(n) for n in nodes
        if n.get("source_file") == src and _node_line(n) > start
    ]
    return min(later) - 1 if later else -1


def _read_span(
    root: Path,
    node: Dict[str, Any],
    nodes: List[Dict[str, Any]],
    max_lines: int,
) -> Tuple[str, int, int, bool]:
    """Extract the text *node* owns, choosing the end rule by node type."""
    lines = (root / node["source_file"]).read_text(
        encoding="utf-8", errors="replace").splitlines()
    start = _node_line(node)
    if start > len(lines):
        raise OSError(f"node starts at L{start} but file has {len(lines)} lines "
                      "(stale graph — run graph_ensure(force=True))")

    if node.get("label") == Path(node["source_file"]).name:
        end = -1  # the node IS the file (graphify emits one per file at L1)
    elif node.get("file_type") == "document":
        end = _doc_end(nodes, node)
    else:
        end = _code_end(lines, start)

    stop = len(lines) if end < 0 else min(end, len(lines))
    body = lines[start - 1:stop]
    truncated = len(body) > max_lines
    return "\n".join(body[:max_lines]), start, stop, truncated


def _score(node: Dict[str, Any], terms: List[str]) -> int:
    hay = f"{node.get('label','')} {node.get('source_file','')}".lower()
    return sum(1 for t in terms if t in hay)


def register_graph_tools(mcp: FastMCP) -> None:
    """Register ``graph_ensure``, ``graph_query`` and ``graph_get_node``.

    Deliberately three tools, not graphify's own MCP server (~25 schemas): this
    project already exposes 137 tool schemas, and schema text sits in context on
    every turn. Three wrappers buy guaranteed freshness and node retrieval for a
    negligible cost.
    """

    @mcp.tool()
    def graph_ensure(run_id: str = "", force: bool = False) -> str:
        """Ensure the knowledge graph (or a session's app graph) is fresh.

        Rebuild happens only when the inputs' content hash changed, when a writer
        marked the graph dirty (skills self-update at runtime), or when
        ``force=True``. A rebuild costs ~4 s and needs no LLM or API key.

        Args:
            run_id: Build the TARGET APPLICATION's graph inside that session's
                workspace. Omit to build/refresh this package's knowledge graph
                (pipeline code + skills + agent definitions).
            force: Rebuild even if the hash is unchanged.

        Returns:
            JSON with ``rebuilt``, ``reason``, ``graph`` (path to graph.json) and
            ``nodes``.
        """
        res = _resolve(run_id, force)
        if res.get("error"):
            return _err(res["error"], **res)
        return _ok(
            f"graph {'rebuilt' if res['rebuilt'] else 'up to date'} "
            f"({res.get('nodes', 0)} nodes)", **res)

    @mcp.tool()
    def graph_query(
        question: str = "",
        mode: str = "query",
        symbol: str = "",
        budget: int = 1200,
        depth: int = 2,
        run_id: str = "",
        context: List[str] | None = None,
        limit: int = 12,
    ) -> str:
        """Locate code and docs without reading them. Ensures graph freshness first.

        **Use this before Read/grep on any source tree.** Measured on this repo:
        locating via the graph cost ~986 tokens where reading the three relevant
        files cost ~29,456 (3.3%).

        Modes:

        * ``query``    — BFS for *question*; returns ``NODE <sym> [src=file loc=Lnn]``.
          Open only the files it names. Budget-capped.
        * ``docs``     — search only markdown nodes (skills, agent definitions,
          memory). Returns one ranked ``NODE`` line per heading with the ``id`` to
          pass to ``graph_get_node``. Use this instead of ``skill_load`` on a whole
          skill: BFS ``query`` leaks into the code subgraph and buries headings
          under generic helpers like ``_ok()``.
        * ``explain``  — definition, callers and callees of *symbol* (~200 tokens).
        * ``affected`` — reverse traversal: everything impacted by changing
          *symbol*. **Run this before editing any shared function** and state the
          blast radius; it is how you learn that changing ``recommend()`` also
          moves ``_estimate_file_impl`` -> ``_validate_python`` -> ``_plan``.

        Args:
            question: Natural-language target (``mode="query"`` / ``"docs"``).
            mode: ``query`` | ``docs`` | ``explain`` | ``affected``.
            symbol: Function/class name (``explain`` / ``affected``).
            budget: Token cap on ``query`` output. Raise only if truncated.
            depth: Reverse-traversal depth for ``affected``.
            run_id: Query the session's application graph instead of this
                package's knowledge graph.
            context: Edge-context filters narrowing a ``query`` BFS (repeatable),
                e.g. ``["call"]``.
            limit: Max rows returned by ``mode="docs"``.

        Returns:
            JSON with ``output`` (the graph answer) and ``graph`` (path used).
        """
        ensured = _resolve(run_id, force=False)
        if ensured.get("error"):
            return _err(ensured["error"], **ensured)
        graph = ensured["graph"]

        mode = mode.strip().lower()
        if mode == "docs":
            if not question:
                return _err("mode='docs' needs a `question`")
            terms = [t for t in re.split(r"\W+", question.lower()) if len(t) > 2]
            if not terms:
                return _err("mode='docs' needs at least one term longer than 2 chars")
            docs = [n for n in _load_nodes(graph) if n.get("file_type") == "document"]
            ranked = sorted(
                ((_score(n, terms), n) for n in docs),
                key=lambda p: -p[0],
            )
            hits = [(s, n) for s, n in ranked if s > 0][:limit]
            if not hits:
                return _err(f"no document node matches {question!r}", graph=graph)
            base = _pkg_root() if not run_id else _content_root(graph)
            lines = [
                f"NODE {n['label']} [id={n['id']} src={base / n['source_file']} "
                f"loc={n['source_location']} score={s}]"
                for s, n in hits
            ]
            lines.append("\nFetch any of these with graph_get_node(node=<id>).")
            return _ok("docs result", output="\n".join(lines), graph=graph,
                       rebuilt=ensured.get("rebuilt", False))
        if mode == "query":
            if not question:
                return _err("mode='query' needs a `question`")
            args = ["query", question, "--budget", str(budget), "--graph", graph]
            for ctx in context or []:
                args += ["--context", ctx]
        elif mode == "explain":
            if not symbol:
                return _err("mode='explain' needs a `symbol`")
            args = ["explain", symbol, "--graph", graph]
        elif mode == "affected":
            if not symbol:
                return _err("mode='affected' needs a `symbol`")
            args = ["affected", symbol, "--depth", str(depth), "--graph", graph]
        else:
            return _err(f"unknown mode {mode!r} (query|explain|affected)")

        try:
            r = _graphify(*args, timeout=180)
        except FileNotFoundError:
            return _err("the `graphify` CLI is not installed (pip install dftracer-agents)")
        except subprocess.TimeoutExpired:
            return _err("graphify timed out; narrow the question or lower --budget")

        out = (r.stdout or "").strip()
        if not out:
            return _err(f"no result (graphify said: {(r.stderr or '').strip()[-200:]})",
                        graph=graph)
        # Knowledge graph is built over a staged copy; map paths back to the real
        # package so `file:line` in the answer is actually openable.
        base = Path(graph).parents[1] if run_id else _pkg_root()
        return _ok(f"{mode} result", output=_rewrite_paths(out, base), graph=graph,
                   rebuilt=ensured.get("rebuilt", False))

    @mcp.tool()
    def graph_get_node(node: str, run_id: str = "", max_lines: int = 200) -> str:
        """Return the text a graph node owns — the retriever half of the graph.

        ``graph_query`` locates; this fetches. A heading node resolves to exactly
        its markdown section and a definition node to its body, so an agent can
        pull the one section it needs instead of a whole skill: loading
        ``dftracer-ml-annotate`` costs ~132K chars, one section costs hundreds.

        Args:
            node: Node ``id`` (exact, preferred — from ``graph_query``) or a node
                ``label``. Ambiguous labels return the candidate ids instead.
            run_id: Read from the session's application graph.
            max_lines: Truncate bodies longer than this.

        Returns:
            JSON with ``text``, ``source_file``, ``lines`` and ``truncated``.
        """
        ensured = _resolve(run_id, force=False)
        if ensured.get("error"):
            return _err(ensured["error"], **ensured)
        graph = ensured["graph"]
        nodes = _load_nodes(graph)

        want = node.strip()
        hits = [n for n in nodes if n.get("id") == want]
        if not hits:
            hits = [n for n in nodes if str(n.get("label", "")).lower() == want.lower()]
        if not hits:
            hits = [n for n in nodes if want.lower() in str(n.get("label", "")).lower()]
        if not hits:
            return _err(f"no node {want!r}; locate one with graph_query", graph=graph)
        if len(hits) > 1:
            return _err(
                f"{len(hits)} nodes match {want!r} — retry with an exact id",
                candidates=[{"id": n["id"], "src": n.get("source_file")} for n in hits[:10]],
                graph=graph)

        hit = hits[0]
        src = hit.get("source_file")
        if not src:
            return _err(f"node {want!r} has no source_file (external symbol)", graph=graph)
        try:
            text, start, stop, truncated = _read_span(
                _content_root(graph), hit, nodes, max_lines)
        except OSError as exc:
            return _err(f"cannot read {src}: {exc}", graph=graph)

        base = _pkg_root() if not run_id else _content_root(graph)
        return _ok(f"node {hit['label']}", text=text, id=hit["id"],
                   source_file=str(base / src), lines=f"L{start}-L{stop}",
                   truncated=truncated, graph=graph)
