#!/usr/bin/env python3
"""
DFTracer MCP Server — stdio (default) or HTTP transport for Goose and other
MCP clients.

Exposes dftracer_utils and dfanalyzer tools over the Model Context Protocol
using FastMCP's stdio transport (default) or streamable-HTTP.

Usage:
    dftracer-mcp-server                          # stdio mode (default)
    dftracer-mcp-server --transport http         # HTTP on 0.0.0.0:5000
    dftracer-mcp-server --transport http --port 8080
    dftracer-mcp-server --service utils
    dftracer-mcp-server --service analyzer
    dftracer-mcp-server --service both

Claude Code config (.claude/settings.json) for stdio:
    {
      "mcpServers": {
        "dftracer": {
          "command": "dftracer-mcp-server",
          "args": ["--service", "both"]
        }
      }
    }

Claude Code config (.claude/settings.json) for HTTP:
    {
      "mcpServers": {
        "dftracer": { "url": "http://localhost:5000/mcp" }
      }
    }

Goose config (~/.config/goose/config.yaml) for HTTP:
    extensions:
      dftracer:
        type: streamable_http
        uri: http://localhost:5000/mcp
        enabled: true
"""
from __future__ import annotations

import argparse
import asyncio
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _runtime_dir() -> Path:
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "unknown"
    return Path("/tmp") / user / "dftracer-agents"


def _pid_file() -> Path:
    return _runtime_dir() / "dftracer-mcp-server.pid"


def _stdout_log_file() -> Path:
    return _runtime_dir() / "dftracer-mcp-server.out.log"


def _stderr_log_file() -> Path:
    return _runtime_dir() / "dftracer-mcp-server.err.log"


def _new_server(name: str):
    from fastmcp import FastMCP

    return FastMCP(name)


def _ensure_runtime_dir() -> Path:
    runtime = _runtime_dir()
    runtime.mkdir(parents=True, exist_ok=True)
    return runtime


def _read_pid() -> int | None:
    pid_path = _pid_file()
    if not pid_path.exists():
        return None
    try:
        return int(pid_path.read_text().strip())
    except (OSError, ValueError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _stop_pid(pid: int, timeout_s: float = 3.0) -> bool:
    if not _pid_alive(pid):
        return True
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)

    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False

    time.sleep(0.1)
    return not _pid_alive(pid)


def _stop_existing_if_any() -> None:
    pid = _read_pid()
    if pid is None:
        return
    pid_path = _pid_file()
    if _stop_pid(pid):
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[daemon] Stopped previous server PID {pid}", file=sys.stderr)
    else:
        raise RuntimeError(f"Could not stop existing server PID {pid}")


def _write_pid(pid: int) -> None:
    _ensure_runtime_dir()
    _pid_file().write_text(f"{pid}\n")


def _build_child_argv(args: argparse.Namespace) -> list[str]:
    argv = [
        sys.executable,
        "-m",
        "dftracer_agents.mcp_server",
        "--_child-run",
        "--service",
        args.service,
        "--transport",
        args.transport,
        "--host",
        args.host,
        "--port",
        str(args.port),
        "--path",
        args.path,
    ]
    if args.skip_setup:
        argv.append("--skip-setup")
    if args.force_setup:
        argv.append("--force-setup")
    if args.skills_target:
        argv.extend(["--skills-target", args.skills_target])
    return argv


def _run_startup_setup(args: argparse.Namespace) -> None:
    if args.skip_setup:
        return

    from pathlib import Path as _Path
    from dftracer_agents.bootstrap import ensure_workspace_setup
    from dftracer_agents.harness_models import prepare_startup_configuration, summarize_harness_models
    from dftracer_agents.skills import ensure_setup, resolve_default_target
    from dftracer_agents.agents import ensure_agents_setup
    from dftracer_agents.mcp_tools.tools.papers.local_library_service import ensure_resources_setup

    target_root = (
        _Path(args.skills_target).expanduser().resolve()
        if args.skills_target
        else resolve_default_target()
    )
    model_path, reused = prepare_startup_configuration(target_root=target_root)
    if reused:
        print(f"[setup] Reusing previous configured system: {model_path}", file=sys.stderr)
    else:
        print(f"[setup] Interactive harness selection saved: {model_path}", file=sys.stderr)

    # Install BOTH the skills (into .claude/skills/) and the pipeline
    # subagents (into .claude/agents/) for the same target. Always
    # report where each went and what happened, so a silent
    # "already_done" no-op is never mistaken for "setup didn't run".
    for label, fn in (("Skills", ensure_setup), ("Agents", ensure_agents_setup),
                      ("Workspace", ensure_workspace_setup), ("Resources", ensure_resources_setup)):
        result = fn(target_root=target_root, force=args.force_setup)
        status = result.get("status")
        target = result.get("target", str(target_root))
        if status == "installed":
            print(f"[setup] {label} installed to {target}", file=sys.stderr)
        elif status == "already_done":
            print(
                f"[setup] {label} already up to date at {target} "
                f"(use --force-setup to re-link)",
                file=sys.stderr,
            )
        else:
            print(f"[setup] {label} setup status={status} target={target}", file=sys.stderr)

        if label == "Workspace":
            replaced = [
                item.get("path")
                for item in result.get("instructions", [])
                if item.get("status") == "replaced"
            ]
            if replaced:
                print(
                    f"[setup] Workspace override confirmed: replaced {len(replaced)} file(s)",
                    file=sys.stderr,
                )
                for path in replaced:
                    print(f"[setup]   override -> {path}", file=sys.stderr)

    for line in summarize_harness_models(target_root=target_root):
        print(line, file=sys.stderr)


