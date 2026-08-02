---
name: software-rocm
description: >
  Generic ROCm/HIP/rocprofiler-sdk knowledge for dftracer HIP tracing on AMD GPU
  systems (e.g. MI300A) — init-ordering requirements, coexistence with the
  PyTorch profiler, ROCm base-vs-patched tree matching, and RCCL transport
  selection. Load this skill for any dftracer session with
  DFTRACER_ENABLE_HIP_TRACING or any ROCm/HIP/RCCL build or run issue, not tied
  to one specific app.
---

Cross-references: [[dftracer-install]] [[software-mpi]] [[software-molformer]] [[workload-scaffold]]

---

## HIP tracing (rocprofiler-sdk) requires a strict init ORDER, not just the right flags

`dftracer` built with `DFTRACER_ENABLE_HIP_TRACING=ON` calls
`rocprofiler_force_configure()` from `HIPFunction::initialize()`, which runs inside
`dftracer.initialize_log()`. rocprofiler-sdk tolerates that call in exactly ONE
window: after the HSA/HIP shared libraries are LOADED, but before `hsa_init()` has
run. Bisected on an MI300A compute node (ROCm 6.2.4, rocprofiler-sdk 0.4.0, torch
2.6.0+rocm6.2.4):

| case | ordering | result |
|---|---|---|
| A | `initialize_log()`, no torch imported at all | **SIGABRT**: `Error Calling hsa_iterate_agents: HSA_STATUS_ERROR_NOT_INITIALIZED` then `Check failed: '_v' Must be non nullptr` |
| B | same as A + narrow `HIP_VISIBLE_DEVICES` after init | same SIGABRT (not about visible-device masking) |
| C | `import torch` + touch GPU, THEN `initialize_log()` | no crash, but **zero HIP events** — force_configure is already locked, silently |
| D | `import dftracer.dftracer` first, then C | same silent no-op |
| E | `import torch` (module load only), THEN `initialize_log()`, THEN touch GPU | **works**: `HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY` all emitted |

**Case C/D is the dangerous one** — it looks healthy and produces a trace with every
other category present, so a missing-HIP-events report is easy to misread as "this
app does no GPU work" rather than an init-ordering bug.

