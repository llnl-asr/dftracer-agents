"""dftracer_annotate_ml: one-call ML/DL project annotation (ml_categorize_files, ml_annotate_plan, ml_annotate_project) and the validator dftracer_annotate_ml_check. Tools moved unchanged from dftracer_agents.mcp_tools.tools.session.ml_pipeline."""

from __future__ import annotations

import json as _json
from dftracer_agents.mcp_tools.tools.session.annotation_ai import (
    _python_annotate_ai_file_impl,
)
from dftracer_agents.mcp_tools.tools.session.annotation_python import (
    _python_annotate_file_impl,
)
from dftracer_agents.mcp_tools.tools.session.annotation_validate import (
    _validate_impl,
)
from dftracer_agents.mcp_tools.tools.session.ml_pipeline import (
    _DEFAULT_EXCLUDES,
    _categorize,
    _plan,
)
from dftracer_agents.mcp_tools.tools.session.python_cost import (
    PY_ANNOTATION_SCORE_THRESHOLD,
)
from dftracer_agents.mcp_tools.tools.session.workspace import (
    _err,
    _ok,
)
from pathlib import (
    Path,
)
from typing import (
    Any,
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


from dftracer_agents.mcp_tools.tools.session.ml_pipeline import _DEFAULT_EXCLUDES, _bucket_of, _strip_noncode

# What each ML bucket (ml_categorize_files) must carry once annotated (dftracer-ml-annotate 4a-4f).
_ML_BUCKET_NEEDS = {
    "train": (("dft_ai.pipeline", "dft_ai.compute", "dft_ai"), "a dft_ai.pipeline.* / dft_ai.compute.* region on the training loop or step"),
    "data": (("dft_ai.data", "dft_ai.dataloader"), "dft_ai.data.item on __getitem__ (or a dft_ai.data/dataloader region)"),
    "ckpt": (("dft_ai.checkpoint",), "dft_ai.checkpoint.capture / .restart on the save / load functions"),
    "comm": (("dft_ai.comm",), "a dft_ai.comm.* region on the collective calls"),
}


def register(mcp):
    """Register the dftracer_annotate_ml tools on *mcp*."""

    @mcp.tool()
    def ml_categorize_files(run_id: str, exclude: str = "",
                            annotated_dir: str = "annotated") -> str:
        """Bucket every Python file into entry/train/data/ckpt/comm/other.

        Deterministic replacement for ``dftracer-ml-annotate`` Step 3b. A file
        lands in the FIRST bucket it matches (entry > ckpt > data > comm >
        train), because a training script that also loads data is still a
        training script.

        Args:
            run_id: Session identifier returned by ``session_create``.
            exclude: Comma-separated extra path parts to skip.

        Returns:
            JSON with ``root`` and ``buckets`` (bucket name -> file list).
        """
        ex = _DEFAULT_EXCLUDES + tuple(x.strip() for x in exclude.split(",") if x.strip())
        res = _categorize(run_id, ex, annotated_dir)
        if "error" in res:
            return _err(res["error"])
        counts = {k: len(v) for k, v in res["buckets"].items()}
        return _ok(f"categorized {sum(counts.values())} python files", counts=counts, **res)

    @mcp.tool()
    def ml_annotate_plan(
        run_id: str,
        threshold: int = PY_ANNOTATION_SCORE_THRESHOLD,
        exclude: str = "",
        annotated_dir: str = "annotated",
    ) -> str:
        """Compute the full ML annotation plan WITHOUT writing anything.

        Combines categorization (4a-4f) with cost-gating of the leftover files
        (4g, via ``python_estimate_file_costs``). Review this before running
        ``ml_annotate_project``.

        Semantic buckets are marked ``cost_gated: false`` — they are always
        annotated. Only ``other`` files carry per-function annotate/skip lists.

        Args:
            run_id: Session identifier.
            threshold: Generic-function score threshold (see ``python_cost``).
            exclude: Comma-separated extra path parts to skip.

        Returns:
            JSON with ``buckets``, ``threshold``, and ``plan`` (one entry per
            file: ``tool``, ``bucket``, ``cost_gated``, ``why``, and for gated
            files ``annotate_functions`` / ``skip_functions``).
        """
        ex = _DEFAULT_EXCLUDES + tuple(x.strip() for x in exclude.split(",") if x.strip())
        res = _plan(run_id, threshold, ex, annotated_dir)
        if "error" in res:
            return _err(res["error"])
        acts = [p for p in res["plan"] if p.get("tool")]
        return _ok(f"plan: {len(acts)} file(s) to annotate, "
                   f"{len(res['plan']) - len(acts)} skipped", **res)

    @mcp.tool()
    def ml_annotate_project(
        run_id: str,
        threshold: int = PY_ANNOTATION_SCORE_THRESHOLD,
        exclude: str = "",
        logfile: str = "None",
        data_dir: str = "None",
        dry_run: bool = False,
        annotated_dir: str = "annotated",
        validate: bool = True,
    ) -> str:
        """Annotate an entire ML project in ONE call (fast path).

        Executes ``ml_annotate_plan``: semantic files go through
        ``python_annotate_ai_file`` (AI/ML region decorators, loop wrapping,
        ``initialize_log``/``finalize`` for entry points); leftover files go
        through ``python_annotate_file`` but only when the AI/ML cost estimator
        found a function worth instrumenting.

        Use the ``dftracer-ml-annotate`` prose recipe as the BACKUP path: when a
        file needs human judgement, when this tool errors, or to repair a file.

        Args:
            run_id: Session identifier.
            threshold: Generic-function score threshold.
            exclude: Comma-separated extra path parts to skip.
            logfile: ``initialize_log(logfile=...)`` for entry points.
            data_dir: ``initialize_log(data_dir=...)`` for entry points.
            dry_run: Compute the plan and report, but write nothing.

        Returns:
            JSON with ``annotated`` (per-file result summaries), ``skipped``,
            ``failed``, and the ``counts`` per bucket.
        """
        ex = _DEFAULT_EXCLUDES + tuple(x.strip() for x in exclude.split(",") if x.strip())
        res = _plan(run_id, threshold, ex, annotated_dir)
        if "error" in res:
            return _err(res["error"])
        if dry_run:
            return _ok("dry run — nothing written", **res)

        annotated: List[Dict[str, Any]] = []
        skipped: List[str] = []
        failed: List[Dict[str, str]] = []

        for step in res["plan"]:
            fp, tool = step["filepath"], step.get("tool")
            if not tool:
                skipped.append(fp)
                continue
            cat = Path(fp).stem
            try:
                if tool == "python_annotate_ai_file":
                    out = _python_annotate_ai_file_impl(
                        run_id=run_id, filepath=fp, category=cat,
                        is_entry=step["is_entry"], logfile=logfile, data_dir=data_dir,
                        annotate_loops=step["annotate_loops"],
                        annotated_dir=annotated_dir,
                        only_functions=",".join(step.get("annotate_functions", [])),
                    )
                else:
                    # Enforce the cost gate: decorate ONLY the functions the
                    # AI/ML estimator selected, not every function in the file.
                    out = _python_annotate_file_impl(
                        run_id=run_id, filepath=fp, category=cat, is_entry=False,
                        logfile=logfile, data_dir=data_dir,
                        only_functions=",".join(step.get("annotate_functions", [])),
                        annotated_dir=annotated_dir,
                    )
                parsed = _json.loads(out)
                if parsed.get("status") == "ok":
                    annotated.append({
                        "filepath": fp, "bucket": step["bucket"], "tool": tool,
                        "decorators": parsed.get("decorators", parsed.get("functions", 0)),
                        "with_regions": parsed.get("with_regions", 0),
                        "skipped_static": parsed.get("skipped_static", []) or [],
                        "message": parsed.get("message", ""),
                    })
                else:
                    failed.append({"filepath": fp, "error": parsed.get("message", "unknown")})
            except Exception as exc:  # a bad file must not abort the project
                failed.append({"filepath": fp, "error": f"{type(exc).__name__}: {exc}"})

        # Validation is part of the pipeline: an annotation run that produces
        # unparseable files or misses a checkpoint writer is a failed run, and the
        # caller must learn that here rather than at build time.
        validation = None
        if validate:
            validation = _json.loads(
                _validate_impl(run_id, "python", "", annotated_dir))

        total_regions = sum(a["with_regions"] for a in annotated)
        all_skipped_static = [s for a in annotated for s in a["skipped_static"]]

        msg = (f"annotated {len(annotated)} file(s) "
               f"({total_regions} contextual `with` region(s)), "
               f"skipped {len(skipped)}, failed {len(failed)}")
        if all_skipped_static:
            msg += (f"; {len(all_skipped_static)} static method(s) left for manual "
                    f"annotation (multi-line string bodies)")
        if validation is not None:
            msg += (f"; validation {'PASSED' if validation.get('passed') else 'FAILED'} "
                    f"({validation.get('total_findings', 0)} finding(s))")
        return _ok(
            msg,
            counts={k: len(v) for k, v in res["buckets"].items()},
            threshold=threshold, annotated=annotated, skipped=skipped, failed=failed,
            with_regions=total_regions, skipped_static=all_skipped_static,
            validation=validation,
        )

    @mcp.tool()
    def dftracer_annotate_ml_check(path: str = "", source: str = "", entry: bool = False) -> dict:
        """Validator for dftracer_annotate_ml: rule deterministically on an ML/DL tree annotated by ml_annotate_project.

        Give `path` (the annotated tree or one .py file; ml_categorize_files' excludes such as tests/, docs/, setup.py are
        skipped) or `source` (one file's text, written to a temp dir).
        Each file is bucketed with ml_categorize_files' own classifier (entry/train/data/ckpt/comm/other) and must
        carry what its bucket needs: train -> a dft_ai.pipeline/compute region, data -> dft_ai.data.item on
        __getitem__ (or a data/dataloader region), ckpt -> dft_ai.checkpoint.*, comm -> dft_ai.comm.*. A DataLoader
        built with num_workers needs a worker_init_fn (Python Rule 2b: workers need their own init/finalize). Plus the
        Python checks of dftracer_annotate_python_check (parses, coverage and cost gate, no decorator on a property,
        initialize_log/finalize in the entry file or when `entry`).

        Returns {status: pass|fail|inconclusive, checks: [{check, ok, detail, fix}], buckets}.
        """
        with _vtempfile.TemporaryDirectory() as tmp:
            files, err = _vfiles(path, source, "python", tmp, excludes=_DEFAULT_EXCLUDES)
            if not files:
                return _verdict([_vchk("input", False, err, "pass the annotated tree, a .py file, or its source text")])
            checks = _vpy_checks(files, need_entry=entry)
            buckets = {}
            for f in files:
                text = f.read_text(errors="replace")
                code = _strip_noncode(text)
                b = _bucket_of(code)
                buckets[f.name] = b
                if b in _ML_BUCKET_NEEDS:
                    toks, what = _ML_BUCKET_NEEDS[b]
                    ok = any(t + "." in code or t + "(" in code or "@" + t in code or (t == "dft_ai" and "@dft_ai\n" in code)
                             for t in toks)
                    checks.append(_vchk(f"{f.name} ({b}): ML region present", ok, "",
                                        "" if ok else f"needs {what}: python_annotate_ai_file(category='{b}') or ml_annotate_project"))
                if _vre.search(r"DataLoader\s*\(", code) and _vre.search(r"num_workers\s*=", code):
                    w = "worker_init_fn" in code
                    checks.append(_vchk(f"{f.name}: DataLoader workers traced", w, "",
                                        "" if w else "pass a module-level worker_init_fn that calls dftracer.initialize_log (Python Rule 2b)"))
            return _verdict(checks, buckets=buckets)
