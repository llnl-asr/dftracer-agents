---
name: dftracer-provenance
description: >
  Provenance mode for dftracer — data-centric lineage capture. Model (entity type /
  entity id / activity with cause→effect edges), the build→run→discover→agree→
  trace-lineage→annotate→build→trace→graph pipeline, the dftracer_prov runtime API
  for C/C++/Python, the provenance_* MCP tools, and how to find the scientific
  artifact. Load for any "capture provenance / lineage" request.
---

# dftracer provenance mode

## Index
- [Scope: data-centric only](#scope)
- [Model](#model)
- [Pipeline](#pipeline)
- [Finding the scientific artifact](#artifact)
- [Choosing entities and ids](#entities)
- [Runtime API](#api)
- [MCP tools](#tools)
- [Run configuration](#run)
- [Graph health gate](#gate)
- [Cases](#cases) → `CASES.md` (grows every session)
- [Lessons](#lessons)
- [Viewing the graph](#viewing)

<a id="scope"></a>
## Scope: data-centric only (MANDATORY)

Provenance mode records **what data exists and how it became what it is**. It does
NOT collect performance telemetry. Do not enable or run:
PAPI, variorum/power, ROC-profiler/CUPTI GPU tracing, the `dftracer_service`
node-counter daemon, HIP/CUDA tracing, utilization sampling. Pipeline Policy rule 12
(node-counter daemon) and the full-feature tracing recipes apply to perf mode, not
here. No optimization loop, analyzer, or diagnoser either.

What IS in scope — every place the app **touches** data:

| verb | examples |
|------|----------|
| access | read file/HDF5/netCDF/DB row, load model, parse input deck |
| store | write output/checkpoint/plot, insert DB row, save model |
| compute | transform entity A into B in memory (solve, fold, train step, filter, reduce) |
| communicate | MPI send/recv/collective, NCCL, host↔device copy, RPC, queue, Ray object |

Function-level POSIX/STDIO/MPI events may stay on (cheap, help locate entities)
but the deliverable is the provenance graph, not timing.

<a id="model"></a>
## Model

- **Entity type** — a class of data/science structure: `protein_structure`, `msa`,
  `mesh_state`, `checkpoint_file`, `model_weights`, `config`.
- **Entity type description** — one or two sentences saying what the type IS in this
  workflow ("Final predicted protein structure for a sequence: the relaxed model with the
  highest mean pLDDT. The primary scientific artifact."). Required in the spec; emitted
  into every trace as `prov_type:<type>` metadata. Users read it to understand the graph,
  and query mapping uses it to resolve "the predicted structures" to the right type.
- **Entity type role** — `input` (comes into the workflow: input files, reference DBs,
  model weights, configs, seeds), `output` (the deliverables: every scientific artifact
  MUST be `output`), or `intermediate` (produced and consumed inside the workflow).
  Required; emitted with the description as `prov_type:<type> = <role>|<description>`.
  It tells users what to extract: "what went in", "what came out", "what happened between".
- **Entity id** — one unique instance of that type: `1abc_model_3`, `step=120`,
  `ckpt/00120.h5`. Identity is `hash = FNV-1a-64(type ␟ id)` — same in every
  language, so a C writer and a Python reader of the same file join.
- **Entity registration** — a dftracer **metadata event**, emitted once per process
  per entity: `key="prov_entity:<hash>"`, `value="type|id|store|uri"`.
- **Activity** — a normal dftracer event (cat `PROV`) spanning the transformation,
  with args `prov_used` (causes) and `prov_generated` (effects) — many-to-many,
  plus `prov_aid`, `prov_activity`, counts. Overflow goes to `PROV_CONT` events.

Rules:
1. **Immutable instances.** If data is updated in place (mesh each step, model
   each epoch), the new state is a NEW id (`step=11`, `epoch=4`). Graph stays a DAG.
2. **Every artifact instance has a producer activity**, and its ancestor walk must
   reach root inputs (files, configs, seeds, external DB records).
3. **Ids must be stable and meaningful** — paths relative to the run dir, step
   numbers, sample keys, content hashes. Never pointers, never timestamps alone.
4. Cross-process edges (MPI, Ray, files) work by giving both sides the SAME
   type+id — e.g. sender `generated("halo","step=5/r2->r3")`, receiver `used(...)` same id.

<a id="pipeline"></a>
## Pipeline (provenance mode)

| # | stage | agent |
|---|-------|-------|
| 0-2 | system detect, session, build original app | same as perf mode |
| 3 | **plain run** of the app (no dftracer) — artifacts land in `<WS>/dataset/<run>/` | `dftracer-tracer` (no tracing) or main thread |
| 4 | **discover**: inventory outputs → name the scientific artifact candidates → scan code for data touch-points → write `provenance/discovery.md` with questions | `dftracer-provenance-discover` |
| 5 | **agree with the user** (main thread, AskUserQuestion): which artifact(s), which entity types upstream/downstream, granularity, exclusions | main thread |
| 6 | write spec (`provenance_write_spec`) — becomes the contract | main thread or annotate agent |
| 7 | install dftracer — data-centric build, no PAPI/variorum/GPU profilers | `dftracer-build-dftracer` |
| 8 | **trace lineage in code + annotate**: from the artifact writer backwards (producers) and forwards (consumers); insert activities; `provenance_validate` must pass | `dftracer-provenance-annotate` |
| 9 | build provenance tree + traced run (`DFTRACER_INC_METADATA=1`) → `<WS>/provenance/traces/` | `dftracer-build-smoke` → `dftracer-tracer` |
| 10 | build graph + health gate; loop to 8 on gaps | `dftracer-provenance-graph` |
| 11-12 | report, privacy guard | same as perf mode |

Source tree for provenance is `<WS>/provenance/source/` (copy of the original,
separate from the perf `annotated/` tree). Spec at `<WS>/provenance/spec.yaml`,
graph at `<WS>/provenance/graph/`.

<a id="artifact"></a>
## Finding the scientific artifact

The artifact is the thing a scientist would cite or analyze: predicted structure,
simulation field/snapshot, trained model + its metrics, variant calls, a catalog,
a figure's underlying table. NOT logs, timing files, scratch, caches.

Procedure:
1. `provenance_inventory_outputs(run_id, "dataset/<run>")` — rank by `hint` and bytes.
2. Read the app's README/docs and output-writing functions (`provenance_scan_candidates`
   kind=write/store, then `graph_query` for callers).
3. Propose 1-3 candidates with evidence (file pattern, writer function, size) and ASK.
   Never pick silently. Rule of thumb confirmed by the user: track all scientific
   artifacts plus everything that leads to them and is derived from them.

<a id="entities"></a>
## Choosing entities and ids

Walk backwards from the artifact writer: each argument/buffer it writes came from
a producer; each producer consumed inputs. Stop at roots (input files, configs,
random seeds, downloaded DB records, model weights). Walk forwards: who reads the
artifact (post-processing, plots, next stage of a workflow).

Describe every type as you name it: what it is scientifically, where it comes from or
goes, and whether it is the artifact, an input, or a transient intermediate. Use the
domain's own words (the ones a user would type in a question), not code identifiers.
Agree the descriptions with the user together with the type list.

Granularity: one entity per meaningful unit the scientist reasons about (a sample,
a timestep, a structure), not per array element. If a loop makes millions, record
the batch/step as the entity and keep members as an id range in the id
(`samples[0:4096]@epoch=3`). Ask the user when granularity is unclear.

<a id="api"></a>
## Runtime API (dftracer entity/relation API)

Provenance is recorded through dftracer's own entity API (C/C++/Python, same ids
everywhere); `provenance_install_helpers` writes a thin provenance-shaped helper
(`dftracer_prov.py` / `dftracer_prov.h`) plus the type registry over it.

| Concept | Representation |
|---|---|
| entity id | `EntityID` = 64-bit FNV-1a(type, key); 16 hex chars in the trace; key never stored |
| type / uri / description | fixed-length (32 / 256 / 256), JSON-safe charset, truncated |
| store | `EntityStore` enum (memory, gpu_memory, local_disk, parallel_fs, burst_buffer, object_store, database, network, other) |
| role | `EntityRole` enum per type (input, output, intermediate, parameter, reference) |
| event relations | `EntityRelation` used / generated / invalidated / updated -> JSON arrays on ANY event |
| entity relations | derived_from, revision_of, contains, part_of, specialization_of, alternate_of, depends_on -> `ER` records |

Records: `EH` (name=id, value=type\|store\|uri), `ET` (name=type, value=role\|description),
`ER` (name=relation, value=subject\|object). Relations are written even without
`DFTRACER_INC_METADATA` (optional args such as the activity type still need it).

Python (`import dftracer_prov as prov`):
```python
with prov.activity("save_data", "store") as a:
    m = a.used("best_structure", seq, "file", path)          # "file" -> store from path
    t = a.generated("structure_tar", seq, prov.Store.PARALLEL_FS, tar)
    a.invalidated("merged_msa", seq, "file", tmp)             # deleted here
prov.relate(prov.Relation.CONTAINS, t, m)                     # entity -> entity
```
C/C++: `DFT_PROV_ACTIVITY(a, "name", "type"); dft_prov_used(&a, type, key, DFT_STORE_*, uri);`
or the core macros `DFTRACER_C/CPP_FUNCTION_USES/GENERATES`, `DFTRACER_*_ENTITY_RELATE`.

<a id="tools"></a>
## MCP tools

`provenance_inventory_outputs`, `provenance_scan_candidates` (access/store/
communicate/compute touch-points), `provenance_write_spec`, `provenance_install_helpers`,
`provenance_insert` (idempotent, `dft-prov` marker), `provenance_validate` (static
gate vs spec), `provenance_extract_graph` (traces → `graph.json`/`graph.dot` + lineage).
Tool broken → fix it in `mcp_tools/tools/session/provenance.py`, ask for MCP restart.

<a id="run"></a>
## Run configuration

```
DFTRACER_ENABLE=1  DFTRACER_INC_METADATA=1   # REQUIRED: args dropped otherwise
DFTRACER_LOG_FILE=<WS>/provenance/traces/raw/<app>
```
No PAPI/variorum/GPU-profiler env, no `session_service_start`. App data still goes
to `<WS>/dataset/<run>/` (PFS); traces stay in the workspace.

<a id="gate"></a>
## Graph health gate (done = all true)

`provenance_extract_graph` → `healthy: true`: `prov_events > 0`, `dangling_hashes == 0`,
`types_undescribed == []`,
no `artifact_types_missing`, `artifacts_without_producer == 0`, no truncation; and
every artifact's `roots` are real inputs agreed in the spec. Report entity/activity
counts by type and one full lineage example to the user.

<a id="cases"></a>
## Cases

`CASES.md` holds per-domain patterns (what the artifact was, entity types, id
schemes, traps). Read the matching case first; append a new one at session end
(after user confirmation, anonymized).

<a id="viewing"></a>
## Viewing the graph

- **Extraction** goes through dftracer-utils, never a hand-rolled gzip scan:
  `provenance_extract_graph` runs `dftracer_view --query 'cat == "PROV" or cat == "PROV_CONT"'`
  (indexed, chunk-skipping). The viewer ALWAYS passes metadata records through, which is
  where `prov_entity:*` lives, so one query returns entities and activities.
  Measured: 29 files / 718k events -> 930 rows in 1.6 s.
- **dftracer_server Provenance tab** (dftracer-utils `feat/provenance-view`):
  `GET /api/prov/graph` runs `trace::provenance::extract_provenance_graph` server-side;
  the tab shows entities colored by type, a lineage/impact tracer (type -> multi-select ids).
  `dftracer_server -d <WS>/provenance/traces/raw --index-dir <WS>/provenance/server_index -p <port>`.
- **Standalone page**: `render_prov_graph.py graph.json out.html --spec spec.yaml`
  (Cytoscape + dagre). Redact `$LUSTRE_ROOT/$USER` paths in `uri` before sharing.

<a id="lessons"></a>
## Lessons

- **First declaration of an entity in a process wins.** Its store and uri come from the
  first `used`/`generated`/`entity` call; later calls only add edges. Declare with the
  full store/uri the first time (or use `prov.entity` up front).
- **Use the relation that says what happened:** deletes are `invalidated`, in-place
  modification is `updated`; containment/versioning are entity relations (`contains`,
  `revision_of`), not extra activities.
- **dftracer's JSON serializer used to drop ALL event args without INC_METADATA**;
  the entity API keeps relation args regardless (serializer fix on the API branch).

- **dftracer deletes near-empty traces.** `STDIOWriter::finalize` unlinks the trace when the
  process logged fewer than 5 data events, counting dftracer's own `start`/`end`. A worker
  that ran one or two activities loses its edges -> shows up as `dangling_hashes`.
  A one-activity smoke check writes NO file; log >=3 activities when testing.
- **Python process pools and spawned servers skip `atexit`** (children leave via `os._exit`).
  Register finalize with both `atexit.register(fini)` and
  `multiprocessing.util.Finalize(None, fini, exitpriority=100)`; init once per pid in the
  pool initializer (`executor_worker_init`-style) and the server's startup.
- **Wrapper pattern for task functions:** rename `f` to `_f_impl` and keep a same-named
  wrapper that opens the activity. Pickled-by-name pool tasks keep working, diffs stay small.
- **Cross-process RPC edges without editing the framework:** subclass the task type
  (`ServerTask.start`, `SubprocessTask.on_finish`) to record the driver side; record the
  server side in the request handler with the identical type+id.
- **Optional config inputs are still entities.** When a config file is optional and absent,
  identify the config by what actually configured the run (e.g. `preset:<name>`); otherwise
  the root silently disappears from lineage.
- **Verify the run did not change the science:** diff artifact members and a metrics file
  between the plain run and the provenance run (elmerfold: byte-identical, +1% wall time).
- `provenance_install_helpers(include_into=...)` must insert after a whole multi-line import
  (AST-based now); `provenance_validate` skips the helper files' own docstring examples.
