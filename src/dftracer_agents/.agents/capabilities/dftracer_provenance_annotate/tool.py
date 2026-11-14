"""dftracer_provenance_annotate: provenance mode, annotation stage. Given the user-approved provenance/spec.yaml, install
the dftracer_prov helper and the entity-type registry, insert dftracer_prov entity/activity calls (C, C++, Python) into
<WS>/provenance/source, make main() initialize and finalize dftracer, and gate statically on the spec."""
import ast
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from dftracer_agents.mcp_tools.tools.session.provenance import (
    DEFAULT_SOURCE,
    MARK_C,
    MARK_PY,
    PROV_C_HEADER,
    PROV_PY_MODULE,
    SPEC_REL,
    _iter_sources,
    _lang_of,
    _load_spec,
    _py_import_insert_index,
    ensure_lifecycle,
    insert_lines,
    static_check,
    type_registry_h,
    type_registry_py,
)
from dftracer_agents.mcp_tools.tools.session.workspace import _err, _ok, _safe_session_path, _ws


def _annotate_verdict(root: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    """Rule on an annotated provenance tree against its spec: helper, registry and static coverage, markers, Python syntax."""
    checks: List[Dict[str, Any]] = []

    def add(name, ok, detail, fix=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})

    helper = any(root.rglob("dftracer_prov.h")) or any(root.rglob("dftracer_prov.py"))
    add("helper", helper, "dftracer_prov helper present" if helper else "no dftracer_prov.h / dftracer_prov.py in the tree",
        "provenance_install_helpers(run_id, language, include_into=<edited files>)")
    res = static_check(root, spec)
    reg = [i for i in res["issues"] if "registry" in i]
    cov = [i for i in res["issues"] if "registry" not in i and ("never recorded" in i or "never opened" in i or "not in spec" in i)]
    wiring = [i for i in res["issues"] if i not in reg and i not in cov]
    add("type_registry", not reg, "; ".join(reg) or "registry matches the spec",
        "re-run provenance_install_helpers after every spec change (descriptions come from the spec)")
    add("coverage", not cov, "; ".join(cov[:8]) or f"{len(res['entity_type_calls'])} types, {len(res['activity_calls'])} activities recorded",
        "provenance_insert the missing used/generated/activity calls at the spec's 'where'; fix literals that are not spec types")
    add("wiring", not wiring, "; ".join(wiring[:8]) or "every file using the API includes/imports the helper; lifecycle present",
        "add the include/import (provenance_install_helpers include_into=...), DFT_PROV_ACTIVITY instead of begin without end, "
        "provenance_ensure_lifecycle on the file defining main()")
    marked = unmarked = 0
    bad_py: List[str] = []
    for p in _iter_sources(root):
        if p.name in ("dftracer_prov.h", "dftracer_prov.py", "dftracer_prov_types.py", "dftracer_prov_types.h"):
            continue
        try:
            txt = p.read_text(errors="replace")
        except OSError:
            continue
        lang = _lang_of(p)
        mark = MARK_PY if lang == "python" else MARK_C
        if mark in txt:
            marked += 1
        elif "dft_prov" in txt or ("dftracer_prov" in txt and re.search(r"\bprov\.(activity|entity|relate)\b|\.(used|generated)\(", txt)):
            unmarked += 1
        if lang == "python" and ("dftracer_prov" in txt or MARK_PY in txt):
            try:
                ast.parse(txt, filename=str(p))
            except SyntaxError as e:
                bad_py.append(f"{p.relative_to(root)}:{e.lineno}: {e.msg}")
    add("markers", marked > 0, f"{marked} file(s) carry the dft-prov marker" + (f", {unmarked} use the API without it" if unmarked else ""),
        "insert calls with provenance_insert (it tags every line), never by hand")
    add("python_syntax", not bad_py, "; ".join(bad_py[:5]) or "every instrumented Python file parses",
        "fix the indentation/statement the insert broke (position before/after), then python -m py_compile")
    status = "pass" if all(c["ok"] for c in checks) else "fail"
    return {"status": status, "checks": checks, "files_with_calls": res["files_with_calls"],
            "note": "C/C++ files are compiled by clang_syntax_check, not here"}


