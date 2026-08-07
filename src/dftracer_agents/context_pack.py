"""Build the context block injected into prompts, tool calls and tool results.

Every harness we support (claude, opencode, copilot, codex) talks to the same
MCP server, but only Claude Code reads the memory store natively. This module
is the harness-neutral producer of the text that closes that gap, carrying
three payloads the pipeline policy requires on every session:

* **memory**    — the relevant slice of the git-tracked memory store.
* **validation** — the standing "verify, don't assume" rules.
* **learning**   — the standing "record the lesson" rules.

Why tiered and deduped, rather than injecting on literally every call
-------------------------------------------------------------------
A pipeline session issues hundreds of tool calls. The memory corpus is ~230 KB;
even a 1 KB digest repeated on every result is six figures of tokens per
session, almost all of it identical text the model has already seen. Worse, the
query signal for a tool call (name + args) is thin, so most of those injections
would be *irrelevant* memory — actively crowding out the real context.

So:

* the standing rules go in **once per session** (``kind="session"``);
* a memory is injected **once per session** — ``SessionState`` tracks what has
  already been shown and later calls carry only what is new;
* when nothing new is relevant, ``build_pack`` returns ``None`` and the caller
  injects nothing at all.

That keeps steady-state cost near zero while still surfacing a lesson the
moment it becomes relevant.
"""
from __future__ import annotations

import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from dftracer_agents.memory_graph import recall, render_digest

# Kept short on purpose: this is repeated context, and the authoritative text
# lives in CLAUDE.md / AGENTS.md and the skills. This is a pointer, not a copy.
VALIDATION_RULES = (
    "Verify before you claim: a subagent's or tool's success report is not "
    "evidence. Grep/read the artifact it says it wrote, and report the real "
    "status of failures rather than laundering them into 'ok'."
)

LEARNING_RULES = (
    "Record what you learn: when something non-obvious breaks, capture "
    "symptom -> root cause -> exact fix in the right skill "
    "(workload-*/system-*/software-*) and, if it changes behaviour, the agent "
    "template. Confirm with the user before persisting. Never record "
    "usernames, absolute user paths, or job ids."
)

MEMORY_USAGE = (
    "Relevant project memory (git-tracked, cross-session). "
    "memory_read(name=...) for full text; memory_recall(query=...) to search."
)

_MAX_MEMORY_CHARS = 1200
_MAX_NEW_PER_CALL = 3


def _state_dir() -> Path:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    return Path(os.environ.get("DFTRACER_CONTEXT_STATE_DIR")
                or f"/tmp/{user}/dftracer-agents/context-state")


