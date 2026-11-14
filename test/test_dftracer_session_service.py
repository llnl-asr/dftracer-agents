"""
Tests for DFTracerSessionService — no subprocess mocking.

Every test calls the real tool function and exercises real system behaviour:
  - git clone operates against an in-process local bare repo (no network for
    most tests) so clone operations are fast and deterministic.
  - smoke-test commands are real shell invocations (echo / false / python).
  - session_configure + session_build_install build a real Python venv.
  - session_split_traces  and session_analyze_traces call the real
    dftracer_split / dftracer_info binaries.  On this container both
    segfault (exit 139, same known behaviour as the dftracer_utils tests).
    Tests assert that the tool reports failure consistently with the binary.

IOR 4.0.0 integration tests (TestIORIntegration) clone the real GitHub repo
and are gated behind --run-slow.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

import pytest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_TRACE_DIR = REPO_ROOT / "test" / "data" / "cm1_1_48_20240926"

IOR_URL = "https://github.com/hpc/ior"
IOR_TAG = "4.0.0"

# ---------------------------------------------------------------------------
# Sample project source
# ---------------------------------------------------------------------------

_IOR_MAIN_C = """\
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <mpi.h>
#include <fcntl.h>
#include <unistd.h>

#include "ior.h"

int main(int argc, char *argv[]) {
    MPI_Init(&argc, &argv);
    int rank, size;
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);

    if (rank == 0)
        printf("IOR benchmark starting, tasks=%d\\n", size);

    int fd = open("/tmp/ior_test.dat", O_WRONLY | O_CREAT | O_TRUNC, 0644);
    write(fd, "benchmark", 9);
    close(fd);

    MPI_Finalize();
    return 0;
}
"""

_IOR_HEADER_H = """\
#ifndef IOR_H
#define IOR_H
#include <mpi.h>

typedef struct {
    int numTasks;
    char *testFileName;
    long long blockSize;
} IOR_param_t;

void IOR_TestSetup(IOR_param_t *test);

#endif /* IOR_H */
"""

_CMAKE_LISTS = """\
cmake_minimum_required(VERSION 3.12)
project(IOR VERSION 4.0.0 LANGUAGES C)

find_package(MPI REQUIRED)

add_executable(ior src/ior.c)
target_include_directories(ior PRIVATE src/)
target_link_libraries(ior PRIVATE MPI::MPI_C)

install(TARGETS ior RUNTIME DESTINATION bin)
"""

_CONFIGURE_AC = """\
AC_INIT([IOR], [4.0.0], [hpc@lists.llnl.gov])
AM_INIT_AUTOMAKE([-Wall -Werror foreign subdir-objects])
AC_PROG_CC
AC_CONFIG_FILES([Makefile src/Makefile])
AC_OUTPUT
"""

_README_MD = """\
# IOR

IOR is a parallel file system I/O benchmark tool.

## Building with autotools

```
./bootstrap
./configure --prefix=/usr/local
make -j4
make install
```

## Building with CMake

```
cmake -S . -B build -DCMAKE_INSTALL_PREFIX=/usr/local
cmake --build build -j4
cmake --install build
```

## Testing

```
make check
```
"""

_SETUP_PY = """\
from setuptools import setup, find_packages

setup(
    name="example-project",
    version="0.1.0",
    packages=find_packages(),
    install_requires=["setuptools"],
    entry_points={"console_scripts": ["example=examplelib.main:main"]},
)
"""

_PYPROJECT_TOML = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[project]
name = "example-project"
version = "0.1.0"
dependencies = [
    "setuptools",
]
"""

_SAMPLE_PYTHON_LIB = """\
import os
import sys


def process_file(path):
    with open(path) as f:
        return f.read()


def run(args):
    data = process_file(args[0]) if args else "no input"
    print(data)
    return data


def main():
    run(sys.argv[1:])


if __name__ == "__main__":
    main()
"""

_SAMPLE_PYTHON_INIT = """\
from .main import run, main
"""

# ---------------------------------------------------------------------------
# Module loader
# ---------------------------------------------------------------------------

class _SessionModules:
    """The session service and its helpers as one namespace.

    DFTracerSessionService lives in tools/dftracer/dftracer_service.py (it was tools/dftracer_session_service.py);
    the helpers it used to define (_detect_info, _annotate_*_source, _patch_*, ...) moved to tools/session/.
    """

    _MODULES = (
        "dftracer_agents.mcp_tools.tools.dftracer.dftracer_service",
        "dftracer_agents.mcp_tools.tools.session.detection",
        "dftracer_agents.mcp_tools.tools.session.annotation",
        "dftracer_agents.mcp_tools.tools.session.build",
    )

    def __init__(self):
        self._mods = [importlib.import_module(m) for m in self._MODULES]

    def __getattr__(self, name):
        for m in self._mods:
            if hasattr(m, name):
                return getattr(m, name)
        raise AttributeError(name)


