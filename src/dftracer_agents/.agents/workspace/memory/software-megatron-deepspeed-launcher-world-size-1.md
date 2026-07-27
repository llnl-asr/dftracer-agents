---
name: software-megatron-deepspeed-launcher-world-size-1
description: Megatron-DeepSpeed under flux run can spawn N independent single-rank inits instead of one N-rank distributed group
metadata:
  type: feedback
---

A Megatron-DeepSpeed multi-node baseline run showed every launched process printing `using world size: 1` / `rank: 0` and 14/16 processes crashing with `torch.distributed.DistNetworkError: EADDRINUSE` on the fixed master port (6000).

**Why:** The launcher was spawning N independent single-rank `deepspeed`/`torch.distributed` inits (each self-assigning rank 0, world_size 1) instead of one coordinated N-rank process group sharing a single `MASTER_ADDR`/`MASTER_PORT`. No real NCCL/RCCL collective communication occurred — this is not legitimate distributed-training sparsity, it's a launcher misconfiguration.

**Symptom check:** grep the run log for `using world size:` — if it appears once PER RANK (each showing world_size=1) instead of once total (world_size=N), the launcher is broken, not doing real distributed training.

**How to apply:** Ensure `RANK`/`LOCAL_RANK`/`WORLD_SIZE`/`MASTER_ADDR`/`MASTER_PORT` are set uniquely per Flux task (e.g. via `flux getattr`/rank-derived env, or a hostfile passed to `deepspeed --launcher MPICH`), not left for each process to self-assign. Verify with a quick 2-rank test that only ONE "using world size: N" line (with N matching total ranks) appears before trusting any multi-rank trace as a real distributed baseline. See [[project-megatron-deepspeed-gpt-pipeline]].
