"""dftracer_papers: academic paper and web search, the local paper/article library with RAG retrieval, and the optimization paper/context search, with an offline citation validator (dftracer_papers_check)."""
from __future__ import annotations

import asyncio
import httpx
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime
from datetime import timezone
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _BOTTLENECK_KEYWORD_EXPANSION
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _SYSTEM_KEYWORD_EXPANSION
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _detected_components
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _local_hits_for
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _remote_search_components
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _run_async
from dftracer_agents.mcp_tools.tools.optimizations.context_search import _save_hit_to_library
from dftracer_agents.mcp_tools.tools.papers.academic_service import ARXIV_BASE
from dftracer_agents.mcp_tools.tools.papers.academic_service import CORE_API_KEY
from dftracer_agents.mcp_tools.tools.papers.academic_service import OPENALEX_BASE
from dftracer_agents.mcp_tools.tools.papers.academic_service import OPENALEX_MAILTO
from dftracer_agents.mcp_tools.tools.papers.academic_service import S2_AUTHOR_FIELDS
from dftracer_agents.mcp_tools.tools.papers.academic_service import S2_BASE
from dftracer_agents.mcp_tools.tools.papers.academic_service import S2_PAPER_FIELDS
from dftracer_agents.mcp_tools.tools.papers.academic_service import _BOTTLENECK_KEYWORD_EXPANSION
from dftracer_agents.mcp_tools.tools.papers.academic_service import _SSL_VERIFY
from dftracer_agents.mcp_tools.tools.papers.academic_service import _SYSTEM_KEYWORD_EXPANSION
from dftracer_agents.mcp_tools.tools.papers.academic_service import _WEB_UA
from dftracer_agents.mcp_tools.tools.papers.academic_service import _arxiv_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _best_web_sections
from dftracer_agents.mcp_tools.tools.papers.academic_service import _core_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _crossref_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _dblp_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _expand_query_terms
from dftracer_agents.mcp_tools.tools.papers.academic_service import _fmt_paper
from dftracer_agents.mcp_tools.tools.papers.academic_service import _openalex_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _parse_arxiv_entry
from dftracer_agents.mcp_tools.tools.papers.academic_service import _parse_webpage
from dftracer_agents.mcp_tools.tools.papers.academic_service import _rate_limit
from dftracer_agents.mcp_tools.tools.papers.academic_service import _reconstruct_openalex_abstract
from dftracer_agents.mcp_tools.tools.papers.academic_service import _s2_headers
from dftracer_agents.mcp_tools.tools.papers.academic_service import _s2_search
from dftracer_agents.mcp_tools.tools.papers.academic_service import _score_paper_relevance
from dftracer_agents.mcp_tools.tools.papers.academic_service import _web_search_ddg
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _SSL_VERIFY
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _WEB_UA
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _extract_pdf_text
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _fuzzy_score
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _load_index
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _record_id
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _resources_dirs
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _save_index
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _slugify
from dftracer_agents.mcp_tools.tools.papers.local_library_service import _text_sidecar_path
from dftracer_agents.mcp_tools.tools.session.workspace import _err
from dftracer_agents.mcp_tools.tools.session.workspace import _load_state
from dftracer_agents.mcp_tools.tools.session.workspace import _ok
from dftracer_agents.mcp_tools.tools.session.workspace import _run
from dftracer_agents.mcp_tools.tools.session.workspace import _write_artifact_log
from dftracer_agents.mcp_tools.tools.session.workspace import _ws
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.optimizations.strategies import _BUILTIN_REFS

# --------------------------------------------------------------------------- validator helpers (deterministic, offline)
_LEVELS = ("L1", "L2", "L3")
_SCOPE_LEVEL = {"workload": "L1", "software": "L2", "system": "L3"}
_ARXIV_RX = re.compile(r"\b\d{4}\.\d{4,5}(?:v\d+)?\b")
_DOI_RX = re.compile(r"\b10\.\d{4,9}/\S+")
_URL_RX = re.compile(r"https?://[^\s/]+\.[^\s]+")
_YEAR_RX = re.compile(r"\b(?:19|20)\d{2}\b")
# "Doing less" is not optimizing: fewer epochs/steps, a smaller input, skipped checkpoints or output.
_DO_LESS_RX = re.compile(
    r"\b(?:reduce|reducing|fewer|cut|cutting|skip|skipping|drop|dropping|disable|disabling|remove|removing|shrink|smaller|lower|lowering)\b"
    r"[^.;\n]{0,40}?\b(?:epochs?|iterations?|steps?|timesteps?|dataset|samples|problem size|input size|checkpoints?|"
    r"output files?|outputs?|validation|tolerance|resolution)\b", re.I)


def _add(checks: List[dict], name: str, ok: bool, detail: str, fix: str = "") -> None:
    checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def _verdict(checks: List[dict], **extra) -> str:
    if not checks:
        return json.dumps({"status": "inconclusive", "checks": [], "reason": "nothing to check: give at least one input", **extra})
    failed = [c["check"] for c in checks if not c["ok"]]
    return json.dumps({"status": "fail" if failed else "pass", "checks": checks, "failed": failed, **extra}, default=str)


def _parse_json_arg(text: str):
    """JSON text, or the path of a JSON file. Raises ValueError with a readable message."""
    t = (text or "").strip()
    if not t:
        raise ValueError("empty input")
    if t[0] not in "[{\"" and Path(t).is_file():
        t = Path(t).read_text()
    try:
        return json.loads(t)
    except json.JSONDecodeError as e:
        raise ValueError(f"not valid JSON: {e}") from None


def _rows(data, keys=("rows", "proposals", "accepted", "findings", "papers", "citations", "entries")) -> List[Any]:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in keys:
            if isinstance(data.get(k), list):
                return data[k]
    return []


def _citation_problem(cit) -> str:
    """'' when the citation is usable, else why it is not."""
    if isinstance(cit, dict):
        if not str(cit.get("title") or "").strip():
            return "citation object has no title"
        if not (cit.get("url") or cit.get("doi") or (cit.get("venue") and cit.get("year"))):
            return "citation object needs url, doi, or venue and year"
        return ""
    c = str(cit or "").strip()
    if not c:
        return "no citation"
    if c.lower().startswith("session:"):
        return "" if len(c) > len("session:") else "session: citation without a run id"
    if c.lower() in _BUILTIN_REFS:
        return ""
    if _URL_RX.search(c) or _DOI_RX.search(c) or _ARXIV_RX.search(c):
        return ""
    if _YEAR_RX.search(c) and len(c) >= 15:
        return ""
    return "not a URL, DOI, arXiv id, built-in reference key or 'Author et al., Venue Year'"


def _level_of(row: dict) -> str:
    lv = str(row.get("level") or row.get("scope") or "").strip()
    if lv.upper() in _LEVELS:
        return lv.upper()
    return _SCOPE_LEVEL.get(lv.lower(), "")


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _row_text(row: dict) -> str:
    return " ".join(str(row.get(k) or "") for k in ("title", "change", "strategy", "description"))

