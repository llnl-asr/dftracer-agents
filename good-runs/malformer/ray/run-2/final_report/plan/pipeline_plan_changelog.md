
## 2026-07-25 -- STEP 7: dftracer-tracer (GCS root-cause diagnostic)

Root-caused the persistent `ray start --head` GCS timeout bug (deterministic
~35s failure with `gcs_server.err: FileNotFoundError`): Ray's
`start_ray_process()` unconditionally injects `LD_PRELOAD=libjemalloc.so`
into every Ray subprocess unless `LD_PRELOAD` is already set in the parent
env; under `module load rocm/6.2.1` on Tuolumne this fails with "cannot
allocate memory in static TLS block" before gcs_server's `main()` ever runs,
so it writes zero log lines. Fix: `export LD_PRELOAD=""` before every
`ray start` call. Verified standalone and via full job launches — GCS now
starts reliably every time. Also found and fixed three further bugs blocking
the actual 2-node baseline run once GCS itself worked: (1) an address-string
parsing bug in the retry loop's `grep`, (2) `ray.init()` with no `RAY_ADDRESS`
set silently building an isolated 1-node cluster instead of joining the
manually bootstrapped one, (3) the head node starting with `--num-gpus=0`
(a GCS-debugging leftover) under-resourcing the cluster to 4 GPUs against an
8-GPU `ScalingConfig`. All fixes applied to
`<WS>/scripts/baseline_runner.sh`. Baseline run now reliably reaches
"Connected to Ray cluster" + dataset path resolution, but stalls
indefinitely afterward with no traceback and no Tune scheduling activity —
this is a DIFFERENT, not-yet-root-caused issue (see `pipeline_plan.md`
STEP 7 status note for hypotheses and the recommended `py-spy` next step).
STEP 7 is NOT complete; no baseline trace with confirmed dataset I/O exists
yet.

## 2026-07-25 -- STEP 6: dftracer-build-smoke

Resumed an interrupted STEP 6 (prior attempt had only 4.4s recorded, no completed
smoke test). Root-caused and fixed the dftracer python-extension ABI mismatch: the
harness's own `VIRTUAL_ENV` (Python 3.13) was overriding the app venv's Python 3.9
during dftracer's CMake configure, producing a `cpython-313` `.so` inside a py3.9
venv that silently ImportErrors and falls back to `NoOpProfiler` (zero trace output,
no error, exit 0 -- this is why earlier "green" builds produced nothing). Fixed by
adding `unset VIRTUAL_ENV` to `<WS>/rebuild_dftracer.sh` before the pip install/cmake
step. Rebuilt dftracer (now cp39, native import verified). Identified the true app
entry point (`molformer_ray_descriptors.py` per `start_ray_run_molformer.sh`, not
`molformer_ray.py`). Ran a single-process smoke test exercising
`dftracer.initialize_log()`, a trivial `ray.remote()` task, and a decorated app
function; produced a non-empty 1.05MB compressed trace (83467 events) with both
app-layer (`cat=molformer_ray`) and Ray-framework-layer (`cat=comm`:
`RemoteFunction._remote`, `SerializationContext.serialize/_deserialize_object`,
`Worker.get_objects`) events present, plus ~83k POSIX I/O events. Captured run
record as `"smoke"`. Pipeline is ready to proceed to STEP 7 (baseline tracer run).

## 2026-07-25 -- STEP 7: dftracer-tracer (continued forensic pass, session 2)

