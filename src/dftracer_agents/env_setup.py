"""
Create/update the project's ``.env`` with API keys and auth tokens.

Installed by pip as the ``dftracer-configure-env`` console script so it works
the same way whether you're in a repo checkout or a plain ``pip install``:
it locates the project root the same way ``dftracer_agents_stack`` does
(walk up from cwd for ``workspaces/``, then ``.git``/``pyproject.toml``, else
cwd itself) and writes ``.env`` there — never inside the installed package.

What it manages:

* ``DFTRACER_MCP_TOKEN`` / ``DFTRACER_COLLECTOR_TOKEN`` — auto-generated with
  ``secrets.token_hex(32)`` if blank. These gate the docker-compose stack's
  MCP/OTLP endpoints behind Caddy (see README.md).
* ``SEMANTIC_SCHOLAR_API_KEY`` / ``CORE_API_KEY`` / ``OPENALEX_MAILTO`` —
  prompted for (hidden input), all optional. Every academic-paper-search tool
  in ``mcp_tools/tools/papers/academic_service.py`` already falls back to
  anonymous, client-side-rate-limited access when these are blank, so leaving
  them empty is a supported, normal configuration — not a degraded one.
* ``MCP_PORT`` / ``COLLECTOR_PORT`` / ``MLFLOW_PORT`` — probed against real
  socket binds on 127.0.0.1 and set to the launcher's default (5000/4318/5001)
  if free, or the next free port after it if not. Shared HPC login nodes are
  multi-tenant on the loopback interface, so a colliding default is common and
  otherwise only discovered the hard way, mid-``dftracer_agents_stack start``
  (see ``dftracer_agents_stack``'s own ``MCP_PORT``/``COLLECTOR_PORT``/
  ``MLFLOW_PORT`` env var overrides).

Idempotent: re-running only fills in blanks, never overwrites a value you
already set. Existing unrelated lines/comments in .env are left untouched.

CLI usage (after pip install)::

    dftracer-configure-env                    # interactive prompts
    dftracer-configure-env --non-interactive   # only fills tokens; seeds API
                                                # keys from the environment if present
"""
from __future__ import annotations

import argparse
import getpass
import os
import secrets
import socket
import stat
import sys
from pathlib import Path
from typing import List, Optional

#: Markers that identify a project root — same convention as dftracer_agents_stack.
_ROOT_MARKERS = ("workspaces", ".git", "pyproject.toml")

#: Fallback template used when neither an existing .env nor .env.example is
#: found (e.g. a bare `pip install` run outside a repo checkout). Kept in
#: sync by hand with the repo's own .env.example.
_DEFAULT_TEMPLATE = """\
# Copy to .env and set for your machine. .env is gitignored — never commit it.
ROOT_DIR=/path/to/dftracer-agents
SANDBOX_FLAGS=--config=$ROOT_DIR/scripts/sandbox/sandbox-config.yaml
CLAUDE_SANDBOX=/path/to/claude-sandbox

# docker-compose.yaml — auth for the dftracer stack behind the Caddy proxy.
# Bearer tokens for MCP and the OTLP collector — any random string works:
#   openssl rand -hex 32
DFTRACER_MCP_TOKEN=
DFTRACER_COLLECTOR_TOKEN=

# MLflow UI basic-auth. Generate the hash with:
#   docker run --rm caddy:2 caddy hash-password --plaintext '<your-password>'
MLFLOW_BASIC_AUTH_USER=
MLFLOW_PASSWORD_HASH=

# Academic paper search (src/dftracer_agents/mcp_tools/tools/papers/academic_service.py).
# All are optional — every source falls back to unauthenticated/anonymous
# access and is still client-side rate-limited when left blank.
#   Semantic Scholar: apply at https://www.semanticscholar.org/product/api
#   CORE:             register at https://core.ac.uk/services/api
#   OpenAlex/Crossref polite pool: just your email, no signup needed
SEMANTIC_SCHOLAR_API_KEY=
CORE_API_KEY=
OPENALEX_MAILTO=

# dftracer_agents_stack port overrides. Blank = the launcher's own defaults
# (5000/4318/5001). dftracer-configure-env probes these on 127.0.0.1 and picks
# a free port automatically — shared HPC login nodes commonly have the
# defaults already taken by another user's session.
MCP_PORT=
COLLECTOR_PORT=
MLFLOW_PORT=
"""

