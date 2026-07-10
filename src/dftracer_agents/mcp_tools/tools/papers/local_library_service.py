"""Local paper/article library — a project-root research cache with fuzzy search.

Every paper or article an agent finds useful (via ``academic_service`` or
manual review) can be persisted here so later dftracer sessions don't re-pay
the network/rate-limit cost of re-discovering it. Storage lives at
``$PROJECT_ROOT/.dftracer_agents/resources/`` — a hidden, purely local cache
directory, **outside** ``src/`` (never part of the ``dftracer-agents`` pip
package — see ``pyproject.toml``'s ``[tool.setuptools.packages.find]`` which
only walks ``src/``) and git-ignored (``.dftracer_agents/`` in ``.gitignore``).
It is a local, persistent, session-independent cache maintained by hand and by
agents over time — never pushed to GitHub, never shipped in the wheel.

The directory structure is created eagerly at MCP server startup (see
``ensure_resources_setup`` below, wired into ``mcp_server.py``'s
``_run_startup_setup``), not just lazily on first ``save_paper`` call.

Layout::

    .dftracer_agents/resources/
      papers/        downloaded PDFs, one file per paper
      articles/      saved web articles/docs, as markdown
      index.json     metadata + cached extracted text pointers for every entry

Tools
-----
* ``save_paper``            — download a paper PDF into the local library
* ``save_article``          — save a web article/doc into the local library
* ``search_local_resources``— fuzzy full-text search across the whole library
* ``list_local_resources``  — list everything currently stored
* ``rag_search``            — semantic + lexical retrieval, ranked by relevance
  to a query plus optional bottleneck/system-config context (see ``rag_service.py``)
"""
from __future__ import annotations

import difflib
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory
from .academic_service import _SSL_VERIFY, _WEB_UA, _parse_webpage, _rate_limit

# rag_service imports _resources_dirs/_text_sidecar_path from this module at
# its own top level, so importing it back here at module scope would deadlock
# on the partially-initialized module. Imported lazily inside the tool body
# below instead — see rag_search().

RESOURCES_SUBDIR = ".dftracer_agents/resources"
PAPERS_SUBDIR    = ".dftracer_agents/resources/papers"
ARTICLES_SUBDIR  = ".dftracer_agents/resources/articles"
INDEX_RELPATH    = ".dftracer_agents/resources/index.json"


def _project_root() -> Path:
    """Resolve the project root the same way ``session_search_local_papers`` does.

    ``DFTRACER_WORKSPACES`` (default ``"workspaces"``) is always a direct
    child of the project root, so its parent is the project root regardless
    of where the MCP server process's cwd happens to be.
    """
    env = os.environ.get("DFTRACER_WORKSPACES", "workspaces")
    ws_root = Path(env)
    ws_root = ws_root if ws_root.is_absolute() else Path.cwd() / ws_root
    return ws_root.parent


def _resources_dirs() -> Dict[str, Path]:
    root = _project_root()
    papers = root / PAPERS_SUBDIR
    articles = root / ARTICLES_SUBDIR
    papers.mkdir(parents=True, exist_ok=True)
    articles.mkdir(parents=True, exist_ok=True)
    return {"root": root, "papers": papers, "articles": articles,
            "index": root / INDEX_RELPATH}


def ensure_resources_setup(target_root: Optional[Path] = None, force: bool = False) -> Dict[str, Any]:
    """Create the ``.dftracer_agents/resources/{papers,articles}`` cache directories.

    Called automatically by ``dftracer-mcp-server`` on startup (see
    ``mcp_server.py``'s ``_run_startup_setup``), the same place skills/agents/
    workspace setup runs, so the cache directory structure exists before any
    agent's first ``save_paper``/``rag_search`` call rather than being created
    lazily and silently on first use.

    ``target_root``/``force`` are accepted only for signature compatibility
    with the other startup-setup functions (``ensure_setup``, etc.) — actual
    directory resolution always goes through ``_project_root()``/
    ``_resources_dirs()``, the single source of truth every other tool in this
    module already uses, so this never diverges from where ``save_paper``
    actually writes.

    Returns:
        Dict with ``status`` (``"installed"`` if a directory was freshly
        created, ``"already_done"`` otherwise) and ``target`` (the resolved
        ``.dftracer_agents/resources`` path).
    """
    root = _project_root()
    resources_root = root / RESOURCES_SUBDIR
    already_existed = resources_root.exists()
    _resources_dirs()  # creates papers/ and articles/ (mkdir is idempotent)
    return {
        "status": "already_done" if already_existed else "installed",
        "target": str(resources_root),
    }


