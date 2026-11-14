---
name: dftracer_papers
description: Literature search and citations for dftracer optimizations - arXiv, Semantic Scholar, OpenAlex, Crossref, CORE, DBLP and web search, webpage article extraction, relevance ranking, the local paper/article library with RAG retrieval, the stack-wide optimization context search and the per-bottleneck optimization paper search, plus the key I/O papers (WisIO, Drishti), DFTracer documentation links and the session artifact-log conventions. Load when a proposal needs a citation, before marking a checklist category "not applicable", or on HTTP 429 from Semantic Scholar, SSL certificate verify failed behind a site CA, "CORE_API_KEY is not set", an empty search result, "diagnosis.json not found - run session_diagnose_bottlenecks first", or an uncited/malformed reference.
---
Cross-references: [[dftracer_opt_kb]], [[dftracer_optimize]], [[dftracer_optimize_io]], [[dftracer_optimize_compute]], [[dftracer_optimize_communication]], [[dftracer_optimize_memory]], [[dftracer_diagnose]]

# dftracer_papers

Tools: `search_arxiv`, `get_arxiv_paper`, `search_semantic_scholar`, `get_semantic_scholar_paper`,
`get_author_papers`, `search_openalex` (250M+ works, no key), `get_openalex_paper`, `search_crossref`
(DOI metadata, no key), `search_core` (full-text open access, needs `CORE_API_KEY`), `search_dblp`,
`search_web` (DuckDuckGo: vendor docs, blogs), `search_papers_combined` (all sources in parallel),
`fetch_webpage_article`, `rank_papers_by_relevance`; the local library `save_paper`, `save_article`,
`search_local_resources`, `list_local_resources`, `rag_search`; and the run-level searches
`session_search_optimization_papers` (arXiv per diagnosed bottleneck, writes
`<workspace>/optimization_papers.json`) and `session_search_optimization_context` (exhaustive,
stack-wide, local-first, plus benchmark-target numbers in `context_opportunities.json`). The validator
`dftracer_papers_check` is offline: it never calls the network.

