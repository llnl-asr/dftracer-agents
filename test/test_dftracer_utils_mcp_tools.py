"""
Real integration tests for dftracer_utils MCP tools.

Each test calls the actual MCP tool function (no subprocess mocking) and
compares its behaviour against running the same binary directly.

Platform note: several binaries segfault on this container (io_uring probe
failure + SIGSEGV in native code).  Those tools are expected to raise
CalledProcessError with a non-zero returncode — both the MCP tool and the
direct subprocess call should fail consistently.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from conftest import get_tool_map, resolve_command_callable

SAMPLE_DIR = str(Path(__file__).resolve().parent / "data" / "cm1_1_48_20240926")
EMPTY_DIR = "/tmp/test-merge-empty"
Path(EMPTY_DIR).mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Registration sanity checks (no subprocess involved)
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "reader", "info", "merge", "split", "event_count", "pgzip", "tar",
    "stats", "aggregator", "call_tree", "comparator", "view", "index",
    "organize", "reconstruct", "replay", "server", "gen_dlio_config",
    "gen_fake_trace", "aggregator_mpi", "call_tree_mpi",
}


def test_all_expected_tools_are_registered(service_instance):
    tool_map = get_tool_map(service_instance)
    registered = set(tool_map)
    print(f"\n  registered tools ({len(registered)}): {sorted(registered)}")
    missing = EXPECTED_TOOLS - registered
    extra = registered - EXPECTED_TOOLS
    if missing:
        print(f"  MISSING: {sorted(missing)}")
    if extra:
        print(f"  EXTRA (unexpected): {sorted(extra)}")
    assert registered == EXPECTED_TOOLS
    print(f"  all {len(EXPECTED_TOOLS)} expected tools present  ✓")


def test_watchdog_flag_builder_includes_only_provided_values(service_module):
    flags = service_module._build_watchdog_flags(
        disable_watchdog=True,
        watchdog_global_timeout=12.5,
        watchdog_task_timeout=3.0,
        watchdog_warning_threshold=0.9,
    )
    print(f"\n  flags built: {flags}")
    assert flags == [
        "--disable-watchdog",
        "--watchdog-global-timeout", "12.5",
        "--watchdog-task-timeout", "3.0",
        "--watchdog-warning-threshold", "0.9",
    ]
    print("  omitted: --watchdog-interval, --watchdog-idle-timeout, --watchdog-deadlock-timeout  ✓")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _call_tool(tool_map, name, **kwargs):
    """Invoke the named MCP tool function directly (no mock). Returns (result, exc)."""
    fn = resolve_command_callable(tool_map[name])
    try:
        return fn(**kwargs), None
    except subprocess.CalledProcessError as exc:
        return None, exc
    except FileNotFoundError as exc:
        # the binary is not installed: the tool fails the way a shell would (127, command not found)
        return None, subprocess.CalledProcessError(127, exc.filename or name, stderr=str(exc))


def _direct(cmd):
    """Run cmd as a real subprocess. Returns CompletedProcess (check=False); a binary that is not installed exits 127
    as it would from a shell (some dftracer-utils releases do not ship every dftracer_* tool)."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess(cmd, 127, "", str(exc))


# ---------------------------------------------------------------------------
# pgzip — exits 0 (no .pfw files in tmp dir); tool returns a static string
# ---------------------------------------------------------------------------

def test_pgzip_on_empty_dir_succeeds(service_instance, tmp_path):
    tool_map = get_tool_map(service_instance)

    direct = _direct(["dftracer_pgzip", "-d", str(tmp_path)])
    print(f"\n  direct subprocess: rc={direct.returncode}")
    print(f"    stdout: {direct.stdout!r}")
    print(f"    stderr: {direct.stderr[:120]!r}")

    result, exc = _call_tool(tool_map, "pgzip", directory=str(tmp_path))
    print(f"\n  mcp result: {result!r}")
    print(f"  mcp exception: {exc!r}")

    if direct.returncode != 0:
        # Binary not available on this platform — both should fail consistently
        assert exc is not None, "MCP tool succeeded but direct subprocess failed — inconsistent behavior"
        print("  pgzip: direct failed and MCP also failed consistently  ✓")
    else:
        assert exc is None, f"MCP tool raised unexpectedly: {exc}"
        assert isinstance(result, str) and result
        print("  pgzip succeeded — both direct and MCP exited cleanly  ✓")


# ---------------------------------------------------------------------------
# Tools that consistently fail (exit non-zero or SIGSEGV) — both the direct
# subprocess call and the MCP tool should fail with CalledProcessError.
# ---------------------------------------------------------------------------

