"""
Configure MCP clients (Claude Code and Goose) to connect to the
dftracer-mcp-server HTTP endpoint.

CLI usage (after pip install)::

    dftracer-configure-mcp                  # localhost:5000 (default)
    dftracer-configure-mcp --port 8080
    dftracer-configure-mcp --no-goose       # skip Goose config
    dftracer-configure-mcp --dry-run        # show what would change

Programmatic usage::

    from dftracer_agents.mcp_setup import configure_claude_code, configure_goose
    configure_claude_code()
    configure_goose()
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path


MCP_SERVER_NAME = "dftracer"


def _mcp_url(host: str, port: int, path: str) -> str:
    return f"http://{host}:{port}{path}"


# ---------------------------------------------------------------------------
# Claude Code — ~/.claude/mcp.json
# ---------------------------------------------------------------------------

def configure_claude_code(
    host: str = "localhost",
    port: int = 5000,
    path: str = "/mcp",
    dry_run: bool = False,
) -> Path:
    """Write/merge the dftracer entry into ~/.claude/mcp.json.

    Returns the path to the config file.
    """
    config_path = Path.home() / ".claude" / "mcp.json"
    url = _mcp_url(host, port, path)

    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            print(
                f"  WARNING: {config_path} contains invalid JSON — will overwrite.",
                file=sys.stderr,
            )

    servers: dict = existing.setdefault("mcpServers", {})
    prev = servers.get(MCP_SERVER_NAME)
    servers[MCP_SERVER_NAME] = {"url": url}

    if prev == servers[MCP_SERVER_NAME]:
        print(f"  Claude Code: {config_path} already up to date ({url})")
        return config_path

    if dry_run:
        print(f"  [dry-run] Would write to {config_path}:")
        print(f"    mcpServers.{MCP_SERVER_NAME}.url = {url}")
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n")
    action = "Updated" if prev else "Configured"
    print(f"  Claude Code: {action} {config_path}")
    print(f"    {MCP_SERVER_NAME} → {url}")
    return config_path


# ---------------------------------------------------------------------------
# Project-level clients — point every harness at the stack's HTTP server
# ---------------------------------------------------------------------------
#
# A project config that spells the server as a `command` makes the HARNESS spawn
# its own private stdio server, ignoring the one `dftracer_agents_stack` manages.
# Two servers then run: the managed one nobody talks to, and an unmanaged one
# that reloads nothing and appears in `status` as untracked. Pointing the clients
# at a `url` is what makes the managed service the one actually used.


def _merge_server_entry(config_path: Path, top_key: str, entry: dict,
                        label: str, dry_run: bool) -> Path:
    """Merge ``{top_key: {dftracer: entry}}`` into a JSON config, preserving the rest."""
    existing: dict = {}
    if config_path.exists():
        try:
            existing = json.loads(config_path.read_text())
        except json.JSONDecodeError:
            print(f"  WARNING: {config_path} contains invalid JSON — will overwrite.",
                  file=sys.stderr)

    servers: dict = existing.setdefault(top_key, {})
    prev = servers.get(MCP_SERVER_NAME)
    if prev == entry:
        print(f"  {label}: {config_path} already up to date")
        return config_path

    servers[MCP_SERVER_NAME] = entry
    if dry_run:
        print(f"  [dry-run] Would write {config_path}: {top_key}.{MCP_SERVER_NAME} = {entry}")
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, indent=2) + "\n")
    was_stdio = isinstance(prev, dict) and "command" in prev
    action = "Switched from stdio to HTTP in" if was_stdio else (
        "Updated" if prev else "Configured")
    print(f"  {label}: {action} {config_path}")
    # `dftracer-bootstrap-workspace` symlinks these configs to the packaged
    # templates under src/. Writing through the link edits repo source, so say so
    # — a non-default port silently committed to the template is a nasty surprise.
    if config_path.is_symlink():
        print(f"    note: symlink → {config_path.resolve()} (repo source was edited)")
    return config_path


def configure_project_claude(root: Path, url: str, dry_run: bool = False) -> Path:
    """Point the project's ``.mcp.json`` (Claude Code) at the managed server."""
    return _merge_server_entry(root / ".mcp.json", "mcpServers",
                               {"type": "http", "url": url}, "Claude Code", dry_run)


def configure_vscode(root: Path, url: str, dry_run: bool = False) -> Path:
    """Point ``.vscode/mcp.json`` (GitHub Copilot) at the managed server."""
    return _merge_server_entry(root / ".vscode" / "mcp.json", "servers",
                               {"type": "http", "url": url}, "Copilot", dry_run)


