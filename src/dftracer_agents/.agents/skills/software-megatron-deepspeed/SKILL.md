---
name: software-megatron-deepspeed
description: Megatron-DeepSpeed (GPT/BERT LLM pretraining, PyTorch + DeepSpeed + apex) build/annotate/run/optimize caveats on AMD ROCm + Cray PE + Flux — launcher and rank wiring, required CLI flags, dataset sizing, dftracer annotation pitfalls, and MEASURED optimization results. Load this skill for any Megatron-LM or Megatron-DeepSpeed workload.
---

# software-megatron-deepspeed

Verified against the argonne-lcf/Megatron-DeepSpeed fork, GPT-3 Medium 350M, on an AMD MI300A
(gfx942) Cray PE cluster with Flux, ROCm 6.4.3, torch 2.9.1+rocm6.4, DeepSpeed 0.19.3.

Prerequisites live in sibling skills: [[software-rocm]], [[software-rccl]],
[[software-apex-rocm]]. Read those first — most "Megatron is broken" symptoms originate there.

## Required CLI flags that are easy to miss

- **`CUDA_DEVICE_MAX_CONNECTIONS=1`** is hard-required by `arguments.py` validation for async
  gradient all-reduce, **even on ROCm** (it is an env-var name check, not CUDA behaviour).
  Without it: `RuntimeError: Using async gradient all reduce requires setting ...`.
- **DeepSpeed must be enabled explicitly**: `--deepspeed --deepspeed_config <json> --zero-stage N`.
  Omit these and `args.deepspeed_config_dict` is never populated, producing
  `AttributeError: 'Namespace' object has no attribute 'deepspeed_config_dict'` deep inside
  `model_provider`. Generate the JSON from `examples_deepspeed/rebase/ds_config_gpt_TEMPLATE.json`
  using the sed-substitution pattern in `ds_pretrain_gpt_125M.sh` (GBSIZE/MBSIZE/LOG_INTERVAL/
  ZERO_STAGE/PRESCALE_GRAD).
- **Verify flags actually took effect** by reading the run's own argument dump, not by trusting
  that you passed them. Invented flags (e.g. `--use-gpu-only-model`, `--gpu-rank`) fail loudly
  with an argparse error, but some real flags fail *silently* — see flash-attention below.

## `--use-flash-attn*` silently no-ops

All four variants (`-v1`, `-v2`, `-triton`, `--use-flash-attn-builder`) require either the
`flash_attn` package or DeepSpeed's `FlashAttentionBuilder`, which returns `None` on
DeepSpeed 0.19.3 + ROCm. Megatron leaves `use_flash_attn=False` **without erroring**. Always
confirm the resolved value in the arg dump. FlashAttention remains the highest-ceiling untested
lever for this stack but needs a multi-hour ROCm/CK build.

## Unconditional vision import breaks text-only runs

`megatron/training.py` imports `megatron.model.vision.knn_monitor` → `vit_dataset` →
`torchvision` at module scope, even for a pure GPT run. Since PyPI torchvision is not ABI-
compatible with a custom ROCm torch (see [[software-rocm]]), installing it is the *wrong* fix.

**Fix:** wrap the import in `try/except ImportError` with a stub that only raises if actually
called. The real call site is already gated by `args.vision_pretraining_type == "dino"`, never
true for GPT/BERT text pretraining.

## Distributed launcher and rank wiring (the biggest time sink)

**Symptom A — every rank thinks it is alone:** every process prints `using world size: 1` /
`rank: 0`, and all but one crash with `EADDRINUSE` on the master port.
*Root cause:* the launcher spawned N independent single-rank inits instead of one N-rank group.
*Diagnostic:* grep the log for `using world size:` — it must appear ONCE with the correct N, not
once per rank.

**Symptom B — `device_id cuda:N is out of range`:** under `flux run -g1`, each task gets
exclusive, *remapped* visibility of exactly ONE GPU (always index 0 from its own view).
`LOCAL_RANK` must therefore be **0**, not `RANK % tasks_per_node`.

**Symptom C — Cray PMI collision:** launching via plain `torchrun` (which spawns several
Python subprocesses per node) on a Cray system yields
`inet_listen_socket_setup:bind() failed ... Address already in use` and
`mpid_cray_pmi_init: Assertion 'PMI2_Initialized()' failed` → SIGABRT on all ranks. Cray's PMI
expects one PMI-aware client per node coordinated by the site launcher, not N self-spawned
subprocesses.
*Fix:* use the site's native one-task-per-rank launch (e.g. `flux run -N<nodes> -n<total_ranks>`)
with `RANK`/`WORLD_SIZE`/`LOCAL_RANK`/`MASTER_ADDR`/`MASTER_PORT` exported in the same script
that execs python; or use the site's supported launcher wrapper (e.g. LLNL `hpc-launcher`'s
`torchrun-hpc`, where `-N` is nodes and `-n` is procs-PER-NODE).

