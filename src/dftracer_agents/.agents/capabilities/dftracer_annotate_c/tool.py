"""dftracer_annotate_c: annotate one C file with dftracer C macros (session_annotate_c_file) and validate it deterministically (dftracer_annotate_c_check: compiles against a macro stub, INIT/FINI in main, every START has END and a comp UPDATE, critical I/O calls covered). Tool moved unchanged from dftracer_agents.mcp_tools.tools.annotations.annotate_c."""

from __future__ import annotations

import re
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
    _ws,
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
    """Register the dftracer_annotate_c tools on *mcp*."""

    @mcp.tool()
    def session_annotate_c_file(
        run_id: str,
        filepath: str,
        build_errors: str = "",
        user_notes: str = "",
    ) -> str:
        """Annotate a single .c source file with dftracer C macros.

        This tool is the per-file C annotation agent.  It is designed to be
        called in parallel with other per-file annotation calls — each invocation
        is independent and operates on a single file inside the session workspace.

        Procedure (mirrors the annotate-c.yaml recipe):

        **Step 0 — Load lessons**
            Read /workspaces/dftracer-agents/.agents/skills/dftracer-annotation-lessons/SKILL.md
            and apply all applicable lessons to this file before writing anything.

        **Step 1 — Read the file**
            Read the current state of the file from annotated/<filepath>.
            Record the original line count — the final written file MUST have MORE lines.
            Pre-scan for braceless early-exit lines before writing any macros::

                grep -n "if.*return|if.*continue|if.*break" <file> | grep -v "{" | grep -v "//"

            For every braceless hit: add explicit braces FIRST, then annotate.

        **Step 2 — Prepare and extract function map**
            2a. Add braces via ``clang_add_braces``:
                Ensures every if/for/while/do body has explicit {}.  Without
                braces, inserting DFTRACER_C_FUNCTION_END() creates dangling-else
                or orphaned macros that break compilation.

            2b. Extract function map via ``clang_extract_functions``:
                Returns JSON list of all function definitions with exact line numbers:
                  • name            — function name
                  • start_line      — first line of the signature
                  • open_brace_line — line of the opening '{'
                  • body_first_line — FIRST line inside the body (insert START here)
                  • close_brace_line— line of the closing '}'
                  • exit_lines      — list of {line, type} for every return/exit()/abort()
                  • is_entry_point  — true for main()

            comp= C-specific guidance:
              • Xfer to/from a file (POSIX/MMAP/HDF5/NCMPI)     → "io"
              • Xfer via network/MPI (MPIIO/S3/HDFS/RADOS/DFS)   → "comm"
              • Xfer that is a memcpy into mmap region             → "mem"
              • Backend lifecycle (init/final/initialize/finalize) → "io"

        **Step 3 — Review exit paths from FUNC_MAP**
            For each function in FUNC_MAP, verify exit_lines against the source:
              • type="return"       → insert DFTRACER_C_FUNCTION_END() on the line
                                       BEFORE that return, matching indentation
              • type="exit"/"abort" → insert END + DFTRACER_C_FINI() before the call
                                       (main only)
              • exit_lines empty    → void function → insert END as last statement
                                       before close_brace_line

            MPI_CHECK / NCMPI_CHECK / HGOTO_ERROR / ERR_GOTO hide an internal
            return/goto — do NOT add END before them; only before visible returns.
            goto-style exits (labels done:/err:/out:) → single END at the label,
            NOT before each individual goto statement.

        **Step 4 — Apply annotation (per-function incremental loop)**
            Process FUNC_MAP ONE FUNCTION AT A TIME using the per-function loop:

            For each function in FUNC_MAP (libraries before entry-points):
              4a. Using EXACT LINE NUMBERS from FUNC_MAP:
                    • Insert DFTRACER_C_FUNCTION_START() at body_first_line
                    • Insert DFTRACER_C_FUNCTION_UPDATE_STR("comp", "<type>") next line
                    • For each {line, type} in exit_lines: insert END on the line
                      IMMEDIATELY BEFORE that exit line, matching indentation
                    • If exit_lines empty: insert END as last statement before close_brace_line

                  For main() additionally:
                    • Insert DFTRACER_C_INIT(NULL, NULL, NULL) as VERY FIRST statement
                      (before START), or after MPI_Init(&argc, &argv) if MPI is used
                    • Insert DFTRACER_C_FINI() before MPI_Finalize() or before final return

              4b. Write the updated file via ``session_write_file``

              4c. Syntax check after EVERY function write::

                    WS=/workspaces/dftracer-agents/workspaces/<run_id>
                    gcc -include /tmp/dftracer_stub.h -fsyntax-only -w \\
                      -x c ${WS}/annotated/<filepath> 2>&1

                  Create the stub header first (once per file)::

                    cat > /tmp/dftracer_stub.h << 'STUB'
                    #define DFTRACER_C_FUNCTION_START()
                    #define DFTRACER_C_FUNCTION_END()
                    #define DFTRACER_C_FUNCTION_UPDATE_STR(k,v)
                    #define DFTRACER_C_FUNCTION_UPDATE_INT(k,v)
                    #define DFTRACER_C_INIT(a,b,c)
                    #define DFTRACER_C_FINI()
                    STUB

              4d. On syntax error: fix ONLY the failing function (max 2 retries),
                  then strip macros from that function only and mark PENDING.

        **Step 5 — Coverage verification**::

            WS=/workspaces/dftracer-agents/workspaces/<run_id>
            F=${WS}/annotated/<filepath>
            # 1. START vs comp count (must be equal)
            echo "START: $(grep -c 'DFTRACER_C_FUNCTION_START' $F)"
            echo "comp=: $(grep -c 'DFTRACER_C_FUNCTION_UPDATE_STR.*\\"comp\\"' $F)"
            # 2. No END at column 0
            grep -n "^DFTRACER_C_FUNCTION_END" $F

        **Step 6 — Output per-file report** in this structure::

            FILE: <filepath>
            STATUS: DONE | PARTIAL | FAILED
            ANNOTATED: <N> functions  (io=<n>, comm=<n>, mem=<n>, cpu=<n>)
            SKIPPED:   <function names with Rule 0 justification, one per line>
            PENDING:   <functions not yet annotated with reason>
            NEW_ERRORS: <any new compile/runtime errors discovered>
            LESSONS:   <new patterns or pitfalls not yet in the lessons file>

        **Absolute placement rules (never violate):**
            • START is the FIRST statement inside { — nothing before it (except INIT in main)
            • START goes AFTER {, never before it
            • START never inside if/for/while/switch blocks
            • comp= UPDATE is the FIRST UPDATE immediately after START
            • END indentation matches the return line it precedes; never at column 0
            • Never place END after a return (dead code)
            • Never annotate a .h header file
            • Never annotate a forward declaration (line ending with ";")

        **ALWAYS ANNOTATE (mandatory — never apply Rule 0 skip):**
            • Lifecycle:   *_init, *_final, *_initialize, *_finalize
            • Sync/flush:  *_fsync, *_flush, *_sync
            • File ops:    *_delete, *_rename, *_stat, *_mknod, *_getfilesize
            • Vendor FS:   gpfs_*, beegfs_*, lustre_*, hdfs_*, ceph_*, daos_*

        **SKIP (Rule 0) ONLY if ALL of these are true:**
            • Pure getter/setter ≤ 5 lines, returns a single field
            • No I/O, no data movement, no syscall

        **BUILD ERROR MODE:**
            If build_errors is non-empty, focus on fixing the functions named in
            those errors FIRST using the per-function loop, then continue with any
            unannotated functions.

        **C-specific pitfalls:**
            PC1  END after return         → dead code; swap: END must precede the return
            PC2  END at column 0          → match indentation of the return it precedes
            PC3  START before {           → syntax error; move START to first line inside {}
            PC4  Error macro hides exit   → MPI_CHECK/H5EPRINT expands to hidden return;
                                            no END before them; only before visible returns
            PC5  goto: END before each    → duplicate ENDs; put single END at label instead
            PC6  Fwd decl annotated       → annotated a ";" line; annotate definition instead
            PC7  DFTRACER_INIT value      → correct values: FUNCTION (recommended), PRELOAD,
                                            HYBRID — never use 0 for POSIX tracing

        Args:
            run_id:       Session identifier (e.g. ``myapp/20260616_120000``).
            filepath:     Path of the ``.c`` file relative to ``annotated/`` subfolder.
            build_errors: Compiler stderr from a failed build (BUILD ERROR MODE).
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

        if target.suffix.lower() not in {".c"}:
            return _err(f"session_annotate_c_file only handles .c files, got: {filepath}")

        # Read file to check it exists and get line count
        try:
            original_content = target.read_text(errors="ignore")
        except OSError as e:
            return _err(f"Cannot read {filepath}: {e}")

        original_lines = len(original_content.splitlines())

        # Check if already fully annotated
        if "DFTRACER_C_FUNCTION_START" in original_content and not build_errors:
            annotated_count = original_content.count("DFTRACER_C_FUNCTION_START")
            return _ok(
                f"File already annotated with {annotated_count} function(s). "
                "Pass build_errors to enter fix mode.",
                filepath=filepath,
                status="already_annotated",
                annotated=annotated_count,
                skipped=[],
                pending=[],
                lessons=[],
            )

        # Provide context for the annotation agent
        context = {
            "run_id": run_id,
            "filepath": filepath,
            "workspace": str(ws),
            "annotated_dir": str(ann_dir),
            "original_line_count": original_lines,
            "has_build_errors": bool(build_errors),
            "build_errors_excerpt": build_errors[:500] if build_errors else "",
            "user_notes": user_notes,
            "file_size_bytes": target.stat().st_size,
        }

        # Quick pre-scan for braceless early exits
        braceless_hits = []
        for i, line in enumerate(original_content.splitlines(), start=1):
            stripped = line.strip()
            if (
                re.match(r'if\s*\(', stripped)
                and ("return" in stripped or "continue" in stripped or "break" in stripped)
                and "{" not in stripped
                and not stripped.startswith("//")
                and not stripped.startswith("*")
            ):
                braceless_hits.append(f"line {i}: {stripped[:80]}")

        # Check for forward declarations that must NOT be annotated
        fwd_decls = []
        for i, line in enumerate(original_content.splitlines(), start=1):
            stripped = line.strip()
            if (
                stripped.endswith(";")
                and "(" in stripped
                and not stripped.startswith("#")
                and not stripped.startswith("//")
                and not stripped.startswith("*")
                and re.match(r'^[a-zA-Z_]', stripped)
            ):
                fwd_decls.append(f"line {i}: {stripped[:80]}")

        return _ok(
            f"C file ready for annotation — {original_lines} lines. "
            + (f"BUILD ERROR MODE: {len(build_errors.splitlines())} error line(s). " if build_errors else "")
            + (f"Pre-scan: {len(braceless_hits)} braceless early-exit(s) to brace first. " if braceless_hits else "")
            + "Use clang_add_braces, clang_extract_functions, then session_write_file "
            "per function with gcc syntax check after each. "
            "See tool docstring for complete procedure.",
            context=context,
            braceless_early_exits=braceless_hits[:20],
            forward_declarations_count=len(fwd_decls),
            annotation_procedure={
                "step_0": "Read lessons from dftracer-annotation-lessons/SKILL.md",
                "step_1": f"Read annotated/{filepath} — record line count ({original_lines})",
                "step_2a": f"clang_add_braces(run_id='{run_id}', filepath='{filepath}')",
                "step_2b": f"clang_extract_functions(run_id='{run_id}', filepath='{filepath}')",
                "step_3": "Review exit_lines from FUNC_MAP for each function",
                "step_4": "Per-function loop: insert macros, write, gcc syntax check",
                "step_5": "Coverage verification: START count == comp count",
                "step_6": "Output per-file report with STATUS/ANNOTATED/SKIPPED/PENDING",
            },
            build_error_mode=bool(build_errors),
        )

    @mcp.tool()
    def dftracer_annotate_c_check(path: str = "", source: str = "", compiler: str = "",
                                  extra_include_dirs: List[str] = None,
            extra_flags: List[str] = None) -> dict:
        """Validator for dftracer_annotate_c: rule deterministically on annotated C (never edits it).

        Give `path` (one annotated .c file, or a tree: every .c/.h under it) or `source` (the text of one file; it is
        written to a temp dir and checked there). Checks, per file: compiles with gcc -fsyntax-only against the
        dftracer macro stub (clang_syntax_check's stub; `compiler` overrides gcc, `extra_include_dirs` adds -I, `extra_flags` adds -D/-U/-std/-I flags such as -DHAVE_CONFIG_H for autotools);
        includes dftracer/dftracer.h; no macros in headers; every DFTRACER_C_FUNCTION_START() is followed first by
        DFTRACER_C_FUNCTION_UPDATE_STR("comp", ...) and only one per function; DFTRACER_C_FUNCTION_END() right before
        every visible return; main has DFTRACER_C_INIT and DFTRACER_C_FINI and INIT's third argument is not a literal;
        every function calling open/read/write/fopen/H5*/MPI_File_* is annotated. No compiler -> that check is None.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], files}.
        """
        with _vtempfile.TemporaryDirectory() as tmp:
            files, err = _vfiles(path, source, "c", tmp)
            if files and not source:
                files = [f for f in files if f.suffix in (".c", ".h")] if _VPath(path).is_dir() else files
            if not files:
                return _verdict([_vchk("input", False, err, "pass an annotated .c file or tree, or its source text")])
            checks = _vc_like_checks(files, "c", compiler, extra_include_dirs, extra_flags=extra_flags)
            return _verdict(checks, files=[str(f.name) for f in files])
