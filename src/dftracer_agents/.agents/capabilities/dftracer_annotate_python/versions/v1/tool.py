"""dftracer_annotate_python: AST-based Python annotation with dftracer decorators (python_extract_functions, python_annotate_file/project, python_annotate_manual_event, python_lint_annotations, python_dedup_annotations, python_write_annotated_file, session_annotate_python_file) and the deterministic validator dftracer_annotate_python_check. Tools moved unchanged from dftracer_agents.mcp_tools.tools.session.annotation_python and annotations.annotate_python."""

from __future__ import annotations

import re
from dftracer_agents.mcp_tools.tools.session.annotation_python import (
    PY_PARALLEL_MIN_FILES,
    _PY_FILE_CACHE,
    _PY_IMPORT,
    _extract_functions_from_ast,
    _last_import_idx,
    _python_annotate_file_impl,
    _python_annotate_file_worker,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
    _ws,
)
from typing import (
    Dict,
    List,
)

# ---------------------------------------------------------------------------------------------------------------
# Validator helpers (new in the capability; the moved tools above do not use them).
# A verdict is {"status": "pass"|"fail"|"inconclusive", "checks": [{"check", "ok", "detail", "fix"}]}:
# ok True passes, False fails, None means the check could not run here (inconclusive).
# ---------------------------------------------------------------------------------------------------------------
import ast as _vast
import re as _vre
import shutil as _vshutil
import subprocess as _vsubprocess
import tempfile as _vtempfile
from pathlib import Path as _VPath
from typing import Any, Dict, List, Optional

# The same dftracer macro stub clang_syntax_check injects, plus the METADATA macros annotate_add_app_metadata emits,
# so an annotated file syntax-checks without an installed dftracer.
_V_STUB_H = (
    "#ifndef _DFTRACER_SYNTAX_STUB_H\n"
    "#define _DFTRACER_SYNTAX_STUB_H\n"
    "#define DFTRACER_C_INIT(a,b,c)              do{}while(0)\n"
    "#define DFTRACER_C_FINI()                   do{}while(0)\n"
    "#define DFTRACER_C_FUNCTION_START()         do{}while(0)\n"
    "#define DFTRACER_C_FUNCTION_END()           do{}while(0)\n"
    "#define DFTRACER_C_FUNCTION_UPDATE_STR(k,v) do{}while(0)\n"
    "#define DFTRACER_C_FUNCTION_UPDATE_INT(k,v) do{}while(0)\n"
    "#define DFTRACER_C_REGION_START(n)          do{}while(0)\n"
    "#define DFTRACER_C_REGION_END(n)            do{}while(0)\n"
    "#define DFTRACER_C_METADATA(n,k,v)          do{}while(0)\n"
    "#define DFTRACER_CPP_INIT(a,b,c)            do{}while(0)\n"
    "#define DFTRACER_CPP_FINI()                 do{}while(0)\n"
    "#define DFTRACER_CPP_FUNCTION()             do{}while(0)\n"
    "#define DFTRACER_CPP_FUNCTION_UPDATE(k,v)   do{}while(0)\n"
    "#define DFTRACER_CPP_REGION_START(n)        do{}while(0)\n"
    "#define DFTRACER_CPP_REGION_END(n)          do{}while(0)\n"
    "#define DFTRACER_CPP_REGION_UPDATE(n,k,v)   do{}while(0)\n"
    "#define DFTRACER_CPP_REGION_DYN_UPDATE(n,k,v) do{}while(0)\n"
    "#define DFTRACER_CPP_METADATA(n,k,v)        do{}while(0)\n"
    "#endif\n"
)
_V_EXTS = {"c": (".c",), "cpp": (".cpp", ".cxx", ".cc", ".C", ".c++"), "python": (".py",)}
_V_SKIP_PARTS = ("__pycache__", ".git")
_V_HDRS = (".h", ".hpp", ".hh", ".hxx", ".ipp", ".tpp")


def _vchk(check: str, ok, detail: str = "", fix: str = "") -> dict:
    return {"check": check, "ok": ok, "detail": detail, "fix": fix}


def _verdict(checks: list, **extra) -> dict:
    if any(c["ok"] is False for c in checks):
        status = "fail"
    elif not checks or any(c["ok"] is None for c in checks):
        status = "inconclusive"
    else:
        status = "pass"
    return {"status": status, "checks": checks, **extra}


def _vlang(p: _VPath, language: str = "auto") -> str:
    if language and language != "auto":
        lang = language.lower()
        return "cpp" if lang in ("c++", "cxx") else lang
    if p.suffix == ".c":
        return "c"
    if p.suffix == ".py":
        return "python"
    if p.suffix in (".C", ".CC", ".CPP", ".CXX") or p.suffix.lower() in (".cpp", ".cxx", ".cc", ".c++"):
        return "cpp"
    return ""


def _vfiles(path: str, source: str, language: str, tmp: str, fixture_name: str = "", excludes=("test", "tests")):
    """Files to check: `source` written into the temp dir `tmp` (a fixture), else `path` (a file or a tree; as in
    validate_annotations, __pycache__/.git and the `excludes` directories (test, tests) are skipped)."""
    if source:
        lang = language if language and language != "auto" else "c"
        name = fixture_name or {"c": "fixture.c", "cpp": "fixture.cpp", "python": "fixture.py"}.get(lang, "fixture.c")
        f = _VPath(tmp) / name
        f.write_text(source)
        return [f], ""
    if not path:
        return [], "give path (an annotated file or tree) or source (the text of one file)"
    p = _VPath(path).expanduser()
    if p.is_file():
        return [p], ""
    if p.is_dir():
        langs = [language] if language and language != "auto" else ["c", "cpp", "python"]
        hdr = _V_HDRS if ("c" in langs or "cpp" in langs) else ()
        skip = set(_V_SKIP_PARTS) | set(excludes or ())
        out = [f for f in sorted(p.rglob("*")) if f.is_file() and not (set(f.relative_to(p).parts) & skip)
               and f.name not in skip and (_vlang(f) in langs or f.suffix.lower() in hdr)]
        return out, "" if out else f"no {'/'.join(langs)} source files under {p}"
    return [], f"path not found: {p}"


def _vcompile(f: _VPath, lang: str, compiler: str = "", extra_include_dirs=None, extra_flags=None):
    """Syntax-check a C/C++ file with the real compiler front-end against the dftracer macro stub (as clang_syntax_check
    does), or a Python file with ast.parse. Returns (ok True|False|None, detail)."""
    if lang == "python":
        try:
            _vast.parse(f.read_text(errors="replace"), filename=str(f))
            return True, "ast.parse ok"
        except SyntaxError as exc:
            return False, f"{f.name}:{exc.lineno}: SyntaxError: {exc.msg}"
    cands = [compiler] if compiler else (["g++", "clang++"] if lang == "cpp" else ["gcc", "clang"])
    exe = next((_vshutil.which(c) for c in cands if c and _vshutil.which(c)), None)
    if not exe:
        return None, f"no compiler found ({', '.join(c for c in cands if c)}): cannot syntax-check {f.name}"
    with _vtempfile.TemporaryDirectory() as d:
        (_VPath(d) / "dftracer").mkdir()
        stub = _VPath(d) / "dftracer" / "dftracer.h"
        stub.write_text(_V_STUB_H)
        cmd = [exe] + (["-x", "c++", "-std=c++14"] if lang == "cpp" else ["-x", "c"]) + ["-fsyntax-only", "-w", "-include", str(stub), f"-I{d}"]
        cmd += [x for x in (extra_flags or []) if x.startswith(("-D", "-U", "-std=", "-I"))]
        cmd += [f"-I{x}" for x in (extra_include_dirs or [])] + [f"-I{f.parent}", str(f)]
        try:
            r = _vsubprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except (OSError, _vsubprocess.TimeoutExpired) as exc:
            return None, f"{exe} could not run: {exc}"
    errs = [ln for ln in (r.stdout + "\n" + r.stderr).splitlines() if "error" in ln.lower() and "dftracer/dftracer.h" not in ln]
    if r.returncode == 0:
        return True, f"{_VPath(exe).name} -fsyntax-only ok"
    return False, "; ".join(errs[:5]) or f"{exe} exited {r.returncode}"


