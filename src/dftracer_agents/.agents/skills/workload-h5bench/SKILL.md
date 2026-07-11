---
name: workload-h5bench
description: >
  H5Bench-specific knowledge: CMake build quirks, annotation edge cases
  (assert/else-if brace insertion), INI config format, dftracer integration,
  and Cray HDF5 compatibility.  Load this skill whenever working with h5bench.
---

Cross-references: [[dftracer-annotation-lessons]] [[dftracer-pipeline]] [[software-hdf5]] [[software-mpi]]

Source repo: https://github.com/hariharan-devarajan/h5bench

---

## Binary Names (look here first — wrong name = silent failure on compute nodes)

All h5bench binaries live under `build_ann/` (annotated) or `build/` (original).
The binary names do NOT always match the workload label. Use this table every time:

| Workload label       | Binary name                        |
|----------------------|------------------------------------|
| write                | `h5bench_write`                    |
| read                 | `h5bench_read`                     |
| append               | `h5bench_append`                   |
| overwrite            | `h5bench_overwrite`                |
| write_unlimited      | `h5bench_write_unlimited`          |
| hdf5_iotest          | `h5bench_hdf5_iotest`              |
| exerciser            | `h5bench_exerciser`                |

**CORRECTION (confirmed 2026-07-10 against hariharan-devarajan/h5bench @ master):**
There is NO `write_normal_dist` / `h5bench_write_var_normal_dist` binary or config
variant in this fork's current CMakeLists.txt or source tree — grepping the whole
source tree for `normal_dist`/`var_normal`/`NORMAL` returns nothing. This entry was
stale (likely inherited from a different h5bench fork/version) — do not plan around
it; it does not exist. The real, buildable workload set from this fork's
`add_executable(...)` calls is: write, write_unlimited, overwrite, append, read,
hdf5_iotest, exerciser (7 total, not 8).

Always verify with:
```bash
ls <WS>/build_ann/h5bench_*
```

### `hdf5_iotest` and `exerciser` are OFF by default — need explicit CMake flags

`CMakeLists.txt` gates both behind options that default OFF:
```
option(H5BENCH_EXERCISER "Enable Exerciser benchmark" OFF)
option(H5BENCH_METADATA  "Enable Metadata benchmark"  OFF)   # gates hdf5_iotest
option(H5BENCH_ALL       "Enable all benchmarks"      OFF)   # turns both ON
```
A plain `cmake -S source -B build` (no extra flags) silently builds only 5 binaries
(write, write_unlimited, overwrite, append, read) with no error or warning about the
missing exerciser/hdf5_iotest targets. **Always pass `-DH5BENCH_ALL=ON`** (or the two
individual `-DH5BENCH_EXERCISER=ON -DH5BENCH_METADATA=ON` flags) to get the full binary
set, and verify all 7 are present after build — don't assume a clean `make` output means
every target was configured.

---

## Build

### Cray HDF5 `chid_t` typo breaks dftracer/brahma (C++ frontend)

```
/opt/cray/pe/hdf5-parallel/1.14.3.7/cray/20.0/include/H5Apublic.h:932:29:
error: unknown type name 'chid_t'; did you mean 'hid_t'?
gmake[5]: *** [CMakeFiles/brahma.dir/...] Error 1
```

**Root cause:** Cray-patched HDF5 (and upstream 1.14.3) have a typo on line 932
of `H5Apublic.h` — `H5Aread_async` declares `chid_t` instead of `hid_t`.
Only the C++ compiler rejects it; IOR's C frontend tolerates it.

**Fix:**
```bash
# Download vanilla HDF5 1.14.3 (GitHub 404s on this system; use HDF Group FTP):
curl -fkL https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.14/hdf5-1.14.3/src/hdf5-1.14.3.tar.gz \
  -o hdf5-1.14.3.tar.gz
tar xf hdf5-1.14.3.tar.gz && cd hdf5-1.14.3
CC=mpicc ./configure --prefix=<ws>/hdf5_1.14 --enable-parallel \
  --enable-shared --enable-build-mode=production --with-zlib=/usr
make -j8 && make install

# Patch the typo:
sed -i 's/H5Aread_async(chid_t attr_id/H5Aread_async(hid_t attr_id/' \
  <ws>/hdf5_1.14/include/H5Apublic.h

# Update session.json to point at <ws>/hdf5_1.14 and re-run session_install_dftracer.
```

**Still present in 1.14.5:** confirmed 2026-07-10 that vanilla HDF5 1.14.5 (not just 1.14.3)
still ships the same `chid_t` typo at `H5Apublic.h:926` — this is not yet fixed upstream.
Apply the same sed patch regardless of which 1.14.x version is fetched; don't assume newer
patch releases have resolved it.

### dftracer build requires Cray PE runtime libs on LD_LIBRARY_PATH, not just at link time

When dftracer/h5bench binaries are built with `cce/20.0.0`, running them later needs the same
Cray runtime lib dirs on `LD_LIBRARY_PATH`, not just during the build:
`/opt/cray/pe/cce/20.0.0/cce/x86_64/lib` and `/opt/cray/pe/cce/20.0.0/cce-clang/x86_64/lib`
(for `libmodules.so.1`, `libfi.so.1`, `libcraymath.so.1`, `libf.so.1`). Write these into the
same `setup_dftracer_env.sh` wrapper used for build, and source it before every run/smoke test
too — not just before `session_install_dftracer`/`session_build_annotated`.

Note: MPI compatibility warning — MPICH 9.0.1 is outside brahma's tested range;
MPI-IO interception is disabled but POSIX and app-level annotation tracing work.

---

### CMake library name mismatch: `-ldftracer` vs `-ldftracer_core`

