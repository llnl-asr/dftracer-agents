"""Python-AST-backed annotation tools for dftracer source code instrumentation.

This module registers three MCP tools on a FastMCP instance:

* ``python_extract_functions``  — build an authoritative function map from a
                                   Python file using the built-in ``ast`` module
* ``python_annotate_file``      — whole-file annotation: insert dftracer
                                   decorators and init/fini stubs in one in-memory
                                   pass, writing the file exactly once
* ``python_write_annotated_file`` — flush the in-memory cache to disk

Python dftracer API
-------------------
The annotation inserts the following pydftracer constructs:

  from dftracer.python import dftracer, dft_fn as DFTracerFn

  # One instance per file, category = module name
  _dft = DFTracerFn("<MODULE>")

  # Entry-point files only (file containing main() or if __name__ == "__main__")
  _dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)

  # Per-function decorators (inserted before the first decorator or 'def'):
  @_dft.log          # regular functions and methods
  @_dft.log_init     # __init__ methods

  # @staticmethod methods use a CONTEXTUAL region, never @log_static: a decorator
  # fights @staticmethod ordering, while dft_fn is a context manager.
  @staticmethod
  def f(...):
      with DFTracerFn("<MODULE>", name="f"):
          ...

  # Entry-point cleanup (inserted at end of main() or script body):
  _dft_log.finalize()

Line-shift safety
-----------------
All insertion positions are computed from the original ``ast`` parse (which
sees the unmodified file), then sorted highest-line-number first and applied
to the in-memory line list in a single pass before writing.  Each insertion
therefore uses the original (unshifted) line numbers, avoiding the classic
off-by-one errors that occur when inserting top-to-bottom.
"""
from __future__ import annotations

import ast
import re
from typing import Dict, List

from fastmcp import FastMCP

from .workspace import _ws, _ok, _err

# ---------------------------------------------------------------------------
# Module-level in-memory file cache (shared with annotation_clang._FILE_CACHE)
# Maps (run_id, filepath) → list[str] lines (no trailing newline per line).
# ---------------------------------------------------------------------------
_PY_FILE_CACHE: Dict[tuple, List[str]] = {}

_PY_IMPORT = "from dftracer.python import dftracer, dft_fn as DFTracerFn"
_DFT_INIT  = "_dft_log = dftracer.initialize_log(logfile=None, data_dir=None, process_id=None)"
_DFT_FINI  = "_dft_log.finalize()"


def _last_import_idx(lines: List[str]) -> int:
    """Return the 0-based index AFTER the last top-level import line.

    Tracks preprocessor-style depth to stay out of try/except/if blocks
    where imports sometimes appear.
    """
    last = -1
    depth = 0
    for i, ln in enumerate(lines):
        s = ln.strip()
        # Simple depth tracking for indented blocks
        if depth == 0 and re.match(r'^(?:import |from .+ import )', s):
            last = i
        if s.endswith(':') and not s.startswith('#'):
            depth += 1
        elif depth > 0 and s and not ln[0].isspace():
            depth = 0
    return last + 1  # insert AFTER the last import



def _indent_of(lines: List[str], lineno: int) -> str:
    """Return the leading whitespace of 1-based *lineno*, or "" if out of range.

    ``finalize()`` inserted before a nested ``return`` must match THAT return's
    indentation, not main()'s body indent — otherwise it lands at the wrong depth
    and raises IndentationError.
    """
    if 1 <= lineno <= len(lines):
        line = lines[lineno - 1]
        return line[: len(line) - len(line.lstrip())]
    return ""


def _find_return_lines(fn_node: ast.AST) -> List[int]:
    """Return 1-based line numbers of all ``return`` statements in *fn_node*."""
    lines: List[int] = []
    for node in ast.walk(fn_node):
        # Skip nested function/class bodies — they have their own returns
        if node is fn_node:
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(node, ast.Return):
            lines.append(node.lineno)
    return lines



def _multiline_string_rows(source: str) -> set:
    """Return the 1-based line numbers spanned by multi-line string literals.

    Re-indenting a function body would rewrite the *contents* of any triple-quoted
    string it spans, so bodies containing one are left untouched.
    """
    import io
    import tokenize
    rows: set = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
                rows.update(range(tok.start[0], tok.end[0] + 1))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return rows