def _load_session_module():
    return _SessionModules()


@pytest.fixture(scope="session")
def session_module():
    return _load_session_module()


@pytest.fixture()
def session_service(session_module):
    return session_module.DFTracerSessionService()


# ---------------------------------------------------------------------------
# Git environment for local repos
# ---------------------------------------------------------------------------

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(*args, cwd):
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, env=_GIT_ENV
    )


# ---------------------------------------------------------------------------
# Local git repo fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def local_c_repo(tmp_path_factory):
    """Local git repo containing an IOR-like C + CMake/autotools project."""
    repo = tmp_path_factory.mktemp("c_repo")
    (repo / "src").mkdir()
    (repo / "src" / "ior.c").write_text(_IOR_MAIN_C)
    (repo / "src" / "ior.h").write_text(_IOR_HEADER_H)
    (repo / "CMakeLists.txt").write_text(_CMAKE_LISTS)
    (repo / "configure.ac").write_text(_CONFIGURE_AC)
    (repo / "README.md").write_text(_README_MD)

    _git("init", "-b", "main", cwd=repo)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


@pytest.fixture(scope="session")
def local_python_repo(tmp_path_factory):
    """Local git repo containing a minimal Python project (setup.py + package)."""
    repo = tmp_path_factory.mktemp("py_repo")
    pkg = repo / "examplelib"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(_SAMPLE_PYTHON_INIT)
    (pkg / "main.py").write_text(_SAMPLE_PYTHON_LIB)
    (repo / "setup.py").write_text(_SETUP_PY)
    (repo / "README.md").write_text("# Example Python project\n")

    _git("init", "-b", "main", cwd=repo)
    _git("add", ".", cwd=repo)
    _git("commit", "-m", "init", cwd=repo)
    return repo


# ---------------------------------------------------------------------------
# Workspace helpers
# ---------------------------------------------------------------------------

def _tool_map(service):
    from conftest import session_tool_map
    return session_tool_map(service)


def _result(raw) -> Dict[str, Any]:
    return json.loads(raw)


