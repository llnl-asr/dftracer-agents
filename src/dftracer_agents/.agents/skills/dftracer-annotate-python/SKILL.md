---
name: dftracer-annotate-python
description: Python annotation rules for dftracer — decorator usage, initialize/finalize, comp types, class methods, and quick checklist
---

## Python Annotation Rules (dftracer)

**CORRECTED 2026-07-16: this skill previously documented a `dftracer.logger`/
`dftracer_fn`/`DFTracer.initialize_log` API that does NOT exist on the current
(`develop`-branch / `pydftracer` 2.0.3) dftracer — that wrong API is exactly
what caused a real production bug on the flux-fiction session (24 files
annotated against this skill's old text failed at import with
`ModuleNotFoundError: No module named 'dftracer.logger'`). The API below is
the one `python_annotate_file`/`python_annotate_project` actually generate and
is confirmed importable (`from dftracer.python import dftracer, dft_fn`).
Always prefer the MCP tools over hand-writing this pattern — this skill
documents what they produce so you recognize correct vs stale output.**

### Python Rule 1 — Use the decorator for regular functions

```python
from dftracer.python import dftracer, dft_fn as DFTracerFn

_dft = DFTracerFn("<category>")   # one instance per file, category = module name

@_dft.log
def my_read(path: str, size: int) -> bytes:
    ...
```

- `@_dft.log` wraps the function with START/END automatically.
- `<category>` groups functions in the trace viewer — defaults to the file's
  module stem (e.g. `"train"` for `train.py`) if you don't have a better name.
- Apply at every function that qualifies under **Rule 0** (see
  `dftracer-annotate-general` skill).

### Python Rule 2 — Initialize and finalize the tracer (entry-point files only)

```python
from dftracer.python import dftracer, dft_fn as DFTracerFn

_dft = DFTracerFn("<category>")
_dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)

# ... entry-point logic, e.g. inside main() ...

_dft_log.finalize()
```

- Only the file containing `main()` (or an `if __name__ == "__main__":` guard)
  gets the `initialize_log`/`finalize()` pair — everything else just gets
  `_dft = DFTracerFn(...)` plus `@_dft.log` decorators.