#: Auth tokens auto-generated with secrets.token_hex(32) when blank.
_AUTO_TOKENS = ["DFTRACER_MCP_TOKEN", "DFTRACER_COLLECTOR_TOKEN"]

#: (key, human-readable description) for the optional, prompted API keys.
_OPTIONAL_KEYS = [
    ("SEMANTIC_SCHOLAR_API_KEY",
     "Semantic Scholar API key — raises your daily quota (still capped at 1 req/s).\n"
     "  Apply at: https://www.semanticscholar.org/product/api"),
    ("CORE_API_KEY",
     "CORE API key — enables full-text open-access search (search_core).\n"
     "  Register at: https://core.ac.uk/services/api"),
    ("OPENALEX_MAILTO",
     "Email for OpenAlex/Crossref's polite pool — improves reliability, no signup needed."),
]

#: (key, dftracer_agents_stack's own default) — must match MCP_PORT/COLLECTOR_PORT/
#: MLFLOW_PORT's fallbacks in the bash launcher itself.
_STACK_PORTS = [
    ("MCP_PORT", 5000),
    ("COLLECTOR_PORT", 4318),
    ("MLFLOW_PORT", 5001),
]

#: How far past the default (or a busy already-set value) to search for a free
#: port before giving up and leaving the field for the user to sort out by hand.
_PORT_SEARCH_RANGE = 50


