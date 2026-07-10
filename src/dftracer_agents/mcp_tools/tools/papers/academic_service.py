"""Academic Papers MCP service — arXiv, Semantic Scholar, and web article tools.

Exposes tools for fetching and searching academic papers so that AI agents
can find relevant literature for I/O performance optimizations identified by
the dftracer pipeline.

Tools
-----
* ``search_arxiv``              — keyword search on arXiv
* ``get_arxiv_paper``           — fetch one arXiv paper by ID
* ``search_semantic_scholar``   — keyword search on Semantic Scholar
* ``get_semantic_scholar_paper``— fetch one S2 paper by ID
* ``get_author_papers``         — retrieve an author's paper list from S2
* ``search_openalex``           — keyword search on OpenAlex (250M+ works, no key, no hard limit)
* ``get_openalex_paper``        — fetch one OpenAlex work by ID/DOI
* ``search_crossref``           — keyword search on Crossref (DOI metadata, no key)
* ``search_core``               — full-text open-access search on CORE (needs CORE_API_KEY)
* ``search_dblp``                — CS bibliography search on DBLP (no key)
* ``search_web``                — general web search (DuckDuckGo) for docs/blogs/papers not in academic APIs
* ``search_papers_combined``    — parallel search across all sources at once
* ``fetch_webpage_article``     — fetch and extract any webpage/blog/article
* ``rank_papers_by_relevance``  — rank papers by bottleneck + system-config relevance

All outbound calls are client-side rate-limited per source (see ``_RATE_LIMITS``)
so a single MCP process never exceeds each provider's stated request budget —
most importantly Semantic Scholar's 1 request/second introductory limit.
"""
from __future__ import annotations

import asyncio
import html
import json
import math
import os
import re
import ssl
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory

ARXIV_BASE        = "https://export.arxiv.org/api/query"
S2_BASE           = "https://api.semanticscholar.org/graph/v1"
OPENALEX_BASE     = "https://api.openalex.org"
CROSSREF_BASE     = "https://api.crossref.org"
CORE_BASE         = "https://api.core.ac.uk/v3"
DBLP_BASE         = "https://dblp.org/search/publ/api"
DDG_HTML_BASE     = "https://html.duckduckgo.com/html/"

# Polite-pool / auth identifiers — optional, improve reliability, never required.
OPENALEX_MAILTO = os.environ.get("OPENALEX_MAILTO", "")
CORE_API_KEY    = os.environ.get("CORE_API_KEY", "")
S2_API_KEY      = os.environ.get("SEMANTIC_SCHOLAR_API_KEY", "")

# ---------------------------------------------------------------------------
# SSL configuration for HPC / corporate-CA environments
#
# On systems with custom certificate authorities (LLNL, AWS GovCloud, etc.)
# the default system trust store may not include the right CA certs.
# Resolution order:
#   1. REQUESTS_CA_BUNDLE / SSL_CERT_FILE env var (user-supplied CA bundle)
#   2. Common HPC CA bundle locations
#   3. HTTPX_SSL_VERIFY=false → disable verification (last resort)
# ---------------------------------------------------------------------------

_HPC_CA_CANDIDATES = [
    "/etc/pki/tls/certs/ca-bundle.crt",           # RHEL / CentOS / LLNL
    "/etc/ssl/certs/ca-certificates.crt",           # Debian / Ubuntu
    "/etc/ssl/ca-bundle.pem",                       # SUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # RHEL update-ca-trust
]


def _ssl_verify() -> Union[bool, str]:
    """Return the ssl verify value for httpx clients.

    Returns a CA bundle path when one is found, False when SSL verification
    is explicitly disabled via HTTPX_SSL_VERIFY=false, or True (default) to
    let httpx use its bundled certifi store.
    """
    if os.environ.get("HTTPX_SSL_VERIFY", "").lower() in ("0", "false", "no"):
        return False
    for env_var in ("REQUESTS_CA_BUNDLE", "SSL_CERT_FILE", "CURL_CA_BUNDLE"):
        val = os.environ.get(env_var, "")
        if val and Path(val).exists():
            return val
    for candidate in _HPC_CA_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return True  # fall back to httpx/certifi default


_SSL_VERIFY = _ssl_verify()


