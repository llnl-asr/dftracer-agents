---
name: software-pegasus
description: >
  Install and run Pegasus workflows (e.g. montage-workflow-v3) on HPC
  systems without a site-wide HTCondor pool, using Pegasus MPI Cluster
  (PMC) under a Flux/Slurm allocation instead of Condor+DAGMan. Covers
  userspace install of Condor (for pegasus-plan tooling only), Pegasus
  5.x, rebuilding pegasus-mpi-cluster against the system MPI, site/
  properties configuration for PMC-only code generation, dftracer
  tracing wiring, and every pitfall hit getting this working on
  Tuolumne (Cray PE / Flux). Load this skill for any Pegasus workflow
  work, not just Montage.
---

Cross-references: [[flux-alloc]] [[dftracer-preload-run]] [[workload-ior]] [[dftracer-annotation-lessons]]

Reference used: https://dftracer.readthedocs.io/en/latest/pegasus_montage.html
(LC Corona instructions — this skill generalizes them to any HPC system with
Flux/Slurm and no HTCondor pool, and replaces Condor/DAGMan execution with PMC).

---

## Architecture decision: PMC-only, not Condor+DAGMan

Pegasus's default execution path is: `pegasus-plan --submit` → HTCondor DAGMan
submits and monitors jobs via `condor_submit_dag` → a live personal Condor
pool schedules each job. On a leadership-class HPC system this is awkward:
Condor wants to own scheduling, doesn't know about your allocation, and personal
Condor pools are fragile under batch schedulers.

**Use Pegasus MPI Cluster (PMC) instead.** PMC replaces Condor+DAGMan entirely:
`pegasus-plan` emits a flat text DAG (`TASK`/`EDGE` lines), and a single
`mpirun`/`flux run` invocation of the `pegasus-mpi-cluster` binary reads that
DAG, schedules tasks onto MPI ranks (1 master + N workers), and executes the
whole workflow — no Condor daemons running, no DAGMan, nothing but one MPI job.

Condor is still needed as a **build/planning-time dependency only** — some
`pegasus-plan` code paths reference Condor tooling even when the code
generator is PMC. Install it, but never start `condor_master`; it does no
work in this architecture.

## Step 1 — Install Condor (planning-time only, never started)

```bash
cd <workspace>
wget https://research.cs.wisc.edu/htcondor/tarball/10.x/current/condor-x86_64_CentOS8-stripped.tar.gz -O condor.tar.gz
tar -x -f condor.tar.gz            # extracts to condor-<ver>-x86_64_<OS>-stripped/
mkdir condor
mv condor-*-stripped/* condor/
rmdir condor-*-stripped
cd condor
./bin/make-personal-from-tarball   # sets up local/, etc/condor_config — no daemons started
```

RHEL8/AlmaLinux8-stripped tarballs work fine on RHEL8-based HPC login/compute
nodes (Tuolumne is RHEL 8.10) — no need to match exact OS.

Only export `CONDOR_CONFIG=<condor>/etc/condor_config` and put
`<condor>/bin:<condor>/sbin` on `PATH` before calling `pegasus-plan`. Do not
run `condor_master`.

## Step 2 — Install Pegasus (binary + rebuild PMC against system MPI)

```bash
wget https://download.pegasus.isi.edu/pegasus/5.0.7/pegasus-binary-5.0.7-x86_64_rhel_7.tar.gz
tar zxf pegasus-binary-5.0.7-x86_64_rhel_7.tar.gz    # extracts to pegasus-5.0.7/
mv pegasus-5.0.7/* pegasus/                           # flatten into your install dir
```

### Pitfall: prebuilt `pegasus-mpi-cluster` binary is ABI-incompatible with vendor MPI

The prebuilt `bin/pegasus-mpi-cluster` in the binary tarball is linked against
generic MPICH ABI v10 (`libmpich.so.10`). Cray MPICH (and most vendor MPI
stacks) ship ABI v12 (`libmpi_cray.so.12` on Cray) — not binary compatible.
`ldd` will show `libmpichcxx.so.10 => not found`.

**Fix: rebuild PMC from source against the system `mpicxx`.** Source lives in
the main Pegasus monorepo, not a separate repo:

```bash
curl -sL https://github.com/pegasus-isi/pegasus/archive/refs/tags/5.0.7.tar.gz \
  -o pegasus-src.tar.gz
tar xzf pegasus-src.tar.gz pegasus-5.0.7/packages/pegasus-mpi-cluster
cd pegasus-5.0.7/packages/pegasus-mpi-cluster
make CXX=$(which mpicxx) prefix=<your-pegasus-install-dir>
cp pegasus-mpi-cluster <pegasus-install-dir>/bin/pegasus-mpi-cluster   # overwrite prebuilt
```

