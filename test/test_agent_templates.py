"""Tests for the agent template rendering and model resolution pipeline.

Verifies:
- YAML agent templates load correctly (dynamic count, no legacy .md files)
- common-sections.yaml shared sections resolve via `- include:`
- render_claude / render_opencode / render_copilot produce correct frontmatter
- harness_models resolves providers and models per harness
- install_agents writes rendered files to the correct discovery paths
- bootstrap creates RELATIVE symlinks (not absolute)
- E2E: render -> install -> verify opencode agents are correct on disk
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Ensure the package is importable when running from the repo root.
REPO = Path(__file__).resolve().parent.parent
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _expected_template_count():
    """Return the expected number of agent templates (excluding common-sections.yaml)."""
    from dftracer_agents.agent_templates import templates_dir
    return len(list(templates_dir().glob("*.yaml"))) - 1  # minus common-sections.yaml


def _extract_frontmatter(rendered: str) -> str:
    """Extract the YAML frontmatter between the first two --- delimiters."""
    if not rendered.startswith("---\n"):
        return ""
    parts = rendered.split("---\n", 2)
    return parts[1] if len(parts) >= 2 else ""


# ---------------------------------------------------------------------------
# agent_templates — loading
# ---------------------------------------------------------------------------

class TestTemplateLoading:
    def test_templates_dir_exists(self):
        from dftracer_agents.agent_templates import templates_dir
        d = templates_dir()
        assert d.is_dir(), f"templates dir not found: {d}"

    def test_templates_dir_contains_yaml_files(self):
        from dftracer_agents.agent_templates import templates_dir
        d = templates_dir()
        yamls = sorted(p.name for p in d.glob("*.yaml"))
        assert len(yamls) >= 25  # 24 agents + common-sections.yaml

    def test_load_common_sections(self):
        from dftracer_agents.agent_templates import load_common_sections
        sections = load_common_sections()
        assert isinstance(sections, dict)
        assert len(sections) > 0
        # Spot-check a known section
        assert "self-learning-confirmation-gate" in sections
        entry = sections["self-learning-confirmation-gate"]
        assert "title" in entry
        assert "body" in entry
        assert isinstance(entry["body"], str)
        assert len(entry["body"]) > 0

    def test_load_template_single(self):
        from dftracer_agents.agent_templates import load_template, templates_dir
        path = templates_dir() / "dftracer-analyzer.yaml"
        tmpl = load_template(path)
        assert tmpl["name"] == "dftracer-analyzer"
        assert "description" in tmpl
        assert tmpl["model_level"] == "level_3"
        assert isinstance(tmpl["sections"], list)
        assert len(tmpl["sections"]) > 0
        # Each section should have title and body after include resolution
        for section in tmpl["sections"]:
            assert "title" in section
            assert "body" in section

    def test_load_template_resolves_includes(self):
        """Templates with `- include:` entries should have them expanded to {title, body}."""
        from dftracer_agents.agent_templates import load_template, templates_dir
        path = templates_dir() / "dftracer-analyzer.yaml"
        tmpl = load_template(path)
        # dftracer-analyzer.yaml includes self-learning-feed-lessons-back-into-skills
        included_titles = [s["title"] for s in tmpl["sections"]]
        assert any("self-learning" in t.lower() for t in included_titles), \
            f"Expected an included self-learning section, got titles: {included_titles}"

    def test_load_all_templates_count(self):
        """Should load exactly the expected number of agent templates."""
        from dftracer_agents.agent_templates import load_all_templates
        templates = load_all_templates()
        expected = _expected_template_count()
        assert len(templates) == expected, f"Expected {expected} templates, got {len(templates)}"
        names = sorted(t["name"] for t in templates)
        assert "dftracer-analyzer" in names
        assert "dftracer-project-router" in names
        assert "dftracer-annotator" in names

    def test_load_all_templates_no_md_files(self):
        """Legacy .md files must NOT be loaded as templates."""
        from dftracer_agents.agent_templates import load_all_templates
        templates = load_all_templates()
        # Legacy .md files were dftracer-annotator.md and dftracer-build-smoke.md
        # These should not appear as separate entries beyond their .yaml counterparts
        names = [t["name"] for t in templates]
        assert names.count("dftracer-annotator") == 1
        assert names.count("dftracer-build-smoke") == 1

    def test_all_templates_have_required_keys(self):
        from dftracer_agents.agent_templates import load_all_templates
        templates = load_all_templates()
        for tmpl in templates:
            assert "name" in tmpl, f"Missing 'name' in template"
            assert "description" in tmpl, f"Missing 'description' in {tmpl.get('name')}"
            assert "model_level" in tmpl, f"Missing 'model_level' in {tmpl['name']}"
            assert tmpl["model_level"] in ("level_1", "level_2", "level_3", "level_4"), \
                f"Invalid model_level {tmpl['model_level']} in {tmpl['name']}"
            assert "sections" in tmpl
            assert len(tmpl["sections"]) > 0

    def test_load_template_include_body_matches_common_sections(self):
        """An included section's body should match common-sections.yaml verbatim."""
        from dftracer_agents.agent_templates import load_template, load_common_sections, templates_dir
        common = load_common_sections()
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        
        section_name = "self-learning-feed-lessons-back-into-skills"
        expected_body = common[section_name]["body"]
        
        # Find the included section by matching the title from common-sections
        included = [s for s in tmpl["sections"] if s["title"] == common[section_name]["title"]]
        assert len(included) >= 1, f"Expected included section '{section_name}' not found"
        
        for section in included:
            assert section["body"] == expected_body, \
                "Included section body should match common-sections.yaml verbatim"


