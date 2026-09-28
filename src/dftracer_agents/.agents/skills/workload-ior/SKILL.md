---
name: workload-ior
description: >
  IOR-specific knowledge: build quirks, annotation pitfalls, ROMIO tuning on
  VAST NVMe storage, and optimal dftracer run configuration for IOR 4.0.0.
  Load this skill whenever working with IOR.
---

Cross-references: [[dftracer-annotation-lessons]] [[dftracer-pipeline]] [[software-mpi]] [[software-hdf5]] [[dftracer-preload-run]]

---

## Build — IOR 4.0.0

### autoreconf fails without -I config and stub files

```
configure: error: cannot find install-sh
X_AC_META: command not found
automake: error: required file './NEWS' not found
```

**Root cause:** IOR 4.0.0 ships without a pre-generated configure script; the
`X_AC_META` m4 macro lives in `config/` not the default autoconf include path.
automake also requires `NEWS` and `AUTHORS` even if empty.

**Fix:**
```bash
cd <source> && touch NEWS AUTHORS && autoreconf -fi -I config
```

---

### Linker fails with duplicate symbols on clang/lld

```
ld.lld: error: duplicate symbol: posix_aiori
ld.lld: error: duplicate symbol: mpiio_aiori
```

**Root cause:** `aiori.h` defines global variables without `extern`, causing
duplicate definitions when included in multiple TUs. Clang/lld is strict;
GCC < 10's `-fcommon` merged them silently.

**Fix:** Always build with `-fcommon` and `-fuse-ld=bfd`, and `make clean`
before rebuilding when changing `CFLAGS`:

```bash
CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon" \
LDFLAGS="-fuse-ld=bfd" \
./configure --without-hdf5 --without-ncmpi ...
make clean && make -j8
```

---

### session_build_annotated ignores custom CFLAGS/LDFLAGS for autotools

**Root cause:** `session_build_annotated` runs its own autoreconf+configure
without the project-specific flag overrides needed for IOR.

**Fix:** Build the annotated version manually:

```bash
rm -rf <ws>/build_ann/
mkdir -p <ws>/build_ann/ && cd <ws>/build_ann/
CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon -I<dftracer_inc>" \
LDFLAGS="-fuse-ld=bfd -L<dftracer_lib> -Wl,-rpath,<dftracer_lib>" \
LIBS="-ldftracer_core" \
<ws>/annotated/configure --prefix=<ws>/install_ann ...
make -j8 install   # build src/ only; skip contrib/ if broken
```

---

### HDF5 backend: stale autotools config ignores --with-hdf5

**Root cause:** Stale `.deps/`, `config.status`, `autom4te.cache` cause
autotools to skip re-detection of HDF5.

**Fix:**
```bash
make distclean
rm -rf .deps src/.deps autom4te.cache config.status config.log Makefile
export CPPFLAGS="-I${HDF5_PREFIX}/include"
export LDFLAGS="-L${HDF5_PREFIX}/lib -Wl,-rpath,${HDF5_PREFIX}/lib"
export LIBS="-lhdf5 -lz"
./configure --with-hdf5 --prefix=<install> ...
# Verify:
grep USE_HDF5_AIORI config.h  # must show 1
```

---

## Annotation Pitfalls — IOR

### FINI must appear AFTER ior_main()

Placing `DFTRACER_C_FINI()` before the benchmark call leaves all backend spans
untraced:

```c
int main(...) {
  MPI_Init(...);
  DFTRACER_C_INIT(NULL, NULL, NULL);
  DFTRACER_C_FUNCTION_START();
  ...
  ior_main(opts);           // ← real I/O happens here
  DFTRACER_C_FUNCTION_END();
  DFTRACER_C_FINI();        // ← AFTER ior_main(), not before it
  MPI_Finalize();
  return 0;
}
```

---

### Braceless single-line `if` (dryRun pattern)

Inserting `END` into a braceless `if` body makes the early return unconditional:

```c
// Before annotation:
if (dryRun) return NULL;

// WRONG — END steals the if body:
if (dryRun)
  DFTRACER_C_FUNCTION_END();
return NULL;   // unconditional now

// CORRECT — add braces first:
if (dryRun) {
  DFTRACER_C_FUNCTION_END();
  return NULL;
}
```

