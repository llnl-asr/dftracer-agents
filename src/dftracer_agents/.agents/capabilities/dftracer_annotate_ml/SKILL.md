---
name: dftracer_annotate_ml
description: End-to-end ML/DL Python workload annotation with dftracer AI regions: ml_categorize_files (entry/train/data/ckpt/comm/other buckets), ml_annotate_plan, ml_annotate_project (one call for the whole project), plus the full dftracer-ml-annotate recipe (system detection, install flags, smoke, traces, patch generation, lessons). Load for PyTorch/DeepSpeed/Megatron/DLIO-style workloads, DataLoader workers, checkpoint annotation, or an ML annotation that validate_annotations rejects.
---

# dftracer_annotate_ml

Converted 2026-11-13 from dftracer-agents: converted from dftracer-agents register_ml_pipeline_tools (session/ml_pipeline.py) and skill dftracer-ml-annotate. Tools keep their dftracer-agents names, signatures and behavior; the validator dftracer_annotate_ml_check rules on what the predictors produce.

## Rules

- Predict with ml_categorize_files, ml_annotate_plan, ml_annotate_project; validate with dftracer_annotate_ml_check; on a failed check follow ## Corrector, then predict and validate again.

### From `dftracer-ml-annotate/SKILL.md (Steps 0-4 fast path)`

Lessons file: workspaces/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md (workspace-local
staging copy — shared with the general dftracer-pipeline skill; sync it back to the source
repo with session_lessons_sync_preview / session_lessons_sync_pr, see Step 10). Load the
compact rules/instructions with skill_load(name="dftracer-annotation-lessons"); load the
accumulated log entries with skill_load(name="dftracer-annotation-lessons", file="LESSONS_LOG.md").

**Read the lessons file before doing anything else.** Apply every standing rule
(ML-R1 through ML-R16) and check every session log entry for context matching
the current application or framework.

DLIO benchmark (`dlio_benchmark`) is the canonical reference pattern. When in
doubt about how to annotate a function, match what DLIO does.

══════════════════════════════════════════════════════════════════════
STEP 0 — SYSTEM DETECTION
══════════════════════════════════════════════════════════════════════

Invoke the `system-detect` skill to load module/compiler environment.

Store environment as ENV (module paths, MPI wrappers, ROCm paths).


══════════════════════════════════════════════════════════════════════
STEP 1 — GATHER INPUTS  (if not supplied via arguments)
══════════════════════════════════════════════════════════════════════

If the user invoked this with named arguments (run_id=…, url=…, etc.),
use those directly. Otherwise ask one question at a time:

  Q1: "What is the Git URL of the ML application?"  → APP_URL
  Q2: "Branch or tag? (default: main)"              → REF
  Q3: "Smoke test command? (leave blank to auto-detect)" → SMOKE_CMD
  Q4: "Extra build flags? (leave blank to skip)"    → EXTRA_FLAGS

If run_id was supplied, skip Q1–Q4 and jump to Step 3.

Print: "Starting ML annotation pipeline for <APP_URL> @ <REF>"

Track pitfalls found this session in a list: PITFALLS = []


══════════════════════════════════════════════════════════════════════
STEP 2 — SESSION SETUP
══════════════════════════════════════════════════════════════════════

2a. Create session and clone:

    session_create(url=APP_URL, ref=REF)
    → store RUN_ID, WS

2b. Detect ML workload:

    session_detect_ml_workload(run_id=RUN_ID)

    Store:
      FRAMEWORKS       = result.frameworks
      HIP_NEEDED       = result.hip_tracing_needed
      ROCM_INFO        = result.rocm_info
      INSTALL_FLAGS    = result.install_flags
      DISTRIBUTED      = result.distributed
      HAS_DATALOADER   = result.has_dataloader
      CAPABILITIES     = result.capabilities

    Print:
      "Frameworks: <FRAMEWORKS>"
      "ROCm: <ROCM_INFO.found> (<ROCM_INFO.path>)"
      "HIP tracing needed: <HIP_NEEDED>"
      "Distributed: <DISTRIBUTED>"

    If HIP_NEEDED but ROCM_PATH is not set in ENV:
      PITFALLS.append({phase: "install", error: "ROCM_PATH not set",
        root_cause: "ROCm detected but ROCM_PATH env var absent",
        fix: "export ROCM_PATH=<ROCM_INFO.path> before install"})
      Set ROCM_PATH manually before Step 3.

2c. Configure and build original source:

    session_configure(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)
    session_build_install(run_id=RUN_ID)

2d. Install dftracer with correct flags:

    session_install_dftracer(run_id=RUN_ID)

    The install reads INSTALL_FLAGS (which includes DFTRACER_ENABLE_HIP_TRACING=ON
    when HIP_NEEDED) automatically from the session state set by
    session_detect_ml_workload. No manual env override needed.

    On failure → record pitfall in PITFALLS, then stop.

Print: "Setup complete. RUN_ID=<RUN_ID>  dftracer installed."

2e. Validate session structure before annotating anything (STRICT RULE —
    see dftracer-cheatsheet S0: always use the MCP tool's own directory
    structure, never a hand-built path):

    session_validate_structure(run_id=RUN_ID)

    If clean=false → session_reorganize_structure(run_id=RUN_ID, dry_run=False)
    then re-validate before proceeding to Step 3.


