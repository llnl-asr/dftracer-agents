---
name: dftracer_provenance_discover
description: dftracer provenance mode (data-centric lineage capture), discovery and agreement stage. The model (entity type / entity id / activity with cause->effect edges), the build->run->discover->agree->trace-lineage->annotate->build->trace->graph pipeline, how to find the scientific artifact and choose entities and ids, and the spec contract. Load for any "capture provenance / lineage" request, before annotating, or when provenance_write_spec rejects a spec.
---
Cross-references: [[dftracer_provenance_annotate]], [[dftracer_provenance_graph]], [[prov_discover]], [[prov_annotate]], [[prov_graph]], [[dftracer_graph]], [[dftracer_profiling]], [[dftracer_privacy]]

# dftracer provenance mode: discover and agree

Related moirai-agents capabilities (moirai provenance, a different runtime): `prov_discover`, `prov_annotate`, `prov_graph`.
This capability is dftracer's own provenance mode (the `dftracer_prov` helper over dftracer's entity API).

## Rules

### Scope: data-centric only (MANDATORY)
Provenance mode records **what data exists and how it became what it is**. It does NOT collect performance
telemetry. Do not enable or run: PAPI, variorum/power, ROC-profiler/CUPTI GPU tracing, the `dftracer_service`
node-counter daemon, HIP/CUDA tracing, utilization sampling. Pipeline Policy rule 12 (node-counter daemon) and the
full-feature tracing recipes apply to perf mode, not here. No optimization loop, analyzer, or diagnoser either.

What IS in scope, every place the app **touches** data:

| verb | examples |
|------|----------|
| access | read file/HDF5/netCDF/DB row, load model, parse input deck |
| store | write output/checkpoint/plot, insert DB row, save model |
| compute | transform entity A into B in memory (solve, fold, train step, filter, reduce) |
| communicate | MPI send/recv/collective, NCCL, host<->device copy, RPC, queue, Ray object |

Function-level POSIX/STDIO/MPI events may stay on (cheap, help locate entities) but the deliverable is the provenance
graph, not timing.

### Model
- **Entity type**: a class of data/science structure: `protein_structure`, `msa`, `mesh_state`, `checkpoint_file`,
  `model_weights`, `config`.
