---
name: graph-tools-skill-loading
description: Always use graph_query + graph_get_node for skill loading instead of loading full skills
metadata:
  type: feedback
---

## Context
Agents were manually calling `skill_load()` with full skill names, loading entire 5000+ line skill files into context unnecessarily.

## Why
The graph tools (`dftracer_graph_query` + `dftracer_graph_get_node`) provide:
1. **Targeted retrieval** - fetch only the specific sections needed
2. **Context economy** - 50-200 lines vs 5000+ lines per skill
3. **Better search** - semantic matching across all skills at once
4. **Already indexed** - no parsing overhead

## How to apply
**ALWAYS use this pattern when you need skill information:**

```
Step 1 - Query the graph:
  graph_query(mode="docs", question="<your topic>", limit=15)
  → returns ranked list of relevant skill sections with node IDs

Step 2 - Fetch only what you need:
  graph_get_node(node="<id from step 1>", max_lines=50)
  → returns just that section

NEVER:
  skill_load(name="entire-skill-name")  # loads 5000+ lines
```

**Example (correct):**
```
graph_query(mode="docs", question="HDF5 version requirements CMake")
→ finds "agents_skills_software_hdf5_skill_always_use_hdf5_1_14_x..."

graph_get_node(node="agents_skills_software_hdf5_skill_always_use_hdf5_1_14_x...")
→ returns ONLY the 12-line HDF5 version section, not the entire 300-line skill
```

**When to use full skill_load:**
- Only when explicitly executing a complete recipe that requires the full workflow
- When the skill itself says "load the whole skill" in its description
- Never for reference lookups or fact-checking

## Related
See [[dftracer-context-economy]] skill (load that one WITH graph tools too, ironically).
