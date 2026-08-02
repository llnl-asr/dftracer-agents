---
name: project-ior-optimization
description: Completed IOR 4.0.0 HDF5 optimization on Tuolumne (session ior/20260625_004605); discovered optimal ROMIO + HDF5 configuration for VAST storage
metadata: 
  node_type: memory
  type: project
---

**Canonical home:** see the `workload-ior` skill (VAST ROMIO tuning table, superseded/
reconfirmed by later IOR sessions in the same skill).

Completed a full dftracer optimization loop for IOR 4.0.0 HDF5 collective I/O on Tuolumne (2026-06-24).

**Session:** `ior/20260625_004605`  
**Workspace:** `$PROJECT_ROOT/workspaces/ior/20260625_004605/`  
**Report:** `workspaces/ior/20260625_004605/session_report.md`

**Why:** Profiling dftracer's optimization pipeline end-to-end; IOR HDF5 on VAST exhibited high posix_seek_ops_slope and posix_data_ops_slope bottlenecks.

**How to apply:** Reference this session when revisiting IOR/HDF5 optimization or when explaining VAST-specific ROMIO tuning to others.

## Optimal configuration found

```bash
MPICH_MPIIO_HINTS="*:romio_cb_write=enable"
IOR: -a HDF5 -b 64m -t 16m -s 4 -c -Y
```

192 processes, 2 nodes, 48 GiB, Tuolumne VAST /p/vast1:
- Total time: 168.8s → 112.9s (-33%)
- Write BW: 352 → 557 MiB/s (+58%)
- Read BW: 1705 → 1991 MiB/s (+17%)
- POSIX calls: 667,363 → 73,991 (-89%)
- seek_slope: 362 → 9.96 (-97%)
- High/critical issues: 153 → 42 (-73%)

## VAST storage ROMIO rules (critical)

- `romio_cb_write=enable` → GOOD: aggregates scattered 512-KiB writes into 16-MiB pwrite calls
- `romio_cb_read=enable` → BAD: 2163 → 659 MiB/s read regression; VAST handles parallel reads natively
- `romio_ds_write=disable` → FATAL: 352 → 95 MiB/s write; never use on VAST

## MCP tool fix applied

`dfanalyzer_service.py` `_hydra_args()` was generating GNU-style `--flag value` syntax.
Fixed to Hydra positional overrides: `key=value`, `analyzer/preset=posix`, `analyzer.checkpoint=True`.
File: `$PROJECT_ROOT/dftracer-agents/mcp-tools/tools/dftracer/dfanalyzer_service.py`

## Flux env var propagation (Tuolumne)

`flux proxy` does NOT propagate shell env vars to compute nodes.
All env vars (MPICH_MPIIO_HINTS, DFTRACER_*, LD_LIBRARY_PATH) must be passed explicitly with `--env KEY=VALUE` in every `flux run` call.
