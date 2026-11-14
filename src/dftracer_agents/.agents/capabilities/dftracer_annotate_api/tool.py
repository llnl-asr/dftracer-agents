"""dftracer_annotate_api: the dftracer annotation API reference (init/fini, function annotations, metadata, function-update API per language) and the validator dftracer_annotate_api_check that rules on a snippet's macro use. Tools moved unchanged from register_annotation_api_tools in dftracer_agents.mcp_tools.tools.dftracer.dftracer_service; its closure constants _DOCS_BASE and _PYDOCS_BASE are hoisted to module level."""

from __future__ import annotations

from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
)

# Hoisted from the registrar's closure (constants, unchanged).
_DOCS_BASE = "https://dftracer.readthedocs.io/en/latest"
_PYDOCS_BASE = "https://dftracer.readthedocs.io/projects/pydftracer/en/latest"

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


# The macro names dftracer_get_* return per language (and the C/C++ macro stub clang_syntax_check uses).
_API_MACROS = {
    "c": {"DFTRACER_C_INIT": 3, "DFTRACER_C_FINI": 0, "DFTRACER_C_FUNCTION_START": 0, "DFTRACER_C_FUNCTION_END": 0,
          "DFTRACER_C_FUNCTION_UPDATE_STR": 2, "DFTRACER_C_FUNCTION_UPDATE_INT": 2, "DFTRACER_C_REGION_START": 1,
          "DFTRACER_C_REGION_END": 1, "DFTRACER_C_METADATA": 3},
    "cpp": {"DFTRACER_CPP_INIT": 3, "DFTRACER_CPP_FINI": 0, "DFTRACER_CPP_FUNCTION": 0, "DFTRACER_CPP_FUNCTION_UPDATE": 2,
            "DFTRACER_CPP_REGION_START": 1, "DFTRACER_CPP_REGION_END": 1, "DFTRACER_CPP_REGION_UPDATE": 3,
            "DFTRACER_CPP_REGION_DYN_UPDATE": 3, "DFTRACER_CPP_METADATA": 3},
}


