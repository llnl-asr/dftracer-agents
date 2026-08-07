"""Tests for memory retrieval, context packing, and per-harness hook rendering."""
from __future__ import annotations

import json

import pytest


def _mem(name, description, body="", type_="feedback"):
    from dftracer_agents.memory_graph import parse_links
    return {
        "name": name, "description": description, "type": type_,
        "body": body, "links": parse_links(body),
        "file": f"{name}.md", "path": f"/tmp/{name}.md", "chars": len(body),
    }


class TestMemoryGraph:
    def test_parse_links_handles_alias_and_anchor(self):
        from dftracer_agents.memory_graph import parse_links
        links = parse_links("see [[alpha]] and [[beta|Beta!]] and [[gamma#part]] and [[alpha]]")
        assert links == ["alpha", "beta", "gamma"]  # de-duped, alias/anchor stripped

    def test_tokenize_splits_slugs_and_drops_stopwords(self):
        from dftracer_agents.memory_graph import tokenize
        terms = tokenize("What about the always-function-mode rule?")
        assert "function" in terms and "mode" in terms
        assert "the" not in terms and "about" not in terms

    def test_body_match_scores_when_title_does_not(self):
        """The whole point of reading bodies: mode='docs' scores label+filename
        only, so a memory whose content is relevant but whose title isn't would
        be invisible."""
        from dftracer_agents.memory_graph import recall
        pool = [
            _mem("unrelated-title", "nothing to do with it",
                 "The ROMIO cb_nodes hint is what fixed the collective write."),
            _mem("other", "also unrelated", "totally different subject matter"),
        ]
        hits = recall("cb_nodes romio", memories=pool)
        assert [h["name"] for h in hits] == ["unrelated-title"]

    def test_coverage_beats_raw_frequency(self):
        """A memory matching more distinct query terms outranks one that just
        repeats a single term."""
        from dftracer_agents.memory_graph import recall
        pool = [
            _mem("spammy", "x", "flux " * 40),
            _mem("covering", "y", "flux submit detached daemon"),
        ]
        hits = recall("flux submit daemon", memories=pool)
        assert hits[0]["name"] == "covering"

    def test_wikilink_expansion_pulls_neighbour(self):
        from dftracer_agents.memory_graph import recall
        pool = [
            _mem("hit", "about zzzquux", "zzzquux matters. See [[neighbour]]."),
            _mem("neighbour", "the linked one", "no query terms here at all"),
        ]
        hits = recall("zzzquux", memories=pool)
        names = {h["name"]: h["via"] for h in hits}
        assert names["hit"] == "match"
        assert names["neighbour"] == "hit"  # pulled in via the link

    def test_dangling_link_is_tolerated(self):
        """Memory conventions explicitly allow linking a memory that does not
        exist yet, so expansion must not crash or invent an entry."""
        from dftracer_agents.memory_graph import recall
        pool = [_mem("hit", "about zzzquux", "zzzquux. See [[does-not-exist]].")]
        hits = recall("zzzquux", memories=pool)
        assert [h["name"] for h in hits] == ["hit"]

    def test_type_filter(self):
        from dftracer_agents.memory_graph import recall
        pool = [_mem("a", "zzzquux", type_="feedback"),
                _mem("b", "zzzquux", type_="project")]
        assert [h["name"] for h in recall("zzzquux", types=["project"], memories=pool)] == ["b"]

    def test_real_store_loads_and_ranks(self):
        from dftracer_agents.memory_graph import load_memories, recall
        mems = load_memories()
        assert len(mems) > 20, "the git-tracked memory store should be populated"
        assert any(m["links"] for m in mems), "wikilinks should be parsed from the real store"
        assert recall("flux", memories=mems), "a common term should match something"


