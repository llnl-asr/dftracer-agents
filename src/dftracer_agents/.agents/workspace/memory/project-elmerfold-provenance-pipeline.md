---
name: project-elmerfold-provenance-pipeline
description: dftracer provenance mode built (skill/tools/agents) and validated on elmerfold (OpenFold) — 240 entities/181 activities/0 dangling; dftracer-utils Provenance tab on feat/provenance-view
metadata:
  type: project
---

Provenance mode for dftracer: data-centric lineage capture (entity type + entity id + activities with cause/effect edges), no perf telemetry.

State (2026-10-01):
- Skill [[dftracer-provenance]] (+ CASES.md with a confirmed elmerfold case), workload skill [[workload-elmerfold]].
- MCP tools in mcp_tools/tools/session/provenance.py: inventory_outputs, scan_candidates, write_spec, install_helpers, insert, validate, extract_graph (extraction via dftracer-utils dftracer_view).
- Agents: dftracer-provenance-discover / -annotate / -graph; planner has a provenance-mode stage table with a hard user gate on the entity spec.
- Elmerfold run on Tuolumne (10 sequences): 240 entities, 181 activities, 761 edges, 0 dangling; +1% wall time; artifacts byte-identical to the plain run.
- dftracer-utils branch feat/provenance-view (uncommitted, for the user to review/push): trace::provenance::extract_provenance_graph utility, GET /api/prov/graph, Provenance tab in the web UI.
- Design doc for dftracer API enhancements published as an artifact (event types, gaps, proposed API).

**Why:** provenance tracks scientific artifacts and everything leading to / derived from them, at event granularity, to build lineage graphs.
**How to apply:** for a new workload follow dftracer-provenance: plain run -> discover -> ASK the user to choose artifacts/entities -> write spec -> annotate -> traced run with DFTRACER_INC_METADATA=1 -> graph health gate. Open items: run_config preset fallback is in the annotated source but not yet re-run; dftracer near-empty-trace deletion (<5 events) is a known gap.
