## Session logs (appended by pipeline Step 8)

<!-- New entries are appended below this line by the pipeline recipe -->

---
date: 2026-06-25
app: general
context: Cray HDF5 parallel module on Tuolumne has chid_t type that breaks dftracer/brahma build
error: |
  /opt/cray/pe/hdf5-parallel/1.14.3.7/cray/20.0/include/H5Apublic.h:932:29:
  error: unknown type name 'chid_t'; did you mean 'hid_t'?
  gmake[5]: *** [CMakeFiles/brahma.dir/build.make:156: CMakeFiles/brahma.dir/src/brahma/interface/hdf5.cpp.o] Error 1
root_cause: |
  The Cray-patched HDF5 installed at /opt/cray/pe/hdf5-parallel/1.14.3.7/cray/20.0
  introduces a Cray-specific type 'chid_t' in H5Apublic.h that is not part of
  the upstream HDF5 standard. When dftracer's brahma dependency compiles against
  these headers, the C++ compiler does not recognise 'chid_t' and fails.
  This affects only the Cray module HDF5 — not vanilla upstream HDF5 builds.
  System: Tuolumne (AMD MI300A, Cray PE 2.7.35, cray-hdf5-parallel/1.14.3.7).
fix: |
  The chid_t bug exists in BOTH the Cray HDF5 module AND vanilla HDF5 1.14.3
  (it is a typo in H5Apublic.h line 932 — H5Aread_async uses chid_t instead of hid_t).
  Steps:
  1. Download vanilla HDF5 1.14.3 from hdfgroup.org FTP (GitHub 404s on this system):
       curl -fkL https://support.hdfgroup.org/ftp/HDF5/releases/hdf5-1.14/hdf5-1.14.3/src/hdf5-1.14.3.tar.gz \
         -o hdf5-1.14.3.tar.gz
  2. Build from source:
       tar xf hdf5-1.14.3.tar.gz && cd hdf5-1.14.3
       CC=mpicc ./configure --prefix=<ws>/hdf5_1.14 --enable-parallel \
         --enable-shared --enable-build-mode=production --with-zlib=/usr
       make -j8 && make install
  3. Patch the chid_t typo in the installed header:
       sed -i 's/H5Aread_async(chid_t attr_id/H5Aread_async(hid_t attr_id/' \
         <ws>/hdf5_1.14/include/H5Apublic.h
  4. Update session.json HDF5_ROOT/HDF5_DIR to point at <ws>/hdf5_1.14
  5. Re-run session_install_dftracer — will now succeed.
  Note: IOR can still use cray-hdf5-parallel (C frontend tolerates chid_t);
  only dftracer/brahma (C++ frontend) cannot.
  MPI compatibility warning: MPICH 9.0.1 is outside brahma's tested range;
  MPI-IO interception is disabled but POSIX and app-level annotation tracing work.
tags: [tuolumne, cray-pe, hdf5, chid_t, brahma, dftracer-install, system-specific]

---
date: 2026-06-22
app: https://github.com/llnl/ior (tag 4.0.0)
context: IOR 4.0.0 autoreconf fails without -I config flag and stub files
error: |
  configure: error: cannot find install-sh, install.sh, or shtool in config
  X_AC_META: command not found
  automake: error: required file './NEWS' not found
root_cause: |
  IOR 4.0.0 ships without a pre-generated configure script. The custom
  X_AC_META m4 macro lives in config/ not the default autoconf include path.
  automake also requires NEWS and AUTHORS files to exist (even empty).
fix: |
  cd <source> && touch NEWS AUTHORS && autoreconf -fi -I config
  Then run configure normally.
tags: [c, autotools, ior, autoreconf, configure]

---
date: 2026-06-22
app: https://github.com/llnl/ior (tag 4.0.0)
context: IOR 4.0.0 linker fails with duplicate symbol errors on clang/lld — needs -fcommon + bfd
error: |
  ld.lld: error: duplicate symbol: posix_aiori
  ld.lld: error: duplicate symbol: mpiio_aiori
  (also with ld.bfd without -fcommon)
root_cause: |
  aiori.h defines global variables (posix_aiori, mpiio_aiori, hdf5_aiori,
  ncmpi_aiori) without extern, causing duplicate definitions when included
  in multiple TUs. GCC < 10 defaulted to -fcommon which merged these as
  COMMON symbols; clang/lld (default on Cray/LLNL systems) is strict.
  Note: must also do make clean before rebuild when changing CFLAGS, otherwise
  cached .o files from the old flags are reused.
fix: |
  CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon" \
  LDFLAGS="-fuse-ld=bfd" \
  ./configure --without-hdf5 --without-ncmpi ...
  Also: make clean before the first build after adding these flags.
tags: [c, ior, linker, fcommon, lld, bfd, duplicate-symbol]

---
date: 2026-06-22
app: https://github.com/llnl/ior (tag 4.0.0)
context: session_build_annotated ignores custom CFLAGS/LDFLAGS for autotools projects
error: |
  Build failed in build_ann/ with same function-pointer and duplicate symbol
  errors as original — identical to pre-fix errors.
root_cause: |
  session_build_annotated runs its own autoreconf+configure pass without
  knowing about project-specific CFLAGS/LDFLAGS overrides. The generated
  Makefile in build_ann/ embeds the default flags, not the ones used to
  successfully build the original binary.
fix: |
  For autotools projects with custom flags:
  1. rm -rf <ws>/build_ann/
  2. mkdir -p <ws>/build_ann/ && cd <ws>/build_ann/
  3. Run configure manually with all custom CFLAGS/LDFLAGS AND dftracer
     include/lib paths:
       CFLAGS="-g -O2 -Wno-incompatible-function-pointer-types -fcommon \
               -I<dftracer_inc>" \
       LDFLAGS="-fuse-ld=bfd -L<dftracer_lib> -Wl,-rpath,<dftracer_lib>" \
       LIBS="-ldftracer_core" \
       <ws>/annotated/configure --prefix=<ws>/install_ann ...
  4. make -j8 install in build_ann/src/ (skip contrib/ if broken)
tags: [c, autotools, ior, build_ann, session_build_annotated, cflags]

---
date: 2026-06-22
app: general (Cray PE / MPICH systems)
context: clang_syntax_check misses MPI and dftracer include paths on Cray PE
error: |
  fatal error: mpi.h: No such file or directory
  fatal error: dftracer/dftracer.h: No such file or directory
root_cause: |
  clang_syntax_check auto-detects MPI paths via mpicc --showme:incdirs but
  Cray PE mpicc outputs -I/path (with -I prefix), not a plain path, so the
  detection silently fails. Annotated files also include <dftracer/dftracer.h>
  directly, which requires the real dftracer include path (not just the stub).
fix: |
  Always pass extra_include_dirs explicitly on Cray PE systems:
    clang_syntax_check(run_id=..., filepath=...,
      extra_include_dirs=[
        "/opt/cray/pe/mpich/<version>/ofi/cray/<ver>/include",
        "<ws>/venv/lib/python3.*/site-packages/dftracer/include"
      ])
  Get the exact MPI path with: mpicc -show | grep -o '\-I[^ ]*' | head -1
tags: [cray-pe, mpich, mpi, syntax-check, extra_include_dirs]

---
date: 2026-06-22
app: general
context: session_analyze_traces reads stale idx/ cache after split update — shows old event count
error: |
  After re-splitting 98 trace files, session_analyze_traces still reported
  391 events (1 file) from the old single-process trace index.
root_cause: |
  traces_split/idx/ is built on first analyze call and cached. Subsequent
  calls with the same traces_split path reuse the cache even when split
  chunks were replaced.
fix: |
  Before re-running split when trace content changes:
    rm -rf <ws>/traces_split/idx/
  Then re-run split (with force=True), then re-run analyze.
tags: [dftracer, traces, split, analyze, idx, cache]

---
date: 2026-06-22
app: general
context: session_generate_optimization_proposals does not support posix_*_ops_slope bottleneck types
error: |
  All 24 diagnosed bottlenecks reported as "unsupported":
  posix_data_ops_slope, posix_ops_slope, posix_read/write/close/open/metadata_ops_slope
root_cause: |
  The proposal tool's strategy table covers absolute bandwidth/IOPS metrics
  but not slope/rate-of-change metrics. These "ops_slope" metrics are
  produced by DFDiagnoser when it detects accelerating I/O patterns across
  time ranges (indicative of lock contention, bursty I/O, or collective storms).
fix: |
  For posix_*_ops_slope bottlenecks, derive proposals manually:
  - ops_slope > 1 means operation rate is accelerating (bursty I/O pattern)
  - posix_data_ops_slope → increase transfer size (L1), ROMIO hints (L2), stripe tuning (L3)
  - posix_close_ops_slope → stagger close timing (L1), ind_wr_buffer_size (L2), client cache (L3)
  - posix_metadata_ops_slope → shared file instead of file-per-process (L1), pre-create (L2), DNE (L3)
  Use the Lustre ecosystem papers found by the iteration search for citations.
tags: [dftracer, proposal, posix_ops_slope, lustre, optimization-loop]

---
date: 2026-06-20
app: general
context: HDF5 1.10.x silently degrades optimization effectiveness — always use 1.14
error: |
  H5Pset_page_buffer_size() had no effect; H5Fcreate_async() fell back to sync;
  posix_close_ops_slope bottleneck could not be fully resolved despite correct hints.
root_cause: |
  HDF5 1.10.x (latest Debian/Ubuntu package at time of writing) does not support
  page buffering with the MPI-IO VFD and the async VOL is not available.
  Applications that call H5Pset_page_buffer_size on an MPIO fapl in 1.10.x get
  a silent no-op; H5Fcreate_async is a stub that falls through to synchronous create.
  This means several L2 optimizations compile and run without error but have zero effect.
fix: |
  Always build with HDF5 ≥ 1.14.x for parallel I/O projects.
  Install from source with --enable-parallel:
    wget https://github.com/HDFGroup/hdf5/releases/download/hdf5_1.14.4/hdf5-1.14.4.tar.gz
    tar xf hdf5-1.14.4.tar.gz && cd hdf5-1.14.4
    CC=mpicc ./configure \
      --prefix=<ws>/hdf5_1.14 --enable-parallel --enable-shared \
      --enable-build-mode=production --with-zlib=/usr
    make -j$(nproc) && make install
  Then rebuild the application with HDF5_DIR pointing at 1.14 install.
  Verify: h5cc -showconfig | grep "Version:"  → should show 1.14.x
tags: [hdf5, version, page-buffer, async-vol, parallel-io, best-practice]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: clang_annotate_project / clang_add_braces corrupts assert() macro call-sites
error: |
  After brace insertion, assert(pconfig->version == 0) was split into:
    assert(pconfig->version ==
    {
    0)
    }
  Compiler: "error: expected ')' before '{' token"
root_cause: |
  glibc's assert(expr) expands to: if (expr) ; else __assert_fail(...)
  The Clang AST reports this IfStmt with a NullStmt then-body (the bare ";").
  _collect_braceless was treating the NullStmt as an unbraced body and inserting
  {/} at the macro call-site line, splitting multi-line macro arguments.
fix: |
  In source_parser.py _collect_braceless(), add a NullStmt guard:
    if kind == "IfStmt":
        _then_is_null = (len(inner) >= 2 and inner[1].get("kind") == "NullStmt")
        for i, child in enumerate(inner):
            if i == 0: continue  # condition
            if _then_is_null: continue  # assert()-style — skip ALL bodies
            ...
  When the then-body (child[1]) is a NullStmt, the entire IfStmt comes from a
  macro expansion like assert(). Skip adding braces to all bodies of that IfStmt.
