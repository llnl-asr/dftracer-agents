#!/usr/bin/env python3
"""
LLM-powered agent for dftracer-agents MCP services — with chain-of-thought.

Chain-of-thought works at two levels:

  Within a single command (within one you> prompt):
    The model reasons before each tool call.  Reasoning is displayed in a
    bordered block above the [tool] line.  Supports <think>…</think> native
    tags (Qwen3, DeepSeek-R1) and prose-before-JSON (any ollama model).

  Across commands (between you> prompts):
    Key findings from tool calls are accumulated in a session working-memory
    and injected into every subsequent LLM call.  This lets the model say
    "Based on our earlier detect_preset result, the preset is posix, so I
    should …" without repeating tool calls.

Usage:
    python test/mcp_agent.py
    python test/mcp_agent.py --service utils       # dftracer_utils (21 tools)
    python test/mcp_agent.py --service analyzer    # dfanalyzer + plot (7 tools)
    python test/mcp_agent.py --service both        # all three services (28 tools)
    python test/mcp_agent.py --config path/to/other_config.json
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import textwrap
from pathlib import Path
from typing import Any

from openai import OpenAI
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

# ── paths ──────────────────────────────────────────────────────────────────
REPO_ROOT = Path(__file__).resolve().parents[1]
VENV_PYTHON = Path(sys.executable)   # run from the project venv (.venv)
VENV_BIN    = VENV_PYTHON.parent
THIS_DIR    = Path(__file__).resolve().parent
DEFAULT_CONFIG = THIS_DIR / "openai_client_config.json"


# ── MCP server params ──────────────────────────────────────────────────────

# the package server, run as a module by the interpreter below (the dftracer-mcp-server console script)
MCP_SERVER_ARGS = ["-m", "dftracer_agents.mcp_server"]


def _server_params(service: str) -> StdioServerParameters:
    env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PATH": f"{VENV_BIN}:{os.environ.get('PATH', '')}",
    }
    return StdioServerParameters(
        command=str(VENV_PYTHON),
        args=[*MCP_SERVER_ARGS, "--service", service],
        cwd=str(REPO_ROOT),
        env=env,
    )


# ── MCP → OpenAI function schema conversion ────────────────────────────────

def _mcp_tool_to_openai(tool) -> dict[str, Any]:
    schema = tool.inputSchema or {}
    params = {
        "type": "object",
        "properties": schema.get("properties", {}),
    }
    if "required" in schema:
        params["required"] = schema["required"]
    return {
        "type": "function",
        "function": {
            "name": tool.name,
            "description": (tool.description or "")[:1024],
            "parameters": params,
        },
    }


# ── result formatter ────────────────────────────────────────────────────────

def _format_result(result) -> str:
    parts = [item.text for item in result.content if hasattr(item, "text")]
    text = "\n".join(parts)
    if result.isError:
        return f"[tool error]\n{text}"
    return text or "(empty output)"


# ── display helpers ─────────────────────────────────────────────────────────

_THINK_OPEN  = re.compile(r"<think(?:ing)?>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</think(?:ing)?>", re.IGNORECASE)

WIDTH = 72


def _strip_think_tags(text: str) -> tuple[str, str]:
    """Return (thinking_text, remainder) by extracting <think>…</think> blocks."""
    thinking_parts: list[str] = []
    remainder = text
    while True:
        m_open = _THINK_OPEN.search(remainder)
        if not m_open:
            break
        m_close = _THINK_CLOSE.search(remainder, m_open.end())
        if not m_close:
            thinking_parts.append(remainder[m_open.end():].strip())
            remainder = remainder[: m_open.start()].strip()
            break
        thinking_parts.append(remainder[m_open.end(): m_close.start()].strip())
        remainder = (remainder[: m_open.start()] + remainder[m_close.end():]).strip()
    return "\n\n".join(thinking_parts), remainder


def _split_prose_from_json(text: str, known_tool_names: list[str]) -> tuple[str, str]:
    """Separate leading prose reasoning from the JSON tool-call payload.

    Returns (thinking_prose, json_payload).
    """
    for i, ch in enumerate(text):
        if ch not in ("{", "["):
            continue
        # Try bracket-balanced extraction
        depth, j = 0, i
        while j < len(text):
            if text[j] in ("{", "["):
                depth += 1
            elif text[j] in ("}", "]"):
                depth -= 1
                if depth == 0:
                    try:
                        json.loads(text[i: j + 1])
                        return text[:i].strip(), text[i: j + 1]
                    except json.JSONDecodeError:
                        pass
            j += 1
    return text.strip(), ""


def _print_thinking(text: str, label: str = "thinking") -> None:
    """Print a reasoning block with a bordered box."""
    if not text.strip():
        return
    inner = WIDTH - 4
    header = f"  ┌─ {label} " + "─" * (WIDTH - 4 - len(label)) + "┐"
    footer = "  └" + "─" * (WIDTH - 2) + "┘"
    print()
    print(header)
    for line in text.splitlines():
        wrapped = textwrap.wrap(line, inner) or [""]
        for wl in wrapped:
            print(f"  │ {wl:<{inner}} │")
    print(footer)


def _print_memory(notes: list[str]) -> None:
    """Print the current working memory at the start of each turn."""
    if not notes:
        return
    inner = WIDTH - 4
    print()
    print("  ┌─ session memory " + "─" * (WIDTH - 18) + "┐")
    for note in notes:
        wrapped = textwrap.wrap(f"• {note}", inner) or [""]
        for i, wl in enumerate(wrapped):
            prefix = "  " if i > 0 else ""
            print(f"  │ {prefix + wl:<{inner}} │")
    print("  └" + "─" * (WIDTH - 2) + "┘")


# ── JSON tool-call parser ───────────────────────────────────────────────────

def _parse_json_tool_calls(content: str, known_tool_names: list[str]) -> list[dict] | None:
    if not content:
        return None
    text = content.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text).strip()

    def _extract_from_parsed(parsed) -> list[dict] | None:
        if isinstance(parsed, dict):
            parsed = [parsed]
        if not isinstance(parsed, list):
            return None
        calls = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            name = item.get("name") or item.get("function") or item.get("tool")
            args = item.get("arguments") or item.get("parameters") or item.get("args") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name and isinstance(args, dict):
                calls.append({"name": name, "arguments": args})
        return calls or None

    try:
        calls = _extract_from_parsed(json.loads(text))
        if calls:
            return calls
    except json.JSONDecodeError:
        pass

    for tool_name in known_tool_names:
        m = re.compile(rf"^{re.escape(tool_name)}\s*(\{{.*\}})", re.DOTALL).match(text)
        if m:
            try:
                args = json.loads(m.group(1))
                if isinstance(args, dict):
                    return [{"name": tool_name, "arguments": args}]
            except json.JSONDecodeError:
                pass

    m = re.search(r'(\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\})', text, re.DOTALL)
    if m:
        try:
            calls = _extract_from_parsed(json.loads(m.group(0)))
            if calls:
                return calls
        except json.JSONDecodeError:
            pass

    return None


# ── working-memory extractor ────────────────────────────────────────────────

def _extract_memory_note(tool_name: str, result_text: str) -> str | None:
    """Distil a one-liner fact from a tool result to store in working memory.

    Called after every successful tool call.  Returns a short string like
    "detect_preset → posix (no AI/ML signals)" or None to skip.
    """
    # Grab first meaningful line of the result
    lines = [l.strip() for l in result_text.splitlines() if l.strip()]
    if not lines:
        return None

    if tool_name == "detect_preset":
        for line in lines:
            if "Recommended preset" in line:
                preset = line.split(":")[-1].strip()
                aiml = "AI/ML detected" if "dlio" in preset else "no AI/ML signals"
                return f"detect_preset → preset={preset} ({aiml})"

    if tool_name == "summarize_trace":
        facts: list[str] = []
        for line in lines:
            for key in ("Trace files", "Processes", "Duration", "Bytes read", "Bytes written"):
                if key in line:
                    facts.append(line.lstrip(" ").lstrip("•"))
        if facts:
            return f"summarize_trace → " + "; ".join(facts[:4])

    if tool_name == "query":
        # Grab header info + first data row
        header = next((l for l in lines if "view_type" in l), "")
        first_row = next((l for l in lines if l[0].isdigit() or (l and l[0] in "0123456789")), "")
        if header:
            vt = header.split(":")[-1].strip()
            summary = f"query view_type={vt}"
            if first_row:
                summary += f" → top row: {first_row[:60]}"
            return summary

    if tool_name in ("analyze",):
        # Just note that it was called
        return f"{tool_name} → {lines[0][:80]}"

    return None


# ── system prompt ───────────────────────────────────────────────────────────

SYSTEM_PROMPT = """\
You are a helpful assistant for the DFTracer I/O tracing toolkit.

