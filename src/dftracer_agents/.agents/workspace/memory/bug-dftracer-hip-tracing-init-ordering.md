---
name: bug-dftracer-hip-tracing-init-ordering
description: dftracer HIP tracing (rocprofiler-sdk) only works if initialize_log() runs AFTER `import torch` but BEFORE the first GPU touch — otherwise SIGABRT or silent no-op
metadata:
  type: project
---

**Canonical home:** see the `software-rocm` skill ("HIP tracing requires a strict
init ORDER" section — this file's full content is now persisted there).

dftracer built with `DFTRACER_ENABLE_HIP_TRACING=ON` calls
`rocprofiler_force_configure()` from `HIPFunction::initialize()`, which runs
inside `dftracer.initialize_log()`. rocprofiler-sdk tolerates that in exactly
ONE window: after the HSA/HIP shared libraries are LOADED, but before
`hsa_init()` has run.

Bisected on an MI300A compute node (ROCm 6.2.4, rocprofiler-sdk 0.4.0,
torch 2.6.0+rocm6.2.4), four probe cases:

| case | ordering | result |
|---|---|---|
| A | `initialize_log()`, no torch imported at all | **SIGABRT**: `Error Calling hsa_iterate_agents: HSA_STATUS_ERROR_NOT_INITIALIZED` then `F ... agent.cpp:640] Check failed: '_v' Must be non nullptr` |
| B | same as A + narrow `HIP_VISIBLE_DEVICES` after init | same SIGABRT (so the crash is NOT about visible-device masking) |
| C | `import torch` + touch GPU, THEN `initialize_log()` | no crash, but **zero HIP events** — force_configure is already locked, silently |
| D | `import dftracer.dftracer` first, then C | same silent no-op |
| E | `import torch` (module load only), THEN `initialize_log()`, THEN touch GPU | **works**: `HIP_RUNTIME_API`, `KERNEL_DISPATCH`, `MEMORY_COPY`, `SCRATCH_MEMORY` all emitted |

**Why:** case C/D is the dangerous one — it looks healthy and produces a trace
with every other category present, so a missing-HIP-events report is easy to
misread as "this app does no GPU work".

**How to apply:** ensure something imports torch before the first
`initialize_log()` in every process that will use a GPU. For frameworks that
initialize dftracer at a process entry point you do not own (e.g. Ray's
`default_worker.py`), do it with a `sitecustomize.py` on `PYTHONPATH` gated on
`DFTRACER_ENABLE == "1"` — `site` imports it at interpreter startup, before any
script body, and it needs no edits under `site-packages`.

Also: `DFTRACER_ENABLE_HIP_TRACING` is COMPILE-time only; there is no runtime
env var to turn HIP tracing off once the build has it. Build against a ROCm
>= 6.2 prefix with `CMAKE_PREFIX_PATH=$ROCM_PATH` or
`find_package(rocprofiler-sdk)` fails and it silently compiles out.

See [[bug-dftracer-torch-profiler-rocprofiler-conflict]],
[[software-ray-molformer]], [[software-rocm]].
