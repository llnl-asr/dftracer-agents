"""Annotation helpers for injecting dftracer instrumentation macros into C/C++ and Python source.

This module is responsible for transforming copies of user source files (kept under
``annotated/``) by inserting dftracer tracing calls without modifying any program
logic.  It is consumed by the MCP session pipeline when an agent is asked to
instrument a codebase for performance profiling.

Annotation strategies
---------------------
Two strategies are applied depending on the role of a source file:

**Entry-point files** (files that contain ``main()`` or the top-level script body)
    C/C++: ``DFTRACER_C_INIT`` / ``DFTRACER_CPP_INIT`` is injected at the start of
    ``main()`` so the tracer is initialised before any region is entered.
    ``DFTRACER_C_FINI`` / ``DFTRACER_CPP_FINI`` is injected at every exit point
    (``return`` in ``main``, ``exit``/``abort`` call sites, and the fall-through
    closing brace).

    Python: ``DFTRACER_INIT(...)`` is emitted immediately after the import block and
    a ``# TODO`` reminder for ``DFTRACER_FINI()`` is appended at the end of the file.

**Inner files** (all other translation units / modules)
    C/C++: ``DFTRACER_C_FUNCTION_START()`` (C) or ``DFTRACER_CPP_FUNCTION()`` (C++)
    is injected at the opening ``{`` of every function body.  Optional metadata calls
    (``DFTRACER_C_FUNCTION_UPDATE_STR`` / ``_UPDATE_INT``) are appended for
    recognisable file-path and size-like parameters.  ``DFTRACER_C_FUNCTION_END()``
    is inserted before every ``return`` and at the fall-through closing brace.

    Python: ``@dft_fn`` is prepended to every top-level ``def`` statement.

Public entry points
-------------------
These functions are called directly by the MCP session tools:

* :func:`_annotate_c_source` — annotate a single C or C++ source file.
* :func:`_annotate_python_source` — annotate a single Python source file.
* :func:`_generate_annotation_report` — build a structured coverage report for a
  workspace, comparing ``source/`` against ``annotated/``.
* :func:`_fix_dftracer_annotation_errors` — roll back dftracer macros from files
  that caused compilation errors after annotation.
* :func:`_strip_dftracer_c_macros` — idempotent macro removal used for cleanup and
  rollback.
* :func:`_strip_mpi_launcher` — strip MPI/parallel launcher prefixes before running
  smoke-test commands.
* :func:`_generate_annotation_report` — produce a structured JSON-serialisable
  coverage report for a given workspace run.

Internal helpers
----------------
:func:`_finalize_c_ends_and_fini`, :func:`_metadata_update_calls`,
:func:`_c_func_at_line`, :func:`_find_all_c_functions`,
:func:`_find_annotated_c_functions`, :func:`_find_all_py_functions`,
:func:`_find_annotated_py_functions`, :func:`_parse_annotation_status`,
:func:`_diff_modified_files`.
"""
from __future__ import annotations

import difflib
import re
import subprocess
import textwrap
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple


# ---------------------------------------------------------------------------
# Annotation helpers — C / C++
# ---------------------------------------------------------------------------

#: The ``#include`` line inserted at the top of every annotated C/C++ file.
#: Must be present for all dftracer macros (``DFTRACER_C_FUNCTION_START``,
#: ``DFTRACER_C_INIT``, etc.) to resolve during compilation.
_C_INCLUDE = "#include <dftracer/dftracer.h>"

#: C and C++ reserved keywords and identifiers that must never be mistaken for
#: function names by the annotation regex.  The function-header pattern
#: ``\b\w[\w\s\*:<>]*\s+(\w+)\s*\(`` matches control-flow constructs like
#: ``if (cond)`` and ``for (…)`` as well as genuine function definitions, so
#: every captured name is checked against this set before injection.
_C_KEYWORDS: frozenset = frozenset({
    "if", "else", "for", "while", "do", "switch", "return", "case",
    "break", "continue", "goto", "default", "sizeof", "typeof", "alignof",
    "typedef", "struct", "union", "enum", "namespace", "class", "template",
    "new", "delete", "throw", "try", "catch", "operator", "using", "friend",
    "public", "private", "protected", "virtual", "override", "final",
    "explicit", "inline", "volatile", "extern", "register", "typename",
    "decltype", "static_assert", "constexpr", "noexcept", "nullptr",
})

#: Matches the leading parallel-launcher token in a shell command string.
#: Supported launchers: ``mpirun``, ``mpiexec``, ``orterun``, ``srun``,
#: ``jsrun``, ``aprun``, ``prun``, and ``flux run``.  Used by
#: :func:`_strip_mpi_launcher` so that smoke-test commands can be executed
#: as single-process binaries without an MPI runtime present.
_MPI_LAUNCHER_RE = re.compile(
    r"^\s*(?:mpirun|mpiexec|orterun|srun|jsrun|aprun|prun|flux\s+run)\b"
)

#: Matches MPI process-count flags that are meaningless when running
#: single-process (``-np``, ``-n``, ``--ntasks``, ``--npernode``,
#: ``--ntasks-per-node``, ``-N``, ``--nodes``).  Stripped by
#: :func:`_strip_mpi_launcher` alongside the launcher token.
_MPI_NP_RE = re.compile(
    r"\s+(?:-np?|-n|--ntasks|--npernode|--ntasks-per-node|-N|--nodes)\s+\d+"
)

#: Matches miscellaneous MPI flags that refer to process placement or host
#: topology (``--oversubscribe``, ``--allow-run-as-root``, ``--bind-to``,
#: ``--map-by``, ``--host``, ``--hostfile``, ``--rankfile``).  Stripped by
#: :func:`_strip_mpi_launcher` so the remaining command can be executed
#: directly in a shell without MPI-aware flag parsing.
_MPI_MISC_FLAGS_RE = re.compile(
    r"\s+--(?:oversubscribe|allow-run-as-root|bind-to\s+\S+|map-by\s+\S+|"
    r"host\s+\S+|hostfile\s+\S+|rankfile\s+\S+)"
)