# ---------------------------------------------------------------------------
# Codex CLI — <root>/.codex/config.toml
# ---------------------------------------------------------------------------
#
# No TOML library is a project dependency (see mcp_setup._strip_jsonc for the
# same hand-rolled-over-dependency choice with JSONC). The merge target is
# narrow enough — replace-or-append one ``[mcp_servers.dftracer]`` table —
# that a small regex-based table replace is safe and keeps every other table
# or comment in the file untouched.

_CODEX_TABLE_RE = re.compile(r"\[mcp_servers\.dftracer\][^\[]*")


def _codex_table_block(url: str) -> str:
    return f'[mcp_servers.dftracer]\nurl = "{url}"\n'


def _is_project_trusted(root: Path) -> bool:
    """Best-effort, read-only check of the user's own ~/.codex/config.toml.

    Never writes there — marking a project trusted is the user's call (a
    repo cannot be allowed to trust itself). This only avoids nagging a user
    who has already done it.
    """
    global_config = Path.home() / ".codex" / "config.toml"
    if not global_config.exists():
        return False
    try:
        text = global_config.read_text()
    except OSError:
        return False
    root_str = str(root.resolve())
    pattern = re.compile(
        r'\[projects\."' + re.escape(root_str) + r'"\][^\[]*trust_level\s*=\s*"trusted"',
        re.DOTALL,
    )
    return bool(pattern.search(text))


def configure_project_codex(root: Path, url: str, dry_run: bool = False) -> Path:
    """Point ``.codex/config.toml`` (Codex CLI) at the managed server."""
    config_path = root / ".codex" / "config.toml"
    block = _codex_table_block(url)

    existing = config_path.read_text() if config_path.exists() else ""
    if _CODEX_TABLE_RE.search(existing):
        new_text = _CODEX_TABLE_RE.sub(block, existing, count=1)
    elif existing:
        sep = "" if existing.endswith("\n\n") else ("\n" if existing.endswith("\n") else "\n\n")
        new_text = existing + sep + block
    else:
        new_text = block

    if new_text == existing:
        print(f"  Codex:       {config_path} already up to date ({url})")
        return config_path

    if dry_run:
        print(f"  [dry-run] Would write {config_path}: mcp_servers.dftracer.url = {url}")
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    action = "Updated" if existing else "Configured"
    config_path.write_text(new_text)
    print(f"  Codex:       {action} {config_path}")
    if config_path.is_symlink():
        print(f"    note: symlink → {config_path.resolve()} (repo source was edited)")
    if not _is_project_trusted(root):
        print(
            f'    note: Codex only loads project config for trusted projects — add '
            f'[projects."{root.resolve()}"]\\n    trust_level = "trusted" to your own '
            f"~/.codex/config.toml to let it load this."
        )
    return config_path


def _strip_jsonc(text: str) -> str:
    """Remove JSONC comments and trailing commas, leaving parseable JSON.

    A regex cannot do this: ``"https://opencode.ai/config.json"`` and every URL we
    write contain ``//`` *inside a string literal*. So scan, copying string
    literals through verbatim and eliding only comments outside them.
    """
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            nl = text.find("\n", i)
            if nl == -1:
                break
            i = nl
        elif text.startswith("/*", i):
            end = text.find("*/", i)
            i = n if end == -1 else end + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _has_comments(text: str) -> bool:
    """True if *text* contains a JSONC comment *outside* a string literal.

    A naive ``"//" in text`` matches the ``//`` in every URL the file contains —
    including the ones we write — and would make the tool refuse to touch the
    file forever after.
    """
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
        elif text.startswith("//", i) or text.startswith("/*", i):
            return True
        else:
            i += 1
    return False


def _jsonc_iter(text: str, start: int = 0, end: int | None = None):
    """Yield ``(kind, a, b)`` spans of *text*, eliding comments.

    ``kind`` is ``"str"`` for a whole string literal (quotes included) or
    ``"chr"`` for a single structural character. Comments are skipped, so a
    caller never has to worry about ``//`` inside a URL — the string literal
    holding it is yielded whole.
    """
    n = len(text) if end is None else end
    i = start
    while i < n:
        if text[i] == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            j = min(j + 1, n)
            yield "str", i, j
            i = j
        elif text.startswith("//", i):
            nl = text.find("\n", i, n)
            i = n if nl == -1 else nl + 1
        elif text.startswith("/*", i):
            e = text.find("*/", i, n)
            i = n if e == -1 else e + 2
        else:
            yield "chr", i, i + 1
            i += 1


def _jsonc_next_significant(text: str, i: int, end: int) -> int:
    """Index of the next character that is neither whitespace nor a comment."""
    while i < end:
        if text.startswith("//", i):
            nl = text.find("\n", i, end)
            i = end if nl == -1 else nl + 1
        elif text.startswith("/*", i):
            e = text.find("*/", i, end)
            i = end if e == -1 else e + 2
        elif text[i].isspace():
            i += 1
        else:
            return i
    return -1


