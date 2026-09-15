#!/usr/bin/env python3
"""Judge an IOR annotate/build/smoke-test run by its artifacts, not its report.

Both arms of eval5 claim success in prose. This checks the disk instead, with
the same five checks for each, so "it said it worked" and "it worked" can be
told apart.

    scripts/verify-ior.py <workdir>             # judge one run
    scripts/verify-ior.py <workdir> --json      # machine-readable

<workdir> is the bare arm's WORK (see /tmp/ior_bare_paths) or the pipeline's
workspaces/ior/<stamp>/ directory. The layout differs between arms, so each
check searches rather than assuming a fixed path.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# An annotation is only real if it both includes the header and opens regions.
HEADER_RE = re.compile(r'#\s*include\s*[<"].*dftracer', re.I)
REGION_RE = re.compile(r"\bDFTRACER_C_FUNCTION_(?:START|UPDATE|END)\b|"
                       r"\bDFTRACER_C_REGION(?:_START|_END)?\b|"
                       r"\bdftracer_(?:initialize|finalize)\b", re.I)

TRACE_GLOBS = ("*.pfw", "*.pfw.gz", "*.pfw.zst")


def find_source(work: Path) -> Path | None:
    """Locate the IOR tree to judge: the directory holding src/ior-main.c.

    The pipeline keeps a pristine 'source'/'baseline' tree beside the
    instrumented 'annotated' one. Judging the shallowest tree scores the
    baseline and calls a successful run a failure, so an annotated tree wins
    whenever one exists.
    """
    hits = list(work.rglob("src/ior-main.c"))
    hits = [h for h in hits if not any(p in {"site-packages", ".venv", "venv"}
                                       for p in h.parts)]
    if not hits:
        return None
    ann = [h for h in hits if "annotated" in h.parts and "baseline" not in h.parts]
    pool = ann or [h for h in hits if "baseline" not in h.parts] or hits
    return min(pool, key=lambda p: len(p.parts)).parent.parent


def check_annotations(src: Path) -> dict:
    """Count .c files carrying a dftracer header AND at least one region."""
    files, headered, regioned, both, regions = 0, 0, 0, [], 0
    for c in sorted(src.glob("src/*.c")):
        files += 1
        text = c.read_text(errors="replace")
        h = bool(HEADER_RE.search(text))
        marks = REGION_RE.findall(text)
        if h:
            headered += 1
        if marks:
            regioned += 1
            regions += len(marks)
        if h and marks:
            both.append(c.name)
    return {"c_files": files, "with_header": headered, "with_regions": regioned,
            "annotated": len(both), "region_marks": regions,
            "annotated_files": both[:20]}


def check_binary(src: Path) -> dict:
    """The binary must exist, be executable, and actually link dftracer."""
    # The annotated binary may land in the tree itself or in a sibling
    # build_ann/install_ann; prefer those over the baseline build/ and
    # install/, which are deliberately built WITHOUT instrumentation.
    roots = [src, src.parent]
    cand: list[Path] = []
    for root in roots:
        for sub in ("src/ior", "build_ann/src/ior", "install_ann/bin/ior",
                    "build/ior", "build/src/ior", "install/bin/ior"):
            p = root / sub
            if p.exists() and p.is_file():
                cand.append(p)
    cand += [p for p in src.rglob("ior") if p.is_file() and p.stat().st_mode & 0o111]
    # An instrumented binary is the point; take the first that links dftracer.
    def _links(p: Path) -> bool:
        try:
            return "dftracer" in subprocess.run(
                ["strings", str(p)], capture_output=True, text=True,
                timeout=60).stdout.lower()
        except Exception:
            return False
    cand = [p for p in cand if _links(p)] or cand
    if not cand:
        return {"built": False}
    binary = cand[0]
    linked = False
    try:
        out = subprocess.run(["ldd", str(binary)], capture_output=True,
                             text=True, timeout=30).stdout
        linked = "dftracer" in out.lower()
    except Exception:
        pass
    if not linked:  # static link, or ldd unavailable
        try:
            out = subprocess.run(["strings", str(binary)], capture_output=True,
                                 text=True, timeout=60).stdout
            linked = "dftracer" in out.lower()
        except Exception:
            pass
    return {"built": True, "binary": str(binary),
            "size": binary.stat().st_size, "links_dftracer": linked}


def check_trace(work: Path, since: float = 0.0) -> dict:
    """A smoke test counts only if THIS run left a non-empty trace behind.

    The pipeline's traces/ directory accumulates across runs, so without the
    mtime floor an arm scores a pass on someone else's 900MB of old traces.
    """
    found = []
    for pat in TRACE_GLOBS:
        for t in work.rglob(pat):
            if t.is_file() and t.stat().st_size > 0 and t.stat().st_mtime >= since:
                found.append((str(t), t.stat().st_size))
    found.sort(key=lambda kv: -kv[1])
    return {"traces": len(found), "largest": found[0] if found else None,
            "total_bytes": sum(sz for _, sz in found)}


def run_smoke(binary: str | None, cmd_tmpl: str, *, do_run: bool) -> dict:
    """Execute the annotated binary and see whether it actually runs.

    Self-reported smoke tests are the one claim both arms make in prose and
    neither proves. With --run-smoke we run it in a scratch directory and
    judge by exit status.
    """
    if not binary:
        return {"ok": False, "detail": "no binary to run", "ran": False}
    if not do_run:
        return {"ok": True, "detail": f"binary present (not executed; "
                                      f"pass --run-smoke to verify)",
                "ran": False}
    import shlex
    import tempfile
    cmd = shlex.split(cmd_tmpl.replace("{binary}", binary))
    with tempfile.TemporaryDirectory(prefix="ior-smoke-") as td:
        try:
            p = subprocess.run(cmd, cwd=td, capture_output=True, text=True,
                               timeout=600)
        except Exception as exc:  # timeout, missing launcher, ...
            return {"ok": False, "detail": f"run failed: {exc}", "ran": True}
    tail = (p.stdout or p.stderr or "").strip().splitlines()
    return {"ok": p.returncode == 0,
            "detail": f"rc={p.returncode}" + (f" | {tail[-1][:60]}" if tail else ""),
            "ran": True, "returncode": p.returncode}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("workdir", type=Path)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--run-smoke", action="store_true",
                    help="actually execute the annotated binary")
    ap.add_argument("--smoke-cmd",
                    default="flux run -n 1 {binary} -a POSIX -t 1m -b 4m",
                    help="command used by --run-smoke; {binary} is substituted")
    ap.add_argument("--require-trace", action="store_true",
                    help="also require a trace file (pipeline STEP 7 scope)")
    ap.add_argument("--since", type=float, default=0.0,
                    help="epoch seconds; ignore traces older than this "
                         "(default: the workdir's own mtime)")
    args = ap.parse_args()

    work = args.workdir.resolve()
    if not work.is_dir():
        print(f"no such workdir: {work}", file=sys.stderr)
        return 2

    # The pipeline arm keeps venv/ and traces/ in the app directory, one level
    # above the per-run stamp directory; the bare arm keeps everything under
    # WORK. Search both so neither layout scores a false negative.
    scopes = [work]
    if work.parent.is_dir() and work.parent.name not in ("workspaces", "/"):
        scopes.append(work.parent)

    src = find_source(work)
    res: dict = {"workdir": str(work), "source": str(src) if src else None}
    res["clone"] = {"ok": src is not None}
    res["annotate"] = check_annotations(src) if src else {}
    res["build"] = check_binary(src) if src else {"built": False}
    # Only traces at least as new as the run's own directory count as this
    # run's. --since overrides when the workdir was created ahead of the run.
    since = args.since if args.since else work.stat().st_mtime
    res["trace_since"] = since
    smoke = {"traces": 0, "largest": None, "total_bytes": 0}
    for scope in scopes:
        s = check_trace(scope, since)
        if s["traces"] > smoke["traces"]:
            smoke = s
    res["smoke"] = smoke

    # The decisive check: run the annotated binary ourselves. Both arms report
    # their own smoke test in prose; executing it is the only way to compare
    # them on the same evidence. Trace collection is a later pipeline step, so
    # a trace is reported but not required unless --require-trace.
    res["smoke_run"] = run_smoke(res["build"].get("binary"), args.smoke_cmd,
                                 do_run=args.run_smoke)

    a, b, t = res["annotate"], res["build"], res["smoke"]
    checks = [
        ("clone", bool(src), str(src or "IOR source not found")),
        ("dftracer installed",
         any(list(s.rglob("dftracer/__init__.py")) or list(s.rglob("libdftracer*"))
             for s in scopes),
         "dftracer package or library present"),
        ("annotated", a.get("annotated", 0) > 0,
         f"{a.get('annotated', 0)}/{a.get('c_files', 0)} .c files with header+regions, "
         f"{a.get('region_marks', 0)} marks"),
        ("built", b.get("built", False) and b.get("links_dftracer", False),
         f"binary={b.get('built', False)} links_dftracer={b.get('links_dftracer', False)}"),
        ("smoke test", res["smoke_run"]["ok"], res["smoke_run"]["detail"]),
    ]
    if args.require_trace:
        checks.append(
            ("trace produced", t.get("traces", 0) > 0,
             f"{t.get('traces', 0)} non-empty trace files, "
             f"{t.get('total_bytes', 0)} bytes"))
    res["checks"] = {name: ok for name, ok, _ in checks}
    res["passed"] = sum(1 for _, ok, _ in checks if ok)
    res["total"] = len(checks)

    if args.json:
        print(json.dumps(res, indent=2))
        return 0 if res["passed"] == res["total"] else 1

    print(f"# {work.name}")
    print()
    for name, ok, detail in checks:
        print(f"  [{'x' if ok else ' '}] {name:20s} {detail}")
    print()
    print(f"  {res['passed']}/{res['total']} checks passed")
    return 0 if res["passed"] == res["total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
