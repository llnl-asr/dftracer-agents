"""System detection and configuration MCP tools.

Detects the current HPC/container system from the hostname (stripping trailing
digits), looks it up in ``.dftracer_agents/resources/systems.yaml``, and
returns the module load sequence, environment variables, and MPI launcher for
that system.

It also PROBES the node for the tracing features dftracer can actually use
here, which no config file can answer: the real PAPI hardware-counter budget
(``papi_avail`` lists ~30 presets on a node with 5 counters, and over-asking
makes PAPI silently time-share them and report scaled estimates), which presets
are *derived* and so cost 2+ counters each, which PAPI components are enabled
or why they are not, and whether a power domain exists that variorum can be
built against.

Tools
-----
* ``system_detect``        — detect system from hostname; return config +
                             probed tracing features + the counter plan
* ``system_papi_counters`` — which PAPI counters to enable for a given kind of
                             code, partitioned into runs that fit the budget
* ``system_list``          — list all known systems in the config file
* ``system_save``          — save or update a system's configuration
"""
from __future__ import annotations

import os
import re
import socket
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory

# Path to the systems config relative to the repo root
# __file__ = <root>/src/dftracer_agents/mcp_tools/tools/system/system_service.py
# parents[0]=system/ [1]=tools/ [2]=mcp_tools/ [3]=dftracer_agents/ [4]=src/ [5]=repo root
_REPO_ROOT = Path(__file__).resolve().parents[5]
_SYSTEMS_YAML = _REPO_ROOT / ".dftracer_agents" / "resources" / "systems.yaml"


def _load_yaml_simple(path: Path) -> Dict[str, Any]:
    """Minimal YAML loader for systems.yaml (avoids PyYAML dependency).

    Only handles the specific structure of systems.yaml: top-level keys,
    nested dicts, lists of strings, and string values (including multiline
    block scalars with |). Env var values with ${VAR} are preserved as-is.
    """
    try:
        import yaml  # type: ignore
        with path.open() as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        pass

    # Fallback: hand-rolled parser for the known structure
    if not path.exists():
        return {"systems": {}}

    lines = path.read_text().splitlines()
    result: Dict[str, Any] = {}
    stack: list = [result]
    indent_stack: list = [-1]
    current_list: Optional[list] = None
    current_list_key: Optional[str] = None
    block_scalar_key: Optional[str] = None
    block_scalar_lines: list = []
    block_scalar_indent: int = 0

    for raw_line in lines:
        stripped = raw_line.lstrip()
        if not stripped or stripped.startswith("#"):
            if block_scalar_key is not None:
                block_scalar_lines.append(raw_line.rstrip())
            continue

        indent = len(raw_line) - len(stripped)

        # Collecting block scalar (|)
        if block_scalar_key is not None:
            if indent > block_scalar_indent or stripped.startswith("-"):
                block_scalar_lines.append(raw_line.rstrip())
                continue
            else:
                # End of block scalar
                stack[-1][block_scalar_key] = "\n".join(
                    line[block_scalar_indent:] for line in block_scalar_lines
                ).strip()
                block_scalar_key = None
                block_scalar_lines = []

        # Pop stack frames
        while indent <= indent_stack[-1]:
            indent_stack.pop()
            stack.pop()
            current_list = None

        if stripped.startswith("- "):
            # List item
            if current_list is None:
                current_list = []
                stack[-1][current_list_key] = current_list
            current_list.append(stripped[2:].strip().strip('"').strip("'"))
            continue

        if ":" in stripped:
            key, _, val = stripped.partition(":")
            key = key.strip()
            val = val.strip()

            current_list = None
            current_list_key = key

            if val == "|":
                block_scalar_key = key
                block_scalar_indent = indent + 2
                block_scalar_lines = []
                if indent not in indent_stack:
                    indent_stack.append(indent)
            elif val == "" or val == "{}":
                child: Dict[str, Any] = {}
                stack[-1][key] = child
                stack.append(child)
                indent_stack.append(indent)
            elif val.startswith("["):
                # inline list — skip, use yaml lib if needed
                stack[-1][key] = []
            else:
                val = val.strip('"').strip("'")
                if val.lower() == "true":
                    val = True
                elif val.lower() == "false":
                    val = False
                stack[-1][key] = val

    # Flush trailing block scalar
    if block_scalar_key is not None:
        stack[-1][block_scalar_key] = "\n".join(
            line[block_scalar_indent:] for line in block_scalar_lines
        ).strip()

    return result


