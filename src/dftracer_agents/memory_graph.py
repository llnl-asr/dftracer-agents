"""Relevance retrieval over the git-tracked markdown memory store.

The store (``.agents/workspace/memory/``) is a *linked* corpus: 57 of its 74
files carry ``[[wikilink]]`` references to sibling memories, 118 links in all.
Until this module those links were prose only — nothing read them — and the
only retrieval available was ``memory_list`` (dump all 74 name+description
pairs) plus ``memory_read`` (exact name). An agent had to eyeball-pick.

Retrieval here is deliberately a direct read of the store rather than a
``graph_query(mode="docs")`` call, for three reasons:

* **Size.** The whole corpus is ~230 KB / 74 files. Reading it costs a few ms,
  so there is nothing to amortise with an index.
* **Body text.** ``mode="docs"`` scores ``label + source_file`` only, so a
  memory whose *content* is relevant but whose title is not scores zero.
  Bodies are where the lessons actually live.
* **Staleness.** The graph rebuilds on a content hash; a memory written
  mid-session is invisible until then. Reading the directory is always current.

Scoring is lexical (weighted field match + saturating body frequency +
query-coverage), then the wikilink graph is walked one hop so a memory that
names the right neighbour pulls it in. Everything is pure and side-effect free
so it can be unit-tested without a server; ``recall()`` is the entry point.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

# Field weights. Name/description are curated one-liners so a hit there is a
# much stronger signal than a hit somewhere in a 3 KB body.
_W_NAME = 4.0
_W_DESC = 3.0
_W_BODY = 1.0

# Body term-frequency saturation: repeating a term 50 times must not outrank a
# memory that matches more of the query. Caps the per-term body bonus at +2.0.
_BODY_TF_CAP = 5

# A 1-hop neighbour inherits this fraction of the score that pulled it in.
_LINK_DECAY = 0.35

_LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
_FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n?", re.DOTALL)

_STOPWORDS = frozenset("""
and are but for from has have how not the that this was were what when
with you your can will its about into than then they there their
""".split())


def parse_links(text: str) -> List[str]:
    """Return the ``[[wikilink]]`` targets in *text*, lowercased and de-duped.

    Targets are stored as memory ``name`` slugs. A link may point at a memory
    that does not exist yet — the memory conventions explicitly allow that as a
    "worth writing later" marker — so callers must tolerate dangling targets.
    """
    seen, out = set(), []
    for raw in _LINK_RE.findall(text):
        # "[[name|alias]]" and "[[name#section]]" both resolve to the name.
        target = re.split(r"[|#]", raw, maxsplit=1)[0].strip().lower()
        if target and target not in seen:
            seen.add(target)
            out.append(target)
    return out


def _parse_frontmatter(text: str) -> Dict[str, str]:
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return {}
    block = m.group(1)
    fields: Dict[str, str] = {}
    for key in ("name", "description"):
        km = re.search(rf"(?m)^{key}:\s*(.+)$", block)
        if km:
            fields[key] = km.group(1).strip().strip('"').strip("'")
    tm = re.search(r"(?m)^\s*type:\s*(.+)$", block)
    if tm:
        fields["type"] = tm.group(1).strip()
    return fields


def _body_of(text: str) -> str:
    m = _FRONTMATTER_RE.match(text)
    return text[m.end():] if m else text


def load_memories(mem_dir: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Parse every memory file into a dict. ``MEMORY.md`` (the index) is skipped.

    Falls back to the filename stem when frontmatter omits ``name`` — several
    older entries predate the schema and would otherwise be unaddressable.
    """
    if mem_dir is None:
        from dftracer_agents.bootstrap import bundled_memory_dir
        mem_dir = bundled_memory_dir()
    mem_dir = Path(mem_dir)
    if not mem_dir.is_dir():
        return []

    out: List[Dict[str, Any]] = []
    for path in sorted(mem_dir.glob("*.md")):
        if path.name == "MEMORY.md":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        fields = _parse_frontmatter(text)
        body = _body_of(text)
        out.append({
            "name": (fields.get("name") or path.stem).strip(),
            "description": fields.get("description", ""),
            "type": fields.get("type", ""),
            "body": body,
            "links": parse_links(body),
            "file": path.name,
            "path": str(path),
            "chars": len(text),
        })
    return out


