"""Source-code parser for extracting function definitions with exact line numbers.

Primary method: clang -Xclang -ast-dump=json (C/C++) or Python's ast module (Python).
Raises ClangNotFoundError if clang is not installed — no regex/ctags fallback.

Returned dicts per function::

    {
        "name":              str,   # function name
        "start_line":        int,   # return-type / signature first line
        "open_brace_line":   int,   # line of opening '{'
        "body_first_line":   int,   # open_brace_line + 1 (insert START here)
        "close_brace_line":  int,   # line of closing '}'
        "exit_lines":        [{"line": int, "type": str}],  # return/exit/abort
        "is_entry_point":    bool,  # True if name == "main" or __main__
        "source":            str,   # "clang" or "ast"
    }
"""
from __future__ import annotations

import ast
import json
import subprocess
from pathlib import Path
from typing import Optional


class ClangNotFoundError(RuntimeError):
    """Raised when the clang binary is not installed."""


# ---------------------------------------------------------------------------
# Cost-estimation constants (used by both C/C++ and Python estimators)
# ---------------------------------------------------------------------------

#: POSIX and stdio I/O syscalls — each hit adds a strong signal to annotate.
_IO_CALLS: frozenset[str] = frozenset({
    "open", "close", "read", "write", "pread", "pwrite",
    "fopen", "fclose", "fread", "fwrite", "fputs", "fgets", "fgetc", "fputc",
    "stat", "fstat", "lstat", "lseek", "lseek64",
    "mmap", "munmap", "mmap2",
    "fsync", "fdatasync", "fallocate", "posix_fallocate", "posix_fadvise",
    "ioctl", "sendfile", "sendfile64",
    "rename", "unlink", "mkdir", "rmdir", "creat", "openat",
    "readdir", "opendir", "closedir",
    "ftruncate", "truncate", "ftruncate64",
    "pread64", "pwrite64",
    "AIO_Read", "AIO_Write",
})

#: Heap / memory-management calls.
_MEM_CALLS: frozenset[str] = frozenset({
    "malloc", "calloc", "realloc", "free",
    "memcpy", "memmove", "memset", "mmap",
})

#: Vendor filesystem function prefixes — always annotate.
_VENDOR_PREFIXES: tuple[str, ...] = (
    "gpfs_", "beegfs_", "llapi_", "cuFile",
    "hdfs_", "daos_", "ceph_", "gfarm_",
)

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def extract_functions(filepath: str | Path) -> list[dict]:
    """Return a list of function-info dicts for *filepath*.

    Raises ClangNotFoundError for C/C++ files when clang is not installed.
    """
    path = Path(filepath)
    suffix = path.suffix.lower()
    if suffix == ".py":
        return _extract_python(path)
    if suffix in (".c", ".cpp", ".cxx", ".cc", ".h", ".hpp"):
        return _extract_c_cpp(path)
    return []


# ---------------------------------------------------------------------------
# C / C++ extraction
# ---------------------------------------------------------------------------

def _extract_c_cpp(path: Path) -> list[dict]:
    result = _try_clang(path)
    if result is None:
        raise ClangNotFoundError(
            "clang binary not found — install clang to enable C/C++ function extraction"
        )
    return result


# ── clang -ast-dump=json ────────────────────────────────────────────────────

def _build_line_offsets(path: Path) -> list[int]:
    """Return a list of byte offsets where each line starts (0-based → 1-based line)."""
    import bisect as _bisect
    text = path.read_bytes()
    offsets = [0]
    for i, b in enumerate(text):
        if b == 10:  # newline
            offsets.append(i + 1)
    return offsets


def _resolve_line(loc: dict, line_offsets: list[int]) -> int:
    """Return 1-based line number from a clang loc dict.

    Clang's -ast-dump=json omits the ``line`` field when consecutive tokens
    are on the same line (delta-compression).  When ``line`` is absent we fall
    back to the ``offset`` field and binary-search the pre-built line-start
    table to recover the correct line number.

    For macro expansions, clang emits ``{"spellingLoc": ..., "expansionLoc": ...}``.
    We use the *expansion* (call-site) location so that brace ranges refer to the
    actual source file, not the macro definition header.
    """
    if "line" in loc:
        return loc["line"]
    if "offset" in loc and line_offsets:
        import bisect as _bisect
        return _bisect.bisect_right(line_offsets, loc["offset"])
    # Macro expansion: use the call-site (expansionLoc), not the definition
    if "expansionLoc" in loc:
        return _resolve_line(loc["expansionLoc"], line_offsets)
    return 0