# ---------------------------------------------------------------------------
# agent_templates — rendering
# ---------------------------------------------------------------------------

class TestRenderClaude:
    def test_render_claude_frontmatter(self):
        from dftracer_agents.agent_templates import load_template, render_claude, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        out = render_claude(tmpl, models)
        assert out.startswith("---\n")
        assert "generated-by: dftracer-agents (claude)" in out
        # model should be a class alias
        assert "model: sonnet" in out  # dftracer-analyzer is level_3 -> sonnet

    def test_render_claude_tools_comma_string(self):
        from dftracer_agents.agent_templates import load_template, render_claude, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        out = render_claude(tmpl, models)
        # Tools should be a comma-separated string, not a list
        assert "tools:" in out
        assert "Read" in out
        assert "Bash" in out
        # MCP tools keep the mcp__dftracer__ format for claude
        assert "mcp__dftracer__analyze" in out

    def test_render_claude_has_description(self):
        from dftracer_agents.agent_templates import load_template, render_claude, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        out = render_claude(tmpl, models)
        assert "dftracer-analyzer" in out
        assert "Pipeline stage 5" in out

    def test_render_claude_effort_isolation_skills_frontmatter(self):
        """Claude frontmatter should include effort, isolation, skills when present."""
        from dftracer_agents.agent_templates import load_template, render_claude, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        out = render_claude(tmpl, models)
        fm = _extract_frontmatter(out)
        assert "effort:" in fm, "Missing effort: in claude frontmatter"
        assert "low" in fm, "Missing effort value 'low'"
        assert "isolation:" in fm, "Missing isolation: in claude frontmatter"
        assert "worktree" in fm, "Missing isolation value 'worktree'"
        assert "skills:" in fm, "Missing skills: in claude frontmatter"
        assert "dftracer-context-economy" in fm, "Missing skill name in frontmatter"

    def test_render_claude_no_skill_preamble_in_body(self):
        """Claude uses skills: frontmatter key, NOT the body preamble."""
        from dftracer_agents.agent_templates import load_template, render_claude, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        out = render_claude(tmpl, models)
        assert "Locate your skills via the graph first" not in out


