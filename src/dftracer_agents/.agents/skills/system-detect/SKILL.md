---
name: system-detect
description: Detect the current HPC/container system via mcp__dftracer__system_detect and load its module/env/MPI-launcher configuration, or register a new system
---

# System Detection

Detect the current HPC/container system and load appropriate configuration.

## Steps

1. **Call the MCP tool** to auto-detect:
   ```
   mcp__dftracer__system_detect()
   ```
   This strips trailing digits from the hostname (e.g. `tuolumne<node>` → `tuolumne`),
   looks up the system in `resources/systems.yaml`, and returns the module load
   order, env vars, MPI launcher, and notes.

2. **If the system is recognised**: confirm to the user which system was detected
   and summarise the key config (modules, MPI launcher, sudo availability).

3. **If the system is not recognised** (MCP tool returns "not in systems.yaml"):
   Present the user with these options:
   - `tuolumne` — AMD MI300A APU cluster, Cray PE environment, no sudo
   - `container` — Docker/Podman container, sudo available
   - `other` — new system; I'll collect details and register it

4. **If user selects `other`** or a new system:
   - Ask: "What is the system name (hostname base, no digits)?
     What modules/compilers/env vars are needed?"
   - Collect the information.
   - Call `mcp__dftracer__system_save` with the gathered details.
   - Confirm the system was saved to `resources/systems.yaml`.

5. **Always end by telling the user** which system is active and whether
   sudo is permitted.

## Session close
If new system nuances were learned during the session (pitfalls, workarounds,
additional env vars), update the system config before finishing:
```
mcp__dftracer__system_save(name="<system>", notes="<updated notes>", ...)
```

## Permissions

Read-only system probe. This skill uses:

- **MCP:** `mcp__dftracer__system_detect`, `mcp__dftracer__system_list`, `mcp__dftracer__system_save`
- **Bash:** `module` (list/avail only) — no builds, no app execution
- **Write:** none — detection only; never modifies source or workspace files

Outer fence (global `.claude/settings.json`) still applies: never `sudo`, never write outside the project root.
