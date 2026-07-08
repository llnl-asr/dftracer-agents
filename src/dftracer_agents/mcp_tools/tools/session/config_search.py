"""Configuration search helpers — paper-backed and repo-backed run parameter discovery.

Provides synchronous wrappers around the async academic paper search AND GitHub
repository search so that session tools (which are all sync) can discover
application-specific configuration recommendations before a production run.

Typical flow (called from ``session_search_papers_for_config``):

1. Build targeted queries from *app_name* + *problem_name*.
2. Search arXiv + Semantic Scholar in parallel via ``_search_papers_combined_sync``.
3. Search the app's official GitHub repository for benchmark parameter files.
4. Fetch the top paper(s) and extract parameter tables / config snippets.
5. Return structured recommendations + persist them to ``session.json``.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

# Re-use the low-level helpers from the papers service
from ..papers.academic_service import _arxiv_search, _SSL_VERIFY

# ---------------------------------------------------------------------------
# Sync wrappers around async paper search
# ---------------------------------------------------------------------------

def _run_async(coro):
    """Run an async coroutine from synchronous code."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # If we are already inside an async context (e.g. FastMCP event loop),
    # we cannot use asyncio.run.  In that case we schedule the coroutine on
    # the running loop and return a Future-like object.  FastMCP tool bodies
    # are sync, so this path is unlikely, but we guard against it.
    import concurrent.futures
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