def _strip_mpi_launcher(cmd: str) -> tuple:
    """Remove an MPI or parallel launcher prefix from a shell command string.

    Strips the launcher token itself (e.g. ``mpirun``), process-count flags
    (e.g. ``-np 4``), and common placement flags (e.g. ``--bind-to core``)
    so that the remaining command can be executed as a single-process binary
    during smoke tests where an MPI runtime may not be available.

    Args:
        cmd: The raw shell command string, which may or may not begin with an
            MPI launcher.

    Returns:
        A two-element tuple ``(clean_cmd, was_stripped)`` where ``clean_cmd``
        is the command with the launcher prefix removed (or the original string
        if no launcher was detected), and ``was_stripped`` is ``True`` when a
        launcher was found and removed.
    """
    if not _MPI_LAUNCHER_RE.search(cmd):
        return cmd, False
    clean = _MPI_LAUNCHER_RE.sub("", cmd)
    clean = _MPI_NP_RE.sub("", clean)
    clean = _MPI_MISC_FLAGS_RE.sub("", clean)
    return clean.strip(), True


#: Matches any single-line dftracer macro injected by :func:`_annotate_c_source`
#: or :func:`_finalize_c_ends_and_fini`.  The pattern covers the full set of
#: macros that may be emitted:
#:
#: * ``DFTRACER_C_FUNCTION_START()``, ``DFTRACER_C_FUNCTION_END()``
#: * ``DFTRACER_CPP_FUNCTION()``
#: * ``DFTRACER_C_FUNCTION_UPDATE_STR(…)``, ``DFTRACER_C_FUNCTION_UPDATE_INT(…)``
#: * ``DFTRACER_CPP_FUNCTION_UPDATE(…)``
#: * ``DFTRACER_C_INIT(…)`` / ``DFTRACER_CPP_INIT(…)``
#: * ``DFTRACER_C_FINI()`` / ``DFTRACER_CPP_FINI()``
#: * ``DFTRACER_C_METADATA(…)`` / ``DFTRACER_CPP_METADATA(…)``
#:
#: Used by :func:`_strip_dftracer_c_macros` to cleanly revert an annotated file
#: to its original form.
_DFTRACER_MACRO_LINE_RE = re.compile(
    r"^\s*DFTRACER_(?:C|CPP)_(?:FUNCTION_(?:START|END)|FUNCTION_UPDATE_(?:STR|INT)|"
    r"CPP_FUNCTION_UPDATE|FUNCTION|INIT|FINI|METADATA)\s*\([^)]*\)\s*;\s*$",
    re.MULTILINE,
)

#: Matches GCC/Clang error diagnostic lines of the form
#: ``path/to/file.c:42:5: error: some message``.
#: Groups: (1) source file path relative to the build root,
#: (2) line number where the error was reported.
#: Used by :func:`_fix_dftracer_annotation_errors` to identify which
#: annotated files triggered compilation failures so their macros can be
#: rolled back automatically.
_COMPILER_ERROR_RE = re.compile(
    r"^([^\s:][^:]*\.(?:c|cpp|cxx|cc|h|hpp)):(\d+):\d+:\s+error:",
    re.MULTILINE,
)


def _strip_dftracer_c_macros(content: str) -> str:
    """Remove all dftracer macros injected by :func:`_annotate_c_source` from source text.

    Performs an idempotent, best-effort removal of:

    * The ``#include <dftracer/dftracer.h>`` header line.
    * Every single-line dftracer macro call matched by
      :data:`_DFTRACER_MACRO_LINE_RE`.
    * The ``TODO: add DFTRACER_FINI()`` comment inserted in entry files
      (both C and Python variants).
    * Runs of three or more consecutive blank lines left by macro removal,
      collapsed to at most two blank lines to preserve readability.

    Other code in ``content`` is left strictly unchanged.

    Args:
        content: The full text of an annotated C, C++, or Python source file.

    Returns:
        The source text with all dftracer instrumentation removed.
    """
    content = content.replace(f"{_C_INCLUDE}\n", "")
    content = _DFTRACER_MACRO_LINE_RE.sub("", content)
    content = content.replace("\n/* TODO: add DFTRACER_FINI() before process exit */\n", "\n")
    content = content.replace("\n# TODO: call DFTRACER_FINI() before process exit\n", "\n")
    # Collapse any runs of blank lines left by macro removal
    content = re.sub(r"\n{3,}", "\n\n", content)
    return content


def _fix_dftracer_annotation_errors(ann: Path, compiler_stderr: str) -> List[str]:
    """Roll back dftracer macros from annotated files that caused compiler errors.

    Parses the compiler's stderr output, identifies source files where injected
    dftracer macros triggered errors, strips those macros from the affected
    files under ``ann/``, and returns the list of relative paths that were
    cleaned.  The cleaned files can be recompiled immediately without dftracer
    instrumentation; the calling agent should then attempt to re-annotate the
    troublesome files manually with more conservative placement.

    A file is only cleaned when at least one of the following conditions holds:

    * The failing diagnostic line is on or immediately adjacent to a line
      containing a dftracer macro symbol (``DFTRACER``, ``data_fn``,
      ``initialize_region``, ``finalize_region``).
    * The overall stderr mentions dftracer symbols, which indicates a link-time
      or type error originating from the instrumentation headers.

    Args:
        ann: Absolute path to the ``annotated/`` workspace directory.  All
            source file paths extracted from ``compiler_stderr`` are resolved
            relative to this directory.
        compiler_stderr: The raw standard-error text produced by the compiler
            or build system.

    Returns:
        A list of file paths relative to ``ann`` that were successfully cleaned.
        Returns an empty list when no dftracer-related errors were detected or
        when no affected files could be located under ``ann``.

    Note:
        Files are written back in-place inside ``ann``.  The original files in
        ``source/`` are never touched.
    """
    if not compiler_stderr.strip():
        return []

    # Determine whether the errors are dftracer-related at all
    dftracer_related = (
        "DFTRACER" in compiler_stderr
        or "data_fn" in compiler_stderr
        or "initialize_region" in compiler_stderr
        or "finalize_region" in compiler_stderr
    )

    # Collect which source files have errors
    error_files: Dict[str, Set[int]] = {}
    for m in _COMPILER_ERROR_RE.finditer(compiler_stderr):
        error_files.setdefault(m.group(1), set()).add(int(m.group(2)))

    if not error_files and not dftracer_related:
        return []

    fixed: List[str] = []
    for raw_path, error_lines in error_files.items():
        # Resolve file path relative to annotated/ directory
        candidate: Optional[Path] = ann / raw_path
        if not candidate.exists():
            hits = list(ann.rglob(Path(raw_path).name))
            candidate = hits[0] if hits else None
        if not candidate or not candidate.exists():
            continue

        content = candidate.read_text(errors="ignore")
        lines = content.splitlines()

        # Only strip if the failing line is on or near one of our macros, OR if
        # the overall stderr mentions dftracer symbols (link/type errors)
        near_macro = any(
            1 <= ln <= len(lines) and (
                "DFTRACER" in lines[ln - 1]
                or "data_fn" in lines[ln - 1]
                or (ln > 1 and "DFTRACER" in lines[ln - 2])
                or (ln < len(lines) and "DFTRACER" in lines[ln])
            )
            for ln in error_lines
        )
        if not (near_macro or dftracer_related):
            continue

        new_content = _strip_dftracer_c_macros(content)
        if new_content != content:
            candidate.write_text(new_content)
            fixed.append(str(candidate.relative_to(ann)))

    return fixed


