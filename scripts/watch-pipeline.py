#!/usr/bin/env python3
"""Follow a headless pipeline run's stream-json log as readable progress.

The transcript is one JSON object per line and mostly tool plumbing; printed
raw it is unreadable. This keeps the parts a human watching a run cares about:
which tool was called, what the model said, and how it ended.

  watch-pipeline.py [log]         # newest log if omitted
  watch-pipeline.py -f [log]      # follow, like tail -f
  watch-pipeline.py -d [log]      # detail: full arguments, results, timings

Detail mode answers "what is it doing RIGHT NOW": every call is paired with
its result, so a call still waiting for one is the call in flight, and it is
printed last with how long it has been running.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time

OUTPUTS = "/usr/workspace/haridev/dftracer-agents/outputs"


def newest() -> str:
    logs = glob.glob(os.path.join(OUTPUTS, "*.jsonl"))
    if not logs:
        sys.exit(f"no logs in {OUTPUTS}")
    return max(logs, key=os.path.getmtime)


def render(line: str) -> str | None:
    line = line.strip()
    if not line.startswith("{"):
        return line or None            # the banner the launcher prints
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None

    kind = event.get("type")
    if kind == "system" and event.get("subtype") == "init":
        servers = ", ".join(f"{s.get('name')}={s.get('status')}"
                            for s in event.get("mcp_servers", []))
        return f"── session {event.get('session_id','?')[:8]}  mcp: {servers}"

    if kind == "assistant":
        out = []
        for block in event.get("message", {}).get("content", []):
            if block.get("type") == "tool_use":
                name = str(block.get("name", "?")).replace("mcp__dftracer__", "dft:")
                name = name.replace("mcp__phronix__", "phx:")
                # The first argument is usually the one that identifies the
                # call (a path, a run_id); the rest is noise at this zoom.
                args = block.get("input") or {}
                head = next((f"{k}={str(v)[:40]}" for k, v in args.items()), "")
                out.append(f"  → {name}({head})")
            elif block.get("type") == "text":
                text = block.get("text", "").strip()
                if text:
                    out.append("  " + text[:300].replace("\n", " "))
        return "\n".join(out) or None

    if kind == "user":
        content = event.get("message", {}).get("content")
        for block in content if isinstance(content, list) else []:
            if block.get("type") == "tool_result":
                body = str(block.get("content"))
                if "rror" in body[:400]:
                    return f"  ! {body[:220]}"
        return None

    if kind == "result":
        return (f"\n══ {event.get('subtype')}  turns={event.get('num_turns')}  "
                f"${event.get('total_cost_usd', 0):.2f}\n"
                f"{str(event.get('result'))[:1500]}")
    return None


def detail(path: str) -> None:
    """Pair every tool call with its result and show what is still running."""
    import datetime as dt

    calls: dict[str, dict] = {}
    order: list[str] = []
    for line in open(path, errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        stamp = event.get("timestamp", "")
        if event.get("type") == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    cid = str(block.get("id"))
                    calls[cid] = {"name": str(block.get("name", "?")),
                                  "args": block.get("input") or {},
                                  "at": stamp, "result": None, "error": False}
                    order.append(cid)
                elif block.get("type") == "text" and block.get("text", "").strip():
                    order.append("say:" + block["text"].strip())
        elif event.get("type") == "user":
            content = event.get("message", {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result":
                    call = calls.get(str(block.get("tool_use_id")))
                    if call is not None:
                        call["result"] = str(block.get("content"))
                        call["error"] = bool(block.get("is_error"))
                        call["done"] = stamp

    def secs(a: str, b: str) -> str:
        try:
            fmt = "%Y-%m-%dT%H:%M:%S"
            t0 = dt.datetime.strptime(a[:19], fmt)
            t1 = dt.datetime.strptime(b[:19], fmt)
            return f"{(t1 - t0).total_seconds():5.1f}s"
        except Exception:
            return "     ?"

    for item in order[-40:]:
        if item.startswith("say:"):
            print(f"\n  » {item[4:][:400]}\n")
            continue
        call = calls[item]
        name = call["name"].replace("mcp__dftracer__", "dft:").replace("mcp__phronix__", "phx:")
        args = json.dumps(call["args"])[:150]
        if call["result"] is None:
            print(f"  {call['at'][11:19]}  {name} {args}")
            print(f"            ⏳ RUNNING — no result yet")
            continue
        took = secs(call["at"], call.get("done", call["at"]))
        mark = "FAIL" if call["error"] else "ok  "
        print(f"  {call['at'][11:19]} {took} {mark} {name} {args}")
        if call["error"]:
            print(f"            ! {call['result'][:300]}")

    pending = [c for c in calls.values() if c["result"] is None]
    print(f"\n{len(calls)} calls, {sum(1 for c in calls.values() if c['error'])} failed, "
          f"{len(pending)} still running")
    for c in pending:
        print(f"  IN FLIGHT since {c['at'][11:19]}: "
              f"{c['name'].replace('mcp__dftracer__','dft:')} {json.dumps(c['args'])[:200]}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default=None)
    ap.add_argument("-f", "--follow", action="store_true")
    ap.add_argument("-d", "--detail", action="store_true",
                    help="full arguments, results, timings, and what is in flight")
    args = ap.parse_args()
    path = args.log or newest()
    print(f"# {path}\n", flush=True)
    if args.detail:
        detail(path)
        return

    with open(path, errors="replace") as fh:
        while True:
            line = fh.readline()
            if line:
                shown = render(line)
                if shown:
                    print(shown, flush=True)
                continue
            if not args.follow:
                return
            time.sleep(1.0)


if __name__ == "__main__":
    main()
