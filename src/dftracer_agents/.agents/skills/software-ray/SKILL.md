---
name: software-ray
description: Ray (ray.io) cluster bring-up caveats — the jemalloc/static-TLS `ray start --head` crash on Cray PE + ROCm systems, and multi-node bootstrap pitfalls. Load this skill for any Ray/Ray Train/Ray Tune/Ray Data workload, especially on Cray PE (module-heavy) or ROCm/AMD-GPU systems.
---

# software-ray

## `ray start --head` fails with a silent `gcs_server.err: FileNotFoundError` on Cray PE + ROCm systems

**Symptom:** `ray start --head` deterministically fails after ~35 seconds (a 5s GCS-connect
timeout followed by a 30s cluster-ID retry timeout) with:
```
gcs_rpc_client.h:151: Failed to connect to GCS at address <ip>:<port> within 5 seconds.
gcs_client.cc:183: Failed to get cluster ID from GCS server: TimedOut...
FileNotFoundError: [Errno 2] No such file or directory: '<temp_dir>/session_.../logs/gcs_server.err'
```
This reproduces across every combination of `--node-ip-address`, `--num-cpus`/`--num-gpus`,
retry loops, and fresh `--temp-dir`s — the `gcs_server` C++ binary itself, when invoked
DIRECTLY with the exact same flags `ray start` uses (verified by reading
`ray/_private/services.py::start_gcs_server`'s constructed `command` list and reproducing it by
hand), starts and stays running fine, binds its port, and writes its own log files normally.
The difference is invisible from `ray start`'s own Python-side traceback, which only ever shows
the driver's connection-timeout failure, never the `gcs_server` subprocess's own stdout/stderr.

**Root cause:** `ray/_private/services.py::start_ray_process()` unconditionally computes
`jemalloc_env_vars = propagate_jemalloc_env_var(...)`, which sets
`LD_PRELOAD=<ray_install>/core/libjemalloc.so` **whenever `os.environ.get("LD_PRELOAD") is
None`** in the parent process — this applies to EVERY Ray subprocess it spawns (gcs_server,
raylet, workers), not just ones explicitly requesting jemalloc profiling. On a Cray PE system
with `module load rocm/<version>` (or any other environment that already preloads/links a large
number of shared libraries, consuming a big share of glibc's static-TLS surplus), dlopen'ing
jemalloc under that already-crowded static-TLS budget fails with `cannot allocate memory in
static TLS block`. Because this happens during dynamic linking, `gcs_server`'s `exec()` never
reaches `main()` — it writes **zero** log lines, exactly matching the observed
`gcs_server.err: FileNotFoundError` (the file was never created because nothing ever ran to
create it). The parent's `subprocess.Popen` handle can still transiently look "alive" to Ray's
own connection-retry loop (`node.py::_init_gcs_client`), which is why the failure surfaces 30+
seconds later as a confusing timeout rather than an immediate, obvious crash.

**Fix:** explicitly set `LD_PRELOAD` to an EMPTY STRING (not merely unset — `os.environ.get(...)
is None` must be `False`) before any `ray start` invocation:
```bash
export LD_PRELOAD=""
ray start --head ...
```
Do this before every `ray start` call in a multi-node bring-up script (head AND each worker's
`ray start --address=...`, since raylet is spawned by the worker call too and is equally
susceptible). Verified: `ray start --head` then succeeds in ~10s and `ray status` reports the
node active, reproduced across multiple full job launches on Tuolumne (Cray PE, ROCm 6.2.1,
Ray 2.48.0, python3.9).

**How to diagnose this class of bug if it recurs (different Ray version / different preloaded
lib):** reproduce the exact `gcs_server`/`raylet` command line by reading
`ray/_private/services.py::start_gcs_server`/`start_raylet` (the constructed `command` list),
then run that binary directly by hand with and without your suspected env var — if it starts
fine standalone but fails only under `ray start`, diff the env `start_ray_process()` actually
uses (`modified_env = os.environ.copy(); modified_env.update(env_updates)`) against your manual
invocation; `LD_PRELOAD` injection is the first thing to check.

## Multi-node manual bring-up: other pitfalls (not jemalloc-specific)

- `ray.init()` with no arguments does NOT auto-join a cluster you bootstrapped by hand with
  `ray start --head` unless `RAY_ADDRESS` is set in that process's environment — otherwise it
  silently creates its own new, isolated single-node cluster, and any `ScalingConfig`/resource
  request sized for the intended multi-node cluster will hang forever waiting for resources
  that will never appear (no error, no traceback — just near-0% CPU indefinitely).
- If a head node is a real accelerator-bearing compute node (not a control-plane-only login
  node), don't reflexively pass `--num-gpus=0`/`--num-cpus=0` to `ray start --head` — that
  under-resources the cluster if your `ScalingConfig`/resource math assumes every physical
  node (including the head) contributes its full device count.
- A worker script that keeps its `ray start --address=...` process (and thus its GPUs) alive
  via a fixed `sleep N` before `ray stop` is a race against the actual head-side job duration.
  Prefer a completion-marker file the head writes on exit, polled by the worker with a generous
  upper bound, over guessing a sleep duration.

## `ray.data.read_csv(...)` materializes once into the object store — do not assume per-epoch re-reads

A diagnoser flagging "critical" POSIX metadata-op-count bursts at epoch boundaries in a
`ray.data` training pipeline does NOT mean the dataset file is being re-opened/re-read each
epoch — verify by resolving the trace's `FH` metadata records to real paths before proposing
any dataset-caching/striping fix. Measured on a 2-node Ray Train run (MoLFormer, 4 epochs):
the source CSV saw 9 POSIX ops / 0.006s for the ENTIRE run (all in the driver process, at
initial `read_csv()` time), and **zero** further opens/reads from any worker across all 4
epochs of `dataset.iter_batches()` — Ray keeps the materialized data resident in the object
store and workers pull from there, not the file. If you see repeated metadata bursts anyway,
resolve the actual `FH` paths first: on that same run, 117,920 of the observed `__lxstat64`
calls were the Python interpreter/venv `site-packages` import path being walked repeatedly by
newly-spawned Ray worker processes (a dynamic-loading/module-import storm, not app I/O) — see
`dftracer-io-optimization` for the general version of this lesson.

## `plasma_store` path strings in a trace can be `default_worker.py` command-line args, not real spill files

Confirmed on ray_molformer (2026-08-02, 4-node/16-GPU): a raw grep for `plasma_store`/spill
paths in a trace can false-positive, because Ray embeds
`--object-store-name=.../plasma_store` in every `default_worker.py` process's command-line
string, which shows up in trace/process metadata and looks like a file access even when it
isn't one. Filter out cmdline records before concluding real object spilling occurred, e.g.
`zcat *.pfw.gz | grep -v default_worker.py | grep '<ray_temp_dir>'`. On ray_molformer the
filtered result was 337 ops, all logs/sockets/`ports_by_node.json`/artifacts metadata — zero
real spill activity, confirming the diagnosis was I/O-negligible rather than spill-bound.