- **Entity type description**: one or two sentences saying what the type IS in this workflow ("Final predicted
  protein structure for a sequence: the relaxed model with the highest mean pLDDT. The primary scientific artifact.").
  Required in the spec; emitted into every trace as `prov_type:<type>` metadata. Users read it to understand the
  graph, and query mapping uses it to resolve "the predicted structures" to the right type.
- **Entity type role**: `input` (comes into the workflow: input files, reference DBs, model weights, configs, seeds),
  `output` (the deliverables: every scientific artifact MUST be `output`), or `intermediate` (produced and consumed
  inside the workflow). Required; emitted with the description as `prov_type:<type> = <role>|<description>`. It tells
  users what to extract: "what went in", "what came out", "what happened between".
- **Entity id**: one unique instance of that type: `1abc_model_3`, `step=120`, `ckpt/00120.h5`. Identity is
  `hash = FNV-1a-64(type, id)`, the same in every language, so a C writer and a Python reader of the same file join.
- **Entity registration**: a dftracer **metadata event**, emitted once per process per entity:
  `key="prov_entity:<hash>"`, `value="type|id|store|uri"`.
- **Activity**: a normal dftracer event (cat `PROV`) spanning the transformation, with args `prov_used` (causes) and
  `prov_generated` (effects), many-to-many, plus `prov_aid`, `prov_activity`, counts. Overflow goes to `PROV_CONT` events.

Model rules:
1. **Immutable instances.** If data is updated in place (mesh each step, model each epoch), the new state is a NEW id
   (`step=11`, `epoch=4`). The graph stays a DAG.
2. **Every artifact instance has a producer activity**, and its ancestor walk must reach root inputs (files,
   configs, seeds, external DB records).
3. **Ids must be stable and meaningful**: paths relative to the run dir, step numbers, sample keys, content hashes.
   Never pointers, never timestamps alone.
4. Cross-process edges (MPI, Ray, files) work by giving both sides the SAME type+id, e.g. sender
   `generated("halo","step=5/r2->r3")`, receiver `used(...)` same id.

### Pipeline (provenance mode)

| # | stage | agent / capability |
|---|-------|-------|
| 0-2 | system detect, session, build original app | same as perf mode |
| 3 | **plain run** of the app (no dftracer); artifacts land in `<WS>/dataset/<run>/` | tracer stage (no tracing) or main thread |
| 4 | **discover**: inventory outputs, name the scientific artifact candidates, scan code for data touch-points, write `provenance/discovery.md` with questions | this capability |
| 5 | **agree with the user** (main thread, AskUserQuestion): which artifact(s), which entity types upstream/downstream, granularity, exclusions | main thread |
| 6 | write spec (`provenance_write_spec`), which becomes the contract; `dftracer_provenance_discover_check` must pass | main thread or annotate agent |
| 7 | install dftracer: data-centric build, no PAPI/variorum/GPU profilers | build-dftracer stage |
| 8 | **trace lineage in code + annotate**: from the artifact writer backwards (producers) and forwards (consumers); insert activities; `provenance_validate` must pass | [[dftracer_provenance_annotate]] |
| 9 | build provenance tree + traced run (`DFTRACER_INC_METADATA=1`) into `<WS>/provenance/traces/` | build-smoke then tracer stage |
| 10 | build graph + health gate; loop to 8 on gaps | [[dftracer_provenance_graph]] |
| 11 | reproduction package `final_provenance/` + **isolated-run verification** (`session_verify_isolation`) | [[dftracer_provenance_graph]] |
| 12-13 | report, privacy guard | [[dftracer_report]], [[dftracer_privacy]] |

The source tree for provenance is `<WS>/provenance/source/` (copy of the original, separate from the perf
`annotated/` tree). Spec at `<WS>/provenance/spec.yaml`, graph at `<WS>/provenance/graph/`.

### Finding the scientific artifact
The artifact is the thing a scientist would cite or analyze: predicted structure, simulation field/snapshot, trained
model + its metrics, variant calls, a catalog, a figure's underlying table. NOT logs, timing files, scratch, caches.

Procedure:
1. `provenance_inventory_outputs(run_id, "dataset/<run>")`: rank by `hint` and bytes.
2. Read the app's README/docs and output-writing functions (`provenance_scan_candidates` kind=write/store, then
   `graph_query` for callers).
3. Propose 1-3 candidates with evidence (file pattern, writer function, size) and ASK. Never pick silently. Rule of
   thumb confirmed by the user: track all scientific artifacts plus everything that leads to them and is derived from them.

### Choosing entities and ids
Walk backwards from the artifact writer: each argument/buffer it writes came from a producer; each producer consumed
inputs. Stop at roots (input files, configs, random seeds, downloaded DB records, model weights). Walk forwards: who
reads the artifact (post-processing, plots, next stage of a workflow).

Describe every type as you name it: what it is scientifically, where it comes from or goes, and whether it is the
artifact, an input, or a transient intermediate. Use the domain's own words (the ones a user would type in a
question), not code identifiers. Agree the descriptions with the user together with the type list.

Granularity: one entity per meaningful unit the scientist reasons about (a sample, a timestep, a structure), not per
array element. If a loop makes millions, record the batch/step as the entity and keep members as an id range in the
id (`samples[0:4096]@epoch=3`). Ask the user when granularity is unclear.

### Discovery output
For each artifact candidate, `provenance/discovery.md` holds: evidence (file pattern, writer function, bytes),
upstream chain (producers to roots), downstream consumers, proposed entity types with id schemes, description and role,
proposed activities (where = file:function, used -> generated), granularity concerns, and ends with
`## Questions for the user`.

## Constraints
- Never decide the entity set alone: the user approves the artifact(s), the types, their descriptions and granularity.
- The discover agent never writes the spec; the main thread does after the user answers.
- Spec schema (`provenance_write_spec`): `app`, `scientific_artifacts`, `entity_types` (name, kind, store from
  file|memory|db|object|network|gpu|other, id_scheme, role, description, where), `activities` (name, activity_type
  optional, where, used, generated), `decisions` (what the user approved/excluded).
