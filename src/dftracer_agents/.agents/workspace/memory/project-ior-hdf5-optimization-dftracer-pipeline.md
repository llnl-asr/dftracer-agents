---
name: project-ior-hdf5-optimization-dftracer-pipeline
description: IOR 4.0.0 + HDF5 1.14.5 dftracer pipeline on Tuolumne (session <session>) — reconfirms prior negative I/O-optimization finding under newer HDF5; full 4-dim checklist walked, final_report/ validated
metadata:
  type: project
---


**Canonical home:** see the `workload-ior` skill ("HDF5 1.14.5 reconfirmation" section
— align1m factorial results, tracing-state-must-match-across-arms methodology rule,
and the `session_detect`/`session_final_report` tool fixes are now persisted there).

## Latest session: `<session>` (IOR 4.0.0, HDF5 1.14.5 built from source, Tuolumne AMD MI300A / Cray PE)

**Status:** Complete. Full pipeline (session-setup -> annotate-c -> build-smoke ->
tracer/baseline -> analyzer+diagnoser -> optimizer x4 dimensions -> report ->
privacy-guard) ran to completion. `final_report/` assembled, validated
(structural: config/script wiring + PDF-render reproducibility confirmed;
full 512-rank flux re-execution not performed at validation time — no
active allocation), and privacy-clean.

**Central question answered:** does HDF5 1.14.5's newer collective-metadata
API (`H5Pset_all_coll_metadata_ops`/`H5Pset_coll_metadata_write`) change the
prior session's (`ior/20260710_172024`) negative finding that no I/O lever
beats the plain 4 KiB-real-transfer baseline for `-a HDF5 -b 16m -t 4k -s 32
-C -F` (file-per-process, 512 ranks/8 nodes)? **No.** Reconfirmed under
HDF5 1.14.5; both write and read deltas are within measurement noise.

**Full N-way comparator result (untraced, n=7 interleaved reps, fresh dirs
per rep — see [[feedback-app-pattern-swap-not-optimization]] methodology):**
v0 baseline write median 28905 MiB/s, read 17143 MiB/s. Five variants tested
(HDF5 alignment=1m, HDF5 collective-metadata-ops, ROMIO collective buffering,
MPI shared-memory collectives, NIC/NUMA affinity) — none beat v0 outside its
noise band. Alignment=1m showed an attractive +10% write gain but it was NOT
range-clean (1/7 reps overlapped v0) and paired with a clean -6.3% read
regression, so it was rejected rather than credited.

**Methodology bug found and fixed mid-session (important, generalizable):**
comparing a FUNCTION-mode-*traced* baseline against *untraced* optimization
variants inflated the untraced arms' apparent gain by ~60%, purely from
removed dftracer tracing overhead (measured independently at ~37% of
apparent write bandwidth for this 4 KiB-transfer workload). The affected
campaign was retracted and fully re-run with tracing state (`DFTRACER_ENABLE`)
identical across every arm. **Rule for future sessions: verify tracing state
is identical across every arm of any A/B before crediting a delta.**

**System-level cross-session pattern reconfirmed (3rd time):** NUMA/core-affinity
binding (`MPICH_OFI_NIC_POLICY=NUMA`) has no measurable effect on this
Tuolumne MI300A system — also seen on h5bench and ScaFFold sessions. Treat as
an established prior; don't re-test from scratch without new reason.
`cb_nodes`/`CRAY_CB_NODES_MULTIPLIER` confirmed ignored by Cray MPICH here.

**Pipeline-tooling bugs found and fixed this session:**
- `session_detect` build-tool misdetection: a vendored `testing/libnfs/CMakeLists.txt`
  inside IOR's source tree caused a false "cmake" classification for this
  Autotools project. Fixed in `detection.py` (scoped detection to repo root).
- `session_final_report` run-discovery gap: only recognized the fixed
  `baseline`/`annotated`/`opt<N>` naming ladder, silently dropping this
  session's free-form `tmp/*.sh` optimizer-variant scripts. Fixed to
  incrementally discover any non-ladder `tmp/*.sh` (mtime order).
- `session_final_report` no longer copies raw run/build logs into
  `final_report/logs/` by default (logs stay in the session's own `artifacts/`).

**Pipeline-tooling issue flagged, NOT fixed at tool level (recurring across
IOR sessions on error-macro-heavy C code):** the clang C annotator
(`clang_add_braces`/`clang_annotate_file`) mis-places `DFTRACER_C_FUNCTION_END()`
— duplicating it ahead of every internal `HDF5_CHECK()`-style error-check
macro instead of once per real return path, and placing `END()`/`FINI()`
after an unreachable `return` in top-level `main()` functions. Documented as
PC8. Corrected by hand again this session in `aiori-HDF5.c`/`ior-main.c`/
`contrib/cbif.c` — will recur on the next IOR (or similarly-styled C) annotation
pass until fixed at the tool level.

**Known gaps disclosed in this session's report (not retrofitted):**
`session_service_start`/`stop` was not bracketed around the optimizer's
untraced recheck runs (policy rule 12 gap); the profiler force-closes
concurrent optimizer subagent steps as "superseded" so per-subagent timing
in `profile_status`/`PERFORMANCE.md` under-reports 3 of 4 dimensions;
`profile_bind` was called late (after STEPs 1-4 had already run).

**Unmeasured-but-applicable candidates for a future round (see REPORT.md
§11 Remaining Work / Resume Point for exact commands):** align1m x Lustre
striping factorial (untested); 15-20 replicate confirmation of align1m's
write-side gain (pdebug's 60-min wall limit blocked this); burst-buffer/Rabbit
near-node flash stage-out (never tried, needs a fresh `#DW`-flagged allocation).

**PDF rendering recipe (new, reusable):** no pandoc on this system; pure-Python
path works with no sudo: `pip install --target <local-dir> xhtml2pdf` (pulls
reportlab), then `markdown-it-py` (already present) to render markdown -> HTML,
`xhtml2pdf.pisa.CreatePDF` to render HTML -> PDF. Script pattern saved as
`final_report/scripts/render_pdf.sh` + `_render_pdf.py` for reuse in future
sessions needing a REPORT.pdf deliverable.

See also: [[project-ior-hdf5-optimization-dftracer-pipeline]] (this file,
updated in place), [[feedback-app-pattern-swap-not-optimization]].