It's a plain Makefile using `mpicxx` directly — no autotools/cmake needed.
Builds cleanly against Cray MPICH 9.0.1 with `crayclang` in ~10 seconds.

Building from only the extracted `packages/pegasus-mpi-cluster` subtree (not
a full monorepo checkout) prints a harmless
`release-tools/getversion: No such file or directory` warning as `make` falls
back on `version.h` generation — the build still succeeds; ignore it.

### Pitfall: runtime LD_LIBRARY_PATH for the rebuilt PMC binary

The rebuilt binary needs every Cray PE lib dir at runtime — `flux run` /
`srun` do **not** inherit your interactive shell's module-loaded
`LD_LIBRARY_PATH` by default (see [[flux-alloc]]). Missing pieces surface
one at a time as successive "cannot open shared object file" errors:
`libmpi_cray.so.12` → `libpmi.so.0` → `libpals.so.0` → ... Rather than
chasing each one, just capture and forward your *entire* interactive shell's
`LD_LIBRARY_PATH` plus the MPICH/CCE/PMI lib dirs explicitly:

```bash
LD="/opt/cray/pe/lib64:/opt/cray/lib64:/opt/cray/pe/papi/<ver>/lib64:\
/opt/cray/pe/pmi/<ver>/lib:\
/opt/cray/pe/mpich/<ver>/ofi/crayclang/<ver>/lib:\
/opt/cray/pe/cce/<ver>/cce/x86_64/lib:/opt/cray/pe/cce/<ver>/cce-clang/x86_64/lib:\
/usr/lib64:<dftracer-venv>/lib/python3.*/site-packages/dftracer/lib64"
```
`/opt/cray/pe/pmi/<ver>/lib` (libpmi.so.0, libpmi2.so.0) is the one people
forget — it's not under the MPICH tree, it's a separate `cray-pmi` product dir.
Find it with `find /opt/cray/pe/pmi -iname libpmi.so.0` if the version differs.
On Tuolumne specifically, `libpmi.so.0`/`libpmi2.so.0` resolve directly from
`/opt/cray/pe/lib64` — no separate `cray-pmi` dir needed there; the generic
template above is still correct for sites where it isn't bundled that way.

## Step 3 — Compile Montage (or reuse an already-annotated build)

```bash
git clone https://github.com/Caltech-IPAC/Montage.git && cd Montage && make
export PATH=<Montage>/bin:$PATH
```

**To get dftracer traces automatically with zero workflow changes**: point
`PATH` at your dftracer-*annotated* Montage `bin/` (see
[[dftracer-annotation-lessons]] for how to build one) instead of the plain
build. `montage-workflow-v3`'s `montage-workflow.py` auto-discovers every
executable in `dirname(which('mProject'))` and registers each as a Pegasus
`Transformation` with that exact full path — so whichever `mProject` resolves
first on `PATH` at DAX-generation time is what every job in the plan will
execute. No `Transformation`/DAX edits needed; just order `PATH` correctly
*before* running `montage-workflow.py`.

Annotated binaries carry an absolute `RPATH` to `libdftracer_core.so`, so they
find dftracer's own libs without `LD_LIBRARY_PATH` — but they still need the
Cray PE lib dirs above for their own linked libs (`libmpi_cray.so.12`,
`libmodules.so.1`, etc., pulled in by the CC wrapper used at annotation-build
time — see [[dftracer-annotation-lessons]]). Set the same `LD` block from
Step 2 in the PMC task environment (Step 6).

## Step 4 — Pegasus Python API on a modern Python

The bundled `pegasus/lib64/python3.6/site-packages/Pegasus` package is pure
Python (no compiled extensions) and imports fine under Python 3.9–3.13 — just
add it to `PYTHONPATH`:

```bash
export PYTHONPATH=<pegasus>/lib64/python3.6/site-packages
python3 -c "from Pegasus.api import *"   # should just work
```

