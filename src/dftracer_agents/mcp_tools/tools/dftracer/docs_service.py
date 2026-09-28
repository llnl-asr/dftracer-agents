"""
DFTracer documentation search and retrieval service.

Covers dftracer, dftracer-utils, pydftracer, and dfanalyzer documentation
hosted on ReadTheDocs.  Uses the RTD v2 search API as the primary search
mechanism, with fallback to direct page fetching and content extraction.

This module makes **live HTTP requests** to ReadTheDocs at call time.  Network
availability and RTD uptime determine whether results are returned.  A
``_TIMEOUT``-second ceiling is applied to every outbound request so the MCP
server does not hang indefinitely on connectivity issues.

Key exports:
    DOCS_SOURCES:           Mapping of canonical source keys to their RTD
                            metadata (base URL, search API, key pages, etc.).
    _SOURCE_ALIASES:        Short-form aliases accepted anywhere a source key
                            is expected (e.g. "py" -> "pydftracer").
    DFTracerDocsService:    ``MCPService`` subclass that registers
                            ``docs_search``, ``docs_fetch_page``,
                            ``docs_list_sources``, and ``docs_search_key_pages``
                            as FastMCP tools.

Module-level constants:
    _USER_AGENT:  HTTP ``User-Agent`` header sent with every outbound request.
    _TIMEOUT:     Socket timeout in seconds applied to every ``urllib`` call.
"""
from __future__ import annotations

import html
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory


# ---------------------------------------------------------------------------
# Known documentation sources
# ---------------------------------------------------------------------------

DOCS_SOURCES: Dict[str, Dict[str, Any]] = {  # noqa: E501
    # Each value is a dict with keys:
    #   name        – human-readable project name
    #   description – one-line summary of what the project covers
    #   base        – root URL of the versioned documentation tree
    #   search_api  – RTD v2 search endpoint for this sub-project
    #   rtd_project – RTD project slug used in search API query params
    #   rtd_version – RTD version slug (typically "latest")
    #   key_pages   – page paths relative to ``base`` used as a fallback
    #                 when the RTD search API returns no results
    "dftracer": {  # canonical key: pass as ``source="dftracer"`` to MCP tools
        "name": "DFTracer (core)",
        "description": "C/C++/Python tracing library — macros, init/fini, configuration",
        "base": "https://dftracer.readthedocs.io/en/latest",
        "search_api": "https://dftracer.readthedocs.io/_/api/v2/search/",
        "rtd_project": "dftracer",
        "rtd_version": "latest",
        "key_pages": [
            "index.html",
            "quickstart.html",
            "c-api.html",
            "python-api.html",
            "configuration.html",
            "installation.html",
            "environment.html",
        ],
    },
    "dftracer-utils": {
        "name": "DFTracer Utils",
        "description": "CLI tools — reader, split, merge, stats, aggregator, replay, etc.",
        "base": "https://dftracer.readthedocs.io/projects/utils/en/latest",
        "search_api": "https://dftracer.readthedocs.io/projects/utils/_/api/v2/search/",
        "rtd_project": "dftracer-utils",
        "rtd_version": "latest",
        "key_pages": [
            "index.html",
            "cli.html",
            "installation.html",
            "api.html",
        ],
    },
    "pydftracer": {
        "name": "PyDFTracer",
        "description": "Python bindings and decorator API for dftracer",
        "base": "https://dftracer.readthedocs.io/projects/pydftracer/en/latest",
        "search_api": "https://dftracer.readthedocs.io/projects/pydftracer/_/api/v2/search/",
        "rtd_project": "pydftracer",
        "rtd_version": "latest",
        "key_pages": [
            "index.html",
            "api.html",
            "quickstart.html",
        ],
    },
    "dfanalyzer": {
        "name": "DFAnalyzer",
        "description": "Trace analysis engine — DFAnalyzer Python API and query interface",
        "base": "https://dftracer.readthedocs.io/projects/dfanalyzer/en/latest",
        "search_api": "https://dftracer.readthedocs.io/projects/dfanalyzer/_/api/v2/search/",
        "rtd_project": "dfanalyzer",
        "rtd_version": "latest",
        "key_pages": [
            "index.html",
            "api.html",
            "quickstart.html",
        ],
    },
}

