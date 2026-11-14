"""Tests for AcademicPapersService — unit tests with httpx mocking."""
from __future__ import annotations

import asyncio
import importlib
import json
import sys
import textwrap

import pytest


# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

def _load_academic_module():
    return importlib.import_module("dftracer_agents.mcp_tools.tools.papers.academic_service")


@pytest.fixture(scope="module")
def amod():
    return _load_academic_module()


@pytest.fixture()
def service(amod):
    return amod.AcademicPapersService()


def _tool_map(service):
    return {t.name: t for t in asyncio.run(service.papers_subservice.list_tools())}


def _fn(tool):
    for attr in ("fn", "function", "callable", "handler", "_fn"):
        v = getattr(tool, attr, None)
        if callable(v):
            return v
    raise TypeError(f"No callable on {tool!r}")


def _call(tool, **kwargs):
    fn = _fn(tool)
    if asyncio.iscoroutinefunction(fn):
        return asyncio.run(fn(**kwargs))
    return fn(**kwargs)


# ---------------------------------------------------------------------------
# Fake httpx helpers
# ---------------------------------------------------------------------------

_ARXIV_XML_ONE = textwrap.dedent("""\
<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2310.12345v1</id>
    <title>Parallel I/O Optimization for HPC Workloads</title>
    <summary>We present a study of parallel I/O optimization techniques.</summary>
    <published>2023-10-19T00:00:00Z</published>
    <updated>2023-10-20T00:00:00Z</updated>
    <author><name>Alice Smith</name></author>
    <author><name>Bob Jones</name></author>
    <arxiv:primary_category term="cs.DC"/>
    <category term="cs.DC"/>
  </entry>
</feed>
""")

_S2_SEARCH_JSON = {
    "data": [
        {
            "title":         "Drishti: Guiding End-Users in I/O Optimization",
            "authors":       [{"name": "Jean Luca Bez"}, {"name": "Suren Byna"}],
            "year":          2022,
            "abstract":      "We present Drishti for I/O optimization guidance.",
            "citationCount": 42,
            "referenceCount": 15,
            "url":           "https://api.semanticscholar.org/paper/abc123",
            "openAccessPdf": {"url": "https://example.com/drishti.pdf"},
            "journal":       {"name": "PDSW 2022"},
        }
    ]
}

_S2_PAPER_JSON = {
    "title":          "WisIO: Automated I/O Bottleneck Detection",
    "authors":        [{"name": "Izzet Yildirim"}, {"name": "Hariharan Devarajan"}],
    "year":           2025,
    "abstract":       "Multi-perspective I/O bottleneck detection for HPC.",
    "citationCount":  10,
    "referenceCount": 30,
    "url":            "https://api.semanticscholar.org/paper/xyz789",
    "openAccessPdf":  None,
    "journal":        None,
    "references":     [
        {"citedPaper": {"title": "POSIX I/O Benchmarking", "year": 2020}},
    ],
    "citations":      [],
}

_S2_AUTHOR_JSON = {
    "data": [{"authorId": "123", "name": "Hariharan Devarajan",
              "affiliations": ["LLNL"], "paperCount": 50,
              "citationCount": 500, "hIndex": 12}]
}

_S2_AUTHOR_PAPERS_JSON = {
    "data": [
        {"title": "DFTracer: I/O Tracing for ML Workflows", "year": 2024,
         "citationCount": 20, "authors": [{"name": "H Devarajan"}],
         "url": "https://example.com/dftracer"}
    ]
}


class _FakeResp:
    def __init__(self, *, text="", json_data=None, status_code=200):
        self.text        = text
        self._json_data  = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("error", request=None, response=self)

    def json(self):
        return self._json_data


