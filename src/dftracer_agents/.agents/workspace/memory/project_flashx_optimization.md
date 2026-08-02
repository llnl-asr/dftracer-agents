---
name: project_flashx_optimization
description: Flash-X I/O optimization on Tuolumne — serial-HDF5 root cause; +parallelIO + Cray aggregator/stripe tuning gave 7.6x
metadata:
  node_type: memory
  type: project
---

**Canonical home:** see the `workload-flashx` skill (this session's findings are
persisted there in full, alongside `software-mpi`/`software-hdf5`/`system-tuolumne`).

Flash-X (git@github.com:Flash-X/Flash-X.git) dftracer annotate→optimize on Tuolumne.

## COMPLETED 2026-07-08 — session `flash_x/20260708_201403`

Sedov 3D, Paramesh AMR (nblockx=9, lrefine_max=2), 384 ranks / 8 nodes, 18 HDF5
checkpoints ~6.7 GB, Lustre. **Critical-path write time 11.09 s → 1.45 s (7.6×);
write syscalls 22.4M → 0.99M.** Final report: `<WS>/artifacts/FINAL_REPORT.md`.

**Root cause:** `bash setup Sedov -auto -3d` builds the SERIAL HDF5 IO unit, whose
`io_h5file_interface.c` hardcodes `HDF5_MODE = 0` ("never COLLECTIVE") — so
`useCollectiveHDF5=.true.` is overridden to INDEPENDENT and rank 0 writes everything
(91% of bytes on one rank). Fix: `bash setup Sedov -auto -3d +parallelIO`.

**Ladder (order matters):** L1 `+parallelIO` (precondition; 11.09→6.80 s) → L2 ROMIO
`cb_nodes=8` alone is **accepted-but-IGNORED** by Cray MPICH (stuck at 2 aggregators,
5.53 s) → L2+L3 `cb_nodes=16` + `CRAY_CB_NODES_MULTIPLIER=2` + `lfs setstripe -c 16 -S 4M`
on a **fresh** dir ⇒ 16 aggregators, 1.45 s.

**Why:** hints/striping are no-ops while one rank does all writing.

**How to apply:** verify from traces, never from config flags. Count ranks issuing
≥1MB writes (= aggregators) and per-rank write BYTES (not calls — every rank writes
tiny log bytes, so a serialized run still shows "384 ranks writing"). Mean write size
misleads: ~957k tiny log writes are 99% of calls but 0.6% of bytes. Always run
`dftracer_split` before `dfanalyzer` (it silently truncates raw traces to ~1542
events / 1 proc).

Other Flash-X gotchas found: `flashx` **ignores a par-file argv** (always reads
`flash.par` from cwd); `setup` **wipes `object/`** (re-apply Makefile.h + dftracer
shim after); **non-ASCII in flash.par** (em-dash) silently drops that line.

Details persisted in skills [[workload-flashx]], [[software-mpi]], [[software-hdf5]],
[[system-tuolumne]], dftracer-trace-utils.

## Prior session context (`flash_x/<session>`)

- HDF5 1.14.5 built **from source**, never Cray. [[feedback_always_source_hdf5]]
- Trace mode that works: **FUNCTION** + **DFTRACER_DATA_DIR=all** + valid LOG_FILE dir.
- App checkpoints → **Lustre**; dftracer traces stay in workspace.
  [[feedback_lustre_io]] [[feedback_optimization_pipeline_traces]]

**PITFALL — flash.par is read with FORTRAN `read(1,'(A80)')`: the WHOLE line must be ≤80 chars.**
A long absolute Lustre `output_directory` (88 chars) truncates past col 80, dropping the
closing quote → unmatched-quote syntax error → parameter silently ignored → files written
to cwd. FIX: short relative symlink `ds` in the run dir → Lustre, set `output_directory = "ds/"`.
The `ds` symlink must exist in the **run cwd** (`object/`), since paths resolve against process cwd.

Pipeline dispatch order: session-setup → annotator → build-smoke → tracer → analyzer → optimizer.
