"""Harness-neutral prompt-injection entry point: ``python3 -m dftracer_agents.hook_entry``.

Reads a JSON hook payload on stdin and writes the context block on stdout.
Claude Code and Codex CLI invoke the generated ``.claude/hooks/inject_context.py``
/ ``.codex/hooks/inject_context.py`` shims; the OpenCode plugin spawns this
module directly. All three share this one implementation so retrieval behaviour
cannot drift between harnesses.

Contract: always exit 0. Printing nothing means "inject nothing". A failure to
look up memory must never block the user's prompt.
"""
from __future__ import annotations

import json
import sys


def run(raw: str) -> str:
    """Return the block to inject for the hook payload *raw* (may be empty)."""
    if not raw.strip():
        return ""
    try:
        payload = json.loads(raw)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""

    prompt = ""
    for key in ("prompt", "user_prompt", "message", "text"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            prompt = value
            break
    if not prompt.strip():
        return ""

    session = str(payload.get("session_id") or payload.get("turn_id") or "hook")
    # persist=True is essential here: each hook invocation is a NEW process, so
    # in-memory dedup would reset every prompt and re-inject the standing rules
    # and the same memories every single turn.
    try:
        from dftracer_agents.context_pack import build_pack, session_state
        kind = "session" if session_state(session, persist=True).take_rules_pending() else "prompt"
        return build_pack(prompt, session_id=session, kind=kind, persist=True) or ""
    except Exception:
        return ""


def main() -> int:
    try:
        block = run(sys.stdin.read())
    except Exception:
        return 0
    if block:
        sys.stdout.write(block + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