class SessionState:
    """Per-session record of what has already been injected.

    Thread-safe because an MCP server handles concurrent tool calls, and two
    calls racing here would otherwise both decide a memory was "new" and inject
    it twice — the exact duplication this class exists to prevent.

    **Optionally disk-backed.** The MCP middleware lives in one long-running
    server process, so in-memory state is enough there. Prompt hooks do not:
    each hook fires as a fresh short-lived process, so without persistence every
    prompt would re-inject the standing rules and the same memories, which is
    exactly the waste the tiering exists to avoid. Hook callers pass
    ``persist=True`` to share state across invocations.

    The file is best-effort: any I/O error degrades to in-memory behaviour
    rather than failing the caller.
    """

    __slots__ = ("_seen", "_queries", "_rules_sent", "_lock", "_path")

    def __init__(self, path: Optional[Path] = None) -> None:
        self._seen: set[str] = set()
        self._queries: set[str] = set()
        self._rules_sent = False
        self._lock = threading.Lock()
        self._path = path
        if path is not None:
            self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text())
        except Exception:
            return
        if not isinstance(data, dict):
            return
        self._seen = set(data.get("seen") or [])
        self._queries = set(data.get("queries") or [])
        self._rules_sent = bool(data.get("rules_sent"))

    def _save(self) -> None:
        """Caller must hold ``_lock``."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps({
                "seen": sorted(self._seen),
                "queries": sorted(self._queries),
                "rules_sent": self._rules_sent,
            }))
            tmp.replace(self._path)  # atomic: a torn file would re-inject everything
        except Exception:
            pass

    def take_query(self, key: str) -> bool:
        """True the first time this query is seen, False on every repeat.

        Without this, an agent retrying the same tool call would be fed three
        *different* memories each time — dedup on names alone only stops exact
        repeats, so a loop would strip-mine the corpus. A query that has already
        been answered has nothing new to say.
        """
        with self._lock:
            if key in self._queries:
                return False
            self._queries.add(key)
            self._save()
            return True

    def take_new(self, names: List[str], limit: int) -> List[str]:
        """Return up to *limit* names not yet injected, marking them as seen."""
        with self._lock:
            fresh = [n for n in names if n not in self._seen][:limit]
            if fresh:
                self._seen.update(fresh)
                self._save()
            return fresh

    def take_rules(self) -> bool:
        """True exactly once per session — the standing rules go in one time."""
        with self._lock:
            if self._rules_sent:
                return False
            self._rules_sent = True
            self._save()
            return True

    def take_rules_pending(self) -> bool:
        """Peek at whether the rules still need sending, without consuming.

        Callers use this to pick the pack ``kind`` *before* calling
        ``build_pack``; the actual once-only guard is still ``take_rules``.
        """
        with self._lock:
            return not self._rules_sent

    @property
    def seen(self) -> set[str]:
        with self._lock:
            return set(self._seen)

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()
            self._queries.clear()
            self._rules_sent = False
            self._save()


_SESSIONS: Dict[str, SessionState] = {}
_SESSIONS_LOCK = threading.Lock()


def session_state(session_id: str = "default", persist: bool = False) -> SessionState:
    """Get (or create) the dedup state for *session_id*.

    ``persist=True`` backs the state with a file so it survives process exit —
    required for prompt hooks, which run as a new process per invocation.
    """
    key = f"{session_id}:{int(persist)}"
    with _SESSIONS_LOCK:
        state = _SESSIONS.get(key)
        if state is None:
            safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)[:120] or "default"
            path = (_state_dir() / f"{safe}.json") if persist else None
            state = _SESSIONS[key] = SessionState(path)
        return state


def reset_sessions() -> None:
    """Drop all per-session dedup state (tests, and server restart)."""
    with _SESSIONS_LOCK:
        _SESSIONS.clear()


def build_pack(
    query: str,
    session_id: str = "default",
    kind: str = "tool",
    k: int = 5,
    types: Optional[List[str]] = None,
    memories: Optional[List[Dict[str, Any]]] = None,
    persist: bool = False,
) -> Optional[str]:
    """Return the block to inject, or ``None`` when there is nothing new to say.

    Args:
        query: the text to match memory against — a user prompt, or a tool name
            plus its arguments.
        session_id: dedup scope. Distinct sessions re-inject independently.
        kind: ``"session"`` forces the standing rules in and tolerates an empty
            memory hit; ``"prompt"`` and ``"tool"`` inject only what is new.
        k: how many scored hits to consider before dedup.
        types: restrict to these memory types.
        memories: pre-parsed corpus (tests / caching).

    Returns:
        A ``<dftracer-context>`` block, or ``None`` to inject nothing.
    """
    state = session_state(session_id, persist=persist)
    sections: List[str] = []

    # Standing rules: once per session, regardless of memory hits.
    want_rules = state.take_rules() if kind == "session" else False
    if want_rules:
        sections.append(f"VALIDATION: {VALIDATION_RULES}")
        sections.append(f"LEARNING: {LEARNING_RULES}")

    limit = k if kind == "session" else _MAX_NEW_PER_CALL
    # A repeated query has already been answered; re-running it would surface a
    # *different* slice of the corpus rather than nothing, so gate on the query
    # itself and not only on which names have been shown.
    # Keyed on the query text ALONE, not on kind: the first call of a session is
    # kind="session" and the next is kind="tool", so including kind would let the
    # very same query through twice.
    if query.strip() and state.take_query(query.strip()[:400].lower()):
        hits = recall(query, k=k, types=types, memories=memories)
        fresh_names = state.take_new([h["name"] for h in hits], limit)
        if fresh_names:
            keep = [h for h in hits if h["name"] in set(fresh_names)]
            digest = render_digest(keep, budget_chars=_MAX_MEMORY_CHARS)
            if digest:
                sections.append(f"MEMORY: {MEMORY_USAGE}\n{digest}")

    if not sections:
        return None
    body = "\n\n".join(sections)
    return f"<dftracer-context>\n{body}\n</dftracer-context>"


def tool_query(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> str:
    """Turn a tool invocation into a retrieval query.

    The tool name alone is a weak signal, so string-ish argument values are
    folded in — a ``session_run_with_dftracer`` call whose args mention
    ``flux`` should surface the flux lessons, not just the generic run ones.
    Long values are clipped; non-scalars are skipped as noise.
    """
    parts = [tool_name.replace("_", " ")]
    for key, value in (arguments or {}).items():
        if isinstance(value, str) and value.strip():
            parts.append(f"{key} {value[:200]}")
        elif isinstance(value, (int, float, bool)):
            parts.append(str(key))
    return " ".join(parts)
