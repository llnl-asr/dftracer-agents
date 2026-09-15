---
name: feedback-cross-fs-copy-verification
description: Never gate a delete on a `du` comparison across filesystems — directory inodes differ and produce phantom mismatches; verify with regular-file bytes + rsync dry-run while the source still exists
metadata:
  type: feedback
---

When relocating data between two filesystems (e.g. bulky `traces/` off the NFS
workspace onto `/p/vast1/$USER`, then symlinking back), **never verify the copy
by comparing `du` totals.** `du -sb --apparent-size` sums directory inodes as
well as file data, and filesystems report directory sizes differently.

**Why:** the phantom delta scales with directory count, not data volume — the
same 2-directory tree read 20480 bytes on workspace NFS vs 8192 on VAST; a
1756-directory tree showed a 111 MB phantom delta. File data was byte-identical
in every case. A verified-move script gated on this fails on *every* directory.

**How to apply:** compare regular-file bytes only
(`find "$d" -type f -printf '%s\n' | awk '{s+=$1} END{print s+0}'`), file
counts, and `rsync -a --dry-run --itemize-changes` requiring zero lines matching
`^[<>ch]`. The rsync pass is the strongest — it compares every file individually
against the source. Run all of it **while the source still exists** and delete
only after it passes; a verifier that fails safe costs a re-run, one that fails
open costs the data. Also expect the *verifier* to be the buggy part, not the
data: diagnose a mismatch before loosening the check.

Corollary: pre-existing corruption (e.g. dftracer traces truncated by a killed
run) surfaces at the destination and reads exactly like the copy broke it.
Distinguish the two before deleting the source — identical per-file sizes on
both sides means it was already truncated. See [[dftracer-trace-utils]].

Full detail lives in the [[software-mpifileutils]] skill (verification section)
and [[system-tuolumne]] (the `/p/vast1` trace-archive pattern). Load those
rather than re-deriving. Related: [[feedback_data_cleanup_quota]],
[[feedback-lustre-io]], [[feedback-optimization-pipeline-traces]].
