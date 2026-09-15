---
name: genesis_run
description: Sweep ANY ice4hpc application across a configuration space (input x node-scale x processes-per-node) collecting full-feature dftracer traces — PAPI, GPU (ROCProfiler or CUPTI), variorum power, node utilization, MPI — into a validated, compacted, self-describing corpus under genesis/<system>/traces/<system>/. System-aware: GPU vendor, scheduler, queue, compiler, GPU-per-node count and PAPI preset set are all re-derived per machine, never copied. Load this to run or resume a genesis sweep.
---

# genesis_run

Produces a **trace corpus**: the same application traced across a grid of problem sizes
and parallel decompositions, with every instrumentation layer on, every run content-
validated, and the result compacted and documented.

Invoke as `genesis_run <app>` where `<app>` is the application name exactly as it appears
in **column 3** of `llnl/ice4hpc_data` `data/merged.txt` (e.g. `miniFE.x`, `laghos`,
`amg`, `XSBench`, `kripke.exe`, `miniVite`, `TestDfft`).

> **Rule 0 — nothing in this skill is a constant.** GPU vendor, GPUs per node, scheduler,
> queue limits, compiler, and the PAPI preset set differ per machine. Every number below
> is *derived at run time* from the target system. Copying another system's numbers is
> the single most common way to produce a corpus that looks healthy and is wrong.

---

## STEP 0 — Write the plan, then stop. Three mandatory gates.

A genesis sweep is hundreds of jobs and many node-hours. It is **never** launched
straight through. The flow is always:

```
  plan  ->  [GATE 1]  ->  annotate/build/smoke  ->  [GATE 2]  ->  dimensions  ->  [GATE 3]  ->  sweep
```

**Nothing after a gate may start until the user has explicitly confirmed that gate.**
Do not treat your own earlier message, a tool result, or silence as confirmation.

### Before anything else — write `genesis/<system>/PLAN.md`

Derive it from STEPs 1-9 and write it into this system's folder, so the plan lives beside
the run it describes and survives the session. It must state, concretely and with the
values actually measured on this machine (never placeholders):

1. **App + variant** — which source variant, and *why* (ice4hpc `maps.sh` mapping vs the
   `merged.txt` provenance, and how any disagreement was resolved)
2. **Source session** — which session supplies the annotated source, and the evidence it
   qualifies
3. **System profile** — GPU vendor/arch, GPUs per node, cores per node, scheduler,
   the queue that actually schedules and its node/time limits, primary Lustre
4. **dftracer source** — branch + why (vendor rule), and the features to compile in
5. **System folder layout** — the exact paths that will be created
6. **PAPI plan** — this machine's counter budget, preset count, derived presets, and the
   proposed partition
7. **Aggregation** — the rule, and the measured keep-rate it is expected to produce
8. **Dimensions** — inputs x node scales x ppn, with **total run count and node-hours**
9. **Risks / known caveats** for this machine
10. **What will be validated** per run, and what "done" means

Then present a summary and **stop at GATE 1**.

### GATE 1 — plan confirmed

The user confirms the plan (or corrects it). Only then run the annotate/build/smoke
pipeline of STEP 5, one step at a time, reporting the real outcome of each — a build that
"succeeded" is not evidence until `ldd` shows dftracer linked.

### GATE 2 — smoke run confirmed

After the single-rank smoke test, **parse the trace** and present an event inventory:
every expected layer with its event count — app annotation (`CPP_APP` or language
equivalent), GPU (`KERNEL_DISPATCH`/`MEMORY_COPY`, or CUPTI equivalents), `papi` with the
counters that actually landed and `multiplex`, MPI (`collective`/`comm`/`p2p`),
`POSIX`/`STDIO`, and the service's `sys`/`io`/`net` plus variorum `gpu`/power.

Say plainly which layers are **present**, which are **absent**, and for each absent one
whether it is expected on this machine (e.g. GPU hardware counters without `SYS_PERFMON`)
or a real defect to fix before sweeping. A layer silently missing here becomes a corpus
with a hole in it that no amount of later processing can fill. **Stop at GATE 2.**

### GATE 3 — dimensions confirmed

Present the final matrix — the exact input list, node scales, ppn ladder, PAPI sets, the
**total run count**, estimated node-hours, and the wall-clock estimate given the queue's
limits. This is the last point at which scope is cheap to change. **Stop at GATE 3.**

Only after GATE 3 does the sweep launch.

---

## STEP 1 — Resolve the app and its inputs

`merged.txt` is CSV: `machine,procs,app,"args",seconds`.

**Use EVERY unique argument string. The input dimension is exhaustive by default.**
Materialise it once into `genesis/<system>/inputs.txt` and have the worker read that
file, so the list is reproducible and the sweep can be resumed against it:

```bash
curl -sL https://raw.githubusercontent.com/llnl/ice4hpc_data/main/data/merged.txt -o merged.txt
# EVERY unique argument string for this app, across all machines -- the args are
# machine-independent, so rows from other machines still describe valid inputs.
grep ",<app>," merged.txt \
  | sed 's/^[^,]*,[^,]*,<app>,"//; s/",[0-9.]*$//' \
  | sort -u > genesis/<system>/inputs.txt
wc -l genesis/<system>/inputs.txt
```

Note the quote handling: the args field is quoted in the CSV, so strip the opening
`"` and the trailing `",<seconds>` together. Dropping only the trailing seconds
leaves a stray quote in every case name.

Also fetch `scripts/maps.sh` from the same repo — it declares, per app:

* `app_gpu[<app>]` — `1` means a GPU build exists
* `app_var[<app>]` and `app_var["$(key <app> <machine>)"]` — which source variant that
  machine uses (e.g. `-openmp45` for GPU, `-ref` for CPU)
* `app_exe`, `app_dir`, `app_mpi`, `app_omp`

**Reconcile the two sources before choosing.** They routinely disagree: `maps.sh` can map
a machine to a GPU variant while every row for that app in `merged.txt` came from a
`*-cpu` machine (the *arguments* are variant-independent, but the *timings* are not).
When they disagree, say so and ask which variant to trace — it changes the ppn ladder,
the build, and roughly halves or doubles the run count.

### Do NOT subset the inputs

A genesis corpus exists to be exhaustive over the configuration space, so **every unique
argument string is swept** — the input dimension is never silently trimmed. Measured
counts, so the scale is not a surprise:

| app | unique args |
|---|---|
| `amg` | 16 |
| `XSBench` | 24 |
| `miniFE.x` | 49 |
| `laghos` | 186 |
| `kripke.exe` | 210 |
| `miniVite` | 288 |

Multiply by node scales x ppn x PAPI sets for the run count: laghos at 186 x 3 x 3 x 2 is
**3348 runs**, which completed in ~380 node-hours. That is large but entirely tractable
with the packing worker of STEP 10 — it is a scheduling problem, not a reason to cut the
science.

Still **state the total run count and node-hours at GATE 3** so the cost is explicit and
the user can choose to trim. But present the full grid as the default; propose a reduced
list only if the user asks, or if the measured node-hours genuinely exceed the allocation
available. If a run is ever trimmed, record exactly which inputs were dropped and why in
`PLAN.md` and in the corpus README — a corpus with a silently missing region is worse
than a smaller one that says what it covers.

## STEP 2 — Find a successful session for the app

Sweeps do **not** re-annotate from scratch. They take the annotated source of a session
that already got the app working and treat it as a **read-only reference**, which STEP 5
then *copies* into this system's own folder. Nothing under the session root is written by
a sweep, so several systems can sweep the same session at once without interfering.

```bash
ls -d $PROJECT_ROOT/workspaces/<app>/*/          # newest first
```

A session qualifies only if all of these hold — check, don't assume:

```bash
test -f <session>/session.json                            # session exists
grep -rl DFTRACER <session>/annotated --include=*.c --include=*.cpp \
                                      --include=*.hpp --include=*.py | wc -l   # > 0
ldd <session>/<path-to-binary> | grep libdftracer_core    # a working build existed
```

The binary check confirms the app was once built successfully with dftracer linked — it
is evidence the session is sound, **not** an artifact the sweep will use. That binary may
well be for a different GPU arch; STEP 5 rebuilds for this machine regardless.

If no session qualifies, stop and run the normal annotate/build pipeline first. A sweep
over an unannotated binary produces traces with no `CPP_APP`/app events — technically
valid files, scientifically useless.

## STEP 3 — Detect the system and derive its parameters

```bash
hostname                       # strip trailing digits -> system name
flux queue list                # limits
flux queue status              # WHICH QUEUES ACTUALLY SCHEDULE  <-- do not skip
flux resource list             # nodes, cores, GPUS  -> GPUs per node
```

Load the matching system skill ([[system-tuolumne]], [[system-tioga]],
[[system-matrix]], [[system-dane]], ...). If none exists, create one as you learn the machine.

Derive and record:

| Parameter | How |
| --- | --- |
| GPU vendor/arch | `rocminfo` / `nvidia-smi`; `/opt/rocm*` vs CUDA toolkit |
| **GPUs per node** | `flux resource list` NGPUS / NNODES — this **is** the GPU ppn ladder |
| Scheduler | Flux (`flux alloc`) vs Slurm (`salloc`/`srun`) |
| Usable queue | one whose **scheduling is started** and whose node/time limits fit |
| Compiler / MPI | from the system skill; ice4hpc pins a specific `rocmcc`/`cray-mpich` |
| PAPI presets | `papi_avail` on THIS machine (see STEP 6) |

**`flux queue status` is mandatory, and so is checking that the account may USE the
queue.** A queue can fail in three independent ways that all look like a busy cluster:
its scheduler is stopped (`flux queue status` says so, `flux queue list` does not); the
account is not authorised for it (only a real submit reveals
*"queue X not valid for user; valid queues for user: ..."*); or it advertises nodes that
belong to other queues it merely aggregates. Confirm with an actual 1-node submit rather
than reading the queue table, and record the usable queues in the system skill.

