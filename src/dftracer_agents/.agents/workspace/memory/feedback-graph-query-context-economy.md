---
name: feedback-graph-query-context-economy
description: MANDATORY: use graph_query mode=docs + graph_get_node for skill reference lookups instead of full skill_load — saves 95%+ context
metadata:
  type: feedback
---

## Why this matters

Loading full skills (5000+ lines) for single-fact lookups wastes the context budget 
and is the dominant cause of token exhaustion in complex sessions.

**The problem:**
Agents reflexively call `skill_load(name="dftracer-ml-annotate")` to answer "what 
comp type for checkpoint save?" — loading 5247 lines to extract 1 fact.

**How to apply:**

1. **Reference lookup** (95% of cases) → TWO-STEP PATTERN:
   ```
   # Step 1: locate (returns node IDs, ~100 tokens)
   graph_query(mode="docs", question="checkpoint comp type", limit=5)
   
   # Step 2: fetch only what's needed (hundreds of lines, not thousands)
   graph_get_node(node="<id-from-step-1>")
   ```

2. **First-time task** (never seen before, need full orientation) → `skill_load` 
   (use sparingly)

3. **Known section** → `skill_load(name="...", section="<heading>")` (middle ground)

**Measured savings:**
- Full skill_load: 5247 lines
- graph_query (locate): 98 tokens
- graph_get_node (fetch section): 134 lines
- **Savings: 97.4%**

**Skills this applies to:**
ALL large skills — dftracer-ml-annotate, dftracer-annotate-{c,cpp,python}, 
dftracer-pipeline, workload-*, software-*, system-* (anything >1000 lines).

**When this was learned:**
2026-08-06 session where repeated full skill loads crowded out actual annotation work.

**Related:**
[[dftracer-context-economy]] SKILL.md updated with this pattern (Rule 5 + new section)