def register(mcp):
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
    def provenance_ensure_lifecycle(run_id: str, filepath: str, compile_flags: str = "",
                                    source_subfolder: str = DEFAULT_SOURCE) -> str:
        """C/C++: make ``main()`` initialize and finalize dftracer (idempotent).

        REQUIRED for any C/C++ provenance build. ``dft_prov_*`` calls only record
        events once dftracer is initialized; without ``DFTRACER_C_INIT`` /
        ``DFTRACER_CPP_INIT`` the run exits 0 but the trace is 0 bytes and the
        provenance graph is empty. ``provenance_validate`` fails until this is done.

        Adds ``#include <dftracer/dftracer.h>``, the INIT macro as the first
        statement of ``main`` and an ``atexit`` handler that calls FINI (covers
        every ``return`` and ``exit()`` path). Python needs no equivalent here:
        initialize it with ``initialize_log`` in the entry script.

        Args:
            filepath: the source file that DEFINES ``main`` (relative to the tree).
            compile_flags: clang flags as one string with ABSOLUTE ``-I`` paths
                (relative ones resolve against the server's cwd, not the
                workspace). Needed when the file includes headers that clang
                cannot otherwise find (e.g. ``mpi.h``).
        """
        import shlex

        ws = _ws(run_id)
        try:
            p = _safe_session_path(ws, f"{source_subfolder}/{filepath}")
        except ValueError as e:
            return _err(str(e))
        if not p.exists():
            return _err(f"{p} does not exist")
        try:
            res = ensure_lifecycle(p, shlex.split(compile_flags) if compile_flags else None)
        except (ValueError, RuntimeError) as e:
            return _err(str(e))
        return _ok("lifecycle already present" if res["already_present"]
                   else "dftracer init/finalize added to main()", file=filepath, **res)

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
    def dftracer_provenance_annotate_check(run_id: str = "", source_dir: str = "", spec_path: str = "") -> dict:
        """Validate an annotated provenance tree deterministically against its spec. Give run_id (checks
        <WS>/provenance/source against <WS>/provenance/spec.yaml) or an absolute source_dir and spec_path. Checks: helper
        installed; entity-type registry matches the spec; every spec type and activity is recorded and no undeclared type
        literal appears (coverage); every file using the API includes/imports the helper and C/C++ trees initialize
        dftracer (wiring); inserted lines carry the dft-prov marker; every instrumented Python file still parses. Use after
        the insert loop and before building. Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}],
        files_with_calls, note}."""
        import yaml
        if run_id.strip():
            ws = _ws(run_id)
            root, sp = ws / DEFAULT_SOURCE, ws / SPEC_REL
        elif source_dir and spec_path:
            root, sp = Path(source_dir), Path(spec_path)
        else:
            return {"status": "inconclusive", "checks": [{"check": "input", "ok": False, "detail": "give run_id, or source_dir and spec_path",
                                                          "fix": "pass the session run_id"}]}
        if not sp.is_file():
            return {"status": "inconclusive", "checks": [{"check": "spec_present", "ok": False,
                                                          "detail": f"unknown run or no spec at {sp.name}",
                                                          "fix": "provenance_write_spec first (dftracer_provenance_discover)"}]}
        if not root.is_dir():
            return {"status": "inconclusive", "checks": [{"check": "source_present", "ok": False, "detail": f"no source tree {root.name}",
                                                          "fix": "copy the original source into provenance/source first"}]}
        try:
            spec = yaml.safe_load(sp.read_text()) or {}
        except yaml.YAMLError as e:
            return {"status": "fail", "checks": [{"check": "spec_yaml", "ok": False, "detail": str(e)[:200],
                                                  "fix": "rewrite the spec with provenance_write_spec"}]}
        return _annotate_verdict(root, spec)

    @mcp.tool()
    def dftracer_provenance_annotate_selftest() -> dict:
        """Self-test dftracer_provenance_annotate_check on a fixture it writes to a temp dir: a small Python app annotated
        with prov.activity/used/generated, the real dftracer_prov helper and a registry generated from the spec, must pass;
        the same tree with the generated() call removed must fail on coverage; a missing spec is inconclusive. Use to check
        the validator itself. Returns {status: pass|fail, detail}."""
        spec = {"app": "demo", "scientific_artifacts": ["field"],
                "entity_types": [
                    {"name": "deck", "store": "file", "role": "input", "id_scheme": "deck path",
                     "description": "The input deck that configures the simulation."},
                    {"name": "field", "store": "file", "role": "output", "id_scheme": "step=N",
                     "description": "The field snapshot written at a step; the artifact."}],
                "activities": [{"name": "simulate", "where": "app.py:run", "used": ["deck"], "generated": ["field"]}]}
        import yaml
        app_ok = ("import dftracer_prov as prov  # dft-prov\n\n\ndef run(deck, out):\n"
                  "    with prov.activity(\"simulate\", \"simulate\") as a:  # dft-prov\n"
                  "        a.used(\"deck\", deck, \"file\", deck)  # dft-prov\n"
                  "        a.generated(\"field\", \"step=1\", \"file\", out)  # dft-prov\n")
        with tempfile.TemporaryDirectory() as d:
            root, sp = Path(d) / "source", Path(d) / "spec.yaml"
            root.mkdir()
            sp.write_text(yaml.safe_dump(spec))
            (root / "dftracer_prov.py").write_text(PROV_PY_MODULE)
            (root / "dftracer_prov_types.py").write_text(type_registry_py(spec))
            (root / "app.py").write_text(app_ok)
            good = dftracer_provenance_annotate_check(source_dir=str(root), spec_path=str(sp))["status"]
            (root / "app.py").write_text(app_ok.replace("        a.generated(\"field\", \"step=1\", \"file\", out)  # dft-prov\n", ""))
            gap = dftracer_provenance_annotate_check(source_dir=str(root), spec_path=str(sp))
            gap_cov = next(c["ok"] for c in gap["checks"] if c["check"] == "coverage")
            nospec = dftracer_provenance_annotate_check(source_dir=str(root), spec_path=str(Path(d) / "none.yaml"))["status"]
        ok = good == "pass" and gap["status"] == "fail" and gap_cov is False and nospec == "inconclusive"
        return {"status": "pass" if ok else "fail", "detail": f"good={good} gap={gap['status']} coverage_ok={gap_cov} no_spec={nospec}"}