You have access to MCP tools that wrap dftracer_* command-line utilities and
dfanalyzer.

CHAIN-OF-THOUGHT INSTRUCTIONS
Before calling any tool, reason out loud in 2–4 sentences:
  1. What the user is asking and what you already know from this session.
  2. What is still unknown and needs a tool call to discover.
  3. Which tool you will call next and exactly why.
  4. What you expect the tool to return.

Write your reasoning as plain prose, then follow it immediately with the tool
call in JSON format on a new line.  Call only one tool per response.

When a "Session memory" block appears in the conversation, use those facts to
skip redundant tool calls and explain your choices in terms of prior findings.
For example: "We already know the preset is posix from detect_preset, so I
will skip that step and go straight to summarize_trace."

RECOMMENDED ANALYSIS WORKFLOW (unless the user specifies otherwise):
  1. detect_preset   — determine posix vs dlio from event categories.
  2. summarize_trace — quick overview (events, duration, bytes, top ops).
  3. query           — drill in with proc_name / file_name / time_range views.
  4. analyze         — full dfanalyzer pipeline (requires native C++ support).

If a tool fails, report the error, explain the likely cause, and suggest a fix.
Summarise large outputs rather than echoing them verbatim.
"""


# ── agent loop ──────────────────────────────────────────────────────────────

async def _agent_loop(session: ClientSession, llm: OpenAI, model: str) -> None:
    tools_response = await session.list_tools()
    openai_tools = [_mcp_tool_to_openai(t) for t in tools_response.tools]
    tool_names = [t.name for t in tools_response.tools]

    print()
    print("=" * WIDTH)
    print("  dftracer-agents LLM Agent  (chain-of-thought + session memory)")
    print("=" * WIDTH)
    print(f"  Model : {model}")
    print(f"  Tools : {len(openai_tools)} MCP tools available")
    print("  Type your question in plain English.  Ctrl-C or 'quit' to exit.")
    print("=" * WIDTH)
    print()

    # Base conversation — never mutated directly; rebuilt each user turn
    base_conversation: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]

    # Working memory: accumulates one-liner facts across ALL user turns
    session_memory: list[str] = []

    # Full transcript: grows across all user turns (for LLM context)
    transcript: list[dict] = list(base_conversation)

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            break
        if not user_input:
            continue
        if user_input.lower() in ("quit", "exit", "q"):
            print("Bye.")
            break

        # Show working memory so user can see what the agent remembers
        if session_memory:
            _print_memory(session_memory)

        # Inject memory as a system note at the start of the new user turn
        if session_memory:
            memory_block = (
                "Session memory (facts gathered so far — use these to avoid "
                "repeating tool calls and to reason about next steps):\n"
                + "\n".join(f"  • {n}" for n in session_memory)
            )
            # Prepend memory note just before the new user message
            transcript.append({"role": "system", "content": memory_block})

        transcript.append({"role": "user", "content": user_input})

        # ── inner agentic loop for this user turn ─────────────────────────────
        while True:
            print("  [thinking…]", end="\r", flush=True)
            try:
                response = llm.chat.completions.create(
                    model=model,
                    messages=transcript,
                    tools=openai_tools,
                    tool_choice="auto",
                )
            except Exception as e:
                print(f"\n  [LLM error] {type(e).__name__}: {e}")
                print("  Is ollama running?  Try: ollama serve")
                break

            msg = response.choices[0].message
            print(" " * 20, end="\r")

            # ── case 1: structured tool_calls ─────────────────────────────────
            if msg.tool_calls:
                if msg.content and msg.content.strip():
                    _print_thinking(msg.content.strip())
                transcript.append(msg.model_dump(exclude_unset=True))
                for tc in msg.tool_calls:
                    try:
                        args = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        args = {}
                    print(f"  [tool] {tc.function.name}({json.dumps(args, separators=(',', ':'))})")
                    try:
                        mcp_result = await session.call_tool(tc.function.name, args)
                        result_text = _format_result(mcp_result)
                    except Exception as exc:
                        result_text = f"Error: {exc}"
                    if len(result_text) > 8000:
                        result_text = result_text[:8000] + "\n…(truncated)"
                    print(f"  [result] {result_text[:300].replace(chr(10), ' ')}")

                    note = _extract_memory_note(tc.function.name, result_text)
                    if note:
                        session_memory.append(note)

                    transcript.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": result_text,
                    })
                continue

            raw_content = msg.content or ""

            # ── extract native <think>…</think> tags ──────────────────────────
            native_thinking, raw_content = _strip_think_tags(raw_content)
            if native_thinking:
                _print_thinking(native_thinking, label="thinking")

            # ── case 2: JSON-in-content (ollama) ──────────────────────────────
            prose_reasoning, json_payload = _split_prose_from_json(raw_content, tool_names)
            parsed_calls = _parse_json_tool_calls(json_payload, tool_names) if json_payload else None

            if parsed_calls:
                if prose_reasoning:
                    _print_thinking(prose_reasoning, label="thinking")

                # Store the assistant's reasoning in the transcript so future
                # turns can see how the model was thinking at each step.
                transcript.append({
                    "role": "assistant",
                    "content": prose_reasoning or raw_content,
                })

                for call in parsed_calls:
                    tool_name = call["name"]
                    args = call["arguments"]
                    print(f"  [tool] {tool_name}({json.dumps(args, separators=(',', ':'))})")
                    if tool_name not in tool_names:
                        result_text = f"Error: unknown tool '{tool_name}'"
                    else:
                        try:
                            mcp_result = await session.call_tool(tool_name, args)
                            result_text = _format_result(mcp_result)
                        except Exception as exc:
                            result_text = f"Error: {exc}"
                    if len(result_text) > 8000:
                        result_text = result_text[:8000] + "\n…(truncated)"
                    print(f"  [result] {result_text[:300].replace(chr(10), ' ')}")

                    note = _extract_memory_note(tool_name, result_text)
                    if note:
                        session_memory.append(note)

                    # Inject result so the LLM can continue its reasoning chain
                    transcript.append({
                        "role": "user",
                        "content": (
                            f"Tool '{tool_name}' returned:\n{result_text}\n\n"
                            "Continue your chain of thought: what did this tell you, "
                            "and what is your next step?"
                        ),
                    })
                continue

            # ── case 3: plain text answer ─────────────────────────────────────
            reply = raw_content.strip()
            print(f"\nagent> {reply}\n")
            # Store final answer so future turns can reference it
            transcript.append({"role": "assistant", "content": reply})
            break


# ── entry point ─────────────────────────────────────────────────────────────

async def _main(service: str, config_path: Path) -> None:
    try:
        cfg = json.loads(config_path.read_text())
    except FileNotFoundError:
        print(f"Config not found: {config_path}")
        sys.exit(1)

    llm = OpenAI(
        base_url=cfg["base_url"],
        api_key=cfg.get("api_key", "ollama"),
    )
    model = cfg["model"]
    params = _server_params(service)

    print(f"Starting MCP server ({service})…")
    print(f"Connecting to LLM at {cfg['base_url']} (model={model})")

    with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as errlog:
        errlog_path = errlog.name
    print(f"Server stderr → {errlog_path}")

    with open(errlog_path, "w") as errlog:
        async with stdio_client(params, errlog=errlog) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                await _agent_loop(session, llm, model)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="LLM-powered agent for dftracer-agents MCP services"
    )
    parser.add_argument(
        "--service", choices=["utils", "analyzer", "both"], default="utils",
        help="Which MCP service to connect to: utils (21 tools), analyzer (5 tools + 2 plot), both = all (default: utils)",
    )
    parser.add_argument(
        "--config", type=Path, default=DEFAULT_CONFIG,
        help="Path to openai_client_config.json",
    )
    args = parser.parse_args()
    asyncio.run(_main(args.service, args.config))


if __name__ == "__main__":
    main()