# (tool_name, mcp_kwargs, direct_cmd, description)
FAILURE_CASES = [
    (
        "reader",
        {"file": "/nonexistent.pfw.gz"},
        ["dftracer_reader", "/nonexistent.pfw.gz"],
        "file does not exist",
    ),
    (
        "info",
        {"directory": SAMPLE_DIR},
        ["dftracer_info", "-d", SAMPLE_DIR, "--query", "summary"],
        "segfaults on this platform (io_uring probe failure)",
    ),
    (
        "merge",
        {"directory": EMPTY_DIR},
        ["dftracer_merge", "-d", EMPTY_DIR],
        "no .pfw/.pfw.gz files in directory → rc=1",
    ),
    (
        "split",
        {},
        ["dftracer_split"],
        "segfaults on this platform",
    ),
    (
        "event_count",
        {"directory": SAMPLE_DIR},
        ["dftracer_event_count", "-d", SAMPLE_DIR],
        "segfaults on this platform",
    ),
    (
        "tar",
        {"file": "/nonexistent.tar.gz"},
        ["dftracer_tar", "/nonexistent.tar.gz"],
        "file does not exist → rc=1",
    ),
    (
        "stats",
        {"directory": SAMPLE_DIR},
        ["dftracer_stats", "-d", SAMPLE_DIR],
        "segfaults on this platform",
    ),
    (
        "aggregator",
        {},
        ["dftracer_aggregator"],
        "segfaults on this platform",
    ),
    (
        "call_tree",
        {},
        ["dftracer_call_tree", "--pattern", "*.pfw.gz"],
        "no input files → rc=1",
    ),
    (
        "comparator",
        {},
        ["dftracer_comparator"],
        "missing --baseline/--variant → rc=1",
    ),
    (
        "view",
        {},
        ["dftracer_view", "--no-metadata"],
        "segfaults with --no-metadata on this platform",
    ),
    (
        "index",
        {"directory": SAMPLE_DIR},
        ["dftracer_index", "-d", SAMPLE_DIR,
         "--expected-entries", "1024",
         "--false-positive-rate", "0.01",
         "--read-batch-size", "4"],
        "segfaults on this platform",
    ),
    (
        "organize",
        {},
        ["dftracer_organize"],
        "missing --groups → rc=1",
    ),
    (
        "reconstruct",
        {},
        ["dftracer_reconstruct", "-d", ".", "-o", "reconstructed"],
        "no .pidx sidecar files → rc=1",
    ),
    (
        "replay",
        {},
        ["dftracer_replay"],
        "no input files → rc=1",
    ),
    (
        "gen_dlio_config",
        {"output": "/tmp/test_gen_dlio.yaml"},
        ["dftracer_gen_dlio_config", "--output", "/tmp/test_gen_dlio.yaml"],
        "segfaults on this platform",
    ),
    (
        "gen_fake_trace",
        {"output_dir": "/tmp/test_gen_fake"},
        ["dftracer_gen_fake_trace", "--output-dir", "/tmp/test_gen_fake"],
        "segfaults on this platform",
    ),
]


@pytest.mark.parametrize(
    "tool_name,mcp_kwargs,direct_cmd,reason",
    FAILURE_CASES,
    ids=[c[0] for c in FAILURE_CASES],
)
def test_tool_and_subprocess_fail_consistently(
    service_instance, tool_map_fixture,
    tool_name, mcp_kwargs, direct_cmd, reason,
):
    """MCP tool behavior must be consistent with direct subprocess behavior.

    If the direct binary fails (non-zero exit), the MCP tool must also fail.
    If the direct binary succeeds, the MCP tool must also succeed.
    This handles platforms where some binaries segfault and others succeed.
    """
    direct = _direct(direct_cmd)
    print(f"\n  [{tool_name}] {reason}")
    print(f"  direct: {' '.join(direct_cmd)}")
    print(f"    rc={direct.returncode}")
    print(f"    stdout: {direct.stdout[:120]!r}")
    print(f"    stderr: {direct.stderr[:200]!r}")

    result, exc = _call_tool(tool_map_fixture, tool_name, **mcp_kwargs)
    print(f"  mcp tool ({tool_name}): raised={exc is not None}")
    if exc is not None:
        print(f"    CalledProcessError(returncode={exc.returncode})")
        print(f"    stderr: {exc.stderr[:200]!r}")
    else:
        print(f"    result: {str(result)[:200]!r}")

    if direct.returncode != 0:
        # Binary failed — MCP tool must also fail
        assert exc is not None, (
            f"Direct binary failed (rc={direct.returncode}) but MCP tool succeeded: "
            f"{str(result)[:200]!r}"
        )
        assert exc.returncode != 0, f"MCP CalledProcessError.returncode was 0"
        print(f"  both failed — direct rc={direct.returncode}, mcp rc={exc.returncode}  ✓")
    else:
        # Binary succeeded — MCP tool must also succeed
        assert exc is None, (
            f"Direct binary succeeded but MCP tool raised: {exc!r}"
        )
        print(f"  both succeeded — direct rc=0  ✓")


# ---------------------------------------------------------------------------
# Tools skipped entirely
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="server blocks indefinitely — cannot call in unit test")
def test_server(service_instance):
    pass


@pytest.mark.skip(reason="dftracer_aggregator_mpi not installed (requires MPI build)")
def test_aggregator_mpi(service_instance):
    pass


@pytest.mark.skip(reason="dftracer_call_tree_mpi not installed (requires MPI build)")
def test_call_tree_mpi(service_instance):
    pass
