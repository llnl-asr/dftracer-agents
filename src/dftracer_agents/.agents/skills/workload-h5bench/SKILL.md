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

---

## Dataset Sizing (memory threshold rule)

Smoke tests and trace collection MUST move >50% of each node's physical memory
to/from the filesystem to avoid OS page-cache effects (see [[dftracer-annotation-lessons]] R9).

**Tuolumne (AMD MI300A APU), 192 ranks, 2 nodes:**
- MemTotal per node: ~502 GiB
- Required total data: >502 GiB
- `DIM_1=33554432` (32M float32) × 192 ranks × 4B × 4 timesteps = **768 GiB** ✓
- `DIM_1=16777216` (16M float32) × 192 ranks × 4B × 4 timesteps = **384 GiB** ✗

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

