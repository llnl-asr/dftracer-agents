---
name: bug-dftracer-cray-runtime-silent-noop
description: dftracer built with Cray CCE silently degrades to NoOpProfiler (zero .pfw, no error) in any venv that doesn't load the Cray PE modules — check `import dftracer.dftracer`
metadata:
  type: feedback
---

On a Cray PE system, `session_install_dftracer` builds the C core and the Python C
extension with the Cray compiler (cce). Those objects link the CCE runtime:
`libmodules.so.1`, `libfi.so.1`, `libcraymath.so.1`, `libf.so.1`, `libu.so.1`,
`libcsup.so.1`.

A Python/AI app venv typically loads only `python/<ver>` and `rocm/<ver>` — NOT the
Cray PE modules — so those `.so.1` files are not on the loader path at run time.

**The failure is SILENT.** `import dftracer` succeeds, `from dftracer.python import
dftracer, dft_fn` succeeds, decorators apply, `initialize_log()` and `finalize()` both
return without error — and **zero `.pfw` files are written**. The Python layer catches
the C-extension ImportError and falls back to `NoOpProfiler`. `dft.log` is created but
stays 0 bytes. It is very easy to misdiagnose this as an MPI problem, a
DFTRACER_ENABLE/DFTRACER_INIT problem, or a DATA_DIR problem. It is none of those.

**Why:** the silent NoOpProfiler fallback converts a hard link error into a no-op, so
every surface-level check passes and only the missing trace file reveals it.

**How to apply:**
1. The real diagnostic — run this, never just `import dftracer`:
   `python -c "import dftracer.dftracer"`
   If it raises `ImportError: libmodules.so.1: cannot open shared object file`, this is
   the bug. Confirm with:
   `ldd <site-packages>/dftracer/dftracer.cpython-*-linux-gnu.so | grep "not found"`
2. The fix — prepend the CCE runtime dirs to `LD_LIBRARY_PATH` in the app's env script
   (the same script the run scripts source), NOT just in an interactive shell:
   `export LD_LIBRARY_PATH="$CCE_ROOT/cce/x86_64/lib:$CCE_ROOT/cce/x86_64/lib/default64:$CCE_ROOT/cce-clang/x86_64/lib:/usr/lib64:$LD_LIBRARY_PATH"`
   where `$CCE_ROOT` is the cce module's install root. `module load cce/<ver>` and
   reading `CRAY_LD_LIBRARY_PATH` gives the authoritative list.
3. **Acceptance gate for every dftracer install step** — `import dftracer` is NOT a
   sufficient check. The step is only done when a minimal FUNCTION-mode script has
   produced a **non-empty `.pfw`/`.pfw.gz` containing `"ph"` events**, verified with
   `ls -l` + `zcat | grep -c '"ph"'`. Report that output, don't assert success.

Related: [[feedback-cray-ld-library-path-fortran-runtime]] (same root cause class for
Cray-MPI-linked Python extensions), [[bug-dftracer-crayclang-python-abi]],
[[feedback-dftracer-aiml-venv]], [[feedback-hpc-python-env]].
