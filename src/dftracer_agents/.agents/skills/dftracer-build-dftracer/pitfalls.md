## Pitfalls

- Tool errors often indicate a missing assumption in the install path.
- Skipping the tool and doing a manual install defeats the point of the agent.
- Not recording install breakage repeats the same failure later.
- **A from-scratch cmake-based dftracer build ALWAYS needs two configure passes
  (MANDATORY, not a one-off bootstrap quirk).** Pass 1:
  `-DDFTRACER_INSTALL_DEPENDENCIES=ON`, then `cmake --build` — this ONLY
  downloads/builds dftracer's vendored deps (cpp-logger, brahma, gotcha,
  libuv). It prints "downloading dependencies. Please run make for
  downloading dependencies and then do reconfigure without dependency flag"
  and produces ZERO dftracer_core/dftracer_service build output — `cmake
  --build` exits 0 almost instantly with no compiler invocations, which is
  easy to mistake for "nothing needed rebuilding" or a silent success. Pass
  2: reconfigure the SAME build dir with `-DDFTRACER_INSTALL_DEPENDENCIES=OFF`,
  then `cmake --build` + `cmake --install` again — only NOW do the real
  dftracer_core/dftracer_service targets get generated and compiled. Skipping
  straight to a single configure+build silently ships an install with stale
  (or missing) dftracer_core/_service binaries while reporting success. `[[bug-dftracer-crayclang-python-abi]]`.
- **On Cray PE with Cray clang, `-static-libstdc++` is silently a no-op**
  (`clang++: warning: argument unused during compilation`) — it does NOT
  statically link libstdc++ despite being accepted without error. If a
  Cray-clang-built `libdftracer_core.so`/`dftracer_service` needs to run
  without depending on this system's (possibly too-old) dynamic
  `libstdc++.so.6`, the flag that actually works is
  `-nostdlib++ <path-to-libstdc++.a> -lm` passed via
  `CMAKE_SHARED_LINKER_FLAGS`/`CMAKE_EXE_LINKER_FLAGS` (verify the static
  archive path with a GCC toolset module, e.g.
  `/opt/rh/gcc-toolset-13/root/usr/lib/gcc/x86_64-redhat-linux/13/libstdc++.a`
  on Tuolumne) — confirmed by direct testing: `-static-libstdc++` left
  `libstdc++.so.6` as a dynamic dependency every time, while `-nostdlib++`
  plus the explicit `.a` eliminated the versioned-symbol requirement and the
  resulting binary ran correctly.