class _FakeClient:
    def __init__(self, responses: dict):
        self._responses = responses

    async def get(self, url, **kwargs):
        for key, resp in self._responses.items():
            if key in url:
                return resp
        return _FakeResp(text="", json_data={"data": []}, status_code=200)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _mock_httpx(amod, monkeypatch, responses: dict):
    import httpx

    class PatchedAsyncClient:
        def __init__(self, **kwargs):
            self._client = _FakeClient(responses)

        async def __aenter__(self):
            return self._client

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr(httpx, "AsyncClient", PatchedAsyncClient)


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    EXPECTED = {
        "search_arxiv", "get_arxiv_paper",
        "search_semantic_scholar", "get_semantic_scholar_paper",
        "get_author_papers", "search_papers_combined",
        # added since: OpenAlex / Crossref / DBLP / CORE search, web search and article fetch, relevance ranking
        "search_openalex", "get_openalex_paper", "search_crossref", "search_dblp", "search_core",
        "search_web", "fetch_webpage_article", "rank_papers_by_relevance",
    }

    def test_all_tools_registered(self, service):
        assert _tool_map(service).keys() == self.EXPECTED

    def test_service_name(self, service):
        assert service.name == "academic_papers"

    def test_factory_registration(self, amod):
        svc = amod.MCPServiceFactory.get_service("academic_papers")
        assert svc is not None
        assert svc.name == "academic_papers"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_parse_arxiv_entry_fields(self, amod):
        import xml.etree.ElementTree as ET
        ns = {"atom": "http://www.w3.org/2005/Atom",
              "arxiv": "http://arxiv.org/schemas/atom"}
        root = ET.fromstring(_ARXIV_XML_ONE)
        entries = root.findall("atom:entry", ns)
        assert entries
        paper = amod._parse_arxiv_entry(entries[0], ns)
        assert paper["title"] == "Parallel I/O Optimization for HPC Workloads"
        assert "Alice Smith" in paper["authors"]
        assert paper["id"] == "2310.12345v1"
        assert paper["pdf_url"].startswith("https://arxiv.org/pdf/")
        assert paper["source"] == "arXiv"

    def test_fmt_paper_includes_title_and_url(self, amod):
        p = {"title": "Test Paper", "authors": ["A", "B"], "published": "2024-01-01",
             "abs_url": "https://arxiv.org/abs/1234", "source": "arXiv"}
        text = amod._fmt_paper(p)
        assert "Test Paper" in text
        assert "A, B" in text
        assert "arxiv.org" in text

    def test_fmt_paper_truncates_long_abstract(self, amod):
        p = {"title": "X", "authors": [], "abstract": "A" * 500,
             "abs_url": "https://arxiv.org/abs/1", "source": "arXiv"}
        text = amod._fmt_paper(p)
        assert "…" in text

    def test_fmt_paper_no_abstract_key(self, amod):
        p = {"title": "Y", "authors": [], "source": "arXiv", "abs_url": "https://example.com"}
        text = amod._fmt_paper(p)
        assert "Y" in text


# ---------------------------------------------------------------------------
# search_arxiv
# ---------------------------------------------------------------------------

class TestSearchArxiv:
    def test_returns_formatted_results(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"arxiv.org": _FakeResp(text=_ARXIV_XML_ONE)})
        tools = _tool_map(service)
        out = _call(tools["search_arxiv"], query="parallel io optimization", max_results=5)
        assert "Parallel I/O Optimization" in out
        assert "Alice Smith" in out
        assert "arXiv" in out

    def test_no_results_message(self, service, amod, monkeypatch):
        empty_xml = ('<?xml version="1.0"?>'
                     '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        _mock_httpx(amod, monkeypatch,
                    {"arxiv.org": _FakeResp(text=empty_xml)})
        tools = _tool_map(service)
        out = _call(tools["search_arxiv"], query="xyzzy_not_a_real_topic")
        assert "No arXiv papers found" in out

    def test_max_results_clamped(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"arxiv.org": _FakeResp(text=_ARXIV_XML_ONE)})
        tools = _tool_map(service)
        # Should not raise even with out-of-range values
        _call(tools["search_arxiv"], query="test", max_results=999)
        _call(tools["search_arxiv"], query="test", max_results=0)

    def test_category_filter_passes_through(self, service, amod, monkeypatch):
        captured = []

        class CapturingClient:
            async def get(self, url, **kwargs):
                captured.append(kwargs.get("params", {}))
                return _FakeResp(text=_ARXIV_XML_ONE)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: CapturingClient())
        tools = _tool_map(service)
        _call(tools["search_arxiv"], query="io", category="cs.DC")
        assert any("cs.DC" in str(p.get("search_query", "")) for p in captured)


# ---------------------------------------------------------------------------
# get_arxiv_paper
# ---------------------------------------------------------------------------