```
/usr/bin/ld: cannot find -ldftracer: No such file or directory
```

**Root cause:** dftracer installs as `libdftracer_core.so`, not `libdftracer.so`.
`session_install_dftracer` patches CMake to link `-ldftracer` which fails.

**Fix:** After `session_install_dftracer`, patch build files:
```bash
sed -i 's/-ldftracer\b/-ldftracer_core/g' build_ann/CMakeCache.txt
find build_ann/CMakeFiles -name "link.txt" \
  -exec sed -i 's/-ldftracer\b/-ldftracer_core/g' {} \;
grep -r "ldftracer[^_]" build_ann/   # must return nothing
```

**Link order:** `-ldftracer_core` must appear AFTER all `.o` files.
When CMake places it before objects (via `CMAKE_EXE_LINKER_FLAGS`), move it:
```bash
sed -i 's/ -ldftracer_core//g' link.txt
# Then append it at the END of the same line as the cc command (not a new line).
```

**Important:** Do NOT put `-ldftracer_core` on its own line in `link.txt`.
`cmake -E cmake_link_script` treats each line as a separate command;
a line with only `-ldftracer_core` is silently ignored.

---

### Patching CMakeCache.txt triggers MPI re-detection failure

**Root cause:** Editing `CMakeCache.txt` causes cmake to re-run configure,
which fails to find MPI in a container environment.

**Fix:** After editing `CMakeCache.txt`, add entries to skip MPI re-detection:
```
MPI_C_WORKS:BOOL=TRUE
MPI_CXX_WORKS:BOOL=TRUE
```

---

## Annotation Pitfalls

### `clang_add_braces` corrupts `assert()` macro call-sites

```
assert(pconfig->version ==
{
0)
}
Compiler: "error: expected ')' before '{' token"
```

**Root cause:** glibc's `assert(expr)` expands to an `IfStmt` with a `NullStmt`
then-body. `_collect_braceless` treated the `NullStmt` as an unbraced body and
split multi-line macro arguments.

**Fix in source_parser.py `_collect_braceless()`:**
```python
if kind == "IfStmt":
    _then_is_null = (len(inner) >= 2 and inner[1].get("kind") == "NullStmt")
    for i, child in enumerate(inner):
        if i == 0: continue  # condition
        if _then_is_null: continue  # assert()-style — skip ALL bodies
```

---

### `clang_add_braces` inserts standalone `{` before `else if`

```
} else
{
if (condition) {
Compiler: "error: expected expression before '{' token"
```

**Root cause:** In `_collect_braceless`, `else-if` bodies (index ≥ 2 that are
themselves `IfStmt`) were being wrapped, inserting `{` before the `else` keyword.

**Fix in source_parser.py `_collect_braceless()`:**
```python
if i >= 2 and child.get("kind") == "IfStmt":
    continue  # else-if: skip wrapping; recursion handles inner IfStmt
```

---

### `DFTRACER_C_INIT(NULL, NULL, -1)` causes segfault

```
Segmentation fault in initialize_main() at fgets call immediately after startup.
```

**Root cause:** The third argument is cast to `(int*)0xffffffffffffffff` → segfault.

**Fix:** Always use `NULL` for the process_id argument:
```c
DFTRACER_C_INIT(NULL, NULL, NULL)
```

---

### `DFTRACER_C_METADATA` / `DFTRACER_CPP_METADATA` are 3-arg macros, not 2-arg

`annotate_add_app_metadata` (C dialect) previously emitted the 2-arg form
`DFTRACER_C_METADATA("app", "h5bench_write")`, which fails to compile — the
real macro in `dftracer/include/dftracer/dftracer.h` is
`DFTRACER_C_METADATA(name, key, val)` (3-arg; `DFTRACER_CPP_METADATA` has the
same 3-arg shape). `name` is a **bare C identifier**, not a string literal —
it's used internally for `##name` token-pasting to declare a local variable,
so it must be unique within scope.

Confirmed 2026-07-10 on `h5bench_write.c`; manually patched to:
```c
DFTRACER_C_METADATA(dft_meta_app, "app", "h5bench_write");
```
Fixed permanently in the `annotate_add_app_metadata` MCP tool
(`src/dftracer_agents/mcp_tools/tools/session/annotation_validate.py`), which
now generates a unique `dft_meta_<key>`-style identifier per metadata call —
no more hand-patching needed in future sessions.

---

### `hdf5_iotest.c` and `h5bench_exerciser.c` annotate cleanly

Confirmed 2026-07-10: unlike `h5bench_write.c`, neither file triggers the
`clang_add_braces` `assert()`/`else-if` brace-insertion issues above — no
special handling needed for these two files.

---

### `clang_syntax_check` needs explicit include dirs for this session's toolchain

In this session's environment, `clang_syntax_check` only validates cleanly
when `extra_include_dirs` is passed pointing at BOTH:
- the venv's dftracer headers, e.g. `<WS>/venv/lib/python3.13/site-packages/dftracer/include`
- the session's source-built HDF5 include dir: `<WS>/hdf5_1.14/include`

Without both, syntax check fails to resolve `dftracer/dftracer.h` and/or
HDF5 headers even though the actual build succeeds (the build's own
Makefile/CMake already wires these paths; `clang_syntax_check` does not
inherit them automatically).

---

## Config Format

### h5bench_write expects INI key=value, NOT the JSON sample files

Passing a JSON file from `samples/` causes a segfault inside `fgets()`.
The JSON files are for the Python runner (`h5bench.py`); the binary needs INI:

