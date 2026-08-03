# DFTracer Pipeline Plan — ray_molformer/20260725_000436

## Overview

- **App**: IBM MoLFormer (chemical-language transformer, PyTorch) launched via Ray 2.40.0
  for distributed task scheduling (NOT MPI, despite mpi4py appearing in requirements.txt —
  Ray handles all distribution; treat `mpi=false` from detection as authoritative).
- **Source**: symlinked from `/p/lustre5/ice4hpc/drug-discovery/ray_molformer` (Python,
  pip-based, no formal package/setup.py structure). Key launch scripts:
  `ray_flux_molformer_job.sh` (top-level job script: experiment name/storage path, node
  count) -> `start_ray_run_molformer.sh` (sets up Ray cluster, sources env, loads ROCm) ->
  python training entry point.
- **System**: Tuolumne (AMD MI300A APU, Cray PE, Flux scheduler). ROCm 6.2.1 required
  (from `start_ray_run_molformer.sh`). Active flux allocation: `f3NCgEB6TL5D` — ASK the
  user to confirm this allocation still has remaining time before any run step; do not
  spawn a second allocation unless it has expired or the user asks for a fresh one.
- **Dependencies**: PyTorch (ROCm 6.2.1 build), Ray 2.40.0, DeepChem, RDKit, Dask,
  HDF5 1.10.5 (system, `/usr`, source `h5cc`), mpi4py present but unused by the app.
  `ml_framework_details.lightning=false` — this app does NOT use PyTorch Lightning, so
  the `lightning_fabric` Ampere-check patch from the prior `software-molformer` skill
  entry likely does not apply here; verify by grepping imports in STEP 2 and only apply
  if actually hit.
- **Existing skill**: `software-molformer` (from a prior PyTorch-only MoLFormer session,
  same system) has these lessons, apply only where genuinely relevant to ray_molformer:
  - Duplicate `dftracer.initialize_log()` in imported (non-entry-point) files silently
    corrupts traces — call `initialize_log()` ONLY in the true entry point script, never
    in modules that get imported (this matters even more here since Ray workers import
    the training module in separate processes — each Ray worker process needs its OWN
    correctly-scoped init, not a duplicated one from an imported module).
  - ROCm/PyTorch wheel version must match the loaded ROCm module exactly (6.2.1).
  - `libcaffe2_nvrtc.so` needs an explicit `LD_LIBRARY_PATH` entry.
  - `lightning_fabric` Ampere-capability crash — likely N/A here (no lightning detected),
    confirm and skip if not applicable.
- **User's explicit requirement**: annotate AND optimize the app, and trace BOTH (a) the
  Ray service/framework layer (task dispatch, object store, worker startup/scheduling)
  and (b) the MoLFormer application code (model compute, data loading/IO), so the
  optimization loop can see Ray-induced overhead alongside model compute/IO.

### Ray tracing feasibility (decision made now, do not re-litigate per step)

dftracer instruments via **source-level Python decorators / FUNCTION-mode annotation**
(never PRELOAD, per project policy). Ray's distributed core (object store, raylet
scheduler, GCS) is implemented in C++ and is out of scope for source annotation from
this pipeline — do not attempt to build/annotate Ray's C++ core.

What IS annotatable and in scope:

1. **App-side Ray usage** — the app's own driver/worker code that calls
   `ray.init()`, `@ray.remote`, `.remote()`, `ray.get()`, `ray.put()`, actor method
   definitions, etc. Annotate these call sites in the app's own source (driver script +
   any modules defining remote functions/actors) exactly like normal Python annotation —
   this captures task-submission latency, `ray.get()` blocking time, and object transfer
   from the app's perspective.
2. **Ray's own Python-level entry points in site-packages** — Ray ships a pure-Python
   layer (`ray/_private/worker.py`, `ray/remote_function.py`, `ray/actor.py`,
   `ray/_private/serialization.py`, `ray/util/scheduling_strategies.py`) that wraps the
   C++ core via Cython bindings (`ray/_raylet.pyx` compiled). These Python entry points
   ARE annotatable with dftracer's Python decorator, and wrapping them (specifically:
   `worker.py`'s task submission/execution paths, `remote_function.py`'s `_remote()`,
   `actor.py`'s actor task submission, and `serialization.py`'s serialize/deserialize)
   gives visibility into task dispatch overhead and object-store serialization cost
   WITHOUT touching the compiled Cython/C++ core. This is the mechanism for tracing
   "Ray's own scheduling/object-store/worker-dispatch layer" per the user's requirement.
3. **Out of scope**: the compiled `_raylet.so` (Cython/C++), the GCS server, and the
   raylet process itself (separate C++ binaries, not Python-importable call sites).
   If diagnosis later shows a bottleneck plausibly inside this compiled layer (e.g.
   object spilling to disk), the diagnoser/optimizer must infer it INDIRECTLY from the
   gaps between the annotated Python-level boundaries (e.g. time between `_remote()`
   entry and the wrapped function actually executing) rather than claim direct
   instrumentation of the C++ core.

STEP 4 (annotator) must identify the exact installed Ray version's site-packages path
(`python -c "import ray, os; print(os.path.dirname(ray.__file__))"` inside the app's
venv) and confirm these module paths exist before annotating; Ray's internal file
layout can shift between minor versions.

### Canonical paths (from session_status / session_get_run_paths)

- Workspace: `/usr/WS2/haridev/dftracer-agents/workspaces/ray_molformer/20260725_000436`
- Subdirs present: `performance, source, artifacts, scripts, build, install`
- `source_dir` pattern per run: `<WS>/<run_name>/source`
- `traces_raw` pattern: `<WS>/<run_name>/traces/raw`, `traces_compact`: `<WS>/<run_name>/traces/compact`
- Always call `session_get_run_paths(run_id, run_name)` fresh in each step — do not
  hand-build paths; `run_name` for the baseline trace run should be `"baseline"`, for
  optimization iterations use `opt1`, `opt2`, etc.
- `profile_bind` was ALREADY called at session creation. Every step below must bracket
  its work with `profile_step_begin(step="STEP N: <agent-name>", agent="<agent-name>")`
  / `profile_step_end(step="STEP N: <agent-name>", status=...)` using the heading text
  verbatim as the step id. The final report step calls `profile_report()`.

### Data placement (MANDATORY, project policy #11)

- App data (Ray object spill dir, experiment storage path, checkpoints, DeepChem/RDKit
  caches, dataset files) → a directory on Lustre (`/p/lustre5/...`), reachable via
  `<WS>/dataset/<run_name>/` — confirm `session_create` set up a `dataset/` symlink; if
  not, create one manually to a Lustre path before any run step, and set
  `ray_flux_molformer_job.sh`'s storage-path arg to point there.
- dftracer TRACES always stay under `<WS>/<run_name>/traces/...` in the workspace, never
  on Lustre.

---

## STEP 1: dftracer-system-detect

Confirm/refresh the system facts already captured by `session_detect` (see Overview).
Inputs: `run_id="ray_molformer/20260725_000436"`.

1. Call `system_detect()` for Tuolumne: confirm module load order, Flux launcher syntax,
   filesystem layout (home vs Lustre), and record any Tuolumne-specific quirks not
   already in `system-tuolumne` skill (check that skill first via
   `graph_query(mode="docs", question="Tuolumne ROCm ray ml")`).
2. Confirm ROCm 6.2.1 module is loadable and ray_molformer's `start_ray_run_molformer.sh`
   module-load sequence still matches current Tuolumne module state (module versions can
   drift; the app script is authoritative per project env-consistency rule).
3. Verify the flux allocation `f3NCgEB6TL5D` is still alive and has enough remaining
   time: `flux jobs -no "{id} {state} {t_remaining}" f3NCgEB6TL5D`. If expired, note that
   STEP 6/STEP 8/STEP 9 will need the user to supply a fresh allocation.
4. Record any new lesson to `system-tuolumne` skill as a PROPOSAL only (do not self-write
   — follow the confirmation gate); report the proposal text back to the orchestrator.

Expected artifact: confirmed system facts appended/validated against Overview; allocation
liveness check result; any proposed `system-tuolumne` skill delta.

---

## STEP 2: dftracer-session-setup

Inputs: `run_id`, source already symlinked at
`file:///p/lustre5/ice4hpc/drug-discovery/ray_molformer` (per session_status). Session
is already in `detected` state — this step finalizes workspace setup, not re-cloning.

1. Use `session_get_run_paths(run_id, run_name="baseline")` to get `source_dir` etc.
2. Read `ray_flux_molformer_job.sh` and `start_ray_run_molformer.sh` in full (these ARE
   the environment definition per project policy — reuse verbatim, do not reinvent).
   Extract: module load sequence, ROCm path, venv/conda activation, Ray cluster startup
   command (`ray start --head` / `ray start --address=...`), python entry point script
   name and its CLI args (experiment name, storage path, node count).