def _vstrip_comments(text: str) -> str:
    """C/C++ text with // and /* */ comments and preprocessor lines blanked and braces inside string literals neutralized
    (newlines kept), for macro counting and the function scan."""
    text = _vre.sub(r"/\*.*?\*/", lambda m: "\n" * m.group(0).count("\n"), text, flags=_vre.S)
    text = _vre.sub(r"//[^\n]*", "", text)
    # braces/semicolons inside string literals must not count as structure ("Pitfall: `{` inside a string literal")
    text = _vre.sub(r'"(?:\\.|[^"\\\n])*"', lambda m: m.group(0).replace("{", " ").replace("}", " ").replace(";", " "), text)
    text = _vre.sub(r"^[ \t]*#[^\n]*(?:\\\n[^\n]*)*", lambda m: "\n" * m.group(0).count("\n"), text, flags=_vre.M)
    return text


def _vc_functions(text: str):
    """(name, start_line, body_text) of each top-level brace block that follows a `name(...)` signature, from
    comment-stripped C/C++ text. Coarse, like validate_annotations' own scan."""
    out = []
    depth, start, name, buf = 0, None, None, []
    sig = _vre.compile(r"\b([A-Za-z_][\w:~]*)\s*\([^;{}()]*(?:\([^;{}()]*\)[^;{}()]*)*\)\s*(?:const\s*)?(?:noexcept\s*)?(?:->\s*[\w:<>\s\*&]+)?$", _vre.S)
    i = 0
    pending = ""
    line = 1
    while i < len(text):
        ch = text[i]
        if ch == "\n":
            line += 1
        if depth == 0:
            if ch == "{" and _vre.search(r"\b(namespace|class|struct|union|enum|extern)\b[^;{}()]*$", pending):
                pending = ""            # scope block: look inside it for member/namespace functions
            elif ch == "{":
                m = sig.search(pending.rstrip())
                if m and m.group(1) not in ("if", "for", "while", "switch", "catch", "return", "sizeof"):
                    name, start, buf = m.group(1), line, []
                else:
                    name = None
                depth = 1
                pending = ""
            elif ch == ";" or ch == "}":
                pending = ""
            else:
                pending += ch
        else:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    if name:
                        out.append((name, start, "".join(buf)))
                    name = None
                    i += 1
                    continue
            if name:
                buf.append(ch)
        i += 1
    return out


_V_C_MACRO = _vre.compile(r"\bDFTRACER_C(?:PP)?_[A-Z_]+\s*\(")
# a non-zero integer literal (e.g. -1) as process_id (int*): 0 is a null pointer constant and compiles
_V_INIT_LITERAL = _vre.compile(r"DFTRACER_C(?:PP)?_INIT\s*\([^,()]*,[^,()]*,\s*(?:-\s*\d+|[1-9]\d*)\s*\)")


def _vprev_stmt_ok(body: str, pos: int, end_tokens) -> tuple:
    """Whether the `return` at *pos* is preceded, inside its own enclosing block (nested blocks before it ignored), by
    one of *end_tokens* (END / FINI / REGION_END), as C Rule 2 / CP8 require; FINI may sit before MPI_Finalize-like
    calls (C Rule 3), so the token need not be the immediately preceding statement. Returns (ok, why)."""
    before = body[:pos]
    cut = max(before.rfind(";"), before.rfind("{"), before.rfind("}"))
    lead = before[cut + 1:].strip()
    if lead and not _vre.fullmatch(r"(?:case\s+[^:]+|default|\w+)\s*:", lead):
        return False, f"return follows `{lead[-40:]}` without braces: END cannot precede it (clang_add_braces)"
    depth, i = 0, len(before) - 1
    while i >= 0:                      # the innermost `{` that encloses the return
        ch = before[i]
        if ch == "}":
            depth += 1
        elif ch == "{":
            if depth == 0:
                break
            depth -= 1
        i -= 1
    seg = before[i + 1:]
    flat, depth = [], 0                # drop nested blocks: an END inside an earlier `if {}` does not cover this return
    for ch in seg:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            continue
        if depth == 0:
            flat.append(ch)
    flat = "".join(flat)
    ok = any(t in flat for t in end_tokens)
    stmts = [x.strip() for x in flat.split(";") if x.strip()]
    return ok, "" if ok else f"no {end_tokens[0]}() before `return` (after `{stmts[-1][-50:] if stmts else '{'}`)"