class TestGetArxivPaper:
    def test_found_paper(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"arxiv.org": _FakeResp(text=_ARXIV_XML_ONE)})
        tools = _tool_map(service)
        out = _call(tools["get_arxiv_paper"], arxiv_id="2310.12345")
        assert "Parallel I/O Optimization" in out

    def test_not_found_returns_message(self, service, amod, monkeypatch):
        empty_xml = ('<?xml version="1.0"?>'
                     '<feed xmlns="http://www.w3.org/2005/Atom"></feed>')
        _mock_httpx(amod, monkeypatch,
                    {"arxiv.org": _FakeResp(text=empty_xml)})
        tools = _tool_map(service)
        out = _call(tools["get_arxiv_paper"], arxiv_id="0000.99999")
        assert "not found" in out.lower() or "No arXiv" in out

    def test_strips_arxiv_prefix(self, service, amod, monkeypatch):
        captured = []

        class CapturingClient:
            async def get(self, url, **kwargs):
                captured.append(kwargs.get("params", {}))
                return _FakeResp(text=_ARXIV_XML_ONE)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: CapturingClient())
        tools = _tool_map(service)
        _call(tools["get_arxiv_paper"], arxiv_id="arXiv:2310.12345")
        assert any(p.get("id_list") == "2310.12345" for p in captured)


# ---------------------------------------------------------------------------
# search_semantic_scholar
# ---------------------------------------------------------------------------

class TestSearchSemanticScholar:
    def test_returns_results(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data=_S2_SEARCH_JSON)})
        tools = _tool_map(service)
        out = _call(tools["search_semantic_scholar"], query="io optimization", max_results=5)
        assert "Drishti" in out
        assert "Jean Luca Bez" in out

    def test_no_results(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data={"data": []})})
        tools = _tool_map(service)
        out = _call(tools["search_semantic_scholar"], query="xyzzy_topic")
        assert "No Semantic Scholar" in out

    def test_min_citations_filter(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data=_S2_SEARCH_JSON)})
        tools = _tool_map(service)
        # The mock paper has 42 citations; filter at 100 should remove it
        out = _call(tools["search_semantic_scholar"], query="io", min_citations=100)
        assert "No papers found" in out or "Drishti" not in out


# ---------------------------------------------------------------------------
# get_semantic_scholar_paper
# ---------------------------------------------------------------------------

class TestGetSemanticScholarPaper:
    def test_found(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data=_S2_PAPER_JSON)})
        tools = _tool_map(service)
        out = _call(tools["get_semantic_scholar_paper"], paper_id="xyz789")
        assert "WisIO" in out
        assert "Izzet Yildirim" in out

    def test_not_found_404(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data={}, status_code=404)})
        tools = _tool_map(service)
        out = _call(tools["get_semantic_scholar_paper"], paper_id="bad_id")
        assert "not found" in out.lower()


# ---------------------------------------------------------------------------
# get_author_papers
# ---------------------------------------------------------------------------

class TestGetAuthorPapers:
    def test_returns_author_profile(self, service, amod, monkeypatch):
        call_num = [0]

        class MultiClient:
            async def get(self, url, **kwargs):
                call_num[0] += 1
                if "author/search" in url:
                    return _FakeResp(json_data=_S2_AUTHOR_JSON)
                return _FakeResp(json_data=_S2_AUTHOR_PAPERS_JSON)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: MultiClient())
        tools = _tool_map(service)
        out = _call(tools["get_author_papers"], author_name="Hariharan Devarajan")
        assert "Hariharan Devarajan" in out
        assert "DFTracer" in out

    def test_author_not_found(self, service, amod, monkeypatch):
        _mock_httpx(amod, monkeypatch,
                    {"semanticscholar.org": _FakeResp(json_data={"data": []})})
        tools = _tool_map(service)
        out = _call(tools["get_author_papers"], author_name="Nobody XYZ")
        assert "No author found" in out


# ---------------------------------------------------------------------------
# search_papers_combined
# ---------------------------------------------------------------------------

class TestSearchPapersCombined:
    def test_returns_both_sources(self, service, amod, monkeypatch):
        class MultiClient:
            async def get(self, url, **kwargs):
                if "arxiv.org" in url:
                    return _FakeResp(text=_ARXIV_XML_ONE)
                return _FakeResp(json_data=_S2_SEARCH_JSON)
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        import httpx
        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: MultiClient())
        tools = _tool_map(service)
        out = _call(tools["search_papers_combined"], query="io optimization")
        assert "arXiv" in out
        assert "Semantic Scholar" in out

    def test_partial_failure_graceful(self, service, amod, monkeypatch):
        """One source failing should not crash the combined tool."""
        import httpx

        class FailingS2Client:
            async def get(self, url, **kwargs):
                if "arxiv.org" in url:
                    return _FakeResp(text=_ARXIV_XML_ONE)
                raise httpx.RequestError("network error")
            async def __aenter__(self): return self
            async def __aexit__(self, *args): pass

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: FailingS2Client())
        tools = _tool_map(service)
        # Should not raise; combined tool uses return_exceptions=True
        out = _call(tools["search_papers_combined"], query="io")
        assert isinstance(out, str)