3. Grep the app's actual Python imports (`grep -rn "^import\|^from" <source_dir>/*.py`)
   to confirm: (a) whether PyTorch Lightning is truly absent (skip that skill lesson if
   so), (b) whether DeepChem's MolFormer wrapper is imported directly or the model is
   hand-built (README says "not directly import MolFormer from deepchem" — confirm).
4. Set up (or confirm) `<WS>/dataset/` as a Lustre symlink for app data placement.
5. Record build_tool as `python`/pip-based (already detected); no formal package, so the
   "build" step (STEP 3) is really "create + populate a venv", not compile.

Expected artifact: confirmed entry-point script + args, confirmed import list, dataset
symlink verified, any deltas to `software-molformer` skill proposed (not written).

---

## STEP 3: dftracer-build-app

Inputs: `source_dir` from STEP 2, ROCm 6.2.1, Python version matching the ROCm PyTorch
wheel (check `software-molformer` skill L38 lesson: wheel version must match loaded
ROCm module exactly).

1. Create a single venv in `<WS>/install/venv` (or wherever `session_configure`
   canonically places it) and install requirements.txt PLUS the ROCm-matched PyTorch
   wheel, Ray 2.40.0, DeepChem, RDKit, Dask, mpi4py (even though unused, keep it so
   imports don't fail; do NOT attempt to build MPI-parallel HDF5 — this app doesn't use
   parallel HDF5).
2. Apply the `libcaffe2_nvrtc.so` `LD_LIBRARY_PATH` fix from `software-molformer` skill
   if the smoke import (`python -c "import torch; print(torch.cuda.is_available())"` —
   ROCm reports via the CUDA API) fails with a missing-library error.
3. Smoke-test Ray cluster bring-up standalone (`ray start --head` then
   `python -c "import ray; ray.init(); print(ray.cluster_resources())"`) BEFORE trying
   the full app, to isolate Ray-cluster problems from model problems.
4. Smoke-test the unmodified app on a small config (few molecules, 1-2 Ray workers, tiny
   node count) to get a known-good baseline BEFORE any dftracer involvement.
5. Log all build/install output to `<WS>/artifacts/03_build_app_*.log`.

Expected artifact: working venv path, confirmed Ray cluster start/stop commands, a green
smoke-test log, ROCm/PyTorch version pinned and recorded.

---

## STEP 4: dftracer-build-dftracer

Inputs: venv from STEP 3 (install dftracer into the SAME venv — mandatory, see
`feedback-dftracer-aiml-venv`), ROCm path, HDF5 1.10.5 system install at `/usr`.

1. Bind `CC`/`CXX` per the app's own module load sequence from STEP 2 (not `which mpicc`
   — this app has no real MPI dependency; use the Cray/GNU compiler the ROCm stack
   expects).
2. Install dftracer with Python bindings ON, HDF5 ON (`HDF5_ROOT=/usr`), HIP tracing OFF
   (per `hip_tracing_needed: false` from detection — this app has no HIP calls itself,
   ROCm here is just the PyTorch backend, not app-level HIP), MPI support can stay off
   or minimal since the app is Ray-only.
3. Verify: `python -c "import dftracer.dftracer"` succeeds inside the SAME venv used by
   the app.
4. Produce a NON-EMPTY smoke `.pfw` from a trivial annotated snippet before moving on.

Expected artifact: dftracer installed in-venv, import verified, smoke `.pfw` confirmed
non-empty, install log at `<WS>/artifacts/04_build_dftracer_*.log`.

---

## STEP 5: dftracer-annotator (dispatches dftracer-annotate-python + dftracer-validate-python)

Inputs: `source_dir` from STEP 2, dftracer-enabled venv from STEP 4, the Ray tracing
feasibility decision from the Overview section (READ IT — do not re-derive).

### Scope (two annotation targets, both required)

**A. App source** (all `.py` files under `source_dir` reachable from the training entry
point — driver script, model definition, data loading/dataset code, checkpoint code,
Ray remote-function/actor definitions). Standard Python `@dft_fn`/decorator annotation
of top-level functions and methods; call `initialize_log()` ONLY once, in the true
top-level entry point invoked by `start_ray_run_molformer.sh`, per the
`software-molformer` skill lesson — critical here because Ray re-imports the training
module inside each worker process; a duplicated init inside an imported module will
corrupt/duplicate traces per-worker.

