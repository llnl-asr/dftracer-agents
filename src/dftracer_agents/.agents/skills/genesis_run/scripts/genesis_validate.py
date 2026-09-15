#!/usr/bin/env python3
"""Validate ONE genesis run's traces. Exit 0 only if the data is actually correct.

Checks, per the requirement that every run be validated on args / rank count /
event types:

APP traces (one per rank):
  * file count               == nodes * ppn
  * every trace has a `start` event AND a trailing `end` event.  The `end` event
    is dftracer's completeness marker -- a trace can be large and still be
    truncated, so file size proves nothing.
  * `SH` command-line metadata contains the EXACT nx/ny/nz this cell asked for,
    so a mislabeled or stale binary invocation cannot masquerade as this run.
  * `PR` rank metadata across all traces forms exactly {0 .. nranks-1}: no
    missing rank, no duplicate, no extra.
  * required event categories are present (annotation, I/O, MPI, GPU, PAPI).
  * `end.used` confirms the runtime actually engaged CPP_APP / MPI / HIP.
  * PAPI: the counters that landed match the counters requested for this set,
    and every sample is multiplex==0 (exact hardware counts, not estimates).

SERVICE traces (one per node):
  * file count               == nodes, one per DISTINCT host
  * utilization categories sys/io/net present
  * variorum `gpu`/power samples present (node power actually collected)
"""
import gzip, json, sys, glob, os, collections

def events(path):
    with gzip.open(path, 'rt', errors='replace') as fh:
        for line in fh:
            line = line.strip().rstrip(',')
            if not line or line in '[]':
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue

def main():
    run_dir, nx, nodes, ppn, papi_csv = sys.argv[1:6]
    nx, nodes, ppn = int(nx), int(nodes), int(ppn)
    nranks = nodes * ppn
    want_papi = {c for c in papi_csv.split(',') if c}
    errs, info = [], {}

    # ---------------------------------------------------------------- app ----
    app_files = sorted(glob.glob(os.path.join(run_dir, "*-app.pfw.gz")))
    info['app_files'] = len(app_files)
    if len(app_files) != nranks:
        errs.append(f"app trace count {len(app_files)} != expected {nranks}")

    ranks, cats = set(), collections.Counter()
    papi_seen, mux, starts, ends = collections.Counter(), collections.Counter(), 0, 0
    used = collections.Counter()
    bad_args = []
    for f in app_files:
        saw_end = saw_start = False
        cmd_ok = None
        try:
            for e in events(f):
                c = e.get('cat'); n = e.get('name'); a = e.get('args') or {}
                cats[c] += 1
                if not isinstance(a, dict):
                    continue
                if c == 'dftracer' and n == 'SH':
                    nm = str(a.get('name', ''))
                    if 'nx=' in nm:
                        cmd_ok = (f"nx={nx};ny={nx};nz={nx}" in nm)
                if c == 'dftracer' and n == 'PR' and a.get('name') == 'rank':
                    try: ranks.add(int(a.get('value')))
                    except Exception: pass
                if c == 'dftracer' and n == 'start':
                    saw_start = True
                if c == 'dftracer' and n == 'end':
                    saw_end = True
                    for k, v in (a.get('used') or {}).items():
                        if v: used[k] += 1
                if 'multiplex' in a:
                    mux[str(a['multiplex'])] += 1
                for k in a:
                    if k.startswith('PAPI_') and not k.endswith('_delta'):
                        papi_seen[k] += 1
        except Exception as ex:
            errs.append(f"{os.path.basename(f)}: unreadable ({ex})")
            continue
        if not saw_start: errs.append(f"{os.path.basename(f)}: no start event")
        if not saw_end:   errs.append(f"{os.path.basename(f)}: TRUNCATED (no end event)")
        if cmd_ok is False: bad_args.append(os.path.basename(f))
        if cmd_ok is None:  errs.append(f"{os.path.basename(f)}: no SH cmd metadata to check args")

    if bad_args:
        errs.append(f"{len(bad_args)} trace(s) have args != nx={nx};ny={nx};nz={nx}")
    if ranks != set(range(nranks)):
        missing = sorted(set(range(nranks)) - ranks); extra = sorted(ranks - set(range(nranks)))
        errs.append(f"rank set wrong: n={len(ranks)} expected {nranks} missing={missing[:8]} extra={extra[:8]}")
    info['ranks'] = len(ranks)

    for need in ('CPP_APP', 'KERNEL_DISPATCH', 'MEMORY_COPY', 'papi'):
        if cats.get(need, 0) == 0:
            errs.append(f"missing event category: {need}")
    if not any(cats.get(c, 0) for c in ('collective', 'comm', 'p2p')):
        errs.append("missing MPI categories (collective/comm/p2p)")
    if not any(cats.get(c, 0) for c in ('POSIX', 'STDIO')):
        errs.append("missing I/O categories (POSIX/STDIO)")
    for need in ('CPP_APP', 'MPI', 'HIP'):
        if used.get(need, 0) == 0:
            errs.append(f"end.used missing {need}")

    got = set(papi_seen)
    if got != want_papi:
        errs.append(f"PAPI mismatch: missing={sorted(want_papi-got)} extra={sorted(got-want_papi)}")
    if set(mux) - {'0'}:
        errs.append(f"PAPI multiplexed (estimates, not exact): {dict(mux)}")
    info['papi'] = sorted(got); info['multiplex'] = dict(mux)

    # ------------------------------------------------------------ service ----
    svc = sorted(glob.glob(os.path.join(run_dir, "service_*.pfw.gz")))
    info['service_files'] = len(svc)
    hosts = {os.path.basename(p)[len("service_"):-len(".pfw.gz")] for p in svc}
    if len(svc) != nodes:
        errs.append(f"service trace count {len(svc)} != nodes {nodes}")
    if len(hosts) != len(svc):
        errs.append(f"service traces not one-per-host: {sorted(hosts)}")
    scat, power = collections.Counter(), 0
    for f in svc:
        try:
            for e in events(f):
                scat[e.get('cat')] += 1
                if e.get('cat') == 'gpu' and e.get('name') == 'power':
                    power += 1
        except Exception as ex:
            errs.append(f"{os.path.basename(f)}: unreadable ({ex})")
    for need in ('sys', 'io', 'net'):
        if scat.get(need, 0) == 0:
            errs.append(f"service missing utilization category: {need}")
    if power == 0:
        errs.append("service has NO variorum gpu power samples")
    info['power_samples'] = power
    info['app_categories'] = dict(cats)
    info['service_categories'] = dict(scat)

    out = {'ok': not errs, 'errors': errs, **info}
    print(json.dumps(out))
    return 0 if not errs else 1

if __name__ == '__main__':
    sys.exit(main())
