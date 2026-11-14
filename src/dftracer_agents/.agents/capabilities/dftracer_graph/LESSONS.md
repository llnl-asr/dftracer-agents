# Lessons: dftracer_graph

- [L-9d9294e6] Installing build artefacts inside site-packages -> graphify writes `<scanned_path>/graphify-out` and has no --out flag -> scan a staged copy under `<workspaces_root>/_graph/` (2026-07-30)
- [L-f008e9b6] "No code files found" on the staged tree -> graphify does not follow symlinked directories -> the stage contains real files (2026-07-30)
- [L-6334f440] No graph for the skills -> a markdown-only tree yields no graph; markdown is only extracted next to code files -> stage code and markdown together (2026-07-30)
- [L-662d1e53] "No code files found" again in the cache -> graphify honours .gitignore and workspaces/ is gitignored -> a `.graphifyignore` at the scan root re-includes the stage (last match wins) (2026-07-30)
- [L-dbdbceeb] Skill answers went stale mid-session -> skills self-update at runtime -> freshness keyed on a content hash of the staged inputs plus a dirty flag any writer (memory_write, skill edits) sets (2026-07-30)
- [L-3cb4f6b5] mode='affected' on a method said "No unique node match" -> symbols are class-qualified (`.save_checkpoint()`) -> use mode='query' with a phrase or the qualified name (2026-07-30)
- [L-592e3202] Doc lookups through BFS query returned helpers like `_ok()` instead of headings -> BFS leaks into the code subgraph -> mode='docs' searches only markdown nodes, then graph_get_node fetches the section (2026-07-30)
- [L-095f65fd] graph_get_node on a method returned only its signature -> graphify emits nodes for nested defs, so "next node" is not a usable end for code -> end code spans by dedent after the header, markdown spans at the next heading (2026-07-30)