**B. Ray framework Python layer** (in the SAME venv's `site-packages/ray/`). Resolve the
exact path with `python -c "import ray, os; print(os.path.dirname(ray.__file__))"`
inside the venv first, then confirm these files exist before annotating (paths can shift
across Ray minor versions — Ray 2.40.0 assumed here):
  - `ray/_private/worker.py` — task submission (`submit_task`) and execution entry
    points (`main_loop`/task execution callback)
  - `ray/remote_function.py` — `RemoteFunction._remote()` (task dispatch)
  - `ray/actor.py` — actor task submission (`ActorMethod._remote()` /
    `ActorHandle._actor_method_call`)
  - `ray/_private/serialization.py` — `serialize`/`deserialize` (object store
    marshal cost)
  - Optionally `ray/util/scheduling_strategies.py` if placement-group / scheduling
    logic is in play for this job's node layout.
  Wrap these specific functions/methods with dftracer decorators, not the whole file —
  Ray's site-packages is large and mostly irrelevant; over-annotating adds overhead and
  noise. Do NOT touch `ray/_raylet.pyx`/`.so` (compiled, out of scope, see Overview).
  Treat this site-packages copy as venv-scoped: re-annotating after any `pip install
  --upgrade ray` will be necessary, note this in the annotated-file manifest.

3. Run `dftracer-validate-python` on both sets of annotated files: confirm balanced
   decorator start/end, confirm no annotation was placed inside a hot per-item loop
   (e.g. per-token, per-batch-element) — annotate the batch/task boundary, not the
   innermost loop.
4. Confirm-list decision point (report to orchestrator, not a blocking question): total
   file count annotated in each of (A) and (B), and the exact Ray internal file list
   used, so the human can review scope before STEP 6 builds against it.

Expected artifact: annotated copies under `<WS>/annotated/` covering both app source and
the identified Ray site-packages files, validation report (balanced macros, no hot-loop
violations), manifest of exactly which files/functions were wrapped in each category.

---

## STEP 6: dftracer-build-smoke

Inputs: annotated tree from STEP 5, dftracer venv from STEP 4.

1. Copy annotated files into place (app source tree + venv's `ray` package) — since this
   is pip/venv-based, "build" here is just ensuring the annotated `.py` files are the
   ones actually imported (either overwrite in the venv site-packages copy directly, or
   set `PYTHONPATH`/editable-install so the annotated copies take precedence — pick
   whichever the existing `session_build_annotated`-equivalent tooling supports and
   record the choice).
2. Run a SHORT smoke test: `ray start --head` (single node, few workers) + the app
   entry point with a tiny problem size (few molecules / 1 short training step), with
   `DFTRACER_ENABLE=1`, `DFTRACER_DATA_DIR=<WS>/baseline/traces/raw`,
   `DFTRACER_LOG_FILE` pointed into the session workspace (never Lustre, per policy #11).
3. Verify non-empty `.pfw` output exists both from app-side annotated functions AND from
   the Ray-internal annotated functions (grep the compact/raw trace for both category's
   function names) — this is the concrete check that Ray-layer tracing actually worked,
   not just app-layer.
4. `ray stop` cleanly after the smoke test.

Expected artifact: green smoke test exit 0, confirmed dual-source trace output (app +
Ray), log at `<WS>/artifacts/06_build_smoke_*.log`.

### STEP 6 RESULT (completed 2026-07-25)

- Root cause of the earlier interrupted/empty-trace state: the dftracer native
  Python extension (`dftracer.dftracer`) was being built as `cpython-313` inside the
  app's `python3.9` venv. `dftracer/python/logger.py` silently catches the resulting
  `ImportError` and falls back to a `NoOpProfiler`, so imports/runs succeeded with
  zero trace output and no error -- this is why STEP 4/prior attempts looked "ok"
  but nothing was ever actually captured.
- True cause of the ABI mismatch: CMake's `find_package(Python3)` in dftracer's
  `setup.py`/CMakeLists prioritizes the `VIRTUAL_ENV` environment variable over the
  `Python3_EXECUTABLE`/`Python3_ROOT_DIR` hints. The Claude harness's own dev venv
  (`/usr/workspace/haridev/dftracer-agents/.venv`, Python 3.13) is inherited via
  `VIRTUAL_ENV` into every Bash tool call in this session, so CMake picked THAT
  interpreter's ABI regardless of PATH ordering or explicit `-DPython3_EXECUTABLE=`
  hints. Fix: `unset VIRTUAL_ENV` before invoking `pip install`/`cmake` for the
  app's dftracer build. Patched into `<WS>/rebuild_dftracer.sh`. After the fix the
  extension builds as `dftracer.cpython-39-*.so` and `import dftracer.dftracer`
  succeeds; `dftracer.python.common.profiler` becomes the real native module instead
  of `NoOpProfiler`.
- Real entry point confirmed: `start_ray_run_molformer.sh` actually invokes
  `molformer_ray_descriptors.py` (NOT `molformer_ray.py`, an older/alternate
  variant). `molformer_ray_descriptors.py` already contains the required
  `dftracer.initialize_log()` call at the entry point (STEP 5 annotator did this
  correctly for the real entry file).
- Smoke test performed (single process, no Ray cluster startup needed -- Ray runs
  in local single-node mode via `ray.init(num_cpus=2)`): imported
  `molformer_ray_descriptors.py`, called `dftracer.initialize_log()`, issued one
  trivial `@ray.remote` task, and called a `@_dft.log`-decorated app function
  (`remove_module_prefix`/`remove_prefix` from `molformer_ray.py`). A full GPU
  training-loop smoke run was NOT attempted here -- `train_func_per_worker` requires
  GPU + the real HF/Lustre model checkpoint + the 1B-row Lustre CSV dataset, which
  belongs to STEP 7's real flux-allocation run, not this lightweight smoke check.
  Result: exit 0, non-empty trace
  `<WS>/baseline/traces/raw/smoke_descriptors-<hash>-app.pfw.gz` (~1.05 MB gz,
  83467 total events): 2 app-layer events (`cat=molformer_ray`:
  `remove_module_prefix`, `remove_prefix`), 7 Ray-framework-layer events
  (`cat=comm`: `RemoteFunction._remote`, `SerializationContext.serialize`,
  `SerializationContext._deserialize_object`, `Worker.get_objects`), plus ~83k
  POSIX I/O events (`DFTRACER_DATA_DIR=all`). Confirms BOTH tracing targets (app +
  Ray Python layer) are functional.
- Run record captured as run "smoke" (`session_capture_run_record`, prev_run
  "annotated"). Fixed rebuild script at `<WS>/rebuild_dftracer.sh` (idempotent,
  rerun before STEP 7 if the venv is rebuilt or dftracer is reinstalled) -- it now
  unsets `VIRTUAL_ENV` before building.
- STEP 7 must: re-verify `import dftracer.dftracer` succeeds (not `NoOpProfiler`)
  before launching the baseline run, since any fresh `pip install` in this
  environment risks re-picking up `VIRTUAL_ENV` if `rebuild_dftracer.sh`'s fix isn't
  reused verbatim. Use `molformer_ray_descriptors.py` as the entry point (matches
  `start_ray_run_molformer.sh`), not `molformer_ray.py`.

---

## STEP 7: dftracer-tracer

Inputs: annotated+smoke-verified build from STEP 6, confirmed flux allocation from
STEP 1 (or ask user for a fresh one if expired).

1. ASK the user (if not already answered this session): use existing allocation
   `f3NCgEB6TL5D` (if still alive) via `flux proxy <jobid> bash <wrapper>.sh ...`, or
   spawn a new one? Also ask for a TIME BUDGET for the training run (DL run-length rule)
   — target at least ~10 minutes of training; calibrate epoch/step count from a short
   probe on this run rather than guessing.
2. Write a bash wrapper script (never inline module loads) at
   `<WS>/scripts/run_baseline.sh` that: loads modules, activates the venv, sets
   `DFTRACER_*` env vars pointing traces at `<WS>/baseline/traces/raw`, sets Ray's
   object-spill / experiment storage path at `<WS>/dataset/baseline/` (Lustre), starts
   `dftracer_service` (node-counter daemon, one instance per node pinned to 1 core) via
   `session_service_start` BEFORE the app launch and `session_service_stop` AFTER —
   mandatory per policy #12 — starts the Ray cluster, launches the fixed-epoch-count
   training entry point, then `ray stop`.
3. Launch with `run_in_background: true` if using `flux proxy` (never block the Bash
   tool >10 min on a live proxy).
4. After completion, call `session_split_traces` on `<WS>/baseline/traces/raw` ->
   `<WS>/baseline/traces/compact`.
5. Record run lessons (Ray cluster start/stop timing quirks, any Flux+Ray interaction
   issues) as a PROPOSAL to `software-molformer` or a new `software-ray` skill.

Expected artifact: `run_id`-scoped `baseline` run directory with raw+compact traces,
node-counter service traces alongside, wrapper script under `<WS>/scripts/`, run log
under `<WS>/artifacts/07_tracer_baseline_*.log`.

**STATUS (2026-07-25, updated by diagnostic session): root-cause GCS bug FIXED and
verified; baseline run NOT yet complete — a second, independent stall found further
downstream.** Four real bugs found & fixed in `<WS>/scripts/baseline_runner.sh`:

1. **ROOT CAUSE of the original `ray start --head` GCS timeout** (the bug this
   diagnostic session was launched to solve): `ray/_private/services.py::
   start_ray_process` unconditionally injects `LD_PRELOAD=<venv>/ray/core/
   libjemalloc.so` into every Ray subprocess (gcs_server, raylet, workers)
   whenever `LD_PRELOAD` is unset in the parent env. With `module load rocm/6.2.1`
   loaded, dlopen'ing jemalloc under Tuolumne's already-crowded glibc static-TLS
   budget fails with `cannot allocate memory in static TLS block` — gcs_server's
   `exec()` never reaches `main()`, so it writes zero log lines, exactly matching
   the `gcs_server.err: FileNotFoundError` symptom. **Fix:** `export LD_PRELOAD=""`
   (explicitly empty, not unset) before every `ray start` call. Verified standalone
   (`scripts/test_gcs_fix.sh`): `ray start --head` now succeeds and `ray status`
   reports the node active within ~10s, reproduced across multiple full job launches.
2. Address-parsing bug: `grep "address="` matched two different log lines and
   corrupted the extracted head address — anchored to the unique
   `ray start --address='...'` line instead.
3. `molformer_ray_descriptors.py` calls bare `ray.init()` (no address). Without
   `RAY_ADDRESS` exported, this silently created an ISOLATED single-node local
   Ray cluster instead of joining the 2-node cluster manually bootstrapped by
   this script — fixed by exporting `RAY_ADDRESS="$ray_addr"` before the
   training launch.
4. The head's `ray start --head` used `--num-gpus=0` (a leftover from GCS
   debugging), so only the worker's 4 GPUs registered cluster-wide while
   `ScalingConfig(num_workers=args.nodes*4=8)` needs 8 — fixed to
   `--num-gpus=4` on the head too. Also added `DATASET_CSV_PATH` export (the
   script's fallback relative path resolves under `annotated/dataset/...`,
   which does not exist) and switched the worker's fixed `sleep 900` to a
   poll on a head-written completion marker file, since a fixed sleep shorter
   than actual training time was dropping the worker's 4 GPUs mid-run.

**Remaining open issue (NOT yet root-caused):** after all 4 fixes, the run
now reliably forms the 2-node/8-GPU cluster, connects, and resolves the
dataset path — but then stalls indefinitely (confirmed via `flux exec ps`/
`pgrep` on the head node: the `python3 -u molformer_ray_descriptors.py`
process is alive with ~0-2% CPU) somewhere between "Connected to Ray cluster"
and the first `ray::` worker actor appearing / any Tune scheduling warning.
No traceback, no `InsufficientResourcesManager` warning, no dftracer I/O
trace growth. `py-spy` is not installed in the session venv, which blocked
getting a Python stack trace of the stalled process — **install `py-spy`
into `<WS>/install/venv` (no internet needed if already pip-cached, or via a
node with outbound access) and `py-spy dump --pid <pid>` on the stalled
process** is the single most useful next diagnostic step. Leading hypotheses,
in order of suspicion: (a) `AutoTokenizer`/`AutoConfig.from_pretrained(...,
trust_remote_code=True)` still attempting an etag/HEAD network round-trip to
huggingface.co despite `HF_HUB_OFFLINE=1`/`TRANSFORMERS_OFFLINE=1` now being
exported (compute nodes have no outbound internet, so this would hang for a
long time under default `requests` timeouts); (b) a Ray placement-group /
actor-scheduling deadlock specific to the MI300A `accelerator_type` resource
label; (c) an OOM-killed worker actor whose exit isn't being surfaced by
Tune's controller. Do NOT re-attempt with the old fixed `sleep 900` worker
wait — always use the completion-marker poll now in the script.

**STATUS (2026-07-25, session 2, live forensic pass): hang isolated to
`ray.init()` itself, NOT `TorchTrainer`/dataset/read_csv/HF network.**
Added `BISECT:` print/flush checkpoints around `ray.init()`, `read_csv`,
`TorchTrainer(...)` construction, and `.fit()` in
`<WS>/annotated/src/molformer_ray_descriptors.py`. Confirmed by direct
reproduction: the ONLY BISECT line that ever prints is
`"BISECT: before ray.init()"`; the process hangs forever between that and
`"BISECT: after ray.init()"`, i.e. strictly inside `ray.init()`'s internal
`connect()` call (`worker.py:1934`), well before dataset loading or
`TorchTrainer`. This rules out hypotheses (a)/(c) above outright.

New evidence gathered live via `flux exec -r <rank>` while hung (this is the
GCS/raylet-log evidence the prior pass didn't get):
- `raylet.err` on the HEAD node floods with dozens of
  `worker_pool.cc:586: Some workers of the worker process(<pid>) have not
  registered within the timeout. The process is still alive, probably it's
  hanging during start` for consecutively-climbing worker PIDs, starting
  within ~1-2 min of `ray start --head`. **Root cause of THIS symptom**:
  raylet auto-detects `num_cpus` from hardware (192 cores on this MI300A
  node) and uses it DIRECTLY as both `--maximum_startup_concurrency` and
  `--num_prestart_python_workers` (`ray/_private/services.py:1667,1910`),
  so it forks ~192 concurrent `setup_worker.py`→`default_worker.py` Python
  processes at startup, all needing to `import ray` (grpcio/pyarrow/many
  `.so`s) from THIS session's Lustre-hosted venv at once — an import storm
  against a network filesystem. **Fix applied and confirmed present in the
  code path**: `--num-cpus=16` added to BOTH `ray start --head` and
  `ray start --address=` calls in `baseline_runner.sh`, which does reduce
  `num_prestart_python_workers`/`maximum_startup_concurrency` from 192 to 16
  (verified via `ps` showing `--num_prestart_python_workers=16` on the
  patched raylet cmdline) — but **this fix alone did NOT resolve the hang**;
  even 16 concurrent prestart workers all fail identically
  ("hanging during start", zero `python-core-worker-*.log` files ever
  created for ANY of them, meaning every one dies before even reaching
  CoreWorker's own log init). Kept anyway: it is still correct sizing
  (workload only needs 8 GPU-bound workers total, not 192 CPU slots/node)
  and cuts unnecessary process/import churn by >10x.
- The DRIVER's own core-worker log
  (`python-core-driver-*.log`) shows exactly two lines and stops forever:
  `core_worker_process.cc:192: Constructing CoreWorkerProcess` then
  `io_service_pool.cc:37: IOServicePool is running with 1 io_service`. No
  further CoreWorker log line is ever written.
- `ss -x` on the head node while hung shows the driver's raylet IPC unix
  socket in `ESTAB` state (0 bytes both directions) — the local connect()
  to raylet DOES complete at the OS level.
- raylet's own `debug_state.txt` (dumped periodically to
  `<ray-temp-dir>/session_*/logs/debug_state.txt`) shows **both nodes'
  resources ARE correctly known to the head's raylet** (`Cluster resources`
  lists both node IDs with full `GPU: 40000, CPU: 160000` etc.), and
  `registered jobs: 1` — i.e. the driver's OWN job registration with
  GCS/raylet SUCCEEDED. This rules out the earlier "GCS node-registration
  propagation is stuck" theory from the memory doc. But the same
  `debug_state.txt` shows `num PYTHON workers: 0, num idle workers: 0` —
  raylet has zero live Python workers of any kind (prestart or on-demand),
  consistent with the identical prestart-worker-startup failure above.
- Confirmed via `/proc/<pid>/environ` + `ps aux | grep -i spindle`: Spindle
  is genuinely OFF for these jobs (`-o spindle.level=off` is taking effect,
  no spindle processes present) — this rules OUT a recurrence of the
  earlier Fix #9 Spindle FUSE-relay deadlock as the cause of this second
  hang.

**Net position:** the job registers with GCS/raylet successfully and both
nodes' resources are mutually visible, but NO worker process of any kind
(prestart OR the eventual Ray Train actors) ever completes CoreWorker
startup — and neither does anything log-visible happen in the DRIVER's own
CoreWorker after `IOServicePool is running`. This is consistent across
`--num-cpus=192` (default) and `--num-cpus=16` (patched), so it is NOT
purely a concurrency/import-storm effect — there is a second, independent
defect in this Ray/raylet build's CoreWorker startup handshake on this
system that the `--num-cpus` fix does not address. **Not yet root-caused.**
Most promising unexplored next steps (in order): (1) install `py-spy` into
`<WS>/install/venv` and get an actual Python stack trace of the frozen
driver (still never done — blocked by no internet on compute nodes in
earlier attempts; try `pip install --no-index` from a pre-cached wheel, or
install on a login/build node with outbound access and copy the venv site-
package in); (2) `gdb -p <driver-pid>` (no internet needed) to get a C++-
level backtrace of the raylet-registration RPC/callback chain since the
Python-level tools (wchan/syscall) only show a generic `recvfrom` and can't
see which async gRPC callback is pending; (3) try disabling Ray Train/Tune
entirely and doing a minimal `ray.init(); print(ray.cluster_resources())`
smoke script standalone (no dftracer, no TorchTrainer, no dataset) to
confirm whether the hang is really 100% inside stock `ray.init()` or
requires something dftracer-adjacent in this driver script; this was NOT
done this pass and would definitively separate "Ray/raylet bug on this
system" from "something in `dftracer.initialize_log()` called immediately
before `ray.init()` in this driver interfering with CoreWorker's fork/
threading state."

STEP 7 is still NOT complete — no baseline trace with confirmed dataset I/O
exists. Do not credit this session with a working baseline.

---

## STEP 8: dftracer-analyzer

Inputs: `<WS>/baseline/traces/compact` from STEP 7.

1. Use `session_analyze_traces` (or `dfanalyzer` MCP tool) with a preset appropriate to
   this mixed Ray+PyTorch workload — check `list_presets()`; likely closest to a
   generic/DL preset rather than posix-IO-only, since this app is compute+scheduling
   heavy, not IO-bound in the traditional HPC sense. Use `cluster_n_workers=32` (never
   `cluster_cores`, per `feedback-analysis-parallel-workers`).
2. Produce separate summary views for: (a) app-layer function timings, (b) Ray-layer
   function timings (task dispatch latency, serialization cost, actor call overhead),
   (c) node-counter service metrics (CPU/mem/IO saturation per node).
3. Save the analysis output under `<WS>/artifacts/08_analyzer_*.log` /
   `<WS>/baseline/analysis/`.

Expected artifact: analysis summary distinguishing Ray-scheduling overhead from
app-compute time from IO time.

---

## STEP 9: dftracer-diagnoser

Inputs: analysis output from STEP 8.

1. Diagnose bottlenecks across all dimensions (IO, compute, memory, communication —
   here "communication" mostly means Ray task dispatch/object-store transfer rather than
   MPI collectives).
2. Specifically flag: (a) Ray task dispatch overhead (gap between `_remote()` call and
   actual execution start), (b) object-store serialization cost, (c) worker startup
   cost (if Ray workers are being restarted/recycled during the run), (d) any object
   spilling to disk (inferred indirectly per the Overview's "out of scope" note, since
   the raylet/GCS internals aren't directly traceable), (e) standard app-side compute/IO
   bottlenecks (data loading, checkpoint IO, tokenization, model forward/backward).
3. Produce a ranked bottleneck list with magnitude estimates, feeding STEP 10.

Expected artifact: ranked bottleneck list covering both Ray-framework and app-layer
findings.

---

## STEP 10: dftracer-optimizer

Inputs: ranked bottleneck list from STEP 9, annotated+working build from STEP 6.

Per policy #14, dispatch ALL FOUR component subagents regardless of which dimension the
diagnosis flagged as dominant, each walking its skill's full checklist and reporting
every L1/L2/L3 candidate considered (applicable and not-applicable-with-reason):

- `dftracer-optimizer-io`: data loading, checkpoint IO, dataset caching, Ray object
  spill-to-disk behavior if flagged.
- `dftracer-optimizer-communication`: Ray task dispatch batching, actor pool sizing,
  object-store transfer size reduction (e.g. passing references instead of large
  objects), placement-group/scheduling-strategy tuning, worker pool warm-start to avoid
  repeated startup cost.
- `dftracer-optimizer-compute`: model forward/backward efficiency, batch size, ROCm
  kernel utilization, tokenization overhead.
- `dftracer-optimizer-memory`: object-store memory pressure vs spilling, Ray worker
  memory footprint, PyTorch activation memory.

For each proposed variant: hold total work constant (fixed epoch/step count from STEP 7
calibration), same dataset size, same node count unless the knob under test IS node
count (call that out explicitly). Apply the "do-less" lever check — reducing
checkpoint frequency or dataset size is not credited as a speedup without normalizing
for total work done.

Run each variant as `opt1`, `opt2`, ... via `session_get_run_paths(run_id, run_name=
"optN")`, bracket each with `dftracer_service` start/stop, trace+split+analyze, compare
against the `baseline` run using `session_compare` if available.

Expected artifact: merged 4-dimension optimization report, best variant identified with
measured delta against baseline + at least one baseline replicate for noise band.

---

## STEP 11: dftracer-tracer (N-node validation run)

Inputs: best variant from STEP 10, same allocation/time-budget policy as STEP 7.

1. Re-run the best optimization variant at the target validation scale (ask the user for
   node count if not already fixed in STEP 7), same fixed epoch count as baseline for a
   fair comparison, same `dftracer_service` node-counter bracketing.
2. Take at least one replicate to establish/confirm the noise band from STEP 10.
3. Verify actual run scale and completion status before crediting any wall-time delta
   (per `feedback-flux-alloc-verify-scale` — do not credit a half-scale/cancelled run).

Expected artifact: validated N-node measured delta (%), replicate-based noise band,
confirmation the run completed at full scale.

---

## STEP 12: dftracer-report

Inputs: all prior step artifacts, `run_id`.

1. Assemble `final_report/` per policy #15: `config.ini` is the only place a real
   path/session value goes; `scripts/lib_load_config.sh` sourced by every other script;
   every script actually run this session (build, baseline, each opt variant,
   validation) copied into `scripts/`.
2. Self-contained validation: point `OUTPUT_ROOT` at `<WS>/final_folder_validate/` and
   run `scripts/run_all.sh <alloc-id>`; only call `session_final_report(validated=True,
   validation_notes=...)` once it reproduces the reported result within noise.
3. Grep the workspace's absolute path across `scripts/` to confirm no leakage.

Expected artifact: `<WS>/final_report/` complete and validated.

---

## STEP 13: dftracer-privacy-guard

Inputs: entire session workspace including `final_report/`.

1. Run `privacy_scan()` across the whole session (including the `final_report/`
   carve-out per `bug-privacy-scan-final-report-gitignore-blindspot`).
2. Confirm `clean` status. If not clean, `privacy_redact()` and re-scan.
3. This is the mandatory final step before the session is considered done.

Expected artifact: `privacy_scan()` reports `clean`.

---

## DISPATCH ORDER

dftracer-system-detect, dftracer-session-setup, dftracer-build-app, dftracer-build-dftracer, dftracer-annotator, dftracer-build-smoke, dftracer-tracer (baseline), dftracer-analyzer, dftracer-diagnoser, dftracer-optimizer, dftracer-tracer (N-node validation), dftracer-report, dftracer-privacy-guard

### STEP 7 Progress Update (2026-07-24 23:49)

- **Job ID**: fMW8Uwxj1
- **Nodes**: tuolumne[1039-1040] (2 nodes, 8 GPUs total)
- **Fix Applied**: Ray cluster setup (hardcoded localhost → dynamic address extraction)
- **Status**: Running, 1+ minute elapsed, no errors yet
- **Next**: Wait for job completion, then validate traces (dataset I/O, event counts)

---

## STEP 10 RESULT (in progress, 2026-07-25)

Diagnosis (STEP 9): COMPUTE-bound. POSIX I/O 0.68s/265.4s (0.26%, 21MB @ 31MB/s);
Ray/DDP comm 19.7s (7.4%); ~92% GPU forward/backward compute.

Four-dimension checklist (Pipeline Policy #14 — all four walked, verdict recorded
even where negligible):

- **COMPUTE (applied, opt1)**: `train_func_per_worker`'s forward pass (model call +
  loss) used pure fp32 with no `torch.autocast`/GradScaler anywhere in the original
  loop (grepped `annotated/src/molformer_ray_descriptors.py` — zero hits for
  autocast/fp16/bf16/GradScaler). On MI300A this is the highest-confidence, lowest-
  risk win: wrapped the forward pass (model + regression head + loss) in
  `torch.autocast(device_type="cuda", dtype=torch.bfloat16)`, backward/optimizer.step
  left in fp32 (bf16 has fp32's exponent range, no GradScaler needed, unlike fp16).
  Applied in `annotated/src/molformer_ray_descriptors_opt1.py`, run via
  `scripts/opt1_runner.sh` (byte-identical to baseline_runner.sh except dataset dir
  opt1, opt1 entry script, opt1 trace paths). Batch size (128) and DataLoader/shard
  worker count (Ray Data default) left unchanged — no diagnosed evidence pointed at
  either.
- **COMMUNICATION (not applied, documented negligible)**: DDP is Ray Train's default
  `prepare_model()` wrapping (standard `find_unused_parameters=False`, default bucket
  size 25MB) — no unused-parameter branches exist in `MolFormerWithRegression.forward`
  (single straight-through path), so `find_unused_parameters=True` is not needed and
  would only add overhead. Comm is 7.4% of wall time and is Ray Data object-store /
  gradient-allreduce traffic on an 8-GPU/2-node job — not large enough relative to the
  92% compute share to prioritize a comm-side change this session. Verdict: negligible
  given the shape of the bottleneck; no candidate met the bar for a code change.
- **I/O (not applicable, documented)**: POSIX I/O measured at 0.68s of 265.4s total
  job time (0.26%), 21MB total moved at 31MB/s (STEP 9 diagnoser output, corroborated
  by dfanalyzer `dlio` preset layer breakdown). Walked `dftracer-io-optimization`
  checklist L1 (app-level buffering/prefetch — dataset is fully read once via Ray
  Data's `read_csv`, already the entire I/O cost) / L2 (readahead hints — moot at
  21MB total) / L3 (filesystem striping — moot at this size): none applicable. Verdict:
  I/O optimization would be un-measurable noise against a 265s compute-dominated run;
  explicitly not applied, not silently skipped.
- **MEMORY (not applied, documented)**: Activation memory for MoLFormer-XL (~44M
  params encoder + small regression head) at batch_size=128, sequence lengths typical
  for SMILES tokens, comfortably fits MI300A's 128GB HBM per APU (confirmed no OOM /
  no `psutil.virtual_memory().percent > 90` cache-clear triggers logged in the
  baseline run's `07_tracer_baseline_run.log`). Gradient checkpointing would trade
  compute (recompute of activations) for memory headroom this workload does not need
  and would REGRESS compute time, the exact dimension already identified as the
  bottleneck — not applied. Verdict: not applicable; memory is not the constraint.

opt1 launched: `flux proxy f3Mwh7sKgyd1 flux submit -N2 -n2 -c1 -o spindle.level=off
scripts/opt1_runner.sh` (job id within proxy: f6e9nUBC3Z, on the pre-existing live
32-node pbatch allocation `f3Mwh7sKgyd1`, ~12h remaining at launch — no new allocation
requested). Same 2-node/8-GPU scale, same 4-epoch/128-batch config as baseline.
Result and comparator numbers to be appended once the run completes.

Known caveat carried from STEP 9 diagnosis: baseline's loss went NaN partway through,
suspected root cause `pubchem_descriptor_stats.npz` computed on a different/earlier
dataset than the current 5000-row real CSV (no `pubchem_stats/*.npz` intermediate
files present in the workspace to recompute it from `calc_stats.py`). NOT fixed this
session — recomputing would require re-running descriptor extraction over the full
dataset from scratch, out of proportion to a wall-clock/throughput compute-optimization
comparison, which remains valid regardless of the NaN loss curve. opt1 will exhibit the
same NaN behavior; only wall-clock/throughput are used for the STEP 10/11 comparison.

### STEP 10 MEASURED RESULT (2026-07-25, honest — no fabricated win)

opt1 (bf16 autocast) run completed successfully on the third submission attempt
(first two hit unrelated Ray cluster flakiness on this shared allocation — see
changelog). 20/20 training iterations completed, 2-node/8-GPU, identical config to
baseline (batch_size=128, 4 epochs configured, run capped/observed at 20 iterations
same as baseline).

| Metric (dfanalyzer `dlio` preset, cluster_n_workers=8) | Baseline | opt1 (bf16) | Delta |
|---|---|---|---|
| Job Time (s)            | 265.4  | 275.4  | **+3.8% (slower)** |
| Communication time (s)  | 19.7 (7.4%) | 20.1 (7.3%) | ~flat |
| POSIX I/O time (s)      | 0.68 (0.26%) | 0.97 (0.35%) | ~flat, both negligible |
| Training-loop wall clock ("Total running time") | ~4min9s–4min26s (249-266s) across baseline reruns | 4min33s (273s) | slower |
| training_iteration=20 time_total_s | ~231–247s (baseline reruns) | 252.6s | slower |

**Honest verdict: the bf16-autocast compute optimization did NOT produce a measured
speedup on this workload at this scale — job time was ~3.8% SLOWER than baseline,**
not the expected win. This is reported as-is, not adjusted or re-run selectively to
find a favorable number. Plausible explanations (not further isolated this session,
noted for future work): (1) autocast's per-op dtype-cast overhead on a relatively
small transformer (MoLFormer-XL encoder + a 3-layer regression head) at batch_size=128
may exceed the matmul-throughput gain at this scale; (2) run-to-run variance on a
shared/contended 32-node allocation (opt1 was the 3rd submission attempt after two
Ray-cluster-bringup failures consumed allocation/network activity on the same two
physical nodes); (3) MI300A's bf16 GEMM path may not be meaningfully faster than fp32
for this op mix. Communication (7.3-7.4%) and POSIX I/O (0.26-0.35%, both ~21MB) are
unchanged and remain correctly diagnosed as non-bottlenecks, confirming the STEP 9
diagnosis was directionally right (this IS compute-bound) even though the specific
compute optimization tried did not pay off.

Given the mandate to apply "the 1-2 highest-confidence candidates" and report honestly
rather than force a positive result, STEP 10 concludes: mixed precision was the
correct highest-confidence hypothesis to test given the compute-bound diagnosis and
zero prior mixed-precision usage, but the controlled A/B measurement shows it is NOT
a net win for this specific model/batch-size/hardware combination. No other compute/
comm/memory candidate met a higher confidence bar this session (see checklist verdicts
above). Recommendation for a future session: try larger batch size (better amortizes
autocast overhead and improves GPU utilization) or torch.compile() before further
precision experiments.

Traces: `<WS>/opt1/traces/raw/` (3 attempts; only the 3rd, successful attempt's files
are the ones analyzed — first two attempts' partial/empty trace files were regenerated
into the same directory across `find -mindepth 1 -delete` cleans between attempts, so
`<WS>/opt1/traces/raw/` contains only the successful run's traces), compact at
`<WS>/opt1/traces/compact/`. Script: `<WS>/scripts/opt1_runner.sh`. Also hardened
`<WS>/scripts/baseline_runner.sh`'s worker address-file wait (60s -> 180s + ls-refresh)
after diagnosing a real Lustre cross-node dentry-cache staleness bug that caused the
first opt1 attempt to strand the cluster at 4/8 GPUs (see changelog).

### STEP 11 RESULT (2026-07-25) — 4-node/16-GPU validation of opt1

Used the same live 32-node allocation `f3Mwh7sKgyd1` (no new allocation requested).
`scripts/opt1_4node_runner.sh` (opt1 bf16-autocast entry script, scaled to
`--nodes=4`, `ScalingConfig(num_workers=16)`) submitted via
`flux submit -N4 -n4 -c1 -o spindle.level=off` — succeeded on the FIRST attempt (all
16 GPUs registered immediately, no repeat of STEP 10's transient bring-up flakiness).
Completed the full 4-epoch config in 12 iterations (fewer iterations than the 2-node
runs' 20, because 16 workers finish each epoch's shard in fewer steps — this is
expected and the correct basis for comparison is total wall time to complete all 4
epochs, not raw iteration count).

| Metric (dfanalyzer dlio, cluster_n_workers=8) | opt1 2-node/8-GPU | opt1 4-node/16-GPU | Delta |
|---|---|---|---|
| Job Time (s)           | 275.4 | 309.9 | **+12.5% slower at 2x scale** |
| Communication time (s) | 20.1 (7.3%) | 70.5 (22.8%) | comm share more than 3x |
| POSIX I/O (s / MB)     | 0.97 / 21MB | 1.0 / 46MB | still negligible (<1%) |
| Total running time (wall) | 4min33s (273s) | 5min7s (307s) | slower |

**Honest verdict: scaling this workload from 2 to 4 nodes made it SLOWER, not faster**
— negative scaling efficiency for this dataset size (5000 rows). Communication's
share of wall time more than tripled (7.3% -> 22.8%), consistent with Ray/DDP
collective and object-store overhead not amortizing over a dataset this small when
split across more workers (fixed per-epoch dataset-read + cluster-bringup costs stay
roughly constant while useful compute-per-worker shrinks). This corroborates, rather
than contradicts, the STEP 9 compute-bound diagnosis at the ORIGINAL 2-node scale:
the workload's absolute dataset size (5000 rows) is simply too small to benefit from
horizontal scale-out past 2 nodes/8 GPUs — a workload-sizing conclusion, not a defect
in the bf16 optimization itself. No larger allocation or additional node count was
requested per the 4-node cap in the task instructions.


### STEP 10a RESULT: dftracer-optimizer-compute (2026-07-25)

**Headline: NO compute optimization produced a measurable win. The apparent -35% was a
stale-baseline artifact, caught by an interleaved control replicate.**

- **Structural bound (reframes the diagnosis).** Of ~256s trainer.fit() wall, ~190s (74%)
  elapses BEFORE training iteration 1 (Ray Train actor bring-up + per-worker HF model load +
  import graph on 8 actors). Only ~40-65s (~25%) is the 20-iteration training loop. Measured
  across 8 runs: startup_to_it1 = 186,190,194,195,186,203,197,194 s. The diagnosis's
  "92% GPU compute" is a share of TRACED APP TIME, not wall clock. => the entire compute
  dimension is capped at ~25% of wall. The next optimization pass should target the ~190s
  startup path, not compute.
- **opt3 (bf16 autocast, MI300A native bf16): NO CHANGE (0.0%).** Training phase 40s vs
  matched-window unmodified baseline 40s. Likely launch/CPU-collate-bound, not FLOP-bound.
- **opt2 (NaN correctness fix): timing-neutral, correctness WIN.** 20/20 iterations, zero NaN.
- **NaN root cause (dispatch hypothesis DISPROVEN).** Not a stats.npz/dataset mismatch --
  pubchem_descriptor_stats.npz is column-aligned with rdkit._descList AND the CSV header and
  agrees statistically (median |z| 0.15). Real cause: pubchem_filtered.csv itself holds 1220
  NaN + 6 inf cells in the RDKit Gasteiger partial-charge descriptors. Fix = torch.nan_to_num
  after normalization. DO NOT recompute the stats file.
- **Measurement hazard for later steps.** Baseline replicates from the 09:20-10:15 window give
  TRAIN=[63,65,65,68,70]s; everything in the 11:30-11:55 window gives 40-42s REGARDLESS OF CODE
  (including an unmodified baseline). Never compare across windows -- always interleave a
  control replicate. Pooling both windows inflates baseline CV from ~4% to 16.2%.
- **L3 GPU frequency: NOT APPLICABLE on Tuolumne.**
  /sys/class/drm/card*/device/power_dpm_force_performance_level reads 'auto' and is
  NOT-WRITABLE as non-root (verified on a compute node inside the allocation); rocm-smi is not
  on PATH even after module load rocm/6.2.1.
- **Artifacts.** Variant sources annotated/src/molformer_ray_descriptors_opt{2,3}.py; runners
  scripts/opt{2,3}_runner.sh; traces opt2/traces/{raw,compact} and opt3/traces/{raw,compact}
  (15 raw files / 2 compact chunks each); logs artifacts/10_tracer_opt{2,3}_run.log.
  dataset/opt2 and dataset/opt3 are Lustre symlinks (policy 11).
- **Gotcha for future variants:** <WS>/<run>/traces/raw MUST exist before launch, else dftracer
  silently drops every trace ("unable to create log file", 0 files) and the run is untraced.
  Call session_get_run_paths + mkdir -p first.
- **Also:** dataset/opt1 was created as a real dir on WS2/NFS instead of a Lustre symlink; the
  head->worker head_address.txt handshake then failed (worker timeout 60s, only 4 of 8 GPUs
  registered). Variant dataset dirs MUST be Lustre symlinks.

## STEP: dftracer-tracer (fresh baseline_4node run, 2026-08-02)

**Result: SUCCESS.** A brand-new `baseline_4node` run (distinct run_name from the original
2-node `baseline`) completed a full 4-node/16-GPU training run end-to-end and produced a
compacted trace with all expected event categories. This required root-causing and fixing
six distinct, previously-undiagnosed bugs (STEP 7's original "unresolved as of 2026-07-25"
stall was one of them) — full technical writeup in the `software-molformer` skill (point 5
onward), not duplicated here; summary only:

1. `dataset/baseline_4node` wasn't a Lustre symlink by default (new run_name gotcha).
2. HF model cache must be on PFS (`/p/lustre5/.../ray_molformer/hf_cache`), never `$HOME`.
3. App pins a specific HF revision (`7b12d946c18...`) that a plain `hf download` (no
   `--revision`) never fetches — every actor failed deterministically, misread as "1 flaky
   actor of 16" because Tune only surfaces the first failure.
4. `transformers`' dynamic-module code cache has a genuine concurrent first-write race across
   actors (matches upstream transformers#27421) — fixed by a single-process pre-warm before
   the concurrent run.
5. The worker join retry-loop's success check grepped for a string Ray's CLI never prints
   ("Successfully started Ray runtime" vs the real "Ray runtime started.") — every join was
   incorrectly treated as failed, inflating apparent GCS registration flakiness. A join-stagger
   delay added while misdiagnosing this was removed again once the real bug was found.
6. `default_worker.py`'s dftracer HIP-tracing auto-init patch called `initialize_log()` before
   `torch` was ever imported in that process — fixed with a bare `import torch` guard right
   before the init call (session-venv-local patch to
   `ray/_private/workers/default_worker.py`).

**Also configured**: full-run `PP` (torch-profiler-bridge) coverage — `repeat=1` (required,
`repeat>=2` crashes per `bug-dftracer-torch-profiler-rocprofiler-conflict`) with
`wait=0, warmup=0, active=1_000_000` so PP events cover every training step, not a sampled
window. Ported the profiler bridge from `molformer_ray_descriptors_v2.py` (which had it) into
`molformer_ray_descriptors.py` (the actually-run v1 script, which didn't).

**Outcome**: 12 training iterations completed, `HEAD NODE - exit: 0`, all 4 workers exited
cleanly. `loss=nan` from iteration ~10 onward is an independently-known, pre-existing
characteristic of this UNMODIFIED baseline code (see STEP 10a's NaN root-cause finding) — not
caused by any of the six fixes above. Compacted trace at
`<WS>/baseline_4node/traces/compact/` (225MB, 56 chunks, ~27.3M events). Verified event
categories present (direct gzip+json scan across ALL chunks, since `dftracer_stats --report
categories` has its own separate bug — see `bug-dftracer-stats-categories-zero-events`):
`HIP_RUNTIME_API` 22.5M, `KERNEL_DISPATCH` 3.0M, `POSIX` 1.15M, `STDIO` 414K, `dftracer` 217K,
`PP` 9.3K, `MEMORY_COPY` 4.9K, `comm` 3.2K, `molformer_ray_descriptors` 324, `SCRATCH_MEMORY`
36.

**Known gap, not fixed this session**: the `dftracer_service` node-counter daemon produced no
output for this run (silent no-op despite `DFTRACER_ENABLE`/`DFTRACER_LOG_FILE` both set
correctly in the inline invocation) — root cause not isolated for the Ray multi-node case.
This is a separate supplementary trace stream from the app's own event categories above, which
are all present and complete.

**Allocation churn note**: this run required 12 relaunch attempts across 4 different flux
allocations (2 pdebug 1h allocations expired mid-debugging before the fixes landed) — normal
given the number of distinct bugs found, not a sign of remaining instability. The final
successful attempt, once all 6 fixes + PP full-coverage were in place, succeeded on its first
try.

## STEP (2026-08-02): dftracer-analyzer — baseline_4node bottleneck diagnosis

**Trace quality (TOOL FINDING, `event_count`):** `mcp__dftracer__event_count` on
`<WS>/baseline_4node/traces/compact/` returned `27,313,634` events, matching the expected
~27.3M and `dftracer_info`'s `Total Lines: 27313634` exactly. `Valid Events: 0` in the
`dftracer_info` summary is the known `bug-dftracer-stats-categories-zero-events` tool bug
(not a data-quality problem) — trace is confirmed complete and not truncated/hot-function-
dominated (17,369 unique files, 16 processes / 4 nodes matching the 4-node/16-GPU run).

**Analyzer (TOOL FINDING, `analyze(analyzer_preset="generic", cluster_n_workers=8)`):**
`cluster_n_workers=32` failed with `DFTUtilsError: Resource temporarily unavailable` (thread
exhaustion — 32 workers x 192 threads each blew past the node's fork/thread limit while
auto-building the trace index); dropping to `cluster_n_workers=8` succeeded cleanly. Diagnoser
tool ran successfully against the checkpoint (16 findings, not the 0-observation failure mode)
but every finding was a generic `<layer>_ops_slope` "investigate" motif (activity-rate slope
per custom `dft_event_logging` category) since the `generic` preset auto-discovers layers per
distinct `cat` value rather than semantic POSIX/DLIO categories — not directly actionable, so
bottleneck ranking below is by aggregated **time** from the analyzer's own Layer Breakdown
table (per the "rank by time, not event count" rule), not from the diagnoser's severity labels.

**Layer Breakdown (cumulative seconds across all 16 processes, NOT wall-clock — layers overlap):**

| Layer | Time (s) | Ops | Notes |
|---|---|---|---|
| App (all annotated spans, nested) | 5621.8 | 70.3M | overarching wrapper, not itself actionable |
| PyTorch Profiler (`PP`, full-coverage this run) | 2286.2 | 23,932 | actual training-step compute, now covers every iteration (not sampled) |
| `molformer_ray_descriptors` (app-region) | 1864.4 | 837 | ~2.23s/event — Ray actor bring-up / HF model load / descriptor computation, i.e. the STEP 10a "startup" phase |
| `hip_runtime_api` | 979.5 | 58.19M | ROCm HIP runtime API call overhead |
| `kernel_dispatch` | 588.9 | 7.73M | GPU kernel launches |
| `comm` | 306.9 | 8,074 | collective/comm ops |
| POSIX | 78.0 | 2.83M | 2047.6 MB total, 26.235 MB/s aggregate BW, ~1 KB avg transfer (small-I/O pattern) |
| STDIO | 4.2 | 1.02M | negligible |
| `scratch_memory` | 0.22 | 93 | negligible |
| `memory_copy` | 0.02 | 12,594 | negligible |

**Ranked bottleneck list (I/O -> comm -> mem -> compute canonical order, severity by relative
magnitude within this trace):**

1. **Compute — CRITICAL.** `PyTorch Profiler` (2286.2s) + `hip_runtime_api` (979.5s) +
   `kernel_dispatch` (588.9s) = 3854.6s cumulative GPU-side compute/runtime-overhead time, the
   single largest contiguous bucket once bring-up is separated out. Dominant bottleneck by time.
2. **Compute/bring-up — CRITICAL, confirms prior finding.** `molformer_ray_descriptors`
   (1864.4s cumulative, 837 events at ~2.23s each) is comparable in magnitude to the entire
   PyTorch Profiler training-loop bucket (2286.2s) — this is the Ray actor bring-up + per-worker
   HF model load + import graph identified in STEP 10a. **The "startup-dominated, not
   compute-bound in the way originally framed" finding HOLDS at 4-node scale with full PP
   coverage**: bring-up (1864s) is ~45% the size of the real training compute bucket (2286s) on
   a per-event-cumulative basis, i.e. still a first-order cost, not a rounding error.
3. **Communication — MEDIUM.** 306.9s cumulative, small relative to compute (~8% of the
   PP+hip+kernel_dispatch bucket) — consistent with STEP 11's finding that comm share triples
   under scale-out pressure but starts from a small base at 4-node/16-GPU when NOT combined with
   an under-scaled dataset optimization variant (this is the plain baseline, not an opt1 variant).
4. **I/O — LOW.** POSIX 78.0s / 2.83M ops, ~1 KB avg transfer size (small-I/O pattern, 26.235
   MB/s aggregate bandwidth) but total time is negligible (~1.4% of the PP+hip+kernel_dispatch
   compute bucket, ~2% of bring-up). Not a real target — any I/O-layer optimization (buffering,
   ROMIO, striping) would have near-zero app-wall-time impact here; this matches the general
   Python/Ray-interpreter-import-traffic caveat in `dftracer-io-optimization` (small-I/O op
   storms in framework-heavy stacks are usually import/metadata traffic, not app data access —
   not independently verified by path-resolution this pass, flagged for the optimizer to check
   if I/O is ever considered).
5. **Memory — TRIVIAL.** `memory_copy` (0.02s) and `scratch_memory` (0.22s) are both negligible;
   no memory-bound signal in this trace.

**Comparator (2-node baseline vs 4-node baseline_4node) — NOT COMPLETED.**
`mcp__dftracer__comparator` failed at the tool level (non-zero exit from the underlying
`dftracer_comparator` binary) on two attempts — first with a 6-clause `OR` query (a known DSL
compound-query gotcha per `dftracer-trace-utils`), then with no query filter at all
(`group_by_dims="cat"` only) — both failed identically. Not root-caused this pass since it is
secondary to the standalone baseline_4node diagnosis (the primary deliverable) and the flux
allocation used (`f3Ppf64XiCgw`) was running low on remaining time. **Flagged as a known gap for
the next agent that needs the 2-node-vs-4-node delta** — retry with a fresh allocation and,
if it still fails, treat as a tool bug to fix rather than a data problem (event_count and
analyze both succeeded cleanly against both trace directories' individual chunks in this
session, so the directories themselves are not the problem).

**Allocation notes:** the session's originally-planned allocation (`f3Ppem8DiFzP`, 4-node) was
in `SCHED` (not yet running) when this step started; used a different live 8-node `pdebug`
allocation (`f3Ppf64XiCgw`) instead per the "any other currently-live allocation" fallback
policy. `cluster_n_workers=32` on `analyze()` triggers thread-limit exhaustion on this trace
size/node combination — use `cluster_n_workers=8` for baseline_4node-sized (27M event, 56-chunk)
traces on this system, a new caveat beyond the existing "never `cluster_cores`" rule in
`feedback_analysis_parallel_workers`.

## STEP 10b (2026-08-02): dftracer-optimizer-io — path-verified I/O attribution (PROPOSAL-ONLY)

**Priority question ANSWERED.** Resolved every POSIX/STDIO event's `fhash` against the trace's
own `FH` metadata records (18,173 entries) across all 56 `baseline_4node` compact chunks;
1,561,322 `ph=1` I/O events, only 30 with an unresolved fhash. Script + full output:
`<WS>/artifacts/step10b_io_path_attribution.log`.

| Path class | Ops | Time (s) | Share of I/O time |
|---|---|---|---|
| venv `site-packages` (Python module imports) | 691,255 | 107.31 | **81.3%** |
| `/proc`, `/dev`, `/etc`, ancestor path-walk | 290,320 | 2.40 | 1.8% |
| app dataset (`dataset/baseline_4node` -> Lustre) | 2,823 | 1.66 | 1.3% |
| HF cache (`hf_cache/hub` + `/modules`, Lustre) | 6,444 | 0.88 | 0.7% |

Top packages by time: `transformers` 212,328 ops/39.04s, `pandas` 62,049/15.81s, `ray`
127,738/15.47s, `torch` 31,458/13.10s, `scipy` 70,104/11.30s, `torchvision` 25,272/4.79s.

**Root finding: the venv is on NFS.** `stat -f <WS>/install/venv` -> `Type: nfs` (`/usr/WS2`),
while `dataset/baseline_4node` and `hf_cache` are correctly Lustre symlinks. The entire
small-op storm is Python import traffic against NFS.

**Verdict on the bring-up hypothesis: MATERIAL BUT NOT DOMINANT.** 107.3s cumulative import
I/O across 16 processes = ~6.7s/process, against a 1864.4s cumulative bring-up bucket ->
import I/O bounds at **<=5.8% of bring-up, <=1.9% of app wall**. Bring-up is CPU-side Python
bytecode execution + model construction, not I/O wait. **Do not chase I/O to fix bring-up.**

**HF pre-warm is measurably effective** (0.7% of I/O time) — no further work needed there.
Ray object spill does not appear in the top-30 by either op count or time.

Nothing applied (proposal-only pass, shared-tree race with the concurrent compute subagent).
Two `opt_kb_record` entries written (path attribution; HF pre-warm verification).

---

## STEP 10 MERGED RESULT (2026-08-02): four-dimension optimization pass on `baseline_4node`

Second optimization pass, run against the fresh 4-node/16-GPU `baseline_4node` diagnosis
(27.3M events, 56 chunks). All four component subagents dispatched per Pipeline Policy #14.
Does NOT supersede STEP 8-11 (prior 2-node pass) — those results stand.

**Headline: negative result. No measurable optimization was found applicable at 4-node scale
beyond what was already known from the 2-node work.** Two variants were applied and measured;
both were null. This is a legitimate, evidence-backed negative result, not a failure to search.

### Dispatch mode

Compute owned all source edits, runner scripts and job launches. I/O, communication and memory
ran in PROPOSAL-ONLY mode against a shared `annotated/` tree, per `feedback-shared-source-tree-race`
(concurrent edits to a shared annotated tree previously crashed a live DDP job with an NCCL
"remote process exited" error).

### Merged proposal / verdict table

Sorted by weighted score (0.5*potential app + 0.5*potential system). Potential and Actual shown
side by side; Actual is em-dash where nothing was applied.

| # | Component | Level | Strategy | Potential App | Potential Sys | Actual App | Actual Sys | Score | Citation | Status / Why |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Compute | L2 | opt5 — target PyTorch's bundled ROCm libs on NFS to cut the 58.9s hipModuleLoad code-object relocation | 19% | 0% | +1.1% (null, within noise) | 0% | 9.5 | session (opt5 measured) | APPLIED, MEASURED NULL |
| 2 | Compute | L2 | opt4 — same lever, mistargeted at the system ROCm module libs instead of PyTorch's bundled ones | 19% | 0% | 0% | 0% | 9.5 | session (opt4 measured) | APPLIED, MEASURED NULL (mistargeted; superseded by opt5) |
| 3 | Communication | L1 | Reduce per-rank work skew at the DDP allreduce barrier: balanced sharding of the 5000 rows across 16 ranks, `drop_last=True`, deterministic per-rank batch counts | 10% | 8% | — | — | 9.0 | PyTorch DDP docs; Li et al., VLDB 2020 | NOT APPLIED (proposal-only mode) |
| 4 | I/O | L1 | Stage the venv to node-local storage or ship it as a squashfs/container image so 16 actors stop import-scanning NFS | 4% | 6% | — | — | 5.0 | session (path-verified attribution) | NOT APPLIED (proposal-only mode; bounded <=5.8% of bring-up) |
| 5 | Communication | L3 | Explicit RCCL tuning (NCCL_ALGO / NCCL_PROTO / topology + NIC selection) instead of defaults | 2% | 5% | — | — | 3.5 | AMD RCCL docs (ROCm) | NOT APPLIED — root cause measured as skew, not bandwidth |
| 6 | Communication | L2 | DDP `bucket_cap_mb` tuning + `gradient_as_bucket_view` for compute/comm overlap | 3% | 4% | — | — | 3.5 | PyTorch DDP performance docs | NOT APPLIED (proposal-only mode) |
| 7 | Communication | L1 | Increase per-rank work (bigger dataset/batch) rather than adding ranks | 0% | 0% | — | — | 0.0 | session (2->4 node +12.5% regression) | NOT APPLICABLE — changes the workload definition, not an optimization |
| 8 | Compute | L1 | bf16 autocast | 0% | 0% | 0% | 0% | 0.0 | session (opt1 +3.8%, opt3 0.0%) | NOT APPLICABLE — already measured null twice |
| 9 | Memory | L1 | Shared / broadcast / mmap model weight loading across per-node actors | 0% | 0% | — | — | 0.0 | session (trace grep) | NOT APPLICABLE — no target exists; zero HF checkpoint weight files are ever read, model is built from config |
| 10 | Memory | L3 | NUMA binding, cache blocking, object-store sizing, gradient checkpointing | 0% | 0% | — | — | 0.0 | session (4.6% peak node mem; memory_copy 0.02s) | NOT APPLICABLE — not memory-bound |

### Per-dimension findings

**COMPUTE (2 variants applied, both null).**
The headline hypothesis carried into this pass — that the 1864.4s `molformer_ray_descriptors`
bucket was Ray actor bring-up plus 16x redundant HuggingFace model loading — was
**DISPROVED by measurement**. The real one-time cost is **58.9s of `hipModuleLoad`**, i.e. GPU
code-object relocation as the ROCm runtime loads and relocates device binaries, and it was
demonstrated to be immovable from user space. `opt4` attacked the wrong libraries (system ROCm
module rather than PyTorch's bundled ROCm libs) and measured null; `opt5` corrected the target
to PyTorch's bundled ROCm libs on NFS and also measured null (+1.1%, within noise). bf16 and
horizontal scale-out were already confirmed dead ends from the 2-node pass and were not retried.

**COMMUNICATION (proposal-only, but produced the most valuable re-attribution of the session).**
The 306.9s originally attributed to the `comm` category was **not** the real collective cost.
RCCL/NCCL collective time is **541.7s** and was **mislabeled as compute**, hidden inside
`kernel_dispatch`. This materially revises the diagnosis: the compute bucket was overstated and
communication understated. The root mechanism is **rank/straggler skew at the barrier — 87% of
collective time sits in a long tail — not bandwidth**, which explains cleanly why the comm share
triples going 2-node -> 4-node on a 5000-row dataset. Four untried proposals carried forward.

*Trace-capture completeness note (resolved, not a bug):* only 12 of 16 traced processes emitted
`ncclDevKernel` events. This was verified directly against
`<WS>/artifacts/12_baseline_4node_dedicated_mgr_final.log`: `world_rank=0` through `world_rank=15`
are all present with correct node_rank/local_rank assignments (4 nodes x 4 local ranks). DDP
`world_size` is genuinely 16 and all 16 GPUs are real training participants. The missing events are
a **dftracer/HIP trace-capture completeness gap** (kernel-dispatch events not captured for 4 of 16
processes), **not** a training-configuration or world-size bug.

**I/O (proposal-only).** Path-verified: **81.3% of I/O time is Python import traffic against the
venv on NFS** (the ~2.83M ~1KB ops). Correctly bounded to **<=5.8% of the bring-up bucket**, so it
does NOT redirect the compute investigation — a useful negative guardrail. The HF cache pre-warm
was confirmed effective. No Ray object spilling found.

**MEMORY (proposal-only, nothing applicable).** Verified by trace grep that **zero HF checkpoint
weight files are ever read** — the model is constructed from config, so the "shared weight loading"
lever has no target at all. Not memory-bandwidth-bound (4.6% peak node memory usage).

### Best overall configuration

**Unchanged from `baseline_4node`.** No variant is recommended for adoption. No cross-component
conflicts arose (memory and compute did not contend over NUMA binding, since memory found the
dimension inapplicable).

### Honest limits of this pass

- Three of four dimensions ran proposal-only, so their candidates are unmeasured hypotheses, not results.
- The `comparator` MCP tool failed **3 separate times** this session across different query shapes.
  Treated as a real tool bug to fix, not re-attempted.
- `dftracer_service` node-counter produced no output for this Ray multi-node run, so there is no
  node-level counter data — memory and I/O conclusions rest on app traces alone.
- The 4-of-16 `ncclDevKernel` capture gap means collective timings are derived from 12 of 16 ranks.

### Recommended next step

Do NOT iterate automatically. The highest-value untried lever is now row 3 (straggler-skew
reduction at the DDP barrier), which follows directly from the communication dimension's
re-attribution and is the only remaining candidate with a double-digit potential estimate.
