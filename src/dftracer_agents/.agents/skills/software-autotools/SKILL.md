---
name: software-autotools
description: Autotools (autoconf/automake/libtool) build system detection, bootstrap/configure/build/install flow via session_configure/session_build_install/session_build_annotated, PKG_CONFIG_PATH/CPPFLAGS/LDFLAGS dftracer injection, and per-system/per-dependency lesson tagging. Load this skill for any project with configure.ac/Makefile.am or a checked-in configure script.
metadata:
  type: software
---

## Detection

- `configure.ac` + `Makefile.am` at the source root (not yet bootstrapped) —
  needs `autoreconf -fi` before `./configure` exists.
- A checked-in `configure` script with no `configure.ac` — already
  bootstrapped (common for release tarballs); do not run `autoreconf`.
- `AC_ARG_WITH`/`AC_ARG_ENABLE` macros in `configure.ac` name the project's
  own `--with-*`/`--enable-*` flags — read these before calling
  `session_configure` with `extra_configure_flags`, since an unrecognized
  flag usually errors loudly (unlike CMake/Meson's silent-ignore behavior).

## Configure / build / install flow (via MCP tools)

```
session_configure(run_id, extra_configure_flags="--with-mpi --disable-shared ...")
session_build_install(run_id, jobs=<n>)
```

or for the annotated tree:

```
session_build_annotated(run_id)
```

- `session_configure` bootstraps with `autoreconf -fi` only if `configure.ac`
  exists and `configure` does not, then runs
  `./configure --prefix=<install> --disable-dependency-tracking <extra_configure_flags>`.
  `--disable-dependency-tracking` is always added — it avoids a common
  `config.status` failure from stale `.deps/` directories.
- Both `session_configure` and `session_build_annotated` proactively delete
  any `.deps/` directories found under `source/`/`annotated/` and `build/`/
  `build_ann/` before configuring — a stale `.deps/` from a previous
  configure with different flags is a common autotools failure mode.
- `session_build_annotated` injects dftracer discovery via environment
  variables (not `--with-dftracer=<prefix>`, since that flag doesn't exist
  generically across projects): `PKG_CONFIG_PATH=<prefix>/lib/pkgconfig`,
  `CPPFLAGS=-I<prefix>/include`, `LDFLAGS=-L<prefix>/lib -Wl,-rpath,<prefix>/lib`.
  This only works if the project's own `configure.ac` actually probes
  `PKG_CONFIG_PATH`/`CPPFLAGS`/`LDFLAGS` (via `PKG_CHECK_MODULES` or plain
  `AC_CHECK_HEADER`/`AC_CHECK_LIB`) — if it hard-codes include paths instead,
  you'll need a project-specific patch (record that in the project's own
  `workload-<app>`/`software-<app>` skill, not here).

## Lessons are per-system and per-dependency, not universal

Autotools itself is portable, but which dependency-detection macro a
project's `configure.ac` uses (`PKG_CHECK_MODULES` vs `AC_CHECK_LIB` vs a
hand-rolled `AC_ARG_WITH` prefix flag), and which system paths get searched,
varies by system and dependency. Tag every lesson with the
`(system, dependency)` pair observed; cross-link the system-specific half
into `system-<system>` when it's about the site, not autotools itself.

## Lessons

(none recorded yet for this repo's sessions beyond what's captured in
per-project `workload-<app>` skills. Record a NEW cross-project autotools
lesson here; keep a single project's `configure.ac` quirks in that project's
own skill.)
