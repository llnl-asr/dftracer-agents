---
name: dftracer_docs
description: Search and read the official dftracer documentation on ReadTheDocs (dftracer core, dftracer-utils, pydftracer, dfanalyzer) through docs_search / docs_fetch_page / docs_search_key_pages / docs_list_sources, and validate source strings, URLs and fetched pages with dftracer_docs_check. Load when a needed dftracer API, macro, environment variable or CLI option is not covered by a skill.
---
Cross-references: [[dftracer_graph]], [[dftracer_skills]], [[dftracer_lessons]]

# dftracer documentation lookup

Sources (key -> what it covers; aliases in brackets):
- `dftracer` [core, main]: C/C++/Python tracing library: macros, init/fini, configuration (`https://dftracer.readthedocs.io/en/latest`).
- `dftracer-utils` [utils]: CLI tools: reader, split, merge, stats, aggregator, replay.
- `pydftracer` [py, python]: Python bindings and decorator API.
- `dfanalyzer` [analyzer]: trace analysis engine, Python API and query interface.

## Rules
- Skills and the knowledge graph first ([[dftracer_graph]]: `graph_query(mode="docs")`); the online docs are for the
  official API that no skill covers.
- `docs_search(query, source=<narrowest>, max_results=5, fetch_content=True)`: RTD v2 search per source, then the
  best 3 matching sections per page with a code example. Multiple sources: `"dftracer,dftracer-utils"`; `"all"` searches
  every source.
- `docs_fetch_page(url, query=..., top_sections=5)` for one page; `docs_search_key_pages(source, query)` for an overview
  of a source's predefined key pages.
- Quote the URL of every fact taken from the docs.

## Constraints
- Live HTTP to ReadTheDocs with a 15 s timeout per request, no caching; results reflect the hosted docs now.
- Never send user or project details to the docs site: queries are about the dftracer API only.

## Corner cases
- RTD search returns nothing or the API is down: `docs_search` falls back to the source's key pages (titles only plus
  sections when fetchable).
- An unknown source token is silently widened to all sources by `docs_search`; `dftracer_docs_check(source=...)` reports it.
- A fetched page with no extractable sections is usually an error page or not a Sphinx page.
- No network on a compute node: the tools return errors or empty results; do the lookup from a login node.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_docs_check` failures: `source` -> use a key or alias from the list above; `url` -> take a URL from
`docs_search` results or `docs_list_sources` base_url; `parse` -> refetch the page (or another key page); `relevance`
-> rephrase or search another source. "Could not fetch page" from docs_fetch_page -> check the URL with the validator
and the network, then retry once.

## Self-heal
- A source moved or a key page was renamed: update `DOCS_SOURCES` in `mcp_tools/tools/dftracer/docs_service.py`
  (dftracer-agents) or `capability_update` this capability, and restart the MCP server.

## Self-learn
- What is explored here becomes a capability: a fact looked up repeatedly becomes part of the stage capability or the
  `software_<lib>` / `tools_<x>` capability that needs it, so the next run does not fetch it; see capability_builder.

## What works
- `docs_search("DFTRACER_C_FUNCTION_START", source="dftracer")` then `docs_fetch_page(<c-api url>, query="function start")`.
- `docs_search("dftracer_split cli options", source="utils", fetch_content=False)` for links only (faster).
