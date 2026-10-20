#!/usr/bin/env python3
"""Write <leaf>/summary.json for one published leaf (genesis_leaf_contract).

Aggregates the per-run records (run_<set>.json + validation_<set>.json, which
live in raw/ or, once raw/ is reclaimed, at the leaf top level) into the
established per-leaf summary whose key names consumers already read.

Usage: genesis_leaf_summary.py <leaf_dir> <app> <system>
The leaf path must end in <case>/nodes_<N>/ppn_<P>.
"""
import glob, json, os, sys


def main():
    leaf, app, system = sys.argv[1:4]
    leaf = os.path.abspath(leaf)
    ppn = int(os.path.basename(leaf).removeprefix("ppn_"))
    nodes = int(os.path.basename(os.path.dirname(leaf)).removeprefix("nodes_"))
    case = os.path.basename(os.path.dirname(os.path.dirname(leaf)))
    src = os.path.join(leaf, "raw") if os.path.isdir(os.path.join(leaf, "raw")) else leaf
    per_set, cats, scats, hosts, papi, mux = {}, {}, {}, set(), set(), set()
    n_app = n_svc = pw_csv = pw_rows = 0
    for rp in sorted(glob.glob(os.path.join(src, "run_*.json"))):
        r = json.load(open(rp))
        s = r.get("papi_set")
        v = {}
        vp = os.path.join(src, f"validation_{s}.json")
        if os.path.exists(vp):
            try:
                v = json.load(open(vp))
            except Exception:
                v = {}
        per_set[s] = {k: r.get(k) for k in ("key", "status", "app_exit", "duration_s",
                                            "hosts", "multiplex", "app_files", "args")}
        hosts.update(h for h in (r.get("hosts") or []) if h)
        n_app += int(v.get("app_files") or 0)
        n_svc += int(v.get("service_files") or 0)
        papi.update(v.get("papi") or [])
        mux.update((v.get("multiplex") or {}).keys())
        for k, c in (v.get("app_categories") or {}).items():
            cats[k] = cats.get(k, 0) + c
        for k, c in (v.get("service_categories") or {}).items():
            scats[k] = scats.get(k, 0) + c
        pw_rows += int(v.get("nvml_power_rows") or 0)
    pw_csv = len(glob.glob(os.path.join(src, "papi_*", "dftracer_service", "gpu_power_*.csv")))
    chunks = {os.path.relpath(p, leaf): os.path.getsize(p)
              for p in glob.glob(os.path.join(leaf, "compacted", "*.pfw.gz"))
              + glob.glob(os.path.join(leaf, "*.pfw.gz"))}
    out = {
        "leaf": f"{system}/{case}/nodes_{nodes}/ppn_{ppn}",
        "per_set_run": per_set,
        "ok": bool(per_set) and all(p.get("status") == "ok" for p in per_set.values()),
        "app": app, "system": system, "input": case,
        "nodes": nodes, "ppn": ppn, "ranks": nodes * ppn,
        "sets": sorted(per_set), "n_sets": len(per_set),
        "n_app_traces": n_app, "n_service_traces": n_svc,
        "hosts": sorted(hosts),
        "app_categories": cats, "service_categories": scats,
        "papi": sorted(papi), "papi_multiplex": sorted(mux),
        "gpu_power_csv": pw_csv, "gpu_power_rows": pw_rows,
        "compacted": chunks,
    }
    json.dump(out, open(os.path.join(leaf, "summary.json"), "w"), indent=1)
    print(json.dumps({"leaf": out["leaf"], "ok": out["ok"], "sets": out["n_sets"]}))


if __name__ == "__main__":
    main()
