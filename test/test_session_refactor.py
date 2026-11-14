"""
Smoke-tests for DFTracerSessionService after the tools/session/ refactor.

Calls the real tools of the installed package — no mocking.
Run with:  pytest test/test_session_refactor.py   (or python test/test_session_refactor.py)
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ── Services under test (the installed package) ───────────────────────────────
from dftracer_agents.mcp_tools.tools.dftracer import dftracer_service as _svc_mod  # noqa: E402
from dftracer_agents.mcp_tools.tools.dftracer import docs_service as _docs_mod  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import session_tool_map  # noqa: E402

_svc = _svc_mod.DFTracerSessionService()
# every session-scoped tool (they are spread over several subservices now); S and P name the same map
S = P = session_tool_map(_svc)

# ── Call helper ───────────────────────────────────────────────────────────────

def call(subservice, name: str, **kwargs):
    if isinstance(subservice, dict):
        tool = subservice.get(name)
    else:
        tool = next((t for t in asyncio.run(subservice.list_tools()) if t.name == name), None)
    if tool is None:
        raise AssertionError(f"Tool not found: {name}")
    fn = None
    for attr in ("fn", "function", "callable", "handler", "_fn"):
        v = getattr(tool, attr, None)
        if callable(v):
            fn = v
            break
    if fn is None:
        raise AssertionError(f"No callable handler on tool: {name}")
    raw = asyncio.run(fn(**kwargs)) if asyncio.iscoroutinefunction(fn) else fn(**kwargs)
    return json.loads(raw)


# ── Test harness ─────────────────────────────────────────────────────────────

PASS: list[str] = []
FAIL: list[tuple[str, str]] = []

def check(label: str, result: dict, *, expect: str | None = None, keys: tuple = ()):
    """expect: None=any JSON ok, 'ok'=status==ok, 'error'=status==error"""
    status = result.get("status")
    if expect == "ok" and status != "ok":
        FAIL.append((label, f"expected ok, got {status!r}: {result.get('message','')[:120]}"))
        return
    if expect == "error" and status != "error":
        FAIL.append((label, f"expected error, got {status!r}: {result.get('message','')[:120]}"))
        return
    for k in keys:
        if k not in result:
            FAIL.append((label, f"missing key '{k}' in {list(result.keys())}"))
            return
    PASS.append(label)


# ── Test cases ────────────────────────────────────────────────────────────────

def run_tests(tmpdir: Path, with_installs: bool = False):
    os.environ["DFTRACER_WORKSPACES"] = str(tmpdir)

    # 1. Pipeline run-ID tools
    r = call(P, "pipeline_create_run", app="https://github.com/hpc/ior.git", description="test")
    check("pipeline_create_run", r, expect="ok", keys=["run_id", "app_name", "workspace", "created_at"])
    r = call(P, "pipeline_get_run_id", app="ior")
    check("pipeline_get_run_id", r, expect="ok", keys=["run_id", "workspace"])
    r = call(P, "pipeline_list_runs", app="ior")
    check("pipeline_list_runs", r, expect="ok", keys=["runs", "current_run_id"])
    r = call(P, "pipeline_get_run_id", app="no_such_app_xyz")
    check("pipeline_get_run_id[no app]", r, expect="error")

    # 2. Session file/state tools (local only)
    RID = "testapp/20260615_000001"
    WS = tmpdir / "testapp" / "20260615_000001"
    SRC = WS / "source"
    SRC.mkdir(parents=True)
    (SRC / "hello.c").write_text(
        '#include <stdio.h>\nint main(int a,char**v){printf("hi\\n");return 0;}\n'
    )
    (SRC / "Makefile").write_text("all:\n\tgcc hello.c -o hello\n")
    (WS / "session.json").write_text(json.dumps({
        "run_id": RID, "url": "file:///fake", "workspace": str(WS),
        "step": "cloned", "detection": {"languages": ["c"], "build_tool": "make"},
    }))

    r = call(S, "session_status", run_id=RID)
    check("session_status", r, expect="ok", keys=["run_id", "step"])
    r = call(S, "session_list_files", run_id=RID, subfolder="source")
    check("session_list_files", r, expect="ok", keys=["files"])
    r = call(S, "session_list_files", run_id=RID, subfolder="nonexistent")
    check("session_list_files[bad subfolder]", r, expect="error")
    r = call(S, "session_read_file", run_id=RID, filepath="hello.c", subfolder="source")
    check("session_read_file", r, expect="ok", keys=["content"])
    r = call(S, "session_read_file", run_id=RID, filepath="nope.c", subfolder="source")
    check("session_read_file[missing]", r, expect="error")
    (WS / "annotated").mkdir()
    r = call(S, "session_write_file", run_id=RID,
             filepath="src/p.c", content="int x=1;\n", subfolder="annotated")
    check("session_write_file", r, expect="ok")
    assert (WS / "annotated" / "src" / "p.c").exists()
    r = call(S, "session_detect", run_id=RID)
    check("session_detect", r, expect="ok", keys=["languages", "build_tool"])
    r = call(S, "session_detect", run_id="no_such_run_xyz")
    check("session_detect[no source]", r, expect="error")

    # 3. session_copy_annotated
    shutil.rmtree(WS / "annotated", ignore_errors=True)
    r = call(S, "session_copy_annotated", run_id=RID)
    check("session_copy_annotated", r, expect="ok")
    r = call(S, "session_copy_annotated", run_id="bad_run_xyz")
    check("session_copy_annotated[no source]", r, expect="error")

    # 4. session_patch_build
    r = call(S, "session_patch_build", run_id=RID)
    check("session_patch_build", r)
    r = call(S, "session_patch_build", run_id="bad_run_xyz")
    check("session_patch_build[no annotated]", r, expect="error")

    # 5. session_annotate_source
    # session_annotate_source was replaced by clang_annotate_project (C/C++) / python_annotate_project
    r = call(S, "clang_annotate_project", run_id=RID, language="c")
    check("clang_annotate_project", r, expect="ok")
    r = call(S, "clang_annotate_project", run_id="bad_run_xyz", language="c")
    check("clang_annotate_project[no dir]", r, expect="error")

    # 6. session_run_smoke_test
    INST = WS / "install" / "bin"
    INST.mkdir(parents=True)
    subprocess.run(["gcc", "hello.c", "-o", str(INST / "hello")], cwd=SRC, check=True)
    (WS / "session.json").write_text(json.dumps({
        "run_id": RID, "workspace": str(WS), "step": "installed",
        "detection": {"languages": ["c"], "build_tool": "make"},
        "install_prefix": str(WS / "install"),
    }))
    r = call(S, "session_run_smoke_test", run_id=RID, command=str(INST / "hello"))
    check("session_run_smoke_test[good]", r, expect="ok")
    r = call(S, "session_run_smoke_test", run_id=RID, command="/no/such/binary --flag")
    check("session_run_smoke_test[bad cmd]", r, expect="error")

    # 7. session_run_with_dftracer
    r = call(S, "session_run_with_dftracer", run_id=RID, command=str(INST / "hello"))
    check("session_run_with_dftracer[good]", r, expect="ok")
    r = call(S, "session_run_with_dftracer", run_id=RID, command="/no/such/binary")
    check("session_run_with_dftracer[bad cmd]", r, expect="error")

    # 8. session_split_traces / session_analyze_traces
    TRACES = WS / "traces"
    TRACES.mkdir(exist_ok=True)
    (TRACES / "test.pfw").write_text('{"events": []}\n')
    r = call(S, "session_split_traces", run_id=RID)
    check("session_split_traces", r)
    r = call(S, "session_analyze_traces", run_id=RID)
    check("session_analyze_traces", r)
    shutil.rmtree(TRACES)
    r = call(S, "session_split_traces", run_id=RID)
    check("session_split_traces[no traces]", r)

    # 9. Build tools (Makefile project — configure/build will likely error gracefully)
    r = call(S, "session_configure", run_id=RID)
    check("session_configure", r)
    r = call(S, "session_build_install", run_id=RID)
    check("session_build_install", r)
    r = call(S, "session_build_annotated", run_id=RID)
    check("session_build_annotated", r)

    # 10. Install tools: network and a from-source build of dftracer-utils (minutes), so only with_installs.
    # session_install_dftracer installs the dftracer prerelease wheel (it used to need a dftracer source tree and
    # errored without one), so either status is a valid answer here.
    if with_installs:
        r = call(S, "session_install_dftracer", run_id=RID)
        check("session_install_dftracer", r)
        r = call(S, "session_install_dftracer_utils", run_id=RID)
        check("session_install_dftracer_utils", r)  # ok if repo file exists; error otherwise

    # 11. Network tools (graceful error expected)
    # a run_id is a resume handle (it must name an existing session); a new session is created without one
    r = call(S, "session_create", url="https://invalid.example.invalid/no-repo.git")
    check("session_create[bad url]", r, expect="error")
    r = call(S, "session_create",
             url="https://invalid.example.invalid/no-repo.git", run_id="test_bad_xyz")
    check("session_create[unknown run_id]", r, expect="error")
    r = call(P, "session_run_pipeline", url="https://invalid.example.invalid/no-repo.git")
    check("session_run_pipeline[bad url]", r, expect="error")
    r = call(P, "session_run_pipeline",
             url="https://invalid.example.invalid/no-repo.git", run_id="test_pipeline_bad_xyz")
    check("session_run_pipeline[unknown run_id]", r, expect="error")


# ── Docs service ──────────────────────────────────────────────────────────────

_docs_svc = _docs_mod.DFTracerDocsService()
D = _docs_svc.docs_subservice


# ── Docs tool tests ───────────────────────────────────────────────────────────

def run_docs_tests():
    """Smoke-test the docs service tools (no network required for list_sources)."""

    # 12. docs_list_sources — no network needed
    r = call(D, "docs_list_sources")
    check("docs_list_sources", r, expect="ok", keys=("sources",))
    srcs = r.get("sources", [])
    src_keys = {s["key"] for s in srcs}
    expected = {"dftracer", "dftracer-utils", "pydftracer", "dfanalyzer"}
    if not expected.issubset(src_keys):
        FAIL.append(("docs_list_sources[keys]", f"Missing sources: {expected - src_keys}"))
    else:
        PASS.append("docs_list_sources[keys]")

    # 13. docs_search — fetch_content=False avoids network
    r = call(D, "docs_search", query="DFTRACER_C_FUNCTION_START",
             source="dftracer", max_results=1, fetch_content=False)
    check("docs_search[no-fetch]", r, keys=("results",))

    # 14. docs_search — bad source alias falls back to all sources
    r = call(D, "docs_search", query="installation", source="unknown_xyz",
             max_results=1, fetch_content=False)
    check("docs_search[unknown-src]", r)

    # 15. docs_fetch_page — non-existent URL returns error status
    r = call(D, "docs_fetch_page", url="https://invalid.example.invalid/no-page.html")
    check("docs_fetch_page[bad-url]", r, expect="error")

    # 16. docs_search_key_pages — unknown source returns error
    r = call(D, "docs_search_key_pages", source="not_a_source")
    check("docs_search_key_pages[bad-src]", r, expect="error")


# ── pytest entry points ───────────────────────────────────────────────────────

def _failures_since(n: int) -> list:
    return FAIL[n:]


def test_session_and_pipeline_tools(tmp_path, monkeypatch):
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build the smoke-test binary")
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    n = len(FAIL)
    run_tests(tmp_path)
    assert not _failures_since(n), _failures_since(n)


@pytest.mark.slow
def test_session_install_tools(tmp_path, monkeypatch):
    """The whole sequence including the install tools (network: PyPI and a dftracer-utils source build)."""
    if shutil.which("gcc") is None:
        pytest.skip("needs gcc to build the smoke-test binary")
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    n = len(FAIL)
    run_tests(tmp_path, with_installs=True)
    assert not _failures_since(n), _failures_since(n)


def test_docs_tools():
    n = len(FAIL)
    run_docs_tests()
    assert not _failures_since(n), _failures_since(n)


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    tmpdir = Path(tempfile.mkdtemp(prefix="dft_test_"))
    try:
        run_tests(tmpdir)
        run_docs_tests()
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    total = len(PASS) + len(FAIL)
    print(f"\n{'='*60}")
    print(f"  PASSED: {len(PASS)}/{total}")
    if FAIL:
        print(f"  FAILED ({len(FAIL)}):")
        for name, reason in FAIL:
            print(f"    x  {name}: {reason}")
        sys.exit(1)
    else:
        print("  All tools passed.")
    print("=" * 60)