def _jsonc_find_key(text: str, key: str, start: int = 0, end: int | None = None) -> int:
    """Index just past the ``:`` of object key *key*, or -1.

    Requiring the colon is what keeps this from matching a string that merely
    happens to equal the key — a ``"url"`` sitting in a value position, say.
    """
    end = len(text) if end is None else end
    for kind, a, b in _jsonc_iter(text, start, end):
        if kind != "str" or text[a + 1:b - 1] != key:
            continue
        c = _jsonc_next_significant(text, b, end)
        if c != -1 and text[c] == ":":
            return c + 1
    return -1


def _jsonc_object_span(text: str, start: int, end: int | None = None) -> tuple[int, int]:
    """``(open, close)`` indices of the ``{...}`` beginning at/after *start*."""
    end = len(text) if end is None else end
    depth, open_idx = 0, -1
    for kind, a, _b in _jsonc_iter(text, start, end):
        if kind != "chr":
            # A string before the opening brace means this value is not an
            # object; inside one it is just a key or a value, so skip it.
            if depth == 0:
                return -1, -1
            continue
        ch = text[a]
        if ch == "{":
            if depth == 0:
                open_idx = a
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return open_idx, a
        elif depth == 0 and not ch.isspace():
            return -1, -1
    return -1, -1


def _jsonc_string_span(text: str, start: int, end: int | None = None) -> tuple[int, int]:
    """``(a, b)`` span of the string literal at/after *start*, quotes included."""
    end = len(text) if end is None else end
    for kind, a, b in _jsonc_iter(text, start, end):
        if kind == "str":
            return a, b
        if not text[a].isspace():
            return -1, -1
    return -1, -1


def _jsonc_set_server_url(text: str, server: str, url: str) -> str | None:
    """Rewrite only the ``mcp.<server>.url`` string, preserving every comment.

    Returns the new text, or ``None`` if the block is not shaped the way we
    expect — in which case the caller falls back to asking a human, rather than
    guessing at a config it cannot faithfully reproduce.
    """
    i = _jsonc_find_key(text, "mcp")
    if i == -1:
        return None
    m_open, m_close = _jsonc_object_span(text, i)
    if m_open == -1:
        return None
    j = _jsonc_find_key(text, server, m_open + 1, m_close)
    if j == -1:
        return None
    s_open, s_close = _jsonc_object_span(text, j, m_close)
    if s_open == -1:
        return None
    k = _jsonc_find_key(text, "url", s_open + 1, s_close)
    if k == -1:
        return None
    a, b = _jsonc_string_span(text, k, s_close)
    if a == -1:
        return None
    return text[:a] + json.dumps(url) + text[b:]


def configure_opencode(root: Path, url: str, dry_run: bool = False) -> Path:
    """Point ``.opencode/opencode.jsonc`` (OpenCode) at the managed server.

    ``opencode.jsonc`` carries comments that a JSON round-trip would silently
    delete, and that file also holds the user's provider and model settings. So
    we never round-trip it. In practice the only thing that drifts is the url
    (the managed server's port moves), which is a single string we can replace
    in place without touching a byte of the surrounding comments. Anything else
    still gets the paste-this-snippet message — printing that for a stale port
    is how this file silently fell out of sync with the other harnesses.
    """
    config_path = root / ".opencode" / "opencode.jsonc"
    entry = {"type": "remote", "url": url, "enabled": True}

    raw = config_path.read_text() if config_path.exists() else ""
    if config_path.exists() and _has_comments(raw):
        # Read through the comments before nagging: the packaged template already
        # carries the right block, and telling the user to "add it by hand" when
        # it is already there teaches them to ignore this tool.
        try:
            current = json.loads(_strip_jsonc(raw)).get("mcp", {}).get(MCP_SERVER_NAME)
        except json.JSONDecodeError:
            current = None
        if current == entry:
            print(f"  OpenCode: {config_path} already up to date")
            return config_path

        if isinstance(current, dict) and {**current, "url": url} == entry:
            new_text = _jsonc_set_server_url(raw, MCP_SERVER_NAME, url)
            if new_text is not None:
                if dry_run:
                    print(f"  [dry-run] Would update {config_path}: "
                          f"mcp.{MCP_SERVER_NAME}.url = {url}")
                    return config_path
                config_path.write_text(new_text)
                print(f"  OpenCode: Updated {config_path} ({url})")
                if config_path.is_symlink():
                    print(f"    note: symlink → {config_path.resolve()} "
                          f"(repo source was edited)")
                return config_path

        print(f"  OpenCode: {config_path} has comments — not rewriting it.")
        print("    Add (or replace) this block by hand:")
        print(f'      "mcp": {{ "{MCP_SERVER_NAME}": {json.dumps(entry)} }}')
        return config_path

    return _merge_server_entry(config_path, "mcp", entry, "OpenCode", dry_run)


