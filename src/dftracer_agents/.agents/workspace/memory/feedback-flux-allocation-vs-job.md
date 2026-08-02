---
name: feedback-flux-allocation-vs-job
description: "Never cancel a flux allocation, only jobs within it; requesting N nodes for a run means submitting an N-node job inside an existing allocation, not allocating exactly N new nodes"
metadata: 
  node_type: memory
  type: feedback
---

**Canonical home:** see the `flux-alloc` skill ("Cancelling a job or allocation"
section now states this rule directly, including the incident detail below).

Never run `flux cancel` on a flux allocation (a top-level `flux batch`/`flux alloc` job, typically shown as `NAME=flux` in `flux jobs -a` with the full requested node count). Only cancel JOBS submitted *within* an allocation (e.g. a `flux run`/`flux submit` invocation inside that instance) if something needs to be killed/retried.

**Why:** Allocations are the scarce, slow-to-acquire resource on a shared HPC scheduler — tearing one down forces requeueing and can cost significant wait time, and is disruptive to other work sharing the cluster. The user explicitly corrected this after I called `flux cancel` on several top-level allocation jobs (including their own pre-existing one) while cleaning up what I thought were stray/duplicate allocations.

Separately: when asked to "run the job on N nodes," that means the actual application launch (`flux run -N<n> ...` or equivalent) should target N nodes — it does NOT mean requesting a fresh N-node allocation. Use whatever allocation is already available/running (even if it has more nodes than N) and submit an N-node job inside it. Do not spin up a new allocation sized to exactly N nodes when one is already usable.

**How to apply:** Before calling `flux cancel` on anything, check whether the target is an allocation (top-level, `NAME=flux`, requested via `flux batch`/`flux alloc`) vs. a job submitted inside one — only cancel the latter. When a task specifies a node count for a run, translate that into the `-N` flag of the `flux run`/`flux submit` call inside an existing allocation, not into a new `flux-alloc` request. See [[feedback-flux-alloc-verify-scale]] for the related lesson on verifying run scale actually matches what was requested.

**2026-08-02 clarification:** the user said, verbatim, "you can always use already allocated nodes. submit jobs using flux proxy" — in context, after an earlier allocation the agent was using expired mid-run and the agent picked a different already-live allocation to continue on. Read this as: reusing an existing live allocation (rather than requesting a new one) does not need a fresh confirmation each time it changes. This does not override normal judgment about which allocation is appropriate (size, remaining wall-clock, queue) — still check `flux jobs -a` state and node count before picking one.