**Pre-check:** `grep -n "if.*return\|if.*continue\|if.*break" <file.c> | grep -v "{" | grep -v "//"`

---

### HDF5 forward declaration vs definition

`HDF5_Create` appears twice — declaration ends with `;`, definition has a body.
Only annotate the definition:

```bash
grep -n "HDF5_Create" file.c | grep -v ";$"   # shows definition only
```

---

### dftracer built without MPI/HDF5 support

Trace files exist but only contain POSIX events; `MPIIO_*` and `HDF5_*` spans
are absent.

**Fix:**
```bash
cmake -DCMAKE_INSTALL_PREFIX=<prefix> \
      -DDFTRACER_ENABLE_MPI=ON \
      -DDFTRACER_ENABLE_HDF5=ON \
      -DDFTRACER_ENABLE_FTRACING=ON <src>
make -j4 install
# Verify:
grep DFTRACER_MPI_ENABLE <prefix>/include/dftracer/core/dftracer_config.hpp
# Expected: #define DFTRACER_MPI_ENABLE 1
```

Note: IOR's C frontend tolerates `chid_t` in Cray HDF5 headers; only
dftracer/brahma (C++ frontend) cannot — see [[workload-h5bench]] for the fix.

---

### MCP tool over-detects error-checking macros, places duplicate END() calls

**Session:** ior/20260724_175545

When using MCP `clang_annotate_project` on HDF5-heavy code, the tool places
`DFTRACER_C_FUNCTION_END()` before EVERY error-checking macro (HDF5_CHECK,
MPI_CHECK, etc.) instead of just before the function's real return statement.

**Symptom:** HDF5_Open() got 15 extra DFTRACER_C_FUNCTION_END() calls at lines
270, 273, 280, 307, 312, 331, 335, 343, 360, 363, 369, 374, 378, 391, 395, 418
— one before each HDF5_CHECK macro — creating fragmented trace spans instead of
one continuous function span.

**Root Cause:** The tool correctly detects that HDF5_CHECK macros contain hidden
returns (they expand to `if (rc != 0) { ... return/goto; }`), but over-applies
the fix per dftracer-annotate-c Rule E (error-checking macros should NOT have
END before them — only visible return statements should).

**Fix (manual post-processing):**
1. Remove all intermediate DFTRACER_C_FUNCTION_END() calls before error-check macros
2. Place a SINGLE DFTRACER_C_FUNCTION_END() before the function's actual return statement
3. This creates one continuous span covering the entire function's execution

**Workaround:** After running `clang_annotate_project`, scan HDF5-heavy files
(especially `aiori-HDF5.c`) for duplicate END() calls and remove them manually.

**Note:** This is a known MCP tool limitation. Ideally the tool should be fixed
to not place END before error-checking macros. See dftracer-annotate-c PC8.

---

## dftracer MPI+HDF5 install on Tuolumne (Cray) — full working recipe (2026-07-06)

`session_install_dftracer` FAILS on Tuolumne two ways: it auto-enables HIP
tracing (no `rocprofiler-sdk/buffer.h` → fatal) and resolves HDF5 to the old
`/usr` 1.10.5. Install manually with **environment variables** (dftracer's
`setup.py` ignores `CMAKE_ARGS` and pip `--config-settings=cmake.args` entirely
— those silently build all-OFF defaults):