- `finalize()` must be called before every `return` in `main()` (or at the end
  of the script if there's no explicit return) — a missing finalize truncates
  the trace.
- For MPI apps: initialize AFTER `MPI.Init()`/`MPI.COMM_WORLD` setup, finalize
  BEFORE `MPI.Finalize()`.

### Python Rule 2b — `torch.utils.data.DataLoader`/multiprocessing workers need their OWN init/finalize (MANDATORY whenever `num_workers > 0`)

**Rule 2's init/finalize pair is per-PROCESS, not per-app.** Any DL training script
that uses a `DataLoader` (or `DataListLoader`, or raw `multiprocessing.Process`) with
`num_workers > 0` spawns SEPARATE OS processes that run their own copy of the
annotated dataset code (e.g. `__getitem__`) — each of those worker processes is a
DIFFERENT process from the one that called `initialize_log()`/`finalize()` in Rule 2,
and therefore has ITS OWN independent dftracer C-core state that nothing finalizes
unless you explicitly say so. Symptom if this rule is skipped: worker-process trace
files are literal 0-byte files (dead/empty, no event ever flushed) or silently
missing their tail events — confirmed on a real PECAN session (2026-07-22): 32 of 80
trace files were exactly 0 bytes, identically across every run of the same config,
until this fix was applied. This is NOT a crash, NOT an exception, and easy to miss —
the training run itself completes normally and prints a normal loss curve while
silently losing (or truncating) a large fraction of its own I/O/compute trace data.

**Fix — wire a `worker_init_fn` that finalizes each worker on exit:**

```python
def _dftracer_worker_init(worker_id):
    if os.getenv("DFTRACER_ENABLE") == "1":
        try:
            import atexit
            logger = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)
            atexit.register(lambda: logger.finalize())
        except Exception:
            pass

train_dataloader = DataLoader(dataset=train_dataset, num_workers=num_workers,
                               worker_init_fn=_dftracer_worker_init, ...)
```

Apply this to EVERY `DataLoader`/`DataListLoader` construction in the app that has
`num_workers > 0` (train, val, test, feature-extraction — all of them, not just the
one that happens to run in the smoke test). This holds regardless of
`persistent_workers` and regardless of `multiprocessing_context` (`fork` or
`spawn`) — under `fork` a worker at least inherits the parent's ALREADY-initialized
logger state (so this symptom is less likely, but still not guaranteed-safe since
the parent's `finalize()` only runs in the parent), but under `spawn` the child
re-imports everything fresh and NEVER sees the parent's init/finalize calls at all —
`spawn` is the exact configuration where this WILL bite if `worker_init_fn` doesn't
also finalize.

**MANDATORY pickling constraint (a second real bug, hit while fixing the first):**
if `multiprocessing_context="spawn"` (or the app's default multiprocessing start
method is spawn), `worker_init_fn` gets PICKLED to hand to the child process. A
closure returned by a factory function (`def _make_x(inner): def _worker(wid): ...;
return _worker`) is NOT picklable and crashes every rank with `AttributeError: Can't
pickle local object '...'` the instant the DataLoader starts. **`worker_init_fn`
must be a plain top-level function, never a closure/nested function.** If you need
to chain an existing per-worker init the app already has (e.g. a custom staging/
prefetch `worker_init`), look it up dynamically INSIDE the function body via
`torch.utils.data.get_worker_info().dataset` + `getattr(...)`, not by capturing it
in an enclosing scope:

```python
def _dftracer_worker_init(worker_id):
    info = torch.utils.data.get_worker_info()
    inner = getattr(info.dataset, "worker_init", None) if info is not None else None
    if inner is not None:
        inner(worker_id)
    if os.getenv("DFTRACER_ENABLE") == "1":
        try:
            import atexit
            logger = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)
            atexit.register(lambda: logger.finalize())
        except Exception:
            pass
```

**When annotating (or reviewing an existing annotation of) any Python DL training
script:** grep the app for every `DataLoader(`/`DataListLoader(`/
`multiprocessing.Process(`/`Pool(` construction. If ANY of them run with more than
one worker/process AND the app is `DFTRACER_ENABLE`-gated, this rule applies — do
not treat Rule 2's single init/finalize pair in the entry-point file as sufficient
just because the trace "looks like it's working" (some worker files may still get
real, if incomplete, data by chance — the absence of a crash is not evidence of
complete data).

### Python Rule 3 — `@property`/`@cached_property`/`@x.setter` NEVER get a stacked decorator (MANDATORY)

```python
# ❌ WRONG — breaks the property protocol entirely
@_dft.log
@property
def jobspec(self):
    ...

# ✅ CORRECT — contextual region inside the body instead
@property
def jobspec(self):
    with DFTracerFn("<category>", name="jobspec"):
        ...
```

**Why this matters (real incident, 2026-07-16):** stacking `@_dft.log` on top
of `@property` replaces the property descriptor with the decorator's wrapper
object. `obj.jobspec` then returns a bound method object instead of invoking
the getter — on flux-fiction this produced
`TypeError: Object of type method is not JSON serializable` deep inside a
job-submission path, because code elsewhere assumed `job.jobspec` was already
the computed value. `python_annotate_file`/`python_annotate_project` handle
this automatically (same contextual-`with`-region treatment as
`@staticmethod`) — never hand-write a decorator stack that puts anything above
`@property`/`@x.setter`/`@x.deleter`/`@x.getter`/`@cached_property`.

### Python Rule 4 — Context manager for ad-hoc regions

```python
from dftracer.python import dftracer, dft_fn as DFTracerFn

_dft = DFTracerFn("<category>")
with DFTracerFn("<category>", name="read_loop"):
    for chunk in data:
        process(chunk)
```

Only use this when a code block is too coarse-grained to fit a function
decorator (same rule as `@staticmethod`/`@property` bodies above).

### Python Rule 5 — Category classification

`DFTracerFn("<category>")` takes a category string, not a `comp=` keyword —
category is the trace-viewer grouping (module name is a reasonable default).
There is no separate `comp="io"/"comm"/"cpu"/"mem"` argument on the Python
side the way there is for the C macros — the category string IS the
classification. Pick a category that reflects what the file/function does
(`"io"`, `"comm"`, `"compute"`, `"lifecycle"`, etc.) rather than leaving every
file with a generic name.

### Python Rule 6 — Skip trivial functions (Rule 0 applies)

```python
# ✅ Annotate — does real file I/O
@_dft.log
def read_checkpoint(path: str, rank: int) -> dict:
    with open(path, "rb") as f:
        return pickle.load(f)

# ❌ Skip — trivial one-liner (but see Rule 3 if it's also @property)
def _fmt_path(self, p: str) -> str:
    return str(Path(p).resolve())
```

### Python Rule 7 — Class methods and `__init__`

```python
class DataLoader:
    @_dft.log_init
    def __init__(self, path): ...   # __init__ uses log_init, not log

    @_dft.log
    def load(self, path: str, batch_size: int) -> list:
        ...

    def _validate(self, x):      # ❌ skip — trivial helper
        return x is not None
```

### Python Rule 8 — Simulated/emulated-clock code MUST use the manual `log_event` API, never the auto decorator

```python
from dftracer.python import dftracer, dft_fn as DFTracerFn
_dft = DFTracerFn("<category>")   # module-level, any file — not just the entry point

# WRONG for a simulated job event — @_dft.log measures REAL wall-clock time,
# not the emulator's own simulated timestep:
@_dft.log
def advance_job(self, job, sim_time): ...

# ALSO WRONG — a per-file `_dft_log` handle is only ever set by the ENTRY-POINT
# file's `dftracer.initialize_log(...)` call; any other module that declares its
# own `_dft_log = None` stub and gates calls behind `if _dft_log is not None:`
# is dead code that never fires (real bug found 2026-07-16 on flux-fiction —
# looked correct, silently logged nothing).

# CORRECT — call the PROCESS-WIDE SINGLETON directly; this works from ANY
# module regardless of which file called initialize_log():
dftracer.get_instance().log_event(
    name="advance_job",
    cat="<category>",
    start_time=sim_start_time,   # the emulator's own simulated clock; unit depends on DFTRACER_TIME_METRIC (see note below) — not time.time()
    duration=sim_duration,
    int_args={"job_id": (0, job.id)},          # MUST be a (tag_type, value) tuple, not a bare value
    string_args={"mhost": (0, node_name)},     # see node-grouping note below — the node identifier itself, no hashing needed
)
```

**`int_args`/`string_args`/`float_args` values MUST be `(tag_type, value)`
2-tuples, never bare values (confirmed 2026-07-17, real bug):** the native
`dftracer.python` C extension raises `log_event(): incompatible function
arguments` if you pass `{"job_id": 123}` instead of `{"job_id": (0, 123)}` —
`0` is `TagType.KEY.value` (the tag type for a normal key/value pair; see
`dftracer/python/common.py`'s `TagValue.value() -> (int(self._tag_type),
self._value)`). Because manual `log_event` calls are typically wrapped in a
broad `try/except Exception: logger.debug(...)` (to avoid crashing the app on
a tracing failure), this exact bug is SILENT at normal log levels — the
process runs to completion successfully, but ships a trace with ZERO events
from every affected call site. Always verify manually-annotated `log_event`
calls actually produced trace events (via the `reader`/`event_count` MCP
tools), never just that the process exited 0.

**`DFTRACER_INC_METADATA=1` is REQUIRED for `int_args`/`string_args`/
`float_args` to appear in the trace AT ALL (confirmed 2026-07-17) — set it
alongside `DFTRACER_ENABLE=1`/`DFTRACER_LOG_FILE`/`DFTRACER_DATA_DIR` for
every run, on the C side too.** Defined in
`dftracer/include/dftracer/core/common/constants.h:18`. Without it, EVERY
`log_event`/decorator call (manual or auto) writes `name`/`cat`/`start_time`/
`duration` correctly but silently drops the entire `args` payload — this is
easy to misdiagnose as a tag-naming or tuple-format bug (it looked exactly
like a broken `mhost` tag on a real session) when it's actually just a
missing env var. Check this FIRST whenever `args`/tag data is missing from a
trace but event names/counts/timing are otherwise correct.

**`start_time`/`duration` unit for manual `log_event` calls depends on
`DFTRACER_TIME_METRIC` (final correction, 2026-07-17) — check which dftracer
branch/version is installed before assuming a unit.** The `feature/time_scale`
branch (`git+https://github.com/LLNL/dftracer.git@feature/time_scale`, not yet
merged to `develop` as of this writing) adds `DFTRACER_TIME_METRIC` — an env
var with values `NS`/`US`/`MS`/`SEC` (default `US`) that tells dftracer how to
interpret raw `start_time`/`duration` integers — defined in
`dftracer/core/common/constants.h`/`enumeration.h`. Set it explicitly (e.g.
`os.environ.setdefault("DFTRACER_TIME_METRIC", "NS")`) BEFORE
`initialize_log()`, and convert your simulated-clock values to match: `* 1e9`
for `NS`, `* 1e6` for `US`, etc. Name variables with the matching suffix
(`_ns`, `_us`, ...) so the unit is obvious at the call site. On the plain
`develop` branch (no `DFTRACER_TIME_METRIC` support), the implicit/default
unit was empirically confirmed to be microseconds — verify against the
installed branch rather than assuming either unit blindly.

**Prefer the `python_annotate_manual_event` MCP tool over hand-writing this.**
It always emits the correct `dftracer.get_instance().log_event(...)` singleton
call (never the dead-stub pattern above) and handles the import/availability
boilerplate idempotently — you (or an agent) identify WHICH function needs
manual treatment (domain judgment the tool can't make), then call the tool
with `start_time_expr`/`duration_expr` as Python expression strings.

**Node/rank grouping via `mhost`:** if a single process emulates work that
conceptually happened on multiple nodes/ranks (e.g. a job scheduler emulator
where one Python process "runs" jobs across many simulated nodes), the
trace's native `pid`/`tid` are fixed per-process and can't be overridden per
event (confirmed: the native `dftracer.dftracer` C-extension's `log_event` has
no such parameter) — leave them as whatever dummy/constant values the process
has. Instead tag each event with `string_args={"mhost": <the real node
identity itself, e.g. a node name/id string>}`. Use a CUSTOM key like
`"mhost"` ("manual host"), deliberately NOT `"hhash"` — `"hhash"` is the
RESERVED short-name the trace indexer/analyzer already recognizes and
populates from the real process hostname (confirmed in
`dftracer/utils/indexer.py`'s own docstring); reusing it would collide with
that native column instead of adding a separate, analysis/viz-overridable
value. `mhost` does not need to be a hash at all — any stable string
identifier for the node works equally well for grouping purposes.

**If a single event conceptually spans MULTIPLE nodes (e.g. a multi-node job),
emit ONE `log_event` PER node, not one event with a comma-joined node-list
string (corrected 2026-07-17, real mistake caught in review).** A single
event tagged `mhost="20,21,...,29"` can't be grouped/filtered per-node by a
trace viewer — that defeats the entire purpose of the tag. Loop over the
node list and call `log_event` once per node, with identical
`start_time`/`duration` and only `mhost` differing per call — same pattern as
per-MPI-rank tracing (one event per rank, not one event listing all ranks).
A 10-node job should produce 10 events at that trace point, not 1.
Must be a STRING, not `int_args`.

**Why the manual API is needed at all:** the `@_dft.log`/`with DFTracerFn(...)`
decorator/context-manager forms always measure real wall-clock start/end at
the point they execute — they have no way to know a function's real execution
represents a DIFFERENT, simulated point in time. Any code path driven by an
app's own simulated/emulated clock (discrete-event simulators, faketime-driven
job schedulers, replay engines) must use the manual
`log_event(name, cat, start_time, duration, ...)` API instead, passing the
simulated clock's own values — this is exactly why `dftracer.python`'s
underlying logger exposes `log_event`/`get_time` as
public methods rather than only the decorator sugar. Identify which
functions represent simulated-time events (vs. real annotation/bookkeeping
functions that legitimately run at wall-clock speed) before blanket-applying
`python_annotate_project` to a simulator's code — the tool cannot make this
distinction automatically, it must be a manual judgment call per function.

### Python Quick checklist

- [ ] `from dftracer.python import dftracer, dft_fn as DFTracerFn` imported (NOT `dftracer.logger`)
- [ ] `_dft = DFTracerFn("<category>")` present once per file
- [ ] Entry-point file: `dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)` called, `_dft_log.finalize()` before every return
- [ ] ALL non-trivial functions decorated with `@_dft.log` (`@_dft.log_init` for `__init__`) — skip only pure getters/one-liners
- [ ] NO function decorated with `@property`/`@cached_property`/`@x.setter`/`@x.deleter`/`@x.getter` has a stacked `@_dft.log` — contextual `with DFTracerFn(...)` region instead
- [ ] No file anywhere in the tree still references `dftracer.logger` or `DFTracer.initialize_log`/`DFTracer.finalize_log` (stale API — grep for it if in doubt)
- [ ] Every `DataLoader`/`DataListLoader`/`multiprocessing.Process`/`Pool` construction with more than one worker has a `worker_init_fn` (or equivalent) that calls `initialize_log()` + registers an `atexit` `finalize()` INSIDE the worker process (Rule 2b) — a plain top-level function, never a closure, if `multiprocessing_context="spawn"` is used anywhere
