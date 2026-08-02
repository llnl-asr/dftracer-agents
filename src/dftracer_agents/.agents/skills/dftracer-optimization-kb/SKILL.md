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

Recorded: 15 system, 17 software, 42 workload entries.

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
5. **Comparator corroboration required before crediting any "win" (MANDATORY).**
   A 5-rep median/CV comparison alone is not sufficient evidence on a noisy
   shared filesystem — always ALSO run a same-rep `comparator` cross-check
   (baseline repN vs. variant repN) and require it to show a non-negligible,
   ideally statistically-significant delta before trusting a median-level
   improvement. Confirmed on an h5bench session: three separate levers
   (network NIC/rendezvous tuning, isolated rendezvous threshold, NUMA
   cpu-affinity) each showed a large, misleading 5-rep median "improvement"
   (+38-68%) that a same-rep `comparator` cross-check revealed as noise (≤3%
   per-rep, correctly flagged negligible). Record the same-rep comparator
   result in the `notes` field alongside the median/CV numbers whenever a win
   is recorded — a median delta with no comparator corroboration is not yet a
   credited result. See `feedback-h5bench-session-incidents` and
   `project-h5bench-read-write-optimization` for the full incident.