Also check for **down nodes**: a queue with N nodes and one down can only schedule N-1,
so a request for the full N queues forever instead of failing. Compare
`flux resource list` free+allocated+down against the queue's advertised size before
choosing the top node-scale.

### On a Slurm machine, the equivalent discovery is different — and has its own trap

```bash
sinfo -o "%20P %5a %10l %6D %10T %N"                      # partitions, limits, nodes, state
scontrol show partition <p> | tr ' ' '\n' | grep -E "MaxNodes|MaxTime|TotalNodes"
scontrol show node <n>      | grep -E "Gres=|CPUTot|Sockets"   # GPUs per node
sbatch -p <p> -N<n> -t <m> --gres=gpu:4 --test-only --wrap=hostname   # backfill ETA, creates NO job
```

**`MaxNodes` is mandatory to check, and it is invisible in default `sinfo` output.**
The Slurm analogue of "queue accepts but never schedules" is a partition that caps every
job at one node: the ladder above N=1 is then impossible there, and the rejection blames
GRES rather than the node count (*"Invalid generic resource (gres) specification"*).

Also check whether partitions **share nodes** (`sinfo -h -p A -o %N` vs `-p B`), and pick
the job shape with `--test-only` rather than intuition — backfill ETA may depend on node
count alone and be insensitive to walltime, so a bigger allocation can cost hours of
queue for no gain.

The system skill records which partition to use, its real `MaxNodes`/`MaxTime`, and any
sharing between partitions. Full workflow: [[slurm-alloc]].

## STEP 4 — Choose the dftracer source (vendor-dependent)

| Target | Source |
| --- | --- |
| **NVIDIA GPU system** | the CUPTI branch **`feature/cupti`** — from czgitlab, or from a local checkout if the system skill names one |
| **Everything else** | latest **`develop`** from czgitlab |

```bash
G=ssh://git@czgitlab.llnl.gov:7999/dftracer
pip install --no-cache-dir --no-deps "git+$G/dftracer.git@develop"      # or @feature/cupti
pip install --no-cache-dir --no-deps "git+$G/pydftracer.git@develop"
pip install --no-cache-dir --no-deps "git+$G/dftracer-utils.git@v0.0.12"
```

* `czgitlab.llnl.gov` is reachable only inside LC, over **SSH port 7999**; HTTPS times out.
* Compute/login nodes often have **no external DNS**, so pip cannot reach PyPI. Use
  `--no-deps` and install all three packages explicitly from gitlab.
* `dftracer-utils` `develop` HEAD currently fails to compile (`ConfigTree::Node` used as
  an incomplete type in `std::vector<std::pair<std::string, Node>>`). Fall back to the
  newest **tag** on the same gitlab repo, not to PyPI.
* Install order is **dftracer first**, then the other two — reversed, stale headers collide.
* `pip uninstall dftracer` wipes the shared `dftracer/bin` prefix, taking
  `dftracer-utils`' binaries with it. Reinstall utils after any core reinstall.
* Uninstalling also leaves a stale cmake brahma tree that breaks the next build with
  `override` errors — remove it.

## STEP 5 — Create a SELF-CONTAINED system folder

Everything a sweep touches on this machine lives under one per-system folder. Nothing is
shared with another system except the session's read-only canonical annotated source.
Two systems can then sweep the same session **concurrently** without contaminating each
other.

```
<session>/
  annotated/                     # canonical annotated source — READ-ONLY reference
  genesis/<system>/
    env.sh                       # modules, CC/CXX, PAPI_DIR, LD_LIBRARY_PATH
    aggregation.yaml             # selective-aggregation rules (STEP 7)
    dataset -> /p/<this-system-lustre>/$USER/<app>_genesis/   # THIS machine's PFS
    annotated/                   # this system's OWN COPY of the annotated source
    venv/                        # this system's dftracer/pydftracer/dftracer-utils
    build/                       # this system's build of the annotated app
    papi_sets.txt                # partition derived + probe-verified HERE
    traces/<system>/             # THE CORPUS — inside the isolated system folder
      <case>/<nodes>/<ppn>/{raw,compacted}
      README.md                  # corpus summary for this system
    artifacts/                   # logs, state, per-run validation
```

The corpus lives **inside** the system folder, not in a shared top-level directory, so a
system folder is a single self-contained unit: its environment, its build, its data
symlink, its traces and its summary all move or archive together, and nothing it writes
can collide with another system's sweep.

### Why each of these is per-system, not shared

* **`annotated/` is copied, not shared.** The build edits the tree in place: GPU arch
  flags (`gfx90a` vs `gfx942` vs a CUDA arch), the compiler wrappers, and dftracer's
  `-I`/`-L`/`-rpath` all get written into the Makefiles, and object files land beside the
  sources. Two systems sharing one tree overwrite each other's build settings and race on
  `.o` files — and the damage is silent, because the loser still produces a binary. Copy
  the annotated tree once per system and let each own its build state. (The *annotations*
  are identical; only the build configuration differs.)
* **`build/` is per-system** — a binary is not portable. An OpenMP-target-offload build
  for `gfx942` will not run its kernels on `gfx90a`, and a CUDA build is different again.
* **`venv/` is per-system** — dftracer links this machine's PAPI, MPI and GPU runtime.
* **`dataset` is a symlink onto THIS system's Lustre**, because Lustre mounts are usually
  *not* shared between machines even when the NFS workspace is. Point it at a locally
  mounted PFS (check `ls -d /p/lustre*` and the system skill for which one is primary).
  A `dataset` symlink inherited from another machine **dangles**: the app cannot write
  its output, and the STEP 11 science audit reports "no result file" for every run while
  the traces themselves read perfectly. Per [[feedback-lustre-io]], application data goes
  on Lustre; dftracer **traces** stay in the session workspace, never on Lustre.
* **`papi_sets.txt` is per-system** — the preset set and the derived-preset list differ
  per CPU (STEP 6).

### Bootstrap a fresh system

```bash
SYS=$(hostname | sed 's/[0-9]*$//')
G=<session>/genesis/$SYS
mkdir -p "$G"

# 1. this machine's PFS for application output
mkdir -p /p/<primary-lustre>/$USER/<app>_genesis
ln -sfn /p/<primary-lustre>/$USER/<app>_genesis "$G/dataset"

# 2. this machine's own annotated tree (preserve symlinks/permissions)
cp -a <session>/annotated/. "$G/annotated/"

# 3. env.sh pinning the ice4hpc toolchain for THIS app on THIS machine
#    (not the site-default PrgEnv). Load `papi` on its own line — folding it into
#    the same `module load` as `rocmcc` can drop it silently, leaving
#    CRAY_PAPI_PREFIX empty with no error.

# 4. this machine's dftracer (STEP 4) into $G/venv
# 5. build $G/annotated -> $G/build against that dftracer and this GPU arch
```

Then **verify the build actually picked up dftracer**, rather than trusting that `make`
returned 0:

```bash
ldd "$G/build/<exe>" | grep -E "libdftracer_core|papi|rocprofiler|cupti|mpi"
readlink -f "$G/dataset"        # must resolve to a LOCALLY mounted PFS
```

Finally smoke-test one rank and confirm **from the trace**, not the exit code, that every
expected layer produced events — app annotation, GPU, PAPI, MPI, and the node service.
Present that inventory and **stop at GATE 2** (STEP 0). Only after the user confirms it
do you derive the PAPI partition (STEP 6).

## STEP 6 — Derive the PAPI partition ON THIS MACHINE

`papi_avail` reports far more preset *names* than the hardware can count at once, and a
**derived** preset expands into 2+ native events. Sizing a set by counting names
therefore under-sizes it; PAPI responds by time-sharing and reporting **scaled estimates
with a zero exit code**.

```bash
papi_avail | grep "Number Hardware Counters"         # the real budget
papi_avail -a | awk '/^PAPI_/ {print $1, $3}'        # $3 == Yes  =>  derived
```

**The preset set, the derived list, and the counter budget all differ per CPU.** Two
machines can expose the same number of hardware counters yet a different number of
presets, and a preset that is *native* on one can be *derived* on another — so it packs
differently. Carrying a partition across machines therefore fails twice over: it requests
presets that do not exist there, and it mis-sizes the sets.

Consult the system skill for this machine's measured counter budget, preset count and
derived list; if the skill does not record them yet, derive them here and **write them
back into that system skill**.

Build a partition that covers **every** available preset with no duplicates, then
**probe** it with cheap 1-rank runs and read `args.multiplex` back out of the trace
(`0` = exact, `1` = estimate) before committing to the full sweep.

**`papi_avail`'s "Deriv" column under-reports cost — probe the boundary, do not compute
it.** A partition sized from the native=1/derived=2 model is a CANDIDATE, and it can be
wrong in the direction that silently degrades data. Measured on one CPU: a set of five
presets that the model scored at 5/5 actually multiplexed, and a four-preset set both
multiplexed *and* dropped a derived counter outright — while three fit exactly. The
partition had to grow from 7 sets to 8 after measurement.

Probe by bisection: try the whole family, then halve until `multiplex == 0`, and diff the
counters that landed against those requested. A cheap 1-rank run on a tiny problem is
enough (~10 s each), and it is far cheaper than discovering it 6 runs into a 672-run
sweep. Skipping this step is exactly how a corpus ends up full of scaled estimates that
still look healthy. Also diff the counter
names that actually landed against those requested — dftracer silently **drops** a preset
it cannot fit (observed with `PAPI_BR_PRC`, "one native event subtracted from another").
Full method in [[software-papi]].

### Porting the harness to a NEW app — the app's identity is baked into more than you think

Copying a previous sweep's `scripts/` is the right move (STEP 5), but the app's
input naming is hardcoded in **five** places, not just the `case_slug`/`case_args`
glue. Four of them fail *silently* — and three only bite at the very end, after the
whole sweep has run:

