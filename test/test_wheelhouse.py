"""Prebuilt dftracer wheels: exact combination keys, relocatable harvest, install into another venv, integrity."""
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from dftracer_agents.mcp_tools.tools.session import wheelhouse as W

needs_tools = pytest.mark.skipif(not (shutil.which("gcc") and shutil.which("patchelf") and shutil.which("git")),
                                 reason="needs gcc, patchelf and git")


def _git_repo(d: Path) -> Path:
    d.mkdir(parents=True)
    (d / "CMakeLists.txt").write_text("project(dftracer)\n")
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "x"]):
        subprocess.run(["git", *args], cwd=d, check=True, capture_output=True)
    return d


ENV = {"DFTRACER_ENABLE_MPI": "ON", "DFTRACER_ENABLE_HDF5": "OFF", "HDF5_ROOT": "/opt/hdf5", "JOBS": "8",
       "CMAKE_BUILD_PARALLEL_LEVEL": "8", "PATH": "/x", "DFTRACER_CMAKE_ARGS": "-DDFTRACER_ENABLE_MPI=ON"}


@pytest.fixture()
def house(tmp_path, monkeypatch):
    monkeypatch.setenv("DFTRACER_WHEELHOUSE", str(tmp_path / "wheelhouse"))
    return tmp_path


def test_combination_is_exact_and_ignores_parallelism(house):
    src = _git_repo(house / "src")
    k = lambda env, **kw: W.combination(sys.executable, env, "https://x/dftracer.git", kw.get("ref", "develop"),  # noqa: E731
                                        src, None, None)["key"]
    base = k(ENV)
    assert base == k(ENV) == k({**ENV, "JOBS": "64", "CMAKE_BUILD_PARALLEL_LEVEL": "64", "PATH": "/other"})
    assert base != k({**ENV, "DFTRACER_ENABLE_HDF5": "ON"}) and base != k(ENV, ref="v4.3")
    (src / "CMakeLists.txt").write_text("project(dftracer) # patched\n")            # a source patch / local edit
    assert base != k(ENV)
    assert W.combination(sys.executable, ENV, "u", "r", None, None, None) is None   # nothing to pin


def _so(path: Path, src: str, rpath: str, link: list = ()):
    c = path.with_suffix(".c")
    c.write_text(src)
    subprocess.run(["gcc", "-shared", "-fPIC", "-o", str(path), str(c), *link, f"-Wl,-rpath,{rpath}",
                    "-Wl,--disable-new-dtags"], check=True)
    c.unlink()


def _venv(d: Path) -> str:
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(d)], check=True)
    subprocess.run([str(d / "bin" / "python"), "-c", "import ensurepip; ensurepip.bootstrap(upgrade=False)"],
                   check=True, capture_output=True)
    return str(d / "bin" / "python")


def _fake_dftracer_install(py: str):
    """What dftracer's build leaves in a venv: a few RECORD files, the rest put there by CMake, absolute RPATHs."""
    facts = W._py_facts(py)
    site, venv = Path(facts["platlib"]), facts["prefix"]
    d = site / "dftracer"
    (d / "lib64" / "cmake" / "dftracer").mkdir(parents=True)
    (d / "utils").mkdir()
    (d / "__init__.py").write_text("")
    (d / "dftracer.py").write_text("VALUE = 42\n")
    _so(d / "lib64" / "libdftracer_core.so", "int core(void){return 7;}", f"{d}/lib64:/tmp/build-xyz/lib:../lib")
    _so(d / "lib64" / "libdftracer_preload.so", "int core(void); int pre(void){return core();}",
        f"{d}/lib64:{d}/lib:/usr/lib64", [f"-L{d}/lib64", "-ldftracer_core"])
    (d / "lib64" / "cmake" / "dftracer" / "dftracer-config.cmake").write_text(f'set(DFTRACER_PREFIX "{d}")\n'
                                                                              f'set(VENV "{venv}")\n')
    (d / "utils" / "owned_by_utils.py").write_text("")
    di = site / "dftracer-4.3.0.dist-info"
    di.mkdir()
    (di / "METADATA").write_text("Metadata-Version: 2.1\nName: dftracer\nVersion: 4.3.0\n")
    tag = "cp%d%d-cp%d%d-%s" % (*sys.version_info[:2], *sys.version_info[:2], facts["platform"].replace("-", "_").replace(".", "_"))
    (di / "WHEEL").write_text(f"Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: {tag}\n")
    (di / "RECORD").write_text("dftracer/__init__.py,,\ndftracer/dftracer.py,,\ndftracer-4.3.0.dist-info/RECORD,,\n")
    ui = site / "dftracer_utils-0.0.14.dist-info"
    ui.mkdir()
    (ui / "RECORD").write_text("dftracer/utils/owned_by_utils.py,,\n")
    return facts


