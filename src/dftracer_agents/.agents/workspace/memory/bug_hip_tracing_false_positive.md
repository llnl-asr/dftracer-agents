---
name: bug_hip_tracing_false_positive
description: session_detect enabled DFTRACER_ENABLE_HIP_TRACING for non-HIP apps just because the node has system-wide ROCm
metadata: 
  node_type: memory
  type: project
  
---

`session_detect` (src/dftracer_agents/mcp_tools/tools/session/detection.py, `_detect_info`)
set `hip_tracing_needed = features["hip"] or rocm_info["found"]`. On Tuolumne every node
has ROCm installed (MI300A APU cluster), so `rocm_info["found"]` is always True — this made
every session, even pure-CPU/MPI apps like h5bench, build with `DFTRACER_ENABLE_HIP_TRACING=ON`.

**Why:** discovered 2026-07-10 during h5bench session — session.json showed `hip: false` but
`hip_tracing_needed: true` and `DFTRACER_ENABLE_HIP_TRACING=ON` in the cmake flags, purely from
`rocm.found=true` (path `/opt/rocm-4.2.0`). Matches the existing rule in
[[feedback_dftracer_install_rocm_mpi]] ("skip ROCProfiler unless app uses ROCm") — the detect
tool wasn't honoring its own downstream rule.

**Fix applied:** `hip_tracing_needed = features["hip"]` only — decoupled from system ROCm
presence. Also broadened the actual HIP-usage source scan (added `.hip`/`.cu` suffixes,
`find_package(HIP)`/`enable_language(HIP)`/`ROCM_PATH` in CMakeLists/Makefile/configure.ac,
literal `.hip` files) so real HIP apps are still caught correctly. Same fallback anti-pattern
fixed in `annotation_ai.py`'s `session_detect_ml_workload`.

**How to apply:** if a future session shows `hip_tracing_needed: true` for an app with no HIP
source, this is the same bug resurfacing (or a new site). Grep `mcp_tools/` for
`rocm_info\[.found.\]` / `rocm_info.get\(.found.` being OR'd into any tracing/build decision —
system-fact detection (`rocm.found`) must never gate app-specific build flags on its own.
MCP server must be restarted after this kind of tool-code fix to take effect.