══════════════════════════════════════════════════════════════════════
STEP 3 — COPY SOURCE AND DISCOVER FILES
══════════════════════════════════════════════════════════════════════

3a. Copy source to annotated workspace:

    session_copy_annotated(run_id=RUN_ID)

3b. Discover Python source files:

    find_source_files(run_id=RUN_ID, folder="annotated", language="python",
      exclude_patterns=["**/test*", "**/__pycache__/**", "**/setup.py",
                        "**/conftest.py", "**/docs/**"])

    Categorize files into:
      ENTRY_FILES    = files with if __name__ == "__main__" or def main(
      TRAIN_FILES    = files with train / fit / backward / optimizer.step
      DATA_FILES     = files with __getitem__ / Dataset / DataLoader
      CKPT_FILES     = files with save_checkpoint / load_checkpoint / state_dict
      COMM_FILES     = files with all_reduce / dist.barrier / horovod / hvd.
      OTHER_FILES    = all remaining .py files

Print: "Files: entry=<N> train=<N> data=<N> ckpt=<N> comm=<N> other=<N>"


══════════════════════════════════════════════════════════════════════
STEP 4 — AI/ML REGION ANNOTATION
══════════════════════════════════════════════════════════════════════

#### FAST PATH (default) — one MCP call for the whole project

The per-file recipe in 4a–4g below is now implemented as deterministic MCP
tools. Use them first; they are faster, reproducible, and do not drift.

    ml_annotate_plan(run_id=RUN_ID, threshold=20)      # review, writes nothing
    ml_annotate_project(run_id=RUN_ID, threshold=20)   # execute everything

`ml_annotate_project` categorizes every file (Step 3b), then runs the AI/ML cost
estimator on EVERY file and passes its allow-list down. Semantic buckets
(entry / train / data / ckpt / comm) go through `python_annotate_ai_file`, the
leftovers through `python_annotate_file`.

**The cost gate always runs. The one exception is the AI API**: functions that
map to a `dft_ai.*` region (data item, checkpoint, training step, comm, forward,
backward) are annotated for what they MEAN and are kept regardless of score.
Everything else must clear the threshold, so a training script does not get a
decorator on every getter. `ml_categorize_files` exposes the bucketing alone.

It also validates at the end (`validate=True`) and returns `validation.passed`.

Then ALWAYS:

    annotate_add_app_metadata(run_id=RUN_ID, filepath=<entry file>,
      language="python",
      params_json='{"app":"...","ranks":"...","batch_size":"..."}')

    validate_annotations(run_id=RUN_ID, language="python")

Do not proceed to the build until `validate_annotations` passes. Also dispatch
the `dftracer-validate-python` agent for an independent check.

#### BACKUP PATH — the manual recipe (4a–4g)

Use the steps below ONLY when: a tool is missing or errors; a file needs
judgement the classifier cannot make; or you are repairing a file the tools got
wrong. They document exactly what the fast path does.

### From `dftracer-ml-annotate/SKILL.md (Steps 5-13)`

══════════════════════════════════════════════════════════════════════
STEP 5 — ANNOTATION VERIFICATION
══════════════════════════════════════════════════════════════════════

    session_annotation_report(run_id=RUN_ID)

Check:
  ☐ Entry file has initialize_log + finalize()
  ☐ Training loop has @dft_ai.pipeline.train
  ☐ epoch for-loop wrapped with dft_ai.pipeline.epoch.iter()
  ☐ batch for-loop wrapped with dft_ai.dataloader.fetch.iter()
  ☐ __getitem__ annotated with @dft_ai.data.item
  ☐ ai.update(step=, epoch=) inside batch loop
  ☐ Comm ops use context manager style (not decorator)
  ☐ optimizer.step uses start/stop style (not decorator)
  ☐ Data I/O phases use ai.data.io.open/read/write/close (ML-R25)
  ☐ Checkpoint I/O phases use ai.checkpoint.io.open/read/write/close (ML-R26)
  ☐ Non-dataloader/non-checkpoint I/O uses ai.other.io.open/read/write/close (ML-R27)
  ☐ image_size metadata passed in every data.io.read / checkpoint.io.read / write region
  ☐ [pytorch] PyTorch Profiler wrapper injected (trace_handler, prof.step()) — ML-R28
  ☐ [pytorch] Dynamo backend wired to torch.compile or model forward — ML-R29
  ☐ image_size metadata passed in every other.io.read / other.io.write region

For any missing item:
  - Re-run python_annotate_ai_file for the relevant file
  - Record the gap as a potential PITFALL if it was not caught automatically

Print:
  ┌────────────────────────────────────────────────────────┐
  │  ML ANNOTATION REPORT — <RUN_ID>                       │
  │  Files annotated:   <N>                                │
  │  AI regions:        <N> (pipeline=N compute=N data=N)  │
  │  Generic regions:   <N>                                │
  │  Entry file:        <name>  initialize_log: YES/NO     │
  │  Frameworks:        <FRAMEWORKS>                       │
  │  HIP tracing:       <HIP_NEEDED>                       │
  └────────────────────────────────────────────────────────┘

Ask: "Proceed with build + trace run? [yes / no / fix <file> <feedback>]"

  "no"  → jump to Step 8 (update lessons only).
  "fix" → re-annotate the named file, re-run verification, ask again.
  "yes" → continue to Step 6.


══════════════════════════════════════════════════════════════════════
STEP 6 — BUILD + SMOKE TEST
══════════════════════════════════════════════════════════════════════

6a. DFTRACER_INIT mode:

    Primary (always try first):
      DFTRACER_INIT = "FUNCTION"

    The Python dftracer API is the integration point:
      from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai
      _dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=-1)
      # ... annotated functions with decorators ...
      _dft_log.finalize()

    Fallback (only if FUNCTION produces empty trace or ImportError at runtime):
      dftracer_lib=$(python -c "import dftracer, os; print(os.path.join(os.path.dirname(dftracer.__file__), 'lib', 'libdftracer_preload.so'))")
      DFTRACER_INIT = "HYBRID"
      LD_PRELOAD = "<dftracer_lib>"

    PRELOAD-only mode is never used — Python API annotations must always be present.

