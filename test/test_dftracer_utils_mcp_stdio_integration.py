from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


def _ensure_sample_data(repo_root: Path) -> Path:
    data_root = repo_root / "test" / "data"
    sample_dir = data_root / "cm1_1_48_20240926"
    if sample_dir.exists() and any(sample_dir.glob("*.pfw.gz")):
        return sample_dir

    data_root.mkdir(parents=True, exist_ok=True)
    clone_dir = data_root / "dftracer-sample"

    if not clone_dir.exists():
        subprocess.run(
            [
                "git",
                "clone",
                "--depth",
                "1",
                "--filter=blob:none",
                "--sparse",
                "https://github.com/llnl-asr/dftracer.git",
                str(clone_dir),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [
                "git",
                "sparse-checkout",
                "set",
                "examples/dfanalyzer/test-trace-distributed/cm1_1_48_20240926",
            ],
            cwd=clone_dir,
            check=True,
            capture_output=True,
            text=True,
        )

    upstream_sample = (
        clone_dir
        / "examples"
        / "dfanalyzer"
        / "test-trace-distributed"
        / "cm1_1_48_20240926"
    )
    if not upstream_sample.exists():
        raise RuntimeError("Unable to prepare sample test-trace directory")

    # Keep a local copy under test/data as requested.
    if sample_dir.exists():
        for p in sample_dir.glob("*"):
            if p.is_file():
                p.unlink()
    else:
        sample_dir.mkdir(parents=True, exist_ok=True)

    for f in upstream_sample.glob("*.pfw.gz"):
        target = sample_dir / f.name
        if not target.exists():
            target.write_bytes(f.read_bytes())

    return sample_dir


def _result_text(call_result) -> str:
    return "".join(item.text for item in call_result.content if hasattr(item, "text"))


async def _call_tool(server_params: StdioServerParameters, tool_name: str, args: dict):
    with tempfile.NamedTemporaryFile(mode="w+", encoding="utf-8") as errlog:
        async with stdio_client(server_params, errlog=errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, args)
        errlog.seek(0)
        return result, errlog.read()


def test_mcp_server_tool_behavior_matches_subprocess_for_info(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    sample_dir = _ensure_sample_data(repo_root)

    venv_python = repo_root / "venv" / "bin" / "python"
    venv_bin = repo_root / "venv" / "bin"
    server_script = repo_root / "test" / "mcp_integration_server.py"

    common_env = {
        **os.environ,
        "PYTHONUNBUFFERED": "1",
        "PATH": f"{venv_bin}:{os.environ.get('PATH', '')}",
    }

    server_params = StdioServerParameters(
        command=str(venv_python),
        args=[str(server_script)],
        cwd=str(repo_root),
        env=common_env,
    )

    # Temporary output locations; pytest will clean these up automatically.
    info_index_dir = tmp_path / "idx_info"
    info_index_dir.mkdir(parents=True, exist_ok=True)

    # 1) info
    info_args = {
        "directory": str(sample_dir),
        "query_type": "summary",
        "index_dir": str(info_index_dir),
    }

    mcp_info, mcp_errlog = asyncio.run(_call_tool(server_params, "info", info_args))
    mcp_info_text = _result_text(mcp_info)

    info_cmd = [
        "dftracer_info",
        "-d",
        str(sample_dir),
        "--query",
        "summary",
        "--index-dir",
        str(info_index_dir),
    ]
    info_proc = subprocess.run(
        info_cmd,
        check=False,
        capture_output=True,
        text=True,
        env=common_env,
    )

    def _strip_timing(s: str) -> str:
        import re
        return re.sub(r"Time:\s+[\d.]+ ms", "Time: <T> ms", s)

    # If the native binary succeeds, outputs must match (ignoring wall-clock time lines).
    if info_proc.returncode == 0:
        assert mcp_info.isError is False
        assert _strip_timing(mcp_info_text) == _strip_timing(info_proc.stdout)
    else:
        # On platforms where dftracer_info fails (e.g., segfault), MCP should surface an error too.
        assert mcp_info.isError is True
        err_text = mcp_info_text + "\n" + mcp_errlog
        assert "dftracer_info" in err_text

    # Ensure direct run produced diagnostic output on failures.
    if info_proc.returncode != 0:
        assert info_proc.stderr.strip()


def test_openai_format_config_file_exists_and_values():
    repo_root = Path(__file__).resolve().parents[1]
    cfg = repo_root / "test" / "openai_client_config.json"
    text = cfg.read_text(encoding="utf-8")
    assert "11434/v1" in text
    assert '"api_key": "ollama"' in text
    assert '"model": "qwen2.5-coder:7b"' in text
