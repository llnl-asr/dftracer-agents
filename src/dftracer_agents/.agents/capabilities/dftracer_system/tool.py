"""dftracer_system: detect the HPC/container system a dftracer session runs on and what dftracer can collect there.

The system is the hostname without trailing digits (tuolumne1003 -> tuolumne), looked up in dftracer-agents'
systems.yaml (module load order, env vars with ${VAR} expansion, MPI launcher, sudo, notes, tracing pins). The
node is also probed for the real PAPI hardware-counter budget, derived presets, PAPI components and a power domain.

Predictors: system_detect, system_list, system_papi_counters, system_save (moved unchanged from dftracer-agents'
register_system_tools; they use its systems.yaml). Validator: dftracer_system_check rules on a system_detect
result or a system config (required keys, known launcher -> scheduler, ${VAR} env syntax, counter plan within budget).
"""
from __future__ import annotations

import os
import re
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional

from dftracer_agents.mcp_tools.tools.system.system_service import (
    _REPO_ROOT, _SYSTEMS_YAML, _load_yaml_simple, _save_yaml_simple, _base_hostname,
    probe_papi, papi_counter_plan, _fmt_papi, _fmt_papi_plan, _fmt_features, _fmt_system,
)

# launcher (first words of mpi_launcher) -> scheduler / resource manager
LAUNCHERS = {"flux run": "flux", "flux submit": "flux", "flux": "flux", "srun": "slurm", "jsrun": "lsf",
             "aprun": "alps", "mpirun": "none", "mpiexec": "none"}
REQUIRED_KEYS = {"description": str, "sudo": bool, "modules": list, "env": dict, "mpi_launcher": str}


def scheduler_of(launcher: str) -> Optional[str]:
    words = (launcher or "").strip().split()
    for n in (2, 1):
        key = " ".join(words[:n])
        if key in LAUNCHERS:
            return LAUNCHERS[key]
    return None


def _chk(out: list, check: str, ok: bool, detail: str, fix: str = "") -> None:
    out.append({"check": check, "ok": bool(ok), "detail": detail, "fix": "" if ok else fix})


def check_detect(text: str) -> List[dict]:
    out: List[dict] = []
    if "is not in systems.yaml" in text:
        m = re.search(r"System `([^`]+)`", text)
        _chk(out, "known_system", False, f"system {m.group(1) if m else '?'} is not registered",
             "register it with system_save(name, description, sudo, modules, env, mpi_launcher, notes) from the "
             "application's own install/run scripts, then system_detect again")
        return out
    m = re.search(r"^## System: (\S+)", text, re.M)
    _chk(out, "system_name", bool(m), f"system {m.group(1) if m else None}",
         "pass system_detect's markdown verbatim (it starts with 'Detected system: **<name>**')")
    sudo = re.search(r"^- sudo available: (True|False)\s*$", text, re.M)
    _chk(out, "sudo", bool(sudo), f"sudo line {'present' if sudo else 'missing'}",
         "the system entry needs sudo: true|false (system_save sudo=...)")
    lm = re.search(r"^- MPI launcher: `([^`]*)`", text, re.M)
    launcher = lm.group(1) if lm else ""
    sch = scheduler_of(launcher)
    _chk(out, "launcher_known", sch is not None, f"launcher {launcher!r} -> scheduler {sch}",
         f"set mpi_launcher to one of {sorted(LAUNCHERS)} with system_save")
    hc = re.search(r"hardware counters: (\d+)", text)
    if "### PAPI hardware counters" in text and "NOT available" not in text.split("### PAPI hardware counters", 1)[1][:200]:
        _chk(out, "papi_budget", bool(hc) and int(hc.group(1)) > 0, f"hardware counters {hc.group(1) if hc else None}",
             "PAPI is listed as available but reports no counter budget: re-probe with system_papi_counters on a "
             "compute node, or check the papi module pin in systems.yaml")
    over = [f"run {r} ({c}/{b})" for r, c, b in re.findall(r"run (\d+) \(est\. cost (\d+)/(\d+)\)", text)
            if int(c) > int(b)]
    if re.search(r"run \d+ \(est\. cost", text):
        _chk(out, "papi_runs_fit", not over, f"runs over budget: {over}" if over else "every run fits the budget",
             "split the over-budget run: a derived preset costs 2+ counters; regenerate with system_papi_counters "
             "and verify args.multiplex in the trace")
    return out


