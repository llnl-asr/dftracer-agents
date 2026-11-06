"""Parallel project annotation (clang_/python_annotate_project) and re-annotation idempotency.

* run_file_pool keeps input order, per-file errors and phase order with slow fake workers, and is faster than serial.
* clang_annotate_project / python_annotate_project give the SAME result (file_results, errors, counts, annotated
  tree bytes) with jobs=1 and jobs=4.
* Annotating a C/C++ file twice gives identical output: no duplicate #include, START/END or INIT/FINI.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dftracer_agents.mcp_tools.tools.session import annotation_clang as AC  # noqa: E402
from dftracer_agents.mcp_tools.tools.session import annotation_python as AP  # noqa: E402

HAVE_CLANG = shutil.which("clang") is not None or shutil.which("clang++") is not None


# ---------------------------------------------------------------- slow fakes (module level: picklable for spawn)
def _slow_worker(item):
    name, delay = item
    time.sleep(delay)
    if name.startswith("bad"):
        raise ValueError(f"cannot annotate {name}")
    return f"done:{name}:{time.time()}"


def test_annotate_jobs_default_env_and_cap(monkeypatch):
    monkeypatch.delenv(AC.ANNOTATE_JOBS_ENV, raising=False)
    import os
    assert AC.annotate_jobs(0) == min(os.cpu_count() or 1, 16)
    assert AC.annotate_jobs(5) == 5
    assert AC.annotate_jobs(0, n_items=3) <= 3
    assert AC.annotate_jobs(0, n_items=0) == 1
    monkeypatch.setenv(AC.ANNOTATE_JOBS_ENV, "7")
    assert AC.annotate_jobs(0) == 7
    assert AC.annotate_jobs(2) == 2          # argument wins over env


def test_run_file_pool_order_errors_phases_and_speed():
    lib = [(f"f{i}", 1.5 if i % 2 == 0 else 0.5) for i in range(6)] + [("bad_lib", 0.5)]
    entry = [("main_a", 0.3), ("bad_main", 0.1)]
    t = time.time()
    outs = AC.run_file_pool(_slow_worker, [lib, entry], 6)
    wall = time.time() - t
    serial_sum = sum(d for _, d in lib + entry)
    assert [len(o) for o in outs] == [len(lib), len(entry)]
    # input order kept; failing file reported in its own slot, not dropped
    for (name, _), o in zip(lib + entry, outs[0] + outs[1]):
        if name.startswith("bad"):
            assert isinstance(o, ValueError) and name in str(o)
        else:
            assert isinstance(o, str) and o.startswith(f"done:{name}:")
    # phase 2 (entry files) started only after every phase-1 file finished
    lib_done = max(float(o.split(":")[2]) for o in outs[0] if isinstance(o, str))
    entry_done = [float(o.split(":")[2]) for o in outs[1] if isinstance(o, str)]
    assert all(e - 0.3 >= lib_done - 0.05 for e in entry_done)
    assert wall < serial_sum, (wall, serial_sum)


def test_run_file_pool_serial_matches_parallel():
    items = [(f"f{i}", 0.01) for i in range(5)] + [("bad_x", 0.0)]
    strip = lambda o: o.split(":")[1] if isinstance(o, str) else repr(o)  # noqa: E731
    ser = AC.run_file_pool(_slow_worker, [items], 1)
    par = AC.run_file_pool(_slow_worker, [items], 4)
    assert [strip(o) for o in ser[0]] == [strip(o) for o in par[0]]


def test_run_file_pool_falls_back_to_serial_when_pool_cannot_start(monkeypatch):
    import concurrent.futures as cf

    class Broken:
        def __init__(self, *a, **k):
            raise OSError("no processes here")
    monkeypatch.setattr(cf, "ProcessPoolExecutor", Broken)
    outs = AC.run_file_pool(_slow_worker, [[("a", 0), ("bad_b", 0)], [("m", 0)]], 4)
    assert outs[0][0].startswith("done:a:") and isinstance(outs[0][1], ValueError)
    assert outs[1][0].startswith("done:m:")


# ---------------------------------------------------------------- real trees
_LIB_C = """#include <stdio.h>
#include <stdlib.h>

int write_block_{n}(const char *path, int n) {{
    FILE *f = fopen(path, "w");
    if (!f)
        return -1;
    for (int i = 0; i < n; i++) {{
        fprintf(f, "%d\\n", i);
    }}
    fclose(f);
    return 0;
}}