class TestRenderOpencode:
    def test_render_opencode_frontmatter(self):
        from dftracer_agents.agent_templates import load_template, render_opencode, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {
            "level_1": "ollama/qwen3.5:9b",
            "level_2": "ollama/qwen3.5:32b",
            "level_3": "ollama/qwen3-coder:480b-cloud",
            "level_4": "ollama/deepseek-v3.2:cloud",
        }
        out = render_opencode(tmpl, models)
        assert out.startswith("---\n")
        assert "generated-by: dftracer-agents (opencode)" in out
        assert "mode: subagent" in out
        # model should be provider/model-id format
        assert "model: ollama/qwen3-coder:480b-cloud" in out  # level_3

    def test_render_opencode_tools_map(self):
        from dftracer_agents.agent_templates import load_template, render_opencode, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {
            "level_1": "ollama/qwen3.5:9b",
            "level_2": "ollama/qwen3.5:32b",
            "level_3": "ollama/qwen3-coder:480b-cloud",
            "level_4": "ollama/deepseek-v3.2:cloud",
        }
        out = render_opencode(tmpl, models)
        # Tools should be a map with "*": false
        assert "'*': false" in out or '"*": false' in out
        # MCP tools reshaped: mcp__dftracer__analyze -> dftracer_analyze
        assert "dftracer_analyze" in out
        # Built-ins lowercased - check in frontmatter specifically
        fm = _extract_frontmatter(out)
        assert "read" in fm  # Read -> read in opencode frontmatter
        assert "bash" in fm  # Bash -> bash in opencode frontmatter

    def test_render_opencode_no_mcp_double_underscore(self):
        """OpenCode frontmatter tool names should NOT contain mcp__ prefix.

        The body prose may legitimately reference ``mcp__dftracer__analyze`` in
        instructional text — only the frontmatter ``tools:`` map is reshaped.
        """
        from dftracer_agents.agent_templates import load_template, render_opencode, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {
            "level_1": "ollama/qwen3.5:9b",
            "level_2": "ollama/qwen3.5:32b",
            "level_3": "ollama/qwen3-coder:480b-cloud",
            "level_4": "ollama/deepseek-v3.2:cloud",
        }
        out = render_opencode(tmpl, models)
        # Extract frontmatter (between first two --- lines)
        fm = out.split("---\n", 2)[1] if out.startswith("---\n") else out
        assert "mcp__dftracer__" not in fm, \
            "OpenCode frontmatter should reshape mcp__dftracer__X to dftracer_X"

    def test_render_opencode_skill_preamble_present(self):
        """OpenCode output should contain skill preamble when template has skills."""
        from dftracer_agents.agent_templates import load_template, render_opencode, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "ollama/qwen3.5:9b", "level_2": "x", "level_3": "ollama/qwen3-coder:480b-cloud", "level_4": "x"}
        out = render_opencode(tmpl, models)
        assert "Locate your skills via the graph first" in out
        assert "dftracer-context-economy" in out

    def test_render_opencode_no_skills_no_preamble(self):
        """Template without skills should not produce a preamble."""
        from dftracer_agents.agent_templates import render_opencode
        minimal_tmpl = {
            "name": "test-agent", "description": "A test agent", "model_level": "level_1",
            "tools": ["Read", "Bash"], "sections": [{"title": "Task", "body": "Do the thing."}],
        }
        models = {"level_1": "ollama/qwen3.5:9b", "level_2": "x", "level_3": "x", "level_4": "x"}
        out = render_opencode(minimal_tmpl, models)
        assert "Locate your skills via the graph first" not in out

    def test_render_opencode_builtin_tool_mappings(self):
        """Verify specific built-in tool name mappings in frontmatter."""
        from dftracer_agents.agent_templates import load_template, render_opencode, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "ollama/qwen3.5:9b", "level_2": "x", "level_3": "ollama/qwen3-coder:480b-cloud", "level_4": "x"}
        out = render_opencode(tmpl, models)
        fm = _extract_frontmatter(out)
        # OpenCode mappings: Read->read, Bash->bash, Edit->edit
        assert "read" in fm, "Read should map to 'read' in opencode frontmatter"
        assert "bash" in fm, "Bash should map to 'bash' in opencode frontmatter"
        assert "edit" in fm, "Edit should map to 'edit' in opencode frontmatter"