tags: [c, clang, brace-insertion, assert, macro, mcp-tool-fix]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: clang_add_braces inserts standalone "{" before "else if", producing illegal C
error: |
  After brace insertion, else-if chains became:
    } else
    {
    if (condition) {
  Compiler: "error: expected expression before '{' token"
root_cause: |
  In _collect_braceless, when an IfStmt's else-body (index >= 2) is itself an
  IfStmt (the else-if case), _maybe_add was wrapping it. This inserted a standalone
  "{" line BEFORE the "else" keyword — producing "{ else if (...)" which is
  illegal C syntax.
fix: |
  In source_parser.py _collect_braceless(), add an else-if guard:
    if i >= 2 and child.get("kind") == "IfStmt":
        continue  # else-if: skip wrapping; recursion handles inner IfStmt
  The recursion already visits the inner IfStmt's own bodies; wrapping the outer
  else-body is never needed and always breaks else-if chains.
tags: [c, clang, brace-insertion, else-if, mcp-tool-fix]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: DFTRACER_C_INIT(NULL, NULL, -1) causes segfault — third arg must be NULL not -1
error: |
  Segmentation fault in initialize_main() at fgets call immediately after startup.
  Stack: main → read_config_from_file → fgets → SIGSEGV
root_cause: |
  DFTRACER_C_INIT macro passes its third argument directly to initialize_main(log,
  dirs, int *process_id). Passing the integer -1 is implicitly cast to (int*)0xffffffffffffffff,
  which initialize_main then dereferences → immediate segfault.
fix: |
  Always use NULL (not -1 or any integer) for the process_id argument:
    DFTRACER_C_INIT(NULL, NULL, NULL)
  NULL is a valid int* meaning "auto-detect PID". The pipeline skill init_args
  default was updated from "NULL, NULL, -1" to "NULL, NULL, NULL".
tags: [c, dftracer-init, segfault, init-args, pipeline-skill]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: CMake library name mismatch — dftracer installs as libdftracer_core.so not libdftracer.so
error: |
  /usr/bin/ld: cannot find -ldftracer: No such file or directory
root_cause: |
  dftracer's installed library filename is libdftracer_core.so (not libdftracer.so).
  session_install_dftracer patches CMake to link -ldftracer, but the actual soname
  is dftracer_core. This causes linker failure on all targets.
fix: |
  After session_install_dftracer completes, patch both CMakeCache.txt and all
  generated link.txt files:
    sed -i 's/-ldftracer\b/-ldftracer_core/g' build_ann/CMakeCache.txt
    find build_ann/CMakeFiles -name "link.txt" \
      -exec sed -i 's/-ldftracer\b/-ldftracer_core/g' {} \;
  Verify: grep -r "ldftracer[^_]" build_ann/ → should return nothing
  Note: session_build_annotated should be updated to auto-apply this fix.
tags: [c, cmake, linker, dftracer-install, libdftracer_core]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: -ldftracer_core on its own line in link.txt causes cmake_link_script to ignore it
error: |
  All -ldftracer_core flags were silently dropped; linker still reported undefined
  reference to initialize_region after the fix was applied.
root_cause: |
  When appending -ldftracer_core to link.txt using "echo -n >> file", the file
  already had a trailing newline, so the flag ended up on its own line.
  cmake -E cmake_link_script executes each line as a separate command; a line
  containing only " -ldftracer_core" is not a valid command and is silently ignored.
fix: |
  When patching link.txt, join the flag to the SAME line as the cc command:
    # Remove from current position first, then re-append properly:
    find build_ann/CMakeFiles -name "link.txt" | while read f; do
      sed -i 's/ -ldftracer_core / /g' "$f"
      # Remove trailing newline from last line, append flag, add newline
      content=$(head -n -1 "$f" | tr -d '\n')  # if on its own line
      echo "${content} -ldftracer_core" > "$f"
    done
tags: [cmake, link-order, link-txt, cmake_link_script, dftracer_core]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: Link order error — -ldftracer_core before .o files causes undefined references
error: |
  undefined reference to `initialize_region'
  undefined reference to `update_metadata_string'
  undefined reference to `finalize_region'
  (symbols DO exist in libdftracer_core.so per nm)
root_cause: |
  The linker processes libraries left-to-right. When -ldftracer_core appears BEFORE
  the .o object files that need it, no symbols are requested yet so the linker
  skips pulling them in. When the .o files are processed later, the library is
  already past and symbols are not found.
fix: |
  Ensure -ldftracer_core appears AFTER all .o files in the link command.
  When CMake places it before the objects (via CMAKE_EXE_LINKER_FLAGS), patch
  the generated link.txt files to move the flag to the end:
    sed -i 's/ -ldftracer_core//g' link.txt
    # append at end of the line (see lesson above about same-line appending)
tags: [c, cmake, linker, link-order, undefined-reference, dftracer_core]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: Patching CMakeCache.txt triggers cmake re-run that loses MPI detection
error: |
  CMake re-ran configure after CMakeCache.txt was edited; MPI was not found:
  "Could NOT find MPI_C" / "Could NOT find MPI_CXX"
root_cause: |
  Modifying CMakeCache.txt causes cmake's cmake_check_build_system to re-run
  configure. The re-run could not find MPI because it looked for mpich/openmpi
  in the wrong place in a container environment.
fix: |
  After editing CMakeCache.txt, also add these entries to skip MPI re-detection:
    MPI_C_WORKS:BOOL=TRUE
    MPI_CXX_WORKS:BOOL=TRUE
  This tells cmake that MPI was already verified and prevents the re-check.
tags: [cmake, mpi, cmake-cache, re-configure, container]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: h5bench_write expects an INI key=value config file, not the JSON sample files
error: |
  Passing samples/sync-write-1d-contig-contig.json as the config file caused a
  segfault inside fgets() — the JSON was parsed as INI and file handle was corrupted.
root_cause: |
  The JSON files in h5bench/samples/ are for the h5bench Python runner (h5bench.py),
  which reads them and generates a temporary key=value INI file. h5bench_write itself
  only accepts a simple KEY=VALUE text file (one pair per line, no sections).
fix: |
  Create a minimal INI-style config directly:
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
  The h5bench.py runner auto-generates this file from JSON; to invoke h5bench_write
  directly, create the INI file manually.
tags: [h5bench, config, ini, json, segfault, smoke-test]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: Missing DFTRACER_DATA_DIR=all silently drops I/O events outside the workspace dir
error: |
  Trace files written but dfanalyzer shows low or zero file I/O event count;
  events for /tmp and other non-workspace paths are missing.
root_cause: |
  dftracer's DFTRACER_DATA_DIR defaults to watching only specific directories.
  When benchmarks write to /tmp, /scratch, or any path outside the default scope,
  those events are silently excluded from the trace.
fix: |
  Always pass DFTRACER_DATA_DIR=all when collecting traces:
    DFTRACER_ENABLE=1 DFTRACER_DATA_DIR=all DFTRACER_INC_METADATA=1 \
      DFTRACER_LOG_FILE=<prefix> DFTRACER_INIT=FUNCTION ./binary ...
  This ensures ALL file paths are traced regardless of location.
  Applies to both session_run_with_dftracer (pass data_dir="all") and manual runs.
tags: [dftracer, data_dir, trace-missing, DFTRACER_DATA_DIR, best-practice]

---
date: 2026-06-20
app: general
context: Missing DFTRACER_INC_METADATA=1 omits metadata events from trace
error: |
  Trace events are missing process/thread name metadata and custom key=value fields
  set via DFTRACER_C_FUNCTION_UPDATE_STR even though the annotations compiled fine.
root_cause: |
  dftracer's metadata events (process name, thread name, custom key=value pairs
  set via UPDATE_STR/UPDATE_INT) are only recorded when DFTRACER_INC_METADATA=1
  is set in the environment. Without it, only timing events are captured.
fix: |
  Always set DFTRACER_INC_METADATA=1 alongside other dftracer env vars:
    DFTRACER_ENABLE=1 DFTRACER_DATA_DIR=all DFTRACER_INC_METADATA=1 \
      DFTRACER_LOG_FILE=<prefix> DFTRACER_INIT=FUNCTION ./binary ...
  The comp=, filename=, count= etc. metadata set via UPDATE_STR will not appear
  in the trace without this flag — the span start/end timestamps will be there
  but the custom attributes won't.
tags: [dftracer, metadata, DFTRACER_INC_METADATA, UPDATE_STR, best-practice]

---
date: 2026-06-20
app: https://github.com/hariharan-devarajan/h5bench (main)
context: DFTRACER_ENABLE=1 is required when explicit DFTRACER_C_INIT() calls are used
error: |
  No trace files written to DFTRACER_LOG_FILE path after h5bench_write run.
  Application completed successfully but /tmp/h5bench_trace* was empty.
root_cause: |
  When the annotated code contains explicit DFTRACER_C_INIT() calls, dftracer's
  default initialization mode (without DFTRACER_ENABLE=1) may not write traces
  unless explicitly enabled via the environment.
fix: |
  Always set both DFTRACER_ENABLE=1 and DFTRACER_LOG_FILE when running:
    DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=/tmp/my_trace \
      DFTRACER_INIT=FUNCTION ./h5bench_write cfg out.h5
  Trace files are written as: <LOG_FILE>-<hash>-app.pfw.gz
tags: [dftracer, DFTRACER_ENABLE, trace-missing, h5bench, smoke-test]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: Inserting DFTRACER_C_FUNCTION_END into braceless single-line if (dryRun pattern)
error: |
  Inserting END made the early return unconditional — dryRun check bypassed.
  Or: compiler error "expected ';' before DFTRACER_C_FUNCTION_END"
root_cause: C braceless-if shares its body with the first following statement.
  END inserted before the return stole the if body, making return fall through always.
fix: |
  Grep for braceless early-exit lines before annotating each file:
    grep -n "if.*return\|if.*continue\|if.*break" <file.c> | grep -v "{" | grep -v "//"
  For each hit, add explicit braces FIRST, then insert END:
    // Before: if (dryRun) return NULL;
    // After:
    if (dryRun) {
      DFTRACER_C_FUNCTION_END();
      return NULL;
    }
tags: [c, annotation, braceless, if-body, dryRun, build-error]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: DFTRACER_C_FINI placed before ior_main() call — all backend spans missing from trace
error: |
  Trace contains only the main() span. No POSIX_Create, MPIIO_Open, HDF5_Close
  or any other backend spans appear even though those functions are annotated.
root_cause: |
  DFTRACER_C_FINI() was placed just before the final return in main(), BEFORE
  ior_main() had been called. dftracer finalized and stopped recording; all backend
  I/O that ran inside ior_main() was untraced.
fix: |
  FINI must appear AFTER the top-level benchmark function returns. Structure:
    int main(...) {
      MPI_Init(...);
      DFTRACER_C_INIT(NULL, NULL, NULL);
      DFTRACER_C_FUNCTION_START();
      ...
      ior_main(opts);           // ← real I/O happens here
      DFTRACER_C_FUNCTION_END();
      DFTRACER_C_FINI();        // ← AFTER ior_main, not before the benchmark call
      MPI_Finalize();
      return 0;
    }
  Before placing FINI, identify the "benchmark call" in main() — the call that
  does all the real work — and ensure FINI comes after it returns.
tags: [c, annotation, fini, main, empty-trace, benchmark-wrapper]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: HDF5 stray END inserted because grep matched forward declaration instead of definition
error: |
  HDF5_Create had a DFTRACER_C_FUNCTION_END() inserted before DFTRACER_C_FUNCTION_START()
  — compile error or incorrect trace span.
root_cause: |
  grep matched the forward declaration of HDF5_Create (which ends with ';')
  and inserted an END there, then also annotated the real definition.
fix: |
  Always filter grep results to definitions only:
    grep -n "HDF5_Create" file.c | grep -v ";$"
  The definition has a body ({...}); the forward declaration ends with ';'.
  Annotate ONLY the definition line, never the declaration.
tags: [c, annotation, forward-declaration, stray-end, hdf5]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: dftracer built without MPI/HDF5 support — MPIIO and HDF5 annotation not captured
error: |
  Trace files exist but contain only POSIX events. MPIIO_* and HDF5_* annotated
  function spans are missing even though the annotations compiled correctly.
root_cause: |
  dftracer was installed without -DDFTRACER_ENABLE_MPI=ON and -DDFTRACER_ENABLE_HDF5=ON.
  The config header shows DFTRACER_MPI_ENABLE 0. Without MPI support, dftracer
  cannot intercept MPI-IO paths and MPI-aware annotations produce no events.
fix: |
  Rebuild dftracer with backend flags:
    cmake -DCMAKE_INSTALL_PREFIX=<prefix> \
          -DDFTRACER_ENABLE_MPI=ON \
          -DDFTRACER_ENABLE_HDF5=ON \
          -DDFTRACER_ENABLE_FTRACING=ON <src>
    make -j4 install
  Verify: grep DFTRACER_MPI_ENABLE <prefix>/include/dftracer/core/dftracer_config.hpp
  Expected output: #define DFTRACER_MPI_ENABLE 1
  Then do a clean rebuild of the annotated project against the new dftracer install.
tags: [c, dftracer-install, mpi, hdf5, missing-spans, dftracer_config]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: IOR autotools configure silently ignored new --with-hdf5 flag due to stale state
error: |
  ./configure --with-hdf5 completed without error but config.h showed USE_HDF5_AIORI=0.
  The HDF5 backend was not compiled in.
root_cause: |
  Stale .deps/, config.status, and autom4te.cache from a previous ./configure run
  caused autotools to skip re-detection of HDF5. The new --with-hdf5 flag was
  effectively ignored.
fix: |
  Before reconfiguring after any flag change:
    make distclean
    rm -rf .deps src/.deps autom4te.cache config.status config.log Makefile
  Then set HDF5 paths via env and use bare --with-hdf5 (no path argument):
    export CPPFLAGS="-I${HDF5_PREFIX}/include"
    export LDFLAGS="-L${HDF5_PREFIX}/lib -Wl,-rpath,${HDF5_PREFIX}/lib"
    export LIBS="-lhdf5 -lz"
    ./configure --with-hdf5 --prefix=<install_prefix> ...
  Verify: grep USE_HDF5_AIORI config.h → should show 1
tags: [autotools, hdf5, stale-config, configure, ior, distclean]

---
date: 2026-06-17
app: https://github.com/llnl/ior (tag 4.0.0)
context: OpenMPI refuses to run as root in a container environment
error: |
  --------------------------------------------------------------------------
  There are components in the Open MPI that should not be run as root.
  --------------------------------------------------------------------------
root_cause: |
  The container runs as uid=0. OpenMPI's default policy refuses to launch
  as root as a safety measure.
fix: |
  Add --allow-run-as-root to mpirun and set the two confirm env vars:
    OMPI_ALLOW_RUN_AS_ROOT=1 OMPI_ALLOW_RUN_AS_ROOT_CONFIRM=1 \
      mpirun -np 1 --allow-run-as-root ./src/ior -a MPIIO ...
  When using session_run_with_dftracer, pass via env_extra:
    {"OMPI_ALLOW_RUN_AS_ROOT": "1", "OMPI_ALLOW_RUN_AS_ROOT_CONFIRM": "1"}
tags: [mpi, openmpi, root, container, smoke-test]

---
date: 2026-06-17
app: general
context: Running dftracer trace collection — always use DFTRACER_DATA_DIR=all and start dftracer_service daemon
error: |
  (not an error — a standing best-practice from IOR session experience)
root_cause: |
  Using a scoped DFTRACER_DATA_DIR (e.g., workspace/source) silently drops I/O events
  on /tmp, /scratch, or other paths where benchmarks actually write data.
  Not running dftracer_service means only inline annotation spans are captured,
  missing system-level I/O that the daemon would have recorded.
fix: |
  In the pipeline trace run (Step 7):
  1. Always pass data_dir="all" to session_run_with_dftracer.
     This sets DFTRACER_DATA_DIR=all so no I/O path is excluded.
  2. Start dftracer_service before the run and stop it after:
       SERVICE_BIN=<WS>/install_ann/bin/dftracer_service
       SERVICE_LOG=<WS>/traces/service
       mkdir -p "$SERVICE_LOG"
       DFTRACER_ENABLE=1 DFTRACER_LOG_FILE=<WS>/traces/<RUN_ID> \
         DFTRACER_DATA_DIR=all DFTRACER_TRACE_INTERVAL_MS=1000 \
         "$SERVICE_BIN" start "$SERVICE_LOG"
       # ... run application ...
       "$SERVICE_BIN" stop "$SERVICE_LOG"
  If $SERVICE_BIN is missing, skip service start/stop (service not compiled in).
tags: [dftracer, data_dir, service, daemon, trace, best-practice]

---
date: 2026-06-18
app: https://github.com/llnl/ior (tag 4.0.0)
context: session_split_traces fails because pfw files are in a subdirectory of traces/
error: |
  {"status": "error", "message": "No .pfw or .pfw.gz files found in <workspace>/traces"}
root_cause: |
  When run_id contains a slash (e.g., "ior/20260617_185032"), dftracer's LOG_FILE is
  set to <workspace>/traces/<run_id>, so it writes:
    <workspace>/traces/ior/20260617_185032-<hash>-app.pfw.gz
  The subdirectory traces/ior/ must be created before the run, AND the split tool
  looks only in traces/ directly (not subdirectories), so files must be copied up.
fix: |
  Before calling session_run_with_dftracer, create the subdirectory:
    mkdir -p <workspace>/traces/<run_id_prefix>   # e.g., traces/ior
  After the run, copy pfw files to the parent traces/ directory before splitting:
    cp <workspace>/traces/ior/*.pfw.gz <workspace>/traces/
  Then call session_split_traces normally.
tags: [dftracer, traces, split, run_id, subdirectory, pfw]

---
date: 2026-06-18
app: general
context: DFTRACER_INIT=0 prevents the POSIX interceptor from capturing syscall-level events
error: |
  dfanalyzer reports "Total Files: 0" and no POSIX-layer events in trace despite
  application running and C_APP annotations recording correctly.
root_cause: |
  Setting DFTRACER_INIT=0 disables the dftracer constructor, which prevents the
  POSIX LD_PRELOAD interceptor from initializing. The current values are FUNCTION (default and recommended), PRELOAD (is no annotation is done), and HYBRID. Only C_APP (application-level)
  annotations are recorded; open/read/write/close syscalls are never hooked.
  dfanalyzer's posix preset requires POSIX-layer events to compute file I/O metrics.
fix: |
  Do NOT set DFTRACER_INIT=0 when you want POSIX-layer tracing. It can be FUNCTION (RECOMMENDED), PRELOAD (when no applictaion annotation is done), or HYBRID (both preload is set and applictaion annotaion is done), but not 0.
  Even when the annotated source has explicit DFTRACER_C_INIT() calls, leave
  DFTRACER_INIT unset (defaults to FUNCTION). The auto-init and explicit C_INIT() coexist
  without conflict — C_INIT() is idempotent when dftracer is already initialized.
  Only set DFTRACER_INIT=0 if you explicitly do NOT want POSIX-level tracing.
tags: [dftracer, DFTRACER_INIT, posix, interceptor, dfanalyzer]

---
date: 2026-06-22
app: general
context: session_optimization_iteration merges old traces into opt-N/traces/ — comparator sees no change
error: |
  mcp__dftracer__comparator between opt{N-1}/traces_split and opt{N}/traces_split
  shows 0 delta on every metric; md5 of split chunks is identical.
root_cause: |
  session_optimization_iteration copies ALL previous iteration trace files into
  opt{N}/traces/ before running the new benchmark, then splits the combined set.
  With 194 old + 96 new = 290 total files, the dominant old-run events produce a
  split chunk byte-for-byte identical to the baseline. The profile field returns
  trace_files=[] confirming the new traces were not isolated before splitting.
fix: |
  After session_optimization_iteration completes, isolate the new-run traces:
    comm -13 <(ls opt{N-1}/traces/ | sort) \
             <(ls opt{N}/traces/   | sort) \
    | while read f; do cp opt{N}/traces/$f opt{N}_traces_clean/; done
  Then re-split the clean directory with a fresh output_dir:
    mcp__dftracer__split(directory=opt{N}_traces_clean/,
                         output_dir=opt{N}_split_clean/, force=True)
  Compare using the clean splits:
    mcp__dftracer__comparator(baseline=opt{N-1}/traces_split,
                              variant=opt{N}_split_clean/)
tags: [dftracer, comparator, traces, optimization-loop, session_optimization_iteration]

---
date: 2026-06-24
app: https://github.com/llnl/ior (tag 4.0.0)
context: ROMIO romio_ds_write=disable is catastrophic on VAST NVMe storage
error: |
  Write bandwidth collapsed to 95 MiB/s (from 352 MiB/s baseline) after setting
  MPICH_MPIIO_HINTS="*:romio_ds_write=disable". Write phase took 515s vs 140s baseline.
  Total job time ballooned from 169s to >600s.
root_cause: |
  On VAST (NVMe parallel storage), ROMIO data sieving handles non-contiguous HDF5
  collective I/O efficiently by reading-modifying-writing large aligned chunks.
  Disabling it (romio_ds_write=disable) forces ROMIO to issue thousands of individual
  small writes to non-contiguous regions, causing extreme I/O amplification.
  VAST is NOT Lustre — data sieving algorithms that hurt on spinning-disk Lustre
  (due to read-before-write) are beneficial on VAST's NVMe fabric.
fix: |
  Never set romio_ds_write=disable on VAST storage. Leave data sieving at its default.
  VAST-specific ROMIO guidance:
    GOOD:  romio_cb_write=enable  (aggregates scattered writes into large pwrite calls)
    BAD:   romio_cb_read=enable   (VAST handles parallel reads natively; CB adds overhead)
    FATAL: romio_ds_write=disable (kills write performance by preventing chunk aggregation)
  When in doubt, test with MPICH_MPIIO_HINTS unset first, then add cb_write only.
tags: [ior, hdf5, romio, vast, mpiio-hints, romio_ds_write, performance-regression]

---
date: 2026-06-24
app: https://github.com/llnl/ior (tag 4.0.0)
context: ROMIO romio_cb_read=enable hurts read performance on VAST NVMe storage
error: |
  Adding romio_cb_read=enable to MPICH_MPIIO_HINTS degraded read bandwidth from
  2163 MiB/s (no hints) to 659 MiB/s — a 70% regression.
  Setting romio_cb_write=enable alone (without cb_read) recovered reads to 1991 MiB/s.
root_cause: |
  VAST is a high-throughput NVMe parallel filesystem that handles 192 concurrent
  read requests natively and efficiently. Collective read buffering (cb_read) forces
  all 192 processes to funnel reads through a small set of aggregator processes,
  creating a coordination bottleneck. This helps on Lustre (where many small reads
  are costly due to network round-trips) but hurts on VAST's NVMe fabric where
  parallel reads are the optimal access pattern.
fix: |
  On VAST storage, use romio_cb_write=enable ONLY. Do NOT add romio_cb_read=enable.
  The hint to use is:
    MPICH_MPIIO_HINTS="*:romio_cb_write=enable"
  General rule: collective READ buffering helps when storage has high per-request
  latency (Lustre, spinning disk). It hurts on parallel NVMe where concurrent reads
  are cheap. Test cb_read vs no-cb_read explicitly before deploying.
tags: [ior, hdf5, romio, vast, mpiio-hints, romio_cb_read, performance-regression]

---
date: 2026-06-24
app: https://github.com/llnl/ior (tag 4.0.0)
context: romio_cb_write=enable is the key optimization for IOR HDF5 on VAST
error: |
  (not an error — optimization result from IOR 4.0.0 dftracer session on Tuolumne)
  posix_seek_ops_slope critical (peak 362) and posix_data_ops_slope critical (peak 74.3)
  persisted across L1 app-level changes (-t 16m, -Y) until ROMIO collective write
  buffering was enabled.
root_cause: |
  ROMIO two-phase collective I/O with 192 processes and 4m-16m transfer sizes generates
  98,304 scattered write()+lseek() pairs per iteration. Each MPI process independently
  writes a non-contiguous 512-KiB region, causing seek-and-write patterns that drive
  posix_seek_ops_slope and posix_data_ops_slope bottlenecks.
  romio_cb_write=enable switches ROMIO to aggregate all 192 process writes into 3,083
  large 16-MiB pwrite() calls via a small number of aggregator processes. This eliminates
  the seek-and-write pattern entirely.
fix: |
  For IOR HDF5 collective I/O on VAST with Cray MPICH, the optimal configuration is:
    MPICH_MPIIO_HINTS="*:romio_cb_write=enable"
    IOR flags: -a HDF5 -b 64m -t 16m -s 4 -c -Y
  Results vs baseline (-t 4m, no hints, 192 procs, 2 nodes, 48 GiB):
    Total time:   168.8s -> 112.9s  (-33%)
    Write BW:     352 -> 557 MiB/s  (+58%)
    Read BW:      1705 -> 1991 MiB/s (+17%)
    POSIX calls:  667,363 -> 73,991  (-89%)
    seek_slope:   362 -> 9.96        (-97%)
    data_slope:   74.3 -> 2.19       (-97%)
  The -t 16m (larger transfer size) and -Y (collective HDF5 metadata) flags are
  synergistic with cb_write — all three together eliminate the dominant bottlenecks.
tags: [ior, hdf5, romio, vast, mpiio-hints, romio_cb_write, optimization, posix-slope]

---
date: 2026-06-24
app: general
context: dfanalyzer uses Hydra positional overrides, not GNU-style flags
error: |
  dfanalyzer: error: unrecognized arguments: --trace-path /path/to/traces
    --view-type time_range -ahydra.analyzer/preset=posix
    --analyzer.checkpoint=true --output=console --cluster=local
  The mcp__dftracer__analyze tool (dfanalyzer_service.py) generated GNU-style
  flags that dfanalyzer does not accept.
root_cause: |
  dfanalyzer is a Hydra-based CLI tool. Hydra apps use positional key=value overrides
  to set configuration, not GNU-style --flag value pairs. The dfanalyzer_service.py
  _hydra_args() function was incorrectly generating --flag syntax.
fix: |
  dfanalyzer CLI syntax uses Hydra positional overrides:
    CORRECT:   dfanalyzer trace_path=/path/to/traces analyzer/preset=posix output=console
    INCORRECT: dfanalyzer --trace-path /path/to/traces -ahydra.analyzer/preset=posix
  Key overrides:
    trace_path=<path>
    view_types=[file_name,proc_name,time_range]   # Hydra list syntax with brackets
    analyzer=dftracer
    analyzer/preset=posix                          # forward-slash for config group
    analyzer.checkpoint=True                       # dot notation for nested keys
    analyzer.checkpoint_dir=<path>
    output=console
    cluster=local
  The fix was applied to dfanalyzer_service.py _hydra_args() to use f-string
  positional overrides instead of cmd.extend(["--flag", value]) patterns.
tags: [dfanalyzer, hydra, cli, mcp-tool, dfanalyzer_service, configuration]

---
date: 2026-06-24
app: general
context: flux proxy does not propagate environment variables to compute nodes
error: |
  After setting MPICH_MPIIO_HINTS in the shell and connecting via flux proxy,
  the env var was not visible on compute nodes. IOR ran without the ROMIO hints.
  dftracer env vars (DFTRACER_ENABLE, DFTRACER_LOG_FILE, etc.) also require
  explicit passing — they are silently dropped by flux proxy.
root_cause: |
  flux proxy creates a forwarded connection to the allocation's Flux broker but
  does NOT export the current shell's environment variables to the broker environment.
  When flux run spawns tasks inside the proxy, it inherits the broker's env (set at
  alloc time), not the current shell's env. Variables set after flux alloc or
  after entering flux proxy are invisible to job tasks.
fix: |
  Always pass env vars explicitly using --env flags with flux run:
    flux proxy <JOBID> flux run \
      -N 2 -n 192 \
      --env MPICH_MPIIO_HINTS="*:romio_cb_write=enable" \
      --env DFTRACER_ENABLE=1 \
      --env DFTRACER_LOG_FILE=<prefix> \
      --env DFTRACER_DATA_DIR=all \
      --env DFTRACER_INC_METADATA=1 \
      --env DFTRACER_INIT=FUNCTION \
      --env LD_LIBRARY_PATH=<libs> \
      <command>
  Do NOT rely on 'export VAR=value' before flux proxy — it will NOT propagate.
  Every env var that matters for the benchmark or tracing MUST be an explicit --env flag.
tags: [flux, flux-proxy, env-vars, mpiio-hints, dftracer, tuolumne, cray-mpich]

---

date: 2026-07-06
app: general (Tuolumne)
context: dftracer install fails linking test_cpp/dftracer_service — undefined reference to dlopen
error: |
  ld.lld: error: undefined reference: dlopen
  >>> referenced by ../lib64/libdftracer_core.so (disallowed by --no-allow-shlib-undefined)
  clang++: error: linker command failed with exit code 1
root_cause: |
  dlopen/dlclose/dlsym live in libdl.so.2 (this glibc has not yet merged libdl into libc).
  ld.lld's --no-allow-shlib-undefined check must locate libdl.so.2 (in /usr/lib64) to prove
  the symbol resolves. Tuolumne's systems.yaml env.LD_LIBRARY_PATH only included the CCE lib
  dirs, not /usr/lib64, so the check failed even though libdl.so.2 exists on the system.
  Separately, LD_LIBRARY_PATH alone was NOT sufficient to fix it — ld.lld does not treat
  LD_LIBRARY_PATH as a link-time search path the way the runtime loader does. Only explicitly
  adding -ldl to the link line (via LDFLAGS or DFTRACER_CMAKE_ARGS -DCMAKE_EXE_LINKER_FLAGS)
  actually resolved it.
fix: |
  1. resources/systems.yaml tuolumne env.LD_LIBRARY_PATH now includes /usr/lib64.
  2. session_install_dftracer (install.py) now merges the current system's env
     (via new get_current_system_env() in system_service.py) into pip_env before
     the pip install subprocess runs — previously it only inherited the MCP server
     process's own environment, which may lack Tuolumne-specific paths entirely.
  3. For the actual link failure, pass LDFLAGS="-ldl" and/or
     DFTRACER_CMAKE_ARGS="-DCMAKE_EXE_LINKER_FLAGS=-ldl -DCMAKE_SHARED_LINKER_FLAGS=-ldl"
     to the dftracer pip install env — this is the fix that actually worked, not just
     the LD_LIBRARY_PATH addition.
  NOTE: code changes to install.py/system_service.py require an MCP server restart
  (not just a client reconnect) to take effect — the running process has the old
  module bytecode loaded in memory.
tags: [tuolumne, dftracer-install, dlopen, libdl, ld.lld, linker, systems.yaml, mcp-tool-fix]

---
date: 2026-07-06
app: general
context: clang_annotate_file caches file content in-memory (_FILE_CACHE) keyed by (run_id, filepath) — a plain disk overwrite is invisible to it
error: |
  After manually `cp`-ing a pristine (unannotated) file over a previously-annotated one
  on disk, clang_annotate_file still reports "already_annotated": true with 0 insertions,
  and clang_write_annotated_file reports "No in-memory state" or overwrites disk with the
  STALE (previously-annotated, possibly corrupted) content instead of the fresh disk content.
root_cause: |
  annotation_clang.py's clang_annotate_file / clang_write_annotated_file keep a module-level
  dict `_FILE_CACHE[(run_id, filepath)] = list_of_lines` populated on first annotate and
  never invalidated by external file changes. Bash `cp` writes bypass this cache entirely.
fix: |
  To force a clean re-annotation after manually restoring a file from source/:
    1. cp <ws>/source/<file> <ws>/annotated/<file>   (attempt restore — may be masked by cache)
    2. clang_write_annotated_file(run_id, filepath)   (flushes the STALE cached content to
       disk AND deletes the cache entry — accept the temporary bad write)
    3. cp <ws>/source/<file> <ws>/annotated/<file>   (restore pristine content again, now
       with cache guaranteed empty)
    4. clang_annotate_file(run_id, filepath, ...)     (now genuinely re-reads disk and
       re-annotates from scratch)
  If clang_write_annotated_file returns "No in-memory state", the cache was already empty —
  skip straight to step 4.
tags: [dftracer, clang_annotate_file, cache, mcp-tool-fix, re-annotation]

---
date: 2026-07-06
app: https://github.com/Caltech-IPAC/Montage
context: clang_add_braces (via clang_annotate_file/clang_annotate_project) corrupts multi-line if-conditions and 3+ arm else-if chains — confirmed real bug, not just a cache artifact
error: |
  montageProject.c:2688:53: error: expected ')' before '{' token
  montageProjectPP.c:2289: if((output.wcs->xinc < 0 && output.wcs->yinc < 0)
                              || (output.wcs->xinc > 0 && output.wcs->yinc > 0))
                              {   <- brace inserted mid-condition, before the ')' that
                                     actually closes the multi-line if(...)
  mAdd.c:72: 'else' without a previous 'if' (4-arm else-if chain, each arm wrapped in its
  own separate { } block instead of being recognized as one chain)
root_cause: |
  The AST-range-based brace inserter mis-resolves the end line/column of an IfStmt's
  condition when the condition itself spans multiple source lines (e.g. an `if((a && b)\n
  || (c && d))` split across 2+ lines). It inserts the opening brace at the first line's
  end instead of after the true closing ')' several lines down, splitting the condition.
  A related but distinct failure mode: else-if chains with 3+ arms are sometimes not fully
  covered by the existing "else-body is IfStmt -> skip wrapping" guard, causing each arm to
  be individually braced as if it were a top-level statement.
  IMPORTANT: an earlier, unrelated bug (stale _FILE_CACHE — see prior lesson) produced
  IDENTICAL-looking symptoms and caused a previous session to misdiagnose ALL "expected
  expression"/"expected identifier" build failures as this brace-insertion bug. Always rule
  out the cache issue first (verify the file on disk actually reflects a fresh
  clang_annotate_file call, not a stale write) before concluding this is a real parser bug.
fix: |
  No source_parser.py fix applied this session (would need a server restart to test, and
  time did not allow safely verifying a fix against the live annotator). Interim mitigation:
  when clang_syntax_check reports "expected ')'", "expected expression before '{'", or
  "'else' without a previous 'if'" AFTER confirming it's not a cache artifact (see prior
  lesson's revalidation steps), revert that single file to pristine via
  `cp source/<f> annotated/<f>` and leave it unannotated — do NOT hand-patch with `#if 0`
  wrapping or manual brace edits (this was tried by a subagent and rejected; hand-patching
  corrupts semantics in ways a full validation pass won't catch).
  Root-cause fix belongs in source_parser.py's `_collect_braceless` / brace-range resolver:
  it needs to walk forward past line-continuations when computing an IfStmt condition's true
  end line before deciding where to insert braces, and the else-if guard needs to handle
  chains of arbitrary depth (recurse fully, not just one level).
tags: [c, clang, brace-insertion, multi-line-condition, else-if, montage, mcp-tool-bug, known-limitation]

---
date: 2026-07-06
app: https://github.com/Caltech-IPAC/Montage
context: plain recursive-Makefile projects (no cmake/autotools) need PATH-shadowing, not CC= override, to inject dftracer link flags
error: |
  mtbl.c:7:10: fatal error: mtbl.h: No such file or directory
  (after passing `make CC=<wrapper-script>` to link against libdftracer_core)
root_cause: |
  Montage's ~140 per-module Makefiles are NOT uniform: most set `CC = gcc` and a separate
  `CFLAGS = -I. -I.. ...`, but some vendored lib/src Makefiles fold everything into one
  line: `CC = gcc -g -fPIC -I . -D_LARGEFILE_SOURCE ...`. Passing `CC=<wrapper>` on the
  `make` command line completely REPLACES that variable for every sub-make (command-line
  vars have highest precedence in GNU make), silently discarding the embedded `-I .` flags
  in the single-line-CC Makefiles and breaking their local header includes.
  Separately, some modules use `CC = cc ...` instead of `gcc`, so a gcc-only wrapper doesn't
  catch them either.
fix: |
  Do NOT override CC via `make CC=...` for heterogeneous legacy Makefile trees. Instead:
    1. mkdir -p <ws>/tmp/binshim
    2. Write executable shim scripts named exactly `gcc` AND `cc` in that directory, each
       execing the REAL compiler (resolved via `command -v` with a restricted PATH before
       shadowing) with all passed-through args plus the extra link flags appended:
         #!/bin/bash
         exec /real/path/to/gcc "$@" -L<dftracer_lib> -Wl,-rpath,<dftracer_lib> -ldftracer_core
    3. export PATH="<ws>/tmp/binshim:$PATH" (no CC= on the make command line at all)
    4. export CPATH="<dftracer_include_dir>" for the #include <dftracer/dftracer.h> resolution
       (CPATH is honored by gcc/clang as an implicit -I for every invocation, so it doesn't
       clobber per-Makefile CFLAGS/CC content the way overriding CC or CFLAGS would).
  Appending -l/-L/-rpath flags to a compile-only ("-c") invocation is harmless (ignored,
  no link phase runs), so a single wrapper safely handles both compile and link calls.
  Also: Montage's nested `(cd X && make)` recipe pattern is not jobserver-safe under `-j`>1
  at the top level (only sub-makes show "jobserver unavailable" warnings, but top-level
  parallel directory recipes still race on shared objects like `ar`-built static libs) —
  use `make -j1` for this codebase's top-level build to avoid nondeterministic races
  (e.g. "ar: util/checkFile.o: No such file or directory" when checkFile.c hadn't finished
  compiling yet in a sibling directory).
tags: [c, make, plain-makefile, cc-override, path-shadow, cpath, montage, jobserver, race-condition]

---
date: 2026-07-06
app: general
context: for large multi-binary C codebases, annotate only the files a specific smoke test actually exercises, not the whole tree
error: |
  (not an error — a scoping/efficiency lesson)
  Montage has ~700 source files across 100+ independently-linked executables. Running
  clang_annotate_project over the whole tree annotated 266 files, most of which (HiPS
  tile-pyramid tools, PNG/JPEG viewers, Globus/Pegasus DAG generators, MovingTarget/rtree,
  vendored third-party libs) are never invoked by the actual mosaic-building pipeline a
  user's workflow (e.g. montage-workflow-v3) runs. This wasted annotation/validation effort
  and needlessly exposed the session to annotator edge-case bugs (see brace-insertion
  lesson above) in code paths nobody will ever trace.
root_cause: |
  No tool existed to answer "given this smoke test command, which source files actually
  matter?" before annotating — clang_annotate_project's only scoping knob was glob-pattern
  exclusion, which requires already knowing which directories to skip.
fix: |
  Added `session_identify_smoke_test_files` MCP tool (annotation_filter.py) that:
    1. Extracts binary names invoked by a smoke_cmd — preferring `strace -f -e trace=execve`
       against the ORIGINAL (unannotated, already-built) tree for ground truth, falling back
       to a static text-scan of smoke_cmd against install/bin/ contents.
    2. For each binary, parses the Makefile link recipe (`$(CC) ... -o <name> <objs...>`,
       following backslash-continuation lines) to extract every .o object file.
    3. Resolves each .o back to its .c/.cpp source, honoring the Makefile's own relative
       path context (e.g. `../util/foo.o` from `MontageLib/Add/Makefile` resolves to
       `MontageLib/util/foo.c`).
    4. Returns the de-duplicated union of source files across all invoked binaries.
  For Montage's 10-binary mosaic pipeline (mArchiveList, mProjExec, mOverlaps, mDiffExec,
  mFitExec, mBgModel, mBackground/mBgExec, mAdd, mImgtbl) this narrowed 267 files to 51 —
  an 81% reduction. ALWAYS present the resulting file list to the user for confirmation
  (grouped by which binary/pipeline-stage needs it and why) before annotating, so they can
  adjust the binary list first if the smoke test scope was wrong.
  New tool registered in dftracer_service.py's session_subservice; requires MCP server
  restart to become callable (code changes to the server module don't hot-reload).
tags: [dftracer, annotation-scoping, montage, large-codebase, filter-tool, mcp-tool-added, best-practice]

---


---
date: 2026-07-06
app: https://github.com/llnl/ior (tag 4.0.0)
context: Annotating IOR C on Tuolumne; DFTRACER_C_INIT third arg type + C→C++ link.
error: |
  annotated/src/ior.c:110:37: error: incompatible integer to pointer conversion
  passing 'int' to parameter of type 'int *' [-Wint-conversion]
    DFTRACER_C_INIT(NULL, NULL, -1);
  (macro expands to initialize_main(log_file, data_dirs, process_id) where
   process_id is 'int *')
root_cause: |
  DFTRACER_C_INIT's third argument (process_id) is 'int *', not int. Passing a
  literal like -1 is an int→pointer conversion error under clang/cce. The clang
  annotate tools default init_args to "NULL, NULL, -1" which is wrong for this
  dftracer header.
fix: |
  Always pass init_args="NULL, NULL, NULL" to clang_annotate_project /
  clang_annotate_file for C. All three DFTRACER_C_INIT args are pointers.
tags: [c, annotation, dftracer-c-init, tuolumne, cce]

---
date: 2026-07-06
app: https://github.com/llnl/ior (tag 4.0.0)
context: Linking a C app against libdftracer_core.so (C++) on Tuolumne/cce.
error: |
  ld.bfd: libdftracer_core.so: undefined reference to
    std::filesystem::...@GLIBCXX_3.4.26 / ...@CXXABI_1.3.13
  then at configure run: "C compiler cannot create executables";
  then at run: /usr/lib64/libstdc++.so.6: version GLIBCXX_3.4.29 not found
root_cause: |
  dftracer_core is C++ and needs libstdc++ >= GLIBCXX_3.4.29 plus libyaml-cpp.
  (1) -lstdc++ is dropped by --as-needed because the C main references no C++
      symbols directly, so NEEDED-shlib undefined refs fail the link.
  (2) The OS /usr/lib64/libstdc++.so.6 is 6.0.25 (only up to 3.4.25) and gets
      picked at runtime.
fix: |
  Link with: LIBS="-ldftracer_core -lstdc++",
  LDFLAGS+=" -Wl,--allow-shlib-undefined -Wl,--no-as-needed".
  Runtime/configure-run: prepend the python module lib dir (has GLIBCXX_3.4.29 +
  libyaml-cpp) AND dftracer/lib64 to LD_LIBRARY_PATH, BEFORE /usr/lib64:
  export LD_LIBRARY_PATH="$DFT/lib64:/usr/tce/packages/python/python-3.13.2/lib:$LD_LIBRARY_PATH"
tags: [c, cpp, linking, libstdc++, tuolumne, cce, dftracer_core]

---
date: 2026-07-06
app: https://github.com/Caltech-IPAC/Montage (montage-workflow-v3 pipeline via Pegasus/PMC)
context: annotating a per-pixel hot-loop function (mAdd_avg_mean) produced 11.9M trace events / 1.87GB from a single 4-image mosaic, drowning out real POSIX I/O signal
error: |
  (not a build/runtime error — a trace-quality/diagnosability failure)
  dfanalyzer summary on the resulting trace showed "Total Files: 3" and an
  EMPTY POSIX layer breakdown table despite 11.9M total events, making
  bottleneck diagnosis impossible. Manually inspecting the largest trace file
  (76MB compressed) showed cat=C_APP name=mAdd_avg_mean accounted for
  499,613 of the first 500,000 sampled events (99.9%).
root_cause: |
  clang_annotate_file's static AST-cost filter (clang_estimate_function_cost)
  scored mAdd_avg_mean above the annotate threshold because its source body
  looks non-trivial (a loop + conditional). But it is called once per output
  pixel during coaddition — for even a small 120x120 mosaic this is tens of
  thousands of calls per image, and scales with mosaic size. Static cost
  estimation has no way to know runtime call frequency; any per-pixel/
  per-element inner-loop function is a blind spot for this heuristic
  regardless of the score threshold used.
  This is NOT the same as the earlier clang_add_braces multi-line-if bug —
  the file compiled and ran correctly; the problem is purely instrumentation
  density overwhelming the trace with true-but-useless micro-events.
fix: |
  1. Added an `exclude_functions` JSON-array parameter to `clang_annotate_file`
     (annotation_clang.py) so specific hot-loop functions can be force-skipped
     regardless of the cost filter, e.g.:
       clang_annotate_file(run_id=..., filepath=..., exclude_functions='["avg_mean"]')
     (Requires an MCP server restart to take effect — see prior lessons on
     code changes not hot-reloading.)
  2. For the live session (no restart available), manually removed the 3
     DFTRACER_C_FUNCTION_START/UPDATE_STR/END lines around mAdd_avg_mean by
     hand — this is safe (unlike the forbidden #if-0 brace hacks) because it
     is a clean full-line deletion of already-syntactically-valid macro
     statements, not a structural patch working around a parser bug.
  3. Rebuilt, re-ran the same Pegasus/PMC workflow: trace count dropped from
     11.9M events (1.87GB, 3 files visible) to 62,708 events (28 files visible,
     811.5MB real I/O, 740.6MB/s aggregate bandwidth) — the difference between
     an undiagnosable trace and a usable one.
  RULE OF THUMB: before trusting a trace-derived bottleneck analysis, always
  sanity-check `unique_file_count` and total event count against expectations
  for the workload size. A tiny unique-file-count with a huge event count is
  the signature of one hot annotated function dominating the trace — go find
  it (grep the largest .pfw.gz for the most frequent `name` field) before
  trusting the numbers.
tags: [dftracer, annotation, hot-loop, cost-filter, trace-noise, montage, mAdd_avg_mean, exclude_functions, mcp-tool-fix, montage-workflow-v3]

---
date: 2026-07-06
app: https://github.com/Caltech-IPAC/Montage (montage-workflow-v3 via Pegasus/PMC)
context: applied and verified real L2 (posix_fadvise) + L3 (NFS->Lustre) I/O optimizations end-to-end through the actual Pegasus workflow
error: |
  (not an error — a successful optimization + a documented decision to NOT
  do the riskier L1 change)
root_cause: |
  Baseline dfanalyzer summary (after fixing the mAdd_avg_mean trace-noise
  issue, see prior lesson) showed 62,380 POSIX ops / 811.5MB / 740.6MB/s /
  13KB avg transfer, running on NFS (the project's NFS filesystem). Two concrete, safe
  optimizations were available:
    L2: mProject.c/montageAdd.c call fits_read_pix()/fits_open_file() but
        never hint the kernel about the row-by-row sequential access
        pattern that follows.
    L3: the whole session had been running on NFS (the project's NFS filesystem), not Lustre,
        despite Lustre being available at /p/lustre5/$USER.
  A third option (L1: rewrite mProject/mAdd's row-by-row fits_read_pix/
  fits_write_pix calls into larger batched multi-row reads) was considered
  and REJECTED — Montage's per-row I/O is an intentional design choice
  (bounds memory usage for arbitrarily large mosaics); rewriting a mature
  scientific library's numerical I/O path without pixel-correctness
  regression tests against known-good mosaics is not something to do
  blindly just to hit an optimization checklist.
fix: |
  L2: add `#include <fcntl.h>` (with `#define _DEFAULT_SOURCE` as the
  FIRST line of the file, before any other #include — feature-test macros
  like _DEFAULT_SOURCE/_POSIX_C_SOURCE only take effect if defined before
  the first system header that would otherwise lock in a stricter default
  under -std=c99; adding it later triggers "POSIX_FADV_SEQUENTIAL
  undeclared" because fcntl.h's own multiple-inclusion guard has already
  fired with the wrong feature-test state).
  Then, right after every `fits_open_file()` call on the hot read path:
    int advise_fd = open(filename, O_RDONLY);
    if (advise_fd >= 0) {
      posix_fadvise(advise_fd, 0, 0, POSIX_FADV_SEQUENTIAL);
      close(advise_fd);
    }
  This is a pure kernel read-ahead hint via a SEPARATE fd — it does not
  touch cfitsio's internal file handle or any FITS data path, so it's safe
  to add without re-verifying pixel correctness.
  L3: write a new Pegasus sites.yml pointing sharedScratch/localStorage at
  /p/lustre5/$USER/<project>/{scratch,storage} instead of an NFS-backed
  workspace dir, then `rm -rf work_lustre && pegasus-plan --dir work_lustre
  --sites local ...` (site name stays "local"; only its directories changed).
  RESULT (identical op count/data volume before vs after, confirming no
  behavior change — only performance):
    Bandwidth:   740.6 MB/s -> 859.8 MB/s   (+16.1%)
    POSIX time:  1.096s     -> 0.944s       (-13.9%)
    Ops/bytes:   62,380 / 811.5MB  (unchanged both runs)
  Caveat: at this test-mosaic scale (4 images, <1s total I/O time), the
  wall-clock impact is dominated by Pegasus/PMC per-task dispatch overhead
  (chmod/register/cleanup bookkeeping across 57 tasks), not I/O — the 16%
  bandwidth gain matters far more at production mosaic scale.
tags: [dftracer, optimization, posix_fadvise, lustre, nfs, montage, fcntl, feature-test-macro, l2-l3-optimization, montage-workflow-v3]

---
date: 2026-07-06
app: general (Pegasus 5.0.7 / PMC)
context: "Lustre" runs were never actually on Lustre -- site catalog sharedScratch is not the execution directory
error: |
  (not an error -- a silent, plausible-looking measurement bug)
  A previous "L3 optimization: moved to Lustre" result (+16% bandwidth) was
  later found to be invalid: the actual PMC task execution directory
  (`-w` flag in every TASK line of the generated PMC dag) was
  `<CWD-at-plan-time>/wf-scratch/LOCAL/.../run0001` -- a real directory on
  whatever filesystem the submit host's CWD was on -- NOT the Lustre path
  declared in sites.yml's sharedScratch/localStorage directories.
root_cause: |
  Pegasus's site catalog sharedScratch/localStorage paths control where
  DATA TRANSFER jobs (stage-in/stage-out) stage files for the replica
  catalog -- they do NOT control where PegasusLite/PMC actually executes
  compute jobs. The execution scratch directory is always
  `<CWD when pegasus-plan ran>/wf-scratch/<site>/<user>/pegasus/<wf-name>/<run-id>`,
  independent of the site catalog, for this style of local-site PMC
  execution. Setting sites.yml to point at Lustre gives a false sense of
  having moved the I/O path.
  A second, related bug: this wf-scratch path is namespaced only by
  workflow name + run number (e.g. "montage" + "run0001"), NOT by the
  `--dir` submit-directory flag passed to pegasus-plan. Two different
  workflow plans issued from the SAME CWD with the same workflow name
  silently share (and contaminate) the same physical wf-scratch directory,
  even if planned into different `--dir` submit trees.
fix: |
  To genuinely run on Lustre: `cd` into a directory that is ITSELF on
  Lustre before calling pegasus-plan (copy montage-workflow.py + data/ +
  pegasus.properties there first). Then verify before running:
    grep -m1 "^TASK mProject" <run-dir>/montage-0.dag | grep -oP '(?<=-w )\S+'
  -- confirm the printed path actually starts with /p/lustre... (or
  whatever your target filesystem's mount prefix is) before submitting to
  PMC. Never trust the site catalog path alone as proof of where jobs ran.
  To avoid cross-run contamination: move or rm the previous run's
  wf-scratch (and wf-output) aside before planning+running a new,
  independent measurement from the same CWD -- do this even between runs
  that are supposed to be "the same workflow, different config", since
  leftover intermediate files from a prior run skew dfanalyzer's
  unique_file_count / total_bytes for the new run.
  See software-pegasus skill's script `plan_and_run_2mass_on_lustre.sh`
  for an automated version of this verification check.
tags: [pegasus, pmc, lustre, nfs, sharedScratch, wf-scratch, measurement-bug, montage-workflow-v3, software-pegasus]

---
date: 2026-07-06
app: general
context: mcp__dftracer__analyze (dfanalyzer) hangs the whole MCP connection indefinitely after finishing real work
error: |
  Calling the `analyze` MCP tool would hang forever / eventually return
  "MCP error -32000: Connection closed". Running the identical dfanalyzer
  command directly via Bash in the background and reading its log file
  showed the command's real output (including the final
  "Cluster teardown" line) appeared within ~1 minute, but the OS process
  itself kept running at 99% CPU indefinitely afterward, never exiting on
  its own (confirmed via `ps aux`).
root_cause: |
  dfanalyzer's dask LocalCluster hangs during its own shutdown/teardown
  phase after all real analysis output has already been printed and
  flushed. The `analyze` MCP tool's implementation
  (dfanalyzer_service.py) called `subprocess.run(cmd, capture_output=True,
  text=True)` with NO timeout -- this blocks until the child process fully
  EXITS, not until it stops producing output, so a hung teardown looks
  identical to a stuck/broken MCP tool from the caller's perspective even
  though the actual analysis already succeeded.
fix: |
  Added `timeout=300` to the subprocess.run() call in
  dfanalyzer_service.py's `analyze()` function, and a `except
  subprocess.TimeoutExpired` handler that treats a timeout AFTER
  non-empty stdout was already captured as success (Python's
  subprocess.run/Popen.communicate populates exc.stdout/exc.stderr with
  whatever was captured before the timeout fired, even though the
  process is then killed). Only reports failure if no output was
  captured before the timeout.
  Workaround for the current session (before an MCP server restart makes
  the fix live): invoke `dfanalyzer` directly via Bash, redirected to a
  log file, run with `&` in the background, sleep briefly, then read the
  log file directly and `pkill -9 -f "dfanalyzer trace_path=<path>"` once
  the log shows the final "Cluster teardown" line -- do NOT `wait` on the
  backgrounded PID, since that reintroduces the same indefinite hang.
tags: [dftracer, dfanalyzer, dask, hang, timeout, mcp-tool-fix, subprocess]

---
date: 2026-07-08
app: n/a (dftracer-agents MCP tool code)
context: follow-up bug in the SAME analyze() timeout handler above — the "timeout after output is success" except branch itself crashed
error: |
  With the timeout=300 fix above in place, a trace whose dfanalyzer dask
  LocalCluster hung on teardown (e.g. h5bench-run1) still made the `analyze`
  MCP tool fail outright, now with `TypeError: can't concat str to bytes`
  instead of hanging.
root_cause: |
  The `except subprocess.TimeoutExpired` handler assumed `exc.stdout`/
  `exc.stderr` were `str` (since the call passed `text=True`), and did
  `stderr += "\n[...]"`. CPython's `subprocess.run(..., text=True,
  timeout=...)` actually returns `TimeoutExpired.stdout`/`.stderr` as
  **bytes**, not str, even though a normal (non-timeout) completion under
  `text=True` decodes them — `text=True` is honored for the successful-return
  path but not for the exception object's captured partial output. So the
  "treat timeout-with-output as success" logic added for the hang bug above
  crashed on its own success path.
fix: |
  In `src/dftracer_agents/mcp_tools/tools/dftracer/dfanalyzer_service.py`,
  decode `exc.stdout`/`exc.stderr` from bytes to str (checking `isinstance(...,
  bytes)` before calling `.decode()`, since the field's actual type is not
  guaranteed by the `text=True` flag when raised via TimeoutExpired) before
  any string concatenation in the except handler. Requires an MCP server
  restart to load — see [[project_claude_agent_models]].
tags: [dftracer, dfanalyzer, dask, hang, timeout, bytes, decode, mcp-tool-fix, subprocess]

---
date: 2026-07-06
app: https://github.com/pegasus-isi/montage-workflow-v3
context: annotation scoping filter must check the ACTUAL executed binaries, not assumed classic-tool names
error: |
  Original smoke-test-scoped annotation (session_identify_smoke_test_files)
  covered mProject, mOverlaps, mDiffExec, mFitExec, mBgModel, mBackground/
  mBgExec, mAdd, mImgtbl, mArchiveList -- but checking the real generated
  Pegasus DAG's TASK lines showed the workflow actually invokes mDiffFit
  (a combined single-shot tool, not the separate batch mDiffExec+mFitExec
  pair), plus mConcatFit and mViewer, neither of which were in the original
  scope at all. 3 of 8 real executables had zero instrumentation.
root_cause: |
  montage-workflow-v3's Pegasus DAX generator (montage-workflow.py) uses a
  different, smaller set of Montage tools than the classic non-workflow
  batch pipeline the original filter run was modeled on. Grepping for
  "mDiffExec"/"mFitExec" in the transformation catalog would have shown
  zero matches -- the actual per-job single-invocation tool names must be
  read from the generated DAG's own TASK lines, not assumed from Montage's
  general tool list or from the batch-pipeline naming convention.
fix: |
  After generating (or receiving) a Pegasus DAG, always cross-check binary
  coverage directly against the DAG itself before declaring annotation
  scope complete:
    grep "^TASK" <run-dir>/montage-0.dag | awk '{print $2}' \
      | sed 's/_ID[0-9]*_*[0-9]*$//' \
      | sed 's/^\(chmod_\|stage_in_\|stage_out_\|clean_up_\|register_\|cleanup_\|create_dir_\)//' \
      | sort | uniq -c | sort -rn
  Then for each real compute-task binary name, verify instrumentation:
    grep -c DFTRACER <annotated-dir>/path/to/entry_file.c
  Do this BEFORE running a large/expensive workflow, not after -- it's the
  only reliable way to confirm trace coverage matches the workflow's real
  execution graph, not just what was assumed during initial scoping.
tags: [dftracer, annotation-scoping, montage-workflow-v3, pegasus, mDiffFit, mConcatFit, mViewer, coverage-verification]


---
date: 2026-07-16
app: https://github.com/flux-framework/flux-fiction
context: Python workload with pyproject.toml [project.scripts] console-script entry points cannot be auto-detected by session_identify_smoke_test_files
error: |
  session_identify_smoke_test_files looks for compiled binaries in install/bin/
  and fails to locate console scripts like "flux-fiction-run" that are defined
  in [project.scripts] of pyproject.toml. No entry-point files are flagged for
  annotation, so the smoke-test annotation scope misses all session-level init/finalize
  calls (dftracer.initialize_log() and finalize()).
root_cause: |
  session_identify_smoke_test_files uses strace on the smoke-test binary to
  discover which application files are actually executed (it's designed for C/C++
  binaries in install/bin/). For Python apps, console scripts are installed as
  thin wrapper scripts (generated by setuptools/pip) that parse arguments and
  call the actual Python module/function target (e.g., from flux_fiction.cli.run_ff:main).
  The session-level entry point is NOT in the binary itself -- it's in the Python
  module code -- so strace on the wrapper script does not find it.
fix: |
  For Python workloads with console-script entry points:
  1. Read [project.scripts] section of pyproject.toml to identify entry-point names
  2. Extract module:function target (e.g., flux_fiction.cli.run_ff:main)
  3. Locate the actual Python module file in source/
  4. Verify that file has dftracer.initialize_log(...) and finalize() calls at
     the entry function (usually inside if __name__ == "__main__" or at the top
     of main(). If missing, add them before running smoke tests.
  5. Annotate the entry-point module with @dftracer_fn decorators on all
     non-trivial functions as usual (cost-gate with threshold=20 to avoid
     over-instrumentation of pure CLI argument parsing).
  When calling session_identify_smoke_test_files, pass the actual Python module
  path (source/src/flux_fiction/cli/run_ff.py) rather than the console-script
  wrapper name, or invoke the module directly via `python -m <module>` in smoke
  tests to bypass the console-script indirection.
tags: [python, annotation, console-script, entry-point, pyproject.toml, session_identify_smoke_test_files]

---
date: 2026-07-16
context: flux-fiction Python annotation (models.py, status.py) — @property getters wrapped with @_dft.log
error: |
  Stacking a plain @_dft.log (or @dftracer_fn) decorator ON TOP of @property
  replaces the property descriptor with the decorator's wrapper object, so
  `obj.attr` returns an unbound method instead of invoking the getter.
  Symptom: "Object of type method is not JSON serializable" when a supposedly
  plain-value property (e.g. Job.jobspec) was actually returning a bound method.
root_cause: |
  Decorator ordering: @property must be OUTERMOST (closest to the class body),
  @_dft.log applied to the underlying function would need to be INSIDE that —
  but a plain stacked decorator can't express that safely across getter/setter/
  deleter variants, so the correct fix is a contextual `with DFTracerFn(...)`
  region inside the property's body instead of any decorator at all (same
  treatment as @staticmethod, which has an analogous ordering conflict).
fix: |
  FIXED AT THE TOOL LEVEL (not just documented): python_annotate_file/
  python_annotate_project (annotation_python.py) now detects @property/
  @cached_property/@x.setter/@x.deleter/@x.getter via has_property in
  _extract_functions_from_ast, and routes those functions through the same
  contextual `with DFTracerFn(...)` region path already used for
  @staticmethod, instead of stacking a decorator. This is a permanent,
  generic fix — future annotation runs cannot reintroduce this bug via the
  MCP tools. Only a hand-written/manual annotation pass could still get this
  wrong.
tags: [python, annotation, property, decorator-ordering, tool-fix]

---
date: 2026-07-22
app: PECAN (PyTorch DataLoader, num_workers>0, persistent_workers=True, multiprocessing_context="spawn")
context: 32 of 80 dftracer trace files were exactly 0 bytes, identically across multiple runs — DataLoader worker process traces missing/incomplete
error: |
  No crash, no exception — trace collection silently produced 32 literal
  0-byte .pfw.gz files out of 80 total (16 main-process "-app" files + 32
  worker files with real data + 32 EMPTY worker files, consistent across
  independent runs of the same config). Populated worker files were also
  suspect: no guarantee their tail events were flushed either.
root_cause: |
  dftracer's Python API (initialize_log()/finalize()) was only called ONCE,
  in the app's single main process (main_app.py, at start/end of the whole
  script). PyTorch DataLoader workers, spawned via
  multiprocessing_context="spawn" with persistent_workers=True, each get
  their own dftracer C-core state (lazily started on first
  dft_event_logging(...) call inside the dataset's __getitem__), but no
  code ever called finalize() inside a worker process. When Python's
  multiprocessing tears a persistent worker down at interpreter exit, no
  app-level cleanup runs, so a worker's gzip trace stream — opened but
  never explicitly flushed/closed — can end up literally 0 bytes on disk
  (low event volume, no internal buffer-size auto-flush ever triggered) or
  silently missing its tail events (higher volume, some periodic auto-flush
  luck, but no guaranteed-complete final flush).
fix: |
  Added a `worker_init_fn` that calls dftracer's initialize_log() and
  registers atexit.register(finalize, logger) INSIDE every DataLoader
  worker process, so each worker flushes and closes its own trace on exit,
  the same way the main process already does. Any existing per-worker init
  the app needs (e.g. a DYAD staging worker_init) should be looked up
  dynamically via torch.utils.data.get_worker_info().dataset inside the
  function body, NOT captured via a closure — see the tags for why.
  Validated: empty-file count went from 32/80 to 0/48 (the placeholder-only
  files stopped being created at all) with no wall-time regression.
tags: [python, annotation, pytorch, dataloader, multiprocessing, spawn, worker_init_fn, finalize, atexit, missing-trace-data, 0-byte-trace, persistent_workers]

---
date: 2026-07-22
app: PECAN (torch.utils.data.DataLoader, multiprocessing_context="spawn")
context: implementing the worker-level dftracer finalize() fix above — first attempt crashed every rank
error: |
  AttributeError: Can't pickle local object
  '_make_dftracer_worker_init.<locals>._worker_init' — raised the instant
  the DataLoader tried to hand worker_init_fn to a spawned child process.
root_cause: |
  The first implementation was a closure-returning factory function
  (`def _make_x(inner_fn): def _worker(wid): ...; return _worker`) so an
  existing per-worker init callable could be captured for chaining.
  multiprocessing_context="spawn" PICKLES worker_init_fn to send it to the
  child process (fork does NOT — it just inherits the parent's memory, so
  this class of bug is invisible under the default fork context and only
  surfaces once an app switches to spawn, e.g. for a HIP/CUDA-safety fix).
  A closure returned by a factory is not picklable in Python.
fix: |
  Rewrote as a plain TOP-LEVEL function (no factory, no closure). Any
  runtime-dependent chaining (e.g. "call the dataset's own worker_init if
  it has one") is resolved INSIDE the function body via
  torch.utils.data.get_worker_info().dataset + getattr(...), not captured
  from an enclosing scope. General rule: any callable that must cross a
  multiprocessing "spawn" boundary (worker_init_fn, a target passed to
  Process(target=...), etc.) must be a plain top-level function or a bound
  method on a picklable object — never a closure/nested function.
tags: [python, multiprocessing, spawn, pickle, closure, worker_init_fn, pytorch, dataloader]

---
date: 2026-08-04
app: https://github.com/llnl/ygm
context: annotating a header-only C++ library (implementation split into .ipp files included at the bottom of .hpp headers) with DFTRACER_CPP_* macros
error: |
  Compile errors deep inside ygm/detail/comm.ipp, collective.hpp, mpi.hpp, and
  comm_environment.hpp complaining that DFTRACER_CPP_FUNCTION / UPDATE macros
  are undefined, even though every one of those files was correctly annotated
  and #include <dftracer/dftracer.h> was present in the entry test_comm.cpp.
root_cause: |
  test_comm.cpp had #include <dftracer/dftracer.h> AFTER #include <ygm/comm.hpp>.
  comm.hpp transitively pulls in comm.ipp/collective.hpp/mpi.hpp/comm_environment.hpp
  at preprocess time; those headers use DFTRACER_CPP_* macros in their annotated
  method bodies, so by the time they're parsed the macros must already be defined.
  Include order matters here in a way it normally doesn't for a single flat .cpp.
  See CP9 in this skill's SKILL.md.
fix: |
  In the entry .cpp, make #include <dftracer/dftracer.h> the FIRST include,
  before any of the library's own headers:
    #include <dftracer/dftracer.h>   // must be first
    #include <ygm/comm.hpp>
  Also do NOT let clang_annotate_file insert a stray #include <dftracer/dftracer.h>
  directly into a .ipp/.hpp header (CP6) — it did so once here and had to be
  manually removed.
tags: [cpp, header-only, ipp, include-order, ygm, cp9, macro-undefined]

---
date: 2026-08-04
app: https://github.com/llnl/ygm
context: clang_annotate_file / clang_extract_functions return 0 functions for a .ipp file that defines Class::method(...) bodies for a class declared in a separate .hpp
error: |
  clang_extract_functions(comm.ipp) returned an empty function list (not an
  error — just 0 results) despite the file containing ~55 real method
  definitions (ygm::comm::method(...) {...}).
root_cause: |
  comm.ipp defines out-of-line method bodies for the ygm::comm class, which is
  only forward/fully declared in the sibling comm.hpp. The clang-based AST
  tools have no -I/extra-include-dirs style parameter to resolve <ygm/...>
  include paths or hand the tool comm.hpp's class context, so the AST parse of
  comm.ipp in isolation fails to resolve the class and silently yields 0
  functions — not a template-instantiation problem, a missing-context problem.
fix: |
  No tool-level fix applied this session (would need clang_annotate_file /
  clang_extract_functions to accept an extra_include_dirs param the same way
  clang_syntax_check already does, so the .ipp can be parsed with the class's
  own headers visible). Interim workaround: fall back to a manual, scoped
  annotation pass applying the same Rule 0 skip criteria and comp= table by
  hand, then verify with a real compiler (mpicxx -fsyntax-only -std=c++20)
  plus clang_lint_annotations instead of relying on clang_extract_functions'
  function count.
tags: [cpp, clang_annotate_file, clang_extract_functions, ipp, header-only, mcp-tool-gap, ygm]

---
date: 2026-08-04
app: general (Tuolumne)
context: dftracer can be installed from a prebuilt prerelease distribution via a modulefile, skipping session_install_dftracer's from-source CMake build entirely
error: |
  (not an error — a new, faster install path discovered this session)
root_cause: |
  Every prior session installed dftracer either via session_install_dftracer's
  from-source pip build (compiling the C/C++ extension against the session's
  own MPI/HDF5/compiler) or a manual `pip install dftracer`/`pip install
  git+https://github.com/LLNL/dftracer.git@develop`, both of which compile.
  Tuolumne also hosts a prebuilt wheel distribution via a modulefile that
  session_install_dftracer does NOT know about — using it skips compilation
  entirely (no CC/CXX/LD_LIBRARY_PATH dance, no chid_t/dlopen/link-order
  pitfalls from the rest of this log).
fix: |
  ml use $HOME/dftracer/distributions/modulefiles
  ml load dftracer-dist
  ml load python/3.11
  python -m venv venv-311 && source venv-311/bin/activate
  pip install --pre dftracer
  This installs dftracer 2.1.1.post22.dev0 + pydftracer + dftracer-utils as a
  prebuilt wheel: libdftracer_core.so, headers, AND ready-to-use CMake config
  files (dftracer-config.cmake etc. under lib64/cmake/dftracer/) all present
  out of the box — no session_generate_dftracer_pc / manual CMake wiring
  needed for CMake-based C++ projects (unlike the autotools .pc-generation
  path in dftracer-install SKILL.md).
  Caveat: this prebuilt wheel showed no MPI library in `ldd` output — if a
  workload needs dftracer's own MPI-IO interception (not just FUNCTION-mode
  app-level annotation around MPI calls, which works fine), verify with
  `ldd libdftracer_core.so | grep -i mpi` before relying on this path; a
  from-source build with DFTRACER_ENABLE_MPI=ON may still be needed.
  Python version must match the modulefile's target (python/3.11 here, not
  the system default python/3.13.2) — mismatched Python breaks the wheel's
  ABI the same way an from-source build would (see RULE 0 in dftracer-install).
tags: [tuolumne, dftracer-install, prerelease, modulefile, dftracer-dist, prebuilt-wheel, mcp-tool-gap]

---
date: 2026-08-04
app: https://github.com/llnl/ygm
context: session_build_annotated's build_subdir parameter was silently ignored for cmake/autotools/meson build tools — CONFIRMED and FIXED at the tool level
error: |
  session_build_annotated(run_id, build_subdir="source", extra_cmake_flags=...)
  failed cmake configure with:
    "The source directory <ws>/annotated does not appear to contain CMakeLists.txt"
  even though build_subdir="source" was passed and <ws>/annotated/source/CMakeLists.txt
  genuinely exists (YGM's session layout nests the repo as annotated/source/, not
  annotated/ directly — matching the top-level source/ tree's own layout).
root_cause: |
  In session_tools.py's _session_build_annotated_impl, the build_subdir parameter
  was ONLY honored inside the custom_build_cmd escape hatch (`work = ann /
  build_subdir if build_subdir else ann`). The cmake branch hardcoded
  `cmake -S str(ann) -B str(build_ann)` — always the bare annotated/ root — and
  the autotools branch hardcoded `ann / "configure"` / `cwd=ann` for autoreconf,
  and the meson branch hardcoded `meson setup <build_ann> str(ann)`. build_subdir
  was accepted as a parameter and documented, but three of the four build-tool
  branches never read it.
fix: |
  Fixed in session_tools.py: introduced a single `src_root = ann / build_subdir
  if build_subdir else ann` resolved once near the top of
  _session_build_annotated_impl (with an existence check), and replaced every
  bare `ann` reference in the cmake/autotools/meson/python branches with
  `src_root`. The custom_build_cmd escape hatch now also just uses src_root
  instead of re-deriving `work` locally. Requires an MCP server restart to take
  effect (code change, not data) — until restarted, use the custom_build_cmd
  escape hatch (which already correctly honored build_subdir even before this
  fix) as an immediate workaround for any project with a nested annotated/<dir>/
  layout: pass a manual `cmake -S . -B ../../build_ann ... && cmake --build
  ../../build_ann && cmake --install ../../build_ann` as custom_build_cmd with
  build_subdir set — custom_build_cmd's cwd is src_root, so relative paths climb
  back to the workspace root correctly.
tags: [dftracer, session_build_annotated, build_subdir, cmake, mcp-tool-fix, ygm, confirmed-bug]

---
date: 2026-08-04
app: https://github.com/llnl/ygm + https://github.com/llnl/ygm-bench
context: scaled 4-node/128-rank traced run of around_the_world_ygm ran 5-10x slower than untraced, with runtime NOT scaling with -n (trip count)
error: |
  DFTRACER_ENABLE=0: -n 1000 -> 105.8s, -n 2000 -> 281.75s (roughly proportional).
  DFTRACER_ENABLE=1 (DATA_DIR=all): -n 2000 and -n 6000 BOTH still running past
  585-589s at the identical elapsed-wall-clock checkpoint (near-identical
  trajectories despite 3x different trip counts) -- runtime was NOT driven by
  workload size at all under tracing, only by elapsed time.
root_cause: |
  ygm::comm::local_progress() -- the core async-communication progress-engine
  poll function -- was annotated with DFTRACER_CPP_FUNCTION(). It is called
  from local_wait_until()'s `while (not fn()) { local_progress(); }` spin loop
  up to millions of times per barrier/wait, and itself calls
  process_receive_queue() and flush_next_send() -- ALSO both separately
  annotated. check_completed_sends(), post_new_irecv(), local_process_incoming()
  (itself an internal `while(true)` MPI_Test spin-loop), handle_completed_send(),
  and check_if_production_halt_required() are all reachable from this same hot
  path. Every poll iteration therefore fired a CASCADE of nested RAII trace
  events (timestamp capture + buffer write per DFTRACER_CPP_FUNCTION()), and at
  millions of iterations this overhead dominates wall-clock time completely,
  swamping the actual communication work being measured.
fix: |
  Stripped DFTRACER_CPP_FUNCTION() (leaving a one-line comment explaining why,
  not silently removed) from the 9 hot-loop functions in
  include/ygm/detail/comm.ipp: local_progress, local_wait_until,
  process_receive_queue, flush_next_send, check_completed_sends,
  check_if_production_halt_required, post_new_irecv, local_process_incoming,
  handle_completed_send. Kept annotation on the coarser, once-per-logical-
  operation functions applications actually call directly: async, async_bcast,
  barrier, all_reduce*, mpi_send/recv/bcast, pack_lambda*, comm_setup, welcome.
  Coverage check still holds (46 DFTRACER_CPP_FUNCTION() == 46
  DFTRACER_CPP_FUNCTION_UPDATE calls after the strip, down from 55/55).
  Verified the annotated tree still compiles/links cleanly (single MPI runtime,
  libmpi_gnu_112.so.12 only) after the strip. See CP10 in this skill's
  SKILL.md for the generalized pattern (any async/polling communication
  engine, not just YGM) and the diagnostic tell (traced runtime independent
  of workload size == hot-loop over-annotation, not a real bottleneck).
  Follow-up idea (not yet implemented): dftracer's own aggregator
  (src/dftracer/core/aggregator/{aggregator,rules}.cpp) can summarize
  repeated events into periodic time buckets (e.g. 5s granularity) instead of
  one event per call -- worth trying as a lighter-weight alternative to fully
  stripping annotation from hot functions when their behavior is itself
  diagnostically interesting.
tags: [cpp, ygm, hot-loop, polling, dftracer-overhead, over-annotation, async-communication, cp10, performance]

---
date: 2026-08-04
app: https://github.com/llnl/ygm-bench (consuming annotated https://github.com/llnl/ygm)
context: making a second, separate repo build against an already-annotated header-only library tree instead of fetching a fresh unannotated copy of it
error: |
  (not an error — a generic, reusable CMake technique worth recording)
  ygm-bench's CMakeLists.txt does find_package(ygm CONFIG) first, falling back
  to FetchContent_Declare(ygm GIT_REPOSITORY https://github.com/llnl/ygm.git...)
  + FetchContent_MakeAvailable(ygm) if that fails. The annotated YGM tree has
  no install()/export() rules, so find_package never succeeds regardless of
  CMAKE_PREFIX_PATH — it would always fall through to fetching a FRESH,
  UNANNOTATED copy of ygm from GitHub, silently defeating the whole point of
  tracing ygm-bench's usage of the instrumented library.
root_cause: |
  FetchContent_MakeAvailable(<name>) by default clones from the declared
  GIT_REPOSITORY every time, with no built-in awareness of a local annotated
  checkout sitting right next to it in the same session workspace.
fix: |
  Pass CMake's own per-dependency override cache variable at configure time:
    cmake ... -DFETCHCONTENT_SOURCE_DIR_YGM=<ws>/annotated/source ...
  (the <NAME> must match the FetchContent_Declare(<name> ...) name, uppercased
  — "ygm" -> FETCHCONTENT_SOURCE_DIR_YGM). This makes FetchContent_MakeAvailable
  add_subdirectory() the local annotated tree in place instead of git-cloning,
  with NO CMakeLists.txt patch needed in either repo. Because the annotated
  library's own CMakeLists.txt already links its dftracer_core_imported target
  into its main INTERFACE target, any downstream consumer that links that
  target (e.g. ygm-bench's own `target_link_libraries(... ygm::ygm)`)
  transitively picks up the dftracer link automatically — no separate dftracer
  wiring needed in the downstream repo's CMakeLists.txt.
  Caveat: the downstream repo's own entry points (main()s) still need their
  own explicit DFTRACER_CPP_INIT()/DFTRACER_CPP_FINI() added (mirroring the
  first repo's smoke-test entry point) — without it, every DFTRACER_CPP_FUNCTION()
  call reached via the annotated library is a silent no-op (dftracer is never
  initialized for that process).
  General pattern: this FETCHCONTENT_SOURCE_DIR_<NAME> override applies to ANY
  downstream/benchmark repo that pulls the traced library via FetchContent —
  not YGM-specific.
tags: [cmake, fetchcontent, header-only, multi-repo, ygm, ygm-bench, dftracer-init, reusable-pattern]

---
date: 2026-08-04
app: general (Tuolumne)
context: dftracer prerelease wheel (dftracer-dist module + pip install --pre) does not ship dftracer_service
error: |
  <venv-311>/bin/dftracer_service not found when trying to bracket a run per
  project policy rule 12 (node-counter daemon, one instance per node).
root_cause: |
  The prebuilt prerelease wheel (see tools-dftracer skill, "Prebuilt
  prerelease distribution") packages libdftracer_core.so, headers, and CMake
  config files, but not the dftracer_service binary — that's only produced by
  a from-source CMake build.
fix: |
  When dftracer_service is required (project policy rule 12, every job
  launch) and only the prerelease wheel is installed, install a SECOND,
  from-source dftracer into a separate session-local venv (e.g. venv-src-gnu)
  using the GNU toolchain, and use ITS dftracer_service binary — while still
  building/linking the actual traced application against whichever dftracer
  install matches its own compiler ABI (GNU toolchain build in this session,
  since Cray-clang 20 couldn't compile ygm-bench's spdlog dependency anyway;
  see [[system-tuolumne]]). Do not assume the prerelease wheel is a complete
  substitute for a from-source install if dftracer_service is needed.
tags: [tuolumne, dftracer-install, prerelease, dftracer_service, mcp-tool-gap, ygm-bench]

---
date: 2026-08-04
app: general
context: refining CP10's selective-aggregation guidance (dftracer-annotation-lessons SKILL.md) — how to spot aggregation candidates and how to size DFTRACER_TRACE_INTERVAL_MS
error: |
  (not an error — a heuristic refinement to an existing lesson)
  CP10's SELECTIVE AGGREGATION fix only listed a fixed example
  DFTRACER_TRACE_INTERVAL_MS=5000 with no guidance on choosing that value,
  and only described spotting hot-loop candidates via static caller-pattern
  grepping (while/spin-wait loops) before annotating.
root_cause: |
  N/A — gap in existing guidance, not a bug. Two things were missing:
  (1) fast-but-not-obviously-hot-loop functions are easy to miss by static
  grep alone but show up immediately in smoke-test timing; (2) a flat
  interval constant doesn't fit both a 3-minute smoke/validation run and an
  hour-long production run — too coarse an interval on a short run collapses
  the whole timeline into 1-2 buckets and destroys phase visibility.
fix: |
  1. Detection: after the smoke test, check observed per-call duration for
     annotated functions, not just static call-graph shape. Any function
     whose smoke-trace `dur` is very small (rule of thumb: dur < 1000 in the
     configured DFTRACER_TIME_METRIC unit) is a selective-aggregation
     candidate for the CP10 fix even if it wasn't caught by the
     while/spin-wait caller-pattern heuristic.
  2. DFTRACER_TRACE_INTERVAL_MS sizing: scale bucket granularity to expected
     job duration instead of using one fixed constant everywhere. Jobs under
     ~5 minutes -> ~1000ms (1s) interval, so aggregated buckets still resolve
     phase changes; longer jobs -> grow the interval roughly proportionally
     (tens of minutes -> several seconds, hour+ -> tens of seconds) so the
     aggregated event count stays bounded without collapsing a short run's
     timeline into 1-2 buckets.
  Applied directly to CP10 in SKILL.md (see that entry for the full
  DFTRACER_ENABLE_AGGREGATION / rules.yaml mechanics this refines).
tags: [dftracer-annotation, selective-aggregation, dur-threshold, trace-interval, cp10-refinement]

---
date: 2026-08-05
app: RAJAPerf (github.com/llnl/rajaperf), general (any CMake/BLT-style project with a nested include tree)
context: clang_add_braces / add_braces_c silently corrupted src/common/Executor.cpp and src/common/KernelBase.cpp with a dozen+ stray brace pairs at locations with no if/for/while at all (a constructor's member-initializer list, a switch's case label, random unrelated statement pairs) — a NEW, broader manifestation of the brace-corruption bug class already partially fixed for YGM/VPIC-Kokkos/flux-fiction (see [[bug-clang-add-braces-overlap-corruption]], [[bug-clang-add-braces-multiline-call-corruption]])
error: |
  Neither an if/for/while-overlap shape nor a multiline-call shape — this
  time the corrupted ranges don't overlap each other at all, so the existing
  overlap/subset guards in _insert_braces never triggered. The build-smoke
  agent correctly refused to hand-patch the corruption itself (per its own
  rule: "a build failure naming a specific function is an annotation bug —
  report it, don't edit source yourself") and routed it back rather than
  guessing a fix.
root_cause: |
  _add_braces_via_clang calls `clang -Xclang -ast-dump=json -fsyntax-only`
  with NO -I include paths at all. RAJAPerf's Executor.cpp/KernelBase.cpp
  include "common/RAJAPerfSuite.hpp" and other project headers nested under
  RAJA/BLT/camp submodule trees that this bare invocation can never resolve
  ("fatal error: 'common/RAJAPerfSuite.hpp' file not found", clang exit code
  1). Clang does NOT simply abort on an unresolved #include under
  -fsyntax-only -- it continues in error-recovery mode and still emits a
  "best effort" AST dump (362MB for this one file) for whatever it can still
  parse. That degraded AST reports BOGUS range.begin/range.end line numbers
  for the if/for/while nodes it does still manage to produce, and the old
  code trusted every (start,end) pair from the AST unconditionally as long
  as it didn't overlap another accepted range -- a clean, non-overlapping,
  simply-WRONG range sails right past that guard.
fix: |
  Two-part fix in source_parser.py / annotation_clang.py:
  1. _add_braces_via_clang now checks clang's exit code. Any nonzero exit
     (parse did not complete cleanly) means the AST is untrustworthy for
     line-range accuracy -- return a safe no-op ({"modified": False,
     "skipped_reason": "..."}) instead of proceeding. This converts silent
     corruption into an explicit, visible signal.
  2. add_braces_c/_add_braces_via_clang now accept an extra_include_dirs
     parameter; clang_annotate_file and clang_add_braces both pass a new
     shared helper's discovered dirs (dftracer install include/,
     annotated/<file-dir>, annotated/src) -- a best-effort improvement for
     simpler projects, NOT a claim of completeness for deep submodule trees
     like RAJA/BLT (a proper fix for those would read a CMake-generated
     compile_commands.json, not implemented yet -- flag as a future
     improvement if this recurs on another BLT/CMake-submodule-heavy app).
  3. clang_annotate_file's response now surfaces `braces_skipped_reason` so
     the calling agent SEES that brace insertion was skipped and can
     manually verify (via grep/read) whether the file has any real
     braceless if/for/while bodies needing correct END-macro placement,
     rather than silently assuming "no insertions" meant "nothing needed
     wrapping".
  4. For THIS session, the two already-corrupted files were fixed by
     reconstructing them from source/ with ONLY the legitimate dftracer
     insertions re-applied (the #include and the 4 correct
     DFTRACER_CPP_FUNCTION()/UPDATE pairs at Executor::runSuite/runKernel
     and KernelBase::execute/runKernel, all previously verified correct by
     the build-smoke agent) -- not a patch-over of the corrupted file.
  General takeaway: on ANY project where clang_syntax_check ALSO reports
  missing-header errors for a file (check this first), clang_add_braces is
  operating on a degraded AST for that same file -- after this fix it will
  safely skip rather than corrupt, but any real braceless control-flow body
  in that file needs manual verification.
tags: [clang-add-braces, ast-corruption, degraded-parse, missing-headers, rajaperf, blt, cmake, reusable-pattern, tool-level-fix]

---
date: 2026-08-05
app: RAJAPerf (github.com/llnl/rajaperf), general (any C++ main() annotated via the REGION_START/END path)
context: linking raja-perf.exe failed with "pasting formed 'profiler_\"main\"', an invalid preprocessing token" -- a bug in the CP2/CP3 "C++ main() must use REGION_START/END, not RAII FUNCTION()" fix recorded earlier THIS SAME SESSION (see the entry above this one's sibling fix in clang_annotate_file) -- the fix itself carried a defect that was never actually compiled/verified against the real dftracer.h before being applied
error: |
  DFTRACER_CPP_REGION_START("main")/DFTRACER_CPP_REGION_END("main") -- WITH
  QUOTES -- fails to compile: dftracer.h defines these as
  `profiler_##name` token-pastes (`#define DFTRACER_CPP_REGION_START(name) \
  DFTracer* profiler_##name = new DFTracer(#name, ...)`). `##` requires a
  bare preprocessor token, not a string literal -- pasting `profiler_` with
  the literal characters of `"main"` (quotes included) produces the invalid
  token `profiler_"main"`, a hard compile error caught only at the FINAL
  LINK stage of a 600+ file RAJAPerf build (~9 minutes in), not at annotation
  or lint time -- clang_lint_annotations has no rule checking macro-argument
  well-formedness against the real header, only ordering/placement.
  Separately, the paired UPDATE call used DFTRACER_CPP_FUNCTION_UPDATE(...),
  which expands to `profiler_dft_fn.update(...)` -- but `profiler_dft_fn` is
  only ever declared by DFTRACER_CPP_FUNCTION() (the RAII macro), which
  main() deliberately does NOT use. The pointer-based REGION_START instead
  declares `profiler_main` (a DFTracer*), which needs the arrow-deref
  DYN_UPDATE variant: DFTRACER_CPP_REGION_DYN_UPDATE(main, "comp", "cpu")
  (expands to `profiler_main->update(...)`).
root_cause: |
  The earlier fix (this session, same date) that corrected clang_annotate_file
  to use REGION_START/END instead of RAII FUNCTION() for a C++ main() copied
  the exact quoted-string pattern from the ANNOTATOR AGENT's own manual hand-fix
  report ("DFTRACER_CPP_REGION_START(\"main\")...") without cross-checking it
  against dftracer.h's actual macro definition -- the annotator's hand-fix was
  ALSO wrong, just never exercised through a real compile before this fix
  copied the same mistake into the tool. Neither the annotator's manual fix
  nor the tool fix that followed it was verified with an actual compiler
  invocation against the real header at the time.
fix: |
  1. dftracer.h ground truth (read directly, not assumed):
     DFTRACER_CPP_REGION_START(name)/_END(name) take a BARE IDENTIFIER (no
     quotes) -- the trace event name is derived internally via #name
     stringization, so DFTRACER_CPP_REGION_START(main) is already correct
     and sufficient; do not add manual quotes.
     DFTRACER_CPP_REGION_DYN_UPDATE(name, key, val) -- not
     DFTRACER_CPP_FUNCTION_UPDATE(key, val) -- is the correct UPDATE call
     paired with REGION_START/END (arrow-deref on the profiler_##name
     pointer REGION_START declares, vs. FUNCTION_UPDATE's hardcoded
     profiler_dft_fn which only DFTRACER_CPP_FUNCTION() ever declares).
  2. Fixed in annotation_clang.py's clang_annotate_file: `fn_start`/`fn_end`
     for a C++ main() are now `DFTRACER_CPP_REGION_START(main);` /
     `DFTRACER_CPP_REGION_END(main);` (bare identifier). `_make_update()`
     gained a `region_name` parameter -- when set, it emits
     `DFTRACER_CPP_REGION_DYN_UPDATE({region_name}, "comp", "{comp}");`
     instead of the FUNCTION_UPDATE form; both call sites for is_main_fn
     pass `region_name="main"`.
  3. Verified with an ACTUAL clang -fsyntax-only compile against the real
     installed dftracer.h (not just visual inspection) before considering
     this fixed -- exit 0. Re-ran the tool end-to-end on a fresh synthetic
     MPI main() to confirm the generated output is the corrected form.
  4. RAJAPerfSuiteDriver.cpp (this session's actual file, corrupted before
     this fix existed) was hand-corrected to match: 3 lines changed
     (REGION_START/END unquoted, UPDATE call switched to
     DFTRACER_CPP_REGION_DYN_UPDATE).
  General takeaway: a macro-usage fix for a C/C++ annotation tool is NOT
  verified until it has actually been compiled against the real vendor
  header -- copying a pattern from an agent's own prose report (even one
  that "looked plausible" and passed clang_lint_annotations, which only
  checks ordering/placement, not argument well-formedness against the real
  macro definitions) is not verification. This exact class of error is
  invisible until final link of a large multi-file build, far downstream of
  where it was introduced.
tags: [clang-annotate-file, dftracer-cpp-region, token-pasting, macro-misuse, main-entry-point, self-correction, rajaperf, tool-level-fix]

---
date: 2026-08-05
app: RAJAPerf, general
context: reconfirmation of the mcp__dftracer__analyze dask-teardown-hang bug (see 2026-07-06/2026-07-08 entries above) on a MUCH smaller trace than previously seen
error: |
  analyze() timed out at the existing 300s ceiling TWICE (generic preset,
  with and without a checkpoint) on an 8.6MB / ~47,588-event compact trace
  -- previous entries assumed/implied this was more of a large-trace
  problem. No partial stdout was captured either time (the "timeout after
  output is success" handling from the 2026-07-06 fix did not help here --
  either analysis itself hung before producing output on this trace shape,
  or the captured-output path still isn't triggering). Not re-investigated
  further this session (time-boxed); the analyzer fell back to a manual
  gzip+json aggregation per this project's documented workaround.
fix: |
  No new fix applied. Flagging that trace SIZE alone does not predict
  whether this hang reproduces -- don't assume a small trace is safe from
  it. If this recurs, the next investigation should check whether analyze()
  is hanging during the ANALYSIS phase itself (not just dask teardown) for
  this trace shape/preset combination, since zero stdout was captured this
  time vs. the original bug report's "output already flushed, only teardown
  hangs" — that's a materially different symptom and may need a different
  fix in dfanalyzer_service.py than the existing timeout-after-output
  handling.
tags: [dfanalyzer, dask, hang, timeout, mcp-tool-gap, rajaperf, reconfirmation, needs-followup]