int read_block_{n}(const char *path) {{
    FILE *f = fopen(path, "r");
    int x = 0, s = 0;
    if (!f) return -1;
    while (fscanf(f, "%d", &x) == 1) {{ s += x; }}
    fclose(f);
    return s;
}}
"""

_MAIN_C = """#include <stdio.h>

int write_block_0(const char *path, int n);
int read_block_0(const char *path);

int main(int argc, char **argv) {
    if (write_block_0("/tmp/x.txt", 10) != 0) {
        return 1;
    }
    printf("%d\\n", read_block_0("/tmp/x.txt"));
    return 0;
}
"""

_BROKEN_C = "int broken( {\n"


def _session(root: Path, rid: str, files: dict) -> Path:
    ws = root / rid
    (ws / "annotated").mkdir(parents=True)
    (ws / "session.json").write_text(json.dumps({"run_id": rid}))
    for rel, text in files.items():
        p = ws / "annotated" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return ws


def _tree(ws: Path) -> dict:
    return {str(p.relative_to(ws / "annotated")): p.read_text()
            for p in sorted((ws / "annotated").rglob("*")) if p.is_file()}


@pytest.fixture()
def clang_tools():
    col = AC._ToolCollector()
    AC.register_clang_tools(col)
    return col.fns


@pytest.mark.skipif(not HAVE_CLANG, reason="clang not installed")
def test_clang_annotate_project_parallel_equals_serial(tmp_path, monkeypatch, clang_tools):
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    files = {f"src/lib{i}.c": _LIB_C.format(n=i) for i in range(5)}
    files["src/main.c"] = _MAIN_C
    files["tools/driver.c"] = _MAIN_C
    files["src/broken.c"] = _BROKEN_C
    out = {}
    for j in (1, 4):
        ws = _session(tmp_path, f"proj_j{j}", files)
        r = json.loads(clang_tools["clang_annotate_project"](run_id=f"proj_j{j}", language="c", jobs=j))
        out[j] = (r, _tree(ws))
    (r1, t1), (r4, t4) = out[1], out[4]
    assert r1["status"] == r4["status"] == "ok"
    assert r1["file_results"] == r4["file_results"]
    assert r1["errors"] == r4["errors"]
    assert (r1["total_files"], r1["annotated"], r1["skipped"]) == (r4["total_files"], r4["annotated"], r4["skipped"])
    assert t1 == t4
    order = [f["file"] for f in r4["file_results"]]
    assert order[-2:] == ["src/main.c", "tools/driver.c"]      # entry points still last, in sorted order
    assert "DFTRACER_C_INIT" in t4["src/main.c"] and "DFTRACER_C_INIT" in t4["tools/driver.c"]
    assert "#include <dftracer/dftracer.h>" in t4["src/lib3.c"]
    # the unparseable file is reported, not dropped
    assert any(e["file"] == "src/broken.c" for e in r4["errors"]) or \
        any(f["file"] == "src/broken.c" for f in r4["file_results"])


# ---------------------------------------------------------------- re-annotation idempotency
@pytest.mark.skipif(not HAVE_CLANG, reason="clang not installed")
@pytest.mark.parametrize("lang,src,entry", [
    ("c", _LIB_C.format(n=0), False),
    ("c", _MAIN_C, True),
    ("cpp", _LIB_C.format(n=0), False),
    ("cpp", _MAIN_C, True),
])
def test_annotate_same_file_twice_is_identical(tmp_path, monkeypatch, clang_tools, lang, src, entry):
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    rel = "a.c" if lang == "c" else "a.cpp"
    ws = _session(tmp_path, "idem", {rel: src})
    kw = dict(run_id="idem", filepath=rel, language=lang, is_entry=entry)
    r1 = json.loads(clang_tools["clang_annotate_file"](**kw))
    once = (ws / "annotated" / rel).read_text()
    r2 = json.loads(clang_tools["clang_annotate_file"](**kw))
    twice = (ws / "annotated" / rel).read_text()
    assert r1["status"] == r2["status"] == "ok"
    assert once == twice
    assert twice.count("#include <dftracer/dftracer.h>") == 1


@pytest.mark.skipif(not HAVE_CLANG, reason="clang not installed")
def test_reannotate_include_only_file_adds_no_second_include(tmp_path, monkeypatch, clang_tools):
    """The iPIC3D case: an earlier pass left only the #include (every function cost-filtered or excluded); the
    already-annotated guard does not fire (no function macro), and the include used to be inserted again."""
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    ws = _session(tmp_path, "inc", {"lib.cpp": _LIB_C.format(n=0)})
    kw = dict(run_id="inc", filepath="lib.cpp", language="cpp")
    excl = json.dumps(["write_block_0", "read_block_0"])
    json.loads(clang_tools["clang_annotate_file"](**kw, exclude_functions=excl))
    first = (ws / "annotated" / "lib.cpp").read_text()
    assert first.count("#include <dftracer/dftracer.h>") == 1
    r = json.loads(clang_tools["clang_annotate_file"](**kw, exclude_functions=excl))
    assert (ws / "annotated" / "lib.cpp").read_text() == first
    assert r["include_already_present"] is True
    # a later full pass annotates the functions but still keeps ONE include
    json.loads(clang_tools["clang_annotate_file"](**kw))
    full = (ws / "annotated" / "lib.cpp").read_text()
    assert full.count("#include <dftracer/dftracer.h>") == 1
    assert full.count("DFTRACER_CPP_FUNCTION();") == 2


@pytest.mark.skipif(not HAVE_CLANG, reason="clang not installed")
def test_reannotate_keeps_existing_init_and_start_single(tmp_path, monkeypatch, clang_tools):
    """Hand-added INIT/FINI and an already-instrumented function are not duplicated."""
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    src = _MAIN_C.replace("int main(int argc, char **argv) {\n",
                          "int main(int argc, char **argv) {\n    DFTRACER_C_INIT(NULL, NULL, NULL);\n") \
                 .replace("    return 0;\n}", "    DFTRACER_C_FINI();\n    return 0;\n}")
    src = "#include <dftracer/dftracer.h>\n" + src + "\n" + _LIB_C.format(n=9).replace(
        "    FILE *f = fopen(path, \"w\");", "    DFTRACER_C_FUNCTION_START();\n    FILE *f = fopen(path, \"w\");", 1)
    ws = _session(tmp_path, "init", {"m.c": src})
    r = json.loads(clang_tools["clang_annotate_file"](run_id="init", filepath="m.c", language="c", is_entry=True))
    text = (ws / "annotated" / "m.c").read_text()
    if r.get("already_annotated"):       # the file-level guard fired (it has a START macro): nothing changed
        assert text == src
    assert text.count("#include <dftracer/dftracer.h>") == 1
    assert text.count("DFTRACER_C_INIT(") == 1
    assert text.count("DFTRACER_C_FINI(") == 1


# ---------------------------------------------------------------- python
_PY_LIB = '''import os


def write_rows(path, n):
    with open(path, "w") as f:
        for i in range(n):
            f.write(str(i))
    return n


class Store:
    def load(self, path):
        with open(path) as f:
            return f.read()
'''

_PY_MAIN = '''import sys


def main():
    print(sys.argv)
    return 0


if __name__ == "__main__":
    main()
'''


def test_python_annotate_project_parallel_equals_serial(tmp_path, monkeypatch):
    monkeypatch.setenv("DFTRACER_WORKSPACES", str(tmp_path))
    col = AC._ToolCollector()
    AP.register_python_tools(col)
    files = {f"pkg/mod{i}.py": _PY_LIB for i in range(6)}
    files["pkg/broken.py"] = "def oops(:\n"
    files["run.py"] = _PY_MAIN
    out = {}
    for j in (1, 4):
        ws = _session(tmp_path, f"py_j{j}", files)
        r = json.loads(col.fns["python_annotate_project"](run_id=f"py_j{j}", jobs=j))
        out[j] = (r, _tree(ws))
    (r1, t1), (r4, t4) = out[1], out[4]
    assert r1["file_results"] == r4["file_results"]
    assert r1["errors"] == r4["errors"]
    assert (r1["annotated"], r1["skipped"]) == (r4["annotated"], r4["skipped"])
    assert t1 == t4
    assert r4["file_results"][-1]["file"] == "run.py"
    assert "initialize_log" in t4["run.py"]
    # the parent's in-memory cache mirrors what the serial path leaves behind
    assert AP._PY_FILE_CACHE[("py_j4", "pkg/mod2.py")] == AP._PY_FILE_CACHE[("py_j1", "pkg/mod2.py")]
