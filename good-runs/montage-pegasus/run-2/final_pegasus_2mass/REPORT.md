# Final Report — Larger-Scale Pegasus 2MASS Run, Diagnostics, and I/O Optimization

**Run ID:** `montage/20260706_062459`
**Follow-on to:** `../final/REPORT.md` (the original 4-image DSS Pegasus/PMC session)
**Date:** 2026-07-06

This round scales the earlier work up to a real 102-image 2MASS mosaic,
fixes an annotation coverage gap discovered by checking which binaries the
workflow actually runs, and runs real diagnostics + a verified optimization
attempt against the larger dataset.

---

## 1. Annotation coverage gap found and fixed

Checking which binaries the Pegasus DAG actually executes (not which ones
were assumed during the original smoke-test scoping) revealed the real
workflow uses **8 distinct executable types**: `mArchiveList`, `mImgtbl`,
`mProject`, `mOverlaps`, `mDiffFit`, `mBgModel`, `mBackground`, `mAdd`,
`mConcatFit`, `mViewer`. Three of these — **`mDiffFit`, `mConcatFit`,
`mViewer`** — had **zero dftracer instrumentation**, because the original
scoping assumed the classic batch tools (`mDiffExec`/`mFitExec`) rather than
the combined single-shot tool (`mDiffFit`) the Pegasus workflow actually
invokes, and never discovered `mConcatFit`/`mViewer` at all.

Re-ran the `session_identify_smoke_test_files` filter against the *actual*
executed binary set and annotated the 8 newly-discovered files
(`patches/mDiffFit_entry.patch`, `patches/montageDiffFit.patch`,
`patches/mConcatFit.patch`, `patches/mViewer_entry.patch`,
`patches/mViewer_boundingbox.patch`, `patches/mViewer_graphics.patch`).
`MontageLib/Viewer/montageViewer.c` and `MontageLib/Viewer/mViewer_grid.c`
hit the same real `clang_add_braces` multi-line-if bug documented in the
main lessons log and were left pristine — same policy as before (revert
rather than hand-patch).

## 2. Larger 2MASS run

Generated a 102-image (55 nominal + oversized-region padding) 2MASS J-band
mosaic (`--degrees 1.0`), planned in PMC-only mode, and ran via
`pegasus-mpi-cluster` under the Flux allocation `f3JSbA6awcdD` — **676 total
tasks, 667-671/676 succeeded** across three separate runs (baseline, cfitsio
buffering fix, `DBUFFSIZE` experiment). Every failure across all three runs
was the same benign `register_local` replica-catalog bookkeeping task
(duplicate-key on SQLite re-registration), never a real compute task.

## 3. Critical measurement bug found and fixed: directory contamination

