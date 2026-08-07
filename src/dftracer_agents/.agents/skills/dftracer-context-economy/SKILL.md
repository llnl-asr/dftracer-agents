---
name: dftracer-context-economy
description: >
  Context/token economy for every dftracer agent. Use the graph tools
  (graph_query / graph_ensure) to LOCATE code and compute change blast-radius
  instead of reading whole files into context. Load this before any tool call,
  annotation, optimization, or pipeline step that would otherwise open source files.
priority: critical
---

# Context economy — locate, don't read

The dominant token cost in this pipeline is **input**: source an agent reads to
orient itself. `graphify` (dependency `graphifyy`) builds a tree-sitter graph
over C/C++/Fortran/Python **plus markdown headings**, so skills and agent
definitions are queryable too.

Measured on this repo:

| Approach | ~tokens |
| --- | --- |
| Read the 3 relevant source files whole | **29,456** |
| `graph_query(question=..., budget=1200)` | **986** (3.3%) |
| `graph_query(mode="explain", symbol=...)` | **208** |
| `graph_query(mode="affected", symbol=...)` | **212** |

## Use the MCP tools, not the CLI

Two tools, deliberately (graphify's own MCP server registers ~25 schemas; this
project already exposes 137). They guarantee freshness before answering:

```
graph_query(question="python annotation cost gate", budget=1200)   # locate
graph_query(mode="explain",  symbol="_python_annotate_file_impl")  # defn + callers
graph_query(mode="affected", symbol="recommend", depth=2)          # blast radius
graph_ensure(force=True)                                           # manual refresh
```

For the **target application** in a session, pass `run_id`:

```
graph_ensure(run_id=RUN_ID)                       # build the app's graph once
graph_query(question="checkpoint save", run_id=RUN_ID)
```

The CLI (`graphify query|explain|affected`) still works and is a fine fallback,
but it does **not** check freshness — the MCP tools do.

## Rules

1. **Locate before you read.** Never `grep`/`Read` a tree to find where something
   lives. Ask the graph, then open only the `file:line` it names. Paths returned
   are absolute and openable.
2. **Before editing any shared function, run `mode="affected"`** and state the
   blast radius. It is how you learn that changing `recommend()` also moves
   `_estimate_file_impl` -> `_validate_python` -> `_plan`. Skipping it is how a
   "local" fix silently breaks a caller.
3. **Freshness is automatic.** The graph rebuilds only when the skills/agents/code
   content hash changes, or a writer marked it dirty (~5 s). An unchanged graph
   costs ~0.1 s to validate. Self-learning skill edits therefore *do* invalidate it.
4. **Budget queries** (`budget=1200`). BFS depth 2 pulls in generic nodes (`_ok`,
   `json`); ignore them rather than widening.
5. **NEVER load full skills for reference lookups.** Use `graph_query(mode="docs", 
   question=...)` to find relevant sections, then `graph_get_node(node=<id>)` to 
   fetch ONLY what's needed. This saves 95%+ context vs `skill_load`.

## Documentation/skill lookup pattern (MANDATORY)

**The anti-pattern (wasteful):**
```
skill_load(name="dftracer-ml-annotate")  # loads 5000+ lines for 1 fact
```

**The correct pattern (95% savings):**
```
# Step 1: Find relevant sections (cheap — returns node IDs + brief context)
graph_query(mode="docs", question="how to annotate training loops", limit=5)
# Returns: NODE <id> [src=.agents/skills/dftracer-ml-annotate/SKILL.md loc=L142]

# Step 2: Fetch ONLY the needed section (hundreds of lines vs thousands)
graph_get_node(node="<id-from-step-1>")
# Returns: just the ## Step 4a section text, not the entire 5000-line skill
```

**When to use each:**
- **Reference lookup** (specific fact/rule/example) → `graph_query(mode="docs")` + 
  `graph_get_node` (this is 95%+ of cases)
- **First-time task** (never seen this task before, need full context) → `skill_load`
  (use sparingly)
- **Section-only load** → `skill_load(name="...", section="<heading>")` when you 
  know the exact section name (middle ground)

**Real example:**
Agent needs to know "what's the ML-specific comp type for checkpoint save":
- ❌ `skill_load("dftracer-ml-annotate")` → 5247 lines loaded
- ✅ `graph_query(mode="docs", question="checkpoint comp type")` → 98 tokens, 
  returns node ID
- ✅ `graph_get_node(node=<id>)` → 134 lines of the relevant section

**Why this matters:**
Large skills (dftracer-ml-annotate, dftracer-annotate-c, workload-*/software-*) 
routinely exceed 3000-5000 lines. Loading one whole for a single lookup wastes 
the majority of your context budget and crowds out the actual work.

## Honest limits

- The graph is **structural**. It finds *where* code is and *how* it connects; it
  does not read semantics. For "what does this do", open the `file:line`.
- **Symbols are class-qualified.** A method appears as `.save_checkpoint()`, so
  `mode="affected"` on a bare method name can report "No unique node match". Use
  `mode="query"` with a phrase, or the qualified name.
- `graphify` honours `.gitignore`, does not follow symlinked dirs, and needs code
  files present for markdown to be extracted. The graph tool works around all
  three; do not hand-run `graphify update` on a gitignored path and expect nodes.
- Upstream advertises 70x; what we actually measured here is ~30x on a realistic
  locate-then-read query.