| File | What is hardcoded | Failure if missed |
| --- | --- | --- |
| `genesis_run.sh` | run-key prefix | wrong keys; resume never matches |
| `genesis_driver.sh` | the same key, rebuilt by hand for the completion check | matches nothing, so **every scale always looks pending** — the driver can never detect completion and keeps allocating until `MAX_ALLOCS` |
| `genesis_compact.sh` | the cell key | every already-compacted cell looks outstanding and is recompacted on each resume |
| `genesis_readme.py` | leaf glob (`input_*`) **and** a numeric sort `int(v.split('x')[0])` | glob finds **zero leaves → an empty README**; the sort raises `ValueError` on any non-numeric case name |
| `genesis_audit.py` | the whole science audit | audits nothing, or crashes |

Rules that prevent all five:

* **Build keys with `run_key`/`case_slug`, never by string-concatenating a prefix.**
  A hand-built key silently drifts from the worker's own naming.
* **Discover leaves by SHAPE (`*/nodes_*/ppn_*`), never by a name prefix.**
* **Never assume an input label is numeric.** Sort through a helper that falls
  back to lexical ordering.
* **The science audit is app-specific by nature — rewrite it, do not patch it.**
  It depends on what the app *writes*: a proxy app with a YAML result file and one
  that reports only on stdout share no code. Check first whether the app produces a
  result file at all; if it does not, the audit's evidence is the captured run log.

After porting, grep the whole `scripts/` directory for the previous app's name and
input vocabulary and confirm every remaining hit is a comment.

## STEP 7 — Selective aggregation (`dur < 100`)

Fold short events into interval counters, keep long ones as individual events:

```bash
export DFTRACER_ENABLE_AGGREGATION=1
export DFTRACER_AGGREGATION_TYPE=SELECTIVE          # UPPERCASE — see trap below
export DFTRACER_AGGREGATION_FILE=<session>/genesis_env/<system>/aggregation.yaml
```

```yaml
# aggregation.yaml — top-level keys, sequences of rule strings
inclusion:
  - "dur < 100"
```

Rule DSL (verified against the parser): fields `cat`, `name`, `ts`, `dur`, `app`, `rank`;
operators `== != >= <= > <`, `IN {a,b}`, `LIKE`, and `AND` / `OR` / `NOT`. An event is
aggregated when `inclusion` matches and `exclusion` does not. Events with `cat ==
"dftracer"` are never aggregated (metadata is always preserved).

The runner reads `GENESIS_AGG_FILE`; if it points at an existing rules file it exports
the three variables, otherwise it explicitly **unsets** them. Each run's log records
`aggregation=<type> file=<path>` so what was actually in force is recoverable from the
artifacts instead of assumed.

**Traps:**

* **Documenting aggregation is not enabling it.** Confirm the runner actually exports
  `DFTRACER_ENABLE_AGGREGATION` for the sweep you are about to launch, and confirm from
  the smoke trace that short events really were folded. A sweep whose harness never set
  the variables produces a perfectly healthy-looking unaggregated corpus.
* `DFTRACER_AGGREGATION_TYPE` is compared **case-sensitively** against `"SELECTIVE"`.
  Anything else — including `selective` — silently falls back to `FULL`, which aggregates
  *everything*. This fails loud nowhere.
* **`dur` is in the trace's time metric, which is microseconds.** Pick the threshold with
  that in mind: in HPC traces almost every event is sub-millisecond, so a threshold set at
  "1 ms" can fold essentially the entire corpus — including *all* GPU kernel dispatches
  and comm events, i.e. exactly the detail the corpus exists to capture. **Always measure
  the keep-rate per category on the smoke trace and report it at GATE 2** before
  committing; do not assume a threshold is conservative because it looks small.
* Aggregation is **capture-time only**. It cannot be applied or undone afterwards, so
  getting the threshold wrong means re-running the whole sweep.
* **The fold-count field is `dft_cnt`.** Do not infer it from `count`: POSIX
  read/write already carry `count`/`count_sum` meaning *syscall bytes*, so reading
  that as a fold count yields absurd totals (measured: "1.59 billion folded events"
  in a 44-second run) and a keep-rate that is nonsense in the direction that looks
  alarming.

### When a duration threshold CANNOT separate signal from noise

Measure the per-category duration range before trusting any threshold. If the
categories the corpus exists to capture are *entirely* below it, no threshold works
— lowering it just folds slightly less of everything.

Measured on one GPU miniapp: **every** category's `dur_max` was <= 99us — GPU
kernels 0-20us (mean 3.0), MPI `comm` 0-7us. So `dur < 100` folded **99.96%** of
`KERNEL_DISPATCH` and **99.98%** of `comm`. There is no threshold above zero that
keeps sub-20us kernels.

The mechanism that does work is a **category exclusion**, which the DSL supports
(`should_aggregate = inclusion.satisfies && !exclusion.satisfies`):

```yaml
inclusion:
  - "dur < 100"
exclusion:
  - "cat IN {KERNEL_DISPATCH,MEMORY_COPY,PAGE_MIGRATION,SCRATCH_MEMORY,collective,comm,p2p,CPP_APP}"
```

This still folds the high-volume, low-information streams (`HIP_RUNTIME_API`,
`POSIX`, `STDIO`) while keeping GPU/MPI/app events individual. Note ` IN ` needs
**spaces around it** — the parser does `trimmed.find(" IN ")` — and `inclusion`/
`exclusion` are **top-level** keys in the aggregation file.

**Assert the exclusion actually took effect, per run.** If the rule fails to parse,
dftracer silently falls back to folding everything, and the run still validates
clean while the detail is already gone irreversibly. Have the validator fail any run
in which an excluded category shows `dft_cnt > 1`, so a mis-parsed rule surfaces as
failures rather than as a quietly degraded corpus.

## STEP 8 — Dimensions

Three dimensions, and the directory tree **is** the dimension space:

| Dimension | Values |
| --- | --- |
| Input | **ALL** unique `merged.txt` argument strings — exhaustive, see STEP 1 |
| Node scale | e.g. 1, 2, 4, 8 — bounded by the queue's node limit |
| Processes per node | **CPU:** powers of two up to the core count &nbsp;&nbsp; **GPU:** powers of two up to **this machine's GPUs/GCDs per node**, which the system skill records and `flux resource list` / `scontrol show node` confirms |

Hold per-rank resources **constant** across the ppn sweep (e.g. 1 GPU + N cores per rank)
so that ppn is the only thing varying.

PAPI counter sets are **not** a dimension — they are the multiple runs needed to cover
all presets, and they collapse into one `compacted/` trace per leaf.

Total runs = `inputs x node_scales x ppns x papi_sets`. Compute this and the node-hours
**before** launching, present them, and **stop at GATE 3** (STEP 0). The sweep starts only
after the user confirms the matrix.

## STEP 9 — Directory structure

```
<session>/genesis/<system>/traces/<system>/
  <case>/                        # one per input, e.g. input_200x200x200
    nodes_<N>/
      ppn_<P>/
        raw/
          papi_<setname>/        # app traces + per-node service traces
          validation_<setname>.json
        compacted/               # verified chunks over the whole cell
        compacted_check.json
  README.md                      # the corpus summary for this system
```

The `<system>` level is repeated inside `traces/` deliberately: it keeps the corpus
self-describing if the directory is copied or archived on its own, and lets several
systems' corpora be merged under one root later without renaming anything.

## STEP 10 — Run each configuration

Per run, in order:

1. `flux submit` the service **detached**, pinned: `-N<n> -n<n> -c1
   --requires=host:<csv> --setattr=exclusive=false`, then poll its per-host pid files.
   `dftracer_service start` under `flux run` **never returns**.
2. `flux run` the app on the **same** pinned hosts, using **this system's**
   `genesis/<system>/build/<exe>`, with its working directory inside
   `genesis/<system>/dataset/` so output lands on this machine's PFS.
3. `flux submit` the service `stop` on the **same** pinned hosts, then let it flush.

Host pinning across all three is mandatory: in an allocation larger than the job, three
separate submits otherwise land on three different node subsets, and the daemons profile
the wrong nodes while the service traces come back empty.

### The same three steps on Slurm

```bash
# 1. service as a LONG-LIVED step (see below), pinned + --overlap
srun -N$N -n$N -c1 --nodelist="$SLICE" --overlap bash -c '
      "$SVC" start "$DIR"
      while [ ! -f "$FLAG" ]; do sleep 1; done
      "$SVC" stop "$DIR"
      for _i in $(seq 1 60); do [ -s "$OUT" ] && break; sleep 1; done   # wait for the flush
    ' &
SVC_PID=$!
# 2. app on the SAME slice, also --overlap
srun -N$N -n$((N*P)) --nodelist="$SLICE" --overlap --gres=gpu:4 -c$C \
     --kill-on-bad-exit=1 "$BINARY" ...
# 3. release and drain
touch "$FLAG"; wait "$SVC_PID"
```

Three Slurm-specific rules, each of which silently produces a 0-byte service trace:

* **`--overlap` is mandatory** on any step that shares nodes with another — without it
  the second step waits forever instead of co-scheduling.
* **`--nodelist=<csv>` replaces flux's `--requires=host:`** for pinning; same reason.
* **A daemon's lifetime is its job STEP.** A fire-and-forget `srun … service start` is
  reaped the instant that step exits, so the service must run as a long-lived step —
  and because `stop` only SIGINTs and returns while the daemon flushes asynchronously,
  the step must also **wait for the trace to become non-empty** before exiting.

Application output (checkpoints, per-run data) goes to the **parallel filesystem**;
dftracer traces stay in the session workspace.

**Leave the node-counter daemon a core, or the top ppn rung deadlocks.** The service
holds one core per node for the whole run. If per-rank cores are sized as
`cores_per_node / max_ppn`, the largest ppn asks for every core on the node, the service
already owns one, and the application job can NEVER be satisfied -- it sits in `S`
forever rather than failing, so the sweep silently stalls instead of erroring. Size it as
`floor((cores_per_node - 1) / max_ppn)` and keep that value CONSTANT across the ppn ladder
(constant per-rank resources is the experimental control). Measured on a 64-core node with
max ppn 8: 8 cores/rank deadlocks, 7 works.