def tokenize(query: str) -> List[str]:
    """Query terms: >2 chars, de-duped, stopwords dropped.

    Hyphenated slugs are split too, so a query of "always-function-mode" also
    matches prose that says "function mode".
    """
    raw = re.split(r"[^\w.]+", query.lower())
    terms: List[str] = []
    seen = set()
    for tok in raw:
        for part in ({tok} | set(tok.split("-")) if "-" in tok else {tok}):
            part = part.strip(".")
            if len(part) > 2 and part not in _STOPWORDS and part not in seen:
                seen.add(part)
                terms.append(part)
    return terms


def score_memory(mem: Dict[str, Any], terms: Sequence[str]) -> float:
    """Weighted field match, saturating body frequency, scaled by query coverage.

    Coverage matters more than raw frequency: a memory matching 3 of 3 query
    terms once each is a better hit than one matching a single term 40 times.
    """
    if not terms:
        return 0.0
    name = mem["name"].lower().replace("-", " ")
    desc = mem["description"].lower()
    body = mem["body"].lower()

    total = 0.0
    matched = 0
    for term in terms:
        hit = False
        if term in name:
            total += _W_NAME
            hit = True
        if term in desc:
            total += _W_DESC
            hit = True
        count = body.count(term)
        if count:
            total += _W_BODY * (1.0 + min(count, _BODY_TF_CAP) * 0.2)
            hit = True
        if hit:
            matched += 1

    if not matched:
        return 0.0
    coverage = matched / len(terms)
    return total * (0.5 + 0.5 * coverage)


def recall(
    query: str,
    k: int = 5,
    types: Optional[Iterable[str]] = None,
    expand_links: bool = True,
    mem_dir: Optional[Path] = None,
    memories: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Rank memories against *query*, then walk one hop of the wikilink graph.

    Args:
        query: free text — a user prompt, a tool name + args, a task summary.
        k: how many *directly scored* hits to keep before link expansion.
        types: restrict to these frontmatter types (project/feedback/reference).
        expand_links: pull in 1-hop ``[[wikilink]]`` neighbours of the hits.
        mem_dir / memories: injection points for tests.

    Returns a ranked list of ``{name, description, type, score, via, file}``,
    where ``via`` is ``"match"`` for a direct hit or the linking memory's name
    for one pulled in by expansion. Bodies are NOT included — callers decide
    how much text to spend (see ``render_digest``).
    """
    pool = memories if memories is not None else load_memories(mem_dir)
    if types:
        wanted = {t.strip().lower() for t in types if t and t.strip()}
        if wanted:
            pool = [m for m in pool if m.get("type", "").lower() in wanted]
    terms = tokenize(query)
    if not terms or not pool:
        return []

    scored = [(score_memory(m, terms), m) for m in pool]
    hits = sorted(((s, m) for s, m in scored if s > 0), key=lambda p: (-p[0], p[1]["name"]))[:k]
    if not hits:
        return []

    by_name = {m["name"].lower(): m for m in pool}
    results: List[Dict[str, Any]] = []
    seen = set()
    for score, mem in hits:
        seen.add(mem["name"].lower())
        results.append({**_summary(mem), "score": round(score, 2), "via": "match"})

    if expand_links:
        for score, mem in hits:
            for target in mem["links"]:
                if target in seen:
                    continue
                neighbour = by_name.get(target)
                if neighbour is None:
                    continue  # dangling link — allowed by the memory conventions
                seen.add(target)
                results.append({
                    **_summary(neighbour),
                    "score": round(score * _LINK_DECAY, 2),
                    "via": mem["name"],
                })

    results.sort(key=lambda r: (-r["score"], r["name"]))
    return results


def _summary(mem: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": mem["name"],
        "description": mem["description"],
        "type": mem.get("type", ""),
        "file": mem.get("file", ""),
        "chars": mem.get("chars", 0),
    }


def render_digest(results: Sequence[Dict[str, Any]], budget_chars: int = 1200) -> str:
    """One line per memory, truncated to *budget_chars*.

    This is the shape injected automatically. It is intentionally
    descriptions-only: the full corpus is ~230 KB and injecting bodies on every
    turn would cost more context than it saves. The line names the memory so
    the agent can ``memory_read`` the ones that matter.
    """
    lines: List[str] = []
    used = 0
    for r in results:
        via = "" if r["via"] == "match" else f" (via {r['via']})"
        line = f"- {r['name']}: {r['description']}{via}"
        if used + len(line) + 1 > budget_chars:
            lines.append(f"- ... {len(results) - len(lines)} more (memory_recall for the rest)")
            break
        lines.append(line)
        used += len(line) + 1
    return "\n".join(lines)
