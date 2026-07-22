---
name: system-tuolumne-spindle
description: Spindle scalable library/Python-module loader on Tuolumne (LC toss4/Cray) — what it is, how it's already wired into flux by default, how to tune its level for Python/DL-heavy workloads, and how to verify it's active. Load this skill for any job launch-time / startup-latency investigation, especially Python/PyTorch/ML workloads with many dynamic libraries or .py/.pyc imports.
---

Cross-references: [[system-tuolumne]] [[dftracer-io-optimization]]

## What Spindle is and why it matters for DL workloads

Spindle (https://github.com/hpc/Spindle) intercepts a process's dynamic-library and Python
module loading (`ld.so` audit interface + Python import hooks) and redirects those file reads
through a scalable tree/broadcast network instead of every rank hammering the shared
filesystem independently. For a large-rank Python/PyTorch job, EACH rank imports the same
hundreds of `.py`/`.pyc`/`.so` files (torch, PyG, h5py, numpy, mpi4py, ROCm libs, ...) — without
Spindle this is O(ranks × files) independent shared-FS reads at startup; with Spindle it's
effectively O(files) reads broadcast to all ranks. This directly targets DL job startup latency
(import storms), which is a real, measured, non-trivial cost for PyTorch+PyG+HDF5 stacks (see
[[software-pecan]] — large rank counts × heavy import graph = long time-to-first-step).

## It is enabled BY DEFAULT on Tuolumne flux jobs — you likely don't need to do anything

`/etc/flux/shell/lua.d/spindle.lua` auto-loads the Spindle flux shell plugin
(`libspindleflux.so`, resolved via the `spindle/live` module's target
`/collab/usr/global/tools/spindle/toss_4_x86_64_ib_cray_live/`) for every non-instance flux job
(i.e. every real job, not a nested `flux start`/`flux broker`), UNLESS:
- `SPINDLE_FLUXOPT=disable` is set in the job's environment, or
- the `fastload` shell option is explicitly requested instead (`-o fastload=...` — a different,
  narrower LD_AUDIT-based accelerator; mutually exclusive with spindle in the plugin logic).

Default level when nothing else is specified: **`medium`**. There is no module load, no CLI
wrapper, no extra flag needed to get baseline Spindle acceleration on a `flux run`/`flux
submit` job on this system — it is already there.

## Levels — `low` / `medium` / `high` / `off`

Spindle's `level` option is a shorthand that sets the individual `reloc-aout`/`reloc-libs`/
`reloc-exec`/`reloc-python` flags together (from the plugin's embedded help string):
- **`high`** — relocates everything possible (aout + libs + exec targets + **python
  .py/.pyc/.pyo files**). This is the one that matters most for Python-heavy DL workloads,
  since it's the only level that explicitly accelerates `.py`/`.pyc` MODULE loading, not just
  `.so` shared libraries.
- **`medium`** (default) — "a balance that tries for minimal impact on debugging." Relocates
  libraries but likely does not fully cover Python file relocation the way `high` does — verify
  with the `-y`/`--reloc-python` bit before trusting silently.
- **`low`** — turns off most relocation, keeps only a file-existence cache.
- **`off`** — Spindle fully disabled.

**For a Python/PyTorch/PyG DL workload (PECAN, ScaFFold, or similar), explicitly requesting
`high` is the natural next step to try** if startup latency is a measured bottleneck, since it's
the only level guaranteed to cover `.py`/`.pyc` reloc — the default `medium` may already help
libs/aout but wasn't confirmed (this session) to relocate Python files.

## How to set the level

Via the flux shell option (Lua reads `shell.options.spindle.level`):
```bash
flux run -N4 -n16 -g1 -o spindle.level=high bash run_script.sh
```
Via environment variable (checked by the plugin before falling back to the "medium" default,
in this priority order — `SPINDLE_FLUXOPT` first, then `FLUX_SPINDLE`):
```bash
export SPINDLE_FLUXOPT=high    # or: export FLUX_SPINDLE=high
flux run -N4 -n16 -g1 bash run_script.sh
```
To disable entirely (e.g. to A/B against a no-Spindle baseline, or if it's ever suspected of
interfering with a debugger/profiler attach):
```bash
export SPINDLE_FLUXOPT=disable
```

**Not yet verified this session:** the exact `-o spindle.level=high` CLI syntax was reasoned
from the Lua source (`shell.options.spindle = { level = ... }`) but not confirmed with a live
test job (no allocation was spent on a dedicated Spindle A/B test — both live allocations were
committed to the PECAN I/O/compute optimizer validation runs). **Before crediting any startup
delta to Spindle, run a real A/B**: same job, same scale, `SPINDLE_FLUXOPT=disable` vs
`SPINDLE_FLUXOPT=high`, and measure time-to-first-training-step (or wrap `import torch`/
`import torch_geometric`/`import h5py` in `dft_event_logging` if not already annotated, and
diff the aggregate import-phase time between the two runs).

## How to verify it's actually active in a given job

- `env | grep -i spindle` inside the job — the plugin doesn't necessarily export a visible env
  var for "spindle is active," so absence of a var is NOT proof it's off; check `FLUX_SPINDLE_REALPATH`
  (set by the plugin itself as a memoized readlink of the spindle install dir) — if this is set,
  the plugin ran.
- Compare wall-clock time-to-first-step (or first traced event) between runs with
  `SPINDLE_FLUXOPT=disable` and without — Spindle's win only shows up as reduced *import-phase*
  latency, not steady-state throughput, so don't expect it to move steady-state metrics.
- `strings <spindle-plugin>.so | grep level` will show the embedded option help text
  (`libspindleflux.so` under `/collab/usr/global/tools/spindle/toss_4_x86_64_ib_cray_live/lib/`)
  if you need to re-derive the level semantics on a different site build.

## Relationship to `fastload`

`/etc/flux/shell/lua.d/fastload.lua` is a SEPARATE, narrower accelerator (`LD_AUDIT` +
`libfastload2.so`, plus a `GLIBC_TUNABLES` hwcaps tweak) — it's mutually exclusive with Spindle
in the plugin ordering (`if shell.options.spindle == nil and fastloadval == "on"` — fastload
only activates if spindle wasn't already set). Don't combine `-o fastload=on` with an explicit
`-o spindle.level=...` — they're alternate approaches to the same startup-latency problem;
Spindle is the more general/complete one (covers Python module loading, which fastload's
LD_AUDIT-only approach does not).

## Standalone `spindle` CLI (non-flux-native path — NOT recommended on this system)

The bare `spindle` command (`/usr/bin/spindle`, `spindle --help`) is a generic MPI-launcher
wrapper (`spindle mpirun ...` / `spindle srun ...`) with built-in parsers for SRun, JSRun, LSF,
OpenMPI, and a generic `--hostbin=EXECUTABLE` hostlist-script fallback for launchers it doesn't
recognize natively — **there is no native Flux launcher parser** (confirmed via `strings
/usr/bin/spindle | grep Parser`: only `SRunParser`/`JSRunParser`/`OpenMPIParser`/etc., no Flux
entry). Getting the standalone CLI working under `flux run` would require the `--hostbin`
fallback wired to `flux hostlist`, which is unvalidated and unnecessary — **use the flux shell
plugin (`-o spindle.level=...` / `SPINDLE_FLUXOPT`) instead; it's already correctly wired for
this scheduler and is what's active by default today.**

## MEASURED 2026-07-22 — first live A/B, surprising result: `high` was SLOWER, not faster

**Mechanical correction first:** `SPINDLE_FLUXOPT` MUST be exported in the shell BEFORE
`flux run`/`flux proxy` is invoked, NOT set inside the script that gets launched by the job —
the flux shell plugin reads the env var at job-shell-init time (before the launched script even
starts), so setting it inside the launched script is a silent no-op. The env-var form
(`SPINDLE_FLUXOPT=high` / `=disable`) works without error when set correctly. The
`-o spindle.level=high` CLI-flag syntax (reasoned from the Lua source) is STILL untested — the
env var was used instead this round.

**Result (PECAN, single replicate each, import-to-first-training-step latency, same 4N×4GPU
scale):** `SPINDLE_FLUXOPT=disable` → ~98s; `SPINDLE_FLUXOPT=high` → ~167s (**+70% SLOWER, the
OPPOSITE of the hypothesized win**). This directly contradicts this skill's own earlier
reasoning that `high` should help a Python/DL-import-heavy workload by relocating `.py`/`.pyc`
files through the broadcast network.

**Do not adopt `SPINDLE_FLUXOPT=high` on this evidence alone.** This is a single replicate on a
shared multi-tenant system with no `medium`-default baseline run for comparison — plausible
confounds include: contention from other jobs sharing the Spindle broadcast tree/network at the
time of the `high` run, Spindle daemon startup/teardown overhead not amortized over a long
enough job, or a genuine mechanism where `high`'s stricter relocation (rewriting more file
paths, tracking more state) adds overhead that outweighs its shared-FS-read savings for a
job at this particular scale (8 nodes) — Spindle's benefit model assumes large-rank-count jobs
where the O(ranks) vs O(files) savings dominates; at small scale the relocation overhead itself
may dominate instead. **Before drawing any real conclusion, run ≥5 replicates for EACH of
disable/medium(default)/high, ideally on an otherwise-idle allocation to remove the shared-
system confound**, and consider testing at a larger rank count where Spindle's design assumption
(savings scale with rank count) is more likely to hold.

## MEASURED 2026-07-22 (follow-up) — proper 5-replicate A/B, all three levels: `disable` wins decisively; `medium` ≈ `high`

Followed up with 5 replicates each of `disable`, `medium` (default, unset), and `high` on the
same PECAN workload (4N×16-rank, `--exclusive`, own dedicated allocation — no shared-system
confound this time), measuring import-to-first-training-step latency only.

| Level | Mean (s) | Stdev | Min | Max | CV |
| --- | --- | --- | --- | --- | --- |
| `disable` | 47.3 | 14.7 | 36.4 | 65.9 | 31% |
| `medium` (default) | 153.5 | 17.8 | 143.7 | 185.1 | 12% |
| `high` | 146.4 | 2.6 | 143.5 | 149.1 | 2% |

**Verdict, now on real statistics:**
- **`disable` is 3-4x faster than EITHER Spindle level** for this workload/scale (8-node
  allocation, 16 ranks) — solid, not single-replicate noise. Spindle (at either level) adds
  ~100s of import-phase latency for this job rather than removing it. Even `disable`'s
  slowest observed rep (65.9s) beats Spindle's fastest (143.5s) by >2x.
- **`medium` (the site default) and `high` are statistically indistinguishable** — 146.4s vs
  153.5s, well within medium's own noise band (`medium`'s CV is 12%, `high`'s is only 2%, i.e.
  `high` is actually MORE consistent than the default, just not faster). The earlier single-
  replicate "high is 70% slower than disable" finding replicates directionally (both Spindle
  levels are much slower than disable) but the implicit assumption that `medium` would beat
  `high` is wrong — they're roughly tied.
- **Do not adopt Spindle (any level) for PECAN-style Python/PyTorch DL jobs at this scale
  (4-8 nodes) based on this evidence** — `SPINDLE_FLUXOPT=disable` measured fastest and most
  consistently across all 15 replicates. Spindle's benefit model (O(ranks) vs O(files) shared-
  FS-read savings) may simply not pay off yet at 16-64 ranks; whether the crossover point is
  at a much larger rank count is an open question for a future session.
- `-o spindle.level=high` CLI-flag syntax: submits and completes without a flux-shell
  exception (accepted syntax), but its actual effect was NOT independently confirmed — an
  unrelated `flux run --output=<file>` capture quirk blocked verifying it took effect (empty
  output for both a Spindle-flag job and a plain baseline job launched the same way). The
  env-var form (`SPINDLE_FLUXOPT=...`, exported before `flux proxy`/`flux run`) remains the
  only mechanism with a confirmed causal effect on measured latency.
- Caveat: `disable`'s own CV was 31% (reps 1-2 at ~61-66s vs reps 3-5 at ~36-37s, possibly
  page-cache warm-up across sequentially-reused nodes) — doesn't change the top-line
  conclusion, but worth another replicate batch if this specific number needs to be tight.