#: Matches C-string parameters whose names suggest they carry a file path,
#: directory path, file name, mode string, command string, or buffer pointer.
#: Capture group 1 is the parameter name.  Used by
#: :func:`_metadata_update_calls` to emit
#: ``DFTRACER_C_FUNCTION_UPDATE_STR`` / ``DFTRACER_CPP_FUNCTION_UPDATE``
#: calls so dftracer can record the actual runtime value alongside the trace.
_STR_PARAM_RE = re.compile(
    r"\b(?:const\s+)?char\s*\*+\s*(\w*(?:file|path|name|dir|mode|cmd|key|buf)\w*)\b",
    re.IGNORECASE,
)

#: Matches integer-typed parameters whose names suggest they carry a size,
#: count, offset, file descriptor, or flags value.  Capture group 1 is the
#: parameter name.  Used by :func:`_metadata_update_calls` to emit
#: ``DFTRACER_C_FUNCTION_UPDATE_INT`` calls (C only; C++ uses the templated
#: ``DFTRACER_CPP_FUNCTION_UPDATE`` for both types).
_INT_PARAM_RE = re.compile(
    r"\b(?:size_t|off_t|ssize_t|uint64_t|int64_t|int|long)\s+(\w*(?:size|count|len|offset|fd|flags|bytes|num)\w*)\b",
    re.IGNORECASE,
)


def _metadata_update_calls(params: str, is_cpp: bool, indent: str = "  ") -> List[str]:
    """Generate DFTRACER metadata-update macro calls for trackable function parameters.

    Scans the raw parameter list string of a function signature and produces
    one update call per recognisable parameter:

    * String/path parameters (matched by :data:`_STR_PARAM_RE`) →
      ``DFTRACER_C_FUNCTION_UPDATE_STR`` (C) or
      ``DFTRACER_CPP_FUNCTION_UPDATE`` (C++).
    * Integer/size parameters (matched by :data:`_INT_PARAM_RE`) →
      ``DFTRACER_C_FUNCTION_UPDATE_INT`` (C only; C++ uses the same
      ``DFTRACER_CPP_FUNCTION_UPDATE`` template which is covered by the
      string branch for C++).

    Duplicate parameter names are silently skipped so that a parameter matched
    by both patterns only generates a single call.

    Args:
        params: The raw text between the outermost ``(`` and ``)`` of a
            function declaration, e.g. ``"const char *path, size_t count"``.
        is_cpp: ``True`` when annotating a C++ translation unit
            (``.cpp``/``.cxx``/``.cc``); ``False`` for C (``.c``/``.h``).
        indent: Whitespace prefix prepended to each emitted line.  Defaults
            to two spaces to align with the indentation style used inside
            function bodies by :func:`_annotate_c_source`.

    Returns:
        A list of macro call strings (each already terminated with ``;``),
        ready to be joined with newlines and inserted into the function body.
        Returns an empty list when no trackable parameters are found.
    """
    calls: List[str] = []
    seen: set = set()
    for m in _STR_PARAM_RE.finditer(params):
        name = m.group(1)
        if name and name not in seen:
            seen.add(name)
            if is_cpp:
                calls.append(f'{indent}DFTRACER_CPP_FUNCTION_UPDATE("{name}", {name});')
            else:
                calls.append(f'{indent}DFTRACER_C_FUNCTION_UPDATE_STR("{name}", {name});')
    for m in _INT_PARAM_RE.finditer(params):
        name = m.group(1)
        if name and name not in seen:
            seen.add(name)
            if not is_cpp:
                calls.append(f'{indent}DFTRACER_C_FUNCTION_UPDATE_INT("{name}", (int){name});')
    return calls