class TestContextPack:
    @pytest.fixture(autouse=True)
    def _isolate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DFTRACER_CONTEXT_STATE_DIR", str(tmp_path / "state"))
        from dftracer_agents.context_pack import reset_sessions
        reset_sessions()
        yield
        reset_sessions()

    def test_session_kind_carries_validation_and_learning(self):
        from dftracer_agents.context_pack import build_pack
        pack = build_pack("zzzquux", kind="session", memories=[])
        assert pack and "VALIDATION:" in pack and "LEARNING:" in pack

    def test_rules_are_sent_only_once(self):
        from dftracer_agents.context_pack import build_pack
        build_pack("first", kind="session", memories=[])
        second = build_pack("second", kind="session", memories=[])
        assert second is None or "VALIDATION:" not in second

    def test_repeated_query_injects_nothing(self):
        """A retry must not strip-mine a *different* slice of the corpus."""
        from dftracer_agents.context_pack import build_pack
        pool = [_mem(f"m{i}", "zzzquux relevant", "zzzquux") for i in range(10)]
        assert build_pack("zzzquux", kind="tool", memories=pool) is not None
        assert build_pack("zzzquux", kind="tool", memories=pool) is None

    def test_memory_not_repeated_across_different_queries(self):
        from dftracer_agents.context_pack import build_pack
        pool = [_mem("only-one", "zzzquux thing", "zzzquux qqqzed")]
        first = build_pack("zzzquux", kind="tool", memories=pool)
        second = build_pack("qqqzed", kind="tool", memories=pool)
        assert first and "only-one" in first
        assert second is None or "only-one" not in second

    def test_sessions_are_independent(self):
        from dftracer_agents.context_pack import build_pack
        pool = [_mem("m", "zzzquux", "zzzquux")]
        assert build_pack("zzzquux", session_id="a", kind="tool", memories=pool)
        assert build_pack("zzzquux", session_id="b", kind="tool", memories=pool)

    def test_persisted_state_survives_a_new_process_view(self, tmp_path):
        """Prompt hooks run as a fresh process each turn; without disk-backed
        state every prompt would re-inject the standing rules."""
        from dftracer_agents.context_pack import build_pack, reset_sessions
        pool = [_mem("m", "zzzquux", "zzzquux")]
        first = build_pack("zzzquux", session_id="S", kind="session",
                           memories=pool, persist=True)
        assert first is not None
        reset_sessions()  # simulates process restart: in-memory cache is gone
        again = build_pack("zzzquux", session_id="S", kind="session",
                           memories=pool, persist=True)
        assert again is None, "persisted dedup should suppress the repeat"

    def test_tool_query_folds_in_arguments(self):
        from dftracer_agents.context_pack import tool_query
        q = tool_query("session_run_with_dftracer", {"cmd": "flux run -N4", "n": 4})
        assert "session run with dftracer" in q and "flux run" in q

    def test_steady_state_cost_is_bounded(self):
        """The design claim: hundreds of calls must not cost hundreds of digests."""
        from dftracer_agents.context_pack import build_pack, tool_query
        from dftracer_agents.memory_graph import load_memories
        pool = load_memories()
        total = 0
        for _ in range(50):
            for tool in ("session_create", "session_detect", "analyze", "diagnose"):
                pack = build_pack(tool_query(tool, {}), kind="tool", memories=pool)
                total += len(pack or "")
        assert total < 8000, f"200 tool calls injected {total} chars — dedup regressed"


