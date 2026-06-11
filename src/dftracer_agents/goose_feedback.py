from __future__ import annotations

import json
import os
import pathlib
import sys
import uuid
from datetime import datetime, timezone
from typing import Any

DEFAULT_FEEDBACK_DB_FILENAME = "goose_feedback_db.json"
_MAX_STAGE_MEMORY = 8


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def resolve_feedback_db_path(defaults: dict[str, str], override: str = "") -> pathlib.Path:
    if override:
        return pathlib.Path(override).expanduser()
    return pathlib.Path(defaults["workspace_root"]) / "artifacts" / "feedback" / DEFAULT_FEEDBACK_DB_FILENAME


def _empty_feedback_db(defaults: dict[str, str]) -> dict[str, Any]:
    now = _now_iso()
    return {
        "schema_version": 1,
        "app_name": defaults.get("name", ""),
        "repo_url": defaults.get("repo_url", ""),
        "repo_ref": defaults.get("repo_ref", ""),
        "created_at": now,
        "updated_at": now,
        "runs": [],
        "stage_memory": {},
    }


def load_feedback_db(path: pathlib.Path, defaults: dict[str, str]) -> dict[str, Any]:
    if not path.exists():
        return _empty_feedback_db(defaults)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _empty_feedback_db(defaults)
    if not isinstance(payload, dict):
        return _empty_feedback_db(defaults)
    payload.setdefault("schema_version", 1)
    payload.setdefault("app_name", defaults.get("name", ""))
    payload.setdefault("repo_url", defaults.get("repo_url", ""))
    payload.setdefault("repo_ref", defaults.get("repo_ref", ""))
    payload.setdefault("created_at", _now_iso())
    payload["updated_at"] = _now_iso()
    payload.setdefault("runs", [])
    payload.setdefault("stage_memory", {})
    return payload


def save_feedback_db(path: pathlib.Path, payload: dict[str, Any]) -> None:
    payload["updated_at"] = _now_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def ensure_feedback_run(db: dict[str, Any], defaults: dict[str, str]) -> str:
    run_id = os.environ.get("DFTRACER_GOOSE_RUN_ID", "").strip() or uuid.uuid4().hex[:12]
    runs = db.setdefault("runs", [])
    now = _now_iso()
    if not any(run.get("run_id") == run_id for run in runs):
        runs.append(
            {
                "run_id": run_id,
                "started_at": now,
                "updated_at": now,
                "name": defaults.get("name", ""),
                "repo_url": defaults.get("repo_url", ""),
                "repo_ref": defaults.get("repo_ref", ""),
                "workspace_root": defaults.get("workspace_root", ""),
                "repo_dir": defaults.get("repo_dir", ""),
                "stages": {},
            }
        )
    return run_id


def _compact_stage_memory(entry: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": entry.get("run_id", ""),
        "recorded_at": entry.get("recorded_at", ""),
        "summary": entry.get("summary", ""),
        "what_worked": entry.get("what_worked", ""),
        "what_did_not_work": entry.get("what_did_not_work", ""),
        "next_time": entry.get("next_time", ""),
        "rating": entry.get("rating", "neutral"),
    }


def build_feedback_context(db: dict[str, Any], stage_name: str, limit: int = 3) -> dict[str, Any]:
    memory = list(db.get("stage_memory", {}).get(stage_name, []))
    compact = [_compact_stage_memory(item) for item in memory[-limit:]]
    return {
        "stage": stage_name,
        "has_feedback": bool(compact),
        "prior_feedback": compact,
        "instruction": (
            "Treat prior_feedback as strong operator guidance from earlier pipeline runs. "
            "Repeat what worked when it still applies, and avoid approaches marked as missing or incorrect."
        ),
    }


def build_feedback_context_text(db: dict[str, Any], limit_per_stage: int = 2) -> str:
    lines = ["Prior run feedback memory:"]
    stage_memory = db.get("stage_memory", {})
    any_feedback = False
    for stage_name in sorted(stage_memory):
        entries = [
            entry
            for entry in stage_memory.get(stage_name, [])
            if any((entry.get("what_worked"), entry.get("what_did_not_work"), entry.get("next_time")))
        ]
        if not entries:
            continue
        any_feedback = True
        lines.append(f"- {stage_name}:")
        for entry in entries[-limit_per_stage:]:
            summary = entry.get("summary", "")
            worked = entry.get("what_worked", "")
            missed = entry.get("what_did_not_work", "")
            next_time = entry.get("next_time", "")
            lines.append(f"  summary: {summary}")
            if worked:
                lines.append(f"  worked: {worked}")
            if missed:
                lines.append(f"  not_good: {missed}")
            if next_time:
                lines.append(f"  next_time: {next_time}")
    if not any_feedback:
        lines.append("- No prior user feedback recorded yet.")
    return "\n".join(lines)


