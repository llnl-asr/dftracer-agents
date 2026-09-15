#!/usr/bin/env python3
"""Assert a leaf's compacted/ really contains BOTH the app traces and the
per-node dftracer_service traces -- not just the app side.

dftracer_split is pointed at a staging dir; if service traces failed to stage,
the split would still succeed and look healthy while silently dropping every
node-level counter. This makes that failure mode loud."""
import gzip, json, glob, os, sys, collections

APP     = {'CPP_APP', 'KERNEL_DISPATCH', 'MEMORY_COPY', 'papi'}
SERVICE = {'sys', 'io', 'net', 'gpu'}

def _read_with_retry(path, fn, attempts=4):
    """Chunks are written on a compute node and read straight back over NFS, which
    intermittently raises [Errno 116] Stale file handle even though the file is fine
    (verified: leaves that failed this way re-check clean once the handle refreshes).
    Retry transient OS errors so only a PERSISTENT read failure condemns the data."""
    import time
    last = None
    for i in range(attempts):
        try:
            return fn(path)
        except OSError as ex:
            last = ex
            time.sleep(1.5 * (i + 1))
    raise last


def main():
    d, nodes = sys.argv[1], int(sys.argv[2])
    cats = collections.Counter(); power = 0; hosts = set()
    files = sorted(glob.glob(os.path.join(d, "*.pfw.gz")))
    if not files:
        print(json.dumps({'ok': False, 'errors': ['no compacted chunks']})); return 1
    def _scan(path):
        with gzip.open(path, 'rt', errors='replace') as fh:
            return fh.readlines()
    for f in files:
        try:
            for line in _read_with_retry(f, _scan):
                if True:
                    line = line.strip().rstrip(',')
                    if not line or line in '[]': continue
                    try: e = json.loads(line)
                    except Exception: continue
                    c = e.get('cat'); cats[c] += 1
                    a = e.get('args') or {}
                    if c == 'gpu' and e.get('name') == 'power': power += 1
                    if isinstance(a, dict) and c == 'dftracer' and e.get('name') == 'HH':
                        hosts.add(str(a.get('name')))
        except Exception as ex:
            print(json.dumps({'ok': False, 'errors': [f'{os.path.basename(f)} unreadable: {ex}']})); return 1
    errs = []
    for need in sorted(APP):
        if cats.get(need, 0) == 0: errs.append(f"compacted missing APP category {need}")
    for need in sorted(SERVICE):
        if cats.get(need, 0) == 0: errs.append(f"compacted missing SERVICE category {need}")
    if power == 0: errs.append("compacted has no variorum power samples")
    # A leaf aggregates the 8 PAPI-set runs, and the bin-packing scheduler is free
    # to place each of those runs on a DIFFERENT disjoint host slice. So the leaf
    # legitimately spans anywhere from `nodes` hosts (all 8 runs got the same slice)
    # up to 8*nodes. Requiring equality here wrongly fails every leaf. Per-run host
    # count is already asserted exactly == nodes by genesis_validate.py.
    if len(hosts) < nodes:
        errs.append(f"compacted spans {len(hosts)} hosts, fewer than the {nodes} of a single run")
    print(json.dumps({'ok': not errs, 'errors': errs, 'chunks': len(files),
                      'events': sum(cats.values()), 'power': power,
                      'hosts': len(hosts), 'categories': dict(cats)}))
    return 0 if not errs else 1

if __name__ == '__main__':
    sys.exit(main())