def _make_workspace(ws_root: Path, run_id: str, with_source: bool = True) -> Path:
    ws = ws_root / run_id
    ws.mkdir(parents=True)
    if with_source:
        src = ws / "source"
        src.mkdir()
        (src / "src").mkdir()
        (src / "src" / "ior.c").write_text(_IOR_MAIN_C)
        (src / "src" / "ior.h").write_text(_IOR_HEADER_H)
        (src / "configure.ac").write_text(_CONFIGURE_AC)
        (src / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (src / "README.md").write_text(_README_MD)
    state = {
        "run_id": run_id,
        "url": "local",
        "ref": "main",
        "workspace": str(ws),
        "step": "cloned",
    }
    (ws / "session.json").write_text(json.dumps(state))
    return ws


def _binary_result(cmd: list) -> int:
    """Run cmd directly and return its returncode (for consistent-behaviour checks)."""
    r = subprocess.run(cmd, capture_output=True, timeout=60)
    return r.returncode


# ===========================================================================
# Section 1 — Helper-function unit tests (no subprocess at all)
# ===========================================================================

class TestDetectInfo:
    def test_detects_c_and_cmake(self, session_module, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "ior.c").write_text(_IOR_MAIN_C)

        info = session_module._detect_info(tmp_path)
        assert "c" in info["languages"]
        assert info["build_tool"] == "cmake"

    def test_detects_autotools(self, session_module, tmp_path):
        (tmp_path / "configure.ac").write_text(_CONFIGURE_AC)
        (tmp_path / "main.c").write_text(_IOR_MAIN_C)

        info = session_module._detect_info(tmp_path)
        assert info["build_tool"] == "autotools"

    def test_autotools_excludes_python_language(self, session_module, tmp_path):
        """Autotools is C/C++ only — .py helper scripts must not pollute languages."""
        (tmp_path / "configure.ac").write_text(_CONFIGURE_AC)
        (tmp_path / "main.c").write_text(_IOR_MAIN_C)
        (tmp_path / "gen_config.py").write_text("# build helper\nprint('hello')\n")

        info = session_module._detect_info(tmp_path)
        assert info["build_tool"] == "autotools"
        assert "python" not in info["languages"]
        assert info["features"]["python"] is False

    def test_python_project_can_have_c_extensions(self, session_module, tmp_path):
        """Python projects (setup.py/pyproject.toml) may contain C/C++ extension code."""
        (tmp_path / "setup.py").write_text(_SETUP_PY)
        (tmp_path / "module.py").write_text(_SAMPLE_PYTHON_LIB)
        (tmp_path / "src" / "ext.c").parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "ext.c").write_text('#include <Python.h>\nint add(int a,int b){return a+b;}\n')

        info = session_module._detect_info(tmp_path)
        assert info["build_tool"] == "python"
        assert "python" in info["languages"]
        assert "c" in info["languages"]

    def test_detects_python_project(self, session_module, tmp_path):
        (tmp_path / "setup.py").write_text(_SETUP_PY)
        (tmp_path / "main.py").write_text(_SAMPLE_PYTHON_LIB)

        info = session_module._detect_info(tmp_path)
        assert "python" in info["languages"]
        assert info["build_tool"] == "python"
        assert info["features"]["python"] is True

    def test_detects_mpi_feature(self, session_module, tmp_path):
        (tmp_path / "configure.ac").write_text(_CONFIGURE_AC)
        (tmp_path / "main.c").write_text(_IOR_MAIN_C)

        info = session_module._detect_info(tmp_path)
        assert info["features"]["mpi"] is True

    def test_detects_posix_io(self, session_module, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (tmp_path / "main.c").write_text(_IOR_MAIN_C)

        info = session_module._detect_info(tmp_path)
        assert info["features"]["posix_io"] is True

    def test_dftracer_cmake_flags_include_mpi(self, session_module, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (tmp_path / "main.c").write_text(_IOR_MAIN_C)

        info = session_module._detect_info(tmp_path)
        flags = info["dftracer_cmake_flags"]
        assert any("DFTRACER_ENABLE_MPI=ON" in f for f in flags)

    def test_readme_excerpt_captured(self, session_module, tmp_path):
        (tmp_path / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (tmp_path / "README.md").write_text(_README_MD)

        info = session_module._detect_info(tmp_path)
        assert info["readme_excerpt"] is not None
        assert "IOR" in info["readme_excerpt"]

    def test_cmake_takes_precedence_over_autotools(self, session_module, tmp_path):
        """When both CMakeLists.txt and configure.ac exist, cmake wins."""
        (tmp_path / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        (tmp_path / "configure.ac").write_text(_CONFIGURE_AC)

        info = session_module._detect_info(tmp_path)
        assert info["build_tool"] == "cmake"


class TestAnnotateC:
    def test_adds_dftracer_include(self, session_module, tmp_path):
        f = tmp_path / "ior.c"
        result = session_module._annotate_c_source(_IOR_MAIN_C, f, is_entry=True)
        assert "#include <dftracer/dftracer.h>" in result

    def test_idempotent(self, session_module, tmp_path):
        f = tmp_path / "ior.c"
        first = session_module._annotate_c_source(_IOR_MAIN_C, f, is_entry=False)
        second = session_module._annotate_c_source(first, f, is_entry=False)
        assert first == second

    def test_entry_point_gets_init(self, session_module, tmp_path):
        f = tmp_path / "main.c"
        result = session_module._annotate_c_source(_IOR_MAIN_C, f, is_entry=True)
        assert "DFTRACER_C_INIT" in result

    def test_cpp_file_uses_cpp_macro(self, session_module, tmp_path):
        cpp_src = "#include <iostream>\nvoid foo() {\n  std::cout << 1;\n}\n"
        f = tmp_path / "foo.cpp"
        result = session_module._annotate_c_source(cpp_src, f, is_entry=False)
        assert "DFTRACER_CPP_FUNCTION" in result

    def test_include_inserted_after_existing_includes(self, session_module, tmp_path):
        src = "#include <stdio.h>\n#include <mpi.h>\nint x = 1;\n"
        f = tmp_path / "a.c"
        result = session_module._annotate_c_source(src, f, is_entry=False)
        lines = result.splitlines()
        mpi_idx = next(i for i, l in enumerate(lines) if "mpi.h" in l)
        dft_idx = next(i for i, l in enumerate(lines) if "dftracer/dftracer.h" in l)
        assert dft_idx > mpi_idx


class TestAnnotatePython:
    def test_adds_dft_fn_decorator(self, session_module):
        result = session_module._annotate_python_source(_SAMPLE_PYTHON_LIB, is_entry=False)
        assert "@dft_fn" in result

    def test_adds_import(self, session_module):
        result = session_module._annotate_python_source(_SAMPLE_PYTHON_LIB, is_entry=False)
        assert "from dftracer.logger import" in result

    def test_entry_point_gets_init(self, session_module):
        result = session_module._annotate_python_source(_SAMPLE_PYTHON_LIB, is_entry=True)
        assert "DFTRACER_INIT" in result

    def test_idempotent(self, session_module):
        first = session_module._annotate_python_source(_SAMPLE_PYTHON_LIB, is_entry=False)
        second = session_module._annotate_python_source(first, is_entry=False)
        assert first == second

    def test_decorator_applied_to_each_function(self, session_module):
        src = "def foo(): pass\ndef bar(): pass\n"
        result = session_module._annotate_python_source(src, is_entry=False)
        assert result.count("@dft_fn") == 2


class TestPatchBuildSystems:
    def test_patch_cmake_adds_find_package(self, session_module, tmp_path):
        p = tmp_path / "CMakeLists.txt"
        p.write_text(_CMAKE_LISTS)
        result = session_module._patch_cmake(p)
        assert "find_package(dftracer" in result
        assert "dftracer::dftracer" in result

    def test_patch_cmake_idempotent(self, session_module, tmp_path):
        p = tmp_path / "CMakeLists.txt"
        p.write_text(_CMAKE_LISTS)
        first = session_module._patch_cmake(p)
        p.write_text(first)
        second = session_module._patch_cmake(p)
        assert first == second

    def test_patch_setup_py_adds_dftracer(self, session_module, tmp_path):
        p = tmp_path / "setup.py"
        p.write_text(_SETUP_PY)
        result = session_module._patch_setup_py(p)
        assert "dftracer" in result

    def test_patch_pyproject_adds_dftracer(self, session_module, tmp_path):
        p = tmp_path / "pyproject.toml"
        p.write_text(_PYPROJECT_TOML)
        result = session_module._patch_pyproject(p)
        assert "dftracer" in result

    def test_patch_autotools_adds_pkg_config(self, session_module, tmp_path):
        p = tmp_path / "Makefile"
        p.write_text("all:\n\tgcc -o ior src/ior.c\n")
        result = session_module._patch_autotools_makefile(p)
        assert "pkg-config" in result
        assert "dftracer" in result


# ===========================================================================
# Section 2 — File-system tool tests (no subprocess)
# ===========================================================================

class TestSessionFileTools:
    def test_session_list_files(self, session_service, monkeypatch, tmp_path):
        run_id = "listtest"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_list_files"].fn(run_id=run_id, subfolder="source"))
        assert out["status"] == "ok"
        assert any("ior.c" in f for f in out["files"])

    def test_session_read_file(self, session_service, monkeypatch, tmp_path):
        run_id = "readtest"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_read_file"].fn(
            run_id=run_id, subfolder="source", filepath="src/ior.c"
        ))
        assert out["status"] == "ok"
        assert "MPI_Init" in out["content"]

    def test_session_read_file_missing(self, session_service, monkeypatch, tmp_path):
        run_id = "readmissing"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_read_file"].fn(
            run_id=run_id, subfolder="source", filepath="nonexistent.c"
        ))
        assert out["status"] == "error"

    def test_session_write_file(self, session_service, monkeypatch, tmp_path):
        run_id = "writetest"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        new_content = "#include <dftracer/dftracer.h>\n" + _IOR_MAIN_C
        out = _result(tools["session_write_file"].fn(
            run_id=run_id, subfolder="annotated", filepath="src/ior.c", content=new_content
        ))
        assert out["status"] == "ok"
        written = (tmp_path / run_id / "annotated" / "src" / "ior.c").read_text()
        assert "dftracer/dftracer.h" in written

    def test_session_status(self, session_service, monkeypatch, tmp_path):
        run_id = "statustest"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_status"].fn(run_id=run_id))
        assert out["status"] == "ok"
        assert out["run_id"] == run_id
        assert "source" in out["subdirs"]

    def test_session_status_unknown_run(self, session_service, monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        out = _result(tools["session_status"].fn(run_id="doesnotexist"))
        assert out["status"] == "error"


class TestSessionAnnotationTools:
    def test_session_copy_annotated(self, session_service, monkeypatch, tmp_path):
        run_id = "copytest"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_copy_annotated"].fn(run_id=run_id))
        assert out["status"] == "ok"
        ann = tmp_path / run_id / "annotated"
        assert (ann / "src" / "ior.c").exists()
        assert (ann / "CMakeLists.txt").exists()

    def test_session_patch_build_cmake(self, session_service, monkeypatch, tmp_path):
        run_id = "patchcmake"
        ws = _make_workspace(tmp_path, run_id)
        ann = ws / "annotated"
        ann.mkdir()
        (ann / "CMakeLists.txt").write_text(_CMAKE_LISTS)
        state = json.loads((ws / "session.json").read_text())
        state["detection"] = {"build_tool": "cmake", "languages": ["c"], "features": {}}
        (ws / "session.json").write_text(json.dumps(state))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_patch_build"].fn(run_id=run_id))
        assert out["status"] == "ok"
        assert "CMakeLists.txt" in out["patched"]
        assert "find_package(dftracer" in (ann / "CMakeLists.txt").read_text()

    def test_session_patch_build_python(self, session_service, monkeypatch, tmp_path):
        run_id = "patchpy"
        ws = _make_workspace(tmp_path, run_id, with_source=False)
        ann = ws / "annotated"
        ann.mkdir()
        (ann / "setup.py").write_text(_SETUP_PY)
        state = json.loads((ws / "session.json").read_text())
        state["detection"] = {"build_tool": "python", "languages": ["python"], "features": {}}
        (ws / "session.json").write_text(json.dumps(state))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_patch_build"].fn(run_id=run_id))
        assert out["status"] == "ok"
        assert "dftracer" in (ann / "setup.py").read_text()

    def test_session_annotate_source_c(self, session_service, monkeypatch, tmp_path):
        import shutil
        run_id = "annsrc"
        ws = _make_workspace(tmp_path, run_id)
        shutil.copytree(ws / "source", ws / "annotated")
        state = json.loads((ws / "session.json").read_text())
        state["detection"] = {"build_tool": "autotools", "languages": ["c"], "features": {}}
        (ws / "session.json").write_text(json.dumps(state))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        # session_annotate_source was replaced by clang_annotate_project (C/C++) and python_annotate_project
        tools = _tool_map(session_service)
        out = _result(tools["clang_annotate_project"].fn(run_id=run_id, language="c"))
        assert out["status"] == "ok", out
        assert out.get("annotated") or out.get("files_annotated") or out.get("results"), out
        ior_c = (ws / "annotated" / "src" / "ior.c").read_text()
        assert "#include <dftracer/dftracer.h>" in ior_c

    def test_session_annotate_source_python(self, session_service, monkeypatch, tmp_path):
        run_id = "annpy"
        ws = _make_workspace(tmp_path, run_id, with_source=False)
        (ws / "source").mkdir()
        (ws / "source" / "main.py").write_text(_SAMPLE_PYTHON_LIB)
        (ws / "annotated").mkdir()
        (ws / "annotated" / "main.py").write_text(_SAMPLE_PYTHON_LIB)
        state = json.loads((ws / "session.json").read_text())
        state["detection"] = {"build_tool": "python", "languages": ["python"], "features": {}}
        (ws / "session.json").write_text(json.dumps(state))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["python_annotate_project"].fn(run_id=run_id))
        assert out["status"] == "ok", out
        py_content = (ws / "annotated" / "main.py").read_text()
        # the annotator binds dft_fn once per module and decorates with it: `_dft = DFTracerFn(...)` / `@_dft.log`
        assert "dft_fn as DFTracerFn" in py_content
        assert "@_dft.log" in py_content


