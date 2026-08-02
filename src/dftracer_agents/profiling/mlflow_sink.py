"""Mirror a :class:`~.aggregate.Profile` into MLflow, and into the session.

Shape of the data in MLflow:

* one **parent run** per dftracer pipeline run (``run_name = run_id``), holding
  whole-pipeline totals;
* one **nested run** per pipeline step (``STEP 3: dftracer-annotator``), holding
  that step's execution time, attempt/retry counts, tokens, cost, and tool timings.

The sink is *upsert*-shaped: :meth:`flush` is called repeatedly on a timer and
re-logs current totals. MLflow keeps every logged value as a point in that
metric's history, so a running step shows a live cost curve and its last point is
the final value. Nothing is deleted or rewritten.

A step's nested run is terminated when its last attempt ends — ``FINISHED`` on
success, ``FAILED`` otherwise — so a crashed pipeline leaves the failing step
marked rather than dangling.

MLflow is an optional dependency (``pip install 'dftracer-agents[profile]'``).
Without it every method is a no-op, and the collector still writes the session's
``performance/`` files. Telemetry capture must never depend on the reporting
backend being installed.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from .aggregate import Profile, MAIN_STEP

#: MLflow rejects metric/param/tag keys outside this character set.
_BAD_KEY = re.compile(r"[^0-9a-zA-Z_\-./ ]")

#: Stay well under MLflow's param-value limit.
_MAX_PARAM = 500


def _key(name: str) -> str:
    return _BAD_KEY.sub("_", name)[:250]


def _slug(name: str) -> str:
    return re.sub(r"[^0-9a-zA-Z]+", "-", name).strip("-").lower() or "step"


class MlflowSink:
    """Best-effort MLflow writer. Never raises into the collector."""

    def __init__(self, tracking_uri: str, experiment: str = "dftracer-agents") -> None:
        self.tracking_uri = tracking_uri
        self.experiment = experiment
        self.enabled = False
        self.error = ""
        self._client = None
        self._exp_id: Optional[str] = None
        self._parent_id: Optional[str] = None
        self._step_ids: Dict[str, str] = {}
        #: step key -> the MLflow run status last written for it. A step whose
        #: attempt failed and is then retried must go FAILED -> RUNNING -> FINISHED.
        self._step_status: Dict[str, str] = {}
        self._flushes = 0

        try:
            from mlflow.tracking import MlflowClient
        except ImportError:
            self.error = ("mlflow not installed; profile still written to the session's "
                          "performance/ dir (pip install 'dftracer-agents[profile]')")
            return

        # MLflow's HTTP client defaults to a 120s timeout and 7 retries with
        # exponential backoff (~4 minutes worst case) — tuned for a client
        # tolerating a rate-limited SaaS backend, not for a profiling sidecar
        # that must never block the collector's own startup/flush loop when
        # the local mlflow server is simply down. setdefault so an operator's
        # own explicit configuration always wins.
        os.environ.setdefault("MLFLOW_HTTP_REQUEST_TIMEOUT", "3")
        os.environ.setdefault("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "1")

        try:
            self._client = MlflowClient(tracking_uri=tracking_uri)
            exp = self._client.get_experiment_by_name(experiment)
            self._exp_id = (exp.experiment_id if exp
                            else self._client.create_experiment(experiment))
            self.enabled = True
        except Exception as exc:                       # server down, bad URI, ...
            self.error = f"{type(exc).__name__}: {exc}"

    # -- runs --------------------------------------------------------------

    @property
    def parent_run_id(self) -> Optional[str]:
        return self._parent_id

    def start(self, profile: Profile, tags: Optional[Dict[str, str]] = None) -> Optional[str]:
        """Create the parent run for *profile*. Idempotent."""
        if not self.enabled or self._parent_id:
            return self._parent_id
        try:
            base = {"dftracer.run_id": profile.run_id or "unbound",
                    "mlflow.runName": profile.run_id or "unbound"}
            base.update({_key(k): str(v)[:_MAX_PARAM] for k, v in (tags or {}).items()})
            self._parent_id = self._client.create_run(self._exp_id, tags=base).info.run_id
            return self._parent_id
        except Exception as exc:
            self.error = f"start: {exc}"
            return None

    def rename(self, run_id: str, tags: Optional[Dict[str, str]] = None) -> None:
        """Point an already-created parent run at *run_id*.

        The collector starts receiving telemetry — and flushing it — before the
        agent has a session to bind to, so the parent run is usually created
        while still ``unbound``. Binding must correct the run's name in place
        rather than orphan it and open a second one, or the run that holds the
        pre-session orchestration cost is lost from the report.
        """
        if not self.enabled or not self._parent_id:
            return
        try:
            self._client.set_tag(self._parent_id, "mlflow.runName", run_id)
            self._client.set_tag(self._parent_id, "dftracer.run_id", run_id)
            for k, v in (tags or {}).items():
                self._client.set_tag(self._parent_id, _key(k), str(v)[:_MAX_PARAM])
        except Exception as exc:
            self.error = f"rename: {exc}"

    def _step_run(self, profile: Profile, step) -> Optional[str]:
        if step.key in self._step_ids:
            return self._step_ids[step.key]
        try:
            rid = self._client.create_run(self._exp_id, tags={
                "mlflow.runName": step.key,
                "mlflow.parentRunId": self._parent_id or "",
                "dftracer.run_id": profile.run_id or "unbound",
                "dftracer.step_index": str(step.index),
                "dftracer.agent": step.agent or "",
            }).info.run_id
            self._step_ids[step.key] = rid
            if step.notes:
                self._client.log_param(rid, "notes", step.notes[:_MAX_PARAM])
            if step.agent:
                self._client.log_param(rid, "agent", step.agent)
            return rid
        except Exception as exc:
            self.error = f"step_run: {exc}"
            return None

    # -- writing -----------------------------------------------------------

    #: Scalar keys copied verbatim from a step/total dict into MLflow metrics.
    _METRICS = ("tokens_input", "tokens_output", "tokens_cache_read",
                "tokens_cache_creation", "tokens_total", "cost_usd", "api_calls",
                "api_errors", "api_duration_ms", "tool_calls", "tool_failures",
                "tool_duration_ms", "mcp_tool_calls", "compactions", "prompts",
                "tries", "retries", "failed_attempts", "successful_attempts",
                "exec_s", "wall_s")

    def _log_agg(self, run_id: str, d: Dict[str, Any], step: int) -> None:
        """Log a step's whole metric set in ONE request.

        ``log_metric`` is one HTTP round-trip per metric; at ~22 metrics per run
        per flush, across a parent and every step, that is hundreds of round-trips
        and seconds of latency. ``log_batch`` sends them together.
        """
        from mlflow.entities import Metric

        ts = int(time.time() * 1000)
        batch = [Metric(m, float(d[m]), ts, step) for m in self._METRICS
                 if isinstance(d.get(m), (int, float)) and not isinstance(d.get(m), bool)]
        # Cost per unit of work: the number that says whether a step earns what
        # it costs. Guard the divide — a step can end with zero tool calls.
        if d.get("tool_calls"):
            batch.append(Metric("cost_usd_per_tool_call",
                                float(d["cost_usd"]) / d["tool_calls"], ts, step))
        if batch:
            self._client.log_batch(run_id, metrics=batch)

    def flush(self, profile: Profile) -> Dict[str, Any]:
        """Re-log current totals for the parent run and every step run."""
        if not self.enabled:
            return {"enabled": False, "error": self.error}
        snap = profile.snapshot()
        if not self.start(profile):
            return {"enabled": False, "error": self.error}

        try:
            n = self._flushes
            parent = {**snap["totals"], **snap["attempts"],
                      "exec_s": snap["exec_s"], "wall_s": snap["wall_s"]}
            self._log_agg(self._parent_id, parent, n)
            self._client.log_metric(self._parent_id, "cost_reconciliation_delta",
                                    snap["cost_reconciliation"]["delta"], step=n)

            for step, sd in zip(profile.steps, snap["steps"]):
                if step.key == MAIN_STEP and not (sd["api_calls"] or sd["tool_calls"]):
                    continue
                rid = self._step_run(profile, step)
                if not rid:
                    continue
                self._log_agg(rid, sd, n)
                self._sync_status(rid, step)

            self._flushes = n + 1
            return {"enabled": True, "parent_run_id": self._parent_id,
                    "flushes": self._flushes, "step_runs": len(self._step_ids)}
        except Exception as exc:
            self.error = f"flush: {exc}"
            return {"enabled": True, "error": self.error}

    def _sync_status(self, run_id: str, step) -> None:
        """Drive the step's run status to match the step's current state.

        Not a one-shot "terminate when done": a step whose first attempt fails is
        closed FAILED, then *reopened* by the retry. The run must go back to
        RUNNING and end FINISHED, or the report and the MLflow UI disagree about
        whether the pipeline succeeded. Writes only on change.
        """
        from .aggregate import is_ok
        want = "RUNNING" if step.open else ("FINISHED" if is_ok(step.status) else "FAILED")
        if self._step_status.get(step.key) == want:
            return
        try:
            self._client.update_run(run_id, status=want)
            self._step_status[step.key] = want
        except Exception:
            pass

    def close(self, profile: Profile, artifacts_dir: Optional[Path] = None,
              status: str = "FINISHED") -> Dict[str, Any]:
        """Final flush, attach the session's ``performance/`` dir, end the run."""
        if not self.enabled:
            return {"enabled": False, "error": self.error}
        res = self.flush(profile)
        try:
            if artifacts_dir and Path(artifacts_dir).is_dir():
                self._client.log_artifacts(self._parent_id, str(artifacts_dir), "performance")
            self._client.set_terminated(self._parent_id, status)
        except Exception as exc:
            self.error = f"close: {exc}"
        return res


