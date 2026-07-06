# Data-Driven Figure Generation for Research Reports

## When to Use This

Whenever a report requires traceability diagrams, causal flow charts, or any figure showing relationships between entities (files, inodes, fds, operations, etc.). The source of truth is ALWAYS preprocessed data — never hardcoded values.

## Pipeline

```
Raw logs (.log, .json, .csv, etc.)
    ↓
Preprocess → JSON with structured entity relationships
    ↓
Programmatic figure generation (Python + SVG or matplotlib)
    ↓
Embed in Typst via #image()
```

## Rules (Hard)

1. **Never hardcode entity values.** If the figure shows `fd=12`, that 12 must come from the data. No exceptions.
2. **Preprocess first.** Extract fd→inode mappings, operation counts, and causal chains into a structured JSON before generating any figure.
3. **Tree diagrams, not stacks.** Branching flows (file → fd → vfs → {pagecache, xfs} → {wb, bio}) are trees. Vertical stacks are unacceptable.
4. **Research-paper aesthetics.** White background, sans-serif fonts (Helvetica/Arial), clean lines. No dark themes, no blog infographics, no emoji.
5. **Tight arrows.** Arrowheads must touch box edges. No floating arrows with gaps.
6. **Color meaning must be consistent.**
   - Active I/O ops (VFS_READ/WRITE): dark red `#8B0000`
   - BIO layer: crimson `#DC143C`
   - Writeback: dark goldenrod `#B8860B`
   - Metadata/control: gray/white `#f5f5f5`
   - Bottlenecks: red `#E74C3C` with white text

## Reference Layout

Top layer:
- Light-colored rounded box: testFile name + phase label
- Down arrow to inode box (gold/orange border)
- Inode box: inode number + total event count

Middle layer:
- Horizontal event bar: one box per event type
- Event name, count (bold), latency, description
- Fan-out lines from inode to each event box
- Color: dark red for active ops, red for bottlenecks, gray for metadata

Bottom layer:
- "Causal Flow (simplified)" label
- Chain of rounded boxes connected by arrows
- Annotations: dotted lines to notes, bottleneck markers

## Example Preprocessed JSON Shape

```json
{
  "run-0-ior-write-small": {
    "phase_name": "Small Buffered Write",
    "phase_size": "128KB",
    "fd_to_ino": {"3": 1062, "12": 30143398},
    "inode_ops": {
      "30143398": {
        "FD_OPEN": 2, "VFS_WRITE": 32, "PAGECACHE_WRITE": 32,
        "XFS_ALLOC": 17, "XFS_FOUND": 15, "FD_CLOSE": 2
      }
    }
  }
}
```

## Verification Checklist

Before delivering figures:
- [ ] All fd values match the preprocessed JSON
- [ ] All inode values match the preprocessed JSON
- [ ] All operation counts match the preprocessed JSON
- [ ] Tree has branching (not a vertical stack)
- [ ] Arrows touch box edges (no gaps)
- [ ] White background, research-paper fonts
- [ ] Color scheme is consistent across all figures