class TestSessionDetectTool:
    def test_session_detect_ior_like_project(self, session_service, monkeypatch, tmp_path):
        run_id = "detectior"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_detect"].fn(run_id=run_id))
        assert out["status"] == "ok"
        assert "c" in out["languages"]
        assert out["build_tool"] in {"cmake", "autotools"}
        assert out["features"]["mpi"] is True
        assert out["features"]["posix_io"] is True

    def test_session_detect_saves_state(self, session_service, monkeypatch, tmp_path):
        run_id = "detectstate"
        ws = _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        tools["session_detect"].fn(run_id=run_id)

        state = json.loads((ws / "session.json").read_text())
        assert "detection" in state
        assert state["step"] == "detected"


# ===========================================================================
# Section 3 — Real subprocess tests (no mocking)
# ===========================================================================

class TestSessionCreateReal:
    """session_create with a real git clone against a local bare repo."""

    def test_create_from_local_c_repo(self, session_service, local_c_repo, monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        # run_id is a resume handle now (it must name an existing session): a new session gets its id back
        out = _result(tools["session_create"].fn(url=str(local_c_repo), ref="main"))
        assert out["status"] == "ok"
        src = Path(out["source"])
        assert src.exists()
        assert (src / "CMakeLists.txt").exists()
        assert (src / "src" / "ior.c").exists()

    def test_create_from_local_python_repo(self, session_service, local_python_repo,
                                            monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        # run_id is a resume handle now (it must name an existing session): a new session gets its id back
        out = _result(tools["session_create"].fn(url=str(local_python_repo), ref="main"))
        assert out["status"] == "ok"
        src = Path(out["source"])
        assert (src / "setup.py").exists()
        assert (src / "examplelib" / "main.py").exists()

    def test_create_stores_session_state(self, session_service, local_c_repo,
                                         monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        # run_id is a resume handle now (it must name an existing session): a new session gets its id back
        out = _result(tools["session_create"].fn(url=str(local_c_repo), ref="main"))
        assert out["status"] == "ok"
        ws = Path(out["workspace"])
        state = json.loads((ws / "session.json").read_text())
        assert state["run_id"] == out["run_id"]
        assert state["step"] == "cloned"

    def test_create_bad_url_returns_error(self, session_service, monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        out = _result(tools["session_create"].fn(
            url="/tmp/this_does_not_exist_as_a_git_repo",
            ref="main",
        ))
        assert out["status"] == "error"


class TestSessionSmokeTestReal:
    """session_run_smoke_test with real shell commands."""

    def test_echo_command_passes(self, session_service, monkeypatch, tmp_path):
        run_id = "smokeecho"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_run_smoke_test"].fn(
            run_id=run_id, command='echo "IOR smoke test passed"', subfolder="build"
        ))
        assert out["status"] == "ok"
        assert "IOR smoke test passed" in out["stdout"]

    def test_false_command_fails(self, session_service, monkeypatch, tmp_path):
        run_id = "smokefalse"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_run_smoke_test"].fn(
            run_id=run_id, command="false", subfolder="build"
        ))
        assert out["status"] == "error"
        assert out["returncode"] != 0

    def test_python_import_smoke_test(self, session_service, monkeypatch, tmp_path):
        run_id = "smokepy"
        ws = _make_workspace(tmp_path, run_id, with_source=False)
        (ws / "build").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_run_smoke_test"].fn(
            run_id=run_id,
            command='python3 -c "import sys; print(f\'Python {sys.version_info.major}.{sys.version_info.minor} OK\')"',
            subfolder="build",
        ))
        assert out["status"] == "ok"
        assert "Python" in out["stdout"]

    def test_nonzero_exit_code_captured(self, session_service, monkeypatch, tmp_path):
        run_id = "smokeexit"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_run_smoke_test"].fn(
            run_id=run_id, command="exit 42", subfolder="build"
        ))
        assert out["status"] == "error"
        assert out["returncode"] == 42


