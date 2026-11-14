"""dftracer_annotate_clang: libclang-driven C/C++ annotation tools of dftracer-agents (extract functions, annotate a file or project, insert lines, add braces, syntax check, lint, cost estimate, regression test), shared by the C and C++ paths. Tools moved unchanged from dftracer_agents.mcp_tools.tools.session.annotation_clang; dftracer_annotate_clang_check is the deterministic validator."""

from __future__ import annotations

import os
import re
import shlex
from dftracer_agents.mcp_tools.tools.session.annotation_clang import (
    ANNOTATION_SCORE_THRESHOLD,
    _FILE_CACHE,
    _LIFECYCLE_RE,
    _VENDOR_PREFIX_RE,
    _annotate_file_worker,
    _cache_is_stale,
    _derive_comp,
    _discover_brace_include_dirs,
    _falls_through,
    _should_annotate,
    annotate_jobs,
    run_file_pool,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
    _ws,
)
from typing import (
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


def register(mcp):
    """Register the dftracer_annotate_clang tools on *mcp*."""

    @mcp.tool()
    def clang_add_braces(run_id: str, filepath: str) -> str:
        """Add ``{`` / ``}`` around braceless ``if`` / ``for`` / ``while`` bodies.

        Rewrites *filepath* inside the ``annotated/`` subfolder so that every
        braceless single-statement body gains an explicit ``CompoundStmt``
        block.  Run this **before** annotation so that inserting a
        ``DFTRACER_C_FUNCTION_END()`` before a ``return`` never creates a
        dangling-else or mismatched-brace syntax error.

        Uses clang ``-ast-dump=json`` for precise AST-level detection; falls
        back to a regex line scanner when clang is unavailable.

        Examples of patterns fixed::

            if (err)                     if (err) {
                return -1;       →           return -1;
                                         }

            for (i = 0; i < n; i++)      for (i = 0; i < n; i++) {
                process(i);      →           process(i);
                                         }

        The file is rewritten in place inside ``annotated/``.  The original
        ``source/`` tree is never touched.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys:
                * ``status``     — ``"ok"`` or ``"error"``.
                * ``modified``   — ``True`` if the file was changed.
                * ``insertions`` — number of brace pairs added.
                * ``method``     — ``"clang"`` or ``"regex"`` (which backend ran).
        """
        from dftracer_agents.mcp_tools.tools.session.source_parser import add_braces_c, ClangNotFoundError

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        _inc_dirs = _discover_brace_include_dirs(ws, filepath, run_id)
        try:
            result = add_braces_c(abs_path, extra_include_dirs=_inc_dirs)
        except ClangNotFoundError as exc:
            return _err(str(exc), filepath=filepath)
        if "error" in result:
            return _err(result["error"], filepath=filepath)

        if result.get("skipped_reason"):
            return _ok(
                "Brace insertion SKIPPED (safety guard): " + result["skipped_reason"],
                filepath=filepath,
                modified=False,
                insertions=0,
                method=result["method"],
                skipped_reason=result["skipped_reason"],
            )

        return _ok(
            f"Brace insertion complete: {result['insertions']} pair(s) added "
            f"via {result['method']}.",
            filepath=filepath,
            modified=result["modified"],
            insertions=result["insertions"],
            method=result["method"],
        )

    @mcp.tool()
    def clang_extract_functions(
        run_id: str, filepath: str, compile_flags: str = ""
    ) -> str:
        """Extract function definitions with exact line numbers from a source file.

        Uses clang ``-ast-dump=json`` for C/C++ files and Python's ``ast`` module
        for ``.py`` files.  Falls back to ``ctags`` then regex-based brace counting
        if clang is not available.

        **Pass ``compile_flags`` for any project with external headers.** A header
        clang cannot find is a *fatal* error that aborts the parse, and out-of-line
        member functions (``void Foo::bar() { ... }``) then disappear from the AST
        because their class declaration was never seen — the file looks
        function-free rather than unparseable. When that happens this tool now
        returns ``status: "error"`` with the clang diagnostic instead of a
        misleading ``ok``/``count: 0``.

        Each returned function record has:

        * ``name``            — function name
        * ``start_line``      — first line of the return type / signature
        * ``open_brace_line`` — line number of the opening ``{``
        * ``body_first_line`` — ``open_brace_line + 1`` — insert DFTRACER_*_START here
        * ``close_brace_line``— line number of the closing ``}``
        * ``exit_lines``      — list of ``{"line": N, "type": "return"|"exit"|…}``
                                 indicating where END macros are needed
        * ``is_entry_point``  — ``True`` for ``main`` / ``__main__``
        * ``source``          — which extractor was used (``"clang"``, ``"ctags"``,
                                ``"regex"``, or ``"ast"``)

        This tool is called by the annotation sub-recipes *before* any macro is
        written so the agent has the authoritative function map and does not need to
        manually scan the file for exit paths.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path to the file relative to the ``annotated/`` sub-folder.
            compile_flags: Extra clang flags as one shell-quoted string, e.g.
                ``"-I/prefix/include -I/prefix/include/mfem -D__HIP_PLATFORM_AMD__=1"``.
                For a Makefile project the reliable source of these is the build's
                own variables (e.g. MFEM's ``MFEM_INCFLAGS``) rather than a guess.

        Returns:
            JSON string with keys:
                * ``status``    — ``"ok"`` or ``"error"``.
                * ``message``   — human-readable summary.
                * ``filepath``  — echoed input path.
                * ``functions`` — list of function-info dicts.
                * ``count``     — number of functions found.
                * ``extractor`` — which backend produced the result.
                * ``clang_fatal_errors`` — clang ``fatal error:`` lines, when any.
        """
        from dftracer_agents.mcp_tools.tools.session.source_parser import extract_functions, ClangNotFoundError

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        _flags = shlex.split(compile_flags) if compile_flags else None
        _diag: dict = {}
        try:
            functions = extract_functions(str(abs_path), _flags, _diag)
        except ClangNotFoundError as exc:
            return _err(str(exc), filepath=filepath)

        extractor = functions[0].get("source", "unknown") if functions else "clang"

        # A fatal parse error that yielded nothing is a FAILURE, not an empty
        # file. Reporting "ok, 0 functions" here is what let a whole
        # translation unit (every out-of-line method in it) go silently
        # un-annotated while the pipeline reported success.
        if _diag.get("fatal_error") and not functions:
            return _err(
                f"clang could not parse {filepath}: "
                f"{'; '.join(_diag.get('fatal_messages') or [])}. "
                f"No functions were extracted, so this file would be left "
                f"UNANNOTATED. Supply the project's include flags via "
                f"compile_flags.",
                filepath=filepath,
                functions=[],
                count=0,
                extractor=extractor,
                clang_fatal_errors=_diag.get("fatal_messages") or [],
            )

        return _ok(
            f"Extracted {len(functions)} function(s) from {filepath} "
            f"using {extractor}.",
            filepath=filepath,
            functions=functions,
            count=len(functions),
            extractor=extractor,
            clang_fatal_errors=_diag.get("fatal_messages") or [],
        )

    @mcp.tool()
    def clang_insert_line(
        run_id: str,
        filepath: str,
        line_number: int,
        content: str,
        position: str = "before",
    ) -> str:
        """Insert a single line of code at an exact line number in an annotated file.

        Allows annotation agents to insert dftracer macros at precise positions
        identified from the ``clang_extract_functions`` output without needing
        to rewrite the entire file.

        .. warning:: Line-shift hazard for multiple insertions
           Each call reads the *current* file state, inserts one line, and writes
           back immediately.  If you need to insert at several positions from the
           same ``clang_extract_functions`` snapshot, process them in
           **highest-line-number-first order** so that earlier insertions do not
           shift the positions of later ones.  For whole-file annotation prefer
           ``clang_annotate_file``, which handles ordering automatically in a
           single in-memory pass.

        The file is modified inside the ``annotated/`` subfolder.  The original
        ``source/`` copy is never touched.

        Args:
            run_id:      Session identifier returned by ``session_create``.
            filepath:    Path relative to the ``annotated/`` subfolder.
            line_number: 1-based line number at which to insert. When
                         ``position="before"`` the new line is inserted *above*
                         this line; when ``position="after"`` it is inserted
                         *below* this line.
            content:     Full text of the line to insert (without trailing newline).
                         Include any desired leading indentation in this string.
            position:    ``"before"`` (default) or ``"after"``.

        Returns:
            JSON string with keys:
                * ``status``      — ``"ok"`` or ``"error"``.
                * ``message``     — human-readable outcome.
                * ``filepath``    — echoed input path.
                * ``line_number`` — effective 1-based insertion point.
                * ``inserted``    — the content that was written.
                * ``total_lines`` — total line count after the insertion.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        if position not in ("before", "after"):
            return _err(
                f"Invalid position '{position}' — must be 'before' or 'after'",
                filepath=filepath,
            )

        lines = abs_path.read_text(errors="replace").splitlines(keepends=True)
        total = len(lines)

        if position == "before":
            # Insert before line_number (0-based index = line_number - 1)
            insert_at = max(0, min(line_number - 1, total))
        else:
            # Insert after line_number
            insert_at = max(0, min(line_number, total))

        new_line = content + "\n"
        lines.insert(insert_at, new_line)
        abs_path.write_text("".join(lines))

        effective_line = insert_at + 1
        return _ok(
            f"Inserted line at position {effective_line} ({position} line {line_number}) "
            f"in {filepath}.",
            filepath=filepath,
            line_number=effective_line,
            inserted=content,
            total_lines=len(lines),
        )

    @mcp.tool()
    def clang_annotate_file(
        run_id: str,
        filepath: str,
        language: str = "c",
        is_entry: bool = False,
        init_args: str = "NULL, NULL, NULL",
        comp_overrides: str = None,
        exclude_functions: str = None,
        write_immediately: bool = True,
        compile_flags: str = "",
    ) -> str:
        """Annotate a C/C++ source file with dftracer macros AND write it to disk.

        **DEFAULT BEHAVIOR (write_immediately=True, the common case): a single
        call to this tool is enough.** It loads the file, computes every macro
        insertion, applies them in memory, and then writes the result straight
        to disk before returning — there is no separate "flush" step you must
        remember to call. The response includes ``written_to_disk``,
        ``bytes_written`` and ``disk_mtime_ns`` so the caller (or a reviewing
        agent) can spot-check that real bytes landed on disk rather than
        trusting the insertion counts alone. This closes a real failure mode:
        an agent previously called the old in-memory-only version of this
        tool, got a plausible "N insertions" response, and reported success to
        the orchestrator without ever writing anything to disk.

        Concretely, it:

        1. Loads the current file content (from the in-memory cache if a
           deferred, not-yet-written pass from a prior call is pending, else
           from disk).
        2. Calls ``clang_extract_functions`` internally to get an authoritative
           function map with exact line numbers.
        3. Computes all insertion points:
           * ``#include <dftracer/dftracer.h>`` before the first top-level ``#include``.
           * ``DFTRACER_C_FUNCTION_START()`` (or ``DFTRACER_CPP_FUNCTION()``) at
             the first line of every function body (``body_first_line``).
           * ``DFTRACER_C_FUNCTION_END()`` before every ``exit_line`` in the
             function map; or before the ``close_brace_line`` for void/fallthrough
             functions with no recorded exits.
           * For entry-point files (``is_entry=True``): ``DFTRACER_C_INIT(…)``
             before ``DFTRACER_C_FUNCTION_START()`` inside ``main()``, and
             ``DFTRACER_C_FINI()`` before each ``DFTRACER_C_FUNCTION_END()``
             in ``main()``.
        4. Sorts all insertion points **highest-line-number first** so that each
           insertion does not shift the positions of subsequent ones — the classic
           bottom-to-top strategy.
        5. Applies every insertion to the in-memory line list.
        6. **Writes the file to disk immediately** (unless ``write_immediately``
           is explicitly set to ``False`` — see escape hatch below).

        The file is modified inside the ``annotated/`` subfolder.  The original
        ``source/`` copy is never touched.  The operation is idempotent: if
        ``#include <dftracer/dftracer.h>`` is already present the file is
        returned unchanged (and is still reported as ``written_to_disk: True``
        since the annotated content is already on disk from an earlier pass).

        **Escape hatch — ``write_immediately=False``:** set this only for a
        deliberate multi-step compose-then-validate-then-write pipeline, e.g.
        ``clang_add_braces`` → ``clang_annotate_file(write_immediately=False)``
        → ``clang_syntax_check`` (validated against the in-memory result) →
        ``clang_write_annotated_file`` once, so a syntax-check failure never
        leaves a half-annotated file sitting on disk mid-pipeline. When this
        flag is set, the result is held only in the module-level in-memory
        cache (keyed by ``(run_id, filepath)``) and ``written_to_disk`` in the
        response will be ``False`` — the caller MUST follow up with
        ``clang_write_annotated_file`` to persist it, or the change is lost.
        Leave the default (``True``) unless you have a concrete reason to
        defer: the tradeoff of always-write is that a later validation step
        failing could theoretically leave a syntax-broken file on disk
        temporarily — but that is judged strictly safer than the previous
        failure mode (an agent silently reporting success with nothing written
        at all), because a broken file makes the very next build/smoke-test
        step fail loudly, whereas a missing write fails silently forever.

        Args:
            run_id:     Session identifier returned by ``session_create``.
            filepath:   Path relative to the ``annotated/`` subfolder.
            language:   ``"c"`` (default) or ``"cpp"``.  Controls which macro
                        family is emitted (``DFTRACER_C_*`` vs
                        ``DFTRACER_CPP_FUNCTION``).
            is_entry:   ``True`` when this file contains ``main()`` and should
                        receive ``DFTRACER_C_INIT`` / ``DFTRACER_C_FINI`` in
                        addition to the per-function START/END macros.
            init_args:  Argument string for ``DFTRACER_C_INIT(…)``; defaults to
                        ``"NULL, NULL, NULL"`` (log to default path, trace all
                        dirs, auto-detect PID). The third parameter is
                        ``int* process_id`` in ``dftracer.h`` — never pass an
                        int literal like ``-1`` here, that's an int-to-pointer
                        mismatch that lenient compilers merely warn about but
                        strict Cray clang treats as fatal, corrupting the rest
                        of the file's parse. Ignored when ``is_entry=False``.
            comp_overrides: Optional JSON object string mapping function names to
                        ``comp`` category strings (e.g. ``'{"main": "cpu",
                        "MPIIO_Xfer": "comm"}'``).  Overrides the automatic
                        ``comp`` classification for the named functions.
                        Defaults to ``None`` (use automatic classification).
            exclude_functions: Optional JSON array string of function names to
                        force-skip regardless of the static cost filter (e.g.
                        ``'["avg_mean"]'``). Use this for functions called in
                        hot per-pixel/per-element inner loops that the AST-cost
                        heuristic can't detect — static cost estimation has no
                        way to know a function is called millions of times at
                        runtime, and annotating it can produce gigabytes of
                        trace noise that drowns out real I/O signal. Defaults
                        to ``None`` (no additional exclusions).
            write_immediately: ``True`` (default) writes the annotated result
                        to disk before returning — one call is sufficient.
                        Set to ``False`` only for a deliberate multi-step
                        compose pipeline (see escape hatch above); you must
                        then call ``clang_write_annotated_file`` yourself.

        Returns:
            JSON string with keys:
                * ``status``      — ``"ok"`` or ``"error"``.
                * ``message``     — human-readable summary.
                * ``filepath``    — echoed input path.
                * ``insertions``  — total number of lines inserted.
                * ``functions``   — number of functions annotated.
                * ``total_lines`` — line count of the file after annotation.
                * ``already_annotated`` — ``True`` if the file was skipped
                  because dftracer macros were already present.
                * ``written_to_disk`` — ``True`` if the annotated content is
                  now on disk (always ``True`` unless ``write_immediately``
                  was explicitly set to ``False``).
                * ``bytes_written`` — size in bytes of the content written
                  (or held in memory if deferred).
                * ``disk_mtime_ns`` — on-disk mtime (ns) after the write, for
                  a caller to spot-check with a fresh ``stat``; ``None`` when
                  the write was deferred.
        """
        from dftracer_agents.mcp_tools.tools.session.source_parser import add_braces_c, extract_functions, ClangNotFoundError

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        # Idempotency guard — read from cache or disk. The cache is only
        # trusted if its recorded mtime still matches the file on disk;
        # otherwise the file was replaced out from under us (e.g. a source
        # tree reset) and we must fall back to a fresh disk read so we never
        # report already_annotated against stale in-memory content.
        cache_key = (run_id, filepath)
        _cache_is_stale(abs_path, cache_key)
        if cache_key in _FILE_CACHE:
            _cached_mtime, _cached_lines = _FILE_CACHE[cache_key]
            text = "\n".join(_cached_lines)
        else:
            text = abs_path.read_text(errors="replace")

        # Idempotency requires BOTH the include AND at least one actual
        # function-level macro — the include alone was previously treated as
        # proof of prior annotation, which silently masked a real failure
        # mode: a call that inserted only the #include (e.g. every target
        # function force-skipped by an exclude_functions/comp_overrides
        # interaction) reported functions=0 once, then "already_annotated"
        # forever after on retry (confirmed on RAJAPerf 2026-08-05).
        _has_function_macros = bool(re.search(
            r"DFTRACER_(?:C_FUNCTION_START|CPP_FUNCTION|CPP_REGION_START)\s*\(",
            text,
        ))
        if "#include <dftracer/dftracer.h>" in text and _has_function_macros:
            return _ok(
                f"{filepath} is already annotated — skipped.",
                filepath=filepath,
                insertions=0,
                functions=0,
                total_lines=len(text.splitlines()),
                already_annotated=True,
                braces_added=0,
                written_to_disk=True,
                bytes_written=len(text.encode("utf-8", errors="replace")),
                disk_mtime_ns=abs_path.stat().st_mtime_ns,
            )

        # ── Step 0: add braces to braceless control-flow bodies ──────────────
        # Required before function extraction: inserting FUNCTION_END before a
        # return inside a braceless if/for/while would otherwise create a
        # dangling-else or brace-mismatch syntax error.
        # Flush any in-memory cache to disk so add_braces_c sees current content.
        if cache_key in _FILE_CACHE:
            _cached_mtime, _cached_lines = _FILE_CACHE[cache_key]
            abs_path.write_text("\n".join(_cached_lines) + "\n")
            del _FILE_CACHE[cache_key]

        try:
            import shlex as _shlex
            brace_result = add_braces_c(
                abs_path,
                extra_include_dirs=_discover_brace_include_dirs(ws, filepath, run_id),
                extra_flags=_shlex.split(compile_flags) if compile_flags else None,
            )
        except ClangNotFoundError as exc:
            return _err(str(exc), filepath=filepath)

        braces_added = brace_result.get("insertions", 0)
        braces_skipped_reason = brace_result.get("skipped_reason")

        # Reload from disk (brace insertion may have shifted line numbers)
        text = abs_path.read_text(errors="replace")
        lines = text.splitlines()

        is_cpp = language.lower() in ("cpp", "c++", "cxx")
        import json as _json
        _comp_overrides: dict = _json.loads(comp_overrides) if comp_overrides else {}
        _exclude_functions: set = set(_json.loads(exclude_functions)) if exclude_functions else set()
        START = (
            "DFTRACER_CPP_FUNCTION();"
            if is_cpp
            else "DFTRACER_C_FUNCTION_START();"
        )
        END = "DFTRACER_C_FUNCTION_END();"  # C++ uses RAII, no explicit END needed
        INIT = (
            f"DFTRACER_CPP_INIT({init_args});" if is_cpp
            else f"DFTRACER_C_INIT({init_args});"
        )
        FINI = "DFTRACER_CPP_FINI();" if is_cpp else "DFTRACER_C_FINI();"

        def _make_update(
            fn: dict, ind: str, comp_override: str = None, region_name: str = None
        ) -> str:
            comp = comp_override if comp_override is not None else _derive_comp(fn)
            if region_name is not None:
                # Pointer-based REGION (profiler_##name is a DFTracer*, declared
                # by REGION_START) needs the arrow-deref DYN_UPDATE variant, NOT
                # FUNCTION_UPDATE — that one hardcodes the unrelated
                # profiler_dft_fn variable, which REGION_START never declares.
                return f'{ind}DFTRACER_CPP_REGION_DYN_UPDATE({region_name}, "comp", "{comp}");'
            if is_cpp:
                return f'{ind}DFTRACER_CPP_FUNCTION_UPDATE("comp", "{comp}");'
            return f'{ind}DFTRACER_C_FUNCTION_UPDATE_STR("comp", "{comp}");'

        # ── Step 1: find where to insert #include (outside any #ifdef block) ──
        # Track preprocessor block depth so we never insert inside a conditional
        # section (which would make the include unavailable in the main code path).
        include_line = "#include <dftracer/dftracer.h>"
        last_inc_idx = -1
        first_inc_idx = -1
        pp_depth = 0
        for i, ln in enumerate(lines):
            s = ln.strip()
            if re.match(r'#\s*(?:ifdef|ifndef|if\b)', s):
                pp_depth += 1
            elif re.match(r'#\s*endif', s):
                pp_depth = max(0, pp_depth - 1)
            elif pp_depth == 0 and re.match(r'#\s*include', s):
                if first_inc_idx < 0:
                    first_inc_idx = i
                last_inc_idx = i
        # Insert BEFORE the first top-level #include, not after the last one:
        # app headers may #define builtin type names (Enzo's
        # macros_and_parameters.h: `#define int long_int`, `#define float
        # double`), which then mangle dftracer's own headers ("redefinition of
        # 'long_int'", "typedef redefinition with different types").
        # dftracer.h is self-contained, so including it first is always safe.
        include_insert_at = first_inc_idx if first_inc_idx >= 0 else last_inc_idx + 1

        # ── Step 2: extract function map ──────────────────────────────────────
        # Write current in-memory state to a temp file so extract_functions can
        # parse it accurately (avoids stale on-disk content).
        import tempfile, os
        suffix = abs_path.suffix
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=suffix, delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write("\n".join(lines) + "\n")
            tmp_path = tmp.name
        # The content is parsed from a COPY in the system temp dir, which throws
        # away the original file's directory. Quoted includes
        # (`#include "laghos_solver.hpp"`, `#include "general/forall.hpp"`) are
        # resolved relative to the including file, so without putting the real
        # directory back on the search path they all fail -- and a failed include
        # is a fatal error that empties the AST. Add the source's own directory
        # (and the annotated/ root) ahead of any caller flags.
        _extract_diag: dict = {}
        _auto_flags = [f"-I{abs_path.parent}", f"-I{ws / 'annotated'}"]
        _user_flags = shlex.split(compile_flags) if compile_flags else []
        try:
            functions = extract_functions(
                tmp_path,
                _auto_flags + _user_flags,
                _extract_diag,
            )
        except ClangNotFoundError as exc:
            os.unlink(tmp_path)
            return _err(str(exc), filepath=filepath)
        finally:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)

        # Never silently annotate nothing. A fatal parse error with zero
        # functions means every function in this translation unit -- including
        # out-of-line class methods, which are the ones that matter in C++ --
        # would be skipped while the call still reported success.
        if _extract_diag.get("fatal_error") and not functions:
            return _err(
                f"clang could not parse {filepath}: "
                f"{'; '.join(_extract_diag.get('fatal_messages') or [])}. "
                f"Refusing to report success for a file where 0 functions were "
                f"found due to a parse failure — pass the project's include "
                f"flags via compile_flags.",
                filepath=filepath,
                functions_found=0,
                clang_fatal_errors=_extract_diag.get("fatal_messages") or [],
            )

        # ── Step 3: build insertion list (0-based indices, original line nums) ─
        # Each entry: (index_0based, text_to_insert)
        # We sort highest-first so each insert does not shift later positions.

        def _indent(lines_list, idx_0based, default="    "):
            ln = lines_list[idx_0based] if 0 <= idx_0based < len(lines_list) else ""
            n = len(ln) - len(ln.lstrip())
            return " " * n if n > 0 else default

        # Re-annotation idempotency: a file can reach this point while already
        # carrying some dftracer code (only the #include from an earlier pass
        # whose functions were all cost-filtered, a hand-added INIT, a partly
        # annotated file). Never insert anything that is already there: the
        # include once, INIT/FINI once, START/END once per function.
        _has_include = bool(re.search(
            r'^\s*#\s*include\s*[<"]dftracer/dftracer\.h[>"]', "\n".join(lines), re.M))
        _has_init = bool(re.search(r"DFTRACER_(?:C|CPP)_INIT\s*\(", "\n".join(lines)))
        _has_fini = bool(re.search(r"DFTRACER_(?:C|CPP)_FINI\s*\(", "\n".join(lines)))
        _FN_MACRO_RE = re.compile(
            r"DFTRACER_(?:C_FUNCTION_START|CPP_FUNCTION|CPP_REGION_START)\s*\(")
        already_annotated_functions: list[str] = []

        insertions: list = [] if _has_include else [(include_insert_at, include_line)]
        skipped_functions: list[str] = []

        # ── Pre-scan for MPI boundary lines (needed for entry-point handling) ──
        # Find the last MPI startup call and EVERY MPI teardown call.  Only used
        # when is_entry=True.
        #
        # Matching the literal MPI_Init/MPI_Finalize symbols alone is not enough:
        # many apps wrap them (miniFE's `miniFE::initialize_mpi(argc, argv, ...)` /
        # `miniFE::finalize_mpi()`, laghos likewise).  When the literal symbol was
        # missing, `_mpi_finalize_line` stayed None and END/FINI fell back to the
        # closing brace -- i.e. AFTER `return return_code;`, which is dead code, so
        # finalize never ran and the trace had zero app events.  The early-exit
        # END/FINI pass was additionally gated on `_mpi_init_line is not None`, so
        # it was skipped entirely for those same apps.  Both were real
        # zero-CPP_APP-event bugs (laghos, then miniFE).  Match the wrappers too.
        _MPI_INIT_RE = re.compile(
            r'\bMPI_(?:Init|Init_thread|Comm_rank|Comm_size)\s*\('
            r'|\b(?:initiali[sz]e|init|setup|start)_mpi\s*\('
            r'|\bmpi_(?:initiali[sz]e|init|setup|start)\s*\(',
            re.IGNORECASE,
        )
        _MPI_FINI_RE = re.compile(
            r'\bMPI_Finalize\s*\('
            r'|\b(?:finali[sz]e|cleanup|shutdown|stop)_mpi\s*\('
            r'|\bmpi_(?:finali[sz]e|cleanup|shutdown|stop)\s*\(',
            re.IGNORECASE,
        )
        _mpi_init_line = None      # 1-based; line AFTER which INIT/START should go
        _mpi_finalize_line = None  # 1-based; FIRST teardown line (back-compat)
        _mpi_fini_lines: list[int] = []  # 1-based; ALL teardown lines
        if is_entry:
            for _i, _ln in enumerate(lines, start=1):
                _s = _ln.strip()
                if _s.startswith("//") or _s.startswith("*"):
                    continue
                if _MPI_INIT_RE.search(_s):
                    _mpi_init_line = _i
                if _MPI_FINI_RE.search(_s):
                    _mpi_fini_lines.append(_i)
                    if _mpi_finalize_line is None:
                        _mpi_finalize_line = _i

        for fn in functions:
            body_first = fn.get("body_first_line")
            close       = fn.get("close_brace_line")
            exits       = fn.get("exit_lines", [])
            fn_name     = fn.get("name", "")
            if body_first is None or close is None:
                continue

            # ── Cost filter (Rule 0 / R6 / R7) ───────────────────────────────
            # Skip trivial functions unless they are lifecycle or vendor-FS.
            # entry-point main() is always annotated.
            if fn_name != "main" and not _should_annotate(fn):
                skipped_functions.append(fn_name)
                continue
            if fn_name in _exclude_functions:
                skipped_functions.append(fn_name)
                continue
            if fn.get("cuda_device"):
                # GPU code (__global__/__device__): host macros cannot run there.
                skipped_functions.append(fn_name)
                continue

            body_idx = body_first - 1  # 0-based index of first body line
            ind = _indent(lines, body_idx)

            if any(_FN_MACRO_RE.search(ln) for ln in lines[max(body_idx - 1, 0):close]):
                # This function already carries its START macro (an earlier
                # pass): annotating it again would duplicate START/END.
                already_annotated_functions.append(fn_name)
                skipped_functions.append(fn_name)
                continue

            is_main_fn = fn_name == "main" and is_entry

            # A C++ main() must NOT use the RAII DFTRACER_CPP_FUNCTION() macro:
            # its destructor would fire at scope exit, i.e. AFTER our own
            # explicit DFTRACER_CPP_FINI() call runs just before `return` —
            # see dftracer-annotation-lessons CP2/CP3. Use the explicit
            # REGION_START/REGION_END pair for a C++ main() instead; every
            # other C++ function keeps the RAII DFTRACER_CPP_FUNCTION() macro
            # (no explicit END needed for those).
            if is_main_fn and is_cpp:
                # Bare identifier, NOT a quoted string: REGION_START/END do
                # profiler_##name token-pasting (dftracer.h) — a quoted
                # "main" would paste into the invalid token profiler_"main".
                # The trace event's name string is derived internally via
                # #name stringization, so no manual quoting is needed or
                # correct here.
                fn_start = "DFTRACER_CPP_REGION_START(main);"
                fn_end = "DFTRACER_CPP_REGION_END(main);"
            else:
                fn_start = START
                fn_end = END
            _region_name = "main" if (is_main_fn and is_cpp) else None

            if is_main_fn and _mpi_init_line is not None:
                # A2: place INIT/START/UPDATE right after the last MPI startup call
                # (MPI_Comm_rank is the last — rank info is embedded in trace metadata)
                init_idx = _mpi_init_line   # insert AFTER line _mpi_init_line (0-based = _mpi_init_line)
                init_ind = _indent(lines, _mpi_init_line - 1, ind)
                # A6: main() is always "cpu" regardless of MPI calls in its body
                if not _has_init:
                    insertions.append((init_idx, init_ind + INIT))      # A3: prio 3 → on top
                insertions.append((init_idx, init_ind + fn_start))      # A3: prio 2 → below INIT
                insertions.append((init_idx, "UPDATE:" + _make_update(fn, init_ind, comp_override=_comp_overrides.get(fn_name, "cpu"), region_name=_region_name)))
            else:
                if is_main_fn and not _has_init:
                    # Fallback when no MPI startup calls detected
                    insertions.append((body_idx, ind + INIT))
                insertions.append((body_idx, ind + fn_start))
                comp_ov = _comp_overrides.get(fn_name) if fn_name in _comp_overrides else ("cpu" if is_main_fn else None)
                insertions.append((body_idx, "UPDATE:" + _make_update(fn, ind, comp_override=comp_ov, region_name=_region_name)))

            # Explicit END/FINI placement is required for: every C function
            # (no RAII in C), AND a C++ main() specifically (DFTRACER_CPP_INIT/
            # FINI and the REGION_START/END pair above are explicit calls, not
            # RAII — the RAII exemption applies only to DFTRACER_CPP_FUNCTION()
            # in non-main C++ functions, which never reach this block).
            if not is_cpp or is_main_fn:
                _exit_lines = sorted({
                    (ex.get("line") if isinstance(ex, dict) else ex)
                    for ex in (exits or [])
                    if (ex.get("line") if isinstance(ex, dict) else ex) is not None
                })

                if is_main_fn:
                    # A4/A5: END+FINI must run on EVERY exit path of main().
                    #
                    # Resolve each exit to the line the pair must precede: the
                    # nearest MPI teardown call above it with no other exit in
                    # between (so FINI precedes MPI_Finalize -- lint rule L3),
                    # otherwise the exit itself.  This replaces the old logic,
                    # which anchored only at the FIRST MPI_Finalize and fell
                    # back to the closing brace when none was found -- putting
                    # END+FINI *after* `return`, where they never execute, so
                    # the logger never flushed and the trace had zero app
                    # events while still looking healthy (POSIX/STDIO/PAPI
                    # flush independently).  Hit on laghos, then miniFE.
                    _anchors: set[int] = set()
                    for _ex in _exit_lines:
                        _anchor = _ex
                        _cands = [f for f in _mpi_fini_lines if f < _ex]
                        if _cands:
                            _f = max(_cands)
                            if not any(_f < _o < _ex for _o in _exit_lines):
                                _anchor = _f
                        _anchors.add(_anchor)

                    if _falls_through(lines, close):
                        # Control also reaches the closing brace (no trailing
                        # return).  Anchor before a trailing teardown call if
                        # there is one, else immediately before the brace.
                        _tail = [f for f in _mpi_fini_lines
                                 if f not in _anchors
                                 and (not _exit_lines or f > max(_exit_lines))]
                        _anchors.add(max(_tail) if _tail else close)
                    if not _anchors:
                        _anchors.add(close)

                    for _a in sorted(_anchors):
                        _a_idx = _a - 1
                        _a_ind = _indent(lines, _a_idx, ind)
                        if not _has_fini:
                            insertions.append((_a_idx, _a_ind + FINI))
                        insertions.append((_a_idx, _a_ind + fn_end))
                else:
                    # Regular (non-entry-point) C functions only — C++ regular
                    # functions never reach here (RAII covers them; guarded by
                    # the `not is_cpp or is_main_fn` check above).
                    for _ex in _exit_lines:
                        _ex_idx = _ex - 1
                        _ex_ind = _indent(lines, _ex_idx, ind)
                        insertions.append((_ex_idx, _ex_ind + END))
                    # A function can BOTH return early and fall through to its
                    # closing brace; the old `if exits / else close-brace` split
                    # missed the fall-through path in that case, silently losing
                    # the span on the most common route through the function.
                    if not _exit_lines or _falls_through(lines, close):
                        insertions.append((close - 1, ind + END))

        # ── Step 4: sort highest-index-first, apply all in one pass ───────────
        # For same index, insertion order (bottom-to-top within the index)
        # determines final vertical order — the LAST inserted ends up on top:
        #   prio 0: END / FINI  → processed first, end up at bottom
        #   prio 1: UPDATE      → processed second, pushed above END
        #   prio 2: START / INIT → processed last, pushed above UPDATE
        def _sort_key(item):
            idx, txt = item
            # Regex (not the START/END/INIT/FINI string constants directly) so
            # this also classifies the C++ main()-only REGION_START/REGION_END
            # pair correctly — those are distinct literal strings from the
            # regular START/END/INIT/FINI constants above but belong in the
            # same priority buckets (REGION_END with END/FINI, REGION_START
            # with START).
            if re.search(r"_(?:FUNCTION_END|REGION_END|FINI)\s*\(", txt):
                prio = 0
            elif txt.startswith("UPDATE:"):
                prio = 1
            elif re.search(r"_INIT\s*\(", txt) and not re.search(r"_(?:FUNCTION_START|REGION_START)\s*\(", txt):
                prio = 3   # INIT inserted last → ends up above START in file
            else:
                prio = 2   # START and everything else
            return (-idx, prio)

        insertions.sort(key=_sort_key)

        for idx, txt in insertions:
            # Strip the UPDATE sentinel prefix before writing to the file.
            lines.insert(idx, txt.removeprefix("UPDATE:"))

        content = "\n".join(lines) + "\n"
        annotated_count = len(functions) - len(skipped_functions)

        if write_immediately:
            # Write to disk first, then cache alongside the mtime that write
            # produced — this keeps the cache entry provably in sync with disk
            # until/unless something external touches the file again.
            abs_path.write_text(content)
            disk_mtime_ns = abs_path.stat().st_mtime_ns
            _FILE_CACHE[cache_key] = (disk_mtime_ns, list(lines))
            written_to_disk = True
        else:
            # Deliberate multi-step compose case (write_immediately=False):
            # hold the result in the module-level cache only. Record the
            # file's CURRENT on-disk mtime (unchanged by this call) so
            # _cache_is_stale sees the cache as still valid on the next
            # read, rather than evicting our freshly computed in-memory
            # content. The caller MUST call clang_write_annotated_file (or
            # re-call this tool with write_immediately=True) to persist it.
            disk_mtime_ns = abs_path.stat().st_mtime_ns
            _FILE_CACHE[cache_key] = (disk_mtime_ns, list(lines))
            written_to_disk = False

        msg = (
            f"Annotated {filepath}: {len(insertions)} line(s) inserted across "
            f"{annotated_count} function(s); {len(skipped_functions)} trivial "
            f"function(s) skipped ({braces_added} brace pair(s) added)."
        )
        if written_to_disk:
            msg += " Written to disk."
        else:
            msg += (
                " NOT yet written to disk (write_immediately=False) — call "
                "clang_write_annotated_file to persist."
            )
        if braces_skipped_reason:
            msg += (
                " WARNING: brace insertion was SKIPPED (safety guard) — "
                + braces_skipped_reason
            )

        return _ok(
            msg,
            filepath=filepath,
            insertions=len(insertions),
            functions=annotated_count,
            skipped=len(skipped_functions),
            skipped_names=skipped_functions,
            already_annotated_functions=already_annotated_functions,
            include_already_present=_has_include,
            total_lines=len(lines),
            already_annotated=False,
            braces_added=braces_added,
            braces_skipped_reason=braces_skipped_reason,
            written_to_disk=written_to_disk,
            bytes_written=len(content.encode("utf-8", errors="replace")),
            disk_mtime_ns=disk_mtime_ns if written_to_disk else None,
        )

    @mcp.tool()
    def clang_annotate_project(
        run_id: str,
        language: str = "c",
        init_args: str = "NULL, NULL, NULL",
        exclude_patterns: List[str] = None,
        compile_flags: str = "",
        cpp_extensions: List[str] = None,
        jobs: int = 0,
    ) -> str:
        """Annotate every C/C++ source file in the ``annotated/`` workspace in one call.

        Discovers all ``.c`` / ``.cpp`` / ``.cxx`` / ``.cc`` files under
        ``annotated/``, determines which contain ``main()`` (entry-point files),
        and annotates them in the correct order:

        1. **Library / inner files first** — annotated with ``is_entry=False``.
        2. **Entry-point files last** — annotated with ``is_entry=True`` so
           ``DFTRACER_C_INIT`` / ``DFTRACER_C_FINI`` are inserted around ``main()``.

        Each file is processed by ``clang_annotate_file`` which:

        * Adds braces to braceless control-flow bodies (required before macro insertion).
        * Skips trivial functions automatically (cost filter, Rules 0 / R6 / R7).
        * Inserts ``DFTRACER_C_FUNCTION_UPDATE_STR("comp", …)`` with the correct
          category derived from the AST cost info (mpi → "comm", io → "io",
          mem → "mem", cpu → "cpu").
        * Is idempotent — already-annotated files are silently skipped.

        Paths can be excluded by passing glob-style substrings in
        ``exclude_patterns`` (e.g. ``["test/", "vendor/"]``).  The following
        patterns are always excluded regardless:
        ``/test/``, ``/tests/``, ``/vendor/``, ``/third_party/``,
        ``/CMakeFiles/``, ``/.git/``.

        Args:
            run_id:           Session identifier returned by ``session_create``.
            language:         ``"c"`` or ``"cpp"``.  Applied to all files.
            init_args:        Argument string for ``DFTRACER_C_INIT(…)``.
            exclude_patterns: Extra path substrings to skip.
            jobs:             Parallel worker processes for the non-entry files
                              (each file is independent: own clang parse, own
                              write). ``0`` = ``$DFTRACER_ANNOTATE_JOBS`` or
                              min(16, CPU count); ``1`` = serial. Entry-point
                              files (``main``) are annotated last (also in
                              parallel), after every library file is written.
                              Output (file order, counts, errors) is identical
                              to the serial run.
            cpp_extensions:   Extra, CASE-SENSITIVE file extensions that are C++
                              (e.g. ``[".C", ".cu.cc", ".cpp.in"]``). Files with
                              these extensions are discovered and annotated as
                              ``cpp`` regardless of ``language``. Uppercase
                              ``.C`` (Enzo, SW4, ...) is always C++ — it used to
                              be lowercased to ``.c`` and annotated as C.
                              Per-file language: standard C++ extensions and
                              ``cpp_extensions`` -> cpp; ``.c`` -> ``language``
                              (``"auto"`` means c).

        Returns:
            JSON string with keys:

            * ``status``         — ``"ok"`` or ``"error"``.
            * ``total_files``    — number of source files discovered.
            * ``annotated``      — number of files newly annotated.
            * ``skipped``        — number of files already annotated or excluded.
            * ``errors``         — list of ``{"file": …, "error": …}`` dicts for
              any file that failed.
            * ``file_results``   — per-file summary dicts.
        """
        import json as _json

        ws = _ws(run_id)
        ann_dir = ws / "annotated"
        if not ann_dir.exists():
            return _err(f"annotated/ directory not found in workspace {run_id}")

        # Always-excluded path fragments
        _ALWAYS_EXCLUDE = (
            "/test/", "/tests/", "/vendor/", "/third_party/",
            "/CMakeFiles/", "/.git/",
        )
        extra_exclude = list(exclude_patterns) if exclude_patterns else []

        def _is_excluded(p: Path) -> bool:
            s = str(p)
            for pat in _ALWAYS_EXCLUDE:
                if pat in s:
                    return True
            for pat in extra_exclude:
                if pat in s:
                    return True
            return False

        # Discover source files
        # Extension matching is CASE-SENSITIVE: ".C" is C++, ".c" is C.
        CPP_EXTS = {".cpp", ".cxx", ".cc", ".c++", ".C", ".CPP", ".CXX", ".CC"}
        for e in (cpp_extensions or []):
            e = e if e.startswith(".") else "." + e
            CPP_EXTS.add(e)

        def _file_lang(p: Path) -> Optional[str]:
            name = p.name
            # longest match first so multi-dot extensions (".cu.cc") win
            for e in sorted(CPP_EXTS, key=len, reverse=True):
                if name.endswith(e) and len(name) > len(e):
                    return "cpp"
            if p.suffix == ".c":
                return "cpp" if language == "cpp" else "c"
            return None

        all_files = sorted(
            p for p in ann_dir.rglob("*")
            if p.is_file() and _file_lang(p) and not _is_excluded(p)
        )

        if not all_files:
            return _ok(
                "No source files found in annotated/.",
                total_files=0, annotated=0, skipped=0, errors=[], file_results=[],
            )

        # Detect entry-point files (contain "int main(")
        def _is_entry(p: Path) -> bool:
            try:
                text = p.read_text(errors="replace")
                # Any return type (`int`, `Eint32`, ...) before `main(`.
                if re.search(r'^\s*(?:\w+\s+)+main\s*\(', text, re.M):
                    return True
                # main defined through a macro: `#define MAIN_NAME main` +
                # `Eint32 MAIN_NAME(int argc, ...)` (Enzo).
                for m in re.finditer(r'#\s*define\s+(\w+)\s+main\b', text):
                    if re.search(r'^\s*(?:\w+\s+)+' + re.escape(m.group(1)) + r'\s*\(',
                                 text, re.M):
                        return True
                return False
            except OSError:
                return False

        regular_files = []
        entry_files = []
        for p in all_files:
            (entry_files if _is_entry(p) else regular_files).append(p)

        # Annotate in order: regular first, entry-points last
        file_results = []
        annotated_count = 0
        skipped_count = 0
        errors = []

        def _kwargs(p: Path) -> dict:
            return dict(
                run_id=run_id,
                filepath=str(p.relative_to(ann_dir)),
                language=_file_lang(p),
                is_entry=p in entry_files,
                init_args=init_args,
                compile_flags=compile_flags,
            )

        # Two phases on ONE process pool: every library file concurrently,
        # then (after all of them are written) every entry-point file
        # concurrently. Results come back in input order; a failing file is a
        # per-file error, never dropped.
        n_jobs = annotate_jobs(jobs, max(len(regular_files), len(entry_files)))
        phases = [[_kwargs(p) for p in regular_files], [_kwargs(p) for p in entry_files]]
        outs = run_file_pool(_annotate_file_worker, phases, n_jobs,
                             serial_fn=lambda kw: clang_annotate_file(**kw))
        raw_results: Dict[Path, object] = dict(zip(regular_files + entry_files, outs[0] + outs[1]))

        for p in regular_files + entry_files:
            rel = str(p.relative_to(ann_dir))
            try:
                raw = raw_results[p]
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
                        "skipped_functions": result.get("skipped", 0),
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
    def clang_write_annotated_file(run_id: str, filepath: str) -> str:
        """Flush the in-memory annotated file buffer to disk.

        Call this after a series of in-memory annotation operations
        (``clang_annotate_file``, ``clang_add_braces``, ``clang_insert_line``)
        to commit all changes with a single write.  If the file is not in the
        in-memory cache (e.g. ``clang_annotate_file`` has not been called yet
        for this file), returns an error.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys:
                * ``status``      — ``"ok"`` or ``"error"``.
                * ``message``     — human-readable outcome.
                * ``filepath``    — echoed input path.
                * ``total_lines`` — number of lines written to disk.
        """
        cache_key = (run_id, filepath)
        if cache_key not in _FILE_CACHE:
            return _err(
                f"No in-memory state for {filepath} — call clang_annotate_file first.",
                filepath=filepath,
            )
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        _cached_mtime, lines = _FILE_CACHE[cache_key]
        abs_path.write_text("\n".join(lines) + "\n")
        del _FILE_CACHE[cache_key]
        return _ok(
            f"Wrote {len(lines)} lines to {filepath}.",
            filepath=filepath,
            total_lines=len(lines),
        )

    @mcp.tool()
    def clang_estimate_function_cost(
        run_id: str,
        filepath: str,
        function_name: str,
    ) -> str:
        """Estimate the instrumentation value of a C/C++/Python function.

        Uses the clang ``-ast-dump=json`` AST (for C/C++) or Python's built-in
        ``ast`` module (for ``.py`` files) to count meaningful structural
        features — no regex or text scanning.

        .. note::
           The Python path here is weighted for HPC/POSIX/MPI code. On AI/ML
           Python it under-scores framework I/O (``torch.load``, ``h5py.File``),
           checkpointing (``state_dict``), ``DataLoader`` work, and
           ``torch.distributed`` collectives — they all look like ordinary calls
           worth 2 points. Use ``python_estimate_function_cost`` /
           ``python_estimate_file_costs`` for ML workloads instead.

        **What is measured (from the AST):**

        * **I/O syscalls** — ``CallExpr`` nodes whose callee is in the POSIX /
          stdio I/O set (``open``, ``read``, ``write``, ``fopen``, ``stat``,
          ``mmap``, …)
        * **MPI calls** — ``CallExpr`` nodes starting with ``MPI_`` / ``NCMPI_``
        * **Memory ops** — ``malloc``, ``calloc``, ``memcpy``, ``memset``, …
        * **Vendor FS** — callee prefixed with ``gpfs_``, ``beegfs_``, ``llapi_``,
          ``cuFile``, ``hdfs_``, ``daos_``, ``ceph_``, ``gfarm_``
        * **Loops** — ``ForStmt`` / ``WhileStmt`` / ``DoStmt`` (C) or
          ``ast.For`` / ``ast.While`` (Python)
        * **Branches** — ``IfStmt`` / ``SwitchStmt`` (C) or ``ast.If`` (Python)
        * **Call count** — total ``CallExpr`` / ``ast.Call`` nodes
        * **Node count** — total AST nodes as a body-size proxy

        Scoring weights: io×30, mpi×25, vendor×30, mem×15, loop×10, branch×3,
        call×2, body-size bonus (capped at 20).

        **Annotation decision:**

        * Lifecycle functions (``*_init``, ``*_final``, ``*_open``, …) →
          always ``annotate`` regardless of score (Rule R6).
        * Vendor FS calls present → always ``annotate`` (Rule R7).
        * Score ≥ ``ANNOTATION_SCORE_THRESHOLD`` (default 20) → ``annotate``.
        * Otherwise → ``skip``.

        Args:
            run_id:        Session identifier returned by ``session_create``.
            filepath:      Path relative to ``annotated/``.
            function_name: Exact name of the function to evaluate.

        Returns:
            JSON string with keys:

            * ``status``         — ``"ok"`` or ``"error"``.
            * ``function``       — echoed function name.
            * ``recommendation`` — ``"annotate"`` or ``"skip"``.
            * ``score``          — numeric cost estimate (higher = more work).
            * ``threshold``      — the score threshold used.
            * ``cost_info``      — full breakdown: ``io_calls``, ``mpi_calls``,
              ``mem_calls``, ``vendor_calls``, ``loop_count``, ``branch_count``,
              ``call_count``, ``node_count``.
            * ``override_reason``— non-empty when lifecycle / vendor rule fired.
        """
        from dftracer_agents.mcp_tools.tools.session.source_parser import extract_functions, ClangNotFoundError

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        try:
            fns = extract_functions(str(abs_path))
        except ClangNotFoundError as exc:
            return _err(str(exc), filepath=filepath, function=function_name)

        target = next((f for f in fns if f.get("name") == function_name), None)
        if target is None:
            return _err(
                f"Function '{function_name}' not found in {filepath}.",
                filepath=filepath,
                function=function_name,
            )

        cost = target.get("cost_info", {})
        score = cost.get("score", 0)

        # Determine recommendation and any override reason
        override_reason = ""
        if _LIFECYCLE_RE.search(function_name):
            recommendation = "annotate"
            override_reason = "lifecycle function (Rule R6)"
        elif _VENDOR_PREFIX_RE.match(function_name):
            recommendation = "annotate"
            override_reason = "vendor FS function (Rule R7)"
        elif cost.get("vendor_calls", 0) > 0:
            recommendation = "annotate"
            override_reason = "vendor FS call in body (Rule R7)"
        elif score >= ANNOTATION_SCORE_THRESHOLD:
            recommendation = "annotate"
        else:
            recommendation = "skip"

        return _ok(
            f"Function '{function_name}' scored {score} → {recommendation}"
            + (f" [{override_reason}]" if override_reason else "") + ".",
            function=function_name,
            filepath=filepath,
            recommendation=recommendation,
            score=score,
            threshold=ANNOTATION_SCORE_THRESHOLD,
            cost_info=cost,
            override_reason=override_reason,
        )

    @mcp.tool()
    def clang_syntax_check(
        run_id: str,
        filepath: str,
        language: str = "auto",
        extra_include_dirs: List[str] = None,
    ) -> str:
        """Check the syntax of an annotated source file using the real compiler front-end.

        Uses the actual language toolchain — no regex or shell pattern matching:

        * **C files**      — ``gcc -fsyntax-only`` (or ``clang -fsyntax-only``)
        * **C++ files**    — ``g++ -fsyntax-only -std=c++14``
        * **Python files** — Python's built-in ``ast.parse()`` (in-process, no
          subprocess)

        A dftracer macro stub header is automatically injected for C/C++ files
        so that dftracer annotations (``DFTRACER_C_FUNCTION_START``, etc.) do not
        block the syntax check before the real dftracer library is installed.

        The session's dftracer ``include/`` directory is added to the include
        path automatically when ``dftracer_install_prefix`` is recorded in
        session state.  MPI include paths are detected via
        ``mpicc --showme:incdirs``.

        Args:
            run_id:             Session identifier returned by ``session_create``.
            filepath:           Path to the file relative to the ``annotated/``
                                subfolder.
            language:           ``"c"``, ``"cpp"``, ``"python"``, or ``"auto"``
                                (default).  ``"auto"`` infers the language from
                                the file extension.
            extra_include_dirs: Optional additional ``-I`` paths injected into
                                the C/C++ compiler invocation.

        Returns:
            JSON string with keys:

            * ``status``   — always ``"ok"`` (errors are in ``passed``/``errors``).
            * ``passed``   — ``True`` if no syntax errors were found.
            * ``language`` — detected or supplied language string.
            * ``errors``   — list of compiler error lines; empty on success.
            * ``command``  — the compiler command that was run (C/C++ only).
        """
        import ast as _ast
        import os
        import subprocess
        import tempfile
        from dftracer_agents.mcp_tools.tools.session.workspace import _load_state

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}", filepath=filepath)

        # ── Detect language ───────────────────────────────────────────────────
        if language == "auto":
            suffix = abs_path.suffix
            if suffix == ".c":
                lang = "c"
            elif suffix in (".C", ".CC", ".CPP", ".CXX") or suffix.lower() in (".cpp", ".cxx", ".cc", ".c++"):
                lang = "cpp"
            elif suffix == ".py":
                lang = "python"
            else:
                return _err(
                    f"Cannot auto-detect language for extension '{abs_path.suffix}'"
                    " — supply language= explicitly.",
                    filepath=filepath,
                )
        else:
            lang = language.lower()
            if lang in ("c++", "cxx"):
                lang = "cpp"

        # ── Python: ast.parse() in-process (no subprocess, no regex) ─────────
        if lang == "python":
            source = abs_path.read_text(errors="replace")
            try:
                _ast.parse(source, filename=str(abs_path))
                return _ok(
                    f"Python syntax OK: {filepath}",
                    filepath=filepath,
                    passed=True,
                    language="python",
                    errors=[],
                    command="ast.parse()",
                )
            except SyntaxError as exc:
                err_msg = f"{abs_path}:{exc.lineno}: SyntaxError: {exc.msg}"
                return _ok(
                    f"Python syntax error in {filepath}",
                    filepath=filepath,
                    passed=False,
                    language="python",
                    errors=[err_msg],
                    command="ast.parse()",
                )

        # ── C / C++: clang/gcc -fsyntax-only ─────────────────────────────────
        # Write a dftracer stub header to a temp file so that dftracer macros
        # in the annotated file compile cleanly even without the real library.
        DFTRACER_STUB = (
            "#ifndef _DFTRACER_SYNTAX_STUB_H\n"
            "#define _DFTRACER_SYNTAX_STUB_H\n"
            "#define DFTRACER_C_INIT(a,b,c)              do{}while(0)\n"
            "#define DFTRACER_C_FINI()                   do{}while(0)\n"
            "#define DFTRACER_C_FUNCTION_START()         do{}while(0)\n"
            "#define DFTRACER_C_FUNCTION_END()           do{}while(0)\n"
            "#define DFTRACER_C_FUNCTION_UPDATE_STR(k,v) do{}while(0)\n"
            "#define DFTRACER_C_FUNCTION_UPDATE_INT(k,v) do{}while(0)\n"
            "#define DFTRACER_CPP_INIT(a,b,c)            do{}while(0)\n"
            "#define DFTRACER_CPP_FINI()                 do{}while(0)\n"
            "#define DFTRACER_CPP_FUNCTION()             do{}while(0)\n"
            "#define DFTRACER_CPP_FUNCTION_UPDATE(k,v)   do{}while(0)\n"
            "#define DFTRACER_CPP_REGION_START(n)        do{}while(0)\n"
            "#define DFTRACER_CPP_REGION_END(n)          do{}while(0)\n"
            "#endif\n"
        )

        # ── Load session state once (used for compiler + includes + defines) ──
        _state: dict = {}
        try:
            _state = _load_state(run_id)
        except Exception:
            pass

        _features   = _state.get("detection", {}).get("features", {})
        _has_mpi    = bool(_features.get("mpi", False))
        _build_tool = _state.get("build_tool", "")

        # Use mpicc when the session detects MPI — it already embeds all MPI
        # include paths, so we skip the separate --showme:incdirs step.
        if _has_mpi and lang != "cpp":
            compiler  = "mpicc"
            lang_flag = []           # mpicc infers C; -x c causes errors with some wrappers
        elif lang == "cpp":
            compiler  = "mpicxx" if _has_mpi else "g++"
            lang_flag = []           # mpicxx/g++ infer C++ from extension
        else:
            compiler  = "gcc"
            lang_flag = ["-x", "c"]

        std_flag = ["-std=c++14"] if lang == "cpp" else []

        # ── Collect include paths ─────────────────────────────────────────────
        include_dirs: List[str] = list(extra_include_dirs) if extra_include_dirs else []

        # dftracer install include dir
        _prefix = _state.get("dftracer_install_prefix", "")
        if _prefix:
            _inc = os.path.join(_prefix, "include")
            if os.path.isdir(_inc):
                include_dirs.append(_inc)

        # build_ann/src and build/src — contain config.h generated by autotools
        for _bd in ("build_ann/src", "build/src"):
            _bd_path = ws / _bd
            if _bd_path.exists():
                include_dirs.append(str(_bd_path))

        # annotated/<dir-of-file> and annotated/src — project headers
        _ann_file_dir = ws / "annotated" / os.path.dirname(filepath)
        if _ann_file_dir.exists() and str(_ann_file_dir) not in include_dirs:
            include_dirs.append(str(_ann_file_dir))
        _ann_src = ws / "annotated" / "src"
        if _ann_src.exists() and str(_ann_src) not in include_dirs:
            include_dirs.append(str(_ann_src))

        # MPI includes (only when NOT using mpicc/mpicxx as compiler)
        if not _has_mpi:
            try:
                mpi_r = subprocess.run(
                    ["mpicc", "--showme:incdirs"],
                    capture_output=True, text=True, timeout=5,
                )
                if mpi_r.returncode == 0:
                    for d in mpi_r.stdout.strip().split():
                        if os.path.isdir(d):
                            include_dirs.append(d)
            except Exception:
                pass

        inc_flags = [f"-I{d}" for d in include_dirs]

        # ── Extra preprocessor defines ────────────────────────────────────────
        # autotools projects define HAVE_CONFIG_H so config.h is pulled in
        define_flags = ["-DHAVE_CONFIG_H"] if _build_tool == "autotools" else []

        stub_fd, stub_path = tempfile.mkstemp(suffix=".h", prefix="dftracer_stub_")
        try:
            with os.fdopen(stub_fd, "w") as fh:
                fh.write(DFTRACER_STUB)

            cmd = (
                [compiler]
                + lang_flag
                + ["-fsyntax-only", "-w"]
                + std_flag
                + define_flags
                + ["-include", stub_path]
                + inc_flags
                + [str(abs_path)]
            )
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=30,
            )
        finally:
            try:
                os.unlink(stub_path)
            except OSError:
                pass

        # Collect meaningful diagnostics; filter out stub-header noise
        combined = "\n".join(filter(None, [result.stdout, result.stderr])).strip()
        error_lines = [
            ln for ln in combined.splitlines()
            if ln.strip() and "dftracer_stub_" not in ln
        ]

        passed = result.returncode == 0
        msg = (
            f"{lang.upper()} syntax OK: {filepath}"
            if passed
            else f"{lang.upper()} syntax error(s) in {filepath}"
        )
        return _ok(
            msg,
            filepath=filepath,
            passed=passed,
            language=lang,
            errors=error_lines,
            command=" ".join(cmd),
        )

    @mcp.tool()
    def clang_fix_header_tentative_defs(run_id: str, filepath: str) -> str:
        """Detect and fix bare global variable declarations in C/C++ header files.

        GCC 10+ changed the default from ``-fcommon`` to ``-fno-common``.
        Bare global declarations in headers included by multiple ``.c`` files
        become *tentative definitions* in every translation unit, causing
        ``multiple definition`` linker errors under the new default.

        This tool scans the header at global scope (brace-depth zero) for
        declarations that are not already qualified with ``extern``, ``static``,
        or ``typedef``, and rewrites them by prepending ``extern``.

        Example::

            // Before (tentative definition — breaks with GCC 10+ -fno-common):
            ior_aiori_t posix_aiori;

            // After (forward declaration — safe in headers):
            extern ior_aiori_t posix_aiori;

        The actual initialized definitions must remain in exactly one ``.c`` file.

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path to the header file relative to ``annotated/``.

        Returns:
            JSON string with keys:
                * ``status``   — ``"ok"`` or ``"error"``.
                * ``message``  — human-readable summary.
                * ``filepath`` — echoed input path.
                * ``modified`` — ``True`` if the file was changed.
                * ``fixes``    — list of ``{"line": N, "before": "...", "after": "..."}``
                  dicts describing each rewritten line.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        text = abs_path.read_text(errors="replace")
        lines = text.splitlines(keepends=True)

        # Keywords that disqualify a line from being a tentative definition
        _SKIP = frozenset((
            "extern", "static", "typedef", "struct", "union", "enum",
            "const", "volatile", "inline", "register",
            "#", "//", "/*", "*", "}", "{",
            "if", "else", "for", "while", "do", "return", "switch", "case",
            "break", "continue", "goto", "sizeof", "void",
        ))

        fixes = []
        depth = 0

        for i, raw_line in enumerate(lines):
            stripped = raw_line.strip()

            # Track brace depth to stay at global scope
            depth += stripped.count("{") - stripped.count("}")
            depth = max(0, depth)

            if depth != 0:
                continue
            if not stripped or not stripped.endswith(";"):
                continue
            # Skip lines with parentheses → function declarations/calls
            if "(" in stripped:
                continue
            # Skip lines beginning with known non-declaration tokens
            first = stripped.split()[0] if stripped.split() else ""
            if first in _SKIP or first.startswith("#") or first.startswith("//") or first.startswith("/*"):
                continue

            # Require at least two words before the semicolon (type + identifier)
            tokens = stripped.rstrip(";").split()
            if len(tokens) < 2:
                continue

            # Already has extern? skip
            if "extern" in tokens:
                continue

            # Rewrite: prepend "extern " respecting the original indentation
            leading = raw_line[: len(raw_line) - len(raw_line.lstrip())]
            new_line = leading + "extern " + stripped + "\n"
            fixes.append({
                "line": i + 1,
                "before": raw_line.rstrip("\n"),
                "after":  new_line.rstrip("\n"),
            })
            lines[i] = new_line

        if fixes:
            abs_path.write_text("".join(lines))

        return _ok(
            f"{'Fixed' if fixes else 'No changes in'} {filepath}: "
            f"{len(fixes)} tentative definition(s) rewritten as extern.",
            filepath=filepath,
            modified=bool(fixes),
            fixes=fixes,
        )

    @mcp.tool()
    def clang_lint_annotations(run_id: str, filepath: str) -> str:
        """Lint an annotated C file for dftracer macro ordering violations.

        Checks each function body for the following rules:

        * **L1 — INIT before START**: In ``main()``, ``DFTRACER_C_INIT`` must
          appear before ``DFTRACER_C_FUNCTION_START``.
        * **L2 — comp= UPDATE after START**: Every ``DFTRACER_C_FUNCTION_START``
          must be immediately followed (within 3 lines) by a
          ``DFTRACER_C_FUNCTION_UPDATE_STR("comp", …)`` call.
        * **L3 — FINI before MPI_Finalize**: In ``main()``,
          ``DFTRACER_C_FINI`` must appear before ``MPI_Finalize``.
        * **L4 — no END before MPI_CHECK**: ``DFTRACER_C_FUNCTION_END`` must
          not appear immediately before a ``MPI_CHECK`` or ``NCMPI_CHECK`` macro
          (those macros hide a ``return`` — adding END there double-ends the span).
        * **L5 — no END outside a function**: ``DFTRACER_C_FUNCTION_END`` lines
          must not appear at global scope (brace depth 0).

        Args:
            run_id:   Session identifier returned by ``session_create``.
            filepath: Path to the file relative to the ``annotated/`` subfolder.

        Returns:
            JSON string with keys:
                * ``status``     — ``"ok"`` (always; violations are in ``issues``).
                * ``passed``     — ``True`` when no issues were found.
                * ``issues``     — list of ``{"rule": "L1", "line": N,
                  "message": "..."}`` dicts.
                * ``issue_count``— total number of violations.
        """
        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        text = abs_path.read_text(errors="replace")
        lines = text.splitlines()
        issues = []

        # -- L1 & L3: scan for MPI boundary and INIT/FINI order in main() ----
        in_main = False
        main_depth = 0
        depth = 0
        init_line = None
        start_line = None
        fini_line = None
        mpi_finalize_line = None

        for i, ln in enumerate(lines, start=1):
            s = ln.strip()
            depth += s.count("{") - s.count("}")
            depth = max(0, depth)

            if re.search(r'\bint\s+main\s*\(', s):
                in_main = True
                main_depth = depth + s.count("{") - s.count("}")

            if in_main:
                if "DFTRACER_C_INIT" in s and init_line is None:
                    init_line = i
                if "DFTRACER_C_FUNCTION_START" in s and start_line is None:
                    start_line = i
                if "DFTRACER_C_FINI" in s and fini_line is None:
                    fini_line = i
                if re.search(r'\bMPI_Finalize\s*\(', s) and mpi_finalize_line is None:
                    mpi_finalize_line = i
                # Exit main when depth returns to pre-main level
                if depth < main_depth and s.startswith("}"):
                    in_main = False

        if init_line is not None and start_line is not None:
            if init_line > start_line:
                issues.append({
                    "rule": "L1",
                    "line": init_line,
                    "message": (
                        f"DFTRACER_C_INIT (line {init_line}) appears after "
                        f"DFTRACER_C_FUNCTION_START (line {start_line}) — "
                        "INIT must precede START"
                    ),
                })

        if fini_line is not None and mpi_finalize_line is not None:
            if fini_line > mpi_finalize_line:
                issues.append({
                    "rule": "L3",
                    "line": fini_line,
                    "message": (
                        f"DFTRACER_C_FINI (line {fini_line}) appears after "
                        f"MPI_Finalize (line {mpi_finalize_line}) — "
                        "FINI must precede MPI_Finalize"
                    ),
                })

        # -- L2: comp= UPDATE within 3 lines of every START ------------------
        for i, ln in enumerate(lines, start=1):
            if "DFTRACER_C_FUNCTION_START" in ln:
                window = lines[i : i + 3]  # next 3 lines (0-based i = 1-based i+1..i+3)
                if not any(
                    "DFTRACER_C_FUNCTION_UPDATE_STR" in wl and '"comp"' in wl
                    for wl in window
                ):
                    issues.append({
                        "rule": "L2",
                        "line": i,
                        "message": (
                            f"DFTRACER_C_FUNCTION_START at line {i} is not followed "
                            "by a comp= UPDATE_STR within 3 lines"
                        ),
                    })

        # -- L4: END immediately before MPI_CHECK / NCMPI_CHECK --------------
        for i, ln in enumerate(lines):
            if "DFTRACER_C_FUNCTION_END" in ln:
                next_ln = lines[i + 1].strip() if i + 1 < len(lines) else ""
                if re.search(r'\b(MPI_CHECK|NCMPI_CHECK|HGOTO_ERROR)\s*\(', next_ln):
                    issues.append({
                        "rule": "L4",
                        "line": i + 1,
                        "message": (
                            f"DFTRACER_C_FUNCTION_END at line {i + 1} immediately "
                            f"precedes {next_ln[:60].strip()} at line {i + 2} — "
                            "MPI_CHECK hides a return; do not add END before it"
                        ),
                    })

        # -- L5: END at global scope ------------------------------------------
        depth = 0
        for i, ln in enumerate(lines, start=1):
            s = ln.strip()
            depth += s.count("{") - s.count("}")
            depth = max(0, depth)
            if depth == 0 and "DFTRACER_C_FUNCTION_END" in s:
                issues.append({
                    "rule": "L5",
                    "line": i,
                    "message": (
                        f"DFTRACER_C_FUNCTION_END at line {i} appears at global "
                        "scope (brace depth 0) — END must be inside a function body"
                    ),
                })

        passed = len(issues) == 0
        return _ok(
            f"{'No issues' if passed else f'{len(issues)} issue(s)'} in {filepath}.",
            filepath=filepath,
            passed=passed,
            issues=issues,
            issue_count=len(issues),
        )

    @mcp.tool()
    def clang_regression_test(
        run_id: str,
        filepath: str,
        language: str = "c",
        is_entry: bool = False,
        init_args: str = "NULL, NULL, NULL",
    ) -> str:
        """Strip annotations from a file, re-annotate, and compare with current state.

        Useful for verifying that ``clang_annotate_file`` produces output
        equivalent to a manually annotated file, and for regression-testing
        tool changes.

        The comparison is line-count and macro-count based (not a full diff),
        so minor indentation differences do not trigger false positives.

        Steps:

        1. Read the current annotated file.
        2. Strip all dftracer macro lines and the ``#include <dftracer/dftracer.h>``
           line to produce a clean baseline.
        3. Write the baseline to a temp file, run ``clang_annotate_file`` on it,
           capture the result.
        4. Compare macro counts between the original and the re-annotated version.
        5. Restore the original file to disk unchanged.

        Args:
            run_id:    Session identifier returned by ``session_create``.
            filepath:  Path relative to ``annotated/``.
            language:  ``"c"`` or ``"cpp"``.
            is_entry:  Pass ``True`` when the file contains ``main()``.
            init_args: INIT argument string (forwarded to ``clang_annotate_file``).

        Returns:
            JSON string with keys:
                * ``status``         — ``"ok"`` or ``"error"``.
                * ``passed``         — ``True`` if macro counts match.
                * ``original_macros``— macro line counts in the current file.
                * ``reannotated_macros``— macro counts after re-annotation.
                * ``discrepancies``  — list of macro names whose counts differ.
        """
        import tempfile, shutil as _shutil, json as _json

        ws = _ws(run_id)
        abs_path = ws / "annotated" / filepath
        if not abs_path.exists():
            return _err(f"File not found in annotated/: {filepath}")

        original_text = abs_path.read_text(errors="replace")

        # ── Step 1: strip all dftracer lines from original ───────────────────
        _DFTRACER_LINE_RE = re.compile(
            r'^\s*(DFTRACER_C_(?:FUNCTION_START|FUNCTION_END|'
            r'FUNCTION_UPDATE_STR|FUNCTION_UPDATE_INT|INIT|FINI)'
            r'|DFTRACER_CPP_(?:FUNCTION|FUNCTION_UPDATE|INIT|FINI|'
            r'REGION_START|REGION_END)'
            r'|#\s*include\s*<dftracer/dftracer\.h>)'
        )
        stripped_lines = [
            ln for ln in original_text.splitlines(keepends=True)
            if not _DFTRACER_LINE_RE.match(ln)
        ]
        stripped_text = "".join(stripped_lines)

        # ── Step 2: write stripped version to temp file ──────────────────────
        suffix = abs_path.suffix
        tmp = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=suffix, dir=abs_path.parent,
                delete=False, encoding="utf-8",
                prefix=abs_path.stem + "_regression_",
            ) as f:
                f.write(stripped_text)
                tmp = Path(f.name)

            # Temporarily replace the annotated file with the stripped version
            _shutil.copy2(abs_path, str(abs_path) + ".orig_bak")
            abs_path.write_text(stripped_text)

            # ── Step 3: re-annotate ──────────────────────────────────────────
            raw = clang_annotate_file(
                run_id=run_id,
                filepath=filepath,
                language=language,
                is_entry=is_entry,
                init_args=init_args,
            )
            reannotated_text = abs_path.read_text(errors="replace")

        finally:
            # Restore original file
            bak = Path(str(abs_path) + ".orig_bak")
            if bak.exists():
                abs_path.write_text(original_text)
                bak.unlink()
            if tmp and tmp.exists():
                tmp.unlink()
            # Clear in-memory cache so subsequent calls see the restored file
            _FILE_CACHE.pop((run_id, filepath), None)

        # ── Step 4: count macros in original vs re-annotated ─────────────────
        _MACRO_NAMES = [
            "DFTRACER_C_FUNCTION_START",
            "DFTRACER_C_FUNCTION_END",
            "DFTRACER_C_FUNCTION_UPDATE_STR",
            "DFTRACER_C_FUNCTION_UPDATE_INT",
            "DFTRACER_C_INIT",
            "DFTRACER_C_FINI",
            "DFTRACER_CPP_FUNCTION",
            "DFTRACER_CPP_FUNCTION_UPDATE",
            "#include <dftracer/dftracer.h>",
        ]

        def _count(text: str) -> dict:
            return {m: text.count(m) for m in _MACRO_NAMES if text.count(m) > 0}

        original_macros    = _count(original_text)
        reannotated_macros = _count(reannotated_text)
        all_keys = set(original_macros) | set(reannotated_macros)
        discrepancies = [
            {
                "macro": k,
                "original": original_macros.get(k, 0),
                "reannotated": reannotated_macros.get(k, 0),
            }
            for k in sorted(all_keys)
            if original_macros.get(k, 0) != reannotated_macros.get(k, 0)
        ]

        passed = len(discrepancies) == 0
        return _ok(
            f"Regression {'PASSED' if passed else 'FAILED'}: {filepath} — "
            f"{len(discrepancies)} macro count discrepanc{'y' if len(discrepancies)==1 else 'ies'}.",
            filepath=filepath,
            passed=passed,
            original_macros=original_macros,
            reannotated_macros=reannotated_macros,
            discrepancies=discrepancies,
        )

    @mcp.tool()
    def dftracer_annotate_clang_check(path: str = "", source: str = "", language: str = "auto", compiler: str = "",
                                      extra_include_dirs: List[str] = None,
            extra_flags: List[str] = None) -> dict:
        """Validator for dftracer_annotate_clang: rule deterministically on what the clang_* tools wrote (never edits).

        Give `path` (an annotated C or C++ file, or a tree: every C/C++ source and header under it) or `source` (one
        file's text, written to a temp dir; `language` c|cpp says which, default c). Per language it runs the same
        checks as dftracer_annotate_c_check / dftracer_annotate_cpp_check: compiles with -fsyntax-only against the
        dftracer macro stub (`compiler`, `extra_include_dirs` as in clang_syntax_check, `extra_flags` -D/-U/-std/-I flags such as -DHAVE_CONFIG_H); dftracer include present; no
        macros in headers; every START/FUNCTION followed first by a comp UPDATE and only one per function; C: END
        before every visible return (a braceless `if (x) return` fails: run clang_add_braces); main has INIT and FINI
        (C++ main uses REGION, not FUNCTION); critical I/O calls sit in annotated functions.

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], files}.
        """
        with _vtempfile.TemporaryDirectory() as tmp:
            lang_in = language if language in ("c", "cpp", "c++") else "auto"
            files, err = _vfiles(path, source, "cpp" if lang_in in ("cpp", "c++") else ("c" if source else "auto"), tmp)
            files = [f for f in files if _vlang(f) in ("c", "cpp") or f.suffix.lower() in _V_HDRS]
            if not files:
                return _verdict([_vchk("input", False, err or "no C/C++ files", "pass an annotated C/C++ file or tree, or its source text")])
            checks = []
            c_files = [f for f in files if _vlang(f) == "c" or f.suffix.lower() == ".h"]
            cpp_files = [f for f in files if f not in c_files]
            if c_files:
                checks += _vc_like_checks(c_files, "c", compiler, extra_include_dirs, extra_flags=extra_flags)
            if cpp_files:
                checks += _vc_like_checks(cpp_files, "cpp", compiler, extra_include_dirs, extra_flags=extra_flags)
            return _verdict(checks, files=[f.name for f in files])
