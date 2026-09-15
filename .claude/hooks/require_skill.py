#!/usr/bin/env python3
"""PreToolUse: do not edit before reading this task's skill."""
import json
import os
import sys
from pathlib import Path

STATE = Path(os.environ.get("PHRONIX_GUARD_STATE", "")
             or Path.home() / ".phronix-skill-guard")


def main() -> int:
    wanted = os.environ.get("PHRONIX_REQUIRED_SKILL", "").strip()
    if not wanted:
        return 0
    try:
        event = json.load(sys.stdin)
    except Exception:
        return 0
    tool = str(event.get("tool_name") or "")

    # Reading the skill satisfies the gate, however it was read.
    blob = json.dumps(event.get("tool_input") or {})
    if wanted in blob:
        try:
            STATE.write_text(wanted, encoding="utf-8")
        except OSError:
            pass
        return 0

    if tool not in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return 0
    try:
        if STATE.is_file() and STATE.read_text(encoding="utf-8").strip() == wanted:
            return 0
    except OSError:
        return 0
    # Fire once: mark satisfied so the next edit proceeds regardless.
    try:
        STATE.write_text(wanted, encoding="utf-8")
    except OSError:
        pass
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Read the skill " + wanted + " before editing. It holds "
                "what an earlier run established about this exact task, "
                "including the patch that made the tests pass if one is "
                "recorded. Call skill_load on it, then edit."),
        }
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
