"""Prebuilt dftracer wheels, reused across sessions: a wheelhouse plus a SQLite index of build combinations.

Building dftracer from source (C++ core + brahma + gotcha + cpp-logger, Cray/MPI/HDF5 aware) takes minutes and gives
the same result for the same inputs. After a successful fresh build, :func:`harvest` packs what the build installed
into a **relocatable wheel** and indexes it by the exact combination that produced it; the next install with the same
combination (:func:`install_cached`) installs that wheel in seconds.

Why a repair step (like ``auditwheel repair``): dftracer's build installs most of its files with CMake straight into
``site-packages/dftracer`` (outside the pip RECORD), its ELF files carry an absolute RPATH into the building venv, and
its CMake / pkg-config / modulefile texts embed that venv path. The harvested wheel therefore

* contains every file the dftracer build put under ``site-packages/dftracer`` that no other distribution owns
  (``dftracer-utils`` and ``pydftracer`` stay ordinary pip dependencies), plus dftracer's own scripts;
* has every ELF RPATH rewritten: entries inside the old ``dftracer/`` tree become ``$ORIGIN``-relative, build-temp
  and working-directory-relative entries are dropped, system entries (Cray MPI, gcc runtime, /usr/lib64) are kept;
* has the old site-packages / venv paths in text files replaced by ``@DFTRACER_SITE@`` / ``@DFTRACER_VENV@``, which
  :func:`install_cached` fills in for the installing venv (the list is in the index and in the wheel).

The combination key (sha256) covers: system, architecture, glibc, Python tag, dftracer repo + ref + commit + the
exact source diff applied (agent patches and uncommitted local edits), the build-relevant environment (every
DFTRACER_*/HDF5_*/MPI*/BRAHMA_*/CMAKE_*/compiler/ROCm/PAPI/variorum variable, session paths normalised) and the
module set the build ran under. Any difference -> another wheel.

Location: ``$DFTRACER_WHEELHOUSE`` (e.g. a group-shared project directory, like a run corpus), default
``<DFTRACER_WORKSPACES>/_wheelhouse``; ``DFTRACER_WHEELHOUSE=off`` disables it. Layout::

    <wheelhouse>/index.sqlite                      the searchable index (table ``wheels``)
    <wheelhouse>/<system>/<python-tag>/dftracer-<version>+wh.<key12>-<tag>.whl
"""
from __future__ import annotations

import base64
import contextlib
import csv
import hashlib
import io
import json
import os
import re
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

SITE_PH, VENV_PH = "@DFTRACER_SITE@", "@DFTRACER_VENV@"
OTHER_OWNERS = ("dftracer_utils", "pydftracer")          # distributions that also install under dftracer/
ENV_KEY_RX = re.compile(r"^(DFTRACER_|HDF5_|MPI|BRAHMA_|CMAKE_(?!BUILD_PARALLEL)|CC$|CXX$|FC$|F77$|CFLAGS|CXXFLAGS|"
                        r"LDFLAGS|ROCM|HIP_|CUDA|VARIORUM|PAPI|GOTCHA|CPP_LOGGER)")
SCHEMA = """
CREATE TABLE IF NOT EXISTS wheels (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  key TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL DEFAULT 'ok',          -- ok | broken (a failed install/sanity check; never offered again)
  status_reason TEXT,
  created REAL, last_used REAL, used_count INTEGER DEFAULT 0, build_seconds REAL,
  system TEXT, arch TEXT, glibc TEXT, python_tag TEXT, platform TEXT,
  dftracer_version TEXT, dftracer_repo TEXT, dftracer_ref TEXT, dftracer_commit TEXT, source_diff_sha TEXT,
  mpi TEXT, hdf5 TEXT, compilers TEXT, features_json TEXT, cmake_args TEXT, modules TEXT, build_env_json TEXT,
  wheel_path TEXT, wheel_sha256 TEXT, wheel_bytes INTEGER, relocate_json TEXT, built_by_run TEXT
);
CREATE INDEX IF NOT EXISTS wheels_lookup ON wheels(system, python_tag, dftracer_commit, status);
"""


