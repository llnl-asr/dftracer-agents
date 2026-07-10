"""App-parameter metadata emission and annotation-coverage validation.

Two jobs that every annotation run needs and that were previously left to prose:

1. **App parameters as metadata events.** A trace is far more useful when the
   run's own parameters (rank count, batch size, block size, checkpoint
   interval, …) are attached to it. ``annotate_add_app_metadata`` emits one
   metadata event per parameter, in the right dialect for C / C++ / Python.

2. **Coverage validation.** ``validate_annotations`` re-parses the annotated
   tree and reports what a typical app flow requires but is missing: an
   init/fini pair, metadata events, instrumentation on the I/O and
   checkpoint paths, and ``comp=`` types on C/C++ macros.

Python metadata note: ``dftracer_get_metadata_api`` used to claim Python had no
metadata API. It does — ``dftracer.log_metadata_event(key, value)`` on the object
returned by ``dftracer.initialize_log(...)`` (see
``dftracer/python/common.py``). That is what this module emits.
"""
from __future__ import annotations

import ast
import json as _json
import re
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastmcp import FastMCP

from .workspace import _ws, _ok, _err
from .python_cost import recommend as _cost_recommend, PY_ANNOTATION_SCORE_THRESHOLD


# --------------------------------------------------------------------------
# Metadata emission
# --------------------------------------------------------------------------

#: DFTRACER_C_METADATA / DFTRACER_CPP_METADATA both use ``##name`` token-pasting
#: (see dftracer/include/dftracer/dftracer.h) to declare a local variable, so the
#: macro signature is 3-arg: ``DFTRACER_C_METADATA(name, key, val)`` /
#: ``DFTRACER_CPP_METADATA(name, key, value)``, where ``name`` is a bare C
#: identifier (NOT a string literal) that must be unique within its scope.
_C_IDENT_SANITIZE_RE = re.compile(r"[^0-9A-Za-z_]+")


def _c_metadata_identifier(key: str, seen: set) -> str:
    """Derive a unique, valid C identifier for a METADATA macro's ``name`` slot.

    Prefixed with ``dft_meta_`` to avoid colliding with user variables; suffixed
    with a numeric counter if the sanitized key repeats (e.g. two params that
    only differ in punctuation) or is empty/starts with a digit.
    """
    base = _C_IDENT_SANITIZE_RE.sub("_", key).strip("_").lower()
    if not base or base[0].isdigit():
        base = f"p_{base}" if base else "p"
    ident = f"dft_meta_{base}"
    final = ident
    n = 1
    while final in seen:
        n += 1
        final = f"{ident}_{n}"
    seen.add(final)
    return final


def _metadata_line(language: str, key: str, value: str, indent: str,
                   expression: bool = False, seen_idents: set = None) -> str:
    """Render one metadata event.

    When *expression* is True the value is emitted as code rather than a quoted
    literal — required for runtime parameters (``args.batch_size``), which are
    only known after argument parsing and would otherwise be recorded as the
    literal text ``"args.batch_size"``.
    """
    lang = language.lower()
    if lang == "c":
        val = value if expression else f'"{value}"'
        name = _c_metadata_identifier(key, seen_idents if seen_idents is not None else set())
        return f'{indent}DFTRACER_C_METADATA({name}, "{key}", {val});'
    if lang in ("cpp", "c++"):
        val = value if expression else f'"{value}"'
        name = _c_metadata_identifier(key, seen_idents if seen_idents is not None else set())
        return f'{indent}DFTRACER_CPP_METADATA({name}, "{key}", {val});'
    # Python: real API is log_metadata_event on the initialized log object.
    val = f"str({value})" if expression else f'"{value}"'
    return f'{indent}_dft_log.log_metadata_event("{key}", {val})'


#: Where the metadata block must go, per language. Metadata is per-process and
#: must be emitted AFTER init (otherwise there is no logger to attach it to).
_C_INIT_RE = re.compile(r"DFTRACER_C(PP)?_INIT\s*\(")
_PY_INIT_RE = re.compile(r"(\w+)\s*=\s*dftracer\.initialize_log\s*\(")

_MARKER = "dftracer app-parameter metadata"


