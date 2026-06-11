from __future__ import annotations

import json
import os
import pathlib
import queue
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

from .goose_feedback import (
    append_stage_feedback,
    build_feedback_context,
    build_feedback_context_text,
    collect_stage_feedback,
    ensure_feedback_run,
    feedback_prompt_enabled,
    load_feedback_db,
    resolve_feedback_db_path,
    save_feedback_db,
)
from .mcp_servers.modules.pipeline import execute_pipeline_stage
from .workspace import detect_repo_attributes, tree_summary

DEFAULT_PIPELINE_NAME = "ior"
DEFAULT_PIPELINE_LANGUAGE = "cpp"
DEFAULT_PIPELINE_REPO_URL = "https://github.com/hpc/ior"
DEFAULT_PIPELINE_REPO_REF = "4.0.0"
DEFAULT_TERMINAL_RUN_ID = "terminal_default"
DEFAULT_GOOSE_STAGE_TIMEOUT_SECONDS = 120
DEFAULT_SYSTEM_PATH = "/usr/local/bin:/usr/bin:/bin"
GOOSE_PIPELINE_STAGE_ORDER = [
    "detect",
    "test_default_build_setup",
    "test_default_run",
    "annotate",
    "build_with_dftracer",
    "postprocess",
    "dfanalyzer",
]

GOOSE_STAGE_RECIPE_FILES = {
    "detect": "subrecipes/10_detect_stage.yaml",
    "test_default_build_setup": "subrecipes/20_build_setup_stage.yaml",
    "test_default_run": "subrecipes/30_default_run_stage.yaml",
    "annotate": None,
    "build_with_dftracer": "subrecipes/50_build_with_dftracer_stage.yaml",
    "postprocess": "subrecipes/60_postprocess_stage.yaml",
    "dfanalyzer": "subrecipes/70_dfanalyzer_stage.yaml",
}

GOOSE_STAGE_INSTRUCTION_TEXT = {
    "detect": "Execute the detect stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "test_default_build_setup": "Execute the default build setup stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "test_default_run": "Execute the default run stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "annotate": "Execute the annotate stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "build_with_dftracer": "Execute the DFTracer rebuild stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "postprocess": "Execute the postprocess stage using the recipe parameters and return only the JSON response required by the recipe schema.",
    "dfanalyzer": "Execute the DFAnalyzer stage using the recipe parameters and return only the JSON response required by the recipe schema.",
}

GOOSE_STAGE_DEPENDENCIES = {
    "detect": [],
    "test_default_build_setup": ["detect"],
    "test_default_run": ["detect", "test_default_build_setup"],
    "annotate": ["detect", "test_default_run"],
    "build_with_dftracer": ["detect", "annotate", "test_default_build_setup"],
    "postprocess": ["build_with_dftracer"],
    "dfanalyzer": ["postprocess"],
}

GOOSE_STAGE_REQUIRED_FIELDS = {
    "detect": {
        "top_level": [
            "summary",
            "language",
            "build_system",
            "uses_mpi",
            "mpi_detection",
            "uses_hip",
            "dftracer_flags",
            "notes",
            "handoff",
        ],
        "handoff": ["language", "build_system", "uses_mpi", "mpi_detection", "uses_hip", "dftracer_flags"],
    },
    "test_default_build_setup": {
        "top_level": ["summary", "needs_docs", "needs_docs_reason", "commands", "notes", "handoff"],
        "handoff": ["commands", "install_prefix", "needs_docs", "needs_docs_reason"],
    },
    "test_default_run": {
        "top_level": ["summary", "run_cmd", "notes", "handoff"],
        "handoff": ["run_cmd"],
    },
    "annotate": {
        "top_level": ["summary", "ok", "annotation", "patch", "notes", "handoff"],
        "handoff": ["ok", "language", "patch_applied"],
    },
    "build_with_dftracer": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["commands", "install_prefix"],
    },
    "postprocess": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["post_dir", "compacted_trace_dir", "index_dir"],
    },
    "dfanalyzer": {
        "top_level": ["summary", "commands", "notes", "handoff"],
        "handoff": ["analysis_dir", "commands"],
    },
}


def project_root() -> pathlib.Path:
    return pathlib.Path(__file__).resolve().parents[2]


def goose_pipeline_recipe_path(root: pathlib.Path | None = None) -> pathlib.Path:
    base = root or project_root()
    return base / "goose" / "recipes" / "00_dftracer_pipeline.yaml"


def goose_stage_recipe_path(stage_name: str, root: pathlib.Path | None = None, language: str = DEFAULT_PIPELINE_LANGUAGE) -> pathlib.Path:
    base = root or project_root()
    if stage_name == "annotate":
        normalized = (language or "").strip().lower()
        recipe_name = "subrecipes/42_annotate_python_stage.yaml" if normalized == "python" else "subrecipes/41_annotate_c_cpp_stage.yaml"
        return base / "goose" / "recipes" / recipe_name
    recipe_name = GOOSE_STAGE_RECIPE_FILES[stage_name]
    if recipe_name is None:
        raise KeyError(f"No direct Goose recipe configured for stage {stage_name}")
    return base / "goose" / "recipes" / recipe_name


def goose_extension_command(root: pathlib.Path | None = None) -> str:
    base = root or project_root()
    python_bin = base / ".venv" / "bin" / "python"
    if not python_bin.exists():
        python_bin = pathlib.Path(os.environ.get("PYTHON", "python3"))
    return shlex.join([str(python_bin), "-m", "dftracer_agents.mcp_servers.server"])


def goose_stage_instruction_text(stage_name: str) -> str:
    return GOOSE_STAGE_INSTRUCTION_TEXT[stage_name]


def _build_setup_doc_targets(defaults: dict[str, str]) -> list[str]:
    candidates = _stage_repo_summary_entries("test_default_build_setup", defaults)
    ordered: list[str] = []
    allowed = (
        "readme",
        "install",
        "build",
        "cmakelists.txt",
        "configure",
        "configure.ac",
        "makefile",
        "makefile.am",
        "makefile.in",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "bootstrap",
    )
    for entry in candidates:
        lowered = entry.lower()
        if any(marker in lowered for marker in allowed):
            ordered.append(entry)
    return ordered[:8]