class TestSessionBuildPythonReal:
    """session_configure + session_build_install against a real Python project."""

    def test_configure_python_creates_venv(self, session_service, local_python_repo,
                                            monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)

        # Clone
        out = _result(tools["session_create"].fn(url=str(local_python_repo), ref="main"))
        assert out["status"] == "ok"
        rid, ws = out["run_id"], Path(out["workspace"])

        # Detect (sets build_tool in state)
        tools["session_detect"].fn(run_id=rid)

        # Configure
        out = _result(tools["session_configure"].fn(run_id=rid))
        assert out["status"] == "ok"

        assert (ws / "install" / "bin" / "python3").exists()
        assert (ws / "install" / "bin" / "pip").exists()

    def test_configured_python_venv_has_package(self, session_service, local_python_repo,
                                                  monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)

        created = _result(tools["session_create"].fn(url=str(local_python_repo), ref="main"))
        rid, ws = created["run_id"], Path(created["workspace"])
        tools["session_detect"].fn(run_id=rid)
        out = _result(tools["session_configure"].fn(run_id=rid))
        assert out["status"] == "ok"

        # The package should be importable in the venv
        venv_python = str(ws / "install" / "bin" / "python3")
        r = subprocess.run(
            [venv_python, "-c", "import examplelib; print(examplelib.run([]))"],
            capture_output=True, text=True, timeout=30,
        )
        assert r.returncode == 0
        assert "no input" in r.stdout

    def test_smoke_test_in_venv(self, session_service, local_python_repo,
                                 monkeypatch, tmp_path):
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)

        created = _result(tools["session_create"].fn(url=str(local_python_repo), ref="main"))
        rid, ws = created["run_id"], Path(created["workspace"])
        tools["session_detect"].fn(run_id=rid)
        tools["session_configure"].fn(run_id=rid)

        venv_python = str(ws / "install" / "bin" / "python3")

        out = _result(tools["session_run_smoke_test"].fn(
            run_id=rid,
            command=f'{venv_python} -c "import examplelib; print(\'smoke ok\')"',
            subfolder="source",
        ))
        assert out["status"] == "ok"
        assert "smoke ok" in out["stdout"]


