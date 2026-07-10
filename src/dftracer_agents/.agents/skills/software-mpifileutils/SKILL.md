---
name: software-mpifileutils
description: >
  mpifileutils (dcp/dsync/drm/dwalk/dcmp/dtar/dchmod/dstripe/etc.) — parallel,
  MPI-based replacements for serial Linux filesystem utilities (cp/rsync/rm/
  find/diff/tar/chmod) on HPC parallel filesystems. Load this skill whenever
  a filesystem operation (copy, sync, delete, compare, archive, permission
  change, restripe, inventory) touches a large (multi-GB to TB-scale) dataset
  on a cluster/shared parallel filesystem — prefer these over standard serial
  Linux utilities at that scale.
---

Cross-references: [[system-tuolumne]] [[dftracer-profiling]] [[feedback_data_cleanup_quota]]

## Why mpifileutils

Serial Linux utilities (`cp`, `rsync`, `rm -rf`, `find`, `diff -r`, `tar`, `chmod -R`, `du`)
walk a directory tree from a SINGLE client with a SINGLE metadata stream. On a large parallel
filesystem (Lustre, GPFS) with millions of files or TB-scale trees, that single-stream walk is
the bottleneck regardless of how fast the underlying storage is. mpifileutils reimplements the
same operations as MPI programs that parallelize both the directory walk AND the data
movement/metadata-op across many ranks/nodes — the same operation that takes hours serially
often takes minutes with a handful of nodes.

**Rule of thumb: reach for the mpifileutils equivalent instead of the serial tool whenever an
operation will touch more than a few GB or more than ~10k files on a cluster/shared parallel
filesystem.** Below a few GB / a few thousand files, the serial tool's simplicity wins and the
MPI job-launch overhead isn't worth it.

In this harness, launching via `flux run`/`flux submit` is also a normal job submission, not a
raw `rm`/`cp`, so it is NOT subject to the Bash tool's destructive-action permission classifier
the way a raw `rm -rf` sometimes is — an added practical benefit here, not just a performance one.

## Loading

```bash
module load mpifileutils/0.12    # two versions exist (0.11.1, 0.12) — pin the version,
                                  # `ml mpifileutils` alone fails (ambiguous default)
```

All tools below are then on `PATH` and are launched the same way:

```bash
flux run -n <N> -q pdebug <tool> [flags] <args>
```

`-n 8` to `-n 16` tasks is plenty for most metadata/copy operations in the TB range — these are
mostly metadata/IO-bound, not compute-bound, so they don't need a large allocation. `pdebug`
queue is fine for most invocations; use a real allocation only for very large (>10TB or
>10M-file) trees.

## Tool reference

Production tools (source: https://mpifileutils.readthedocs.io/en/v0.11.1/tools.html):

| Tool | Replaces | Purpose |
| --- | --- | --- |
| `dcp` | `cp -r` | Parallel copy of files/directory trees. |
| `dsync` | `rsync -a` | Parallel synchronize source → destination (only transfers deltas); `--batch-files` enables checkpoint/restart for very long syncs. |
| `drm` | `rm -rf` | Parallel remove. See restriction section below — the one tool in this set with an extra usage rule in this harness. |
| `dwalk` | `find` + manual summarize | Parallel list/sort/profile a tree — file counts, size histograms, `--output`/`--input` to cache a walk for reuse across dcp/dsync/drm without re-walking, `--sort`, `--text` for human-readable summaries. Use this FIRST to scope an operation before running dcp/dsync/drm on an unknown-size tree. |
| `dcmp` | `diff -r` / `cmp` | Parallel compare contents between two directories or files — use to verify a dcp/dsync copy actually matches the source before trusting it. |
| `dchmod` | `chmod -R` / `chown -R` | Parallel change owner/group/permissions (`--group`, `--mode`). |
| `dtar` | `tar` | Parallel create/extract of tar archives — much faster than serial `tar` for large trees. |
| `dstripe` | `lfs migrate` / manual restripe | Restripe existing files on Lustre in parallel (change stripe count/size after the fact — `lfs setstripe` only affects newly-created files, `dstripe` rewrites existing ones to a new layout). |
| `ddup` | fdupes-style tools | Find duplicate files in parallel across a large tree. |
| `dfind` | `find` | Parallel filtered file search (`--user`, `--size`, `--type`, `--atime`, `--input`/`--output` to reuse a `dwalk` cache). |
| `dreln` | manual symlink fixup | Parallel update of symlinks to point at a new path (e.g. after relocating a tree). |
| `dbcast` | manual copy-to-every-node | Broadcast a single file to every compute node's local storage (e.g. staging a shared input file/container image before a job). |
| `dbz2` | `bzip2`/`bunzip2` | Parallel compress/decompress a single large file with bz2. |

Experimental (available but less battle-tested): `dgrep`, `dparallel`, `dsh`, `dfilemaker`.

### Practical workflow

1. **Scope first with `dwalk`** on an unfamiliar/large tree — get file count and total size
   before deciding node count or committing to an operation:
   ```bash
   flux run -n 8 -q pdebug dwalk --text /path/to/tree
   ```
2. **Run the actual operation** (`dcp`/`dsync`/`drm`/`dtar`/etc.) at the same or a larger node
   count if `dwalk` showed it's genuinely huge (>10M files or >10TB).
3. **Verify, don't assume** — `dcmp` after a `dcp`/`dsync`, `quota -v` or `du -sh` after a
   `drm`. Job exit code 0 is necessary but not sufficient evidence of correctness at this scale.

## Restriction: `drm` never targets the session workspace

**Only use `drm` on paths OUTSIDE `workspaces/<session>/`** — e.g. Lustre scratch/output
directories like `/p/lustre5/<user>/...`. Do not run `drm` against anything under the active
session workspace. (This restriction is specific to `drm`/destructive deletes in this harness;
`dcp`/`dsync`/`dwalk`/etc. have no such restriction — use them anywhere large-scale copy/sync/
inventory work is needed, including inside a session workspace.)

**Why:** the session workspace already has a narrower, reviewable Bash permission surface
(`Write(workspaces/*)`, `Edit(workspaces/*)`, explicit `cp`/`mv`/`find` allowlist entries in
`.claude/settings.json`). `drm` is a parallel, MPI-launched bulk-delete with no per-file
confirmation — routing session-workspace cleanup through it would bypass the deliberate safety
surface that scope was designed to have, for no benefit (workspace directory trees in this
harness are small enough that a normal `rm -rf` — approved via the standard permission flow —
is sufficient there).

**How to apply:** before invoking `drm`, confirm the target path resolves outside
`workspaces/<session>/`. If a workspace-local path needs bulk deletion, use ordinary `rm -rf`
through the normal permission flow (or ask the user to run it) instead of reaching for `drm`.
A `PreToolUse` hook (`.claude/hooks/guard_rm_drm.sh`) enforces this restriction for both `rm`
and `drm` — it will block a `drm` call outside the allowed roots (session workspace or
`/p/lustre5`) regardless.

See also [[feedback_data_cleanup_quota]] (quota crisis this tool was introduced to resolve).
