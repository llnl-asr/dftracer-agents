---
name: genesis_run
description: Sweep ANY ice4hpc application across a configuration space (input x node-scale x processes-per-node) collecting full-feature dftracer traces — PAPI, GPU (ROCProfiler or CUPTI), variorum power, node utilization, MPI — into a validated, compacted, self-describing corpus under genesis_traces/<system>/. System-aware: GPU vendor, scheduler, queue, compiler, GPU-per-node count and PAPI preset set are all re-derived per machine, never copied. Load this to run or resume a genesis sweep.
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

## STEP 1 — Resolve the app and its inputs

`merged.txt` is CSV: `machine,procs,app,"args",seconds`.

```bash
curl -sL https://raw.githubusercontent.com/llnl/ice4hpc_data/main/data/merged.txt -o merged.txt
# unique argument strings for this app, across all machines
grep ",<app>," merged.txt | sed 's/^[^,]*,[^,]*,<app>,//' | sed 's/,[0-9.]*$//' | sort -u
```

Also fetch `scripts/maps.sh` from the same repo — it declares, per app:

* `app_gpu[<app>]` — `1` means a GPU build exists
* `app_var[<app>]` and `app_var["$(key <app> <machine>)"]` — which source variant that
  machine uses (e.g. `-openmp45` for GPU, `-ref` for CPU)
* `app_exe`, `app_dir`, `app_mpi`, `app_omp`

**Reconcile the two sources before choosing.** They can disagree: for `miniFE.x`,
`maps.sh` maps tuolumne to the GPU `-openmp45` variant, while every miniFE row in
`merged.txt` comes from a `*-cpu` machine. When they disagree, say so and ask which
variant to trace — it changes the ppn ladder, the build, and roughly halves or doubles
the run count.

The 49 unique miniFE inputs are typical; a full grid over all of them is usually
infeasible. Propose a defensible subset (e.g. the isotropic cubes) with the run count and
node-hours spelled out, and let the user choose.

## STEP 2 — Find a successful session for the app

Sweeps do **not** re-annotate from scratch; they reuse the annotated **source tree** of a
session that already got the app working. The *build* is redone per system (STEP 5) —
only the annotated source is shared.

```bash
ls -d $PROJECT_ROOT/workspaces/<app>/*/          # newest first
```

A session qualifies only if all of these hold — check, don't assume:

```bash
test -f <session>/session.json                            # session exists
grep -rl DFTRACER <session>/annotated --include=*.c --include=*.cpp \
                                      --include=*.hpp --include=*.py | wc -l   # > 0
ldd <session>/<path-to-binary> | grep libdftracer_core    # actually linked
```

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
[[system-matrix]], ...). If none exists, create one as you learn the machine.

Derive and record:

| Parameter | How |
| --- | --- |
| GPU vendor/arch | `rocminfo` / `nvidia-smi`; `/opt/rocm*` vs CUDA toolkit |
| **GPUs per node** | `flux resource list` NGPUS / NNODES — this **is** the GPU ppn ladder |
| Scheduler | Flux (`flux alloc`) vs Slurm (`salloc`/`srun`) |
| Usable queue | one whose **scheduling is started** and whose node/time limits fit |
| Compiler / MPI | from the system skill; ice4hpc pins a specific `rocmcc`/`cray-mpich` |
| PAPI presets | `papi_avail` on THIS machine (see STEP 6) |

**`flux queue status` is mandatory.** A queue can accept submissions while its scheduler
is stopped; jobs then sit in `S` forever and look like contention. Observed on tioga's
`pall`.

## STEP 4 — Choose the dftracer source (vendor-dependent)

| Target | Source |
| --- | --- |
| **NVIDIA GPU system** | the CUPTI branch: `${DFTRACER_LOCAL_SRC:-/usr/workspace/$USER/dftracer-project/dftracer}`, branch **`feature/cupti`** (same commit is on czgitlab as `refs/heads/feature/cupti`) |
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

## STEP 5 — Create the system folder: env, dftracer, AND the app build

Everything belonging to one system lives under one per-system folder, so a single session
can hold corpora from several machines side by side without collision:

```
<session>/
  genesis/<system>/
    env.sh                 # modules, CC/CXX, PAPI_DIR, LD_LIBRARY_PATH — sourced by EVERY step
    aggregation.yaml       # selective-aggregation rules (STEP 7)
    venv/                  # this system's dftracer/pydftracer/dftracer-utils
    build/                 # THIS SYSTEM'S build of the annotated app
    papi_sets.txt          # the partition derived + probe-verified on THIS machine
  genesis_traces/<system>/ # the corpus
  artifacts/genesis/<system>/
```