def default_tracking_uri(workspaces_root: Path) -> str:
    """Where MLflow lives when the stack script has not said otherwise.

    ``MLFLOW_TRACKING_URI`` wins, so one collector can point at a shared server.
    Otherwise fall back to the same SQLite file the stack script serves from,
    which keeps a bare ``dftracer-profile-collector`` usable with no daemon.
    """
    env = os.environ.get("MLFLOW_TRACKING_URI")
    if env:
        return env
    db = workspaces_root / "_mlflow" / "mlflow.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{db}"


# --------------------------------------------------------------------------
# Session-local artefacts: performance/
# --------------------------------------------------------------------------

def write_step_files(snapshot: Dict[str, Any], perf_dir: Path) -> int:
    """Write one JSON file per step under ``performance/steps/``.

    Rewritten on every flush, so a step's file is a live view while it runs and
    its final state once it ends. Named ``<index>-<slug>.json`` so the directory
    lists in pipeline order.
    """
    steps_dir = perf_dir / "steps"
    steps_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    for s in snapshot["steps"]:
        if s["key"] == MAIN_STEP and not (s["api_calls"] or s["tool_calls"]):
            continue
        path = steps_dir / f"{s['index']:02d}-{_slug(s['key'])}.json"
        path.write_text(json.dumps(
            {"run_id": snapshot["run_id"], **s}, indent=2) + "\n")
        written += 1
    return written