def _extract_functions_from_ast(source: str) -> List[dict]:
    """Parse *source* with ``ast`` and return a list of function-info dicts.

    Each dict has:
      name                  — function name
      qualname              — dotted qualified name (e.g. ``MyClass.method``)
      start_line            — 1-based line of the ``def``/``async def`` keyword
      decorator_insert_line — 1-based line before which the decorator is inserted
                              (= first existing decorator line, or start_line)
      body_first_line       — 1-based line of first statement in body
      end_line              — 1-based last line of the function (end_lineno)
      col_offset            — column of the ``def`` keyword (= indentation)
      is_init               — True for ``__init__``
      has_staticmethod      — True if ``@staticmethod`` is in the decorator list
      has_property           — True if ``@property``/``@cached_property``/
                               ``@x.setter``/``@x.deleter``/``@x.getter`` is in
                               the decorator list
      is_async              — True for ``async def``
      is_entry_point        — True for ``main`` or module-level ``__main__``
      source                — always ``"ast"``
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results: List[dict] = []

    def _walk(node: ast.AST, prefix: str = "") -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                # Where to insert the decorator: before the first existing
                # decorator, or before the 'def' line itself.
                if child.decorator_list:
                    dec_insert = child.decorator_list[0].lineno
                else:
                    dec_insert = child.lineno

                has_static = any(
                    (isinstance(d, ast.Name) and d.id == "staticmethod")
                    or (isinstance(d, ast.Attribute) and d.attr == "staticmethod")
                    for d in child.decorator_list
                )

                # A function decorated with @property/@cached_property/@x.setter/
                # @x.deleter/@x.getter must NEVER get a plain @_dft.log decorator
                # stacked on top — that replaces the property descriptor with the
                # decorator's wrapper object, so `obj.attr` returns an unbound
                # wrapper/bound-method instead of invoking the getter (observed:
                # a `@property def jobspec` on flux-fiction's Job class silently
                # broke, producing "Object of type method is not JSON serializable"
                # because `job.jobspec` returned a bound method object instead of
                # calling it). Treat these exactly like @staticmethod: a contextual
                # `with DFTracerFn(...)` region inside the body, never a decorator.
                has_property = any(
                    (isinstance(d, ast.Name) and d.id in ("property", "cached_property"))
                    or (isinstance(d, ast.Attribute) and d.attr in ("setter", "deleter", "getter"))
                    for d in child.decorator_list
                )

                body_first = (
                    child.body[0].lineno if child.body else child.lineno + 1
                )
                body_col = (
                    child.body[0].col_offset if child.body else child.col_offset + 4
                )
                return_lines = _find_return_lines(child)

                results.append({
                    "name": child.name,
                    "qualname": qualname,
                    "start_line": child.lineno,
                    "decorator_insert_line": dec_insert,
                    "body_first_line": body_first,
                    "body_col_offset": body_col,
                    "end_line": child.end_lineno or child.lineno,
                    "return_lines": return_lines,
                    "col_offset": child.col_offset,
                    "is_init": child.name == "__init__",
                    "has_staticmethod": has_static,
                    "has_property": has_property,
                    "is_async": isinstance(child, ast.AsyncFunctionDef),
                    "is_entry_point": child.name == "main" and prefix == "",
                    "source": "ast",
                })
                # Recurse into nested functions / class bodies
                _walk(child, qualname)
            elif isinstance(child, ast.ClassDef):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                _walk(child, qualname)
            else:
                _walk(child, prefix)

    _walk(tree)
    return results



# ---------------------------------------------------------------------------
# Module-level implementation so orchestrators (ml_pipeline) can drive the
# same generic annotation logic without the MCP tool wrapper.
# ---------------------------------------------------------------------------
def _python_annotate_file_impl(
    run_id: str,
    filepath: str,
    category: str = "",
    is_entry: bool = False,
    logfile: str = "None",
    data_dir: str = "None",
    process_id: int = -1,
    annotate_nested: bool = True,
    only_functions: str = "",
    annotated_dir: str = "annotated",
) -> str:
    """Annotate a Python file with dftracer decorators in a single in-memory pass.

    This is the preferred way to instrument a whole Python file.  It:

    1. Loads the file into memory (from cache or disk).
    2. Parses it with ``ast`` to build an authoritative function map.
    3. Computes all insertion points:

       * ``from dftracer.python import dftracer, dft_fn as DFTracerFn``
         after the last top-level import.
       * ``_dft = DFTracerFn("<category>")``
         immediately after the import (one instance per file).
       * For entry files (``is_entry=True``):
         ``_dft_log = dftracer.initialize_log(...)``
         right after the ``_dft`` line.
       * ``@_dft.log`` / ``@_dft.log_init``; static methods get a
         contextual ``with DFTracerFn(...)`` region instead of ``@log_static``
         before each function's first decorator or ``def`` line.
       * For entry files:
         ``_dft_log.finalize()`` before the last ``return`` in ``main()``,
         or appended to the end of the script if no ``main()`` is found.

    4. Sorts all insertion points **highest-line-number first** so that each
       insertion does not shift earlier (lower-numbered) positions.
    5. Applies every insertion to the in-memory line list.
    6. Writes the file once and caches the result.

    The file is modified inside the ``annotated/`` subfolder; the original
    ``source/`` copy is never touched.  Operation is idempotent: if the
    dftracer import is already present the file is returned unchanged.

    Args:
        run_id:          Session identifier returned by ``session_create``.
        filepath:        Path relative to the ``annotated/`` subfolder.
        category:        Category string for ``dft_fn("<category>")``.
                         Defaults to the stem of the filename
                         (e.g. ``"train"`` for ``train.py``).
        is_entry:        ``True`` when this file is the program entry point
                         (has ``main()`` or ``if __name__ == "__main__"``).
                         Controls insertion of ``initialize_log`` /
                         ``finalize()``.
        logfile:         ``logfile`` argument for ``initialize_log``; pass
                         as a Python expression string (default ``"None"``).
        data_dir:        ``data_dir`` argument for ``initialize_log``.
        process_id:      ``process_id`` argument for ``initialize_log``
                         (default ``-1``).
        annotate_nested: If ``True`` (default), annotate nested functions
                         and class methods.  Set to ``False`` to annotate
                         only top-level functions.

    Returns:
        JSON string with keys:
            * ``status``           — ``"ok"`` or ``"error"``.
            * ``message``          — human-readable summary.
            * ``filepath``         — echoed input path.
            * ``insertions``       — total lines inserted.
            * ``functions``        — number of functions decorated.
            * ``total_lines``      — line count after annotation.
            * ``already_annotated``— ``True`` if skipped (already done).
    """
    from pathlib import Path

    ws = _ws(run_id)
    abs_path = ws / annotated_dir / filepath

    cache_key = (run_id, filepath)
    if cache_key in _PY_FILE_CACHE:
        lines = list(_PY_FILE_CACHE[cache_key])
    elif abs_path.exists():
        lines = abs_path.read_text(errors="replace").splitlines()
    else:
        return _err(f"File not found in annotated/: {filepath}")

    text = "\n".join(lines)

    # Idempotency guard
    if "from dftracer.python import" in text and "DFTracerFn" in text:
        return _ok(
            f"{filepath} is already annotated — skipped.",
            filepath=filepath,
            insertions=0,
            functions=0,
            total_lines=len(lines),
            already_annotated=True,
        )

    # Default category = module stem
    cat = category or Path(filepath).stem

    # ── Step 1: parse function map ─────────────────────────────────────
    all_fns = _extract_functions_from_ast(text)
    if not annotate_nested:
        all_fns = [f for f in all_fns if "." not in f["qualname"]]

    # Selective mode: restrict to an explicit allow-list of function names.
    # This is how the AI/ML cost gate (python_estimate_file_costs) is enforced —
    # without it, every getter and one-line helper gets a decorator and the
    # trace drowns in noise.
    if only_functions:
        wanted = {n.strip() for n in only_functions.split(",") if n.strip()}
        # Prefer exact qualname matches. A bare name is honoured only when it is
        # unambiguous in this file — otherwise `__init__` would select the
        # __init__ of every class.
        names = [f["name"] for f in all_fns]
        all_fns = [
            f for f in all_fns
            if f["qualname"] in wanted
            or (f["name"] in wanted and names.count(f["name"]) == 1)
        ]

    # ── Step 2: build import/init block (inserted after last import) ──
    import_idx = _last_import_idx(lines)  # 0-based index to insert at

    init_lines: List[str] = [
        _PY_IMPORT,
        f'_dft = DFTracerFn("{cat}")',
    ]
    if is_entry:
        init_lines.append(
            f"_dft_log = dftracer.initialize_log("
            f"logfile={logfile}, data_dir={data_dir}, process_id={process_id})"
        )

    # ── Step 3: build per-function decorator insertions ───────────────
    # Each entry: (0-based-index, text-to-insert)
    insertions: List[tuple] = []

    # The init block is a multi-line insertion at import_idx — add each
    # line as a separate insertion so they end up in order.  Since we
    # sort highest-first and all have the same index, we add them in
    # REVERSE order so the sort produces the right final sequence.
    for ln in reversed(init_lines):
        insertions.append((import_idx, ln))

    # Static methods AND property-decorated methods (@property/@cached_property/
    # @x.setter/@x.deleter/@x.getter) are instrumented with a contextual `with`
    # region inside the body, never with a stacked @_dft.log decorator: for
    # staticmethod, a decorator has to fight @staticmethod ordering; for a
    # property, stacking @_dft.log on top of @property REPLACES the property
    # descriptor with the decorator's wrapper object, so `obj.attr` returns an
    # unbound wrapper instead of invoking the getter (see has_property comment
    # in _extract_functions_from_ast). `dft_fn` is a context manager
    # (__enter__/__exit__) and nests correctly inside the body either way.
    # Collected here, applied after the decorator insertions.
    regions: List[dict] = []
    skipped_static: List[str] = []
    multiline_str_rows = _multiline_string_rows(text)

    for fn in all_fns:
        dec_idx = fn["decorator_insert_line"] - 1  # 0-based
        indent   = " " * fn["col_offset"]

        if fn.get("has_property") and not fn["is_init"]:
            body_rows = range(fn["body_first_line"], fn["end_line"] + 1)
            if multiline_str_rows.intersection(body_rows):
                skipped_static.append(fn["qualname"])
                continue
            regions.append(fn)
            continue

        if fn["has_staticmethod"] and not fn["is_init"]:
            body_rows = range(fn["body_first_line"], fn["end_line"] + 1)
            if multiline_str_rows.intersection(body_rows):
                # Re-indenting would rewrite the contents of a multi-line
                # literal. Leave it alone rather than corrupt the source.
                skipped_static.append(fn["qualname"])
                continue
            regions.append(fn)
            continue

        if fn["is_init"]:
            dec = f"{indent}@_dft.log_init"
        else:
            dec = f"{indent}@_dft.log"

        insertions.append((dec_idx, dec))

    # Entry-point finalize: insert before every return in main(), and
    # before the function's closing line if main() has no explicit return.
    if is_entry:
        main_fn = next((f for f in all_fns if f["is_entry_point"]), None)
        if main_fn:
            body_ind = " " * main_fn.get("body_col_offset", 4)
            fini_line = f"{body_ind}{_DFT_FINI}"
            returns = main_fn.get("return_lines", [])
            if returns:
                for ret_line in returns:
                    ind = _indent_of(lines, ret_line)
                    insertions.append((ret_line - 1, f"{ind}{_DFT_FINI}"))
            else:
                # end_line is the 1-based LAST line of main(); insert AFTER it so
                # finalize() does not become the first statement of the function.
                insertions.append((main_fn["end_line"], fini_line))
        else:
            # No main() found — append at end of file
            insertions.append((len(lines), _DFT_FINI))

    # ── Step 4: sort highest-first, apply in one pass ─────────────────
    # For ties (same index): the item added LAST in the list should land
    # HIGHEST in the file after insertion.  reversed + stable sort achieves
    # this: items with higher idx go first; for ties, last-added goes first
    # which means it gets inserted last at that position, pushing earlier
    # items up (so first-added ends up topmost at that position).
    insertions.sort(key=lambda x: -x[0])

    for idx, txt in insertions:
        lines.insert(idx, txt)

    # ── Step 4b: contextual `with` regions for @staticmethod ──────────
    # Applied after the decorator insertions, against a FRESH parse, so the line
    # numbers are correct. Bottom-up, so earlier functions keep their positions.
    if regions:
        want = {fn["qualname"] for fn in regions}
        try:
            fresh = _extract_functions_from_ast("\n".join(lines))
        except Exception:
            fresh = []
        targets = sorted(
            (f for f in fresh if f["qualname"] in want),
            key=lambda f: -f["body_first_line"],
        )
        for fn in targets:
            b0 = fn["body_first_line"] - 1          # 0-based first body line
            e0 = fn["end_line"] - 1                 # 0-based last body line
            body_ind = " " * fn["body_col_offset"]
            for i in range(b0, min(e0, len(lines) - 1) + 1):
                if lines[i].strip():                # never indent blank lines
                    lines[i] = "    " + lines[i]
            lines.insert(
                b0,
                f'{body_ind}with DFTracerFn("{cat}", name="{fn["name"]}"):',
            )

    # ── Step 5: write once ────────────────────────────────────────────
    _PY_FILE_CACHE[cache_key] = list(lines)
    abs_path.write_text("\n".join(lines) + "\n")

    fn_count = len(all_fns)
    msg = (f"Annotated {filepath}: {len(insertions)} line(s) inserted "
           f"({fn_count} function(s) instrumented, {len(regions)} `with` region(s)).")
    if skipped_static:
        msg += (f" SKIPPED {len(skipped_static)} static method(s) whose body spans a "
                f"multi-line string — annotate by hand: {', '.join(skipped_static)}.")
    return _ok(
        msg,
        filepath=filepath,
        insertions=len(insertions),
        functions=fn_count,
        with_regions=len(regions),
        skipped_static=skipped_static,
        total_lines=len(lines),
        already_annotated=False,
    )


def register_python_tools(mcp: FastMCP) -> None:
    """Register Python annotation tools on *mcp*."""

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

        for p in regular_files + entry_files:
            rel = str(p.relative_to(ann_dir))
            is_entry_file = p in entry_files
            try:
                raw = _python_annotate_file_impl(
                    run_id=run_id,
                    filepath=rel,
                    is_entry=is_entry_file,
                    logfile=logfile,
                    data_dir=data_dir,
                    process_id=process_id,
                )
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