6b. Build annotated:

    session_build_annotated(run_id=RUN_ID, extra_cmake_flags=EXTRA_FLAGS)

    On failure:
      - Extract failing function(s) from compiler error
      - Re-annotate only those files with the offending function excluded
      - Re-run build (max 2 retries)
      - Record the error in PITFALLS

6c. Smoke test without dftracer:

    session_run_smoke_test(run_id=RUN_ID, command=SMOKE_CMD,
      subfolder="build_ann")

    On failure → record in PITFALLS, ask user: "Continue to trace? [yes/stop]"


══════════════════════════════════════════════════════════════════════
STEP 7 — TRACE COLLECTION + VERIFICATION
══════════════════════════════════════════════════════════════════════

7a. Create traces directory:

    mkdir -p <WS>/traces/<APP_NAME>

7b. Run with dftracer:

    session_run_with_dftracer(run_id=RUN_ID, command=SMOKE_CMD,
      subfolder="build_ann",
      env_extra={
        "DFTRACER_ENABLE": "1",
        "DFTRACER_INC_METADATA": "1",
        "DFTRACER_INIT": DFTRACER_INIT,
      },
      data_dir="all")

7c. Split and analyze:

    session_split_traces(run_id=RUN_ID, app_name=<APP_NAME>)
    session_analyze_traces(run_id=RUN_ID, query_type="summary")

7d. Verify AI/ML categories in trace (ML-R15):

    Check that the trace summary includes categories:
      pipeline, compute, data, dataloader, data.io, checkpoint.io, other

    Also verify that data.io, checkpoint.io, and other.io events carry image_size in args.
    Missing image_size means ML-R25/ML-R26/ML-R27 was not applied — re-annotate.

    checkpoint.io events will be ABSENT if checkpoint_interval=-1 (checkpointing disabled).
    This is expected — do not treat absent checkpoint.io as an annotation failure.

    If HIP_NEEDED and "hip" category absent (ML-R16):
      PITFALLS.append({phase:"run", error:"HIP events absent from trace",
        root_cause:"DFTRACER_ENABLE_HIP_TRACING=ON not set at dftracer install time",
        fix:"Re-install dftracer with DFTRACER_ENABLE_HIP_TRACING=ON, re-run with DFTRACER_INIT=FUNCTION; if still absent fall back to HYBRID with LD_PRELOAD=libdftracer_preload.so"})

    Print missing categories as warnings.