def _annotate_c_source(content: str, filepath: Path, is_entry: bool) -> str:
    """Inject dftracer C/C++ tracing macros into a source file's text.

    The transformation is idempotent: if ``content`` already contains the
    dftracer ``#include``, the function returns it unchanged.

    The injection proceeds in three ordered steps:

    **Step 1 — function-open annotations**
        A regex scans for function-header patterns of the form
        ``<return-type> <name>(<params>) {``.  For each match whose name is
        not in :data:`_C_KEYWORDS`:

        * C files: ``DFTRACER_C_FUNCTION_START();`` is inserted immediately
          after the opening ``{``.
        * C++ files: ``DFTRACER_CPP_FUNCTION();`` is inserted instead (the
          C++ macro captures the function name automatically via
          ``__func__``).
        * Optional ``DFTRACER_*_FUNCTION_UPDATE_*`` calls are appended for
          recognisable string and integer parameters
          (see :func:`_metadata_update_calls`).

    **Step 2 — entry-point INIT (entry files only)**
        ``DFTRACER_C_INIT(nullptr, nullptr, nullptr)`` or
        ``DFTRACER_CPP_INIT(nullptr, nullptr, nullptr)`` is inserted at the
        start of ``main()`` so the tracer is initialised before any region is
        entered.

    **Step 3 — function-close annotations (C files only)**
        :func:`_finalize_c_ends_and_fini` is called to insert
        ``DFTRACER_C_FUNCTION_END()`` before every ``return`` statement and
        at the fall-through closing brace of each annotated function, and to
        insert ``DFTRACER_C_FINI()`` at every exit point of ``main()``
        (entry files only).  This step is skipped for C++ files because the
        C++ ``DFTRACER_CPP_FUNCTION()`` macro uses RAII and does not require
        explicit ``END``/``FINI`` calls.

    Args:
        content: The full text of the source file to annotate.
        filepath: Absolute or relative path to the source file.  The suffix
            (``.cpp``, ``.cxx``, ``.cc`` vs ``.c``, ``.h``) determines
            whether C or C++ macro variants are used.
        is_entry: ``True`` when this file contains the program's entry point
            (i.e. it defines ``main()``).  Controls whether INIT/FINI macros
            are emitted.

    Returns:
        The annotated source text.  If the file was already annotated (the
        dftracer ``#include`` was present), the original ``content`` is
        returned unmodified.

    Note:
        The function-header regex may occasionally match constructor
        initialisers or complex template signatures.  The :data:`_C_KEYWORDS`
        guard prevents the most common false positives, but unusual C++
        patterns may still require manual review.
    """
    if _C_INCLUDE in content:
        return content

    is_cpp = filepath.suffix.lower() in {".cpp", ".cxx", ".cc"}

    lines = content.splitlines(keepends=True)
    last_inc = max(
        (i for i, ln in enumerate(lines) if ln.strip().startswith("#include")),
        default=-1,
    )
    out: List[str] = []
    for i, ln in enumerate(lines):
        out.append(ln)
        if i == last_inc:
            out.append(f"{_C_INCLUDE}\n")

    result = "".join(out)

    # Step 1: inject DFTRACER_C_FUNCTION_START() / DFTRACER_CPP_FUNCTION() and
    # parameter metadata at the opening of every real function body.
    # Group 2 captures the function name so we can reject C/C++ keywords (if/else/for/…).
    _FN_HEADER_RE = re.compile(
        r"(\b\w[\w\s\*:<>]*\s+(\w+)\s*\(([^)]*)\)\s*(?:const\s*)?(?:override\s*)?(?:noexcept\s*)?\{)"
    )

    def _inject_fn_open(m: re.Match) -> str:
        header = m.group(1)
        fn_name = m.group(2)
        params = m.group(3)
        # Skip control-flow keywords that look like functions to the regex
        if fn_name in _C_KEYWORDS:
            return header
        if is_cpp:
            injected = f"{header}\n  DFTRACER_CPP_FUNCTION();"
        else:
            injected = f"{header}\n  DFTRACER_C_FUNCTION_START();"
        meta_calls = _metadata_update_calls(params, is_cpp)
        if meta_calls:
            injected += "\n" + "\n".join(meta_calls)
        return injected

    result = _FN_HEADER_RE.sub(_inject_fn_open, result)

    # Step 2 (entry file only): insert DFTRACER_C_INIT *before* DFTRACER_C_FUNCTION_START
    # in main() so the tracer is initialized before any region/metadata calls are made.
    if is_entry:
        init_call = (
            "DFTRACER_CPP_INIT(nullptr, nullptr, nullptr);"
            if is_cpp
            else "DFTRACER_C_INIT(nullptr, nullptr, nullptr);"
        )
        result = re.sub(
            r"(int\s+main\s*\([^)]*\)\s*\{)",
            r"\1\n  " + init_call,
            result,
            count=1,
        )

    # Step 3 (C only): inject FUNCTION_END before every return, FUNCTION_END at the
    # closing } of void/fallthrough functions, and FINI in the entry (main) function.
    if not is_cpp:
        result = _finalize_c_ends_and_fini(result, is_entry)

    return result


def _finalize_c_ends_and_fini(content: str, is_entry: bool) -> str:
    """Insert ``DFTRACER_C_FUNCTION_END`` and ``DFTRACER_C_FINI`` calls into C source.

    This is the second annotation pass applied to C (not C++) files after
    :func:`_annotate_c_source` has already inserted
    ``DFTRACER_C_FUNCTION_START()`` calls.  It is safe to call on text that
    has already been processed — if the required macros are already present
    immediately before a ``return`` or closing brace, no duplicate is added.

    **For every function that contains** ``DFTRACER_C_FUNCTION_START()``:

    * ``DFTRACER_C_FUNCTION_END();`` is inserted before every ``return``
      statement found within the function body.
    * ``DFTRACER_C_FUNCTION_END();`` is inserted before the closing ``}`` of
      the function when the last real statement is neither a ``return`` nor an
      already-present ``END`` (i.e. for ``void`` functions and functions with
      fall-through control flow).

    **For** ``main()`` **(entry file only, detected by proximity of**
    ``DFTRACER_C_INIT`` **within ±4 lines of START)**:

    * ``DFTRACER_C_FINI();`` is inserted immediately before each
      ``DFTRACER_C_FUNCTION_END();`` that is about to be emitted at a
      ``return`` site.
    * ``DFTRACER_C_FINI();`` is inserted before any ``exit``/``_exit``/
      ``_Exit``/``quick_exit``/``abort`` call anywhere inside the function,
      because those calls terminate the process without returning to ``main``.

    The insertion order is carefully maintained: when both ``FINI`` and
    ``END`` are needed before a ``return``, ``FINI`` appears first so the
    tracer is shut down before the region is closed.  This is achieved by
    appending ``FINI`` to the insertion list before ``END``, then applying
    all insertions in reverse line order to keep earlier indices stable.

    Args:
        content: The full text of an already ``FUNCTION_START``-annotated C
            source file.
        is_entry: ``True`` when this file contains ``main()`` so that INIT and
            FINI macros have been (or will be) emitted for the entry point.

    Returns:
        The source text with all ``FUNCTION_END`` and ``FINI`` calls inserted.

    Note:
        Brace matching is done with a simple character-level depth counter and
        will not correctly handle braces inside string literals, character
        literals, or ``#if 0`` blocks.  For the vast majority of real-world C
        code this is sufficient; unusual patterns may result in misplaced
        ``END`` calls that must be corrected manually.
    """
    END = "DFTRACER_C_FUNCTION_END()"
    FINI = "DFTRACER_C_FINI()"
    _EXIT_RE = re.compile(r"\b(?:exit|_exit|_Exit|quick_exit|abort)\s*\(")

    lines = content.splitlines(keepends=True)
    insertions: List[Tuple[int, str]] = []  # (line_index, text to insert before that line)

    # Find each annotated function by its DFTRACER_C_FUNCTION_START() line
    for si, ln in enumerate(lines):
        if "DFTRACER_C_FUNCTION_START()" not in ln:
            continue

        fn_indent = len(ln) - len(ln.lstrip())
        ind = " " * fn_indent  # same indent as the START call

        # Determine if this is main (DFTRACER_C_INIT is within ±4 lines of START)
        search = range(max(0, si - 4), min(len(lines), si + 5))
        is_main = is_entry and any("DFTRACER_C_INIT" in lines[k] for k in search)

        # Brace-count from just after START to find the function's closing }
        depth = 1
        j = si + 1
        while j < len(lines) and depth > 0:
            for ch in lines[j]:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        break
            if depth > 0:
                j += 1
        fn_end = j  # index of the line that contains the closing }

        # Scan lines inside this function
        for k in range(si + 1, fn_end):
            s = lines[k].strip()
            if not s or s.startswith("//") or s.startswith("*"):
                continue
            k_ind = " " * (len(lines[k]) - len(lines[k].lstrip()))
            prev = lines[k - 1].strip() if k > 0 else ""

            # Before return statements (skip if END is already the previous line)
            if re.match(r"return\b", s) and END not in prev:
                # list.insert(k, x) pushes the previous occupant of k to k+1.
                # Appending in stable-sort order means the first-appended runs first.
                # To get END → FINI → return we need the LAST insert to land at k
                # (i.e. END), so append FINI first, then END.
                if is_main and FINI not in prev:
                    insertions.append((k, f"{k_ind}{FINI};\n"))
                insertions.append((k, f"{k_ind}{END};\n"))

            # Before process-exit calls anywhere — process terminates here so FINI is needed
            elif _EXIT_RE.search(s) and FINI not in prev:
                insertions.append((k, f"{k_ind}{FINI};\n"))

        # Handle void / fallthrough functions: add END before the closing }
        # if the last real statement is not already a return or END
        m = fn_end - 1
        while m > si and not lines[m].strip():
            m -= 1
        last = lines[m].strip() if m > si else ""
        if last and END not in last and not re.match(r"return\b", last):
            close_ind = ind  # same indent as START
            if is_main and FINI not in last:
                insertions.append((fn_end, f"{close_ind}{FINI};\n"))
            insertions.append((fn_end, f"{close_ind}{END};\n"))

    # Apply insertions in reverse order so earlier indices stay valid
    result = list(lines)
    for idx, text in sorted(insertions, key=lambda x: x[0], reverse=True):
        result.insert(idx, text)

    return "".join(result)