def _stage_repo_summary_entries(stage_name: str, defaults: dict[str, str]) -> list[str]:
    summary_entries = json.loads(defaults["repo_summary_json"])
    if stage_name != "test_default_build_setup":
        return summary_entries

    build_markers = (
        "readme",
        "install",
        "build",
        "cmakelists.txt",
        "configure",
        "configure.ac",
        "makefile",
        "makefile.am",
        "makefile.in",
        "pyproject.toml",
        "setup.py",
        "requirements.txt",
        "bootstrap",
    )
    prioritized: list[str] = []
    for entry in summary_entries:
        lowered = entry.lower()
        if any(marker in lowered for marker in build_markers):
            prioritized.append(entry)
    if not prioritized:
        return summary_entries[:20]
    return prioritized[:20]



def _pipeline_context_payload(stage_name: str, defaults: dict[str, str]) -> dict[str, Any]:
    return {
        "name": defaults["name"],
        "repo_url": defaults["repo_url"],
        "repo_ref": defaults["repo_ref"],
        "language": defaults["language"],
        "workspace_root": defaults["workspace_root"],
        "repo_dir": defaults["repo_dir"],
        "venv_dir": defaults["venv_dir"],
        "trace_dir": defaults["trace_dir"],
        "post_dir": defaults["post_dir"],
        "compacted_trace_dir": defaults["compacted_trace_dir"],
        "analysis_dir": defaults["analysis_dir"],
        "repo_summary": _stage_repo_summary_entries(stage_name, defaults),
        "repo_attrs": json.loads(defaults["repo_attrs_json"]),
    }