def _daemon_start(args: argparse.Namespace) -> int:
    _ensure_runtime_dir()
    _stop_existing_if_any()

    _run_startup_setup(args)

    stdout_path = _stdout_log_file()
    stderr_path = _stderr_log_file()
    with stdout_path.open("a", encoding="utf-8") as stdout_f, stderr_path.open("a", encoding="utf-8") as stderr_f:
        proc = subprocess.Popen(
            _build_child_argv(argparse.Namespace(**{**vars(args), "skip_setup": True})),
            stdin=subprocess.DEVNULL,
            stdout=stdout_f,
            stderr=stderr_f,
            cwd=os.getcwd(),
            close_fds=True,
            start_new_session=True,
        )

    _write_pid(proc.pid)
    print(f"[daemon] Started dftracer-mcp-server PID {proc.pid}")
    print(f"[daemon] PID file: {_pid_file()}")
    print(f"[daemon] stdout log: {stdout_path}")
    print(f"[daemon] stderr log: {stderr_path}")
    return 0


def _daemon_stop() -> int:
    pid = _read_pid()
    pid_path = _pid_file()
    if pid is None:
        print("[daemon] Not running (no PID file)")
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        return 0

    if _stop_pid(pid):
        try:
            pid_path.unlink(missing_ok=True)
        except OSError:
            pass
        print(f"[daemon] Stopped PID {pid}")
        return 0

    print(f"[daemon] Failed to stop PID {pid}", file=sys.stderr)
    return 1


def _daemon_status() -> int:
    pid = _read_pid()
    pid_path = _pid_file()
    if pid is None:
        print("[daemon] stopped")
        return 0
    if _pid_alive(pid):
        print(f"[daemon] running (pid={pid})")
        print(f"[daemon] PID file: {pid_path}")
        return 0
    print(f"[daemon] stale PID file (pid={pid})")
    try:
        pid_path.unlink(missing_ok=True)
    except OSError:
        pass
    return 1


# ---------------------------------------------------------------------------
# Server builders
# ---------------------------------------------------------------------------

def _build_utils_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import dftracer_utils_service

    service = dftracer_utils_service.DftracerUtilsService()

    server = _new_server("DFTracerUtils")
    for sub_name in (
        "core_subservice",
        "analysis_subservice",
        "query_subservice",
        "utility_subservice",
        "dlio_subservice",
        "synthetic_subservice",
        "mpi_subservice",
    ):
        sub = getattr(service, sub_name, None)
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            server.add_tool(tool)
    return server