# ---------------------------------------------------------------------------
# Annotation helpers — Python
# ---------------------------------------------------------------------------

#: The import statement inserted into annotated Python files after the last
#: existing ``import``/``from`` line.  Provides:
#:
#: * ``dft_fn`` — decorator applied to every top-level ``def`` by
#:   :func:`_annotate_python_source`.
#: * ``DFTRACER_INIT`` — called once at module start in entry files.
#: * ``DFTRACER_FINI`` — should be called before the process exits (a
#:   ``# TODO`` comment is inserted as a reminder since the exact location
#:   depends on user code structure).
_PY_IMPORT = "from dftracer.logger import dft_fn, DFTRACER_INIT, DFTRACER_FINI"


def _annotate_python_source(content: str, is_entry: bool) -> str:
    """Inject dftracer Python decorators and init/fini stubs into a source file.

    The transformation is idempotent: if ``content`` already references the
    ``dftracer`` package, the function returns it unchanged.

    The injection proceeds as follows:

    1. The :data:`_PY_IMPORT` line is inserted after the last ``import`` or
       ``from … import`` statement in the file.
    2. For entry files, ``DFTRACER_INIT(log_file=None, data_dirs=None,
       process_id=None)`` is emitted immediately after the import line.
    3. Every top-level ``def`` statement is prefixed with ``@dft_fn`` so that
       all module-level functions are traced automatically.
    4. For entry files, a ``# TODO`` comment reminding the developer to call
       ``DFTRACER_FINI()`` before process exit is appended at the end of the
       file.

    Args:
        content: The full text of the Python source file to annotate.
        is_entry: ``True`` when this file is the program's entry point (i.e.
            it is executed as ``__main__`` or contains the top-level
            application startup code).  Controls whether ``DFTRACER_INIT``
            is emitted and the ``FINI`` reminder is appended.

    Returns:
        The annotated source text, or the original ``content`` unchanged if
        the file already imports from ``dftracer``.
    """
    if "dftracer" in content:
        return content

    lines = content.splitlines(keepends=True)
    last_imp = max(
        (i for i, ln in enumerate(lines)
         if ln.strip().startswith(("import ", "from "))),
        default=-1,
    )
    out: List[str] = []
    for i, ln in enumerate(lines):
        out.append(ln)
        if i == last_imp:
            out.append(f"\n{_PY_IMPORT}\n")
            if is_entry:
                out.append(
                    "DFTRACER_INIT(log_file=None, data_dirs=None, process_id=None)\n\n"
                )
    result = "".join(out)

    # Decorate top-level function definitions
    result = re.sub(r"^(def\s)", r"@dft_fn\n\1", result, flags=re.MULTILINE)

    if is_entry:
        result += "\n# TODO: call DFTRACER_FINI() before process exit\n"
    return result


# ---------------------------------------------------------------------------
# Annotation coverage report
# ---------------------------------------------------------------------------

#: The macro name used as a sentinel to detect annotated C functions.
#: A function body in an annotated C file is considered instrumented when
#: this string appears on any line inside the body.
#: C++ functions use ``DFTRACER_CPP_FUNCTION`` instead but that variant is
#: not currently tracked by the coverage report (C++ functions are included
#: in totals only when they appear in files that changed between ``source/``
#: and ``annotated/``).
_DFTRACER_C_START = "DFTRACER_C_FUNCTION_START"

#: The decorator line used as a sentinel to detect annotated Python functions.
#: A ``def`` statement in an annotated Python file is considered instrumented
#: when the immediately preceding non-blank line equals this string exactly.
_DFTRACER_PY_DEC = "@dft_fn"

#: A subset of :data:`_C_KEYWORDS` used specifically by the coverage-report
#: helpers (:func:`_c_func_at_line`, :func:`_find_all_c_functions`).
#: These identifiers appear at the start of a line followed by ``(`` and
#: therefore look like function definitions to a simple regex, but they are
#: control-flow constructs or type keywords that must be excluded from the
#: function list.
_NOT_FUNC = frozenset({
    "if", "else", "for", "while", "do", "switch", "return", "case",
    "break", "continue", "goto", "default", "sizeof", "typeof",
    "typedef", "struct", "union", "enum", "namespace", "class",
    "template", "new", "delete", "throw", "try", "catch",
})


