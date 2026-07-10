Tools
=====

The MCP server exposes multiple tool groups. These are the major ones that the
current repository wires together.

Core services
-------------

``DFTracerUtils``
  The low-level dftracer trace utilities. These wrap the ``dftracer_*``
  command-line tools and expose trace merge/split/index/inspect operations.

``DFAnalyzer``
  Analyzer tools for reading compacted traces and generating summaries.

``DFTracerPlot``
  Plotting tools for generating charts from trace data.

``DFTracerDocs``
  Documentation/search helpers for dftracer reference material.

``DFTracerSkills``
  Skills discovery tools used by harnesses that do not natively discover
  ``SKILL.md`` files.

``DFDiagnoser``
  Trace diagnosis tools that map symptoms to likely bottlenecks.

``AcademicPapers``
  Paper/article search and retrieval helpers used by the optimization
  workflow, plus a local paper library. Searches seven sources — arXiv,
  Semantic Scholar, OpenAlex, Crossref, CORE, DBLP, and general web search
  (DuckDuckGo) — each independently client-side rate-limited so one source's
  budget (notably Semantic Scholar's 1 request/second introductory tier)
  never blocks the others. See :ref:`configuring-api-keys` for how to set
  ``SEMANTIC_SCHOLAR_API_KEY``, ``CORE_API_KEY``, and ``OPENALEX_MAILTO`` —
  all optional, every source falls back to anonymous access.

  Search tools: ``search_arxiv``, ``search_semantic_scholar``,
  ``search_openalex``, ``search_crossref``, ``search_core``, ``search_dblp``,
  ``search_web``, ``search_papers_combined`` (fans out to every configured
  source in parallel).

  Fetch/rank tools: ``get_arxiv_paper``, ``get_semantic_scholar_paper``,
  ``get_openalex_paper``, ``get_author_papers``, ``fetch_webpage_article``,
  ``rank_papers_by_relevance``.

  Local library tools — persist findings to ``resources/`` at the project
  root (outside ``src/``, so never shipped in the pip package, and
  git-ignored): ``save_paper`` (download a PDF, extract full text with
  ``pypdf``), ``save_article`` (save a fetched web page as markdown),
  ``search_local_resources`` (fuzzy search — title similarity + query-token
  coverage — across every stored paper/article's full text), and
  ``list_local_resources``.

``DFTracerSystem``
  System detection and system catalog helpers.

``DFTracerSession``
  Session management, build, annotation, smoke-test, and optimization tools.

Session-level tools
-------------------

Commonly used session tools include:

* ``session_create``
* ``session_detect``
* ``session_configure``
* ``session_build_install``
* ``session_run_smoke_test``
* ``session_install_dftracer``
* ``session_annotate_c_file``
* ``session_annotate_cpp_file``
* ``session_annotate_python_file``
* ``session_run_with_dftracer``
* ``session_split_traces``
* ``session_analyze_traces``
* ``session_diagnose_bottlenecks``
* ``session_generate_optimization_proposals``
* ``session_optimize_l1_app``
* ``session_optimize_l2_software``
* ``session_optimize_l3_filesystem``

Trace utilities
---------------

The repository’s own guidance says to use the MCP trace utilities instead of
raw Python or gzip scripts when reading ``.pfw`` / ``.pfw.gz`` files.

That includes tools such as:

* ``reader``
* ``info``
* ``split``
* ``event_count``
* ``comparator``
