"""dftracer_annotate_ai: dftracer AI/ML-region annotation of Python (dft_ai.* decorators and regions): find_source_files, python_annotate_ai_file, python_write_ai_file, dftracer_get_ai_annotation, session_detect_ml_workload, session_search_local_papers, session_ml_append_lesson, plus the validator dftracer_annotate_ai_check. Tools moved unchanged from dftracer_agents.mcp_tools.tools.session.annotation_ai."""

from __future__ import annotations

from datetime import (
    datetime,
    timezone,
)
from dftracer_agents.mcp_tools.tools.session.annotation_ai import (
    _AI_FILE_CACHE,
    _LANG_EXTS,
    _LESSONS_ANCHOR,
    _LESSONS_REL,
    _LOCAL_PAPERS_SUBDIR,
    _LOCAL_PAPER_INDEX,
    _python_annotate_ai_file_impl,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
    _ws,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
    Dict,
    List,
    Optional,
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


from dftracer_agents.mcp_tools.tools.session.annotation_ai import _AI_REGION_PATTERNS

# dft_ai regions documented by dftracer-ml-annotate / dftracer-annotate-python and emitted by the AI annotator.
_AI_KNOWN_REGIONS = (
    "dft_ai",
    "dft_ai.checkpoint.capture",
    "dft_ai.checkpoint.restart",
    "dft_ai.comm",
    "dft_ai.comm.all_gather",
    "dft_ai.comm.all_reduce",
    "dft_ai.comm.all_to_all",
    "dft_ai.comm.barrier",
    "dft_ai.comm.broadcast",
    "dft_ai.comm.gather",
    "dft_ai.comm.receive",
    "dft_ai.comm.reduce",
    "dft_ai.comm.reduce_scatter",
    "dft_ai.comm.scatter",
    "dft_ai.comm.send",
    "dft_ai.compute",
    "dft_ai.compute.backward",
    "dft_ai.compute.forward",
    "dft_ai.compute.step",
    "dft_ai.data.io.close",
    "dft_ai.data.io.open",
    "dft_ai.data.io.read",
    "dft_ai.data.io.write",
    "dft_ai.data.item",
    "dft_ai.dataloader.fetch",
    "dft_ai.data.preprocess",
    "dft_ai.data.preprocess.derive",
    "dft_ai.device.transfer",
    "dft_ai.pipeline.evaluate",
    "dft_ai.pipeline.test",
    "dft_ai.pipeline.train",
)


def register(mcp):
    """Register the dftracer_annotate_ai tools on *mcp*."""

    @mcp.tool()
    def find_source_files(
        folder: str,
        language: str,
        run_id: str = "",
        recursive: bool = True,
        exclude_patterns: List[str] = None,
    ) -> str:
        """Recursively list source files of the given language in a folder.

        Works without a session (pass an absolute *folder* path) or within a
        session workspace (pass *run_id* and a path relative to the session root).

        Supported languages / extensions:

        +----------+----------------------------------+
        | language | extensions                       |
        +==========+==================================+
        | c        | .c                               |
        +----------+----------------------------------+
        | cpp      | .cpp .cxx .cc .C                 |
        +----------+----------------------------------+
        | python   | .py                              |
        +----------+----------------------------------+
        | all      | all of the above                 |
        +----------+----------------------------------+

        Args:
            folder:           Absolute path to the directory to search, OR a path
                              relative to the session workspace root when *run_id*
                              is provided.
            language:         One of ``"c"``, ``"cpp"``, ``"c++"``, ``"python"``,
                              ``"py"``, ``"all"`` (case-insensitive).
            run_id:           Optional session identifier (from ``session_create``).
                              When supplied, *folder* is resolved relative to the
                              session workspace directory.
            recursive:        If ``True`` (default) search sub-directories.
            exclude_patterns: Optional list of glob patterns to exclude
                              (e.g. ``["**/test*", "**/__pycache__/**"]``).

        Returns:
            JSON with ``status``, ``message``, ``files`` (list of dicts with
            ``path``, ``size_bytes``, ``language``), ``count``, ``language``,
            ``folder``.
        """
        lang_key = language.strip().lower()
        if lang_key not in _LANG_EXTS:
            return _err(
                f"Unknown language '{language}'. "
                f"Supported: {sorted(_LANG_EXTS)}",
                supported=sorted(_LANG_EXTS),
            )
        exts = set(_LANG_EXTS[lang_key])

        # Resolve the folder
        if run_id:
            ws = _ws(run_id)
            base = ws / folder
        else:
            base = Path(folder)

        if not base.exists():
            return _err(f"Folder does not exist: {base}", folder=str(base))
        if not base.is_dir():
            return _err(f"Path is not a directory: {base}", folder=str(base))

        exclude = exclude_patterns or []

        # Walk
        glob_fn = base.rglob if recursive else base.glob
        found: List[dict] = []
        for p in sorted(glob_fn("*")):
            if not p.is_file():
                continue
            if p.suffix not in exts:
                continue
            # Apply exclusion patterns
            rel = p.relative_to(base)
            if any(rel.match(pat) for pat in exclude):
                continue
            # Determine language label
            if p.suffix == ".py":
                file_lang = "python"
            elif p.suffix == ".c":
                file_lang = "c"
            else:
                file_lang = "cpp"
            found.append({
                "path": str(p),
                "relative_path": str(rel),
                "size_bytes": p.stat().st_size,
                "language": file_lang,
            })

        return _ok(
            f"Found {len(found)} {language} file(s) in {base}.",
            files=found,
            count=len(found),
            language=language,
            folder=str(base),
        )

    @mcp.tool()
    def python_annotate_ai_file(
        run_id: str,
        filepath: str,
        category: str = "",
        is_entry: bool = False,
        logfile: str = "None",
        data_dir: str = "None",
        process_id: int = -1,
        annotate_loops: bool = True,
        annotate_nested: bool = True,
    ) -> str:
        """Annotate a Python file with dftracer AI/ML region decorators.

        Delegates to :func:`_python_annotate_ai_file_impl`. See that function for
        the full behaviour (decorator selection, loop wrapping, import injection,
        idempotency).
        """
        return _python_annotate_ai_file_impl(
            run_id=run_id, filepath=filepath, category=category,
            is_entry=is_entry, logfile=logfile, data_dir=data_dir,
            process_id=process_id, annotate_loops=annotate_loops,
            annotate_nested=annotate_nested,
        )

    @mcp.tool()
    def python_write_ai_file(run_id: str, filepath: str) -> str:
        """Flush the in-memory AI-annotated Python file buffer to disk.

        Call this after one or more in-memory AI annotation operations if you
        want to do the disk write separately.  ``python_annotate_ai_file``
        already writes to disk automatically, so this tool is for workflows
        that update the cache further before committing.

        Args:
            run_id:   Session identifier from ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON with ``status``, ``message``, ``filepath``, ``total_lines``.
        """
        cache_key = (run_id, filepath)
        if cache_key not in _AI_FILE_CACHE:
            return _err(
                f"No in-memory AI state for {filepath} — "
                "call python_annotate_ai_file first.",
                filepath=filepath,
            )
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        lines = _AI_FILE_CACHE[cache_key]
        abs_path.write_text("\n".join(lines) + "\n")
        del _AI_FILE_CACHE[cache_key]
        return _ok(
            f"Wrote {len(lines)} lines to {filepath}.",
            filepath=filepath,
            total_lines=len(lines),
        )

    @mcp.tool()
    def dftracer_get_ai_annotation(
        function_name: str = "",
        code_snippet: str = "",
        context: str = "",
        phase: str = "",
    ) -> str:
        """Look up the correct dftracer AI/ML annotation for a given function or code pattern.

        Uses the official AI/DL logging conventions from pydftracer to map
        function names, code patterns, and AI/ML phases to the correct
        dft_ai.* decorator or context manager.

        AI/DL Logging Conventions (six categories):

        COMPUTE:
          dft_ai.compute.forward    — forward pass (model(x), __call__, predict)
          dft_ai.compute.backward   — backward pass / gradient computation (loss.backward)
          dft_ai.compute.step       — optimizer step (optimizer.step, parameter update)
          dft_ai.compute            — generic compute / train_step / eval_step

        DATA:
          dft_ai.data.item          — per-item read (__getitem__, read_index, load_sample)
          dft_ai.data.preprocess    — dataset-level preprocessing, transform, augment, collate

        DATALOADER:
          dft_ai.dataloader.fetch   — batch fetching, next(iter), __next__, load_batch
          dft_ai.dataloader.fetch.iter(iterable)  — wraps a for-loop over batches

        COMM (distributed):
          dft_ai.comm.all_reduce    — all-reduce gradient synchronization
          dft_ai.comm.barrier       — synchronization barrier
          dft_ai.comm.broadcast     — one-to-many broadcast
          dft_ai.comm.reduce        — many-to-one reduce
          dft_ai.comm.all_gather    — all-gather
          dft_ai.comm.scatter       — scatter
          dft_ai.comm.send          — point-to-point send
          dft_ai.comm.receive       — point-to-point receive

        DEVICE:
          dft_ai.device.transfer    — host-to-device or device-to-host memory transfer
                                      (.cuda(), .to(device), .cpu(), torch.Tensor.to)

        CHECKPOINT:
          dft_ai.checkpoint.capture — model checkpoint save (save_checkpoint, torch.save)
          dft_ai.checkpoint.restart — model checkpoint load (load_checkpoint, torch.load)

        PIPELINE:
          dft_ai.pipeline.train     — training loop function
          dft_ai.pipeline.evaluate  — evaluation / validation loop
          dft_ai.pipeline.test      — test loop
          dft_ai                    — top-level run() / main() / __call__()
          dft_ai.pipeline.epoch.iter(range(epochs))  — wraps epoch for-loop

        GENERIC (for expensive non-AI regions via dft_fn):
          @_dlp.log_init            — __init__ methods
          with DFTracerFn(...)      — @staticmethod methods (contextual region)
          @_dlp.log                 — any other expensive function

        API STYLES:
          Decorator:        @dft_ai.compute.forward
          Context manager:  with dft_ai.compute.forward():
          Iterator wrapper: for batch in dft_ai.dataloader.fetch.iter(loader):
          Start/stop:       dft_ai.compute.step.start() / dft_ai.compute.step.stop()
          Metadata update:  ai.update(step=step, epoch=epoch)

        Args:
            function_name:  Name of the function to annotate (e.g. "forward", "train_epoch").
            code_snippet:   Short code snippet showing the function body or call pattern.
            context:        Free-text description of what this function does in the pipeline
                            (e.g. "reads a batch from disk", "runs optimizer step").
            phase:          Explicit phase hint: "compute", "data", "dataloader", "comm",
                            "device", "checkpoint", "pipeline", or "" for auto-detect.

        Returns:
            JSON with:
                decorator       — the recommended @dft_ai.* decorator string
                iterator_wrap   — recommended .iter() wrapper if the fn is a loop, or ""
                api_style       — "decorator" | "context_manager" | "iterator" | "start_stop"
                category        — category name (compute/data/dataloader/comm/device/checkpoint/pipeline)
                rationale       — why this annotation was chosen
                example         — short code example showing correct usage
                alternatives    — list of alternative annotations that could also apply
        """
        import re as _re

        name_lower = (function_name or "").lower()
        snippet_lower = (code_snippet or "").lower()
        context_lower = (context or "").lower()
        combined = f"{name_lower} {snippet_lower} {context_lower}"
        phase_lower = (phase or "").lower()

        # Build lookup table: (regex_for_combined, decorator, category, rationale, example, api_style)
        _LOOKUP = [
            # ── Checkpoint ──────────────────────────────────────────────────────
            (r'save_checkpoint|save_ckpt|write_ckpt|torch\.save|checkpoint.*save|ckpt.*write',
             "@dft_ai.checkpoint.capture", "checkpoint",
             "Function saves model state to disk (checkpoint capture).",
             "@dft_ai.checkpoint.capture\ndef save_checkpoint(model, path):\n    torch.save(model.state_dict(), path)",
             "decorator"),
            (r'load_checkpoint|load_ckpt|restore_ckpt|torch\.load|checkpoint.*load|ckpt.*read|ckpt.*restore',
             "@dft_ai.checkpoint.restart", "checkpoint",
             "Function loads model state from disk (checkpoint restart).",
             "@dft_ai.checkpoint.restart\ndef load_checkpoint(model, path):\n    model.load_state_dict(torch.load(path))",
             "decorator"),
            # ── Distributed communication ────────────────────────────────────────
            (r'all.?reduc|allreduc',
             "@dft_ai.comm.all_reduce", "comm",
             "All-reduce gradient synchronization across distributed workers.",
             "with dft_ai.comm.all_reduce():\n    dist.all_reduce(tensor)",
             "context_manager"),
            (r'\bbarrier\b',
             "@dft_ai.comm.barrier", "comm",
             "Synchronization barrier across distributed workers.",
             "with dft_ai.comm.barrier():\n    dist.barrier()",
             "context_manager"),
            (r'\ball.?gather\b',
             "@dft_ai.comm.all_gather", "comm",
             "All-gather communication (many-to-many).",
             "with dft_ai.comm.all_gather():\n    dist.all_gather(output_list, tensor)",
             "context_manager"),
            (r'\bscatter\b',
             "@dft_ai.comm.scatter", "comm",
             "Scatter communication (one-to-many).",
             "with dft_ai.comm.scatter():\n    dist.scatter(tensor, scatter_list)",
             "context_manager"),
            (r'\breduce.scatter\b|reduce_scatter',
             "@dft_ai.comm.reduce_scatter", "comm",
             "Reduce-scatter communication (many-to-many).",
             "with dft_ai.comm.reduce_scatter():\n    dist.reduce_scatter(output, input_list)",
             "context_manager"),
            (r'\bbroadcast\b',
             "@dft_ai.comm.broadcast", "comm",
             "Broadcast communication (one-to-many).",
             "with dft_ai.comm.broadcast():\n    dist.broadcast(tensor, src=0)",
             "context_manager"),
            (r'\breduce\b(?!.*scatter)',
             "@dft_ai.comm.reduce", "comm",
             "Reduce communication (many-to-one).",
             "with dft_ai.comm.reduce():\n    dist.reduce(tensor, dst=0)",
             "context_manager"),
            (r'all.?to.?all',
             "@dft_ai.comm.all_to_all", "comm",
             "All-to-all communication (many-to-many).",
             "with dft_ai.comm.all_to_all():\n    dist.all_to_all(output_list, input_list)",
             "context_manager"),
            (r'\bsend\b',
             "@dft_ai.comm.send", "comm",
             "Point-to-point send to another rank.",
             "with dft_ai.comm.send():\n    dist.send(tensor, dst=rank)",
             "context_manager"),
            (r'\breceive\b|\brecv\b',
             "@dft_ai.comm.receive", "comm",
             "Point-to-point receive from another rank.",
             "with dft_ai.comm.receive():\n    dist.recv(tensor, src=rank)",
             "context_manager"),
            # ── Device transfer ──────────────────────────────────────────────────
            (r'to.?device|\.cuda\(\)|\.to\s*\(\s*device|device.*transfer|host.to.device|\.cpu\(\)',
             "@dft_ai.device.transfer", "device",
             "Host-to-device or device-to-host memory transfer.",
             "@dft_ai.device.transfer\ndef transfer(batch, device):\n    return batch.to(device)",
             "decorator"),
            # ── Compute: optimizer step ──────────────────────────────────────────
            (r'optimizer.*step|optim.*step|param.*update|^step$',
             "@dft_ai.compute.step", "compute",
             "Optimizer parameter update step.",
             "dft_ai.compute.step.start()\noptimizer.step()\ndft_ai.compute.step.stop()",
             "start_stop"),
            # ── Compute: forward pass ────────────────────────────────────────────
            (r'^forward$|model\(|predict\b|__call__.*model|inference\b',
             "@dft_ai.compute.forward", "compute",
             "Forward pass through the model.",
             "@dft_ai.compute.forward\ndef forward(model, x):\n    return model(x)",
             "decorator"),
            # ── Compute: backward pass ───────────────────────────────────────────
            (r'backward|\.backward\(\)|grad.*comput|loss.*back',
             "@dft_ai.compute.backward", "compute",
             "Backward pass / gradient computation.",
             "@dft_ai.compute.backward\ndef backward(loss):\n    loss.backward()",
             "decorator"),
            # ── Compute: generic ─────────────────────────────────────────────────
            (r'train.?step|eval.?step|\bcompute\b|run.?step',
             "@dft_ai.compute", "compute",
             "Generic compute step (combined forward+backward or custom).",
             "@dft_ai.compute\ndef train_step(model, batch):\n    ...",
             "decorator"),
            # ── Data: per-item ────────────────────────────────────────────────────
            (r'__getitem__|read.?index|load.?sample|load.?item|get.?item|read.?file\b',
             "@dft_ai.data.item", "data",
             "Per-item data loading from storage (__getitem__ or equivalent).",
             "@dft_ai.data.item\ndef __getitem__(self, idx):\n    return np.load(self.files[idx])",
             "decorator"),
            # ── Data: preprocessing ───────────────────────────────────────────────
            (r'preprocess|transform|augment|collate|normalize|resize|tokenize',
             "@dft_ai.data.preprocess", "data",
             "Data preprocessing, transformation, or augmentation.",
             "@dft_ai.data.preprocess.derive(name='augment')\ndef augment(sample):\n    ...",
             "decorator"),
            # ── Dataloader: fetch ─────────────────────────────────────────────────
            (r'fetch.?batch|load.?batch|next\(iter|__next__|data.?loader.*iter|batch.*fetch',
             "@dft_ai.dataloader.fetch", "dataloader",
             "Fetch a batch from the dataloader.",
             "@dft_ai.dataloader.fetch\ndef fetch_batch(loader):\n    return next(iter(loader))",
             "decorator"),
            # ── Pipeline: epoch iteration ─────────────────────────────────────────
            (r'for.*epoch\b',
             "dft_ai.pipeline.epoch.iter(...)", "pipeline",
             "Epoch-level iteration — wrap the epoch for-loop iterator.",
             "for epoch in dft_ai.pipeline.epoch.iter(range(num_epochs)):\n    ...",
             "iterator"),
            # ── Pipeline: training loop ───────────────────────────────────────────
            (r'\btrain\b|\btraining\b|^fit$|^_fit$|train.?loop|run.?train',
             "@dft_ai.pipeline.train", "pipeline",
             "Training loop function (outer training routine).",
             "@dft_ai.pipeline.train\ndef train(model, loader, epochs):\n    ...",
             "decorator"),
            # ── Pipeline: evaluation/validation ──────────────────────────────────
            (r'\beval\b|\bevaluat|\bvalidat|val.?loop|run.?eval',
             "@dft_ai.pipeline.evaluate", "pipeline",
             "Evaluation or validation loop.",
             "@dft_ai.pipeline.evaluate\ndef evaluate(model, loader):\n    ...",
             "decorator"),
            # ── Pipeline: test ────────────────────────────────────────────────────
            (r'\btest\b|\btesting\b|run.?test|test.?loop',
             "@dft_ai.pipeline.test", "pipeline",
             "Test loop function.",
             "@dft_ai.pipeline.test\ndef test(model, test_loader):\n    ...",
             "decorator"),
            # ── Top-level runner ──────────────────────────────────────────────────
            (r'^run$|^main$|^__call__$|^run_training$',
             "@dft_ai", "pipeline",
             "Top-level entry point for the ML pipeline.",
             "@dft_ai\ndef run(config):\n    ...",
             "decorator"),
        ]

        # Apply phase filter first
        phase_category_map = {
            "compute": ["compute"],
            "data": ["data"],
            "dataloader": ["dataloader"],
            "comm": ["comm"],
            "device": ["device"],
            "checkpoint": ["checkpoint"],
            "pipeline": ["pipeline"],
        }
        phase_filter = phase_category_map.get(phase_lower, [])

        best_decorator = ""
        best_category = ""
        best_rationale = ""
        best_example = ""
        best_api_style = "decorator"
        alternatives = []

        for pattern, decorator, category, rationale, example, api_style in _LOOKUP:
            if phase_filter and category not in phase_filter:
                alternatives_check = False
            else:
                alternatives_check = True

            if _re.search(pattern, combined, _re.I):
                if not best_decorator and alternatives_check:
                    best_decorator = decorator
                    best_category = category
                    best_rationale = rationale
                    best_example = example
                    best_api_style = api_style
                elif decorator != best_decorator:
                    alternatives.append({"decorator": decorator, "category": category})

        # Default fallback
        if not best_decorator:
            if phase_lower == "compute":
                best_decorator = "@dft_ai.compute"
                best_category = "compute"
                best_rationale = "No specific compute pattern matched; use generic compute decorator."
                best_example = "@dft_ai.compute\ndef my_compute_fn(...):\n    ..."
                best_api_style = "decorator"
            elif phase_lower == "data":
                best_decorator = "@dft_ai.data.item"
                best_category = "data"
                best_rationale = "No specific data pattern matched; use data.item for individual reads."
                best_example = "@dft_ai.data.item\ndef read(self, idx):\n    ..."
                best_api_style = "decorator"
            else:
                best_decorator = "@_dlp.log"
                best_category = "generic"
                best_rationale = "No AI/ML region pattern matched; use generic dft_fn log decorator."
                best_example = "_dlp = DFTracerFn('mymodule')\n@_dlp.log\ndef my_fn(...):\n    ..."
                best_api_style = "decorator"

        iterator_wrap = ""
        if best_api_style == "iterator":
            iterator_wrap = best_decorator
            best_decorator = ""

        return _ok(
            f"Annotation for '{function_name or context}': {best_decorator or iterator_wrap}",
            decorator=best_decorator,
            iterator_wrap=iterator_wrap,
            api_style=best_api_style,
            category=best_category,
            rationale=best_rationale,
            example=best_example,
            alternatives=alternatives[:5],
        )

    @mcp.tool()
    def session_detect_ml_workload(
        run_id: str = "",
        source_folder: str = "",
    ) -> str:
        """Detect ML/AI workload characteristics from a source tree.

        Scans for:
        - AI/ML frameworks (PyTorch, TensorFlow, JAX, Keras, Horovod, DeepSpeed, etc.)
        - ROCm/HIP GPU stack (for AMD GPU profiling)
        - CUDA GPU stack
        - Distributed training patterns
        - Data pipeline patterns (DataLoader, HDF5, tfrecord, DALI)
        - HIP profiling requirement (ROCm detected or HIP source code patterns)

        Use this tool BEFORE session_install_dftracer to ensure dftracer is built
        with the correct HIP tracing support when an AMD/ROCm workload is detected.

        Args:
            run_id:        Session identifier (from session_create). When supplied,
                           reads or re-runs detection from the session workspace.
            source_folder: Absolute path to source folder. Used when run_id is empty.

        Returns:
            JSON with:
                frameworks          — list of detected ML frameworks
                rocm_found          — bool: ROCm stack detected on system
                hip_source          — bool: HIP code patterns in source
                hip_tracing_needed  — bool: dftracer should be built with HIP tracing
                rocm_info           — ROCm detection details (path, version, source)
                cuda_found          — bool: CUDA patterns detected
                distributed         — bool: distributed training patterns detected
                has_dataloader      — bool: DataLoader / data pipeline patterns
                annotation_strategy — recommended annotation approach
                install_flags       — dict of env vars to set for session_install_dftracer
                capabilities        — list of capability strings describing this workload
        """
        import re as _re
        from dftracer_agents.mcp_tools.tools.session.detection import _detect_info, _detect_rocm, _detect_ml_frameworks

        if run_id:
            ws = _ws(run_id)
            src = ws / "source"
            if not src.exists():
                src = ws / "annotated"
            if not src.exists():
                return _err(f"No source or annotated directory in workspace for run_id={run_id}")
        elif source_folder:
            src = Path(source_folder)
            if not src.exists():
                return _err(f"Source folder does not exist: {source_folder}")
        else:
            return _err("Provide either run_id or source_folder.")

        info = _detect_info(src)
        features = info.get("features", {})
        rocm_info = features.get("rocm", _detect_rocm())
        ml_frameworks = features.get("ml_frameworks", [])
        # Default False (not rocm_info["found"]): ROCm being installed on the node
        # is a system fact, not evidence the app uses HIP — see detection.py's
        # hip_tracing_needed derivation, which is source-based (features["hip"]).
        hip_tracing_needed = features.get("hip_tracing_needed", False)

        # Scan source text for CUDA and distributed patterns
        all_text = ""
        for ext in (".py", ".cpp", ".cu", ".cxx", ".cc", ".c", ".h"):
            for f in sorted(src.rglob(f"*{ext}"))[:200]:
                try:
                    all_text += f.read_text(errors="ignore")
                    if len(all_text) > 3_000_000:
                        break
                except OSError:
                    pass

        cuda_found = bool(_re.search(r"\.cuda\(\)|cuda\.is_available|torch\.cuda|cudaMalloc|#include\s*[<\"]cuda", all_text, _re.I))
        distributed = bool(_re.search(
            r"dist\.init_process_group|torch\.distributed|horovod|hvd\.init|MPI_Init|DeepSpeed|FSDP|DistributedDataParallel",
            all_text, _re.I
        ))
        has_dataloader = bool(_re.search(
            r"DataLoader|tf\.data|torch\.utils\.data|nvidia\.dali|h5py\.|hdf5|tfrecord|numpy\.load",
            all_text, _re.I
        ))

        # Build annotation strategy
        strategy_parts = []
        if ml_frameworks:
            strategy_parts.append(f"Detected frameworks: {', '.join(ml_frameworks)}")
        if hip_tracing_needed:
            strategy_parts.append("HIP tracing REQUIRED — build dftracer with DFTRACER_ENABLE_HIP_TRACING=ON")
        if distributed:
            strategy_parts.append("Distributed training detected — annotate comm.* regions")
        if has_dataloader:
            strategy_parts.append("Data pipeline detected — annotate data.item and dataloader.fetch regions")

        install_flags = {}
        if hip_tracing_needed:
            install_flags["DFTRACER_ENABLE_HIP_TRACING"] = "ON"
        if features.get("mpi"):
            install_flags["DFTRACER_ENABLE_MPI"] = "ON"
        if features.get("hdf5"):
            install_flags["DFTRACER_ENABLE_HDF5"] = "ON"

        capabilities = []
        for fw in ml_frameworks:
            capabilities.append(f"framework:{fw}")
        if hip_tracing_needed:
            capabilities.append("hip_tracing")
        if rocm_info.get("found"):
            capabilities.append(f"rocm:{rocm_info.get('version', 'unknown')}")
        if cuda_found:
            capabilities.append("cuda")
        if distributed:
            capabilities.append("distributed")
        if has_dataloader:
            capabilities.append("data_pipeline")

        annotation_strategy = " | ".join(strategy_parts) if strategy_parts else "Standard Python annotation with generic dft_fn logging"

        return _ok(
            f"ML workload detected: frameworks={ml_frameworks}, hip_tracing={hip_tracing_needed}",
            frameworks=ml_frameworks,
            rocm_found=rocm_info.get("found", False),
            hip_source=features.get("hip", False),
            hip_tracing_needed=hip_tracing_needed,
            rocm_info=rocm_info,
            cuda_found=cuda_found,
            distributed=distributed,
            has_dataloader=has_dataloader,
            annotation_strategy=annotation_strategy,
            install_flags=install_flags,
            capabilities=capabilities,
            build_tool=info.get("build_tool"),
            languages=info.get("languages", []),
        )

    @mcp.tool()
    def session_search_local_papers(
        query: str,
        bottleneck: str = "",
        framework: str = "",
        top_k: int = 4,
    ) -> str:
        """Search the local ML/AI optimization paper library for relevant citations.

        Searches the pre-indexed local paper collection at
        ``resources/papers/`` — a curated set of targeted ML/AI I/O
        optimization papers that complement online search results.

        Current library (4 papers):

        * **GLANCED-IO** (HPDC '26) — Cross-layer DL I/O optimization;
          parameter ordering (num_workers → prefetch → access_pattern →
          PFS_striping → transfer_size); OFAT greedy autotuner.
        * **Cladia** (IPDPS '26) — Cross-layer diagnosis of DL I/O
          bottlenecks; uncertainty-aware AI; SHAP-based root-cause attribution.
        * **LiveFlow** (SC '26) — Online data movement optimization for
          distributed DL; bounded decision windows; hierarchical coordination.
        * **HORATIO** (SSDBM '26) — Raw trace management at scale; 75x
          faster selective queries; 80-83x analysis speedup over Dask.

        Each result includes:

        * Full citation metadata (title, authors, venue, year)
        * Matching bottleneck categories and frameworks
        * Key concepts extracted from the paper
        * Concrete optimization rules derived from paper findings
        * Path to the local PDF for deeper reading

        Use this tool **before** generating optimization proposals so that
        every proposed optimization is backed by a local paper citation.

        Args:
            query:      Free-text query describing the bottleneck or optimization
                        goal (e.g. ``"prefetch depth data loading stall"``).
            bottleneck: Optional filter by bottleneck category — one of:
                        ``data_loading``, ``prefetch``, ``num_workers``,
                        ``transfer_size``, ``pfs_striping``,
                        ``dataset_access_pattern``, ``cross_layer``,
                        ``checkpoint``, ``distributed``, ``synchronization``,
                        ``critical_path``, ``python_overhead``,
                        ``framework_overhead``, ``trace_management``.
            framework:  Optional filter by ML framework (e.g. ``"pytorch"``).
            top_k:      Maximum number of papers to return (default: 4 = all).

        Returns:
            JSON with ``papers`` (list of match dicts), ``count``, ``query``,
            and ``papers_dir`` (absolute path to local PDF directory).
        """
        from dftracer_agents.mcp_tools.tools.session.workspace import _workspaces_root

        # Resolve papers directory relative to workspaces root's parent (project root)
        ws_root = _workspaces_root()
        project_root = ws_root.parent
        papers_dir = project_root / _LOCAL_PAPERS_SUBDIR
        if not papers_dir.exists():
            # Fall back: search sibling directories
            for candidate in [ws_root / _LOCAL_PAPERS_SUBDIR,
                               Path(__file__).resolve().parents[5] / _LOCAL_PAPERS_SUBDIR]:
                if candidate.exists():
                    papers_dir = candidate
                    break

        query_lower = query.lower()
        bottleneck_lower = bottleneck.lower()
        framework_lower = framework.lower()

        scored: List[Dict[str, Any]] = []
        for paper in _LOCAL_PAPER_INDEX:
            score = 0

            # Bottleneck filter
            if bottleneck_lower and bottleneck_lower not in paper["bottlenecks"]:
                # Allow partial match
                if not any(bottleneck_lower in b for b in paper["bottlenecks"]):
                    continue

            # Framework filter
            if framework_lower and framework_lower not in paper.get("frameworks", []):
                continue

            # Score by query term matches
            combined_text = (
                paper["title"].lower() + " " +
                " ".join(paper["bottlenecks"]) + " " +
                " ".join(paper.get("frameworks", [])) + " " +
                " ".join(paper["key_concepts"]).lower() + " " +
                " ".join(paper["optimization_rules"]).lower() + " " +
                paper["abstract_excerpt"].lower()
            )
            for term in query_lower.split():
                if len(term) > 2 and term in combined_text:
                    score += 1

            # Recency bonus (all 2026 papers get +2)
            score += max(0, paper["year"] - 2023)

            scored.append({**paper, "_score": score})

        # Sort by score descending
        scored.sort(key=lambda p: -p["_score"])
        results = scored[:top_k]

        # Build clean output (remove internal score key, add PDF path)
        output_papers = []
        for p in results:
            pdf_path = papers_dir / p["filename"]
            out = {k: v for k, v in p.items() if not k.startswith("_")}
            out["pdf_path"] = str(pdf_path) if pdf_path.exists() else f"(not found: {pdf_path})"
            out["relevance_score"] = p["_score"]
            output_papers.append(out)

        return _ok(
            f"Found {len(output_papers)} local paper(s) for query: '{query}'",
            papers=output_papers,
            count=len(output_papers),
            query=query,
            papers_dir=str(papers_dir),
        )

    @mcp.tool()
    def session_ml_append_lesson(
        app: str,
        context: str,
        error: str,
        root_cause: str,
        fix: str,
        phase: str = "",
        framework: Optional[List[str]] = None,
        annotation_rule: str = "",
        tags: Optional[List[str]] = None,
        run_id: str = "",
    ) -> str:
        """Append a new lesson to the shared annotation lessons file.

        Called automatically at the end of every ``dftracer-ml-annotate`` session
        to record pitfalls, framework-specific quirks, and new annotation rules.
        The lessons accumulate as institutional memory and are read at the start
        of every future session to prevent repeating mistakes.

        This is a **workspace-local staging copy** — it lives at a fixed path
        relative to the workspaces root::

            workspaces/.agents/skills/dftracer-annotation-lessons/LESSONS_LOG.md

        It is not the source-repo skill file itself. Use
        ``session_lessons_sync_preview`` / ``session_lessons_sync_pr`` to merge
        these staged entries back into the real
        ``dftracer-annotation-lessons/LESSONS_LOG.md`` in the source repo via a PR.

        Entries are appended after the anchor comment line in the file.  If the
        file does not yet exist it is created with the anchor in place.

        Duplicate detection: if an entry with an identical ``context`` string
        already exists in the file, the new entry is skipped and
        ``already_recorded=True`` is returned.

        Args:
            app:             Git URL or short name of the application.
            context:         One-line description of what was being attempted
                             when the issue occurred.
            error:           Verbatim error excerpt (compiler, runtime, or
                             import error message).
            root_cause:      Why the error happened — one concise paragraph.
            fix:             Exact steps, code, or rule that resolved the issue.
            phase:           Pipeline phase where the issue occurred — one of:
                             ``compute``, ``data``, ``dataloader``, ``comm``,
                             ``device``, ``checkpoint``, ``pipeline``,
                             ``install``, or ``run``.
            framework:       List of ML framework names affected
                             (e.g. ``["pytorch", "horovod"]``).
            annotation_rule: New or updated standing rule derived from this
                             lesson (e.g. ``"ML-R17: always call …"``), or
                             empty string if no new rule applies.
            tags:            Additional keyword tags for search.
            run_id:          Optional session identifier — used only to locate
                             the workspaces root when the tool is called from
                             inside a session context.

        Returns:
            JSON with ``status``, ``message``, ``lessons_file``,
            ``already_recorded``, ``entry_date``.
        """
        from dftracer_agents.mcp_tools.tools.session.workspace import _workspaces_root

        ws_root = _workspaces_root()
        lessons_path = ws_root / _LESSONS_REL
        lessons_path.parent.mkdir(parents=True, exist_ok=True)

        # Read or create the file
        if lessons_path.exists():
            text = lessons_path.read_text(errors="replace")
        else:
            text = (
                "# dftracer Annotation Lessons\n\n"
                f"{_LESSONS_ANCHOR}\n"
            )

        # Duplicate check on context
        if context.strip() and context.strip() in text:
            return _ok(
                f"Lesson already recorded (context matches): {context[:80]}",
                lessons_file=str(lessons_path),
                already_recorded=True,
                entry_date="",
            )

        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        fw_str = str(framework or [])
        tag_str = str(tags or ([phase] if phase else []))

        # Build YAML-like block
        entry_lines = [
            "",
            "---",
            f"date: {today}",
            f"app: {app}",
            f"framework: {fw_str}",
            f"context: {context}",
            f"phase: {phase}",
            "error: |",
        ]
        for line in error.splitlines():
            entry_lines.append(f"  {line}")
        entry_lines.append(f"root_cause: {root_cause}")
        entry_lines.append("fix: |")
        for line in fix.splitlines():
            entry_lines.append(f"  {line}")
        if annotation_rule:
            entry_lines.append(f"annotation_rule: {annotation_rule}")
        entry_lines.append(f"tags: {tag_str}")
        entry_lines.append("")

        entry_block = "\n".join(entry_lines)

        if _LESSONS_ANCHOR in text:
            new_text = text.replace(
                _LESSONS_ANCHOR,
                _LESSONS_ANCHOR + entry_block,
                1,
            )
        else:
            new_text = text + entry_block

        lessons_path.write_text(new_text)

        return _ok(
            f"Lesson appended to {lessons_path.name}: {context[:80]}",
            lessons_file=str(lessons_path),
            already_recorded=False,
            entry_date=today,
        )

    @mcp.tool()
    def dftracer_annotate_ai_check(path: str = "", source: str = "", entry: bool = False) -> dict:
        """Validator for dftracer_annotate_ai: rule deterministically on Python annotated with dft_ai regions.

        Give `path` (a .py file or tree written by python_annotate_ai_file / python_write_ai_file) or `source` (one
        file's text, written to a temp dir). Checks: the Python checks of dftracer_annotate_python_check (parses,
        coverage, no decorator on properties, init/finalize in an entry file or when `entry`); every file that uses
        dft_ai binds it by import (`from dftracer.python import ... ai as dft_ai`); every @dft_ai... decorator is one
        the AI annotator knows (_AI_REGION_PATTERNS plus the regions dftracer-ml-annotate documents: checkpoint, data, data.io, dataloader, device, comm.*); no function
        carries two dft_ai decorators.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], files}.
        """
        known = {d.lstrip("@") for _p, d, _l in _AI_REGION_PATTERNS} | set(_AI_KNOWN_REGIONS)
        with _vtempfile.TemporaryDirectory() as tmp:
            files, err = _vfiles(path, source, "python", tmp)
            if not files:
                return _verdict([_vchk("input", False, err, "pass a .py file or tree, or its source text")])
            checks = _vpy_checks(files, need_entry=entry)
            for f in files:
                try:
                    tree = _vast.parse(f.read_text(errors="replace"))
                except SyntaxError:
                    continue
                uses = any(isinstance(n, _vast.Name) and n.id == "dft_ai" for n in _vast.walk(tree))
                if not uses:
                    continue
                bound = any(isinstance(n, (_vast.Import, _vast.ImportFrom)) and any((a.asname or a.name) == "dft_ai" for a in n.names)
                            for n in _vast.walk(tree))
                checks.append(_vchk(f"{f.name}: dft_ai imported", bound, "",
                                    "" if bound else "add `from dftracer.python import dftracer, dft_fn as DFTracerFn, ai as dft_ai`"))
                unknown, double = [], []
                for fn in _vast.walk(tree):
                    if isinstance(fn, (_vast.FunctionDef, _vast.AsyncFunctionDef)):
                        ds = [_vast.unparse(d).split("(")[0] for d in fn.decorator_list]
                        ai = [d for d in ds if d == "dft_ai" or d.startswith("dft_ai.")]
                        unknown += [f"{fn.name}:@{d}" for d in ai if d not in known]
                        if len(ai) > 1:
                            double.append(f"{fn.name}:{fn.lineno}")
                checks.append(_vchk(f"{f.name}: dft_ai decorators known", not unknown, ", ".join(unknown[:5]),
                                    "" if not unknown else "use a region from dftracer_get_ai_annotation / the AI annotator's table"))
                checks.append(_vchk(f"{f.name}: one dft_ai decorator per function", not double, ", ".join(double[:5]),
                                    "" if not double else "keep the most specific dft_ai region (python_dedup_annotations)"))
            return _verdict(checks, files=[f.name for f in files])
