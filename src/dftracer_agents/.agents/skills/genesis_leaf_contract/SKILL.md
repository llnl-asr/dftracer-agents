---
name: genesis_leaf_contract
description: The published-leaf contract for a genesis trace corpus — exactly which artifacts every leaf in the shared archive must carry (data, COMPACTION_NOT_RUN.md, summary.json, run_<set>.json, validation_<set>.json), why each clause exists, and the deterministic checker that enforces it. Load before publishing a leaf, before declaring a corpus complete, and whenever two sweeps must be consistent with each other.
---

# genesis_leaf_contract

A genesis corpus is published leaf by leaf, often across many allocations, several
machines and more than one sweep. Without an enforced contract each round quietly
publishes slightly less than the last, and the gaps are only discovered by whoever
tries to *use* the corpus — long after the nodes that could have fixed it are gone.

**This skill is the contract plus its checker.** Prose alone has already failed
here: leaves were published with a stale `COMPACTION_NOT_RUN.md`, with 2 of 9
`validation_*.json`, and with `summary.json` reporting `hosts: []`. Every one of
those passed a human read-through and would have shipped.

---

## The contract

Every directory matching `<archive>/<case>/nodes_<N>/ppn_<P>/` must satisfy ALL of:

| clause | requirement |
| --- | --- |
| **DATA** | at least one copy of the traces — compacted chunks (`*.pfw.gz` at the leaf top level or in `compacted/`) **or** `raw/papi_*/**.pfw.gz` |
| **NOTE** | no chunks ⇒ `COMPACTION_NOT_RUN.md` **must** exist · chunks ⇒ it **must not** |
| **SUMMARY** | `summary.json` present, parseable, its `leaf` ending in this cell's own `<case>/nodes_<N>/ppn_<P>`, and `ranks == nodes*ppn` |
| **RUN JSON** | one `run_<set>.json` per validated PAPI set |
| **VALIDATION** | one `validation_<set>.json` per set |
| **EXACTNESS** | every run record reports `multiplex` of exactly `0` |
| **COMPLETE** | every run record reports `app_files == ranks` |

### Why each clause is there, not just what it says

* **DATA** — a leaf with neither chunks nor `raw/` is an empty directory
  impersonating data. It happens when a publish is interrupted between `mkdir` and
  the copy.
* **NOTE** — the two directions matter equally. Missing note on an uncompacted leaf
  leaves a reader no way to know `raw/` is the authoritative copy. A *stale* note on
  a compacted leaf makes a finished leaf look unfinished, which is how work gets
  needlessly redone.
* **SUMMARY `leaf` and `ranks`** — catches a `summary.json` copied from another
  cell, or generated against the wrong `(nodes, ppn)`. Both are easy to produce and
  invisible without the cross-check.
* **RUN JSON** — one leaf is N runs (one per PAPI set). `summary.json` aggregates
  them, so it cannot say *which* run multiplexed or *which* lost a rank. Without
  the per-run record a defect is detectable but not locatable.
* **EXACTNESS** — `multiplex != 0` means PAPI time-shared the counters and the
  values are **scaled estimates**, not measurements. dftracer reports this in the
  trace but nothing refuses it; the corpus looks healthy and the numbers are wrong.
* **COMPLETE** — `app_files < ranks` is a missing rank. No later processing can
  reconstruct it, so it must fail at publish time, while a rerun is still cheap.

---

## The checker

```bash
genesis_validate_leaf.py <archive_root> [--sets=N] [--json]
```

Exits `0` only when every leaf passes; prints one `FAIL <leaf>: <reasons>` line per
violation. `--sets=N` additionally asserts each leaf carries N run records (the size
of this machine's PAPI partition), which catches a leaf that sealed early.

Run it **before declaring a corpus complete**, and after any publish round.

```bash
# measured on the amg/tuolumne corpus
$ genesis_validate_leaf.py .../dftracer-traces/amg/tuolumne --sets=9
343/343 leaves satisfy the published-leaf contract
```

### A checker that only ever passes is worthless — prove it fails

Never trust a green run you have not falsified. Each clause was verified against a
deliberately broken copy of a good leaf:

| control | expected | result |
| --- | --- | --- |
| verbatim good leaf | pass | `1/1 satisfy` |
| `summary.json` deleted | fail | `missing summary.json` |
| chunks **and** `raw/` deleted | fail | `no data: neither compacted chunks nor raw/ traces` |
| note added beside chunks | fail | `compacted but stale COMPACTION_NOT_RUN.md present` |
| run record set to `multiplex={1:5}`, `app_files=1` | fail | `PAPI multiplexed (estimates)`; `app_files 1 != ranks 2` |

Re-run these controls whenever a clause is added or the checker is edited.

---

## Where the artifacts live, and the two traps

* `run_<set>.json` and `validation_<set>.json` live **inside `raw/`** so they travel
  with the traces they describe. When `raw/` has been reclaimed after compaction
  they sit at the **leaf top level** instead. **A checker must look in both** — one
  that only checks `raw/` reports every compacted leaf as missing its records.
* Compacted chunks in this archive are at the **leaf top level**
  (`app-<n>_chunk0.pfw.gz`), *not* in a `compacted/` subdirectory. Checking only for
  `compacted/` wrongly stamps `COMPACTION_NOT_RUN.md` onto leaves that are already
  compacted — measured, on a real publish.

## Match the archive's existing shape before inventing a new one

`summary.json` is the established per-leaf file (`laghos/*` publishes it; its key
names are what consumers read). `run_<set>.json` is an addition, not a replacement.
Check a neighbouring app's published leaves before adding any new file to a corpus —
a parallel convention is worse than a slightly imperfect shared one.

## Related

[[genesis_run]] — STEP 12 defines the publish order and both metadata schemas.