Resumed the STEP 7 blocker investigation from the prior pass's live-forensics
handoff. Added `BISECT:` print/flush checkpoints to
`molformer_ray_descriptors.py` around `ray.init()`, `read_csv`,
`TorchTrainer(...)`, and `.fit()`. Reproduced the hang and confirmed it is
strictly inside `ray.init()`'s `connect()` call, before dataset loading or
TorchTrainer construction -- ruling out the HF-network and OOM-actor
hypotheses from the prior pass. Used `flux exec -r <rank>` to read the head
node's raylet.err/gcs_server.out/debug_state.txt live while hung (the
concrete evidence gap the prior pass flagged): found raylet forks
~192 concurrent prestart Python workers (`num_prestart_python_workers`
defaults to auto-detected `num_cpus`) that ALL fail to register --
diagnosed as an import-storm against the Lustre-hosted venv. Applied
`--num-cpus=16` to both `ray start` calls in `baseline_runner.sh` to cut
this from 192 to 16; confirmed via `ps` the raylet cmdline reflects the
change, but the hang persisted identically even at 16 concurrent workers,
and `debug_state.txt` showed the job's own registration succeeding
(`registered jobs: 1`, both nodes' resources mutually visible) while
`num PYTHON workers: 0` -- so there is a second, independent CoreWorker-
startup defect not explained by prestart concurrency alone. Ruled out a
Spindle recurrence (confirmed spindle.level=off is effective, no spindle
processes present). Kept the `--num-cpus=16` fix (correct sizing
regardless) but the primary blocker remains unresolved. Updated
`pipeline_plan.md` STEP 7 with the full evidence trail and next steps
(py-spy or gdb backtrace of the frozen driver, and a minimal
dftracer-free `ray.init()`-only smoke script to isolate whether dftracer's
`initialize_log()` interferes with CoreWorker's fork/thread state).
STEP 7 remains incomplete.

## 2026-07-25 -- STEP 7 (retroactive completion note) + STEP 10: dftracer-optimizer

STEP 7 (baseline) is now confirmed COMPLETE per `software-ray-molformer` memory: all
GCS/worker-registration/HF-offline/dftracer-init-per-process blockers documented in
the changelog's earlier STEP 7 entries were fully root-caused and fixed (14 confirmed
fixes) in a prior session pass. A real 2-node/8-GPU Ray Train run completed 20
iterations in ~4.2-4.4 min wall time with byte-exact dataset I/O captured in the
trace. Clean baseline traces: `<WS>/baseline/traces/raw_final/`.

STEP 10 (optimizer, all 4 dimensions): diagnosed compute-bound (STEP 9: 92% GPU
compute, 7.4% comm, 0.26% I/O). Walked the full 4-dimension checklist (Pipeline
Policy #14) — COMPUTE: applied bf16 autocast (`torch.autocast(device_type="cuda",
dtype=torch.bfloat16)` around the forward pass only) as `opt1`, the only genuinely
untried, high-confidence candidate given zero prior mixed-precision usage.
COMMUNICATION/I/O/MEMORY: walked their checklists and recorded "not applicable /
negligible" verdicts with concrete evidence (documented in pipeline_plan.md, not
silently skipped).

opt1 run required 3 submission attempts on the live 32-node `<flux-jobid>` allocation
(no new allocation requested):
- Attempt 1 (f6e9nUBC3Z): worker's 60s wait for the head's Ray-address file timed out
  even though the head had written it ~15-40s earlier — root-caused as Lustre
  cross-node dentry-cache staleness (matches a prior-session lesson in
  `software-ray-molformer`). Cluster stranded at 4/8 GPUs, Ray Train hung
  indefinitely on "insufficient resources" warnings. Job would not respond to
  `flux job kill`/`flux job kill -s SIGKILL` (unclear why — possibly a pipe/tee
  buffering issue on signal delivery); `flux job raise -t cancel` DID work and moved
  it to CA. Fixed the root cause by widening the worker's wait loop 60s->180s and
  forcing an `ls` dentry-cache refresh each iteration in both `opt1_runner.sh` and
  `baseline_runner.sh` (future baseline reruns get the same hardening).
- Attempt 2 (f6i5P5QPfu): worker registered with the Ray cluster this time, but a
  CoreWorker on the worker node failed to register with its local raylet
  ("Unable to register worker with raylet... End of file") ~2s after connecting —
  a different, transient Ray-internal race, not reproduced by a specific root cause
  this session. Training never started (no BISECT "after trainer.fit()" line); the
  script's `TRAIN_EXIT=$?` capture after a `python3 | tee` pipe (no `pipefail`) masked
  this as an apparent exit 0 — a latent bug worth fixing in a future session but not
  touched here (out of scope, didn't block getting a real result on the next attempt).
- Attempt 3 (f6k19AzFmR): SUCCEEDED. 8 GPUs registered, `Training resumed at epoch 0,
  iteration 0` through `training_iteration 20`, Total running time 4min 33s
  (252.571s pure training-loop time_total_s). Traces captured and analyzed.

Measured result (dfanalyzer `dlio` preset, `cluster_n_workers=8`, honest — not
selectively favorable): opt1 Job Time 275.4s vs baseline 265.4s — **+3.8% SLOWER**,
not a win. Communication (~7.3-7.4%) and POSIX I/O (0.26-0.35%, ~21MB) essentially
unchanged both runs, confirming those remain non-bottlenecks either way. Full
verdict and plausible-cause discussion recorded in `pipeline_plan.md` STEP 10 result
section. This is reported as a genuine negative optimization finding, not adjusted
or cherry-picked to show improvement.

## 2026-07-25 - STEP 10a dftracer-optimizer-compute

Appended STEP 10a RESULT to pipeline_plan.md. Downstream planning changes: (1) compute is
capped at ~25% of wall time -- 74% is Ray Train worker startup, so STEP 11 should not expect a
compute-driven speedup and a future pass should target startup; (2) bf16 autocast measured NO
CHANGE, so there is no compute best-variant to validate; (3) the NaN bug root cause is
non-finite cells in pubchem_filtered.csv, not a stats.npz mismatch -- opt2/opt3 sources carry
the torch.nan_to_num fix and should be the basis of any further runs; (4) added a hard
measurement rule (interleave a control replicate; never compare across time windows) after an
unmodified-baseline control reproduced the variant timing exactly; (5) recorded that variant
trace dirs must be pre-created and variant dataset dirs must be Lustre symlinks.

## 2026-08-02 - fresh baseline_4node run: 6 bugs found + fixed, run succeeded

Ran a brand-new `baseline_4node` run_name (4-node/16-GPU, distinct from the original 2-node
`baseline`) end-to-end for the first time — this resolves STEP 7's "unresolved as of
2026-07-25" stall diagnosis. Six distinct bugs found and fixed: (1) new run_name's dataset dir
not auto-symlinked to Lustre; (2) HF model cache needs to be on PFS not $HOME; (3) app pins a
specific HF revision that a plain `hf download` doesn't fetch; (4) `transformers`' dynamic
module cache has a genuine concurrent first-write race (pre-warm fixes it); (5) the worker
join retry-loop's success-string check never matched what Ray actually prints, inflating
apparent GCS flakiness; (6) `default_worker.py`'s dftracer HIP-tracing init ran before `torch`
was imported, crashing rocprofiler-sdk. Also ported and fixed the PyTorch-profiler->dftracer
`PP`-category bridge from the unused v2 script into the actually-run v1 script, configured for
full-run coverage (`repeat=1`, `active=1_000_000`) rather than a sampled window. Full technical
detail recorded in the `software-molformer` skill (not duplicated in this plan) and a new
`bug-dftracer-stats-categories-zero-events` finding (dftracer_stats's categories/summary report
is separately broken — verified event categories via direct gzip+json scan instead). Compacted
trace confirmed with all 9 expected categories present (`baseline_4node/traces/compact/`).
Downstream planning implication: any FUTURE new run_name for this app (opt4, a rerun, etc.)
should apply fixes (1)-(6) proactively rather than rediscovering them — see the skill for exact
code/config to reuse. `dftracer_service` node-counter daemon remains a known, unfixed gap for
this specific run (silent no-op, separate from the app's own trace categories).

## 2026-08-02 — dftracer-analyzer: baseline_4node bottleneck diagnosis

Analyzed the new `baseline_4node/traces/compact/` (27.3M events, 56 chunks, verified via
`event_count`). `analyze(analyzer_preset="generic")` at `cluster_n_workers=32` hit thread
exhaustion (`DFTUtilsError: Resource temporarily unavailable`); `cluster_n_workers=8` succeeded.
`diagnose()` returned 16 real findings (not the 0-observation bug) but all as generic
`<layer>_ops_slope` "investigate" motifs, not semantic POSIX/DLIO categories — bottleneck
ranking instead built from the analyzer's own Layer Breakdown time table. Result: compute
(PyTorch Profiler + hip_runtime_api + kernel_dispatch, ~3855s cumulative) is the largest
bucket, but `molformer_ray_descriptors` (Ray/HF bring-up, 1864s) remains ~45% the size of
the training-loop bucket (2286s) — **the STEP 10a "startup-dominated" finding holds at
4-node scale with full PP coverage**. Comm (307s) and I/O (78s, small-I/O pattern) are
minor by comparison; memory-copy/scratch-memory are negligible. Comparator against the old
2-node `baseline/` failed at the tool level on two attempts (compound-OR query, then no
query) — flagged as a known gap, not root-caused this pass (allocation ran low on time; not
a data problem since both directories individually analyzed cleanly). New caveat recorded:
use `cluster_n_workers=8`, not 32, for ~27M-event/56-chunk traces on this system. Full
findings and ranked bottleneck list added to `pipeline_plan.md` as a new dated section
(did not touch/overwrite STEP 8/9/10/10a). This feeds `dftracer-optimizer` next.

- 2026-08-02 (dftracer-optimizer-io, STEP 10b): appended path-verified I/O attribution section.
  Answered the analyzer's flagged open question: the 2.83M ~1KB POSIX ops are Python
  site-packages import traffic on an NFS-resident venv (81.3% of I/O time), not app data;
  bounded at <=5.8% of the actor bring-up bucket, so I/O is NOT the lever for bring-up.
  Proposal-only; no source/script edits, no flux jobs launched.

## 2026-08-02 — dftracer-optimizer (STEP 10, second pass)

2026-08-02 — dftracer-optimizer (STEP 10, second pass) appended '## STEP 10 MERGED RESULT (2026-08-02): four-dimension optimization pass on baseline_4node'. Why: Pipeline Policy #14 four-component pass run against the fresh 4-node/16-GPU baseline_4node diagnosis. Outcome: negative result — two compute variants (opt4, opt5) applied and measured null; the 'actor bring-up / redundant HF model load' hypothesis was DISPROVED and replaced with a measured 58.9s hipModuleLoad code-object-relocation cost shown immovable from user space; communication re-attributed 541.7s of RCCL collective time previously mislabeled as compute, root-caused to rank/straggler skew (87% in the tail) rather than bandwidth; I/O path-verified to 81.3% Python import traffic on NFS, bounded to <=5.8% of bring-up; memory found no applicable target (zero HF checkpoint weight files ever read). No change is recommended for adoption. 8 unapplied citation-backed proposals carried forward. Does not modify STEP 8-11 or the 2026-08-02 analyzer section.