Watch for this specifically: the symptom is a sweep whose per-run times are healthy but
whose throughput collapses, with one job stuck `S` and the service job `R` beside it.
`flux proxy <alloc> flux jobs -a` shows both.

**Scheduling:** use one queue of all pending runs sorted by node count descending
(first-fit decreasing) and dispatch the largest run that fits the moment any run returns
its hosts — no phase barriers. A phase-per-node-scale design makes a batch of eight
1-node runs all wait on whichever is largest. Host slices handed out must be **disjoint**:
two configs sharing a node contaminate each other's PAPI counters and per-host power.

**Never idle in the queue waiting for the largest node-scale.** Requesting N nodes when
fewer are free parks the sweep behind every other job while free nodes sit unused. Size
each allocation to what is **free right now**: the worker skips any node-scale larger
than its pool, so a small allocation drains the small-N work and a later, larger one
picks up the rest. Because every run is checkpointed independently, the order in which
the cells get filled has no effect on the final corpus — only on how soon it finishes.
A machine whose big-node queue is busy will therefore still make continuous progress
instead of blocking.

### Backfill the remainder — never run one job at a time

Dispatching the largest fitting run is only half the rule. Whatever nodes that run leaves
over must be **packed with smaller runs immediately, concurrently**, not held until it
finishes. Within one allocation of `A` nodes:

1. Take the largest pending run that fits in `A`. Launch it on a disjoint host slice.
2. Recompute the free remainder `A - used`. Take the largest pending run that fits in
   *that*, launch it too. Repeat until nothing pending fits the remainder.
3. Each time any run returns its hosts, repeat from step 1 with the new free set.

So a 4-node allocation runs one 4-node job, **or** a 2-node job alongside two 1-node jobs,
whichever the pending queue allows — it never runs a single 1-node job and leaves 3 nodes
idle. This is the difference between a sweep that finishes in a day and one that finishes
in a week, and it costs nothing but bookkeeping.

**When there are not enough nodes for the largest cells, do not wait for them.** Launch
every run that *does* fit and make as much progress as possible; the oversized cells stay
in the pending set for a later, larger allocation. A sweep is never blocked on its widest
configuration — checkpointing makes partial progress permanently useful.

Two invariants the packer must not break:

* **Host slices stay disjoint.** Two configs sharing a node contaminate each other's PAPI
  counters and per-host power, and the corpus has no way to detect it afterwards.
* **Each run's service daemon is pinned to that run's own slice.** Concurrency multiplies
  the "daemons profiled the wrong nodes" failure — with several jobs live at once, an
  unpinned `service start` can easily land on a *neighbouring* run's nodes.

Cap concurrency at the point where per-node contention would distort the measurements:
these runs are being traced for their performance counters, so oversubscribing a node
across two runs invalidates both.

### Ask for SHORT allocations — a long request cannot backfill

Request a walltime near what the work actually needs, not the queue maximum. A job
asking for the 12-hour limit **cannot be backfilled into a shorter gap**, so it waits
for a full-length window even while nodes sit idle. Measured: with 12-hour requests, a
1-node request showed an ETA of **10 hours with a node visibly free**, because the
scheduler was reserving nodes for the *same sweep's* larger pending job and nothing
12 hours long fit in front of that reservation. Re-requesting the identical work at
2 hours dropped it to ~1.4 hours and it started almost immediately.

This costs nothing: every run is checkpointed independently, so an allocation
expiring mid-sweep loses only what was in flight, and the driver simply requests
another and resumes. Size the request from the **longest single run** plus margin.

### Partition parallel drivers, do not just run several

One driver holds one allocation at a time, so the other node-scales sit idle. Running
several gets them all in flight — but they must own **disjoint work**, because a run is
only checkpointed *after* it validates, so two unscoped drivers can both pick the same
pending run, execute it simultaneously, and write into the same `raw/` directory,
corrupting both copies.

Partition along a dimension of the matrix and give each driver **its own state file**:

* by **node scale** (the natural split — it also matches the allocation size), and
* by **ppn** within a scale, when two queues are usable at once and one scale's work
  should run in both.

Size the set so the concurrent requests fit the queue: `1 + 2 + 4 = 7` nodes on a
7-usable-node queue. If a second queue exists that only fits small jobs, give it the
small-node work — it uses capacity the other drivers cannot reach, and removing that
request from the main queue measurably improves the remaining jobs' ETAs.

Verify no two live drivers share a state file before walking away — read each
process's own environment rather than trusting the launch commands:

```bash
for p in $(pgrep -f genesis_driver.sh); do
  tr '\0' '\n' < /proc/$p/environ 2>/dev/null | grep ^GENESIS_STATE=
done | sort | uniq -c | awk '$1>1{print "DUPLICATE SCOPE - WOULD RACE: "$2}'
```

**Kill driver processes by recorded PID, never with a broad `pkill -f` pattern.** A
pattern like `pkill -f genesis_driver.sh` also matches your own monitoring shells
whose command line merely mentions the script, killing them too.

```bash
free_n=$(flux resource list -o "{state} {queue} {nnodes}" \
         | awk -v q="$QUEUE" '$1=="free" && $2 ~ q {s+=$3} END{print s+0}')
want_n=$ALLOC_N; [ "$free_n" -gt 0 ] && [ "$free_n" -lt "$ALLOC_N" ] && want_n=$free_n
```

### Request SHORT allocations — a long one cannot backfill

Size the allocation's **time limit** to the work, not to the queue maximum. A request as
long as the queue allows cannot be backfilled into the gap before a reservation, so it
waits for a full-length window even when nodes are free right now. Worse, your own larger
pending job reserves nodes and starves your smaller one: measured on a 7-node queue, 12h
requests left a 1-node job with a **10-hour ETA while a node sat visibly idle**, and
re-requesting the same work at 2h started it almost immediately.

Every run is checkpointed independently and the driver resumes across allocations, so a
short allocation loses nothing when it expires. Pick a limit a few times the longest
single run, never the queue ceiling.

### Split work across every queue the account may use

Node-scale is not the only disjoint axis — **ppn is one too**. When two queues are usable
but one is too small for the large node-scales (a 2-node queue beside an 8-node one), give
each queue its own driver over a different slice of the *same* node-scale: one takes
`ppn 1 2`, the other `ppn 4 8`. Their leaves (`.../ppn_1` vs `.../ppn_4`) never overlap.

The invariant is **one driver per disjoint slice, each with its OWN state file**. Two
drivers sharing a slice or a state file is the failure to avoid: a run is checkpointed
only AFTER it validates, so both can pick the same pending run, execute it concurrently,
and write into the same `raw/` directory — corrupting both copies.

**Checkpoint** every run that passes validation to a state file, keyed by
`(input, nodes, ppn, set)`, and skip those on resume. If the queue's time limit is
shorter than the sweep, compute a deadline from the worker's own start time plus the
allocation budget, stop dispatching before it, exit with a distinct code, and let the
driver request a fresh allocation and resume.

### STEP 10a — CHECKPOINT / RESTART: never redo finished work

A sweep spans days, many allocations, several driver generations and at least one
harness restart. The checkpoint is the ONLY thing that makes that survivable. Treat it
as the sweep's database, not as a log.

#### The checkpoint file

One append-only text file per worker-group. One line per run, first field the KEY:

```
<case>_N<nodes>_ppn<ppn>_<papiset> ok dt=<seconds>
<case>_N<nodes>_ppn<ppn>_<papiset> SKIPPED <reason>
```

Rules that make it trustworthy:

* **A line is written ONLY after content validation passes.** Never on `rc==0` alone --
  a run killed during trace flush exits 0 with zero usable traces. Checkpoint after
  `genesis_validate.py`, never before.
* **The key is the full coordinate** `(case, nodes, ppn, papi_set)`. Anything coarser
  cannot express "this cell is done for set flop_p2 but not cache_p1".
* **Append, never rewrite.** A retried run appends a SECOND line with a different `dt=`.
  That is intentional history, and it is why counting must dedup (below).
* **`SKIPPED` is a first-class outcome**, not a failure: it marks work that is
  structurally impossible (STEP 10b) so no future worker ever retries it.

#### Resume = rebuild the pending set from the checkpoint

At worker startup, build the work list by differencing the full matrix against the
checkpoint. Never carry a pending list across allocations:

```bash
for N in "${NODE_SCALES[@]}"; do for nx in "${INPUTS[@]}"; do
  for P in "${PPNS[@]}"; do for e in "${PAPI_SETS[@]}"; do
    key=$(run_key "$nx" "$N" "$P" "${e%%:*}")
    grep -q "^$key " "$STATE" 2>/dev/null && continue   # done OR skipped -> never redo
    echo "$N $nx $P ${e%%:*} ${e#*:}"
done; done; done; done
```

`grep -q "^$key "` with the trailing space is deliberate: it anchors the whole key so
`..._flop_p1` cannot match `..._flop_p10`.

#### Counting progress correctly

```bash
# CORRECT -- distinct keys, skips excluded
grep -v SKIPPED "$STATE" | awk '{print $1}' | sort -u | wc -l
# WRONG -- over-reports, because retries add lines with different dt=
sort -u "$STATE" | wc -l
```

Report against the REACHABLE total (`matrix_size - skipped`), and say which you used.
A sweep that quotes line counts will silently claim more progress than it has.

#### Never let two workers share one checkpoint

The pending set is computed ONCE at startup, so two concurrent workers on one file pick
the same keys and destroy each other's traces (STEP 10b). Enforce it structurally:

* partition by CASE -- disjoint checkpoints AND disjoint `traces/<system>/<case>/`; or
* serialise with `flux batch --dependency=afterany:<prev>` so only one is ever runnable.

#### Merging checkpoints when the partitioning changes

Regrouping mid-sweep (per-case -> per-group -> one pool) is normal. Merge by UNION and
rebuild at worker STARTUP, never at submit time:

```bash
cat "$G"/artifacts/state_* 2>/dev/null | sort -u > "$G/artifacts/state_all.new"
mv "$G/artifacts/state_all.new" "$G/artifacts/state_all"
```

