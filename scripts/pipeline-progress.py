#!/usr/bin/env python3
"""Where a pipeline run has got to, and what each step cost.

Reads a headless run's stream-json transcript and reports per step: the MCP
calls made, how many failed, and how often a call was repeated. Repeats are
the interesting number -- the pipeline has no retry counter of its own, so a
step that called `system_detect` twenty times is the only visible trace of a
step that could not get past itself.

Steps come from the profile_step_begin/end markers the pipeline emits. Work
done before the first marker is reported under "(setup)", which is where a run
that never gets going spends all of its time.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
from collections import Counter, defaultdict

OUTPUTS = "/usr/workspace/haridev/dftracer-agents/outputs"


def newest() -> str:
    logs = glob.glob(os.path.join(OUTPUTS, "*.jsonl"))
    if not logs:
        raise SystemExit(f"no logs in {OUTPUTS}")
    return max(logs, key=os.path.getmtime)


def short(name: str) -> str:
    return (name.replace("mcp__dftracer__", "dft:")
                .replace("mcp__phronix__", "phx:"))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("log", nargs="?", default=None)
    args = ap.parse_args()
    path = args.log or newest()

    step = "(setup)"
    order: list[str] = [step]
    calls: dict[str, Counter] = defaultdict(Counter)
    errors: dict[str, int] = defaultdict(int)
    # tool_use_id -> step, so a failure is charged to the step that made it
    # rather than to whichever step was current when the result came back.
    owner: dict[str, str] = {}
    text_last = ""
    # The agent's own task list. It is the only step record when
    # profile_step_begin/end are not emitted -- which, left uninstructed, is
    # most of the time.
    tasks: dict[str, dict] = {}
    result = None
    started: dict[str, str] = {}
    # Per step: how often each (tool, arguments) pair was seen. Distinct from
    # a call count -- nine list_files calls over nine subfolders is a step
    # doing its job, nine identical ones is a step circling.
    repeat_detail: dict[str, Counter] = defaultdict(Counter)

    for line in open(path, errors="replace"):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        kind = event.get("type")
        if kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    name = str(block.get("name", "?"))
                    args_in = block.get("input") or {}
                    if name.endswith("profile_step_begin"):
                        step = str(args_in.get("step", "?"))[:52]
                        if step not in order:
                            order.append(step)
                        started[step] = event.get("timestamp", "")[11:19]
                    if name == "TaskCreate":
                        tasks[str(len(tasks) + 1)] = {
                            "subject": args_in.get("subject", "?"),
                            "status": "pending"}
                    elif name in ("TaskUpdate", "TodoWrite"):
                        tid = str(args_in.get("taskId", ""))
                        if tid in tasks:
                            tasks[tid].update(
                                {k: v for k, v in args_in.items()
                                 if k in ("status", "subject")})
                    calls[step][short(name)] += 1
                    repeat_detail[step][
                        (short(name), json.dumps(args_in, sort_keys=True)[:300])] += 1
                    owner[str(block.get("id"))] = step
                elif block.get("type") == "text" and block.get("text", "").strip():
                    text_last = block["text"].strip()
        elif kind == "user":
            content = event.get("message", {}).get("content")
            for block in content if isinstance(content, list) else []:
                if block.get("type") == "tool_result" and block.get("is_error"):
                    errors[owner.get(str(block.get("tool_use_id")), step)] += 1
        elif kind == "result":
            result = event

    print(f"# {os.path.basename(path)}\n")
    for name in order:
        counts = calls[name]
        if not counts:
            continue
        total = sum(counts.values())
        # Report REPEATS -- the same tool with the same arguments -- not the
        # most-called tool. Nine session_list_files calls across nine different
        # subfolders is a step doing its job; nine identical ones is a step
        # circling, and only the second deserves a warning.
        worst = repeat_detail[name].most_common(1)
        stuck = ""
        if worst and worst[0][1] >= 3:
            stuck = f"   repeated {worst[0][0][0]} ×{worst[0][1]}"
        at = started.get(name, "")
        print(f"{name:54s} {total:4d} calls  {errors[name]:3d} err{stuck}"
              + (f"   [{at}]" if at else ""))

    if tasks:
        mark = {"completed": "done", "in_progress": " >> ", "pending": "    "}
        print("\ntask list (the agent's own step record):")
        for tid, t in tasks.items():
            print(f"  {mark.get(t['status'], '    '):>5}  {tid}. {t['subject'][:60]}")

    print(f"\nlast said: {text_last[:400]}")
    if result:
        print(f"\nFINISHED {result.get('subtype')}  turns={result.get('num_turns')}  "
              f"${result.get('total_cost_usd', 0):.2f}")
    else:
        print("\nstill running")


if __name__ == "__main__":
    main()