def _try_clang(path: Path) -> Optional[list[dict]]:
    lang = "c" if path.suffix.lower() == ".c" else "c++"
    try:
        proc = subprocess.run(
            ["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only", "-w",
             f"-x{lang}", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return None
    except subprocess.TimeoutExpired:
        return []

    stdout = proc.stdout
    if not stdout:
        return None

    # Clang sometimes emits diagnostics before the JSON blob
    json_start = stdout.find("{")
    if json_start == -1:
        return None

    try:
        ast_root = json.loads(stdout[json_start:])
    except json.JSONDecodeError:
        return None

    line_offsets = _build_line_offsets(path)
    target = str(path)
    functions: list[dict] = []
    _visit_tu(ast_root, functions, target, line_offsets)
    for fn in functions:
        fn["source"] = "clang"
    return functions


def _visit_tu(ast_root: dict, functions: list[dict],
              target: str, line_offsets: list[int]) -> None:
    """Walk TranslationUnitDecl children tracking current file context."""
    current_file: str = ""
    for node in ast_root.get("inner", []):
        # Update file context from loc if present
        loc = node.get("loc", {})
        if "file" in loc:
            current_file = loc["file"]

        # Skip nodes from other files (system headers etc.)
        if current_file and target not in current_file:
            continue

        kind = node.get("kind", "")
        if kind in ("FunctionDecl", "CXXMethodDecl",
                    "CXXConstructorDecl", "CXXDestructorDecl"):
            info = _extract_func_node(node, line_offsets)
            if info:
                functions.append(info)
        elif kind == "CXXRecordDecl":
            _visit_class(node, functions, target, current_file, line_offsets)
        elif kind == "NamespaceDecl":
            _visit_namespace(node, functions, target, current_file, line_offsets)


def _visit_class(node: dict, functions: list[dict],
                 target: str, current_file: str,
                 line_offsets: list[int]) -> None:
    for child in node.get("inner", []):
        loc = child.get("loc", {})
        child_file = loc.get("file", current_file)
        if child_file and target not in child_file:
            continue
        kind = child.get("kind", "")
        if kind in ("CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl",
                    "FunctionDecl"):
            info = _extract_func_node(child, line_offsets)
            if info:
                functions.append(info)
        elif kind == "CXXRecordDecl":
            _visit_class(child, functions, target,
                         child_file or current_file, line_offsets)


def _visit_namespace(node: dict, functions: list[dict],
                     target: str, current_file: str,
                     line_offsets: list[int]) -> None:
    for child in node.get("inner", []):
        loc = child.get("loc", {})
        child_file = loc.get("file", current_file)
        if child_file and target not in child_file:
            continue
        kind = child.get("kind", "")
        if kind in ("FunctionDecl", "CXXMethodDecl",
                    "CXXConstructorDecl", "CXXDestructorDecl"):
            info = _extract_func_node(child, line_offsets)
            if info:
                functions.append(info)
        elif kind in ("CXXRecordDecl", "NamespaceDecl"):
            _visit_namespace(child, functions, target,
                             child_file or current_file, line_offsets)


def _extract_func_node(node: dict, line_offsets: list[int]) -> Optional[dict]:
    """Build a function-info dict from a FunctionDecl AST node."""
    inner = node.get("inner", [])
    body = next((c for c in inner if c.get("kind") == "CompoundStmt"), None)
    if body is None:
        return None  # declaration only, not definition

    rng = node.get("range", {})
    begin_line = _resolve_line(rng.get("begin", {}), line_offsets)
    # Prefer loc.line for start_line (always present on definition node)
    loc_line = node.get("loc", {}).get("line", begin_line)
    start_line = loc_line if loc_line else begin_line

    body_rng = body.get("range", {})
    open_brace  = _resolve_line(body_rng.get("begin", {}), line_offsets)
    close_brace = _resolve_line(body_rng.get("end",   {}), line_offsets)

    # When open_brace is still 0, the { is on the same line as the signature
    if open_brace == 0:
        open_brace = start_line

    exits: list[dict] = []
    _collect_exits(body, exits, line_offsets)
    exits.sort(key=lambda x: x["line"])

    name = node.get("name", "")
    return {
        "name":            name,
        "start_line":      start_line,
        "open_brace_line": open_brace,
        "body_first_line": open_brace + 1,
        "close_brace_line": close_brace,
        "exit_lines":      exits,
        "is_entry_point":  name == "main",
        "cost_info":       _compute_cost_c_cpp(body),
    }


# ---------------------------------------------------------------------------
# AST-based cost estimation — C / C++
# ---------------------------------------------------------------------------

def _compute_cost_c_cpp(body_node: dict) -> dict:
    """Walk a CompoundStmt AST node and compute runtime-cost metrics.

    Uses the clang AST structure exclusively — no regex or text scanning.
    Counts meaningful node kinds as proxies for I/O, MPI, memory, and
    control-flow complexity.

    Returns a dict with integer counters and a composite ``score``.
    """
    io_calls = 0
    mpi_calls = 0
    mem_calls = 0
    vendor_calls = 0
    loop_count = 0
    branch_count = 0
    call_count = 0
    node_count = 0

    def _walk(node: dict, depth: int) -> None:
        nonlocal io_calls, mpi_calls, mem_calls, vendor_calls
        nonlocal loop_count, branch_count, call_count, node_count

        kind = node.get("kind", "")
        node_count += 1

        if kind == "CallExpr":
            call_count += 1
            callee = _callee_name(node)
            if callee in _IO_CALLS:
                io_calls += 1
            elif callee.startswith("MPI_") or callee.startswith("NCMPI_"):
                mpi_calls += 1
            elif callee in _MEM_CALLS:
                mem_calls += 1
            elif any(callee.startswith(p) for p in _VENDOR_PREFIXES):
                vendor_calls += 1

        elif kind in ("ForStmt", "WhileStmt", "DoStmt"):
            loop_count += 1

        elif kind in ("IfStmt", "SwitchStmt"):
            branch_count += 1

        # Never recurse into nested function / lambda bodies
        if depth > 0 and kind in (
            "LambdaExpr", "FunctionDecl",
            "CXXMethodDecl", "CXXConstructorDecl", "CXXDestructorDecl",
        ):
            return

        for child in node.get("inner", []):
            _walk(child, depth + 1)

    _walk(body_node, depth=0)

    score = (
        io_calls     * 30
        + mpi_calls  * 25
        + mem_calls  * 15
        + vendor_calls * 30
        + loop_count * 10
        + branch_count * 3
        + call_count * 2
        + min(node_count // 5, 20)   # body-size bonus, capped at 20
    )

    return {
        "io_calls":     io_calls,
        "mpi_calls":    mpi_calls,
        "mem_calls":    mem_calls,
        "vendor_calls": vendor_calls,
        "loop_count":   loop_count,
        "branch_count": branch_count,
        "call_count":   call_count,
        "node_count":   node_count,
        "score":        score,
    }


def _collect_exits(node: dict, exits: list[dict],
                   line_offsets: list[int]) -> None:
    """Recursively find ReturnStmt / exit() / abort() in a CompoundStmt."""
    kind = node.get("kind", "")

    if kind == "ReturnStmt":
        loc = node.get("loc") or node.get("range", {}).get("begin", {}) or {}
        line = _resolve_line(loc, line_offsets)
        if line:
            exits.append({"line": line, "type": "return"})
        return  # don't recurse into return value (may contain lambdas)

    if kind == "CallExpr":
        callee = _callee_name(node)
        if callee in ("exit", "_exit", "abort", "quick_exit", "_Exit"):
            loc = node.get("loc") or node.get("range", {}).get("begin", {}) or {}
            line = _resolve_line(loc, line_offsets)
            if line:
                exits.append({"line": line, "type": callee})

    # Don't recurse into nested function/lambda bodies
    if kind in ("LambdaExpr", "FunctionDecl", "CXXMethodDecl",
                "CXXConstructorDecl", "CXXDestructorDecl"):
        return

    for child in node.get("inner", []):
        _collect_exits(child, exits, line_offsets)


def _callee_name(call_expr: dict) -> str:
    for child in call_expr.get("inner", []):
        if child.get("kind") in ("ImplicitCastExpr", "DeclRefExpr"):
            name = child.get("referencedDecl", {}).get("name", "")
            if name:
                return name
            for inner in child.get("inner", []):
                n = inner.get("referencedDecl", {}).get("name", "")
                if n:
                    return n
    return ""



# ---------------------------------------------------------------------------
# Python extraction via ast module
# ---------------------------------------------------------------------------

def add_braces_c(path: Path) -> dict:
    """Add ``{`` / ``}`` around braceless ``if`` / ``for`` / ``while`` bodies.

    Uses clang ``-Xclang -ast-dump=json`` for precise AST-level detection.
    Raises ``ClangNotFoundError`` if clang is not installed — no regex fallback.

    Args:
        path: Absolute path to a C or C++ source file inside ``annotated/``.

    Returns:
        Dict with keys:
            * ``modified``   (bool) — ``True`` if the file was rewritten.
            * ``insertions`` (int)  — number of brace pairs added.
            * ``method``     (str)  — always ``"clang"``.
    """
    lang = "c" if path.suffix.lower() == ".c" else "c++"
    return _add_braces_via_clang(path, lang)


def _add_braces_via_clang(path: Path, lang: str) -> dict:
    """Collect braceless control-flow bodies via clang AST then rewrite the file.

    Raises ClangNotFoundError if the clang binary is missing.
    """
    try:
        proc = subprocess.run(
            ["clang", "-Xclang", "-ast-dump=json", "-fsyntax-only", "-w",
             f"-x{lang}", str(path)],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        raise ClangNotFoundError(
            "clang binary not found — install clang to enable brace insertion"
        )
    except subprocess.TimeoutExpired:
        return {"modified": False, "insertions": 0, "method": "clang"}

    stdout = proc.stdout
    if not stdout:
        return {"modified": False, "insertions": 0, "method": "clang"}

    json_start = stdout.find("{")
    if json_start == -1:
        return {"modified": False, "insertions": 0, "method": "clang"}
    try:
        ast_root = json.loads(stdout[json_start:])
    except json.JSONDecodeError:
        return {"modified": False, "insertions": 0, "method": "clang"}

    line_offsets = _build_line_offsets(path)
    target = str(path)
    braceless: list[tuple[int, int]] = []
    _collect_braceless(ast_root, braceless, target, current_file="",
                       line_offsets=line_offsets)

    if not braceless:
        return {"modified": False, "insertions": 0, "method": "clang"}

    lines = path.read_text(errors="replace").splitlines(keepends=True)
    new_lines = _insert_braces(lines, braceless)
    path.write_text("".join(new_lines))
    return {"modified": True, "insertions": len(braceless), "method": "clang"}



def _collect_braceless(
    node: dict,
    result: list[tuple[int, int]],
    target: str,
    current_file: str,
    line_offsets: list[int],
) -> None:
    kind = node.get("kind", "")

    # Track file context
    loc = node.get("loc", {})
    if "file" in loc:
        current_file = loc["file"]

    # Skip system headers
    if current_file and target not in current_file and current_file != "":
        if kind != "TranslationUnitDecl":
            return

    # Kinds that are already syntactically self-contained — never need wrapping.
    # CompoundStmt already has braces; DoStmt (do { } while(0)) is self-contained.
    _ALREADY_BRACED = {"CompoundStmt", "DoStmt"}

    def _maybe_add(child: dict) -> None:
        ck = child.get("kind", "")
        if ck in _ALREADY_BRACED:
            return
        rng = child.get("range", {})
        start = _resolve_line(rng.get("begin", {}), line_offsets)
        end   = _resolve_line(rng.get("end",   {}), line_offsets)
        # Guard against unresolvable / out-of-file locations (e.g. macro
        # expansions whose spellingLoc points at a header, or an
        # expansionLoc that resolves past this file's own line count).
        # A bogus line number here previously caused either an IndexError
        # in _insert_braces (line >= len(lines)) or silent corruption
        # (line 0 aliasing the last line via negative indexing).
        max_line = len(line_offsets)
        if not start or not end or start > end:
            return
        if start > max_line or end > max_line:
            return
        result.append((start, end))

    inner = node.get("inner", [])

    if kind == "IfStmt":
        # inner: [condition, then-body]  or  [condition, then-body, else-body]
        # Always skip inner[0] (condition) regardless of its AST kind.
        #
        # Guard 1 — assert()-style macro expansion:
        #   assert(expr) typically expands to  if (expr) ; else __assert_fail()
        #   The then-body is a NullStmt (the bare ";").  Adding braces at the
        #   macro call-site line corrupts the multi-line macro arguments.
        #   When we detect this pattern, skip ALL bodies of the IfStmt.
        #
        # Guard 2 — else-if chains:
        #   For else-body (index >= 2) that is itself an IfStmt (else-if),
        #   do NOT wrap the whole else-if in braces — a stand-alone "{"
        #   inserted before "else" on its own line breaks the if-else chain.
        #   Recursion below handles the inner IfStmt's own bodies.
        _then_is_null = (len(inner) >= 2 and inner[1].get("kind") == "NullStmt")
        for i, child in enumerate(inner):
            if i == 0:
                continue  # condition — never a body
            if _then_is_null:
                # assert()-style: skip all bodies to avoid corrupting macro
                continue
            if i >= 2 and child.get("kind") == "IfStmt":
                # else-if: skip wrapping; recurse will add braces inside it
                continue
            _maybe_add(child)

    elif kind in ("WhileStmt",):
        # inner: [condition, body]
        if len(inner) >= 2:
            _maybe_add(inner[-1])

    elif kind == "ForStmt":
        # inner: [init?, cond?, incr?, body]  — body is always last
        if inner:
            _maybe_add(inner[-1])

    # DoStmt body is always a CompoundStmt in well-formed C; skip.

    for child in inner:
        _collect_braceless(child, result, target, current_file, line_offsets)


def _insert_braces(
    lines: list[str],
    braceless: list[tuple[int, int]],
) -> list[str]:
    """Insert ``{`` and ``}`` around each (start, end) pair (1-based lines)."""
    # Sort descending so insertions don't shift earlier line numbers
    pairs = sorted(set(braceless), key=lambda p: (-p[0], -p[1]))

    # Ranges are assumed disjoint/properly nested so that processing
    # highest-start-first never disturbs the (still-unprocessed, lower-start)
    # indices used later. When a range's END reaches into or past a
    # later-processed (higher-start) range — which happens with VPIC-style
    # macro-expanded statements whose clang-reported end location overshoots
    # the actual statement — that assumption breaks: the earlier insertion's
    # line shift lands on top of this range's stale end-index, splitting a
    # single if/else into two disconnected compound blocks (a real syntax
    # error). Track the lowest start seen so far and skip any pair whose end
    # reaches at or past it, rather than risk corrupting the file.
    min_start_seen = None
    for start, end in pairs:
        if min_start_seen is not None and end >= min_start_seen:
            continue  # overlaps an already-scheduled range — skip, don't corrupt
        min_start_seen = start

        si = start - 1  # 0-based
        ei = end   - 1
        if si < 0 or ei >= len(lines) or si > ei:
            continue  # out-of-range location — skip defensively

        # Determine indentation from the body line
        body_line = lines[si]
        indent = len(body_line) - len(body_line.lstrip())
        ind = " " * indent

        # Insert closing brace after body end line
        close_nl = lines[ei].rstrip("\n").rstrip("\r")
        # If the end line already ends with { or }, skip (already braced)
        stripped = close_nl.strip()
        if stripped.endswith("{") or stripped.endswith("}"):
            continue

        lines.insert(ei + 1, ind + "}\n")
        lines.insert(si, ind + "{\n")

    return lines



def _extract_python(path: Path) -> list[dict]:
    try:
        source = path.read_text(errors="replace")
        tree = ast.parse(source, filename=str(path))
    except SyntaxError:
        return []

    lines = source.splitlines()
    functions: list[dict] = []
    _visit_py_node(tree, functions, lines, parent_class=None)
    for fn in functions:
        fn["source"] = "ast"
    return functions


def _visit_py_node(node: ast.AST, functions: list[dict],
                   lines: list[str], parent_class: Optional[str]) -> None:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exits = _py_exits(child)
            name = child.name
            functions.append({
                "name":            name,
                "start_line":      child.lineno,
                "open_brace_line": child.lineno,  # Python: def line
                "body_first_line": child.body[0].lineno if child.body else child.lineno + 1,
                "close_brace_line": child.end_lineno or child.lineno,
                "exit_lines":      exits,
                "is_entry_point":  name == "main" or name == "__main__",
                "parent_class":    parent_class,
                "cost_info":       _compute_cost_python(child),
            })
            # Recurse into nested functions (but use the class name as parent)
            _visit_py_node(child, functions, lines, parent_class)
        elif isinstance(child, ast.ClassDef):
            _visit_py_node(child, functions, lines, parent_class=child.name)
        else:
            _visit_py_node(child, functions, lines, parent_class)


# ---------------------------------------------------------------------------
# AST-based cost estimation — Python
# ---------------------------------------------------------------------------

#: Python built-in / stdlib I/O names that indicate the function is non-trivial.
_PY_IO_NAMES: frozenset[str] = frozenset({
    "open", "read", "write", "close", "seek", "flush", "readline", "readlines",
    "writelines", "stat", "rename", "unlink", "mkdir", "rmdir",
    "listdir", "scandir", "glob", "walk",
    "send", "recv", "sendall", "sendto", "recvfrom",
})

#: MPI method names (as attribute calls, e.g. ``comm.Send``).
_PY_MPI_NAMES: frozenset[str] = frozenset({
    "Send", "Recv", "Bcast", "Gather", "Scatter",
    "Allreduce", "Reduce", "Allgather", "Alltoall",
    "Barrier", "Sendrecv", "Isend", "Irecv", "Wait", "Waitall",
})


def _compute_cost_python(func_node: ast.AST) -> dict:
    """Walk a Python ``FunctionDef`` AST subtree and compute cost metrics.

    Uses ``ast.walk`` exclusively — no regex or text scanning.

    Returns a dict with integer counters and a composite ``score``.
    """
    io_calls = 0
    mpi_calls = 0
    mem_calls = 0
    loop_count = 0
    branch_count = 0
    call_count = 0
    node_count = 0

    for node in ast.walk(func_node):
        node_count += 1

        if isinstance(node, ast.Call):
            call_count += 1
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr

            if name in _PY_IO_NAMES:
                io_calls += 1
            elif name in _PY_MPI_NAMES or name.startswith("MPI_"):
                mpi_calls += 1
            elif name in _MEM_CALLS:
                mem_calls += 1

        elif isinstance(node, (ast.For, ast.While, ast.AsyncFor)):
            loop_count += 1

        elif isinstance(node, ast.If):
            branch_count += 1

    score = (
        io_calls     * 30
        + mpi_calls  * 25
        + mem_calls  * 15
        + loop_count * 10
        + branch_count * 3
        + call_count * 2
        + min(node_count // 5, 20)
    )

    return {
        "io_calls":     io_calls,
        "mpi_calls":    mpi_calls,
        "mem_calls":    mem_calls,
        "vendor_calls": 0,           # Python has no vendor FS prefix heuristic
        "loop_count":   loop_count,
        "branch_count": branch_count,
        "call_count":   call_count,
        "node_count":   node_count,
        "score":        score,
    }


def _py_exits(func_node: ast.FunctionDef) -> list[dict]:
    exits: list[dict] = []
    for node in ast.walk(func_node):
        if isinstance(node, ast.Return):
            exits.append({"line": node.lineno, "type": "return"})
        elif isinstance(node, ast.Call):
            name = ""
            if isinstance(node.func, ast.Name):
                name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                name = node.func.attr
            if name in ("exit", "sys.exit", "_exit", "abort"):
                exits.append({"line": node.lineno, "type": name})
    exits.sort(key=lambda x: x["line"])
    return exits