Doing it at submit time is a real bug: the job then sits in the queue while other workers
keep validating cells, and it starts with a stale view and re-runs finished work. Rebuild
inside the worker script, as its first action.

#### Reconciling failures against the checkpoint

A failure record is NOT an outstanding item. Most failures are transient -- boundary
kills, orphan races -- and succeed on a later retry:

```bash
for k in $(awk '{print $1}' artifacts/failures | sort -u); do
  grep -qh "^$k " artifacts/state_* || echo "$k"     # genuinely outstanding
done
```

Measured on one sweep: 201 distinct failing keys, **182 already self-healed**; only 19
were real. Reporting the raw failure count would have overstated the problem tenfold.
Equally, a key that fails repeatedly and NEVER appears in a checkpoint is a real defect
(see the impossible-cell case in STEP 10b) -- distinguish the two before acting.

#### What a restart must re-derive, and what it must not

| re-derive every restart | carry over |
| --- | --- |
| pending set (from the checkpoint) | the checkpoint itself |
| host list, deadline, MAXOBS seed | validated traces on disk |
| free-node count / allocation size | `SKIPPED` determinations |

A restart that trusts anything else -- an in-memory pending list, a submit-time snapshot,
a log tail -- will either redo finished work or skip unfinished work.

### STEP 10b — Making the sweep actually FINISH (the part that costs days)

A sweep is thousands of runs over many hours. Almost all lost time comes not from the
science but from the *harness surviving*, and from the queue. Every rule below was paid
for in stalled hours.

**ONE worker per checkpoint file. Never two.** A worker builds its pending list ONCE at
startup by reading the state file. Two workers sharing one state file therefore pick the
SAME keys and launch them into the SAME leaf directory: the leaf ends up with
`app trace count 32 != expected 8`, half-written `.pfw.gz` files that fail
`Compressed file ended before the end-of-stream marker`, and wasted node-hours. Partition
by CASE (disjoint state files AND disjoint `traces/<system>/<case>/` subtrees) or
serialise with a dependency chain. This single mistake produced the large majority of one
sweep's ~500 failure records.

**Do NOT run the driver as a login-node process.** A driver that holds allocations with
`flux alloc` needs a live client; login sessions get torn down, and every teardown stalls
the sweep until a human notices. Observed: four separate multi-hour stalls. Worse, the
allocation OUTLIVES the dead driver and keeps dispatching — an *orphan worker* that then
races the replacement driver. Symptom: more allocations than drivers, and jobs with no
`flux-job attach` client.

**Use a dependency chain of batch jobs instead.**

```bash
prev=""
for i in $(seq 1 "$DEPTH"); do
  dep=""; [ -n "$prev" ] && dep="--dependency=afterany:$prev"
  id=$(flux batch -q "$Q" -N"$N" -t "$T" --job-name=genesis-$grp \
        --output="$G/artifacts/batch_${grp}_{{id}}.log" $dep "$G/worker_$grp.sh")
  prev="$id"
done
```

`flux batch` needs no attach client, so nothing a login session does can kill it; and
`afterany` guarantees exactly one runnable worker per group, which is the one-worker rule
above enforced by the scheduler rather than by hope. Note `{{id}}` — flux uses mustache
templating; `%j` is Slurm syntax and is NOT expanded, so every job silently overwrites one
log file.

**Do not rely on cron.** On multi-login-node sites `crontab` is NODE-LOCAL and `crond` may
not even run on the node you happen to land on. A supervisor installed from one login node
is simply absent from the next.

**Kill launchers by PID, and remember other login nodes.** `pkill -f <pattern>` also
matches the shell running your own script and kills it mid-edit. Collect PIDs first, then
`kill` them. And a detached launcher left on ANOTHER login node keeps submitting into the
same flux instance: if jobs keep appearing with no local process to explain them, that is
where they come from.

**Pick the allocation SHAPE from a probe, never from intuition.** Schedulability is not
monotone in size, and the walltime matters more than the node count:

```bash
for spec in "256 1h" "64 1h" "32 2h" "16 1h"; do
  set -- $spec; flux submit -q "$Q" -N$1 -n$1 -t $2 --job-name=probe hostname
done            # then look at which ones actually START
```

Measured on one machine on one day: **N=64 t=1h started instantly while N=32 t=2h never
scheduled at all.** A wide-but-SHORT job backfills into gaps a long job can never fit.
Re-probe when throughput drops — the answer changes with machine load, and a queue that
worked yesterday may not dispatch for your account today (probe a second queue before
concluding the machine is simply full).

**Size allocations ADAPTIVELY, with a floor.** A rigid "always request exactly N" is right
only on an empty machine; on a full one it queues forever behind capacity that never
appears (observed: 5 hours of zero progress). Take what is free, but never accept fewer
nodes than the SMALLEST pending run needs, or the allocation can dispatch nothing.

**Watch what the ladder leaves behind.** Smallest-first ordering drains N=1/2/4 quickly and
leaves a long tail of the widest rung, where concurrency is `pool_nodes / max_scale`. Once
only N=8 remains, an 8-node grant runs ONE job at a time — that is the moment to ask for a
much larger pool, not at the start.

**Deadline guard: `MAXOBS * factor + headroom`.** Factor 2 wastes most of a short grant;
factor 1 keeps dispatching until the longest run barely fits and the allocation then kills
whatever is still in flight (observed: 16 runs cancelled at one boundary). Those killed
runs are exactly the truncated-trace "failures" — they self-heal on retry, but the compute
is gone. On a 1 h grant with ~20 min runs, expect to trade one for the other and say which
you chose.

**Some cells are structurally IMPOSSIBLE — detect them, do not retry them.** If ranks
exceed the decomposable units of the problem (e.g. a 16-zone mesh on 32 ranks), ranks get
zero work and the app SIGSEGVs (`rc=139`, `Zones min/max: 0 1`). Retrying can never
succeed; one sweep burned 53 attempts across 9 PAPI sets with 0 recoveries. Compute the
per-case limit up front, mark those keys `SKIPPED <reason>` in the state file so the worker
skips them, and state the reachable total as `total - skipped` in the README.

**Count DISTINCT KEYS, not lines.** A retried run appends a second line with a different
`dt=`, so `sort -u` over whole lines over-reports progress. Always
`awk '{print $1}' | sort -u | wc -l`, and exclude `SKIPPED`.

**Reconcile failures against the checkpoint, never a log tail.** Most "failures" are
transient (boundary kills, orphan races) and later succeed. The only number worth
reporting is *keys that failed AND are still absent from the state file*. In one sweep 182
of 201 failing keys had already self-healed.

## STEP 11 — Validate every run (a run is not done until its data is checked)

`rc == 0` is **not** evidence. A run killed during trace flush exits 0 with **zero
traces** — observed, and it would have been silently accepted. Assert:

* app trace count `== nodes * ppn`, and the ranks recovered from the `PR` metadata form
  exactly `{0 .. nranks-1}` — no missing, duplicate, or extra rank
* the `SH` command-line metadata in every trace matches this cell's arguments
* every trace has a `start` **and** a trailing **`end`** event — `end` is dftracer's
  completeness marker, so file size proves nothing
* required categories present, and `end.used` confirms the layers actually engaged
* PAPI counters present **equal** those requested, all with `multiplex == 0`
* one service trace per **distinct host**, carrying `sys`/`io`/`net` and variorum power

**Count failures from the state/failures files, never from a log tail.** Driver logs
accumulate output across restarts and partition changes, so a tail can show `FAIL` lines
naming run keys or PAPI sets that no longer exist. Reconcile against the checkpoint: a
key absent from the state file is genuinely outstanding, and one present in it passed
validation.

Separately audit that the app solved the right problem (parse its own result files, not
just its exit code): problem size, rank count, and a finite result. Do **not** invent
convergence thresholds — many proxy apps stop at a fixed iteration cap, so a "large"
residual is expected, not a failure. Inventing one manufactures failures out of healthy
runs; verify what the app's iteration policy actually is before judging its output.

**This audit is PFS-local.** Application output lives on the machine's parallel
filesystem, and Lustre mounts are usually *not* shared between systems even when the NFS
workspace is (e.g. `/p/lustre5` on one machine, `/p/lustre1,2` on another). Run the
science audit where that PFS is mounted; from elsewhere the symlink dangles and every run
looks like "no result file" while the traces themselves read perfectly.

## STEP 11a — The back half is a PIPELINE, not a phase

STEPs 12 and 13 read like terminal phases. **They are not.** Running them only after the
last cell lands is the single biggest avoidable delay in a sweep, and it fails in three
ways that all cost real time:

* the raw corpus grows to its full size on the PFS before anything is reduced, so the
  sweep can hit a quota or a full filesystem at the very end — with nothing published;
* compaction of the whole corpus in one go is hours of serial work the sweep could have
  absorbed for free while it was queueing anyway;
* nothing is usable by anyone until everything is done, so a sweep that is 90 % complete
  delivers **zero** value.

Run the back half as a **continuously-draining pipeline beside the sweep**. The unit is
the **leaf** (`<case>/nodes_<N>/ppn_<P>`), and it moves through four stages:

```
  sweep  ->  [SEALED]  ->  compact+verify  ->  publish+verify  ->  perms  ->  [DONE]
```

Each stage is an independent, idempotent, resumable worker that scans for its own input
state and claims work. None of them ever blocks the sweep, and the sweep never waits for
them.

### A leaf is SEALED only when it is provably complete

This is the gate the whole pipeline rests on, and getting it wrong produces a
**verified, published, permanently-wrong** cell that no later step can detect.

A leaf is sealed when its **distinct validated keys equal its reachable keys** — every
PAPI set for that `(case, nodes, ppn)`, counted from the checkpoint (STEP 10a),
excluding `SKIPPED`:

