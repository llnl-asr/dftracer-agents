---
name: software-meson
description: Meson build system detection, configure/build/install flow, dependency() resolution (pkg-config vs workspace/prefix fallbacks), and meson-python packaging. Load this skill for any project whose top level is meson.build / meson.options.
metadata:
  type: software
---

## Detection

- Top-level `meson.build` with `project(<name>, '<lang>', ...)` — grep for
  `meson_version:` to confirm the minimum Meson version required.
- `meson.options` (or legacy `meson_options.txt`) holds project-specific
  `option()` declarations — always read this BEFORE calling
  `session_configure`, since the exact option names (e.g. `use_system_flux`,
  `flux_prefix`) are project-specific and configure will silently ignore an
  unrecognized `-D` flag name typo rather than erroring loudly in all cases.
- `pyproject.toml` with `build-backend = "mesonpy"` and
  `requires = ["meson-python>=...", "meson>=..."]` means the project is
  installed via `pip install .` (or `python -m build`), NOT `meson setup` +
  `ninja install` directly — pip/meson-python drives the meson configure+
  compile+install internally. Prefer `pip install -e .` (or non-editable if
  editable isn't supported) over hand-rolling `meson setup builddir`.

## Configure / build flow (when driving meson directly, no pyproject wrapper)

```
meson setup <builddir> -Doption_name=value ...
meson compile -C <builddir>
meson install -C <builddir>
```

- Re-running `meson setup` on an existing builddir with new `-D` options
  reconfigures in place; you do not need to `rm -rf` the builddir first.
- `dependency('<name>', required: true)` resolution order is typically:
  pkg-config → CMake config files → meson subproject wrap fallback. If a
  dependency is missing and there's no wrap fallback, configure fails at
  that `dependency()` call with a clear "Dependency X found: NO" line — read
  the exact line, don't guess which dependency broke.
- A project may deliberately support several ways to locate an external
  library it depends on (system pkg-config, an explicit `_prefix` option, a
  workspace-relative sibling checkout, or a container-installs directory) by
  building a list of "candidates" in its own `meson.build` and searching them
  in order — check for a system-installed version via `pkg-config --exists
  <name>` FIRST before assuming a sibling checkout or container build is
  required just because a README recommends it.

## Lessons are per-system and per-dependency, not universal

Meson itself is portable, but WHICH `dependency()` resolves via pkg-config,
which needs a `_prefix`/`use_system_*` option, and which needs a sibling
checkout or module load varies by system (Tuolumne Cray PE vs a generic
Linux box vs a container) and by which software the project depends on
(flux-core, HDF5, jansson, MPI, ...). Tag every lesson below with the
`(system, dependency)` pair it was observed on — don't generalize a
system-specific pkg-config/module finding into "meson always does X".
Cross-link the system-specific side of the lesson into the relevant
`system-<system>` skill too when it's about the site, not about meson.

## Lessons

- **(Tuolumne, flux-core) flux-fiction 2026-07-16: `use_system_flux` boolean
  option lets a meson-based C+Python project skip building its own C
  dependency (flux-core) from a sibling checkout entirely when the dependency
  is already present on the system.** Symptom: README recommends a podman
  container with sibling `flux-core`/`flux-sched` checkouts. Root cause:
  that's only needed when system flux-core isn't installed. Fix: run
  `pkg-config --exists flux-core` first — on Tuolumne this succeeded (flux
  0.86.0 is part of the site's standard environment), so
  `-Duse_system_flux=true` (often the default) avoided the whole
  container/sibling-checkout path. On a system WITHOUT a site-wide flux
  install, expect to need `flux_prefix` or the sibling-checkout candidate
  instead — re-check `pkg-config --exists` per system rather than assuming
  this result transfers. `flux-sched` was NOT required at all for this
  project's C jobtap plugin on Tuolumne — don't build an optional dependency
  just because a sibling directory convention exists for it in the
  `meson.build` candidate list.
- **(Tuolumne, Cray clang) flux-fiction 2026-07-16: a meson project built with
  `c_std=c99`/`warning_level=2` default_options can fail to compile with
  `strdup()`/other POSIX-but-not-C99 functions "implicit declaration"
  under Cray clang, even though the same source compiles fine on GCC/regular
  clang.** Root cause: Cray clang's strict C99 mode doesn't pull in POSIX
  declarations, and the traditional `_POSIX_C_SOURCE` feature-test macro did
  NOT fix it here (retried and still failed — see
  `03_meson_compile_posix.log` in the flux-fiction session artifacts). Fix:
  add `-D_GNU_SOURCE` to the project's meson `c_args` (e.g. in `src/meson.build`
  via `add_project_arguments('-D_GNU_SOURCE', language: 'c')`), which is
  honored more reliably across compilers than `_POSIX_C_SOURCE` even in
  strict `c_std=c99` mode. Check for this whenever a meson C target on
  Tuolumne/Cray clang reports an implicit-declaration error for a libc/POSIX
  function that isn't strict C99.

- **(session tooling) 2026-07-16: native meson support landed in
  `session_configure`/`session_build_install`/`session_build_annotated`
  (branches added in `session_tools.py`).** Prior to this date those tools
  raised `"Unsupported build tool: meson"` and required a hand-rolled
  `custom_build_cmd` (`meson setup <build_dir> <source_dir> -D... && ninja -C
  <build_dir> && ninja -C <build_dir> install`). A fresh session should not
  need `custom_build_cmd` for the plain configure/build/install flow anymore
  — check the tool's error message before assuming the workaround is still
  needed. Note: linking against a dependency that has no `.pc`/pkg-config
  file (only a CMake config, or nothing at all) is a per-project meson.build
  gap, not something the generic session tooling can fix — see
  `software-flux-fiction` for the dftracer-specific case.
