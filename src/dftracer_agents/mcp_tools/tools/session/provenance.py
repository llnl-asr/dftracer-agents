"""MCP tools for dftracer *provenance mode*.

Provenance mode instruments an app so its trace records the lineage of every
scientific artifact: which entities (typed, uniquely identified instances of
data/science structures in memory, files, databases) were consumed and produced
by which activities.  The runtime event model lives in
:mod:`provenance_templates`; these tools cover the deterministic parts of the
pipeline around it:

* ``provenance_inventory_outputs`` -- what did the plain run produce? (artifact candidates)
* ``provenance_scan_candidates``   -- where in the code are things read/written/created?
* ``provenance_write_spec``        -- validate + persist the user-approved entity spec
* ``provenance_install_helpers``   -- drop dftracer_prov.h / dftracer_prov.py into the tree
* ``provenance_insert``            -- idempotent, indentation-aware line insertion
* ``provenance_validate``          -- static check: annotations cover the spec
* ``provenance_extract_graph``     -- traces -> provenance graph (JSON + DOT) + lineage checks

Judgement (which artifact matters, which entities to track) stays with the
agent and the user; see the ``dftracer-provenance`` skill.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import shutil
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from fastmcp import FastMCP

from .provenance_templates import PROV_C_HEADER, PROV_PY_MODULE
from .workspace import _ws, _ok, _err, _safe_session_path

PROV_RUN = "provenance"
DEFAULT_SOURCE = f"{PROV_RUN}/source"
SPEC_REL = f"{PROV_RUN}/spec.yaml"
MARK_C = "/* dft-prov */"
MARK_PY = "# dft-prov"
HELPER_FILES = {"dftracer_prov.h", "dftracer_prov.py"}

C_EXT = {".c", ".h"}
CPP_EXT = {".cc", ".cpp", ".cxx", ".C", ".hpp", ".hh", ".hxx", ".cu", ".hip"}
PY_EXT = {".py"}
SKIP_DIRS = {".git", "build", "_build", "__pycache__", ".venv", "venv", "node_modules",
             "CMakeFiles", "dftracer_install", "traces"}

# extensions that usually hold a scientific artifact, by domain hint
SCIENCE_EXT = {
    ".pdb": "molecular structure", ".cif": "molecular structure", ".mmcif": "molecular structure",
    ".sdf": "molecule", ".mol2": "molecule", ".xyz": "atomic coords", ".gro": "MD structure",
    ".xtc": "MD trajectory", ".trr": "MD trajectory", ".dcd": "MD trajectory", ".nc": "netCDF field",
    ".h5": "HDF5 dataset", ".hdf5": "HDF5 dataset", ".he5": "HDF5 dataset", ".bp": "ADIOS2 data",
    ".vtk": "mesh/field", ".vtu": "mesh/field", ".pvtu": "mesh/field", ".xdmf": "mesh/field",
    ".silo": "mesh/field", ".exo": "mesh", ".e": "mesh", ".fits": "astronomy image",
    ".vcf": "variants", ".bam": "alignments", ".sam": "alignments", ".fa": "sequence",
    ".fasta": "sequence", ".fastq": "reads", ".a3m": "MSA", ".sto": "MSA",
    ".npy": "array", ".npz": "arrays", ".pt": "model/tensor", ".pth": "model/tensor",
    ".ckpt": "checkpoint", ".safetensors": "model weights", ".onnx": "model",
    ".csv": "table", ".parquet": "table", ".json": "record", ".sqlite": "database",
    ".db": "database", ".zarr": "array store", ".tif": "image", ".png": "image/plot",
}

# (regex, kind, family) -- kind: read|write|open|create|db|exec (access/store),
# communicate (data moving between ranks/devices/services), compute (in-memory transform)
_CANDIDATE_PATTERNS: List[Tuple[str, str, str]] = [
    # C / C++
    (r"\bfopen\s*\(", "open", "c"), (r"\bopen\s*\(", "open", "c"),
    (r"\bfwrite\s*\(", "write", "c"), (r"\bfread\s*\(", "read", "c"),
    (r"\bH5Fcreate\s*\(", "create", "c"), (r"\bH5Fopen\s*\(", "open", "c"),
    (r"\bH5Dcreate\d?\s*\(", "create", "c"), (r"\bH5Dwrite\s*\(", "write", "c"),
    (r"\bH5Dread\s*\(", "read", "c"), (r"\bMPI_File_open\s*\(", "open", "c"),
    (r"\bMPI_File_write\w*\s*\(", "write", "c"), (r"\bMPI_File_read\w*\s*\(", "read", "c"),
    (r"\bnc_create\w*\s*\(", "create", "c"), (r"\bnc_open\w*\s*\(", "open", "c"),
    (r"\bnc_put_var\w*\s*\(", "write", "c"), (r"\bnc_get_var\w*\s*\(", "read", "c"),
    (r"\bstd::ofstream\b|\bofstream\b", "write", "c"), (r"\bstd::ifstream\b|\bifstream\b", "read", "c"),
    (r"\badios2::\w+|\bDefineVariable\b", "write", "c"), (r"\bsqlite3_open\w*\s*\(", "db", "c"),
    (r"\bsqlite3_exec\s*\(", "db", "c"), (r"\bmkdir\s*\(", "create", "c"),
    (r"\bsilo|DBPutQuadmesh|DBCreate\b", "write", "c"), (r"\bvtk\w*Writer\b", "write", "c"),
    # Python
    (r"\bopen\s*\(", "open", "py"), (r"\bnp\.(save|savez\w*|savetxt|tofile)\b", "write", "py"),
    (r"\bnp\.(load|loadtxt|fromfile|genfromtxt)\b", "read", "py"),
    (r"\btorch\.save\b", "write", "py"), (r"\btorch\.load\b", "read", "py"),
    (r"\bh5py\.File\b", "open", "py"), (r"\bpd\.read_\w+", "read", "py"),
    (r"\.to_(csv|parquet|hdf|json|pickle|feather)\s*\(", "write", "py"),
    (r"\bjson\.dump\b", "write", "py"), (r"\bjson\.load\b", "read", "py"),
    (r"\bpickle\.dump\b", "write", "py"), (r"\bpickle\.load\b", "read", "py"),
    (r"\bsqlite3\.connect\b|\bcreate_engine\b|\bpymongo\b", "db", "py"),
    (r"\bxr\.open_\w+|\.to_netcdf\b|\bnetCDF4\.Dataset\b", "open", "py"),
    (r"\bPDBParser|MMCIFParser|PDBIO|MMCIFIO|gemmi\.|mdtraj\.|MDAnalysis", "open", "py"),
    (r"\bsave_pretrained\b|\bfrom_pretrained\b", "open", "py"),
    (r"\bsavefig\b|\bimwrite\b|\bimsave\b", "write", "py"),
    (r"\bshutil\.(copy\w*|move)\b|\bos\.(rename|replace)\b", "create", "py"),
    (r"\bsubprocess\.\w+\(|\bos\.system\(", "exec", "py"),
    # communicate: data moving between processes / nodes / services
    (r"\bMPI_(I?[sS]end|I?[rR]ecv|Sendrecv\w*|Put|Get|Accumulate)\s*\(", "communicate", "c"),
    (r"\bMPI_(I?(All)?[rR]educe\w*|I?Bcast|I?(All)?[gG]ather\w*|I?[sS]catter\w*|I?Alltoall\w*)\s*\(", "communicate", "c"),
    (r"\b(nccl|rccl)\w+\s*\(|\bsend\s*\(|\brecv\s*\(|\bcurl_easy_perform\b", "communicate", "c"),
    (r"\bcomm\.(send|recv|isend|irecv|bcast|gather|scatter|allreduce|reduce|allgather|alltoall|Send|Recv|Bcast|Gather|Scatter|Allreduce|Reduce|Allgather)\b", "communicate", "py"),
    (r"\bdist\.(all_reduce|broadcast|all_gather\w*|reduce_scatter\w*|send|recv|gather|scatter)\b", "communicate", "py"),
    (r"\brequests\.(get|post|put)\b|\bsocket\.|\bray\.(get|put)\b|\.remote\(|\bqueue\.(put|get)\b", "communicate", "py"),
    # compute: in-memory transformations of data (accelerator copies, model steps)
    (r"\b(cuda|hip)Memcpy\w*\s*\(|\bKokkos::deep_copy\b", "communicate", "c"),
    (r"\.(fit|predict|forward|backward|transform|fit_transform)\s*\(|\boptimizer\.step\b", "compute", "py"),
    (r"\.to\(\s*['\"]?(cuda|cpu|device)|\.cuda\(\)|\.cpu\(\)", "communicate", "py"),
]


# ---------------------------------------------------------------------------
# pure helpers (unit-testable without the MCP server)
# ---------------------------------------------------------------------------

def prov_hash(etype: str, eid: str) -> str:
    """FNV-1a-64(type + 0x1f + id), identical to dftracer_prov.h / .py."""
    h = 1469598103934665603
    for b in str(etype).encode() + b"\x1f" + str(eid).encode():
        h ^= b
        h = (h * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return "%016x" % h


def _lang_of(p: Path) -> Optional[str]:
    if p.suffix in PY_EXT:
        return "python"
    if p.suffix in C_EXT:
        return "c"
    if p.suffix in CPP_EXT:
        return "cpp"
    return None


def _iter_sources(root: Path, max_files: int = 20000) -> Iterable[Path]:
    n = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            p = Path(dirpath) / fn
            if _lang_of(p):
                yield p
                n += 1
                if n >= max_files:
                    return


_FUNC_RE = {
    "python": re.compile(r"^\s*(?:async\s+)?def\s+(\w+)\s*\("),
    "c": re.compile(r"^[A-Za-z_][\w\s\*:<>,&~]*?\b(\w+)\s*\([^;]*$"),
}
_NOT_FUNCS = {"if", "for", "while", "switch", "return", "sizeof", "catch", "elif"}


def scan_candidates(root: Path, include: str = "", max_hits: int = 400) -> Dict[str, Any]:
    """Regex sweep for entity touch-points (I/O, DB, object creation).

    Returns hits grouped by file with the enclosing function (best effort) so an
    agent can open only the named lines.
    """
    inc = re.compile(include) if include else None
    compiled = [(re.compile(rx), kind, lang) for rx, kind, lang in _CANDIDATE_PATTERNS]
    hits: List[Dict[str, Any]] = []
    per_kind: Counter = Counter()
    files_scanned = 0
    for p in _iter_sources(root):
        rel = str(p.relative_to(root))
        if inc and not inc.search(rel):
            continue
        lang = _lang_of(p)
        fam = "py" if lang == "python" else "c"
        try:
            lines = p.read_text(errors="replace").splitlines()
        except OSError:
            continue
        files_scanned += 1
        func = None
        frx = _FUNC_RE["python" if fam == "py" else "c"]
        for i, line in enumerate(lines, 1):
            s = line.strip()
            if not s or s.startswith("#") or (fam == "c" and s.startswith(("//", "/*", "*"))):
                continue
            m = frx.match(line)
            if m and not s.endswith(";") and m.group(1) not in _NOT_FUNCS:
                func = m.group(1)
            for rx, kind, plang in compiled:
                if plang != fam:
                    continue
                if rx.search(line):
                    per_kind[kind] += 1
                    if len(hits) < max_hits:
                        hits.append({"file": rel, "line": i, "function": func, "kind": kind,
                                     "code": s[:160]})
                    break
    by_file: Dict[str, int] = Counter(h["file"] for h in hits)
    return {"files_scanned": files_scanned, "total_hits": sum(per_kind.values()),
            "by_kind": dict(per_kind), "top_files": by_file.most_common(25),
            "hits": hits, "truncated": sum(per_kind.values()) > len(hits)}


def inventory_outputs(root: Path, max_entries: int = 200000, newer_than: float = 0.0) -> Dict[str, Any]:
    """Group files under *root* by extension; flag likely scientific formats."""
    by_ext: Dict[str, Dict[str, Any]] = {}
    n = 0
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
        for fn in filenames:
            p = Path(dirpath) / fn
            try:
                st = p.lstat()
            except OSError:
                continue
            if newer_than and st.st_mtime < newer_than:
                continue
            ext = p.suffix.lower() or "<none>"
            e = by_ext.setdefault(ext, {"count": 0, "bytes": 0, "examples": [],
                                        "hint": SCIENCE_EXT.get(ext, "")})
            e["count"] += 1
            e["bytes"] += st.st_size
            if len(e["examples"]) < 5:
                e["examples"].append(str(p.relative_to(root)))
            n += 1
            if n >= max_entries:
                break
        if n >= max_entries:
            break
    ranked = sorted(by_ext.items(), key=lambda kv: (kv[1]["hint"] == "", -kv[1]["bytes"]))
    return {"files": n, "truncated": n >= max_entries,
            "extensions": [{"ext": k, **v} for k, v in ranked],
            "likely_science": [k for k, v in ranked if v["hint"]]}


def validate_spec(spec: Dict[str, Any]) -> List[str]:
    """Return a list of problems with a provenance spec (empty == valid)."""
    probs: List[str] = []
    types = spec.get("entity_types") or []
    acts = spec.get("activities") or []
    arts = spec.get("scientific_artifacts") or []
    if not arts:
        probs.append("scientific_artifacts is empty -- name the artifact type(s) the user agreed on")
    if not types:
        probs.append("entity_types is empty")
    names = set()
    for t in types:
        if not isinstance(t, dict) or not t.get("name"):
            probs.append(f"entity_type without name: {t!r}")
            continue
        if t["name"] in names:
            probs.append(f"duplicate entity_type {t['name']}")
        names.add(t["name"])
        if not t.get("id_scheme"):
            probs.append(f"entity_type {t['name']}: id_scheme missing (how is a unique id formed?)")
        if t.get("role") not in ("input", "output", "intermediate"):
            probs.append(f"entity_type {t['name']}: role must be input | output | intermediate "
                         f"(got {t.get('role')!r})")
        desc = (t.get("description") or "").strip()
        if len(desc) < 20:
            probs.append(f"entity_type {t['name']}: description missing or too short -- say what "
                         "this type represents in the workflow (users read it; queries are mapped by it)")
        if t.get("store") not in (None, "memory", "file", "db", "object", "network", "gpu", "other"):
            probs.append(f"entity_type {t['name']}: unknown store {t.get('store')!r}")
    roles = {t["name"]: t.get("role") for t in types if isinstance(t, dict) and t.get("name")}
    for a in arts:
        if a not in names:
            probs.append(f"scientific artifact {a!r} is not declared in entity_types")
        elif roles.get(a) != "output":
            probs.append(f"scientific artifact {a!r} must have role: output")
    touched_used, touched_gen = set(), set()
    for a in acts:
        if not isinstance(a, dict) or not a.get("name"):
            probs.append(f"activity without name: {a!r}")
            continue
        if not a.get("where"):
            probs.append(f"activity {a['name']}: 'where' (file:function) missing")
        if not (a.get("used") or a.get("generated")):
            probs.append(f"activity {a['name']}: has neither used nor generated")
        for k, bucket in (("used", touched_used), ("generated", touched_gen)):
            for t in a.get(k) or []:
                if t not in names:
                    probs.append(f"activity {a['name']}: {k} type {t!r} not declared")
                bucket.add(t)
    for a in arts:
        if a in names and a not in touched_gen:
            probs.append(f"scientific artifact {a!r} is generated by no activity")
    for n in names - touched_used - touched_gen:
        probs.append(f"entity_type {n!r} participates in no activity (orphan)")
    return probs


_PROV_CALL = re.compile(
    r"""(?:dft_prov_(?:used|generated|invalidated|updated|entity)\s*\(\s*&?\w*\s*,?\s*|\.(?:used|generated|invalidated|updated)\s*\(\s*|prov\.entity\s*\(\s*|dftprov::entity\s*\(\s*)["']([\w.\-:/]+)["']""")
_PROV_ACT = re.compile(
    r"""(?:DFT_PROV_ACTIVITY\s*\(\s*\w+\s*,\s*"[^"]*"\s*,\s*|dftprov::Activity\s+\w+\s*\(\s*"[^"]*"\s*,\s*|activity\s*\(\s*["'][^"']*["']\s*,\s*|traced\s*\(\s*["'][^"']*["']\s*,\s*)["']([\w.\-:/]+)["']""")


def static_check(root: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Compare provenance calls found in *root* with the spec."""
    seen_types: Counter = Counter()
    seen_acts: Counter = Counter()
    issues: List[str] = []
    files_with_calls: List[str] = []
    for p in _iter_sources(root):
        if p.name in HELPER_FILES:  # the generated helper's docstring examples are not app calls
            continue
        try:
            txt = p.read_text(errors="replace")
        except OSError:
            continue
        if "dft_prov" not in txt and "dftprov" not in txt and "prov." not in txt:
            continue
        types = _PROV_CALL.findall(txt)
        acts = _PROV_ACT.findall(txt)
        if not types and not acts:
            continue
        rel = str(p.relative_to(root))
        files_with_calls.append(rel)
        seen_types.update(types)
        seen_acts.update(acts)
        lang = _lang_of(p)
        if lang in ("c", "cpp") and "dftracer_prov.h" not in txt:
            issues.append(f"{rel}: uses dft_prov but does not #include \"dftracer_prov.h\"")
        if lang == "python" and not re.search(r"import\s+dftracer_prov|from\s+\S*dftracer_prov", txt):
            issues.append(f"{rel}: uses prov.* but does not import dftracer_prov")
        if lang == "c" and re.search(r"dft_prov_begin\s*\(", txt) and "dft_prov_end" not in txt:
            issues.append(f"{rel}: dft_prov_begin without dft_prov_end (use DFT_PROV_ACTIVITY for auto-end)")
    spec_types = {t["name"] for t in spec.get("entity_types") or [] if isinstance(t, dict)}
    regs = list(root.rglob("dftracer_prov_types.py")) + list(root.rglob("dftracer_prov_types.h"))
    if not regs:
        issues.append("no dftracer_prov_types registry -- re-run provenance_install_helpers "
                      "so entity type descriptions are emitted")
    for r in regs:
        txt = r.read_text(errors="replace")
        stale = sorted(t for t in spec_types if repr(t) not in txt and f'"{t}"' not in txt)
        if stale:
            issues.append(f"{r.relative_to(root)}: type registry is stale (missing {stale}) -- "
                          "re-run provenance_install_helpers")
    spec_acts = {a.get("activity_type") or a["name"] for a in spec.get("activities") or []
                 if isinstance(a, dict) and a.get("name")}
    missing_types = sorted(spec_types - set(seen_types))
    missing_acts = sorted(spec_acts - set(seen_acts))
    undeclared = sorted(set(seen_types) - spec_types)
    for t in missing_types:
        issues.append(f"entity type {t!r} from spec is never recorded in code")
    for a in missing_acts:
        issues.append(f"activity {a!r} from spec is never opened in code")
    for t in undeclared:
        issues.append(f"entity type {t!r} recorded in code but not in spec (add it or fix the literal)")
    return {"passed": not issues, "issues": issues, "files_with_calls": files_with_calls,
            "entity_type_calls": dict(seen_types), "activity_calls": dict(seen_acts)}


def insert_lines(path: Path, line: int, code: str, position: str = "after",
                 language: Optional[str] = None) -> Dict[str, Any]:
    """Insert *code* before/after 1-based *line*, matching indentation; idempotent.

    Each inserted line gets a ``dft-prov`` marker so re-running is a no-op and the
    instrumentation is greppable.
    """
    lang = language or _lang_of(path) or "c"
    mark = MARK_PY if lang == "python" else MARK_C
    lines = path.read_text().splitlines(keepends=True)
    if not (1 <= line <= len(lines) + (1 if position == "after" else 0)):
        raise ValueError(f"line {line} out of range 1..{len(lines)}")
    anchor = lines[min(line, len(lines)) - 1]
    indent = re.match(r"[ \t]*", anchor).group(0)
    if position == "after" and lang == "python" and anchor.rstrip().endswith(":"):
        indent += "    "
    if position == "after" and lang != "python" and anchor.rstrip().endswith("{"):
        indent += "  "
    new = []
    for c in code.splitlines():
        if not c.strip():
            continue
        new.append(f"{indent}{c.strip()}  {mark}\n")
    window = "".join(lines[max(0, line - 4): line + len(new) + 3])
    if all(n.strip() in window for n in new):
        return {"inserted": 0, "already_present": True}
    idx = line if position == "after" else line - 1
    lines[idx:idx] = new
    path.write_text("".join(lines))
    return {"inserted": len(new), "already_present": False, "at_line": idx + 1}


def _open_trace(p: Path):
    return gzip.open(p, "rt", errors="replace") if p.suffix == ".gz" else open(p, errors="replace")


# dftracer entity API (core/common/entity.h) enum names
_STORE_NAMES = ["memory", "gpu_memory", "local_disk", "parallel_fs", "burst_buffer",
                "object_store", "database", "network", "other"]
_ROLE_NAMES = ["unknown", "input", "output", "intermediate", "parameter", "reference"]
_EREL_NAMES = {16: "derived_from", 17: "revision_of", 18: "contains", 19: "part_of",
               20: "specialization_of", 21: "alternate_of", 22: "depends_on"}
_EVENT_RELS = ("used", "generated", "invalidated", "updated")
_PREFILTER = tuple(f'"{r}":[' for r in _EVENT_RELS) + ('"relations":{',
    '"name":"EH"', '"name":"ET"', '"name":"ER"', "prov")


def _maybe_prov(raw: str) -> bool:
    return any(t in raw for t in _PREFILTER)


def _enum_name(v: str, names, default: str) -> str:
    try:
        i = int(v)
    except (TypeError, ValueError):
        return v or default
    if isinstance(names, dict):
        return names.get(i, default)
    return names[i] if 0 <= i < len(names) else default


def _iter_events(trace_dir: Path) -> Iterable[Dict[str, Any]]:
    if trace_dir.is_file():
        files = [trace_dir]
    else:
        files = sorted(list(trace_dir.rglob("*.pfw")) + list(trace_dir.rglob("*.pfw.gz")))
    for f in files:
        try:
            with _open_trace(f) as fh:
                for raw in fh:
                    raw = raw.strip().rstrip(",")
                    if not raw or raw in ("[", "]") or not _maybe_prov(raw):
                        continue
                    try:
                        yield json.loads(raw)
                    except json.JSONDecodeError:
                        continue
        except (OSError, EOFError):
            continue


PROV_QUERY = 'cat == "PROV" or cat == "PROV_CONT"'


def view_prov_events(trace_dir: Path, out_jsonl: Path, viewer: str, index_dir: Path,
                     threads: int = 0, timeout: int = 3600) -> Dict[str, Any]:
    """Pull PROV/PROV_CONT events (+ the metadata records, which the viewer always
    passes through -- that is where prov_entity lives) with dftracer_view.

    dftracer_view builds a bloom-filter index once and skips chunks without
    PROV events, so this scales to large multi-rank traces where a full
    decompress-and-scan does not.
    """
    import subprocess
    index_dir.mkdir(parents=True, exist_ok=True)
    out_jsonl.parent.mkdir(parents=True, exist_ok=True)
    cmd = [viewer, "-d", str(trace_dir), "--index-dir", str(index_dir), "--query", PROV_QUERY,
           "--log-level", "error"]
    if threads:
        cmd += ["--executor-threads", str(threads), "--io-threads", str(threads)]
    with open(out_jsonl, "w") as fo:
        p = subprocess.run(cmd, stdout=fo, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return {"cmd": " ".join(cmd), "returncode": p.returncode, "stderr": p.stderr[-2000:],
            "lines": sum(1 for _ in open(out_jsonl))}


def build_graph(trace_dir: Path, artifact_types: Optional[List[str]] = None) -> Dict[str, Any]:
    """Parse PROV events + prov_entity metadata into a lineage graph."""
    entities: Dict[str, Dict[str, Any]] = {}
    acts: Dict[str, Dict[str, Any]] = {}
    types: Dict[str, str] = {}
    cont: Dict[str, Dict[str, List[str]]] = defaultdict(lambda: {"prov_used": [], "prov_generated": []})
    meta_events = prov_events = 0
    entity_rels: List[Dict[str, str]] = []
    for ev in _iter_events(trace_dir):
        args = ev.get("args") or {}
        key = args.get("name")
        rec = ev.get("name")
        # ---- dftracer entity API: EH / ET / ER records (structured fields, or
        # the earlier name/value-with-'|' layout) and the relations object
        if rec in ("EH", "ET", "ER"):
            pipe = (str(args.get("value", "")) + "||").split("|")
        if rec == "EH":
            eid = args.get("id") or key
            if not isinstance(eid, str):
                continue
            meta_events += 1
            structured = "id" in args
            typ = args.get("type") if structured else pipe[0]
            store = args.get("store") if structured else pipe[1]
            uri = args.get("uri") if structured else pipe[2]
            e = entities.setdefault(eid, {"hash": eid, "type": typ, "id": eid,
                                          "store": _enum_name(store, _STORE_NAMES, "memory"),
                                          "uri": uri or "", "pids": []})
            if ev.get("pid") not in e["pids"]:
                e["pids"].append(ev.get("pid"))
            continue
        if rec == "ET":
            tname = args.get("type") or key
            structured = "role" in args
            role = args.get("role") if structured else pipe[0]
            desc = args.get("description") if structured else str(args.get("value", "")).partition("|")[2]
            if isinstance(tname, str):
                types.setdefault(tname, {"role": _enum_name(role, _ROLE_NAMES, "unknown"),
                                         "description": desc or ""})
            continue
        if rec == "ER":
            structured = "subject" in args
            rel = args.get("relation") if structured else key
            subj = args.get("subject") if structured else pipe[0]
            obj = args.get("object") if structured else pipe[1]
            if subj and obj:
                entity_rels.append({"relation": _enum_name(rel, _EREL_NAMES, "related"),
                                    "subject": subj, "object": obj})
            continue
        relobj = args.get("relations") if isinstance(args.get("relations"), dict) else args
        if any(isinstance(relobj.get(r), list) for r in _EVENT_RELS):
            prov_events += 1
            aid = f"{ev.get('pid')}:{ev.get('id')}:{ev.get('ts')}"
            acts[aid] = {"aid": aid, "name": ev.get("name"),
                         "activity": args.get("activity") or ev.get("cat"),
                         "pid": ev.get("pid"), "ts": ev.get("ts"), "dur": ev.get("dur"),
                         "used": list(relobj.get("used") or []),
                         "generated": list(relobj.get("generated") or []),
                         "invalidated": list(relobj.get("invalidated") or []),
                         "updated": list(relobj.get("updated") or []),
                         "nused": len(relobj.get("used") or []),
                         "ngen": len(relobj.get("generated") or [])}
            continue
        # ---- legacy dftracer_prov helper format
        if isinstance(key, str) and key.startswith("prov_type:"):
            role, _, desc = str(args.get("value", "")).partition("|")
            types.setdefault(key.split(":", 1)[1], {"role": role, "description": desc})
            continue
        if isinstance(key, str) and key.startswith("prov_entity:"):
            meta_events += 1
            h = key.split(":", 1)[1]
            parts = (str(args.get("value", "")) + "|||").split("|")
            e = entities.setdefault(h, {"hash": h, "type": parts[0], "id": parts[1],
                                         "store": parts[2] or "memory", "uri": parts[3],
                                         "pids": []})
            if ev.get("pid") not in e["pids"]:
                e["pids"].append(ev.get("pid"))
            continue
        cat = ev.get("cat")
        if cat == "PROV_CONT" and "prov_aid" in args:
            for f in ("prov_used", "prov_generated"):
                if args.get(f):
                    cont[args["prov_aid"]][f].extend(x for x in str(args[f]).split(",") if x)
            continue
        if cat == "PROV" and "prov_aid" in args:
            prov_events += 1
            aid = args["prov_aid"]
            acts[aid] = {"aid": aid, "name": ev.get("name"), "activity": args.get("prov_activity"),
                         "pid": ev.get("pid"), "ts": ev.get("ts"), "dur": ev.get("dur"),
                         "used": [x for x in str(args.get("prov_used", "")).split(",") if x],
                         "generated": [x for x in str(args.get("prov_generated", "")).split(",") if x],
                         "nused": int(args.get("prov_nused", 0) or 0),
                         "ngen": int(args.get("prov_ngen", 0) or 0)}
    for aid, c in cont.items():
        if aid in acts:
            acts[aid]["used"] = c["prov_used"] + acts[aid]["used"]
            acts[aid]["generated"] = c["prov_generated"] + acts[aid]["generated"]
    edges: List[Dict[str, str]] = []
    producers: Dict[str, List[str]] = defaultdict(list)
    consumers: Dict[str, List[str]] = defaultdict(list)
    truncated = []
    for a in acts.values():
        if len(a["used"]) != a["nused"] or len(a["generated"]) != a["ngen"]:
            truncated.append(a["aid"])
        for h in a["used"]:
            edges.append({"src": h, "dst": a["aid"], "rel": "used"})
            consumers[h].append(a["aid"])
        for h in a["generated"]:
            edges.append({"src": a["aid"], "dst": h, "rel": "wasGeneratedBy"})
            producers[h].append(a["aid"])
        for rel in ("invalidated", "updated"):
            for h in a.get(rel, []):
                edges.append({"src": a["aid"], "dst": h, "rel": rel})
                consumers[h].append(a["aid"])
    for r in entity_rels:
        edges.append({"src": r["subject"], "dst": r["object"], "rel": r["relation"]})
    referenced = set(producers) | set(consumers) | {
        x for r in entity_rels for x in (r["subject"], r["object"])}
    dangling = sorted(referenced - set(entities))
    isolated = sorted(set(entities) - referenced)
    # lineage closure for each artifact entity
    lineage: Dict[str, Any] = {}
    art = set(artifact_types or [])
    for h, e in entities.items():
        if art and e["type"] not in art:
            continue
        if not art:
            continue
        anc, stack, roots = set(), [h], set()
        while stack:
            cur = stack.pop()
            ps = producers.get(cur, [])
            if not ps and cur != h:
                roots.add(cur)
            for aid in ps:
                for u in acts[aid]["used"]:
                    if u not in anc:
                        anc.add(u)
                        stack.append(u)
        desc, stack = set(), [h]
        while stack:
            cur = stack.pop()
            for aid in consumers.get(cur, []):
                for g in acts[aid]["generated"]:
                    if g not in desc:
                        desc.add(g)
                        stack.append(g)
        lineage[h] = {"type": e["type"], "id": e["id"], "produced_by": len(producers.get(h, [])),
                      "ancestors": len(anc), "roots": sorted(roots)[:20], "descendants": len(desc),
                      "ancestor_types": sorted({entities[x]["type"] for x in anc if x in entities})}
    missing_art = sorted(a for a in art if not any(e["type"] == a for e in entities.values()))
    unproduced = sorted(h for h, l in lineage.items() if l["produced_by"] == 0)
    return {
        "entities": entities, "activities": acts, "edges": edges, "types": types,
        "entity_relations": entity_rels,
        "summary": {
            "entity_metadata_events": meta_events, "prov_events": prov_events,
            "entities": len(entities), "activities": len(acts), "edges": len(edges),
            "entities_by_type": dict(Counter(e["type"] for e in entities.values())),
            "activities_by_type": dict(Counter(a["activity"] for a in acts.values())),
            "dangling_hashes": len(dangling), "isolated_entities": len(isolated),
            "truncated_activities": len(truncated),
            "artifact_types_missing": missing_art,
            "artifacts_without_producer": len(unproduced),
            "types_described": len(types),
            "types_undescribed": sorted({e["type"] for e in entities.values()} - set(types)),
        },
        "dangling": dangling[:50], "isolated": isolated[:50], "truncated": truncated[:50],
        "lineage": lineage,
    }


def graph_to_dot(g: Dict[str, Any], max_nodes: int = 3000) -> str:
    out = ["digraph provenance {", "  rankdir=LR; node [fontsize=10];"]
    n = 0
    for h, e in g["entities"].items():
        if n >= max_nodes:
            break
        lbl = f"{e['type']}\\n{e['id'][:40]}".replace('"', "'")
        out.append(f'  "{h}" [shape=ellipse, style=filled, fillcolor="#fde9b6", label="{lbl}"];')
        n += 1
    for aid, a in g["activities"].items():
        if n >= max_nodes:
            break
        lbl = f"{a['activity']}\\n{a['name']}".replace('"', "'")
        out.append(f'  "{aid}" [shape=box, style=filled, fillcolor="#c6dbf7", label="{lbl}"];')
        n += 1
    for e in g["edges"]:
        out.append(f'  "{e["src"]}" -> "{e["dst"]}";')
    out.append("}")
    return "\n".join(out) + "\n"


def _py_import_insert_index(lines: List[str]) -> int:
    """0-based index of the last line of the last top-level import statement.

    Uses the AST so a multi-line ``from x import (\n a,\n b)`` is treated as one
    statement; inserting after its first line would split it. Falls back to the
    end of the module docstring, or -1 (top of file).
    """
    import ast
    try:
        tree = ast.parse("".join(lines))
    except SyntaxError:
        return max((i for i, l in enumerate(lines) if re.match(r"^(import |from \S+ import )", l)), default=-1)
    last = -1
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            last = node.end_lineno - 1
        elif (last == -1 and isinstance(node, ast.Expr) and isinstance(getattr(node, "value", None), ast.Constant)
              and isinstance(node.value.value, str)):
            last = node.end_lineno - 1  # module docstring
        elif last != -1:
            break
    return last


def type_registry_py(spec: Dict[str, Any]) -> str:
    """dftracer_prov_types.py: {type: description}, emitted by dftracer_prov at runtime."""
    lines = ['"""Entity types for provenance: role (input|output|intermediate) and description',
             '(generated from provenance/spec.yaml by provenance_install_helpers; regenerate,',
             'do not hand-edit)."""', "", "TYPES = {"]
    for t in spec.get("entity_types") or []:
        if isinstance(t, dict) and t.get("name"):
            lines.append(f"    {t['name']!r}: ({t.get('role') or ''!r}, "
                         f"{(t.get('description') or '').strip()!r}),")
    lines.append("}")
    return "\n".join(lines) + "\n"


def type_registry_h(spec: Dict[str, Any]) -> str:
    """dftracer_prov_types.h: dft_prov_register_types() over the dftracer core
    API (dftracer_declare_entity_type with an EntityRole), included by
    dftracer_prov.h."""
    def cstr(x: str) -> str:
        return '"' + x.replace("\\", "\\\\").replace('"', '\\"') + '"'
    roles = {"input": "DFT_ROLE_INPUT", "output": "DFT_ROLE_OUTPUT",
             "intermediate": "DFT_ROLE_INTERMEDIATE", "parameter": "DFT_ROLE_PARAMETER",
             "reference": "DFT_ROLE_REFERENCE"}
    out = ["/* Entity types (generated from provenance/spec.yaml by",
           " * provenance_install_helpers; regenerate, do not hand-edit). */",
           "#ifndef DFTRACER_PROV_TYPES_H", "#define DFTRACER_PROV_TYPES_H",
           "#include <dftracer/dftracer.h>",
           "static inline void dft_prov_register_types(void) {"]
    for t in spec.get("entity_types") or []:
        if isinstance(t, dict) and t.get("name"):
            role = roles.get(str(t.get("role") or "").lower(), "DFT_ROLE_UNKNOWN")
            out.append(f"  dftracer_declare_entity_type({cstr(t['name'])}, {role}, "
                       f"{cstr((t.get('description') or '').strip())});")
    out += ["}", "#endif", ""]
    return "\n".join(out)


def _load_spec(ws: Path) -> Dict[str, Any]:
    p = ws / SPEC_REL
    if not p.exists():
        return {}
    import yaml
    return yaml.safe_load(p.read_text()) or {}


# ---------------------------------------------------------------------------
# MCP registration
# ---------------------------------------------------------------------------

def register_provenance_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def provenance_inventory_outputs(run_id: str, output_dir: str = "dataset",
                                     newer_than_epoch: float = 0.0) -> str:
        """Inventory what a plain (un-instrumented) run produced, to find the scientific artifact.

        Walks ``<WS>/<output_dir>`` (default ``dataset`` -- the PFS-backed run
        output dir) and groups files by extension with counts, bytes, examples and
        a domain hint (``.pdb`` -> molecular structure, ``.h5`` -> HDF5 dataset ...).
        Use it in provenance discovery BEFORE talking to the user: the artifact is
        usually the largest/most domain-specific output, not a log.

        Args:
            run_id: session id.
            output_dir: path relative to the session workspace (symlinks followed at the top).
            newer_than_epoch: only count files modified after this unix time
                (pass the run's start time to isolate one run's outputs).
        """
        ws = _ws(run_id)
        try:
            root = _safe_session_path(ws, output_dir) if output_dir not in (".", "") else ws
        except ValueError:
            # dataset/ is a symlink onto the PFS -- resolve() leaves the workspace by design
            root = ws / output_dir
        if not root.exists():
            return _err(f"{root} does not exist", run_id=run_id)
        return _ok("inventory complete", run_id=run_id, root=str(root),
                   **inventory_outputs(root, newer_than=newer_than_epoch))

    @mcp.tool()
    def provenance_scan_candidates(run_id: str, source_subfolder: str = "source",
                                   include_regex: str = "", max_hits: int = 400) -> str:
        """Find code touch-points where entities are read, written, created or stored.

        Regex sweep over C/C++/Python for file/HDF5/netCDF/MPI-IO/ADIOS/DB/torch/
        numpy/pandas/structure-library calls. Returns ``file:line``, enclosing
        function and kind (read/write/open/create/db/exec) so the agent can open
        only those lines. Use ``include_regex`` to narrow to the files the
        artifact's producer chain lives in. Pair with ``graph_query`` for call
        chains (who calls the writer of the artifact).
        """
        ws = _ws(run_id)
        try:
            root = _safe_session_path(ws, source_subfolder)
        except ValueError as e:
            return _err(str(e))
        if not root.exists():
            return _err(f"{root} does not exist")
        return _ok("scan complete", run_id=run_id, root=str(root),
                   **scan_candidates(root, include_regex, max_hits))

    @mcp.tool()
    def provenance_write_spec(run_id: str, spec_yaml: str, force: bool = False) -> str:
        """Validate and persist the user-approved provenance spec to ``<WS>/provenance/spec.yaml``.

        Schema::

            app: <name>
            scientific_artifacts: [<entity_type>, ...]     # agreed with the user
            entity_types:
              - name: protein_structure          # entity TYPE (science/data class)
                kind: science|input|intermediate|config|model|derived
                store: file|memory|db|object|network|gpu|other
                id_scheme: "output path relative to run dir"   # how the unique id is built
                where: "path/to/file.py:func"    # where instances appear
            activities:
              - name: predict_structure          # event name
                activity_type: inference         # optional; defaults to name
                where: "run_alphafold.py:predict_structure"
                used: [msa, model_params]
                generated: [protein_structure]
            decisions: ["free-text notes of what the user approved/excluded"]

        Rejected (unless ``force``) if: no artifact, artifact never generated,
        undeclared types in activities, entity types with no id_scheme, or orphan
        types. Returns the problem list either way.
        """
        import yaml
        try:
            spec = yaml.safe_load(spec_yaml) or {}
        except yaml.YAMLError as e:
            return _err(f"spec is not valid YAML: {e}")
        probs = validate_spec(spec)
        if probs and not force:
            return _err("spec failed validation", problems=probs)
        p = _ws(run_id) / SPEC_REL
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(spec, sort_keys=False))
        return _ok("spec written", path=str(p), problems=probs,
                   entity_types=len(spec.get("entity_types") or []),
                   activities=len(spec.get("activities") or []))

    @mcp.tool()
    def provenance_install_helpers(run_id: str, language: str,
                                   source_subfolder: str = DEFAULT_SOURCE,
                                   dest_relpath: str = "",
                                   include_into: str = "") -> str:
        """Write the provenance runtime helper into the provenance source tree.

        * ``c`` / ``cpp`` -> ``dftracer_prov.h`` (one header for both; C++ also
          gets ``dftprov::Activity`` RAII). Put it next to the files that use it
          or in a directory already on the include path.
        * ``python`` -> ``dftracer_prov.py`` (place it inside the app's package or
          next to the entry script so ``import dftracer_prov`` resolves).

        Also writes the entity-type registry generated from the spec
        (``dftracer_prov_types.py`` / ``.h``): every type's description is emitted
        as ``prov_type:<type>`` metadata the first time a process declares an
        entity. Re-run after editing the spec's descriptions.

        Args:
            dest_relpath: directory relative to the source tree (default: tree root).
            include_into: comma-separated source-relative files to receive the
                ``#include "dftracer_prov.h"`` / ``import dftracer_prov as prov``
                line (added after the last existing include/import; idempotent).
        """
        lang = language.strip().lower()
        ws = _ws(run_id)
        try:
            root = _safe_session_path(ws, source_subfolder)
        except ValueError as e:
            return _err(str(e))
        if not root.exists():
            return _err(f"{root} does not exist -- copy the source tree into {source_subfolder} first")
        dest = root / dest_relpath if dest_relpath else root
        dest.mkdir(parents=True, exist_ok=True)
        spec = _load_spec(ws)
        registry = None
        if lang in ("c", "cpp", "c++"):
            out = dest / "dftracer_prov.h"
            out.write_text(PROV_C_HEADER)
            if spec:
                registry = dest / "dftracer_prov_types.h"
                registry.write_text(type_registry_h(spec))
            inc_line = '#include "dftracer_prov.h"'
            pat = re.compile(r"^\s*#\s*include\b")
        elif lang == "python":
            out = dest / "dftracer_prov.py"
            out.write_text(PROV_PY_MODULE)
            if spec:
                registry = dest / "dftracer_prov_types.py"
                registry.write_text(type_registry_py(spec))
            inc_line = "import dftracer_prov as prov"
            pat = re.compile(r"^(import |from \S+ import )")
        else:
            return _err("language must be c, cpp or python")
        added, skipped = [], []
        for rel in [x.strip() for x in include_into.split(",") if x.strip()]:
            f = root / rel
            if not f.exists():
                skipped.append(f"{rel}: missing")
                continue
            lines = f.read_text().splitlines(keepends=True)
            if any(inc_line in l for l in lines):
                skipped.append(f"{rel}: already present")
                continue
            if lang == "python":
                last = _py_import_insert_index(lines)
            else:
                last = max((i for i, l in enumerate(lines) if pat.match(l)), default=-1)
            lines.insert(last + 1, inc_line + "\n")
            f.write_text("".join(lines))
            added.append(rel)
        return _ok("helper installed", helper=str(out.relative_to(ws)),
                   type_registry=str(registry.relative_to(ws)) if registry else
                   "none -- write the spec first so entity type descriptions ship with the trace",
                   include_added=added,
                   include_skipped=skipped,
                   runtime_env={"DFTRACER_INC_METADATA": "1"},
                   reminder="cause/effect args are DROPPED unless DFTRACER_INC_METADATA=1 at run time")

    @mcp.tool()
    def provenance_insert(run_id: str, filepath: str, line: int, code: str,
                          position: str = "after", source_subfolder: str = DEFAULT_SOURCE) -> str:
        """Insert provenance call(s) at a 1-based line, matching indentation (idempotent).

        ``code`` may hold several statements, one per line, e.g. (C++)::

            DFT_PROV_ACTIVITY(pa, "write_pdb", "write_structure");
            dft_prov_used(&pa, "structure_model", model_name.c_str(), "memory", NULL);
            dft_prov_generated(&pa, "protein_structure", path.c_str(), "file", path.c_str());

        Every inserted line is tagged ``/* dft-prov */`` (C/C++) or ``# dft-prov``
        (Python) so the instrumentation is greppable and re-runs are no-ops.
        ``position`` is ``after`` (default; indents one level after ``{`` / ``:``)
        or ``before``. Re-read the file after a batch of inserts: line numbers shift.
        """
        ws = _ws(run_id)
        try:
            p = _safe_session_path(ws, f"{source_subfolder}/{filepath}")
        except ValueError as e:
            return _err(str(e))
        if not p.exists():
            return _err(f"{p} does not exist")
        try:
            res = insert_lines(p, line, code, position)
        except ValueError as e:
            return _err(str(e))
        return _ok("inserted" if res["inserted"] else "already present", file=filepath, **res)

    @mcp.tool()
    def provenance_validate(run_id: str, source_subfolder: str = DEFAULT_SOURCE) -> str:
        """Static gate: does the annotated tree record every entity type and activity in the spec?

        Checks: every spec entity type appears as a literal in a used/generated/
        entity call; every spec activity is opened; no undeclared type literals;
        every file using the API includes/imports the helper; C files using the
        manual begin API also call ``dft_prov_end``. Must return ``passed: true``
        before building. Runtime completeness is checked by
        ``provenance_extract_graph`` on the trace.
        """
        ws = _ws(run_id)
        spec = _load_spec(ws)
        if not spec:
            return _err(f"no spec at {SPEC_REL} -- run provenance_write_spec first")
        try:
            root = _safe_session_path(ws, source_subfolder)
        except ValueError as e:
            return _err(str(e))
        res = static_check(root, spec)
        helper = any(root.rglob("dftracer_prov.h")) or any(root.rglob("dftracer_prov.py"))
        if not helper:
            res["issues"].insert(0, "no dftracer_prov helper in tree -- run provenance_install_helpers")
            res["passed"] = False
        return _ok("validation complete", run_id=run_id, **res)

    @mcp.tool()
    def provenance_extract_graph(run_id: str, trace_dir: str = f"{PROV_RUN}/traces",
                                 artifact_types: str = "", out_dir: str = f"{PROV_RUN}/graph",
                                 use_viewer: bool = True, viewer_bin: str = "") -> str:
        """Build the provenance graph from traces and check lineage completeness.

        Reads ``prov_entity:<hash>`` metadata events and ``PROV``/``PROV_CONT``
        activity events from every ``*.pfw``/``*.pfw.gz`` under ``trace_dir``
        (all ranks/processes are merged; hashes join across them). Writes
        ``graph.json`` (entities, activities, edges, lineage) and ``graph.dot``
        to ``out_dir`` and returns the summary.

        Health signals to act on:
          * ``prov_events == 0`` -> args dropped: run lacked ``DFTRACER_INC_METADATA=1``
            or activities never executed.
          * ``dangling_hashes`` -> an edge references an entity with no metadata
            event (metadata disabled, or a different process emitted it and its trace is missing).
          * ``isolated_entities`` -> registered but never used/generated.
          * ``artifacts_without_producer`` / ``artifact_types_missing`` -> the
            scientific artifact's lineage is not captured -- annotation gap.
          * ``truncated_activities`` -> list chunks lost.

        Args:
            artifact_types: comma-separated; defaults to spec.scientific_artifacts.
            use_viewer: extract events with dftracer-utils' ``dftracer_view`` (indexed,
                query-driven; scales to large traces). Falls back to a direct scan
                when the viewer is not found.
            viewer_bin: path to ``dftracer_view`` (e.g. ``<venv>/bin/dftracer_view``);
                default: first on PATH.
        """
        ws = _ws(run_id)
        try:
            tdir = _safe_session_path(ws, trace_dir)
            odir = _safe_session_path(ws, out_dir)
        except ValueError as e:
            return _err(str(e))
        if not tdir.exists():
            return _err(f"{tdir} does not exist")
        arts = [x.strip() for x in artifact_types.split(",") if x.strip()] or \
            list(_load_spec(ws).get("scientific_artifacts") or [])
        view = None
        exe = viewer_bin or shutil.which("dftracer_view") or ""
        if exe and use_viewer:
            view = view_prov_events(tdir, odir / "prov_events.jsonl", exe, odir / "index")
            if view["returncode"] != 0:
                return _err("dftracer_view failed", view=view)
            g = build_graph(odir / "prov_events.jsonl", arts)
        else:
            g = build_graph(tdir, arts)
        odir.mkdir(parents=True, exist_ok=True)
        (odir / "graph.json").write_text(json.dumps(g, indent=1, default=str))
        (odir / "graph.dot").write_text(graph_to_dot(g))
        s = g["summary"]
        healthy = (s["prov_events"] > 0 and s["dangling_hashes"] == 0 and not s["artifact_types_missing"]
                   and s["artifacts_without_producer"] == 0 and s["truncated_activities"] == 0)
        sample = dict(list(g["lineage"].items())[:10])
        return _ok("graph built", run_id=run_id, healthy=healthy, artifact_types=arts,
                   extraction=("dftracer_view" if view else "direct-scan"), view=view,
                   graph_json=str((odir / "graph.json").relative_to(ws)),
                   graph_dot=str((odir / "graph.dot").relative_to(ws)),
                   summary=s, dangling=g["dangling"][:10], isolated=g["isolated"][:10],
                   lineage_sample=sample)