- `force=True` on `provenance_write_spec` writes a spec that fails validation: only with the user's explicit agreement,
  and the annotate stage still gates on the same rules.
- Locate, do not read: `graph_query` and the scan's `file:line`, then open only those lines.

## Corner cases
- Per-step entities explode at fine cadence (simulation): record only steps that are written or consumed downstream.
- DDP allreduce is a communicate edge between ranks (ML training): model ids must include the epoch, not the rank.
- Optional config inputs are still entities: when the config file is optional and absent, identify it by what actually
  configured the run (e.g. `preset:<name>`), otherwise the root silently disappears from lineage.
- `dataset/` is a symlink onto the PFS: `provenance_inventory_outputs` follows it at the top on purpose.
- Pass the run's start time as `newer_than_epoch` to isolate one run's outputs when several runs share `dataset/`.
- Three process kinds (driver, pool, server) in one workflow each need their own init/finalize (see the elmerfold case).

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_provenance_discover_check` names the failed check:
- `yaml`: fix indentation or quoting of the spec text; validate again.
- `artifacts`: name the artifact type(s) the user agreed on in `scientific_artifacts`, declare each in `entity_types`
  with `role: output`, and make some activity `generated` it.
- `entity_types`: give every type a unique name and a known store.
- `id_scheme`: write how a stable unique id is built (relative path, `step=N`, `seq_id`), never a pointer or a timestamp.
- `role`: tag input | output | intermediate.
- `description`: write one or two sentences in domain words (>= 20 characters).
- `activities`: add `where` (file:function) and declared used/generated types.
- `orphans`: connect the type to an activity, or drop it with the user's agreement.
- `spec_present` (inconclusive): no spec yet for that run: finish the user agreement and `provenance_write_spec`.
Then re-run `provenance_write_spec` and the check. The same failure twice stops the loop: ask the user.

## Self-heal
- A tool is broken: fix it in `mcp_tools/tools/session/provenance.py` of dftracer-agents (or `capability_update`
  this capability) and ask for an MCP restart; never fall back to hand work when a restart is what is needed.
- A spec problem the validator does not catch but the graph stage finds later: add the rule to `validate_spec` and a
  check here.

## Self-learn
- What is explored here becomes a capability: a new workload's verified case becomes (or extends) its `workload_<app>`
  capability, and its domain pattern goes into What works below; see capability_builder.
- Append a new case at session end (after user confirmation, anonymized). Propose updates to the main thread
  (confirmation gate); redact before persisting ([[dftracer_privacy]]).

## What works
Cases, one entry per workload family (`artifact`, `entity types`, `activities`, `traps`, `confirmed`). Seed patterns
are starting hypotheses, not verified sessions: confirm with the user and replace with a verified entry.

- Simulation (mesh/particle codes), seed: artifact = field/snapshot files, checkpoints, derived diagnostics; types
  config (input deck path), mesh_state (step=N), checkpoint_file (rel path), diagnostic (name@step); activities
  read_config: config -> mesh_state(step=0); step: mesh_state(N) -> mesh_state(N+1); write_ckpt: mesh_state -> checkpoint_file.
- ML training, seed: artifact = trained model weights + eval metrics; types dataset (name/split), batch (epoch/step
  range), model_weights (epoch=N), checkpoint_file, metrics (epoch=N); activities load_data, train_epoch:
  model(N-1)+dataset -> model(N), evaluate: model(N) -> metrics(N), save.
- Structure prediction, elmerfold (OpenFold monomer pipeline), verified 2026-10-01 (10 sequences, 240 entities / 181
  activities, 0 dangling): see WORKED.md and [[workload_elmerfold]].
- Workflows (Pegasus/1000genome style), seed: artifact = final analysis outputs of the DAG; one type per file kind in
  the DAG, ids = logical file names; one activity per task type; files join tasks across processes by identical type+id.