@needs_tools
def test_harvest_is_relocatable_and_installs_into_another_venv(house):
    src = _git_repo(house / "src")
    py1 = _venv(house / "venv1")
    f1 = _fake_dftracer_install(py1)
    combo = W.combination(py1, ENV, "https://x/dftracer.git", "develop", src, None, None)
    h = W.harvest(py1, combo, 321.0, "run/1")
    assert h["stored"], h
    with zipfile.ZipFile(h["wheel"]) as z:
        names = z.namelist()
        assert "dftracer/utils/owned_by_utils.py" not in names                    # another distribution's file
        assert "dftracer/lib64/libdftracer_core.so" in names
        cfg = z.read("dftracer/lib64/cmake/dftracer/dftracer-config.cmake").decode()
        assert W.SITE_PH in cfg and W.VENV_PH in cfg and f1["platlib"] not in cfg
        z.extract("dftracer/lib64/libdftracer_preload.so", house / "x")
    rp = subprocess.run(["patchelf", "--print-rpath", str(house / "x/dftracer/lib64/libdftracer_preload.so")],
                        capture_output=True, text=True).stdout.strip()
    assert rp.split(":")[:2] == ["$ORIGIN", "$ORIGIN/../lib"] and "/usr/lib64" in rp and f1["platlib"] not in rp
    row = W.lookup(combo)
    assert row and row["build_seconds"] == 321.0 and row["mpi"] and W.search(text="MPI")["count"] == 1
    # a second, different venv: install the prebuilt wheel, offline
    py2 = _venv(house / "venv2")
    pip = lambda args: (lambda r: {"success": r.returncode == 0, "stderr": r.stderr})(  # noqa: E731
        subprocess.run([py2, "-m", "pip", *args, "--no-index", "-q"], capture_output=True, text=True))
    r = W.install_cached(row, py2, pip)
    assert r["success"] and r["wheelhouse"]["used"], r
    f2 = W._py_facts(py2)
    cfg2 = (Path(f2["platlib"]) / "dftracer/lib64/cmake/dftracer/dftracer-config.cmake").read_text()
    assert f2["platlib"] in cfg2 and f2["prefix"] in cfg2 and W.SITE_PH not in cfg2
    ldd = subprocess.run(["ldd", str(Path(f2["platlib"]) / "dftracer/lib64/libdftracer_preload.so")],
                         capture_output=True, text=True).stdout
    assert f"{f2['platlib']}/dftracer/lib64/libdftracer_core.so" in ldd and "not found" not in ldd
    assert W.search()["wheels"][0]["used_count"] == 1


@needs_tools
def test_a_tampered_wheel_is_never_offered(house):
    src = _git_repo(house / "src")
    py1 = _venv(house / "venv1")
    _fake_dftracer_install(py1)
    combo = W.combination(py1, ENV, "u", "develop", src, None, None)
    h = W.harvest(py1, combo, 1.0)
    with open(h["wheel"], "ab") as fh:
        fh.write(b"tampered")
    assert W.lookup(combo) is None
    row = W.search(include_broken=True)["wheels"][0]
    assert row["status"] == "broken" and "sha256" in row["status_reason"]


def test_disabled_wheelhouse(monkeypatch):
    monkeypatch.setenv("DFTRACER_WHEELHOUSE", "off")
    assert W.root() is None and W.search()["status"] == "error"
