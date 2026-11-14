"""dftracer_annotate_scope: decide WHAT to annotate before annotating: session_identify_smoke_test_files (files a smoke test reaches) and the Python cost gate (python_estimate_function_cost, python_estimate_file_costs), plus the validator dftracer_annotate_scope_check. Tools moved unchanged from dftracer_agents.mcp_tools.tools.session.annotation_filter and python_cost."""

from __future__ import annotations

import ast
from dftracer_agents.mcp_tools.tools.session.annotation_filter import (
    identify_smoke_test_source_files,
)
from dftracer_agents.mcp_tools.tools.session.python_cost import (
    PY_ANNOTATION_SCORE_THRESHOLD,
    _estimate_file_impl,
    _iter_functions,
    _resolve,
    recommend,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
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


from dftracer_agents.mcp_tools.tools.session.annotation_clang import _LIFECYCLE_RE, _VENDOR_PREFIX_RE
from dftracer_agents.mcp_tools.tools.session.python_cost import PY_ANNOTATION_SCORE_THRESHOLD, recommend


def register(mcp):
    """Register the dftracer_annotate_scope tools on *mcp*."""

    @mcp.tool()
    def session_identify_smoke_test_files(
        run_id: str,
        smoke_cmd: str,
        use_strace: bool = True,
    ) -> str:
        """Identify the minimal set of source files a smoke test actually exercises.

        For large C/C++ codebases with many independently-linked executables
        (e.g. Montage: ~700 files, 100+ binaries), annotating every file is
        wasteful and increases exposure to annotator edge cases in code paths
        that are never run. This tool narrows the annotation scope to just
        the files needed to build the binaries the smoke test invokes.

        Method:
            1. If ``use_strace`` (default), run *smoke_cmd* under
               ``strace -f -e trace=execve`` against the **original**
               (``source/`` + already-built ``install/bin/``) tree to capture
               every binary actually invoked at runtime. Falls back to a
               static scan of *smoke_cmd* text for binary names matching
               files in ``install/bin/`` if strace is unavailable or the
               command doesn't touch any workspace binaries.
            2. For each invoked binary, parse the ``Makefile`` link recipe
               that builds it (searches every ``Makefile`` under ``source/``)
               to extract every ``.o`` object file feeding that link.
            3. Resolve each object file back to its ``.c``/``.cpp`` source,
               honoring the Makefile's own relative-path context (handles
               ``../util/foo.o`` style references).
            4. Return the de-duplicated union of source files across all
               invoked binaries.

        Args:
            run_id: Session identifier returned by ``session_create``.
            smoke_cmd: The smoke-test command line (same string you'd pass to
                ``session_run_smoke_test``). Must be runnable against the
                original ``install/bin/`` (i.e. call this *before* annotating).
            use_strace: Prefer dynamic discovery via ``strace`` (default
                ``True``). Set ``False`` to skip straight to the static
                text-scan fallback (useful when strace isn't installed or
                the smoke test can't actually be executed yet).

        Returns:
            JSON string with keys:
                * ``status`` — ``"ok"``.
                * ``method`` — ``"strace"`` or ``"static"``.
                * ``binaries`` — sorted list of binary names discovered.
                * ``binary_to_files`` — dict mapping each binary name to its
                  list of required source files.
                * ``source_files`` — sorted de-duplicated union of all
                  required source files (paths relative to ``source/``) —
                  pass this list to ``clang_annotate_file`` in a loop instead
                  of ``clang_annotate_project`` to scope annotation.
                * ``total_binaries`` / ``total_source_files`` — counts.
        """
        import json as _json
        from dftracer_agents.mcp_tools.tools.session.workspace import _ws

        ws = _ws(run_id)
        result = identify_smoke_test_source_files(run_id, ws, smoke_cmd, use_strace=use_strace)
        return _json.dumps(result, indent=2)

    @mcp.tool()
    def python_estimate_function_cost(
        run_id: str,
        filepath: str,
        function_name: str,
        threshold: int = PY_ANNOTATION_SCORE_THRESHOLD,
    ) -> str:
        """Estimate the instrumentation value of ONE Python function (AI/ML aware).

        Use this to decide whether a function deserves *generic* dftracer
        instrumentation (``@_dlp.log``). It is the Python counterpart of
        ``clang_estimate_function_cost``, but scored on signals that matter for
        AI/ML workloads rather than POSIX/MPI symbols.

        **Do NOT gate AI/ML semantic regions on this.** Data loading, model
        init, checkpoint, training step, and distributed comm are annotated for
        what they mean, not what they cost — always annotate those
        (``dftracer-ml-annotate`` 4a–4f). This tool governs section 4g only.

        Signals counted from the AST (no regex): framework I/O
        (``torch.load``/``save``, ``h5py.File``, ``np.load``, ``read_parquet``,
        ``pickle``…), checkpoint/persistence (``state_dict``,
        ``from_pretrained``…), distributed comm (``all_reduce``, ``barrier``…),
        data pipeline (``DataLoader``, ``collate``…), host/device transfers
        (``.to()``, ``.cuda()``, ``.numpy()``…), autograd/compute
        (``backward``, ``step``…), loops, branches, calls, body size.

        Weights: io×30, checkpoint×35, comm×25, data×20, xfer×12, compute×10,
        loop×8, branch×3, call×2, size bonus ≤20.

        Overrides, in order: ``@property``/trivial dunder without I/O → skip;
        ``__getitem__``/``__iter__``/``__next__`` → annotate; any
        checkpoint/I/O/comm call → annotate; I/O or lifecycle name → annotate;
        trivial body (≤5 statements, no loop, no I/O) → skip; else score vs
        ``threshold``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            filepath: Path relative to ``annotated/`` (falls back to ``source/``).
            function_name: Exact function or method name to evaluate.
            threshold: Score at/above which a generic function is annotated.

        Returns:
            JSON with ``recommendation`` (``"annotate"``/``"skip"``), ``score``,
            ``threshold``, ``category``, ``reason``, and a ``cost_info``
            breakdown.
        """
        p = _resolve(run_id, filepath)
        if p is None:
            return _err(f"File not found in annotated/ or source/: {filepath}")
        try:
            tree = ast.parse(p.read_text(errors="ignore"))
        except SyntaxError as exc:
            return _err(f"SyntaxError parsing {filepath}: {exc}")
        target = next((f for f, q in _iter_functions(tree)
                       if f.name == function_name or q == function_name), None)
        if target is None:
            return _err(f"Function not found: {function_name}", filepath=filepath)
        return _ok(f"cost estimate for {function_name}", **recommend(target, threshold))

    @mcp.tool()
    def python_estimate_file_costs(
        run_id: str,
        filepath: str,
        threshold: int = PY_ANNOTATION_SCORE_THRESHOLD,
    ) -> str:
        """Score EVERY function in a Python file and return annotate/skip lists.

        One call per file instead of one per function — use this to drive
        selective generic annotation (``dftracer-ml-annotate`` 4g). Same scoring
        and overrides as ``python_estimate_function_cost``.

        Args:
            run_id: Session identifier returned by ``session_create``.
            filepath: Path relative to ``annotated/`` (falls back to ``source/``).
            threshold: Score at/above which a generic function is annotated.

        Returns:
            JSON with ``annotate`` (list of names), ``skip`` (list of names),
            and ``functions`` (per-function recommendation + cost breakdown).
        """
        return _estimate_file_impl(run_id, filepath, threshold)

    @mcp.tool()
    def dftracer_annotate_scope_check(files: List[str] = None, skip_functions: List[str] = None, source: str = "",
                                      annotate_functions: List[str] = None,
                                      threshold: int = PY_ANNOTATION_SCORE_THRESHOLD) -> dict:
        """Validator for dftracer_annotate_scope: rule deterministically on an annotation scope before it is applied.

        Inputs (all optional, give what the scope produced): `files` the files chosen for annotation (e.g. from
        session_identify_smoke_test_files); `skip_functions` functions the scope leaves out; `source` and
        `annotate_functions` one Python file's text and the functions chosen for it (python_estimate_file_costs).
        Checks: no header (.h/.hpp) is in `files` (R5: never annotate a header); no lifecycle function (R6, the clang
        annotator's _LIFECYCLE_RE: *_init, *_final(ize), *_open, *_close, *_create, *_sync, *_flush, *_stat, ...) and
        no vendor filesystem function (R7, _VENDOR_PREFIX_RE: gpfs_, beegfs_, llapi_, cuFile, hdfs_, daos_, ceph_,
        gfarm_) is skipped; every function in `annotate_functions` passes the Python cost gate at `threshold`, and
        every function the gate says to annotate is in it.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}]}.
        """
        checks = []
        if files is not None:
            hdr = [f for f in files if _VPath(f).suffix.lower() in (".h", ".hpp", ".hh", ".hxx")]
            checks.append(_vchk("no header files in scope", not hdr, ", ".join(hdr[:5]),
                                "" if not hdr else "drop the headers: annotate the .c/.cpp definitions instead"))
            checks.append(_vchk("scope is not empty", bool(files), "" if files else "no files",
                                "" if files else "re-run session_identify_smoke_test_files with the smoke command, or scope the whole tree"))
        if skip_functions is not None:
            bad = [f for f in skip_functions if _LIFECYCLE_RE.search(f) or _VENDOR_PREFIX_RE.search(f)]
            checks.append(_vchk("no lifecycle or vendor-fs function skipped", not bad, ", ".join(bad[:5]),
                                "" if not bad else "annotate them regardless of size (R6, R7: ALWAYS_ANNOTATE)"))
        if source:
            try:
                tree = _vast.parse(source)
            except SyntaxError as exc:
                return _verdict([_vchk("source parses", False, f"line {exc.lineno}: {exc.msg}", "pass valid Python")])
            want = {}
            for fn in _vast.walk(tree):
                if isinstance(fn, (_vast.FunctionDef, _vast.AsyncFunctionDef)):
                    want[fn.name] = recommend(fn, threshold)["recommendation"]
            chosen = set(annotate_functions or [])
            unknown = sorted(chosen - set(want))
            over = sorted(n for n in chosen & set(want) if want[n] == "skip")
            under = sorted(n for n, r in want.items() if r == "annotate" and n not in chosen)
            checks.append(_vchk("chosen functions exist", not unknown, ", ".join(unknown[:5]),
                                "" if not unknown else "use names from python_estimate_file_costs"))
            checks.append(_vchk("no chosen function fails the cost gate", not over, ", ".join(over[:5]),
                                "" if not over else "drop them (trace noise) or use an AI-API region"))
            checks.append(_vchk("every function the cost gate keeps is chosen", not under, ", ".join(under[:5]),
                                "" if not under else "add them to the annotate list"))
        if not checks:
            return _verdict([_vchk("input", None, "nothing to check", "give files, skip_functions, or source + annotate_functions")])
        return _verdict(checks)