def _build_analyzer_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import dfanalyzer_service

    service = dfanalyzer_service.DFAnalyzerService()

    server = _new_server("DFAnalyzer")
    for tool in asyncio.run(service.analyzer_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_plot_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import dftracer_plot_service

    service = dftracer_plot_service.DFTracerPlotService()

    server = _new_server("DFTracerPlot")
    for tool in asyncio.run(service.plot_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_docs_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import docs_service

    service = docs_service.DFTracerDocsService()

    server = _new_server("DFTracerDocs")
    for tool in asyncio.run(service.docs_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_skills_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import skills_service

    service = skills_service.DFTracerSkillsService()

    server = _new_server("DFTracerSkills")
    for tool in asyncio.run(service.skills_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_diagnoser_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import dfdiagnoser_service

    service = dfdiagnoser_service.DFDiagnoserService()

    server = _new_server("DFDiagnoser")
    for tool in asyncio.run(service.diagnoser_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_papers_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.papers import academic_service, local_library_service

    service = academic_service.AcademicPapersService()
    library = local_library_service.LocalLibraryService()

    server = _new_server("AcademicPapers")
    for tool in asyncio.run(service.papers_subservice.list_tools()):
        server.add_tool(tool)
    for tool in asyncio.run(library.library_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_system_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.system import system_service

    service = system_service.SystemService()

    server = _new_server("DFTracerSystem")
    for tool in asyncio.run(service.system_subservice.list_tools()):
        server.add_tool(tool)
    return server


def _build_session_server() -> FastMCP:
    from dftracer_agents.mcp_tools.tools.dftracer import dftracer_service

    service = dftracer_service.DFTracerSessionService()

    server = _new_server("DFTracerSession")
    for sub_name in (
        "session_subservice",
        "pipeline_subservice",
        "daemon_subservice",
        "clang_subservice",
        "annotation_api_subservice",
        "annotation_subservice",
        "optimization_subservice",
    ):
        sub = getattr(service, sub_name, None)
        if sub is None:
            continue
        for tool in asyncio.run(sub.list_tools()):
            server.add_tool(tool)
    return server



def _default_reload_dirs() -> list:
    """Watch the installed package source. With `pip install -e .` this is the repo."""
    return [str(Path(__file__).resolve().parent)]


def _run_with_reload(args: argparse.Namespace) -> int:
    """Re-exec the server whenever a watched source file changes.

    Why a fresh process rather than an in-process ``importlib.reload``: the tool
    objects registered on the FastMCP instance are closures captured at import
    time. Reloading modules in place leaves the old closures registered (FastMCP
    already warns "Component already exists" on double registration), so the
    server would keep serving stale code while *looking* reloaded. A clean
    re-exec has no such failure mode.

    Why HTTP only: under stdio the MCP client owns the process and its pipes —
    stdout IS the JSON-RPC channel. Killing and respawning the child would break
    the client's transport, and any reloader chatter on stdout corrupts the
    protocol stream. Reload therefore requires a transport where the server owns
    its own lifetime.
    """
    if args.transport == "stdio":
        print(
            "error: --reload is not supported with --transport stdio.\n"
            "  Under stdio the MCP client spawns and owns this process, and stdout is\n"
            "  the protocol channel — a reloader cannot restart it without breaking the\n"
            "  connection. Run the server over HTTP instead:\n\n"
            "    dftracer-mcp-server run --transport http --reload\n\n"
            "  and point the client at http://<host>:<port>/mcp.",
            file=sys.stderr,
        )
        return 2

    try:
        from watchfiles import run_process, PythonFilter
    except ImportError:
        print("error: --reload requires `watchfiles` (pip install watchfiles)", file=sys.stderr)
        return 2

    dirs = args.reload_dir or _default_reload_dirs()
    # The child runs the same server WITHOUT --reload (no recursion) and without
    # re-running startup setup on every restart.
    child = [
        sys.executable, "-m", "dftracer_agents.mcp_server",
        "--_child-run", "--skip-setup",
        "--service", args.service,
        "--transport", args.transport,
        "--host", args.host, "--port", str(args.port), "--path", args.path,
    ]

    print(f"[reload] watching {', '.join(dirs)}", file=sys.stderr)
    print(f"[reload] serving {args.transport} on {args.host}:{args.port}{args.path}", file=sys.stderr)
    print("[reload] NOTE: new/renamed tools require the MCP client to reconnect; "
          "edits to existing tool bodies take effect immediately.", file=sys.stderr)

    def _on_change(changes) -> None:
        for _kind, path in sorted(changes):
            print(f"[reload] changed: {path}", file=sys.stderr)

    # watchfiles requires a command STRING for target_type="command"; shlex.join
    # quotes each argv element so paths with spaces survive the round-trip.
    import shlex

    return run_process(
        *dirs,
        target=shlex.join(child),
        target_type="command",
        watch_filter=PythonFilter(),
        callback=_on_change,
        debounce=400,
    )


_NO_AUTH_DISCOVERY_PATHS = (
    "/.well-known/oauth-protected-resource",
    "/.well-known/oauth-protected-resource/{path:path}",
    "/.well-known/oauth-authorization-server",
    "/.well-known/oauth-authorization-server/{path:path}",
)


def _register_no_auth_routes(server: FastMCP) -> None:
    """Answer OAuth discovery with a JSON 404 instead of Starlette's text/plain one.

    This server is unauthenticated, so 404 is the spec-correct answer. But MCP
    clients probe these paths before registering tools and parse the body as
    JSON even on the error path -- a text/plain "Not Found" makes them abort the
    connection with a JSON parse error rather than concluding "no auth needed".
    """
    from starlette.requests import Request
    from starlette.responses import JSONResponse

    async def _not_found(_request: Request) -> JSONResponse:
        return JSONResponse({"error": "not_found"}, status_code=404)

    for i, path in enumerate(_NO_AUTH_DISCOVERY_PATHS):
        server.custom_route(
            path, methods=["GET"], name=f"no_auth_discovery_{i}", include_in_schema=False
        )(_not_found)


def build_server(service: str) -> FastMCP:
    """Build and return the combined FastMCP server for the requested service(s)."""
    if service == "utils":
        return _build_utils_server()

    if service == "analyzer":
        combined = _new_server("DFAnalyzer+Plot+Diagnoser")
        for srv in (_build_analyzer_server(), _build_plot_server(), _build_diagnoser_server()):
            for tool in asyncio.run(srv.list_tools()):
                combined.add_tool(tool)
        return combined

    if service == "diagnoser":
        return _build_diagnoser_server()

    if service == "papers":
        return _build_papers_server()

    if service == "session":
        return _build_session_server()

    if service == "docs":
        return _build_docs_server()

    if service == "skills":
        return _build_skills_server()

    if service == "system":
        return _build_system_server()

    # both — all services
    combined = _new_server("DFTracer")
    for srv in (
        _build_utils_server(),
        _build_analyzer_server(),
        _build_plot_server(),
        _build_diagnoser_server(),
        _build_session_server(),
        _build_docs_server(),
        _build_papers_server(),
        _build_system_server(),
        _build_skills_server(),
    ):
        for tool in asyncio.run(srv.list_tools()):
            combined.add_tool(tool)
    return combined


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="DFTracer MCP Server — stdio (default) or HTTP transport for Goose and MCP clients"
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["start", "stop", "status", "run"],
        default="start",
        help=(
            "Daemon command (default: start). Use 'run' to run in the foreground "
            "without daemonization."
        ),
    )
    parser.add_argument(
        "--service",
        choices=["utils", "analyzer", "session", "docs", "diagnoser", "papers", "system", "skills", "both"],
        default="both",
        help="Which service(s) to expose (default: both)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "streamable-http", "sse"],
        default="http",
        help="Transport protocol (default: http)",
    )
    parser.add_argument(
        "--host",
        default="0.0.0.0",
        help="Host to bind to for HTTP transports (default: 0.0.0.0)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=5000,
        help="Port to listen on for HTTP transports (default: 5000)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help=(
            "Auto-restart the server when source files change (HTTP transports "
            "only; see --reload-dir). Intended for `pip install -e .` development. "
            "NOTE: changed tool bodies take effect on the next call, but a NEWLY "
            "ADDED tool only becomes visible after the MCP client reconnects, "
            "because this FastMCP build does not emit notifications/tools/list_changed."
        ),
    )
    parser.add_argument(
        "--reload-dir",
        action="append",
        default=None,
        metavar="PATH",
        help="Directory to watch with --reload (repeatable; default: the package source dir)",
    )
    parser.add_argument(
        "--path",
        default="/mcp",
        help="URL path for HTTP transports (default: /mcp)",
    )
    parser.add_argument(
        "--skip-setup",
        action="store_true",
        default=False,
        help="Skip the automatic (tracked, idempotent) skill-symlink setup on startup.",
    )
    parser.add_argument(
        "--skills-target",
        default=None,
        help=(
            "Directory under which to install skills into '.claude/skills/'. "
            "Overrides the default (current directory if it looks like a project "
            "-- has .git or pyproject.toml -- otherwise your home directory). "
            "Use this to avoid the silent home-directory fallback when launching "
            "the server from a non-project directory."
        ),
    )
    parser.add_argument(
        "--force-setup",
        action="store_true",
        default=False,
        help=(
            "Re-run skill setup even if the tracking state says it is already "
            "done for this target (also self-heals if the .claude/skills symlinks "
            "were deleted since the last run)."
        ),
    )
    parser.add_argument(
        "--_child-run",
        action="store_true",
        default=False,
        help=argparse.SUPPRESS,
    )
    args = parser.parse_args()

    if not args._child_run:
        if args.command == "stop":
            raise SystemExit(_daemon_stop())
        if args.command == "status":
            raise SystemExit(_daemon_status())
        if args.command == "start":
            raise SystemExit(_daemon_start(args))
        # command == "run": continue with foreground server below.

    if not args.skip_setup:
        try:
            _run_startup_setup(args)
        except Exception as exc:  # never let setup issues block the server
            import traceback
            print(f"[setup] Skipped setup ({type(exc).__name__}): {exc}", file=sys.stderr)
            traceback.print_exc()

    if getattr(args, "reload", False):
        raise SystemExit(_run_with_reload(args))

    server = build_server(args.service)

    if args.transport == "stdio":
        asyncio.run(server.run_stdio_async(show_banner=False))
    else:
        _register_no_auth_routes(server)
        transport = "streamable-http" if args.transport == "http" else args.transport
        asyncio.run(
            server.run_http_async(
                transport=transport,
                host=args.host,
                port=args.port,
                path=args.path,
                show_banner=True,
            )
        )


if __name__ == "__main__":
    main()