def check_config(cfg: Dict[str, Any], name: str = "") -> List[dict]:
    out: List[dict] = []
    if name:
        clean = re.sub(r"\d+$", "", name.strip())
        _chk(out, "name", bool(re.match(r"^[a-z][a-z0-9_-]*$", clean)), f"name {name!r} -> {clean!r}",
             "the system name is the hostname without its trailing digits, lower case (e.g. tuolumne)")
    missing = [k for k in REQUIRED_KEYS if k not in cfg]
    _chk(out, "required_keys", not missing, f"missing {missing}" if missing else "all required keys",
         f"system_save needs {sorted(REQUIRED_KEYS)}")
    wrong = [k for k, t in REQUIRED_KEYS.items() if k in cfg and not isinstance(cfg[k], t)]
    _chk(out, "key_types", not wrong, f"wrong types: {wrong}" if wrong else "types ok",
         "description/mpi_launcher str, sudo bool, modules list of str, env dict of str")
    mods = cfg.get("modules") or []
    if isinstance(mods, list):
        dup = sorted({m for m in mods if mods.count(m) > 1})
        _chk(out, "modules", all(isinstance(m, str) and m.strip() for m in mods) and not dup,
             f"{len(mods)} module(s), duplicates {dup}", "modules are an ordered list of non-empty names, each once")
    sch = scheduler_of(cfg.get("mpi_launcher", "")) if isinstance(cfg.get("mpi_launcher"), str) else None
    _chk(out, "launcher_known", sch is not None, f"launcher {cfg.get('mpi_launcher')!r} -> scheduler {sch}",
         f"mpi_launcher must be one of {sorted(LAUNCHERS)}")
    env = cfg.get("env") or {}
    if isinstance(env, dict):
        bare = [k for k, v in env.items() if re.search(r"\$(?!\{)\w", str(v))]
        _chk(out, "env_var_syntax", not bare, f"bare $VAR in {bare}" if bare else "env references use ${VAR}",
             "write ${VAR}: only ${VAR} is expanded by get_current_system_env; a bare $VAR stays literal")
    tr = cfg.get("tracing")
    if tr is not None:
        okp = isinstance(tr, dict) and tr.get("power", "on") in ("on", "off", True, False) and \
            isinstance(tr.get("papi_module", ""), str)
        _chk(out, "tracing", okp, f"tracing {tr!r}", "tracing: {power: on|off, papi_module: '<module>' or ''}")
    return out