Resolve `MASTER_ADDR` dynamically from the job's own node list (first node), identically on
every rank — do not hardcode it.

## Dataset sizing: never point a smoke test at a production corpus

`_build_index_mappings` runs **single-rank and is unbounded by `--train-iters`** — it builds a
`sample_idx.npy` sized to the FULL `--data-path` corpus before the first iteration. A full
corpus produced a 67 GB+ growing index that never finished inside a time-boxed allocation.

**Fix:** pre-slice a few thousand documents with Megatron's own `MMapIndexedDataset` /
`MMapIndexedDatasetBuilder` and point `--data-path` at the slice. Store it on the parallel
filesystem, not the workspace.

**Index-cache races:** with N ranks building the cache concurrently you get truncated `.npy`
files → `EOFError: No data left in file` from `numpy.load` inside `_build_index_mappings`.
**Fix:** pre-build the cache single-rank first (same params — the cache key depends on
num_samples/seq_length/seed, not world size), then launch N ranks against it. Delete stale
cache dirs after any crashed run; leftovers are silently corrupt.

## dftracer annotation notes

- Scope to the entry point + training loop + checkpoint + data path: `pretrain_gpt.py`,
  `megatron/{training,checkpointing,utils,initialize,arguments}.py`,
  `megatron/data/{gpt_dataset,indexed_dataset,data_samplers,blendable_dataset}.py`.
  ~97% function coverage is achievable; the residual is argparse-builder helpers (Rule 0 skips).
- **`initialize_log(process_id=...)` must be the rank**, not `-1`. With `-1` every rank writes
  to the same path, contends on the file lock, and only 1-2 ranks produce non-empty traces
  while the rest silently emit 0-byte files.
- Pass the log path/data dir explicitly from env rather than relying on `None` defaults:
  `initialize_log(logfile=os.environ.get('DFTRACER_LOG_FILE'), data_dir=os.environ.get('DFTRACER_DATA_DIR'), process_id=rank)`.
- `DFTRACER_ENABLE` is read at **module import time**; export it before python starts or
  dftracer silently installs a no-op profiler.
- `@_dft.log` must be INNERMOST relative to `@classmethod`/`@staticmethod`, and must not be
  stacked above `@lru_cache` (the wrapper cannot introspect those objects).
- `train_step` is a leaf in the default annotation: DDP/DeepSpeed fuse gradient reduction into
  `backward()`, so **collective time is invisible** without HIP tracing. For communication
  diagnosis set `wall_clock_breakdown: true` in the DeepSpeed config to get a measured
  `backward_allreduce` number.

## MEASURED optimization results (350M params, 16 ranks, 4 nodes, ZeRO-0, fp16)

Baseline steady-state ≈ 142-145 samples/sec. Iteration 1 is apex-JIT warmup and MUST be
excluded. Cross-replicate noise band ≈ 4%.

| Change | Result | Verdict |
|---|---|---|
| gloo → nccl/RCCL backend | +1.2% | no change (see [[software-rccl]]) |
| DataLoader `num_workers` 0 → 4 | −2.0% | no change |
| `TORCH_BLAS_PREFER_HIPBLASLT` 1 or 0 | ±2% | inert both directions |
| **DeepSpeed ZeRO stage 0 → 1** | **−20% (gloo), −23% (RCCL)** | **REGRESSION** |
| **fp16 → bf16** | **−25%** | **REGRESSION** |

**Headline: the default configuration is already near-optimal at this model size.** Kernel
fusion is already fully applied out of the box; ZeRO stage-raising costs more in extra
collective round-trips than it saves (measured optimizer-state construction was only 2.0% of
runtime — there is no capacity pressure to relieve on a large-HBM APU); and bf16 is slower
because DeepSpeed's `BF16_Optimizer` path is less optimized than its fused fp16 loss-scaler
path, **not** because the hardware lacks bf16.

**Profile shape:** Training ~67%, init ~29%, POSIX I/O ~0.8%. Steady-state training issues
**zero POSIX ops** — all in-train I/O belongs to iteration 1 (JIT artifacts + dataset
first-touch); later iterations are served from page cache. The large STDIO op count is a
pre-train Python `site-packages` import storm, not logging, and does not scale with iterations.

**Scope every conclusion:** the results above come from short runs on a small sliced dataset.
Low absolute I/O here does NOT prove I/O is irrelevant at production scale, and ZeRO stages
remain justified at model sizes where optimizer state genuinely does not fit.