def write_summary_json(snapshot: Dict[str, Any], perf_dir: Path) -> Path:
    perf_dir.mkdir(parents=True, exist_ok=True)
    path = perf_dir / "summary.json"
    path.write_text(json.dumps(snapshot, indent=2) + "\n")
    return path


def write_mlflow_pointer(perf_dir: Path, sink: "MlflowSink", ui_url: str = "") -> Path:
    """Record where this run's MLflow data landed, so the report can link to it."""
    perf_dir.mkdir(parents=True, exist_ok=True)
    path = perf_dir / "mlflow.json"
    path.write_text(json.dumps({
        "enabled": sink.enabled,
        "tracking_uri": sink.tracking_uri,
        "experiment": sink.experiment,
        "parent_run_id": sink.parent_run_id,
        "ui_url": (f"{ui_url.rstrip('/')}/#/experiments/{sink._exp_id}/runs/{sink.parent_run_id}"
                   if ui_url and sink.parent_run_id else ""),
        "error": sink.error,
    }, indent=2) + "\n")
    return path


def _fmt_status(s: Dict[str, Any]) -> str:
    icon = {"ok": "ok", "running": "running", "superseded": "superseded"}
    return icon.get(s["status"], s["status"])


def write_performance_report(snapshot: Dict[str, Any], perf_dir: Path,
                             mlflow_url: str = "") -> Path:
    """Render ``performance/performance_report.md``.

    The report answers three questions in order: what did the pipeline cost, which
    step cost it, and which step wasted it (retries, failed attempts, slow tools).
    """
    t, a = snapshot["totals"], snapshot["attempts"]
    steps = [s for s in snapshot["steps"]
             if s["key"] != MAIN_STEP or s["api_calls"] or s["tool_calls"]]

    lines = [
        f"# Pipeline performance report — {snapshot['run_id'] or '(unbound)'}",
        "",
        "## Summary",
        "",
        f"- **Cost:** ${t['cost_usd']:.4f} across {t['api_calls']} API calls",
        f"- **Tokens:** {t['tokens_total']:,} total — {t['tokens_input']:,} in, "
        f"{t['tokens_output']:,} out, {t['tokens_cache_read']:,} cache-read, "
        f"{t['tokens_cache_creation']:,} cache-write",
        f"- **Time:** {snapshot['wall_s']:.1f} s wall, {snapshot['exec_s']:.1f} s inside steps",
        f"- **Steps:** {a['steps']} ({a['steps_succeeded']} succeeded, "
        f"{a['steps_failed']} failed, {a['steps_running']} running)",
        f"- **Attempts:** {a['tries']} tries, {a['retries']} retries, "
        f"{a['failed_attempts']} failed",
        f"- **Tools:** {t['tool_calls']} calls ({t['mcp_tool_calls']} MCP), "
        f"{t['tool_failures']} failed, {t['tool_duration_ms'] / 1000:.1f} s total",
        f"- **API errors:** {t['api_errors']} · **Compactions:** {t['compactions']}",
    ]
    if mlflow_url:
        lines.append(f"- **MLflow:** {mlflow_url}")

    lines += [
        "",
        "## Per-step",
        "",
        "| # | Step | Agent | Status | Tries | Retries | Failed | Exec (s) | Wall (s) "
        "| Cost (USD) | Tokens | API | Tools |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for s in steps:
        lines.append(
            f"| {s['index']} | {s['key']} | {s['agent'] or '—'} | {_fmt_status(s)} | "
            f"{s['tries']} | {s['retries']} | {s['failed_attempts']} | "
            f"{s['exec_s']:.1f} | {s['wall_s']:.1f} | {s['cost_usd']:.4f} | "
            f"{s['tokens_total']:,} | {s['api_calls']} | {s['tool_calls']} |")

    # Retries are the pipeline's rework. Call them out with their errors, since
    # that is the actionable part of the report.
    retried = [s for s in steps if s["retries"] or s["failed_attempts"]]
    if retried:
        lines += ["", "## Rework (retries and failed attempts)", ""]
        for s in retried:
            lines.append(f"### {s['key']}")
            lines.append("")
            lines.append("| Attempt | Status | Duration (s) | Error |")
            lines.append("|---:|---|---:|---|")
            for at in s["attempts"]:
                err = (at["error"] or "").replace("\n", " ").replace("|", "\\|")[:160]
                lines.append(f"| {at['number']} | {at['status']} | "
                             f"{at['duration_s']:.1f} | {err or '—'} |")
            lines.append("")

    top = sorted(t["tools"].items(), key=lambda kv: -kv[1]["ms"])[:12]
    if top:
        lines += ["", "## Slowest tools", "",
                  "| Tool | Calls | Total (s) | Mean (ms) | Failures |",
                  "|---|---:|---:|---:|---:|"]
        for name, v in top:
            lines.append(f"| {name} | {int(v['calls'])} | {v['ms'] / 1000:.1f} | "
                         f"{v['ms'] / max(v['calls'], 1):.0f} | {int(v['failures'])} |")

    if t["models"]:
        lines += ["", "## Models", "", "| Model | API calls |", "|---|---:|"]
        for m, c in sorted(t["models"].items(), key=lambda kv: -kv[1]):
            lines.append(f"| {m} | {c} |")

    rec = snapshot["cost_reconciliation"]
    if abs(rec["delta"]) > 0.001:
        lines += ["", f"> **Note:** cost from the OTEL counters (${rec['from_metrics']:.4f}) "
                      f"differs from the sum of `api_request` events "
                      f"(${rec['from_events']:.4f}) by ${rec['delta']:.4f}. Some log events "
                      f"were dropped or are still in flight; per-step attribution may "
                      f"under-count by that amount."]

    perf_dir.mkdir(parents=True, exist_ok=True)
    path = perf_dir / "performance_report.md"
    path.write_text("\n".join(lines) + "\n")
    return path
