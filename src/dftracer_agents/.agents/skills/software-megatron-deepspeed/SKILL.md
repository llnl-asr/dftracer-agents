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

## BERT data is NOT interchangeable with GPT data

GPT and BERT need **different tokenizations**, so a GPT dataset cannot be reused for BERT:

| | GPT | BERT |
|---|---|---|
| `--tokenizer-type` | `GPT2BPETokenizer` | `BertWordPieceLowerCase` |
| vocab files | `gpt2-vocab.json` + `gpt2-merges.txt` | a single `vocab.txt` (e.g. `bert-large-uncased-vocab.txt`) |
| produced by | `tools/preprocess_data.py` | `tools/preprocess_data.py` (different flags) |
| dataset stem | `..._text_document` | `..._text_sentence` |

`examples_deepspeed/bert_with_pile/prepare_pile_data.py` shows the expected BERT flow. Both
end up as Megatron mmap `.bin`/`.idx` pairs, but the token IDs are incompatible — pointing BERT
at GPT2-BPE data is silently wrong, not an error.

**Also not directly usable:** TensorFlow-era BERT `.tfrecord` datasets. They need conversion
AND retokenization into Megatron's mmap-indexed format.

**Practical consequence:** budget dataset preparation as real work on the critical path for any
BERT run — download the vocab, obtain a text corpus, and run `tools/preprocess_data.py`
yourself. Then pre-slice it (see the dataset-sizing section above) before any smoke test.

## `preprocess_data.py --partitions 1` returns early when splitting sentences

**Symptom:** you pass `--split-sentences` for BERT, the command appears to finish, but you get
no `.bin`/`.idx` at all (only a `<input>_ss.jsonl`), or — if you then "work around" it by
re-running without the flag — you silently get the WRONG `_text_document` (document-level)
stem instead of `_text_sentence`.

**Root cause:** with `--partitions` at its default of 1, `tools/preprocess_data.py` writes the
sentence-split `_ss.jsonl` and then hits an `if args.partitions == 1: return` immediately after
the split block. It never reaches the encode step in that same invocation.

**Correct two-call recipe:**
1. Run with `--split-sentences` → produces `<input>_ss.jsonl`, then returns.
2. Run AGAIN with the SAME `--input` (the ORIGINAL jsonl, not the `_ss` file) and
   `--split-sentences` STILL set. The tool sees `_ss.jsonl` already exists, skips re-splitting,
   and proceeds into encoding at `level="sentence"`, emitting `_text_sentence.bin/.idx`.

Do NOT pass `--input <file>_ss.jsonl` with the flag dropped — that is what silently yields the
document-level stem BERT cannot use.

`--split-sentences` also requires NLTK `punkt`/`punkt_tab`; install and point `NLTK_DATA` at a
session-local dir.

## The `ezpz` dependency is name-squatted on PyPI