# --------------------------------------------------------------------------- dftracer_papers_check
def _papers_check(citations_json: str) -> str:
    from dftracer_agents.mcp_tools.tools.optimizations.knowledge_base import _classify_citation
    checks: List[dict] = []
    try:
        rows = _rows(_parse_json_arg(citations_json))
    except ValueError as e:
        _add(checks, "citations_parse", False, str(e), "pass a JSON list of citations (strings or {title, url|doi|venue+year}), or optimization_papers.json")
        return _verdict(checks)
    _add(checks, "citations_present", bool(rows), f"{len(rows)} citation(s)", "search (opt_kb_lookup, rag_search, then the paper search tools) and cite what you found")
    bad = [f"#{i}: {p}" for i, c in enumerate(rows, 1) if (p := _citation_problem(c))]
    _add(checks, "citations_usable", not bad, "; ".join(bad)[:1500] or "every citation resolves to a URL, DOI, arXiv id, reference key or venue+year",
         "give each citation a URL/DOI/arXiv id, or title with venue and year")
    titles = [re.sub(r"\W+", " ", str(c.get("title") if isinstance(c, dict) else c)).strip().lower() for c in rows]
    dups = sorted({t for t in titles if t and titles.count(t) > 1})
    _add(checks, "no_duplicates", not dups, f"duplicates: {dups[:5]}" if dups else "ok", "deduplicate by title before citing")
    bad_ids = []
    for i, c in enumerate(rows, 1):
        s = json.dumps(c) if isinstance(c, dict) else str(c)
        for m in re.finditer(r"arxiv\.org/(?:abs|pdf)/([^\s\"'?#]+)", s, re.I):
            if not re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?(?:\.pdf)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", m.group(1)):
                bad_ids.append(f"#{i}: {m.group(1)}")
    _add(checks, "arxiv_ids_wellformed", not bad_ids, "; ".join(bad_ids) or "ok", "an arXiv id is YYMM.NNNNN (or archive/NNNNNNN)")
    kinds: Dict[str, int] = {}
    for c in rows:
        s = c.get("url") or c.get("doi") or c.get("title") if isinstance(c, dict) else c
        k = _classify_citation(str(s or "")) or "none"
        kinds[k] = kinds.get(k, 0) + 1
    return _verdict(checks, kinds=kinds)