def build_feedback_prompt(stage_name: str, payload: dict[str, Any], feedback_context: dict[str, Any]) -> str:
    summary = str(payload.get("summary") or "").strip() or "<no summary>"
    prior = feedback_context.get("prior_feedback", [])
    prior_text = "none"
    if prior:
        prior_lines = []
        for entry in prior:
            line = f"- {entry.get('recorded_at', '')}: {entry.get('summary', '')}"
            if entry.get("what_did_not_work"):
                line += f" | avoid: {entry['what_did_not_work']}"
            if entry.get("next_time"):
                line += f" | next: {entry['next_time']}"
            prior_lines.append(line)
        prior_text = "\n".join(prior_lines)
    return (
        f"Stage '{stage_name}' completed.\n"
        f"Stage summary: {summary}\n"
        f"Prior feedback for this stage:\n{prior_text}\n\n"
        "Please review this stage and answer:\n"
        "1. What was good?\n"
        "2. What was not good or missing?\n"
        "3. What should the next run do differently?\n"
        "4. Optional rating: good / mixed / bad"
    )


def feedback_prompt_enabled(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    raw = os.environ.get("DFTRACER_GOOSE_FEEDBACK_PROMPT", "auto").strip().lower()
    if raw in {"0", "false", "no", "off"}:
        return False
    if raw in {"1", "true", "yes", "on"}:
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def collect_stage_feedback(
    stage_name: str,
    payload: dict[str, Any],
    feedback_context: dict[str, Any],
    *,
    enabled: bool,
) -> dict[str, Any]:
    prompt_text = build_feedback_prompt(stage_name, payload, feedback_context)
    response: dict[str, Any] = {
        "prompt": prompt_text,
        "prompted": enabled,
        "what_worked": "",
        "what_did_not_work": "",
        "next_time": "",
        "rating": "neutral",
    }
    if not enabled:
        response["status"] = "prompt_skipped"
        return response

    print(f"\n[goose-feedback][{stage_name}] review", file=sys.stderr, flush=True)
    print(prompt_text, file=sys.stderr, flush=True)
    response["what_worked"] = input("What was good? ").strip()
    response["what_did_not_work"] = input("What was not good or missing? ").strip()
    response["next_time"] = input("What should the next run do differently? ").strip()
    rating = input("Rating [good/mixed/bad, blank=mixed]: ").strip().lower()
    response["rating"] = rating or "mixed"
    response["status"] = "recorded" if any(
        (response["what_worked"], response["what_did_not_work"], response["next_time"])
    ) else "empty"
    return response


def append_stage_feedback(
    db: dict[str, Any],
    *,
    run_id: str,
    stage_name: str,
    payload: dict[str, Any],
    feedback_response: dict[str, Any],
) -> dict[str, Any]:
    entry = {
        "run_id": run_id,
        "stage": stage_name,
        "recorded_at": _now_iso(),
        "summary": str(payload.get("summary") or "").strip(),
        "what_worked": str(feedback_response.get("what_worked") or "").strip(),
        "what_did_not_work": str(feedback_response.get("what_did_not_work") or "").strip(),
        "next_time": str(feedback_response.get("next_time") or "").strip(),
        "rating": str(feedback_response.get("rating") or "mixed").strip() or "mixed",
        "status": str(feedback_response.get("status") or "recorded"),
        "prompt": str(feedback_response.get("prompt") or ""),
    }
    has_user_guidance = any((entry["what_worked"], entry["what_did_not_work"], entry["next_time"]))
    if has_user_guidance:
        stage_memory = db.setdefault("stage_memory", {}).setdefault(stage_name, [])
        stage_memory.append(entry)
        if len(stage_memory) > _MAX_STAGE_MEMORY:
            del stage_memory[:-_MAX_STAGE_MEMORY]

    for run in db.setdefault("runs", []):
        if run.get("run_id") != run_id:
            continue
        run.setdefault("stages", {})[stage_name] = {
            "summary": entry["summary"],
            "feedback": {
                "what_worked": entry["what_worked"],
                "what_did_not_work": entry["what_did_not_work"],
                "next_time": entry["next_time"],
                "rating": entry["rating"],
                "status": entry["status"],
            },
        }
        run["updated_at"] = entry["recorded_at"]
        break
    return entry
