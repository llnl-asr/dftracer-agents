---
name: system-container
description: System profile for Docker/Podman container environments — sudo available, no HPC module system, package-manager installs, MPI via mpirun
---

# System: Container

Docker/Podman container environment. Sudo is available.

## Key Characteristics
- Root or sudo access is permitted in this environment.
- No HPC module system — use system package manager (`apt`, `yum`, etc.) as needed.
- MPI may or may not be pre-installed; check with `which mpirun`.

## Temp / Workspace
- Use `workspaces/<session>/tmp/` inside the project directory for session-related temp files.
- `/tmp` is acceptable for truly ephemeral scratch (container resets on restart anyway).

## Package Installation
```bash
# Debian/Ubuntu containers
sudo apt-get install -y <package>

# RHEL/Rocky containers
sudo yum install -y <package>
```

## Detection
This system is active if:
- `/.dockerenv` exists, OR
- `hostname` returns `container*`, OR
- The user explicitly selects it.

## Notes
- sudo is permitted here (unlike HPC systems).
- No module system; use direct paths or virtualenvs.
- MPI launch: use `mpirun -n <N>` directly.

## Permissions

This skill uses:

- **Bash:** `module`, `mpirun` — run only inside the active session workspace (`cwd=workspaces/<session>/...`), never the project root
- **Write:** `workspaces/<session>/*` only

Never `sudo`; never write outside the project root.