_SOURCE_ALIASES: Dict[str, str] = {
    # Convenient short-forms accepted as the ``source`` parameter of every
    # MCP tool.  Each value must be a key that exists in ``DOCS_SOURCES``.
    "utils": "dftracer-utils",     # dftracer-utils CLI tooling
    "py": "pydftracer",            # Python bindings short-form
    "python": "pydftracer",        # Python bindings long-form
    "analyzer": "dfanalyzer",      # DFAnalyzer analysis engine
    "core": "dftracer",            # core C/C++/Python library
    "main": "dftracer",            # alias for "core"
}

# HTTP ``User-Agent`` header sent with every outbound request to ReadTheDocs.
# Identifies this client to RTD rate-limiters and log analysis.
_USER_AGENT = "dftracer-agents/1.0 (MCP docs tool; +https://github.com/llnl-asr/dftracer)"

# Socket timeout in seconds applied to every ``urllib`` HTTP call.
# Prevents the MCP server from stalling indefinitely when RTD is unreachable.
_TIMEOUT = 15


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _fetch(url: str, timeout: int = _TIMEOUT) -> Optional[str]:
    """Fetch a URL and return its decoded body text, or ``None`` on any error.

    Uses ``_USER_AGENT`` in the ``User-Agent`` request header and respects the
    charset reported by the server (falling back to UTF-8).  All network and
    HTTP errors are silently swallowed and represented as ``None`` so callers
    can gracefully degrade.

    Args:
        url:     Fully-qualified URL to retrieve.
        timeout: Socket timeout in seconds (default: ``_TIMEOUT``).

    Returns:
        Decoded response body as a ``str``, or ``None`` if the request failed
        for any reason (DNS failure, HTTP error, timeout, OS error).
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            charset = "utf-8"
            ct = resp.headers.get_content_charset()
            if ct:
                charset = ct
            return resp.read().decode(charset, errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, OSError):
        return None


# ---------------------------------------------------------------------------
# HTML content extractor
# ---------------------------------------------------------------------------

class _DocParser(HTMLParser):
    """SAX-style streaming HTML parser that extracts structured content from RTD pages.

    Walks the HTML token stream and builds a flat list of *sections*, where
    each section corresponds to the content between two consecutive heading
    tags (``h1``–``h6``).  Navigation chrome (``<nav>``, ``<header>``,
    ``<footer>``, ``<script>``, ``<style>``, ``<noscript>``) is skipped
    entirely so only prose and code are captured.

    The parser is **single-use**: instantiate, call ``feed(html_text)``, call
    ``close()``, then read ``title`` and ``sections``.

    Attributes:
        title (str): Text content of the page's ``<title>`` element, stripped
            of surrounding whitespace.
        sections (List[Dict[str, Any]]): Ordered list of section dicts, each
            with the following keys:

            - ``heading`` (str): Text of the preceding heading tag, or ``""``
              for content before the first heading.
            - ``content`` (str): Accumulated paragraph/list/table text within
              the section, with runs of three or more blank lines collapsed.
            - ``code_blocks`` (List[str]): Stripped text of every ``<code>``
              or ``<pre>`` element within the section, in document order.

    Class attributes:
        _SKIP_TAGS (frozenset): Non-void block elements whose subtrees are
            skipped completely (``script``, ``style``, ``nav``, ``footer``,
            ``header``, ``noscript``).  Only non-void tags may appear here
            because the depth-tracking logic relies on matching open/close
            pairs; void tags have no closing tag and would permanently
            activate skip mode.
        _VOID_IGNORE (frozenset): Void HTML elements silently ignored because
            they carry no text content (``meta``, ``link``, ``br``, etc.).
        _BLOCK_TAGS (frozenset): Block-level elements after whose closing tag
            a newline separator is injected into ``_current_text``.
        _CODE_TAGS (frozenset): Elements whose text content is accumulated
            into the current section's ``code_blocks`` list (``code``,
            ``pre``).
        _HEADING_TAGS (frozenset): Elements that flush the current section and
            start a new one (``h1``–``h6``).
    """

    # Only non-void elements here — void elements (meta, link) have no closing
    # tag so they would permanently activate skip mode and eat the rest of the page.
    _SKIP_TAGS = frozenset({
        "script", "style", "nav", "footer", "header", "noscript",
    })
    # Void elements we silently ignore (no text content possible anyway)
    _VOID_IGNORE = frozenset({
        "meta", "link", "br", "hr", "img", "input", "area",
        "base", "col", "embed", "param", "source", "track", "wbr",
    })
    _BLOCK_TAGS = frozenset({
        "p", "li", "td", "th", "dt", "dd",
        "h1", "h2", "h3", "h4", "h5", "h6",
    })
    _CODE_TAGS = frozenset({"code", "pre"})
    _HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self):
        """Initialise parser state.  All buffers start empty."""
        super().__init__()
        self.title: str = ""
        self.sections: List[Dict[str, Any]] = []  # [{heading, content, code_blocks}]
        self._current_heading: str = ""   # heading text for the section being built
        self._current_text: List[str] = []        # running prose accumulator
        self._current_code: List[str] = []        # completed code snippets for section
        self._in_title = False            # True while inside <title>…</title>
        self._in_code = False             # True while inside <code>/<pre>
        self._in_heading = False          # True while inside <h1>–<h6>
        self._heading_buf: List[str] = [] # accumulates text of the current heading
        self._skip_depth = 0              # nesting depth of the active skip subtree
        self._skip_tag: Optional[str] = None      # tag name of the outermost skipped element
        self._current_code_buf: List[str] = []    # accumulates text of the current code block

    def handle_starttag(self, tag: str, attrs):
        """React to an opening HTML tag.

        If a skip subtree is active, only tracks nested occurrences of the
        same tag to maintain accurate depth.  Otherwise activates skip mode
        for tags in ``_SKIP_TAGS``, or sets the appropriate ``_in_*`` flag
        for ``<title>``, heading, and code tags.

        Args:
            tag:   Lower-cased tag name (e.g. ``"div"``, ``"h2"``).
            attrs: List of ``(name, value)`` attribute pairs (unused here but
                   required by the ``HTMLParser`` interface).
        """
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth += 1
            return
        if tag in self._SKIP_TAGS:
            self._skip_tag = tag
            self._skip_depth = 1
            return
        if tag == "title":
            self._in_title = True
        if tag in self._HEADING_TAGS:
            self._in_heading = True
            self._heading_buf = []
        if tag in self._CODE_TAGS:
            self._in_code = True
            self._current_code_buf = []

    def handle_endtag(self, tag: str):
        """React to a closing HTML tag.

        Decrements the skip-depth counter when inside a skipped subtree.
        Outside skip mode:

        - ``</title>`` ends title capture.
        - ``</code>`` / ``</pre>`` finalises the current code block and
          appends it to ``_current_code`` if non-empty.
        - ``</h1>``–``</h6>`` flushes the active section via
          ``_flush_section()`` and records the new heading.
        - Other block-level closing tags inject a newline separator into the
          prose accumulator.

        Args:
            tag: Lower-cased tag name of the closing element.
        """
        if self._skip_depth:
            if tag == self._skip_tag:
                self._skip_depth -= 1
                if self._skip_depth == 0:
                    self._skip_tag = None
            return
        if tag == "title":
            self._in_title = False
        if tag in self._CODE_TAGS:
            self._in_code = False
            snippet = "".join(self._current_code_buf).strip()
            if snippet:
                self._current_code.append(snippet)
        if tag in self._HEADING_TAGS:
            self._in_heading = False
            heading_text = "".join(self._heading_buf).strip()
            self._flush_section()
            self._current_heading = heading_text
        if tag in self._BLOCK_TAGS and tag not in self._HEADING_TAGS:
            t = "".join(self._current_text).strip()
            if t:
                self._current_text.append("\n")

    def handle_data(self, data: str):
        """Dispatch text data to the appropriate accumulator buffer.

        Text inside a skipped subtree is silently dropped.  Otherwise the
        HTML-unescaped text is routed to:

        - ``self.title``            — when inside ``<title>``.
        - ``self._heading_buf``     — when inside a heading tag.
        - ``self._current_code_buf``— when inside ``<code>`` / ``<pre>``.
        - ``self._current_text``    — all other visible text.

        Args:
            data: Raw character data token from the HTML stream (may contain
                  HTML entities such as ``&amp;``).
        """
        if self._skip_depth:
            return
        cleaned = html.unescape(data)
        if self._in_title:
            self.title += cleaned
        elif self._in_heading:
            self._heading_buf.append(cleaned)
        elif self._in_code:
            self._current_code_buf.append(cleaned)
        else:
            self._current_text.append(cleaned)

    def _flush_section(self):
        """Finalise the current section and append it to ``self.sections``.

        Joins ``_current_text`` into a single string, collapses runs of three
        or more consecutive newlines to two, and strips leading/trailing
        whitespace.  Appends a section dict only when there is non-empty prose
        or at least one code block.  Resets both accumulators afterwards.
        """
        body = re.sub(r"\n{3,}", "\n\n", "".join(self._current_text)).strip()
        if body or self._current_code:
            self.sections.append({
                "heading": self._current_heading,
                "content": body,
                "code_blocks": list(self._current_code),
            })
        self._current_text = []
        self._current_code = []

    def close(self):
        """Flush the final in-progress section and close the underlying parser.

        Must be called after the last ``feed()`` call to ensure that trailing
        content after the last heading is not lost.
        """
        super().close()
        self._flush_section()


def _parse_page(html_text: str) -> Dict[str, Any]:
    """Parse an HTML documentation page and return its structured content.

    Instantiates a ``_DocParser``, feeds the full HTML string, closes the
    parser to flush the last section, and returns the result dict.

    Args:
        html_text: Complete HTML source of a documentation page.

    Returns:
        A dict with two keys:

        - ``"title"`` (str): Stripped page title from ``<title>``, or ``""``
          if no title element was found.
        - ``"sections"`` (List[Dict[str, Any]]): Ordered list of section dicts
          as produced by ``_DocParser`` (see its class docstring for the
          per-section schema).
    """
    p = _DocParser()
    p.feed(html_text)
    p.close()
    return {"title": p.title.strip(), "sections": p.sections}


# ---------------------------------------------------------------------------
# Scoring / relevance
# ---------------------------------------------------------------------------

def _score_section(section: Dict[str, Any], terms: List[str]) -> float:
    """Score a section by total term frequency across heading, content, and code.

    Headings are weighted by repetition (the heading string is concatenated
    three times before scoring) so that sections whose headings match the
    query rank higher than sections where terms appear only in body text.

    Args:
        section: A section dict with optional keys ``"heading"`` (str),
            ``"content"`` (str), and ``"code_blocks"`` (List[str]).
        terms:   List of lower-cased search terms to count.

    Returns:
        Sum of per-term occurrence counts across the combined text.  Higher
        values indicate greater relevance.  Returns ``0.0`` when no terms
        match.
    """
    text = " ".join([
        section.get("heading", "") * 3,  # weight headings more
        section.get("content", ""),
        " ".join(section.get("code_blocks", [])),
    ]).lower()
    return sum(text.count(t.lower()) for t in terms)


def _best_sections(
    parsed: Dict[str, Any],
    terms: List[str],
    top_n: int = 3,
    max_chars: int = 800,
) -> List[Dict[str, Any]]:
    """Return the top-N most relevant sections from a parsed page.

    Sections are scored with ``_score_section`` and sorted by descending
    relevance.  If a section scores zero *and* at least one result has already
    been collected, iteration stops early to avoid returning irrelevant content.

    Args:
        parsed:    Dict returned by ``_parse_page`` (must have a ``"sections"``
            key).
        terms:     Lower-cased search terms used for scoring.
        top_n:     Maximum number of sections to return (default ``3``).
        max_chars: Content of each section is truncated to this many characters;
            a ``" …"`` suffix is appended when truncation occurs (default
            ``800``).

    Returns:
        List of section dicts, each containing:

        - ``"heading"`` (str): Section heading text.
        - ``"content"`` (str): Prose text, possibly truncated.
        - ``"code_example"`` (str, optional): First code block of the section,
          truncated to 600 characters.  Present only when the section has at
          least one code block.
    """
    scored = [
        (s, _score_section(s, terms))
        for s in parsed.get("sections", [])
    ]
    scored.sort(key=lambda x: x[1], reverse=True)
    results = []
    for sec, score in scored[:top_n]:
        if score == 0 and results:
            break
        content = sec.get("content", "")[:max_chars]
        if len(sec.get("content", "")) > max_chars:
            content += " …"
        entry: Dict[str, Any] = {"heading": sec.get("heading", ""), "content": content}
        if sec.get("code_blocks"):
            entry["code_example"] = sec["code_blocks"][0][:600]
        results.append(entry)
    return results


# ---------------------------------------------------------------------------
# RTD search API
# ---------------------------------------------------------------------------

def _rtd_search(
    source_key: str,
    query: str,
    max_results: int = 5,
) -> List[Dict[str, Any]]:
    """Query the ReadTheDocs v2 search API for a single documentation project.

    Constructs the search URL from ``DOCS_SOURCES[source_key]["search_api"]``,
    encodes ``q``, ``project``, and ``version`` query parameters, fetches the
    JSON response, and extracts highlight snippets from the ``blocks`` field of
    each result hit.  RTD highlight marker tags (``<em>``, ``<mark>``, etc.)
    are stripped from snippets with a simple regex.

    Args:
        source_key:  Key in ``DOCS_SOURCES`` identifying which project to
            search (e.g. ``"dftracer"``, ``"dftracer-utils"``).
        query:       Free-text search string forwarded verbatim to the RTD API.
        max_results: Maximum number of result hits to return (default ``5``).

    Returns:
        List of result dicts (up to ``max_results``), each with:

        - ``"title"``      (str): Page title from RTD.
        - ``"url"``        (str): Absolute URL to the matching page.
        - ``"highlights"`` (List[str]): Up to four cleaned highlight snippets
          extracted from the ``blocks[*].highlights`` field.

        Returns an empty list when ``source_key`` is unknown, the HTTP request
        fails, or the response body is not valid JSON.
    """
    src = DOCS_SOURCES.get(source_key, {})
    search_api = src.get("search_api", "")
    if not search_api:
        return []

    params = urllib.parse.urlencode({
        "q": query,
        "project": src.get("rtd_project", source_key),
        "version": src.get("rtd_version", "latest"),
    })
    url = f"{search_api}?{params}"
    body = _fetch(url)
    if not body:
        return []

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return []

    results = []
    for hit in data.get("results", [])[:max_results]:
        blocks = hit.get("blocks", [])
        highlights: List[str] = []
        for blk in blocks[:2]:
            hl = blk.get("highlights", {})
            for field in ("content", "title"):
                for snippet in hl.get(field, [])[:2]:
                    # Strip RTD highlight markers
                    clean = re.sub(r"<[^>]+>", "", snippet).strip()
                    if clean:
                        highlights.append(clean)

        page_url = hit.get("domain", src.get("base", "")) + hit.get("path", "")
        results.append({
            "title": hit.get("title", ""),
            "url": page_url,
            "highlights": highlights,
        })
    return results


# ---------------------------------------------------------------------------
# Page-level fetch + relevant content extraction
# ---------------------------------------------------------------------------

def _fetch_and_extract(
    url: str,
    terms: List[str],
    top_n: int = 3,
) -> Dict[str, Any]:
    """Fetch a page URL, parse its HTML, and return the most relevant sections.

    Combines ``_fetch``, ``_parse_page``, and ``_best_sections`` into a single
    convenience call used by ``docs_search`` when ``fetch_content=True``.

    Args:
        url:   Fully-qualified URL of the documentation page to retrieve.
        terms: Lower-cased search terms used to score and select sections.
        top_n: Maximum number of sections to return (default ``3``).

    Returns:
        On success — a dict with:

        - ``"url"``      (str): The fetched URL (echoed for caller convenience).
        - ``"title"``    (str): Page title extracted from ``<title>``.
        - ``"sections"`` (List[Dict]): Top-N relevant sections from the page.

        On network/HTTP failure — a dict with:

        - ``"url"``   (str): The requested URL.
        - ``"error"`` (str): Human-readable error message.
    """
    body = _fetch(url)
    if body is None:
        return {"url": url, "error": "could not fetch page"}
    parsed = _parse_page(body)
    sections = _best_sections(parsed, terms, top_n=top_n)
    return {
        "url": url,
        "title": parsed.get("title", ""),
        "sections": sections,
    }


def _resolve_source_keys(source: str) -> List[str]:
    """Resolve a source specification string to a list of ``DOCS_SOURCES`` keys.

    Handles three forms:

    1. ``"all"`` — returns every key in ``DOCS_SOURCES`` in insertion order.
    2. Comma- or whitespace-separated list of tokens — each token is
       normalised to lower-case, resolved through ``_SOURCE_ALIASES``, and
       included only if it exists in ``DOCS_SOURCES``.
    3. Single token — same normalisation as (2).

    Falls back to all sources when none of the supplied tokens resolve to a
    known key, so callers always receive a non-empty list.

    Args:
        source: Source specification string, e.g. ``"all"``,
            ``"dftracer,dftracer-utils"``, or ``"py"``.

    Returns:
        Ordered list of canonical ``DOCS_SOURCES`` keys with duplicates
        removed (order of first occurrence is preserved).
    """
    if source.strip().lower() == "all":
        return list(DOCS_SOURCES.keys())
    keys: List[str] = []
    for part in re.split(r"[,\s]+", source.strip()):
        part = part.lower().strip()
        part = _SOURCE_ALIASES.get(part, part)
        if part in DOCS_SOURCES:
            keys.append(part)
    return keys or list(DOCS_SOURCES.keys())


# ---------------------------------------------------------------------------
# MCP service
# ---------------------------------------------------------------------------

class DFTracerDocsService(MCPService):
    """MCP service providing dftracer documentation search and retrieval.

    Registers four FastMCP tools against a ``DFTracerDocs`` sub-server:

    - ``docs_list_sources``       — enumerate available documentation sources.
    - ``docs_search``             — search RTD and fetch relevant sections.
    - ``docs_fetch_page``         — retrieve and parse a specific page by URL.
    - ``docs_search_key_pages``   — summarise all key pages of a source.

    This service makes **live HTTP requests** to ReadTheDocs every time a tool
    is invoked.  No caching is performed; results reflect the current state of
    the hosted documentation.

    Attributes:
        docs_subservice (FastMCP): The internal ``FastMCP`` server instance
            named ``"DFTracerDocs"`` that owns the four registered tool
            functions.  Exposed so that ``dftracer_mcp_server.py`` can extract
            the tool list and mount it on a combined server.
    """

    def __init__(self) -> None:
        """Initialise the service and register all documentation tools."""
        self.docs_subservice = FastMCP("DFTracerDocs")
        _register_docs_tools(self.docs_subservice)

    def execute(self, data: dict) -> Optional[str]:
        """Return a usage hint (direct tool calls are preferred).

        This method satisfies the ``MCPService`` abstract interface but is not
        the primary call path for this service.  Callers should invoke the
        individual FastMCP tools (``docs_search``, etc.) instead.

        Args:
            data: Ignored.

        Returns:
            A static hint string directing callers to the specific MCP tools.
        """
        return "Use docs_search / docs_fetch_page / docs_list_sources to query dftracer docs."

    @property
    def name(self) -> str:
        """Canonical service identifier used with ``MCPServiceFactory``.

        Returns:
            ``"dftracer-docs"``
        """
        return "dftracer-docs"


def _register_docs_tools(mcp: FastMCP) -> None:
    """Register all documentation MCP tools on the provided FastMCP instance.

    Defines and decorates four inner functions with ``@mcp.tool()`` so that
    FastMCP discovers them as callable tools:

    - ``docs_list_sources``     — list available sources.
    - ``docs_search``           — RTD search + optional content extraction.
    - ``docs_fetch_page``       — fetch and parse an arbitrary doc page URL.
    - ``docs_search_key_pages`` — summarise a source's predefined key pages.

    Args:
        mcp: A ``FastMCP`` server instance on which the tools are registered.
            Typically ``DFTracerDocsService.docs_subservice``.
    """

    @mcp.tool()
    def docs_list_sources() -> str:
        """List all available dftracer documentation sources with their descriptions
        and base URLs.

        Returns JSON with a list of sources, each containing:
          - key:         identifier used in docs_search (e.g. "dftracer-utils")
          - name:        human-readable name
          - description: what this source covers
          - base_url:    root URL of the documentation
        """
        sources = [
            {
                "key": k,
                "name": v["name"],
                "description": v["description"],
                "base_url": v["base"],
            }
            for k, v in DOCS_SOURCES.items()
        ]
        return json.dumps({
            "status": "ok",
            "sources": sources,
            "usage": (
                "Pass any key or comma-separated list as the `source` parameter "
                "to docs_search, or use 'all' to search everywhere."
            ),
        }, indent=2)

    @mcp.tool()
    def docs_search(
        query: str,
        source: str = "all",
        max_results: int = 5,
        fetch_content: bool = True,
    ) -> str:
        """Search dftracer documentation and return relevant sections.

        Uses the ReadTheDocs search API to find matching pages, then fetches
        and extracts the most relevant sections from each page.  Results
        include the page title, URL, search highlights, and (when
        fetch_content=True) the best matching content sections with any code
        examples.

        Args:
            query:         Free-text search query.  Examples:
                             "how to annotate C functions"
                             "dftracer_split cli options"
                             "DFTRACER_C_FUNCTION_START"
                             "python decorator dft_fn"
                             "comp types io mem cpu comm"
            source:        Which documentation to search.  One of:
                             "all"             — all four sources (default)
                             "dftracer"        — core C/C++/Python library
                             "dftracer-utils"  — CLI tools (split, merge, stats…)
                             "pydftracer"      — Python bindings
                             "dfanalyzer"      — trace analysis API
                           Aliases: "utils", "py", "python", "analyzer", "core"
                           Multiple sources: "dftracer,dftracer-utils"
            max_results:   Maximum number of pages to return per source (default 5).
            fetch_content: If True (default), fetch each matching page and extract
                           the best matching sections.  Set to False to get only
                           titles and URLs (faster when you just need links).

        Returns:
            JSON with:
              - query:    the search query used
              - sources:  list of source keys searched
              - results:  per-source list of matching pages, each with:
                  - title, url, highlights (from RTD search)
                  - sections[]: [{heading, content, code_example?}] (when fetch_content=True)
              - total_pages_found: count across all sources
        """
        terms = re.split(r"\W+", query.lower())
        terms = [t for t in terms if len(t) > 2]

        source_keys = _resolve_source_keys(source)
        all_results: Dict[str, Any] = {}
        total = 0

        for key in source_keys:
            hits = _rtd_search(key, query, max_results=max_results)

            if not hits:
                # RTD API failed or returned nothing — try key pages directly
                src = DOCS_SOURCES[key]
                hits = []
                for page in src.get("key_pages", []):
                    page_url = f"{src['base']}/{page}"
                    hits.append({"title": page, "url": page_url, "highlights": []})
                hits = hits[:max_results]

            pages = []
            for hit in hits:
                entry: Dict[str, Any] = {
                    "title": hit["title"],
                    "url": hit["url"],
                    "highlights": hit.get("highlights", []),
                }
                if fetch_content and hit.get("url"):
                    extracted = _fetch_and_extract(hit["url"], terms, top_n=3)
                    if "error" not in extracted:
                        entry["page_title"] = extracted["title"]
                        entry["sections"] = extracted["sections"]
                pages.append(entry)

            if pages:
                all_results[key] = {
                    "source_name": DOCS_SOURCES[key]["name"],
                    "pages": pages,
                }
                total += len(pages)

        return json.dumps({
            "status": "ok",
            "query": query,
            "sources_searched": source_keys,
            "total_pages_found": total,
            "results": all_results,
        }, indent=2)

    @mcp.tool()
    def docs_fetch_page(
        url: str,
        query: Optional[str] = None,
        top_sections: int = 5,
    ) -> str:
        """Fetch a specific dftracer documentation page and return its content.

        Retrieves the page at `url`, parses the HTML, and returns the title
        and content sections.  When `query` is provided, returns only the most
        relevant sections (scored by term frequency); otherwise returns all
        sections in order.

        Args:
            url:          Full URL of the page to fetch.  Examples:
                            "https://dftracer.readthedocs.io/en/latest/c-api.html"
                            "https://dftracer.readthedocs.io/projects/utils/en/latest/cli.html"
            query:        Optional search query to filter/rank sections by
                          relevance.  If omitted, all sections are returned.
            top_sections: Maximum number of sections to return (default 5).

        Returns:
            JSON with:
              - url:      the fetched URL
              - title:    page title
              - sections: list of {heading, content, code_example?}
        """
        body = _fetch(url)
        if body is None:
            return json.dumps({
                "status": "error",
                "message": f"Could not fetch page: {url}",
                "url": url,
            }, indent=2)

        parsed = _parse_page(body)

        if query:
            terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
            sections = _best_sections(parsed, terms, top_n=top_sections, max_chars=1200)
        else:
            sections = [
                {
                    "heading": s.get("heading", ""),
                    "content": s.get("content", "")[:1200],
                    **({"code_example": s["code_blocks"][0][:600]}
                       if s.get("code_blocks") else {}),
                }
                for s in parsed.get("sections", [])[:top_sections]
            ]

        return json.dumps({
            "status": "ok",
            "url": url,
            "title": parsed.get("title", ""),
            "sections": sections,
        }, indent=2)

    @mcp.tool()
    def docs_search_key_pages(
        source: str,
        query: Optional[str] = None,
    ) -> str:
        """Fetch and summarise all key pages for a documentation source.

        Useful for a broad overview of what a source covers, or for
        discovering available pages before calling docs_search.

        Args:
            source:  Documentation source key: "dftracer", "dftracer-utils",
                     "pydftracer", or "dfanalyzer".
            query:   Optional query to rank sections by relevance within
                     each page.

        Returns:
            JSON with a list of pages, each containing title, url, and
            a short content summary.
        """
        source = _SOURCE_ALIASES.get(source.lower(), source.lower())
        src = DOCS_SOURCES.get(source)
        if src is None:
            available = ", ".join(DOCS_SOURCES)
            return json.dumps({
                "status": "error",
                "message": f"Unknown source '{source}'. Available: {available}",
            }, indent=2)

        terms = [t for t in re.split(r"\W+", (query or "").lower()) if len(t) > 2]
        pages = []
        for page_name in src.get("key_pages", []):
            url = f"{src['base']}/{page_name}"
            body = _fetch(url)
            if body is None:
                pages.append({"url": url, "error": "could not fetch"})
                continue
            parsed = _parse_page(body)
            if terms:
                sections = _best_sections(parsed, terms, top_n=2, max_chars=600)
            else:
                # Just first section as a summary
                sections = [
                    {"heading": s.get("heading", ""), "content": s.get("content", "")[:400]}
                    for s in parsed.get("sections", [])[:2]
                ]
            pages.append({
                "url": url,
                "title": parsed.get("title", page_name),
                "sections": sections,
            })

        return json.dumps({
            "status": "ok",
            "source": source,
            "source_name": src["name"],
            "base_url": src["base"],
            "pages": pages,
        }, indent=2)


MCPServiceFactory.register("dftracer-docs", DFTracerDocsService())