def _search_arxiv_sync(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """Synchronous wrapper for arXiv search."""
    return _run_async(_arxiv_search(query, max_results=max_results))


def _search_papers_combined_sync(
    query: str,
    max_results_each: int = 3,
) -> Dict[str, Any]:
    """Search both arXiv and Semantic Scholar synchronously.

    Returns a dict with keys ``arxiv``, ``semantic_scholar``, ``combined_count``.
    """
    from ..papers.academic_service import _arxiv_search

    arxiv_papers = _search_arxiv_sync(query, max_results=max_results_each)
    # Semantic Scholar does not have a module-level async helper exposed,
    # so we fall back to a simple httpx call here.
    import httpx
    S2_BASE = "https://api.semanticscholar.org/graph/v1"
    S2_PAPER_FIELDS = (
        "title,authors,year,abstract,citationCount,referenceCount,"
        "url,externalIds,publicationDate,journal,openAccessPdf"
    )
    s2_papers: List[Dict[str, Any]] = []
    try:
        with httpx.Client(timeout=30, verify=_SSL_VERIFY) as client:
            resp = client.get(
                f"{S2_BASE}/paper/search",
                params={
                    "query": query,
                    "limit": max_results_each,
                    "fields": S2_PAPER_FIELDS,
                },
            )
            resp.raise_for_status()
            data = resp.json()
            for p in data.get("data", []):
                pdf_url = None
                oa = p.get("openAccessPdf")
                if oa:
                    pdf_url = oa.get("url")
                s2_papers.append({
                    "title": p.get("title", ""),
                    "authors": [a.get("name", "") for a in p.get("authors", [])],
                    "year": p.get("year"),
                    "abstract": p.get("abstract", ""),
                    "citationCount": p.get("citationCount"),
                    "url": p.get("url", ""),
                    "pdf_url": pdf_url,
                    "source": "Semantic Scholar",
                })
    except Exception:
        pass  # S2 is best-effort

    return {
        "arxiv": arxiv_papers,
        "semantic_scholar": s2_papers,
        "combined_count": len(arxiv_papers) + len(s2_papers),
    }


# ---------------------------------------------------------------------------
# GitHub repository search for benchmark parameter files
# ---------------------------------------------------------------------------

# Mapping of app names to their official GitHub repositories
_APP_GITHUB_REPOS: Dict[str, str] = {
    "flash-x": "Flash-X/Flash-X",
    "flashx": "Flash-X/Flash-X",
    "ior": "hpc/ior",
    "h5bench": "hpc-io/h5bench",
    "montage": "Montage-Framework/montage",
    "pegasus": "pegasus-isi/pegasus",
}


def _search_github_repo_for_params(
    repo: str,
    problem_name: Optional[str] = None,
    max_results: int = 10,
) -> List[Dict[str, Any]]:
    """Search a GitHub repository for parameter files matching a problem.

    Uses the GitHub REST API ``/search/code`` endpoint to find files that
    contain known parameter patterns (e.g. ``nblockx``, ``lrefine_max``).
    Returns a list of file metadata dicts with ``path``, ``url``, and
    ``html_url`` keys.
    """
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    # Build a query that looks for parameter files in the repo
    # We search for common parameter keywords in the repo's code
    queries = []
    if problem_name:
        queries.append(f"repo:{repo} {problem_name} nblockx")
        queries.append(f"repo:{repo} {problem_name} lrefine_max")
        queries.append(f"repo:{repo} {problem_name} checkpointFileIntervalTime")
    else:
        queries.append(f"repo:{repo} nblockx")
        queries.append(f"repo:{repo} lrefine_max")
        queries.append(f"repo:{repo} checkpointFileIntervalTime")

    found_files: List[Dict[str, Any]] = []
    seen_paths: set = set()

    for q in queries:
        try:
            with httpx.Client(timeout=30, verify=_SSL_VERIFY) as client:
                resp = client.get(
                    "https://api.github.com/search/code",
                    headers=headers,
                    params={"q": q, "per_page": max_results},
                )
                resp.raise_for_status()
                data = resp.json()
                for item in data.get("items", []):
                    path = item.get("path", "")
                    if path in seen_paths:
                        continue
                    seen_paths.add(path)
                    found_files.append({
                        "path": path,
                        "url": item.get("url", ""),
                        "html_url": item.get("html_url", ""),
                        "repository": repo,
                        "query": q,
                    })
        except Exception:
            pass  # GitHub search is best-effort

    return found_files


def _fetch_github_file_raw(repo: str, path: str) -> Optional[str]:
    """Fetch the raw content of a file from a GitHub repository.

    Uses the GitHub raw content URL (no auth required for public repos).
    """
    raw_url = f"https://raw.githubusercontent.com/{repo}/main/{path}"
    try:
        with httpx.Client(timeout=30, verify=_SSL_VERIFY) as client:
            resp = client.get(raw_url)
            if resp.status_code == 200:
                return resp.text
            # Try "master" branch if "main" fails
            raw_url = f"https://raw.githubusercontent.com/{repo}/master/{path}"
            resp = client.get(raw_url)
            if resp.status_code == 200:
                return resp.text
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Configuration extraction heuristics
# ---------------------------------------------------------------------------

# Known parameter patterns for common HPC / AMR codes.
# Each entry maps a regex (case-insensitive) to a canonical parameter name.
_KNOWN_PARAM_PATTERNS: Dict[str, str] = {
    # Flash-X / FLASH
    r"nblockx\s*=\s*(\d+)": "nblockx",
    r"nblocky\s*=\s*(\d+)": "nblocky",
    r"nblockz\s*=\s*(\d+)": "nblockz",
    r"lrefine_max\s*=\s*(\d+)": "lrefine_max",
    r"lrefine_min\s*=\s*(\d+)": "lrefine_min",
    r"checkpointFileIntervalTime\s*=\s*([\d.]+)": "checkpointFileIntervalTime",
    r"checkpointFileIntervalStep\s*=\s*(\d+)": "checkpointFileIntervalStep",
    r"wall_clock_time_limit\s*=\s*(\d+)": "wall_clock_time_limit",
    r"useCollectiveHDF5\s*=\s*(\.\w+\.)": "useCollectiveHDF5",
    r"iProcs\s*=\s*(\d+)": "iProcs",
    r"jProcs\s*=\s*(\d+)": "jProcs",
    r"kProcs\s*=\s*(\d+)": "kProcs",
    r"nxb\s*=\s*(\d+)": "nxb",
    r"nyb\s*=\s*(\d+)": "nyb",
    r"nzb\s*=\s*(\d+)": "nzb",
    r"nend\s*=\s*(\d+)": "nend",
    r"tmax\s*=\s*([\d.]+)": "tmax",
    # IOR
    r"transferSize\s*=\s*(\d+[kmgtKMGT]?)": "transferSize",
    r"blockSize\s*=\s*(\d+[kmgtKMGT]?)": "blockSize",
    r"segmentCount\s*=\s*(\d+)": "segmentCount",
    r"repetitions\s*=\s*(\d+)": "repetitions",
    # H5Bench
    r"MEMBERS\s*=\s*(\d+)": "MEMBERS",
    r"DIMENSIONS\s*=\s*([\d,\s]+)": "DIMENSIONS",
    r"TIMESTEPS\s*=\s*(\d+)": "TIMESTEPS",
    r"COMPRESS\s*=\s*(\w+)": "COMPRESS",
}


def _extract_params_from_text(text: str) -> Dict[str, Any]:
    """Scan *text* for known parameter patterns and return a dict of findings."""
    found: Dict[str, Any] = {}
    for pattern, canonical in _KNOWN_PARAM_PATTERNS.items():
        for match in re.finditer(pattern, text, re.IGNORECASE):
            val = match.group(1).strip()
            # Try to coerce to int/float/bool
            if val.lower() in (".true.", "true", ".yes.", "yes"):
                coerced = True
            elif val.lower() in (".false.", "false", ".no.", "no"):
                coerced = False
            else:
                try:
                    coerced = int(val)
                except ValueError:
                    try:
                        coerced = float(val)
                    except ValueError:
                        coerced = val
            found[canonical] = coerced
    return found


def _build_queries(app_name: str, problem_name: Optional[str] = None) -> List[str]:
    """Return a list of search queries tailored to *app_name* and *problem_name*."""
    queries: List[str] = []
    app = app_name.lower()
    problem = (problem_name or "").lower()

    # Generic benchmark / scaling / configuration queries
    queries.append(f"{app_name} benchmark configuration scaling")
    queries.append(f"{app_name} production run parameters")
    if problem:
        queries.append(f"{app_name} {problem_name} benchmark parameters")
        queries.append(f"{app_name} {problem_name} scaling study")

    # App-specific tuned queries
    if "flash" in app:
        queries.append("Flash-X Sedov AMR checkpoint configuration")
        queries.append("FLASH AMR nblockx nblocky nblockz benchmark")
        queries.append("Flash-X checkpoint restart AMR parameters")
    elif "ior" in app:
        queries.append("IOR benchmark transferSize blockSize configuration")
    elif "h5bench" in app:
        queries.append("h5bench HDF5 benchmark configuration parameters")
    elif "montage" in app or "pegasus" in app:
        queries.append("Montage workflow I/O configuration benchmark")

    # Deduplicate while preserving order
    seen = set()
    uniq = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            uniq.append(q)
    return uniq


# ---------------------------------------------------------------------------
# Public helper used by the session tool
# ---------------------------------------------------------------------------

def search_papers_for_config(
    app_name: str,
    problem_name: Optional[str] = None,
    max_results_each: int = 3,
) -> Dict[str, Any]:
    """Search literature AND GitHub repos for configuration recommendations.

    This is the primary entry point for paper-backed and repo-backed
    configuration discovery.  It searches:

    1. arXiv + Semantic Scholar for academic papers with parameter tables.
    2. The app's official GitHub repository for benchmark parameter files.

    Returns a structured dict with:
        * ``queries`` — list of search strings used.
        * ``papers`` — combined list of paper dicts (title, authors, year, url, pdf_url).
        * ``github_files`` — list of benchmark parameter files found in the repo.
        * ``github_params`` — dict of parameter names → values extracted from repo files.
        * ``extracted_params`` — combined dict of all parameters found (papers + GitHub).
        * ``recommendations`` — human-readable bullet list of config suggestions.
    """
    queries = _build_queries(app_name, problem_name)
    all_papers: List[Dict[str, Any]] = []
    combined_params: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # 1. Search academic literature (arXiv + Semantic Scholar)
    # ------------------------------------------------------------------
    for q in queries:
        result = _search_papers_combined_sync(q, max_results_each=max_results_each)
        for src in ("arxiv", "semantic_scholar"):
            for paper in result.get(src, []):
                all_papers.append(paper)
                # Extract parameters from title + abstract
                text = f"{paper.get('title', '')}\n{paper.get('abstract', '')}"
                params = _extract_params_from_text(text)
                combined_params.update(params)

    # ------------------------------------------------------------------
    # 2. Search GitHub repository for benchmark parameter files
    # ------------------------------------------------------------------
    github_files: List[Dict[str, Any]] = []
    github_params: Dict[str, Any] = {}
    repo = _APP_GITHUB_REPOS.get(app_name.lower())
    if repo:
        github_files = _search_github_repo_for_params(
            repo, problem_name=problem_name, max_results=10
        )
        # Fetch raw content of the top files and extract parameters
        for gf in github_files[:5]:  # Limit to top 5 to avoid rate limits
            raw_text = _fetch_github_file_raw(repo, gf["path"])
            if raw_text:
                params = _extract_params_from_text(raw_text)
                github_params.update(params)
                gf["extracted_params"] = params
        combined_params.update(github_params)

    # ------------------------------------------------------------------
    # 3. Build human-readable recommendations
    # ------------------------------------------------------------------
    recommendations: List[str] = []
    if combined_params:
        recommendations.append("Parameters found in literature and repository:")
        for k, v in sorted(combined_params.items()):
            recommendations.append(f"  * {k} = {v}")
    else:
        recommendations.append(
            "No specific parameter values were found in paper abstracts or "
            "repository files. Consider fetching full PDFs for deeper extraction."
        )

    if github_files:
        recommendations.append("")
        recommendations.append(
            f"Found {len(github_files)} benchmark parameter file(s) in "
            f"{repo}:"
        )
        for gf in github_files[:5]:
            recommendations.append(
                f"  * {gf['path']} — {gf.get('html_url', '')}"
            )

    # Deduplicate papers by title
    seen_titles: set = set()
    deduped: List[Dict[str, Any]] = []
    for p in all_papers:
        t = p.get("title", "").lower().strip()
        if t and t not in seen_titles:
            seen_titles.add(t)
            deduped.append(p)

    return {
        "queries": queries,
        "papers": deduped,
        "github_files": github_files,
        "github_params": github_params,
        "extracted_params": combined_params,
        "recommendations": recommendations,
        "paper_count": len(deduped),
        "github_file_count": len(github_files),
    }