**The app must be rebuilt per system — a binary is not portable across them.** The
annotated *source* is shared (it comes from the session found in STEP 2); the *build* is
not. Concretely, an OpenMP-target-offload binary built for `gfx942` (MI300A / Tuolumne)
will not run its kernels on `gfx90a` (MI250X / Tioga), and a CUDA build is a different
variant again. The same applies to dftracer itself: it links this machine's PAPI, MPI and
GPU runtime, so each system gets its own venv and its own install.

So a genesis run from fresh, on a system that has never been used before, is:

1. create `genesis/<system>/` and write `env.sh` pinning the ice4hpc toolchain for this
   app on this machine (NOT the site default `PrgEnv`)
2. create `genesis/<system>/venv` and install dftracer there (STEP 4), with every feature
   this machine supports compiled in
3. **build the annotated app** from the session's shared annotated tree into
   `genesis/<system>/build`, against that dftracer and this machine's GPU arch
4. smoke-test one rank and confirm from the TRACE (not the exit code) that every expected
   layer produced events — app annotation, GPU, PAPI, MPI, and the node service
5. only then derive the PAPI partition (STEP 6) and start the sweep

Verify the build actually picked up dftracer rather than trusting `make` succeeded:

```bash
ldd genesis/<system>/build/<exe> | grep -E "libdftracer_core|papi|rocprofiler|cupti|mpi"
```

`env.sh` is sourced by every install/build/run step so they cannot drift apart. Load
`papi` on its own line — loading it in the same `module load` as `rocmcc` can get it
silently dropped, leaving `CRAY_PAPI_PREFIX` empty with no error.

## STEP 6 — Derive the PAPI partition ON THIS MACHINE

`papi_avail` reports far more preset *names* than the hardware can count at once, and a
**derived** preset expands into 2+ native events. Sizing a set by counting names
therefore under-sizes it; PAPI responds by time-sharing and reporting **scaled estimates
with a zero exit code**.

```bash
papi_avail | grep "Number Hardware Counters"         # the real budget
papi_avail -a | awk '/^PAPI_/ {print $1, $3}'        # $3 == Yes  =>  derived
```

Measured, to show why this cannot be copied:

| | Tuolumne (MI300A) | Tioga (MI250X / Trento) |
| --- | --- | --- |
| Hardware counters | 5 | 5 |
| Presets available | 30 | **27** |
| Derived presets | 12 | **5** |

Tioga lacks `PAPI_L2_TCM`, `PAPI_L2_TCH`, `PAPI_FMA_INS` entirely, and several presets
that are derived on Tuolumne are native there. A Tuolumne partition fails on tioga twice:
it requests presets that do not exist, and it mis-sizes the sets.

Build a partition that covers **every** available preset with no duplicates, then
**probe** it with cheap 1-rank runs and read `args.multiplex` back out of the trace
(`0` = exact, `1` = estimate) before committing to the full sweep. Also diff the counter
names that actually landed against those requested — dftracer silently **drops** a preset
it cannot fit (observed with `PAPI_BR_PRC`, "one native event subtracted from another").
Full method in [[software-papi]].

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

**Traps:**

* `DFTRACER_AGGREGATION_TYPE` is compared **case-sensitively** against `"SELECTIVE"`.
  Anything else — including `selective` — silently falls back to `FULL`, which aggregates
  *everything*. This fails loud nowhere.
* **`dur` is in the trace's time metric, which is microseconds.** Pick the threshold with
  that in mind. Measured on a real miniFE leaf, `dur < 1000` (1 ms) would aggregate
  **99.81%** of all events, including **100%** of `KERNEL_DISPATCH` and `comm` — i.e. it
  removes exactly the GPU and MPI detail the corpus exists to capture. `dur < 100` is far
  more conservative; still measure the keep-rate on one leaf before the full sweep.
* Aggregation is **capture-time only**. It cannot be applied or undone afterwards, so
  getting the threshold wrong means re-running the whole sweep.

## STEP 8 — Dimensions

Three dimensions, and the directory tree **is** the dimension space:

| Dimension | Values |
| --- | --- |
| Input | the unique `merged.txt` argument strings (or the agreed subset) |
| Node scale | e.g. 1, 2, 4, 8 — bounded by the queue's node limit |
| Processes per node | **CPU:** 1,2,4,8,16,32 &nbsp;&nbsp; **GPU:** one per GPU, so *derive from `flux resource list`* — 1,2,4 on a 4-GPU node (Tuolumne), 1,2,4,8 on an 8-GCD node (Tioga) |