class TestHooks:
    def test_hook_entry_ignores_junk(self):
        from dftracer_agents.hook_entry import run
        assert run("") == ""
        assert run("not json") == ""
        assert run(json.dumps({"no_prompt": "here"})) == ""

    def test_hook_entry_returns_block_for_a_prompt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DFTRACER_CONTEXT_STATE_DIR", str(tmp_path))
        from dftracer_agents.context_pack import reset_sessions
        reset_sessions()
        out = run_hook({"prompt": "flux run hangs", "session_id": "t1"})
        assert "<dftracer-context>" in out
        reset_sessions()

    def test_sync_hooks_writes_all_supported_harnesses(self, tmp_path):
        from dftracer_agents.hooks import (HOOK_SCRIPT_NAME, OPENCODE_PLUGIN_NAME,
                                           sync_hooks)
        result = sync_hooks(base=tmp_path)
        assert result["copilot"] == "not_applicable"  # no hook mechanism exists
        assert (tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME).is_file()
        assert (tmp_path / ".codex" / "hooks" / HOOK_SCRIPT_NAME).is_file()
        assert (tmp_path / ".codex" / "hooks.json").is_file()
        assert (tmp_path / ".opencode" / "plugin" / OPENCODE_PLUGIN_NAME).is_file()

    def test_sync_hooks_is_idempotent(self, tmp_path):
        from dftracer_agents.hooks import sync_hooks
        sync_hooks(base=tmp_path)
        again = sync_hooks(base=tmp_path)
        assert again["changed"] == []

    def test_claude_hook_merge_preserves_other_hooks(self, tmp_path):
        """settings.json already carries a hand-written PreToolUse rm/drm guard;
        rendering ours must not delete it."""
        from dftracer_agents.hooks import sync_hooks
        settings = tmp_path / ".claude" / "settings.json"
        settings.parent.mkdir(parents=True)
        settings.write_text(json.dumps({
            "permissions": {"allow": ["Bash(ls)"]},
            "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
                {"type": "command", "command": "bash .claude/hooks/guard_rm_drm.sh"}]}]},
        }))
        sync_hooks(base=tmp_path)
        data = json.loads(settings.read_text())
        assert data["permissions"] == {"allow": ["Bash(ls)"]}
        assert data["hooks"]["PreToolUse"], "the rm/drm guard must survive"
        assert data["hooks"]["UserPromptSubmit"]

    def test_claude_hook_does_not_duplicate_on_resync(self, tmp_path):
        from dftracer_agents.hooks import sync_hooks
        sync_hooks(base=tmp_path)
        sync_hooks(base=tmp_path)
        data = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        assert len(data["hooks"]["UserPromptSubmit"]) == 1

    def test_settings_never_reference_a_missing_script(self, tmp_path):
        """The failure this guards: a UserPromptSubmit entry was written while
        the script existed only in the packaged workspace, so every prompt ran a
        nonexistent file and failed."""
        from dftracer_agents.hooks import HOOK_SCRIPT_NAME, sync_claude_hooks
        status = sync_claude_hooks(base=tmp_path)
        script = tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME
        settings = tmp_path / ".claude" / "settings.json"
        assert script.is_file(), "script must be written before the settings entry"
        if settings.exists():
            data = json.loads(settings.read_text())
            for entry in data.get("hooks", {}).get("UserPromptSubmit", []):
                for hook in entry.get("hooks", []):
                    referenced = hook["command"].split()[-1]
                    assert (tmp_path / referenced).is_file(), \
                        f"settings references missing {referenced}"
        assert status != "skipped_missing_script"

    def test_install_hook_scripts_into_a_real_directory(self, tmp_path):
        """bootstrap symlinks .claude/hooks, but refuses when a REAL directory is
        already there (this repo has a hand-written guard). The scripts must
        still arrive."""
        from dftracer_agents.hooks import HOOK_SCRIPT_NAME, install_hook_scripts
        real = tmp_path / ".claude" / "hooks"
        real.mkdir(parents=True)
        (real / "guard_rm_drm.sh").write_text("#!/bin/bash\nexit 0\n")
        install_hook_scripts(tmp_path)
        assert (real / HOOK_SCRIPT_NAME).is_file()
        assert (real / "guard_rm_drm.sh").is_file(), "pre-existing hook preserved"

    def test_ensure_hooks_setup_installs_into_target_root(self, tmp_path):
        from dftracer_agents.hooks import HOOK_SCRIPT_NAME, ensure_hooks_setup
        ensure_hooks_setup(target_root=tmp_path)
        assert (tmp_path / ".claude" / "hooks" / HOOK_SCRIPT_NAME).is_file()
        assert (tmp_path / ".codex" / "hooks" / HOOK_SCRIPT_NAME).is_file()

    def test_bootstrap_delivers_every_referenced_hook_artifact(self, tmp_path):
        """End-to-end on the real init path: after bootstrap, every command a
        harness config references must resolve at the target root.

        Reproduces the original break — a REAL .claude/hooks directory (this
        repo has one for guard_rm_drm.sh) makes bootstrap refuse the symlink, so
        the script has to be copied in instead.
        """
        from dftracer_agents.bootstrap import ensure_workspace_setup
        from dftracer_agents.hooks import ensure_hooks_setup

        real = tmp_path / ".claude" / "hooks"
        real.mkdir(parents=True)
        (real / "guard_rm_drm.sh").write_text("#!/bin/bash\nexit 0\n")

        ensure_hooks_setup(target_root=tmp_path)
        ensure_workspace_setup(target_root=tmp_path, force=True)

        settings = json.loads((tmp_path / ".claude" / "settings.json").read_text())
        for event, entries in settings.get("hooks", {}).items():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    ref = hook["command"].split()[-1]
                    assert (tmp_path / ref).is_file(), \
                        f"claude {event} references unresolvable {ref}"

        codex = tmp_path / ".codex" / "hooks.json"
        assert codex.is_file(), "codex hook wiring never reached the target root"
        for entries in json.loads(codex.read_text())["hooks"].values():
            for entry in entries:
                for hook in entry.get("hooks", []):
                    ref = hook["command"].split()[-1]
                    assert (tmp_path / ref).is_file(), \
                        f"codex references unresolvable {ref}"

    def test_generated_script_is_syntactically_valid(self):
        """The script is a string constant; a syntax error there breaks every
        prompt and no import would catch it."""
        from dftracer_agents.hooks import hook_script_source
        compile(hook_script_source(), "inject_context.py", "exec")

    def test_codex_advisory_is_reported(self):
        """Codex hooks need a user-level opt-in a repo must not write itself."""
        from dftracer_agents.hooks import advisories
        assert "codex_hooks" in advisories()["codex"]


def run_hook(payload):
    from dftracer_agents.hook_entry import run
    return run(json.dumps(payload))


class TestPackagedConfigHygiene:
    def test_codex_config_has_no_absolute_user_paths(self):
        """Rule 9: git-tracked files must not carry usernames or user paths.
        Codex writes machine-local trust/hook state into this file."""
        import re
        from pathlib import Path
        cfg = (Path(__file__).resolve().parent.parent / "src" / "dftracer_agents"
               / ".agents" / "workspace" / ".codex" / "config.toml")
        text = cfg.read_text()
        assert not re.search(r"/usr/WS2/|/g/g92/|/p/lustre\d", text), \
            "absolute user path leaked into the packaged codex config"
        assert "[hooks.state]" not in text, "machine-local hook hashes leaked"
