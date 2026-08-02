---
name: software-molformer
description: Build/annotate/run caveats for IBM MoLFormer (chemical-language transformer, PyTorch + PyTorch Lightning + apex) discovered on Tuolumne (AMD MI300A, ROCm).
---

# software-molformer

Lessons from annotating and optimizing IBM MoLFormer (github.com/IBM/molformer) with
dftracer on Tuolumne (AMD MI300A APU, ROCm 6.3.1).

## Duplicate `dftracer.initialize_log()` in imported (non-entry-point) files silently corrupts traces

**Symptom:** dftracer FUNCTION-mode tracing produces a trace file with 0 or near-0 events, or
one that looks structurally empty, with no error raised anywhere.

**Root cause:** `pubchem_encoder.py` and `dataset_pubchem.py` — both regular imported library
modules, not entry points — carried their own `dftracer.initialize_log()` (and matching
`finalize()`) calls, presumably copy-pasted from the entry-point annotation template. Because
these files are imported before the real entry point (`train_pubchem_light.py`) runs its own
`initialize_log()`, the C++ profiler singleton gets initialized (and in some import orders,
finalized) by the wrong caller, corrupting all subsequent annotated spans in the real run.

**Fix:** `initialize_log()`/`finalize()` must ONLY appear in the true entry-point script(s)
(here: `train_pubchem_light.py` and the 3 `finetune_pubchem_light*.py` files). Any imported
module that is annotated with `@dft_fn` for its own functions must NOT also call
`initialize_log()`/`finalize()` — those calls belong exactly once, at the top of the real
`if __name__ == "__main__":` entry point.