Hold per-rank resources **constant** across the ppn sweep (e.g. 1 GPU + N cores per rank)
so that ppn is the only thing varying.

PAPI counter sets are **not** a dimension — they are the multiple runs needed to cover
all presets, and they collapse into one `compacted/` trace per leaf.

Total runs = `inputs x node_scales x ppns x papi_sets`. Compute this and the node-hours
**before** launching, and report both.

## STEP 9 — Directory structure

```
genesis_traces/<system>/
  input_<args-slug>/
    nodes_<N>/
      ppn_<P>/
        raw/
          papi_<setname>/          # app traces + per-node service traces
          validation_<setname>.json
        compacted/                 # 4 MB verified chunks over the whole cell
        compacted_check.json
  README.md
```

## STEP 10 — Run each configuration

Per run, in order:

1. `flux submit` the service **detached**, pinned: `-N<n> -n<n> -c1
   --requires=host:<csv> --setattr=exclusive=false`, then poll its per-host pid files.
   `dftracer_service start` under `flux run` **never returns**.
2. `flux run` the app on the **same** pinned hosts.
3. `flux submit` the service `stop` on the **same** pinned hosts, then let it flush.

Host pinning across all three is mandatory: in an allocation larger than the job, three
separate submits otherwise land on three different node subsets, and the daemons profile
the wrong nodes while the service traces come back empty.

Application output (checkpoints, per-run data) goes to the **parallel filesystem**;
dftracer traces stay in the session workspace.

**Scheduling:** use one queue of all pending runs sorted by node count descending
(first-fit decreasing) and dispatch the largest run that fits the moment any run returns
its hosts — no phase barriers. A phase-per-node-scale design makes a batch of eight
1-node runs all wait on whichever is largest. Host slices handed out must be **disjoint**:
two configs sharing a node contaminate each other's PAPI counters and per-host power.

**Checkpoint** every run that passes validation to a state file, keyed by
`(input, nodes, ppn, set)`, and skip those on resume. If the queue's time limit is
shorter than the sweep, compute a deadline from the worker's own start time plus the
allocation budget, stop dispatching before it, exit with a distinct code, and let the
driver request a fresh allocation and resume.

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

Separately audit that the app solved the right problem (parse its own result files, not
just its exit code): problem size, rank count, and a finite result. Do **not** invent
convergence thresholds — many proxy apps stop at a fixed iteration cap, so a "large"
residual is expected, not a failure.

## STEP 12 — Compact and document

`dftracer_split` **does not recurse**: it globs `*.pfw*` in exactly the directory given.
The traces live one level down in `raw/papi_*/`, so pointing it at `raw/` finds nothing
and exits 1. Stage a flat directory of **hardlinks** (same filesystem, no copy) and
compact that. Per-node service traces are all named `service_<host>.pfw.gz` and **collide
across the PAPI sets** — splice the set name in when staging.

Use `--verify` (event-ID hash in == out) and then separately assert the compacted output
contains the **service** categories, not just the app ones — otherwise a staging bug
silently drops every node-level counter while the split still reports success.

A leaf legitimately spans more hosts than `nodes`: it aggregates several runs and the
scheduler may place each on a different slice. Assert `hosts >= nodes`, not equality.

Finish by generating `genesis_traces/<system>/README.md` describing what the traces are,
the dimensions, the corpus statistics (events by category and by dimension, PAPI
coverage, power samples, size), a per-cell inventory, the validation criteria, and the
**caveats** — including any layer that is silently unavailable on this machine.

## Known per-system caveats to check and document

* **GPU hardware counters** may be unavailable: ROCProfiler logs `could not be locked for
  profiling ... (capability SYS_PERFMON)`. GPU *activity* tracing still works; say so
  explicitly rather than letting a reader assume GPU PMC data exists.
* **variorum**: `DFTRACER_BUILD_VARIORUM=AUTO` can find a system RPM that returns
  `_ERROR_VARIORUM_UNSUPPORTED_PLATFORM` and yields zero power events. Use
  `DFTRACER_BUILD_VARIORUM=ALWAYS` and verify a `gpu`/`power` event actually lands.
* **service teardown abort** (`corrupted size vs. prev_size in fastbins`) from two
  `librocm_smi64` ABIs in one process is post-flush and costs no data;
  `HWLOC_COMPONENTS=-rsmi` does not avoid it.

## Related

[[software-papi]] · [[software-cupti]] · [[software-rocm]] · [[dftracer-trace-utils]] ·
[[flux-alloc]] · [[system-tuolumne]] · [[system-tioga]] · [[system-matrix]]
