---
name: bug-module-harvest-swaps-toolchain-fixed
description: FIXED: smoke-test module auto-harvesting pulled another facility's modules and silently DOWNGRADED the compiler/MPI, breaking native builds
metadata:
  type: project
---

`_extract_module_load_lines()` / `_build_module_preamble()` in `mcp_tools/tools/session/session_tools.py` scan the app's own shell scripts for `module load` lines and prepend them to every run. On the argonne-lcf Megatron-DeepSpeed fork this harvested Argonne's stack (`conda`, `cudatoolkit`, `pytorch/2.0.1`, `frameworks/...`, `oneapi/...`, `graphics-compute-runtime/...`).

**The failure is far worse than "unknown module" warnings.** Lmod resolved what it could and SWAPPED the working toolchain:
```
Lmod is automatically replacing "cce/20.0.0" with "gcc/12.2.0"
Lmod is automatically replacing "PrgEnv-cray/8.7.0" with "PrgEnv-gnu/8.7.0"
cray-mpich/9.0.1 => cray-mpich/8.1.25
```
The build then died with `mpicxx: line 351: crayCC: command not found` → `Making C++ dataset helpers module failed, exiting.` Four consecutive smoke attempts were lost to this before it was root-caused.

**Fix (applied, verified):**
1. Added facility-launcher dirs to `_MODULE_SCAN_EXCLUDE_DIRS`: `alcf`, `olcf`, `nersc`, `ncsa`, `tacc`, `riken`, `cscs`, `jsc`, `polaris`, `aurora`, `theta`, `summit`, `frontier`, `perlmutter`.
2. Made every harvested load best-effort (`... 2>/dev/null || true`) so a foreign module can never abort the wrapper under `set -e`, and labelled the emitted block as best-effort.

Verified against both Megatron-DeepSpeed sessions: harvested lines went from the poisoning block to **0**.

**Generalizable lesson:** auto-harvesting `module load` lines from an app's scripts is inherently unsafe across sites — upstream repos routinely carry another facility's stack. Prefer the session's own validated `env.sh`; treat harvested modules as advisory only. A silent toolchain swap is far more damaging than a missing module, because the run continues with the wrong compiler.

See [[software-megatron-deepspeed]], [[software-dl-stack]].
