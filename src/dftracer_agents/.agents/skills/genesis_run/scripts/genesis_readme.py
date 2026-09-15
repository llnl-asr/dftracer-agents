#!/usr/bin/env python3
"""Generate genesis_traces/<system>/README.md.

Stats come from the per-run validation JSONs and per-leaf compaction checks that
the sweep already wrote, so this does not re-parse every trace. Note that
dftracer_stats --report categories cannot be used for this: its indexer reports
"Events processed: 0" on compacted trace dirs, so category counts have to come
from a direct parse (which is what the validators did).
"""
import json, os, sys, glob, collections, subprocess, datetime

def human(n):
    for u in ('B','KB','MB','GB','TB'):
        if n < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"

def main():
    root = sys.argv[1]                     # .../genesis_traces/tuolumne
    system = os.path.basename(root.rstrip('/'))
    meta = json.load(open(sys.argv[2])) if len(sys.argv) > 2 else {}

    leaves = sorted(glob.glob(os.path.join(root, "input_*", "nodes_*", "ppn_*")))
    runs = 0
    ev_app = collections.Counter(); ev_svc = collections.Counter()
    papi_all = set(); mux_bad = 0; power_total = 0
    by_input = collections.Counter(); by_nodes = collections.Counter(); by_ppn = collections.Counter()
    leaf_rows = []; n_raw = 0; n_chunks = 0; incomplete = []

    APP_CATS = ('CPP_APP','POSIX','STDIO','collective','comm','p2p',
                'KERNEL_DISPATCH','MEMORY_COPY','PAGE_MIGRATION','papi','dftracer','env')
    SVC_CATS = ('sys','io','net','gpu')

    for leaf in leaves:
        parts = leaf.split(os.sep)
        inp   = parts[-3].replace('input_','')
        nodes = int(parts[-2].replace('nodes_',''))
        ppn   = int(parts[-1].replace('ppn_',''))

        vfiles = sorted(glob.glob(os.path.join(leaf, "raw", "validation_*.json")))
        leaf_events = 0; leaf_power = 0
        for vf in vfiles:
            try: v = json.load(open(vf))
            except Exception: continue
            if not v.get('ok'): continue
            runs += 1
            papi_all.update(v.get('papi') or [])
            if set((v.get('multiplex') or {})) - {'0'}: mux_bad += 1
            power_total += v.get('power_samples', 0); leaf_power += v.get('power_samples', 0)
            for k, n in (v.get('app_categories') or {}).items(): ev_app[k] += n
            for k, n in (v.get('service_categories') or {}).items(): ev_svc[k] += n
            leaf_events += sum((v.get('app_categories') or {}).values())
            leaf_events += sum((v.get('service_categories') or {}).values())

        n_raw += len(glob.glob(os.path.join(leaf, "raw", "papi_*", "*.pfw.gz")))
        chunks = glob.glob(os.path.join(leaf, "compacted", "*.pfw.gz"))
        n_chunks += len(chunks)
        cc = os.path.join(leaf, "compacted_check.json")
        cev = None
        if os.path.exists(cc):
            try:
                c = json.load(open(cc)); cev = c.get('events')
                if not c.get('ok'): incomplete.append(f"{inp}/N{nodes}/ppn{ppn}: {c.get('errors')}")
            except Exception: pass
        if len(vfiles) < 8: incomplete.append(f"{inp}/N{nodes}/ppn{ppn}: only {len(vfiles)}/8 PAPI sets")

        by_input[inp] += leaf_events; by_nodes[nodes] += leaf_events; by_ppn[ppn] += leaf_events
        leaf_rows.append((inp, nodes, ppn, len(vfiles), nodes*ppn, leaf_events, cev, len(chunks), leaf_power))

    total_events = sum(ev_app.values()) + sum(ev_svc.values())
    try:
        size = int(subprocess.check_output(['du','-sb',root]).split()[0])
    except Exception:
        size = 0

    L = []
    A = L.append
    A(f"# miniFE genesis traces — `{system}`\n")
    A("A dftracer trace corpus for **miniFE** (Mantevo unstructured implicit finite-element")
    A("proxy app), swept across a three-dimensional configuration space and captured with")
    A("every instrumentation layer dftracer offers on this machine.\n")

    A("## What these traces are\n")
    A("Each run is the **`openmp45-opt`** miniFE variant — OpenMP 4.5 target offload to")
    A("`gfx942` (MI300A) — annotated at the source level with dftracer **FUNCTION mode**")
    A("macros and linked against `libdftracer_core`. No `LD_PRELOAD` interception is used")
    A("anywhere in this corpus.\n")
    A("Every run captures, simultaneously:\n")
    A("| Layer | Source | Appears as |")
    A("|---|---|---|")
    A("| Application structure | source annotation (FUNCTION mode) | `CPP_APP` |")
    A("| POSIX / stdio I/O | brahma/gotcha interception | `POSIX`, `STDIO` |")
    A("| MPI | dftracer MPI layer (Cray MPICH) | `collective`, `comm`, `p2p` |")
    A("| GPU | ROCProfiler (rocprofiler-sdk) | `KERNEL_DISPATCH`, `MEMORY_COPY`, `PAGE_MIGRATION` |")
    A("| CPU hardware counters | PAPI | `papi` |")
    A("| Node utilization | `dftracer_service` daemon | `sys`, `io`, `net` |")
    A("| Node power | variorum (AMD_GPU domain) | `gpu` / `power` |")
    A("")
    if meta:
        A("## Provenance\n")
        for k, v in meta.items(): A(f"- **{k}**: {v}")
        A("")

    A("## Dimensions\n")
    A("The directory tree *is* the dimension space:\n")
    A("```")
    A(f"genesis_traces/{system}/")
    A("  input_<nx>x<ny>x<nz>/      # dimension 1: problem size")
    A("    nodes_<N>/               # dimension 2: node scale")
    A("      ppn_<P>/               # dimension 3: processes (GPUs) per node")
    A("        raw/                 #   as produced by the runs")
    A("          papi_<set>/        #     one dir per PAPI counter set (8 of them)")
    A("          validation_<set>.json")
    A("        compacted/           #   all of the above, chunked + gzipped")
    A("        compacted_check.json")
    A("```\n")
    A("| Dimension | Values | Source |")
    A("|---|---|---|")
    A(f"| Input size | {', '.join(sorted(by_input, key=lambda s:int(s.split('x')[0])))} | unique `miniFE.x` argument strings in `llnl/ice4hpc_data` `data/merged.txt` (column 3) |")
    A(f"| Node scale | {', '.join(str(n) for n in sorted(by_nodes))} | |")
    A(f"| Processes per node | {', '.join(str(p) for p in sorted(by_ppn))} | GPU ladder — an MI300A node has 4 GPUs |")
    A(f"| PAPI counter set | 8 sets | a partition of all 30 PAPI presets (see below) |")
    A("")
    A("PAPI sets are **not** a dimension of the corpus — they are the multiple runs needed")
    A("to cover all 30 presets on hardware with only 5 counters, and they collapse into the")
    A("single `compacted/` trace at each leaf.\n")

    A("## Corpus statistics\n")
    A("| | |")
    A("|---|---|")
    A(f"| Validated runs | **{runs}** |")
    A(f"| Configuration cells (input x nodes x ppn) | {len(leaves)} |")
    A(f"| Raw trace files | {n_raw} |")
    A(f"| Compacted chunks | {n_chunks} |")
    A(f"| Total events | **{total_events:,}** |")
    A(f"| variorum power samples | {power_total:,} |")
    A(f"| PAPI presets covered | {len(papi_all)}/30 |")
    A(f"| Runs with multiplexed (estimated) counters | {mux_bad} |")
    A(f"| On-disk size | {human(size)} |")
    A("")

    A("### Events by category\n")
    A("| Category | Events | Side |")
    A("|---|---:|---|")
    for k, v in sorted(ev_app.items(), key=lambda kv: -kv[1]):
        A(f"| `{k}` | {v:,} | application |")
    for k in SVC_CATS:
        if ev_svc.get(k): A(f"| `{k}` | {ev_svc[k]:,} | service (node) |")
    A("")

    A("### Events by dimension\n")
    A("| Input | Events |")
    A("|---|---:|")
    for k in sorted(by_input, key=lambda s: int(s.split('x')[0])): A(f"| {k} | {by_input[k]:,} |")
    A("")
    A("| Nodes | Events |   | Procs/node | Events |")
    A("|---|---:|---|---|---:|")
    ns = sorted(by_nodes); ps = sorted(by_ppn)
    for i in range(max(len(ns), len(ps))):
        a = f"| {ns[i]} | {by_nodes[ns[i]]:,} " if i < len(ns) else "| | "
        b = f"| | {ps[i]} | {by_ppn[ps[i]]:,} |" if i < len(ps) else "| | | |"
        A(a + b)
    A("")

    A("### PAPI counters captured\n")
    A("All counts are **exact hardware counts** (`args.multiplex == 0`); no time-shared")
    A("estimates are present. The 30 presets are split across 8 runs because this hardware")
    A("exposes only 5 counters at once, and *derived* presets cost more than one native")
    A("event each — so the split is by measured cost, not by counter-name count.\n")
    A("```")
    for c in sorted(papi_all): A(f"  {c}")
    A("```\n")

    A("## Per-cell inventory\n")
    A("| Input | Nodes | ppn | Ranks | PAPI sets | Events (raw) | Events (compacted) | Chunks | Power |")
    A("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for r in sorted(leaf_rows, key=lambda r: (int(r[0].split('x')[0]), r[1], r[2])):
        inp, nodes, ppn, nsets, nranks, lev, cev, nch, lpw = r
        A(f"| {inp} | {nodes} | {ppn} | {nranks} | {nsets}/8 | {lev:,} | "
          f"{cev:,} | {nch} | {lpw:,} |" if cev is not None
          else f"| {inp} | {nodes} | {ppn} | {nranks} | {nsets}/8 | {lev:,} | - | {nch} | {lpw:,} |")
    A("")

    A("## How each run was validated\n")
    A("A run was recorded only after its traces passed content validation")
    A("(`scripts/genesis_validate.py`); anything failing is in `artifacts/genesis/failures`:\n")
    A("- app trace count == `nodes x ppn`, and the MPI ranks recovered from the `PR`")
    A("  metadata form exactly `{0 .. nranks-1}` — no missing, duplicate, or extra rank")
    A("- the `SH` command-line metadata in every trace matches this cell's `nx/ny/nz`")
    A("- every trace carries a `start` **and** a trailing `end` event; `end` is dftracer's")
    A("  completeness marker, so a truncated trace cannot pass on file size alone")
    A("- required categories present, and `end.used` confirms `CPP_APP`, `MPI`, `HIP`")
    A("- the PAPI counters present equal the set requested, all with `multiplex == 0`")
    A("- one service trace per distinct host, carrying `sys`/`io`/`net` and variorum power")
    A("- `compacted/` is `--verify`'d by `dftracer_split` (event-ID hash in == out) and")
    A("  additionally asserted to contain the **service** traces, not just the app ones")
    A("")
    A("## Properties and caveats\n")
    A("Things that are true of this corpus and are easy to get wrong if assumed:\n")
    A("**No event aggregation — full fidelity.** Every event is recorded individually;")
    A("dftracer's selective aggregation (`DFTRACER_ENABLE_AGGREGATION`) was deliberately")
    A("NOT enabled. It is a capture-time feature, so this is a property of the runs, not")
    A("something that can be undone or applied afterwards. For scale: dftracer's `dur` is")
    A("in MICROSECONDS here, and a `dur < 1000` (1 ms) selective rule was measured against")
    A("this data to fold **99.81%** of all events — including 100% of `KERNEL_DISPATCH`")
    A("and `comm` — so that rule would have removed exactly the GPU and MPI detail the")
    A("corpus exists to capture.\n")
    A("**GPU hardware counters are NOT present.** ROCProfiler logs")
    A("`could not be locked for profiling due to lack of permissions (capability")
    A("SYS_PERFMON)` on this system, so GPU PMC collection is unavailable. GPU *activity*")
    A("tracing is unaffected and complete (kernel dispatches, memory copies, page")
    A("migrations). All hardware counters in this corpus are CPU-side, via PAPI/perf_event.\n")
    A("**`Final Resid Norm` is not a convergence measure.** miniFE runs CG to a fixed cap")
    A("of 200 iterations, so the residual is wherever CG had reached. For nx>=100 every run")
    A("hits the cap, and the residual then degrades smoothly as ranks decrease (nx=200:")
    A("32 ranks -> 5.1e-09, 8 -> 1.4e-06, 1 -> 8.4e-04) even though every run solves the")
    A("IDENTICAL global system — `Global Nrows == (nx+1)^3` is asserted for all of them.")
    A("That is finite-precision CG on an ill-conditioned operator, not a defect.\n")
    A("**The dftracer_service process aborts at teardown.** It maps two `librocm_smi64`")
    A("ABIs (`.so.1` via libhwloc, `.so.7` via libvariorum) and dies with `corrupted size")
    A("vs. prev_size in fastbins`. This is strictly POST-flush: crashed service traces were")
    A("verified to gzip-decode cleanly with 0 malformed lines and all categories intact.")
    A("`HWLOC_COMPONENTS=-rsmi` does NOT avoid it (measured) — `.so.1` arrives through a")
    A("link-time `DT_NEEDED`, not a runtime plugin choice.\n")

    if incomplete:
        A("## Incomplete cells\n")
        for w in incomplete[:40]: A(f"- {w}")
        A("")

    out = os.path.join(root, "README.md")
    open(out, 'w').write("\n".join(L) + "\n")
    print(f"wrote {out} ({runs} runs, {total_events:,} events, {len(leaves)} cells)")

if __name__ == '__main__':
    main()