def _port_is_free(port: int, host: str = "127.0.0.1") -> bool:
    """True if *port* can be bound on *host* right now.

    A real bind/close, not a connect probe — a connect probe misses a
    listener that refuses connections (e.g. still starting up) and would
    wrongly report the port as free. The bind is released immediately after
    the check, so callers assigning several ports in the same run must track
    what they've already handed out themselves (see ``reserved`` below) —
    otherwise two services can race onto the same just-released port.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind((host, port))
        except OSError:
            return False
    return True


def _find_free_port(start: int, reserved: set) -> Optional[int]:
    for port in range(start, start + _PORT_SEARCH_RANGE):
        if port not in reserved and _port_is_free(port):
            return port
    return None


def _project_root() -> Path:
    """Find the project root by walking up from the working directory.

    Same resolution order as ``dftracer_agents_stack``'s ``_project_root``:
    prefer a directory that already owns ``workspaces/``, then fall back to
    ``.git``/``pyproject.toml``, then the cwd itself.
    """
    cwd = Path.cwd().resolve()
    for parent in (cwd, *cwd.parents):
        if (parent / "workspaces").is_dir():
            return parent
    for parent in (cwd, *cwd.parents):
        if any((parent / m).exists() for m in _ROOT_MARKERS):
            return parent
    return cwd


def _load_lines(env_file: Path, example_file: Path) -> List[str]:
    if env_file.exists():
        return env_file.read_text().splitlines()
    if example_file.exists():
        return example_file.read_text().splitlines()
    return _DEFAULT_TEMPLATE.splitlines()


def _get(lines: List[str], key: str) -> str:
    for line in lines:
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return ""


def _set(lines: List[str], key: str, value: str) -> List[str]:
    for i, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[i] = f"{key}={value}"
            return lines
    return [*lines, f"{key}={value}"]


def _write(env_file: Path, lines: List[str]) -> None:
    env_file.write_text("\n".join(lines) + "\n")
    # Never leave secrets group/world-readable, regardless of umask.
    env_file.chmod(stat.S_IRUSR | stat.S_IWUSR)


def _ensure_token(lines: List[str], key: str) -> List[str]:
    if _get(lines, key):
        print(f"[setup-env] {key} already set — leaving as-is")
        return lines
    lines = _set(lines, key, secrets.token_hex(32))
    print(f"[setup-env] {key} generated")
    return lines


def _ensure_port(lines: List[str], key: str, default: int, reserved: set) -> List[str]:
    existing = _get(lines, key)
    if existing:
        print(f"[setup-env] {key} already set to {existing} — leaving as-is")
        if existing.isdigit():
            reserved.add(int(existing))
        return lines

    if default not in reserved and _port_is_free(default):
        lines = _set(lines, key, str(default))
        print(f"[setup-env] {key} set to {default} (default, free)")
        reserved.add(default)
        return lines

    found = _find_free_port(default + 1, reserved)
    if found is None:
        print(f"[setup-env] {key} left blank — {default} is busy and no free port found in "
              f"{default + 1}-{default + _PORT_SEARCH_RANGE}; set {key} by hand")
        return lines

    lines = _set(lines, key, str(found))
    reason = "already assigned to another service in this run" if default in reserved \
        else f"default {default} is busy on this node"
    print(f"[setup-env] {key} set to {found} — {reason}")
    reserved.add(found)
    return lines


def _prompt_optional(lines: List[str], key: str, desc: str, interactive: bool) -> List[str]:
    if _get(lines, key):
        print(f"[setup-env] {key} already set — leaving as-is")
        return lines

    if not interactive:
        env_value = os.environ.get(key, "")
        if env_value:
            lines = _set(lines, key, env_value)
            print(f"[setup-env] {key} set from environment")
        else:
            print(f"[setup-env] {key} left blank (anonymous access)")
        return lines

    print(f"\n{desc}")
    try:
        value = getpass.getpass(f"  {key} (blank = skip, use anonymous access): ")
    except (EOFError, KeyboardInterrupt):
        value = ""
        print()
    if value:
        lines = _set(lines, key, value)
        print(f"[setup-env] {key} set")
    else:
        print(f"[setup-env] {key} left blank (anonymous access)")
    return lines


def configure_env(root: Optional[Path] = None, interactive: bool = True) -> Path:
    """Create/update .env at *root* (default: auto-detected project root).

    Returns the path to the .env file that was written.
    """
    root = root or _project_root()
    env_file = root / ".env"
    example_file = root / ".env.example"

    created = not env_file.exists()
    lines = _load_lines(env_file, example_file)

    if created:
        print(f"[setup-env] creating .env at {env_file}")
    else:
        print(f"[setup-env] using existing .env at {env_file}")

    print("\n-- dftracer auth tokens (gate the docker-compose MCP/OTLP endpoints) --")
    for key in _AUTO_TOKENS:
        lines = _ensure_token(lines, key)

    print("\n-- academic paper search (all optional, see .env.example for docs) --")
    for key, desc in _OPTIONAL_KEYS:
        lines = _prompt_optional(lines, key, desc, interactive)

    print("\n-- dftracer_agents_stack ports (probed on 127.0.0.1) --")
    reserved_ports: set = set()
    for key, default in _STACK_PORTS:
        lines = _ensure_port(lines, key, default, reserved_ports)

    _write(env_file, lines)

    print(f"\n[setup-env] done. {env_file} is only ever read by:")
    print("  * docker-compose (docker compose up) — reads .env automatically")
    print("  * dftracer_agents_stack (local mode) — sources .env before starting the mcp-server daemon")
    print("Re-run any time; it only fills in blanks and never overwrites a value you've already set.")
    return env_file


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--non-interactive", action="store_true",
        help="Skip prompts; only auto-generate tokens and seed API keys from "
             "the environment when already exported.",
    )
    args = parser.parse_args()
    try:
        configure_env(interactive=not args.non_interactive)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