```bash
reachable=$(( $(wc -l < "$G/papi_sets.txt_nocomments") ))   # sets minus any SKIPPED here
have=$(grep -h "^${case}_N${N}_ppn${P}_" "$G"/artifacts/state_* 2>/dev/null \
       | grep -v SKIPPED | awk '{print $1}' | sort -u | wc -l)
[ "$have" -eq "$reachable" ] || exit 0      # NOT sealed -- do not touch this leaf
```

Never seal on "the directory looks populated" or on a wall-clock timeout. A leaf with a
key still in retry back-off (STEP 10b) is **pending**, not done — compacting it yields a
cell that is internally consistent and missing a PAPI set, and `--verify` passes happily
because the hash only checks in == out.

### One claim per leaf per stage

Same rule as STEP 10b's one-worker-per-checkpoint, for the same reason: two compactors on
one leaf share a staging directory of hardlinks and one `compacted/` output, and destroy
each other. Claim with an atomic `mkdir` (NFS-safe; `test -f && touch` is not):

```bash
mkdir "$G/artifacts/compact_claims/$leafkey" 2>/dev/null || exit 0   # someone else has it
```

Record the stage outcome in a **per-stage state file** (`state_compact`, `state_publish`),
keyed by leaf, so a restart re-derives its pending set exactly like the sweep does.

### The pipeline must not steal the sweep's nodes — or its counters

Compaction is I/O-heavy and `dcp` is deliberately parallel. Both compete with the sweep.

* **Never run a pipeline stage inside a sweep allocation's host slice.** A concurrent
  compaction on a node that is running a traced cell contaminates that cell's PAPI
  counters and per-host power, and the corpus has no way to detect it afterwards — it is
  the same disjoint-slice invariant as STEP 10, extended to the back half.
* Give the pipeline its **own small allocation** (or the small/short queue the sweep is
  not using). 1 node x 16 processes is enough for `dcp`; compaction parallelises across
  leaves, not within one.
* Size its walltime to a **wave of leaves**, not to the corpus — short requests backfill,
  long ones park (STEP 10b).

### Publish in WAVES, not per leaf

`dcp` amortises over many files; invoking it once per leaf on hundreds of small leaves is
mostly startup. Accumulate compacted-and-verified leaves and publish them in waves (a few
tens of leaves, or a size threshold), one `dcp` per wave.

The verify-then-delete rule of STEP 13 is unchanged and applies **per wave**: compare
regular-file bytes and counts plus `dcmp` while the source still exists, and only then
remove that wave's source. Never `du` across the PFS -> NFS boundary.

### A partially-published corpus must SAY it is partial

Streaming publication means the archive holds an incomplete corpus for most of the
sweep's life, and a consumer cannot tell that from the tree. Write a
`STATUS.md` beside the corpus on the **first** wave, stating that it is in progress, the
target matrix size, and the leaves published so far; refresh it each wave. Write the real
`README.md` (STEP 12) only at the end, and delete `STATUS.md` in the same step so the two
never coexist. A corpus with a README is complete; one with a STATUS.md is not.

### Re-publishing after a late retry

A cell retried *after* its leaf was published leaves the archive stale. Handle it
explicitly rather than hoping it does not happen: a leaf that gains a new validated key
after publication is **unsealed** — its `state_compact`/`state_publish` entries are
removed, and it flows through the pipeline again, overwriting the archived copy. Record
every re-publish; a silent one is indistinguishable from corruption.

### Permissions are cheap and idempotent — run them every wave

`chgperm.sh` on the `<app>/<system>` subtree is fast and safe to repeat, so run it at the
end of **each** wave rather than once at the end. That way anything published is
immediately group-readable, and a sweep that is interrupted still leaves a usable corpus
instead of a tree only you can read.

## STEP 12 — Compact INSIDE the job, then drain the leaf

**Compaction is part of running a cell, not a phase that happens afterwards.** The
worker compacts a leaf the moment that leaf's last run validates, in the SAME
allocation, and then drains it: publish, verify, delete `raw/`. A leaf is either
still filling, or it is finished and already sitting in the archive. There is no
middle state and no end-of-sweep backlog.

Deferring compaction to the end is the single most expensive mistake in this
pipeline. Measured, when it was left until last: **188 leaves, 6-10 days** of serial
compaction discovered after the runs were done, on a machine whose nodes had since
drained — plus 1.6 TB of `raw/` held the entire time because nothing could be
deleted until it was compacted. Compacting in-job costs the same CPU but overlaps it
with work that is already holding the nodes.

### The per-leaf drain sequence

Run this the instant a leaf seals (every reachable PAPI-set key validated):

```
compact -> verify -> publish -> verify published -> rm -rf raw/ -> chgperm
```

**Delete `raw/` only when TWO verified copies exist** — the local `compacted/` and
the archived copy. `raw/` is the only thing a bad compaction can be redone from, so
deleting it on the strength of a local check alone leaves one unverified copy of an
irreplaceable artifact. The gate is all of:

* `dftracer_split --verify` reported `Verification: PASSED` (grep with **`grep -a`**)
* the content assertion passed: service categories present, `hosts >= nodes`
* the archived copy matches on **regular-file count and bytes** (never `du`)
* the archive copy is readable back (the check script runs against the DESTINATION)

Then, and only then, `rm -rf` that leaf's `raw/`. Record the stage reached per leaf
(`validated` / `compacted` / `published` / `drained`) in the state file, so an
interrupted sweep resumes at the right step instead of redoing or, worse, skipping one.

### Compaction is embarrassingly parallel — never walk the leaves serially

Each leaf reads only its own `raw/` and writes only its own `compacted/`. If a sweep
ever does end up with a compaction backlog, deal the work across concurrent tasks
keyed off `FLUX_TASK_RANK` (16 tasks on 2 nodes measured **~20x** faster than serial).
Two rules that are not optional:

* **Deal configs round-robin, not in contiguous blocks.** Cost correlates strongly
  with the config, so blocks hand one task every expensive cell while the rest idle.
* **`-o exit-timeout=none` on the `flux run`.** Flux's default kills every task 30 s
  after the FIRST one exits. With uneven chunks the cheap tasks finish early and
  legitimately — and take the whole job down with them. Measured: a run died at
  39/188 reporting `FAILED` while every task that had finished had exited 0.

Never run two compaction jobs over one corpus concurrently: they deal the same chunk
list, so rank 0 in each picks the same leaf and both `rm -rf` its `compacted/`. Chain
allocations — wait for the running job to be *gone*, then start the next.

`dftracer_split` **does not recurse**: it globs `*.pfw*` in exactly the directory given.
The traces live one level down in `raw/papi_*/`, so pointing it at `raw/` finds nothing
and exits 1. Stage a flat directory of **hardlinks** (same filesystem, no copy) and
compact that. Per-node service traces are all named `service_<host>.pfw.gz` and **collide
across the PAPI sets** — splice the set name in when staging.

When grepping split logs for `Verification: PASSED`, use **`grep -a`**: these logs can
contain a non-UTF8 byte, and plain `grep` then treats the file as binary and reports no
match, making a perfectly verified leaf look unverified.

Use `--verify` (event-ID hash in == out) and then separately assert the compacted output
contains the **service** categories, not just the app ones — otherwise a staging bug
silently drops every node-level counter while the split still reports success.

A leaf legitimately spans more hosts than `nodes`: it aggregates several runs and the
scheduler may place each on a different slice. Assert `hosts >= nodes`, not equality.

Finish by generating `genesis/<system>/traces/<system>/README.md` describing what the traces are,
the dimensions, the corpus statistics (events by category and by dimension, PAPI
coverage, power samples, size), a per-cell inventory, the validation criteria, and the
**caveats** — including any layer that is silently unavailable on this machine.

## STEP 13 — Publish the corpus to the shared archive

A finished corpus does not live in the session workspace. It is MOVED to the shared
group archive so other people and other systems' sweeps can use it.

### Destination layout

```
/usr/workspace/genesis-wisdom/dftracer-traces/<app>/<system>/<case>/nodes_<N>/ppn_<P>/
```

`<app>` is the ice4hpc application name (`laghos`, `minife`, ...), `<system>` the machine.
Systems sit SIDE BY SIDE under one app (`laghos/matrix`, `laghos/tuolumne`), which is
exactly why the corpus keeps its own `<system>` level (STEP 9) -- the tree drops straight
in. **Check the existing layout before copying** and mirror it; do not invent a variant
(there is already a stray misspelled app directory there from someone doing exactly that).

### Move it with mpifileutils, not cp

The corpus is hundreds of GB and hundreds of thousands of files. Use `dcp` on **1 node
with 16 processes** -- serial `cp`/`rsync` takes hours on this shape:

```bash
module load mpifileutils
flux run -N1 -n16 dcp /p/<pfs>/$USER/<app>_genesis_traces/<system> \
                      /usr/workspace/genesis-wisdom/dftracer-traces/<app>/<system>
```

`dcp` COPIES; there is no `dmv`. A move is copy -> verify -> delete, and the delete is
gated on the verification below, never on the copy's exit status.

### Verify BEFORE deleting the source

This is a CROSS-FILESYSTEM move (PFS -> NFS), so never compare with `du`: directory
inodes differ between filesystems and produce phantom mismatches. Compare regular-file
bytes and counts, and confirm with `dcmp` while the source still exists:

```bash
S=$(find "$SRC" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}')
D=$(find "$DST" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}')
[ "$S" = "$D" ] || { echo "MISMATCH -- do NOT delete"; exit 1; }
flux run -N1 -n16 dcmp "$SRC" "$DST"        # content comparison
```

Only when bytes, file counts and `dcmp` all agree may the source be removed. **Also
confirm the destination directory was empty or held a different `<system>` first** --
a byte total that comes back LARGER than the source means you are comparing against
somebody else's data already in that tree, not a bad copy.

### Fix permissions with the archive's own script

The archive is group `genesis-wisdom` with setgid directories. Do not hand-roll
`chmod`/`chgrp`; run the script the archive provides, pointed at what you just added:

```bash
/usr/workspace/genesis-wisdom/chgperm.sh \
    /usr/workspace/genesis-wisdom/dftracer-traces/<app>/<system>
```

