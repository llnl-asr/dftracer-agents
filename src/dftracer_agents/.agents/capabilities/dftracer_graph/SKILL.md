---
name: dftracer_graph
description: Context/token economy for every dftracer agent. Use the graph tools (graph_query / graph_ensure / graph_get_node) to LOCATE code and docs and compute a change's blast radius instead of reading whole files or loading whole skills into context. Load before any tool call, annotation, optimization or pipeline step that would otherwise open source files or a large skill, or when graph_query says 'No unique node match', 'no document node matches', 'stale graph', or 'graphify CLI is not installed'.
---
Cross-references: [[dftracer_skills]], [[dftracer_profiling]], [[knowledge]]

# Context economy: locate, don't read

The dominant token cost in this pipeline is **input**: source an agent reads to orient itself. `graphify` (dependency
`graphifyy`) builds a tree-sitter graph over C/C++/Fortran/Python **plus markdown headings**, so skills and agent
definitions are queryable too. Two graphs: the **knowledge graph** (this package's code, skills, agent definitions,
memory) and a **session graph** (the target application, built inside that session's workspace).

Measured on this repo:

| Approach | ~tokens |
| --- | --- |
| Read the 3 relevant source files whole | **29,456** |
| `graph_query(question=..., budget=1200)` | **986** (3.3%) |
| `graph_query(mode="explain", symbol=...)` | **208** |
| `graph_query(mode="affected", symbol=...)` | **212** |

## Rules
Use the MCP tools, not the CLI: they guarantee freshness before answering (graphify's own MCP server registers ~25
schemas that would sit in context permanently on top of this project's ~137 tools).
```
graph_query(question="python annotation cost gate", budget=1200)   # locate -> NODE <sym> [src=file loc=Lnn]
graph_query(mode="explain",  symbol="_python_annotate_file_impl")  # definition + callers/callees
graph_query(mode="affected", symbol="recommend", depth=2)          # blast radius
graph_query(mode="docs", question="<topic>")                       # skill/agent sections + ids
graph_get_node(node="<id>")                                        # just that section or definition
graph_ensure(force=True)                                           # manual refresh
graph_ensure(run_id=RUN_ID); graph_query(question="checkpoint save", run_id=RUN_ID)   # the target application
```
1. **Locate before you read.** Never `grep`/`Read` a tree to find where something lives. Ask the graph, then open only
   the `file:line` it names. Paths returned are absolute and openable.
2. **Before editing any shared function, run `mode="affected"`** and state the blast radius (that is how you learn
   that changing `recommend()` also moves `_estimate_file_impl` -> `_validate_python` -> `_plan`).
3. **Freshness is automatic.** The graph rebuilds only when the skills/agents/code content hash changes, or a writer
   marked it dirty (~4-5 s, no LLM, no API key); an unchanged graph costs ~0.1 s to validate. Self-learning skill edits
   and `memory_write` invalidate it.
4. **Budget queries** (`budget=1200`). BFS depth 2 pulls in generic nodes (`_ok`, `json`); ignore them rather than widening.
5. **NEVER load full skills for reference lookups.** `graph_query(mode="docs", question=...)` finds the section, then
   `graph_get_node(node=<id>)` fetches ONLY it (95%+ saving vs `skill_load`).

### Documentation/skill lookup pattern (MANDATORY)
- Anti-pattern: `skill_load(name="dftracer-ml-annotate")` loads 5000+ lines for one fact.
- Correct: `graph_query(mode="docs", question="how to annotate training loops", limit=5)` returns
  `NODE <id> [src=.agents/skills/dftracer-ml-annotate/SKILL.md loc=L142]`; then `graph_get_node(node=<id>)` returns
  just that `## Step 4a` section.
- When: reference lookup (95%+ of cases) -> docs + get_node; first-time task needing the full context -> `skill_load`
  (sparingly); known section -> `skill_load(name, section="<heading>")`.
- Example: "the ML comp type for checkpoint save": `skill_load` = 5247 lines; `graph_query(mode="docs")` = 98 tokens;
  `graph_get_node` = 134 lines. Large skills (dftracer-ml-annotate, dftracer-annotate-c, workload-*/software-*) routinely
  exceed 3000-5000 lines.

## Constraints
- The graph is **structural**: it finds where code is and how it connects, not what it means; for semantics open the `file:line`.
- Build artefacts go to the writable cache `<workspaces_root>/_graph/` (a staged copy), never into site-packages.
- Do not hand-run `graphify update` on a gitignored path and expect nodes.

## Corner cases
- **Symbols are class-qualified**: a method is `.save_checkpoint()`, so `mode="affected"` on a bare method name can
  report "No unique node match": use `mode="query"` with a phrase, or the qualified name.
- `graphify` writes to `<scanned_path>/graphify-out` (no `--out` flag): the tool scans a staged copy under
  `<workspaces_root>/_graph/`, never the installed package.
- `graphify` does not follow symlinked directories ("No code files found"): the stage holds real files.
- A markdown-only tree yields no graph: markdown is only extracted when code files are present in the same tree, so the
  stage carries both.
- `graphify` honours `.gitignore`; the cache under `workspaces/` is gitignored, so a `.graphifyignore` at the scan root
  re-includes it (`_ensure_graphifyignore`).
- `mode="docs"` searches only markdown nodes: BFS `query` leaks into the code subgraph and buries headings under helpers like `_ok()`.
- Upstream advertises 70x; measured here ~30x on a realistic locate-then-read query.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_graph_check` failures (pass the `graph` path graph_ensure returned):
- `parse` / `nodes` / `ids` -> `graph_ensure(force=True)` (or with `run_id`); a tree with no code yields no nodes.
- `docs` -> skills/agents were not staged next to code or were ignored: rebuild with force; check `.graphifyignore`.
- `sources` / `spans` -> stale graph (files moved or edited after the build): `graph_ensure(force=True)`.
- `graph_present` (inconclusive) -> run `graph_ensure` first.
`graph_query` errors: "the graphify CLI is not installed" -> install dftracer-agents' dependency `graphifyy`;
"graphify timed out" -> narrow the question or lower the budget.

## Self-heal
- Tool wrong: fix `mcp_tools/tools/session/code_graph.py` in dftracer-agents (or `capability_update` this capability)
  and restart the MCP server.

## Self-learn
- What is explored here becomes a capability: a graphify limitation found on a new language or tree becomes a lesson
  here, a target application's structure goes into its `workload_<app>`; see capability_builder.

## What works
- Locate with `graph_query(mode="docs")`, fetch with `graph_get_node(node=<id>)`; measured 986 tokens vs 29,456 for
  reading three files (3.3%); `explain`/`affected` ~210 tokens each.
