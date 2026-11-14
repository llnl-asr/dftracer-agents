"""dftracer_graph: context economy for every dftracer agent. Locate code and docs through a graphify knowledge graph
(this package's code, skills and agent definitions, or a session's application) instead of reading whole files, fetch
only the section a node owns, and verify that a graph is fresh and its nodes resolve to readable spans."""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.code_graph import (
    _content_root,
    _graphify,
    _load_nodes,
    _node_line,
    _pkg_root,
    _read_span,
    _resolve,
    _rewrite_paths,
    _score,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok


def _graph_verdict(graph: Path) -> Dict[str, Any]:
    """Rule on a graph.json: it parses, has nodes with ids and labels, carries markdown nodes, and every node resolves
    to an existing file and a line inside it under the graph's content root (<root>/graphify-out/graph.json)."""
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    try:
        nodes = _load_nodes(str(graph))
    except (OSError, ValueError) as e:
        return {"status": "fail", "checks": [{"check": "parse", "ok": False, "detail": f"{e}"[:200],
                                              "fix": "graph_ensure(force=True) rebuilds graph.json"}]}
    add("nodes", bool(nodes), f"{len(nodes)} nodes", "graphify found no code: the scanned tree needs code files (markdown is only extracted next to code); graph_ensure(force=True)")
    ids = [n.get("id") for n in nodes]
    bad = [n for n in nodes if not n.get("id") or not n.get("label")]
    add("ids", not bad and len(set(ids)) == len(ids), f"{len(bad)} node(s) without id/label, {len(ids) - len(set(ids))} duplicate id(s)",
        "rebuild the graph (graph_ensure(force=True)); a hand-edited graph.json is not supported")
    docs = [n for n in nodes if n.get("file_type") == "document"]
    add("docs", bool(docs), f"{len(docs)} document (markdown heading) node(s)",
        "skills/agents were not staged next to code or .graphifyignore dropped them: graph_ensure(force=True)")
    root = _content_root(str(graph))
    missing, stale = [], []
    for n in nodes:
        src = n.get("source_file")
        if not src:
            continue
        f = root / src
        if not f.is_file():
            missing.append(src)
            continue
        if n.get("file_type") == "document" or n.get("source_location"):
            try:
                nlines = len(f.read_text(encoding="utf-8", errors="replace").splitlines())
            except OSError:
                nlines = 0
            if _node_line(n) > max(nlines, 1):
                stale.append(f"{src}:L{_node_line(n)}")
    add("sources", not missing, f"missing: {sorted(set(missing))[:5]}" if missing else "every source_file exists",
        "the graph is stale (files moved or deleted): graph_ensure(force=True)")
    add("spans", not stale, f"beyond end of file: {stale[:5]}" if stale else "every node line is inside its file",
        "the graph is stale (files edited after the build): graph_ensure(force=True)")
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks, "nodes": len(nodes)}


def register(mcp):
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

    @mcp.tool()
    def dftracer_graph_check(graph_json: str) -> dict:
        """Validate a graphify knowledge graph deterministically: graph_json is the absolute path of a graph.json (the
        'graph' key graph_ensure/graph_query return; its content root is the directory above graphify-out). Checks: it
        parses; it has nodes, each with an id and a label and no duplicate id; it carries markdown (document) nodes so
        mode='docs' works; every node's source_file exists under the content root; every node's line is inside its file
        (not stale). Use after graph_ensure, or when graph_get_node says 'stale graph'. Returns {status:
        pass|fail|inconclusive, checks: [{check, ok, detail, fix}], nodes}."""
        g = Path(graph_json)
        if not g.is_file():
            return {"status": "inconclusive", "checks": [{"check": "graph_present", "ok": False, "detail": "no graph.json at that path",
                                                          "fix": "graph_ensure() (or graph_ensure(run_id=...)) and pass the returned 'graph'"}]}
        return _graph_verdict(g)

    @mcp.tool()
    def dftracer_graph_selftest() -> dict:
        """Self-test dftracer_graph_check and the node retriever on a fixture graph it writes to a temp dir (a skill
        markdown file with two headings, a Python file with one function): the fresh graph passes and _read_span returns
        exactly the second section; a node pointing past the end of its file fails on spans; a node whose file is gone
        fails on sources. Use to check the validator. Returns {status: pass|fail, detail}."""
        with tempfile.TemporaryDirectory() as d:
            root = Path(d) / "stage"
            (root / "graphify-out").mkdir(parents=True)
            (root / "SKILL.md").write_text("# Skill\n\n## Rules\nrule one\n\n## Corner cases\ncase one\ncase two\n")
            (root / "a.py").write_text("def f(x):\n    return x + 1\n\n\ndef g():\n    pass\n")
            nodes = [{"id": "skill", "label": "SKILL.md", "source_file": "SKILL.md", "source_location": "L1", "file_type": "document"},
                     {"id": "rules", "label": "Rules", "source_file": "SKILL.md", "source_location": "L3", "file_type": "document"},
                     {"id": "cases", "label": "Corner cases", "source_file": "SKILL.md", "source_location": "L6", "file_type": "document"},
                     {"id": "f", "label": "f()", "source_file": "a.py", "source_location": "L1", "file_type": "code"}]
            gp = root / "graphify-out" / "graph.json"
            gp.write_text(json.dumps({"nodes": nodes, "links": []}))
            good = dftracer_graph_check(str(gp))
            text = _read_span(root, nodes[2], nodes, 50)[0]
            span_ok = text.startswith("## Corner cases") and "case two" in text and "rule one" not in text
            gp.write_text(json.dumps({"nodes": nodes + [{"id": "x", "label": "Gone", "source_file": "SKILL.md", "source_location": "L99", "file_type": "document"}]}))
            stale = [c["check"] for c in dftracer_graph_check(str(gp))["checks"] if not c["ok"]]
            (root / "a.py").unlink()
            gp.write_text(json.dumps({"nodes": nodes}))
            gone = [c["check"] for c in dftracer_graph_check(str(gp))["checks"] if not c["ok"]]
        ok = good["status"] == "pass" and span_ok and stale == ["spans"] and gone == ["sources"]
        return {"status": "pass" if ok else "fail", "detail": f"good={good['status']} span_ok={span_ok} stale={stale} gone={gone}"}