This fork imports `ezpz` (Argonne's `github.com/saforem2/ezpz`), but the PyPI package named
`ezpz` is an unrelated image-crop tool. Installing it does not satisfy the import, and
`megatron/training.py` + `megatron/timers.py` both depend on the real symbols.

**Fix:** drop a small functional shim into the venv's site-packages providing
`get_rank`/`get_world_size`/`get_hostname`/`get_torch_device`/`breakpoint` plus `dist` and
`profile` submodules, backed by `torch.distributed`/stdlib. It is load-bearing for training,
not just for data prep.

## BERT-specific run flags and build gotchas

- **`--no-pipeline-parallel` is REQUIRED for BERT** in this fork. Without it `train_step`
  asserts `isinstance(model[0], deepspeed.PipelineEngine)` and dies.
- **`--eval-iters 0` is not a valid "disable eval"** — it raises
  `ValueError: Need to specify either max_num_samples or num_epochs`. Use `--eval-iters 1`.
- **`megatron/data/helpers*.so` ABI-tag mismatch:** the Makefile calls `python3-config`, which
  can resolve to a system/anaconda python rather than the session venv, producing a wrongly
  tagged `helpers.cpython-3X-*.so` that the venv's interpreter refuses to import. Rename (or
  rebuild with the venv's `python3-config`) to match the venv's actual ABI tag.
- **This fork is Argonne's:** scripts under `ALCF/` and many `examples_deepspeed/**` launchers
  hardcode ALCF module stacks (`conda`, `cudatoolkit`, `pytorch/2.0.1`) that do not exist
  elsewhere. Never source them on another site — take their ARGUMENT blocks only, and drive
  `pretrain_bert.py` / `pretrain_gpt.py` directly from your own env script.

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

## The capability probe runs on the LOGIN NODE — never spend an allocation on it

```bash
source <WS>/scripts/env.sh
python3 -c "import amp_C, fused_layer_norm_cuda, fused_weight_gradient_mlp_cuda; \
from apex.optimizers import FusedAdam; import flash_attn"   # last one is expected to fail
```
This reproduces every compute-node answer in seconds. Verified current stack: apex fused HIP
kernels **PRESENT** (`amp_C`, `fused_layer_norm_cuda`, `fused_weight_gradient_mlp_cuda`,
`apex.optimizers.FusedAdam` all import); `flash_attn` **ABSENT** and DeepSpeed
`get_op_builder('FlashAttentionBuilder')` -> `None`; `transformer_engine` **ABSENT** (so fp8 is
off the table); `triton` present but Megatron's triton attention path still imports the
`flash_attn` package, so the package is required either way.

Treat this probe's output as **authoritative over any premise in a dispatch brief**, including
one sourced from a prior session's report — capability claims go stale.

## Subtract iteration 1 before quoting a kernel ceiling

`forward_backward_no_pipelining` includes the iteration-1 JIT/autotune warmup. Measured at 400
iters (BERT-Base, 16 ranks): 53.0s raw = 22.2% of wall, but only **~37s = ~15.4% steady state**
once iteration 1 (fwd 11.0s + bwd 5.0s) is removed. Quoting the raw span overstates the
kernel-tuning ceiling by ~1.5x.

## Two non-kernel levers that beat most kernel work on this stack

- **`Timers.log` / `_get_global_min_max_time`: 6.97s = 2.9% of wall** — a distributed timer
  all-reduce on EVERY iteration at `--log-interval 1`. Raising it to 10 changes telemetry
  granularity only, not the computation or iteration count.
- **At `--num-workers 0`, MLM masking sits INLINE on the critical path** —
  `BertDataset.__getitem__` 9.11s + `create_masked_lm_predictions` 8.02s = **4.0% of wall**.
  Unlike GPT (which does zero steady-state data work), BERT does real per-batch CPU work, so
  `--num-workers 2-4` is a genuine overlap lever here. **Do not carry the GPT "num_workers is
  inert" result over to BERT** — the workloads differ in exactly this respect.

## `initialize_megatron` (~40s) is NOT I/O — do not route it to the I/O dimension

Measured breakdown of the ~40s init on ROCm/Tuolumne, 16 ranks:
`_initialize_distributed`/`finish_mpu_init` **21.3s** (NCCL/torch.distributed rendezvous →
communication) + `_compile_dependencies` **18.5s**, of which 13.1s is
`torch.utils.cpp_extension.load` (→ compute). **I/O inside the whole init window is 0.167s =
0.42%.** The largest single I/O item is `__xstat64` at 0.031s.

## Measured memory footprint (skip the capacity investigation)

BERT-Base 110M / seq 512 / micro_batch 4 / ZeRO-0 / 16 ranks on MI300A:
**~13.1 GiB/rank = ~10.5% of node HBM** (52.5 GiB/node of ~501 GiB). Zero swap, no writeback
pressure, no huge pages in use. Analytic check: ZeRO-0 state 1.76 GB + activations 1.40 GB;
remainder is HIP context + RCCL buffers + caching-allocator cache. **There is no memory-capacity
pressure at this scale** — ZeRO stage-raising, activation recomputation and huge pages are all
inert-or-harmful here.

## MEASURED optimization results (BERT-Base, 16 ranks, 400 iters, replicated)

Metric is `train_step` total for 400 iterations. **Measured run-to-run noise band on this
system is ~5% on the socket transport and ~2% once the libfabric plugin is in use** — treat any
single-sample delta below that as unmeasured.

| Config | n | mean (s) | spread | vs libfabric baseline |
|---|---|---|---|---|
| socket fallback (no RCCL net plugin) | 3 | 177.92 | 4.9% | **+75.8%** |
| **+ aws-ofi-rccl plugin on LD_LIBRARY_PATH** | 4 | **101.23** | 1.9% | baseline |
| **+ `--num-workers 2`** | 2 | **92.88** | 1.8% | **-8.2%** |
| + `--log-interval 10` | 1 | 104.48 | — | +3.2% (no win) |
| + ZeRO-1 | 1 | 121.52 | — | **+20.0% regression** |

**Best stack = libfabric plugin + `--num-workers 2`: -47.8% vs the original socket/nw0 config.**

Key points:
- **The transport fix dominates everything else** (-43.1% on its own). See [[software-rccl]] —
  it is a one-line `LD_LIBRARY_PATH` addition, and RCCL silently falls back to host TCP without
  it. Fix this before evaluating any other lever, because it changes every other ceiling.
- **`--num-workers 2` only became a real win after the transport fix.** On the socket transport
  it measured as inert (data cost was hidden behind a 43%-of-wall all-reduce); with fast
  collectives, BERT's inline MLM masking (`create_masked_lm_predictions`) became a visible
  8% of the step. **Ceilings computed against a broken baseline are stale — re-measure levers
  after any large fix.**
- **ZeRO-1 regresses ~20% on BOTH transports** (socket: -20/-23%, libfabric: +20.0% slower).
  It is not a transport artifact: at 110M params the optimizer state is ~1.4% of HBM, so there
  is no capacity pressure to relieve and the extra collectives are pure cost.
- Fixing the transport also cut run-to-run variance ~3x (4.9% -> 1.9%), because TCP contention
  was itself a major noise source.

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