# ---------------------------------------------------------------------------
# Client-side rate limiting, per source
#
# Every provider has its own budget (Semantic Scholar's introductory tier is
# explicitly 1 request/second across all endpoints). Rather than trust every
# call site to self-throttle, every outbound request goes through
# ``_rate_limit(name)`` first, which enforces a minimum interval between
# requests to that source across the whole process using one asyncio.Lock
# per source name. Authenticated Semantic Scholar keys get a shorter
# interval since the approved-key tier allows a higher rate.
# ---------------------------------------------------------------------------

_RATE_LIMITS: Dict[str, float] = {
    # S2's key-holder tier is still capped at 1 req/s — the key raises the
    # *daily* quota, not the per-second rate — so keep a healthy buffer
    # above 1.0s regardless of whether a key is configured. Never race the
    # limit on the client side.
    "semantic_scholar": 1.2,
    "arxiv":             3.0,   # arXiv's own "be polite" guidance
    "openalex":          0.12,  # no hard limit, still be polite (~8 req/s)
    "crossref":          0.12,  # same — polite pool via mailto
    "core":              1.0,   # CORE free tier is modest
    "dblp":              0.5,   # no published limit, self-throttle
    "web_search":        1.0,   # avoid getting blocked by the search engine
}

_rate_locks: Dict[str, asyncio.Lock] = {}
_rate_last_call: Dict[str, float] = {}