def _insert_after_init(lines: List[str], language: str, params: Dict[str, Any],
                       anchor_regex: str = "", expressions: bool = False
                       ) -> Tuple[List[str], str]:
    r"""Insert the metadata block after the dftracer init call, or after *anchor_regex*.

    Runtime parameter values (batch size, rank count, …) are only available after
    the app parses them, which is usually well below the init call. Pass an
    ``anchor_regex`` such as ``r"args\s*=\s*parser\.parse_args\(\)"`` together with
    ``expressions=True`` to place the block there and emit the real values.
    """
    lang = language.lower()
    if anchor_regex:
        try:
            pat = re.compile(anchor_regex)
        except re.error as exc:
            return lines, f"invalid anchor_regex: {exc}"
        idx = next((i for i, l in enumerate(lines) if pat.search(l)), None)
        if idx is None:
            return lines, f"anchor_regex did not match any line: {anchor_regex}"
    else:
        init_re = _PY_INIT_RE if lang == "python" else _C_INIT_RE
        idx = next((i for i, l in enumerate(lines) if init_re.search(l)), None)
        if idx is None:
            return lines, "no dftracer init call found — cannot place metadata"

    indent = re.match(r"\s*", lines[idx]).group(0)
    block = [f"{indent}// {_MARKER}" if lang != "python" else f"{indent}# {_MARKER}"]
    seen_idents: set = set()
    block += [_metadata_line(language, k, str(v), indent, expressions, seen_idents)
              for k, v in params.items()]
    return lines[: idx + 1] + block + lines[idx + 1:], ""


def _add_metadata_impl(run_id: str, filepath: str, language: str,
                       params_json: str, anchor_regex: str = "",
                       expressions: bool = False,
                       annotated_dir: str = "annotated") -> str:
    ws = _ws(run_id)
    p = ws / annotated_dir / filepath
    if not p.is_file():
        return _err(f"File not found in annotated/: {filepath}")
    try:
        params = _json.loads(params_json)
        if not isinstance(params, dict) or not params:
            return _err("params_json must be a non-empty JSON object")
    except _json.JSONDecodeError as exc:
        return _err(f"params_json is not valid JSON: {exc}")

    text = p.read_text(errors="ignore")
    if _MARKER in text:
        return _ok("metadata already present (idempotent no-op)",
                   filepath=filepath, added=0)

    lines = text.splitlines()
    new_lines, err = _insert_after_init(lines, language, params,
                                        anchor_regex, expressions)
    if err:
        return _err(err, filepath=filepath)
    p.write_text("\n".join(new_lines) + "\n")
    return _ok(f"added {len(params)} metadata event(s) to {filepath}",
               filepath=filepath, added=len(params), keys=list(params))


# --------------------------------------------------------------------------
# Coverage validation
# --------------------------------------------------------------------------

#: Call names that mark an I/O or checkpoint flow a trace must not miss.
_PY_CRITICAL = {
    "open", "load", "save", "state_dict", "load_state_dict", "File",
    "read_csv", "read_parquet", "from_pretrained", "save_pretrained",
    "all_reduce", "barrier", "broadcast",
}
_C_CRITICAL = (
    "open", "read", "write", "fopen", "fwrite", "fread", "close",
    "H5Fcreate", "H5Dwrite", "H5Dread", "H5Fopen",
    "MPI_File_open", "MPI_File_write", "MPI_File_read",
)

#: Substrings that identify a dftracer decorator or in-body region. Matched
#: against the FULL dotted decorator expression (``_dlp.log``, ``dft_ai.compute``,
#: ``_dft.log_init``), not just its trailing attribute — checking only the attr
#: makes every ``@_dlp.log`` look unannotated.
_PY_DECOS = ("_dlp.", "_dft.", "dft_ai", "dft_fn", "dftracer")

#: Context-expression substrings that mark a contextual `with` instrumentation
#: region — the preferred style for static methods and any function a decorator
#: cannot cleanly wrap: ``with DFTracerFn("cat", name="fn"):`` /
#: ``with dft_ai.comm.all_reduce():``. ``dft_fn`` is a context manager
#: (``__enter__``/``__exit__``), so this is a first-class annotation, not a hack.
_PY_REGIONS = ("_dlp", "_dft", "dft_ai", "dft_fn", "DFTracerFn", "dftracer")