def _c_func_at_line(line: str) -> Optional[str]:
    """Return the function name if ``line`` looks like a C/C++ function definition.

    Uses a set of lightweight heuristics rather than a full parser:

    * The line must start at column 0 (no leading whitespace, ``#``, ``/``,
      or ``*``), ruling out indented code, preprocessor directives, and
      comments.
    * The line must contain ``(`` and must not end with ``;``, which filters
      out forward declarations and ``typedef`` lines.
    * The last identifier before ``(`` is extracted and checked against
      :data:`_NOT_FUNC` to exclude control-flow keywords.

    Args:
        line: A single source line (with or without a trailing newline).

    Returns:
        The function name string if the line appears to be a function
        definition opening, or ``None`` otherwise.
    """
    stripped = line.rstrip()
    # Must start at column 0 and not be a preprocessor directive or comment
    if not stripped or stripped[0] in (' ', '\t', '#', '/', '*'):
        return None
    # Must contain '(' and must NOT end with ';' (declarations end with ';')
    if '(' not in stripped or stripped.endswith(';'):
        return None
    # Extract the last identifier before '('
    m = re.match(r'^[^(]*?(\w+)\s*\(', stripped)
    if not m:
        return None
    name = m.group(1)
    if name in _NOT_FUNC:
        return None
    return name


def _find_all_c_functions(path: Path) -> List[str]:
    """Return an ordered list of C/C++ function definition names in a source file.

    Uses :func:`_c_func_at_line` to identify candidate function-definition
    lines and then confirms each candidate by checking that an opening ``{``
    appears within the next eight lines before any ``;`` (which would indicate
    a declaration rather than a definition).  Duplicate names are suppressed
    so each function is listed once in source order.

    Args:
        path: Absolute path to the C or C++ source file to scan.

    Returns:
        A list of function names in the order they are defined, with
        duplicates removed.  Returns an empty list if the file cannot be read
        or contains no detectable function definitions.
    """
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return []

    funcs: List[str] = []
    seen: Set[str] = set()
    n = len(lines)
    for i, line in enumerate(lines):
        name = _c_func_at_line(line)
        if not name:
            continue
        # Confirm a `{` appears within the next 8 lines before any `;`
        for j in range(i, min(i + 8, n)):
            jl = lines[j]
            if '{' in jl:
                if name not in seen:
                    funcs.append(name)
                    seen.add(name)
                break
            if j > i and ';' in jl:
                break
    return funcs


def _find_annotated_c_functions(path: Path) -> Set[str]:
    """Return the names of C/C++ functions in ``path`` that contain a dftracer START macro.

    Iterates through the file line by line, tracking the current function
    context with :func:`_c_func_at_line`.  Whenever a line containing
    :data:`_DFTRACER_C_START` is encountered and a current function context
    is known, that function name is added to the result set.

    Args:
        path: Absolute path to the annotated C or C++ source file to inspect.

    Returns:
        A set of function names that are instrumented with
        ``DFTRACER_C_FUNCTION_START``.  Returns an empty set if the file
        cannot be read.
    """
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return set()

    annotated: Set[str] = set()
    current_func: Optional[str] = None
    for line in lines:
        name = _c_func_at_line(line)
        if name:
            current_func = name
        if _DFTRACER_C_START in line and current_func:
            annotated.add(current_func)
    return annotated


def _find_all_py_functions(path: Path) -> List[str]:
    """Return function and method names defined in a Python source file.

    Scans for ``def`` and ``async def`` at any indentation level, capturing
    both module-level functions and class methods.  Dunder names are included
    (e.g. ``__getitem__``) because they may carry dftracer annotations.
    Duplicate names (same name in multiple classes) appear once each in the
    returned list, in first-seen order.

    Args:
        path: Absolute path to the Python source file to scan.

    Returns:
        A list of unique function/method names in source order.  Returns an
        empty list if the file cannot be read or contains no ``def`` statements.
    """
    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return []
    seen: set = set()
    result: List[str] = []
    for ln in lines:
        m = re.match(r'^\s*(?:async\s+)?def\s+(\w+)', ln)
        if m:
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def _find_annotated_py_functions(path: Path) -> Set[str]:
    """Return names of Python functions annotated with any dftracer decorator or API call.

    Detects three annotation styles:

    1. ``@dft_fn`` decorator (low-level function tracing)
    2. ``@dft_ai`` / ``@dft_ai.<sub>`` decorators (AI/ML pipeline API)
    3. ``dft_ai.<anything>`` call inside a method/function body (e.g.
       ``dft_ai.initialize_log()``, ``dft_ai.compute.step.start()``,
       ``dft_ai.update()``) — the enclosing ``def`` is considered annotated.

    Args:
        path: Absolute path to the annotated Python source file to inspect.

    Returns:
        A set of function/method names that carry any dftracer annotation.
        Returns an empty set if the file cannot be read.
    """
    # Patterns for decorator-based annotations
    _DEC_PAT = re.compile(r'^@(dft_fn|dft_ai(\.\w+)*)\s*$')
    # Pattern for dft_ai API calls inside a body (any dft_ai.* usage)
    _CALL_PAT = re.compile(r'\bdft_ai\.')
    # Pattern to capture def name (handles indented methods too)
    _DEF_PAT = re.compile(r'^\s*def\s+(\w+)')

    try:
        lines = path.read_text(errors="ignore").splitlines()
    except OSError:
        return set()

    annotated: Set[str] = set()
    prev_dft_dec = False
    current_func: str | None = None

    for ln in lines:
        stripped = ln.strip()

        # Decorator check — set flag, keep it sticky through stacked decorators
        if _DEC_PAT.match(stripped):
            prev_dft_dec = True
            continue

        m_def = _DEF_PAT.match(ln)
        if m_def:
            current_func = m_def.group(1)
            if prev_dft_dec:
                annotated.add(current_func)
            prev_dft_dec = False
        elif stripped.startswith('@'):
            # Another decorator on the same function — don't clear the flag
            pass
        else:
            prev_dft_dec = False

        # Body call check: any dft_ai.* usage annotates the enclosing function
        if current_func and _CALL_PAT.search(ln):
            annotated.add(current_func)

    return annotated