class TestRenderCopilot:
    def test_render_copilot_frontmatter(self):
        from dftracer_agents.agent_templates import load_template, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {
            "level_1": "gpt-5-codex-mini",
            "level_2": "gpt-5-codex",
            "level_3": "gpt-5-codex",
            "level_4": "gpt-5-codex-pro",
        }
        out = render_copilot(tmpl, models)
        assert out.startswith("---\n")
        assert "generated-by: dftracer-agents (copilot)" in out
        # model should be bare id
        assert "model: gpt-5-codex" in out  # level_3

    def test_render_copilot_tools_list(self):
        from dftracer_agents.agent_templates import load_template, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {
            "level_1": "gpt-5-codex-mini",
            "level_2": "gpt-5-codex",
            "level_3": "gpt-5-codex",
            "level_4": "gpt-5-codex-pro",
        }
        out = render_copilot(tmpl, models)
        # MCP tools reshaped: mcp__dftracer__analyze -> dftracer/analyze
        assert "dftracer/analyze" in out
        # Frontmatter should NOT have mcp__ prefix (body prose may reference it)
        fm = out.split("---\n", 2)[1] if out.startswith("---\n") else out
        assert "mcp__dftracer__" not in fm

    def test_render_copilot_skill_preamble_present(self):
        """Copilot output should contain skill preamble when template has skills."""
        from dftracer_agents.agent_templates import load_template, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "x", "level_2": "x", "level_3": "gpt-5-codex", "level_4": "x"}
        out = render_copilot(tmpl, models)
        assert "Locate your skills via the graph first" in out

    def test_render_copilot_no_skills_no_preamble(self):
        """Template without skills should not produce a preamble."""
        from dftracer_agents.agent_templates import render_copilot
        minimal_tmpl = {
            "name": "test-agent", "description": "A test agent", "model_level": "level_1",
            "tools": ["Read", "Bash"], "sections": [{"title": "Task", "body": "Do the thing."}],
        }
        models = {"level_1": "x", "level_2": "x", "level_3": "x", "level_4": "x"}
        out = render_copilot(minimal_tmpl, models)
        assert "Locate your skills via the graph first" not in out

    def test_render_copilot_builtin_tool_mappings(self):
        """Verify specific copilot built-in tool name mappings in frontmatter."""
        from dftracer_agents.agent_templates import load_template, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "x", "level_2": "x", "level_3": "gpt-5-codex", "level_4": "x"}
        out = render_copilot(tmpl, models)
        fm = _extract_frontmatter(out)
        # Copilot mappings: Bash->shell (NOT bash), Edit->edit, Read->read
        assert "shell" in fm, "Bash should map to 'shell' in copilot frontmatter"
        assert "edit" in fm, "Edit should map to 'edit' in copilot frontmatter"
        assert "read" in fm, "Read should map to 'read' in copilot frontmatter"

    def test_render_copilot_model_omitted_when_unresolved(self):
        """Copilot frontmatter should NOT have model: key when model is UNRESOLVED."""
        from dftracer_agents.agent_templates import load_template, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models = {"level_1": "x", "level_2": "x", "level_3": "UNRESOLVED", "level_4": "x"}
        out = render_copilot(tmpl, models)
        fm = _extract_frontmatter(out)
        assert "model:" not in fm, "model: should be omitted when UNRESOLVED"


class TestRenderAll:
    def test_render_all_returns_three_harnesses(self):
        from dftracer_agents.agent_templates import render_all
        out = render_all()
        assert "claude" in out
        assert "opencode" in out
        assert "copilot" in out

    def test_render_all_has_all_templates(self):
        from dftracer_agents.agent_templates import render_all
        out = render_all()
        expected = _expected_template_count()
        for harness in ("claude", "opencode", "copilot"):
            assert len(out[harness]) == expected, \
                f"Expected {expected} rendered files for {harness}, got {len(out[harness])}"

    def test_render_all_file_paths(self):
        from dftracer_agents.agent_templates import render_all, HARNESS_OUTPUT
        out = render_all()
        # Check path patterns
        for harness, (subdir, pattern) in HARNESS_OUTPUT.items():
            files = out[harness]
            for rel_path in files:
                assert rel_path.startswith(subdir + "/"), \
                    f"{harness} path {rel_path} doesn't start with {subdir}/"

    def test_render_all_content_has_markers(self):
        from dftracer_agents.agent_templates import render_all
        out = render_all()
        for harness, files in out.items():
            for rel_path, content in files.items():
                assert f"generated-by: dftracer-agents ({harness})" in content, \
                    f"{rel_path} missing generation marker for {harness}"


