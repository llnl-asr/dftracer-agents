---
name: bug-dftracer-torch-profiler-rocprofiler-conflict
description: dftracer HIP tracing + PyTorch profiler co-exist for ONE kineto window; a second on_trace_ready cycle re-arms roctracer and aborts rocprofiler — use repeat=1
metadata:
  type: project
---

**Canonical home:** see the `software-rocm` skill ("PyTorch profiler + dftracer HIP
tracing coexist for exactly ONE profiler window" section — this file's full content
is now persisted there).

pydftracer (develop) ships `dftracer.python.torch.trace_handler`, which replays
every torch `FunctionEvent` into the dftracer log as `cat="PP"` events anchored
on kineto's `trace_start_ns`. Wire it as
`torch.profiler.profile(..., on_trace_ready=trace_handler)`.

Requirements that are easy to get wrong:
- `record_shapes=True` is REQUIRED — the handler derives its `input_size` arg
  from `event.input_shapes`.
- `profile_memory=True` feeds the `cpu_memory` / `device_memory_usage` args.
- `with_stack` should stay OFF: very expensive, and the handler never reads
  stacks.
- On ROCm, `ProfilerActivity.CUDA` is the correct enum (HIP is exposed through
  torch's `cuda` namespace).
- Stop the profiler BEFORE `dftracer.finalize()`, or the last active window's
  PP events are dropped — the handler writes through the dftracer logger.

**Measured co-existence result** (MI300A, ROCm 6.2.4, Ray Train 8-GPU
DDP, dftracer develop with HIP tracing ON): a single per-worker trace
contained, simultaneously, `HIP_RUNTIME_API` 972k, `KERNEL_DISPATCH` 146k,
`POSIX` 89k, `STDIO` 32k, `dftracer` 17k, `PP` 777, `MEMORY_COPY` 347,
`comm` 260. So dftracer's rocprofiler-sdk HIP tracing and the kineto-based
PyTorch profiler DO work in the same process.

**But:** with `schedule(wait=2, warmup=1, active=5, repeat=2)` the run
completed 7 training iterations and then aborted at the SECOND profiler cycle
boundary with the same rocprofiler assertion as the init-ordering bug:
`F ... agent.cpp:640] Check failed: '_v' Must be non nullptr`. Tearing kineto's
GPU collection down and re-arming it while dftracer's rocprofiler-sdk context
is live is what trips it.

**Why:** the failure is silent-looking from the app side — Ray reports it as a
generic `TrainingFailedError` / worker `SYSTEM_ERROR`, and the abort line
appears far below the Python traceback in the log.

**How to apply:** use `repeat=1` (one active window per process) when HIP
tracing and the PyTorch profiler are both on. If more coverage is needed,
widen `active=` rather than adding cycles.

See [[bug-dftracer-hip-tracing-init-ordering]], [[software-ray-molformer]].
