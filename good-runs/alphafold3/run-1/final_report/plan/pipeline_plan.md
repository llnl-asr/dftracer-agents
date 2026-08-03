# DFTracer Pipeline Plan — af3/20260729_044330

## Overview

**App:** AlphaFold3 (AF3) inference, JAX/XLA (not PyTorch), Python 3.11, ~100 .py
files under `src/alphafold3/` + ~35 C++ pybind extensions under
`src/alphafold3/{structure,data,parsers}/cpp`.

**Session facts (already resolved — do not re-derive):**
- `run_id`: `af3/20260729_044330`
- `workspace` (WS): `$PROJECT_ROOT/workspaces/af3/20260729_044330`
- subdirs present: `performance, source, baseline, annotated, artifacts, scripts, tmp, dataset`
- `source_dir` (cloned): `<WS>/source` (cloned from `$VAST_ROOT/smol_pythonenv/af3`, ref `main`, HEAD `0248d47`)
- `dataset/` symlink target: `$LUSTRE_ROOT/af3_data` (Lustre PFS) — ALL app-written output goes here, never into `/p/vast1` or `/usr/workspace` (read-only, rule 11 hard constraint)
- `<WS>/baseline/{source,patches,traces/raw,traces/compact,scripts}` created by `session_get_run_paths(run_name="baseline")`
- System: **tuolumne**, AMD MI300A APU, Cray PE, ROCm, ROCm module `rocm/6.0.0`, `flux run` launcher, `sudo` unavailable.
- Module load order (system-detect): `craype-x86-trento, libfabric/match_SHS, craype-network-ofi, perftools-base/25.09.0, craype/2.7.35, PrgEnv-cray/8.7.0, flux_wrappers/0.1, xpmem/2.6.5, cce/20.0.0, cray-libsci/25.09.0, cray-mpich/9.0.1` — but AF3 additionally needs `python/3.11.5` (NOT the system default `python/3.13.2` — swap it).
- **CRITICAL LD_LIBRARY_PATH note (system-tuolumne skill):** `LD_LIBRARY_PATH` must include the CCE lib dirs AND `/usr/lib64` (for `libdl`) BEFORE calling `session_install_dftracer`/`session_build_annotated` — these MCP tools run in a separate process that does not inherit Bash-tool exports. Set:
  `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"`
  in every wrapper script BEFORE any dftracer install/build call, not just at run time.
- Do not reload StdEnv (S) — it's loaded by default.

**Reference scripts (READ-ONLY, copy into `<WS>/scripts/` and edit the copy, never the originals):**
- `$HOME/smol_workflow/01_af3/submit_batch.sh`
- `$HOME/smol_workflow/01_af3/run_af.sh`
- `$VAST_ROOT/smol_pythonenv/venv_af3.sh`

**Entry point:** `run_alphafold.py --json_path=<input.json> --output_dir=<dir> --flash_attention_implementation=xla`

**Runtime env:** `XLA_FLAGS="--xla_gpu_enable_triton_softmax_fusion=true --xla_gpu_triton_gemm_any=True"`, `module load rocm/6.0.0`, `module load python/3.11.5` (swap out `python/3.13.2`).

**Launch shape:** `flux batch -N 1 --exclusive`, bank `biomisc`, one structure per invocation (loop in `submit_batch.sh`).

**HARD CONSTRAINT (read-only source of truth):** `$VAST_ROOT/**` and
`$HOME/**` are STRICTLY READ-ONLY. NEVER write, mkdir, or
redirect output into them at any step. Inputs (JSON configs, MSA `.a3m` files)
are READ from their real vast1 paths — never via the workspace symlink (a prior
session lesson: apps must open data via the REAL PFS path, not a workspace
symlink, because traces record whichever path was actually opened). AF3's own
`--output_dir` must be redirected away from the stock `run_af.sh`'s
`OUTPUT_DIR=$data_dir` (which points into the ice4hpc sample dir) to
`<WS>/dataset/<run_name>/` (which resolves through the `dataset/` symlink onto
`$LUSTRE_ROOT/af3_data`). dftracer TRACES always stay under
`<WS>/traces/...` (or `<WS>/baseline/traces/...`), never on Lustre.

**Baseline scope (user-selected — do not re-ask):** two structures to isolate
MSA-size scaling —
- `1eby` (18MB MSA, ~3 min prior run) — small/fast probe
- `1e66` (80MB MSA, ~46 min prior run) — large/slow probe

Input JSONs (READ-ONLY, real vast1 paths):
`$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1eby.json`
`$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/1e66.json`
Each JSON references `unpairedMsaPath`/`pairedMsaPath` `.a3m` files under
`$VAST_ROOT/smol_workflow/msa_db` (read-only, pre-computed — MSA search
itself is NOT run in this configuration).

**The performance question this pipeline exists to answer:** the scientist
believes MSA *search* is the bottleneck, but MSA is pre-computed here. Prior
run-log data shows wall time scales with MSA **file size** (1eby 18MB→181s,
1a30 18MB→198s, 1c5z→189s, 1bcu 27MB→430s, 1bzc→426s, 1e66 80MB→2790s) while
GPU "model inference with 5 seeds" was only ~146s for 1eby. Hypothesis:
the dominant cost is CPU-side reading/parsing/featurizing of large `.a3m`
files (`src/alphafold3/data/`, `src/alphafold3/data/parsers/`,
`src/alphafold3/model/pipeline/`), not diffusion/GPU inference. Annotation
scope and diagnosis in this plan are aimed squarely at confirming or refuting
this — the data pipeline is the primary annotation target, model/pipeline
inference is the secondary comparison target.

**Tracing mode:** FUNCTION mode only (source-level `@dft_fn`/`_dft.log`
decorators compiled/imported directly), NEVER `LD_PRELOAD` (rule 13).

**Node-counter daemon:** every AF3 launch (smoke test AND the two baseline
runs) must be bracketed with `session_service_start` / `session_service_stop`
(rule 12) — one instance per node, pinned to a single core.

**Optimizer:** all four dimensions (`dftracer-optimizer-io`,
`-communication`, `-compute`, `-memory`) must be dispatched every session
(rule 14), even though this is a single-node, no-MPI-collectives workload —
`-communication` will likely report "not applicable, single process" but
still must walk its checklist and record the verdict explicitly.

**Cost/risk flags to surface to the user (see summary):**
1. Rebuilding the AF3 venv from the README (ROCm jaxlib/jax_rocm60 wheels +
   `jax_triton/__init__.py` patch) is itself a multi-step, potentially fragile
   install — budget real time/iteration for it, matching the exact recipe below.