**How to apply:** ensure something imports torch (module load only is enough) BEFORE
the first `initialize_log()` in every process that will use a GPU, and before any
code touches the GPU. For frameworks that initialize dftracer at a process entry
point you do not own (e.g. Ray's `default_worker.py`), do it with a
`sitecustomize.py` on `PYTHONPATH` gated on `DFTRACER_ENABLE == "1"` — `site` imports
it at interpreter startup, before any script body, and it needs no edits under
`site-packages`.

Also: `DFTRACER_ENABLE_HIP_TRACING` is COMPILE-time only; there is no runtime env var
to turn HIP tracing off once the build has it. Build against a ROCm >= 6.2 prefix
with `CMAKE_PREFIX_PATH=$ROCM_PATH` or `find_package(rocprofiler-sdk)` fails and it
silently compiles out (no error, just no HIP tracing).

## PyTorch profiler + dftracer HIP tracing coexist for exactly ONE profiler window

`pydftracer` (develop) ships `dftracer.python.torch.trace_handler`, which replays
every torch `FunctionEvent` into the dftracer log as `cat="PP"` events anchored on
kineto's `trace_start_ns`. Wire it as `torch.profiler.profile(...,
on_trace_ready=trace_handler)`.

Requirements that are easy to get wrong:
- `record_shapes=True` is REQUIRED — the handler derives its `input_size` arg from
  `event.input_shapes`.
- `profile_memory=True` feeds the `cpu_memory` / `device_memory_usage` args.
- `with_stack` should stay OFF: very expensive, and the handler never reads stacks.
- On ROCm, `ProfilerActivity.CUDA` is the correct enum (HIP is exposed through
  torch's `cuda` namespace).
- Stop the profiler BEFORE `dftracer.finalize()`, or the last active window's `PP`
  events are dropped — the handler writes through the dftracer logger.

**Measured co-existence result** (MI300A, ROCm 6.2.4, Ray Train 8-GPU DDP, dftracer
develop with HIP tracing ON): a single per-worker trace contained, simultaneously,
`HIP_RUNTIME_API` 972k, `KERNEL_DISPATCH` 146k, `POSIX` 89k, `STDIO` 32k, `dftracer`
17k, `PP` 777, `MEMORY_COPY` 347, `comm` 260 — so HIP tracing and the kineto-based
PyTorch profiler DO work together in the same process.

**But a SECOND profiler cycle re-arms roctracer and aborts.** With
`schedule(wait=2, warmup=1, active=5, repeat=2)` the run completed 7 training
iterations, then aborted at the SECOND profiler cycle boundary with the same
rocprofiler assertion as the init-ordering bug (`Check failed: '_v' Must be non
nullptr`). Tearing kineto's GPU collection down and re-arming it while dftracer's
rocprofiler-sdk context is live trips it. The failure is silent-looking from the app
side — e.g. Ray reports a generic `TrainingFailedError`/worker `SYSTEM_ERROR`, and
the abort line appears far below the Python traceback in the log.

**How to apply:** use `repeat=1` (one active profiler window per process) when HIP
tracing and the PyTorch profiler are both on. If more coverage is needed, widen
`active=` rather than adding cycles. If you need PP events spread across the whole
run rather than one end-of-run flush, use a REPEATING short cycle
(`schedule(wait=0, warmup=0, active=K, repeat=0)`, `repeat=0` = forever) so
`trace_handler` flushes every K steps — but this has NOT been validated against the
second-cycle-abort hazard above, so treat it as untested, not assumed safe, until a
session actually runs it to completion.

## ROCm base-vs-patched tree: dftracer and the torch wheel must agree

Building dftracer against a *patched* `rocm/<ver>hangfix` (or `leakfix`/`cgroupfix`)
tree while torch is a `+rocm<ver>` wheel built against the BASE (unpatched) tree
mixes two rocprofiler/HIP runtimes and can abort the app before training even
starts. Rebuilding dftracer against the SAME base ROCm tree the torch wheel targets
lets training start and the profiler engage on all ranks. This is a real qualifier
on "prefer the patched module for its bug fixes" — prefer patched when nothing else
constrains it, but dftracer and the torch wheel must agree on which tree, patched or
base, they were both built against. Also confirmed: a ROCm version with NO patched
variant at all (e.g. `rocm/7.1.1` on Tuolumne, where the torch wheel is pinned to
`+rocm710`) sidesteps this entirely — check whether a patched variant even exists
before assuming one must be chosen.

## RCCL transport selection

- `rccl/working-env` (or the site's equivalent RCCL environment module) is required,
  not optional, for multi-node RCCL collectives on ROCm — without it the job hangs
  instead of erroring. Its `FI_MR_CACHE_MONITOR=userfaultfd` setting is load-bearing;
  don't drop it when hand-rolling an environment instead of using the module.
- RCCL can silently fall back to slow host TCP sockets instead of the fast
  fabric-provider transport if the site's libfabric plugin (e.g. `aws-ofi-rccl`) is
  not on `LD_LIBRARY_PATH` — this can cost 40%+ of a collective-heavy workload's
  wall time with zero errors or warnings. Fix: add the plugin's lib dir to
  `LD_LIBRARY_PATH`. Do NOT set `NCCL_NET` to a guessed provider name (e.g.
  `NCCL_NET=libfabric`) as a "fix" — if it doesn't exactly match the plugin's own
  registration name (e.g. the AWS plugin registers as `AWS Libfabric`, not
  `libfabric`), NCCL/RCCL treats the mismatch as a hard failure that is easy to
  misdiagnose as an ABI incompatibility rather than a naming mismatch. Leave
  `NCCL_NET` UNSET and let RCCL auto-select once the plugin is actually resolvable
  on `LD_LIBRARY_PATH`.
- A same-system A/B between `gloo` and `nccl`/`rccl` backends is not a valid
  transport comparison if BOTH ended up on host TCP due to the plugin-path issue
  above — verify the actual transport in use (not just which backend name was
  requested) before drawing a conclusion from a backend comparison.

## PyTorch/Lightning DDP fork-bomb on a non-`--exclusive` single-task `flux run`

Launching node-local multi-GPU DDP training via `flux run` without `--exclusive` can
silently fork-bomb the node — Flux's PMI shim sets `PMI_RANK=0`/`PMI_SIZE=1`/
`PALS_*`/`LDCS_RANKINFO` for ANY `flux run`, even a plain single-task non-MPI one,
and every spawned DDP child inherits the SAME `PMI_RANK=0` unchanged, so none of them
recognize themselves as an already-spawned child — each re-triggers spawning its
siblings, recursively, without bound. This is generic to any subprocess-based
multi-GPU launcher (PyTorch Lightning's `ddp` strategy, raw `torch.distributed`
subprocess launch), not ROCm-specific, but reliably shows up on ROCm/MI300A sessions
because the fork-bomb only actually fires once combined with a real error condition —
typically requesting more GPUs than a non-`--exclusive` job's fair-share slice
actually grants. See the `flux-alloc` skill's "PyTorch Lightning / torch.distributed
DDP on a non-`--exclusive` single-task `flux run` can fork-bomb the node" section for
the full fix (always launch with `--exclusive`, and `unset PMI_RANK PMI_SIZE PMI_FD
PALS_APID PALS_APINFO PALS_NODEID PALS_RANKID PALS_SPOOL_DIR LDCS_RANKINFO` before
invoking the training process) and the detection recipe — not duplicated here to
avoid drift between the two copies.