def configure_project_clients(root: Path, host: str = "127.0.0.1", port: int = 5000,
                              path: str = "/mcp", dry_run: bool = False) -> str:
    """Point Claude Code, Copilot, OpenCode and Codex at the stack's HTTP MCP server."""
    url = _mcp_url(host, port, path)
    print(f"  Managed MCP server: {url}")
    configure_project_claude(root, url, dry_run)
    configure_vscode(root, url, dry_run)
    configure_opencode(root, url, dry_run)
    configure_project_codex(root, url, dry_run)
    return url


# ---------------------------------------------------------------------------
# Goose — ~/.config/goose/config.yaml  (or ~/.goose/config.yaml)
# ---------------------------------------------------------------------------

def _goose_config_path() -> Path:
    """Return the active Goose config path (prefer ~/.config/goose, fallback ~/.goose)."""
    primary = Path.home() / ".config" / "goose" / "config.yaml"
    if primary.exists():
        return primary
    fallback = Path.home() / ".goose" / "config.yaml"
    if fallback.exists():
        return fallback
    return primary  # default create location


def configure_goose(
    host: str = "localhost",
    port: int = 5000,
    path: str = "/mcp",
    dry_run: bool = False,
) -> Path | None:
    """Write/merge the dftracer extension into the Goose config.

    Returns the path written, or None if PyYAML is not available.
    """
    try:
        import yaml  # type: ignore
    except ImportError:
        _print_goose_snippet(host, port, path)
        return None

    config_path = _goose_config_path()
    url = _mcp_url(host, port, path)

    config: dict = {}
    if config_path.exists():
        with config_path.open() as f:
            config = yaml.safe_load(f) or {}

    extensions: dict = config.setdefault("extensions", {})
    entry = extensions.get(MCP_SERVER_NAME, {})
    new_entry = {
        "enabled": True,
        "type": "streamable_http",
        "name": MCP_SERVER_NAME,
        "description": "mcp tools for using dftracer",
        "uri": url,
        "bundled": None,
        "available_tools": [],
    }

    if entry == new_entry:
        print(f"  Goose:       {config_path} already up to date ({url})")
        return config_path

    extensions[MCP_SERVER_NAME] = new_entry

    if dry_run:
        print(f"  [dry-run] Would write to {config_path}:")
        print(f"    extensions.{MCP_SERVER_NAME}.uri = {url}")
        return config_path

    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w") as f:
        yaml.dump(config, f, default_flow_style=False, allow_unicode=True)

    action = "Updated" if entry else "Configured"
    print(f"  Goose:       {action} {config_path}")
    print(f"    {MCP_SERVER_NAME} → {url}")
    return config_path


def _print_goose_snippet(host: str, port: int, path: str) -> None:
    url = _mcp_url(host, port, path)
    print(
        "  Goose:       PyYAML not installed — add this to ~/.config/goose/config.yaml manually:\n"
        f"\n"
        f"    extensions:\n"
        f"      {MCP_SERVER_NAME}:\n"
        f"        enabled: true\n"
        f"        type: streamable_http\n"
        f"        name: {MCP_SERVER_NAME}\n"
        f"        description: mcp tools for using dftracer\n"
        f"        uri: {url}\n"
    )


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    """Entry point for the ``dftracer-configure-mcp`` CLI command."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-configure-mcp",
        description=(
            "Configure Claude Code and Goose to connect to the dftracer MCP "
            "HTTP server. Writes ~/.claude/mcp.json and ~/.config/goose/config.yaml."
        ),
    )
    parser.add_argument(
        "--host",
        default="localhost",
        help="Host the MCP server will run on (default: localhost)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port the MCP server will listen on (default: 5000)",
    )
    parser.add_argument(
        "--path",
        default="/mcp",
        help="URL path for the MCP endpoint (default: /mcp)",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Skip Claude Code configuration",
    )
    parser.add_argument(
        "--no-goose",
        action="store_true",
        help="Skip Goose configuration",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would change without writing any files",
    )
    args = parser.parse_args()

    print("Configuring MCP clients for dftracer-mcp-server...")
    print(f"  Server URL:  http://{args.host}:{args.port}{args.path}")
    print()

    if not args.no_claude:
        configure_claude_code(
            host=args.host,
            port=args.port,
            path=args.path,
            dry_run=args.dry_run,
        )

    if not args.no_goose:
        configure_goose(
            host=args.host,
            port=args.port,
            path=args.path,
            dry_run=args.dry_run,
        )

    print()
    print("Done. Start the server with:  dftracer-mcp-server")
    print("  (or add it to your shell startup / systemd user unit)")


if __name__ == "__main__":
    main()
