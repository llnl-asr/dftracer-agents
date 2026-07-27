---
name: software-megatron-deepspeed-smoke-data-slicing
description: Never point Megatron smoke tests at a full-scale corpus — pre-slice a few thousand documents with MMapIndexedDataset
metadata:
  type: feedback
---

Megatron's `_build_index_mappings` runs single-rank and is unbounded by `--train-samples` — it builds a `sample_idx.npy` sized to the FULL `--data-path` corpus before any training iteration starts. Pointing a smoke test at a full production corpus (e.g. full oscar/openwebtext, 288M+ docs) produces a 67GB+ growing index that never finishes in a time-boxed allocation.

**How to apply:** Always pre-slice a small (~2000-5000 document) subset using Megatron's own `MMapIndexedDataset`/`MMapIndexedDatasetBuilder` classes directly (read N docs from the real tokenized `.bin`/`.idx`, write a truncated pair), and point the smoke test's `--data-path` at that slice instead. Store the slice under the session's Lustre-backed `dataset/` symlink, not the workspace itself. See [[software-megatron-deepspeed-rocm-run-pitfalls]], [[project-megatron-deepspeed-gpt-pipeline]].