def _load_index() -> List[Dict[str, Any]]:
    idx_path = _resources_dirs()["index"]
    if not idx_path.exists():
        return []
    try:
        return json.loads(idx_path.read_text())
    except Exception:
        return []


def _save_index(entries: List[Dict[str, Any]]) -> None:
    idx_path = _resources_dirs()["index"]
    idx_path.write_text(json.dumps(entries, indent=2))


def _slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return slug[:max_len] or hashlib.sha1(text.encode()).hexdigest()[:12]


def _record_id(url_or_title: str) -> str:
    return hashlib.sha1(url_or_title.encode()).hexdigest()[:12]


def _extract_pdf_text(pdf_path: Path) -> str:
    """Best-effort text extraction; returns '' if pypdf is unavailable or extraction fails."""
    try:
        import pypdf
    except ImportError:
        return ""
    try:
        reader = pypdf.PdfReader(str(pdf_path))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception:
        return ""


def _text_sidecar_path(stored_path: Path) -> Path:
    return stored_path.with_suffix(stored_path.suffix + ".txt")


# ---------------------------------------------------------------------------
# Fuzzy scoring
# ---------------------------------------------------------------------------

def _fuzzy_score(query: str, entry: Dict[str, Any], full_text: str) -> Tuple[float, str]:
    """Return (score, best_snippet) for one library entry against *query*.

    Combines three signals so both "close title match" and "term appears
    somewhere in the body" queries work well without any external
    dependency:

    1. ``difflib`` similarity ratio of query vs. title (catches
       misspellings / partial titles).
    2. Fraction of query tokens found (as substrings, case-insensitive) in
       title + abstract + full text.
    3. A short snippet around the first strong match, for context.
    """
    title = (entry.get("title") or "").lower()
    abstract = (entry.get("abstract") or "").lower()
    q = query.lower().strip()
    tokens = [t for t in re.split(r"\W+", q) if len(t) > 2]

    title_ratio = difflib.SequenceMatcher(None, q, title).ratio() if title else 0.0

    haystack = f"{title} {abstract} {full_text.lower()}"
    hits = sum(1 for t in tokens if t in haystack)
    token_score = hits / len(tokens) if tokens else 0.0

    # Whole-query substring match anywhere is a strong signal.
    exact_bonus = 0.5 if q and q in haystack else 0.0

    score = title_ratio * 2.0 + token_score * 3.0 + exact_bonus

    snippet = abstract[:240]
    if full_text:
        for t in tokens:
            m = re.search(re.escape(t), full_text, re.IGNORECASE)
            if m:
                start = max(0, m.start() - 120)
                snippet = full_text[start:m.start() + 200].replace("\n", " ").strip()
                break

    return score, snippet


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class LocalLibraryService(MCPService):
    """MCP service for the project-root local paper/article research cache."""

    def __init__(self) -> None:
        self.library_subservice = FastMCP("LocalLibrary")
        self._register_tools()

    def _register_tools(self) -> None:  # noqa: C901

        @self.library_subservice.tool()
        async def save_paper(
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

        @self.library_subservice.tool()
        async def save_article(
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

        @self.library_subservice.tool()
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

        @self.library_subservice.tool()
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

        @self.library_subservice.tool()
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

    def execute(self, data: dict) -> str:
        return "Use save_paper/save_article to populate the local library, search_local_resources to query it."

    @property
    def name(self) -> str:
        return "local_library"


MCPServiceFactory.register("local_library", LocalLibraryService())