def register(mcp):

    @mcp.tool()
    def system_detect(
        hostname: Optional[str] = None,
        probe_features: bool = True,
        code_type: str = "mixed",
    ) -> str:
        """Detect the current HPC/container system and return its configuration.

        Strips trailing digits from the hostname (e.g. tuolumne1003 →
        tuolumne) to find the system base name, then looks it up in
        .dftracer_agents/resources/systems.yaml. Returns the module load order, environment
        variables, MPI launcher, and any system-specific notes.

        With ``probe_features`` (the default) it also PROBES the node for the
        tracing features dftracer can actually use here, which is the part that
        cannot be answered from a config file:

        * **PAPI** — the real hardware-counter budget (``papi_avail`` lists ~30
          presets on a node with 5 counters; over-asking makes PAPI silently
          time-share and report scaled estimates), which presets exist, which
          are *derived* and so cost 2+ counters each, and which components
          (rocp_sdk, cray_cassini, rapl, ...) are enabled or why they are not.
        * **The counters to enable for this kind of code**, partitioned into
          runs that fit the budget — see ``code_type``.
        * **Node power** — whether a power domain exists that variorum can be
          built against, and therefore whether power collection should be
          switched on.

        If the system is not recognised, returns an 'unknown' message with
        a prompt to register it via system_save.

        Args:
            hostname: Override the auto-detected hostname. Useful for
                      testing or when called from a login node on behalf of
                      a compute node.
            probe_features: Run the PAPI/power probes (a few seconds, shells out
                      to papi_avail under a login shell). Set False for a pure
                      config lookup.
            code_type: What the traced code is doing, which selects the counter
                      groups: compute, memory, branch, instruction, gpu,
                      communication, io, or mixed.

        Returns:
            str: Markdown-formatted system configuration, or an unknown-system
                 message with instructions.
        """
        base = _base_hostname(hostname)
        actual = socket.gethostname()
        data = _load_yaml_simple(_SYSTEMS_YAML)
        systems = data.get("systems", {})

        if base in systems:
            cfg = systems[base]
            header = (
                f"Detected system: **{base}** (from hostname `{actual}`)\n\n"
            )
            body = header + _fmt_system(base, cfg)
            if probe_features:
                body += "\n" + _fmt_features(hostname, code_type)
            return body

        # Try container detection as fallback
        is_container = Path("/.dockerenv").exists() or os.path.exists("/run/.containerenv")
        if is_container and "container" in systems:
            cfg = systems["container"]
            header = (
                f"Hostname `{actual}` (base: `{base}`) not in systems.yaml, "
                f"but container environment detected.\n\n"
            )
            return header + _fmt_system("container", cfg)

        known = ", ".join(f"`{k}`" for k in systems)
        return (
            f"System `{base}` (from hostname `{actual}`) is not in systems.yaml.\n\n"
            f"Known systems: {known or 'none'}\n\n"
            f"To register this system, call `system_save` with the system name, "
            f"description, modules, env vars, and MPI launcher.\n\n"
            f"Example:\n"
            f"```\n"
            f"system_save(name=\"{base}\", description=\"...\", sudo=False,\n"
            f"            modules=[\"module1\", \"module2\"],\n"
            f"            env={{\"VAR\": \"value\"}},\n"
            f"            mpi_launcher=\"srun\", notes=\"...\")\n"
            f"```"
        )

    @mcp.tool()
    def system_list() -> str:
        """List all known systems in .dftracer_agents/resources/systems.yaml.

        Returns:
            str: Markdown table of known systems with their descriptions and
                 key attributes.
        """
        data = _load_yaml_simple(_SYSTEMS_YAML)
        systems = data.get("systems", {})
        if not systems:
            return "No systems configured in .dftracer_agents/resources/systems.yaml."
        lines = [
            f"Known systems in `{_SYSTEMS_YAML.relative_to(_REPO_ROOT)}`:\n",
            "| Name | Description | sudo | MPI launcher |",
            "|------|-------------|------|--------------|",
        ]
        for name, cfg in systems.items():
            desc = cfg.get("description", "")
            sudo = "yes" if cfg.get("sudo", False) else "no"
            mpi = cfg.get("mpi_launcher", "mpirun")
            lines.append(f"| `{name}` | {desc} | {sudo} | `{mpi}` |")
        return "\n".join(lines)

    @mcp.tool()
    def system_papi_counters(
        code_type: str = "mixed",
        hostname: Optional[str] = None,
        budget: Optional[int] = None,
    ) -> str:
        """Which PAPI counters to enable for a given kind of code, on this node.

        Answers the question "I am tracing a <compute|memory|gpu|...> code,
        what do I put in DFTRACER_PAPI_EVENTS?" — filtered against the presets
        this CPU actually implements and partitioned into runs that fit the
        node's hardware-counter budget, with the ready-to-export env for each.

        Why it is not a lookup table: a *derived* preset is computed from two or
        more native events, so it consumes 2+ of the budget. Sizing a set by
        counting names therefore undersizes it, and PAPI responds by
        time-sharing the counters and reporting scaled estimates — no error, no
        non-zero exit. The returned partition uses an estimated cost model
        (native 1, derived 2) and is explicitly a CANDIDATE to probe: run it and
        read ``args.multiplex`` back out of the trace to confirm.

        Args:
            code_type: compute, memory, branch, instruction, gpu, communication,
                       io, or mixed. For gpu/communication/io the report says
                       plainly that CPU presets are the wrong instrument and
                       what to use instead.
            hostname:  Override the auto-detected hostname.
            budget:    Override the probed hardware-counter budget (for planning
                       a partition for a different node than the one you are on).

        Returns:
            str: Markdown counter plan: groups considered, presets missing on
                 this node, the per-run partition with env, and how to verify.
        """
        papi = probe_papi(hostname)
        plan = papi_counter_plan(code_type, hostname=hostname, budget=budget, papi=papi)
        lines = [f"# PAPI counter plan — `{_base_hostname(hostname)}`"]
        lines += _fmt_papi(papi)
        lines += _fmt_papi_plan(plan)
        return "\n".join(lines)

    @mcp.tool()
    def system_save(
        name: str,
        description: str = "",
        sudo: bool = False,
        modules: Optional[List[str]] = None,
        env: Optional[Dict[str, str]] = None,
        mpi_launcher: str = "mpirun",
        notes: str = "",
    ) -> str:
        """Save or update a system's configuration in .dftracer_agents/resources/systems.yaml.

        Call this to register a new system or update an existing one. The
        config is persisted across sessions so future `system_detect` calls
        will recognise it automatically.

        Args:
            name:          Base system name (no digits; e.g. ``"corona"``).
            description:   Short human-readable label.
            sudo:          Whether sudo is available on this system.
            modules:       Ordered list of modules to load before building/running.
            env:           Dict of environment variables to export after module load.
                           Use ``${VAR}`` syntax to reference existing env vars.
            mpi_launcher:  Command used to launch MPI jobs (``"flux run"``,
                           ``"srun"``, ``"mpirun"``).
            notes:         Free-text notes (pitfalls, workarounds, tips).

        Returns:
            str: Confirmation message with the stored config summary.
        """
        data = _load_yaml_simple(_SYSTEMS_YAML)
        if "systems" not in data:
            data["systems"] = {}

        clean_name = re.sub(r"\d+$", "", name.strip())
        # Merge onto the existing entry rather than replacing it: keys this
        # tool has no parameter for (`tracing:` — the PAPI module pin and the
        # power opt-out) would otherwise be silently dropped the next time
        # someone called system_save to update a module list.
        entry = dict(data["systems"].get(clean_name) or {})
        entry.update({
            "description": description,
            "sudo": bool(sudo),
            "modules": modules or [],
            "env": env or {},
            "mpi_launcher": mpi_launcher,
            "notes": notes,
        })
        data["systems"][clean_name] = entry

        _SYSTEMS_YAML.parent.mkdir(parents=True, exist_ok=True)
        _save_yaml_simple(data, _SYSTEMS_YAML)
        return (
            f"Saved system `{clean_name}` to `{_SYSTEMS_YAML.relative_to(_REPO_ROOT)}`.\n\n"
            + _fmt_system(clean_name, data["systems"][clean_name])
        )

    @mcp.tool()
    def dftracer_system_check(detect_output: str = "", config: Optional[Dict[str, Any]] = None, name: str = "") -> dict:
        """Deterministic validator of system detection. Use it on system_detect's markdown (detect_output) before
        downstream agents rely on it, and on a system config (config, the dict passed to system_save or a
        systems.yaml entry; name optional) before saving it. For detect_output it checks the system is registered
        (else: system_save), the system name, the sudo line, a known MPI launcher and its scheduler (flux run ->
        flux, srun -> slurm, jsrun -> lsf, aprun -> alps, mpirun/mpiexec -> none), a positive PAPI counter budget when
        PAPI is available, and that every planned PAPI run fits the budget. For config it checks the required keys
        and types (description, sudo, modules, env, mpi_launcher), duplicate modules, the launcher, ${VAR} env syntax
        and the tracing block. Needs no host access.
        Returns {status: pass|fail|inconclusive, scheduler, checks: [{check, ok, detail, fix}]}."""
        checks: List[dict] = []
        launcher = ""
        if detect_output:
            checks += check_detect(detect_output)
            lm = re.search(r"^- MPI launcher: `([^`]*)`", detect_output, re.M)
            launcher = lm.group(1) if lm else ""
        if config is not None:
            if not isinstance(config, dict):
                checks.append({"check": "config", "ok": False, "detail": "config is not a dict",
                               "fix": "pass the system entry as a dict"})
            else:
                checks += check_config(config, name)
                launcher = launcher or str(config.get("mpi_launcher", ""))
        if not checks:
            return {"status": "inconclusive", "scheduler": None, "checks": [],
                    "why": "nothing to check: pass detect_output and/or config"}
        return {"status": "pass" if all(c["ok"] for c in checks) else "fail", "scheduler": scheduler_of(launcher),
                "checks": checks}