class TestSessionRunWithDftracerReal:
    """session_run_with_dftracer: verify env vars are set and command runs."""

    def test_env_vars_visible_to_child(self, session_service, monkeypatch, tmp_path):
        run_id = "dftenv"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build_ann").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        # Use env command to print DFTRACER_ENABLE so we can verify it's set
        out = _result(tools["session_run_with_dftracer"].fn(
            run_id=run_id,
            command='echo "ENABLE=${DFTRACER_ENABLE} LOGFILE=${DFTRACER_LOG_FILE}"',
            subfolder="build_ann",
        ))
        assert out["status"] == "ok"
        assert "ENABLE=1" in out["stdout"]
        assert "LOGFILE=" in out["stdout"]

    def test_traces_dir_created(self, session_service, monkeypatch, tmp_path):
        run_id = "dfttrace"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build_ann").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        tools["session_run_with_dftracer"].fn(
            run_id=run_id, command="true", subfolder="build_ann"
        )
        # traces go to <workspace>/<run_name>/traces/raw/ (run_name defaults to "baseline")
        assert (ws / "baseline" / "traces" / "raw").is_dir()

    def test_failed_command_reported(self, session_service, monkeypatch, tmp_path):
        run_id = "dftfail"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "build_ann").mkdir()
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_run_with_dftracer"].fn(
            run_id=run_id, command="false", subfolder="build_ann"
        ))
        assert out["status"] == "error"