## Rules
- Search order, local first (it is free and costs no rate limit): `opt_kb_lookup` ([[dftracer_opt_kb]])
  and `rag_search` / `search_local_resources` over the local library, then
  `session_search_optimization_context` (searches every detected software/system layer, not just the
  current bottleneck; remote fan-out only for what local did not answer; every kept hit is saved into
  the library so the next session's first step is free), then targeted `session_search_optimization_papers`
  / `search_papers_combined` / single-source searches.
- Run the literature search for every checklist category before marking it "not applicable": "I didn't
  look" is never a reason. The literature pass runs every iteration, in addition to diagnosis-driven
  proposals, never instead.
- Use the two key I/O papers when diagnosing bottlenecks and proposing optimizations: **WisIO**
  (Yildirim et al., ICS 2025: multi-perspective bottleneck detection; maps metric names such as
  small_io_pct, rand_pct, fetch_pressure to root causes and L1/L2/L3 fixes) and **Drishti** (Bez et al.,
  PDSW 2022: user-facing optimization journey; organizes suggestions into application / library / system
  tiers matching the L1/L2/L3 levels). The full metric mapping is in [[dftracer_optimize_io]].
- Citation preference: paper (arXiv/DOI/ACM/IEEE/USENIX) > official docs > web page >
  `session:<run_id>` (in-house measurement only). Save every paper/article you cite (`save_paper`,
  `save_article`) so the citation is reproducible offline.
- Every outbound call is client-side rate-limited per source (one lock per source, process-wide):
  semantic_scholar 1.2 s (the API key raises the daily quota, not the 1 req/s rate), arxiv 3.0 s,
  openalex and crossref 0.12 s, core 1.0 s, dblp 0.5 s, web_search 1.0 s. Never race the limit;
  prefer `search_papers_combined`, where the other sources fill in without waiting on Semantic Scholar.
- `rag_search` is retrieval only (no synthesis): it returns ranked passages for the agent to read.
- DFTracer references for implementing DFTracer behavior: https://dftracer.readthedocs.io/,
  https://dftracer.readthedocs.io/en/latest/api.html, https://dftracer.readthedocs.io/projects/python/en/latest/,
  https://dftracer.readthedocs.io/projects/utils/, https://dftracer.readthedocs.io/projects/analyzer/en/latest/,
  https://github.com/llnl-asr/dftracer
- Last step: run `dftracer_papers_check(citations_json)` on the citations (or `optimization_papers.json`)
  before they go into a proposal table or the final report.

## Constraints
- Never cite what you did not find: an uncited proposal is a guess and is rejected by `opt_proposal_table`.
- The local library lives at `$PROJECT_ROOT/.dftracer_agents/resources/` (project root = parent of
  `DFTRACER_WORKSPACES`, default `workspaces`): `papers/` (one PDF per paper), `articles/` (markdown),
  `index.json` (metadata and extracted-text pointers), `embeddings.npz` + `embeddings_meta.json` (RAG
  cache). It is a local, git-ignored cache outside `src/`: never commit it, never ship it in the wheel.
- API keys come from the environment only: `SEMANTIC_SCHOLAR_API_KEY`, `CORE_API_KEY` (free at
  https://core.ac.uk/services/api), `OPENALEX_MAILTO` (polite pool). Never write a key into a file,
  skill or log.
- Session artifact logs (from the references skill, HARD RULE): every log goes under
  `<workspace>/artifacts/`, named `<step>_<what>.log`; never only in the terminal, never under
  `<workspace>/tmp/` (wrapper scripts and scratch inputs only) or outside the workspace.

## Corner cases
- SSL behind a site/corporate CA: the httpx `verify` value resolves `HTTPX_SSL_VERIFY=false` (disable,
  last resort), then `REQUESTS_CA_BUNDLE` / `SSL_CERT_FILE` / `CURL_CA_BUNDLE`, then the common HPC
  bundles (`/etc/pki/tls/certs/ca-bundle.crt`, `/etc/ssl/certs/ca-certificates.crt`,
  `/etc/ssl/ca-bundle.pem`, `/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem`), then certifi. It is
  computed at import: set the variable and restart the server.
- `search_core` without `CORE_API_KEY` returns a "not set" message rather than failing; the combined
  search skips CORE silently.
- `rag_search` degrades to lexical scoring when `sentence-transformers` is not installed
  (`pip install -e '.[embeddings]'`); edited or re-saved entries have their stale chunks recomputed on the
  next call.
- `session_search_optimization_papers` needs `<workspace>/diagnosis.json` (run session_diagnose_bottlenecks
  first) and maps metric fragments (small_io, rand, read_time, write_time, metadata, fetch_pressure,
  epoch_straggler, checkpoint, intensity, imbalance, bw, fs_bw, comm, mem_bw, compute) to queries; with no
  mapped bottleneck it falls back to a general I/O query.
- In this capability the original async search tools run synchronously through `_run_async` (a worker
  thread when an event loop is already running); behavior and results are the same.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_papers_check` reports these checks; repair the citation list, validate again. The same failure
twice stops for the user.
- citations_parse: pass a JSON list (strings or `{title, url | doi | venue + year}` objects), an object
  with `papers`/`citations`, or a path to such a file.
- citations_present: search (local first, then remote) and cite what you found.
- citations_usable: give each citation a URL, DOI, arXiv id, a built-in reference key (wisio, drishti,
  mpi_collective, stream, roofline, data_stalls, fs_bandwidth) or "Author et al., Venue Year"; fetch
  missing metadata with `get_arxiv_paper` / `get_openalex_paper` / `search_crossref`.
- no_duplicates: deduplicate by title (a paper found by several sources is one citation).
- arxiv_ids_wellformed: an arXiv id is YYMM.NNNNN (or archive/NNNNNNN); re-fetch the paper to get its id.

## Self-heal
- If a search tool is wrong (endpoint change, parser break), capability_update it; do not scrape by hand.
- HTTP 429 or a timeout: wait for the source's interval or use `search_papers_combined` / another source.

## Self-learn
- What is explored here becomes a capability: see capability_builder. A source that keeps proving
  useful for a component (e.g. a venue or a benchmark-number paper) becomes a lesson here, its citations
  go into the local library, and measured outcomes go to [[dftracer_opt_kb]].

## What works
- 2026-11-13 (conversion): local first (`opt_kb_lookup`, `rag_search`), then
  `session_search_optimization_context` per run, `search_papers_combined` for a topic, `save_paper` every
  cited paper, `dftracer_papers_check` before citing.

## Reference: artifact log step numbering
Every `session_*` tool call writes a stage log to `<workspace>/artifacts/<NN>_<stage_name>.log`
(timestamp, run_id, step number, stdout, stderr, exit code, stage-specific fields):

| NN | Log file | Session tool |
|----|----------|--------------|
| 01 | `01_session_create.log` | session_create |
| 02 | `02_session_detect.log` | session_detect |
| 03 | `03_session_configure.log` | session_configure |
| 04 | `04_session_build_install.log` | session_build_install |
| 05 | `05_session_run_smoke_test.log` | session_run_smoke_test |
| 06 | `06_session_copy_annotated.log` | session_copy_annotated |
| 07 | `07_session_patch_build.log` | session_patch_build |
| 08 | `08_session_annotate_source.log` | session_annotate_source |
| 09 | `09_session_install_dftracer.log` | session_install_dftracer |
| 10 | `10_session_build_annotated.log` | session_build_annotated |
| 11 | `11_session_run_with_dftracer.log` | session_run_with_dftracer |
| 12 | `12_session_split_traces.log` | session_split_traces |
| 13 | `13_session_analyze_traces.log` | session_analyze_traces |

After each step: exit code 0, stdout has the stage's expected output, no unexpected stderr errors. After
`session_annotate_source`, inspect `<workspace>/annotation.patch` and confirm it applies with
`patch --dry-run -p1 < annotation.patch` from `source/`. On failure, read the failing step's log,
diagnose from stderr, and retry that individual `session_*` tool, not the full pipeline. Name your own
logs `<step>_<what>.log` (e.g. `opt3_run.log`, `opt1_build.log`) so `session_final_report` collects them.

## Reference: key papers
- WisIO: Yildirim, I., Devarajan, H., Kougkas, A., Sun, X.-H., Mohror, K., *WisIO: Automated I/O
  Bottleneck Detection with Multi-Perspective Views for HPC Workflows*, ACM ICS 2025, pp. 749-763,
  https://dl.acm.org/doi/10.1145/3721145.3730395
- Drishti: Bez, J. L. et al., PDSW 2022 (application / library / system optimization tiers).