**Generically applicable:** this is not a MoLFormer-specific bug — any Python FUNCTION-mode
annotation pass that puts `initialize_log()` in more than one file (e.g. because an annotator
tool applied its "each file gets the full decorator template" logic uniformly instead of
distinguishing entry points from imports) will hit the same silent corruption. Worth checking
against the `ml_annotate`/python-annotator tool's own logic: it should only emit
`initialize_log()`/`finalize()` calls for files that are confirmed to be actual script entry
points (`if __name__ == "__main__"` present and/or named in the task's launch command), never
for every annotated file.

## ROCm/PyTorch wheel version must match the loaded ROCm module exactly

A torch wheel built for a different ROCm minor version than the currently-loaded `rocm/X.Y.Z`
module causes import-time or first-kernel-launch failures. On Tuolumne with `module load
rocm/6.3.1`, the PyTorch wheel must be the ROCm 6.3-series build, not e.g. a 6.1 or 6.4 wheel.

## `libcaffe2_nvrtc.so` needs an explicit `LD_LIBRARY_PATH` entry

Even with the correct ROCm-matched torch wheel installed, `libcaffe2_nvrtc.so` is not always
resolvable via the venv's own site-packages layout on this system; add
`${WS}/install/lib/python3.13/site-packages/torch/lib` to `LD_LIBRARY_PATH` explicitly before
running (see `final_report/scripts/run_baseline.sh` for the working pattern).

## `lightning_fabric`'s Ampere-capability check crashes on AMD GPU device names

PyTorch Lightning's `lightning_fabric` accelerator-capability probing code assumes NVIDIA-style
`torch.cuda.get_device_capability()` semantics/naming; on ROCm-backed devices with AMD GPU
names (e.g. MI300A) this probe can crash outright rather than gracefully falling back. Requires
a targeted skip/patch of the Ampere-capability check when running PyTorch Lightning on ROCm.

## apex must be built from the ROCm/apex fork, never upstream NVIDIA/apex

Upstream `NVIDIA/apex --cuda_ext` hard-requires `nvcc`/`CUDA_HOME` (calls
`/usr/local/cuda/bin/nvcc`), which does not exist on this ROCm cluster. Build
`https://github.com/ROCm/apex` instead, against the session's HIP toolchain — same class of
fix as building dftracer's own C core for HIP.

## bf16-mixed precision hurts small models; batch size is the real throughput lever

Measured on Tuolumne, 1 node x 4 MI300A GPUs, ~1M-parameter MoLFormer validation config, real
20K-molecule PubChem slice, DDP world_size=4, fixed 1-epoch/157-step work:

| Config | Throughput | vs baseline |
|---|---|---|
| baseline (batch=32/GPU, workers=2) | 536 samples/s | -- |
| bf16-mixed precision | 307 samples/s | **-43% (measured worse, rejected)** |
| num_workers=1 (vs 2) | 525 samples/s | ~neutral (noise) |
| batch=64/GPU | 970 samples/s | **+81%** |
| batch=128/GPU + workers=2 | 1638 samples/s | **+206% (best)** |

At this small model scale, GradScaler's per-step dtype-casting/loss-scaling overhead outweighs
any reduced-precision GEMM throughput gain — bf16-mixed should only be re-tested at the full
~350M-parameter production MoLFormer config, where GEMMs dominate more of the step time.
Batch-size scaling is the lever that actually amortizes Python-level per-step overhead
(dataloader iteration, autograd graph construction, kernel-launch dispatch) on this
unified-memory APU. `PYTORCH_HIP_ALLOC_CONF=expandable_segments:True` is confirmed NOT
SUPPORTED on this ROCm platform (explicit PyTorch runtime warning, silently a no-op) — the
+206% result is attributable entirely to the batch-size change, not this flag.

## Non-`--exclusive` single-task `flux run` + PyTorch Lightning DDP can fork-bomb the node

**Symptom:** launching the 4-GPU DDP training command via `flux run -N1 -n1 bash wrapper.sh`
(no `--exclusive`) produces zero stdout/trace output for tens of minutes, then hundreds of
identical `bash wrapper.sh` processes pile up on the node (verified via `ps aux` on the node —
process count kept growing, no error ever surfaced to the job's stdout/stderr).

**Root cause, two compounding issues:**
1. Flux's built-in PMI shim sets `PMI_RANK=0` / `PMI_SIZE=1` / `PALS_APID` / `PALS_NODEID` /
   `PALS_RANKID` / `PALS_SPOOL_DIR` / `LDCS_RANKINFO` in the environment for **any** `flux run`,
   even a plain single-task, non-MPI one. Every child process PyTorch Lightning's DDP
   subprocess launcher spawns inherits this SAME `PMI_RANK=0` unchanged (Flux does not rewrite
   it per child), so no child can ever tell "I am an already-spawned child" — each one
   re-evaluates "am I the parent that needs to spawn my siblings?" as true, causing infinite
   recursive self-relaunch.
2. This only actually fires when combined with a real error condition: launching
   `flux run -N1 -n1` WITHOUT `--exclusive` only grants the job a fair-share slice of the
   node's resources — here, 1 of the node's 4 GPUs — while the training command asks for
   `--gpus 4`. PyTorch Lightning's `MisconfigurationException` ("You requested gpu: [0,1,2,3]
   But your machine only has: [0]") is the trigger that sends the corrupted-env relaunch logic
   into its infinite loop instead of surfacing the exception once and exiting.

**Fix (apply both, defense-in-depth):**
- Always launch this app's DDP training with `--exclusive` on the `flux run`/`flux submit` —
  it needs the WHOLE node's 4 GPUs, not a fair-share slice. This alone fixes the actual GPU
  mismatch and is what the originally-working run scripts always did
  (`flux run -N 1 -n 1 --exclusive ...`) — dropping `--exclusive` (e.g. when a tool call
  doesn't add it, as `session_run_with_dftracer` does not by default) is the regression trigger.
- Before invoking `python`, unset the Flux/Cray PMI pollution:
  `unset PMI_RANK PMI_SIZE PMI_FD PALS_APID PALS_APINFO PALS_NODEID PALS_RANKID PALS_SPOOL_DIR LDCS_RANKINFO`
  so PyTorch Lightning's cluster-environment auto-detection falls back to its own
  `LightningEnvironment` (LOCAL_RANK-based, correctly self-terminating) instead of whatever
  MPI-like environment it infers from the PMI vars.

**If this ever recurs:** cancel the job immediately (`flux proxy <alloc> flux cancel <jobid>`)
and verify process cleanup on the node
(`flux proxy <alloc> flux run -N1 -n1 --requires=hosts:<node> ps aux | grep -c <script_name>`)
before relaunching — do not let a suspected fork bomb keep running while you investigate.

## HIP tracing + PyTorch profiler re-run — build recipe and app-specific findings

Re-ran MoLFormer under the LATEST `develop` dftracer + pydftracer with HIP tracing
(rocprofiler-sdk) and the PyTorch profiler both enabled. For the GENERIC ROCm/HIP
findings this surfaced (init-ordering, profiler-cycle rocprofiler conflict, ROCm
base-vs-patched tree matching, RCCL transport selection, PMI fork-bomb), see
`software-rocm` — not duplicated here.

**Build recipe that works for this app:**
- `pip install git+.../dftracer.git@develop` with `DFTRACER_ENABLE_HIP_TRACING=ON`,
  `CC=$(which mpicc) CXX=$(which mpicxx)`, `CMAKE_PREFIX_PATH=$ROCM_PATH`.
  rocprofiler-sdk is present in the site ROCm trees already (cmake config + headers
  + `librocprofiler-sdk.so`), so HIP tracing needs no extra dependency.
- `pydftracer` MUST come from `@develop` — the PyPI release has no
  `dftracer.python.torch` module, which is what provides `trace_handler`.
- Two install pitfalls hit building this: `bug-dftracer-stale-brahma-after-pip-uninstall`
  and `bug-dftracer-crayclang-rpath-shadows-libstdcxx`.

**Categories VERIFIED present** (single-rank smoke test AND a 4-rank DDP run, 5.46M
events): `HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY`,
`PAGE_MIGRATION`, `RCCL_API` (collectives), `PP` (torch profiler: `aten::*`,
`hipLaunchKernel`, rocsolver kernels), `POSIX`, `STDIO`, plus the app's own
annotation categories. Trace metadata's `used` map (`{LIBC_IO, HIP, PYTHON,
torch_profiler, python_function}`) is the cheapest authoritative check that a layer
actually fired.

**OPEN ISSUE — 4-rank DDP SIGABRT with a long profiler active window.** With 4 ranks
on one node, a rank dies with code -6 a few minutes into training whenever the torch
profiler runs a long active window (`wait=5,warmup=2,active=10`), with no Python
traceback. Bisection: NOT the torch profiler alone (restricting profiling to rank 0
still killed a non-profiling rank, pointing at peers desyncing rather than the
profiler API); HIP tracing alone (profiler off) ran 40+ min without aborting but is
very slow; a SHORT window (`wait=2,warmup=1,active=2`, rank 0 only) cleared the
point where the long window died and was still running at 20+ min when the
allocation expired — promising but not confirmed to completion.

**4-node / 16-GPU scale-out — two upstream bugs had to be patched first:**
1. Upstream hardcodes **LSF** (`os.environ["LSB_MCPU_HOSTS"]`) in the
   `num_nodes > 1` branch and then prints `NCCL_IB_CUDA_SUPPORT` unguarded — both
   raise `KeyError` on a Flux site. **Fix:** patch to honour a pre-set
   `MASTER_ADDR`/`NODE_RANK` supplied by the launch script and `.get()` the prints
   instead of subscripting.
2. **Launch ONE flux task per node** (`-N4 -n4 -c 96 -g 4`) and let Lightning spawn
   the 4 local ranks (`--num_nodes 4 --gpus 4`). Do NOT launch one task per GPU:
   flux then hands every task the SAME physical device and RCCL dies with
   `Duplicate GPU detected : rank N and rank 0 both on CUDA device 302000`
   (`ncclInvalidUsage`). With 1 task/node each task correctly sees
   `torch.cuda.device_count() == 4`.

Verified at 16 GPUs: DDP init succeeds ("All distributed processes registered.
Starting with 16 processes"), 16 per-rank app traces + 4 per-node `service_<host>`
counter traces, and categories `HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`,
`SCRATCH_MEMORY`, `PAGE_MIGRATION`, `RCCL_API`, `POSIX`, `STDIO` + app annotations.
A standalone cross-node all-reduce gate (4 ranks, 1/node) passes — run that gate
BEFORE the workload.

**CORRECTED — the 16-rank abort is NOT an OOM.** A fully instrumented run (per-sample
cgroup, NUMA, VRAM, per-process RSS) disproves an earlier OOM guess: at the instant
of the kill, cgroup `memory.max`/`current`/`peak` were 512GB/18GB/19.5GB,
`memory.events oom`/`oom_kill` were **0/0**, node `MemAvailable` was 489GB, VRAM used
per card was ~1.0GB (0.7%). **Lesson: never infer OOM from exit 137 alone** — measure
`memory.events` from INSIDE the job's own cgroup (a login-side probe reads the wrong
cgroup entirely).

**Real failure chain (measured):** one LOCAL rank dies with a silent `SIGABRT`
(Lightning reports `Child process ... terminated with code -6`) -> Lightning
forcefully SIGKILLs its siblings (the observed exit 137) -> peers see
`ncclRemoteError: remote process exited` -> flux's exit-timeout SIGKILLs the last
hung node. No Python traceback, no C++ `terminate called` message;
`PYTHONFAULTHANDLER=1` does not catch it either — consistent with an `abort()` inside
a native library while rocprofiler-sdk is attached. Reproduces at step ~19-36 of 40.
The torch profiler is NOT the trigger: a HIP-only 16-rank run (`PP == 0`, profiler
never fired) died the same way at `SeqNum=28`. Disabling PyTorch's watchdog
(`TORCH_NCCL_ASYNC_ERROR_HANDLING=0`, `TORCH_NCCL_ENABLE_MONITORING=0`) removes the
watchdog-initiated teardown but the underlying rank abort remains — the watchdog is
a messenger, not the cause.

**Why `PP` was empty at 4 nodes.** `PP` events only exist once a torch profiler
cycle CLOSES and `on_trace_ready` runs. `active` spanning every step with `repeat=1`
schedules the single flush AFTER the last step, so a run that dies at step 19 of 40
emits zero `PP` — and even a successful run would produce one huge end-of-run flush.
To get `PP` for ALL steps, use a REPEATING short cycle (`schedule(wait=0, warmup=0,
active=K, repeat=0)`) so `trace_handler` flushes every K steps — written and ready
but NOT yet executed (allocation expired), and it also collides with the
second-profiler-cycle rocprofiler hazard documented in `software-rocm`, so it needs
testing, not assumption, before relying on it.

**How to apply:** for this app, drive the torch profiler from ONE rank with a short
active window, and treat a non-profiling rank's abort as a collective-desync symptom
rather than a profiler bug — single-rank tracing is the reliable configuration today.

## Ray variant (`molformer_ray_descriptors.py`, Ray Train/Tune): multi-node bring-up bugs

The Ray-based variant of this app (distinct from the PyTorch-Lightning variant documented
above) hits several distinct, non-obvious multi-node bring-up bugs on Tuolumne. See also
`software-ray` for the general (not MoLFormer-specific) `ray start --head` jemalloc bug.

1. **`ray.init()` with no args silently builds an isolated single-node cluster** if
   `RAY_ADDRESS` isn't exported, even when a real multi-node cluster was already bootstrapped
   via `ray start --head` / `ray start --address=`. Symptom: the driver process stays alive at
   near-0% CPU indefinitely with `ScalingConfig(num_workers=N)` requesting more
   GPUs/workers than the accidentally-isolated 1-node cluster has, and Tune's
   `insufficient_resources_manager` warns "cluster only has ... GPUs available" every 60s
   forever. Fix: `export RAY_ADDRESS="<head_ip>:<port>"` before launching the training script.
2. `molformer_ray_descriptors.py` falls back to `DATASET_CSV_PATH = script_dir/../dataset/
   baseline/pubchem_filtered.csv` when the `DATASET_CSV_PATH` env var isn't set — this relative
   path does not exist under `annotated/src/../dataset` (the real dataset lives at
   `<WS>/dataset/baseline/`). Always export `DATASET_CSV_PATH` explicitly.
3. When bootstrapping head+worker manually with `ray start --head` (not the head's default
   auto-detected GPU count), do NOT pass `--num-gpus=0` to the head unless the head is
   genuinely a non-GPU control-plane node — on Tuolumne the "head" is a real MI300A GPU node
   like every other allocated node, and `ScalingConfig(num_workers=args.nodes*4)` expects
   4 GPUs contributed by EACH physical node, not just the workers.
4. A worker script that `sleep`s a fixed duration before `ray stop` (to keep its GPUs alive
   while the head trains) is a race: if actual training takes longer than the sleep, the
   worker drops out from under the head mid-run. Use a completion-marker file the head
   `touch`es on exit, polled by the worker, instead of a fixed sleep.
5. **RESOLVED 2026-08-02** (was "unresolved as of 2026-07-25" above): the stall was NOT a Ray
   placement-group deadlock or OOM — it was suspect (a), confirmed. Root causes below, found
   while running a fresh 4-node/16-GPU baseline (`baseline_4node` run_name) from scratch:

   a. **New run_name's `dataset/<run_name>` dir isn't a Lustre symlink by default.** Every
      prior run (`baseline`, `opt2`, `opt3`, `rerun_v2`) had `<WS>/dataset/<run_name>` manually
      symlinked to `/p/lustre5/.../ray_molformer/<run_name>/`; a brand-new run_name's
      `dataset/<run_name>` is auto-created as a plain LOCAL directory (violates Pipeline Policy
      #11). Fix: create the Lustre dir, symlink `dataset/<run_name>` to it, and set
      `DATASET_CSV_PATH` to the LITERAL Lustre path of an existing run's CSV (reuse the shared
      9MB input file, don't duplicate it) rather than building it off the possibly-unsymlinked
      `$WS/dataset/<run_name>/...`.
   b. **HF model cache must be on PFS, never `$HOME`** (home quota is tiny). Download via the
      session venv's `hf download <repo> --cache-dir $HF_HOME/hub` into a PFS path (e.g.
      `/p/lustre5/.../ray_molformer/hf_cache`), and export `HF_HOME` (not just
      `HF_HUB_OFFLINE`/`TRANSFORMERS_OFFLINE`) in EVERY place `HF_HUB_OFFLINE` is already
      exported in the runner script — head `ray start --head`, head's python launch, AND each
      worker's `ray start --address=` (Ray actors are forked using that call's env, not the
      driver's `os.environ`).
   c. **The app pins a SPECIFIC HF revision** (`MOLFORMER_REMOTE_CODE_REVISION =
      "7b12d946c181a37f6012b9dc3b002275de070314"` in `molformer_ray_descriptors.py`, chosen to
      avoid a breaking change in newer commits — see the "torch profiler" section above for
      why). A plain `hf download ibm/MoLFormer-XL-both-10pct` (no `--revision`) only fetches
      the `main` snapshot, which is a DIFFERENT commit — every actor requesting the pinned
      revision then fails deterministically with `LocalEntryNotFoundError`/`OSError`, reported
      inconsistently in logs because Tune only surfaces the FIRST failing actor before killing
      the trial (looks like "1 of 16 actors flaky" when it's actually "all actors, same cause").
      Fix: `hf download ibm/MoLFormer-XL-both-10pct --revision 7b12d946c181a37f6012b9dc3b002275de070314 --cache-dir $HF_HOME/hub`.
   d. **`transformers`' dynamic-module code cache (`$HF_HOME/modules/transformers_modules/...`)
      has a genuine concurrent first-WRITE race** across many actors calling
      `AutoModelForMaskedLM.from_config(..., trust_remote_code=True)` simultaneously for the
      first time (matches upstream `transformers` issue #27421's symptom class exactly) — even
      with the `hub/` snapshot cache fully pre-populated per (c). Fix: pre-warm by making ONE
      single-process call (`AutoConfig.from_pretrained(...)` with the pinned revision) from the
      launch script/main thread BEFORE the concurrent multi-actor run, so the `modules/` tree
      is fully populated single-threaded first; all actors then only READ.
   e. **Ray's own retry-loop check was broken independent of (a)-(d)**: the worker-side
      `ray start --address=` retry logic grepped stdout for the literal string `"Successfully
      started Ray runtime"`, which Ray's CLI never actually prints — the real success line is
      `"Ray runtime started."` (`SUCC scripts.py:1111`). This mismatch meant the retry loop
      ALWAYS treated every join as a failure and killed/retried it even when `ray start` had
      genuinely succeeded — inflating apparent "GCS registration flakiness" that mostly wasn't
      real. Fixed the grep pattern; a join-staggering delay (`sleep(rank*15)`) added while
      misdiagnosing this as GCS overload turned out to be unnecessary once the check was fixed
      and was removed again.
   f. **Topology: match `opt1_4node_runner.sh`'s PROVEN pattern exactly for 4-node scale** — no
      dedicated manager-only node. The head node ALSO runs a Ray Train worker actor
      (`--num-gpus=4`, same as the 3 worker nodes) so `ScalingConfig(num_workers=16)` = 4 nodes
      × 4 GPUs each, head included. A "dedicated GCS manager node" (head gets `--num-gpus=0`,
      5 total nodes) was tried to rule out GCS being starved by a co-located training actor —
      it did NOT fix anything (the real bug was (e)) and added an extra node for no benefit at
      this scale; reverted. Keep this pattern in mind only if scaling meaningfully past
      4 nodes, where GCS registration load genuinely could become head-bound.

   With (a)-(f) all fixed, the 4-node/16-GPU baseline completed 12 training iterations
   end-to-end (`loss=nan` from iteration ~10 onward is an independently-known, expected
   characteristic of this UNMODIFIED baseline code — not caused by any of the above fixes).

6. **The `dftracer_service` node-counter daemon can silently no-op even with `DFTRACER_ENABLE`/
   `DFTRACER_LOG_FILE` set correctly** when invoked inline (`taskset -c 0 dftracer_service start
   <dir> >> log 2>&1`, no error, no trace file produced, "No running server found" on the
   matching `stop` call) — root cause not yet isolated for the Ray multi-node case specifically;
   always verify a non-empty `traces/dftracer_service/<hostname>/` output file exists after a
   run before trusting node-counter data, per `feedback-dftracer-service-node-counters`.

7. **Getting full-run `PP` (torch-profiler-bridge) coverage, not just a sampled window**: the
   documented safe pattern (`bug-dftracer-torch-profiler-rocprofiler-conflict` — `repeat=1`,
   since `repeat>=2` crashes the process) combined with `wait=0, warmup=0,
   active=<very large, e.g. 1_000_000>` gives PP events for literally every training step in a
   single profiler window that outlives the whole run, instead of only the small
   `wait/warmup/active` sample the app's own v2 script defaults to. Verify coverage by counting
   `cat="PP"` events directly (see `bug-dftracer-stats-categories-zero-events` for why
   `dftracer_stats --report categories` can't be trusted for this) — don't assume presence in a
   few sampled chunks proves full coverage; PP was ~9.3k of 27.3M total events (~0.03%) in one
   verified run, easy to miss without scanning every compacted chunk.

8. **`default_worker.py`'s dftracer auto-init patch must import `torch` before calling
   `initialize_log()`**, or dftracer's HIP tracing (rocprofiler-sdk, compiled in via
   `DFTRACER_ENABLE_HIP_TRACING=ON`) crashes with `F ... agent.cpp:640] Check failed: '_v' Must
   be non nullptr` — this is exactly "Case A" from `bug-dftracer-hip-tracing-init-ordering`,
   hit specifically because `default_worker.py`'s dftracer-init patch runs at Ray's actual
   process entry point, before ANY of the user's own script imports (including `import torch`)
   have executed. Fix applied directly in the session venv's
   `ray/_private/workers/default_worker.py`: wrap a bare `try: import torch` (module load
   only, swallow ImportError) immediately before the `initialize_log()` call. Note this crash
   can still fire harmlessly during final process teardown (`__hip_module_dtor` /
   `__run_exit_handlers`) even with the fix — that's fine as long as it happens AFTER training
   and trace-writing complete, which the fix ensures (confirmed via `HEAD NODE - exit: 0` and
   all traces present after the crash line in the log).
