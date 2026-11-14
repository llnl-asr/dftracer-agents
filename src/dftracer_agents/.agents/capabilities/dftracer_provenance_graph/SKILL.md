---
name: dftracer_provenance_graph
description: dftracer provenance mode, graph stage. Build the provenance graph from the provenance traces (dftracer_view, indexed), apply the health gate (events present, no dangling hashes, types described, every artifact produced and rooted in a spec input), map gaps to annotation fixes, view the graph, and package + verify the reproduction in isolation. Load after the provenance traced run or when the graph is unhealthy.
---
Cross-references: [[dftracer_provenance_annotate]], [[dftracer_provenance_discover]], [[dftracer_isolation]], [[prov_graph]], [[prov_diagram]], [[repro_package]], [[dftracer_report]], [[dftracer_privacy]]

# dftracer provenance mode: graph and reproduction

Related moirai-agents capabilities: `prov_graph` (moirai's graph build/compare), `prov_diagram`, `repro_package`.

## Rules
- `provenance_extract_graph(run_id)` (trace_dir defaults to `provenance/traces`, out_dir to `provenance/graph`).
- **Graph health gate (done = all true)**: `healthy: true` from the tool and `dftracer_provenance_graph_check` pass:
  `prov_events > 0`, `dangling_hashes == 0`, `types_undescribed == []`, no `artifact_types_missing`,
  `artifacts_without_producer == 0`, no truncation; and every artifact's `roots` are real inputs agreed in the spec.
  Report entity/activity counts by type and one full lineage example to the user.
- Write `provenance/graph/SUMMARY.md`: counts by type, one full lineage chain for an artifact (root -> ... -> artifact
  -> consumers), gaps and fixes. Return healthy/unhealthy + the exact fix list for the annotate stage.
- **Verify the run did not change the science**: diff artifact members and a metrics file between the plain run and the
  provenance run.
- **Extraction goes through dftracer-utils, never a hand-rolled gzip scan**: `provenance_extract_graph` runs
  `dftracer_view --query 'cat == "PROV" or cat == "PROV_CONT"'` (indexed, chunk-skipping), with `or cat == "dftracer"`
  and `--phase any` on newer viewers so entity records come through. Measured: 29 files / 718k events -> 930 rows in 1.6 s.
  Pass `viewer_bin=<venv>/bin/dftracer_view` if it is not on PATH.
- **Reproduction package + isolated-run verification (MANDATORY)**: `<WS>/final_provenance/`: `config.ini` (only place
  for site values; `${USER}`, never a literal name), `scripts/lib_load_config.sh` (sourced by every script; env
  overrides config), numbered steps `00_check_prereqs ... 08_view`, `run_all.sh`, the instrumentation as
  `patches/<app>_provenance.patch` against the pinned app commit, `inputs/spec.yaml`, and a **standalone** checker
  (`prov_check.py`: reads EH/ET/ER + `args.relations`, stdlib+PyYAML only, no dftracer-agents import) that writes
  `run.json`/`README.md` and fails on dangling ids, undescribed types or ungenerated artifact types. Pin
  dftracer/pydftracer/utils by commit.
- Verify, don't assert: `session_verify_isolation` `static` until clean -> `launch` (copy to a new PFS dir outside
  <WS>, `env -i ... bash -l`, fresh OUTPUT_ROOT) -> poll `status`. Pass = exit 0 and the checker reproduces the
  session's entity/activity counts with 0 dangling. Report any fix the isolated run forced into the package.

### Viewing the graph
- **dftracer_server Provenance tab** (dftracer-utils `feat/provenance-view`): `GET /api/prov/graph` runs
  `trace::provenance::extract_provenance_graph` server-side; the tab shows entities colored by type and a lineage/impact
  tracer (type -> multi-select ids). `dftracer_server -d <WS>/provenance/traces/raw --index-dir <WS>/provenance/server_index -p <port>`.
- **Standalone page**: `render_prov_graph.py graph.json out.html --spec spec.yaml` (Cytoscape + dagre). Redact
  `$LUSTRE_ROOT/$USER` paths in `uri` before sharing.

## Constraints
- Data-centric only: no performance telemetry, analyzer or optimization loop in provenance mode.
- Never declare the graph healthy without the check's own verdict; never report a reproduction that was not run.
- Traces stay in the workspace; app data stays on the PFS `dataset/`.
- Never read traces by decompressing them yourself: the viewer path, or the tool's own fallback scan when the viewer is absent.

## Corner cases
- Newer viewers return activities and edges but zero entities (every hash dangles) with a PROV-only query: EH/ET/ER
  records carry cat "dftracer" and are not ph=M; the tool asks for them with `--phase any`, falling back to PROV only
  for older viewers.
- dftracer deletes near-empty traces: `STDIOWriter::finalize` unlinks the trace when the process logged fewer than 5
  data events (counting start/end). A worker that ran one or two activities loses its edges -> `dangling_hashes`.
- Lmod's `init/bash` and `module` are not `set -u` clean (`FPATH: unbound variable`): wrap them
  (`set +u; module ...; set -u`) in the loader.
- A non-login `env -i` shell has no MODULEPATH; verify from `bash -l`, which is what a new user gets.
- Pin repos by FULL 40-char SHA: feature branches get merged and deleted; GitHub/GitLab still serve the commit by full
  SHA but refuse a short-SHA fetch, and `git checkout --detach <unknown>` then fails as "does not take a path argument".
- When publishing to a shared group dir, `chmod g+rX` every parent you create.
- `pip uninstall dftracer` leaves the cmake/brahma tree; remove `<site-packages>/dftracer` before reinstalling.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_provenance_graph_check` failures and their repair (then re-trace and predict/validate again):
- `prov_events` 0 -> the run lacked `DFTRACER_INC_METADATA=1`, dftracer was not initialized
  (`provenance_ensure_lifecycle`), or the activities never ran.
- `dangling` -> a process's trace is missing (fewer than 5 events, or an `os._exit` child without finalize), or
  metadata disabled; with a viewer, check the entity query (`--phase any`).
- `types_described` -> the type registry was not installed or is stale; re-run `provenance_install_helpers`
  (descriptions come from the spec).
- `artifacts_present` / `artifacts_produced` -> annotation gap at the writer: `generated(<artifact>)` after the write.
- `not_truncated` -> list chunks lost (PROV_CONT): check for missing events or overly long entity lists.
- `lineage` -> no artifact entity reached the graph: pass `artifact_types` or fix `scientific_artifacts`.
- `roots_are_inputs` -> a producer upstream is unannotated: annotate it (dftracer_provenance_annotate), or agree with
  the user that the type is an input and update the spec.
- `graph_present` (inconclusive) -> run `provenance_extract_graph` first.
The same failure twice stops the loop: ask the user.

## Self-heal
- `dftracer_view failed` -> read `view.stderr`; pass `viewer_bin`, or `use_viewer=False` for the direct fallback scan.
- Tool broken: fix `mcp_tools/tools/session/provenance.py` (dftracer-agents) or `capability_update` this capability,
  and ask for an MCP restart.

## Self-learn
- What is explored here becomes a capability: a new site's packaging or isolation trap becomes a lesson here or a
  `system_<machine>` capability; a workload's verified graph becomes part of its `workload_<app>`; see capability_builder.
- Propose updates through the confirmation gate; redact before persisting.

## What works
- 2026-10-01, elmerfold: healthy graph, 240 entities / 181 activities, 0 dangling; science unchanged (byte-identical
  artifact members and metrics, +1% wall time). See WORKED.md for the package and isolation recipe.
