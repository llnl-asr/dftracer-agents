---
name: bug-clang-add-braces-multiline-call-corruption
description: "clang_add_braces (source_parser.py _insert_braces) corrupted a multi-line function-call body on flux-fiction's emu-jobtap.c — confirmed root cause and fixed"
metadata: 
  node_type: memory
  type: bug
  
---

**Canonical home:** see the `dftracer-annotate-c` skill (general C/C++ annotation
pitfalls on macro-heavy code) — the fix itself lives only in
`source_parser.py`/`bug-clang-add-braces-overlap-corruption`, there is no separate
skill write-up of this specific overlap shape yet.

**Confirmed root cause (2026-07-16):** `_insert_braces()` in
`src/dftracer_agents/mcp_tools/tools/session/source_parser.py` collected a
duplicate/overlapping AST range for the same `if`-statement body: one
CORRECT range spanning a multi-line `flux_log(...)` call, and one WRONG
range with the same (or overlapping) start but a shorter end — a strict
subset, from a duplicate AST node (a macro-internal sub-expression)
reporting a truncated end location. The pre-existing `min_start_seen` guard
(added for the earlier VPIC-Kokkos recurrence, see
`[[bug-clang-add-braces-overlap-corruption]]`) only rejects a LATER pair
whose end reaches back into an EARLIER pair's start — it did not reject a
pair that starts at/inside an already-accepted range but ends before it, so
both got applied, splitting the `flux_log(...)` call's argument list with a
spurious `{`/`}` pair.

**Fix applied:** track every accepted `(start, end)` range in
`_insert_braces` and additionally reject any candidate pair where
`start >= acc_start and start <= acc_end and end < acc_end` for some
already-accepted `(acc_start, acc_end)` — a single real statement never
legitimately produces two different ranges. This is a second, distinct
overlap shape from the one `min_start_seen` already covered; both guards are
now needed together.

**How to apply:** if `clang_add_braces` is suspected of corrupting a
macro-heavy C/C++ file again, diff the annotated file against pristine
source looking for a brace pair that SPLITS a single statement/call in half
(not just extra-but-harmless over-wrapping). If found even after this fix,
the overlap-detection logic in `_insert_braces` likely needs a third guard
shape — don't hand-patch the file, extend the tool.