```bash
cat > /tmp/h5bench.cfg << 'EOF'
MEM_PATTERN=CONTIG
FILE_PATTERN=CONTIG
TIMESTEPS=3
DELAYED_CLOSE_TIMESTEPS=0
COLLECTIVE_DATA=NO
COLLECTIVE_METADATA=NO
NUM_DIMS=1
DIM_1=1048576
DIM_2=1
DIM_3=1
EOF
mpirun -np 2 ./h5bench_write /tmp/h5bench.cfg /tmp/test.h5
```

### `h5bench_read` needs a PRE-EXISTING file matching its config dims — it does not create one

`h5bench_patterns/h5bench_read.c` (`main()`, `argv[2]` = "data file to read") opens
`argv[2]` and expects a dataset already shaped per the config's `NUM_DIMS`/`DIM_1`/etc.
Pointing it at a fresh/empty path does NOT fail loudly — it produces an HDF5
error-storm (`H5Sget_simple_extent_dims`: "not a dataspace", `H5Dclose`: "not a
dataset ID", `H5Gclose`: "not a group ID", ...) yet the process still **exits 0**
and dftracer still writes a (bogus) trace. A baseline collection run can look
"successful" (5/5 reps captured, non-empty trace) while every rep is silently
measuring an error path, not real read I/O — always grep the run log for these
signatures before trusting a `read` baseline.

**Fix: always run a two-phase write-then-read pattern**, never point
`h5bench_read` at a fresh/empty path:

```bash
# Phase 1 (untraced setup): create the file read.cfg expects, matching dims/pattern
DFTRACER_ENABLE=0 ./h5bench_write write_for_read.cfg read.h5
# Phase 2 (traced benchmark): now the file exists with the right shape
DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=... ./h5bench_read read.cfg read.h5
```

`write_for_read.cfg` should use `MEM_PATTERN=CONTIG`/`FILE_PATTERN=CONTIG` and the
same `NUM_DIMS`/`DIM_1` as `read.cfg`. Only enable dftracer for phase 2 so the
trace reflects the read benchmark, not the setup write.

---

## Dataset Sizing (memory threshold rule)

Smoke tests and trace collection MUST move >50% of each node's physical memory
to/from the filesystem to avoid OS page-cache effects (see [[dftracer-annotation-lessons]] R9).

**Tuolumne (AMD MI300A APU), 192 ranks, 2 nodes:**
- MemTotal per node: ~502 GiB
- Required total data: >502 GiB
- `DIM_1=33554432` (32M float32) × 192 ranks × 4B × 4 timesteps = **768 GiB** ✓
- `DIM_1=16777216` (16M float32) × 192 ranks × 4B × 4 timesteps = **384 GiB** ✗

### Sample-config `DIM_1` values need real `du`-verified sanity math before trusting them — trace-log size is NOT a valid proxy

A sample/prior-session config's `DIM_1` can be wrong by 10-16x for the rank count
and node budget actually in use — do the bytes/rank × ranks arithmetic yourself
before launching, and re-verify empirically:

1. **Single-process test first**, then check the ACTUAL output file size with
   `du -sh` on the real `.h5` file — not the reported "Total write size" line
   alone, and never the dftracer trace-log size as a stand-in. The trace log
   stays small (event metadata only, a few hundred MB) regardless of how large
   the real dataset is — a ~200-300 MB trace can sit next to a 1.5+ TB `.h5`
   output file. Trusting trace-log size as evidence of "the run was small" is
   how a 16x-oversized config went undetected for a full baseline pass.
2. **1-node test**, `du -sh` again, confirm linear scaling from the
   single-process number.
3. **8-node/768-rank test**, `du -sh` the real output file AND check the real
   flux job elapsed wall time (`flux jobs -a`, the `TIME` column) at multiple
   points while it runs — do not stop watching once the byte count looks close
   to the target; keep polling until the job actually reaches `CD`/completed
   state, since data volume can plateau (write phase done) while the job is
   still running a slow finalize/verify phase for several more minutes.
4. Only after all three stages confirm a sane size (target ~500-700 GB/run at
   768 ranks to fit a 5-replicate floor inside a shared multi-workload Lustre
   quota budget) and a real elapsed time in the intended window (10-15 min),
   launch the full replicate set.

---

## dftracer Environment Variables

### DFTRACER_ENABLE=1 is required with explicit DFTRACER_C_INIT() calls

```bash
DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=/tmp/my_trace \
  DFTRACER_INIT=FUNCTION ./h5bench_write cfg out.h5
# Trace files: <LOG_FILE>-<hash>-app.pfw.gz
```

### DFTRACER_DATA_DIR=all avoids missing I/O events

Without this, events for `/tmp`, `/scratch`, and other non-workspace paths are
silently excluded. Always set `data_dir="all"` in `session_run_with_dftracer`.

For full dftracer environment variable reference, see [[dftracer-preload-run]].
For HDF5-specific optimization strategies, see [[software-hdf5]].

---

## Optimization Loop — Failed Attempts (learn before retrying)

Each entry records a config/hint/code change that was tried and failed.
**Check this section before proposing any optimization for h5bench.**

**Fork caveat (added 2026-07-10):** several entries below reference `write_normal_dist`
/ `h5bench_write_var_normal_dist`, which does NOT exist in hariharan-devarajan/h5bench
@ master (see the Binary Names correction above) — those entries were recorded against
a different fork/version. The underlying ROMIO/Lustre/cb_nodes findings (colon-separated
MPICH_MPIIO_HINTS syntax, aggregator-count tradeoffs, alignment-on-Lustre regressions,
etc.) are still generally useful for `write`/`write_unlimited`/`overwrite`/`append`, but
don't expect to reproduce the `write_normal_dist` runs themselves on this fork.

### ❌ DELAYED_CLOSE_TIMESTEPS=2 — segfault on multi-node MPI runs

- **What was tried:** Set `DELAYED_CLOSE_TIMESTEPS=2` in h5bench INI config to defer
  H5Fclose() across timesteps, targeting the `posix_close_ops_slope` bottleneck.
- **Result:** Segfault (SIGSEGV) in rank 97 at ~77s on a 2-node × 192-rank run.
  All 384 trace files were created but 0 bytes — no data written.
- **Root cause:** `DELAYED_CLOSE_TIMESTEPS > 0` with MPI collective writes causes a
  memory corruption or race condition in h5bench's buffer management when timesteps
  overlap across ranks on separate nodes.
- **Status:** DO NOT USE. Keep `DELAYED_CLOSE_TIMESTEPS=0` for all MPI runs.
- **Alternative for close-burst bottleneck:** Tune ROMIO collective buffering
  (`cb_buffer_size`, `cb_nodes`) and Lustre stripe count instead.
- **Date confirmed broken:** 2026-06-24, Tuolumne, 192 ranks, MPICH 9.0.1, HDF5 1.14.6

---

### ❌ ROMIO hints via `--env` to `flux proxy flux run` — binary silently dropped

- **What was tried:** Pass `MPICH_MPIIO_HINTS` directly via `--env` flag to `flux run`
  inside a `flux proxy` call (any value containing `;`, `:`, or `*`).
- **Result:** `flux-run: ERROR: job command and arguments are missing` — the binary and
  its arguments were silently dropped. h5bench ran as a 1-rank local process.
- **Root cause:** `flux proxy JOBID cmd args...` passes arguments to flux's internal
  parser which treats special characters (`;`) as command separators, consuming the
  binary path as part of the `--env` value.
- **Status:** NEVER use `--env` for complex values. See [[software-mpi]] wrapper script pattern.
- **Date confirmed broken:** 2026-06-24, Tuolumne, flux_wrappers/0.1

---

### ❌ MPICH_MPIIO_HINTS with semicolons as hint separators — key gets entire string

- **What was tried:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable;cb_buffer_size=67108864;..."`
- **Result:** `hint_get_key() info key 'romio_cb_write' unrecognized value specified
  'enable;cb_buffer_size=...'` — entire semicolon-delimited string treated as value.
- **Root cause:** Cray MPICH 9.0.1 uses **colons** (not semicolons) to separate
  key=value pairs. Correct format: `"*:key=val:key=val:key=val"`.
- **Status:** Use colons. See [[software-mpi]] for correct format table.
- **Date confirmed broken:** 2026-06-24, Tuolumne, cray-mpich/9.0.1

---

### ❌ MPICH_MPIIO_HINTS with commas as hint separators — same parse error

- **What was tried:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable,cb_buffer_size=67108864,..."`
- **Result:** Same "unrecognized value" error as semicolon approach.
- **Root cause:** Commas separate file patterns, not key=value pairs. Colons separate
  key=value pairs within a pattern. Correct: `"*:key1=val1:key2=val2,/other/file:key=val"`.
- **Status:** Use colons between hints. Commas only between different filename patterns.
- **Date confirmed broken:** 2026-06-24, Tuolumne, cray-mpich/9.0.1

---

### ❌ cb_nodes=16 + cb_buffer_size + striping hints — segfault at 2 nodes × 192 ranks, large data

- **What was tried:** Full ROMIO hint set in MPICH_MPIIO_HINTS:
  `*:romio_cb_write=enable:cb_buffer_size=67108864:cb_nodes=16:romio_ds_write=disable:striping_factor=16:striping_unit=4194304`
  with `lfs setstripe -c 16 -S 4m` on output dir.
- **Result:** Segmentation fault (rank 0 or rank 97, varies by run) at ~106s.
  All 384 trace files 0 bytes. Exit non-zero.
- **Root cause (hypothesis):** `cb_nodes` must be **2× the stripe count (OST count)**,
  not equal to it. With `cb_nodes=16` matching `striping_factor=16`, ROMIO does not
  have enough aggregator headroom — buffer lock contention or memory overcommit at
  large data sizes (DIM_1=16M × 4 timesteps × 192 ranks).
  Additionally, passing `striping_factor` and `striping_unit` as ROMIO hints when the
  file is already striped by `lfs setstripe` may conflict at large I/O sizes.
- **Status:** DO NOT use `cb_nodes=stripe_count`. Set `cb_nodes=2×stripe_count` or omit it.
  Also avoid passing `striping_factor`/`striping_unit` hints when `lfs setstripe` is
  already set — let the filesystem enforce it.
- **Date confirmed broken:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5 (28 OSTs)

---

### ❌ H5Pset_alignment to 4MB — regression on Lustre + ROMIO collective writes

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5
- **Workload:** write (16-OST), write_normal_dist (28-OST), 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps
- **Config attempted (a):** `ALIGN=1, ALIGN_THRESHOLD=0, ALIGN_LEN=4194304` — align ALL objects to 4MB
- **Config attempted (b):** `ALIGN=1, ALIGN_THRESHOLD=1048576, ALIGN_LEN=4194304` — align objects ≥ 1MB to 4MB
- **Results:**
  - write (a) ALIGN_THRESHOLD=0:       1.174 GB/s vs 1.652 GB/s baseline → **−29%**
  - write (b) ALIGN_THRESHOLD=1MB:     1.250 GB/s vs 1.652 GB/s baseline → **−24%**
  - ND (b) ALIGN_THRESHOLD=1MB:        1.324 GB/s vs 1.509 GB/s baseline → **−12%**
- **Root cause:** `H5Pset_alignment` inserts file-layout padding gaps between HDF5 internal objects. ROMIO's two-phase collective write must fill those gaps with zeros (extra write amplification) and seek across non-contiguous extents. On Lustre, data chunks are already naturally stripe-aligned (192 × 64MB = exact multiples of 4MB), so there is no alignment benefit to recover — only overhead.
- **Note:** `H5Pset_alignment` IS beneficial on GPFS/IBM Spectrum Scale, which has efficient block pre-allocation. On Lustre + ROMIO, ROMIO handles stripe alignment at the I/O layer; HDF5-level alignment only adds overhead.
- **Do not use when:** Lustre filesystem with ROMIO collective writes. Keep `ALIGN=0` (default) for all Lustre runs.

---

### ❌ cb_nodes=32 (write, 16-OST stripe) — overall neutral, open improved but lxstat inflated

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5 (16-OST stripe)
- **Workload:** write, 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps
- **Config attempted:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable:cb_nodes=32"` + `lfs setstripe -c 16 -S 4m`
- **Result:** 1.646 GB/s vs iter2_optB 1.652 GB/s → **−0.4% (effectively neutral)**. Passed smoke at all scales.
- **What improved:** `open` latency −49.3% (15.52ms → 7.87ms). Aggregators reduced per-file open time.
- **What worsened:** `__lxstat` +10076% (fresh-dir stat artifact masked true comparison).
- **Conclusion:** cb_nodes=32 reduces open latency but doesn't translate to throughput gain at this config. May compound with other L1 improvements in future iters.
- **Do not use alone** when minimal ROMIO hints + 16-OST stripe already applied — no net benefit without L1 alignment changes.

---

### ❌ cb_nodes=56 (ND, 28-OST stripe) — close improved −96.5% but pwrite p95 +2650%

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5 (28-OST stripe)
- **Workload:** write_normal_dist, 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps
- **Config attempted:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable:cb_nodes=56"` + `lfs setstripe -c 28 -S 4m`
- **Result:** 1.505 GB/s vs iter2_optC 1.509 GB/s → **−0.3% (effectively neutral)**. Passed smoke.
- **What improved:** `close mean` −96.5% (54ms → 1.92ms); `open mean` −60.5%; `fopen mean` −74%.
- **What worsened:** `pwrite p50` +212.7% (58ms → 181ms); `pwrite p95` +2650% (83ms → 2.29s); `lseek mean` +25101% (60ms → 15.2s).
- **Root cause:** 56 aggregators for variable-size writes fragment the ND chunks into many small pieces redistributed across 28 OSTs. The inter-aggregator redistribution overhead (extra lseeks across stripe extents) dominates the write path.
- **Lesson:** For variable-size workloads (ND), cb_nodes should NOT follow the 2× stripe_count rule. Fewer aggregators (default=2) perform better for pwrite with variable chunk sizes, even though close overhead increases with more OSTs.
- **Do not use when:** workload has variable-size writes (normal/skewed distribution). The 2× rule is for contiguous fixed-size write workloads only.

---

### ✅ WORKING: Minimal ROMIO hints + lfs stripe — write workload, 2.85× improvement

- **Config:** Wrapper with `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable"`
  + `lfs setstripe -c 16 -S 4m` on output dir. No cb_nodes or striping hints.
- **Result:** 1.652 GB/s vs 586 MB/s baseline = **+182% write bandwidth improvement**.
  Exit 0, stable at 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps.
- **System:** Tuolumne, cray-mpich/9.0.1, lustre5 (28 OSTs), h5bench_write annotated.
- **Date confirmed:** 2026-06-25
- **Lesson:** Start with minimal ROMIO hints; add cb_nodes and cb_buffer_size only after
  confirming stability at each scale step (see R11 in [[dftracer-annotation-lessons]]).

### ✅ WORKING: Minimal ROMIO hints + 28-OST stripe — write_normal_dist, +124% improvement

- **Config:** Wrapper with `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable"`
  + `lfs setstripe -c 28 -S 4m` on output dir (ALL 28 OSTs on lustre5). No cb_nodes.
- **Result:** 1.509 GB/s vs 675 MB/s baseline = **+124% ND bandwidth improvement**.
  Exit 0, stable at 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps.
- **System:** Tuolumne, cray-mpich/9.0.1, lustre5 (28 OSTs), h5bench_write_var_normal_dist annotated.
- **Date confirmed:** 2026-06-25
- **Lesson:** Variable-size writes benefit from more OSTs (28 vs 16) because the non-uniform
  chunk distribution naturally avoids lock contention. This is the OPPOSITE of contiguous write:
  do NOT use 28-OST stripe for `h5bench_write` (only 2 default ROMIO aggregators deadlock).

### ✅ WORKING / NEW DEFAULT: cb_nodes=16 + CRAY_CB_NODES_MULTIPLIER=2 + 16-OST stripe — write, 768 ranks, +490.5% median, ~8.7x faster wall time

- **Config:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:cb_nodes=16"` +
  `CRAY_CB_NODES_MULTIPLIER=2` (effective 32 aggregators — the Cray-specific multiplier is
  what actually raises the real aggregator count; `cb_nodes=16` alone is not enough) +
  `lfs setstripe -c 16 -S 4M` on a FRESH output dir. Exported directly in the wrapper script
  (never via `flux run --env` — see the pitfall entry above; the flag silently drops hints).
- **Result (8 nodes x 96 ppn = 768 ranks, DIM_1=2097152, INTERLEAVED/INTERLEAVED,
  ~575GB/rep):** median POSIX bandwidth 325.94 -> 1924.71 MB/s (**+490.5%**), and critically
  the 5-rep ranges DO NOT OVERLAP AT ALL (worst optimized rep beats best baseline rep by
  +26.7%) despite both distributions being individually noisy (CV 64% baseline, 39% optimized)
  on this shared-tenant Lustre filesystem. Real elapsed wall time per rep also dropped from
  ~12.1 min to ~1.4 min for the identical 575GB output (confirmed via real `du` + real flux
  job time at single-process/1-node/8-node calibration, not projection). A `comparator`
  spot-check corroborates the mechanism: `open()` mean -55.9% (significant), `lseek` mean
  -97.1% (significant), `__lxstat` mean -55.3% (significant), avg POSIX transfer size
  1MB->4MB (+300%, significant — exactly the 32-effective-aggregator arithmetic).
- **System:** Tuolumne, cray-mpich/9.0.1, lustre5, h5bench_write annotated, session
  h5bench/20260710_061131.
- **Date confirmed:** 2026-07-10.
- **This is the SAME lever/mechanism that won +73.8% on flash_x** (session
  flash_x/20260708_201403) for a matching collective-write-bandwidth-bound shape — now
  confirmed transferring cleanly to a second workload on this system.
- **Recommend as the h5bench write-workload default on Tuolumne/lustre5 going forward.**
- **Caveat on layering further levers on top of this one:** network-layer (Slingshot NIC
  policy, `FI_CXI_RDZV_THRESHOLD`) and memory-layer (NUMA `cpu-affinity=per-task`) levers
  layered on top of this config all showed large, misleading 5-rep median deltas (+38-68%)
  that a same-rep `comparator` cross-check revealed as noise (+3% or less, flagged
  negligible) every single time. **Do not trust a 5-rep median alone for this workload's
  aggregate-bandwidth metric on shared Lustre — always run a same-rep `comparator` check
  before calling anything a win** (this generic cross-workload lesson is now also recorded
  in `dftracer-optimization-kb` Rule 5; this note is the h5bench-specific instance of it).
  NUMA `cpu-affinity=per-task` in particular showed a NEGLIGIBLE per-rep effect (-0.5%),
  consistent with a prior finding on this same MI300A system for an unrelated GPU workload —
  launcher-level core pinning appears to have no headroom to recover on Tuolumne generally.

### ❌ H5Pset_file_space_strategy(PAGE) + H5Pset_page_buffer_size — incompatible with COLLECTIVE_METADATA=YES

- **What was tried:** Paged HDF5 file-space aggregation (`H5F_FSPACE_STRATEGY_PAGE` on a
  FILE-CREATE plist + `H5Pset_page_buffer_size` on the FAPL), intended to reduce open()/seek()
  churn under an unchanged access pattern (Li et al. 2025, Howison et al. 2010).
- **Result:** `H5Fcreate` fails outright: `H5Fint.c: collective metadata writes are not
  supported with page buffering`. Any h5bench config with `COLLECTIVE_METADATA=YES` (which
  `h5bench_write.c`'s `set_metadata()` turns into `H5Pset_all_coll_metadata_ops`/
  `H5Pset_coll_metadata_write` on the same FAPL) cannot use page buffering — a fundamental
  HDF5 1.14.x library restriction, not a bug in the h5bench config or the annotated build.
- **Also uncovered:** `h5bench_write.c` never checks `H5Fcreate_async`'s return value — a
  failed create silently cascades into every subsequent `H5D`/`H5G` call failing too (each
  logs its own `HDF5-DIAG` block), yet the benchmark still prints a fabricated "successful"
  performance summary (e.g. 142 GB/s single-rank write rate, 0-byte output file). **Always
  grep the h5bench_write run log for `HDF5-DIAG`, never trust exit-code-0 + a printed summary
  alone.**
- **Status:** DO NOT propose page buffering for any h5bench config with
  `COLLECTIVE_METADATA=YES` on HDF5 1.14.x. Would require disabling collective metadata to
  even test (a confounding change, not pursued).
- **Date confirmed:** 2026-07-10, Tuolumne, HDF5 1.14.5, session h5bench/20260710_061131.

## Benchmarking Pitfalls

### ❌ Reusing output directory inflates `__lxstat` latency by 100×

When ROMIO opens an HDF5 file with `H5F_ACC_TRUNC`, it calls `lxstat` on the output path first.
If a **large pre-existing file** is there (from a prior run with many Lustre stripe allocations),
Lustre fetches the full inode metadata including stripe layout — observed at **43ms mean per call**.
With 192 ranks this adds **~8 seconds** of spurious overhead that looks like a `posix_stat_ops_slope`
bottleneck in dftracer diagnose.

With a **fresh empty directory**, lxstat returns in ~450µs (100× faster).

**Rule:** Always write to a fresh output directory (or `rm -f out.h5` before each run). Never
re-run into the same HDF5 output file path as a previous benchmark and compare traces — the
lxstat inflation will mask real optimization signal.

---

## Failed Configurations

Entries below were applied during optimization loops and caused regressions or had no effect.
Check this section before proposing any configuration for this workload/software/filesystem.

Format per entry:
  date, app, workload, filesystem, system, bottleneck,
  config_attempted, result, metrics_before, metrics_after, delta,
  root_cause, do_not_use_when

<!-- New failed-config entries are appended below by the optimization loop (Step 8d-iii-FAIL) -->

- 2026-07-10 (RE-CONFIRMED — an earlier same-day entry mistakenly reversed this,
  see below), h5bench, write, Lustre, Tuolumne, quota/runtime oversizing STILL
  APPLIES: `write.cfg` (DIM_1=33554432, NUM_DIMS=2, DIM_2=2, TIMESTEPS=6,
  unmodified h5bench sample value) at 768 ranks writes a REAL, non-sparse
  `write.h5` of **~1.6-1.7TB per replicate** (confirmed via `du` on the actual
  output file, not `ls` apparent size, not trace-log size) and takes 35-40+
  minutes wall time — both far over a 10-15 min / budget-fit target. Two
  reruns of this config on 2026-07-10 were killed mid-run after pushing
  `/p/lustre5` from 79.3T to 86.5T in under 40 minutes. **DIM_1=33554432
  genuinely needs shrinking (~/16 or more) — do not use as-is.**
  root_cause of the confusion that produced the incorrect "corrected" entry
  below: the dftracer TRACE log for this config is only ~227MB/rep (it's just
  I/O call metadata — file/offset/size/duration per call — not the actual
  data payload), which was wrongly read as "actual output is small." Always
  check the real HDF5 output file size (`du -sh <output>.h5`, actual blocks,
  not `ls -la` apparent/sparse size) to judge data volume — the trace log
  size is NOT a proxy for it. do_not_use_when: never conclude a write-heavy
  config is "fine" from trace-log size alone; check the app's own output file
  on disk.
- 2026-07-10, h5bench, exerciser, Lustre, Tuolumne, catastrophic oversizing,
  `--numdims 2 --minels 67108864 67108864` (naive first CLI translation),
  result: computed to ~36 PB/rank, root_cause: `h5bench_exerciser` does not
  take a config file — every dimension needs an explicit `--minels`/`--maxcheck`
  style flag and the real per-rank memory formula must be read directly from
  `exerciser/h5bench_exerciser.c` before choosing values, do_not_use_when:
  guessing exerciser flag values without reading the source formula first.

## Known-good baseline configs (Tuolumne, 8 nodes x 96 ppn = 768 ranks, ~few-minute runs)

**WARNING — trace-log size is NOT a proxy for real output data volume.** The
"raw trace size" column below is the dftracer I/O-call log (~200 bytes/event
metadata), not the actual bytes h5bench wrote to its output `.h5` file. Always
verify with `du -sh <output>.h5` on the REAL output file (not `ls -la`
apparent/sparse size) before calling a config "small." `write` below was
wrongly judged safe from trace size alone on 2026-07-10 and turned out to
write ~1.6TB/rep — see "Failed Configurations" above.

| Workload | Config | Raw trace size/rep | Real output (.h5) size/rep | Notes |
| --- | --- | --- | --- | --- |
| write | `MEM_PATTERN=INTERLEAVED FILE_PATTERN=INTERLEAVED TIMESTEPS=6 COLLECTIVE_DATA=YES COLLECTIVE_METADATA=YES NUM_DIMS=2 DIM_1=33554432 DIM_2=2 DIM_3=1` | ~227MB | **~1.6-1.7TB (confirmed via `du`)** | STILL OVERSIZED — shrink DIM_1 before reuse, do not trust as "known-good" |
| read | `MEM_PATTERN=CONTIG FILE_PATTERN=CONTIG READ_OPTION=STRIDED TIMESTEPS=4 COLLECTIVE_DATA=YES COLLECTIVE_METADATA=YES NUM_DIMS=1 DIM_1=33554432 DIM_2=1 DIM_3=1` | ~3MB | not re-verified — output dir already cleaned up before real size could be checked; event count (75k) is 3-4 orders of magnitude below write's, but treat as unconfirmed, not proven safe | |
| append | `COLLECTIVE_DATA=YES COLLECTIVE_METADATA=YES READ_OPTION=FULL TIMESTEPS=4 NUM_DIMS=1 DIM_1=33554432 DIM_2=1 DIM_3=1` | ~1.2MB | not re-verified (same caveat as read) | |
| overwrite | `COLLECTIVE_DATA=YES COLLECTIVE_METADATA=YES READ_OPTION=FULL TIMESTEPS=6 NUM_DIMS=1 DIM_1=33554432 DIM_2=1 DIM_3=1` | ~1.2MB | not re-verified (same caveat as read) | |
| write_unlimited | `MEM_PATTERN=CONTIG FILE_PATTERN=CONTIG COMPRESS=YES COLLECTIVE_DATA=YES COLLECTIVE_METADATA=YES TIMESTEPS=6 NUM_DIMS=1 DIM_1=33554432 DIM_2=1 DIM_3=1` | ~6.4MB | not re-verified (same caveat as read) | |
| hdf5_iotest | INI: `steps=24 arrays=5 rows=512 columns=512 process-rows=32 process-columns=24 scaling=weak dataset-rank=4 layout=contiguous mpi-io=collective` | n/a | ~395GB/rep, ~7-10min elapsed (measured via flux job accounting) | within the 10-15 min target despite large volume — high sustained bandwidth on this Lustre config |
| exerciser | `h5bench_exerciser --numdims 2 --minels 5523 5523 --nsizes 1` | n/a | ~383GB/rep, ~20-21min elapsed (measured via flux job accounting) | over the 10-15 min target — shrink `--minels` further (try ~1500-2000 instead of 5523) for a stricter target next time |

**Observation:** `write`'s trace event count (18.7M) is 250-660x every sibling workload's —
consistent with it also being the only workload confirmed to write real multi-TB data
volume (interleaved 2D collective writes generate far more small I/O calls per byte than
the mostly-contiguous-1D siblings). The trace-event-count disparity was a signal worth
noticing, not something to explain away.

## Diagnosed bottleneck shape (2026-07-10, dfanalyzer+dfdiagnoser, severity-scored)

`read`, `append`, `overwrite` baselines (768/768 processes, full severity diagnosis) are all
**metadata-time bound**, not bandwidth-bound: `posix_metadata_time_*_frac_parent` ≈ 0.97-0.99,
i.e. open()/metadata calls consume nearly all POSIX-layer time, not the actual data transfer.
This is a DIFFERENT bottleneck shape than flash_x's write-bandwidth case (which the
`cb_nodes`+`CRAY_CB_NODES_MULTIPLIER` MPI-IO collective-buffering combo addresses — see the
transferable finding at the top of this file). **Do not default to that combo for these
workloads** — it's wrong-shape (targets bandwidth; only ~2% of POSIX time here is data
transfer) and L3 OST striping is also inert here (open()/stat() cost lives on the Lustre
MDS, not the OSTs — confirmed via `lfs getstripe -d`, and `/p/lustre5`'s default Data-on-MDT
PFL already covers small metadata-heavy files).