It adds group `rw` to files, group `rws` to directories, strips other's `wx`, re-adds
group `x` only where the user has it, and `chgrp`s everything to `genesis-wisdom`.
Run `newgrp genesis-wisdom` afterwards if your shell still has the old primary group.

### Order of operations

This order is **per wave of sealed leaves** (STEP 11a), repeated continuously alongside
the sweep — not once at the end:

1. seal: every reachable PAPI-set key for the leaf is validated in the checkpoint
2. compact + `--verify` the leaf (STEP 12), assert service categories and `hosts >= nodes`
3. `dcp` the wave to the archive
4. verify bytes/counts/`dcmp` **while the source still exists**
5. delete that wave's source only then
6. `chgperm.sh` on the `<app>/<system>` subtree
7. refresh `STATUS.md` so the archive declares itself incomplete

**Staging for the split must live on the SAME filesystem as `raw/`.** The stage is a
tree of hardlinks, and once a corpus is moved to the PFS with a symlink left behind in
the workspace, a stage under `$WS/tmp` is a different device: every `ln` fails with
EXDEV and every leaf reports `staging mismatch 0/N`. Put the stage inside the leaf so
it follows the corpus wherever it lives.

**Decide raw-vs-compacted for the archive explicitly, and record it.** `raw/` can be
an order of magnitude larger than anything already published (measured: 1.6 TB of raw
against a 94 GB existing corpus for the same app on another machine) and the archive
is shared group space. Ask before pushing it. Whichever way it goes, say so in the
README — a corpus that states what it does NOT contain is trustworthy; one that
silently omits a level looks complete and is not.

Once the last leaf drains, and only then:

8. generate `README.md` INSIDE the corpus from the full inventory, publish it, and delete
   `STATUS.md` in the same step

## Diagnosing a crash in the tracing path

GPU profilers (rocprofiler, CUPTI) call dftracer back from **their own threads**, so
crashes there are races. Three things make them hard to see, and all three cost real time
if you learn them the slow way:

* **gdb hides the bug.** Running under a debugger slows startup enough to close the
  window; a crash that is 3/3 reproducible bare can be 0/3 under gdb. Do NOT conclude the
  bug is gone. Take a **post-mortem core** instead: `ulimit -c unlimited`, run bare, then
  open the core. Cores may not be named `core*` — look for `<host>-<exe>-<pid>.core`.
* **The system gdb may not read the DWARF.** A clang-built library emits DWARF 5 and an
  older gdb answers `Cannot handle DW_FORM_strx1`, giving you a symbol-only backtrace with
  no file:line. Use the vendor debugger (`rocgdb` on ROCm) to get real line numbers,
  argument values and locals — which is where the actual answer is.
* **Read the ARGUMENTS, not just the stack.** The frame that identifies the bug is usually
  the one whose arguments are impossible. A garbage `process_id`, a `rank` of -1, or a
  null `config` says *which lifetime window* you are in far more precisely than the call
  chain does. Constructor-initialiser defaults appearing in a live call (`rank=-1`) mean
  the object is published but not yet initialised; garbage means freed.

**A runtime env var cannot disable a profiler that registers at LOAD time.** rocprofiler
discovers dftracer through the exported `rocprofiler_configure` symbol before `main()`,
so `DFTRACER_ENABLE_HIP_TRACING=0` is inert — the callbacks still fire. Only a rebuild
with the feature `OFF` removes them. Do not use that variable to A/B whether GPU tracing
is implicated; you will "prove" it is not.

**Verify a rebuild actually happened** before re-testing a fix: compare the timestamp of
the installed `libdftracer_core.so.*` against now. And remember `pip uninstall dftracer`
wipes the shared `dftracer/bin` prefix, taking `dftracer-utils`' binaries with it —
reinstall utils after any core rebuild, or compaction fails later with no obvious cause.

## Failure classes to check on every new system

These are **classes** of silent failure, not facts about any particular machine. Check
each one on the system you are on, and **record the answer in that machine's system
skill** so the next sweep does not re-derive it. If no system skill exists yet, create
one.

* **GPU hardware counters** may be unavailable: ROCProfiler logs `could not be locked for
  profiling ... (capability SYS_PERFMON)`. GPU *activity* tracing still works; say so
  explicitly rather than letting a reader assume GPU PMC data exists.
* **variorum**: `DFTRACER_BUILD_VARIORUM=AUTO` can find a system RPM that returns
  `_ERROR_VARIORUM_UNSUPPORTED_PLATFORM` and yields zero power events. Use
  `DFTRACER_BUILD_VARIORUM=ALWAYS` and verify a `gpu`/`power` event actually lands.
* **service teardown abort** (`corrupted size vs. prev_size in fastbins`) from two
  `librocm_smi64` ABIs in one process is post-flush and costs no data;
  `HWLOC_COMPONENTS=-rsmi` does not avoid it.

* **NVIDIA machines**: the site-default `/usr/local/cuda*` may contain **no CUPTI** at
  all, in which case the build silently disables GPU tracing — pin `DFTRACER_CUDA_PATH`
  at a toolkit that really has `include/cupti.h` and confirm the
  `-- [DFTRACER] found CUPTI at ...` configure line. CUPTI also allows only **one
  profiling client per process**, so it cannot share a run with `nsys`/`ncu`/a framework
  profiler. See [[software-cupti]].
* **variorum can be worse than useless**: on a two-socket node a variorum-enabled
  `dftracer_service` **pinned to one core** makes variorum's init see
  `cores(1) mod sockets(2) != 0` and call `exit()`, killing the whole daemon while its
  pid file still looks healthy. Where power is unreadable anyway (root-only
  `/dev/cpu/*/msr`, no `msr_safe`, mode-0400 RAPL `energy_uj`), set
  `DFTRACER_DISABLE_VARIORUM_POWER=1` and sample **GPU** power via NVML/rocm-smi
  instead. Record `tracing.power: off` for that machine in `systems.yaml` so the next
  session does not re-derive it.
* **The PAPI ceiling is per-machine and is not portable.** A plan that reuses another
  cluster's preset count will silently multiplex *and drop* counters here: the number of
  presets exposed, how many fit at once, and which are derived all vary per CPU. Re-derive
  with STEP 6 every time and write the result into the system skill.

## Worked references — read the neighbouring sweep before deriving anything

Every sweep lives in its own execution folder:

```
$PROJECT_ROOT/workspaces/<app>/<session>/genesis/<system>/
```

**Before planning a sweep on a machine that has already hosted one, read that folder.**
Its `PLAN.md`, `env.sh`, `papi_sets.txt`, `aggregation.yaml` and
`traces/<system>/README.md` carry every machine-level fact already paid for — the
probe-verified PAPI partition, the toolchain module order, the usable queue, the
per-rank core count, and any dftracer patches that sweep needed (`*.patch` beside
`env.sh`). Those are properties of the **machine**, not the app, and re-deriving them
costs hours and risks getting them wrong.

What still must be re-derived **per app**, never copied:

* the input list and its `merged.txt` / `maps.sh` reconciliation
* the build — every app has its own dependency stack and its own link quirks
* the **tracing overhead factor and aggregation keep-rate**, which vary by orders of
  magnitude between apps (a miniFE run and a laghos run differ ~1000x in event density,
  so a threshold that is conservative for one can fold the entire corpus of the other)
* the science audit — what the app writes, and where

What is safely reused from a same-machine sweep: the PAPI partition, `env.sh`'s module
set and ordering, the queue/authorisation findings, the per-rank core arithmetic, and
dftracer source patches.

## Related

[[software-papi]] · [[software-cupti]] · [[software-rocm]] · [[dftracer-trace-utils]] ·
[[flux-alloc]] · [[slurm-alloc]] · [[system-tuolumne]] · [[system-tioga]] · [[system-matrix]] · [[system-dane]]

## The resubmission/teardown path is where sweeps die — and it only runs when something ends

Four separate stalls in one sweep were all in code that executes **only at the end of
an allocation or the end of a cell**. That code never runs during a healthy dispatch,
so it is untested precisely until it matters, and every failure looks like "the
cluster is busy" rather than a bug. Budget for testing it deliberately.

Each of these cost between 5 and 30 hours of idle machine time:

### 1. The pending-counter must fail LOUD, not empty

A caps file whose `#` header also contained the delimiter made the counter's parser
raise; it printed nothing; `[ "" -gt 0 ]` was false; the driver then looped forever
submitting **no work at all while looking perfectly alive**. Skip comments when
parsing, and refuse to run if the count is not a number:

```bash
remaining=$(pending_total)
[[ "$remaining" =~ ^[0-9]+$ ]] || { echo "FATAL: counter returned '$remaining'"; exit 1; }
```

### 2. Deadline detection must fail SAFE

```bash
DEADLINE=0
_e=$(squeue -h -j "$SLURM_JOB_ID" -o %e)     # can return empty
have_time() { [ "$DEADLINE" -eq 0 ] && return 0; ... }   # <-- "forever" on failure
```

`squeue` returned nothing, so the pack assumed infinite time, dispatched a ~1680 s
cell right up to a 12 h wall, and Slurm TIMEOUTed the job mid-cell — killing the
successor-submission with it. **A sweep then sat idle for a week.** Fall back to
`scontrol show job ... EndTime`, and if that fails too, assume the requested walltime
from now. Being wrong-short ends the allocation early and chains; being wrong-long
loses the whole successor.

Also size `RESERVE` to **exceed the longest single cell**, not just teardown. At
600 s against a 1350 s cell the job dies mid-cell every time.

### 3. Self-chaining must exclude ITS OWN job from the cap

```bash
live=$(squeue -h -u "$USER" -o %j | grep -c '^mypack_')
[ "$live" -ge "$cap" ] && return          # cap=1 -> live=1 -> NEVER resubmits
```

The chaining job is itself still live while it runs its own chain step, so with a cap
of 1 it always declines. Exclude `$SLURM_JOB_ID`. Symptom in the log is explicit and
easy to miss: `chain: 1/1 jobs already live; not resubmitting`.

