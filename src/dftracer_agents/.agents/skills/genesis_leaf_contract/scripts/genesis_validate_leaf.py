#!/usr/bin/env python3
"""Validate that a PUBLISHED leaf carries everything the corpus contract requires.

This is the consistency gate across runs: a leaf either satisfies every clause or
it is reported, so one sweep cannot quietly publish less than another.

Usage:
    genesis_validate_leaf.py <archive_root> [--sets N] [--json]

Exit 0 only when every leaf passes. Prints one line per failing leaf.

The contract, and why each clause exists:

  DATA        at least one copy of the traces -- compacted chunks OR raw/.
              A leaf with neither is an empty directory pretending to be data.
  NOTE        no chunks  => COMPACTION_NOT_RUN.md MUST exist (raw/ is then the
              authoritative copy and a reader has to be told).
              chunks     => the note MUST NOT exist (a stale note makes a
              complete leaf look unfinished).
  SUMMARY     summary.json present and parseable, its `leaf` matching its own
              path, and ranks == nodes*ppn. Catches a summary copied from
              elsewhere or generated against the wrong cell.
  RUN JSON    one run_<set>.json per validated set. This is the per-run
              provenance; summary.json cannot say WHICH run multiplexed.
  VALIDATION  one validation_<set>.json per set, so the capture-time content
              check travels with the data.
  EXACTNESS   every run reports multiplex 0. Anything else means PAPI
              time-shared and the counters are scaled estimates.
  COMPLETE    every run reports app_files == ranks. A short rank set is a hole
              in the corpus that no later processing can fill.
"""
import glob, json, os, sys


def leaf_cells(root):
    for d in sorted(glob.glob(os.path.join(root, "*", "nodes_*", "ppn_*"))):
        if os.path.isdir(d):
            yield d


def check(leaf, want_sets=None):
    errs = []
    case = os.path.basename(os.path.dirname(os.path.dirname(leaf)))
    nodes = int(os.path.basename(os.path.dirname(leaf)).removeprefix("nodes_"))
    ppn = int(os.path.basename(leaf).removeprefix("ppn_"))
    ranks = nodes * ppn
    raw = os.path.join(leaf, "raw")

    chunks = glob.glob(os.path.join(leaf, "*.pfw.gz")) + \
             glob.glob(os.path.join(leaf, "compacted", "*.pfw.gz"))
    raw_traces = glob.glob(os.path.join(raw, "papi_*", "*.pfw.gz"))
    note = os.path.join(leaf, "COMPACTION_NOT_RUN.md")

    # DATA
    if not chunks and not raw_traces:
        errs.append("no data: neither compacted chunks nor raw/ traces")
    # NOTE
    if not chunks and not os.path.exists(note):
        errs.append("uncompacted but no COMPACTION_NOT_RUN.md")
    if chunks and os.path.exists(note):
        errs.append("compacted but stale COMPACTION_NOT_RUN.md present")

    # SUMMARY
    sp = os.path.join(leaf, "summary.json")
    summ = None
    if not os.path.exists(sp):
        errs.append("missing summary.json")
    else:
        try:
            summ = json.load(open(sp))
        except Exception as e:
            errs.append(f"summary.json unparseable ({e})")
    if summ is not None:
        if not str(summ.get("leaf", "")).endswith(f"{case}/nodes_{nodes}/ppn_{ppn}"):
            errs.append(f"summary.leaf mismatch: {summ.get('leaf')!r}")
        if summ.get("ranks") != ranks:
            errs.append(f"summary.ranks {summ.get('ranks')} != nodes*ppn {ranks}")

    # RUN JSON / VALIDATION -- in raw/ when present, else at the leaf top level
    src = raw if os.path.isdir(raw) else leaf
    runs = glob.glob(os.path.join(src, "run_*.json")) + \
           glob.glob(os.path.join(leaf, "run_*.json"))
    vals = glob.glob(os.path.join(src, "validation_*.json")) + \
           glob.glob(os.path.join(leaf, "validation_*.json"))
    runs = sorted(set(runs)); vals = sorted(set(vals))
    if not runs:
        errs.append("no run_<set>.json")
    if not vals:
        errs.append("no validation_<set>.json")
    if want_sets and len(runs) and len(runs) < want_sets:
        errs.append(f"only {len(runs)} run_*.json, expected {want_sets}")

    # EXACTNESS + COMPLETE, from the per-run records
    for rp in runs:
        try:
            r = json.load(open(rp))
        except Exception as e:
            errs.append(f"{os.path.basename(rp)} unparseable ({e})"); continue
        if r.get("status") == "SKIPPED":
            continue
        mux = r.get("multiplex") or {}
        bad = [k for k in mux if str(k) != "0"]
        if bad:
            errs.append(f"{r.get('papi_set')}: PAPI multiplexed (estimates) {mux}")
        af = r.get("app_files")
        if af is not None and af != ranks:
            errs.append(f"{r.get('papi_set')}: app_files {af} != ranks {ranks}")
    return errs


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    root = args[0]
    want = None
    for a in sys.argv[1:]:
        if a.startswith("--sets"):
            want = int(a.split("=", 1)[1]) if "=" in a else None
    as_json = "--json" in sys.argv

    total = bad = 0
    report = {}
    for leaf in leaf_cells(root):
        total += 1
        errs = check(leaf, want)
        if errs:
            bad += 1
            rel = os.path.relpath(leaf, root)
            report[rel] = errs
            if not as_json:
                print(f"FAIL {rel}: {'; '.join(errs[:4])}")
    if as_json:
        print(json.dumps({"leaves": total, "failing": bad, "detail": report}, indent=2))
    else:
        print(f"\n{total - bad}/{total} leaves satisfy the published-leaf contract"
              f"{'' if bad == 0 else f' -- {bad} FAILING'}")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