# ------------------------------------------------------------------ location / index
def root() -> Optional[Path]:
    v = os.environ.get("DFTRACER_WHEELHOUSE", "").strip()
    if v.lower() in ("off", "0", "false", "none", "disabled"):
        return None
    if v:
        return Path(v).expanduser()
    return Path(os.environ.get("DFTRACER_WORKSPACES", "workspaces")).expanduser().resolve() / "_wheelhouse"


def _share(*paths: Path) -> None:
    """A wheelhouse is shared by a group (like the corpus): keep what we create group read/write, dirs setgid."""
    for p in paths:
        try:
            st = p.stat()
            if st.st_uid == os.getuid():
                p.chmod((st.st_mode | 0o060 | (0o2010 if p.is_dir() else 0)) & 0o7777)
        except OSError:
            pass


@contextlib.contextmanager
def _db(rt: Path):
    rt.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(rt / "index.sqlite"), timeout=60)
    _share(rt, rt / "index.sqlite")
    con.row_factory = sqlite3.Row
    try:
        con.executescript(SCHEMA)
        yield con
        con.commit()
    finally:
        con.close()


# ------------------------------------------------------------------ the combination
def _py_facts(py: str) -> Dict[str, str]:
    code = ("import sys,sysconfig,platform,json;print(json.dumps({'tag':sys.implementation.cache_tag,"
            "'abi':'cp%d%d'%sys.version_info[:2],'platform':sysconfig.get_platform(),'arch':platform.machine(),"
            "'glibc':'-'.join(platform.libc_ver()),'platlib':sysconfig.get_paths()['platlib'],'prefix':sys.prefix}))")
    r = subprocess.run([py, "-c", code], capture_output=True, text=True, timeout=60)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _system_name() -> str:
    return re.sub(r"\d+$", "", socket.gethostname().split(".")[0]) or "unknown"


def _git(args: List[str], cwd: Path, binary: bool = False):
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, timeout=300)
    return r.stdout if binary else r.stdout.decode(errors="replace").strip()


def combination(py: str, pip_env: Dict[str, str], repo_url: str, ref: str, source_dir: Optional[Path],
                env_script: Optional[Path], ws: Optional[Path]) -> Optional[Dict[str, Any]]:
    """The exact build combination (and its sha256 key). None when it cannot be pinned (no source checkout)."""
    if not source_dir or not (Path(source_dir) / ".git").exists():
        return None
    src = Path(source_dir)
    facts = _py_facts(py)
    commit = _git(["rev-parse", "HEAD"], src)
    if not re.fullmatch(r"[0-9a-f]{40}", commit or ""):
        return None
    diff = _git(["diff", "HEAD", "--binary"], src, binary=True) or b""
    untracked = _git(["ls-files", "--others", "--exclude-standard"], src)
    h = hashlib.sha256(diff)
    for rel in sorted(filter(None, untracked.splitlines())):
        f = src / rel
        if f.is_file() and f.stat().st_size < 50_000_000:
            h.update(rel.encode() + b"\0" + f.read_bytes())
    venv = facts["prefix"]

    def norm(v: str) -> str:
        v = str(v)
        for p, ph in ((facts["platlib"], SITE_PH), (venv, VENV_PH), (str(ws) if ws else None, "@WS@"),
                      (str(src), "@SRC@")):
            if p:
                v = v.replace(p, ph)
        return v
    env = {k: norm(v) for k, v in sorted(pip_env.items()) if ENV_KEY_RX.match(k)}
    modules = ""
    if env_script and Path(env_script).is_file():
        modules = norm("\n".join(ln.strip() for ln in Path(env_script).read_text(errors="replace").splitlines()
                                 if ln.strip() and not ln.strip().startswith("#")))
    feats = {k[len("DFTRACER_"):].lower(): v for k, v in env.items() if k.startswith(("DFTRACER_ENABLE", "DFTRACER_DISABLE",
                                                                                       "DFTRACER_BUILD"))}
    combo = {"system": _system_name(), "arch": facts["arch"], "glibc": facts["glibc"], "python_tag": facts["abi"],
             "platform": facts["platform"], "dftracer_repo": re.sub(r"\.git$", "", repo_url.rstrip("/")),
             "dftracer_ref": ref, "dftracer_commit": commit, "source_diff_sha": h.hexdigest(),
             "build_env": env, "modules": modules}
    key = hashlib.sha256(json.dumps(combo, sort_keys=True).encode()).hexdigest()
    mpi = " ".join(f"{k}={v}" for k, v in env.items() if k.startswith(("MPI", "BRAHMA_MPI"))) or \
          ("on" if env.get("DFTRACER_ENABLE_MPI") == "ON" else "off")
    hdf5 = " ".join(f"{k}={v}" for k, v in env.items() if k.startswith("HDF5_")) or \
           ("on" if env.get("DFTRACER_ENABLE_HDF5") == "ON" else "off")
    comps = " ".join(f"{k}={v}" for k, v in env.items() if k in ("CC", "CXX", "FC")) or \
            " ".join(m for m in modules.split() if re.search(r"(cce|gcc|clang|intel|nvhpc|rocm|PrgEnv)", m))
    return {**combo, "key": key, "env_meta": {"mpi": mpi, "hdf5": hdf5, "compilers": comps, "features": feats,
                                              "cmake_args": env.get("DFTRACER_CMAKE_ARGS", "")},
            "facts": facts}


