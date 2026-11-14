"""Per-file Python annotation MCP tool for dftracer instrumentation.

Exposes ``session_annotate_python_file`` — a self-contained, parallelizable
agent tool for annotating a single ``.py`` source file with dftracer Python
decorators and init/fini stubs.

Annotation API (Python)
-----------------------
Function decoration::

    from dftracer.logger import dftracer_fn, DFTracer

    @dftracer_fn(cat="IO", comp="io")
    def read_checkpoint(path: str, rank: int) -> dict:
        ...

    @dftracer_fn(cat="COMM", comp="comm")
    def broadcast_weights(tensor, comm) -> None:
        ...

Entry point initialisation without MPI::

    tracer = DFTracer.initialize_log(
        log_file="traces/myapp",   # prefix; dftracer appends .<pid>.pfw
        data_dir="/path/to/data",
        process_id=0,
    )
    # ... program runs ...
    DFTracer.finalize_log()

Entry point initialisation with mpi4py::

    from mpi4py import MPI
    MPI.Init()
    tracer = DFTracer.initialize_log(
        log_file="traces/myapp",
        data_dir="/data",
        process_id=MPI.COMM_WORLD.Get_rank(),
    )
    # ... program runs ...
    DFTracer.finalize_log()
    MPI.Finalize()

When @dftracer_fn does not accept comp= keyword::

    @dftracer_fn(cat="IO")
    def read_data(path: str) -> bytes:
        DFTracer.get_instance().update("comp", "io")   # fallback inside body
        ...

Decorator ordering (dftracer_fn must be CLOSEST to the def)::

    @some_other_decorator
    @dftracer_fn(cat="IO", comp="io")   # closest to def
    def my_function(...):
        ...

comp= classification::

    "io"   → checkpoint I/O, file open/read/write, numpy/h5py/torch.load
    "comm" → MPI.Send/Recv, distributed communication
    "mem"  → large tensor copies, memcpy, buffer allocation
    "cpu"  → preprocessing, transforms, encoding, hashing

Parallelism
-----------
Each call to ``session_annotate_python_file`` is fully independent.  An
orchestrator can issue calls for all Python files simultaneously.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ..session.workspace import _ws, _ok, _err


def register_python_annotation_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def session_annotate_python_file(
        run_id: str,
        filepath: str,
        build_errors: str = "",
        user_notes: str = "",
    ) -> str:
        """Annotate a single .py source file with dftracer Python decorators.

        This tool is the per-file Python annotation agent.  It is designed to be
        called in parallel with other per-file annotation calls — each invocation
        is independent and operates on a single file inside the session workspace.

        Procedure (mirrors the annotate-python.yaml recipe):

        **Step 0 — Load lessons**
            Read /workspaces/dftracer-agents/.agents/skills/dftracer-annotation-lessons/SKILL.md
            and apply all applicable lessons before writing anything.

        **Step 1 — Read and classify every function/method**
            Read the current state from annotated/<filepath>.
            Record the original line count — the final written file MUST have MORE lines.

            Fetch Python-specific docs first::

                dftracer__docs_search(
                    query="dftracer_fn decorator DFTracer initialize_log finalize_log \\
                           process_id log_file data_dir",
                    source="pydftracer", fetch_content=True
                )

            Build a function/method inventory:

            MANDATORY (always annotate — never apply Rule 0):
              • Checkpoint I/O: *_load, *_save, *_read, *_write, *_fetch, *_push
              • __init__ / __del__ that open/close file handles or network connections
              • Any function calling open(), h5py.File, np.load, torch.load,
                pickle.load, MPI.Send/Recv, or similar I/O APIs

            ANNOTATE if the function:
              • Does file or network I/O
              • Large data transfers (numpy/torch operations, tensor copies)
              • Training loops, inference loops, benchmark drivers
              • MPI communication wrappers

            SKIP (Rule 0) ONLY if ALL of these are true:
              • @property, one-liner getters with no I/O
              • __repr__, __str__, __len__ with no I/O
              • String formatters, argument validators ≤ 5 lines
              • Small helpers called in tight inner loops with no I/O

        **Step 2 — Map exit paths for initialize_log / finalize_log**
            Python decorators handle START/END automatically — no manual exit mapping.
            DFTracer.initialize_log / finalize_log require explicit placement:

              • initialize_log: add after MPI.Init() if mpi4py used; otherwise at the
                top of ``if __name__ == "__main__"`` or the entry function body
              • finalize_log: add before MPI.Finalize() and before any sys.exit() call,
                and at the end of the main execution path

        **Step 3 — Apply annotation (per-function incremental loop)**
            Process functions ONE AT A TIME.  After each function:

            3a. Insert ``@dftracer_fn(cat="<CAT>", comp="<type>")`` on the line
                IMMEDIATELY BEFORE the function's ``def`` statement (below any
                other existing decorators — dftracer_fn must be CLOSEST to def).

            3b. Write the updated file via ``session_write_file``

            3c. Syntax check after EVERY function write::

                    WS=/workspaces/dftracer-agents/workspaces/<run_id>
                    python3 -c "
                    import ast, sys
                    try:
                        ast.parse(open('${WS}/annotated/<filepath>').read())
                    except SyntaxError as e:
                        print(f'SyntaxError line {e.lineno}: {e.msg}')
                        sys.exit(1)
                    " 2>&1

            3d. On syntax error: fix ONLY the failing function (max 2 retries),
                then strip decorator from that function only and mark PENDING.

        **Step 4 — Entry point initialisation**
            For files containing ``if __name__ == "__main__"`` or the top-level
            entry function:
              • Add ``from dftracer.logger import dftracer_fn, DFTracer`` import
              • Add ``DFTracer.initialize_log(log_file=..., data_dir=..., process_id=...)``
              • Add ``DFTracer.finalize_log()`` before every exit path
              • Write file and run syntax check

        **Step 5 — Coverage verification**::

            WS=/workspaces/dftracer-agents/workspaces/<run_id>
            F=${WS}/annotated/<filepath>
            # 1. Decorator count vs comp count (must be equal)
            echo "decorators: $(grep -c '@dftracer_fn' $F)"
            echo "comp=:      $(grep -c 'comp=' $F)"
            # 2. Import present?
            grep -n "from dftracer" $F
            # 3. initialize_log / finalize_log
            grep -n "initialize_log\\|finalize_log" $F
            # 4. Property methods with decorator (should be zero)
            grep -B2 "@dftracer_fn" $F | grep "@property"

        **Step 6 — Output per-file report** in this structure::

            FILE: <filepath>
            STATUS: DONE | PARTIAL | FAILED
            ANNOTATED: <N> functions  (io=<n>, comm=<n>, mem=<n>, cpu=<n>)
            SKIPPED:   <function names with Rule 0 justification>
            PENDING:   <functions not yet annotated with reason>
            NEW_ERRORS: <any new compile/runtime errors discovered>
            LESSONS:   <new patterns or pitfalls not yet in the lessons file>

        **Absolute rules (never violate):**
            • One @dftracer_fn per function — never stack two dftracer decorators
            • cat= is required — use consistent names across the file
              ("IO", "Compute", "MPI", "Data", "Init" are good conventions)
            • comp= is required
            • Never decorate @property methods
            • initialize_log must appear in every entry point file
            • finalize_log must appear before every process exit path
            • dftracer_fn must be the LAST decorator (closest to def)

        **Python-specific pitfalls:**
            PP1  Missing import       → ImportError at runtime; add from dftracer.logger import ...
            PP2  comp= missing        → decorator count ≠ comp count; add comp= to each
            PP3  Inconsistent cat=    → mixed "io"/"IO"/"file"; standardise across file
            PP4  initialize_log missing → empty trace; add to every entry point
            PP5  finalize_log missing  → trace truncated; add before sys.exit and MPI.Finalize
            PP6  Decorator order wrong → @dftracer_fn above other decorators; move it below them
            PP7  @property decorated  → conflict with property; skip @property methods
            PP8  DFTRACER_INIT= incorrect → if code calls DFTracer.initialize_log(), set
                                            DFTRACER_INIT=0 when running the script

        **BUILD ERROR MODE:**
            If build_errors is non-empty, it contains a runtime error or ImportError.
            Focus on fixing those issues first, then continue with unannotated functions.

        Args:
            run_id:       Session identifier (e.g. ``myapp/20260616_120000``).
            filepath:     Path of the ``.py`` file relative to ``annotated/`` subfolder.
            build_errors: Runtime error or import error from a failed test run.
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

        if target.suffix.lower() != ".py":
            return _err(f"session_annotate_python_file only handles .py files, got: {filepath}")

        try:
            original_content = target.read_text(errors="ignore")
        except OSError as e:
            return _err(f"Cannot read {filepath}: {e}")

        original_lines = len(original_content.splitlines())

        if "dftracer_fn" in original_content and not build_errors:
            annotated_count = original_content.count("@dftracer_fn")
            return _ok(
                f"File already annotated with {annotated_count} decorator(s). "
                "Pass build_errors to enter fix mode.",
                filepath=filepath,
                status="already_annotated",
                annotated=annotated_count,
                skipped=[],
                pending=[],
                lessons=[],
            )

        # Detect entry-point file
        is_entry = (
            'if __name__ == "__main__"' in original_content
            or "if __name__ == '__main__'" in original_content
        )

        # Count functions and methods
        top_level_fns = [
            m.group(1) for m in re.finditer(r'^def\s+(\w+)', original_content, re.MULTILINE)
        ]
        method_fns = [
            m.group(1) for m in re.finditer(r'^    def\s+(\w+)', original_content, re.MULTILINE)
        ]

        # Detect I/O calls
        io_calls = []
        io_patterns = [
            "open(", "h5py.File", "np.load", "np.save", "torch.load", "torch.save",
            "pickle.load", "pickle.dump", "MPI.Send", "MPI.Recv", "aiofiles",
        ]
        for i, line in enumerate(original_content.splitlines(), start=1):
            for pat in io_patterns:
                if pat in line and not line.strip().startswith("#"):
                    io_calls.append(f"line {i}: {pat}")
                    break

        context = {
            "run_id": run_id,
            "filepath": filepath,
            "workspace": str(ws),
            "annotated_dir": str(ann_dir),
            "original_line_count": original_lines,
            "is_entry_point": is_entry,
            "top_level_functions": len(top_level_fns),
            "methods": len(method_fns),
            "has_build_errors": bool(build_errors),
            "build_errors_excerpt": build_errors[:500] if build_errors else "",
            "user_notes": user_notes,
        }

        return _ok(
            f"Python file ready for annotation — {original_lines} lines. "
            + (f"Entry point: yes (initialize_log + finalize_log needed). " if is_entry else "")
            + f"Functions: {len(top_level_fns)} top-level, {len(method_fns)} methods. "
            + (f"BUILD ERROR MODE: {len(build_errors.splitlines())} error line(s). " if build_errors else "")
            + "Use python_extract_functions, then session_write_file per function "
            "with ast.parse syntax check after each. "
            "See tool docstring for complete Python procedure.",
            context=context,
            io_calls_detected=io_calls[:20],
            annotation_procedure={
                "step_0": "Read lessons from dftracer-annotation-lessons/SKILL.md",
                "step_1": f"Read annotated/{filepath} — classify all functions",
                "step_2": "Map exit paths for initialize_log/finalize_log (entry files only)",
                "step_3": "Per-function loop: @dftracer_fn(cat=, comp=), write, ast.parse check",
                "step_4": "Add DFTracer.initialize_log / finalize_log (entry files only)",
                "step_5": "Coverage: decorator count == comp count; no @property decorated",
                "step_6": "Output per-file report with STATUS/ANNOTATED/SKIPPED/PENDING",
            },
            build_error_mode=bool(build_errors),
        )