Also install `astropy` (montage-workflow.py needs `astropy.io.ascii`) and
`gitpython`/`six` (needed by `pegasus-plan`/`pegasus-transfer`'s Python CLI
scripts even though they're not obviously workflow dependencies) into
whichever `python3` is first on `PATH` — `pegasus-plan` and `pegasus-transfer`
are `#!/usr/bin/env python3` scripts that pick up whatever `python3` resolves
to, not a bundled interpreter.

### Pitfall: stale bundled `six.py` shadows a working modern `six`

`pegasus-transfer` fails with:
```
File ".../Pegasus/tools/worker_utils.py", line 34, in <module>
    from six.moves.builtins import int
ModuleNotFoundError: No module named 'six.moves'
```
even though `pip show six` reports a modern version installed and importable
directly. Cause: Pegasus bundles its own ancient `six.py` at
`pegasus/lib64/pegasus/externals/python/six.py`, and that directory sits
earlier on `sys.path` (auto-prepended for the running script) than your
venv's site-packages. That old `six.py` predates proper `six.moves`
lazy-module support under Python 3.10+.

**Fix**: rename it out of the way (it's your own extracted copy, safe to
touch) — nothing else in the Pegasus binary distribution needs it once a
modern `six` is on `PYTHONPATH`:
```bash
mv <pegasus>/lib64/pegasus/externals/python/six.py{,.bak}
```

## Step 5 — Generate the DAX and site catalog

```bash
export PATH=<montage-bin>:<pegasus>/bin:<condor>/bin:<condor>/sbin:$PATH
export PYTHONPATH=<pegasus>/lib64/python3.6/site-packages
export CONDOR_CONFIG=<condor>/etc/condor_config
cd montage-workflow-v3
python3 ./montage-workflow.py --center "<ra dec>" --degrees <N> --band dss:DSS2B:blue
```

`--degrees 1.0` gives ~4 input images / 6 overlaps — enough to exercise
reprojection, diff/fit, background modeling, and coadd stages without a long
download. `--degrees 0.3` may yield only 1 image (no overlaps, background
correction stages skipped) — too small to be a meaningful workflow.

Pegasus 5's built-in default site catalog does **not** define a usable
`local` site (`pegasus-plan --sites local` without a site catalog fails with
`File Server not specified for shared-scratch filesystem`). Write an explicit
`sites.yml`:

```yaml
pegasus: "5.0"
sites:
 - name: local
   arch: x86_64
   os.type: linux
   directories:
    - type: sharedScratch
      path: <workspace>/scratch
      fileServers:
       - operation: all
         url: file://<workspace>/scratch
    - type: localStorage
      path: <workspace>/storage
      fileServers:
       - operation: all
         url: file://<workspace>/storage
```

## Step 6 — pegasus.properties for PMC-only code generation

```properties
pegasus.code.generator = PMC
pegasus.data.configuration = sharedfs
pegasus.transfer.links = true
```

`pegasus.code.generator = PMC` is the one property that matters: it makes
`pegasus-plan` emit a flat `TASK`/`EDGE` DAG text file
(`work/.../run0001/<name>-0.dag`) directly executable by `pegasus-mpi-cluster`,
instead of a Condor DAGMan submit file tree.

`pegasus.transfer.links = true` turns same-filesystem stage-in/stage-out
copies into symlinks — cheap, but **does not eliminate the stage-in/cleanup
job nodes themselves**. `pegasus.transfer.bypass.input.staging = true` also
does not remove them for inputs coming from a genuinely remote replica
(e.g. `http://archive.stsci.edu/...` DSS image URLs) — those still need a real
transfer since the bytes aren't local yet. It only helps when the *input*
replica is already on the same execution site's shared filesystem. For a
workflow whose real inputs are archive downloads (like Montage), expect
`stage_in_remote_*` jobs regardless — they're correct, not overhead.

### Pitfall: `--cluster horizontal` breaks PMC code generation

```
java.io.FileNotFoundException: .../run0001 (Is a directory)
  at ...MPIExec.generatePMCInputFile(MPIExec.java:246)
```
Passing `--cluster horizontal` to `pegasus-plan` alongside
`pegasus.code.generator=PMC` on Pegasus 5.0.7 triggers a planner bug in the
PMC-clustering code path that leaves an empty/corrupt `.dag` file. **Fix: omit
`--cluster horizontal` entirely.** `pegasus.code.generator=PMC` alone already
produces one PMC `TASK` per workflow job (a flat, ungrouped PMC DAG) — for
workflows in the tens-to-hundreds-of-jobs range this is fine; PMC's own
scheduler handles per-task overhead well. Only chase clustering if profiling
shows PMC's per-task dispatch overhead actually matters at your job count.

```bash
pegasus-plan \
  --conf pegasus.properties \
  --sites local \
  --dir work \
  --output-site local \
  data/montage-workflow.yml
```

This just plans (no `--submit` — PMC execution is manual, see Step 7).

### Pitfall: omitting `--conf` silently produces a DAGMan DAG, not a PMC DAG

If `pegasus-plan` is invoked WITHOUT `--conf <properties-file>` (e.g. by a
wrapper script or agent that re-derives its own `pegasus-plan` command line
and forgets the flag), `pegasus.code.generator = PMC` never takes effect —
there is no error, no warning. The planner silently falls back to its
default Condor DAGMan code generator and emits a `.dag` file full of
`JOB`/`SCRIPT`/`RETRY`/`VARS`/`MAXJOBS` lines instead of `TASK`/`EDGE`.
`pegasus-mpi-cluster` then aborts on the very first non-`TASK`/`EDGE`/`#`
line it sees:
```
ABORT: failure.cpp(22): Invalid DAG record: MAXJOBS registration 1
```
(or `Invalid DAG record: JOB ...` if MAXJOBS lines were stripped first —
stripping MAXJOBS is NOT a real fix, it's treating a symptom of the same
missing-`--conf` root cause). **Fix: always pass `--conf <path-to-your-
pegasus.properties>` explicitly to every `pegasus-plan` invocation** — never
rely on a `pegasus.properties`/`pegasus.xml` file being auto-discovered in
the CWD. Verify a successful PMC plan before ever launching PMC:
```bash
grep -c '^TASK\|^EDGE' <name>-0.dag   # should be large (one per job)
grep -c '^JOB\|^MAXJOBS' <name>-0.dag # must be 0
```

### Pitfall: replica-catalog input paths from a wrong daxgen `-D`/dataset dir

If the workflow generator script (`daxgen.py`/`montage-workflow.py`-style)
is invoked with a dataset/data directory argument that doesn't match where
the actual input files live in the session workspace, the replica catalog
(`site_label: local, url: file://...`) will point stage-in transfers at a
path that doesn't exist. Symptom: `stage_in_remote_*`/`stage_in_local_*`
tasks hang for several minutes (pegasus-transfer's retry/poll loop against
a missing local `file://` source) then fail with exit status 256, all
*before* any real workflow task ever runs. Fix: verify every `file://` URL
referenced in the generated `catalogs/replicas.yml` (or a task's `.in`
Kickstart input file) resolves with `ls -la <path>` BEFORE launching PMC —
catching this pre-flight is much cheaper than waiting out the transfer
timeout. A symlink from the wrong-but-expected path to the real data
directory is a valid quick fix if the generator's dataset-dir argument
can't easily be corrected.

## Step 7 — Run the DAG via PMC under a Flux allocation

```bash
RUN=work/<user>/pegasus/<name>/run0001
flux proxy <ALLOC_JOBID> flux run -N1 -n<workers+1> \
  bash pmc_wrapper.sh <pegasus>/bin/pegasus-mpi-cluster -v $RUN/<name>-0.dag
```

Use a wrapper script (never inline `--env` for anything beyond simple
key=value — see [[flux-alloc]] for why) to set the CWD and all runtime env:

```bash
#!/bin/bash
export LD_LIBRARY_PATH="<the LD block from Step 2>"
export DFTRACER_ENABLE=1
export DFTRACER_INIT=FUNCTION
export DFTRACER_INC_METADATA=1
export DFTRACER_DATA_DIR=all
export DFTRACER_LOG_FILE=<traces-dir>/<name>
cd <RUN-dir>          # PMC's stdout/stderr/rescue paths in the .dag are relative
exec "$@"
```

PMC forks task processes from its worker ranks, which inherit this
environment — so every job in the workflow gets dftracer tracing without
touching the workflow generator or the transformation catalog at all. One
MPI rank is the master (schedules, no compute); the rest are workers (one
task each at a time). `-n8` → 1 master + 7 concurrent task slots.

`-v` (verbose) is worth keeping on for the first run of any new workflow —
it logs every task submission/exit inline, which is the fastest way to spot
a stuck or silently-failing stage.

### Pitfall: MPI rank count must match DAG task parallelism, not just fill cores

PMC's rank count (`-n<workers+1>`) should scale with the DAG's actual
concurrent-task parallelism, not with how many cores an allocation has to
offer. Requesting far more workers than the DAG can ever run concurrently
(e.g. `-N4 -n380` — one rank per core across 4 nodes — for a DAG with only
~46 total tasks and a handful of ever-runnable-at-once tasks) can cause
PMC's own MPI_Init/worker-registration handshake to hang indefinitely: every
rank logs its startup env dump and "Running PMC cluster with DAG", then
total silence — no `Master starting with N workers`, no task-start/finish
lines, no trace output, seemingly forever. A right-sized rank count for the
same DAG (`-N4 -n32`, ~8 workers/node) completes `MPI_Init` and starts
scheduling tasks within ~10 seconds. Rule of thumb: pick `-n` around
2-4× the number of tasks that can realistically run concurrently in the
DAG (fan-out width of the widest parallel stage), not `cores_per_node ×
N_nodes`. If a PMC run shows only startup-env-dump lines and zero task
activity for more than ~2-3 minutes, don't wait it out — cancel
(`flux cancel <jobid>`) and retry at a smaller `-n` before assuming
anything else is wrong.

### Pitfall: `pegasus-plan` output is confusingly still Condor-flavored

Even with `pegasus.code.generator=PMC`, the planner prints
`pegasus-run <submit-dir>` as "the next step" — **ignore this**, `pegasus-run`
invokes Condor DAGMan and will not work (no Condor daemons running). The real
next step is the manual `pegasus-mpi-cluster` invocation above.

### Expected run profile

For a ~1° / 4-image / single-band Montage mosaic (57 total jobs including
stage-in/chmod/cleanup/register bookkeeping tasks): full run takes well under
15 minutes on 1 node with 7-8 PMC workers, most of the wall time in the
initial remote stage-in (real HTTP downloads from the DSS archive, ~5 min for
first `stage_in_local_local` type jobs due to `pegasus-transfer`'s retry/poll
loop — not a bug, just conservative default transfer timeout behavior).
`pegasus-mpi-cluster`'s own summary line confirms success:
```
[cluster-summary stat="ok", tasks=57, submitted=57, succeeded=57, failed=0, ...]
[info] Workflow suceeded
```
(sic — "suceeded" typo is in PMC's own output, not a transcription error.)

## Verifying dftracer coverage end to end

```bash
ls -la <traces-dir>/*.pfw.gz
```
Expect one non-empty trace per annotated-binary invocation (0-byte files are
fine — they're PMC bookkeeping tasks like `chmod`/`register`/`cleanup` that
never call an annotated Montage binary). The heaviest-I/O stage (batch
reprojection, `mProject`/`mProjExec`) produces by far the largest trace file.

## Full pitfall summary (fastest lookup)

| Symptom | Root cause | Fix |
|---|---|---|
| `libmpichcxx.so.10 => not found` | prebuilt PMC binary is generic-MPICH-ABI, vendor MPI uses a different SONAME | rebuild PMC from source with `mpicxx` |
| successive `lib*.so not found` errors one at a time | `flux run`/`srun` don't inherit interactive shell's `LD_LIBRARY_PATH` | forward full CCE+MPICH+PMI lib path block explicitly |
| `File Server not specified for shared-scratch filesystem for site: condorpool` | no site catalog given, Pegasus 5 default site isn't usable as-is | write explicit `sites.yml` with a `local` site |
| `FileNotFoundException: run0001 (Is a directory)` during planning | `--cluster horizontal` + `pegasus.code.generator=PMC` planner bug (5.0.7) | drop `--cluster horizontal` |
| `ModuleNotFoundError: No module named 'six.moves'` in `pegasus-transfer` | stale bundled `six.py` shadows modern `six` on `sys.path` | rename bundled `pegasus/lib64/pegasus/externals/python/six.py` |
| `pegasus-plan` needs `GitPython` | not bundled, planner Python tooling assumes it | `pip install gitpython` into whichever `python3` runs `pegasus-plan` |
| jobs silently run un-annotated Montage binaries | wrong `mProject` earlier on `PATH` at DAX-generation time | put annotated `install_ann/bin` **first** on `PATH` before `montage-workflow.py` |
| `pegasus-run <dir>` suggested after planning | that's the Condor/DAGMan path, not PMC | ignore it; run `pegasus-mpi-cluster <dag-file>` manually instead |

## Permissions

This skill uses:

- **Bash:** `wget`/`curl` (Condor/Pegasus tarballs, PMC source), `make`/`mpicxx` (rebuilding PMC), `pegasus-plan`/`pegasus-mpi-cluster`/`pegasus-transfer`, `flux run`/`flux proxy` (see [[flux-alloc]])
- **Write:** `workspaces/<session>/*` only — Condor/Pegasus installs, site catalogs, properties files, PMC wrapper scripts all live under the session workspace
- **Never:** start `condor_master`/`condor_schedd` (Condor is planning-time tooling only in this architecture); never `sudo`
