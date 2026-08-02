---
name: bug-dftracer-crayclang-rpath-shadows-libstdcxx
description: Cray-clang-built dftracer stamps a DT_RPATH listing /usr/lib64 BEFORE the python module's lib, so `import dftracer.dftracer` always dies with CXXABI_1.3.13 not found — patchelf the rpath, LD_LIBRARY_PATH cannot fix it
metadata:
  type: feedback
---

**Canonical home:** see the `dftracer-build-dftracer` skill (`pitfalls.md`) alongside
`bug-dftracer-crayclang-python-abi` — this file supersedes that one's "non-blocking
for pure C++ apps" framing for Python/PyTorch workloads.

On a Cray PE + toss4 site, a dftracer built with Cray clang (`cce/*`) fails at
import time for ANY Python workload:

```
ImportError: /usr/lib64/libstdc++.so.6: version `CXXABI_1.3.13' not found
  (required by .../dftracer/dftracer.cpython-3XX-x86_64-linux-gnu.so)
```

**Why:** two compounding facts.
1. `/usr/lib64/libstdc++.so.6` is old and provides neither `CXXABI_1.3.13` nor
   `GLIBCXX_3.4.29`. The site `python/3.X` module ships its own
   `libstdc++.so.6.0.29` (GCC 11.2) in its `lib/` dir — that is the only
   reachable libstdc++ that satisfies the build. (The site python's own
   `libyaml-cpp.so` needs it too, so this is not dftracer-specific.)
2. The Cray clang build stamps **DT_RPATH**, not DT_RUNPATH, and that rpath
   lists `/usr/lib64` BEFORE the python module's lib dir. DT_RPATH outranks
   `LD_LIBRARY_PATH`, so prepending the good directory to `LD_LIBRARY_PATH`
   changes nothing — which is the confusing part, and burns a debug cycle.

This SUPERSEDES the older "non-blocking for pure C++ apps" framing in
[[bug-dftracer-crayclang-python-abi]]: for a Python/PyTorch workload it is a
hard blocker, because the Python annotation API imports the C extension.

**How to apply:** after every dftracer (re)install with Cray clang, patch the
rpath of every shipped object AND the `dftracer_service` binary so the good
libstdc++ dir comes first:

```bash
GOOD=/usr/tce/packages/python/python-3.X.Y/lib
for so in $(find <venv>/.../site-packages/dftracer \( -name '*.so' -o -name '*.so.*' \) -type f) $(which dftracer_service); do
  cur=$(patchelf --print-rpath "$so") || continue
  patchelf --force-rpath --set-rpath "$GOOD:$cur" "$so"
done
```

Verify with `python -c "import dftracer.dftracer"` — and remember
[[bug-dftracer-cray-runtime-silent-noop]]: an import that *succeeds* can still
be a NoOpProfiler, so also check that a trace is actually produced.