2. The 1e66 baseline run is ~46 minutes uninstrumented; with FUNCTION-mode
   tracing overhead and the node-counter daemon it should still fit a single
   `pdebug`/`pbatch` 1-node allocation, but confirm allocation length with the
   user before submitting (allocation policy).
3. AF3's C++ pybind extensions (`structure/`, `data/`, `parsers/` cpp) need
   C++ annotation (`dftracer-annotate-cpp`) in addition to Python — both must
   be scoped and validated.
4. `--output_dir` MUST be overridden per run to `<WS>/dataset/<run_name>/`
   (Lustre via symlink) — the copied `run_af.sh` in `<WS>/scripts/` must have
   this changed from the stock `OUTPUT_DIR=$data_dir`, and this must be
   verified again in the tracer step before launch, not assumed fixed once.

## STEP 1: dftracer-session-setup

Session already exists (`af3/20260729_044330`) — do NOT create a new one; call
`session_status(run_id="af3/20260729_044330")` to confirm state (`source`
already cloned from `$VAST_ROOT/smol_pythonenv/af3`, ref `main`,
HEAD `0248d47`). This step's remaining work:

1. Detect build system for the Python package + C++ extensions
   (`pyproject.toml` / `setup.py` with pybind11 CMake extensions expected —
   confirm via `graph_query(run_id="af3/20260729_044330", question="build
   system setup.py cmake extension")` rather than reading files directly).
2. Copy the three read-only reference scripts into `<WS>/scripts/` (as
   `_ref_submit_batch.sh`, `_ref_run_af.sh`, `_ref_venv_af3.sh` — keep the
   `_ref_` prefix so nobody edits them thinking they're live) for later steps
   to derive working copies from. Do NOT edit the originals under
   `$HOME/...` or `$VAST_ROOT/...`.
3. Confirm `dataset/` symlink resolves to `$LUSTRE_ROOT/af3_data`
   (`session_status` already reports `structure_dataset_path`) — if missing,
   create it via the session tool, not manually.
4. Record HDF5/MPI needs: NONE — AF3 is single-process JAX/XLA, no MPI, no
   HDF5. Note this explicitly so later steps don't waste time chasing MPI
   toolchain matching.
5. Record canonical paths for downstream steps: `source_dir=<WS>/source`,
   `baseline` run paths from `session_get_run_paths(run_id, "baseline")`
   (`run_dir, source_dir, patches_dir, traces_raw, traces_compact,
   scripts_dir, dftracer_log_prefix` — already resolved above in Overview).
6. Self-learning: if the build system or repo layout differs from what's
   assumed here, update the `workload-af3` skill (create if absent) with the
   corrected facts — PROPOSE to the router/user before persisting.

Expected artifact: confirmation note in `pipeline_plan.md`/changelog of build
tool detected + scripts copied; no code changes yet.

## STEP 2: dftracer-build-app (venv rebuild — README recipe, exact steps)

This step's "build" is a from-scratch **venv rebuild into the session
workspace** — the user explicitly rejected reusing the shared
`$VAST_ROOT/smol_pythonenv/af3/af3env`. Target venv path:
`<WS>/baseline/af3env` (or `<WS>/af3env` if the build tool prefers session
root — confirm via `session_get_run_paths`, do not hand-build the path).

Exact recipe (from AF3 README, as given — follow verbatim, in order):

1. `module load python/3.11.5` (module swap away from any default python
   module active from system-detect).
2. `python3.11 -m virtualenv --system-site-packages <WS>/baseline/af3env`
3. `module load rocm/6.0.0`
4. Activate venv, then:
   `pip install -r <WS>/source/llnl-requirements.txt`
   `pip install --no-deps -e <WS>/source` (editable install of AF3 itself,
   `--no-deps` is load-bearing — do not drop it)
5. Install the three ROCm JAX wheels from
   `github.com/ROCm/jax` releases (exact versions):
   `jaxlib-0.4.34`, `jax_rocm60_pjrt-0.4.34`, `jax_rocm60_plugin-0.4.34`
   — download to `<WS>/tmp/` first, `pip install` from local wheel files.
6. Run AF3's `build_data` step (locate via
   `graph_query(run_id=..., question="build_data script data setup")` rather
   than guessing the path).
7. `pip install pandas==1.5.3 numpy==1.26` (exact pins — installed AFTER the
   jax wheels and build_data, order matters per README).
8. PATCH `<WS>/baseline/af3env/lib/python3.11/site-packages/jax_triton/__init__.py`:
   set `get_compute_capability = None` and `get_serialized_metadata = None`
   (these are functions/attrs being nulled out to disable a triton
   capability probe incompatible with ROCm — apply as a source patch, save a
   copy of the unpatched file to `<WS>/artifacts/jax_triton_init.orig.py`
   first for reference).
9. Verify: `python -c "import alphafold3; import jax; print(jax.devices())"`
   inside the venv on a GPU node — expect ROCm/HIP device(s) listed, not CPU
   or CUDA. This verification MUST run on an allocation with a GPU, not the
   login node.

Apply CLAUDE.md rule: **install env == run env**, same venv, same modules,
every subsequent step (tracer, smoke, optimizer runs) sources this exact
venv. dftracer installs into this SAME venv in STEP 3 — do not create a
second one.

Log everything to `<WS>/artifacts/02_venv_build.log`. If any wheel/version is
unavailable or the patch target differs from what's assumed, STOP and report
— do not silently substitute a different jax/jaxlib version, ROCm ABI
mismatches are a known failure mode (see `feedback-tuolumne-torch-rocm-wheel-required`,
analogous JAX risk).

Self-learning: record the exact working recipe (or any deviation found) to a
new `software-af3-jax-rocm` or `workload-af3` skill — PROPOSE before persisting.

Expected artifact: working `<WS>/baseline/af3env`, `jax.devices()` showing
ROCm GPU, patched `jax_triton/__init__.py`, build log.

## STEP 3: dftracer-build-dftracer

Install dftracer's Python bindings (`pydftracer`) into the SAME venv built in
STEP 2 (`<WS>/baseline/af3env`) — never a separate install
(`feedback-dftracer-aiml-venv`).

1. Before calling `session_install_dftracer`, set in the wrapper script (not
   just the Bash tool env, which the MCP process does not inherit):
   `export LD_LIBRARY_PATH="/opt/cray/pe/cce/20.0.0/cce/x86_64/lib:/opt/cray/pe/cce/20.0.0/cce/x86_64/lib/default64:/usr/lib64:${LD_LIBRARY_PATH}"`