# The original async tool bodies, unchanged except that calls to sibling tools name their coroutine.
async def _async_search_arxiv(
    query: str,
    max_results: int = 5,
    sort_by: str = "relevance",
    category: Optional[str] = None,
) -> str:
    """Search arXiv for academic papers.

    Args:
        query: Search query string (e.g. "parallel I/O optimization HDF5").
        max_results: Number of results to return (1-25, default 5).
        sort_by: Sort order — "relevance", "lastUpdatedDate", or "submittedDate".
        category: Optional arXiv category filter (e.g. "cs.DC", "cs.PF").

    Returns:
        Formatted list of matching papers with abstracts and URLs.
    """
    max_results = max(1, min(25, max_results))
    papers = await _arxiv_search(query, max_results, sort_by, category)
    if not papers:
        return f"No arXiv papers found for query: '{query}'"
    output = [f"arXiv search results for '{query}' ({len(papers)} papers):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_get_arxiv_paper(arxiv_id: str) -> str:
    """Fetch a single arXiv paper by its ID.

    Args:
        arxiv_id: The arXiv paper ID (e.g. "2310.06825" or "cs/0301001").

    Returns:
        Full details of the paper including abstract and PDF link.
    """
    arxiv_id = arxiv_id.strip().lstrip("arxiv:").lstrip("arXiv:")
    params = {"id_list": arxiv_id, "max_results": 1}
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(ARXIV_BASE, params=params)
        resp.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(resp.text)
    entries = root.findall("atom:entry", ns)
    if not entries:
        return f"No arXiv paper found with ID: {arxiv_id}"
    return f"arXiv Paper Details:\n\n{_fmt_paper(_parse_arxiv_entry(entries[0], ns))}"

async def _async_search_semantic_scholar(
    query: str,
    max_results: int = 5,
    year_range: Optional[str] = None,
    fields_of_study: Optional[str] = None,
    min_citations: Optional[int] = None,
) -> str:
    """Search Semantic Scholar for academic papers.

    Args:
        query: Search query string.
        max_results: Number of results (1-20, default 5).
        year_range: Optional year filter like "2020-2024" or "2023".
        fields_of_study: Comma-separated fields, e.g. "Computer Science".
        min_citations: Optional minimum citation count filter.

    Returns:
        Formatted list of matching papers.
    """
    max_results = max(1, min(20, max_results))
    papers = await _s2_search(query, max_results, year_range, fields_of_study)
    if min_citations is not None:
        papers = [p for p in papers if (p.get("citationCount") or 0) >= min_citations]
    if not papers:
        return f"No Semantic Scholar papers found for query: '{query}'"

    output = [f"Semantic Scholar results for '{query}' ({len(papers)} papers):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_get_semantic_scholar_paper(paper_id: str) -> str:
    """Fetch full details for a paper from Semantic Scholar.

    Args:
        paper_id: Semantic Scholar ID, DOI, or arXiv ID prefixed with "arXiv:"
            (e.g. "arXiv:2310.06825" or "DOI:10.18653/v1/2020.acl-main.196").

    Returns:
        Detailed paper information including references.
    """
    await _rate_limit("semantic_scholar")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(
            f"{S2_BASE}/paper/{paper_id}",
            params={"fields": S2_PAPER_FIELDS + ",references,citations"},
            headers=_s2_headers(),
        )
        if resp.status_code == 404:
            return f"Paper not found: {paper_id}"
        resp.raise_for_status()
        p = resp.json()

    authors  = [a.get("name", "") for a in p.get("authors", [])]
    pdf_url  = p.get("openAccessPdf", {}).get("url") if p.get("openAccessPdf") else None
    lines = [
        f"**{p.get('title', 'Untitled')}**",
        f"Authors: {', '.join(authors) or 'N/A'}",
        f"Year: {p.get('year', 'N/A')}",
        f"Citations: {p.get('citationCount', 'N/A')}",
        f"References: {p.get('referenceCount', 'N/A')}",
    ]
    if p.get("journal"):
        lines.append(f"Journal: {p['journal'].get('name', '')}")
    if p.get("abstract"):
        lines.append(f"\nAbstract:\n{p['abstract']}")
    if pdf_url:
        lines.append(f"\nPDF: {pdf_url}")
    if p.get("url"):
        lines.append(f"Semantic Scholar URL: {p['url']}")
    refs = p.get("references", [])
    if refs:
        lines.append(f"\nTop References ({min(5, len(refs))} of {len(refs)}):")
        for r in refs[:5]:
            rp = r.get("citedPaper", {})
            lines.append(f"  - {rp.get('title', 'N/A')} ({rp.get('year', '')})")
    return "\n".join(lines)

async def _async_get_author_papers(author_name: str, max_results: int = 5) -> str:
    """Search for an author on Semantic Scholar and retrieve their papers.

    Args:
        author_name: Full name of the author (e.g. "John Bent").
        max_results: Number of papers to return (1-20, default 5).

    Returns:
        Author profile and list of their papers sorted by citation count.
    """
    max_results = max(1, min(20, max_results))
    await _rate_limit("semantic_scholar")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        search_resp = await client.get(
            f"{S2_BASE}/author/search",
            params={"query": author_name, "limit": 1, "fields": S2_AUTHOR_FIELDS},
            headers=_s2_headers(),
        )
        search_resp.raise_for_status()
        authors = search_resp.json().get("data", [])
        if not authors:
            return f"No author found for: '{author_name}'"
        author    = authors[0]
        author_id = author["authorId"]
        await _rate_limit("semantic_scholar")
        papers_resp = await client.get(
            f"{S2_BASE}/author/{author_id}/papers",
            params={"limit": max_results, "fields": "title,year,citationCount,authors,url",
                    "sort": "citationCount"},
            headers=_s2_headers(),
        )
        papers_resp.raise_for_status()
        papers = papers_resp.json().get("data", [])

    lines = [
        f"**Author: {author.get('name', author_name)}**",
        f"Affiliations: {', '.join(author.get('affiliations', [])) or 'N/A'}",
        f"Total papers: {author.get('paperCount', 'N/A')}",
        f"Total citations: {author.get('citationCount', 'N/A')}",
        f"h-index: {author.get('hIndex', 'N/A')}",
        f"\nTop {len(papers)} papers (by citations):",
    ]
    for i, p in enumerate(papers, 1):
        lines.append(
            f"  [{i}] {p.get('title', 'N/A')} ({p.get('year', '?')}) "
            f"— {p.get('citationCount', 0)} citations"
        )
    return "\n".join(lines)

async def _async_search_openalex(query: str, max_results: int = 5) -> str:
    """Search OpenAlex for academic papers (250M+ works, no API key, no hard rate limit).

    Args:
        query: Search query string.
        max_results: Number of results to return (1-25, default 5).

    Returns:
        Formatted list of matching papers with abstracts and URLs.
    """
    max_results = max(1, min(25, max_results))
    papers = await _openalex_search(query, max_results)
    if not papers:
        return f"No OpenAlex papers found for query: '{query}'"
    output = [f"OpenAlex search results for '{query}' ({len(papers)} papers):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_get_openalex_paper(work_id: str) -> str:
    """Fetch full details for one OpenAlex work.

    Args:
        work_id: OpenAlex work ID (e.g. "W2741809807"), full OpenAlex URL,
            or a DOI (e.g. "10.1145/3295500.3356173").

    Returns:
        Detailed paper information.
    """
    work_id = work_id.strip()
    if work_id.startswith("10."):
        url = f"{OPENALEX_BASE}/works/https://doi.org/{work_id}"
    elif work_id.startswith("http"):
        url = work_id
    else:
        url = f"{OPENALEX_BASE}/works/{work_id}"
    params = {"mailto": OPENALEX_MAILTO} if OPENALEX_MAILTO else {}
    await _rate_limit("openalex")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(url, params=params)
        if resp.status_code == 404:
            return f"OpenAlex work not found: {work_id}"
        resp.raise_for_status()
        w = resp.json()
    oa = w.get("open_access") or {}
    primary = w.get("primary_location") or {}
    source = (primary.get("source") or {}) if primary else {}
    authors = [(a.get("author") or {}).get("display_name", "") for a in w.get("authorships", [])]
    lines = [
        f"**{w.get('display_name', 'Untitled')}**",
        f"Authors: {', '.join(authors) or 'N/A'}",
        f"Year: {w.get('publication_year', 'N/A')}",
        f"Citations: {w.get('cited_by_count', 'N/A')}",
    ]
    if source.get("display_name"):
        lines.append(f"Journal/Venue: {source['display_name']}")
    if w.get("doi"):
        lines.append(f"DOI: {w['doi']}")
    abstract = _reconstruct_openalex_abstract(w.get("abstract_inverted_index"))
    if abstract:
        lines.append(f"\nAbstract:\n{abstract}")
    pdf_url = oa.get("oa_url") or primary.get("pdf_url")
    if pdf_url:
        lines.append(f"\nPDF: {pdf_url}")
    lines.append(f"OpenAlex URL: {w.get('id', '')}")
    return "\n".join(lines)

async def _async_search_crossref(query: str, max_results: int = 5) -> str:
    """Search Crossref for academic papers by DOI metadata (no API key required).

    Best for resolving venues, DOIs, and citation counts; abstracts are
    often absent since Crossref indexes metadata, not full text.

    Args:
        query: Search query string.
        max_results: Number of results to return (1-25, default 5).

    Returns:
        Formatted list of matching papers.
    """
    max_results = max(1, min(25, max_results))
    papers = await _crossref_search(query, max_results)
    if not papers:
        return f"No Crossref papers found for query: '{query}'"
    output = [f"Crossref search results for '{query}' ({len(papers)} papers):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_search_core(query: str, max_results: int = 5) -> str:
    """Search CORE for open-access full-text papers.

    Requires a free CORE_API_KEY (register at https://core.ac.uk/services/api)
    set in the environment. Best source when a full-text PDF is needed
    rather than just an abstract.

    Args:
        query: Search query string.
        max_results: Number of results to return (1-25, default 5).

    Returns:
        Formatted list of matching papers, or a setup message if no
        CORE_API_KEY is configured.
    """
    if not CORE_API_KEY:
        return (
            "CORE_API_KEY is not set. Register a free key at "
            "https://core.ac.uk/services/api and set CORE_API_KEY in the "
            "environment to enable full-text open-access search."
        )
    max_results = max(1, min(25, max_results))
    papers = await _core_search(query, max_results)
    if not papers:
        return f"No CORE papers found for query: '{query}'"
    output = [f"CORE search results for '{query}' ({len(papers)} papers):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_search_dblp(query: str, max_results: int = 5) -> str:
    """Search DBLP for computer-science publications (no API key required).

    DBLP has no abstracts but is the most reliable source for CS venue/
    conference metadata and author disambiguation.

    Args:
        query: Search query string.
        max_results: Number of results to return (1-25, default 5).

    Returns:
        Formatted list of matching publications.
    """
    max_results = max(1, min(25, max_results))
    papers = await _dblp_search(query, max_results)
    if not papers:
        return f"No DBLP publications found for query: '{query}'"
    output = [f"DBLP search results for '{query}' ({len(papers)} publications):\n"]
    for i, paper in enumerate(papers, 1):
        output.append(f"[{i}] {_fmt_paper(paper)}\n")
    return "\n".join(output)

async def _async_search_web(query: str, max_results: int = 5) -> str:
    """General web search (DuckDuckGo) for papers, docs, and articles not in academic APIs.

    Use this to find vendor whitepapers, conference talk slides,
    engineering blog posts, or mailing-list threads that academic
    search engines don't index — a complement to, not a replacement
    for, ``search_papers_combined``. Pair with ``fetch_webpage_article``
    to pull full content from any result.

    Args:
        query: Search query string.
        max_results: Number of results to return (1-20, default 5).

    Returns:
        Formatted list of web results (title, url, snippet).
    """
    max_results = max(1, min(20, max_results))
    results = await _web_search_ddg(query, max_results)
    if not results:
        return f"No web results found for query: '{query}'"
    output = [f"Web search results for '{query}' ({len(results)} results):\n"]
    for i, r in enumerate(results, 1):
        output.append(
            f"[{i}] **{r['title']}**\n{r['snippet']}\nURL: {r['url']}\n"
        )
    return "\n".join(output)

async def _async_search_papers_combined(
    query: str,
    max_results_each: int = 3,
    include_web: bool = False,
) -> str:
    """Search arXiv, Semantic Scholar, OpenAlex, Crossref, and DBLP simultaneously.

    CORE is included automatically when CORE_API_KEY is configured.
    All sources are queried in parallel and each is individually
    rate-limited, so this call is safe to use even when Semantic
    Scholar's 1 req/sec budget is tight — the other sources fill in
    without waiting on it.

    Args:
        query: Search query string.
        max_results_each: Papers to fetch from each source (1-10, default 3).
        include_web: Also run a general web search (DuckDuckGo) for
            non-academic sources like vendor docs and blog posts.

    Returns:
        Combined, labeled results from every source.
    """
    max_results_each = max(1, min(10, max_results_each))
    tasks = {
        "arXiv": _async_search_arxiv(query, max_results=max_results_each),
        "Semantic Scholar": _async_search_semantic_scholar(query, max_results=max_results_each),
        "OpenAlex": _async_search_openalex(query, max_results=max_results_each),
        "Crossref": _async_search_crossref(query, max_results=max_results_each),
        "DBLP": _async_search_dblp(query, max_results=max_results_each),
    }
    if CORE_API_KEY:
        tasks["CORE"] = _async_search_core(query, max_results=max_results_each)
    if include_web:
        tasks["Web"] = _async_search_web(query, max_results=max_results_each)

    names = list(tasks.keys())
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)

    output = [f"Combined academic paper search for: '{query}'\n{'='*60}\n"]
    for name, result in zip(names, results):
        output.append(f"## {name} Results")
        output.append(str(result) if not isinstance(result, Exception)
                      else f"{name} error: {result}")
        output.append("")
    return "\n".join(output)

async def _async_fetch_webpage_article(
    url: str,
    query: Optional[str] = None,
    top_sections: int = 5,
) -> str:
    """Fetch and extract structured content from any webpage, blog post, or article.

    Retrieves the page at ``url``, strips navigation/ads/scripts, and
    returns title, description, and content sections.  When ``query`` is
    given the most relevant sections are ranked by keyword frequency.

    Args:
        url:          Full URL of any webpage or article to fetch.
        query:        Optional keywords to filter/rank sections by relevance.
        top_sections: Maximum number of content sections to return (default 5).

    Returns:
        JSON with:
          - url:         fetched URL
          - title:       page title
          - description: meta description if available
          - sections:    list of {heading, content, code_example?}
    """
    try:
        async with httpx.AsyncClient(
            timeout=30,
            follow_redirects=True,
            verify=_SSL_VERIFY,
            headers={"User-Agent": _WEB_UA},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            html_text = resp.text
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "url": url,
            "message": str(exc),
        }, indent=2)

    parsed = _parse_webpage(html_text)
    terms = (
        [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
        if query else []
    )
    sections = _best_web_sections(parsed, terms, top_n=top_sections)

    return json.dumps({
        "status": "ok",
        "url": url,
        "title": parsed["title"],
        "description": parsed.get("description", ""),
        "sections": sections,
    }, indent=2)

async def _async_save_paper(
    url: str,
    title: str = "",
    authors: str = "",
    year: Optional[int] = None,
    source: str = "",
    query: str = "",
    abstract: str = "",
    doi: str = "",
) -> str:
    """Download a paper PDF into the local library at resources/papers/.

    Extracts and caches the full text (via pypdf) so
    ``search_local_resources`` can fuzzy-search the paper body, not
    just its title/abstract. Idempotent — re-saving the same URL
    updates the existing entry instead of duplicating it.

    Args:
        url:      Direct PDF URL (e.g. an ``pdf_url`` from
            ``search_arxiv``/``search_openalex``/``search_papers_combined``).
        title:    Paper title (used for the stored filename and search).
        authors:  Comma-separated author names.
        year:     Publication year.
        source:   Where this was found, e.g. "arXiv", "OpenAlex", "Semantic Scholar".
        query:    The search query that surfaced this paper (kept for provenance).
        abstract: Abstract text, used as a search-scoring fallback when
            full-text extraction is unavailable.
        doi:      DOI if known.

    Returns:
        JSON with the stored record (id, path, title, text_extracted).
    """
    dirs = _resources_dirs()
    rec_id = _record_id(url)
    entries = _load_index()
    existing = next((e for e in entries if e["id"] == rec_id), None)

    filename = f"{_slugify(title or url)}-{rec_id}.pdf"
    dest = dirs["papers"] / filename

    await _rate_limit("web_search")  # be polite to arbitrary PDF hosts too
    try:
        async with httpx.AsyncClient(
            timeout=60, follow_redirects=True, verify=_SSL_VERIFY,
            headers={"User-Agent": _WEB_UA},
        ) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            dest.write_bytes(resp.content)
    except Exception as exc:
        return json.dumps({"status": "error", "url": url, "message": str(exc)}, indent=2)

    full_text = _extract_pdf_text(dest)
    if full_text:
        _text_sidecar_path(dest).write_text(full_text)

    record = {
        "id": rec_id,
        "type": "paper",
        "filename": str(dest.relative_to(dirs["root"])),
        "title": title or filename,
        "authors": authors,
        "year": year,
        "source": source,
        "query": query,
        "abstract": abstract,
        "doi": doi,
        "url": url,
        "added": datetime.now(timezone.utc).isoformat(),
        "text_extracted": bool(full_text),
        "text_len": len(full_text),
    }
    if existing:
        entries[entries.index(existing)] = record
    else:
        entries.append(record)
    _save_index(entries)

    return json.dumps({"status": "ok", "record": record}, indent=2)

async def _async_save_article(
    url: str,
    title: str = "",
    query: str = "",
    content: str = "",
) -> str:
    """Save a web article/doc/blog post into the local library at resources/articles/.

    If ``content`` is omitted, the tool fetches and extracts the page
    itself (same extraction logic as ``fetch_webpage_article``).
    Idempotent — re-saving the same URL updates the existing entry.

    Args:
        url:     URL of the article/doc to save.
        title:   Title override; auto-detected from the page if empty.
        query:   The search query that surfaced this article (provenance).
        content: Pre-fetched article text/markdown; skips the network
            fetch when provided.

    Returns:
        JSON with the stored record (id, path, title).
    """
    dirs = _resources_dirs()
    rec_id = _record_id(url)
    entries = _load_index()
    existing = next((e for e in entries if e["id"] == rec_id), None)

    if content:
        page_title = title
        body = content
    else:
        await _rate_limit("web_search")
        try:
            async with httpx.AsyncClient(
                timeout=30, follow_redirects=True, verify=_SSL_VERIFY,
                headers={"User-Agent": _WEB_UA},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                parsed = _parse_webpage(resp.text)
        except Exception as exc:
            return json.dumps({"status": "error", "url": url, "message": str(exc)}, indent=2)
        page_title = title or parsed["title"]
        body_parts = [f"# {page_title}\n\nSource: {url}\n"]
        for sec in parsed.get("sections", []):
            if sec.get("heading"):
                body_parts.append(f"\n## {sec['heading']}\n")
            body_parts.append(sec.get("content", ""))
        body = "\n".join(body_parts)

    filename = f"{_slugify(page_title or url)}-{rec_id}.md"
    dest = dirs["articles"] / filename
    dest.write_text(body)

    record = {
        "id": rec_id,
        "type": "article",
        "filename": str(dest.relative_to(dirs["root"])),
        "title": page_title or filename,
        "authors": "",
        "year": None,
        "source": "Web",
        "query": query,
        "abstract": body[:300],
        "doi": "",
        "url": url,
        "added": datetime.now(timezone.utc).isoformat(),
        "text_extracted": True,
        "text_len": len(body),
    }
    if existing:
        entries[entries.index(existing)] = record
    else:
        entries.append(record)
    _save_index(entries)

    return json.dumps({"status": "ok", "record": record}, indent=2)


def register(mcp):
    """Register this capability's tools on *mcp*."""

    @mcp.tool()
    def search_arxiv(
        query: str,
        max_results: int = 5,
        sort_by: str = "relevance",
        category: Optional[str] = None,
    ) -> str:
        """Search arXiv for academic papers.

        Args:
            query: Search query string (e.g. "parallel I/O optimization HDF5").
            max_results: Number of results to return (1-25, default 5).
            sort_by: Sort order — "relevance", "lastUpdatedDate", or "submittedDate".
            category: Optional arXiv category filter (e.g. "cs.DC", "cs.PF").

        Returns:
            Formatted list of matching papers with abstracts and URLs.
        """
        return _run_async(_async_search_arxiv(query=query, max_results=max_results, sort_by=sort_by, category=category))

    @mcp.tool()
    def get_arxiv_paper(arxiv_id: str) -> str:
        """Fetch a single arXiv paper by its ID.

        Args:
            arxiv_id: The arXiv paper ID (e.g. "2310.06825" or "cs/0301001").

        Returns:
            Full details of the paper including abstract and PDF link.
        """
        return _run_async(_async_get_arxiv_paper(arxiv_id=arxiv_id))

    @mcp.tool()
    def search_semantic_scholar(
        query: str,
        max_results: int = 5,
        year_range: Optional[str] = None,
        fields_of_study: Optional[str] = None,
        min_citations: Optional[int] = None,
    ) -> str:
        """Search Semantic Scholar for academic papers.

        Args:
            query: Search query string.
            max_results: Number of results (1-20, default 5).
            year_range: Optional year filter like "2020-2024" or "2023".
            fields_of_study: Comma-separated fields, e.g. "Computer Science".
            min_citations: Optional minimum citation count filter.

        Returns:
            Formatted list of matching papers.
        """
        return _run_async(_async_search_semantic_scholar(query=query, max_results=max_results, year_range=year_range, fields_of_study=fields_of_study, min_citations=min_citations))

    @mcp.tool()
    def get_semantic_scholar_paper(paper_id: str) -> str:
        """Fetch full details for a paper from Semantic Scholar.

        Args:
            paper_id: Semantic Scholar ID, DOI, or arXiv ID prefixed with "arXiv:"
                (e.g. "arXiv:2310.06825" or "DOI:10.18653/v1/2020.acl-main.196").

        Returns:
            Detailed paper information including references.
        """
        return _run_async(_async_get_semantic_scholar_paper(paper_id=paper_id))

    @mcp.tool()
    def get_author_papers(author_name: str, max_results: int = 5) -> str:
        """Search for an author on Semantic Scholar and retrieve their papers.

        Args:
            author_name: Full name of the author (e.g. "John Bent").
            max_results: Number of papers to return (1-20, default 5).

        Returns:
            Author profile and list of their papers sorted by citation count.
        """
        return _run_async(_async_get_author_papers(author_name=author_name, max_results=max_results))

    @mcp.tool()
    def search_openalex(query: str, max_results: int = 5) -> str:
        """Search OpenAlex for academic papers (250M+ works, no API key, no hard rate limit).

        Args:
            query: Search query string.
            max_results: Number of results to return (1-25, default 5).

        Returns:
            Formatted list of matching papers with abstracts and URLs.
        """
        return _run_async(_async_search_openalex(query=query, max_results=max_results))

    @mcp.tool()
    def get_openalex_paper(work_id: str) -> str:
        """Fetch full details for one OpenAlex work.

        Args:
            work_id: OpenAlex work ID (e.g. "W2741809807"), full OpenAlex URL,
                or a DOI (e.g. "10.1145/3295500.3356173").

        Returns:
            Detailed paper information.
        """
        return _run_async(_async_get_openalex_paper(work_id=work_id))

    @mcp.tool()
    def search_crossref(query: str, max_results: int = 5) -> str:
        """Search Crossref for academic papers by DOI metadata (no API key required).

        Best for resolving venues, DOIs, and citation counts; abstracts are
        often absent since Crossref indexes metadata, not full text.

        Args:
            query: Search query string.
            max_results: Number of results to return (1-25, default 5).

        Returns:
            Formatted list of matching papers.
        """
        return _run_async(_async_search_crossref(query=query, max_results=max_results))

    @mcp.tool()
    def search_core(query: str, max_results: int = 5) -> str:
        """Search CORE for open-access full-text papers.

        Requires a free CORE_API_KEY (register at https://core.ac.uk/services/api)
        set in the environment. Best source when a full-text PDF is needed
        rather than just an abstract.

        Args:
            query: Search query string.
            max_results: Number of results to return (1-25, default 5).

        Returns:
            Formatted list of matching papers, or a setup message if no
            CORE_API_KEY is configured.
        """
        return _run_async(_async_search_core(query=query, max_results=max_results))

    @mcp.tool()
    def search_dblp(query: str, max_results: int = 5) -> str:
        """Search DBLP for computer-science publications (no API key required).

        DBLP has no abstracts but is the most reliable source for CS venue/
        conference metadata and author disambiguation.

        Args:
            query: Search query string.
            max_results: Number of results to return (1-25, default 5).

        Returns:
            Formatted list of matching publications.
        """
        return _run_async(_async_search_dblp(query=query, max_results=max_results))

    @mcp.tool()
    def search_web(query: str, max_results: int = 5) -> str:
        """General web search (DuckDuckGo) for papers, docs, and articles not in academic APIs.

        Use this to find vendor whitepapers, conference talk slides,
        engineering blog posts, or mailing-list threads that academic
        search engines don't index — a complement to, not a replacement
        for, ``search_papers_combined``. Pair with ``fetch_webpage_article``
        to pull full content from any result.

        Args:
            query: Search query string.
            max_results: Number of results to return (1-20, default 5).

        Returns:
            Formatted list of web results (title, url, snippet).
        """
        return _run_async(_async_search_web(query=query, max_results=max_results))

    @mcp.tool()
    def search_papers_combined(
        query: str,
        max_results_each: int = 3,
        include_web: bool = False,
    ) -> str:
        """Search arXiv, Semantic Scholar, OpenAlex, Crossref, and DBLP simultaneously.

        CORE is included automatically when CORE_API_KEY is configured.
        All sources are queried in parallel and each is individually
        rate-limited, so this call is safe to use even when Semantic
        Scholar's 1 req/sec budget is tight — the other sources fill in
        without waiting on it.

        Args:
            query: Search query string.
            max_results_each: Papers to fetch from each source (1-10, default 3).
            include_web: Also run a general web search (DuckDuckGo) for
                non-academic sources like vendor docs and blog posts.

        Returns:
            Combined, labeled results from every source.
        """
        return _run_async(_async_search_papers_combined(query=query, max_results_each=max_results_each, include_web=include_web))

    @mcp.tool()
    def fetch_webpage_article(
        url: str,
        query: Optional[str] = None,
        top_sections: int = 5,
    ) -> str:
        """Fetch and extract structured content from any webpage, blog post, or article.

        Retrieves the page at ``url``, strips navigation/ads/scripts, and
        returns title, description, and content sections.  When ``query`` is
        given the most relevant sections are ranked by keyword frequency.

        Args:
            url:          Full URL of any webpage or article to fetch.
            query:        Optional keywords to filter/rank sections by relevance.
            top_sections: Maximum number of content sections to return (default 5).

        Returns:
            JSON with:
              - url:         fetched URL
              - title:       page title
              - description: meta description if available
              - sections:    list of {heading, content, code_example?}
        """
        return _run_async(_async_fetch_webpage_article(url=url, query=query, top_sections=top_sections))

    @mcp.tool()
    def rank_papers_by_relevance(
        papers_json: str,
        bottleneck_description: str,
        system_config: Optional[str] = None,
        top_n: int = 10,
    ) -> str:
        """Rank papers and articles by relevance to I/O bottlenecks and system configuration.

        Uses domain-aware keyword expansion to match bottleneck concepts
        (small I/O, metadata, bandwidth, checkpoint, etc.) against paper titles
        and abstracts.  System configuration terms (Lustre, MPI, GPU, HDF5, etc.)
        are used as a secondary boost signal.  Citation count and publication
        recency add a small bonus to well-cited or newer works.

        Args:
            papers_json:             JSON string — either a list of paper dicts
                or a dict with a ``"papers"`` list key (as returned by
                ``search_papers_combined``, ``search_arxiv``, or
                ``session_search_optimization_papers``).  Each paper may
                contain any of: ``title``, ``abstract``, ``authors``,
                ``year``, ``published``, ``citationCount``, ``url``,
                ``pdf_url``, ``source``, ``topic``.
            bottleneck_description:  Free-text description of the bottleneck(s)
                to solve.  Examples:
                  "high metadata ops, small random writes under Lustre"
                  "data loader bottleneck, slow checkpoint I/O, GPU stalls"
                  "low read bandwidth, imbalanced I/O across MPI ranks"
            system_config:           Optional free-text description of the system,
                used as a boost signal.  Examples:
                  "Lustre filesystem, 512 MPI ranks, HDF5 checkpoint"
                  "GPFS, A100 GPU cluster, PyTorch, NVLink"
            top_n:                   Number of top-ranked papers to return (default 10).

        Returns:
            JSON with:
              - status:         "ok" or "error"
              - total_papers:   total number of papers scored
              - query_terms:    expanded keyword list derived from bottleneck_description
              - boost_terms:    expanded keyword list derived from system_config
              - ranked_papers:  top-N paper dicts, each with an added ``relevance_score``
                (float) and ``matched_terms`` (list of matched expansion terms)
        """
        try:
            raw = json.loads(papers_json)
        except Exception as exc:
            return json.dumps({
                "status": "error",
                "message": f"Could not parse papers_json: {exc}",
            }, indent=2)

        if isinstance(raw, list):
            papers: List[Dict[str, Any]] = raw
        elif isinstance(raw, dict):
            papers = raw.get("papers", [])
            if not papers:
                for v in raw.values():
                    if isinstance(v, list) and v and isinstance(v[0], dict):
                        papers = v
                        break
        else:
            papers = []

        if not papers:
            return json.dumps({
                "status": "error",
                "message": "No papers found in papers_json.",
            }, indent=2)

        query_terms = _expand_query_terms(bottleneck_description, _BOTTLENECK_KEYWORD_EXPANSION)
        boost_terms = (
            _expand_query_terms(system_config, _SYSTEM_KEYWORD_EXPANSION)
            if system_config else []
        )

        scored: List[Tuple[float, List[str], Dict[str, Any]]] = []
        for paper in papers:
            score, matched = _score_paper_relevance(paper, query_terms, boost_terms)
            scored.append((score, matched, paper))

        scored.sort(key=lambda x: x[0], reverse=True)

        ranked = []
        for score, matched, paper in scored[:top_n]:
            entry = dict(paper)
            entry["relevance_score"] = round(score, 3)
            entry["matched_terms"] = matched
            ranked.append(entry)

        return json.dumps({
            "status": "ok",
            "total_papers": len(papers),
            "query_terms": query_terms[:30],
            "boost_terms": boost_terms[:20],
            "ranked_papers": ranked,
        }, indent=2)

    @mcp.tool()
    def save_paper(
        url: str,
        title: str = "",
        authors: str = "",
        year: Optional[int] = None,
        source: str = "",
        query: str = "",
        abstract: str = "",
        doi: str = "",
    ) -> str:
        """Download a paper PDF into the local library at resources/papers/.

        Extracts and caches the full text (via pypdf) so
        ``search_local_resources`` can fuzzy-search the paper body, not
        just its title/abstract. Idempotent — re-saving the same URL
        updates the existing entry instead of duplicating it.

        Args:
            url:      Direct PDF URL (e.g. an ``pdf_url`` from
                ``search_arxiv``/``search_openalex``/``search_papers_combined``).
            title:    Paper title (used for the stored filename and search).
            authors:  Comma-separated author names.
            year:     Publication year.
            source:   Where this was found, e.g. "arXiv", "OpenAlex", "Semantic Scholar".
            query:    The search query that surfaced this paper (kept for provenance).
            abstract: Abstract text, used as a search-scoring fallback when
                full-text extraction is unavailable.
            doi:      DOI if known.

        Returns:
            JSON with the stored record (id, path, title, text_extracted).
        """
        return _run_async(_async_save_paper(url=url, title=title, authors=authors, year=year, source=source, query=query, abstract=abstract, doi=doi))

    @mcp.tool()
    def save_article(
        url: str,
        title: str = "",
        query: str = "",
        content: str = "",
    ) -> str:
        """Save a web article/doc/blog post into the local library at resources/articles/.

        If ``content`` is omitted, the tool fetches and extracts the page
        itself (same extraction logic as ``fetch_webpage_article``).
        Idempotent — re-saving the same URL updates the existing entry.

        Args:
            url:     URL of the article/doc to save.
            title:   Title override; auto-detected from the page if empty.
            query:   The search query that surfaced this article (provenance).
            content: Pre-fetched article text/markdown; skips the network
                fetch when provided.

        Returns:
            JSON with the stored record (id, path, title).
        """
        return _run_async(_async_save_article(url=url, title=title, query=query, content=content))

    @mcp.tool()
    def search_local_resources(
        query: str,
        top_k: int = 5,
        resource_type: str = "",
    ) -> str:
        """Fuzzy-search the local paper/article library at resources/.

        Scores every stored entry against *query* using a combination of
        title similarity (typo/partial-title tolerant), query-token
        coverage across title+abstract+full-text, and an exact-substring
        bonus — so both "close title" and "topic keyword" queries work
        without needing an external fuzzy-matching dependency. Returns a
        short snippet of surrounding context for the best match in each
        result.

        Args:
            query:         Free-text search query.
            top_k:         Maximum number of results to return (default 5).
            resource_type: Optional filter — "paper" or "article".

        Returns:
            JSON with ``count``, ``results`` (each: id, type, title,
            authors, year, source, score, snippet, filename, url), and
            ``library_size`` (total entries searched).
        """
        entries = _load_index()
        if resource_type:
            entries = [e for e in entries if e.get("type") == resource_type]
        if not entries:
            return json.dumps({
                "status": "ok", "count": 0, "results": [], "library_size": 0,
                "message": "Local library is empty — use save_paper / save_article first.",
            }, indent=2)

        dirs = _resources_dirs()
        scored = []
        for entry in entries:
            full_text = ""
            stored_path = dirs["root"] / entry["filename"]
            text_path = (
                _text_sidecar_path(stored_path) if entry["type"] == "paper" else stored_path
            )
            if text_path.exists():
                try:
                    full_text = text_path.read_text(errors="ignore")
                except Exception:
                    full_text = ""
            score, snippet = _fuzzy_score(query, entry, full_text)
            scored.append((score, snippet, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        top_k = max(1, min(50, top_k))
        results = []
        for score, snippet, entry in scored[:top_k]:
            if score <= 0:
                continue
            results.append({
                "id": entry["id"],
                "type": entry["type"],
                "title": entry.get("title"),
                "authors": entry.get("authors"),
                "year": entry.get("year"),
                "source": entry.get("source"),
                "score": round(score, 3),
                "snippet": snippet,
                "filename": entry.get("filename"),
                "url": entry.get("url"),
            })

        return json.dumps({
            "status": "ok",
            "count": len(results),
            "results": results,
            "library_size": len(entries),
        }, indent=2)

    @mcp.tool()
    def rag_search(
        query: str,
        bottleneck: str = "",
        system_config: str = "",
        top_k: int = 5,
        resource_type: str = "",
    ) -> str:
        """Retrieve the most relevant passages in the local library for an optimization query.

        Retrieval only — never calls an LLM itself. Ranks by two combined
        signals: semantic similarity (local ``sentence-transformers``
        embeddings, cosine-compared against every chunk of every stored
        paper/article) and lexical relevance (the same bottleneck/system
        keyword-expansion scoring ``rank_papers_by_relevance`` uses).
        Semantic similarity dominates the ranking when available since
        that's what catches conceptually-related-but-differently-worded
        passages (e.g. "collective buffering" vs "two-phase I/O
        aggregation"); lexical scoring is the fallback so ranking still
        works — just less precisely — if the optional
        ``sentence-transformers`` dependency isn't installed
        (``pip install -e '.[embeddings]'``).

        Call ``save_paper``/``save_article`` first to build up the local
        library — this only searches what's already stored, it does not
        reach out to arXiv/OpenAlex/etc. itself.

        Args:
            query:         Free-text description of the behavior you're
                trying to optimize (e.g. "small writes stalling on the
                metadata server").
            bottleneck:    Optional bottleneck category/description —
                expanded through the same taxonomy as
                ``rank_papers_by_relevance`` (e.g. "metadata", "small_io").
            system_config: Optional system description used as a ranking
                boost (e.g. "Lustre, 512 MPI ranks, HDF5 checkpoint").
            top_k:         Maximum number of results to return (default 5).
            resource_type: Optional filter — "paper" or "article".

        Returns:
            JSON with ``embeddings_available`` (whether semantic scoring
            ran), ``count``, ``library_size``, and ``results`` — each with
            ``combined_score``, ``semantic_score``, ``lexical_score``,
            ``matched_terms``, and ``chunk`` (the actual best-matching
            passage text, not just the abstract).
        """
        from . import rag_service  # lazy: see the module-level import note above

        entries = _load_index()
        if resource_type:
            entries = [e for e in entries if e.get("type") == resource_type]
        if not entries:
            return json.dumps({
                "status": "ok", "embeddings_available": rag_service.embeddings_available(),
                "count": 0, "results": [], "library_size": 0,
                "message": "Local library is empty — use save_paper / save_article first.",
            }, indent=2)

        result = rag_service.rag_search(query, entries, bottleneck, system_config, top_k)
        if not result["embeddings_available"]:
            result["message"] = (
                "sentence-transformers is not installed — ranked by lexical "
                "(keyword) scoring only. Install the optional extra for semantic "
                "ranking: pip install -e '.[embeddings]'"
            )
        result["status"] = "ok"
        return json.dumps(result, indent=2)

    @mcp.tool()
    def list_local_resources(resource_type: str = "") -> str:
        """List everything currently stored in the local library.

        Args:
            resource_type: Optional filter — "paper" or "article".

        Returns:
            JSON list of all entries (id, type, title, year, source, added, filename).
        """
        entries = _load_index()
        if resource_type:
            entries = [e for e in entries if e.get("type") == resource_type]
        summary = [
            {
                "id": e["id"], "type": e["type"], "title": e.get("title"),
                "year": e.get("year"), "source": e.get("source"),
                "added": e.get("added"), "filename": e.get("filename"),
            }
            for e in entries
        ]
        return json.dumps({"status": "ok", "count": len(summary), "entries": summary}, indent=2)

    @mcp.tool()
    def session_search_optimization_papers(
        run_id: str,
        max_results_per_topic: int = 3,
        extra_query: Optional[str] = None,
    ) -> str:
        """Search arXiv for optimization papers relevant to the diagnosed bottlenecks.

        Reads ``<workspace>/diagnosis.json`` (produced by
        ``session_diagnose_bottlenecks``) and maps each high/critical bottleneck
        metric to a targeted arXiv search query.  Results are saved as
        ``<workspace>/optimization_papers.json`` and returned as a structured
        summary for the agent to interpret.

        Metric → query mapping examples:

        * ``small_io``   → "small I/O aggregation buffering optimization HPC"
        * ``rand``       → "random access sequential I/O prefetching optimization"
        * ``read_time``  → "parallel I/O read throughput optimization filesystem"
        * ``write_time`` → "parallel I/O write throughput checkpoint optimization"
        * ``metadata``   → "metadata operation overhead reduction parallel filesystem"

        Args:
            run_id: Session identifier returned by ``session_create``.
            max_results_per_topic: Papers to fetch per unique bottleneck topic
                (1-10, default 3).
            extra_query: Optional additional search terms appended to every query
                (e.g. the application name or storage system name).

        Returns:
            JSON with keys:

            * ``status``         — ``"ok"`` or ``"error"``.
            * ``topics_searched``— list of search queries issued.
            * ``papers``         — flat list of unique papers (deduplicated by title),
              each with ``title``, ``authors``, ``published``, ``abstract`` (truncated),
              ``url``, and ``topic``.
            * ``papers_file``    — path to the saved ``optimization_papers.json``.
        """
        ws = _ws(run_id)
        diagnosis_file = ws / "diagnosis.json"
        if not diagnosis_file.exists():
            return _err(
                "diagnosis.json not found — run session_diagnose_bottlenecks first",
                run_id=run_id,
            )

        try:
            diagnosis = json.loads(diagnosis_file.read_text())
        except Exception as exc:
            return _err(f"Could not read diagnosis.json: {exc}", run_id=run_id)

        bottlenecks: List[Dict[str, Any]] = diagnosis.get("bottlenecks", [])

        # Map metric name fragments to human-readable search queries
        _METRIC_QUERIES: Dict[str, str] = {
            "small_io":        "small I/O aggregation buffering optimization HPC parallel filesystem",
            "small_read":      "small read aggregation optimization parallel I/O",
            "small_write":     "small write buffering optimization parallel I/O",
            "rand":            "random I/O access pattern optimization sequential prefetching HPC",
            "seq":             "sequential I/O access pattern fragmentation optimization",
            "read_time":       "parallel I/O read throughput optimization high performance computing",
            "write_time":      "parallel I/O write throughput checkpoint optimization",
            "metadata":        "metadata operation overhead reduction parallel filesystem POSIX",
            "fetch_pressure":  "data loader prefetching pipeline deep learning I/O optimization",
            "epoch_straggler": "stragglers load imbalance distributed training I/O optimization",
            "checkpoint":      "checkpoint I/O optimization deep learning distributed training",
            "intensity":       "I/O intensity compute I/O overlap optimization",
            "imbalance":       "I/O load imbalance optimization distributed HPC",
            "bw":              "bandwidth utilization optimization parallel I/O filesystem",
            "fs_bw":           "parallel filesystem bandwidth utilization storage system throughput HPC",
            "comm":            "MPI collective communication optimization overlap HPC",
            "mem_bw":          "memory bandwidth optimization NUMA cache-aware HPC",
            "compute":         "compute kernel vectorization roofline optimization HPC",
        }

        # Collect unique topics from high/critical bottlenecks
        seen_topics: Dict[str, str] = {}  # query → representative metric name
        for bn in bottlenecks:
            metric = bn.get("metric", "")
            for fragment, query in _METRIC_QUERIES.items():
                if fragment in metric and query not in seen_topics:
                    seen_topics[query] = metric
                    break

        # If no bottlenecks mapped, fall back to a general I/O performance query
        if not seen_topics:
            seen_topics["I/O performance optimization parallel filesystem HPC"] = "general"

        if extra_query:
            seen_topics = {f"{q} {extra_query}": m for q, m in seen_topics.items()}

        # Search arXiv for each topic (synchronous wrapper around async HTTP)
        try:
            import httpx as _httpx  # noqa: F401 — presence check
            import xml.etree.ElementTree as _ET
            import urllib.parse

            _ARXIV = "https://export.arxiv.org/api/query"
            _NS    = {"atom": "http://www.w3.org/2005/Atom",
                      "arxiv": "http://arxiv.org/schemas/atom"}

            def _fetch_arxiv(query: str, n: int) -> List[Dict[str, Any]]:
                params = {
                    "search_query": f"all:{query}",
                    "max_results":  n,
                    "sortBy":       "relevance",
                    "sortOrder":    "descending",
                }
                qs = urllib.parse.urlencode(params)
                url = f"{_ARXIV}?{qs}"
                r = _run(["curl", "-s", "--max-time", "30", url], timeout=45)
                if not r["success"] or not r["stdout"]:
                    return []
                try:
                    root = _ET.fromstring(r["stdout"])
                    papers = []
                    for entry in root.findall("atom:entry", _NS):
                        def _t(tag):
                            el = entry.find(tag, _NS)
                            return el.text.strip() if el is not None and el.text else ""
                        arxiv_id = _t("atom:id").split("/abs/")[-1]
                        authors  = [
                            a.find("atom:name", _NS).text.strip()
                            for a in entry.findall("atom:author", _NS)
                            if a.find("atom:name", _NS) is not None
                        ]
                        papers.append({
                            "title":     _t("atom:title").replace("\n", " "),
                            "authors":   authors,
                            "published": _t("atom:published")[:10],
                            "abstract":  _t("atom:summary").replace("\n", " ")[:400],
                            "url":       f"https://arxiv.org/abs/{arxiv_id}",
                            "pdf_url":   f"https://arxiv.org/pdf/{arxiv_id}",
                        })
                    return papers
                except Exception:
                    return []

            max_results_per_topic = max(1, min(10, max_results_per_topic))
            all_papers: List[Dict[str, Any]] = []
            seen_titles: set = set()
            topics_searched: List[str] = []

            for query, metric in seen_topics.items():
                topics_searched.append(query)
                for p in _fetch_arxiv(query, max_results_per_topic):
                    title_key = p["title"].lower()[:80]
                    if title_key not in seen_titles:
                        seen_titles.add(title_key)
                        all_papers.append({**p, "topic": metric})

        except Exception as exc:
            return _err(f"Paper search failed: {exc}", run_id=run_id)

        result = {
            "run_id":          run_id,
            "topics_searched": topics_searched,
            "papers":          all_papers,
        }
        papers_file = ws / "optimization_papers.json"
        papers_file.write_text(json.dumps(result, indent=2))
        result["papers_file"] = str(papers_file)

        _write_artifact_log(ws, 16, "session_search_optimization_papers", {
            "topics":       len(topics_searched),
            "papers_found": len(all_papers),
        }, run_id)

        return _ok(
            f"Found {len(all_papers)} unique optimization papers across "
            f"{len(topics_searched)} bottleneck topic(s).",
            papers_file=str(papers_file),
            topics_searched=topics_searched,
            papers=all_papers,
        )

    @mcp.tool()
    def session_search_optimization_context(
        run_id: str,
        system: str = "",
        workload: str = "",
        metric_scope: str = "",
        max_remote_per_query: int = 3,
    ) -> str:
        """Exhaustive, stack-wide literature/docs/benchmark search — beyond the current diagnosis.

        Searches every software/system layer actually detected in this session
        (not just the metric tied to the current bottleneck), local-first
        (`opt_kb_lookup` + local `rag_search`, free) then remote fan-out across
        7 paper sources only for what's not already known locally. Adds a query
        class nothing else in the pipeline has: benchmark-target search — hunts
        for published achievable bandwidth/throughput numbers at this scale so
        the loop has a concrete target, not just a direction.

        Call this BEFORE `session_optimize_l1_app`/`l2_software`/`l3_filesystem`
        — it's step "0.5" of the optimization loop, after `opt_kb_lookup`
        (step 1) and before proposal generation.

        Args:
            run_id:       Session identifier — reads the detected stack from
                this session's `session.json` (mpi/hdf5/frameworks/languages).
            system:       System name for KB lookup + benchmark-target queries
                (e.g. "tuolumne"). Optional but sharpens target-number search.
            workload:     Application name for KB lookup (e.g. "flash_x").
            metric_scope: Optional filter — "app" or "system" — to only search
                query classes relevant to one axis (see `opt_kb_record`'s
                `metric_scope`). Blank searches both.
            max_remote_per_query: Papers to fetch per source per query (default 3).

        Returns:
            JSON written to `<workspace>/context_opportunities.json` and
            returned inline: `components_searched`, `opportunities` (candidate
            optimizations beyond the current bottleneck, each tagged
            `component`/`metric_scope`/`query_class`/citation), and
            `benchmark_targets` (published numbers with citations — read the
            `snippet` to extract the actual value; this tool does not attempt
            to parse numbers out of free text, that's a judgment call for the
            calling agent).
        """
        state = _load_state(run_id)
        if not state:
            return _err(f"No session found for run_id={run_id!r}")

        components = _detected_components(state)
        want_classes = [metric_scope] if metric_scope in ("app", "system") else None

        opportunities: List[Dict[str, Any]] = []
        benchmark_targets: List[Dict[str, Any]] = []
        components_needing_remote: List[str] = []

        # ── Step 1: local first (free) ───────────────────────────────────
        for component in components:
            kb_rows, rag_results = _local_hits_for(component, system, workload)
            if kb_rows or rag_results:
                for r in rag_results:
                    opportunities.append({
                        "component": component, "metric_scope": "app",
                        "query_class": "opportunity", "source": "local:" + r.get("source", ""),
                        "title": r.get("title"), "url": r.get("url"),
                        "score": r.get("combined_score"), "chunk": r.get("chunk"),
                        "citation": r.get("url") or f"session:{run_id}",
                    })
            # Even with local hits, still worth a remote check for THIS
            # session's specific scale/system if nothing local mentions it —
            # local coverage of opportunity queries does not imply target
            # numbers exist locally too, so always attempt remote for target
            # class unless explicitly filtered out below.
            if not rag_results:
                components_needing_remote.append(component)

        # ── Step 2: remote fan-out only for what step 1 didn't answer ────
        query_classes = want_classes or ["opportunity", "target"]
        remote_components = components_needing_remote or components  # always try target queries
        for query_class in query_classes:
            if query_class == "opportunity" and not components_needing_remote:
                continue  # already covered locally
            by_component = _run_async(
                _remote_search_components(remote_components, system, query_class, max_remote_per_query)
            )
            for component, papers in by_component.items():
                query_terms = _expand_query_terms(component, _BOTTLENECK_KEYWORD_EXPANSION)
                boost_terms = _expand_query_terms(system, _SYSTEM_KEYWORD_EXPANSION) if system else []
                for paper in papers:
                    score, matched = _score_paper_relevance(paper, query_terms, boost_terms)
                    if score <= 0:
                        continue
                    saved_id = _save_hit_to_library(paper, f"{component} {query_class}")
                    row = {
                        "component": component,
                        "metric_scope": "system" if query_class == "target" else "app",
                        "query_class": query_class,
                        "source": paper.get("source", ""),
                        "title": paper.get("title"), "url": paper.get("url") or paper.get("pdf_url"),
                        "authors": paper.get("authors"), "year": paper.get("year"),
                        "score": score, "snippet": (paper.get("abstract") or "")[:400],
                        "citation": paper.get("url") or paper.get("pdf_url") or "",
                        "saved_to_library": saved_id,
                    }
                    if query_class == "target":
                        benchmark_targets.append(row)
                    else:
                        opportunities.append(row)

        opportunities.sort(key=lambda r: r.get("score") or 0, reverse=True)
        benchmark_targets.sort(key=lambda r: r.get("score") or 0, reverse=True)

        result = {
            "run_id": run_id,
            "components_searched": components,
            "remote_fetched_for": remote_components if query_classes else [],
            "opportunities": opportunities[:30],
            "benchmark_targets": benchmark_targets[:15],
        }
        out_path = _ws(run_id) / "context_opportunities.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(result, indent=2))

        return _ok(
            f"{len(components)} components searched, {len(opportunities)} opportunities, "
            f"{len(benchmark_targets)} benchmark targets found -> {out_path}",
            **result,
        )


    @mcp.tool()
    def dftracer_papers_check(citations_json: str) -> str:
        """Deterministically validate a list of citations or found papers, offline (no network call).

        Use before putting citations into a proposal table or the final report. citations_json is a JSON list of
        citation strings or paper objects ({title, url | doi | venue + year}), an object with papers/citations, or
        a path to such a file (e.g. optimization_papers.json).
        Checks: at least one citation; each resolves to a URL, DOI, arXiv id, built-in reference key (wisio,
        drishti, ...) or "Author et al., Venue Year"; no duplicate titles; arXiv ids are well formed.

        Returns JSON with keys: status ("pass" | "fail"), checks (check, ok, detail, fix), failed, kinds (count per
        citation class: paper, docs, web, session).
        """
        return _papers_check(citations_json)