def register(mcp):
    """Register the dftracer_annotate_api tools on *mcp*."""

    @mcp.tool()
    def dftracer_get_init_fini(language: str) -> str:
        """Return the correct dftracer init/fini macros and API doc URL for a language.

        Provides the exact initialization and finalization macro signatures so the
        annotation agent inserts the right calls in ``main()`` (or the equivalent
        module-level setup) without guessing.

        Args:
            language: ``"c"``, ``"cpp"``, or ``"python"`` (case-insensitive).

        Returns:
            JSON string with keys:
                * ``status``   — ``"ok"`` or ``"error"``.
                * ``language`` — normalized language name.
                * ``init``     — init macro / call signature.
                * ``fini``     — fini macro / call signature.
                * ``notes``    — placement guidance.
                * ``doc_url``  — URL of the relevant API doc page.
        """
        lang = language.strip().lower()
        if lang == "c":
            return _ok(
                "C init/fini macros",
                language="c",
                init="DFTRACER_C_INIT(log_file, data_dirs, process_id)",
                fini="DFTRACER_C_FINI()",
                notes=(
                    "Insert DFTRACER_C_INIT as the FIRST statement in main() "
                    "(or after MPI_Init if MPI is used). "
                    "IMPORTANT: DFTRACER_C_INIT must come BEFORE DFTRACER_C_FUNCTION_START "
                    "when both are present in the same function. "
                    "Insert DFTRACER_C_FINI before MPI_Finalize (if present), "
                    "and DFTRACER_C_FUNCTION_END before every return/exit in main(). "
                    "Pass NULL for unused args: DFTRACER_C_INIT(NULL, NULL, NULL)."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang in ("cpp", "c++"):
            return _ok(
                "C++ init/fini macros",
                language="cpp",
                init="DFTRACER_CPP_INIT(log_file, data_dirs, process_id)",
                fini="DFTRACER_CPP_FINI()",
                notes=(
                    "Insert DFTRACER_CPP_INIT as the FIRST statement in main() "
                    "(or after MPI_Init). "
                    "IMPORTANT: DFTRACER_CPP_INIT must come BEFORE DFTRACER_CPP_FUNCTION() "
                    "when both are present in the same function. "
                    "Insert DFTRACER_CPP_FINI before MPI_Finalize / before the final return. "
                    "Use DFTRACER_CPP_REGION_START/END to bracket main() body "
                    "instead of DFTRACER_CPP_FUNCTION(). "
                    "Pass nullptr for unused args: DFTRACER_CPP_INIT(nullptr, nullptr, nullptr)."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang == "python":
            return _ok(
                "Python init/fini API",
                language="python",
                init=(
                    "from dftracer.python import dftracer, dft_fn as DFTracerFn\n"
                    "_dft = DFTracerFn(\"<category>\")\n"
                    "_dft_log = dftracer.initialize_log("
                    "logfile=None, data_dir=None, process_id=None)"
                ),
                fini="_dft_log.finalize()",
                notes=(
                    "dft_fn is a CLASS, not a function — instantiate it with a category string: "
                    "_dft = DFTracerFn(\"mymodule\"). "
                    "Call dftracer.initialize_log() at module top-level (entry-point files only). "
                    "Note: data_dir (singular), not data_dirs. "
                    "Call _dft_log.finalize() before every return in main(), not at module level. "
                    "Use @_dft.log / @_dft.log_init / @_dft.log_static decorators — "
                    "NOT @dft_fn.log. See python_annotate_file tool for automated insertion."
                ),
                doc_url=f"{_PYDOCS_BASE}/api.html",
            )
        return _err(
            f"Unknown language '{language}'. Supported: 'c', 'cpp', 'python'.",
            supported=["c", "cpp", "python"],
        )

    @mcp.tool()
    def dftracer_get_function_annotations(language: str) -> str:
        """Return the correct function-level annotation macros for a language.

        Provides the exact macro (or decorator) to annotate individual functions
        so that dftracer records per-function timing spans.

        Args:
            language: ``"c"``, ``"cpp"``, or ``"python"`` (case-insensitive).

        Returns:
            JSON string with keys:
                * ``status``      — ``"ok"`` or ``"error"``.
                * ``language``    — normalized language name.
                * ``start``       — the opening macro / decorator.
                * ``end``         — the closing macro, or ``null`` if RAII/decorator handles it.
                * ``placement``   — where in the function to place the macros.
                * ``doc_url``     — URL of the relevant API doc page.
        """
        lang = language.strip().lower()
        if lang == "c":
            return _ok(
                "C function annotation macros",
                language="c",
                start="DFTRACER_C_FUNCTION_START()",
                end="DFTRACER_C_FUNCTION_END()",
                placement=(
                    "START: first statement inside the opening brace of every function. "
                    "EXCEPTION: when DFTRACER_C_INIT is also present (in main()), "
                    "DFTRACER_C_INIT must come BEFORE DFTRACER_C_FUNCTION_START. "
                    "END: immediately BEFORE every return / exit() / abort() call. "
                    "For void functions with no explicit return: last statement before '}'."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang in ("cpp", "c++"):
            return _ok(
                "C++ function annotation macros (RAII)",
                language="cpp",
                start="DFTRACER_CPP_FUNCTION()",
                end=None,
                placement=(
                    "Place DFTRACER_CPP_FUNCTION() as the FIRST statement inside '{'."
                    " EXCEPTION: when DFTRACER_CPP_INIT is also present (in main()), "
                    "DFTRACER_CPP_INIT must come BEFORE DFTRACER_CPP_FUNCTION()."
                    " RAII destructor fires automatically on scope exit — NO manual END needed."
                    " For main(): use DFTRACER_CPP_REGION_START(name) / DFTRACER_CPP_REGION_END(name)"
                    " instead of DFTRACER_CPP_FUNCTION()."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang == "python":
            return _ok(
                "Python function annotation decorators",
                language="python",
                start="@_dft.log",
                end=None,
                variants={
                    "regular": "@_dft.log",
                    "__init__": "@_dft.log_init",
                    "staticmethod": "@_dft.log_static",
                },
                placement=(
                    "Apply the decorator immediately above every def (or above the first "
                    "existing decorator on that function). "
                    "_dft must be a DFTracerFn instance: _dft = DFTracerFn(\"category\"). "
                    "Use @_dft.log_init for __init__, @_dft.log_static for @staticmethod methods, "
                    "@_dft.log for everything else. "
                    "For @staticmethod, the dftracer decorator goes ABOVE the @staticmethod line. "
                    "No explicit END — the decorator wraps the whole function body."
                ),
                doc_url=f"{_PYDOCS_BASE}/api.html",
            )
        return _err(
            f"Unknown language '{language}'. Supported: 'c', 'cpp', 'python'.",
            supported=["c", "cpp", "python"],
        )

    @mcp.tool()
    def dftracer_get_metadata_api(language: str) -> str:
        """Return the dftracer per-process metadata macro for a language.

        Metadata macros attach key-value pairs to the current process's trace
        records so that traces can be filtered and correlated by application context.

        Args:
            language: ``"c"``, ``"cpp"``, or ``"python"`` (case-insensitive).

        Returns:
            JSON string with keys:
                * ``status``   — ``"ok"`` or ``"error"``.
                * ``language`` — normalized language name.
                * ``macro``    — the metadata macro / call signature.
                * ``notes``    — usage guidance.
                * ``doc_url``  — URL of the relevant API doc page.
        """
        lang = language.strip().lower()
        if lang == "c":
            return _ok(
                "C metadata macro",
                language="c",
                macro='DFTRACER_C_METADATA(name, "key", "value")',
                notes=(
                    "3-arg macro: name is a bare, unique C identifier (NOT a "
                    "string literal) used internally for ##name token-pasting; "
                    "key/value are string literals. "
                    "Attach a string key-value pair to the current process trace. "
                    "Call after DFTRACER_C_INIT(). "
                    "Example: DFTRACER_C_METADATA(dft_meta_app, \"app\", \"IOR\");"
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang in ("cpp", "c++"):
            return _ok(
                "C++ metadata macro",
                language="cpp",
                macro='DFTRACER_CPP_METADATA(name, "key", "value")',
                notes=(
                    "3-arg macro: name is a bare, unique C++ identifier (NOT a "
                    "string literal) used internally for ##name token-pasting; "
                    "key/value are string literals. "
                    "Attach a string key-value pair to the current process trace. "
                    "Call after DFTRACER_CPP_INIT(). "
                    "Example: DFTRACER_CPP_METADATA(dft_meta_app, \"app\", \"IOR\");"
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang == "python":
            return _ok(
                "Python metadata API",
                language="python",
                macro='_dft_log.log_metadata_event("key", "value")',
                notes=(
                    "Python DOES expose a metadata API: call log_metadata_event(key, value) "
                    "on the object returned by dftracer.initialize_log(...) "
                    "(dftracer/python/common.py::dftracer.log_metadata_event). "
                    "Call it AFTER initialize_log() so a logger exists. "
                    "Use annotate_add_app_metadata to emit all app parameters at once."
                ),
                doc_url=f"{_PYDOCS_BASE}/api.html",
            )
        return _err(
            f"Unknown language '{language}'. Supported: 'c', 'cpp', 'python'.",
            supported=["c", "cpp", "python"],
        )

    @mcp.tool()
    def dftracer_get_function_update_api(language: str) -> str:
        """Return the dftracer per-function metadata update macros for a language.

        Function update macros attach key-value pairs to an *individual function
        span* so that each traced call carries its own context (e.g. ``comp=io``,
        file path, byte count).

        Args:
            language: ``"c"``, ``"cpp"``, or ``"python"`` (case-insensitive).

        Returns:
            JSON string with keys:
                * ``status``     — ``"ok"`` or ``"error"``.
                * ``language``   — normalized language name.
                * ``str_update`` — macro for string values.
                * ``int_update`` — macro for integer values, or ``null`` if unavailable.
                * ``notes``      — usage and placement guidance.
                * ``doc_url``    — URL of the relevant API doc page.
        """
        lang = language.strip().lower()
        if lang == "c":
            return _ok(
                "C function update macros",
                language="c",
                str_update='DFTRACER_C_FUNCTION_UPDATE_STR("key", char_ptr_value)',
                int_update='DFTRACER_C_FUNCTION_UPDATE_INT("key", (int)value)',
                notes=(
                    "Place immediately after DFTRACER_C_FUNCTION_START(). "
                    "comp= is mandatory first UPDATE: "
                    "DFTRACER_C_FUNCTION_UPDATE_STR(\"comp\", \"io\"). "
                    "String params (paths, names): use UPDATE_STR. "
                    "Numeric params (counts, sizes, handles): use UPDATE_INT with (int) cast. "
                    "Opaque handles (MPI_File, hid_t): UPDATE_INT(\"handle\", (int)fh)."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang in ("cpp", "c++"):
            return _ok(
                "C++ function update macro",
                language="cpp",
                str_update='DFTRACER_CPP_FUNCTION_UPDATE("key", string_value)',
                int_update=None,
                notes=(
                    "Place immediately after DFTRACER_CPP_FUNCTION(). "
                    "comp= is mandatory first UPDATE: "
                    "DFTRACER_CPP_FUNCTION_UPDATE(\"comp\", \"io\"). "
                    "C++ UPDATE accepts string values only (const char *). "
                    "For numeric params: convert to string or omit — "
                    "there is NO UPDATE_INT variant in the C++ API."
                ),
                doc_url=f"{_DOCS_BASE}/c-api.html",
            )
        if lang == "python":
            return _ok(
                "Python function update API",
                language="python",
                str_update=None,
                int_update=None,
                notes=(
                    "The Python dftracer bindings do not expose per-call UPDATE macros. "
                    "Pass context via function arguments; the @dft_fn.log decorator "
                    "records function name and timing automatically."
                ),
                doc_url=f"{_PYDOCS_BASE}/api.html",
            )
        return _err(
            f"Unknown language '{language}'. Supported: 'c', 'cpp', 'python'.",
            supported=["c", "cpp", "python"],
        )

    @mcp.tool()
    def dftracer_annotate_api_check(language: str, code: str) -> dict:
        """Validator for dftracer_annotate_api: rule deterministically on a snippet's use of the dftracer annotation API.

        Give `language` (c, cpp or python) and `code` (a snippet or a whole file). C/C++: every DFTRACER_* macro is
        one the API reference (dftracer_get_*) names for that language, with its number of arguments (INIT 3,
        UPDATE 2, METADATA 3, ...); C and C++ macros are never mixed; UPDATE_STR/UPDATE keys are string literals;
        INIT's third argument is a pointer (NULL/nullptr/&pid), never a non-zero integer literal such as -1. Python: an
        initialize_log call is dftracer.initialize_log(...) assigned to a handle, finalize() is called on that
        handle, and metadata uses log_metadata_event.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}.
        """
        lang = (language or "").strip().lower()
        lang = "cpp" if lang in ("c++", "cxx") else lang
        if lang not in ("c", "cpp", "python"):
            return _verdict([_vchk("language", False, f"unsupported language {language!r}", "use c, cpp or python")])
        checks = []
        if lang == "python":
            init = _vre.search(r"(\w+)\s*=\s*dftracer\.initialize_log\s*\(", code)
            bare = _vre.search(r"(?<![\w.])initialize_log\s*\(", code) and not init
            checks.append(_vchk("initialize_log assigned from dftracer.initialize_log", not bare,
                                "initialize_log called without the dftracer handle" if bare else "",
                                "" if not bare else "_dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=-1)"))
            if init:
                h = init.group(1)
                fin = _vre.search(rf"\b{_vre.escape(h)}\.finalize\s*\(\s*\)", code)
                checks.append(_vchk("finalize() on the initialize_log handle", bool(fin), "",
                                    "" if fin else f"call {h}.finalize() before the entry point exits"))
            meta_bad = _vre.search(r"\.(log_metadata|add_metadata|metadata)\s*\(", code)
            checks.append(_vchk("metadata via log_metadata_event", not meta_bad, meta_bad.group(0) if meta_bad else "",
                                "" if not meta_bad else "use <handle>.log_metadata_event(key, value)"))
            return _verdict(checks)
        other = "cpp" if lang == "c" else "c"
        known = _API_MACROS[lang]
        used = [(m.group(1), m.start()) for m in _vre.finditer(r"\b(DFTRACER_[A-Z_]+)\s*\(", code)]
        unknown = sorted({n for n, _ in used if n not in known and n not in _API_MACROS[other]})
        mixed = sorted({n for n, _ in used if n in _API_MACROS[other]})
        checks.append(_vchk(f"only {lang} API macros", not unknown, ", ".join(unknown),
                            "" if not unknown else f"use a name from dftracer_get_function_annotations('{lang}')"))
        checks.append(_vchk("C and C++ macros not mixed", not mixed, ", ".join(mixed),
                            "" if not mixed else f"use the {lang} form of {', '.join(mixed)}"))
        argbad = []
        for name, pos in used:
            if name not in known:
                continue
            m = _vre.match(r"\b\w+\s*\(((?:[^()]|\([^()]*\))*)\)", code[pos:])
            if not m:
                continue
            args = [a for a in _vre.split(r",(?![^()]*\))", m.group(1)) if a.strip()]
            if len(args) != known[name]:
                argbad.append(f"{name}: {len(args)} args, expects {known[name]}")
            if name.endswith(("UPDATE_STR", "UPDATE_INT", "FUNCTION_UPDATE")) and args and not args[0].strip().startswith('"'):
                argbad.append(f"{name}: key {args[0].strip()} is not a string literal")
            if name.endswith("_INIT") and len(args) == 3 and _vre.fullmatch(r"\s*(?:-\s*\d+|[1-9]\d*)\s*", args[2]):
                argbad.append(f"{name}: third argument {args[2].strip()} is a literal, must be int* (NULL or &pid)")
        checks.append(_vchk("macro arguments", not argbad, "; ".join(argbad[:5]),
                            "" if not argbad else "match the signature from dftracer_get_* (see Corner cases)"))
        if not used:
            checks.append(_vchk("snippet uses the API", None, "no DFTRACER_* macro found", "pass the annotated code"))
        return _verdict(checks)
