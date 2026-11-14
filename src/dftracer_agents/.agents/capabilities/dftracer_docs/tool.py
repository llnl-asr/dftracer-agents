"""dftracer_docs: search and read the dftracer documentation on ReadTheDocs (dftracer core, dftracer-utils, pydftracer,
dfanalyzer) with live HTTP requests: RTD search API first, key pages as fallback, best-matching sections with code
examples extracted. A deterministic validator rules on a source selection, a page URL and a fetched page's parse."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.dftracer.docs_service import (
    DOCS_SOURCES,
    _SOURCE_ALIASES,
    _best_sections,
    _fetch,
    _fetch_and_extract,
    _parse_page,
    _resolve_source_keys,
    _rtd_search,
)


def _docs_verdict(source: str, url: str, html: str, query: str) -> Dict[str, Any]:
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    if source.strip():
        if source.strip().lower() == "all":
            add("source", True, f"all: {list(DOCS_SOURCES)}", "")
        else:
            toks = [t.lower() for t in re.split(r"[,\s]+", source.strip()) if t]
            unknown = [t for t in toks if _SOURCE_ALIASES.get(t, t) not in DOCS_SOURCES]
            add("source", not unknown, f"resolves to {_resolve_source_keys(source)}" + (f"; unknown: {unknown} (silently widened to all)" if unknown else ""),
                f"use a key or alias: {sorted(DOCS_SOURCES)} / {sorted(_SOURCE_ALIASES)}")
    if url.strip():
        known = [k for k, v in DOCS_SOURCES.items() if url.startswith(v["base"] + "/") or url == v["base"]]
        add("url", bool(known), f"page of {known[0]}" if known else "not under a known documentation base",
            "pick a URL from docs_search results or docs_list_sources base_url; other sites are not dftracer docs")
    if html.strip():
        parsed = _parse_page(html)
        secs = [s for s in parsed.get("sections") or [] if s.get("heading") or s.get("content")]
        add("parse", bool(secs), f"title {parsed.get('title', '')!r}, {len(secs)} section(s)",
            "the page has no extractable sections (not an RTD/Sphinx page, or fetched an error page): fetch the page URL again")
        if query.strip() and secs:
            terms = [t for t in re.split(r"\W+", query.lower()) if len(t) > 2]
            best = _best_sections(parsed, terms, top_n=1, max_chars=400)
            hit = bool(best) and any(t in (best[0].get("content", "") + best[0].get("heading", "")).lower() for t in terms)
            add("relevance", hit, (best[0].get("heading", "") if best else "no section") + " ranks first",
                "the page does not mention the query terms: search another source or rephrase")
    if not checks:
        return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "nothing to check",
                                                      "fix": "pass source, url and/or html"}]}
    return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "checks": checks}


def register(mcp):
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

    @mcp.tool()
    def dftracer_docs_check(source: str = "", url: str = "", html: str = "", query: str = "") -> dict:
        """Validate a docs lookup deterministically, offline. source: every token must be a known source key or alias
        (docs_search silently widens unknown ones to all); url: must be a page under a known documentation base; html (a
        fetched page) must parse into sections, and with query the best section must mention a query term. Use before
        docs_fetch_page on a URL from elsewhere, or to confirm a fetched page was a real docs page. Returns {status:
        pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}."""
        return _docs_verdict(source, url, html, query)

    @mcp.tool()
    def dftracer_docs_selftest() -> dict:
        """Self-test dftracer_docs_check offline: aliases 'py,utils' resolve; 'bogus' fails; a key page URL passes and a
        foreign URL fails; a small Sphinx-like HTML page parses into sections and a query term ranks its section first; an
        empty page fails on parse. Use to check the validator and the HTML parser. Returns {status: pass|fail, detail}."""
        page = ("<html><head><title>C API</title></head><body><div role='main'><h1>C API</h1><p>Intro text.</p>"
                "<h2>Function macros</h2><p>Use DFTRACER_C_FUNCTION_START in every function body.</p>"
                "<pre>DFTRACER_C_FUNCTION_START();</pre><h2>Init</h2><p>Call DFTRACER_C_INIT once.</p></div></body></html>")
        base = DOCS_SOURCES["dftracer"]["base"]
        r = {
            "alias": _docs_verdict("py,utils", "", "", "")["status"],
            "bogus": _docs_verdict("bogus", "", "", "")["status"],
            "url": _docs_verdict("", f"{base}/c-api.html", "", "")["status"],
            "foreign": _docs_verdict("", "https://example.org/x.html", "", "")["status"],
            "page": _docs_verdict("", "", page, "function macros start")["status"],
            "empty": _docs_verdict("", "", "<html></html>", "")["status"],
        }
        ok = r == {"alias": "pass", "bogus": "fail", "url": "pass", "foreign": "fail", "page": "pass", "empty": "fail"}
        return {"status": "pass" if ok else "fail", "detail": json.dumps(r)}