def _vc_like_checks(files, lang: str, compiler: str = "", extra_include_dirs=None, need_main: bool = False,
                    extra_flags=None) -> list:
    """Deterministic checks of annotated C or C++ files: each compiles against the macro stub; no header carries macros;
    the dftracer include is present; every START/FUNCTION has a comp UPDATE first; C: END before every visible return;
    main: INIT (third argument a pointer, not a literal) and FINI; critical I/O calls sit in annotated functions
    (validate_annotations' own scan)."""
    from dftracer_agents.mcp_tools.tools.session.annotation_validate import _validate_c_like

    cpp = lang == "cpp"
    start_tok = "DFTRACER_CPP_FUNCTION()" if cpp else "DFTRACER_C_FUNCTION_START()"
    start_re = _vre.compile(r"DFTRACER_CPP_FUNCTION\s*\(\s*\)" if cpp else r"DFTRACER_C_FUNCTION_START\s*\(\s*\)")
    comp_re = _vre.compile(r'DFTRACER_CPP_(?:FUNCTION|REGION_DYN|REGION)_UPDATE\s*\([^;]*"comp"' if cpp
                           else r'DFTRACER_C_FUNCTION_UPDATE_STR\s*\(\s*"comp"')
    upd_re = _vre.compile(r"DFTRACER_CPP_\w*UPDATE\w*\s*\(" if cpp else r"DFTRACER_C_FUNCTION_UPDATE_\w+\s*\(")
    init, fini = ("DFTRACER_CPP_INIT", "DFTRACER_CPP_FINI") if cpp else ("DFTRACER_C_INIT", "DFTRACER_C_FINI")
    checks = []
    mains = []
    saw_main = has_init = has_fini = False
    for f in files:
        raw = f.read_text(errors="replace")
        rel = f.name
        if f.suffix.lower() in _V_HDRS:
            stripped = _vstrip_comments(raw)
            has_m = bool(_V_C_MACRO.search(stripped))
            tmpl = bool(_vre.search(r"\btemplate\s*<", stripped))
            checks.append(_vchk(f"{rel}: no macros in headers", (not has_m) or tmpl,
                                ("template header: annotation allowed (header-only C++, lessons: template-only projects)"
                                 if has_m and tmpl else "header carries dftracer macros" if has_m else ""),
                                "" if (not has_m) or tmpl else "move the annotation into the .c/.cpp definition (R5: never annotate a header)"))
            inc = bool(_vre.search(r"#\s*include\s*[<\"]dftracer/dftracer\.h[>\"]", raw))
            if inc and not tmpl:
                checks.append(_vchk(f"{rel}: no dftracer include in a header", False, "#include <dftracer/dftracer.h> in a header",
                                    "move it to the .c/.cpp source (CP6); in the entry .cpp put it before the library's headers (CP9)"))
            continue
        ok, detail = _vcompile(f, lang, compiler, extra_include_dirs, extra_flags)
        checks.append(_vchk(f"{rel}: compiles", ok, detail,
                            "" if ok else "fix the reported line (clang_syntax_check / clang_lint_annotations); see Corner cases"))
        text = _vstrip_comments(raw)
        if not _V_C_MACRO.search(text):
            if len(files) == 1:
                checks.append(_vchk(f"{rel}: annotated", False, "no dftracer macro in the file",
                                    "annotate it (clang_annotate_file / session_annotate_*_file) or drop it from scope"))
            cov = _validate_c_like(f, cpp=cpp)
            miss = cov.get("findings", [])
            checks.append(_vchk(f"{rel}: critical I/O calls annotated", not miss,
                                "; ".join(f"{x['function']}:{x['line']} {x['calls']}" for x in miss[:5]),
                                "" if not miss else "annotate those functions (they call open/read/write/H5*/MPI_File_*)"))
            continue
        has_inc = bool(_vre.search(r"#\s*include\s*[<\"]dftracer/dftracer\.h[>\"]", raw))
        checks.append(_vchk(f"{rel}: includes dftracer/dftracer.h", has_inc, "",
                            "" if has_inc else "add #include <dftracer/dftracer.h> after the last #include"))
        has_init |= init in text
        has_fini |= fini in text
        lit = _V_INIT_LITERAL.search(text)
        if lit:
            checks.append(_vchk(f"{rel}: INIT third argument is a pointer", False, lit.group(0),
                                "pass NULL/nullptr or &pid: the third argument is int*, never a literal"))
        for name, line, body in _vc_functions(text):
            n_start = len(start_re.findall(body))
            is_main = name == "main"
            if is_main:
                saw_main = True
                mains.append((rel, line, init in body, fini in body, bool(_V_C_MACRO.search(body))))
                if cpp and n_start:
                    checks.append(_vchk(f"{rel}:{line} main uses REGION, not FUNCTION", False,
                                        "DFTRACER_CPP_FUNCTION() in main: its destructor runs after FINI",
                                        "use DFTRACER_CPP_REGION_START(main)/REGION_END(main) in main"))
            if cpp and is_main and "DFTRACER_CPP_REGION_START" in body:
                bad_r = []
                for m in _vre.finditer(r"\breturn\b", body):
                    ok_r, why = _vprev_stmt_ok(body, m.start(), ("DFTRACER_CPP_REGION_END", "DFTRACER_CPP_FINI"))
                    if not ok_r:
                        bad_r.append(why or "no REGION_END before return")
                checks.append(_vchk(f"{rel}:{line} main: REGION_END before every return", not bad_r, "; ".join(bad_r[:3]),
                                    "" if not bad_r else "add DFTRACER_CPP_REGION_END(main) and DFTRACER_CPP_FINI() before each return in main (CP8)"))
            if n_start == 0:
                continue
            if n_start > 1:
                checks.append(_vchk(f"{rel}:{line} {name}: one {start_tok}", False, f"{n_start} found",
                                    "remove the duplicate START (re-annotation); keep the first statement after {"))
            first = start_re.search(body)
            after = body[first.end():]
            u = upd_re.search(after)
            comp_ok = bool(u) and bool(comp_re.match(after[u.start():]))
            checks.append(_vchk(f"{rel}:{line} {name}: comp UPDATE first", comp_ok,
                                "" if comp_ok else "no comp classification right after the START",
                                "" if comp_ok else ('insert DFTRACER_CPP_FUNCTION_UPDATE("comp", "<io|mem|cpu|comm>")' if cpp
                                                    else 'insert DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<io|mem|cpu|comm>")') + " right after the START"))
            if cpp:
                if "DFTRACER_CPP_FUNCTION_UPDATE_INT" in body:
                    checks.append(_vchk(f"{rel}:{line} {name}: no UPDATE_INT in C++", False, "DFTRACER_CPP_FUNCTION_UPDATE_INT does not exist",
                                        "drop the numeric UPDATE or pass it as a string to DFTRACER_CPP_FUNCTION_UPDATE (CP5)"))
                if "DFTRACER_C_FUNCTION_END" in body:
                    checks.append(_vchk(f"{rel}:{line} {name}: no END with RAII", False, "C END macro in a C++ RAII function",
                                        "remove it: DFTRACER_CPP_FUNCTION() closes on scope exit"))
                continue
            n_end = body.count("DFTRACER_C_FUNCTION_END")
            bad = []
            for m in _vre.finditer(r"\breturn\b", body):
                ok_r, why = _vprev_stmt_ok(body, m.start(), ("DFTRACER_C_FUNCTION_END", "DFTRACER_C_FINI"))
                if not ok_r:
                    bad.append(why or "no END before return")
            if n_end == 0:
                bad.append("no DFTRACER_C_FUNCTION_END() at all")
            checks.append(_vchk(f"{rel}:{line} {name}: END before every exit", not bad, "; ".join(bad[:3]),
                                "" if not bad else "insert DFTRACER_C_FUNCTION_END() right before each visible return (and at the end of a void body)"))
        cov = _validate_c_like(f, cpp=cpp)
        miss = cov.get("findings", [])
        checks.append(_vchk(f"{rel}: critical I/O calls annotated", not miss,
                            "; ".join(f"{x['function']}:{x['line']} {x['calls']}" for x in miss[:5]),
                            "" if not miss else "annotate those functions (they call open/read/write/H5*/MPI_File_*)"))
    # Every main needs INIT and FINI, except that in a tree with an instrumented entry main, other mains carrying no
    # dftracer macro at all (unit-test / tool mains) are reported but do not fail.
    entry_ok = any(i and f_ for _r, _l, i, f_, _m in mains)
    for r_, l_, i, f_, anym in mains:
        ok = i and f_
        if not ok and entry_ok and not anym and not i and not f_:
            checks.append(_vchk(f"{r_}:{l_} main: {init} and {fini}", True,
                                "not instrumented; another main in this set is the instrumented entry point", ""))
            continue
        checks.append(_vchk(f"{r_}:{l_} main: {init} and {fini}", ok, f"init={'yes' if i else 'no'} fini={'yes' if f_ else 'no'}",
                            "" if ok else f"insert {init}(NULL, NULL, NULL) first in main and {fini}() before every exit"))
    if need_main or saw_main:
        checks.append(_vchk(f"project: {init} and {fini} present", has_init and has_fini,
                            f"init={'yes' if has_init else 'no'} fini={'yes' if has_fini else 'no'}",
                            "" if has_init and has_fini else "the entry file's main needs both (a missing FINI truncates the trace)"))
    return checks


def _vpy_checks(files, need_entry: bool = False) -> list:
    """Deterministic checks of annotated Python files: each parses; validate_annotations' own scan finds no
    un-annotated critical I/O and no cost-gate violation; an entry file (if __name__ == "__main__" or def main) has
    initialize_log and finalize(); no decorator is stacked on @property (Python Rule 3)."""
    from dftracer_agents.mcp_tools.tools.session.annotation_validate import _validate_python

    checks = []
    entry = has_init = has_fini = False
    for f in files:
        rel = f.name
        ok, detail = _vcompile(f, "python")
        checks.append(_vchk(f"{rel}: parses", ok, detail, "" if ok else "fix the syntax error (python_lint_annotations, python_dedup_annotations)"))
        if not ok:
            continue
        text = f.read_text(errors="replace")
        tree = _vast.parse(text)
        r = _validate_python(f)
        has_init |= bool(r.get("has_init"))
        has_fini |= bool(r.get("has_fini"))
        if "__main__" in text:
            entry = True
        miss = r.get("findings", [])
        checks.append(_vchk(f"{rel}: coverage and cost gate", not miss,
                            "; ".join(f"{x['function']}:{x['line']} {x['issue'][:70]}" for x in miss[:5]),
                            "" if not miss else "decorate the I/O functions; remove annotations the cost gate rejects"))
        stacked = []
        for fn in _vast.walk(tree):
            if isinstance(fn, (_vast.FunctionDef, _vast.AsyncFunctionDef)):
                decos = [_vast.unparse(d) for d in fn.decorator_list]
                if any(d.split("(")[0].endswith(("property", "setter", "getter", "deleter")) for d in decos) and \
                        any(t in d for d in decos for t in ("_dft.", "_dlp.", "dft_fn", "dft_ai")):
                    stacked.append(f"{fn.name}:{fn.lineno}")
        checks.append(_vchk(f"{rel}: no decorator on a property", not stacked, ", ".join(stacked),
                            "" if not stacked else "use a `with` region inside the property body (Python Rule 3)"))
    if need_entry or entry:
        checks.append(_vchk("project: initialize_log and finalize() present", has_init and has_fini,
                            f"init={'yes' if has_init else 'no'} fini={'yes' if has_fini else 'no'}",
                            "" if has_init and has_fini else "the entry file needs dftracer.initialize_log(...) and .finalize() (Python Rule 2)"))
    return checks


