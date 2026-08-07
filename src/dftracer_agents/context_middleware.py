"""FastMCP middleware that injects memory/validation/learning context.

Why middleware rather than per-harness hooks
--------------------------------------------
Only Claude Code reads the memory store natively. The other three harnesses we
support have very uneven extension points:

* **codex** — ``PreToolUse`` fires for the shell tool only; MCP tool calls never
  trigger it, and hooks need a user-level opt-in flag.
* **opencode** — plugin hooks exist (``tool.execute.before/after``) but are a
  separate TypeScript artifact, and upstream still cannot inject AI-visible
  conversation messages from them.
* **copilot** — no path-scoped hook mechanism at all.

All four, however, reach this server over MCP. Wrapping ``on_call_tool`` here is
therefore the only mechanism that covers tool traffic uniformly with zero
per-harness configuration — hooks are the complement for the one thing this
cannot see, the user's raw prompt.

The injected text is appended to the tool *result*. Budgeting and dedup live in
``context_pack``: the standing rules go in once, each memory goes in once, and a
repeated query injects nothing, so a 200-call session costs about a thousand
tokens in total rather than repeating a digest hundreds of times.

Failure policy: never break a tool call. Any error while building context is
swallowed and the untouched result is returned — memory injection is an
enhancement, not a dependency.
"""
from __future__ import annotations

import os
from typing import Any, Optional

from fastmcp.server.middleware import Middleware, MiddlewareContext

from dftracer_agents.context_pack import build_pack, tool_query

# Tools whose results must stay machine-parseable, or whose own purpose is to
# return memory (injecting there is circular and doubles the payload).
_SKIP_TOOLS = frozenset({
    "memory_recall", "memory_read", "memory_list", "memory_write",
    "privacy_scan", "privacy_redact", "privacy_suspects",
})

_ENV_FLAG = "DFTRACER_CONTEXT_INJECTION"


def injection_enabled() -> bool:
    """Injection is on unless explicitly disabled.

    Set ``DFTRACER_CONTEXT_INJECTION=0`` to turn it off — useful when measuring
    token cost, or when a harness renders tool results verbatim to a user.
    """
    return os.environ.get(_ENV_FLAG, "1").strip().lower() not in ("0", "false", "no", "off")


def _session_id(context: MiddlewareContext) -> str:
    """Best-effort per-connection dedup scope.

    FastMCP does not guarantee a stable session id across transports, so fall
    back to a single shared scope rather than defeating dedup entirely with a
    per-call unique value.
    """
    for attr in ("session_id", "client_id"):
        value = getattr(context, attr, None)
        if isinstance(value, str) and value:
            return value
    fastmcp_ctx = getattr(context, "fastmcp_context", None)
    session = getattr(fastmcp_ctx, "session_id", None)
    if isinstance(session, str) and session:
        return session
    return "default"


def _append_to_result(result: Any, block: str) -> Any:
    """Attach *block* to a tool result without breaking its shape.

    FastMCP results carry a ``content`` list of blocks; appending a new text
    block is non-destructive, whereas rewriting the first block's text would
    corrupt any JSON payload a caller parses.
    """
    try:
        from mcp.types import TextContent
    except Exception:
        return result

    content = getattr(result, "content", None)
    if isinstance(content, list):
        content.append(TextContent(type="text", text=block))
        return result
    if isinstance(result, list):
        return [*result, TextContent(type="text", text=block)]
    return result


class ContextInjectionMiddleware(Middleware):
    """Append relevant memory + standing rules to tool results."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        result = await call_next(context)
        if not injection_enabled():
            return result

        try:
            name = getattr(context.message, "name", "") or ""
            if name in _SKIP_TOOLS:
                return result
            arguments = getattr(context.message, "arguments", None) or {}
            session = _session_id(context)

            # First tool call of a session carries the standing validation and
            # learning rules; later calls carry only newly-relevant memory.
            from dftracer_agents.context_pack import session_state
            kind = "session" if session_state(session).take_rules_pending() else "tool"

            block = build_pack(
                tool_query(name, arguments),
                session_id=session,
                kind=kind,
            )
            if block:
                return _append_to_result(result, block)
        except Exception:
            return result  # never break a tool call over context enrichment
        return result


def install(server) -> bool:
    """Attach the middleware to *server*. Returns False if unsupported."""
    if not hasattr(server, "add_middleware"):
        return False
    server.add_middleware(ContextInjectionMiddleware())
    return True