def _save_yaml_simple(data: Dict[str, Any], path: Path) -> None:
    """Write systems data back to YAML using PyYAML if available."""
    try:
        import yaml  # type: ignore
        with path.open("w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        return
    except ImportError:
        pass

    # Minimal serialiser for the known structure
    lines = [
        "# Known HPC/container system configurations.",
        "# Keyed by the base hostname with trailing digits stripped.",
        "",
        "systems:",
    ]
    for sys_name, cfg in data.get("systems", {}).items():
        lines.append(f"  {sys_name}:")
        for k, v in cfg.items():
            if isinstance(v, bool):
                lines.append(f"    {k}: {'true' if v else 'false'}")
            elif isinstance(v, list):
                lines.append(f"    {k}:")
                for item in v:
                    lines.append(f"      - {item}")
            elif isinstance(v, dict):
                lines.append(f"    {k}:")
                for ek, ev in v.items():
                    lines.append(f"      {ek}: \"{ev}\"")
            elif isinstance(v, str) and "\n" in v:
                lines.append(f"    {k}: |")
                for vline in v.splitlines():
                    lines.append(f"      {vline}")
            else:
                lines.append(f"    {k}: {v}")
    path.write_text("\n".join(lines) + "\n")


def _base_hostname(hostname: Optional[str] = None) -> str:
    """Strip trailing digits from a hostname to get the system base name."""
    h = hostname or socket.gethostname().split(".")[0]
    return re.sub(r"\d+$", "", h)


def get_current_system_env(hostname: Optional[str] = None) -> Dict[str, str]:
    """Return the env dict for the current system, with ${VAR} expanded.

    Looks up ``.dftracer_agents/resources/systems.yaml`` for the detected base hostname and
    resolves any ``${VAR}`` references against the current process
    environment (e.g. ``LD_LIBRARY_PATH: "/opt/x:${LD_LIBRARY_PATH}"``).
    Returns an empty dict if the system is unknown or has no ``env`` section.

    Subprocess-launching tools (dftracer install, annotated build, smoke
    test, trace runs) must merge this into their subprocess env — the MCP
    server process's own environment does not necessarily have these vars
    set, so system-specific paths (e.g. Tuolumne's CCE lib dirs) are silently
    missing unless explicitly re-applied here.
    """
    base = _base_hostname(hostname)
    data = _load_yaml_simple(_SYSTEMS_YAML)
    cfg = (data.get("systems") or {}).get(base)
    if not cfg:
        return {}
    raw_env = cfg.get("env") or {}
    resolved: Dict[str, str] = {}
    for k, v in raw_env.items():
        resolved[k] = re.sub(
            r"\$\{(\w+)\}", lambda m: os.environ.get(m.group(1), ""), str(v)
        )
    return resolved


def get_current_system_modules(hostname: Optional[str] = None) -> List[str]:
    """Return the ordered ``module load`` list for the current system.

    Subprocess-launching tools that need a *real* Cray PE environment (not a
    hand-assembled subset of env vars) should run their command inside a
    login shell that does ``module load <these, in order>`` first --
    hand-picking individual env vars (CC, LD_LIBRARY_PATH, ...) misses
    PE_ENV/CRAY_* variables the compiler driver scripts rely on internally
    to select their companion GNU toolchain, which silently breaks C++
    builds (e.g. Cray Clang falling back to a stray system GCC toolset).
    """
    base = _base_hostname(hostname)
    data = _load_yaml_simple(_SYSTEMS_YAML)
    cfg = (data.get("systems") or {}).get(base)
    if not cfg:
        return []
    return list(cfg.get("modules") or [])


# ---------------------------------------------------------------------------
# Tracing-feature probes: PAPI hardware counters and node power
#
# "What can dftracer actually collect on this node" is a property of the
# SYSTEM, not of the application, so it is answered here rather than in
# session_detect (which re-derives per app and had no way to know a counter
# budget or whether a power backend exists). session_detect consumes
# detect_tracing_features() to pick its defaults.
# ---------------------------------------------------------------------------

#: A reference counter kept in EVERY run of a multi-run partition. It costs one
#: slot per run and is what makes counters gathered in different runs
#: comparable (normalise each to cycles). See the software-papi skill, Rule 2.
_PAPI_REFERENCE = "PAPI_TOT_CYC"

#: Curated PAPI preset groups, keyed by what the code is doing. These are NAMES
#: ONLY — which of them exist on a given node is resolved at probe time against
#: `papi_avail`, because a preset the CPU does not implement is simply absent
#: (AMD Zen exposes no PAPI_L3_* presets at all, for instance). Never hand this
#: list to dftracer unfiltered.
_PAPI_GROUPS: Dict[str, Dict[str, Any]] = {
    "cycles": {
        "why": "Wall-clock anchor and IPC denominator; the baseline for everything else.",
        "presets": ["PAPI_TOT_CYC", "PAPI_TOT_INS"],
    },
    "flops": {
        "why": "Is the kernel actually doing math, and in what mix (FMA/vector/divide)?",
        "presets": [
            "PAPI_FP_OPS", "PAPI_FP_INS", "PAPI_FMA_INS", "PAPI_VEC_INS",
            "PAPI_FML_INS", "PAPI_FAD_INS", "PAPI_FDV_INS", "PAPI_FSQ_INS",
        ],
    },
    "cache": {
        "why": "Cache-hierarchy misses — the usual cause of a low-IPC compute kernel.",
        "presets": [
            "PAPI_L1_DCM", "PAPI_L1_DCA", "PAPI_L2_DCM", "PAPI_L2_TCM",
            "PAPI_L2_DCR", "PAPI_L2_DCH", "PAPI_L2_TCH",
        ],
    },
    "tlb": {
        "why": "Page-walk pressure; separates a working-set problem from a locality problem.",
        "presets": ["PAPI_TLB_DM", "PAPI_TLB_IM"],
    },
    "branch": {
        "why": "Misprediction cost in control-heavy code (traversals, sparse indexing).",
        "presets": [
            "PAPI_BR_INS", "PAPI_BR_MSP", "PAPI_BR_CN", "PAPI_BR_TKN",
            "PAPI_BR_NTK", "PAPI_BR_PRC", "PAPI_BR_UCN",
        ],
    },
    "instruction": {
        "why": "Instruction-side cache behaviour; matters for large templated/inlined codes.",
        "presets": ["PAPI_L2_ICM", "PAPI_L2_ICA", "PAPI_L2_ICH", "PAPI_L2_ICR"],
    },
}

#: Which groups to sample for a given kind of code, most informative first.
#: The key is the "type of code" the caller is tracing.
_CODE_TYPE_GROUPS: Dict[str, List[str]] = {
    "compute":       ["cycles", "flops", "cache"],
    "memory":        ["cycles", "cache", "tlb"],
    "branch":        ["cycles", "branch"],
    "instruction":   ["cycles", "instruction", "cache"],
    # GPU-offload codes: the GPU side needs PAPI's rocp_sdk/cuda component (see
    # the probe's component report), but the HOST side is still worth counting —
    # a GPU code that is actually host-bound looks identical from the GPU side.
    "gpu":           ["cycles", "cache"],
    # Communication- and I/O-bound codes: CPU presets say almost nothing about
    # time spent in MPI or in the kernel. Counted anyway ONLY as a control.
    "communication": ["cycles"],
    "io":            ["cycles"],
    "mixed":         ["cycles", "flops", "cache", "tlb", "branch", "instruction"],
}

#: Code types where PAPI is the wrong instrument, and what to use instead. Said
#: out loud rather than silently returning a thin counter list that looks like
#: coverage.
_CODE_TYPE_CAVEATS: Dict[str, str] = {
    "gpu": (
        "GPU-side counters need PAPI's rocp_sdk (AMD) or cuda (NVIDIA) component, "
        "which is separate from these CPU presets — check the components report "
        "below. dftracer's own HIP tracing "
        "(DFTRACER_ENABLE_HIP_TRACING) is usually the better instrument for "
        "kernel-level GPU data; these host-side counters are for deciding "
        "whether a 'GPU code' is in fact host-bound."
    ),
    "communication": (
        "CPU presets cannot see time spent in MPI. Use dftracer's MPI tracing for "
        "that, and the cray_cassini PAPI component (if enabled below) for NIC "
        "counters. The cycles group is included only as a control."
    ),
    "io": (
        "CPU presets cannot see I/O. Use dftracer's POSIX/HDF5/MPI-IO tracing. "
        "The cycles group is included only as a control."
    ),
}

_PAPI_PRESET_RE = re.compile(
    r"^(PAPI_\w+)\s+0x[0-9a-fA-F]+\s+(Yes|No)\s*(.*)$"
)
_PAPI_COMPONENT_RE = re.compile(r"^Name:\s+(\S+)\s*(.*)$")
_PAPI_DISABLED_RE = re.compile(r"^\s*\\->\s*Disabled:\s*(.*)$")


def _run_login_shell(script_body: str, timeout: int = 120) -> Dict[str, Any]:
    """Run *script_body* under ``bash -l`` so ``module`` is a real function.

    A plain subprocess does NOT have lmod's shell function, so ``module load``
    silently does nothing and every probe below would report the system PAPI
    (an ancient 5.6 in /usr) instead of the pinned one. Same reasoning as
    install.py's ``_run_pip_via_module_script``.
    """
    try:
        proc = subprocess.run(
            ["bash", "-l", "-c", script_body],
            capture_output=True, text=True, timeout=timeout,
        )
        return {"rc": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"rc": 124, "stdout": "", "stderr": f"timed out after {timeout}s"}
    except Exception as exc:  # pragma: no cover - environment dependent
        return {"rc": 1, "stdout": "", "stderr": str(exc)}


def get_tracing_config(hostname: Optional[str] = None) -> Dict[str, Any]:
    """The ``tracing:`` block for the detected system (pins, opt-outs)."""
    base = _base_hostname(hostname)
    data = _load_yaml_simple(_SYSTEMS_YAML)
    cfg = (data.get("systems") or {}).get(base) or {}
    return dict(cfg.get("tracing") or {})


def probe_papi(hostname: Optional[str] = None, timeout: int = 120) -> Dict[str, Any]:
    """Probe this node for PAPI: counter budget, presets, and components.

    The counter BUDGET is the whole point. ``papi_avail`` lists ~30 presets on
    an MI300A node while the hardware has 5 counters; asking for more than fits
    makes PAPI time-share them and report scaled estimates, with no error and a
    zero exit code. Everything downstream (counter partitioning, "is this run
    exact?") depends on reading that number rather than assuming it.
    """
    cfg = get_tracing_config(hostname)
    module = str(cfg.get("papi_module") or "").strip()

    lines = []
    if module:
        # Pinned deliberately: see tracing.papi_module_note in systems.yaml.
        lines.append(f"module load {module} 2>/dev/null || true")
    lines += [
        'echo "===PAPI_AVAIL==="',
        "papi_avail -a 2>/dev/null || true",
        'echo "===PAPI_COMPONENTS==="',
        "papi_component_avail 2>/dev/null || true",
        'echo "===PAPI_WHICH==="',
        "command -v papi_avail || true",
    ]
    out = _run_login_shell("\n".join(lines), timeout=timeout)
    text = out["stdout"]

    result: Dict[str, Any] = {
        "available": False,
        "module": module or None,
        "binary": None,
        "prefix": None,
        "version": None,
        "hardware_counters": None,
        "presets": {},
        "derived_presets": [],
        "components": {},
        "error": None,
    }

    if "===PAPI_AVAIL===" not in text:
        result["error"] = (out["stderr"] or "no output from probe shell").strip()[:400]
        return result

    avail = text.split("===PAPI_AVAIL===", 1)[1].split("===PAPI_COMPONENTS===")[0]
    components_text = ""
    if "===PAPI_COMPONENTS===" in text:
        components_text = text.split("===PAPI_COMPONENTS===", 1)[1].split("===PAPI_WHICH===")[0]
    if "===PAPI_WHICH===" in text:
        binary = text.split("===PAPI_WHICH===", 1)[1].strip().splitlines()
        if binary:
            result["binary"] = binary[0].strip()
            # <prefix>/bin/papi_avail -> <prefix>
            result["prefix"] = str(Path(binary[0].strip()).parent.parent)

    m = re.search(r"PAPI version\s*:\s*(\S+)", avail)
    if m:
        result["version"] = m.group(1)
    m = re.search(r"Number Hardware Counters\s*:\s*(\d+)", avail)
    if m:
        result["hardware_counters"] = int(m.group(1))

    for line in avail.splitlines():
        pm = _PAPI_PRESET_RE.match(line.strip())
        if pm:
            name, deriv = pm.group(1), pm.group(2) == "Yes"
            result["presets"][name] = {"derived": deriv, "description": pm.group(3).strip()}
            if deriv:
                result["derived_presets"].append(name)

    current: Optional[str] = None
    for line in components_text.splitlines():
        cm = _PAPI_COMPONENT_RE.match(line.strip())
        if cm:
            current = cm.group(1)
            result["components"][current] = {
                "description": cm.group(2).strip(),
                "enabled": True,
                "reason": None,
            }
            continue
        dm = _PAPI_DISABLED_RE.match(line)
        if dm and current:
            result["components"][current]["enabled"] = False
            result["components"][current]["reason"] = dm.group(1).strip()

    result["available"] = bool(result["presets"]) and bool(result["hardware_counters"])
    if not result["available"] and not result["error"]:
        result["error"] = (
            "papi_avail produced no presets — PAPI is not on PATH, or the module "
            "pin in systems.yaml is wrong for this node"
        )
    return result


def probe_power(hostname: Optional[str] = None) -> Dict[str, Any]:
    """Probe for node-power measurement backends.

    dftracer reads power through variorum, which it BUILDS ITSELF
    (DFTRACER_BUILD_VARIORUM) against whatever power domains exist at build
    time — so the question is not "is variorum installed" but "does this node
    expose a domain variorum can compile support for".
    """
    sources: List[Dict[str, str]] = []

    rocm_path = os.environ.get("ROCM_PATH") or ""
    candidates: List[Path] = []
    if rocm_path:
        candidates.append(Path(rocm_path))
    candidates += sorted(Path("/opt").glob("rocm-*"), reverse=True)
    candidates.append(Path("/opt/rocm"))
    for cand in candidates:
        libs = list((cand / "lib").glob("librocm_smi64.so*")) if cand.is_dir() else []
        if libs:
            sources.append({
                "kind": "gpu",
                "backend": "rocm_smi",
                "detail": str(cand),
                "note": "GPU package power via variorum's rocm_smi domain",
            })
            break

    if Path("/sys/class/powercap/intel-rapl").exists():
        sources.append({
            "kind": "cpu",
            "backend": "rapl_powercap",
            "detail": "/sys/class/powercap/intel-rapl",
            "note": "CPU/package energy via the powercap sysfs interface",
        })
    if Path("/dev/cpu/0/msr").exists():
        sources.append({
            "kind": "cpu",
            "backend": "msr",
            "detail": "/dev/cpu/0/msr",
            "note": "variorum's MSR path; needs read permission on the msr device",
        })
    if Path("/sys/cray/pm_counters").exists():
        sources.append({
            "kind": "node",
            "backend": "cray_pm_counters",
            "detail": "/sys/cray/pm_counters",
            "note": "Cray node-level power/energy counters",
        })

    cfg = get_tracing_config(hostname)
    opted_out = str(cfg.get("power", "auto")).lower() in ("off", "false", "no", "disabled")

    result: Dict[str, Any] = {
        "sources": sources,
        "can_enable": bool(sources) and not opted_out,
        "opted_out": opted_out,
        "features": {},
        "hazards": [],
    }
    if result["can_enable"]:
        result["features"] = {"variorum": True, "variorum_build": "ALWAYS"}
        gpu = next((s for s in sources if s["kind"] == "gpu"), None)
        if gpu:
            result["features"]["rocm_path_for_variorum"] = gpu["detail"]
        result["hazards"] = [
            "Variorum is SERVICE-SIDE only — it is sampled by dftracer_service, "
            "never linked into the traced application. A dftracer that puts it in "
            "the global DEPENDENCY_LIB drags librocm_smi64 into the app and "
            "corrupts the heap at exit (fixed upstream; verify with `ldd` on the "
            "instrumented binary if apps start aborting in __run_exit_handlers).",
            "DFTRACER_BUILD_VARIORUM=ALWAYS: an already-installed variorum is "
            "usually built for none of this machine's power domains and fails at "
            "init with _ERROR_VARIORUM_UNSUPPORTED_PLATFORM.",
        ]
        if gpu:
            result["hazards"].append(
                "rocm_path_for_variorum is the NEWEST ROCm on this node, which is "
                "not necessarily the one the app was built against. When a session "
                "has an app-matched ROCm (session_detect's features.rocm.path), "
                "that one wins — two rocm_smi ABIs in one process is the failure "
                "mode above."
            )
    elif not sources:
        result["hazards"] = ["No power domain found on this node — nothing to enable."]
    return result


def papi_counter_plan(
    code_type: str = "mixed",
    hostname: Optional[str] = None,
    budget: Optional[int] = None,
    papi: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pick the PAPI counters to enable for a given kind of code, and partition
    them into runs that fit this node's hardware-counter budget.

    Two things make this more than a lookup table:

    1. A preset the CPU does not implement is simply absent from
       ``papi_avail``, so the curated group lists are FILTERED against what the
       node actually has before anything is proposed.
    2. A *derived* preset is computed from two or more native events and so
       costs two or more of the budget. Counting names therefore undersizes a
       set, silently, and PAPI multiplexes rather than failing.

    The cost model here (native 1, derived 2) is an ESTIMATE and is reported as
    one: derived cost is not uniformly 2, and the only way to know is to run
    the set and read ``args.multiplex`` back out of the trace. The returned plan
    says so and carries the verification step — it is a candidate partition to
    probe, not a promise of exactness.
    """
    papi = papi if papi is not None else probe_papi(hostname)
    code_type = (code_type or "mixed").strip().lower()
    groups = _CODE_TYPE_GROUPS.get(code_type)
    plan: Dict[str, Any] = {
        "code_type": code_type,
        "known_code_types": sorted(_CODE_TYPE_GROUPS),
        "caveat": _CODE_TYPE_CAVEATS.get(code_type),
        "budget": None,
        "runs": [],
        "groups": [],
        "unavailable_presets": [],
        "cost_model": "native=1, derived>=2 (ESTIMATE — verify with args.multiplex)",
        "error": None,
    }
    if groups is None:
        plan["error"] = (
            f"unknown code_type {code_type!r}; expected one of "
            f"{', '.join(sorted(_CODE_TYPE_GROUPS))}"
        )
        return plan
    if not papi.get("available"):
        plan["error"] = papi.get("error") or "PAPI is not available on this node"
        return plan

    available = papi.get("presets") or {}
    budget = int(budget or papi.get("hardware_counters") or 0)
    plan["budget"] = budget
    if budget <= 0:
        plan["error"] = "could not determine this node's hardware-counter budget"
        return plan

    def cost(name: str) -> int:
        return 2 if available.get(name, {}).get("derived") else 1

    wanted: List[str] = []
    for group in groups:
        spec = _PAPI_GROUPS[group]
        present = [p for p in spec["presets"] if p in available]
        missing = [p for p in spec["presets"] if p not in available]
        plan["groups"].append({
            "group": group,
            "why": spec["why"],
            "presets": present,
            "not_on_this_node": missing,
        })
        plan["unavailable_presets"].extend(missing)
        for name in present:
            if name not in wanted:
                wanted.append(name)

    reference = _PAPI_REFERENCE if _PAPI_REFERENCE in available else None
    body = [n for n in wanted if n != reference]

    # Greedy fill. Each run reserves the reference counter first so counters
    # from different runs can be normalised against each other.
    runs: List[List[str]] = []
    current: List[str] = []
    used = 0
    reserved = cost(reference) if reference else 0
    for name in body:
        c = cost(name)
        if c + reserved > budget:
            # Cannot fit even alone alongside the reference — give it its own
            # run without the reference and flag it, rather than dropping it.
            runs.append([name])
            continue
        if used + c > budget - reserved:
            if current:
                runs.append(([reference] if reference else []) + current)
            current, used = [], 0
        current.append(name)
        used += c
    if current:
        runs.append(([reference] if reference else []) + current)
    if not runs and reference:
        runs = [[reference]]

    for i, events in enumerate(runs, 1):
        est = sum(cost(e) for e in events)
        plan["runs"].append({
            "run": i,
            "events": events,
            "estimated_cost": est,
            "fits_estimate": est <= budget,
            "derived": [e for e in events if available.get(e, {}).get("derived")],
            # A counter too expensive to share a run with the reference gets its
            # own run without it. Flagged, not hidden: those numbers cannot be
            # normalised per-cycle against the other runs.
            "has_reference": bool(reference) and reference in events,
            "env": {
                "DFTRACER_ENABLE_PAPI_TRACING": "1",
                "DFTRACER_PAPI_EVENTS": ",".join(events),
                "DFTRACER_PAPI_SAMPLE_INTERVAL_MS": "100",
            },
        })

    plan["verify"] = (
        "For every run, read args.multiplex back out of the trace: 0 = exact "
        "hardware counts, 1 = time-shared estimates (do not quote those as "
        "measurements). Also compare the PAPI_* keys that actually landed in "
        "args against DFTRACER_PAPI_EVENTS — dftracer drops a preset it cannot "
        "fit rather than reporting nonsense, so a run can succeed with fewer "
        "counters than requested."
    )
    return plan


def detect_tracing_features(
    hostname: Optional[str] = None,
    code_type: str = "mixed",
) -> Dict[str, Any]:
    """One call that answers what this node can collect, for session_detect.

    Returns the PAPI probe, the power probe, the counter plan for *code_type*,
    and a ``recommended_features`` dict that maps straight onto session_detect's
    feature flags.
    """
    papi = probe_papi(hostname)
    power = probe_power(hostname)
    plan = papi_counter_plan(code_type, hostname=hostname, papi=papi)

    recommended: Dict[str, Any] = {}
    # Power is enabled whenever the node can measure it: it is node-level
    # telemetry collected by dftracer_service, so it costs the application
    # nothing. PAPI is NOT auto-enabled — it samples inside the traced process
    # and the operator chooses to pay for it — but when it is switched on, the
    # counters come from the plan rather than from dftracer's build-time default.
    if power.get("can_enable"):
        recommended.update(power["features"])
    if papi.get("available"):
        recommended["papi_supported"] = True
        recommended["papi_hardware_counters"] = papi.get("hardware_counters")
        if plan.get("runs"):
            recommended["papi_events"] = plan["runs"][0]["events"]

    return {
        "system": _base_hostname(hostname),
        "papi": papi,
        "power": power,
        "papi_plan": plan,
        "recommended_features": recommended,
    }


def _fmt_papi(papi: Dict[str, Any]) -> List[str]:
    lines = ["\n### PAPI hardware counters"]
    if not papi.get("available"):
        lines.append(f"  NOT available — {papi.get('error')}")
        if papi.get("module"):
            lines.append(f"  (systems.yaml pins `{papi['module']}`)")
        return lines
    lines += [
        f"  version: {papi.get('version')}  (module: `{papi.get('module') or 'not pinned'}`)",
        f"  prefix: `{papi.get('prefix')}`",
        f"  **hardware counters: {papi.get('hardware_counters')}** — "
        f"{len(papi.get('presets') or {})} presets are listed, but only this many fit at once;",
        "  ask for more and PAPI time-shares them and reports scaled estimates, "
        "with a zero exit code.",
        f"  derived presets (cost >= 2 counters each): "
        f"{', '.join(papi.get('derived_presets') or []) or 'none'}",
    ]
    comps = papi.get("components") or {}
    if comps:
        lines.append("\n  components:")
        for name, info in comps.items():
            if info.get("enabled"):
                lines.append(f"    - `{name}` enabled — {info.get('description', '')}")
            else:
                lines.append(f"    - `{name}` DISABLED — {info.get('reason', '')}")
    return lines


def _fmt_power(power: Dict[str, Any]) -> List[str]:
    lines = ["\n### Node power"]
    if not power.get("sources"):
        lines.append("  No power domain found on this node — nothing to enable.")
        return lines
    for src in power["sources"]:
        lines.append(f"  - {src['kind']}: `{src['backend']}` ({src['detail']}) — {src['note']}")
    if power.get("opted_out"):
        lines.append("  Power is available but systems.yaml sets `tracing.power: off`.")
    elif power.get("can_enable"):
        feats = ", ".join(f"{k}={v}" for k, v in (power.get("features") or {}).items())
        lines.append(f"  **ENABLE IT** — session_detect features: {feats}")
    for hazard in power.get("hazards") or []:
        lines.append(f"  ! {hazard}")
    return lines


def _fmt_papi_plan(plan: Dict[str, Any]) -> List[str]:
    lines = [f"\n### PAPI counters for code_type=`{plan.get('code_type')}`"]
    if plan.get("error"):
        lines.append(f"  {plan['error']}")
        return lines
    if plan.get("caveat"):
        lines.append(f"  NOTE: {plan['caveat']}")
    for group in plan.get("groups") or []:
        lines.append(f"  - **{group['group']}** — {group['why']}")
        lines.append(f"    {', '.join(group['presets']) or '(none on this node)'}")
        if group["not_on_this_node"]:
            lines.append(f"    not on this node: {', '.join(group['not_on_this_node'])}")
    lines.append(
        f"\n  Partitioned into {len(plan.get('runs') or [])} run(s) against a budget of "
        f"{plan.get('budget')} ({plan.get('cost_model')}):"
    )
    for run in plan.get("runs") or []:
        note = "" if run.get("has_reference", True) else \
            f"  (no {_PAPI_REFERENCE} — too costly to share this run; not normalisable)"
        lines.append(
            f"    run {run['run']} (est. cost {run['estimated_cost']}/{plan.get('budget')}): "
            f"`DFTRACER_PAPI_EVENTS={run['env']['DFTRACER_PAPI_EVENTS']}`{note}"
        )
    if plan.get("verify"):
        lines.append(f"\n  VERIFY: {plan['verify']}")
    return lines


def _fmt_features(hostname: Optional[str], code_type: str) -> str:
    """The probed tracing-feature report appended to system_detect."""
    info = detect_tracing_features(hostname, code_type)
    lines = ["## Tracing features (probed on this node)"]
    lines += _fmt_papi(info["papi"])
    lines += _fmt_papi_plan(info["papi_plan"])
    lines += _fmt_power(info["power"])
    rec = info.get("recommended_features") or {}
    if rec:
        lines.append("\n### Recommended session_detect features")
        for key, value in rec.items():
            lines.append(f"  - `{key}` = {value}")
    return "\n".join(lines)


def _fmt_system(name: str, cfg: Dict[str, Any]) -> str:
    """Format a system config dict into a human-readable string."""
    lines = [
        f"## System: {name}",
        f"**{cfg.get('description', '')}**",
        f"- sudo available: {cfg.get('sudo', False)}",
        f"- MPI launcher: `{cfg.get('mpi_launcher', 'mpirun')}`",
    ]
    modules = cfg.get("modules", [])
    if modules:
        lines.append("\n### Modules (load in order)")
        for i, m in enumerate(modules, 1):
            lines.append(f"  {i}. {m}")
    env = cfg.get("env", {})
    if env:
        lines.append("\n### Environment variables")
        for k, v in env.items():
            lines.append(f"  `export {k}=\"{v}\"`")
    notes = cfg.get("notes", "").strip()
    if notes:
        lines.append(f"\n### Notes\n{notes}")
    return "\n".join(lines)


def register_system_tools(mcp: FastMCP) -> None:
    """Register all system detection tools onto *mcp*."""

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


class SystemService(MCPService):
    """MCP service for system detection and configuration."""

    def __init__(self) -> None:
        self.system_subservice = FastMCP("DFTracerSystem")
        register_system_tools(self.system_subservice)

    def execute(self, data: dict) -> Optional[str]:
        return "Use system_detect, system_list, or system_save tools."

    @property
    def name(self) -> str:
        return "dftracer-system"


MCPServiceFactory.register("dftracer-system", SystemService())