```bash
# 1. Patch the Cray HDF5 chid_t typo into a private include tree (Cray libs unchanged,
#    so IOR's ABI is identical). Copy from the crayclang variant:
HDF5_SRC=/opt/cray/pe/hdf5-parallel/1.14.3.7/crayclang/20.0
P=<ws>/tmp/hdf5_patched
cp -r "$HDF5_SRC/include" "$P/include"; ln -s "$HDF5_SRC/lib" "$P/lib"
chmod -R u+w "$P/include"
sed -i 's/H5Aread_async(chid_t attr_id/H5Aread_async(hid_t attr_id/' "$P/include/H5Apublic.h"

# 2. Build dftracer @develop. CRITICAL: module UNLOAD cray-hdf5-parallel first, else the
#    Cray cc-wrapper re-injects the unpatched -I.../cray/20.0/include ahead of $P.
module unload cray-hdf5-parallel
export DFTRACER_ENABLE_MPI=ON DFTRACER_ENABLE_HDF5=ON DFTRACER_ENABLE_FTRACING=ON
export DFTRACER_ENABLE_HIP_TRACING=OFF DFTRACER_DISABLE_HWLOC=ON
export MPI_C_COMPILER=$(which mpicc) MPI_CXX_COMPILER=$(which mpicxx)
export HDF5_ROOT=$P HDF5_DIR=$P
pip install --no-cache-dir --force-reinstall --no-deps -v \
  "git+https://github.com/llnl-asr/dftracer.git@develop"

# 3. Verify ALL three are ==1:
grep -E "DFTRACER_(MPI|HDF5|FTRACING)_ENABLE" \
  <ws>/venv/.../dftracer/include/dftracer/core/dftracer_config.hpp
```

---

## Annotated-build link chain (C app → C++ dftracer_core) on Tuolumne

Linking IOR (C) against `libdftracer_core.so` (C++, needs GLIBCXX_3.4.29 +
`libyaml-cpp`) hits a cascade. Working flags for the annotated `configure`:

```bash
DFT=<ws>/venv/lib/python3.13/site-packages/dftracer
CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon -I$DFT/include"
LDFLAGS="-fuse-ld=bfd -L$DFT/lib64 -Wl,-rpath,$DFT/lib64 \
         -Wl,--allow-shlib-undefined -Wl,--no-as-needed"
LIBS="-ldftracer_core -lstdc++"
# Runtime libs so configure can RUN test binaries AND smoke/prod runs work.
# The python module's libstdc++ has GLIBCXX_3.4.29 + libyaml-cpp; it MUST precede
# /usr/lib64 (whose libstdc++ 6.0.25 only has up to 3.4.25):
export LD_LIBRARY_PATH="$DFT/lib64:/usr/tce/packages/python/python-3.13.2/lib:$LD_LIBRARY_PATH"
```

Symptom → cause map:
- `undefined reference ...@GLIBCXX_3.4.26` at link → `-lstdc++` dropped by
  `--as-needed` (C main uses no C++ directly) → add `--no-as-needed` +
  `--allow-shlib-undefined` (NEEDED shlib syms resolve at runtime).
- `C compiler cannot create executables` (link OK) → runtime loader picked old
  `/usr/lib64/libstdc++.so.6` → prepend the python module's lib dir.
- `incompatible integer to pointer conversion ... DFTRACER_C_INIT(NULL,NULL,-1)`
  → `process_id` is `int *`; INIT args MUST be `NULL, NULL, NULL` (pass
  `init_args="NULL, NULL, NULL"` to clang_annotate_project, NOT `-1`).

Build `src/` only (`make -C src -j8 install`); `contrib/cbif.c` fails on
`open64`/`lseek64` implicit decls under cce — unrelated, skip it.

---

### clang_annotate_project bulk pass needs a post-pass sanity scan

On IOR 4.0.0 the bulk `clang_annotate_project` pass produced END-after-return
dead code in 6/12 annotated files, and once spliced `DFTRACER_C_FUNCTION_END();`
literally into the middle of a `WARNF(...)` format-string argument (breaking the
macro call across two lines → real clang syntax errors). Always grep-scan every
annotated file for `return ...;\n *END();` ordering and manually inspect any
file containing macro-wrapped `WARNF`/`ERRF`/`INFOF` calls near a `return`
before trusting the bulk tool's output.

`utilities.c` failed `clang_extract_functions` (returned 0 functions), so the
bulk pass silently skipped 100% of its functions (only added the `#include`).
Files like this need manual annotation via `clang_insert_line` for their real
I/O functions (e.g. `SetHints`, `ReadStoneWallingIterations`,
`StoreStoneWallingIterations`, `aligned_buffer_alloc/free`, `GetNumTasks*`).

