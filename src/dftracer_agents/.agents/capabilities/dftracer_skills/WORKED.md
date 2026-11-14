# What worked: dftracer_skills

## tool:skill_load
`skill_load(name="dftracer-annotate-c", section="CRITICAL")` returns one section and its related_skills; `file="LESSONS_LOG.md"` loads a sibling log.

## tool:memory_recall
`memory_recall(query="<task or error message>", k=5, types="project,feedback")`, then `memory_read` the hits that matter.

## tool:agents_sync
After a confirmed edit to `.agents/agents/*.yaml` or `common-sections.yaml`: `agents_sync()`; status ok, or partial with conflicts to surface.