# ---------------------------------------------------------------------------
# Cross-harness content differences
# ---------------------------------------------------------------------------

class TestRenderCrossHarness:
    """Same template → different frontmatter formats per harness."""
    
    def test_same_template_different_frontmatter_format(self):
        from dftracer_agents.agent_templates import load_template, render_claude, render_opencode, render_copilot, templates_dir
        tmpl = load_template(templates_dir() / "dftracer-analyzer.yaml")
        models_claude = {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"}
        models_opencode = {"level_1": "ollama/qwen3.5:9b", "level_2": "ollama/qwen3.5:32b", "level_3": "ollama/qwen3-coder:480b-cloud", "level_4": "ollama/deepseek-v3.2:cloud"}
        models_copilot = {"level_1": "gpt-5-codex-mini", "level_2": "gpt-5-codex", "level_3": "gpt-5-codex", "level_4": "gpt-5-codex-pro"}
        
        claude_out = render_claude(tmpl, models_claude)
        opencode_out = render_opencode(tmpl, models_opencode)
        copilot_out = render_copilot(tmpl, models_copilot)
        
        claude_fm = _extract_frontmatter(claude_out)
        opencode_fm = _extract_frontmatter(opencode_out)
        copilot_fm = _extract_frontmatter(copilot_out)
        
        # Claude: tools as comma-separated string
        assert "tools:" in claude_fm
        # OpenCode: tools as map with "*": false
        assert "'*': false" in opencode_fm or '"*": false' in opencode_fm
        # Copilot: tools as YAML list with dftracer/ format
        assert "dftracer/analyze" in copilot_fm
        
        # Verify they are meaningfully different
        assert claude_fm != opencode_fm
        assert opencode_fm != copilot_fm
        assert claude_fm != copilot_fm


# ---------------------------------------------------------------------------
# harness_models — model resolution
# ---------------------------------------------------------------------------

class TestHarnessModels:
    def test_fallback_level_map(self):
        from dftracer_agents.harness_models import FALLBACK_LEVEL_MAP, LEVELS
        for level in LEVELS:
            assert level in FALLBACK_LEVEL_MAP
            assert "class" in FALLBACK_LEVEL_MAP[level]
            assert "providers" in FALLBACK_LEVEL_MAP[level]
            providers = FALLBACK_LEVEL_MAP[level]["providers"]
            assert "ollama" in providers
            assert "claude" in providers
            assert "copilot" in providers

    def test_default_provider_by_harness(self):
        from dftracer_agents.harness_models import DEFAULT_PROVIDER_BY_HARNESS
        assert DEFAULT_PROVIDER_BY_HARNESS["claude"] == "claude"
        assert DEFAULT_PROVIDER_BY_HARNESS["opencode"] == "ollama"
        assert DEFAULT_PROVIDER_BY_HARNESS["copilot"] == "copilot"

    def test_load_active_config(self, tmp_path):
        from dftracer_agents.harness_models import load_active_config, HARNESSES
        # Set up workspace structure in tmp_path to avoid falling back to bundled workspace
        workspace = tmp_path / "src" / "dftracer_agents" / ".agents" / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        config = load_active_config(target_root=tmp_path)
        assert config.get("version") == 1
        harnesses = config.get("harnesses", {})
        for harness in HARNESSES:
            assert harness in harnesses
            entry = harnesses[harness]
            assert "provider" in entry
            assert "class_by_level" in entry
            assert "model_by_level" in entry

    def test_active_config_copilot_provider_is_copilot(self, tmp_path):
        """After our fix, the committed template should have copilot -> copilot."""
        from dftracer_agents.harness_models import load_active_config
        # Set up workspace structure in tmp_path to avoid falling back to bundled workspace
        workspace = tmp_path / "src" / "dftracer_agents" / ".agents" / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        config = load_active_config(target_root=tmp_path)
        assert config["harnesses"]["copilot"]["provider"] == "copilot", \
            f"Expected copilot provider 'copilot', got '{config['harnesses']['copilot']['provider']}'"

    def test_resolve_models(self, tmp_path):
        from dftracer_agents.harness_models import load_active_config, resolve_models, LEVELS
        # Set up workspace structure in tmp_path to avoid falling back to bundled workspace
        workspace = tmp_path / "src" / "dftracer_agents" / ".agents" / "workspace"
        workspace.mkdir(parents=True, exist_ok=True)
        config = load_active_config(target_root=tmp_path)
        resolved = resolve_models(config, target_root=tmp_path)
        for harness in ("claude", "opencode", "copilot"):
            assert harness in resolved
            entry = resolved[harness]
            assert "provider" in entry
            for level in LEVELS:
                assert level in entry, f"Missing {level} for {harness}"
                assert entry[level] != "UNRESOLVED", \
                    f"{harness} {level} resolved to UNRESOLVED"

    def test_resolve_harness_models_dialects(self):
        """Each harness should get models in its own dialect format."""
        from dftracer_agents.agent_templates import resolve_harness_models
        models = resolve_harness_models()
        # Claude → class alias (haiku/sonnet/opus)
        for level in ("level_1", "level_2", "level_3", "level_4"):
            assert models["claude"][level] in ("haiku", "sonnet", "opus"), \
                f"claude {level} should be a class alias, got {models['claude'][level]}"
        # OpenCode → provider/model-id
        for level in ("level_1", "level_2", "level_3", "level_4"):
            assert "/" in models["opencode"][level], \
                f"opencode {level} should be 'provider/model-id', got {models['opencode'][level]}"
        # Copilot → bare model id
        for level in ("level_1", "level_2", "level_3", "level_4"):
            assert models["copilot"][level] != "UNRESOLVED", \
                f"copilot {level} should be resolved, got UNRESOLVED"

    def test_update_harness_config_provider_override(self, tmp_path):
        from dftracer_agents.harness_models import update_harness_config, load_active_config
        update_harness_config(harnesses=["opencode"], provider="claude", target_root=tmp_path)
        config = load_active_config(target_root=tmp_path)
        assert config["harnesses"]["opencode"]["provider"] == "claude"

    def test_update_harness_config_class_overrides(self, tmp_path):
        from dftracer_agents.harness_models import update_harness_config, load_active_config
        update_harness_config(harnesses=["claude"], class_overrides={"level_3": "opus"}, target_root=tmp_path)
        config = load_active_config(target_root=tmp_path)
        assert config["harnesses"]["claude"]["class_by_level"]["level_3"] == "opus"

    def test_update_harness_config_model_overrides(self, tmp_path):
        from dftracer_agents.harness_models import update_harness_config, load_active_config
        update_harness_config(harnesses=["copilot"], model_overrides={"level_2": "gpt-5-codex-pro"}, target_root=tmp_path)
        config = load_active_config(target_root=tmp_path)
        assert config["harnesses"]["copilot"]["model_by_level"]["level_2"] == "gpt-5-codex-pro"

    def test_update_harness_config_no_overrides_preserves(self, tmp_path):
        from dftracer_agents.harness_models import update_harness_config, load_active_config
        update_harness_config(harnesses=["claude"], class_overrides={"level_3": "opus"}, target_root=tmp_path)
        update_harness_config(harnesses=["claude"], target_root=tmp_path)
        config = load_active_config(target_root=tmp_path)
        assert config["harnesses"]["claude"]["class_by_level"]["level_3"] == "opus"

    def test_resolve_models_with_model_overrides(self, tmp_path):
        """model_by_level overrides should take priority over class-based defaults."""
        from dftracer_agents.harness_models import resolve_models, save_active_config
        config = {
            "version": 1,
            "harnesses": {
                "opencode": {
                    "provider": "ollama",
                    "class_by_level": {"level_1": "haiku", "level_2": "sonnet", "level_3": "sonnet", "level_4": "opus"},
                    "model_by_level": {"level_3": "custom-model-override"},
                },
            },
        }
        save_active_config(config, target_root=tmp_path)
        resolved = resolve_models(config, target_root=tmp_path)
        assert resolved["opencode"]["level_3"] == "custom-model-override"
        assert resolved["opencode"]["level_1"] != "UNRESOLVED"


# ---------------------------------------------------------------------------
# E2E — install_agents
# ---------------------------------------------------------------------------

class TestInstallAgentsE2E:
    def test_install_agents_creates_all_harness_dirs(self, tmp_path):
        from dftracer_agents.agents import install_agents
        result = install_agents(target_root=tmp_path)
        assert result["target"] == str(tmp_path)
        claude_dir = tmp_path / ".claude" / "agents"
        opencode_dir = tmp_path / ".opencode" / "agents"
        copilot_dir = tmp_path / ".github" / "agents"
        assert claude_dir.is_dir(), f"Missing {claude_dir}"
        assert opencode_dir.is_dir(), f"Missing {opencode_dir}"
        assert copilot_dir.is_dir(), f"Missing {copilot_dir}"

    def test_install_agents_file_counts(self, tmp_path):
        from dftracer_agents.agents import install_agents
        install_agents(target_root=tmp_path)
        claude_files = list((tmp_path / ".claude" / "agents").glob("*.md"))
        opencode_files = list((tmp_path / ".opencode" / "agents").glob("*.md"))
        copilot_files = list((tmp_path / ".github" / "agents").glob("*.agent.md"))
        expected = _expected_template_count()
        assert len(claude_files) == expected, f"Expected {expected} claude files, got {len(claude_files)}"
        assert len(opencode_files) == expected, f"Expected {expected} opencode files, got {len(opencode_files)}"
        assert len(copilot_files) == expected, f"Expected {expected} copilot files, got {len(copilot_files)}"

    def test_install_agents_claude_content(self, tmp_path):
        from dftracer_agents.agents import install_agents
        install_agents(target_root=tmp_path)
        analyzer = tmp_path / ".claude" / "agents" / "dftracer-analyzer.md"
        assert analyzer.is_file()
        content = analyzer.read_text()
        assert "generated-by: dftracer-agents (claude)" in content
        assert "model: sonnet" in content  # level_3 -> sonnet
        assert "mcp__dftracer__analyze" in content  # MCP tools keep original format for claude

    def test_install_agents_opencode_content(self, tmp_path):
        from dftracer_agents.agents import install_agents
        install_agents(target_root=tmp_path)
        analyzer = tmp_path / ".opencode" / "agents" / "dftracer-analyzer.md"
        assert analyzer.is_file()
        content = analyzer.read_text()
        assert "generated-by: dftracer-agents (opencode)" in content
        assert "mode: subagent" in content
        assert "ollama/" in content  # provider prefix for opencode
        # MCP tools reshaped
        assert "dftracer_analyze" in content
        # Frontmatter should NOT have mcp__ prefix (body prose may reference it)
        fm = content.split("---\n", 2)[1] if content.startswith("---\n") else content
        assert "mcp__dftracer__" not in fm

    def test_install_agents_copilot_content(self, tmp_path):
        from dftracer_agents.agents import install_agents
        install_agents(target_root=tmp_path)
        analyzer = tmp_path / ".github" / "agents" / "dftracer-analyzer.agent.md"
        assert analyzer.is_file()
        content = analyzer.read_text()
        assert "generated-by: dftracer-agents (copilot)" in content
        assert "dftracer/analyze" in content  # MCP tools reshaped for copilot
        # Frontmatter should NOT have mcp__ prefix (body prose may reference it)
        fm = content.split("---\n", 2)[1] if content.startswith("---\n") else content
        assert "mcp__dftracer__" not in fm

    def test_install_agents_idempotent(self, tmp_path):
        from dftracer_agents.agents import install_agents
        r1 = install_agents(target_root=tmp_path)
        r2 = install_agents(target_root=tmp_path)
        # Second run should not fail and should report already_installed for all
        actions2 = [i["action"] for i in r2["installed"]]
        assert all(a == "already_installed" for a in actions2), \
            f"Second install should be idempotent, got actions: {set(actions2)}"

    def test_install_agents_conflict_detection(self, tmp_path):
        """Pre-existing file without generation marker should NOT be overwritten."""
        from dftracer_agents.agents import install_agents
        conflict_dir = tmp_path / ".claude" / "agents"
        conflict_dir.mkdir(parents=True, exist_ok=True)
        conflict_file = conflict_dir / "dftracer-analyzer.md"
        original_content = "# This is my custom agent, do not overwrite!\n"
        conflict_file.write_text(original_content)
        
        result = install_agents(target_root=tmp_path)
        
        assert conflict_file.read_text() == original_content, "Conflict file was overwritten!"
        assert str(conflict_file) in result["conflicts"], \
            f"Conflict not reported: {result['conflicts']}"


# ---------------------------------------------------------------------------
# bootstrap — relative symlinks
# ---------------------------------------------------------------------------

class TestBootstrapRelativeSymlinks:
    def test_ensure_workspace_setup_creates_relative_symlinks(self, tmp_path):
        from dftracer_agents.bootstrap import ensure_workspace_setup
        result = ensure_workspace_setup(target_root=tmp_path, force=True)
        assert result["status"] in ("installed", "already_done", "partial")

        # Check that symlinks are relative, not absolute
        for item in result.get("instructions", []):
            dest = Path(item["path"])
            if dest.is_symlink():
                target = os.readlink(dest)
                assert not os.path.isabs(target), \
                    f"Symlink {dest} has absolute target: {target}"

    def test_mcp_json_symlink_relative(self, tmp_path):
        from dftracer_agents.bootstrap import ensure_workspace_setup
        ensure_workspace_setup(target_root=tmp_path, force=True)
        mcp = tmp_path / ".mcp.json"
        assert mcp.is_symlink(), f".mcp.json is not a symlink"
        target = os.readlink(mcp)
        assert not os.path.isabs(target), f".mcp.json symlink is absolute: {target}"

    def test_opencode_jsonc_symlink_relative(self, tmp_path):
        from dftracer_agents.bootstrap import ensure_workspace_setup
        ensure_workspace_setup(target_root=tmp_path, force=True)
        opencode = tmp_path / ".opencode" / "opencode.jsonc"
        assert opencode.is_symlink(), f".opencode/opencode.jsonc is not a symlink"
        target = os.readlink(opencode)
        assert not os.path.isabs(target), f"opencode.jsonc symlink is absolute: {target}"

    def test_vscode_mcp_json_symlink_relative(self, tmp_path):
        from dftracer_agents.bootstrap import ensure_workspace_setup
        ensure_workspace_setup(target_root=tmp_path, force=True)
        vscode = tmp_path / ".vscode" / "mcp.json"
        assert vscode.is_symlink(), f".vscode/mcp.json is not a symlink"
        target = os.readlink(vscode)
        assert not os.path.isabs(target), f".vscode/mcp.json symlink is absolute: {target}"

    def test_symlinks_resolve_correctly(self, tmp_path):
        """Relative symlinks should actually resolve to the right files."""
        from dftracer_agents.bootstrap import ensure_workspace_setup
        ensure_workspace_setup(target_root=tmp_path, force=True)
        mcp = tmp_path / ".mcp.json"
        assert mcp.is_symlink(), f".mcp.json is not a symlink"
        assert mcp.exists(), f".mcp.json symlink is broken: {os.readlink(mcp)}"
        content = mcp.read_text()
        assert "dftracer" in content
        assert "5000" in content  # port should be 5000 after our fix