2. `CC`/`CXX`: AF3 has no MPI dependency, so bind to the Cray compiler
   wrappers directly (`CC=cc`/`craycc`, `CXX=CC`/`craycxx` per module `cce/20.0.0`)
   rather than an MPI wrapper — confirm the exact compiler executable names
   via `system-tuolumne` skill before setting.
3. dftracer install ROCm/MPI env vars (per `feedback-dftracer-install-rocm-mpi`
   and `feedback-dftracer-install-env-vars`): dftracer's `setup.py` reads ENV
   VARS not `CMAKE_ARGS`. Enable HIP tracing ONLY if AF3 actually calls into
   ROCm/HIP directly from Python-visible C++ (it likely does not — JAX owns
   the GPU dispatch) — check via
   `graph_query(run_id=..., question="hip rocm direct call")` before
   enabling `DFTRACER_ENABLE_HIP_TRACING` (avoid the known false-positive bug
   where system ROCm presence alone triggered it).
4. No MPI needed for dftracer core here (AF3 is single-process) — skip MPI
   headers/version passing.
5. Verify: `python -c "import dftracer.dftracer"` inside `<WS>/baseline/af3env`
   succeeds, AND a smoke `.pfw` trace is non-empty (not just a zero exit
   code) — rule from `dftracer-install` skill RULE 5.

Log to `<WS>/artifacts/03_dftracer_install.log`.

Expected artifact: `pydftracer` importable inside `<WS>/baseline/af3env`,
verified non-empty `.pfw` from a trivial smoke annotation.

## STEP 4: dftracer-annotator (+ dftracer-annotate-python, dftracer-annotate-cpp)

**Decision point needing human confirmation:** file count before annotating.
Report the scoped file list (see below) to the user before writing any
annotations.

Scope, in priority order (aimed at confirming/refuting the MSA-size-scaling
hypothesis — do NOT blanket-annotate all ~135 files without first scoping to
the smoke-test's actual call path):

1. **Primary target (Python, CPU data path)** — everything under
   `src/alphafold3/data/` and `src/alphafold3/data/parsers/` (MSA/.a3m file
   reading, parsing) and `src/alphafold3/model/pipeline/` (featurization).
   Locate exact files via
   `graph_query(run_id="af3/20260729_044330", question="a3m MSA parsing
   featurize pipeline", budget=1200)` — do not grep manually.
2. **Secondary target (Python, GPU path)** — model inference / diffusion
   entry points, enough to bound "5 seeds" GPU time as a comparator (do not
   deep-annotate every layer — top-level inference call + diffusion loop
   entry/exit is sufficient).
3. **C++ pybind extensions** — `src/alphafold3/{structure,data,parsers}/cpp`
   (~35 files) via `dftracer-annotate-cpp`, using
   `DFTRACER_C_FUNCTION_START`/`END` (or C++ equivalent) macros — these are
   likely on the hot MSA-parsing path (fast string/structure parsing is
   commonly pushed to C++) so they matter for the hypothesis.
4. Python annotation rules: use `@dft_fn`/`_dft.log` decorator, innermost
   relative to `@classmethod`/`@staticmethod` (rule from
   `feedback-dft-log-classmethod-decorator-order`); skip trivial
   functions (`dftracer-annotate-python` SKILL.md Rule 6); never insert the
   dftracer import inside an open multi-line parenthesized import block
   (fixed at tool level, but re-verify output).
5. Run `session_run_smoke_test` (or `af3` equivalent) BEFORE and use its call
   graph to prune scope — annotate only files actually reachable from
   `run_alphafold.py` on the code path for `1eby.json`, not the whole
   repository.

Write annotated copies into `<WS>/annotated/` (never `<WS>/source/`, which
stays a pristine copy).

Validation: run `dftracer-validate-python` / `dftracer-validate-cpp` (lint —
every `_dft.log`/`FUNCTION_START` has a matching `END`, no corrupted
if/else or macro-heavy C code — see `bug-clang-add-braces-overlap-corruption`
class of issues) before declaring annotation done. GREP-VERIFY the
subagent's own write claims (a prior session hit a fabricated-report bug —
`bug_annotator_fabricated_report.md` — always confirm annotations are
actually on disk, not just claimed).

Self-learning: record AF3-specific annotation caveats (decorator order in
JAX-traced functions, any jit-wrapped function that can't carry a Python
decorator, C++ pybind boundary specifics) to `workload-af3` — PROPOSE first.

Expected artifact: annotated file list + line counts under `<WS>/annotated/`,
validation pass/fail per file, smoke-test-derived call-path file list used to
scope.

## STEP 5: dftracer-build-smoke

1. Build the annotated tree (`<WS>/annotated/`) inside the SAME venv
   (`<WS>/baseline/af3env`) — reinstall the editable package
   (`pip install --no-deps -e <WS>/annotated`) so the C++ extensions rebuild
   against the annotated C++ sources; pure-Python annotated files just need
   to be on `PYTHONPATH`/reinstalled editable.
2. `DFTRACER_INIT` mode: FUNCTION mode only (rule 13) — set
   `DFTRACER_ENABLE=1`, `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/smoke`,
   `DFTRACER_DATA_DIR` scoped to the smoke run's I/O (the small MSA file's
   directory + `<WS>/dataset/smoke/`), never `DFTRACER_DISABLE_IO`.
3. Smoke command: a SHORT AF3 run, NOT the full `1eby` (3 min) — use the
   smallest input available or truncate seeds/recycles if AF3 exposes such a
   flag, purely to prove the annotated build imports, runs, and emits a
   non-empty `.pfw`. `--output_dir=<WS>/dataset/smoke/` (Lustre via symlink,
   never vast1).
4. Bracket the smoke launch with `session_service_start`/`session_service_stop`
   even though it's just a smoke test (rule 12 says every launch).
5. Verify non-empty `.pfw` files land under `<WS>/baseline/traces/raw/`, not
   under `<WS>/dataset/` or Lustre.

Log to `<WS>/artifacts/05_build_smoke.log`.

Expected artifact: annotated build succeeds, smoke run produces non-empty
`.pfw` trace(s) under `<WS>/baseline/traces/raw/`.