**CORRECTION (2026-07-10, dftracer-optimizer, via source inspection — an earlier same-day
entry here wrongly proposed this as the fix):** `H5Pset_coll_metadata_write` +
`H5Pset_all_coll_metadata_ops` is **already the baseline for read/append/overwrite** —
`h5bench_read.c` L400-401, `h5bench_append.c` L490-491, `h5bench_overwrite.c` L466-467 all
call both UNCONDITIONALLY in `set_pl()`, not gated on the `COLLECTIVE_METADATA` config key.
(Contrast: `h5bench_write.c` L927 and `h5bench_write_unlimited.c` DO gate on the config —
collective metadata IS a real, config-controlled lever for those two.) Since these 3
workloads remain metadata-bound *despite* collective metadata already being on, the real
bottleneck is the POSIX `open()/close()/__lxstat` storm across 768 ranks on a shared file,
which collective metadata ops don't address. **Do not propose this lever for
read/append/overwrite again — verify via source (`grep set_pl` in the app's own .c file)
whether a lever is actually config-gated before assuming it's tunable.**

The one real, paper-backed, not-yet-tested lever for this shape: `H5Pset_meta_block_size`
(raise from the ~2KB HDF5 default to several MB) + `H5Pset_file_space_strategy(PAGE)` +
`H5Pset_page_buffer_size` (Li et al. 2025, arXiv:2506.15114; Howison et al. 2010) — requires
a source rebuild, not just a config/env change, so it's a DIFFERENT category of fix than the
other levers on this page.

**Caveat CONFIRMED (2026-07-10, re-verified across all 5 reps, not just rep1):** every
`write_unlimited` replicate shows the same ~162/768-process capture (~6.3MB raw traces,
1536 files, uniformly across reps 1-5) — this is a SYSTEMIC issue with this workload's
config, not a one-off rep1 fluke. **Do not optimize write_unlimited until this is root
caused** (check whether `COMPRESS=YES` + the unlimited-dimension/chunking combination is
suppressing most ranks' I/O, or whether it's a trace-capture gap specific to this binary).

### ❌ romio_no_indep_rw=enable — unrecognized hint on cray-mpich 9.0.1

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1
- **Workload:** write, write_normal_dist
- **Config attempted:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable:romio_no_indep_rw=enable"`
- **Result:** `hint_get_key() info key 'romio_no_indep_rw' unrecognized value specified 'enable'` — hint silently dropped. Run succeeded but optimization had no effect.
- **Root cause:** `romio_no_indep_rw` is not a valid MPICH hint key on cray-mpich 9.0.1. This hint exists in OpenMPI/OMPIO but not in Cray MPICH.
- **Do not use when:** cray-mpich/9.0.1 on Tuolumne. No alternative; independent read/write suppression is not configurable via hints on this stack.

---

### ❌ cb_buffer_size=134217728 (128MB) alone — regression at production scale

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5 (16-OST stripe)
- **Workload:** write, 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps
- **Config attempted:** `MPICH_MPIIO_HINTS="*:romio_cb_write=enable:romio_ds_write=disable:cb_buffer_size=134217728"` + `lfs setstripe -c 16 -S 4m`
- **Result:** 1.230 GB/s vs iter1_v2 baseline 1.463 GB/s → **−16% regression**. Passed smoke.
- **Root cause (hypothesis):** Larger cb_buffer holds more data per aggregator before flushing, increasing peak memory pressure on the 2 default aggregator nodes. With 192 ranks × 4MB buffers × 2 aggregators, the aggregator buffer overhead may exceed L3 cache, causing more TLB misses and worse NUMA behavior on AMD MI300A APU.
- **Do not use when:** 2 aggregator nodes (default cb_nodes) with DIM_1=16M and 4 timesteps on Tuolumne. May work if cb_nodes is also set appropriately (2× OST count = 32) but that combination untested.

---

### ❌ lfs setstripe -c 28 -S 4m (all OSTs) — HDF5 errors + hang at production scale

- **Date:** 2026-06-25, Tuolumne, cray-mpich/9.0.1, lustre5 (28 OSTs total)
- **Workload:** write, 2 nodes × 192 ranks, DIM_1=16M, 4 timesteps
- **Config attempted:** `lfs setstripe -c 28 -S 4m` on output dir + minimal ROMIO hints
- **Result:** HDF5-DIAG errors on ranks 8–10, incomplete output (12GB of expected ~49GB = 1 of 4 timesteps), job hung after 10 min.
- **Root cause (hypothesis):** Default ROMIO cb_nodes=2 (one per node) with stripe_count=28 means 28 Lustre locks must be acquired by only 2 aggregators. Lock contention across 28 OSTs with 2 aggregators at large data causes HDF5 collective metadata flush to deadlock or timeout.
- **Do not use when:** stripe_count > number of aggregator nodes × cores-per-node / typical_ranks_per_agg. Keep stripe_count ≤ 16 unless cb_nodes is set to 2× stripe_count. For Tuolumne lustre5: safe max stripe without cb_nodes tuning is 16.
- **Passed at:** medium data (DIM_1=16M, TIMESTEPS=2), 192 ranks — failure only appears at 4 timesteps production scale.

