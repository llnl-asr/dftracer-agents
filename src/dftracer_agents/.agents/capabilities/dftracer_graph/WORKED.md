# What worked: dftracer_graph

## tool:graph_query
`graph_query(question="<what>", budget=1200)` locates code (~986 tokens vs ~29,456 reading three files); `mode="docs"` for skill/agent sections, then `graph_get_node(node=<id>)`; `mode="explain"`/`"affected"` ~210 tokens each.

## tool:graph_ensure
Knowledge graph: `graph_ensure()` (rebuilds only on a content-hash change or dirty flag, ~4 s, no LLM); application: `graph_ensure(run_id=RUN_ID)` once per session, then `graph_query(..., run_id=RUN_ID)`.