---

## Storage on Tuolumne is Lustre (/p/lustre5), NOT always VAST

The VAST ROMIO rules below assume VAST NVMe. When the run dir is on
`/p/lustre5` (`stat -f -c %T` → `lustre`), those rules DO NOT apply — on Lustre,
collective buffering (cb_read/cb_write) usually HELPS (high per-request
latency), striping (`lfs setstripe`) is the key L3 lever, and data-sieving
tuning behaves oppositely to VAST. Always `stat -f` the output dir first.

Baseline (2026-07-06, Lustre, 8 nodes × 64 ranks, `-a HDF5 -b 64m -t 16m -s 8 -c`,
32 GiB shared file): Write 2197 MiB/s / 14.9s, Read 1332 MiB/s / 24.6s
(read is the weaker path). Annotated trace confirmed annotated spans +
MPI interception (MPI_Reduce/Barrier/Bcast) + POSIX interception (lseek/write/read).

## HDF5 1.14.5 reconfirmation (session `<session>`, IOR 4.0.0, HDF5 1.14.5 built from source)

Re-ran the full pipeline under HDF5 1.14.5 (built from source, per `feedback_always_source_hdf5`)
to check whether the newer collective-metadata API
(`H5Pset_all_coll_metadata_ops`/`H5Pset_coll_metadata_write`) changes the prior negative
I/O-optimization finding (no lever beats the plain 4 KiB-real-transfer baseline for
`-a HDF5 -b 16m -t 4k -s 32 -C -F`, file-per-process, 512 ranks/8 nodes). **Answer: no** — both
write and read deltas stayed within measurement noise under 1.14.5 too.

**Full N-way comparator result** (untraced, n=7 interleaved reps, fresh dirs per rep — see
`feedback-app-pattern-swap-not-optimization` methodology): v0 baseline write median
28905 MiB/s, read 17143 MiB/s. Five variants tested — HDF5 `align1m` (alignment=1m), HDF5
collective-metadata-ops, ROMIO collective buffering, MPI shared-memory collectives, NIC/NUMA
affinity — NONE beat v0 outside its own noise band. `align1m` showed an attractive +10% write
gain but it was NOT range-clean (1/7 reps overlapped v0) and paired with a clean -6.3% read
regression, so it was rejected rather than credited. An `align1m` x Lustre-striping factorial
sweep, and a 15-20 replicate confirmation run of `align1m`'s write-side gain, remain unmeasured
(a future round's natural next step — blocked this session by pdebug's 60-min wall limit).

**Methodology bug found and fixed mid-session — tracing state must match across every arm
(generalizable, not IOR-specific).** Comparing a FUNCTION-mode-*traced* baseline against
*untraced* optimization variants inflated the untraced arms' apparent gain by ~60%, purely from
removed dftracer tracing overhead (measured independently at ~37% of apparent write bandwidth
for this 4 KiB-transfer workload). The affected campaign was retracted and fully re-run with
`DFTRACER_ENABLE` identical across every arm. **Rule for future sessions: verify tracing state
is identical across every arm of any A/B before crediting a delta** — this is a standing
methodology check, not a one-off fix.

**Pipeline-tooling bugs found and fixed this session:**
- `session_detect` build-tool misdetection: a vendored `testing/libnfs/CMakeLists.txt` inside
  IOR's own source tree caused a false "cmake" classification for this Autotools project.
  Fixed in `detection.py` by scoping detection to the repo root.
- `session_final_report` run-discovery gap: it only recognized the fixed
  `baseline`/`annotated`/`opt<N>` naming ladder, silently dropping this session's free-form
  `tmp/*.sh` optimizer-variant scripts. Fixed to incrementally discover any non-ladder
  `tmp/*.sh` (mtime order) too.
