---
name: bug-cray-papi-731-rocp-sdk-abi-segfault
description: Cray papi/7.3.0.1 on Tuolumne SIGSEGVs at PAPI_library_init (rocp_sdk component ABI-incompatible with ROCm 6.4.2) — pin papi/7.2.0.2, which also gives the most presets (30)
metadata:
  type: project
---

`module load papi/7.3.0.1` (the NEWEST available, which a "load the latest" habit
picks) makes **any** process that calls `PAPI_library_init` segfault on Tuolumne:

```
E... agent.cpp:1226] size of rocprofiler agent struct used by caller is
     ABI-incompatible with rocprofiler_agent_v0_t in rocprofiler
Segmentation fault (core dumped)
```

Cause: that PAPI ships a `rocp_sdk` component which `dlopen()`s
`librocprofiler-sdk.so`, built against a different rocprofiler-sdk than ROCm 6.4.2
provides (0.6.0).

**Pin `papi/7.2.0.2`** (which is also the module default). Measured with dftracer's
`cmake/probes/papi_probe.c` on an MI300A node: 7.3.0.1 → SIGSEGV; 7.2.0.2 → 30
presets (5 hw slots / 7 fitting); 7.1.0.4 → 30; 7.0.1.2 → 19; 6.0.0.16 → 23.
7.2.0.2 is therefore both safe AND the richest, and it does include cache counters
(PAPI_L1_DCM, PAPI_L2_DCM/ICM/TCM, PAPI_L1_DCA, TLB, branch, FP/FMA/vector).

**Why:** this bit a laghos PAPI-tracing session. It is nastier than a normal build
break because the same crash hits both dftracer's build-time counter probe (which
then silently falls back to only PAPI_TOT_CYC,PAPI_TOT_INS) and the traced
application at run time.

**How to apply:** in the session `env.sh`, load `papi/7.2.0.2` explicitly, LAST and
on its own `module load` line (loading it alongside `rocmcc` gets it silently dropped
by the MODULEPATH change, leaving `CRAY_PAPI_PREFIX` empty with no error), and
`export PAPI_DIR="${CRAY_PAPI_PREFIX}"` — the modulefile does not set `PAPI_DIR`, and
without it cmake resolves the distro's ancient PAPI 5.6 at `/usr/include/papi.h`
instead. Full detail in [[system-tuolumne]]; session context in
[[project-laghos-papi-dftracer-baseline]].