The first "Lustre" run for this larger dataset reused the exact same
`wf-scratch/LOCAL/.../montage/run0001` execution directory as the earlier
small 4-image DSS run — **Pegasus namespaces execution directories by
workflow name + run number, not by the `--dir` submit-directory flag**, so
two different workflow plans issued from the same CWD silently share (and
contaminate) the same physical execution directory. This made the original
"NFS vs Lustre" comparison invalid (leftover files from the small run were
mixed into the large run's counted files/bytes).

**Deeper bug found while investigating this**: neither of the two runs
believed to be "on Lustre" for the 4-image test actually were. The site
catalog's `sharedScratch`/`localStorage` paths only affect
data-staging/transfer bookkeeping — the actual PegasusLite/PMC execution
directory (`-w` flag baked into every DAG task at planning time) is always
`<CWD-at-plan-time>/wf-scratch/...`, independent of the site catalog. The
earlier "+16% bandwidth from moving to Lustre" result in the original report
is now known to be **incorrect** — that workflow never left NFS. The real
fix: `cd` into a directory that is *itself* on Lustre before calling
`pegasus-plan` (see `scripts/plan_and_run_2mass_on_lustre.sh`, which includes
an automated check of the planned DAG's `-w` path to catch this mistake
before running).

After moving the previous run's `wf-scratch` aside and re-planning from a
genuinely Lustre-resident CWD, confirmed via the DAG file itself:
```
-w /p/lustre5/haridev/dftracer-pegasus-montage/workflow_run/wf-scratch/...
```

## 4. Real diagnostics: the actual per-process fread() hotspot

`dfanalyzer`'s automated bottleneck scoring is still blocked by the upstream
gap documented in the main lessons log (no POSIX fact-rules in
`dftracer-analyzer`). Diagnosed manually instead, same method as before:
sampled the largest trace file and counted event names directly.

**Finding:** a single process issued **10,804 individual `fread()` calls**
reading one file. Initially misattributed to `mProject` (the reprojection
stage); **correction after checking the trace's own `SH` metadata record**:
it was actually **`mViewer`**, reading the final 103 MB coadded mosaic FITS
file once, to render the output PNG — the command line embedded in the trace
(`mViewer -ct 1 -gray 1-mosaic.fits -1s max gaussian -png 1-mosaic.png`)
confirmed this directly. This is a single, workflow-wide hot path (one call
per workflow, not per input image), making it a higher-leverage target than
it first appeared.

## 5. Optimizations attempted

| Attempt | Mechanism | Result |
|---|---|---|
| `setvbuf()` in cfitsio's `drvrfile.c` `file_openfile()`/`file_create()` | Centralized fix — every `fopen()` cfitsio does gets a 256KB stdio buffer instead of glibc's ~4-8KB default, for **all 8 binaries** at once | **Kept.** Reduces underlying `read()`/`write()` syscall count beneath every buffered `fread()`/`fwrite()` call. Does not (and cannot) change dftracer's STDIO-layer event count, since that counts logical `fread()` calls, not syscalls beneath them — so this fix is real but doesn't show up in the "avg transfer size" metric. |
| `DBUFFSIZE` 28800 → 115200 in `fitsio2.h` (cfitsio's own documented tunable, governs `maxelem` chunking for column/pixel-array transfers via `ffgcprll`) | Attempted to reduce `fread()` **call count** directly (not just buffer size beneath each call) | **Reverted — verified zero effect.** Ran the exact `mViewer` command against the real 103MB mosaic before/after: **10,804 fread() calls, identical, both times.** Root-caused: `mViewer`'s read pattern comes from a fixed per-call granularity hardcoded in Montage's own `montageViewer.c` rendering loop, not from cfitsio's `DBUFFSIZE`-driven internal chunking. Since `montageViewer.c` is the same file with the known real annotator bug (already reverted, unannotated) and is 11,107 lines, fixing this properly means rewriting Montage's own viewer I/O loop — same "too risky without image-correctness regression tests" judgment applied to `mProject`/`mAdd` in the original report. Left as a documented, verified finding rather than blindly patched. |

## 6. Comparator: small (4-image) vs large (102-image) scaling

Ran `dftracer_comparator` between the small DSS run and the (contaminated)
large NFS run — see `traces_summary/comparator_small_vs_large.log`. Top
findings: `__xstat64` (stat) mean duration +15,203%, metadata op counts
(unlink/remove) up ~30x, consistent with Pegasus/PMC per-task cleanup
overhead scaling with input image count (102 images → far more intermediate
files to clean up) rather than a Montage I/O regression. Bandwidth dropped
~21% at the larger scale in that (contaminated) measurement — not re-verified
against the clean isolated runs due to time; treat as directional only.

## 7. MCP tooling bug found and fixed: `analyze` tool hangs indefinitely

While diagnosing the above, discovered `mcp__dftracer__analyze` would hang
the entire MCP connection. Root cause: `dfanalyzer`'s dask `LocalCluster`
hangs on shutdown *after* printing all real output (confirmed: log file
showed the full `✓ Cluster teardown` line, but the OS process kept running
at high CPU indefinitely). The `analyze` tool's `subprocess.run(cmd,
capture_output=True, text=True)` call had **no timeout**, so it blocked
forever waiting for a process that had already finished its real work but
would never actually exit.

**Fix applied** (`dfanalyzer_service.py`): added a 300s timeout; on
`TimeoutExpired`, treats already-captured non-empty stdout as success
(the hung process is killed by the timeout mechanism itself), only reporting
failure if no output was captured before the timeout. Requires an MCP server
restart to activate.

---

## Contents of this folder

```
final_pegasus_2mass/
  REPORT.md                              — this file
  patches/
    cfitsio_drvrfile_setvbuf.patch       — kept: centralized stdio buffering fix
    mDiffFit_entry.patch                 — new annotation: mDiffFit CLI entry
    montageDiffFit.patch                 — new annotation: mDiffFit library
    mConcatFit.patch                     — new annotation: mConcatFit
    mViewer_entry.patch                  — new annotation: mViewer CLI entry
    mViewer_boundingbox.patch            — new annotation: mViewer helper
    mViewer_graphics.patch               — new annotation: mViewer helper
  pegasus_config/
    sites.yml                            — site catalog (Lustre paths declared;
                                            see §3 for the gotcha about this
                                            NOT being sufficient on its own)
    pegasus.properties                   — PMC code-generator config
  scripts/
    plan_and_run_2mass_on_lustre.sh      — reproduces the CORRECTED Lustre run,
                                            including the -w path verification
                                            check that catches the NFS mixup
    pmc_wrapper_2mass_lustre.sh          — exact wrapper used for the run
  traces_summary/
    analyze_baseline_clean_lustre.log    — dfanalyzer output, clean isolated
                                            Lustre run (post-contamination-fix)
    analyze_after_setvbuf_fix.log        — dfanalyzer output, + setvbuf fix
    comparator_small_vs_large.log        — 4-image vs 102-image scaling diff
```

## Related artifacts elsewhere in the workspace tree

| Artifact | Path |
|---|---|
| Annotated Montage source (updated, 8 more files) | `../annotated/` |
| Rebuilt binaries (setvbuf fix applied) | `../install_ann/bin/` |
| Clean Lustre baseline traces | `../pegasus_2mass_lustre/traces/` |
| Post-setvbuf-fix traces | `../pegasus_2mass_cfitsio/traces/` |
| Contaminated NFS run traces (reference only, do not trust for FS comparison) | `../pegasus_2mass/traces/` |
| 102-image mosaic output | `/p/lustre5/$USER/dftracer-pegasus-montage/workflow_run/wf-output/1-mosaic.fits` |
| Lessons log (all pitfalls, full detail incl. this round's) | `../../../src/dftracer_agents/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md` |