### 4. Chaining off `SLURM_JOB_PARTITION` fans out across queues

Releasing jobs made each one chain a replacement into *its own* partition, so
cancelling to reduce the queue quietly repopulated it in two partitions at once. Pin
the chain target explicitly.

Add `--signal=B:TERM@900` so Slurm signals the batch script before the hard wall,
giving a trap a chance while the script is still healthy — but treat that as
belt-and-braces, not the primary mechanism. A trap can be starved when the script is
blocked in `wait` on background children.

## The validator is O(events) and that WILL become the bottleneck

A per-run validator that does `json.loads` on every line does not survive a real
corpus. Measured: **19.6 s for one 48 MB / 6.7 M-event trace**, and a single leaf
holds 66 of them — ~20 minutes to validate ONE cell, with the largest cells carrying
100x the events. A pack sat **4 hours inside the validator with no Slurm step
running**, which reads exactly like a cluster problem and is not one.

Almost every event is an app/MPI record you only need to COUNT by category. Pull the
category out with a cheap string slice and pay for `json.loads` only on lines that
carry semantics (dftracer metadata, `papi` records, anything with `dft_cnt`):

```python
_CAT = '"cat":"'
i = line.find(_CAT)
if i < 0: continue
j = line.find('"', i + len(_CAT))
cat = line[i + len(_CAT):j]
if cat in want_full or '"dft_cnt"' in line:
    obj = json.loads(line.strip().rstrip(","))
```

Measured 19.6 s -> 4.5 s with **identical event counts**. Prove equivalence on the
negative controls too (aggregation guard, wrong decomposition, wrong PAPI set), not
just on a passing case — a faster validator that stopped catching things would be
worse than a slow one.

At corpus scale even that is not enough. A cell holding **1.36 billion events** took
**57–91 minutes**, and the harness burned 12+ allocations with zero completed cells
(rc=142, 165 of 200 allocations spent) purely because the validator could not keep up
with the runs. Two more changes take it to **68 s** — a further ~60x:

1. **Substring PREFILTER before any per-field work.** The overwhelming majority of
   lines carry nothing the validator needs. One `any(needle in line)` pass rejects
   them before you even locate `"cat"`:

   ```python
   _NEEDLES = ('"dftracer"', 'multiplex', 'PAPI_', 'dft_cnt')
   if not any(nd in line for nd in _NEEDLES):
       continue          # still counted by category, never parsed
   ```

2. **One process per trace file.** Validation is embarrassingly parallel across
   files; use `concurrent.futures.ProcessPoolExecutor` and make each worker return
   **picklable partials** (counters/dicts), merging in the parent. Returning objects
   that do not pickle is the usual way this rewrite fails.

**Keep the old validator as `*_slow.py.bak` and diff their JSON output on a real
cell** — byte-identical output on a 209 MB cell is what makes a 60x rewrite safe to
trust. A validator this much faster is not a nice-to-have: it is the difference
between a sweep that converges and one that spends its whole allocation budget
re-queueing.

## Gate validation on the app's own completion marker

Validation is the expensive step, so never spend it on a run that did not finish.
Before validating, grep the captured stdout for the application's completion
marker — the last thing it prints on a healthy run — and fail the cell immediately
if it is absent:

```bash
if ! grep -q "$MARKER" "$log" 2>/dev/null; then
  echo "$key FAIL rc=$rc INCOMPLETE: no '$MARKER' marker in app output" >> "$LOGDIR/failures"
  return
fi
```

`rc=0` is NOT completion. A rank can exit cleanly after a truncated solve, and the
trace will validate as well-formed — a flawless trace of a run that never finished.
Keep the markers in one table next to the app list, e.g. `Energy` (Laghos),
`Figure of Merit` (AMG), `END` (Kripke), `Final Resid Norm` (miniFE), `MODS`
(miniVite), `Verification` (XSBench). When porting to a new app, finding its marker
is a required step, not an optional one.

## ONE state file means ONE worker — enforce it with flock

The single worst corruption class in a sweep: **two workers sharing one state file**.
Both read the same pending list, both pick the same run, both `rm -rf` the same
`raw/` directory, and each clobbers the other mid-write. The result is cells whose
traces are 100% truncated gzip but which are **checkpointed as valid**, because a
stale `validation_*.json` from the losing worker is left behind. Nothing downstream
can detect this.

A shell guard ("is another one running?") does not close the race. Take a real
exclusive lock at worker start:

```bash
exec 9>"${STATE}.lock"
if ! flock -n 9; then
  echo "=== another worker already owns $(basename "$STATE") -- exiting (guard working) ==="
  exit 0
fi
```

The lock is released automatically when the worker dies, including on a hard kill,
so a crashed worker never wedges the sweep. **Verify it is actually firing** by
counting both messages across allocation logs — "acquired" and "already owns" should
both appear; if only "acquired" ever does, the second worker is not reaching the lock.

Note the scope: this protects the *worker*, i.e. the data. It does not stop two
*drivers* from each holding an allocation — that wastes nodes but corrupts nothing.
Guard the driver separately (its own lock/name check) if allocation budget matters.

When you find corrupted cells: back up the state file first
(`state_backups/state_all.before_corrupt_removal`), remove only the affected keys,
delete their trace directories, and let them re-run. Do not repair traces in place.

## A content assertion that over-specifies fails healthy data

A post-compaction check asserted the app categories `{CPP_APP, KERNEL_DISPATCH,
MEMORY_COPY, papi}` must all be present. It failed **188 of 188 leaves** whose
compaction had otherwise PASSED verification — `verified=1`, 500-2500 chunks written,
hundreds of millions of events intact. Whether a run performs explicit host<->device
copies is a property of the physics configuration, not evidence of a broken trace.

`KERNEL_DISPATCH` is the hard proof that the GPU profiler engaged. `MEMORY_COPY` and
`PAGE_MIGRATION` are informational. **Keep the required set and the informational set
in sync across every checker** — the validator had already demoted `MEMORY_COPY`; the
compaction checker had not, and nothing connected the two until an entire corpus
reported as failed. When you relax a requirement in one checker, grep for that
category name across all of them.

Symptom to recognise: a failure record that carries `verified=1` and a healthy chunk
count is an over-strict assertion, not a data defect. Re-check the existing output
rather than recompacting terabytes.

## Kill orphaned drivers by scanning ALL processes, not recent ones

A `genesis_driver.sh` orphaned **16 days earlier** was still chaining allocations,
silently competing for the only free nodes and re-submitting jobs after every cleanup.
It survived repeated teardowns because each cleanup only looked at processes the
current session knew about.

* Scan with `ps -eo pid,lstart,args` and **check `lstart`** — a driver dated days or
  weeks back is an orphan, however healthy it looks.
* Trace a mystery allocation back to its submitter before cancelling it:
  `flux job info <id> jobspec` gives the `cwd` and the command, which names the script.
* Recurring unexplained allocations are never spontaneous. If one reappears after you
  cancel it, something is alive; find it rather than cancelling in a loop.
* **Never `pkill -f <pattern>` when the pattern appears in your own command line** —
  it matches the shell running it and kills your own session. Collect PIDs first
  (bracket the pattern, e.g. `genesis_[d]river`), then kill by PID.
* A `flock` on the worker protects the DATA; it does not stop a second driver from
  holding allocations. Guard the driver separately, and remember the lock is released
  only when every fd-inheriting child exits — a backoff `sleep` that inherited the
  descriptor will hold it after the driver is killed.

## A failing cell must back off, or it monopolises the allocation

A cell that fails is released for retry and gets re-dispatched the instant its slice
frees. With a systematic bug that is an infinite loop: measured **179 attempts at one
OOM-ing case**, and a 2-node pack stuck in a tight loop on a single truncated-trace
cell — **2.7 h of node time across 11 failed attempts**. Count failures per key and
skip past a cell after N strikes; it stays *pending*, not done. Archive the failure
log when you fix the underlying cause, so the back-off counts post-fix attempts only
and does not permanently exclude the cells the fix repairs.

## Diagnose stalls from Slurm accounting, not from the trace

`sacct -j <job> --format=JobID,JobName,State,Elapsed,MaxRSS` distinguishes the cases
that look identical from the outside:

* `CANCELLED by <uid>` — something in YOUR harness killed it (check the uid is yours)
* `TIMEOUT` — hit the wall
* `OUT_OF_MEMORY` / an `oom_kill` line — the memory cap, not the app
* steps `COMPLETED` while the batch step runs on for hours — the pack is wedged in
  post-run code, i.e. your own validator or compaction

Also check `/proc`-level: a pack with **no Slurm steps running** but a live batch step
is never waiting on the cluster.

## Two post-run amplifiers that look exactly like "the cluster is slow"

Both burn a whole allocation while **no Slurm step is running**, so `squeue` shows a
healthy job and the log says nothing. Check `sacct` for steps `COMPLETED` while the
batch step runs on for hours — that means the pack is wedged in your own code.

### The flush-wait must retest only the FAILURES, and be wall-clock bounded

A loop that re-tests every trace in the leaf until all are complete gzip streams is
quadratic in leaf size the moment one file is *permanently* truncated (a daemon
killed mid-flush can never become valid):

```bash
for _w in $(seq 1 60); do                       # 60 passes...
  find "$raw" -name '*.pfw.gz' | xargs -n1 gzip -t || sleep 1   # ...over EVERY file
done
```

Measured: one pass over a 10 GB / 130-file leaf takes **715 s**, so the full 60
passes is **~12 hours of an idle node per affected cell**. (A cell observed stalled
for 102 minutes was only ~8 passes in — the loop had not even got going.) Retest only the
files that failed, bound the whole wait by wall clock (180 s is ample — a healthy
flush completes in ~1 s), and **log which files are still truncated** instead of
silently falling through.

### The per-run validator must not parse every event

See the validator note above: `json.loads` per line cost 19.6 s for one 6.7 M-event
trace, and a leaf holds dozens. A pack sat **4 hours** inside it.

Combined, these two made big cells appear to take hours of "compute" that was
entirely bookkeeping.
