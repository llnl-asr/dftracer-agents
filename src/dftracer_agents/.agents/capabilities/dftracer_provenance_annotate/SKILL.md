---
name: dftracer_provenance_annotate
description: dftracer provenance mode, annotation stage. The dftracer_prov runtime API (C/C++/Python over dftracer's entity API), the provenance_* insertion tools, run configuration (DFTRACER_INC_METADATA=1), and the traps that drop provenance (no init, os._exit children, near-empty traces, first declaration wins). Load before inserting provenance calls, or when provenance_validate / dftracer_provenance_annotate_check fails.
---
Cross-references: [[dftracer_provenance_discover]], [[dftracer_provenance_graph]], [[prov_annotate]], [[dftracer_graph]], [[dftracer_profiling]]

# dftracer provenance mode: annotate

Related moirai-agents capability: `prov_annotate` (moirai's provenance annotation). This one inserts dftracer_prov calls.

## Rules
- **The spec is the contract.** Read `provenance/spec.yaml`. If it is missing or the code disagrees with it, stop and
  report: never invent entity types.
- `<WS>/provenance/source/` is a copy of the original source, separate from the perf `annotated/` tree.
- Ensure dftracer init/finalize exist at the entry point (reuse the normal entry annotation /
  `annotate_add_app_metadata`); no function-level perf annotation is required. C/C++:
  `provenance_ensure_lifecycle(run_id, <file defining main>)` (INIT as first statement of main, FINI through an
  `atexit` handler covering every return and exit path). Python: `initialize_log` in the entry script.
- `provenance_install_helpers(run_id, language, include_into=<files to edit>)`. It also writes the entity-type
  registry (`dftracer_prov_types.py` / `.h`) from the spec's descriptions; re-run it whenever the spec changes. Do not
  hand-write descriptions in code: they live in the spec.
- For each spec activity: locate it (`graph_query`), then `provenance_insert` an activity at the start of the
  transformation scope, `used(...)` where each cause is available and `generated(...)` where each effect exists
  (after the write succeeds). Ids follow the spec's id_scheme exactly; in-place updates get a new versioned id.
  Communication: both sides use the SAME type+id.
- Every inserted line is tagged `/* dft-prov */` (C/C++) or `# dft-prov` (Python): greppable, and re-runs are no-ops.
  `position` is `after` (default; indents one level after `{` / `:`) or `before`. Re-read the file after a batch of
  inserts: line numbers shift.
- C/C++: `clang_syntax_check` each edited file. Python: `python -m py_compile`.
- `provenance_validate(run_id)` must return `passed: true` and `dftracer_provenance_annotate_check(run_id)` must
  pass; fix and repeat.
- Report: files edited, activities inserted per spec entry, id expressions used.

### Runtime API (dftracer entity/relation API)
Provenance is recorded through dftracer's own entity API (C/C++/Python, same ids everywhere);
`provenance_install_helpers` writes a thin provenance-shaped helper (`dftracer_prov.py` / `dftracer_prov.h`) plus the
type registry over it.

| Concept | Representation |
|---|---|
| entity id | `EntityID` = 64-bit FNV-1a(type, key); 16 hex chars in the trace; key never stored |
| type / uri / description | fixed-length (32 / 256 / 256), JSON-safe charset, truncated |
| store | `EntityStore` enum (memory, gpu_memory, local_disk, parallel_fs, burst_buffer, object_store, database, network, other) |
| role | `EntityRole` enum per type (input, output, intermediate, parameter, reference) |
| event relations | `EntityRelation` used / generated / invalidated / updated -> JSON arrays on ANY event |
| entity relations | derived_from, revision_of, contains, part_of, specialization_of, alternate_of, depends_on -> `ER` records |

Records: `EH` (name=id, value=type|store|uri), `ET` (name=type, value=role|description), `ER` (name=relation,
value=subject|object). Relations are written even without `DFTRACER_INC_METADATA` (optional args such as the activity
type still need it).

Python (`import dftracer_prov as prov`):
```python
with prov.activity("save_data", "store") as a:
    m = a.used("best_structure", seq, "file", path)          # "file" -> store from path
    t = a.generated("structure_tar", seq, prov.Store.PARALLEL_FS, tar)
    a.invalidated("merged_msa", seq, "file", tmp)             # deleted here
prov.relate(prov.Relation.CONTAINS, t, m)                     # entity -> entity
```
C/C++: `DFT_PROV_ACTIVITY(a, "name", "type"); dft_prov_used(&a, type, key, DFT_STORE_*, uri);` or the core macros
`DFTRACER_C/CPP_FUNCTION_USES/GENERATES`, `DFTRACER_*_ENTITY_RELATE`. C++ also gets `dftprov::Activity` RAII.

### Run configuration
```
DFTRACER_ENABLE=1  DFTRACER_INC_METADATA=1   # REQUIRED: args dropped otherwise
DFTRACER_LOG_FILE=<WS>/provenance/traces/raw/<app>
```
No PAPI/variorum/GPU-profiler env, no `session_service_start`. App data still goes to `<WS>/dataset/<run>/` (PFS);
traces stay in the workspace.

## Constraints
- Never edit the original `source/` or the perf `annotated/` tree; only `<WS>/provenance/source/`.
- Never insert provenance by hand: `provenance_insert` keeps it idempotent and marked.
- Data-centric only: no PAPI, power, GPU profilers or node-counter daemon in a provenance build or run.
- Hot loops: one activity per meaningful unit (step/batch), never per element.

## Corner cases
- C `DFT_PROV_ACTIVITY` ends at scope exit; open it in the scope that spans the whole transformation, not inside a branch.
- `generated` for a file goes after the write/close succeeds, not before open.
- Initialize dftracer in EVERY process that records provenance (driver, process-pool initializer, spawned servers).
  Pool/server children exit via `os._exit` and skip atexit: register finalize with `atexit` AND
  `multiprocessing.util.Finalize`.
- dftracer deletes a process's trace if it logged < 5 data events (start/end count). Smoke-check with >= 3 activities
  or you will see no file and suspect a broken install.
- Task functions run in a pool by pickled name: rename to `_f_impl` and keep a same-named wrapper that opens the
  activity. For RPC/subprocess tasks, subclass the task type (`start`, `on_finish`) to record the driver side.
- Optional config inputs: when the file is absent, identify the config by what did configure the run (e.g.
  `preset:<name>`) so the root never disappears.
- First declaration of an entity in a process wins: its store and uri come from the first used/generated/entity call.
- Use the relation that says what happened: deletes are `invalidated`, in-place modification `updated`;
  containment/versioning are entity relations (`contains`, `revision_of`).
- `provenance_ensure_lifecycle` needs clang flags with ABSOLUTE `-I` paths when the file includes headers clang cannot
  find (e.g. `mpi.h`): relative ones resolve against the server's cwd.
- Before handing off, smoke-check end to end on a login node: init via the app's own helper, record >= 3 activities,
  confirm a trace file exists and parses.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector
`dftracer_provenance_annotate_check` (and `provenance_validate`) report:
- `helper`: run `provenance_install_helpers(run_id, language, include_into=...)`.
- `type_registry`: the spec changed after install: re-run `provenance_install_helpers`.
- `coverage`: a spec type or activity is never recorded, or a literal is not a spec type: `provenance_insert` the
  missing call at the spec's `where`, or fix the literal; if the code truly differs from the spec, stop and go back to
  the user (discover stage), never invent a type.
- `wiring`: add the include/import (`include_into`), use `DFT_PROV_ACTIVITY` instead of `dft_prov_begin` without
  `dft_prov_end`, and `provenance_ensure_lifecycle` on the file defining main().
- `markers`: the calls were added by hand: re-insert with `provenance_insert`.
- `python_syntax`: an insert broke indentation: fix `position` (before/after) and re-insert, then py_compile.
- `spec_present` / `source_present` (inconclusive): write the spec or copy the source tree first.
Then predict (insert) and validate again; the same failure twice stops the loop.

## Self-heal
- Tool broken: fix it in `mcp_tools/tools/session/provenance.py` (dftracer-agents) or `capability_update` this
  capability, and ask for an MCP restart; never fall back to manual edits when a restart is what is needed.

## Self-learn
- What is explored here becomes a capability: a new framework pattern (pool, RPC, scheduler) becomes a lesson here, and
  a workload's annotation recipe goes into its `workload_<app>` capability; see capability_builder.
- Propose skill/lesson updates to the main thread (confirmation gate); redact before persisting.

## What works
- 2026-10-01, elmerfold (OpenFold monomer pipeline): driver + process pool + GPU inference server instrumented in
  Python with `dftracer_prov`; init in every process (pool initializer and server startup), finalize through both atexit
  and `multiprocessing.util.Finalize`; `_f_impl` wrappers for pickled pool tasks; RPC edges by subclassing the task
  type; config root as `preset:<name>`. Result: 240 entities / 181 activities, 0 dangling; artifact members and a
  metrics file byte-identical to the plain run, +1% wall time.
