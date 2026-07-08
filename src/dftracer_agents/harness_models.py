"""Harness model selection helpers and CLI for dftracer-agents."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

from dftracer_agents.bootstrap import bundled_workspace_dir
from dftracer_agents.skills import resolve_default_target

LEVELS = ("level_1", "level_2", "level_3", "level_4")
HARNESSES = ("claude", "opencode", "copilot")
MODEL_CLASSES = ("haiku", "sonnet", "opus")

# Fallback map that mirrors src/dftracer_agents/.agents/workspace/models.yaml.
# Kept in code so model selection works even if YAML parsing is unavailable.
FALLBACK_LEVEL_MAP = {
    "level_1": {
        "class": "haiku",
        "providers": {
            "ollama": "qwen3.5:9b",
            "claude": "claude-haiku-4-20250514",
            "copilot": "gpt-5-codex-mini",
        },
    },
    "level_2": {
        "class": "sonnet",
        "providers": {
            "ollama": "qwen3.5:32b",
            "claude": "claude-sonnet-4-20250514",
            "copilot": "gpt-5-codex",
        },
    },
    "level_3": {
        "class": "sonnet",
        "providers": {
            "ollama": "qwen3-coder:480b-cloud",
            "claude": "claude-sonnet-4-20250514",
            "copilot": "gpt-5-codex",
        },
    },
    "level_4": {
        "class": "opus",
        "providers": {
            "ollama": "deepseek-v3.2:cloud",
            "claude": "claude-opus-4-20250514",
            "copilot": "gpt-5-codex-pro",
        },
    },
}

DEFAULT_PROVIDER_BY_HARNESS = {
    "claude": "claude",
    "opencode": "ollama",
    "copilot": "copilot",
}

# Curated selectable models by provider for interactive setup.
AVAILABLE_MODELS_BY_PROVIDER = {
    "ollama": [
        "qwen3.5:9b",
        "qwen3.5:32b",
        "qwen3-coder:480b-cloud",
        "deepseek-v3.2:cloud",
        "minimax-m2.7:cloud",
        "gpt-oss:120b-cloud",
    ],
    "claude": [
        "claude-haiku-4-20250514",
        "claude-sonnet-4-20250514",
        "claude-opus-4-20250514",
    ],
    "copilot": [
        "gpt-5-codex-mini",
        "gpt-5-codex",
        "gpt-5-codex-pro",
    ],
}


def _workspace_root(target_root: Optional[Path] = None) -> Path:
    if target_root is None:
        root = resolve_default_target()
    else:
        root = Path(target_root).expanduser().resolve()
    source_workspace = root / "src" / "dftracer_agents" / ".agents" / "workspace"
    if source_workspace.is_dir():
        return source_workspace
    return bundled_workspace_dir()


def _active_models_path(target_root: Optional[Path] = None) -> Path:
    return _workspace_root(target_root) / "active-models.json"


def _setup_state_path(target_root: Optional[Path] = None) -> Path:
    return _workspace_root(target_root) / "setup-state.json"


def _default_class_by_level(level_map: Dict[str, Dict[str, object]]) -> Dict[str, str]:
    return {level: str(level_map[level]["class"]) for level in LEVELS}


def _class_models_for_provider(level_map: Dict[str, Dict[str, object]], provider: str) -> Dict[str, str]:
    class_models: Dict[str, str] = {}
    for level in LEVELS:
        level_info = level_map[level]
        class_name = str(level_info["class"])
        providers = level_info["providers"]
        if not isinstance(providers, dict):
            continue
        model = providers.get(provider)
        if model:
            class_models.setdefault(class_name, str(model))
    return class_models


def _default_active_config(level_map: Dict[str, Dict[str, object]]) -> Dict[str, object]:
    class_by_level = _default_class_by_level(level_map)
    harnesses: Dict[str, object] = {}
    for harness in HARNESSES:
        harnesses[harness] = {
            "provider": DEFAULT_PROVIDER_BY_HARNESS[harness],
            "class_by_level": dict(class_by_level),
            "model_by_level": {},
        }
    return {"version": 1, "harnesses": harnesses}


def _read_level_map_from_yaml(target_root: Optional[Path] = None) -> Dict[str, Dict[str, object]]:
    yaml_path = _workspace_root(target_root) / "models.yaml"
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(yaml_path.read_text())
        levels = data.get("levels", {}) if isinstance(data, dict) else {}
        parsed: Dict[str, Dict[str, object]] = {}
        for level in LEVELS:
            level_data = levels.get(level, {}) if isinstance(levels, dict) else {}
            default_class = level_data.get("default")
            providers = level_data.get("providers")
            if not isinstance(default_class, str) or not isinstance(providers, dict):
                return FALLBACK_LEVEL_MAP
            parsed[level] = {"class": default_class, "providers": dict(providers)}
        return parsed
    except Exception:
        return FALLBACK_LEVEL_MAP


def load_active_config(target_root: Optional[Path] = None) -> Dict[str, object]:
    level_map = _read_level_map_from_yaml(target_root)
    path = _active_models_path(target_root)
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            if isinstance(loaded, dict) and isinstance(loaded.get("harnesses"), dict):
                return loaded
        except Exception:
            pass
    config = _default_active_config(level_map)
    save_active_config(config, target_root=target_root)
    return config


def save_active_config(config: Dict[str, object], target_root: Optional[Path] = None) -> Path:
    path = _active_models_path(target_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2) + "\n")
    return path


def save_setup_state(target_root: Optional[Path] = None) -> Path:
    path = _setup_state_path(target_root)
    state = {
        "configured": True,
        "active_models": str(_active_models_path(target_root)),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n")
    return path


def has_setup_state(target_root: Optional[Path] = None) -> bool:
    state = _setup_state_path(target_root)
    models = _active_models_path(target_root)
    return state.exists() and models.exists()


def resolve_models(config: Dict[str, object], target_root: Optional[Path] = None) -> Dict[str, Dict[str, str]]:
    level_map = _read_level_map_from_yaml(target_root)
    harnesses = config.get("harnesses", {})
    if not isinstance(harnesses, dict):
        return {}

    resolved: Dict[str, Dict[str, str]] = {}
    default_classes = _default_class_by_level(level_map)

    for harness in HARNESSES:
        raw = harnesses.get(harness, {})
        if not isinstance(raw, dict):
            continue
        provider = str(raw.get("provider", DEFAULT_PROVIDER_BY_HARNESS[harness]))
        class_by_level = raw.get("class_by_level", {})
        model_by_level = raw.get("model_by_level", {})
        if not isinstance(class_by_level, dict):
            class_by_level = {}
        if not isinstance(model_by_level, dict):
            model_by_level = {}

        class_models = _class_models_for_provider(level_map, provider)
        harness_models: Dict[str, str] = {}
        for level in LEVELS:
            override_model = model_by_level.get(level)
            if isinstance(override_model, str) and override_model.strip():
                harness_models[level] = override_model.strip()
                continue

            class_name = class_by_level.get(level)
            if not isinstance(class_name, str) or not class_name.strip():
                class_name = default_classes[level]
            class_name = class_name.strip()

            model = None
            if class_name == default_classes[level]:
                providers = level_map[level].get("providers", {})
                if isinstance(providers, dict) and provider in providers:
                    model = str(providers[provider])

            if model is None:
                model = class_models.get(class_name)
            if model is None:
                providers = level_map[level].get("providers", {})
                if isinstance(providers, dict) and provider in providers:
                    model = str(providers[provider])
                else:
                    model = "UNRESOLVED"
            harness_models[level] = model
        resolved[harness] = {"provider": provider, **harness_models}

    return resolved


def summarize_harness_models(target_root: Optional[Path] = None) -> List[str]:
    config = load_active_config(target_root=target_root)
    resolved = resolve_models(config, target_root=target_root)

    lines = ["[setup] Harness model configuration:"]
    for harness in HARNESSES:
        entry = resolved.get(harness)
        if not entry:
            lines.append(f"[setup]   {harness}: not configured")
            continue
        provider = entry.get("provider", "unknown")
        levels = ", ".join(f"{level}={entry.get(level, 'UNSET')}" for level in LEVELS)
        lines.append(f"[setup]   {harness}: provider={provider}; {levels}")
    return lines


def print_previous_harness_models(target_root: Optional[Path] = None) -> None:
    for line in summarize_harness_models(target_root=target_root):
        print(line)


def _choose_from_list(prompt: str, options: Sequence[str], default_index: int = 0) -> str:
    def _read() -> str:
        try:
            return input("> ").strip()
        except EOFError:
            return ""

    while True:
        print(prompt)
        for idx, option in enumerate(options, start=1):
            marker = " (default)" if idx - 1 == default_index else ""
            print(f"  {idx}. {option}{marker}")
        raw = _read()
        if not raw:
            return options[default_index]
        if raw.isdigit():
            selected = int(raw)
            if 1 <= selected <= len(options):
                return options[selected - 1]
        print("Invalid selection. Enter a number from the list.")


def _choose_harnesses_interactive() -> List[str]:
    options = [*HARNESSES, "all"]
    selected = _choose_from_list(
        "Select harness to configure:",
        options,
        default_index=len(options) - 1,
    )
    if selected == "all":
        return list(HARNESSES)
    return [selected]


def run_interactive_setup(target_root: Optional[Path] = None) -> Path:
    level_map = _read_level_map_from_yaml(target_root)
    config = load_active_config(target_root=target_root)
    harness_section = config.setdefault("harnesses", {})
    if not isinstance(harness_section, dict):
        raise ValueError("Invalid active-models.json: harnesses must be a map")

    default_classes = _default_class_by_level(level_map)
    selected_harnesses = _choose_harnesses_interactive()

    for harness in selected_harnesses:
        current = harness_section.get(harness, {})
        if not isinstance(current, dict):
            current = {}

        current_provider = str(current.get("provider", DEFAULT_PROVIDER_BY_HARNESS[harness]))
        provider = _choose_from_list(
            f"Select model provider for harness '{harness}':",
            ["ollama", "claude", "copilot"],
            default_index=["ollama", "claude", "copilot"].index(current_provider)
            if current_provider in ("ollama", "claude", "copilot")
            else 0,
        )

        current_classes = current.get("class_by_level", {})
        if not isinstance(current_classes, dict):
            current_classes = {}
        current_models = current.get("model_by_level", {})
        if not isinstance(current_models, dict):
            current_models = {}

        new_classes: Dict[str, str] = {}
        new_models: Dict[str, str] = {}

        print(f"Configuring {harness} with provider={provider}")
        available_models = AVAILABLE_MODELS_BY_PROVIDER.get(provider, [])

        for level in LEVELS:
            # Keep semantic class mapping canonical to avoid duplicated prompts.
            new_classes[level] = default_classes[level]

            resolved_default = ""
            # If staying on same provider and explicit override exists, keep it as default.
            if current_provider == provider:
                existing_override = current_models.get(level)
                if isinstance(existing_override, str) and existing_override.strip():
                    resolved_default = existing_override.strip()

            if not resolved_default:
                providers = level_map[level].get("providers", {})
                if isinstance(providers, dict) and provider in providers:
                    resolved_default = str(providers[provider])
                else:
                    resolved_default = ""

            model_options = list(available_models)
            if resolved_default and resolved_default not in model_options:
                model_options.insert(0, resolved_default)

            print(f"Select model for {level} (provider={provider}).")
            if model_options:
                print(f"Press Enter to use default: {resolved_default}")
                for idx, model in enumerate(model_options, start=1):
                    marker = " (default)" if model == resolved_default else ""
                    print(f"  {idx}. {model}{marker}")
            else:
                print(f"No predefined options. Default is: {resolved_default}")

            try:
                raw = input("> ").strip()
            except EOFError:
                raw = ""
            if not raw:
                selected_model = resolved_default
            elif raw.isdigit() and model_options and 1 <= int(raw) <= len(model_options):
                selected_model = model_options[int(raw) - 1]
            else:
                selected_model = raw

            if selected_model and selected_model != resolved_default:
                new_models[level] = selected_model

        current["provider"] = provider
        current["class_by_level"] = new_classes
        current["model_by_level"] = new_models
        harness_section[harness] = current

    path = save_active_config(config, target_root=target_root)
    save_setup_state(target_root=target_root)
    return path


def prepare_startup_configuration(target_root: Optional[Path] = None) -> tuple[Path, bool]:
    """Resolve startup config. Returns (active_models_path, reused_previous)."""

    if has_setup_state(target_root=target_root):
        print("[setup] Previous configured system:")
        print_previous_harness_models(target_root=target_root)
        try:
            answer = input("[setup] Continue with previous configured system? [Y/n]: ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("", "y", "yes"):
            return _active_models_path(target_root), True

    path = run_interactive_setup(target_root=target_root)
    save_setup_state(target_root=target_root)
    return path, False


def update_harness_config(
    *,
    harnesses: Iterable[str],
    provider: Optional[str] = None,
    class_overrides: Optional[Dict[str, str]] = None,
    model_overrides: Optional[Dict[str, str]] = None,
    target_root: Optional[Path] = None,
) -> Path:
    level_map = _read_level_map_from_yaml(target_root)
    config = load_active_config(target_root=target_root)
    harness_section = config.setdefault("harnesses", {})
    if not isinstance(harness_section, dict):
        raise ValueError("Invalid active-models.json: harnesses must be a map")

    default_classes = _default_class_by_level(level_map)
    class_overrides = class_overrides or {}
    model_overrides = model_overrides or {}

    for harness in harnesses:
        if harness not in HARNESSES:
            raise ValueError(f"Unknown harness: {harness}")
        current = harness_section.get(harness, {})
        if not isinstance(current, dict):
            current = {}

        if provider:
            current["provider"] = provider
        else:
            current.setdefault("provider", DEFAULT_PROVIDER_BY_HARNESS[harness])

        current_classes = current.get("class_by_level", {})
        if not isinstance(current_classes, dict):
            current_classes = {}
        for level in LEVELS:
            current_classes.setdefault(level, default_classes[level])
        for level, value in class_overrides.items():
            if level in LEVELS and value:
                current_classes[level] = value
        current["class_by_level"] = current_classes

        current_models = current.get("model_by_level", {})
        if not isinstance(current_models, dict):
            current_models = {}
        for level, value in model_overrides.items():
            if level in LEVELS and value:
                current_models[level] = value
        current["model_by_level"] = current_models

        harness_section[harness] = current

    return save_active_config(config, target_root=target_root)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        prog="dftracer-configure-harness",
        description=(
            "Configure harness backends and level-class/model selection used "
            "by dftracer startup summaries."
        ),
    )
    parser.add_argument("--target", default=None, help="Project root to configure (default: auto-detect).")
    parser.add_argument(
        "--harness",
        choices=[*HARNESSES, "all"],
        default="all",
        help="Harness to configure (default: all).",
    )
    parser.add_argument(
        "--provider",
        choices=["ollama", "claude", "copilot"],
        default=None,
        help="Model backend provider for the selected harness(es).",
    )
    parser.add_argument("--class-level-1", choices=MODEL_CLASSES, default=None)
    parser.add_argument("--class-level-2", choices=MODEL_CLASSES, default=None)
    parser.add_argument("--class-level-3", choices=MODEL_CLASSES, default=None)
    parser.add_argument("--class-level-4", choices=MODEL_CLASSES, default=None)
    parser.add_argument("--model-level-1", default=None, help="Explicit model override for level_1")
    parser.add_argument("--model-level-2", default=None, help="Explicit model override for level_2")
    parser.add_argument("--model-level-3", default=None, help="Explicit model override for level_3")
    parser.add_argument("--model-level-4", default=None, help="Explicit model override for level_4")
    parser.add_argument(
        "--show",
        action="store_true",
        default=False,
        help="Show resolved harness configuration without applying changes.",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        default=False,
        help=(
            "Run interactive setup: choose harness, provider, and per-level "
            "models with defaults shown from current configuration."
        ),
    )

    args = parser.parse_args()
    target_root = Path(args.target).expanduser().resolve() if args.target else None

    class_overrides = {
        "level_1": args.class_level_1,
        "level_2": args.class_level_2,
        "level_3": args.class_level_3,
        "level_4": args.class_level_4,
    }
    model_overrides = {
        "level_1": args.model_level_1,
        "level_2": args.model_level_2,
        "level_3": args.model_level_3,
        "level_4": args.model_level_4,
    }

    has_changes = bool(args.provider) or any(class_overrides.values()) or any(model_overrides.values())
    selected_harnesses = HARNESSES if args.harness == "all" else (args.harness,)

    if args.interactive:
        path = run_interactive_setup(target_root=target_root)
        print(f"Saved harness model config: {path}")
        print(f"Saved setup state: {_setup_state_path(target_root)}")
        for line in summarize_harness_models(target_root=target_root):
            print(line)
        return

    if has_changes:
        path = update_harness_config(
            harnesses=selected_harnesses,
            provider=args.provider,
            class_overrides=class_overrides,
            model_overrides=model_overrides,
            target_root=target_root,
        )
        print(f"Saved harness model config: {path}")

    if args.show or not has_changes:
        # Ensure the file exists and display the resolved mapping.
        load_active_config(target_root=target_root)

    for line in summarize_harness_models(target_root=target_root):
        print(line)


if __name__ == "__main__":
    main()