def register(mcp):
    """Register the dftracer_annotate_python tools on *mcp*."""

    @mcp.tool()
    def python_extract_functions(run_id: str, filepath: str) -> str:
        """Extract function definitions with exact line numbers from a Python file.

        Uses Python's built-in ``ast`` module (Python 3.8+ ``end_lineno``
        support) to produce an authoritative function map.  No external
        dependencies required.

        Each returned record contains:

        * ``name``                  — function name
        * ``qualname``              — dotted qualified name (``Class.method``)
        * ``start_line``            — 1-based line of the ``def`` keyword
        * ``decorator_insert_line`` — insert dftracer decorator **before** this
                                       line (= first existing decorator or ``def``)
        * ``body_first_line``       — first line of the function body
        * ``end_line``              — last line of the function
        * ``col_offset``            — column of ``def`` (reflects indentation)
        * ``is_init``               — ``True`` for ``__init__`` methods
        * ``has_staticmethod``      — ``True`` if ``@staticmethod`` already present
        * ``has_property``          — ``True`` if ``@property``/``@cached_property``/
                                      ``@x.setter``/``@x.deleter``/``@x.getter`` present
        * ``is_async``              — ``True`` for ``async def``
        * ``is_entry_point``        — ``True`` for module-level ``main()``
        * ``source``                — always ``"ast"``

        Decorator insertion rule (from the pydftracer API):
          * ``@_dft.log_init``    for ``is_init`` functions
          * a contextual ``with DFTracerFn(...)`` region for ``has_staticmethod``
            OR ``has_property`` — a plain decorator stacked on ``@staticmethod``
            fights its ordering, and stacked on ``@property`` it REPLACES the
            property descriptor entirely (breaking attribute access)
          * ``@_dft.log``         for everything else

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys ``status``, ``message``, ``filepath``,
            ``functions`` (list of dicts), ``count``, ``extractor``.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath

        cache_key = (run_id, filepath)
        if cache_key in _PY_FILE_CACHE:
            source = "\n".join(_PY_FILE_CACHE[cache_key])
        elif abs_path.exists():
            source = abs_path.read_text(errors="replace")
        else:
            return _err(f"File not found in annotated/: {filepath}")

        functions = _extract_functions_from_ast(source)
        return _ok(
            f"Extracted {len(functions)} function(s) from {filepath} using ast.",
            filepath=filepath,
            functions=functions,
            count=len(functions),
            extractor="ast",
        )

    @mcp.tool()
    def python_annotate_file(
        run_id: str,
        filepath: str,
        category: str = "",
        is_entry: bool = False,
        logfile: str = "None",
        data_dir: str = "None",
        process_id: int = -1,
        annotate_nested: bool = True,
        only_functions: str = "",
    ) -> str:
        """Annotate a Python file with generic dftracer decorators.

        Delegates to :func:`_python_annotate_file_impl`.

        ``only_functions`` is a comma-separated allow-list of function names; when
        given, ONLY those functions are decorated. Pass the ``annotate`` list from
        ``python_estimate_file_costs`` to enforce AI/ML cost gating.
        """
        return _python_annotate_file_impl(run_id=run_id, filepath=filepath, category=category, is_entry=is_entry, logfile=logfile, data_dir=data_dir, process_id=process_id, annotate_nested=annotate_nested, only_functions=only_functions)

    @mcp.tool()
    def python_annotate_manual_event(
        run_id: str,
        filepath: str,
        function_name: str,
        event_name: str,
        category: str = "",
        start_time_expr: str = "",
        duration_expr: str = "",
        int_args_expr: str = "{}",
        float_args_expr: str = "{}",
        string_args_expr: str = "{}",
        insert_at: str = "start",
    ) -> str:
        """Insert a MANUAL, explicit-timestamp dftracer event into one function.

        Use this instead of ``python_annotate_file``'s ``@_dft.log`` decorator
        whenever a function's real execution time is NOT what the trace should
        record — e.g. a discrete-event simulator, a replay engine, or anything
        driven by its own internal/fake clock. The auto decorator always
        measures real wall-clock start/end; this tool instead emits a
        ``dftracer.get_instance().log_event(...)`` call with explicit
        ``start_time``/``duration`` VALUES YOU SUPPLY as Python expression
        strings (e.g. ``"int(job.submit_time * 1e9)"``), so the trace reflects
        whatever clock the caller's own domain logic uses.

        This is the tool-based half of a two-step workflow: a human/agent first
        identifies WHICH functions represent simulated/emulated time (the tool
        cannot know this — it requires domain judgment), then calls this tool
        to insert the event correctly and consistently, avoiding hand-written
        mistakes like referencing a per-file ``_dft_log`` handle that is never
        actually wired to the process-wide logger (a real bug found doing this
        by hand: a module-level ``_dft_log = None`` stub gated every manual
        call behind ``if _dft_log is not None`` — always False, so the calls
        were silent dead code). This tool always uses the correct pattern:
        ``dftracer.get_instance().log_event(...)``, the process-wide singleton
        pydftracer itself uses internally, wrapped in ``if _dft_available`` +
        ``try/except`` so a missing/uninitialized dftracer never breaks the
        app.

        Idempotent: if the target function already contains a
        ``dftracer.get_instance().log_event(`` call, this is a no-op.

        Args:
            run_id:            Session identifier returned by ``session_create``.
            filepath:          Path relative to the ``annotated/`` subfolder.
            function_name:     Exact function or method name (bare name, not
                                dotted qualname) to insert the event into.
            event_name:        The ``name=`` argument for ``log_event`` (e.g.
                                ``"job_submit"``).
            category:          The ``cat=`` argument for ``log_event``.
                                Defaults to the file's module stem.
            start_time_expr:   A Python expression (as a string) evaluating to
                                the event's start time in MICROSECONDS, using
                                whatever variables are in scope at the
                                insertion point (e.g.
                                ``"int(job.submit_time * 1e9)"``). REQUIRED.
            duration_expr:     A Python expression (as a string) evaluating to
                                the event's duration in MICROSECONDS. REQUIRED.
            int_args_expr:     A Python dict-literal expression (as a string)
                                for ``log_event``'s ``int_args=``. Each value
                                MUST be a ``(tag_type, value)`` 2-tuple, e.g.
                                ``'{"job_id": (0, int(job.jobid))}'`` — ``0`` is
                                ``TagType.KEY.value`` (a normal key/value tag).
                                A bare value (``{"job_id": int(job.jobid)}``,
                                no tuple) raises
                                ``log_event(): incompatible function
                                arguments`` at the native binding — and since
                                manual ``log_event`` calls are typically
                                wrapped in ``try/except``, that failure is
                                SILENT, producing a trace with zero events
                                from the call site (a real bug found and
                                fixed 2026-07-17 on flux-fiction). Defaults to
                                ``"{}"``.
            float_args_expr:   Same shape/tuple requirement, for ``float_args=``. Defaults to ``"{}"``.
            string_args_expr:  Same shape/tuple requirement, for ``string_args=``. Defaults to ``"{}"``.
            insert_at:         ``"start"`` (default) inserts at the first line
                                of the function body; ``"end"`` inserts just
                                before the function's closing line (after any
                                logic that computes the values referenced in
                                the expressions — use this when
                                start_time/duration are only known by the end
                                of the function).

        Returns:
            JSON string with keys ``status``, ``message``, ``filepath``,
            ``function_name``, ``inserted`` (bool), ``already_present`` (bool).
        """
        from pathlib import Path as _Path

        if not start_time_expr or not duration_expr:
            return _err("start_time_expr and duration_expr are both required.")

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath

        cache_key = (run_id, filepath)
        if cache_key in _PY_FILE_CACHE:
            lines = list(_PY_FILE_CACHE[cache_key])
        elif abs_path.exists():
            lines = abs_path.read_text(errors="replace").splitlines()
        else:
            return _err(f"File not found in annotated/: {filepath}")

        text = "\n".join(lines)
        cat = category or _Path(filepath).stem

        all_fns = _extract_functions_from_ast(text)
        target = next((f for f in all_fns if f["name"] == function_name), None)
        if target is None:
            return _err(f"Function '{function_name}' not found in {filepath}.")

        # Idempotency: don't insert twice into the same function body.
        body_text = "\n".join(lines[target["body_first_line"] - 1: target["end_line"]])
        if "dftracer.get_instance().log_event(" in body_text:
            return _ok(
                f"{function_name} in {filepath} already has a manual log_event call — skipped.",
                filepath=filepath, function_name=function_name,
                inserted=False, already_present=True,
            )

        indent = " " * (target["body_col_offset"])
        block = [
            f"{indent}if _dft_available:",
            f"{indent}    try:",
            f"{indent}        dftracer.get_instance().log_event(",
            f'{indent}            name="{event_name}",',
            f'{indent}            cat="{cat}",',
            f"{indent}            start_time={start_time_expr},",
            f"{indent}            duration={duration_expr},",
            f"{indent}            int_args={int_args_expr},",
            f"{indent}            float_args={float_args_expr},",
            f"{indent}            string_args={string_args_expr},",
            f"{indent}        )",
            f"{indent}    except Exception:",
            f"{indent}        pass",
        ]

        insertions: List[tuple] = []

        # Ensure the module-level import/singleton-availability stub exists.
        if _PY_IMPORT not in text or "_dft_available" not in text:
            import_idx = _last_import_idx(lines)
            for ln in reversed([
                "try:",
                f"    {_PY_IMPORT}",
                f'    _dft = DFTracerFn("{cat}")',
                "    _dft_available = True",
                "except ImportError:",
                "    _dft = None",
                "    _dft_available = False",
            ]):
                insertions.append((import_idx, ln))

        if insert_at == "end":
            insert_idx = target["end_line"]  # after the last line (0-based == end_line)
        else:
            insert_idx = target["body_first_line"] - 1

        for ln in reversed(block):
            insertions.append((insert_idx, ln))

        insertions.sort(key=lambda x: -x[0])
        for idx, ln in insertions:
            lines.insert(idx, ln)

        _PY_FILE_CACHE[cache_key] = list(lines)
        abs_path.write_text("\n".join(lines) + "\n")

        return _ok(
            f"Inserted manual log_event('{event_name}') into {function_name} ({filepath}).",
            filepath=filepath, function_name=function_name,
            inserted=True, already_present=False,
        )

    @mcp.tool()
    def python_annotate_project(
        run_id: str,
        exclude_patterns: List[str] = None,
        logfile: str = "None",
        data_dir: str = "None",
        process_id: int = -1,
        jobs: int = 0,
    ) -> str:
        """Annotate every Python source file in the ``annotated/`` workspace in one call.

        This is the Python counterpart of ``clang_annotate_project`` and follows
        the exact same discover -> classify -> annotate-in-order shape — it is
        NOT a regex sweep, it drives the same AST-backed ``python_annotate_file``
        (built on the stdlib ``ast`` module, no regex parsing of Python source)
        that already exists per-file, just applied project-wide so a whole tree
        can be annotated with one call instead of one per file.

        Discovers all ``.py`` files under ``annotated/``, determines which are
        entry points (a module-level ``main`` function, or an
        ``if __name__ == "__main__":`` guard), and annotates them in the correct
        order:

        1. **Library / inner files first** — annotated with ``is_entry=False``.
        2. **Entry-point files last** — annotated with ``is_entry=True`` so
           ``dftracer.initialize_log(...)`` / ``_dft_log.finalize()`` are
           inserted around ``main()``.

        Each file is processed by ``python_annotate_file`` which:

        * Inserts the ``dftracer.python`` import and a per-file ``DFTracerFn``.
        * Decorates every function/method found by the AST function map
          (``@_dft.log`` / ``@_dft.log_init``, or a contextual ``with
          DFTracerFn(...)`` region for ``@staticmethod``).
        * Is idempotent — already-annotated files are silently skipped.

        Paths can be excluded by passing glob-style substrings in
        ``exclude_patterns`` (e.g. ``["test/", "vendor/"]``).  The following
        patterns are always excluded regardless: ``/test/``, ``/tests/``,
        ``/vendor/``, ``/third_party/``, ``/__pycache__/``, ``/.git/``.

        Args:
            run_id:           Session identifier returned by ``session_create``.
            exclude_patterns: Extra path substrings to skip.
            logfile:          ``logfile`` argument for ``initialize_log`` on
                               every entry-point file found.
            data_dir:         ``data_dir`` argument for ``initialize_log``.
            process_id:       ``process_id`` argument for ``initialize_log``.
            jobs:             Parallel worker processes (each file is
                              independent). ``0`` = ``$DFTRACER_ANNOTATE_JOBS``
                              or min(16, CPU count), but only when there are at
                              least ``PY_PARALLEL_MIN_FILES`` (2000) files (the
                              per-file ast work is ~10 ms; a spawn pool costs ~3 s
                              to start); ``1`` = serial. Entry
                              files are annotated after every library file.
                              Output is identical to the serial run.

        Returns:
            JSON string with keys:

            * ``status``       — ``"ok"`` or ``"error"``.
            * ``total_files``  — number of ``.py`` files discovered.
            * ``annotated``    — number of files newly annotated.
            * ``skipped``      — number of files already annotated or errored.
            * ``errors``       — list of ``{"file": ..., "error": ...}`` dicts.
            * ``file_results`` — per-file summary dicts.
        """
        import json as _json
        from pathlib import Path as _Path

        ws = _ws(run_id)
        ann_dir = ws / "annotated"
        if not ann_dir.exists():
            return _err(f"annotated/ directory not found in workspace {run_id}")

        _ALWAYS_EXCLUDE = (
            "/test/", "/tests/", "/vendor/", "/third_party/",
            "/__pycache__/", "/.git/",
        )
        extra_exclude = list(exclude_patterns) if exclude_patterns else []

        def _is_excluded(p: "_Path") -> bool:
            s = str(p)
            for pat in _ALWAYS_EXCLUDE:
                if pat in s:
                    return True
            for pat in extra_exclude:
                if pat in s:
                    return True
            return False

        all_files = sorted(
            p for p in ann_dir.rglob("*.py") if not _is_excluded(p)
        )

        if not all_files:
            return _ok(
                "No Python source files found in annotated/.",
                total_files=0, annotated=0, skipped=0, errors=[], file_results=[],
            )

        def _is_entry(p: "_Path") -> bool:
            try:
                text = p.read_text(errors="replace")
            except OSError:
                return False
            if 'if __name__ == "__main__"' in text or "if __name__ == '__main__'" in text:
                return True
            return any(f["is_entry_point"] for f in _extract_functions_from_ast(text))

        regular_files, entry_files = [], []
        for p in all_files:
            (entry_files if _is_entry(p) else regular_files).append(p)

        file_results, errors = [], []
        annotated_count = skipped_count = 0

        from dftracer_agents.mcp_tools.tools.session.annotation_clang import annotate_jobs, run_file_pool

        def _kw(p: "_Path") -> dict:
            return dict(run_id=run_id, filepath=str(p.relative_to(ann_dir)),
                        is_entry=p in entry_files, logfile=logfile,
                        data_dir=data_dir, process_id=process_id)

        n_files = len(all_files)
        if jobs and jobs > 0:
            n_jobs = annotate_jobs(jobs, n_files)
        else:
            n_jobs = annotate_jobs(0, n_files) if n_files >= PY_PARALLEL_MIN_FILES else 1

        def _serial(kw: dict):
            return _python_annotate_file_impl(**kw), None

        phases = [[_kw(p) for p in regular_files], [_kw(p) for p in entry_files]]
        outs = run_file_pool(_python_annotate_file_worker, phases, n_jobs, serial_fn=_serial)
        raw_by_file = {}
        for p, o in zip(regular_files + entry_files, outs[0] + outs[1]):
            if isinstance(o, tuple):
                raw, cached = o
                if cached is not None:   # mirror the worker's cache entry
                    _PY_FILE_CACHE[(run_id, str(p.relative_to(ann_dir)))] = cached
                raw_by_file[p] = raw
            else:
                raw_by_file[p] = o       # the file's exception

        for p in regular_files + entry_files:
            rel = str(p.relative_to(ann_dir))
            try:
                raw = raw_by_file[p]
                if isinstance(raw, Exception):
                    raise raw
                result = _json.loads(raw)
                already = result.get("already_annotated", False)
                if result.get("status") == "ok":
                    if already:
                        skipped_count += 1
                    else:
                        annotated_count += 1
                    file_results.append({
                        "file": rel,
                        "status": "ok",
                        "already_annotated": already,
                        "functions": result.get("functions", 0),
                        "insertions": result.get("insertions", 0),
                    })
                else:
                    errors.append({"file": rel, "error": result.get("message", "unknown error")})
                    skipped_count += 1
                    file_results.append({"file": rel, "status": "error", "error": result.get("message")})
            except Exception as exc:
                errors.append({"file": rel, "error": str(exc)})
                skipped_count += 1
                file_results.append({"file": rel, "status": "error", "error": str(exc)})

        total = len(all_files)
        msg = (
            f"Project annotation complete: {annotated_count}/{total} file(s) annotated, "
            f"{skipped_count} skipped/already-done, {len(errors)} error(s)."
        )
        return _ok(
            msg,
            total_files=total,
            annotated=annotated_count,
            skipped=skipped_count,
            errors=errors,
            file_results=file_results,
        )

    @mcp.tool()
    def python_lint_annotations(run_id: str, filepath: str) -> str:
        """Lint an annotated Python file for dftracer decorator/ordering violations.

        The Python counterpart of ``clang_lint_annotations`` — same
        ``{"rule": ..., "line": ..., "message": ...}`` finding shape, but
        checking the pydftracer decorator API instead of the C macro API.
        Uses the stdlib ``ast`` module for structure (no regex parsing of
        Python semantics); only line-presence checks for the import/init
        statements use simple string containment.

        Checks:

        * **PL1 — import before use**: ``from dftracer.python import
          dftracer, dft_fn as DFTracerFn`` must appear before the first
          ``_dft = DFTracerFn(...)`` / ``@_dft.log`` / ``@_dft.log_init``
          reference — a decorator referencing an undefined name is a
          ``NameError`` at import time, not a soft failure.
        * **PL2 — `_dft` defined before first decorator use**: every
          ``@_dft.log`` / ``@_dft.log_init`` line must have a preceding
          ``_dft = DFTracerFn(...)`` assignment in the same file.
        * **PL3 — INIT before any decorated function runs, in entry files**:
          if the file calls ``dftracer.initialize_log(...)``, that call must
          appear before the first ``@_dft.log``/``@_dft.log_init`` line.
        * **PL4 — missing finalize in an entry file**: a file that calls
          ``dftracer.initialize_log(...)`` must also call
          ``_dft_log.finalize()`` somewhere — an entry point that never
          finalizes truncates its own trace.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path to the file relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys:
                * ``status``      — ``"ok"`` (always; violations are in ``issues``).
                * ``passed``      — ``True`` when no issues were found.
                * ``issues``      — list of ``{"rule", "line", "message"}`` dicts.
                * ``issue_count`` — total number of violations.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        text = abs_path.read_text(errors="replace")
        lines = text.splitlines()
        issues: List[dict] = []

        import_line = None
        dft_assign_line = None
        init_line = None
        finalize_line = None
        first_decorator_line = None

        for i, ln in enumerate(lines, start=1):
            s = ln.strip()
            if "from dftracer.python import" in s and import_line is None:
                import_line = i
            if re.match(r'_dft\s*=\s*DFTracerFn\(', s) and dft_assign_line is None:
                dft_assign_line = i
            if "dftracer.initialize_log(" in s and init_line is None:
                init_line = i
            if re.search(r'_dft_log\.finalize\(\)', s) and finalize_line is None:
                finalize_line = i
            if re.match(r'@_dft\.(log|log_init)\b', s) and first_decorator_line is None:
                first_decorator_line = i

        if first_decorator_line is not None:
            if import_line is None:
                issues.append({
                    "rule": "PL1",
                    "line": first_decorator_line,
                    "message": (
                        f"@_dft decorator used at line {first_decorator_line} but "
                        "the dftracer.python import is missing from this file"
                    ),
                })
            elif import_line > first_decorator_line:
                issues.append({
                    "rule": "PL1",
                    "line": import_line,
                    "message": (
                        f"dftracer.python import (line {import_line}) appears after "
                        f"its first use at line {first_decorator_line}"
                    ),
                })

            if dft_assign_line is None:
                issues.append({
                    "rule": "PL2",
                    "line": first_decorator_line,
                    "message": (
                        f"@_dft decorator used at line {first_decorator_line} but "
                        "no `_dft = DFTracerFn(...)` assignment was found in this file"
                    ),
                })
            elif dft_assign_line > first_decorator_line:
                issues.append({
                    "rule": "PL2",
                    "line": dft_assign_line,
                    "message": (
                        f"_dft = DFTracerFn(...) (line {dft_assign_line}) appears after "
                        f"its first decorator use at line {first_decorator_line}"
                    ),
                })

        if init_line is not None:
            if first_decorator_line is not None and init_line > first_decorator_line:
                issues.append({
                    "rule": "PL3",
                    "line": init_line,
                    "message": (
                        f"dftracer.initialize_log(...) (line {init_line}) appears after "
                        f"the first @_dft decorator use at line {first_decorator_line} — "
                        "INIT should precede any function it will trace"
                    ),
                })
            if finalize_line is None:
                issues.append({
                    "rule": "PL4",
                    "line": init_line,
                    "message": (
                        f"dftracer.initialize_log(...) at line {init_line} has no "
                        "matching _dft_log.finalize() anywhere in this file — the "
                        "trace will be truncated"
                    ),
                })

        return _ok(
            f"Lint complete: {len(issues)} issue(s) found." if issues else "Lint complete: no issues found.",
            passed=len(issues) == 0,
            issues=issues,
            issue_count=len(issues),
        )

    @mcp.tool()
    def python_dedup_annotations(run_id: str, filepath: str) -> str:
        """Detect and fix duplicate dftracer annotation logger/decorator stacks.

        Real bug this catches: when a Python file is annotated twice by two
        different tools/passes (e.g. ``python_annotate_file`` then
        ``python_annotate_ai_file``, or the generic tool run twice), each pass
        inserts its own ``DFTracerFn("<same category>")`` logger variable
        (``_dft``, ``_dlp``) and stacks its own decorator on every function.
        The result is every call emitting TWO overlapping, near-duplicate
        trace spans with the same category — the "events in ranges with
        similar names" symptom. ``python_lint_annotations`` does not catch
        this because its checks are single-occurrence guards; this tool is
        the dedicated fix.

        Always run this AFTER any annotation pass (whole-file, AI, or
        project-level) and BEFORE ``python_lint_annotations`` /
        ``validate_annotations`` — it is idempotent and a no-op on files with
        no duplication.

        Fixes applied (in order):

        1. **Duplicate logger assignments** — when two or more top-level
           ``var = DFTracerFn("<cat>")`` assignments share the same category
           string, keep exactly one (``_dft`` if present, else the first
           found) and delete the other assignment line(s).
        2. **Duplicate decorators** — delete every ``@<removed_var>.log`` /
           ``@<removed_var>.log_init`` line, since the kept variable's
           decorator on the same function already covers it.
        3. **Duplicate dftracer.python imports** — when more than one
           ``from dftracer.python import ...`` line is present, keep only the
           most complete one (the one importing the most names, e.g. including
           ``ai as dft_ai``) and delete the rest.
        4. **Duplicate initialize_log() calls** — when more than one
           ``_dft_log = dftracer.initialize_log(...)`` assignment exists, keep
           the first and delete the rest, reporting each removed call's
           arguments so a human can confirm no real config was lost.
        5. **Duplicate finalize() calls** — collapse consecutive/duplicate
           ``_dft_log.finalize()`` calls in the same function down to one.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys:
                * ``status``               — ``"ok"`` or ``"error"``.
                * ``changed``              — ``True`` if any fix was applied.
                * ``removed_loggers``      — list of ``{"var", "category", "line"}``.
                * ``removed_decorators``   — list of ``{"var", "line", "text"}``.
                * ``removed_imports``      — list of removed import line texts.
                * ``removed_inits``        — list of removed
                  ``initialize_log(...)`` call texts (for manual review).
                * ``removed_finalizes``    — count of duplicate finalize calls removed.
                * ``total_lines``          — line count after fixing.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        cache_key = (run_id, filepath)
        if cache_key in _PY_FILE_CACHE:
            lines = list(_PY_FILE_CACHE[cache_key])
        else:
            lines = abs_path.read_text(errors="replace").splitlines()

        removed_loggers: List[dict] = []
        removed_decorators: List[dict] = []
        removed_imports: List[str] = []
        removed_inits: List[str] = []
        removed_finalizes = 0

        to_delete: set = set()

        # ── Step 1: find top-level `var = DFTracerFn("cat")` assignments ──
        logger_re = re.compile(r'^(\w+)\s*=\s*DFTracerFn\((.*)\)\s*$')
        by_cat: Dict[str, List[tuple]] = {}
        for i, ln in enumerate(lines):
            if ln.startswith((" ", "\t")):
                continue
            m = logger_re.match(ln.strip())
            if m:
                var, cat_expr = m.group(1), m.group(2)
                by_cat.setdefault(cat_expr, []).append((i, var))

        removed_vars: set = set()
        for cat_expr, occurrences in by_cat.items():
            if len(occurrences) < 2:
                continue
            keep_var = next((v for _, v in occurrences if v == "_dft"), occurrences[0][1])
            for idx, var in occurrences:
                if var == keep_var and idx == next(i for i, v in occurrences if v == keep_var):
                    continue
                to_delete.add(idx)
                removed_vars.add(var)
                removed_loggers.append({"var": var, "category": cat_expr, "line": idx + 1})

        # ── Step 2: strip decorators referencing a removed logger var ─────
        if removed_vars:
            dec_re = re.compile(r'^@(\w+)\.(log|log_init)\b')
            for i, ln in enumerate(lines):
                m = dec_re.match(ln.strip())
                if m and m.group(1) in removed_vars:
                    to_delete.add(i)
                    removed_decorators.append({"var": m.group(1), "line": i + 1, "text": ln.strip()})

        # ── Step 3: dedupe `from dftracer.python import ...` lines ────────
        import_idxs = [i for i, ln in enumerate(lines) if ln.strip().startswith("from dftracer.python import")]
        if len(import_idxs) > 1:
            best = max(import_idxs, key=lambda i: len(lines[i]))
            for i in import_idxs:
                if i != best:
                    to_delete.add(i)
                    removed_imports.append(lines[i].strip())

        # ── Step 4: dedupe `_dft_log = dftracer.initialize_log(...)` ──────
        init_re = re.compile(r'^(\w+)\s*=\s*dftracer\.initialize_log\(')
        init_idxs = [i for i, ln in enumerate(lines) if init_re.match(ln.strip()) and i not in to_delete]
        if len(init_idxs) > 1:
            keep = init_idxs[0]
            for i in init_idxs[1:]:
                to_delete.add(i)
                removed_inits.append(lines[i].strip())

        # ── Step 5: collapse duplicate consecutive finalize() calls ───────
        # Handles the common "two annotation passes both appended
        # `_dft_log.finalize()` right before `main()`'s return" case.
        fin_re = re.compile(r'^(\w+)\.finalize\(\)\s*$')
        i = 1
        while i < len(lines):
            cur = lines[i].strip()
            prev = lines[i - 1].strip()
            if (i not in to_delete and (i - 1) not in to_delete
                    and fin_re.match(cur) and cur == prev):
                to_delete.add(i)
                removed_finalizes += 1
            i += 1

        if not to_delete:
            return _ok(
                f"{filepath}: no duplicate annotations found.",
                changed=False,
                removed_loggers=[], removed_decorators=[], removed_imports=[],
                removed_inits=[], removed_finalizes=0,
                total_lines=len(lines),
            )

        new_lines = [ln for i, ln in enumerate(lines) if i not in to_delete]
        _PY_FILE_CACHE[cache_key] = list(new_lines)
        abs_path.write_text("\n".join(new_lines) + "\n")

        msg = (
            f"Deduplicated {filepath}: removed {len(removed_loggers)} logger(s), "
            f"{len(removed_decorators)} decorator(s), {len(removed_imports)} duplicate import(s), "
            f"{len(removed_inits)} duplicate init(s), {removed_finalizes} duplicate finalize(s)."
        )
        return _ok(
            msg,
            changed=True,
            removed_loggers=removed_loggers,
            removed_decorators=removed_decorators,
            removed_imports=removed_imports,
            removed_inits=removed_inits,
            removed_finalizes=removed_finalizes,
            total_lines=len(new_lines),
        )

    @mcp.tool()
    def python_write_annotated_file(run_id: str, filepath: str) -> str:
        """Flush the in-memory annotated Python file buffer to disk.

        Call this after a series of in-memory annotation operations to commit
        all changes with a single write.  If the file is not in the in-memory
        cache, returns an error — call ``python_annotate_file`` first.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with ``status``, ``message``, ``filepath``,
            ``total_lines``.
        """
        cache_key = (run_id, filepath)
        if cache_key not in _PY_FILE_CACHE:
            return _err(
                f"No in-memory state for {filepath} — "
                f"call python_annotate_file first.",
                filepath=filepath,
            )
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        lines = _PY_FILE_CACHE[cache_key]
        abs_path.write_text("\n".join(lines) + "\n")
        del _PY_FILE_CACHE[cache_key]
        return _ok(
            f"Wrote {len(lines)} lines to {filepath}.",
            filepath=filepath,
            total_lines=len(lines),
        )

    @mcp.tool()
    def session_annotate_python_file(
        run_id: str,
        filepath: str,
        build_errors: str = "",
        user_notes: str = "",
    ) -> str:
        """Annotate a single .py source file with dftracer Python decorators.

        This tool is the per-file Python annotation agent.  It is designed to be
        called in parallel with other per-file annotation calls — each invocation
        is independent and operates on a single file inside the session workspace.

        Procedure (mirrors the annotate-python.yaml recipe):

        **Step 0 — Load lessons**
            Read /workspaces/dftracer-agents/.agents/skills/dftracer-annotation-lessons/SKILL.md
            and apply all applicable lessons before writing anything.

        **Step 1 — Read and classify every function/method**
            Read the current state from annotated/<filepath>.
            Record the original line count — the final written file MUST have MORE lines.

            Fetch Python-specific docs first::

                dftracer__docs_search(
                    query="dftracer_fn decorator DFTracer initialize_log finalize_log \\
                           process_id log_file data_dir",
                    source="pydftracer", fetch_content=True
                )

            Build a function/method inventory:

            MANDATORY (always annotate — never apply Rule 0):
              • Checkpoint I/O: *_load, *_save, *_read, *_write, *_fetch, *_push
              • __init__ / __del__ that open/close file handles or network connections
              • Any function calling open(), h5py.File, np.load, torch.load,
                pickle.load, MPI.Send/Recv, or similar I/O APIs

            ANNOTATE if the function:
              • Does file or network I/O
              • Large data transfers (numpy/torch operations, tensor copies)
              • Training loops, inference loops, benchmark drivers
              • MPI communication wrappers

            SKIP (Rule 0) ONLY if ALL of these are true:
              • @property, one-liner getters with no I/O
              • __repr__, __str__, __len__ with no I/O
              • String formatters, argument validators ≤ 5 lines
              • Small helpers called in tight inner loops with no I/O

        **Step 2 — Map exit paths for initialize_log / finalize_log**
            Python decorators handle START/END automatically — no manual exit mapping.
            DFTracer.initialize_log / finalize_log require explicit placement:

              • initialize_log: add after MPI.Init() if mpi4py used; otherwise at the
                top of ``if __name__ == "__main__"`` or the entry function body
              • finalize_log: add before MPI.Finalize() and before any sys.exit() call,
                and at the end of the main execution path

        **Step 3 — Apply annotation (per-function incremental loop)**
            Process functions ONE AT A TIME.  After each function:

            3a. Insert ``@dftracer_fn(cat="<CAT>", comp="<type>")`` on the line
                IMMEDIATELY BEFORE the function's ``def`` statement (below any
                other existing decorators — dftracer_fn must be CLOSEST to def).

            3b. Write the updated file via ``session_write_file``

            3c. Syntax check after EVERY function write::

                    WS=/workspaces/dftracer-agents/workspaces/<run_id>
                    python3 -c "
                    import ast, sys
                    try:
                        ast.parse(open('${WS}/annotated/<filepath>').read())
                    except SyntaxError as e:
                        print(f'SyntaxError line {e.lineno}: {e.msg}')
                        sys.exit(1)
                    " 2>&1

            3d. On syntax error: fix ONLY the failing function (max 2 retries),
                then strip decorator from that function only and mark PENDING.

        **Step 4 — Entry point initialisation**
            For files containing ``if __name__ == "__main__"`` or the top-level
            entry function:
              • Add ``from dftracer.logger import dftracer_fn, DFTracer`` import
              • Add ``DFTracer.initialize_log(log_file=..., data_dir=..., process_id=...)``
              • Add ``DFTracer.finalize_log()`` before every exit path
              • Write file and run syntax check

        **Step 5 — Coverage verification**::

            WS=/workspaces/dftracer-agents/workspaces/<run_id>
            F=${WS}/annotated/<filepath>
            # 1. Decorator count vs comp count (must be equal)
            echo "decorators: $(grep -c '@dftracer_fn' $F)"
            echo "comp=:      $(grep -c 'comp=' $F)"
            # 2. Import present?
            grep -n "from dftracer" $F
            # 3. initialize_log / finalize_log
            grep -n "initialize_log\|finalize_log" $F
            # 4. Property methods with decorator (should be zero)
            grep -B2 "@dftracer_fn" $F | grep "@property"

        **Step 6 — Output per-file report** in this structure::

            FILE: <filepath>
            STATUS: DONE | PARTIAL | FAILED
            ANNOTATED: <N> functions  (io=<n>, comm=<n>, mem=<n>, cpu=<n>)
            SKIPPED:   <function names with Rule 0 justification>
            PENDING:   <functions not yet annotated with reason>
            NEW_ERRORS: <any new compile/runtime errors discovered>
            LESSONS:   <new patterns or pitfalls not yet in the lessons file>

        **Absolute rules (never violate):**
            • One @dftracer_fn per function — never stack two dftracer decorators
            • cat= is required — use consistent names across the file
              ("IO", "Compute", "MPI", "Data", "Init" are good conventions)
            • comp= is required
            • Never decorate @property methods
            • initialize_log must appear in every entry point file
            • finalize_log must appear before every process exit path
            • dftracer_fn must be the LAST decorator (closest to def)

        **Python-specific pitfalls:**
            PP1  Missing import       → ImportError at runtime; add from dftracer.logger import ...
            PP2  comp= missing        → decorator count ≠ comp count; add comp= to each
            PP3  Inconsistent cat=    → mixed "io"/"IO"/"file"; standardise across file
            PP4  initialize_log missing → empty trace; add to every entry point
            PP5  finalize_log missing  → trace truncated; add before sys.exit and MPI.Finalize
            PP6  Decorator order wrong → @dftracer_fn above other decorators; move it below them
            PP7  @property decorated  → conflict with property; skip @property methods
            PP8  DFTRACER_INIT= incorrect → if code calls DFTracer.initialize_log(), set
                                            DFTRACER_INIT=0 when running the script

        **BUILD ERROR MODE:**
            If build_errors is non-empty, it contains a runtime error or ImportError.
            Focus on fixing those issues first, then continue with unannotated functions.

        Args:
            run_id:       Session identifier (e.g. ``myapp/20260616_120000``).
            filepath:     Path of the ``.py`` file relative to ``annotated/`` subfolder.
            build_errors: Runtime error or import error from a failed test run.
            user_notes:   Free-text feedback from the pipeline orchestrator.

        Returns:
            JSON with ``status``, ``filepath``, ``annotated``, ``skipped``,
            ``pending``, ``lessons``, and ``report`` keys.
        """
        ws = _ws(run_id)
        if not ws.exists():
            return _err(f"Workspace not found for run_id: {run_id}")

        ann_dir = ws / "annotated"
        if not ann_dir.exists():
            return _err("annotated/ directory not found — run session_copy_annotated first.")

        target = ann_dir / filepath
        if not target.exists():
            return _err(f"File not found: annotated/{filepath}")

        if target.suffix.lower() != ".py":
            return _err(f"session_annotate_python_file only handles .py files, got: {filepath}")

        try:
            original_content = target.read_text(errors="ignore")
        except OSError as e:
            return _err(f"Cannot read {filepath}: {e}")

        original_lines = len(original_content.splitlines())

        if "dftracer_fn" in original_content and not build_errors:
            annotated_count = original_content.count("@dftracer_fn")
            return _ok(
                f"File already annotated with {annotated_count} decorator(s). "
                "Pass build_errors to enter fix mode.",
                filepath=filepath,
                status="already_annotated",
                annotated=annotated_count,
                skipped=[],
                pending=[],
                lessons=[],
            )

        # Detect entry-point file
        is_entry = (
            'if __name__ == "__main__"' in original_content
            or "if __name__ == '__main__'" in original_content
        )

        # Count functions and methods
        top_level_fns = [
            m.group(1) for m in re.finditer(r'^def\s+(\w+)', original_content, re.MULTILINE)
        ]
        method_fns = [
            m.group(1) for m in re.finditer(r'^    def\s+(\w+)', original_content, re.MULTILINE)
        ]

        # Detect I/O calls
        io_calls = []
        io_patterns = [
            "open(", "h5py.File", "np.load", "np.save", "torch.load", "torch.save",
            "pickle.load", "pickle.dump", "MPI.Send", "MPI.Recv", "aiofiles",
        ]
        for i, line in enumerate(original_content.splitlines(), start=1):
            for pat in io_patterns:
                if pat in line and not line.strip().startswith("#"):
                    io_calls.append(f"line {i}: {pat}")
                    break

        context = {
            "run_id": run_id,
            "filepath": filepath,
            "workspace": str(ws),
            "annotated_dir": str(ann_dir),
            "original_line_count": original_lines,
            "is_entry_point": is_entry,
            "top_level_functions": len(top_level_fns),
            "methods": len(method_fns),
            "has_build_errors": bool(build_errors),
            "build_errors_excerpt": build_errors[:500] if build_errors else "",
            "user_notes": user_notes,
        }

        return _ok(
            f"Python file ready for annotation — {original_lines} lines. "
            + (f"Entry point: yes (initialize_log + finalize_log needed). " if is_entry else "")
            + f"Functions: {len(top_level_fns)} top-level, {len(method_fns)} methods. "
            + (f"BUILD ERROR MODE: {len(build_errors.splitlines())} error line(s). " if build_errors else "")
            + "Use python_extract_functions, then session_write_file per function "
            "with ast.parse syntax check after each. "
            "See tool docstring for complete Python procedure.",
            context=context,
            io_calls_detected=io_calls[:20],
            annotation_procedure={
                "step_0": "Read lessons from dftracer-annotation-lessons/SKILL.md",
                "step_1": f"Read annotated/{filepath} — classify all functions",
                "step_2": "Map exit paths for initialize_log/finalize_log (entry files only)",
                "step_3": "Per-function loop: @dftracer_fn(cat=, comp=), write, ast.parse check",
                "step_4": "Add DFTracer.initialize_log / finalize_log (entry files only)",
                "step_5": "Coverage: decorator count == comp count; no @property decorated",
                "step_6": "Output per-file report with STATUS/ANNOTATED/SKIPPED/PENDING",
            },
            build_error_mode=bool(build_errors),
        )

    @mcp.tool()
    def dftracer_annotate_python_check(path: str = "", source: str = "", entry: bool = False) -> dict:
        """Validator for dftracer_annotate_python: rule deterministically on annotated Python (never edits it).

        Give `path` (an annotated .py file or a tree of them) or `source` (one file's text, written to a temp dir).
        Checks: every file parses (ast.parse); validate_annotations' own scan finds no function doing critical I/O
        (open/load/save/state_dict/File/read_csv/all_reduce/barrier/...) without a dftracer decorator or `with`
        region, no @log_static, and no generic annotation the cost gate rejects; no dftracer decorator stacked on a
        @property/@x.setter (Python Rule 3); and when a file is an entry point (`__main__`) or `entry` is true,
        dftracer.initialize_log(...) and .finalize() are both present.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], files}.
        """
        with _vtempfile.TemporaryDirectory() as tmp:
            files, err = _vfiles(path, source, "python", tmp)
            if not files:
                return _verdict([_vchk("input", False, err, "pass an annotated .py file or tree, or its source text")])
            return _verdict(_vpy_checks(files, need_entry=entry), files=[f.name for f in files])
