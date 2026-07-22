## Rules

- Verify trace quality before diagnosis.
- Use `event_count` and `comparator` when available.
- Prefer ranked bottlenecks over narrative guesses.
- If the trace is too small or malformed, fix collection first.
- **Always index before analyzing.** Call `index(directory=<compact>,
  force=True, executor_threads=1)` before `analyze()` on any trace directory
  that hasn't been analyzed before. Concurrent `analyze()` workers all trying
  to build the RocksDB trace index at once race and crash
  (`Failed to open RocksDB ... .dftindex/*.log: No such file or directory`).
  Building the index single-threaded first means every worker then only
  *reads* it — safe. (`analyze()`'s own `_ensure_analyzable_path` now does
  this automatically when it has to split raw per-rank traces first, but
  build the index yourself before analyzing an already-compact/split
  directory that skips that code path.)
- **Two-stage analysis, always: generic first, then specialize.** Run
  `analyze(analyzer_preset="generic")` FIRST, unconditionally, on every trace
  — it auto-discovers one layer per distinct `cat` value actually present
  (works for ANY app, including ones with custom `dft_event_logging`
  category strings like `"compute"`/`"preprocess"`/`"communication-io"`, not
  just POSIX/DLIO-shaped traces). Only reach for `posix` or `dlio` presets
  AFTER the generic pass, and only when you specifically need their built-in
  semantic layer breakdown (raw POSIX call classification, or DLIO benchmark
  phase timing) — never as the first/only analysis, since both presets
  return a *silent* `returncode=0` with an EMPTY Layer Breakdown / zero-row
  flat views (no error at all) when the trace's `cat` values don't match
  their fixed layer-name list. `diagnose()` likewise works against ANY
  preset's checkpoint now (dynamic `<layer>_ops_slope` layer discovery) —
  no need to special-case it per preset.
- **Compute/data/comm overlap ("is X well-overlapped with compute", "how much
  is compute stalled waiting on Y") is a REAL, BUILT-IN dfanalyzer metric —
  don't hand-derive it from flat-view bucket sums.** `dftracer/analyzer/
  metrics.py` computes, for every layer named in the preset's `async_layers`
  config list, `u_<layer>_time_proc = (layer_time_proc - compute_time_proc)
  .clip(lower=0)` per bucket/process — i.e. the portion of that layer's time
  that did NOT overlap with concurrent `compute` time, emitted as
  `u_<layer>_time_proc`, `u_<layer>_time_proc_frac_self`,
  `u_<layer>_time_proc_frac_total` columns in the flat view. This directly
  answers "how much does compute stall on layer X" / overlap-sufficiency
  questions. **The catch:** `async_layers` defaults to `[]` for the plain
  base preset AND for `generic` (`AnalyzerPresetConfigGeneric` never
  overrides it) — only the DLIO preset ships a default list, and it's DLIO's
  own layer names (`data_loader`, `reader_posix`, etc.), not your app's
  custom `dft_event_logging` category names. If `async_layers` is empty, the
  metric silently never runs and no `u_` columns appear — this looks like
  "the feature doesn't exist" if you don't know to check the config, not
  like an error. **Fix:** pass an explicit Hydra override on `analyze()`
  naming the app's own layer(s) to diff against compute, e.g.
  `async_layers=[communication_io]` or `async_layers=[communication_io,
  communication_except_io]` for an app whose I/O/comm categories are named
  that way — then re-run `analyze()` and read the resulting `u_` columns.
  This metric is hardcoded to diff against `compute_time_proc` specifically
  (not an arbitrary pair), so a non-compute pairwise overlap question (e.g.
  "does data overlap with communication") isn't answered by this mechanism
  directly — reason about it via both layers' `u_*_vs_compute` numbers, or
  fall back to a bucket-level min/max bound (`min(A_time,B_time)` as an
  upper bound on overlap, `max(0, A_time+B_time-bucket_duration)` as a lower
  bound) only for that non-compute pair.