class TestSessionTraceToolsReal:
    """
    session_split_traces and session_analyze_traces against real sample data.

    Both dftracer_split and dftracer_info segfault (exit 139) on this
    container due to the io_uring kernel probe failure — identical to the
    behaviour seen in test_dftracer_utils_mcp_tools.py.  Tests verify that
    the MCP tool reports failure consistently with the direct binary call.
    """

    @pytest.fixture()
    def ws_with_sample_traces(self, monkeypatch, tmp_path):
        """Workspace with sample .pfw.gz files copied into baseline/traces/raw/ (where runs write them)."""
        import shutil
        if not SAMPLE_TRACE_DIR.exists() or not list(SAMPLE_TRACE_DIR.glob("*.pfw.gz")):
            pytest.skip("Sample trace data not available")
        run_id = "tracetest"
        ws = tmp_path / run_id
        ws.mkdir()
        traces = ws / "baseline" / "traces" / "raw"
        shutil.copytree(SAMPLE_TRACE_DIR, traces)
        state = {"run_id": run_id, "workspace": str(ws), "step": "ran_with_dftracer"}
        (ws / "session.json").write_text(json.dumps(state))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        return run_id, tmp_path

    def test_split_traces_no_files_returns_error(self, session_service, monkeypatch, tmp_path):
        """Empty traces dir → error before any binary is called."""
        run_id = "splitnofiles"
        ws = _make_workspace(tmp_path, run_id)
        (ws / "baseline" / "traces" / "raw").mkdir(parents=True)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_split_traces"].fn(run_id=run_id))
        assert out["status"] == "error"
        assert "No .pfw" in out["message"]

    def test_split_traces_consistent_with_binary(self, session_service,
                                                  ws_with_sample_traces):
        """Tool and direct binary fail consistently on this container."""
        run_id, tmp_path = ws_with_sample_traces
        traces_dir = tmp_path / run_id / "baseline" / "traces" / "raw"
        output_dir = tmp_path / "split_out"
        output_dir.mkdir()

        # What does the binary do directly?
        direct_rc = _binary_result(
            ["dftracer_split", "--directory", str(traces_dir), "--output", str(output_dir)]
        )

        tools = _tool_map(session_service)
        out = _result(tools["session_split_traces"].fn(run_id=run_id))

        if direct_rc == 0:
            assert out["status"] == "ok"
        else:
            assert out["status"] == "error", (
                f"Binary exited {direct_rc} but tool reported ok — should match"
            )

    def test_analyze_traces_consistent_with_binary(self, session_service,
                                                    ws_with_sample_traces, tmp_path):
        """Tool and direct binary fail consistently on this container."""
        import shutil
        run_id, ws_root = ws_with_sample_traces
        # session_analyze_traces reads the compacted traces, <workspace>/<run_name>/traces/compact/
        traces_split_dir = ws_root / run_id / "baseline" / "traces" / "compact"
        shutil.copytree(SAMPLE_TRACE_DIR, traces_split_dir)

        idx_dir = tmp_path / "direct_idx"
        idx_dir.mkdir()
        direct_rc = _binary_result([
            "dftracer_info",
            "--directory", str(traces_split_dir),
            "--query", "summary",
            "--index-dir", str(idx_dir),
            "--force-rebuild",
        ])

        tools = _tool_map(session_service)
        out = _result(tools["session_analyze_traces"].fn(run_id=run_id))

        if direct_rc == 0:
            assert out["status"] == "ok"
        else:
            assert out["status"] == "error", (
                f"Binary exited {direct_rc} but tool reported ok — should match"
            )

    def test_analyze_missing_subdir_returns_error(self, session_service,
                                                   monkeypatch, tmp_path):
        run_id = "analyzeno"
        _make_workspace(tmp_path, run_id)
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_analyze_traces"].fn(run_id=run_id))
        assert out["status"] == "error"


