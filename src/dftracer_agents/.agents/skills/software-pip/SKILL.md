---
name: software-pip
description: pip / PEP 517-518 build-backend detection (setuptools, mesonpy, hatchling, ...), editable vs non-editable install choice, and venv/module-load ordering on HPC systems. Load this skill for any project installed via `pip install` off a pyproject.toml.
metadata:
  type: software
---

## Detection

- Top-level `pyproject.toml` with a `[build-system]` table — read
  `build-backend` before doing anything:
  - `build-backend = "mesonpy"` → the real build system is Meson underneath;
    pip/meson-python drives `meson setup`/`compile`/`install` internally.
    Load [[software-meson]] too, since project-specific meson `-D` options
    are passed through pip via
    `pip install . --config-settings=setup-args="-Doption=value"`.
  - `setuptools.build_meta` → classic `setup.py`/`setup.cfg` semantics apply.
  - `hatchling.build` → Hatch-managed, usually no compiled extension.
- `[project.scripts]` entries tell you the CLI names that will land on PATH
  after install — use these (not a guess) when writing a smoke-test command.
- `[project.optional-dependencies]` groups (e.g. `dev`, `otel`, `plot`) are
  extras — install with `pip install '.[dev]'` when the extra's contents
  (test deps, optional instrumentation) are actually needed for the pipeline
  stage at hand; don't blanket-install every extra by default.

## Configure / build flow

```
pip install --no-build-isolation -e .        # editable, reuses env's own meson/ninja
# or, if the backend forbids/mishandles editable with a compiled extension:
pip install --no-build-isolation .
```

- `--no-build-isolation` is usually necessary in HPC session venvs: build
  isolation creates an ephemeral env that won't see already-loaded
  site-specific toolchain state (compiler wrappers, module-provided
  pkg-config paths), so the meson/cmake configure step underneath can fail to
  find system dependencies it would find if driven from the active shell.
  Install the `[build-system].requires` packages (meson, meson-python, etc.)
  into the venv yourself first when using `--no-build-isolation`.
- Compiled-extension backends need `CC`/`CXX` and `LD_LIBRARY_PATH` set
  BEFORE `pip install` runs, same ordering requirement as a raw
  configure/cmake build — the pip subprocess does not re-source anything.

## Lessons are per-system and per-dependency, not universal

Which extras are needed, whether `--no-build-isolation` is required, and
whether a compiled extension needs extra linker flags all depend on the
specific system's module/toolchain setup and the specific project's native
dependencies — tag lessons with the `(system, dependency)` pair observed, and
cross-link the system-specific half into `system-<system>` rather than
generalizing "pip always needs X" from one project.

## Lessons

- **(Tuolumne, flux-core) flux-fiction 2026-07-16:** `pyproject.toml` used
  `build-backend = "mesonpy"` with a `use_system_flux` meson option — when
  installing via pip rather than `meson setup` directly, that option must be
  forwarded as
  `pip install --no-build-isolation --config-settings=setup-args="-Duse_system_flux=true" .`
  (or via `flux_prefix` similarly) since plain `pip install .` has no way to
  pass project-specific meson `-D` flags otherwise.