- `session_final_report` no longer copies raw run/build logs into `final_report/logs/` by
  default (logs stay in the session's own `artifacts/`).

## Trace analysis speed + diagnose tool bug

- `mcp__dftracer__analyze` with MANY per-rank .pfw.gz files: `cluster_n_workers>1`
  RACES on the shared `.dftindex` build and silently ingests only a PARTIAL subset
  (non-deterministic: saw 4 procs/122k events, then 3 procs/63k, on the same 64-file
  dir). Use `cluster_type=local cluster_n_workers=1` for a reliable FULL read (64
  procs / 8 nodes / 1.75M events / 71,275 POSIX ops in ~9s). Wipe stale `.dftindex`
  + `checkpoint/` before re-analyzing. Do NOT pass `cluster_cores` (invalid key).
  **Re-confirmed 2026-07-10** at larger scale (779-file, 512-rank HDF5/Lustre baseline):
  `cluster_n_workers=8` raced on TWO separate attempts (fresh `.dftindex` wipe
  between them) and produced two different, both-wrong under-counts (2.96M and
  8.69M ops vs the true 40.7M from `cluster_n_workers=1`). The race is not
  file-count- or backend-specific — reproduces on both small-file POSIX and
  HDF5/MPI-IO runs. Default to `cluster_n_workers=1` for any IOR trace set until
  the dfanalyzer index build is made worker-safe (e.g. per-worker temp index
  merged at the end, or a lock around `.dftindex` creation).
- `mcp__dftracer__analyze` non-checkpoint mode (`checkpoint=False`/default) vs
  checkpoint mode: on the same 779-file trace set, non-checkpoint reported
  EXACTLY 2x the checkpoint-mode event/byte counts (896.5M vs 448.2M trace
  events) with identical bandwidth/avg-transfer/job-time — looks like a
  double-read or double-count bug in the non-checkpoint ingestion path.
  Checkpoint-mode's file/process counts matched ground truth (779 files/514
  procs vs the tracer's 773/512), so treat checkpoint mode as authoritative
  until this is fixed. Worth a targeted code fix in `dfanalyzer_service.py`
  (or wherever the two ingestion paths diverge), not just a skill note.
- `mcp__dftracer__diagnose` currently errors `'Diagnoser' object has no
  attribute 'diagnose_checkpoint'` — API drift vs installed dfdiagnoser. Read
  the `checkpoint/_flat_view_*.parquet` + `_raw_stats_*.json` directly instead.
- **Re-confirmed 2026-07-24** (HDF5 1.14.5, 512-rank/8-node, 2-replicate baseline):
  `dfanalyzer` on the LOGIN node with an explicit `cluster.n_workers` override
  (32, then 8) crashed both times with `DFTUtilsError: Resource temporarily
  unavailable` (each worker spawns its own multi-hundred-thread I/O runtime —
  thread-creation limits under the login-node's cgroup). What worked: run
  inside a dedicated flux allocation (even 1 node is enough for analyze/
  diagnose — no need to match the app run's node count) with the tool's
  DEFAULT worker sizing (no explicit `cluster.n_workers`). Try default sizing
  first inside an allocation; only pass an explicit worker count if the
  default clearly under/oversizes for that node.
- A `dfanalyzer` checkpoint run can silently ingest only a time-window SUBSET
  of a large multi-replicate trace set with no error: on this session's 2,971-
  chunk-file baseline it reported only 386 files / 299 procs / 46.7s job time
  (vs. the true 512 ranks × 2 replicates over a run that took far longer).
  The ingested subset was internally consistent (POSIX/MPI/HDF5 layer math
  checked out), so partial results aren't necessarily wrong, just incomplete —
  always cross-check `raw_stats_summary.unique_file_count`/
  `unique_process_count` against the actual file count on disk before trusting
  a diagnose severity list as a complete-run census.
- **2026-07-24**: IOR deletes its own test files on completion unless `-k`
  (keepFile) is passed — post-hoc `du`/`stat` on the output dir cannot confirm
  data was actually committed to the PFS. Pass `-k` if you need to verify
  written volume after the run, or rely on indirect evidence (e.g. a
  subsequent read-back phase completing at the expected rate/size).
- **2026-07-24 optimization re-verification**: at 512 ranks/8 nodes, HDF5
  1.14.5, `-a HDF5 -b 16m -t 4k -s 32 -C -F`, NONE of collective-metadata-ops,
  ROMIO cb, or NIC/NUMA affinity changed bandwidth outside noise (confirms
  the 2026-07-10 negative finding still holds under the newer HDF5). A
  `--hdf5.setAlignment=1m` variant showed a marginal, not-fully-clean write
  gain (+10%, p<0.01 but one rep overlapped baseline) at the cost of a clean
  −6.3% read regression — net negative for this read+write workload, not
  recommended.

---

## Smoke Test

Run as a single process:

```bash
./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /tmp/ior_smoke_test
```

**OpenMPI root in container:** Add `--allow-run-as-root` and env vars:

```bash
OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
  mpirun -np 1 --allow-run-as-root ./src/ior -a POSIX -b 1m -t 1m -s 1 -F -C -o /tmp/ior_smoke_test
```

---

## Trace Collection

### pfw files land in a subdirectory — must copy up before splitting

When `run_id` contains a slash (e.g. `ior/20260617_185032`), dftracer writes:

```
<ws>/traces/ior/20260617_185032-<hash>-app.pfw.gz
```

`session_split_traces` looks only in `traces/` directly:

```bash
mkdir -p <ws>/traces/ior
# ... run benchmark ...
cp <ws>/traces/ior/*.pfw.gz <ws>/traces/
# then call session_split_traces
```

---

### dftracer_service daemon (best practice)

```bash
SERVICE_BIN=<ws>/install_ann/bin/dftracer_service
mkdir -p <ws>/traces/service
DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=<ws>/traces/<RUN_ID> \
  DFTRACER_DATA_DIR=all DFTRACER_TRACE_INTERVAL_MS=1000 \
  "$SERVICE_BIN" start <ws>/traces/service
# ... run benchmark ...
"$SERVICE_BIN" stop <ws>/traces/service
```

---

## ROMIO Optimization on VAST NVMe (Tuolumne)

### Optimal configuration (IOR 4.0.0, HDF5, 192 ranks, 2 nodes, VAST storage)

```bash
MPICH_MPIIO_HINTS="*:romio_cb_write=enable"
# IOR flags:
-a HDF5 -b 64m -t 16m -s 4 -c -Y
```

| Metric | Baseline | Optimized | Change |
|--------|----------|-----------|--------|
| Total time | 168.8s | 112.9s | -33% |
| Write BW | 352 MiB/s | 557 MiB/s | +58% |
| Read BW | 1705 MiB/s | 1991 MiB/s | +17% |
| POSIX calls | 667,363 | 73,991 | -89% |
| seek_slope | 362 | 9.96 | -97% |
| data_slope | 74.3 | 2.19 | -97% |

### VAST-specific ROMIO rules

| Hint | Effect on VAST | Verdict |
|------|---------------|---------|
| `romio_cb_write=enable` | Aggregates scattered writes → large pwrite calls | **USE** |
| `romio_cb_read=enable` | Funnels parallel reads through aggregators; VAST handles natively | **AVOID** (−70% read BW) |
| `romio_ds_write=disable` | Forces individual small non-contiguous writes | **FATAL** (352→95 MiB/s) |

**Key:** VAST is NVMe-based, not Lustre. Tuning that helps on spinning-disk Lustre
(disabling data sieving to avoid read-modify-write) is catastrophic on VAST.
Always test with hints unset first, then add `cb_write` only.

For full MPI/ROMIO software strategies, see [[software-mpi]].
For HDF5-specific tuning, see [[software-hdf5]].

## Autotools build: annotated tree needs its OWN configure regen, not a copy (2026-07-24)

**Symptom:** `session_build_annotated` fails with
`[Errno 2] No such file or directory: '<ws>/annotated/configure'` (the tool
expects `annotated/configure` to already exist for autotools apps — it does
not run `autoreconf`/`./configure` from scratch itself).

**Root cause:** the annotation pass copies IOR's `.c`/`.h` sources into
`annotated/source/` but does not carry over the pre-patched `configure`/
`configure.ac`/`Makefile.in` from the plain `source/` tree. Naively copying
the SIBLING tree's generated `configure`/`Makefile.in`/`config/*` files
across is wrong too — `config.status` then regenerates `Makefile`s that
reference the wrong (or missing) input files (`cannot find input file:
'src/config.h.in'`), because `Makefile.in` embeds file lists specific to
the source tree it was generated from.

**Fix:** regenerate the annotated tree's OWN build system from its OWN
`configure.ac`:
```bash
cd <ws>/annotated/source
sed -i 's/AC_PREREQ(\[2.71\])/AC_PREREQ([2.69])/' configure.ac  # match system autoconf 2.69
autoreconf -fi -I config
```
Only fix `AC_PREREQ` if the system's `autoconf --version` is older than what
`configure.ac` demands (check first, don't blindly downgrade).

## `--with-hdf5` takes yes/no, not a path (2026-07-24)

IOR's `configure.ac` HDF5 stanza is `AC_ARG_WITH([hdf5], ...)` with no
argument value handling — `AM_CONDITIONAL([USE_HDF5_AIORI], [test x$with_hdf5
= xyes])`. Passing `--with-hdf5=<path>` silently sets `with_hdf5=<path>`
which is never `yes`, so `USE_HDF5_AIORI` stays false and `aiori-HDF5.c`
is never compiled — no build error, the binary just lacks the HDF5 backend.

**Fix:** always pass `--with-hdf5=yes` (or bare `--with-hdf5`) and supply the
HDF5 install location via `CPPFLAGS="-I<hdf5>/include"` /
`LDFLAGS="-L<hdf5>/lib -Wl,-rpath,<hdf5>/lib"` instead. Verify with
`grep USE_HDF5_AIORI config.log` after configuring (`USE_HDF5_AIORI_TRUE=''`
means enabled; `='#'` means disabled) — same pattern applies to `--with-mpiio`
and other `--with-*` backends.

## Annotated build needs `LIBS=-ldftracer_core` explicitly — no .pc file (2026-07-24)

dftracer's install ships no `libdftracer.pc` for pkg-config, so
`PKG_CONFIG_PATH` alone does not add the link flag. Configuring/linking
without `LIBS="-ldftracer_core"` compiles cleanly but fails at the final
link step with undefined symbols `initialize_main`, `finalize`,
`update_metadata_string`, `initialize_region`, `finalize_region`,
`finalize_region_cleanup` — these are the expansions of the
`DFTRACER_C_INIT`/`DFTRACER_C_FUNCTION_START`/`_END`/`DFTRACER_C_FINI` macros
from `<dftracer/dftracer.h>`. Always pass `LIBS="-ldftracer_core"` alongside
`CPPFLAGS`/`LDFLAGS` pointing at the dftracer install's `include`/`lib64`.

## Failed Configurations

Entries below were applied during optimization loops and caused regressions or had no effect.
Check this section before proposing any configuration for this workload/software/filesystem.

Format per entry:
  date, app, workload, filesystem, system, bottleneck,
  config_attempted, result, metrics_before, metrics_after, delta,
  root_cause, do_not_use_when

<!-- New failed-config entries are appended below by the optimization loop (Step 8d-iii-FAIL) -->

---
date: 2026-07-10
app: https://github.com/llnl/ior (tag 4.0.0)
workload: IOR HDF5 independent write/read, file-per-process (-F), real request size -t 4k
filesystem: lustre (/p/lustre5)
system: Tuolumne (AMD MI300A, cray-mpich/9.0.1), 512 ranks / 8 nodes
bottleneck: small transfer size (4KB) — 40.7M POSIX ops in the baseline trace
config_attempted: |
  (a) MPI-IO data sieving: MPICH_MPIIO_HINTS="*:romio_ds_write=enable:romio_ds_read=enable"
  (b) ROMIO collective/two-phase buffering: romio_cb_write=enable:romio_cb_read=enable + CRAY_CB_NODES_MULTIPLIER=2
  (c) lfs setstripe -c 4 -S 4m on the output dir (kept at the workload's real -t 4k)
  All held the app's real -t 4k transfer size fixed; only the underlying serving mechanism was tuned.
result: ALL NEUTRAL-TO-NEGATIVE (no valid technique beat the plain -t 4k baseline)
metrics_before: write 21737 MiB/s (CV 2.1%), read 12548 MiB/s (CV 2.4%), 5 replicates
metrics_after: |
  (a) data sieving: write 21385 (-1.6%), read 12341 (-1.6%)
  (b) collective buffering: write 21416 (-1.5%), read 11739 (-6.4%)
  (c) striping c4/S4m: write 21805 (+0.3%), read 12495 (-0.4%)
delta: within run-to-run noise for (a)/(c); small real read regression for (b)
root_cause: |
  IOR with -F (file-per-process) writes 4KB transfers CONTIGUOUSLY within each
  rank's own file, opened effectively on MPI_COMM_SELF (one rank, one
  aggregator). Data sieving has no non-contiguous gaps to coalesce here, and
  collective/two-phase buffering has no cross-rank aggregation opportunity
  (each file has exactly one writer). Independently, the Lustre client already
  coalesces the 4KB writes into large RPCs via its page cache before they ever
  reach the OSTs, so write bandwidth is already near-peak and OST-layout
  insensitive at this transfer size — none of the classic small-I/O-coalescing
  levers have anything left to do. See FBench (arXiv 2606.30197): collective
  I/O on Lustre can be up to 30x SLOWER than independent I/O for this exact
  contiguous file-per-process shape.
do_not_use_when: |
  IOR (or similar) is running file-per-process (-F) with a CONTIGUOUS per-rank
  access pattern on Lustre — data sieving, collective/two-phase buffering, and
  forced striping are all ineffective levers for this shape regardless of the
  raw transfer size being small. These techniques only pay off for shared-file,
  non-contiguous, or genuinely small-and-scattered access patterns.

CORRECTION to the 2026-06-24-adjacent striping finding below this entry: a
separate -19% write regression from `lfs setstripe -c4 -S4m` was measured
ONLY at an artificially inflated `-t 4m` transfer size (a pattern-swap
characterization run, not a valid optimization — see dftracer-optimizer
standing rule). Re-verified at the workload's real `-t 4k`: striping is
NEUTRAL (+0.3% write), not regressed. The -19% figure does not apply at 4k.

---
date: 2026-06-24
app: https://github.com/llnl/ior (tag 4.0.0)
workload: IOR HDF5 collective write
filesystem: vast
system: Tuolumne (AMD MI300A, cray-mpich/9.0.1)
bottleneck: posix_seek_ops_slope, posix_data_ops_slope
config_attempted: |
  MPICH_MPIIO_HINTS="*:romio_ds_write=disable"
result: REGRESSED
metrics_before: write BW 352 MiB/s, total time 140s
metrics_after:  write BW 95 MiB/s, total time 515s
delta: -73% write BW, +268% total time
root_cause: |
  VAST uses NVMe-backed parallel storage. ROMIO data sieving handles
  non-contiguous HDF5 collective I/O by read-modify-writing large aligned chunks.
  Disabling it forces ROMIO to issue thousands of individual small writes to
  non-contiguous regions, causing extreme I/O amplification. Unlike Lustre
  (spinning disk), VAST NVMe is not hurt by the read-before-write cost.
do_not_use_when: filesystem is VAST, NVMe-backed NAS, or any all-flash parallel storage

---
date: 2026-06-24
app: https://github.com/llnl/ior (tag 4.0.0)
workload: IOR HDF5 collective read
filesystem: vast
system: Tuolumne (AMD MI300A, cray-mpich/9.0.1)
bottleneck: posix_seek_ops_slope (read path)
config_attempted: |
  MPICH_MPIIO_HINTS="*:romio_cb_read=enable"
result: REGRESSED
metrics_before: read BW 2163 MiB/s
metrics_after:  read BW 659 MiB/s
delta: -70% read BW
root_cause: |
  VAST handles 192 concurrent reads natively and efficiently. Collective read
  buffering funnels all reads through a small set of aggregator processes,
  creating a coordination bottleneck. This helps on Lustre (high per-request
  latency) but hurts on VAST's NVMe fabric where parallel reads are optimal.
do_not_use_when: filesystem is VAST or any NVMe parallel storage where concurrent reads are cheap