#: AI-API annotations (dft_ai.data.item, dft_ai.checkpoint.*, dft_ai.pipeline.train,
#: dft_ai.comm.*, ...). These are applied for SEMANTIC reasons and are exempt from
#: the cost gate — never flag one as over-annotated.
_PY_AI_API = ("dft_ai",)
_C_START = "DFTRACER_C_FUNCTION_START"
_CPP_FN = "DFTRACER_CPP_FUNCTION"



def _unwrap_region(fn: ast.AST) -> ast.AST:
    """Return *fn* with a dftracer `with`-region wrapper stripped from its body.

    Scoring an annotated function directly is misleading: once its body has been
    wrapped in ``with DFTracerFn(...):`` the function looks like a single
    statement with no loops and no I/O, so the cost estimator would call an
    expensive function trivial. Score what the function actually does.
    """
    body = list(getattr(fn, "body", []) or [])
    if not body:
        return fn
    start = 1 if (isinstance(body[0], ast.Expr)
                  and isinstance(body[0].value, ast.Constant)
                  and isinstance(body[0].value.value, str)) else 0
    if len(body) > start and isinstance(body[start], (ast.With, ast.AsyncWith)):
        w = body[start]
        src = " ".join(ast.unparse(i.context_expr) for i in w.items)
        if any(tok in src for tok in _PY_REGIONS):
            clone = ast.FunctionDef(
                name=getattr(fn, "name", "f"),
                args=fn.args,
                body=body[:start] + list(w.body),
                decorator_list=list(fn.decorator_list),
                returns=None, type_comment=None, type_params=[],
            )
            return ast.fix_missing_locations(clone)
    return fn


