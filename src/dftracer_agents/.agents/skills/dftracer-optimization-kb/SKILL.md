---
name: dftracer-optimization-kb
description: >
  Cross-session, citation-backed knowledge base of every MEASURED dftracer
  optimization, partitioned into system-centric (L3), software-centric (L2),
  and workload-centric (L1) findings. Load this FIRST in any optimization
  loop to avoid re-deriving what is already known.
---

# dftracer optimization knowledge base

**Step 1 of every optimization loop is recall, not proposal.** Call
`opt_kb_lookup(system=..., workload=..., software=..., scope=...)` before
generating any proposal, and cite prior results in the proposal table.

Scopes and what they transfer to:

| Scope | Level | Transfers to | File |
| --- | --- | --- | --- |
| system | L3 | any workload **on that system** | [system.md](system.md) |
| software | L2 | any workload **linking that software**, any system | [software.md](software.md) |
| workload | L1 | that application, **any system** | [workload.md](workload.md) |

Recorded: 6 system, 4 software, 12 workload entries.

## A second, orthogonal axis: metric_scope

`scope` (above) answers *who inherits* a finding. `metric_scope` answers a
different question — *which metric moved*:

| metric_scope | What it measures |
| --- | --- |
| `app` (default) | The app's own trace: epoch/I-O time, app-observed bandwidth. |
| `system` | A filesystem/system-level outcome: aggregate achieved bandwidth,
  reduced filesystem load — currently a trace-derived proxy, not real
  OST/MDT-side telemetry (this pipeline has no Lustre-admin monitoring access). |

**Non-degradation guard (MANDATORY):** a `metric_scope="system"` entry MUST
carry a paired `app_metric`/`app_before`/`app_after` — `opt_kb_record` rejects
one without it. If the paired app metric regressed more than 2.0%, the verdict is force-set to `regression`
regardless of how good the system-side number looks. A system optimization
that costs the app is not a win — never apply/keep one where this guard fired.

## Rules

1. Record only **measured** results — `metric`, `before`, `after` are required.
   Record failures too: knowing a lever did nothing is a result.
2. Every entry carries a **citation**: paper (preferred) > official docs > web.
   `session:<run_id>` marks a result measured in-house, never external evidence.
3. Apply optimizations **one at a time** and measure each, or the attribution
   is worthless.
4. A `metric_scope="system"` entry always carries its paired app-metric proof
   — see the non-degradation guard above.
5. **Median-of-N alone is not sufficient to call a lever a win — require
   comparator corroboration.** Confirmed 2026-07-10 on h5bench/Tuolumne: three
   independent network/memory-layer levers (NIC policy, rendezvous threshold,
   NUMA cpu-affinity) each showed a large, tempting median-of-5 delta
   (+38.6%, +68.2%, and a CV that got worse respectively) that a same-rep
   `comparator` cross-check revealed as noise (+3.3%, +3.0%, -0.5%,
   all flagged negligible) — the shared-Lustre contention noise floor on this
   system is large enough to produce a misleadingly large median delta from
   pure variance alone, even at 5 replicates. **Before recording ANY verdict
   above `no_change`/`inconclusive`, run a same-rep (or matched-rep)
   `comparator` check in addition to the median/CV comparison** — a real win
   needs BOTH a large median delta AND ranges that don't overlap AND a
   comparator-confirmed per-operation mechanism (e.g. matching what the
   literature says the lever should change). Contrast with the one confirmed
   win this pass (ROMIO `cb_nodes`+striping): non-overlapping 5-rep ranges,
   +490.5% median, AND comparator-corroborated (open/lseek/lxstat all
   significantly faster, transfer size change exactly matching the aggregator
   math) — that is the bar. Don't let the KB tool's automatic verdict field
   (which can trigger off raw median magnitude alone) substitute for this
   check; put the honest call in the `notes` field if the two disagree.