def _parse_annotation_status(ws: Path) -> Dict[str, Dict[str, Dict[str, str]]]:
    """Parse ``annotation_logs/annotation_status.md`` into a structured mapping.

    Reads the Markdown status table produced by the annotation pipeline and
    returns a nested dictionary that maps each file name to a per-function
    record containing the annotation status, complexity rating, and any
    associated notes or skip reason.

    The file may contain two table formats, distinguished by the current
    Markdown section heading:

    * **Main table** (five or more columns):
      ``| File | Function | Status | comp | … | Notes |``
      Status cells are parsed for emoji / keyword tokens:

      - ``✅ DONE`` → ``'annotated'``
      - ``⏭️`` or ``SKIP`` → ``'skipped'``
      - ``❌`` or ``INCLUDE`` → ``'failed'``
      - ``⚠️`` or ``PENDING`` → ``'pending'``

    * **Skipped table** (three columns, under a heading containing ``skip``
      or ``rule 0``):
      ``| File | Function | Reason |``
      All entries in this section are recorded with status ``'skipped'``.

    Args:
        ws: Absolute path to the workspace root directory.  The status file
            is expected at ``ws/annotation_logs/annotation_status.md``.

    Returns:
        A nested dictionary of the form::

            {
                "<filename>": {
                    "<function_name>": {
                        "status": "<annotated|skipped|failed|pending>",
                        "comp": "<complexity string or empty>",
                        "reason": "<notes or skip reason>",
                    },
                    …
                },
                …
            }

        Returns an empty dictionary if the status file does not exist.

    Note:
        Header rows (where the ``File`` or ``Function`` column contains the
        literal words ``file`` or ``function``) and separator rows (``---``)
        are skipped automatically.
    """
    status_file = ws / "annotation_logs" / "annotation_status.md"
    if not status_file.exists():
        return {}

    result: Dict[str, Dict[str, Dict[str, str]]] = {}
    in_skipped_section = False

    for line in status_file.read_text(errors="ignore").splitlines():
        # Track which section we're in
        if line.startswith('#'):
            in_skipped_section = 'skip' in line.lower() or 'rule 0' in line.lower()
            continue

        if not line.startswith('|') or '---' in line:
            continue

        parts = [p.strip() for p in line.split('|')]
        # Remove empty first/last entries from leading/trailing '|'
        parts = [p for p in parts if p != '']
        if len(parts) < 2:
            continue

        fname, func = parts[0], parts[1]
        if not fname or not func or fname.lower() in ('file', 'function'):
            continue

        if in_skipped_section or len(parts) < 4:
            # 3-column skipped table: File | Function | Reason
            reason = parts[2] if len(parts) > 2 else "Rule 0"
            result.setdefault(fname, {})[func] = {
                "status": "skipped",
                "comp": "",
                "reason": reason,
            }
        else:
            # Main table: File | Function | Status | comp | ... | Notes
            status_raw = parts[2]
            comp = parts[3] if len(parts) > 3 else ""
            notes = parts[-1] if len(parts) > 6 else (parts[4] if len(parts) > 4 else "")

            if '✅' in status_raw and 'DONE' in status_raw:
                status = 'annotated'
            elif '⏭️' in status_raw or 'SKIP' in status_raw.upper():
                status = 'skipped'
            elif '❌' in status_raw or 'INCLUDE' in status_raw.upper():
                status = 'failed'
            elif '⚠️' in status_raw or 'PENDING' in status_raw.upper():
                status = 'pending'
            else:
                continue

            result.setdefault(fname, {})[func] = {
                "status": status,
                "comp": comp if comp not in ('—', '-') else "",
                "reason": notes,
            }
    return result


def _diff_modified_files(source_dir: Path, annotated_dir: Path) -> List[str]:
    """Return paths of C/C++/Python files that differ between ``source/`` and ``annotated/``.

    Scans every file under ``annotated_dir`` recursively, restricts the
    comparison to recognised source-file extensions
    (``.c``, ``.h``, ``.cpp``, ``.cxx``, ``.cc``, ``.hpp``, ``.hxx``,
    ``.py``), and compares each file's content against its counterpart in
    ``source_dir``.  Files that exist only in ``annotated_dir`` (no
    counterpart in ``source_dir``) are silently skipped.

    Args:
        source_dir: Absolute path to the original ``source/`` directory
            containing unmodified source files.
        annotated_dir: Absolute path to the ``annotated/`` directory
            containing potentially modified copies of those files.

    Returns:
        A sorted list of file paths relative to ``annotated_dir`` where the
        content in ``annotated_dir`` differs from the content in
        ``source_dir``.
    """
    changed: List[str] = []
    for ann_file in sorted(annotated_dir.rglob("*")):
        if not ann_file.is_file():
            continue
        if ann_file.suffix.lower() not in {'.c', '.h', '.cpp', '.cxx', '.cc',
                                             '.hpp', '.hxx', '.py'}:
            continue
        rel = ann_file.relative_to(annotated_dir)
        src_file = source_dir / rel
        if not src_file.exists():
            continue
        if ann_file.read_text(errors="ignore") != src_file.read_text(errors="ignore"):
            changed.append(str(rel))
    return changed


#: Recognised source-file extensions used when aligning annotated/source roots.
_SOURCE_EXTS = {'.c', '.h', '.cpp', '.cxx', '.cc', '.hpp', '.hxx', '.py'}


def _count_counterparts(source_dir: Path, ann_root: Path) -> int:
    """Count recognised source files under ``ann_root`` that have a counterpart in ``source_dir``.

    Used to auto-detect the correct annotated root when some pipelines nest the
    annotated tree one directory deeper than ``ws/annotated`` (e.g. Flash-X
    copies ``ws/<run>/source`` → ``ws/annotated/source``, so annotated files
    live at ``ws/annotated/source/...`` while their originals live at
    ``ws/source/...``).  A rel-path computed against the wrong root never
    resolves, yielding a false 0/0 coverage report.
    """
    count = 0
    for f in ann_root.rglob("*"):
        if not f.is_file() or f.suffix.lower() not in _SOURCE_EXTS:
            continue
        if (source_dir / f.relative_to(ann_root)).exists():
            count += 1
    return count


def _resolve_annotated_root(source_dir: Path, ann_dir: Path) -> Path:
    """Return the annotated root whose rel-paths best align with ``source_dir``.

    Considers ``ann_dir`` itself and its immediate sub-directories (one level),
    and picks whichever maximises the number of annotated files that have an
    existing counterpart under ``source_dir``.  Falls back to ``ann_dir`` when
    nothing matches so the flat/common layout is preserved.
    """
    candidates = [ann_dir]
    try:
        candidates += [d for d in sorted(ann_dir.iterdir()) if d.is_dir()]
    except OSError:
        pass
    best_root = ann_dir
    best_score = _count_counterparts(source_dir, ann_dir)
    for cand in candidates[1:]:
        score = _count_counterparts(source_dir, cand)
        if score > best_score:
            best_root, best_score = cand, score
    return best_root