**STATUS (2026-08-02, retry 2): PASSED.** Model-weight blocker from attempt 1
resolved (weights readable at `$VAST_ROOT/alphafold_data/params_af3/`,
AF3's own default `_DEFAULT_MODEL_DIR`, no `--model_dir` override needed).
Editable install into `<WS>/baseline/af3env` from `<WS>/annotated/source` was
already current from a prior attempt; no rebuild needed this retry.

Two real bugs were found and fixed in the annotated tree / venv during this
retry (both are STEP-2/STEP-4 class issues surfaced only once weights let the
run actually reach that code):
1. **Decorator-order bug (annotation, STEP 4 class):** `@_dft.log` was placed
   ABOVE `@functools.lru_cache`/`@functools.cache` in 3 functions
   (`model/pipeline/inter_chain_bonds.py` x2, `data/pipeline.py` x1) -- dft.log
   wrapped the cache wrapper object, and `inspect.getfullargspec` cannot
   introspect a `functools._lru_cache_wrapper`, raising
   `TypeError: unsupported callable` at import time. Fix: cache decorator
   OUTERMOST, `@_dft.log` innermost (closest to `def`) -- same principle as
   the existing `@classmethod`/`@staticmethod` ordering rule, extended to
   any caching decorator.
2. **jax_triton ROCm patch was dead code (STEP 2 class):** the patch had been
   APPENDED after the stock `try/except AttributeError: raise ImportError`
   block in `jax_triton/__init__.py`, but the `raise` aborts module import
   before execution ever reaches the appended override lines. Fix: the
   override (`get_compute_capability = None`, `get_serialized_metadata =
   None`) must REPLACE the try/except block, not follow it.

Smoke command actually used: `1eby` (no smaller/truncated fixture exists in
the sample set -- `1eby` at 18MB MSA is already the smallest/fastest baseline
input per the Overview's own selection). Full run completed in ~110s
(featurization 24.9s + inference 84.7s across 5 seeds), non-empty
162KB / 14723-event `.pfw.gz` trace at
`<WS>/baseline/traces/raw/smoke-15c9402e76051990-app.pfw.gz`, full AF3 output
correctly under `<WS>/dataset/smoke/1eby/` (Lustre via symlink).

**Tool-gap note:** `session_service_start`/`session_service_stop` (node-counter
daemon, rule 12) were not available as MCP tools to this step invocation --
only `session_run_smoke_test` was available for launching. The smoke run was
executed WITHOUT the node-counter daemon bracketing. STEP 6 (the real baseline
runs) must bracket with these tools if/when available, or this gap must be
flagged again before STEP 6.

**RETRY 3 (2026-08-02): re-verification after targeted re-annotation of
`data/pipeline.py` (Rule 9 pass) -- PASSED.** Annotator removed `@_dft.log`
from `DataPipeline.process()`/`process_protein_chain()` and added a
contextual `with DFTracerFn("Data", name="assemble_templates")` span plus
kept leaf-function decorators. Editable install already pointed at
`<WS>/annotated/source`; pure-Python change needs no C++ rebuild.

`session_run_smoke_test` with a raw command + hand-rolled `env_extra`
(missing Cray CCE lib paths) let AF3 run to completion but produced NO trace
file at all -- reproduces the known silent-no-op failure mode: dftracer's C
extension needs `cce/20.0.0/cce/x86_64/lib`, `.../lib/default64`,
`.../cce-clang/x86_64/lib`, and `/usr/lib64` on `LD_LIBRARY_PATH` or it
silently falls back to a NoOpProfiler with zero error. Fix: always launch AF3
via the existing wrapper `<WS>/scripts/env_af3.sh`, not a raw
`session_run_smoke_test` env_extra.

Re-ran via `<WS>/scripts/smoke2_run_af.sh` (sources `env_af3.sh`, targets
`smoke2`/`dataset/smoke2`). Exit 0, AF3 completed fully (5 seeds, correct
outputs under `dataset/smoke2/1eby/`). Non-empty trace at
`baseline/traces/raw/smoke2-5c5303322e663286-app.pfw.gz`, 14723 events --
same total as prior 14723 (coincidental; diffed by event name and confirmed
`DataPipeline.process`/`DataPipeline.process_protein_chain` spans correctly
dropped 1->0 each, with `FH`/`opendir` +1 each elsewhere as normal I/O
noise, not a regression). Node-counter daemon tools still unavailable to
this invocation -- gap remains open for STEP 6.

## STEP 6: dftracer-tracer

**Decision point needing human confirmation:** allocation choice (existing
JOBID vs new `flux batch`) and allocation length for the 1e66 run (~46 min
uninstrumented + tracing overhead + node-counter daemon — request at least
70 minutes to be safe). ASK the user before submitting, per CLAUDE.md
allocation policy — do not assume.

Two baseline runs, run_name = `1eby` and `1e66`:

For EACH structure:
1. Copy/derive a working `run_af.sh` from `<WS>/scripts/_ref_run_af.sh`,
   editing ONLY: (a) `OUTPUT_DIR` to `<WS>/dataset/<run_name>/` (create the
   subdir first — this is the rule-11 fix the hard constraint calls out
   explicitly; the stock script's `OUTPUT_DIR=$data_dir` must NOT be used
   as-is), (b) `--json_path` to the real vast1 path
   (`$VAST_ROOT/smol_workflow/pdbbind_casf2016_sample/01_af/<run_name>.json`,
   read directly, not through any symlink), (c) module loads
   (`python/3.11.5`, `rocm/6.0.0`), (d) activate `<WS>/baseline/af3env`,
   (e) `XLA_FLAGS` as given in Overview, (f) `DFTRACER_ENABLE=1`,
   `DFTRACER_LOG_FILE=<WS>/baseline/traces/raw/<run_name>`,
   `DFTRACER_DATA_DIR` covering the MSA db read path AND
   `<WS>/dataset/<run_name>/` write path.
2. `session_service_start` immediately before launch (one instance per node,
   pinned to a single core, rule 12).
3. Launch via `flux run -N 1 --exclusive` (single-node, no MPI ranks needed —
   AF3 is single-process) inside the allocation, using `run_in_background:
   true` if launched via a long-lived `flux proxy`.
4. `session_service_stop` immediately after the run completes.
5. Verify wall time roughly matches the prior-run-log expectation (1eby
   ~181-198s total run, 1e66 ~2790s) — a large deviation (e.g. cancelled or
   truncated run) must be caught here before analysis, not discovered later
   (`feedback-flux-alloc-verify-scale`).
6. `session_split_traces` on each run's `.pfw` output into
   `<WS>/baseline/traces/compact/<run_name>/`.

Expected artifact: two verified, complete trace sets
(`traces/compact/1eby/`, `traces/compact/1e66/`), each with matched
wall-time sanity-check against the known prior numbers, plus
`service_<hostname>.*` node-counter files alongside the `<run_name>.*`
application traces.

## STEP 7: dftracer-analyzer then dftracer-diagnoser

**STATUS (2026-08-02): dftracer-analyzer portion COMPLETE.** See
`pipeline_plan_changelog.md` for the full numbers. Summary for the
diagnoser:

- Trace quality: `event_count` matched expected counts exactly
  (1eby/1eby_r2 = 14,723; 1e66/1e66_r2 = 75,343); `analyze(preset="generic")`
  Trace Count off by 2 (14,721 / 75,341) -- 2 dftracer metadata events
  excluded from the layer view, immaterial. Replicates match primary runs
  within ~1-2% on every key duration (noise band established).
- `diagnose()` on the generic-preset checkpoint returns only generic
  `*_ops_slope` findings (event-count-vs-time trend scores per layer), not
  a per-function breakdown -- not useful for the hypothesis test on its own.
  Per-function duration breakdown was pulled by manual zcat+python
  aggregation (labeled MANUAL ANALYSIS) since the `view` MCP tool used by
  `dftracer-trace-utils` for this kind of query was not available to this
  agent's toolset.
- `dftracer_comparator` (both via MCP and via direct flux run) returns
  "Aggregation complete: 0 unique keys, 0 total events" for EVERY query
  tried (`cat == "alphafold3"`, `cat == "POSIX"`, `cat != "dftracer"`,
  default `POSIX OR STDIO`) against these AF3 traces, even though the raw
  `.pfw.gz` clearly contains thousands of matching events. Root cause:
  these traces encode `ph` as an INTEGER (`ph:1`, `ph:4`) rather than the
  Chrome-trace-JSON STRING codes (`"X"`, `"M"`) `dftracer_comparator`'s
  event parser expects -- so it silently treats every chunk as containing
  0 valid events regardless of the query. Genuine tool-compatibility gap
  with Python-FUNCTION-mode/contextual (`DFTracerFn`) traces of this
  shape -- proposed as a bug lesson (see report).
- Per-function time breakdown (1eby vs 1e66), in seconds, aggregated by
  (cat, name) over ALL non-dftracer events (see changelog for full table
  and both replicates):

  | Span | 1eby (18MB MSA) | 1e66 (80MB MSA) | Scale factor |
  |---|---|---|---|
  | alphafold3.process_fold_input (total) | 95.48s | 1090.03s | 11.4x |
  | alphafold3.predict_structure | 94.11s | 1077.48s | 11.5x |
  | alphafold3.ModelRunner.run_inference (5 seeds, GPU) | 67.84s (71.0% of total) | 956.00s (87.7% of total) | 14.1x |
  | Data.featurise_input (CPU data pipeline, total) | 19.37s (20.3% of total) | 107.36s (9.9% of total) | 5.5x |
  | Data.get_random_conformer (largest sub-cost within featurise_input) | 15.34s (1,990 calls) | 66.78s (10,650 calls) | 4.4x |
  | Data.extract_msa_features (actual .a3m MSA parsing) | 1.46s (62 calls) | 22.71s (62 calls) | 15.6x |
  | POSIX (all file ops) | 0.63s | 1.13s | 1.8x |

  MSA file-size ratio 1e66/1eby = 80MB/18MB = 4.44x; event-count ratio =
  75343/14723 = 5.12x.

Expected artifact (unchanged): diagnoser produces the explicit
CONFIRM/REFUTE verdict -- the analyzer's numbers above show the naive
"GPU inference is constant" half of the hypothesis is REFUTED (inference
scaled 14.1x, MORE than proportionally to MSA size, and grew from 71% to
87.7% of total wall time) while the "CPU featurization scales with MSA
size" half is weakly CONFIRMED only for the specific MSA-parsing function
(extract_msa_features, 15.6x) but NOT for the dominant CPU sub-cost
(get_random_conformer, 4.4x, which tracks MSA size proportionally but is
not itself pure MSA parsing). Net: CPU featurization is NOT the dominant
cost at either MSA size -- GPU model inference dominates and grows faster
than linearly with MSA depth, likely because larger MSA depth increases
the sequence dimension fed into the model's attention/inference compute,
not because of CPU-side I/O or parsing overhead. The diagnoser should
render this nuance explicitly rather than a flat confirm/refute.

**STATUS (2026-08-02): dftracer-diagnoser portion COMPLETE.**

### Verdict: PARTIALLY REFUTED, with a twist

The scientist's hypothesis ("MSA parsing/featurization on the CPU is the
dominant cost for large-MSA structures, GPU inference stays ~constant") is
**REFUTED on its GPU-constant half and only weakly/narrowly confirmed on its
CPU half**. Net: this is a **GPU-inference-bound** workload, not a
CPU-I/O-bound one, and the imbalance gets WORSE (not better) as MSA size
grows.

- GPU half REFUTED: `ModelRunner.run_inference` (5 seeds) scales **14.1x**
  (67.84s -> 956.00s) against a **4.44x** MSA-size increase (18MB -> 80MB) --
  super-linear, not constant. Its share of total wall time GREW from 71.0%
  to 87.7%.
- CPU half only narrowly confirmed: the actual `.a3m` text-parse function
  (`Data.extract_msa_features`) scales steeply in isolation (15.6x,
  1.46s->22.71s) -- consistent with "parsing cost grows with MSA size" --
  but it is a tiny absolute fraction of wall time at either scale (1.5-2%).
  The CPU pipeline as a whole (`Data.featurise_input`) scales sub-linearly
  (5.5x) and SHRINKS as a fraction of total time (20.3% -> 9.9%). Its
  largest sub-cost (`Data.get_random_conformer`, 4.4x, 5.5x more calls at
  1e66) tracks MSA depth (more residues/conformers to generate) but is
  conformer-generation compute, not MSA I/O/parsing.
- Likely mechanism: MSA depth is not merely a one-time parse cost -- it
  almost certainly feeds the model's own attention/sequence dimension (MSA
  representation processed through Evoformer/PairFormer stack and diffusion
  sampling), so a deeper MSA makes the GPU do proportionally MORE work per
  seed, not the same amount. This is architecturally consistent with
  AF-style models where MSA depth sets the sequence-dimension size of
  MSA-attention and pair-representation tensors.

### Finer-grained GPU-inference decomposition: NOT AVAILABLE in this trace

Per task requirement, checked explicitly rather than guessing: STEP 4
annotation scope for the GPU path was deliberately shallow ("secondary
target... top-level inference call + diffusion loop entry/exit is
sufficient" -- see STEP 4). `graph_query` against the annotated source
confirms the underlying Evoformer/PairFormer/diffusion/attention modules
exist (`model/network/{diffusion_head,diffusion_transformer,
template_modules,atom_cross_attention,modules}.py`,
`jax/attention/{flash_attention,xla_attention,attention_base}.py`) but
**none of these carry their own dftracer span** -- only the top-level
`alphafold3.ModelRunner.run_inference` function is annotated. The manual
per-(cat,name) aggregation confirms no finer event names exist under it in
either trace. **We cannot attribute the 14.1x scaling to diffusion sampling
steps vs. Evoformer/pairformer vs. attention specifically from this
trace** -- that would require a follow-up annotation pass adding spans
inside `run_inference`/diffusion loop iterations (a STEP-4-class rework,
out of scope for this diagnosis step). Architecturally, MSA-attention/
pair-representation cost scaling with MSA depth is the most likely
mechanism (JAX/XLA attention over an MSA dimension that grows with MSA
depth), but this is inference from code structure, not a measured
decomposition -- report it as a hypothesis for the optimizer to test, not a
confirmed finding.

### Ranked bottleneck list for STEP 8 optimizer dispatch

| Rank | Dimension | Bottleneck | Evidence | Expected yield |
|---|---|---|---|---|
| 1 | **compute** | GPU model inference (`run_inference`, 5 seeds) scaling super-linearly with MSA depth -- 14.1x for 4.44x MSA size, 87.7% of 1e66 wall time | 956.00s of 1090.03s total (1e66) | HIGH -- primary target |
| 2 | **compute** | CPU featurization `Data.get_random_conformer` (66.78s, 10,650 calls at 1e66) -- conformer generation scaling with MSA depth, not I/O | 66.78s / 9.9% of total (1e66) | MEDIUM -- secondary target |
| 3 | **memory** | Large `.a3m` (up to 80MB) + growing conformer/tensor working set loaded per seed -- check for avoidable copies/duplication in parse->featurize->model handoff | Not directly measured; inferred from CPU pipeline growth + GPU tensor scaling | LOW-MEDIUM, needs profiling to confirm |
| 4 | **io** | POSIX file ops | 0.63s (1eby) / 1.13s (1e66), <1.2% of total wall time either way | **LOW-YIELD -- expected, not a gap.** Must still get a full L1-L12 checklist pass per rule 14, but do not expect a measurable win; single read-only vast1 `.a3m` reads are already a negligible fraction of wall time. |
| 5 | **communication** | N/A -- single-process JAX/XLA, no MPI/NCCL collectives | Confirmed in Overview/STEP 1 (no MPI dependency) | **LOW-YIELD, structurally not applicable.** Must still walk full checklist and record explicit "not applicable, single process" verdict per rule 14 -- do not skip. |

**Tool-First vs Manual separation for this diagnosis:**

| Finding | Source |
|---|---|
| `event_count` matched exactly (14,723 / 75,343); replicate noise ~1-2% | TOOL -- `mcp__dftracer__event_count` (via analyzer, STEP 7 first half) |
| `diagnose()` generic-preset checkpoint returns only `*_ops_slope` scores, no per-function breakdown | TOOL -- `mcp__dftracer__diagnose` (attempted, confirmed not useful for this specific hypothesis test) |
| `dftracer_comparator` returns 0 matched events on all queries against these traces | TOOL -- `mcp__dftracer__comparator` (confirmed broken for Python FUNCTION-mode integer-`ph` traces; see `dftracer-trace-utils` skill Known Bugs table) |
| Per-(cat,name) duration breakdown table (1eby vs 1e66) | MANUAL -- zcat+python aggregation, since `view`/comparator were not usable |
| `opt_kb_lookup` for prior AF3/GPU-MSA-scaling findings | TOOL -- `mcp__dftracer__opt_kb_lookup` (0 prior results -- no cross-session precedent for this workload/bottleneck) |
| Evoformer/diffusion/attention modules exist but are unannotated (no finer trace decomposition) | TOOL -- `mcp__dftracer__graph_query` (mode=query) against the annotated source graph |


Analyzer (level_3):
1. Preset: generic/POSIX-style file-I/O preset (AF3 is not DLIO-shaped) —
   confirm via `list_presets()` before choosing; if a "generic" preset exists
   per `feedback-dftracer-analyzer-generic-preset`, use it.
2. `cluster_n_workers=32` for dask parallelism (never `cluster_cores`, per
   `feedback-analysis-parallel-workers`).
3. Run analysis SEPARATELY for `1eby` and `1e66` so the size-scaling
   comparison is direct, then a combined comparison view.
4. Views to pull: per-function time breakdown (data/parsers/ vs
   model/pipeline/ vs model/diffusion inference), I/O read-size distribution
   for `.a3m` file opens, CPU time in `data/parsers/cpp` extensions vs
   Python glue, wall-time-vs-MSA-size regression across the two points (plus
   the four prior-run-log data points for context, if usable as reference
   only — not re-run).

Diagnoser (level_3):
1. Directly test the hypothesis: is `data/`+`parsers/` (CPU parse/featurize)
   time proportional to `.a3m` file size, and does it dominate total wall
   time for 1e66 vs 1eby? Is GPU/diffusion inference time roughly constant
   across both (matching the "~146s for 5 seeds" prior number)?
2. Produce an explicit CONFIRM/REFUTE verdict with the supporting numbers —
   this is the deliverable the user asked for, make it unambiguous in the
   diagnoser's own output, not left implicit for the optimizer to infer.
3. Identify the specific bottleneck functions within `data/parsers/` (e.g.
   is it the .a3m text parsing itself, or downstream featurization/tensor
   construction) for the optimizer to target.

Expected artifact: comparison report (1eby vs 1e66) with per-stage time
breakdown, explicit confirm/refute verdict on the MSA-parsing hypothesis,
ranked bottleneck function list feeding STEP 8.

## STEP 8: dftracer-optimizer

Dispatch ALL four dimension subagents against the diagnosed bottleneck list
(rule 14 — mandatory even if a dimension looks inapplicable):

- `dftracer-optimizer-io`: likely PRIMARY dimension given the hypothesis —
  candidates: parallel/chunked `.a3m` reads, memory-mapping, avoiding
  redundant re-parses, caching parsed MSA across seeds if AF3 currently
  reparses per-seed, read-ahead/buffering tuning for Lustre reads of the
  vast1 MSA db (read path only, since vast1 is read-only — no ROMIO hint
  tuning on writes needed here since AF3 writes are small compared to reads).
- `dftracer-optimizer-compute`: CPU-side parsing/featurization algorithmic
  cost (e.g. vectorizing Python parsing loops, pushing more of the parse
  path into the existing C++ extensions vs. Python, avoiding redundant
  string operations) — this is likely the SECOND primary dimension.
- `dftracer-optimizer-memory`: large `.a3m` files (up to 80MB) loaded
  wholesale into memory — check for avoidable copies/duplication during
  parse→featurize handoff.
- `dftracer-optimizer-communication`: expected "not applicable, single
  process, no MPI/NCCL collectives" — MUST still walk the full checklist
  and record this verdict explicitly with reasoning (rule 14), not skip it.

Each subagent walks its skill's "Exhaustive Dimension Checklist" and reports
every L1/L2/L3 candidate considered, applicable and not, with reasons.
Metric objective: reduce 1e66-class (large-MSA) wall time without regressing
1eby-class (small-MSA) wall time or output correctness (validate
structure/confidence outputs match un-optimized baseline within numerical
tolerance — AF3 is not embarrassingly tolerant of nondeterminism the way
some ML training is, so a correctness check matters here).
Termination: propose top 2-3 candidates ranked by expected impact ×
implementation risk; iterate build→re-trace→re-diagnose on the top candidate
against BOTH 1eby and 1e66 before declaring a winner. Take at least one
replicate of baseline and of the best variant to establish a noise band
before crediting any delta (per DL-run-length / noise-band rules, applied
here even though this isn't strictly a DL training run — same principle: no
single-run deltas).

"Do-less" check: any optimization that merely truncates/pre-caches MSA
content out-of-band, skips seeds, or otherwise reduces total work done must
be flagged as NOT a real speedup and excluded from headline claims.

Expected artifact: merged 4-dimension proposal report, applied + measured
top candidate(s), before/after wall-time comparison with noise band for
1eby and 1e66, correctness check result.

**STATUS (2026-08-02): STEP 8 COMPLETE (all 4 component subagents dispatched
and reported; merge done). One measured win, three high-value candidates left
UNVERIFIED at 1e66 scale for lack of allocation.**

### DIAGNOSIS CORRECTION (supersedes the STEP 7 mechanism claim)

STEP 7 attributed the 14.1x `run_inference` scaling to "MSA depth feeding the
model's attention/sequence dimension". `dftracer-optimizer-compute` REFUTED
that mechanism using AF3's own absl logs: the cost is **quadratic in the
PADDED BUCKET token count** (measured exponent **N^2.02**; reconstructed
totals match traced totals within rounding) **plus a fixed ~32-43s per-process
XLA JIT compile cost** (33.8% of the 1eby run's wall time, only 4.0% of
1e66's). MSA size *correlates* with token count, but the causal lever is
bucket padding + compile, NOT MSA depth per se. The final report narrative
must use this refined mechanism.

Corollary: the actionable levers are (a) bucket alignment, (b) compile-cache
reuse, (c) seed-level parallelism -- none of which are "MSA parsing" fixes.
This is why the I/O dimension correctly found nothing to do.

### MERGED RESULTS -- what was actually measured

| Component | Change | Scale tested | Result |
|---|---|---|---|
| memory | `XLA_PYTHON_CLIENT_PREALLOCATE=false` (env-var only, no source edit) | 1eby, 3 reps/side | **WIN: -4.9% wall (disjoint ranges: variant max 110.6s < control min 114.4s), -77% peak node memory (122.8 -> 28.4 GiB)**. UNVERIFIED at 1e66. |
| compute | `--flash_attention_implementation=triton` | 1eby | NO-OP -- silently falls back to xla on ROCm (triton path is Ampere-only) |
| compute | `--xla_gpu_autotune_level=4` | 1eby | NO-OP on wall time; slightly perturbs `ranking_score` (0.81 -> 0.80) |
| compute | tight bucket 248 (vs default 256) | 1eby | NO-OP / slight REGRESSION -- 248 is not tile-aligned (not a multiple of 64/128). Does NOT rule out the 64-aligned 1088 option at 1e66. |
| compute | XLA persistent JIT compilation cache | 1eby | CACHE-HIT verified (file atime) but run then CRASHED: `HIP_ERROR_OutOfMemory` instantiating a HIP graph. Untested workaround: `XLA_FLAGS=--xla_gpu_enable_command_buffer=` |
| io | (all L1-L12 categories) | n/a | DECLINED, correctly. I/O is 0.10% of 1e66 wall (1.13s of 1090s) and POSIX op COUNTS are IDENTICAL across MSA scales (only bytes-per-read differ) -- there is no I/O scaling problem to fix. |
| communication | (all categories) | n/a | NOT APPLICABLE, verified STRUCTURALLY (1 unique (pid,tid) pair, single `jax.local_devices()[i]` selection in source, no MPI/RCCL linked) -- not merely inferred from an empty trace. |

Correctness methodology (important, reusable): AF3 is **run-to-run
nondeterministic on GPU regardless of configuration** -- a CIF byte-diff is
NOT a valid correctness check (baseline-vs-baseline CIF diff is the same
magnitude as control-vs-variant). Use the `ptm`/`iptm` confidence metrics
instead. The memory win passed this check.

### UNVERIFIED AT 1e66 -- the candidates that actually matter

None has been confirmed at the scale where GPU inference is 87.7% of wall
time. Each needs one ~18-min 1e66 traced run plus a replicate:
1. **64-aligned bucket 1088** (instead of the computed 1280) -- ~18-25%
   padding waste at 1e66; ~25% wall-time saving EXTRAPOLATED from the N^2.02
   fit, **not measured**.
2. **JIT compile cache + `--xla_gpu_enable_command_buffer=` OOM workaround**
   -- reclaims the fixed ~32-43s compile cost (4% of 1e66, 33.8% of 1eby).
3. The memory `PREALLOCATE=false` win itself -- mechanism is pool reservation
   vs 1eby's small footprint, so it may dilute OR hold at scale; unconfirmed
   either way.
4. **4-GPU concurrent seed execution** -- highest potential of anything in the
   table, never run.

### ORPHANED CANDIDATES -- found by one component, live in another's files

| Candidate | Found by | Lives in | Ceiling | Status |
|---|---|---|---|---|
| Memoize `Data.extract_msa_features` across the 5 seeds (PURE function of `(msa_sequences, chain_poly_type)`, no seed arg, yet re-run per seed; ~4.53s/seed at 1e66) | io | compute/memory territory | ~18.1s = 1.7% of 1e66 wall | UNOWNED -- needs an owner assigned; edge-of-noise, needs sign-off on holding MSA arrays live across all 5 seeds |
| Memoize `Data.mol_from_ccd_cif` (RDKit Mol from a STATIC CCD dict, called 10,640x/run) | memory | io territory (`data/tools/rdkit_utils.py`) | ~4.95s = 0.45% of 1e66 wall | UNOWNED -- legitimate memoization, not do-less |

### CROSS-COMPONENT FINDINGS (merged, not duplicated)

- **4-GPU seed parallelism (compute x communication).** compute proposes
  running the 5 independent rng seeds concurrently across the node's 4 APUs
  (identical total work -- a real parallelization, NOT do-less). communication
  independently assessed its cost: **safe, ~0 communication cost**, because
  the seeds are independent (data parallelism, no collectives). Highest
  weighted score in the merged table (67.5) but NOT MEASURED.
- **MI300A unified memory is INTRA-PACKAGE only (communication; affects
  memory and compute).** One APU's CPU+GPU share that package's HBM at zero
  copy cost; transfers BETWEEN the node's 4 APUs still cross Infinity Fabric
  at real cost. Consequence: independent-seed data parallelism is cheap, but
  **model-sharding a single seed across APUs is NOT recommended** -- it would
  introduce a communication cost where none exists today.
- **Deeper Evoformer/diffusion annotation: RECOMMENDED AGAINST** (compute).
  AF3's own absl logging already yields a sufficient, decision-relevant
  decomposition; a STEP-4-class re-annotation would not change any decision.

### BEST KNOWN CONFIGURATION (with caveats)

`XLA_PYTHON_CLIENT_PREALLOCATE=false` added to the run environment -- the ONLY
measured win, env-var-only, zero source edits, no conflict with any other
component's proposal. **Caveat: validated at 1eby only (-4.9%); its behavior
at 1e66 is unconfirmed.** No component's winning change conflicts with
another's. All other candidates remain proposals.

Honest scope note: nothing in this pass was verified at the scale where the
dominant bottleneck lives. The headline claim is therefore "-4.9% at 1eby",
NOT a 1e66 result.

### 1e66-SCALE VERIFICATION (2026-08-02, POST-MERGE — supersedes "BEST KNOWN CONFIGURATION" above)

Two of the four unverified candidates were tested directly at 1e66 scale
(single node each, single replicate, allocation-limited):

| Candidate | 1e66 result | Verdict |
|---|---|---|
| **64-aligned bucket 1088** (replacing default 1280) | Total time 793s vs 1077-1097s (2 baseline replicates) — **-27% wall time**. Steady-state per-seed 125.5s vs ~182-186s. `ptm`/`iptm`/`ranking_score` (0.66/0.80/0.77) within the same range as baseline (0.65/0.79/0.76) — correctness preserved. | **CONFIRMED WIN — the largest in this session, supersedes the memory result as the headline finding.** Zero source changes, one CLI flag (`--buckets=256,512,768,1024,1088,1536,2048,2560,3072,3584,4096,4608,5120`). |
| `XLA_PYTHON_CLIENT_PREALLOCATE=false` (the 1eby memory win) | Total time 1109s vs 1077-1097s baseline range — no improvement (within/slightly outside noise). | **DOES NOT TRANSFER to 1e66.** Real at 1eby scale, diluted/absent at 1e66 where GPU inference dominates. Do not carry into a 1e66-class recommendation. |

Not tested (no remaining allocation time): JIT compile cache +
`--xla_gpu_enable_command_buffer=` workaround at 1e66; 4-GPU concurrent seed
execution.

**REVISED BEST KNOWN CONFIGURATION:** `--buckets=256,512,768,1024,1088,1536,2048,2560,3072,3584,4096,4608,5120`
(replace the app's default bucket list, swapping `1280`→`1088`) — confirmed
**-27% wall time at 1e66** (the scale that matters), correctness-neutral,
zero source edits. The `XLA_PYTHON_CLIENT_PREALLOCATE=false` env var remains
a genuine but scale-limited win (1eby only) — recommend applying both
together only if 1eby-class runs are also common in production, since they
don't conflict with each other, but do not claim the memory win at 1e66
scale.

## STEP 9: dftracer-report

`run_id="af3/20260729_044330"`. Call `session_final_report` with a fully
detailed `report_md`/`conversation_md`/`readme_md` per the `dftracer-report`
agent's own Report Structure template. Must include:
- The MSA-size-scaling hypothesis, the confirm/refute verdict from STEP 7,
  and the supporting 1eby/1e66 numbers.
- Every script actually run this session under `scripts/` (venv build,
  dftracer install, annotated build, smoke test, both baseline run
  wrappers, each optimizer variant, roofline/profiling checks) — grep the
  workspace for scripts an automated glob might miss.
- `config.ini` is the ONLY place a real path/session value goes;
  `scripts/lib_load_config.sh` sourced by every other script; grep
  `scripts/` for the literal workspace absolute path to confirm none leaked.
- Self-contained validation: point `OUTPUT_ROOT` at
  `<WS>/final_folder_validate/` (isolated from real run data), fill
  `WORKSPACE_ROOT`, run `scripts/run_all.sh <alloc-id>` and confirm it
  reproduces the reported result within the established noise band before
  calling `session_final_report` again with `validated=True` and a
  one-line `validation_notes`.
- Confirm `session_final_report` returns `pdf.generated`, `completeness.ok`,
  and `readme_check.ok` all true before marking this step done — if any
  gate fails, fix the named gap and re-call, don't mark done on a partial
  pass.

Expected artifact: `<WS>/final_report/` complete, validated, all three gates
true.

## STEP 10: dftracer-privacy-guard

`run_id="af3/20260729_044330"`. Call `privacy_scan(paths=[..., "final_report"])`
— `final_report/` is NOT in the tool's default scan set, include it
explicitly (known blind spot,
`bug-privacy-scan-final-report-gitignore-blindspot`, already fixed at tool
level but still requires the explicit path arg). Also scan
`scripts/`, `artifacts/`, any skill/memory files touched this session
(`workload-af3`, any `software-af3-*` skill created in STEP 2/3/4).
Redact any hits (usernames, `/usr/WS2/<user>/...`, `/p/lustre5/<user>/...`,
flux job ids, session UUIDs, hostnames with node numbers →
`$USER`/`$LUSTRE_ROOT`/`<flux-jobid>`/`<uuid>`/`<system><node>`) and re-scan
until `clean`. Session is not done until this reports clean.

## DISPATCH ORDER

dftracer-session-setup, dftracer-build-app, dftracer-build-dftracer,
dftracer-annotator (dftracer-annotate-python, dftracer-annotate-cpp),
dftracer-build-smoke, dftracer-tracer, dftracer-analyzer, dftracer-diagnoser,
dftracer-optimizer (dftracer-optimizer-io, dftracer-optimizer-communication,
dftracer-optimizer-compute, dftracer-optimizer-memory), dftracer-report,
dftracer-privacy-guard
