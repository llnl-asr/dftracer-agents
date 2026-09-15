---
name: system-dane-pointer
description: dane (LLNL CTS-2, CPU-only Sapphire Rapids, Slurm) is characterized and registered — load the system-dane skill, don't re-derive
metadata:
  type: reference
---

dane is registered in `systems.yaml` (srun, gcc/12.1.1-magic + mvapich2/2.3.7,
`tracing.papi_module: ""`, `tracing.power: off`) and fully documented in the
**[[system-dane]]** skill. Load that skill for any dane session; do not
re-derive the machine.

The three facts most likely to be got wrong by copying a sibling machine:

1. **dane is CPU-ONLY**, but `/usr/bin/nvidia-smi` exists and *eight* `/opt/rocm-*`
   trees exist (6.4.3 and 7.2.0 are complete userspaces). Any probe keying on path
   existence reports a GPU here — `system_detect`'s power probe does exactly that.
2. **No power source at all** — msr root-only, no msr_safe, RAPL 0400, PAPI rapl
   disabled, and no GPU for an NVML/rocm-smi fallback. Leaving variorum enabled is
   worse than useless: on this 2-socket node a service pinned to 1 core makes
   variorum `exit()` and kills the daemon.
3. **PAPI = 17 presets / 19 counters** via the SYSTEM lib (5.6.0.0). The
   `papi/6.0.0.1` module reports 0/0 and fails silently. Same CPU as matrix, so
   [[system-matrix]]'s PAPI numbers transfer; an MI300A/Cray 30-preset plan does not.
   There are **no cache-miss presets** on this CPU.

Also: `/p/lustre5` is NOT mounted on dane (it is `/p/lustre1..3` + `/p/vast1`), so a
session `dataset/` symlink inherited from tuolumne or matrix dangles silently.

**Why:** dane is the odd one out — the only CPU-only machine among tuolumne
(MI300A), tioga (MI250X) and matrix (H100) — so GPU-shaped assumptions carry over
without failing loudly.

**How to apply:** `skill_load(name="system-dane")` at the start of any dane work,
and check for a Slurm bank first (`sacctmgr show assoc user=... cluster=dane`) —
without one, every submit including `--test-only` is rejected with a message that
blames the partition, not the account.