7e. Compute / DataLoader overlap analysis  (GLANCED-IO §4.3, HPDC '26):

    This is the single most important pipeline correctness check.
    A correct DL pipeline runs the DataLoader in parallel with GPU compute
    so that the next batch is already in memory when the current forward/backward
    finishes. If that overlap is absent the pipeline stalls on every step.

    From the dftracer trace timeline, check whether `dataloader` category events
    and `compute` category events overlap in wall-clock time:

      OVERLAPPING (correct):
        |-- dataloader.fetch (batch N+1) --|
                      |-- compute.forward (batch N) -- compute.backward --|

      SEQUENTIAL (broken — pipeline stall):
        |-- compute.forward -- compute.backward --|
                                                   |-- dataloader.fetch --|

    How to detect in dftracer traces:

      session_analyze_traces(run_id=RUN_ID, query_type="timeline_overlap",
        categories=["dataloader", "compute"])

    If the tool returns `overlap_fraction < 0.1` (less than 10% of compute
    time has concurrent dataloader activity), treat this as NO OVERLAP.

    No-overlap diagnosis flowchart:
      Step A — Check DataLoader config in source:
               num_workers == 0  → root cause: no parallel workers at all
               prefetch_factor == 0 or 1 → root cause: no prefetch buffer
               persistent_workers not set → workers respawn each epoch (slow)
      Step B — Check tensor transfer:
               tensor.to(device) without non_blocking=True → blocks CPU,
               stalls the dataloader thread
      Step C — Check distributed sync (if multi-GPU):
               MPI.Barrier / dist.barrier() inside the batch loop →
               serializes all ranks, destroying prefetch pipeline
      Step D — Check DataLoader annotation:
               If `for batch in loader:` was NOT wrapped with
               `dft_ai.dataloader.fetch.iter(loader)`, the trace won't
               show dataloader events at all — re-annotate and re-run

    Fix patterns (DLIO benchmark is the canonical reference):

      # Minimum correct config
      loader = DataLoader(
          dataset,
          num_workers  = max(4, os.cpu_count() // 2),   # L1 param #1
          prefetch_factor = 2,                            # L1 param #2
          persistent_workers = True,
          pin_memory   = True,
      )

      # Non-blocking H2D transfer
      batch = batch.to(device, non_blocking=True)

      # Distributed: avoid per-step barrier unless gradient sync requires it
      # Use gradient accumulation to amortize sync cost

    After applying fix, re-run and re-check overlap_fraction.
    Target: overlap_fraction ≥ 0.5 (dataloader runs during ≥50% of compute).

    Record overlap result in session summary:
      OVERLAP_OK = (overlap_fraction >= 0.1)
    If OVERLAP_OK is False, add to PITFALLS:
      PITFALLS.append({
        phase: "dataloader",
        error: f"No compute/IO overlap — overlap_fraction={overlap_fraction:.2f}",
        root_cause: "... root cause identified in Step A/B/C/D above ...",
        fix: "... exact DataLoader config change applied ...",
        annotation_rule: "ML-R24",
      })

7f. Search local papers for optimization guidance:

    For each top bottleneck identified in the trace summary, call:

        session_search_local_papers(
          query      = "<bottleneck description from trace>",
          bottleneck = "<bottleneck category>",   # e.g. "data_loading", "prefetch"
          framework  = FRAMEWORKS[0] if FRAMEWORKS else "",
        )

    Collect all returned papers into PAPER_REFS list.

    Then generate OPTIMIZATION_PROPOSALS using the paper-backed rules below.
    Every proposal MUST cite at least one paper from PAPER_REFS (use
    "Paper: <title> (<venue> '<year>)" format).

    GLANCED-IO Parameter Ordering (always apply in this order — from
    Table 1 of "Automated Cross-Layer Optimization", HPDC '26):
      L1 — Application-level (highest portability, highest impact first):
           1. num_workers        (DataLoader parallelism)
           2. prefetch_factor    (pipeline depth)
           3. dataset_access_pattern  (sequential vs. random)
      L2 — Framework-level:
           4. framework-specific batch settings, pin_memory, persistent_workers
      L3 — Filesystem-level (lowest portability, tune last):
           5. PFS striping count and stripe size
           6. transfer_size / I/O block size

      RULE: Never report "optimal" after tuning only one parameter — GLANCED-IO
      shows siloed single-parameter tuning misses up to 2.4x throughput gain.
      Always propose joint L1+L2 before L3.

    Cladia Diagnosis Rules (apply when bottleneck is ambiguous):
      DIAG-1  Always build cross-layer dependency graph before diagnosing:
              dftracer → I/O layer → framework layer → compute layer.
      DIAG-2  Compute-bound ≠ I/O-free: use SHAP attribution from Cladia
              to confirm I/O contribution even when GPU utilization is high.
      DIAG-3  Never attribute bottleneck to a single layer from single-layer
              metrics alone. Cross-layer correlation is required.
      DIAG-4  Use quantile regression (Cladia) to separate outlier I/O events
              from median behavior — outliers often dominate epoch time.
      DIAG-5  When Cladia reports uncertainty > 0.3 on root cause, collect
              additional traces with finer granularity before proposing fixes.
      DIAG-6  Checkpoint bottlenecks are cross-layer by definition: combine
              checkpoint frequency (L2) with parallel save (L1) with
              filesystem striping (L3).

    LiveFlow Rules (apply for distributed / multi-GPU workloads):
      LIVE-1  Run critical-path analysis before proposing communication
              optimizations — not all all-reduce ops are on the critical path.
      LIVE-2  Bounded decision windows: propose only changes that can be
              applied within the current epoch without restarting training.
      LIVE-3  For gradient compression proposals, measure compression overhead
              vs. communication savings — LiveFlow shows net benefit varies
              by model size and interconnect bandwidth.
      LIVE-4  Hierarchical coordination: node-level decisions (intra-node
              I/O) before cluster-level decisions (inter-node bandwidth).
      LIVE-5  If training throughput drops after scaling from 1 to N GPUs,
              check critical-path I/O overlap — LiveFlow identifies when
              I/O is on the critical path vs. hidden behind compute.

    HORATIO Rules (apply when trace size > 10 GB or > 100M events):
      TRACE-1  Use HORATIO's RocksDB index for selective event queries —
               do not load full trace into memory for analysis.
      TRACE-2  Bloom filter pre-screening: query by function name before
               loading any trace segment (75x speedup vs. linear scan).
      TRACE-3  For clustering similar trace patterns, use HORATIO's native
               C++ analyzer — Dask-based analysis is 80-83x slower.
      TRACE-4  When storing traces for later analysis, apply HORATIO's
               lossless clustering to reduce storage 40-60% with no info loss.

    Print OPTIMIZATION_PROPOSALS as a numbered list with paper citations.


══════════════════════════════════════════════════════════════════════
STEP 8 — TRACE ORGANIZATION (MANDATORY)
══════════════════════════════════════════════════════════════════════

All traces MUST be organized in the session workspace under the **canonical
per-run layout** so runs are comparable and reproducible. Never leave raw
traces flat, and never invent a different hierarchy — get exact paths from
`session_get_run_paths(run_id=RUN_ID, run_name=<label>)` or
`session.json["paths"]`, never by hand-building strings (STRICT RULE, see
dftracer-cheatsheet S0).

Canonical structure (note: `traces/` nests *under* each run, not the reverse):
  <WS>/
    baseline/traces/{raw,compact}/
    annotated/traces/{raw,compact}/     ← "annotated" run == first iteration
    opt1/traces/{raw,compact}/
    opt2/traces/{raw,compact}/
    ...

Rules:
  1. Before each run: call `session_get_run_paths(run_id=RUN_ID, run_name=<label>)`
     and use its `dftracer_log_prefix` for `DFTRACER_LOG_FILE` — do not
     construct `<WS>/traces/<label>/raw` (that path does not exist).
  2. After each run, split immediately into that same run's `traces_compact` path.
  3. Label convention: "baseline", "annotated", "opt1", "opt2", ... matching
     `session_optimization_iteration` numbering.
  4. Never write DFTRACER_LOG_FILE to Lustre for optimization pipeline runs — the
     MCP tools only scan the project workspace for traces.
  5. Call `session_validate_structure(run_id=RUN_ID)` after organizing traces
     for each label; if `clean=false`, call
     `session_reorganize_structure(run_id=RUN_ID, dry_run=False)` to quarantine
     any stray top-level `traces/` before moving to the next iteration.

When `session_optimization_iteration` runs the profile step, ensure its
command sets DFTRACER_LOG_FILE to the value returned by
`session_get_run_paths(...).dftracer_log_prefix` for that label — never a
hand-built path, and never on Lustre.


══════════════════════════════════════════════════════════════════════
STEP 9 — PATCH GENERATION (MANDATORY — run after every annotation and every opt iteration)
══════════════════════════════════════════════════════════════════════

Every change made during annotation or optimization MUST be captured as a unified diff
patch stored in `<WS>/patches/` so the work is reproducible and PR-ready.

#### Patch structure:

  <WS>/patches/
    npy_annotation.patch          ← annotated/ScaFFold vs source/ScaFFold (full annotation diff)
    hdf5_data_loading.patch       ← HDF5 I/O changes only (data_loading.py annotated vs source)
    scripts_and_configs.patch     ← all new run/install scripts + new configs (new-file diffs)
    opt1_<description>.patch      ← diff between annotated/ and opt1/annotated_snapshot/
    opt2_<description>.patch      ← diff between opt1/ and opt2/
    dftracer_upstream_<fix>.patch ← patches to pydftracer site-packages (upstream PR candidates)

#### When to generate each patch:

  After Step 4 (annotation): generate `npy_annotation.patch` and `hdf5_data_loading.patch`
  After each run script is written: regenerate `scripts_and_configs.patch`
  After each `session_optimization_iteration`: generate `opt<N>_<description>.patch`
  After any dftracer site-packages fix: generate `dftracer_upstream_<fix>.patch`

#### How to generate (use session_snapshot_l1_source or raw diff):

  # Full annotation patch
  diff -ruN --exclude="__pycache__" --exclude="*.pyc" \
    <WS>/source/ScaFFold/ <WS>/annotated/ScaFFold/ \
    > <WS>/patches/npy_annotation.patch

  # Single-file HDF5 patch
  diff -u <WS>/source/ScaFFold/utils/data_loading.py \
          <WS>/annotated/ScaFFold/utils/data_loading.py \
    > <WS>/patches/hdf5_data_loading.patch

  # New scripts (no source equivalent) — new-file format
  for f in <WS>/annotated/ScaFFold/scripts/*.sh <WS>/annotated/ScaFFold/configs/<new>.yml; do
    rel="${f#<WS>/annotated/ScaFFold/}"
    { echo "--- /dev/null"; echo "+++ b/$rel"
      echo "@@ -0,0 +1,$(wc -l < "$f") @@"; sed 's/^/+/' "$f"; echo ""; }
  done > <WS>/patches/scripts_and_configs.patch

  # Opt iteration delta patch
  diff -ruN --exclude="__pycache__" --exclude="*.pyc" \
    <WS>/opt<N-1>/annotated_snapshot/ <WS>/opt<N>/annotated_snapshot/ \
    > <WS>/patches/opt<N>_<description>.patch

  # dftracer upstream patch
  diff -u <original_common.py> <fixed_common.py> > <WS>/patches/dftracer_upstream_torch_input_shapes.patch

#### Rules:
  - NEVER skip patch generation — patches are the audit trail for PRs.
  - Regenerate after every file change; stale patches are worse than none.
  - Use `session_snapshot_l1_source` MCP tool to capture opt iteration snapshots
    before generating opt<N> patches.
  - Patches in `<WS>/patches/` are source-controlled alongside the session.
  - After `session_optimization_iteration`, call `session_snapshot_l1_source`
    BEFORE the next iteration so each opt<N> delta is clean.

Print: "Patches written to <WS>/patches/ — <N> files"


══════════════════════════════════════════════════════════════════════
STEP 10 — UPDATE LESSONS LEARNED  (MANDATORY — always run)
══════════════════════════════════════════════════════════════════════

This step runs whether or not the pipeline completed. Any pitfall discovered
during the session must be recorded so future sessions avoid it.

For each entry in PITFALLS:

    session_ml_append_lesson(
      app        = APP_URL,
      context    = <one-line description of what was attempted>,
      error      = <verbatim error excerpt>,
      root_cause = <why it happened>,
      fix        = <exact steps that resolved it>,
      phase      = <phase where it occurred>,
      framework  = FRAMEWORKS,
      annotation_rule = <new ML-R rule if this generalizes, else "">,
      tags       = FRAMEWORKS + [<phase>, <error_keyword>],
      run_id     = RUN_ID,
    )

Also append a lesson for any annotation pattern that was NOT caught
automatically by python_annotate_ai_file and had to be fixed manually —
these are candidates for new detection patterns in the tool.

If PITFALLS is empty, call session_ml_append_lesson once with:
  context = "Clean run — no pitfalls"
  error   = ""
  fix     = "No action needed"
  tags    = FRAMEWORKS

This keeps a session history even for successful runs.

Print: "Lessons file updated: workspaces/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md"

Sync the new entries back to the source repo so future sessions and other users
inherit them:

    session_lessons_sync_preview(run_id=RUN_ID)

Review the diff it reports. If it shows genuinely new entries, ask for
confirmation, then:

    session_lessons_sync_pr(run_id=RUN_ID, confirm=True)

This opens a pull request against llnl/dftracer-agents. Requires GITHUB_TOKEN
or GH_TOKEN to be set — if absent, note this in the session report and skip
silently (do not treat a missing token as a session failure).


══════════════════════════════════════════════════════════════════════
STEP 11 — FINAL COMPARATOR REPORT  (MANDATORY — always run at session end)
══════════════════════════════════════════════════════════════════════

After all optimization iterations are complete (or at the end of any profiling session
with ≥2 trace sets), run the dftracer comparator across all compact trace directories
and present the results to the user.

11a. Run comparator. Get each label's `traces_compact` path from
     `session_get_run_paths(run_id=RUN_ID, run_name=<label>)` — never
     hand-build `<WS>/traces/<label>/compact`:

    comparator(
      traces=[
        "<WS>/baseline/traces/compact",
        "<WS>/opt1/traces/compact",
        ...
      ],
      labels=["baseline", "opt1", ...],
      metrics=["time", "bandwidth", "iops", "metadata_ops"],
    )

11b. Present results as a color-coded table with three highlight classes:
  🟢 GREEN  — metric improved by > 5%  (positive change)
  🔴 RED    — metric regressed by > 5% (negative change)
  ⚪ NEUTRAL — metric within ±5%       (no significant change)

  Format:

  | Metric              | baseline | opt1    | Δ%      | Trend |
  |---------------------|----------|---------|---------|-------|
  | Total I/O time (s)  | 12.4     | 8.1     | -35%    | 🟢    |
  | Read BW (MB/s)      | 220      | 310     | +41%    | 🟢    |
  | Write BW (MB/s)     | 180      | 175     | -3%     | ⚪    |
  | IOPS                | 450      | 980     | +118%   | 🟢    |
  | Metadata ops/s      | 1200     | 800     | -33%    | 🔴    |
  | data.io.read (ms)   | 45       | 28      | -38%    | 🟢    |
  | other.io.write (ms) | 3.2      | 3.1     | -3%     | ⚪    |

11c. Follow the table with a 3–5 sentence analyst summary:
  - What changed and why (link to optimization applied)
  - What regressed and what to investigate next
  - Overall verdict: net improvement / neutral / net regression

Print this report as the LAST output of every session so the user has a
single consolidated view of what changed.


══════════════════════════════════════════════════════════════════════
STEP 13 — SESSION REPORT
══════════════════════════════════════════════════════════════════════

Write <WS>/session_report.md with:

  # DFTracer ML Annotation Session Report — <RUN_ID>
  **Application:** <APP_URL> @ <REF>
  **Date:** <YYYY-MM-DD>
  **Frameworks:** <FRAMEWORKS>
  **ROCm/HIP:** <HIP_NEEDED>  <ROCM_INFO.path>

  ## Pipeline Steps
  | Step | Status | Duration |
  |------|--------|----------|
  | session_create | ... | ...s |
  | session_detect_ml_workload | ... | ...s |
  | session_install_dftracer | ... | ...s |
  | session_copy_annotated | ... | ...s |
  | python_annotate_ai_file (N files) | ... | ...s |
  | session_build_annotated | ... | ...s |
  | session_run_with_dftracer | ... | ...s |
  | session_analyze_traces | ... | ...s |

  ## Annotation Summary
  | Metric | Value |
  |--------|-------|
  | Files annotated | N |
  | AI/ML regions | N |
  | Generic regions | N |
  | Missing categories | ... |

  ## Step Timings
  (populate from step_timings.json if session_run_pipeline was used)

  ## Pitfalls This Session
  (one sub-section per PITFALLS entry, or "None" if clean)

  ## Lessons Written
  List each lesson appended to SKILL.md this session.


══════════════════════════════════════════════════════════════════════
TOOL REFERENCE
══════════════════════════════════════════════════════════════════════

| Purpose                               | MCP Tool                          |
|---------------------------------------|-----------------------------------|
| Create session + clone                | session_create                    |
| Detect ML frameworks + ROCm           | session_detect_ml_workload        |
| Look up correct AI/ML annotation      | dftracer_get_ai_annotation        |
| Configure original build              | session_configure                 |
| Build + install original              | session_build_install             |
| Install dftracer (pip, with HIP flag) | session_install_dftracer          |
| Copy source → annotated/              | session_copy_annotated            |
| Find Python source files              | find_source_files                 |
| Annotate Python file (AI/ML regions)  | python_annotate_ai_file           |
| Annotate Python file (generic)        | python_annotate_file              |
| Extract function list                 | python_extract_functions          |
| Cost-gate generic fns (whole file)    | python_estimate_file_costs        |
| Cost-gate one function (breakdown)    | python_estimate_function_cost     |
| Categorize files (Step 3b)            | ml_categorize_files               |
| Plan whole project (no writes)        | ml_annotate_plan                  |
| Annotate whole project (fast path)    | ml_annotate_project               |
| Emit app parameters as metadata       | annotate_add_app_metadata         |
| Validate annotation coverage          | validate_annotations              |
| Annotation coverage report            | session_annotation_report         |
| Build annotated version               | session_build_annotated           |
| Smoke test                            | session_run_smoke_test            |
| Run with dftracer tracing             | session_run_with_dftracer         |
| Split trace files                     | session_split_traces              |
| Analyze traces                        | session_analyze_traces            |
| Search local optimization papers      | session_search_local_papers       |
| Append lesson to lessons file         | session_ml_append_lesson          |
| Preview lessons sync diff             | session_lessons_sync_preview      |
| Open PR with synced lessons           | session_lessons_sync_pr           |
| Compare ≥2 trace sets (final report)  | comparator                        |

NEVER:
  • Use Edit/Write to manually insert decorators into source files
  • Use @dft_ai.compute.step as a function decorator (use start/stop style)
  • Use @dft_ai.comm.* as a function decorator (use context manager style)
  • Double-wrap a for-loop that already uses dft_ai.*.iter()
  • Omit image_size from data.io.read / data.io.write / checkpoint.io.read / checkpoint.io.write
  • Compute image_size BEFORE the operation (size must be known after read/write completes)
  • Use os.path.getsize(path) or len(path) for image_size — must be in-memory byte count
  • Write DFTRACER_LOG_FILE to Lustre when using session_optimization_iteration — the tool
    looks for traces under each run's own <label>/traces/raw/ inside the session
    workspace; write there instead (get the exact path from session_get_run_paths)
  • Use -n <total_procs> with torchrun-hpc — the -n flag is procs-PER-NODE;
    for 8 nodes × 4 GPUs/node use: torchrun-hpc -N 8 -n 4 --gpus-per-proc 1
  • Hand-build a session path instead of calling session_get_run_paths /
    session_validate_structure — this is how workspaces drift from the
    canonical layout (see dftracer-cheatsheet S0)
  • Skip the final comparator report — it is MANDATORY at the end of every session
  • Use `find /usr/tce`, `find /opt/cray` etc. — use `module avail <name>` instead
  • Install h5py with plain `pip install h5py` on Tuolumne — bundled HDF5 clashes
    with system HDF5 in forked DataLoader workers; use `module load cray-hdf5 &&
    HDF5_DIR=$HDF5_DIR pip install --no-binary=h5py h5py` instead
  • Keep install/run scripts in tmp/ — always write them to annotated/scripts/ or
    opt<N>/scripts/ so they are tracked with the source and reproducible


══════════════════════════════════════════════════════════════════════
DLIO BENCHMARK REFERENCE PATTERNS
══════════════════════════════════════════════════════════════════════

DLIO benchmark is the canonical reference for ML annotation patterns.
Map new applications' structural equivalents to these files:

| DLIO file                                      | Key annotations                           |
|------------------------------------------------|-------------------------------------------|
| dlio_benchmark/main.py                         | @dft_ai on main(); initialize_log at top  |
| dlio_benchmark/framework/pytorch_loader.py     | @dft_ai.pipeline.train, .evaluate         |
| dlio_benchmark/reader/hdf5_reader.py           | @dft_ai.data.item on __getitem__          |
| dlio_benchmark/data_generator/hdf5_generator.py| @dft_ai.data.preprocess, @_dlp.log       |
| dlio_benchmark/checkpointing/torch_checkpoint.py| @dft_ai.checkpoint.capture/.restart      |
|                                                | ai.checkpoint.io.write + image_size      |
| dlio_benchmark/reader/hdf5_reader.py           | ai.data.io.open/read + image_size        |
| dlio_benchmark/utils/utility.py                | @_dlp.log on expensive helpers           |

Epoch loop: `for epoch in dft_ai.pipeline.epoch.iter(range(num_epochs)):`
Batch loop: `for batch in dft_ai.dataloader.fetch.iter(data_loader):`
Step meta:  `ai.update(step=batch_idx, epoch=epoch_num)` inside batch loop

### From `dftracer-ml-annotate/SKILL.md § Mandatory final validation gate (ALWAYS — even after manual fixes)`

#### Mandatory final validation gate (ALWAYS — even after manual fixes)

Annotation is not finished when files are written; it is finished when validation
passes. Run this LAST on every path — MCP fast path, prose backup path, or a
hand-edit after a tool failed:

```
validate_annotations(run_id=RUN_ID, language="python")   # or "c" / "cpp"
```

then dispatch the matching validator agent (`dftracer-validate-python` /
`-c` / `-cpp`).

`ml_annotate_project` already runs this internally (`validate=True`) and returns
`validation.passed`. **That does not excuse the manual path.** The dangerous
sequence is: MCP tool errors → agent hand-edits the file → nobody re-checks. A
hand edit is the least-trusted change in the pipeline, and a tool that errored
may have left a file half-written.

Do not proceed to the build, and do not report success, until validation returns
`passed: true` with zero findings and zero project issues. Otherwise report the
findings verbatim (`file:line`, function, the uninstrumented critical call) and
escalate.

It enforces: every I/O / checkpoint / collective-comm function instrumented;
init AND finalize present (a missing finalize truncates the trace); app-parameter
metadata emitted; annotated functions pass the cost gate — with `dft_ai.*`
AI-API regions exempt; and every file still parses.


---

## Constraints

- Prefer the one-call fast path; the manual recipe only repairs what the tools got wrong.

### From `dftracer-ml-annotate/SKILL.md § Permissions`

#### Permissions

This skill uses:

- **MCP (session + AI/Python annotation):** `session_create`, `session_configure`, `session_detect_ml_workload`, `session_install_dftracer`, `session_build_install`, `session_build_annotated`, `session_copy_annotated`, `session_annotation_report`, `session_run_smoke_test`, `session_run_with_dftracer`, `session_run_pipeline`, `session_analyze_traces`, `session_split_traces`, `session_optimization_iteration`, `session_search_local_papers`, `session_ml_append_lesson`, `session_lessons_sync_preview`, `session_lessons_sync_pr`; `python_annotate_ai_file`, `python_annotate_file`, `python_extract_functions`, `python_estimate_file_costs`, `python_estimate_function_cost`, `ml_categorize_files`, `ml_annotate_plan`, `ml_annotate_project`, `annotate_add_app_metadata`, `validate_annotations`; `analyze`, `reader`, `split`, `stats`, `view`
- **Bash (in `workspaces/<session>/...` only):** `module`, `pip`, `torchrun-hpc`
- **Write / Edit:** `workspaces/<session>/*` only (dftracer and app share one venv; traces → `workspaces/<session>/traces/`)
- **Network (only via `session_lessons_sync_pr`, and only with `confirm=True`):** pushes a branch and opens a PR against `github.com/llnl-asr/dftracer-agents` — requires `GITHUB_TOKEN`/`GH_TOKEN` set; never runs automatically

Auto-update the lessons file with new pitfalls via `session_ml_append_lesson`; sync them to the source repo with `session_lessons_sync_preview`/`session_lessons_sync_pr` (confirmation required — never automatic). Never `sudo`; never write outside the project root.


---

### From `dftracer-ml-annotate/SKILL.md § Context economy: query the graph, don't read the tree`

#### Context economy: query the graph, don't read the tree

Before any step that would open source files, use the `graphify` knowledge graph
(project dependency `graphifyy`, CLI `graphify`):

```bash
graphify query "<target>" --budget 1200   # locate: NODE <sym> [src=file loc=Lnn]
graphify explain <symbol>                 # definition + callers/callees
graphify affected <symbol> --depth 2      # blast radius before you change it
graphify update .                         # refresh after edits (~4s, no LLM)
```

Measured on this repo: locating cost 986 tokens vs 29,456 to read the three
relevant files (3.3%). Run `affected` before editing any shared function and
state the blast radius. Use the CLI, never `graphify-mcp` — its extra tool
schemas would sit in context permanently. See [[dftracer-context-economy]].

## Corner cases

- See LESSONS.md for the dated cases met so far.

## Parallel plan

Independent calls go out together in one message: predictor calls on different inputs, and lookups such as knowledge_search or lesson_search, do not depend on each other. Sequential, and why: the validator runs after the predictor whose result it rules on, and the corrector only after the validator's verdict. Larger fan-outs build on [[tools_parallel]] (parallel_plan / parallel_call / parallel_validate).

## Corrector

dftracer_annotate_ml_check failures and their repairs:
- `(<bucket>): ML region present` false: re-run ml_annotate_project, or python_annotate_ai_file(filepath, category=<bucket>) for that file (dftracer_annotate_ai).
- `DataLoader workers traced` false: pass a module-level worker_init_fn that initializes dftracer in each worker (Python Rule 2b, picklable under spawn).
- Python checks (parses, coverage and cost gate, properties, init/finalize): as in dftracer_annotate_python's Corrector.
Check again; the same failure twice stops for the user.

## Self-heal

- If ml_categorize_files puts a file in the wrong bucket, fix _BUCKET_PATTERNS in the tool (code-only matching) and add the file shape to checks.json.

## Self-learn

- What is explored here becomes a capability: see capability_builder. A new annotation failure is a lesson here (LESSONS.md, symptom -> root cause -> fix, dated); a new verified recipe is a WORKED.md entry; a new workload or software met while annotating becomes workload_<x> / software_<x>.

## What works

Verified recipes, dated, as in WORKED.md (full list there):

- [tool:ml_annotate_project] fast path: ml_annotate_plan(run_id, threshold=20) to review, ml_annotate_project(run_id, threshold=20) to execute (categorize, cost gate with the dft_ai exemption, python_annotate_ai_file for semantic buckets, python_annotate_file for the rest, validate=True), then annotate_add_app_metadata on the entry file and validate_annotations(language="python"). (dftracer-ml-annotate STEP 4 FAST PATH)