async def _rate_limit(name: str) -> None:
    """Block until it is safe to issue another request to *name*.

    Enforces ``_RATE_LIMITS[name]`` seconds of minimum spacing between
    requests to the same source, shared across all concurrent callers in
    this process via a per-source lock.
    """
    interval = _RATE_LIMITS.get(name, 0.0)
    if interval <= 0:
        return
    lock = _rate_locks.setdefault(name, asyncio.Lock())
    async with lock:
        loop = asyncio.get_event_loop()
        now = loop.time()
        wait = interval - (now - _rate_last_call.get(name, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        _rate_last_call[name] = loop.time()


S2_PAPER_FIELDS  = (
    "title,authors,year,abstract,citationCount,referenceCount,"
    "url,externalIds,publicationDate,journal,openAccessPdf"
)
S2_AUTHOR_FIELDS = "name,affiliations,paperCount,citationCount,hIndex"


# ── Module-level helpers (pure, reusable by session tools) ────────────────────

def _parse_arxiv_entry(entry: ET.Element, ns: dict) -> dict:
    def txt(tag):
        el = entry.find(tag, ns)
        return el.text.strip() if el is not None and el.text else ""

    authors = [
        a.find("atom:name", ns).text.strip()
        for a in entry.findall("atom:author", ns)
        if a.find("atom:name", ns) is not None
    ]
    categories = [
        c.attrib.get("term", "")
        for c in entry.findall("arxiv:primary_category", ns)
        + entry.findall("atom:category", ns)
    ]
    arxiv_id = txt("atom:id").split("/abs/")[-1]
    return {
        "id":         arxiv_id,
        "title":      txt("atom:title").replace("\n", " "),
        "authors":    authors,
        "abstract":   txt("atom:summary").replace("\n", " "),
        "published":  txt("atom:published")[:10],
        "updated":    txt("atom:updated")[:10],
        "categories": list(dict.fromkeys(categories)),
        "pdf_url":    f"https://arxiv.org/pdf/{arxiv_id}",
        "abs_url":    f"https://arxiv.org/abs/{arxiv_id}",
        "source":     "arXiv",
    }


def _fmt_paper(p: dict) -> str:
    lines = [
        f"**{p.get('title', 'Untitled')}**",
        f"Authors: {', '.join(p.get('authors', [])) or 'N/A'}",
        f"Year/Date: {p.get('year') or p.get('published', 'N/A')}",
    ]
    if p.get("journal"):
        lines.append(f"Journal: {p['journal']}")
    if p.get("citationCount") is not None:
        lines.append(f"Citations: {p['citationCount']}")
    if p.get("abstract"):
        abstract = p["abstract"]
        lines.append(f"Abstract: {abstract[:300]}{'…' if len(abstract) > 300 else ''}")
    for key in ("pdf_url", "abs_url", "url"):
        if p.get(key):
            lines.append(f"URL: {p[key]}")
            break
    lines.append(f"Source: {p.get('source', 'N/A')}")
    return "\n".join(lines)


_WEB_UA = "Mozilla/5.0 (compatible; dftracer-agents/1.0)"

_WEB_SKIP_TAGS = frozenset({
    "script", "style", "nav", "footer", "header", "noscript",
    "aside", "form", "button", "svg", "iframe",
})
_WEB_VOID_TAGS = frozenset({
    "meta", "link", "br", "hr", "img", "input", "area",
    "base", "col", "embed", "param", "source", "track", "wbr",
})
_WEB_BLOCK_TAGS = frozenset({"p", "li", "td", "th", "dt", "dd"})
_WEB_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_WEB_CODE_TAGS = frozenset({"code", "pre"})


class _WebParser(HTMLParser):
    """Generic webpage content extractor — title + sections from arbitrary HTML."""

    def __init__(self):
        super().__init__()
        self.title: str = ""
        self.description: str = ""
        self.sections: List[Dict[str, Any]] = []
        self._heading = ""
        self._text: List[str] = []
        self._code: List[str] = []
        self._in_title = False
        self._in_heading = False
        self._in_code = False
        self._in_meta_desc = False
        self._skip_depth = 0
        self._skip_tag: Optional[str] = None
        self._heading_buf: List[str] = []
        self._code_buf: List[str] = []

    def handle_starttag(self, tag: str, attrs):
        attrs_dict = dict(attrs)
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in _WEB_SKIP_TAGS:
            self._skip_tag = tag
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            n = attrs_dict.get("name", "").lower()
            if n in ("description", "og:description"):
                self.description = attrs_dict.get("content", "")
        if tag in _WEB_HEADING_TAGS:
            self._in_heading = True
            self._heading_buf = []
        if tag in _WEB_CODE_TAGS:
            self._in_code = True
            self._code_buf = []

    def handle_endtag(self, tag: str):
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag == "title":
            self._in_title = False
        if tag in _WEB_CODE_TAGS:
            self._in_code = False
            snippet = "".join(self._code_buf).strip()
            if snippet:
                self._code.append(snippet)
        if tag in _WEB_HEADING_TAGS:
            self._in_heading = False
            h = "".join(self._heading_buf).strip()
            self._flush()
            self._heading = h
        if tag in _WEB_BLOCK_TAGS:
            t = "".join(self._text).strip()
            if t:
                self._text.append("\n")

    def handle_data(self, data: str):
        if self._skip_depth:
            return
        cleaned = html.unescape(data)
        if self._in_title:
            self.title += cleaned
        elif self._in_heading:
            self._heading_buf.append(cleaned)
        elif self._in_code:
            self._code_buf.append(cleaned)
        else:
            self._text.append(cleaned)

    def _flush(self):
        body = re.sub(r"\n{3,}", "\n\n", "".join(self._text)).strip()
        if body or self._code:
            self.sections.append({
                "heading": self._heading,
                "content": body,
                "code_blocks": list(self._code),
            })
        self._text = []
        self._code = []

    def close(self):
        super().close()
        self._flush()


def _parse_webpage(html_text: str) -> Dict[str, Any]:
    p = _WebParser()
    p.feed(html_text)
    p.close()
    return {
        "title": p.title.strip(),
        "description": p.description.strip(),
        "sections": p.sections,
    }


def _score_web_section(section: Dict[str, Any], terms: List[str]) -> float:
    text = " ".join([
        section.get("heading", "") * 3,
        section.get("content", ""),
        " ".join(section.get("code_blocks", [])),
    ]).lower()
    return sum(text.count(t.lower()) for t in terms)


def _best_web_sections(
    parsed: Dict[str, Any],
    terms: List[str],
    top_n: int = 5,
    max_chars: int = 800,
) -> List[Dict[str, Any]]:
    sections = parsed.get("sections", [])
    if terms:
        scored = sorted(sections, key=lambda s: _score_web_section(s, terms), reverse=True)
    else:
        scored = sections
    results = []
    for sec in scored[:top_n]:
        content = sec.get("content", "")[:max_chars]
        if len(sec.get("content", "")) > max_chars:
            content += " …"
        entry: Dict[str, Any] = {"heading": sec.get("heading", ""), "content": content}
        if sec.get("code_blocks"):
            entry["code_example"] = sec["code_blocks"][0][:400]
        results.append(entry)
    return results


# ── Domain keyword expansion for semantic ranking ─────────────────────────────

_BOTTLENECK_KEYWORD_EXPANSION: Dict[str, List[str]] = {
    "small_io":    ["buffering", "aggregation", "collective", "mpi-io", "write-combining",
                    "small-file", "many-file", "coalescing", "packing", "small writes"],
    "small":       ["buffering", "aggregation", "coalescing", "small-file", "packing"],
    "metadata":    ["inode", "directory", "stat", "open", "close", "namespace", "mdt",
                    "posix", "metadata server", "file creation", "unlink"],
    "random":      ["seek", "prefetch", "layout", "out-of-core", "irregular", "non-contiguous",
                    "stride", "indirect", "reorder", "spatial locality"],
    "sequential":  ["streaming", "prefetch", "contiguous", "fragmentation", "stripe",
                    "sequential read", "buffered"],
    "bandwidth":   ["throughput", "stripe", "raid", "network", "interconnect", "saturation",
                    "effective bandwidth", "storage bandwidth"],
    "checkpoint":  ["fault-tolerance", "restart", "scr", "fti", "incremental", "snapshot",
                    "persistence", "recovery", "dmtcp"],
    "read":        ["throughput", "prefetch", "cache", "read-ahead", "collective read",
                    "parallel read", "mpio"],
    "write":       ["buffering", "write-back", "async write", "collective write", "checkpoint",
                    "parallel write", "compression"],
    "imbalance":   ["load balancing", "skew", "uneven", "redistribution", "work stealing",
                    "straggler", "synchronization"],
    "intensity":   ["compute overlap", "asynchronous", "hiding", "non-blocking", "pipeline",
                    "overlap", "offload"],
    "fetch":       ["prefetch", "pipeline", "data loader", "ingestion", "staging",
                    "preprocessing", "cache", "deep learning data"],
    "epoch":       ["straggler", "distributed training", "synchronization", "allreduce",
                    "load imbalance", "batch"],
    "compression": ["lossless", "zlib", "zstd", "blosc", "hdf5", "szip", "bandwidth reduction"],
    "cache":       ["page cache", "buffer cache", "burst buffer", "nvme", "ssd", "flash",
                    "tiered storage"],
    "mpi":         ["collective", "mpi-io", "romio", "adio", "parallel", "ranks", "processes"],
    "lustre":      ["lnet", "ost", "mdt", "stripe", "parallel filesystem", "lustre"],
    "gpfs":        ["ibm spectrum scale", "gpfs", "parallel filesystem", "blocks"],
    "hdf5":        ["hdf5", "h5", "parallel hdf5", "phdf5", "chunking", "compression"],
    "gpu":         ["cuda", "gpu", "deep learning", "training", "nvidia", "tensor", "pytorch"],
}

_SYSTEM_KEYWORD_EXPANSION: Dict[str, List[str]] = {
    "lustre":    ["lustre", "parallel filesystem", "ost", "mdt", "stripe"],
    "gpfs":      ["gpfs", "spectrum scale", "parallel filesystem"],
    "nfs":       ["nfs", "network filesystem", "nfsv4"],
    "hdf5":      ["hdf5", "parallel hdf5", "chunking"],
    "mpi":       ["mpi", "mpi-io", "collective", "romio"],
    "gpu":       ["gpu", "cuda", "deep learning", "pytorch", "tensorflow"],
    "hpc":       ["hpc", "supercomputer", "cluster", "high performance computing"],
    "nvme":      ["nvme", "ssd", "flash", "burst buffer"],
    "infiniband": ["infiniband", "rdma", "high-speed network"],
    "posix":     ["posix", "posix io", "system call", "pread", "pwrite"],
    "python":    ["python", "pytorch", "tensorflow", "numpy", "h5py"],
    "checkpoint": ["checkpoint", "fault tolerance", "restart"],
}


def _expand_query_terms(text: str, expansion_map: Dict[str, List[str]]) -> List[str]:
    """Return original words + expansions from domain map for any matched key."""
    words = [w.lower() for w in re.split(r"\W+", text) if len(w) > 1]
    expanded = list(words)
    lower_text = text.lower()
    for key, synonyms in expansion_map.items():
        if key in lower_text or any(w.startswith(key[:4]) for w in words):
            expanded.extend(synonyms)
    return list(dict.fromkeys(expanded))  # deduplicate, preserve order


def _score_paper_relevance(
    paper: Dict[str, Any],
    query_terms: List[str],
    boost_terms: List[str],
) -> Tuple[float, List[str]]:
    """Return (score, matched_terms) for a single paper."""
    title = (paper.get("title") or "").lower()
    abstract = (paper.get("abstract") or "").lower()
    matched: List[str] = []

    term_score = 0.0
    for term in query_terms:
        t = term.lower()
        title_hits = title.count(t)
        abs_hits = abstract.count(t)
        if title_hits or abs_hits:
            matched.append(term)
        term_score += title_hits * 3.0 + abs_hits * 1.0

    boost_score = 0.0
    for term in boost_terms:
        t = term.lower()
        if t in title:
            boost_score += 2.0
        elif t in abstract:
            boost_score += 0.5

    cit_count = paper.get("citationCount") or 0
    citation_bonus = math.log10(1 + cit_count) * 0.5

    year_str = str(paper.get("year") or paper.get("published") or "")[:4]
    try:
        year = int(year_str)
        recency_bonus = max(0.0, (year - 2010) / 50.0)
    except ValueError:
        recency_bonus = 0.0

    score = term_score + boost_score + citation_bonus + recency_bonus
    return score, list(dict.fromkeys(matched))


async def _arxiv_search(query: str, max_results: int = 5, sort_by: str = "relevance",
                        category: Optional[str] = None) -> list[dict]:
    """Low-level arXiv search; returns list of paper dicts."""
    sort_by = {"relevance": "relevance", "lastUpdatedDate": "lastUpdatedDate",
                "submittedDate": "submittedDate"}.get(sort_by, "relevance")
    search_query = f"all:{query}"
    if category:
        search_query += f" AND cat:{category}"
    params = {"search_query": search_query, "max_results": max_results,
              "sortBy": sort_by, "sortOrder": "descending"}
    await _rate_limit("arxiv")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(ARXIV_BASE, params=params)
        resp.raise_for_status()
    ns = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    root = ET.fromstring(resp.text)
    return [_parse_arxiv_entry(e, ns) for e in root.findall("atom:entry", ns)]


def _s2_headers() -> Dict[str, str]:
    return {"x-api-key": S2_API_KEY} if S2_API_KEY else {}


async def _s2_search(
    query: str,
    max_results: int = 5,
    year_range: Optional[str] = None,
    fields_of_study: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Low-level, rate-limited Semantic Scholar search; returns list of paper dicts."""
    params: dict = {"query": query, "limit": max_results, "fields": S2_PAPER_FIELDS}
    if year_range:
        params["year"] = year_range
    if fields_of_study:
        params["fieldsOfStudy"] = fields_of_study
    await _rate_limit("semantic_scholar")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(f"{S2_BASE}/paper/search", params=params, headers=_s2_headers())
        resp.raise_for_status()
        data = resp.json()
    papers = []
    for p in data.get("data", []):
        pdf_url = p.get("openAccessPdf", {}).get("url") if p.get("openAccessPdf") else None
        papers.append({
            "title": p.get("title", ""),
            "authors": [a.get("name", "") for a in p.get("authors", [])],
            "year": p.get("year"),
            "abstract": p.get("abstract", ""),
            "citationCount": p.get("citationCount"),
            "url": p.get("url", ""),
            "pdf_url": pdf_url,
            "journal": p.get("journal", {}).get("name") if p.get("journal") else None,
            "source": "Semantic Scholar",
        })
    return papers


def _reconstruct_openalex_abstract(inverted_index: Optional[Dict[str, List[int]]]) -> str:
    """OpenAlex returns abstracts as an inverted index (word -> positions); rebuild plain text."""
    if not inverted_index:
        return ""
    positions: List[Tuple[int, str]] = []
    for word, idxs in inverted_index.items():
        for i in idxs:
            positions.append((i, word))
    positions.sort(key=lambda x: x[0])
    return " ".join(w for _, w in positions)


async def _openalex_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Low-level, rate-limited OpenAlex search; returns list of paper dicts."""
    params: dict = {"search": query, "per_page": max_results}
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO
    await _rate_limit("openalex")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(f"{OPENALEX_BASE}/works", params=params)
        resp.raise_for_status()
        data = resp.json()
    papers = []
    for w in data.get("results", []):
        oa = w.get("open_access") or {}
        primary = w.get("primary_location") or {}
        source = (primary.get("source") or {}) if primary else {}
        papers.append({
            "title": w.get("display_name", ""),
            "authors": [
                (a.get("author") or {}).get("display_name", "")
                for a in w.get("authorships", [])
            ],
            "year": w.get("publication_year"),
            "abstract": _reconstruct_openalex_abstract(w.get("abstract_inverted_index")),
            "citationCount": w.get("cited_by_count"),
            "url": w.get("id", ""),
            "pdf_url": oa.get("oa_url") or primary.get("pdf_url"),
            "journal": source.get("display_name"),
            "doi": w.get("doi"),
            "source": "OpenAlex",
        })
    return papers


async def _crossref_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Low-level, rate-limited Crossref search; returns list of paper dicts."""
    params: dict = {"query": query, "rows": max_results}
    if OPENALEX_MAILTO:
        params["mailto"] = OPENALEX_MAILTO  # same polite-pool convention Crossref uses
    await _rate_limit("crossref")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(f"{CROSSREF_BASE}/works", params=params)
        resp.raise_for_status()
        data = resp.json()
    papers = []
    for it in data.get("message", {}).get("items", []):
        titles = it.get("title") or []
        authors = [
            " ".join(filter(None, [a.get("given"), a.get("family")]))
            for a in it.get("author", [])
        ] if it.get("author") else []
        date_parts = (
            (it.get("published") or it.get("published-print") or it.get("published-online") or {})
            .get("date-parts", [[None]])
        )
        year = date_parts[0][0] if date_parts and date_parts[0] else None
        container = it.get("container-title") or []
        papers.append({
            "title": titles[0] if titles else "",
            "authors": authors,
            "year": year,
            "abstract": re.sub(r"<[^>]+>", "", it.get("abstract", "")) if it.get("abstract") else "",
            "citationCount": it.get("is-referenced-by-count"),
            "url": it.get("URL", ""),
            "pdf_url": None,
            "journal": container[0] if container else None,
            "doi": it.get("DOI"),
            "source": "Crossref",
        })
    return papers


async def _core_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Low-level, rate-limited CORE full-text search; returns list of paper dicts.

    Requires ``CORE_API_KEY`` (free registration at core.ac.uk). Returns an
    empty list — not an error — when no key is configured, so combined
    searches degrade gracefully.
    """
    if not CORE_API_KEY:
        return []
    await _rate_limit("core")
    # CORE 301-redirects /search/works -> /search/works/ (trailing slash).
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY, follow_redirects=True) as client:
        resp = await client.get(
            f"{CORE_BASE}/search/works",
            params={"q": query, "limit": max_results},
            headers={"Authorization": f"Bearer {CORE_API_KEY}"},
        )
        resp.raise_for_status()
        data = resp.json()
    papers = []
    for r in data.get("results", []):
        authors = [a.get("name", "") for a in (r.get("authors") or [])]
        papers.append({
            "title": r.get("title", ""),
            "authors": authors,
            "year": r.get("yearPublished"),
            "abstract": r.get("abstract", ""),
            "citationCount": r.get("citationCount"),
            "url": (r.get("sourceFulltextUrls") or [None])[0] or r.get("downloadUrl", ""),
            "pdf_url": r.get("downloadUrl"),
            "journal": (r.get("publisher") or None),
            "doi": r.get("doi"),
            "source": "CORE",
        })
    return papers


async def _dblp_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Low-level, rate-limited DBLP search; returns list of paper dicts (CS bibliography, no abstracts)."""
    await _rate_limit("dblp")
    async with httpx.AsyncClient(timeout=30, verify=_SSL_VERIFY) as client:
        resp = await client.get(DBLP_BASE, params={"q": query, "format": "json", "h": max_results})
        resp.raise_for_status()
        data = resp.json()
    hits = ((data.get("result") or {}).get("hits") or {}).get("hit") or []
    papers = []
    for h in hits:
        info = h.get("info", {})
        raw_authors = (info.get("authors") or {}).get("author")
        if isinstance(raw_authors, list):
            authors = [a.get("text", a) if isinstance(a, dict) else str(a) for a in raw_authors]
        elif isinstance(raw_authors, dict):
            authors = [raw_authors.get("text", "")]
        else:
            authors = []
        papers.append({
            "title": info.get("title", ""),
            "authors": authors,
            "year": info.get("year"),
            "abstract": "",
            "citationCount": None,
            "url": info.get("ee") or info.get("url", ""),
            "pdf_url": None,
            "journal": info.get("venue"),
            "doi": info.get("doi"),
            "source": "DBLP",
        })
    return papers


_DDG_RESULT_RE = re.compile(
    r'<a rel="nofollow" class="result__a" href="([^"]+)">(.*?)</a>.*?'
    r'class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)


def _clean_ddg_html(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


async def _web_search_ddg(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Low-level, rate-limited general web search via DuckDuckGo's HTML endpoint.

    No API key required. Intended for finding papers, docs, and blog posts
    that academic APIs don't index (vendor whitepapers, conference slide
    decks, mailing-list threads, etc.) — not a replacement for the academic
    sources above.
    """
    await _rate_limit("web_search")
    async with httpx.AsyncClient(
        timeout=30, verify=_SSL_VERIFY, follow_redirects=True,
        headers={"User-Agent": _WEB_UA},
    ) as client:
        resp = await client.post(DDG_HTML_BASE, data={"q": query})
        resp.raise_for_status()
        body = resp.text
    results = []
    for m in _DDG_RESULT_RE.finditer(body):
        url, title_html, snippet_html = m.groups()
        results.append({
            "title": _clean_ddg_html(title_html),
            "url": html.unescape(url),
            "snippet": _clean_ddg_html(snippet_html),
            "source": "Web (DuckDuckGo)",
        })
        if len(results) >= max_results:
            break
    return results


# ── Service class ─────────────────────────────────────────────────────────────

class AcademicPapersService(MCPService):
    """MCP service for fetching academic papers from arXiv and Semantic Scholar.

    Attributes:
        papers_subservice (FastMCP): Sub-server named ``"AcademicPapers"``
            hosting all search and fetch tools.
    """

    def __init__(self) -> None:
        self.papers_subservice = FastMCP("AcademicPapers")
        self._register_tools()

    def _register_tools(self) -> None:  # noqa: C901

        @self.papers_subservice.tool()
        async def search_arxiv(
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

        @self.papers_subservice.tool()
        async def get_arxiv_paper(arxiv_id: str) -> str:
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

        @self.papers_subservice.tool()
        async def search_semantic_scholar(
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

        @self.papers_subservice.tool()
        async def get_semantic_scholar_paper(paper_id: str) -> str:
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

        @self.papers_subservice.tool()
        async def get_author_papers(author_name: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def search_openalex(query: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def get_openalex_paper(work_id: str) -> str:
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

        @self.papers_subservice.tool()
        async def search_crossref(query: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def search_core(query: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def search_dblp(query: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def search_web(query: str, max_results: int = 5) -> str:
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

        @self.papers_subservice.tool()
        async def search_papers_combined(
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
                "arXiv": search_arxiv(query, max_results=max_results_each),
                "Semantic Scholar": search_semantic_scholar(query, max_results=max_results_each),
                "OpenAlex": search_openalex(query, max_results=max_results_each),
                "Crossref": search_crossref(query, max_results=max_results_each),
                "DBLP": search_dblp(query, max_results=max_results_each),
            }
            if CORE_API_KEY:
                tasks["CORE"] = search_core(query, max_results=max_results_each)
            if include_web:
                tasks["Web"] = search_web(query, max_results=max_results_each)

            names = list(tasks.keys())
            results = await asyncio.gather(*tasks.values(), return_exceptions=True)

            output = [f"Combined academic paper search for: '{query}'\n{'='*60}\n"]
            for name, result in zip(names, results):
                output.append(f"## {name} Results")
                output.append(str(result) if not isinstance(result, Exception)
                              else f"{name} error: {result}")
                output.append("")
            return "\n".join(output)

        @self.papers_subservice.tool()
        async def fetch_webpage_article(
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

        @self.papers_subservice.tool()
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

    def execute(self, data: dict) -> str:
        return "Use the search tools to find academic papers on arXiv and Semantic Scholar."

    @property
    def name(self) -> str:
        return "academic_papers"


MCPServiceFactory.register("academic_papers", AcademicPapersService())
