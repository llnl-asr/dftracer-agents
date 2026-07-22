---
name: feedback-shared-source-tree-race
description: Running two optimizer subagents in parallel against the same session's shared annotated/source/ tree can crash an in-flight DDP job with NCCL "remote process exited" when one agent edits a file the other's job is mid-loading
metadata:
  type: feedback
---

Symptom: an in-flight multi-rank DDP training job crashed with NCCL `remote process exited`
partway through a validation run.

Root cause: two optimizer subagents (I/O and compute) were dispatched in parallel on separate
flux allocations against the SAME session workspace's `annotated/source/` tree. While the I/O
agent's job was running, the compute agent concurrently edited a shared file
(`model/model_trainer.py`, adding `torch.compile`). Different ranks of the I/O agent's job
loaded different versions of that file mid-run (some before the edit landed, some after),
which desynchronized DDP state across ranks and crashed the collective.

Fix applied in the moment: the compute agent had already added an env-gate
(`os.getenv("PECAN_TORCH_COMPILE", "1")`) around its change — setting `PECAN_TORCH_COMPILE=0`
in the I/O agent's own run script cleanly isolated its measurement without needing to touch or
revert the other agent's file.

**Why:** parallelizing optimizer subagents across separate flux allocations is a legitimate way
to save wall-clock time (validated this session — I/O and compute proposals for PECAN were
measured in parallel successfully after this incident was caught), but the shared, un-versioned
`annotated/source/` directory is a real race surface once more than one agent can write to it
while a job is reading from it over NFS/PFS.

**How to apply:** when dispatching multiple optimizer subagents in parallel against files under
the same session's `annotated/source/`, prefer ONE of:
(a) give each subagent a private snapshot/copy of the source tree for its own run (cleanest —
    no shared-file race possible), or
(b) if editing the shared tree directly (cheaper, what this session did), require every change
    to be behind an env-var gate that defaults to the PRE-change behavior, so a concurrent job
    that doesn't set the var is unaffected by an in-flight edit — this is what actually saved
    the run here.
Either way, an agent whose job crashes unexpectedded while a sibling agent is running in
parallel should check for an unexpected diff in files it did not itself touch before assuming
its own change caused the failure.