def build_stage_input_payload(
    stage_name: str,
    *,
    defaults: dict[str, str],
    stage_results: dict[str, dict[str, Any]],
    feedback_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    upstream: dict[str, Any] = {}
    for dependency in GOOSE_STAGE_DEPENDENCIES[stage_name]:
        payload = stage_results[dependency]
        upstream[dependency] = {
            "stage": payload.get("stage", dependency),
            "summary": payload.get("summary", ""),
            "handoff": payload.get("handoff", {}),
        }

    stage_input = {
        "stage": stage_name,
        "context": _pipeline_context_payload(stage_name, defaults),
        "upstream": upstream,
        "feedback_context": feedback_context or {"stage": stage_name, "has_feedback": False, "prior_feedback": []},
        "workspace_policy": {
            "workspace_root": defaults["workspace_root"],
            "repo_dir": defaults["repo_dir"],
            "allowed_write_roots": [
                defaults["workspace_root"],
                defaults["venv_dir"],
                defaults["trace_dir"],
                defaults["post_dir"],
                defaults["analysis_dir"],
            ],
            "forbidden_commands": ["sed"],
            "instruction": "Stay inside workspace_root for all reads and writes. Do not inspect, create, or modify files outside that workspace. Prefer repo summaries, MCP tools, cat, or python-based file reads instead of sed.",
        },
    }
    if stage_name == "test_default_build_setup":
        stage_input["planning_constraints"] = {
            "mode": "compact_build_setup",
            "inspect_budget_files": 4,
            "prefer_direct_plan": True,
            "avoid_delegation": True,
            "checkpoint_on_uncertainty": True,
        }
        stage_input["doc_targets"] = _build_setup_doc_targets(defaults)
        stage_input["repo_reference"] = {
            "repo_url": defaults["repo_url"],
            "repo_ref": defaults["repo_ref"],
            "repo_dir": defaults["repo_dir"],
        }
    return stage_input


def _effective_language(defaults: dict[str, str], stage_results: dict[str, dict[str, Any]]) -> str:
    detect_handoff = (stage_results.get("detect") or {}).get("handoff", {})
    return str(detect_handoff.get("language") or defaults["language"])


def build_goose_stage_params(
    stage_name: str,
    *,
    defaults: dict[str, str],
    pipeline_context_file: pathlib.Path,
    stage_results: dict[str, dict[str, Any]],
    feedback_context: dict[str, Any] | None = None,
) -> tuple[dict[str, str], dict[str, Any]]:
    stage_input = build_stage_input_payload(
        stage_name,
        defaults=defaults,
        stage_results=stage_results,
        feedback_context=feedback_context,
    )
    compact_repo_summary_json = json.dumps(_stage_repo_summary_entries(stage_name, defaults))
    params = {
        "stage_name": stage_name,
        "name": defaults["name"],
        "repo_url": defaults["repo_url"],
        "repo_ref": defaults["repo_ref"],
        "venv_dir": defaults["venv_dir"],
        "trace_dir": defaults["trace_dir"],
        "post_dir": defaults["post_dir"],
        "compacted_trace_dir": defaults["compacted_trace_dir"],
        "analysis_dir": defaults["analysis_dir"],
        "language": _effective_language(defaults, stage_results),
        "repo_dir": defaults["repo_dir"],
        "repo_summary_json": compact_repo_summary_json,
        "repo_attrs_json": defaults["repo_attrs_json"],
        "pipeline_context_file": str(pipeline_context_file),
        "stage_input_json": json.dumps(stage_input),
    }
    postprocess_handoff = (stage_results.get("postprocess") or {}).get("handoff", {})
    if postprocess_handoff.get("compacted_trace_dir"):
        params["compacted_trace_dir"] = str(postprocess_handoff["compacted_trace_dir"])
    if postprocess_handoff.get("post_dir"):
        params["post_dir"] = str(postprocess_handoff["post_dir"])
    dftracer_build_handoff = (stage_results.get("build_with_dftracer") or {}).get("handoff", {})
    if dftracer_build_handoff.get("install_prefix"):
        params["venv_dir"] = str(dftracer_build_handoff["install_prefix"])
    build_setup_handoff = (stage_results.get("test_default_build_setup") or {}).get("handoff", {})
    if build_setup_handoff.get("install_prefix"):
        params["venv_dir"] = str(build_setup_handoff["install_prefix"])
    return params, stage_input




def _validate_stage_payload(stage_name: str, payload: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(payload)
    normalized.setdefault("stage", stage_name)
    contract = GOOSE_STAGE_REQUIRED_FIELDS[stage_name]
    missing = [key for key in contract["top_level"] if key not in normalized]
    handoff = normalized.get("handoff")
    if not isinstance(handoff, dict):
        missing.append("handoff")
        handoff = {}
    missing.extend(f"handoff.{key}" for key in contract["handoff"] if key not in handoff)
    if missing:
        missing_text = ", ".join(sorted(set(missing)))
        raise RuntimeError(f"Goose terminal pipeline returned mismatched output at {stage_name}: missing {missing_text}")
    return normalized


def _parse_goose_json_text(text: str) -> dict[str, Any] | None:
    raw = (text or "").strip()
    if not raw:
        return None

    def _extract_payload(candidate: Any) -> dict[str, Any] | None:
        if isinstance(candidate, dict):
            if "handoff" in candidate and ("stage" in candidate or "summary" in candidate):
                return candidate

            for key in (
                "structuredContent",
                "response",
                "result",
                "output",
                "final_output",
                "final_response",
                "content",
                "toolResult",
                "toolCall",
                "arguments",
                "value",
            ):
                if key in candidate:
                    parsed = _extract_payload(candidate[key])
                    if parsed is not None:
                        return parsed

            messages = candidate.get("messages")
            if isinstance(messages, list):
                parsed = _extract_payload(messages)
                if parsed is not None:
                    return parsed
            return None

        if isinstance(candidate, list):
            for item in reversed(candidate):
                parsed = _extract_payload(item)
                if parsed is not None:
                    return parsed
            return None

        if isinstance(candidate, str):
            inner = candidate.strip()
            if not inner:
                return None
            try:
                return _extract_payload(json.loads(inner))
            except json.JSONDecodeError:
                start = inner.find("{")
                end = inner.rfind("}")
                if start != -1 and end != -1 and end > start:
                    try:
                        return _extract_payload(json.loads(inner[start : end + 1]))
                    except json.JSONDecodeError:
                        pass
                return _extract_from_raw_scan(inner)

        return None

    def _sanitize_json_text(snippet: str) -> str:
        result: list[str] = []
        in_string = False
        escape = False
        for char in snippet:
            if in_string:
                if escape:
                    result.append(char)
                    escape = False
                    continue
                if char == "\\":
                    result.append(char)
                    escape = True
                    continue
                if char == '"':
                    result.append(char)
                    in_string = False
                    continue
                if char == "\n":
                    result.append(r"\n")
                    continue
                if char == "\r":
                    result.append(r"\r")
                    continue
                if char == "\t":
                    result.append(r"\t")
                    continue
                result.append(char)
                continue
            result.append(char)
            if char == '"':
                in_string = True
        return "".join(result)

    def _extract_braced_object(source: str, start_index: int) -> dict[str, Any] | None:
        depth = 0
        in_string = False
        escape = False
        for index in range(start_index, len(source)):
            char = source[index]
            if in_string:
                if escape:
                    escape = False
                elif char == "\\":
                    escape = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == '{':
                depth += 1
            elif char == '}':
                depth -= 1
                if depth == 0:
                    snippet = source[start_index : index + 1]
                    try:
                        parsed = json.loads(snippet)
                    except json.JSONDecodeError:
                        try:
                            parsed = json.loads(_sanitize_json_text(snippet))
                        except json.JSONDecodeError:
                            return None
                    return _extract_payload(parsed)
        return None

    def _extract_from_raw_scan(source: str) -> dict[str, Any] | None:
        def _scan_argument_blocks(blob: str) -> dict[str, Any] | None:
            marker = '"arguments": {'
            positions: list[int] = []
            offset = blob.find(marker)
            while offset != -1:
                positions.append(offset)
                offset = blob.find(marker, offset + 1)
            for offset in reversed(positions):
                brace_index = blob.find('{', offset)
                if brace_index == -1:
                    continue
                parsed = _extract_braced_object(blob, brace_index)
                if parsed is not None:
                    return parsed
            return None

        parsed = _scan_argument_blocks(source)
        if parsed is not None:
            return parsed

        for marker in ('{"stage"', '{"summary"'):
            positions: list[int] = []
            offset = source.find(marker)
            while offset != -1:
                positions.append(offset)
                offset = source.find(marker, offset + 1)
            for offset in reversed(positions):
                parsed = _extract_braced_object(source, offset)
                if parsed is not None:
                    return parsed

        escaped = source.replace(r'\"', '"').replace(r'\n', '\n')
        if escaped != source:
            parsed = _scan_argument_blocks(escaped)
            if parsed is not None:
                return parsed
            for marker in ('{"stage"', '{"summary"'):
                positions = []
                offset = escaped.find(marker)
                while offset != -1:
                    positions.append(offset)
                    offset = escaped.find(marker, offset + 1)
                for offset in reversed(positions):
                    parsed = _extract_braced_object(escaped, offset)
                    if parsed is not None:
                        return parsed
        return None


    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                payload = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                payload = None
        else:
            payload = None
        if payload is None:
            return _extract_from_raw_scan(raw)

    parsed = _extract_payload(payload)
    if parsed is not None:
        return parsed
    return _extract_from_raw_scan(raw)


def _path_is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _require_workspace_containment(defaults: dict[str, str]) -> None:
    workspace = pathlib.Path(defaults["workspace_root"]).expanduser().resolve()
    checked = {
        "repo_dir": pathlib.Path(defaults["repo_dir"]).expanduser().resolve(),
        "venv_dir": pathlib.Path(defaults["venv_dir"]).expanduser().resolve(),
        "trace_dir": pathlib.Path(defaults["trace_dir"]).expanduser().resolve(),
        "post_dir": pathlib.Path(defaults["post_dir"]).expanduser().resolve(),
        "compacted_trace_dir": pathlib.Path(defaults["compacted_trace_dir"]).expanduser().resolve(),
        "analysis_dir": pathlib.Path(defaults["analysis_dir"]).expanduser().resolve(),
    }
    for name, path in checked.items():
        if not _path_is_within(path, workspace):
            raise RuntimeError(f"{name} must stay inside workspace_root: {path} not under {workspace}")


def _workspace_runtime_dirs(defaults: dict[str, str]) -> dict[str, pathlib.Path]:
    workspace = pathlib.Path(defaults["workspace_root"]).expanduser().resolve()
    goose_root = workspace / ".goose"
    runtime = {
        "workspace_root": workspace,
        "goose_root": goose_root,
        "pipeline_contexts": goose_root / "pipeline_contexts",
        "home": goose_root / "home",
        "tmp": goose_root / "tmp",
        "cache": goose_root / "cache",
    }
    for path in runtime.values():
        if isinstance(path, pathlib.Path):
            path.mkdir(parents=True, exist_ok=True)
    return runtime


def _normalized_stage_environment(defaults: dict[str, str]) -> dict[str, str]:
    runtime = _workspace_runtime_dirs(defaults)
    env = os.environ.copy()
    current_path = env.get("PATH", "")
    extras = [segment for segment in DEFAULT_SYSTEM_PATH.split(":") if segment and segment not in current_path.split(":")]
    env["PATH"] = ":".join([part for part in [current_path, *extras] if part]) or DEFAULT_SYSTEM_PATH
    env["HOME"] = str(runtime["home"])
    env["TMPDIR"] = str(runtime["tmp"])
    env["TMP"] = str(runtime["tmp"])
    env["TEMP"] = str(runtime["tmp"])
    env["XDG_CACHE_HOME"] = str(runtime["cache"])
    env["DFTRACER_WORKSPACE_ROOT"] = str(runtime["workspace_root"])
    env["DFTRACER_REPO_DIR"] = str(pathlib.Path(defaults["repo_dir"]).expanduser().resolve())
    env["DFTRACER_ALLOW_ONLY_WORKSPACE"] = "1"
    return env


def _default_paths(
    root: pathlib.Path,
    *,
    name: str,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
) -> dict[str, str]:
    workspace = pathlib.Path(workspace_root).expanduser() if workspace_root else root / "workspaces" / name
    repo_path = pathlib.Path(repo_dir).expanduser() if repo_dir else workspace / "source" / name
    venv_path = pathlib.Path(venv_dir).expanduser() if venv_dir else workspace / "venv"
    trace_path = pathlib.Path(trace_dir).expanduser() if trace_dir else workspace / "traces" / DEFAULT_TERMINAL_RUN_ID
    artifacts_root = workspace / "artifacts" / DEFAULT_TERMINAL_RUN_ID
    post_path = pathlib.Path(post_dir).expanduser() if post_dir else artifacts_root / "postprocess"
    compacted_path = pathlib.Path(compacted_trace_dir).expanduser() if compacted_trace_dir else post_path / "compacted"
    analysis_path = pathlib.Path(analysis_dir).expanduser() if analysis_dir else artifacts_root / "analysis"
    return {
        "workspace_root": str(workspace),
        "repo_dir": str(repo_path),
        "venv_dir": str(venv_path),
        "trace_dir": str(trace_path),
        "post_dir": str(post_path),
        "compacted_trace_dir": str(compacted_path),
        "analysis_dir": str(analysis_path),
    }


def build_terminal_pipeline_defaults(
    root: pathlib.Path | None = None,
    *,
    name: str = DEFAULT_PIPELINE_NAME,
    repo_url: str = DEFAULT_PIPELINE_REPO_URL,
    repo_ref: str = DEFAULT_PIPELINE_REPO_REF,
    language: str = DEFAULT_PIPELINE_LANGUAGE,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
) -> dict[str, str]:
    base = root or project_root()
    defaults = {
        "name": name,
        "repo_url": repo_url,
        "repo_ref": repo_ref,
        "language": language,
    }
    defaults.update(
        _default_paths(
            base,
            name=name,
            workspace_root=workspace_root,
            repo_dir=repo_dir,
            venv_dir=venv_dir,
            trace_dir=trace_dir,
            post_dir=post_dir,
            compacted_trace_dir=compacted_trace_dir,
            analysis_dir=analysis_dir,
        )
    )
    repo_path = pathlib.Path(defaults["repo_dir"])
    repo_summary: list[str] = []
    repo_attrs: dict[str, Any] = {}
    if repo_path.exists():
        try:
            repo_summary = tree_summary(repo_path, max_entries=60)
        except Exception:
            repo_summary = []
        try:
            repo_attrs = detect_repo_attributes(repo_path)
        except Exception:
            repo_attrs = {}
    defaults["repo_summary_json"] = json.dumps(repo_summary)
    defaults["repo_attrs_json"] = json.dumps(repo_attrs)
    return defaults


def write_terminal_pipeline_context(
    defaults: dict[str, str],
    *,
    root: pathlib.Path | None = None,
    feedback_context_text: str = "",
) -> pathlib.Path:
    runtime = _workspace_runtime_dirs(defaults)
    context_dir = runtime["pipeline_contexts"]
    repo_name = defaults["name"]
    run_hint = pathlib.Path(defaults["repo_dir"]) / "src" / repo_name
    context_lines = [
        f"Application Name: {defaults['name']}",
        f"Repository URL: {defaults['repo_url']}",
        f"Repository Ref: {defaults['repo_ref']}",
        f"Repository Directory: {defaults['repo_dir']}",
        f"Workspace Root: {defaults['workspace_root']}",
        f"Language: {defaults['language']}",
        f"Workspace Venv: {defaults['venv_dir']}",
        f"Trace Directory: {defaults['trace_dir']}",
        f"Postprocess Directory: {defaults['post_dir']}",
        f"Compacted Trace Directory: {defaults['compacted_trace_dir']}",
        f"Analysis Directory: {defaults['analysis_dir']}",
        f"Default Baseline Run Hint: {run_hint} -a POSIX -w -r -k -t 64k -b 4m -F",
        "Goal: Plan the full DFTracer pipeline for the default IOR workflow.",
    ]
    if feedback_context_text:
        context_lines.extend(["", feedback_context_text])
    context = "\n".join(context_lines)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        prefix="terminal_pipeline_",
        suffix=".txt",
        dir=context_dir,
        delete=False,
    ) as handle:
        handle.write(context)
        return pathlib.Path(handle.name)


def build_goose_stage_command(
    stage_name: str,
    *,
    defaults: dict[str, str],
    pipeline_context_file: pathlib.Path,
    stage_results: dict[str, dict[str, Any]],
    root: pathlib.Path | None = None,
    extra_args: list[str] | None = None,
    feedback_context: dict[str, Any] | None = None,
) -> tuple[list[str], dict[str, str], dict[str, Any]]:
    base = root or project_root()
    recipe_path = goose_stage_recipe_path(stage_name, base, defaults["language"])
    launcher = base / "scripts" / "start_goose.sh"
    params, stage_input = build_goose_stage_params(
        stage_name,
        defaults=defaults,
        pipeline_context_file=pipeline_context_file,
        stage_results=stage_results,
        feedback_context=feedback_context,
    )

    cmd = [
        "bash",
        str(launcher),
        "run",
        "--with-builtin",
        "summon",
        "--recipe",
        str(recipe_path),
        "--no-session",
        "--no-profile",
        "--output-format",
        "json",
        "--with-extension",
        goose_extension_command(base),
    ]
    for key in (
        "stage_name",
        "name",
        "repo_url",
        "repo_ref",
        "venv_dir",
        "trace_dir",
        "post_dir",
        "compacted_trace_dir",
        "analysis_dir",
        "language",
        "repo_dir",
        "repo_summary_json",
        "repo_attrs_json",
        "pipeline_context_file",
        "stage_input_json",
    ):
        cmd.extend(["--params", f"{key}={params[key]}"])
    if extra_args:
        cmd.extend(extra_args)
    return cmd, params, stage_input


def _emit_progress(message: str, *, enabled: bool) -> None:
    if enabled:
        print(message, file=sys.stderr, flush=True)


def _redacted_env_summary() -> str:
    fields = {
        "OPENAI_BASE_URL": "set" if os.environ.get("OPENAI_BASE_URL") else "missing",
        "OPENAI_MODEL": os.environ.get("OPENAI_MODEL", "missing"),
        "OPENAI_API_KEY": "set" if os.environ.get("OPENAI_API_KEY") else "missing",
        "LIVAI_BASE_URL": "set" if os.environ.get("LIVAI_BASE_URL") else "missing",
        "LIVAI_MODEL": os.environ.get("LIVAI_MODEL", "missing"),
        "LIVAI_API_KEY": "set" if os.environ.get("LIVAI_API_KEY") else "missing",
    }
    return ", ".join(f"{key}={value}" for key, value in fields.items())


def _local_detect_stage_payload(defaults: dict[str, str]) -> dict[str, Any]:
    attrs = json.loads(defaults["repo_attrs_json"])
    summary_entries = json.loads(defaults["repo_summary_json"])
    languages_used = [str(item).strip().lower() for item in attrs.get("languages_used", []) if str(item).strip()]
    language = str(attrs.get("language") or defaults["language"] or "unknown").strip().lower()
    if language not in {"cpp", "python"}:
        if attrs.get("has_cpp"):
            language = "cpp"
        elif attrs.get("has_python"):
            language = "python"
        elif languages_used:
            language = languages_used[0]
        else:
            language = "unknown"

    build_system = "unknown"
    if attrs.get("has_cmake"):
        build_system = "cmake"
    elif attrs.get("has_pyproject"):
        build_system = "pyproject"
    elif any(entry.endswith("setup.py") for entry in summary_entries):
        build_system = "setuptools"
    elif any(pathlib.Path(entry).name in {"Makefile", "Makefile.am", "Makefile.in"} for entry in summary_entries):
        build_system = "make"
    elif any(pathlib.Path(entry).name in {"configure.ac", "configure"} for entry in summary_entries):
        build_system = "autotools"

    uses_mpi = bool(attrs.get("uses_mpi"))
    mpi_detection = "optional" if uses_mpi else "not_detected"
    uses_hip = bool(attrs.get("uses_hip"))
    build_python_bindings = "ON" if language == "python" else "OFF"
    notes = [
        "Detection completed locally from repository heuristics and a full repository file scan.",
        f"Languages detected across the repository: {', '.join(languages_used)}." if languages_used else "No source language files were detected in the repository scan.",
        "MPI is enabled whenever any MPI-related component is detected in repository content or build files." if uses_mpi else "No MPI component was detected in the repository heuristics.",
    ]
    summary = f"Detected language={language}, build_system={build_system}, languages_used={languages_used}, uses_mpi={str(uses_mpi).lower()}, uses_hip={str(uses_hip).lower()}."
    flags = {
        "DFTRACER_ENABLE_MPI": "ON" if uses_mpi else "OFF",
        "DFTRACER_ENABLE_HIP_TRACING": "ON" if uses_hip else "OFF",
        "DFTRACER_ENABLE_DYNAMIC_DETECTION": "ON",
        "DFTRACER_BUILD_PYTHON_BINDINGS": build_python_bindings,
        "DFTRACER_BUILD_TYPE": "RelWithDebInfo",
    }
    return {
        "stage": "detect",
        "summary": summary,
        "language": language,
        "languages_used": languages_used,
        "build_system": build_system,
        "uses_mpi": uses_mpi,
        "mpi_detection": mpi_detection,
        "uses_hip": uses_hip,
        "dftracer_flags": flags,
        "notes": notes,
        "handoff": {
            "language": language,
            "build_system": build_system,
            "uses_mpi": uses_mpi,
            "mpi_detection": mpi_detection,
            "uses_hip": uses_hip,
            "dftracer_flags": flags,
        },
    }


def _stage_artifacts_root(defaults: dict[str, str]) -> pathlib.Path:
    return pathlib.Path(defaults["workspace_root"]).expanduser().resolve() / "artifacts" / DEFAULT_TERMINAL_RUN_ID / "stage_logs"


def _json_text(payload: Any) -> str:
    return json.dumps(payload, indent=2, sort_keys=True)


def _write_stage_artifacts(
    stage_name: str,
    *,
    defaults: dict[str, str],
    stage_input: dict[str, Any],
    stage_result: dict[str, Any],
    execution_result: dict[str, Any] | None,
    feedback_entry: dict[str, Any] | None,
) -> dict[str, str]:
    root = _stage_artifacts_root(defaults) / stage_name
    root.mkdir(parents=True, exist_ok=True)

    stage_input_path = root / "stage_input.json"
    stage_result_path = root / "stage_result.json"
    planned_commands_path = root / "planned_commands.txt"
    execution_result_path = root / "execution_result.json"
    execution_log_path = root / "execution_steps.log"
    feedback_path = root / "feedback.json"

    stage_input_path.write_text(_json_text(stage_input), encoding="utf-8")
    stage_result_path.write_text(_json_text(stage_result), encoding="utf-8")

    planned_commands = _stage_planned_commands(stage_result)
    if planned_commands:
        planned_commands_path.write_text("\n".join(planned_commands) + "\n", encoding="utf-8")
    elif planned_commands_path.exists():
        planned_commands_path.unlink()

    if execution_result is not None:
        execution_result_path.write_text(_json_text(execution_result), encoding="utf-8")
        log_lines: list[str] = []
        for index, step in enumerate(execution_result.get("steps", []), start=1):
            log_lines.append(f"[{index}] command: {step.get('command', '')}")
            log_lines.append(f"[{index}] cwd: {step.get('cwd', '')}")
            log_lines.append(f"[{index}] returncode: {step.get('returncode', '')}")
            stdout = str(step.get("stdout", "") or "").rstrip()
            stderr = str(step.get("stderr", "") or "").rstrip()
            if stdout:
                log_lines.append(f"[{index}] stdout:\n{stdout}")
            if stderr:
                log_lines.append(f"[{index}] stderr:\n{stderr}")
            log_lines.append("")
        execution_log_path.write_text("\n".join(log_lines).rstrip() + ("\n" if log_lines else ""), encoding="utf-8")
    else:
        if execution_result_path.exists():
            execution_result_path.unlink()
        if execution_log_path.exists():
            execution_log_path.unlink()

    if feedback_entry is not None:
        feedback_path.write_text(_json_text(feedback_entry), encoding="utf-8")
    elif feedback_path.exists():
        feedback_path.unlink()

    return {
        "stage_dir": str(root),
        "stage_input": str(stage_input_path),
        "stage_result": str(stage_result_path),
        "planned_commands": str(planned_commands_path),
        "execution_result": str(execution_result_path),
        "execution_log": str(execution_log_path),
        "feedback": str(feedback_path),
    }


def _stage_should_execute(stage_name: str) -> bool:
    if stage_name == "test_default_build_setup":
        setting = os.environ.get("DFTRACER_GOOSE_EXECUTE_BUILD_SETUP", "1").strip().lower()
        return setting not in {"0", "false", "no", "off"}
    return False


def _stage_planned_commands(payload: dict[str, Any]) -> list[str]:
    commands = payload.get("commands")
    if isinstance(commands, list) and all(isinstance(command, str) for command in commands):
        return commands
    handoff = payload.get("handoff")
    if isinstance(handoff, dict):
        commands = handoff.get("commands")
        if isinstance(commands, list) and all(isinstance(command, str) for command in commands):
            return commands
    return []


def _execute_stage_plan(
    stage_name: str,
    *,
    defaults: dict[str, str],
    stage_results: dict[str, dict[str, Any]],
    progress: bool,
) -> dict[str, Any] | None:
    if not _stage_should_execute(stage_name):
        return None

    payload = stage_results.get(stage_name) or {}
    commands = _stage_planned_commands(payload)
    if not commands:
        _emit_progress(f"[goose-pipeline][{stage_name}] no planned commands to execute", enabled=progress)
        return {"stage": stage_name, "ok": False, "skipped": True, "reason": "no_commands"}

    needs_docs = bool(payload.get("needs_docs") or (payload.get("handoff") or {}).get("needs_docs"))
    if needs_docs:
        _emit_progress(f"[goose-pipeline][{stage_name}] skipping execution because needs_docs=true", enabled=progress)
        return {"stage": stage_name, "ok": False, "skipped": True, "reason": "needs_docs"}

    detect_handoff = (stage_results.get("detect") or {}).get("handoff", {})
    _emit_progress(f"[goose-pipeline][{stage_name}] executing {len(commands)} planned command(s)", enabled=progress)
    for index, command in enumerate(commands, start=1):
        _emit_progress(f"[goose-pipeline][{stage_name}][plan:{index}] {command}", enabled=progress)

    result = execute_pipeline_stage(
        stage=stage_name,
        workspace_root=defaults["workspace_root"],
        repo_dir=defaults["repo_dir"],
        trace_dir=defaults["trace_dir"],
        install_prefix=str((payload.get("handoff") or {}).get("install_prefix") or defaults["venv_dir"]),
        language=str(detect_handoff.get("language") or defaults["language"]),
        uses_mpi=bool(detect_handoff.get("uses_mpi")),
        uses_hip=bool(detect_handoff.get("uses_hip")),
        build_commands=commands,
        continue_on_failure=False,
    )
    _emit_progress(
        f"[goose-pipeline][{stage_name}] execution ok={result.get('ok', False)} steps={len(result.get('steps', []))}",
        enabled=progress,
    )
    return result


def _goose_provider_summary(env: dict[str, str]) -> dict[str, str]:
    return {
        "GOOSE_PROVIDER": env.get("GOOSE_PROVIDER", "missing") or "missing",
        "GOOSE_MODEL": env.get("GOOSE_MODEL", "missing") or "missing",
        "OPENAI_BASE_URL": env.get("OPENAI_BASE_URL", "missing") or "missing",
        "OPENAI_API_KEY": "set" if env.get("OPENAI_API_KEY") else "missing",
        "OPENAI_MODEL": env.get("OPENAI_MODEL", "missing") or "missing",
    }


def _preflight_goose_runtime(*, base: pathlib.Path, defaults: dict[str, str], env: dict[str, str], progress: bool) -> dict[str, Any]:
    provider = (env.get("GOOSE_PROVIDER") or "").strip().lower()
    model = (env.get("GOOSE_MODEL") or env.get("OPENAI_MODEL") or "").strip()
    if not provider:
        raise RuntimeError(
            "Goose provider is not configured. Set GOOSE_PROVIDER and provider-specific variables before running the pipeline."
        )
    if provider == "openai":
        if not env.get("OPENAI_API_KEY"):
            raise RuntimeError("GOOSE_PROVIDER=openai but OPENAI_API_KEY is missing.")
        if not (env.get("OPENAI_BASE_URL") or env.get("OPENAI_HOST")):
            raise RuntimeError("GOOSE_PROVIDER=openai but OPENAI_BASE_URL or OPENAI_HOST is missing.")
        if not model:
            raise RuntimeError("GOOSE_PROVIDER=openai but no model is configured. Set GOOSE_MODEL or OPENAI_MODEL.")

    launcher = base / "scripts" / "start_goose.sh"
    info_cmd = ["bash", str(launcher), "info"]
    started = time.monotonic()
    result = subprocess.run(
        info_cmd,
        cwd=defaults["workspace_root"],
        env=env,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )
    elapsed = time.monotonic() - started
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    if result.returncode != 0:
        detail = stderr or stdout or f"goose info exited with rc={result.returncode}"
        raise RuntimeError(f"Goose preflight failed before stage 1: {detail}")

    summary = {
        "provider": provider,
        "model": model or "missing",
        "elapsed_seconds": round(elapsed, 2),
        "info_stdout": stdout,
        "info_stderr": stderr,
        "provider_summary": _goose_provider_summary(env),
    }
    _emit_progress(
        f"[goose-pipeline] preflight: provider={summary['provider']}, model={summary['model']}, goose_info_rc=0, elapsed={summary['elapsed_seconds']:.2f}s",
        enabled=progress,
    )
    if stdout:
        first_line = stdout.splitlines()[0]
        _emit_progress(f"[goose-pipeline] preflight_info: {first_line}", enabled=progress)
    return summary


def _run_goose_stage(
    stage_name: str,
    *,
    cmd: list[str],
    instruction_text: str,
    progress: bool,
    timeout_seconds: int,
    cwd: str,
    env: dict[str, str],
) -> tuple[dict[str, Any], float]:
    start = time.monotonic()
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdin=subprocess.PIPE,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    if process.stdin is not None:
        process.stdin.write(instruction_text)
        if not instruction_text.endswith("\n"):
            process.stdin.write("\n")
        process.stdin.close()
        process.stdin = None

    heartbeat_at = start + 5.0
    stderr_chunks: list[str] = []
    stderr_queue: queue.Queue[str | None] = queue.Queue()
    last_stderr_at = start
    diagnostic_emitted = False

    def _stderr_reader() -> None:
        if process.stderr is None:
            stderr_queue.put(None)
            return
        for line in process.stderr:
            stderr_queue.put(line)
        stderr_queue.put(None)

    stderr_thread = threading.Thread(target=_stderr_reader, daemon=True)
    stderr_thread.start()
    stderr_closed = False

    while True:
        rc = process.poll()

        while True:
            try:
                line = stderr_queue.get_nowait()
            except queue.Empty:
                break
            if line is None:
                stderr_closed = True
                break
            clean = line.rstrip("\n")
            stderr_chunks.append(clean)
            last_stderr_at = now if "now" in locals() else time.monotonic()
            _emit_progress(f"[goose-pipeline][{stage_name}][stderr] {clean}", enabled=progress)

        now = time.monotonic()
        if rc is not None:
            break
        if timeout_seconds > 0 and (now - start) >= timeout_seconds:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            raise RuntimeError(
                f"Goose terminal pipeline timed out at {stage_name} after {timeout_seconds}s"
            )
        if now >= heartbeat_at:
            elapsed = now - start
            idle = now - last_stderr_at
            _emit_progress(f"[goose-pipeline][{stage_name}] still running after {elapsed:.1f}s (stderr idle {idle:.1f}s)", enabled=progress)
            if not diagnostic_emitted and idle >= 10.0:
                _emit_progress(
                    f"[goose-pipeline][{stage_name}] no new stderr for {idle:.1f}s; Goose is likely waiting on the model backend or an internal tool turn.",
                    enabled=progress,
                )
                diagnostic_emitted = True
            heartbeat_at = now + 5.0
        time.sleep(0.2)

    stdout = process.stdout.read() if process.stdout is not None else ""
    remaining_stderr = ""
    if process.stderr is not None and not stderr_closed:
        remaining_stderr = process.stderr.read()
    process.wait()
    if not stderr_closed and remaining_stderr:
        for raw_line in remaining_stderr.splitlines():
            stderr_chunks.append(raw_line)
            _emit_progress(f"[goose-pipeline][{stage_name}][stderr] {raw_line}", enabled=progress)
    stderr_thread.join(timeout=1.0)

    stdout = (stdout or "").strip()
    stderr = "\n".join(part for part in stderr_chunks if part).strip()
    elapsed = time.monotonic() - start

    if process.returncode != 0:
        detail = stderr or stdout or f"Goose exited with rc={process.returncode}"
        raise RuntimeError(f"Goose terminal pipeline failed at {stage_name}: {detail}")

    payload = _parse_goose_json_text(stdout)
    if payload is None:
        raise RuntimeError(
            f"Goose terminal pipeline returned non-JSON output at {stage_name}: {stdout or stderr or '<empty>'}"
        )
    return payload, elapsed


def run_terminal_goose_pipeline(
    root: pathlib.Path | None = None,
    *,
    name: str = DEFAULT_PIPELINE_NAME,
    repo_url: str = DEFAULT_PIPELINE_REPO_URL,
    repo_ref: str = DEFAULT_PIPELINE_REPO_REF,
    language: str = DEFAULT_PIPELINE_LANGUAGE,
    workspace_root: str = "",
    repo_dir: str = "",
    venv_dir: str = "",
    trace_dir: str = "",
    post_dir: str = "",
    compacted_trace_dir: str = "",
    analysis_dir: str = "",
    progress: bool = True,
    feedback_db_path: str = "",
    feedback_prompt: bool | None = None,
) -> dict[str, Any]:
    base = root or project_root()
    timeout_seconds = int(os.environ.get("DFTRACER_GOOSE_STAGE_TIMEOUT_SECONDS", str(DEFAULT_GOOSE_STAGE_TIMEOUT_SECONDS)))
    defaults = build_terminal_pipeline_defaults(
        base,
        name=name,
        repo_url=repo_url,
        repo_ref=repo_ref,
        language=language,
        workspace_root=workspace_root,
        repo_dir=repo_dir,
        venv_dir=venv_dir,
        trace_dir=trace_dir,
        post_dir=post_dir,
        compacted_trace_dir=compacted_trace_dir,
        analysis_dir=analysis_dir,
    )
    _require_workspace_containment(defaults)
    stage_env = _normalized_stage_environment(defaults)
    feedback_path = resolve_feedback_db_path(defaults, override=feedback_db_path)
    feedback_db = load_feedback_db(feedback_path, defaults)
    feedback_run_id = ensure_feedback_run(feedback_db, defaults)
    context_path = write_terminal_pipeline_context(
        defaults,
        root=base,
        feedback_context_text=build_feedback_context_text(feedback_db),
    )
    feedback_enabled = feedback_prompt_enabled(feedback_prompt)
    preflight = _preflight_goose_runtime(base=base, defaults=defaults, env=stage_env, progress=progress)
    results: dict[str, Any] = {
        "recipe": "direct-stage-recipes",
        "defaults": defaults,
        "stage_order": list(GOOSE_PIPELINE_STAGE_ORDER),
        "stages": {},
        "stage_inputs": {},
        "executions": {},
        "artifacts": {},
        "context_file": str(context_path),
        "feedback_db": str(feedback_path),
        "feedback_run_id": feedback_run_id,
        "feedback": {},
        "preflight": preflight,
    }

    _emit_progress(f"[goose-pipeline] recipe: {results['recipe']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] context: {results['context_file']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] feedback_db: {results['feedback_db']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] environment: {_redacted_env_summary()}", enabled=progress)
    _emit_progress(f"[goose-pipeline] workspace_root: {defaults['workspace_root']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] repo_dir: {defaults['repo_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] venv_dir: {defaults['venv_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] trace_dir: {defaults['trace_dir']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] repo_attrs_hint: {defaults['repo_attrs_json']}", enabled=progress)
    _emit_progress(f"[goose-pipeline] stage_timeout_seconds: {timeout_seconds}", enabled=progress)
    _emit_progress(f"[goose-pipeline] feedback_prompt_enabled: {feedback_enabled}", enabled=progress)
    _emit_progress(f"[goose-pipeline] stage_cwd: {defaults['workspace_root']}", enabled=progress)

    for index, stage_name in enumerate(GOOSE_PIPELINE_STAGE_ORDER, start=1):
        stage_feedback_context = build_feedback_context(feedback_db, stage_name)
        _emit_progress(
            f"[goose-pipeline] stage {index}/{len(GOOSE_PIPELINE_STAGE_ORDER)} starting: {stage_name}",
            enabled=progress,
        )
        cmd, params, stage_input = build_goose_stage_command(
            stage_name,
            defaults=defaults,
            pipeline_context_file=context_path,
            stage_results=results["stages"],
            root=base,
            feedback_context=stage_feedback_context,
        )
        results["stage_inputs"][stage_name] = stage_input
        _emit_progress(f"[goose-pipeline][{stage_name}] command: {shlex.join(cmd)}", enabled=progress)
        _emit_progress(
            (
                f"[goose-pipeline][{stage_name}] params: "
                f"repo_url={params['repo_url']}, repo_ref={params['repo_ref']}, language={params['language']}, "
                f"repo_dir={params['repo_dir']}, trace_dir={params['trace_dir']}"
            ),
            enabled=progress,
        )
        if stage_name == "detect":
            started = time.monotonic()
            payload = _local_detect_stage_payload(defaults)
            elapsed = time.monotonic() - started
            _emit_progress("[goose-pipeline][detect] using local heuristic fast-path", enabled=progress)
        else:
            payload, elapsed = _run_goose_stage(
                stage_name,
                cmd=cmd,
                instruction_text=goose_stage_instruction_text(stage_name),
                progress=progress,
                timeout_seconds=timeout_seconds,
                cwd=defaults["workspace_root"],
                env=stage_env,
            )
        normalized = _validate_stage_payload(stage_name, payload)
        results["stages"][stage_name] = normalized
        summary = str(normalized.get("summary") or "").strip() or "<no summary>"
        _emit_progress(
            f"[goose-pipeline] stage {index}/{len(GOOSE_PIPELINE_STAGE_ORDER)} complete: {stage_name} ({elapsed:.1f}s)",
            enabled=progress,
        )
        _emit_progress(f"[goose-pipeline][{stage_name}] summary: {summary}", enabled=progress)
        planned_commands = _stage_planned_commands(normalized)
        for command_index, command in enumerate(planned_commands, start=1):
            _emit_progress(f"[goose-pipeline][{stage_name}][planned:{command_index}] {command}", enabled=progress)
        execution_result = _execute_stage_plan(
            stage_name,
            defaults=defaults,
            stage_results=results["stages"],
            progress=progress,
        )
        if execution_result is not None:
            results["executions"][stage_name] = execution_result
        feedback_response = collect_stage_feedback(
            stage_name,
            normalized,
            stage_feedback_context,
            enabled=feedback_enabled,
        )
        feedback_entry = append_stage_feedback(
            feedback_db,
            run_id=feedback_run_id,
            stage_name=stage_name,
            payload=normalized,
            feedback_response=feedback_response,
        )
        save_feedback_db(feedback_path, feedback_db)
        results["feedback"][stage_name] = feedback_entry
        artifact_paths = _write_stage_artifacts(
            stage_name,
            defaults=defaults,
            stage_input=stage_input,
            stage_result=normalized,
            execution_result=execution_result,
            feedback_entry=feedback_entry,
        )
        results["artifacts"][stage_name] = artifact_paths
        _emit_progress(f"[goose-pipeline][{stage_name}] artifacts: {artifact_paths['stage_dir']}", enabled=progress)
        if execution_result is not None and not execution_result.get("ok", False):
            execution_log = artifact_paths.get("execution_log", "")
            raise RuntimeError(
                f"Goose terminal pipeline execution failed at {stage_name}. See execution log: {execution_log}"
            )

    _emit_progress("[goose-pipeline] all stages complete", enabled=progress)
    return results