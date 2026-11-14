# What worked: dftracer_papers

One entry per key, newest wins. Portable: no user paths.

- [tool:session_search_optimization_context] 2026-11-13: run once per iteration after diagnosis; local KB + rag_search first, remote fan-out only for what is unanswered; read context_opportunities.json snippets for the benchmark-target number.
- [tool:search_papers_combined] 2026-11-13: one call queries arXiv, Semantic Scholar, OpenAlex, Crossref, DBLP (and CORE with a key, web with include_web) in parallel under per-source rate limits.
- [tool:dftracer_papers_check] 2026-11-13: validate optimization_papers.json or the citation list offline before citing (no network).
