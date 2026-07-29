---
name: feedback-pydftracer-api-import-path
description: The pydftracer Python API is `dftracer.python`, not `dftracer.logger` — the wrong path wastes a debug cycle
metadata:
  type: feedback
---

In pydftracer 2.x the annotation API lives at `dftracer.python`:

    from dftracer.python import dftracer, dft_fn
    log = dftracer.initialize_log(logfile=<prefix>, data_dir=<dir>, process_id=-1)

    @dft_fn("cat").log
    def f(): ...

    log.finalize()

`from dftracer.logger import ...` raises `ModuleNotFoundError: No module named
'dftracer.logger'`. Submodules actually present under the `dftracer` package are:
`_version`, `dftracer` (the compiled C extension), `dftracer_dbg`, `python`, `utils`.

`dftracer.python` also exports the AI/ML region helpers used by ML annotation:
`Compute`, `Data`, `DataLoader`, `Checkpoint`, `Communication`, `Device`, `Pipeline`,
`IO`, `Other`, plus `DFTracerAI` / `ai_init`.

**Why:** the wrong import path throws a plain ModuleNotFoundError that reads like a
broken install, sending you to reinstall dftracer instead of fixing one line.

**How to apply:** use `dftracer.python`. To check what a given install exposes:
`python -c "import dftracer,pkgutil,os; print([m.name for m in pkgutil.iter_modules([os.path.dirname(dftracer.__file__)])])"`

Note `dftracer.dftracer` (the C extension) importing cleanly is a separate and
mandatory check — see [[bug-dftracer-cray-runtime-silent-noop]].