class TestServiceRegistration:
    def test_service_name(self, session_service):
        assert session_service.name == "dftracer-session"

    def test_service_registered_in_factory(self, session_module):
        svc = session_module.MCPServiceFactory.get_service("dftracer-session")
        assert svc is not None
        assert svc.name == "dftracer-session"

    def test_all_expected_tools_registered(self, session_service):
        tools = _tool_map(session_service)
        # The pipeline's session tools. session_annotate_source was replaced by clang_annotate_project /
        # python_annotate_project; session_autobuild_dftracer is gone (dftracer is installed as the prerelease
        # wheel and composed by session_install_dftracer). The session service now also serves many more tools
        # (annotation, provenance, profiling, privacy, ...), so this checks the pipeline's set is all there.
        expected = {
            "session_create", "session_detect", "session_list_files",
            "session_read_file", "session_write_file", "session_configure",
            "session_build_install", "session_run_smoke_test", "session_copy_annotated",
            "session_patch_build", "clang_annotate_project", "python_annotate_project",
            "session_install_dftracer", "session_install_dftracer_utils",
            "session_build_annotated", "session_run_with_dftracer", "session_split_traces",
            "session_analyze_traces", "session_status", "session_run_pipeline",
        }
        assert expected <= tools.keys(), sorted(expected - tools.keys())


# ===========================================================================
# Section 4 — IOR integration tests (--run-slow, real network)
# ===========================================================================

@pytest.fixture(scope="session")
def ior_source(tmp_path_factory):
    """Clone IOR 4.0.0 once per session; skip if network is unavailable."""
    cache = tmp_path_factory.mktemp("ior_cache")
    src = cache / "ior"
    result = subprocess.run(
        ["git", "clone", "--depth", "1", "--branch", IOR_TAG, IOR_URL, str(src)],
        capture_output=True, text=True, timeout=180, env=_GIT_ENV,
    )
    if result.returncode != 0:
        pytest.skip(f"Could not clone IOR {IOR_TAG}: {result.stderr[:300]}")
    return src


@pytest.mark.slow
class TestIORIntegration:
    """Integration tests against the real IOR 4.0.0 repository."""

    def test_detect_build_tool(self, session_module, ior_source):
        info = session_module._detect_info(ior_source)
        assert info["build_tool"] in {"autotools", "cmake"}

    def test_detect_c_language(self, session_module, ior_source):
        info = session_module._detect_info(ior_source)
        assert "c" in info["languages"]

    def test_detect_mpi_feature(self, session_module, ior_source):
        info = session_module._detect_info(ior_source)
        assert info["features"]["mpi"] is True

    def test_detect_posix_io_feature(self, session_module, ior_source):
        info = session_module._detect_info(ior_source)
        assert info["features"]["posix_io"] is True

    def test_find_c_entry_points(self, session_module, ior_source):
        entries = session_module._find_c_entry_points(ior_source)
        assert entries, "Expected at least one file containing main() in IOR"

    def test_annotate_entry_point(self, session_module, ior_source):
        entries = session_module._find_c_entry_points(ior_source)
        assert entries
        content = entries[0].read_text(errors="ignore")
        annotated = session_module._annotate_c_source(content, entries[0], is_entry=True)
        assert "#include <dftracer/dftracer.h>" in annotated
        assert "DFTRACER_C_INIT" in annotated

    def test_patch_cmake_or_autotools(self, session_module, ior_source, tmp_path):
        import shutil
        info = session_module._detect_info(ior_source)
        bt = info["build_tool"]
        if bt == "cmake":
            src_cml = ior_source / "CMakeLists.txt"
            if src_cml.exists():
                tmp_cml = tmp_path / "CMakeLists.txt"
                shutil.copy(src_cml, tmp_cml)
                result = session_module._patch_cmake(tmp_cml)
                assert "find_package(dftracer" in result
        elif bt == "autotools":
            candidates = list(ior_source.glob("Makefile*"))
            if candidates:
                tmp_mf = tmp_path / candidates[0].name
                shutil.copy(candidates[0], tmp_mf)
                result = session_module._patch_autotools_makefile(tmp_mf)
                assert "pkg-config" in result

    def test_session_create_from_github(self, session_service, monkeypatch, tmp_path):
        """session_create tool: real clone from GitHub."""
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
        tools = _tool_map(session_service)
        out = _result(tools["session_create"].fn(
            url=IOR_URL, ref=IOR_TAG,
        ))
        assert out["status"] == "ok"
        src = Path(out["source"])
        assert src.exists()
        assert any(src.rglob("*.c"))

    def test_session_detect_ior_via_tool(self, session_service, ior_source,
                                          monkeypatch, tmp_path):
        """session_detect tool: detect real IOR source."""
        import shutil
        run_id = "ior_detect_e2e"
        ws = tmp_path / run_id
        ws.mkdir()
        shutil.copytree(ior_source, ws / "source")
        (ws / "session.json").write_text(json.dumps(
            {"run_id": run_id, "workspace": str(ws), "step": "cloned"}
        ))
        monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))

        tools = _tool_map(session_service)
        out = _result(tools["session_detect"].fn(run_id=run_id))
        assert out["status"] == "ok"
        assert "c" in out["languages"]
        assert out["features"]["mpi"] is True