def _py_functions(tree: ast.AST):
    """Yield ``(node, qualname)`` for every function/method."""
    def walk(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                q = f"{prefix}.{child.name}" if prefix else child.name
                yield child, q
                yield from walk(child, q)
            elif isinstance(child, ast.ClassDef):
                q = f"{prefix}.{child.name}" if prefix else child.name
                yield from walk(child, q)
            else:
                yield from walk(child, prefix)
    yield from walk(tree)


def _validate_python(path: Path) -> Dict[str, Any]:
    text = path.read_text(errors="ignore")
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return {"file": str(path), "error": f"SyntaxError: {exc}"}

    findings: List[Dict[str, str]] = []
    for fn in [n for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        crit = set()
        for node in ast.walk(fn):
            if isinstance(node, ast.Call):
                f = node.func
                name = f.id if isinstance(f, ast.Name) else (
                    f.attr if isinstance(f, ast.Attribute) else "")
                if name in _PY_CRITICAL:
                    crit.add(name)
        # Full dotted decorator expressions, e.g. "_dlp.log", "dft_ai.compute".
        decos = " ".join(ast.unparse(d) for d in (fn.decorator_list or []))

        # @log_static is a style violation regardless of what the function does:
        # static methods must use a contextual `with` region. Checked BEFORE the
        # "does it do I/O" filter, or a @log_static on a pure-compute static
        # method slips through unreported.
        if "log_static" in decos:
            findings.append({
                "function": fn.name, "line": str(fn.lineno),
                "issue": "uses @log_static — replace with a contextual `with` region "
                         "inside the function body",
                "calls": ", ".join(sorted(crit)) or "(no I/O)",
            })
            continue

        if not crit:
            continue

        # Preferred style: a contextual `with` region inside the body. This is
        # how static methods (and any function a decorator cannot cleanly wrap)
        # must be instrumented.
        has_with = any(isinstance(n, (ast.With, ast.AsyncWith)) for n in ast.walk(fn))
        with_src = " ".join(
            ast.unparse(item.context_expr)
            for n in ast.walk(fn) if isinstance(n, (ast.With, ast.AsyncWith))
            for item in n.items
        )
        in_body_region = has_with and any(tok in with_src for tok in _PY_REGIONS)

        if not any(tok in decos for tok in _PY_DECOS) and not in_body_region:
            is_static = "staticmethod" in decos
            issue = ("critical I/O flow not annotated — static method needs a "
                     "contextual `with` region inside the body"
                     if is_static else "critical I/O flow not annotated")
            findings.append({
                "function": fn.name, "line": str(fn.lineno),
                "issue": issue,
                "calls": ", ".join(sorted(crit)),
            })

    # Cost-gate enforcement: a function carrying a GENERIC annotation
    # (@_dlp.log / @_dft.log / a `with` region) that the estimator would skip is
    # over-annotation — it adds trace noise for no signal. AI-API annotations
    # (dft_ai.*) are exempt: they exist for semantic reasons, not cost.
    for fn, qual in _py_functions(tree):
        decos = " ".join(ast.unparse(d) for d in (fn.decorator_list or []))
        region_src = " ".join(
            ast.unparse(item.context_expr)
            for n in ast.walk(fn) if isinstance(n, (ast.With, ast.AsyncWith))
            for item in n.items
        )
        # AI-API annotations are exempt from the cost gate, whether they appear as
        # a decorator (@dft_ai.compute) or as a region (with dft_ai.comm.all_reduce()).
        if any(tok in decos or tok in region_src for tok in _PY_AI_API):
            continue
        if fn.name == "main":
            continue                                  # entry point — always allowed
        has_generic = any(tok in decos for tok in ("_dlp.", "_dft."))
        has_region = "DFTracerFn" in region_src
        if not (has_generic or has_region):
            continue
        rec = _cost_recommend(_unwrap_region(fn), PY_ANNOTATION_SCORE_THRESHOLD)
        if rec["recommendation"] == "skip":
            findings.append({
                "function": qual, "line": str(fn.lineno),
                "issue": (f"annotated but fails the cost gate — {rec['reason']} "
                          f"(score {rec['score']}, threshold {rec['threshold']}); "
                          f"remove the annotation or use an AI-API region"),
                "calls": "(over-annotated)",
            })
    has_init = "initialize_log" in text
    has_fini = "finalize()" in text
    return {"file": str(path), "findings": findings,
            "has_init": has_init, "has_fini": has_fini,
            "has_metadata": _MARKER in text or "log_metadata_event" in text}


def _validate_c_like(path: Path, cpp: bool) -> Dict[str, Any]:
    text = path.read_text(errors="ignore")
    lines = text.splitlines()
    marker = _CPP_FN if cpp else _C_START
    findings: List[Dict[str, str]] = []

    # Coarse but reliable: a function body containing a critical call must
    # contain the function macro somewhere above the call in the same file.
    fn_re = re.compile(r"^[A-Za-z_][\w\s\*]*\b(\w+)\s*\([^;]*\)\s*\{")
    cur_fn, cur_start, annotated = None, 0, False
    for i, line in enumerate(lines):
        m = fn_re.match(line)
        if m:
            cur_fn, cur_start, annotated = m.group(1), i, False
        if cur_fn and marker in line:
            annotated = True
        if cur_fn and any(c in line for c in _C_CRITICAL) and not annotated:
            call = next(c for c in _C_CRITICAL if c in line)
            findings.append({"function": cur_fn, "line": str(i + 1),
                             "issue": "critical I/O flow not annotated",
                             "calls": call})
            cur_fn = None  # report once per function

    init = "DFTRACER_CPP_INIT" if cpp else "DFTRACER_C_INIT"
    fini = "DFTRACER_CPP_FINI" if cpp else "DFTRACER_C_FINI"
    meta = "DFTRACER_CPP_METADATA" if cpp else "DFTRACER_C_METADATA"

    # comp= is mandatory on every UPDATE (cheatsheet rule C3).
    missing_comp = [
        str(i + 1) for i, l in enumerate(lines)
        if "_UPDATE" in l and "comp" not in l
    ]
    return {"file": str(path), "findings": findings,
            "has_init": init in text, "has_fini": fini in text,
            "has_metadata": meta in text,
            "updates_missing_comp": missing_comp}


def _validate_impl(run_id: str, language: str, subdir: str,
                   annotated_dir: str = "annotated") -> str:
    ws = _ws(run_id)
    root = ws / annotated_dir
    if subdir:
        root = root / subdir
    if not root.is_dir():
        return _err(f"annotated tree not found: {root}")

    lang = language.lower()
    exts = {"python": (".py",), "c": (".c",), "cpp": (".cpp", ".cxx", ".cc")}.get(lang)
    if not exts:
        return _err(f"unsupported language '{language}' (c|cpp|python)")

    reports, total_findings = [], 0
    any_init = any_fini = any_meta = False
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in exts:
            continue
        if any(p in ("__pycache__", ".git", "test", "tests") for p in f.parts):
            continue
        r = (_validate_python(f) if lang == "python"
             else _validate_c_like(f, cpp=(lang == "cpp")))
        any_init |= bool(r.get("has_init"))
        any_fini |= bool(r.get("has_fini"))
        any_meta |= bool(r.get("has_metadata"))
        n = len(r.get("findings", [])) + len(r.get("updates_missing_comp", []))
        total_findings += n
        if n or r.get("error"):
            reports.append(r)

    project: List[str] = []
    if not any_init:
        project.append("no dftracer init found anywhere (entry point uninstrumented)")
    if not any_fini:
        project.append("no dftracer finalize found (trace may be truncated)")
    if not any_meta:
        project.append("no app-parameter metadata events "
                       "(run annotate_add_app_metadata)")

    ok = total_findings == 0 and not project
    return _ok(
        "annotation validation passed" if ok
        else f"{total_findings} finding(s) across {len(reports)} file(s)",
        language=lang, passed=ok, total_findings=total_findings,
        project_issues=project, files=reports,
    )


def register_validation_tools(mcp: FastMCP) -> None:
    """Register ``annotate_add_app_metadata`` and ``validate_annotations``."""

    @mcp.tool()
    def annotate_add_app_metadata(
        run_id: str,
        filepath: str,
        language: str,
        params_json: str,
        anchor_regex: str = "",
        expressions: bool = False,
    ) -> str:
        """Emit the app's run parameters as dftracer metadata events.

        Attaches key/value context (rank count, batch size, block size,
        checkpoint interval, problem name, …) to the process's trace so traces
        can be filtered and correlated after the fact. Insert into the file that
        performs dftracer init; the block is placed immediately AFTER the init
        call, because metadata needs a live logger.

        Dialects:

        * C      — ``DFTRACER_C_METADATA(dft_meta_<key>, "key", "value");``
          (3-arg macro; ``name`` is a bare, unique C identifier used for
          ``##name`` token-pasting internally — NOT a string literal).
        * C++    — ``DFTRACER_CPP_METADATA(dft_meta_<key>, "key", "value");``
          (same 3-arg ``name, key, value`` shape as the C macro).
        * Python — ``_dft_log.log_metadata_event("key", "value")`` (the object
          returned by ``dftracer.initialize_log(...)``).

        Idempotent: re-running is a no-op once the block is present.

        Args:
            run_id: Session identifier.
            filepath: Path relative to ``annotated/`` containing the init call.
            language: ``"c"``, ``"cpp"``, or ``"python"``.
            params_json: JSON object of parameter name -> value.

        Returns:
            JSON with ``added`` (count) and the ``keys`` written.
        """
        return _add_metadata_impl(run_id, filepath, language, params_json,
                                  anchor_regex, expressions)

    @mcp.tool()
    def validate_annotations(
        run_id: str,
        language: str,
        subdir: str = "",
    ) -> str:
        """Verify the annotated tree is complete and correct for a language.

        Checks, per file:

        * Every function that performs a **critical flow** — file I/O,
          HDF5/MPI-IO, checkpoint save/load, or collective communication — is
          actually annotated. This is the check that catches "we instrumented
          the helpers but missed the checkpoint writer".
        * C/C++: every ``*_UPDATE`` carries a ``comp=`` type (cheatsheet C3).

        And project-wide:

        * an init and a finalize exist (a missing finalize truncates the trace),
        * app-parameter metadata events are present.

        Args:
            run_id: Session identifier.
            language: ``"c"``, ``"cpp"``, or ``"python"``.
            subdir: Optional sub-path under ``annotated/`` to restrict the scan.

        Returns:
            JSON with ``passed``, ``total_findings``, ``project_issues``, and
            ``files`` (per-file findings with function name, line, and the
            critical call that is unannotated).
        """
        return _validate_impl(run_id, language, subdir)
