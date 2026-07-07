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
  13KB avg transfer, running on NFS (/usr/WS2). Two concrete, safe
  optimizations were available:
    L2: mProject.c/montageAdd.c call fits_read_pix()/fits_open_file() but
        never hint the kernel about the row-by-row sequential access
        pattern that follows.
    L3: the whole session had been running on NFS (/usr/WS2), not Lustre,
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