# ------------------------------------------------------------------ lookup / install
def lookup(combo: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    rt = root()
    if not rt or not (rt / "index.sqlite").exists():
        return None
    with _db(rt) as con:
        row = con.execute("SELECT * FROM wheels WHERE key=? AND status='ok'", (combo["key"],)).fetchone()
    if not row:
        return None
    row = dict(row)
    p = Path(row["wheel_path"])
    if not p.is_file() or _sha256(p) != row["wheel_sha256"]:
        mark(row["key"], "broken", "wheel file missing or its sha256 does not match the index")
        return None
    return row


def mark(key: str, status: str, reason: str = "") -> None:
    rt = root()
    if not rt:
        return
    with _db(rt) as con:
        con.execute("UPDATE wheels SET status=?, status_reason=? WHERE key=?", (status, reason[:1000], key))


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def relocate(platlib: str, venv: str, files: Iterable[str]) -> int:
    """Fill the placeholders in the listed text files (paths relative to site-packages) for this venv."""
    n = 0
    for rel in files:
        f = Path(platlib) / rel
        if f.is_file():
            t = f.read_text(errors="surrogateescape")
            if SITE_PH in t or VENV_PH in t:
                f.write_text(t.replace(SITE_PH, platlib).replace(VENV_PH, venv), errors="surrogateescape")
                n += 1
    return n


def sanity(py: str, platlib: str) -> Tuple[bool, str]:
    """dftracer imports, and its own libraries resolve each other through the rewritten RPATHs."""
    r = subprocess.run([py, "-c", "import dftracer, dftracer.dftracer; print('ok')"], capture_output=True, text=True,
                       timeout=120)
    if r.returncode != 0:
        return False, "import dftracer.dftracer failed: " + (r.stderr or r.stdout)[-600:]
    d = Path(platlib) / "dftracer"
    for lib in list((d / "lib64").glob("libdftracer_*.so")) + list((d / "lib").glob("libdftracer_*.so")):
        out = subprocess.run(["ldd", str(lib)], capture_output=True, text=True, timeout=60).stdout
        own = [ln.split()[0] for ln in out.splitlines() if "not found" in ln and
               ((d / "lib64" / ln.split()[0]).exists() or (d / "lib" / ln.split()[0]).exists())]
        if own:
            return False, f"{lib.name}: dftracer's own libraries not found through RPATH: {own}"
    return True, "ok"


def install_cached(row: Dict[str, Any], py: str, pip_install) -> Dict[str, Any]:
    """Install a prebuilt wheel: pip (deps from the index as usual), fill paths, sanity check.
    ``pip_install(args) -> result`` runs pip the same way the build path does (modules loaded, same env).
    Returns the pip result plus ``wheelhouse``; on any failure the wheel is marked broken and success is False."""
    t0 = time.time()
    r = pip_install(["install", "--upgrade", "--force-reinstall", "--no-deps", row["wheel_path"]])
    if r.get("success"):
        r2 = pip_install(["install", row["wheel_path"]])        # its dependencies (dftracer-utils, pydftracer, ...)
        r = {**r, "deps": {k: r2.get(k) for k in ("success", "returncode")}}
        r["success"] = bool(r2.get("success"))
    if not r.get("success"):
        mark(row["key"], "broken", "pip install of the wheel failed: " + str(r.get("stderr", ""))[-400:])
        return {**r, "wheelhouse": {"hit": True, "used": False, "reason": "pip install of the prebuilt wheel failed"}}
    facts = _py_facts(py)
    fixed = relocate(facts["platlib"], facts["prefix"], json.loads(row["relocate_json"] or "[]"))
    ok, why = sanity(py, facts["platlib"])
    if not ok:
        mark(row["key"], "broken", why)
        return {**r, "success": False, "wheelhouse": {"hit": True, "used": False, "reason": why}}
    rt = root()
    with _db(rt) as con:
        con.execute("UPDATE wheels SET used_count=used_count+1, last_used=? WHERE key=?", (time.time(), row["key"]))
    return {**r, "success": True, "wheelhouse": {
        "hit": True, "used": True, "wheel": row["wheel_path"], "key": row["key"], "built": row["created"],
        "install_seconds": round(time.time() - t0, 1), "build_seconds_saved": row["build_seconds"],
        "relocated_files": fixed,
        "message": f"installed the prebuilt wheel {Path(row['wheel_path']).name} (built "
                   f"{time.strftime('%Y-%m-%d %H:%M', time.localtime(row['created']))} for this exact combination) "
                   f"in {round(time.time() - t0)} s instead of building from source "
                   f"(~{round((row['build_seconds'] or 0) / 60, 1)} min)"}}


# ------------------------------------------------------------------ harvest (pack a fresh build)
def _record_paths(distinfo: Path) -> List[str]:
    rec = distinfo / "RECORD"
    if not rec.exists():
        return []
    return [row[0] for row in csv.reader(io.StringIO(rec.read_text())) if row]


def _is_elf(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            return fh.read(4) == b"\x7fELF"
    except OSError:
        return False


def _is_text(p: Path) -> bool:
    try:
        with open(p, "rb") as fh:
            chunk = fh.read(8192)
        return b"\0" not in chunk
    except OSError:
        return False


def new_rpath(old: str, file_dir: Path, old_tree: Path) -> str:
    """$ORIGIN-relative for entries inside the old dftracer tree; keep system dirs; drop temp / cwd-relative ones."""
    out: List[str] = []
    for e in filter(None, old.split(":")):
        if e.startswith("$ORIGIN"):
            n = e
        elif e.startswith(str(old_tree)):
            rel = os.path.relpath(e, file_dir)
            n = "$ORIGIN" if rel == "." else "$ORIGIN/" + rel
        elif not e.startswith("/") or re.match(r"^/(var/)?tmp/", e):
            continue                                   # cwd-relative or a build directory that no longer exists
        else:
            n = e
        if n not in out:
            out.append(n)
    return ":".join(out)


def _b64(digest: bytes) -> str:
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def harvest(py: str, combo: Dict[str, Any], build_seconds: float, run_id: str = "") -> Dict[str, Any]:
    """Pack the dftracer build just installed into ``py``'s venv into a relocatable wheel and index it."""
    rt = root()
    if not rt:
        return {"stored": False, "reason": "wheelhouse disabled"}
    patchelf = shutil.which("patchelf")
    if not patchelf:
        return {"stored": False, "reason": "patchelf not found: cannot make the wheel relocatable"}
    facts = combo.get("facts") or _py_facts(py)
    site, venv = Path(facts["platlib"]), facts["prefix"]
    di = sorted(site.glob("dftracer-*.dist-info"))
    if not di:
        return {"stored": False, "reason": "no dftracer dist-info in the venv"}
    di = di[-1]
    meta = di.joinpath("METADATA").read_text()
    version = re.search(r"^Version:\s*(\S+)", meta, re.M).group(1)
    tag = re.search(r"^Tag:\s*(\S+)", di.joinpath("WHEEL").read_text(), re.M).group(1)
    others = set()
    for name in OTHER_OWNERS:
        for odi in site.glob(f"{name}-*.dist-info"):
            others.update(_record_paths(odi))
    own = [p for p in _record_paths(di) if not p.startswith(di.name + "/")]
    tree = site / "dftracer"
    files = sorted({str(p.relative_to(site)) for p in tree.rglob("*") if p.is_file() and "__pycache__" not in p.parts}
                   - others - {p for p in own if p.startswith("../")})
    files += [p for p in own if p.startswith("dftracer/") and p not in files and "__pycache__" not in p
              and (site / p).is_file()]
    scripts = [p for p in own if p.startswith("../") and (site / p).is_file()]
    newver = f"{version.split('+')[0]}+wh.{combo['key'][:12]}"
    dist = f"dftracer-{newver}"
    relocate_list: List[str] = []
    stats = {"elf_rewritten": 0, "binary_refs": 0}
    with tempfile.TemporaryDirectory(prefix="dftracer_wheel_") as td:
        stage = Path(td) / "stage"
        for rel in files:
            src, dst = site / rel, stage / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst, follow_symlinks=True)
            if _is_elf(dst):
                cur = subprocess.run([patchelf, "--print-rpath", str(dst)], capture_output=True, text=True).stdout.strip()
                if cur:
                    nr = new_rpath(cur, (site / rel).parent, tree)
                    subprocess.run([patchelf, "--force-rpath", "--set-rpath", nr, str(dst)], check=True,
                                   capture_output=True)
                    stats["elf_rewritten"] += 1
                if str(site).encode() in dst.read_bytes():
                    stats["binary_refs"] += 1          # a path compiled into a binary: cannot be rewritten
            elif _is_text(dst):
                t = dst.read_text(errors="surrogateescape")
                if str(site) in t or venv in t:
                    dst.write_text(t.replace(str(site), SITE_PH).replace(venv, VENV_PH), errors="surrogateescape")
                    relocate_list.append(rel)
        for s in scripts:                                # ../../../bin/<x> -> <dist>.data/scripts/<x>
            dst = stage / f"{dist}.data" / "scripts" / Path(s).name
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(site / s, dst)
        ndi = stage / f"{dist}.dist-info"
        ndi.mkdir(parents=True)
        ndi.joinpath("METADATA").write_text(re.sub(r"^Version:.*$", f"Version: {newver}", meta, count=1, flags=re.M))
        ndi.joinpath("WHEEL").write_text(f"Wheel-Version: 1.0\nGenerator: dftracer-agents wheelhouse\n"
                                         f"Root-Is-Purelib: false\nTag: {tag}\n")
        for extra in ("top_level.txt", "licenses"):
            p = di / extra
            if p.is_file():
                shutil.copy2(p, ndi / extra)
            elif p.is_dir():
                shutil.copytree(p, ndi / extra)
        ndi.joinpath("wheelhouse.json").write_text(json.dumps({
            "key": combo["key"], "relocate": relocate_list, "placeholders": [SITE_PH, VENV_PH],
            "combination": {k: v for k, v in combo.items() if k not in ("facts",)}}, indent=1, default=str))
        rows = []
        for p in sorted(stage.rglob("*")):
            if p.is_file():
                b = p.read_bytes()
                rows.append([str(p.relative_to(stage)), "sha256=" + _b64(hashlib.sha256(b).digest()), str(len(b))])
        rows.append([f"{dist}.dist-info/RECORD", "", ""])
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(rows)
        ndi.joinpath("RECORD").write_text(buf.getvalue())
        out_dir = rt / combo["system"] / combo["python_tag"]
        out_dir.mkdir(parents=True, exist_ok=True)
        wheel = out_dir / f"{dist}-{tag}.whl"
        tmpw = out_dir / f".{wheel.name}.{os.getpid()}.tmp"
        with zipfile.ZipFile(tmpw, "w", zipfile.ZIP_DEFLATED) as z:
            for p in sorted(stage.rglob("*")):
                if p.is_file():
                    z.write(p, str(p.relative_to(stage)))
        os.replace(tmpw, wheel)
        _share(out_dir.parent, out_dir, wheel)
    em = combo["env_meta"]
    with _db(rt) as con:
        con.execute("""INSERT INTO wheels (key, status, created, last_used, used_count, build_seconds, system, arch,
                       glibc, python_tag, platform, dftracer_version, dftracer_repo, dftracer_ref, dftracer_commit,
                       source_diff_sha, mpi, hdf5, compilers, features_json, cmake_args, modules, build_env_json,
                       wheel_path, wheel_sha256, wheel_bytes, relocate_json, built_by_run)
                       VALUES (?, 'ok', ?, NULL, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(key) DO UPDATE SET status='ok', status_reason=NULL, created=excluded.created,
                       build_seconds=excluded.build_seconds, wheel_path=excluded.wheel_path,
                       wheel_sha256=excluded.wheel_sha256, wheel_bytes=excluded.wheel_bytes,
                       relocate_json=excluded.relocate_json""",
                    (combo["key"], time.time(), round(build_seconds, 1), combo["system"], combo["arch"], combo["glibc"],
                     combo["python_tag"], combo["platform"], newver, combo["dftracer_repo"], combo["dftracer_ref"],
                     combo["dftracer_commit"], combo["source_diff_sha"], em["mpi"], em["hdf5"], em["compilers"],
                     json.dumps(em["features"], sort_keys=True), em["cmake_args"], combo["modules"],
                     json.dumps(combo["build_env"], sort_keys=True), str(wheel), _sha256(wheel), wheel.stat().st_size,
                     json.dumps(relocate_list), run_id))
    return {"stored": True, "wheel": str(wheel), "key": combo["key"], "files": len(files),
            "relocate_files": len(relocate_list), **stats,
            "message": f"built fresh (no prebuilt wheel for this combination) and stored it as {wheel.name} "
                       f"for the next install"}


# ------------------------------------------------------------------ search
COLS = ("id", "status", "created", "used_count", "build_seconds", "system", "arch", "python_tag", "dftracer_version",
        "dftracer_repo", "dftracer_ref", "dftracer_commit", "mpi", "hdf5", "compilers", "features_json", "cmake_args",
        "wheel_path", "wheel_bytes", "key", "status_reason")


def search(text: str = "", system: str = "", python_tag: str = "", ref: str = "", commit: str = "",
           include_broken: bool = False, limit: int = 50) -> Dict[str, Any]:
    """Rows of the index matching the filters (substring match on every text column for ``text``)."""
    rt = root()
    if not rt:
        return {"status": "error", "error": "wheelhouse disabled (DFTRACER_WHEELHOUSE=off)"}
    if not (rt / "index.sqlite").exists():
        return {"status": "ok", "wheelhouse": str(rt), "wheels": [], "count": 0}
    q, args = [], []
    for col, val in (("system", system), ("python_tag", python_tag), ("dftracer_ref", ref)):
        if val:
            q.append(f"{col}=?")
            args.append(val)
    if commit:
        q.append("dftracer_commit LIKE ?")
        args.append(commit + "%")
    if not include_broken:
        q.append("status='ok'")
    if text:
        like = " OR ".join(f"{c} LIKE ?" for c in ("mpi", "hdf5", "compilers", "features_json", "cmake_args",
                                                    "modules", "dftracer_version", "dftracer_repo"))
        q.append(f"({like})")
        args += [f"%{text}%"] * 8
    sql = f"SELECT {', '.join(COLS)} FROM wheels {'WHERE ' + ' AND '.join(q) if q else ''} ORDER BY created DESC LIMIT ?"
    with _db(rt) as con:
        rows = [dict(r) for r in con.execute(sql, (*args, int(limit))).fetchall()]
        total = con.execute("SELECT COUNT(*), COALESCE(SUM(wheel_bytes),0), COALESCE(SUM(used_count),0) FROM wheels "
                            "WHERE status='ok'").fetchone()
    return {"status": "ok", "wheelhouse": str(rt), "index": str(rt / "index.sqlite"), "count": len(rows),
            "wheels": rows, "totals": {"wheels": total[0], "bytes": total[1], "installs_served": total[2]}}