def _generate_annotation_report(ws: Path, run_id: str) -> Dict[str, Any]:
    """Build a structured annotation coverage report for a workspace run.

    Compares the original source files in ``ws/source/`` against the
    annotated copies in ``ws/annotated/``, detects all C/C++ and Python
    function definitions, checks which functions carry dftracer macros, and
    cross-references ``annotation_logs/annotation_status.md`` for skip, fail,
    and pending reasons.

    Per-function status resolution (in priority order):

    1. If the function appears annotated in the ``annotated/`` file
       (``DFTRACER_C_FUNCTION_START`` for C, ``@dft_fn`` for Python) →
       ``'annotated'``.
    2. Else if the annotation-status log records it as ``'skipped'`` →
       ``'skipped'`` (with the logged skip reason).
    3. Else if the log records it as ``'failed'`` → ``'failed'``.
    4. Else if the log records it as ``'pending'`` → ``'pending'``.
    5. Otherwise → ``'not_annotated'`` (no annotation and not in log).

    The coverage percentage is computed over *annotatable* functions only
    (total minus skipped), so intentionally skipped trivial functions do not
    penalise the score.

    Args:
        ws: Absolute path to the workspace root.  Must contain ``source/``
            and ``annotated/`` subdirectories; may contain
            ``annotation_logs/annotation_status.md``.
        run_id: An opaque identifier for the current annotation run, included
            verbatim in the returned dictionary for traceability.

    Returns:
        A JSON-serialisable dictionary with the following top-level keys:

        * ``run_id`` (*str*): the value passed in ``run_id``.
        * ``annotation_log_present`` (*bool*): whether
          ``annotation_logs/annotation_status.md`` exists.
        * ``summary`` (*dict*): aggregate counts —
          ``relevant_files``, ``total_functions``, ``annotated``,
          ``skipped``, ``failed_or_missing``, ``pending``,
          ``coverage_pct``.
        * ``files`` (*list[dict]*): one entry per modified file, each with
          ``file``, ``total_functions``, ``annotated``, ``skipped``,
          ``failed``, ``pending``, ``coverage_pct``, and ``functions``
          (a list of per-function dicts with ``function``, ``status``,
          ``comp``, ``reason``).

        If ``source/`` or ``annotated/`` is missing, returns a minimal error
        dictionary with keys ``error`` and ``run_id``.

    Raises:
        No exceptions are raised directly; filesystem errors during file reads
        are handled inside the helper functions and result in empty function
        lists rather than propagated exceptions.
    """
    source_dir = ws / "source"
    ann_dir = ws / "annotated"
    if not source_dir.exists() or not ann_dir.exists():
        return {
            "error": "source/ or annotated/ directory missing",
            "run_id": run_id,
        }

    # Some pipelines nest the annotated tree one level deeper than ws/annotated
    # (e.g. Flash-X copies ws/<run>/source → ws/annotated/source), so the
    # annotated files live at ws/annotated/source/... while their originals
    # live at ws/source/...  Auto-detect the annotated root that aligns with
    # source_dir, otherwise the report falsely shows 0/0 coverage.
    ann_dir = _resolve_annotated_root(source_dir, ann_dir)

    status_map = _parse_annotation_status(ws)
    changed_files = _diff_modified_files(source_dir, ann_dir)

    total_funcs = 0
    total_annotated = 0
    total_skipped = 0
    total_failed = 0
    total_pending = 0

    file_reports: List[Dict[str, Any]] = []

    # Process changed (relevant) files
    for rel in changed_files:
        rel_path = Path(rel)
        src_file = source_dir / rel_path
        ann_file = ann_dir / rel_path
        ext = rel_path.suffix.lower()

        if ext in {'.c', '.h', '.cpp', '.cxx', '.cc', '.hpp', '.hxx'}:
            all_funcs = _find_all_c_functions(src_file)
            ann_funcs = _find_annotated_c_functions(ann_file)
        elif ext == '.py':
            all_funcs = _find_all_py_functions(src_file)
            ann_funcs = _find_annotated_py_functions(ann_file)
        else:
            continue

        if not all_funcs:
            continue

        file_status = status_map.get(rel_path.name, {})
        func_entries: List[Dict[str, Any]] = []

        for func in all_funcs:
            log = file_status.get(func, {})

            if func in ann_funcs:
                fstatus = 'annotated'
                comp = log.get('comp', '')
                reason = None
            elif log.get('status') == 'skipped':
                fstatus = 'skipped'
                comp = ''
                reason = log.get('reason') or 'Rule 0 — trivial function'
            elif log.get('status') == 'failed':
                fstatus = 'failed'
                comp = ''
                reason = log.get('reason') or 'Annotation failed — see annotation_logs'
            elif log.get('status') == 'pending':
                fstatus = 'pending'
                comp = ''
                reason = log.get('reason') or 'Not yet annotated'
            else:
                # Not in log and not annotated — treat as not started
                fstatus = 'not_annotated'
                comp = ''
                reason = 'No annotation found and not in annotation_status.md'

            func_entries.append({
                "function": func,
                "status": fstatus,
                "comp": comp,
                "reason": reason,
            })

        n_ann = sum(1 for f in func_entries if f['status'] == 'annotated')
        n_skip = sum(1 for f in func_entries if f['status'] == 'skipped')
        n_fail = sum(1 for f in func_entries if f['status'] in ('failed', 'not_annotated'))
        n_pend = sum(1 for f in func_entries if f['status'] == 'pending')

        total_funcs += len(func_entries)
        total_annotated += n_ann
        total_skipped += n_skip
        total_failed += n_fail
        total_pending += n_pend

        file_reports.append({
            "file": rel,
            "total_functions": len(func_entries),
            "annotated": n_ann,
            "skipped": n_skip,
            "failed": n_fail,
            "pending": n_pend,
            "coverage_pct": round(100 * n_ann / len(func_entries), 1) if func_entries else 0.0,
            "functions": func_entries,
        })

    annotatable = total_funcs - total_skipped
    coverage_pct = round(100 * total_annotated / annotatable, 1) if annotatable else 0.0

    return {
        "run_id": run_id,
        "annotation_log_present": (ws / "annotation_logs" / "annotation_status.md").exists(),
        "summary": {
            "relevant_files": len(file_reports),
            "total_functions": total_funcs,
            "annotated": total_annotated,
            "skipped": total_skipped,
            "failed_or_missing": total_failed,
            "pending": total_pending,
            "coverage_pct": coverage_pct,
        },
        "files": file_reports,
    }
