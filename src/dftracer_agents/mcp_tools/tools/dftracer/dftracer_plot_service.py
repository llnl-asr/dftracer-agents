"""
DFTracer Plot Service — MCP tools for visualising dftracer I/O traces.

Generates publication-quality plots from dftracer ``.pfw.gz`` trace data using
one of three backends, tried in the following order:

1. **dftracer.analyzer Python API** (``init_with_hydra`` → ``get_flat_view``):
   Uses the official DFAnalyzer library when available.  Requires a working
   dftracer Python installation with the Hydra configuration stack.

2. **dftracer_stats CLI** (``--report detailed --json``): Falls back to the
   command-line tool for fast indexed data access when the Python API is
   unavailable or fails.

3. **Pure-Python scan** (reads ``.pfw.gz`` / ``.pfw`` files directly via
   ``gzip`` + ``json``): Always available; no external dependencies beyond
   ``matplotlib``.  Used unconditionally for ``io_breakdown`` and ``heatmap``
   plot types, and as the universal fallback for all other types.

Output formats:

- ``png``  — static raster image via matplotlib at 150 dpi (default).
- ``html`` — interactive Bokeh chart opened in any browser; supports pan, zoom,
  hover tooltips, and legend toggling.
- ``svg``  — scalable vector image via matplotlib (no DPI setting applied).

All plot functions write a file to disk and return a result string containing
the output file path.  The caller (or the MCP client) is responsible for
opening or displaying the file.

Key exports:
    DFTracerPlotService:  ``MCPService`` subclass registering the ``plot`` and
                          ``plot_all`` FastMCP tools.
    run():                Standalone entry point for running the plot server
                          directly (``python -m dftracer_plot_service``).

Runtime notes:
    ``matplotlib`` is imported at module load time with the ``Agg`` non-
    interactive backend forced before any other ``matplotlib`` import, so the
    module is safe to load in headless server environments with no display.
    ``bokeh``, ``PIL`` (Pillow), and ``numpy`` are optional and imported lazily
    inside the functions that need them.
"""
from __future__ import annotations

import base64
import collections
import gzip
import zlib
import json
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")  # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from fastmcp import FastMCP

from ...mcp_service_factory import MCPService, MCPServiceFactory


# ── shared trace-reading helpers ────────────────────────────────────────────

def _fmt_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _eval_filter(expr: str, ev: Dict[str, Any]) -> bool:
    if not expr:
        return True
    ns: Dict[str, Any] = {
        "cat":  ev.get("cat", ""),
        "name": ev.get("name", ""),
        "dur":  ev.get("dur", 0),
        "ts":   ev.get("ts", 0),
        "pid":  ev.get("pid", 0),
        "tid":  ev.get("tid", 0),
    }
    ns.update(ev.get("args", {}))
    try:
        return bool(eval(expr, {"__builtins__": {}}, ns))  # noqa: S307
    except Exception:
        return False


def _scan_events(
    trace_path: str,
    max_files: int,
    filter_expr: str = "",
) -> Tuple[List[Dict[str, Any]], int]:
    """Return (events, total_files) from .pfw.gz scan."""
    p = Path(trace_path)
    pfw_files = sorted(p.glob("*.pfw.gz")) + sorted(p.glob("*.pfw"))
    total = len(pfw_files)
    events: List[Dict[str, Any]] = []
    for fpath in pfw_files[:max_files]:
        opener = gzip.open if str(fpath).endswith(".gz") else open
        # A trace whose writer was killed at teardown (common for the dftracer_service
        # node-counter daemon) leaves a TRUNCATED gzip stream. Iterating it raises EOFError
        # partway through. Catching that around the WHOLE file would discard every record
        # already read -- in a real case ~515k usable records per node would have been
        # thrown away and reported as "no events". Keep the readable prefix instead.
        try:
            with opener(fpath, "rt") as fh:
                try:
                    for line in fh:
                        line = line.strip().rstrip(",")
                        if not line or line in ("[", "]"):
                            continue
                        try:
                            ev = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if ev.get("ph") != "X":
                            continue
                        if _eval_filter(filter_expr, ev):
                            events.append(ev)
                except (EOFError, OSError, gzip.BadGzipFile, zlib.error):
                    # Truncated/corrupt tail: retain everything decoded so far.
                    pass
        except Exception:
            continue
    return events, total


# ── plot-output helpers ─────────────────────────────────────────────────────

def _default_output(suffix: str) -> str:
    return str(Path(tempfile.gettempdir()) / f"dftracer_{uuid.uuid4().hex[:8]}{suffix}")


def _thumb_b64(path: str, max_kb: int = 80) -> str:
    """Return a base64-encoded thumbnail of a PNG (≤ max_kb KB) or ''."""
    try:
        import io
        from PIL import Image  # type: ignore[import]
        img = Image.open(path)
        img.thumbnail((640, 480))
        buf = io.BytesIO()
        img.save(buf, format="PNG", optimize=True)
        data = buf.getvalue()
        if len(data) <= max_kb * 1024:
            return base64.b64encode(data).decode()
    except Exception:
        # PIL not installed — just encode the raw file if small enough
        try:
            raw = Path(path).read_bytes()
            if len(raw) <= max_kb * 1024:
                return base64.b64encode(raw).decode()
        except Exception:
            pass
    return ""


def _fig_result(fig: "plt.Figure", output_path: str, fmt: str, title: str) -> str:
    """Save figure, close it, return a summary string with the file path."""
    suffix = f".{fmt}"
    if not output_path:
        output_path = _default_output(suffix)
    if not output_path.endswith(suffix):
        output_path += suffix

    dpi = 150 if fmt == "png" else None
    fig.savefig(output_path, format=fmt, dpi=dpi, bbox_inches="tight")
    plt.close(fig)

    result = f"Plot saved: {output_path}\nTitle: {title}\nFormat: {fmt}"
    if fmt == "png":
        thumb = _thumb_b64(output_path)
        if thumb:
            result += f"\n[thumbnail base64 omitted — open the file to view]"
    return result


def _bokeh_result(output_path: str, bk_plot: Any, title: str) -> str:
    """Save Bokeh plot as HTML and return summary."""
    from bokeh.plotting import output_file, save  # type: ignore[import]
    if not output_path:
        output_path = _default_output(".html")
    if not output_path.endswith(".html"):
        output_path += ".html"
    output_file(output_path, title=title)
    save(bk_plot)
    return f"Interactive plot saved: {output_path}\nTitle: {title}\nFormat: html\nOpen in a browser to explore."


# ── plot builders ───────────────────────────────────────────────────────────

def _plot_time_series(
    events: List[Dict[str, Any]],
    title: str,
    n_buckets: int,
    fmt: str,
    output_path: str,
) -> str:
    """Line chart: read and write throughput (MB/s) over time."""
    if not events:
        return "Error: no events to plot"

    ts_vals = [ev["ts"] for ev in events if "ts" in ev]
    ts_min, ts_max = min(ts_vals), max(ts_vals)
    duration_us = max(ts_max - ts_min, 1)
    bucket_us = max(1_000_000, duration_us // max(n_buckets, 1))
    n = max(1, int(duration_us / bucket_us) + 1)

    read_b  = [0.0] * n
    write_b = [0.0] * n
    ops     = [0]   * n

    for ev in events:
        ts = ev.get("ts", ts_min)
        b = min(int((ts - ts_min) / bucket_us), n - 1)
        ops[b] += 1
        name = ev.get("name", "")
        ret = ev.get("args", {}).get("ret", 0) or 0
        if name in ("read", "fread", "pread", "pread64", "readv"):
            read_b[b] += max(ret, 0)
        elif name in ("write", "fwrite", "pwrite", "pwrite64", "writev"):
            write_b[b] += max(ret, 0)

    bucket_s = bucket_us / 1e6
    times = [ts_min / 1e6 + i * bucket_s for i in range(n)]
    # Normalise to elapsed seconds from start
    t0 = times[0]
    times = [t - t0 for t in times]
    read_mbs  = [b / bucket_s / 1e6 for b in read_b]
    write_mbs = [b / bucket_s / 1e6 for b in write_b]

    if fmt == "html":
        from bokeh.plotting import figure  # type: ignore[import]
        from bokeh.models import HoverTool  # type: ignore[import]
        p = figure(
            title=title, width=900, height=400,
            x_axis_label="Time (s)", y_axis_label="Throughput (MB/s)",
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )
        p.add_tools(HoverTool(tooltips=[("time", "@x{0.1f}s"), ("MB/s", "@y{0.3f}")]))
        p.line(times, read_mbs,  legend_label="read",  line_color="#2196F3", line_width=2)
        p.line(times, write_mbs, legend_label="write", line_color="#F44336", line_width=2)
        p.legend.location = "top_right"
        p.legend.click_policy = "hide"
        return _bokeh_result(output_path, p, title)

    fig, ax = plt.subplots(figsize=(10, 4))
    ax.plot(times, read_mbs,  label="read",  color="#2196F3", linewidth=1.5)
    ax.plot(times, write_mbs, label="write", color="#F44336", linewidth=1.5)
    ax2 = ax.twinx()
    ax2.bar(times, ops, width=bucket_s * 0.8, color="#9E9E9E", alpha=0.25, label="ops")
    ax2.set_ylabel("ops / bucket", color="#9E9E9E", fontsize=9)
    ax2.tick_params(axis="y", labelcolor="#9E9E9E")
    ax.set_xlabel("Elapsed time (s)")
    ax.set_ylabel("Throughput (MB/s)")
    ax.set_title(title)
    ax.legend(loc="upper left")
    fig.tight_layout()
    return _fig_result(fig, output_path, fmt, title)


def _plot_top_files(
    events: List[Dict[str, Any]],
    top_n: int,
    title: str,
    fmt: str,
    output_path: str,
) -> str:
    """Horizontal bar chart: top N files by total bytes transferred."""
    rows: Dict[int, Dict[str, Any]] = {}
    for ev in events:
        fhash = ev.get("args", {}).get("fhash", 0)
        if fhash not in rows:
            rows[fhash] = {"read_b": 0.0, "write_b": 0.0, "ops": 0}
        r = rows[fhash]
        r["ops"] += 1
        name = ev.get("name", "")
        ret = ev.get("args", {}).get("ret", 0) or 0
        if name in ("read", "fread", "pread", "pread64", "readv"):
            r["read_b"] += max(ret, 0)
        elif name in ("write", "fwrite", "pwrite", "pwrite64", "writev"):
            r["write_b"] += max(ret, 0)

    top = sorted(rows.items(), key=lambda x: -(x[1]["read_b"] + x[1]["write_b"]))[:top_n]
    if not top:
        return "Error: no file-level events found"

    labels  = [f"fhash={fh}" for fh, _ in top]
    reads   = [r["read_b"]  / 1e6 for _, r in top]
    writes  = [r["write_b"] / 1e6 for _, r in top]

    if fmt == "html":
        from bokeh.plotting import figure  # type: ignore[import]
        from bokeh.transform import dodge  # type: ignore[import]
        from bokeh.models import ColumnDataSource  # type: ignore[import]
        src = ColumnDataSource(dict(labels=labels, reads=reads, writes=writes))
        p = figure(
            title=title, y_range=list(reversed(labels)),
            width=900, height=max(300, top_n * 28),
            x_axis_label="MB",
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )
        p.hbar(y=dodge("labels", -0.2, range=p.y_range), right="reads",
               height=0.35, source=src, color="#2196F3", legend_label="read")
        p.hbar(y=dodge("labels",  0.2, range=p.y_range), right="writes",
               height=0.35, source=src, color="#F44336", legend_label="write")
        p.legend.location = "bottom_right"
        return _bokeh_result(output_path, p, title)

    fig, ax = plt.subplots(figsize=(10, max(4, top_n * 0.35)))
    y = range(len(labels))
    bar_h = 0.35
    ax.barh([i + bar_h / 2 for i in y], reads,  bar_h, label="read",  color="#2196F3")
    ax.barh([i - bar_h / 2 for i in y], writes, bar_h, label="write", color="#F44336")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=8)
    ax.invert_yaxis()
    ax.set_xlabel("MB")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    return _fig_result(fig, output_path, fmt, title)


def _plot_top_procs(
    events: List[Dict[str, Any]],
    top_n: int,
    title: str,
    fmt: str,
    output_path: str,
) -> str:
    """Horizontal stacked bar: top N PIDs by bytes read vs written."""
    rows: Dict[int, Dict[str, Any]] = {}
    for ev in events:
        pid = ev.get("pid", 0)
        if pid not in rows:
            rows[pid] = {"read_b": 0.0, "write_b": 0.0, "ops": 0}
        r = rows[pid]
        r["ops"] += 1
        name = ev.get("name", "")
        ret = ev.get("args", {}).get("ret", 0) or 0
        if name in ("read", "fread", "pread", "pread64", "readv"):
            r["read_b"] += max(ret, 0)
        elif name in ("write", "fwrite", "pwrite", "pwrite64", "writev"):
            r["write_b"] += max(ret, 0)

    top = sorted(rows.items(), key=lambda x: -(x[1]["read_b"] + x[1]["write_b"]))[:top_n]
    if not top:
        return "Error: no process-level events found"

    labels = [str(pid) for pid, _ in top]
    reads  = [r["read_b"]  / 1e6 for _, r in top]
    writes = [r["write_b"] / 1e6 for _, r in top]

    if fmt == "html":
        from bokeh.plotting import figure  # type: ignore[import]
        from bokeh.models import ColumnDataSource  # type: ignore[import]
        src = ColumnDataSource(dict(
            labels=labels, reads=reads, writes=writes,
            total=[a + b for a, b in zip(reads, writes)],
        ))
        p = figure(
            title=title, y_range=list(reversed(labels)),
            width=900, height=max(300, top_n * 28),
            x_axis_label="MB",
            tools="pan,wheel_zoom,box_zoom,reset,save",
        )
        p.hbar_stack(["reads", "writes"], y="labels", height=0.6,
                     color=["#2196F3", "#F44336"],
                     legend_label=["read", "write"], source=src)
        p.legend.location = "bottom_right"
        return _bokeh_result(output_path, p, title)

    fig, ax = plt.subplots(figsize=(10, max(4, top_n * 0.4)))
    y = range(len(labels))
    ax.barh(list(y), reads,  0.6, label="read",  color="#2196F3")
    ax.barh(list(y), writes, 0.6, left=reads, label="write", color="#F44336")
    ax.set_yticks(list(y))
    ax.set_yticklabels(labels, fontsize=9)
    ax.invert_yaxis()
    ax.set_xlabel("MB")
    ax.set_title(title)
    ax.legend()
    fig.tight_layout()
    return _fig_result(fig, output_path, fmt, title)


def _plot_io_breakdown(
    events: List[Dict[str, Any]],
    title: str,
    fmt: str,
    output_path: str,
) -> str:
    """Dual pie charts: operation-count breakdown by category and by function."""
    cat_counts: collections.Counter = collections.Counter()
    func_counts: collections.Counter = collections.Counter()
    for ev in events:
        cat_counts[ev.get("cat", "other")] += 1
        func_counts[ev.get("name", "other")] += 1

    if not cat_counts:
        return "Error: no events to plot"

    # Collapse tail functions into "other"
    top_funcs = dict(func_counts.most_common(8))
    other_n = sum(v for k, v in func_counts.items() if k not in top_funcs)
    if other_n:
        top_funcs["other"] = other_n

    colors_cat  = ["#2196F3", "#F44336", "#4CAF50", "#FF9800", "#9C27B0", "#00BCD4"]
    colors_func = ["#1565C0", "#B71C1C", "#2E7D32", "#E65100", "#6A1B9A",
                   "#00838F", "#37474F", "#558B2F", "#AD1457"]

    if fmt == "html":
        from bokeh.plotting import figure  # type: ignore[import]
        from bokeh.transform import cumsum  # type: ignore[import]
        from bokeh.models import ColumnDataSource  # type: ignore[import]
        import math
        from bokeh.layouts import row  # type: ignore[import]

        def _pie(counts: Dict[str, int], plot_title: str, colors: list) -> Any:
            total = sum(counts.values())
            angles = [v / total * 2 * math.pi for v in counts.values()]
            starts = [sum(angles[:i]) for i in range(len(angles))]
            ends   = [sum(angles[:i+1]) for i in range(len(angles))]
            src = ColumnDataSource(dict(
                label=list(counts.keys()),
                value=list(counts.values()),
                start_angle=starts,
                end_angle=ends,
                color=colors[:len(counts)],
                pct=[f"{v/total*100:.1f}%" for v in counts.values()],
            ))
            p = figure(title=plot_title, width=420, height=380,
                       tools="hover", tooltips="@label: @value (@pct)")
            p.wedge(x=0, y=1, radius=0.4,
                    start_angle="start_angle", end_angle="end_angle",
                    color="color", legend_field="label", source=src)
            p.axis.visible = False
            p.grid.visible = False
            p.legend.location = "right"
            return p

        p1 = _pie(dict(cat_counts.most_common()), "By category", colors_cat)
        p2 = _pie(top_funcs, "By function (top 8)", colors_func)
        layout = row(p1, p2)
        return _bokeh_result(output_path, layout, title)

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5))
    fig.suptitle(title, fontsize=13, fontweight="bold")

    cats  = list(cat_counts.keys())
    cvals = list(cat_counts.values())
    ax1.pie(cvals, labels=cats, autopct="%1.1f%%",
            colors=colors_cat[:len(cats)], startangle=90)
    ax1.set_title("By category")

    funcs = list(top_funcs.keys())
    fvals = list(top_funcs.values())
    ax2.pie(fvals, labels=funcs, autopct="%1.1f%%",
            colors=colors_func[:len(funcs)], startangle=90)
    ax2.set_title("By function (top 8)")

    fig.tight_layout()
    return _fig_result(fig, output_path, fmt, title)


def _plot_heatmap(
    events: List[Dict[str, Any]],
    top_n_pids: int,
    n_time_buckets: int,
    title: str,
    fmt: str,
    output_path: str,
) -> str:
    """Heatmap: PID (y-axis) × time bucket (x-axis), coloured by op count."""
    import numpy as np  # type: ignore[import]

    if not events:
        return "Error: no events to plot"

    ts_vals = [ev["ts"] for ev in events if "ts" in ev]
    ts_min, ts_max = min(ts_vals), max(ts_vals)
    duration_us = max(ts_max - ts_min, 1)
    bucket_us = max(1_000_000, duration_us // max(n_time_buckets, 1))
    n_t = max(1, int(duration_us / bucket_us) + 1)

    # Pick top N PIDs by event count
    pid_counts: collections.Counter = collections.Counter(
        ev.get("pid", 0) for ev in events
    )
    top_pids = [pid for pid, _ in pid_counts.most_common(top_n_pids)]
    pid_idx  = {pid: i for i, pid in enumerate(top_pids)}

    matrix = np.zeros((len(top_pids), n_t), dtype=float)
    for ev in events:
        pid = ev.get("pid", 0)
        if pid not in pid_idx:
            continue
        ts = ev.get("ts", ts_min)
        b = min(int((ts - ts_min) / bucket_us), n_t - 1)
        matrix[pid_idx[pid], b] += 1

    t_labels = [f"{(ts_min / 1e6 + i * bucket_us / 1e6 - ts_min / 1e6):.0f}s"
                for i in range(n_t)]

    if fmt == "html":
        from bokeh.plotting import figure  # type: ignore[import]
        from bokeh.models import LinearColorMapper, ColorBar  # type: ignore[import]
        from bokeh.palettes import Viridis256  # type: ignore[import]

        pid_labels = [str(p) for p in top_pids]
        mapper = LinearColorMapper(
            palette=Viridis256,
            low=0,
            high=float(matrix.max() or 1),
        )

        # Flatten for ColumnDataSource
        xs, ys, vals = [], [], []
        for pi, pid in enumerate(top_pids):
            for ti in range(n_t):
                xs.append(t_labels[ti])
                ys.append(str(pid))
                vals.append(float(matrix[pi, ti]))

        from bokeh.models import ColumnDataSource  # type: ignore[import]
        src = ColumnDataSource(dict(x=xs, y=ys, values=vals))
        p = figure(
            title=title,
            x_range=t_labels, y_range=pid_labels,
            width=max(800, n_t * 25), height=max(300, len(top_pids) * 20),
            x_axis_label="Time", y_axis_label="PID",
            tools="pan,wheel_zoom,box_zoom,reset,save,hover",
            tooltips=[("pid", "@y"), ("time", "@x"), ("ops", "@values")],
        )
        p.rect(x="x", y="y", width=1, height=1, source=src,
               fill_color={"field": "values", "transform": mapper},
               line_color=None)
        cb = ColorBar(color_mapper=mapper, location=(0, 0))
        p.add_layout(cb, "right")
        return _bokeh_result(output_path, p, title)

    fig, ax = plt.subplots(figsize=(max(10, n_t * 0.4), max(4, len(top_pids) * 0.35)))
    im = ax.imshow(matrix, aspect="auto", cmap="viridis", origin="upper")
    plt.colorbar(im, ax=ax, label="ops / bucket")
    ax.set_xticks(range(n_t))
    ax.set_xticklabels(t_labels, rotation=45, ha="right", fontsize=7)
    ax.set_yticks(range(len(top_pids)))
    ax.set_yticklabels([str(p) for p in top_pids], fontsize=8)
    ax.set_xlabel("Elapsed time")
    ax.set_ylabel("PID")
    ax.set_title(title)
    fig.tight_layout()
    return _fig_result(fig, output_path, fmt, title)


# ── dfanalyzer Python API path ──────────────────────────────────────────────

def _plot_from_python_api(
    trace_path: str,
    plot_type: str,
    analyzer_preset: str,
    top_n: int,
    title: str,
    fmt: str,
    output_path: str,
) -> Optional[str]:
    """Attempt to use dftracer.analyzer.init_with_hydra for data, then plot."""
    try:
        import tempfile as _tf
        from dftracer.analyzer import init_with_hydra  # type: ignore[import]

        run_dir = _tf.mkdtemp(prefix="dftracer_plot_")
        dfa = init_with_hydra(hydra_overrides=[
            "analyzer=dftracer",
            f"analyzer/preset={analyzer_preset}",
            "analyzer.checkpoint=False",
            f"trace_path={trace_path}",
            f"hydra.run.dir={run_dir}",
        ])

        # Map plot_type to a view
        vt_map = {
            "time_series": "time_range",
            "top_files":   "file_name",
            "top_procs":   "proc_name",
        }
        view = vt_map.get(plot_type)
        if not view:
            return None

        result = dfa.analyze_trace(view_types=[view])
        df = result.get_flat_view(view)
        if hasattr(df, "compute"):
            df = df.compute()

        # Build plot from the DataFrame
        if plot_type == "time_series":
            fig, ax = plt.subplots(figsize=(10, 4))
            time_col = next((c for c in df.columns if "time" in c.lower()), df.columns[0])
            read_col  = next((c for c in df.columns if "read"  in c.lower()), None)
            write_col = next((c for c in df.columns if "write" in c.lower()), None)
            if read_col:
                ax.plot(df[time_col], df[read_col],  label="read",  color="#2196F3")
            if write_col:
                ax.plot(df[time_col], df[write_col], label="write", color="#F44336")
            ax.set_title(title)
            ax.legend()
            return _fig_result(fig, output_path, fmt, title)

        if plot_type in ("top_files", "top_procs"):
            key_col = "fhash" if plot_type == "top_files" else "pid"
            key_col = next((c for c in df.columns if key_col in c.lower()), df.columns[0])
            val_col = next((c for c in df.columns if "byte" in c.lower() or "size" in c.lower()), df.columns[-1])
            df_top = df.nlargest(top_n, val_col)
            fig, ax = plt.subplots(figsize=(10, max(4, len(df_top) * 0.4)))
            ax.barh(df_top[key_col].astype(str), df_top[val_col] / 1e6, color="#2196F3")
            ax.invert_yaxis()
            ax.set_xlabel("MB")
            ax.set_title(title)
            return _fig_result(fig, output_path, fmt, title)

    except Exception:
        pass
    return None


# ── dftracer_stats CLI path ─────────────────────────────────────────────────

def _events_from_stats_cli(trace_path: str, filter_expr: str) -> Optional[List[Dict]]:
    """Try dftracer_stats --report detailed --json for event data."""
    cmd = ["dftracer_stats", "-d", trace_path, "--report", "detailed", "--json"]
    if filter_expr:
        cmd += ["--query", filter_expr]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if r.returncode == 0 and r.stdout.strip():
            raw = json.loads(r.stdout)
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict) and "events" in raw:
                return raw["events"]
    except Exception:
        pass
    return None


# ── main dispatcher ─────────────────────────────────────────────────────────

def _make_plot(
    trace_path: str,
    plot_type: str,
    filter_expr: str,
    analyzer_preset: str,
    top_n: int,
    n_buckets: int,
    title: str,
    output_format: str,
    output_path: str,
    max_files: int,
) -> str:
    p = Path(trace_path)
    if not p.exists():
        return f"Error: trace_path does not exist: {trace_path}"

    valid = {"time_series", "top_files", "top_procs", "io_breakdown", "heatmap"}
    if plot_type not in valid:
        return f"Unknown plot_type {plot_type!r}. Choose from: {', '.join(sorted(valid))}"

    valid_fmt = {"png", "html", "svg"}
    if output_format not in valid_fmt:
        return f"Unknown output_format {output_format!r}. Choose from: {', '.join(sorted(valid_fmt))}"

    if not title:
        title = f"{plot_type.replace('_', ' ').title()} — {p.name}"

    # ── 1. Python API ─────────────────────────────────────────────────────────
    if plot_type in ("time_series", "top_files", "top_procs"):
        api_result = _plot_from_python_api(
            trace_path, plot_type, analyzer_preset, top_n, title, output_format, output_path
        )
        if api_result:
            return "source: dftracer.analyzer Python API\n" + api_result

    # ── 2. Pure-Python scan (io_breakdown and heatmap always use this; ─────────
    #       others fall through here if the API crashed)
    events, total_files = _scan_events(trace_path, max_files, filter_expr)
    if not events:
        return "Error: no matching events found in trace"

    source_note = f"source: python_scan ({len(events):,} events, {min(max_files, total_files)}/{total_files} files)"

    if plot_type == "time_series":
        body = _plot_time_series(events, title, n_buckets, output_format, output_path)
    elif plot_type == "top_files":
        body = _plot_top_files(events, top_n, title, output_format, output_path)
    elif plot_type == "top_procs":
        body = _plot_top_procs(events, top_n, title, output_format, output_path)
    elif plot_type == "io_breakdown":
        body = _plot_io_breakdown(events, title, output_format, output_path)
    elif plot_type == "heatmap":
        body = _plot_heatmap(events, top_n, n_buckets, title, output_format, output_path)
    else:
        body = f"plot_type {plot_type!r} not implemented"

    return source_note + "\n" + body


# ── MCP service class ───────────────────────────────────────────────────────

class DFTracerPlotService(MCPService):
    """MCP tools for plotting dftracer I/O trace data."""

    def __init__(self) -> None:
        self.plot_subservice = FastMCP("DFTracerPlot")
        self._register_plot()
        self._register_plot_all()

    def _register_plot(self) -> None:  # noqa: F841
        @self.plot_subservice.tool()
        def plot(  # noqa: F841
            trace_path: str,
            plot_type: str = "time_series",
            filter_expr: str = "",
            analyzer_preset: str = "posix",
            top_n: int = 15,
            n_buckets: int = 20,
            title: str = "",
            output_format: str = "png",
            output_path: str = "",
            max_files: int = 30,
        ) -> str:
            """Generate a plot from a dftracer I/O trace directory.

            Reads .pfw.gz trace files and produces a chart saved to disk.
            Returns the output file path.  Open it in any image viewer or
            browser (for html format).

            PLOT TYPES:
              "time_series"   Line chart of read/write throughput (MB/s) over
                              time, with an op-count bar chart on a secondary
                              axis.  Best for understanding I/O bursts and
                              quiet periods.

              "top_files"     Horizontal grouped bar chart (read vs write in MB)
                              for the top N files by bytes transferred.  Use to
                              identify the hottest files in the trace.

              "top_procs"     Horizontal stacked bar chart (read + write in MB)
                              for the top N PIDs.  Use to compare MPI rank I/O
                              load balance.

              "io_breakdown"  Two pie charts: one for event-category breakdown
                              (POSIX, STDIO, COMPUTE, …) and one for top-8
                              function breakdown (read, write, open, …).

              "heatmap"       2-D heatmap: PIDs (y) × time buckets (x), coloured
                              by operation count.  Reveals which ranks are busy
                              at which times — great for load-imbalance diagnosis.

            OUTPUT FORMATS:
              "png"   Static image (matplotlib, 150 dpi).  Default.
              "html"  Interactive Bokeh chart.  Open in a browser; supports
                      pan, zoom, hover tooltips, and legend toggling.
              "svg"   Scalable vector image (matplotlib).

            FILTER EXPRESSION (filter_expr):
              Same DSL as the query tool.  Examples:
                'cat == "POSIX"'
                'name in ("read", "write") and dur > 500'
                'pid == 3537780'
              Leave empty to include all events.

            RECOMMENDED WORKFLOW:
              1. detect_preset(trace_path)       — pick posix vs dlio
              2. summarize_trace(trace_path)      — high-level overview
              3. plot(trace_path, "io_breakdown") — understand I/O mix
              4. plot(trace_path, "time_series")  — see burst patterns
              5. plot(trace_path, "heatmap")      — spot load imbalance
              6. plot(trace_path, "top_files",
                      filter_expr='name=="write"') — find hot write targets

            Args:
                trace_path:      Directory with .pfw.gz / .pfw trace files.
                plot_type:       time_series | top_files | top_procs |
                                 io_breakdown | heatmap  (default: time_series)
                filter_expr:     Optional event filter (default: all events).
                analyzer_preset: posix or dlio — used by the Python API backend.
                top_n:           Max bars / PIDs / files to show (default 15).
                n_buckets:       Time buckets for time_series / heatmap (default 20).
                title:           Custom chart title (auto-generated if empty).
                output_format:   png | html | svg  (default: png).
                output_path:     Where to write the file (auto-generated if empty).
                max_files:       Max .pfw.gz files to scan in Python fallback (default 30).
            """
            return _make_plot(
                trace_path=trace_path,
                plot_type=plot_type,
                filter_expr=filter_expr,
                analyzer_preset=analyzer_preset,
                top_n=top_n,
                n_buckets=n_buckets,
                title=title,
                output_format=output_format,
                output_path=output_path,
                max_files=max_files,
            )

    def _register_plot_all(self) -> None:  # noqa: F841
        @self.plot_subservice.tool()
        def plot_all(  # noqa: F841
            trace_path: str,
            output_dir: str = "",
            output_format: str = "png",
            filter_expr: str = "",
            analyzer_preset: str = "posix",
            top_n: int = 15,
            max_files: int = 30,
        ) -> str:
            """Generate all five standard plots for a trace in one call.

            Produces: time_series, top_files, top_procs, io_breakdown, heatmap.
            Saves each to output_dir (default: a new temp directory) and returns
            all five file paths.

            Use this for a complete visual overview of a trace when you want to
            show the user everything at once without making five separate tool calls.

            Args:
                trace_path:      Directory with .pfw.gz / .pfw trace files.
                output_dir:      Where to write files (auto temp dir if empty).
                output_format:   png | html | svg  (default: png).
                filter_expr:     Optional event filter (default: all events).
                analyzer_preset: posix or dlio.
                top_n:           Max bars / PIDs / files per chart.
                max_files:       Max .pfw.gz files to scan.
            """
            if not output_dir:
                output_dir = tempfile.mkdtemp(prefix="dftracer_plots_")
            Path(output_dir).mkdir(parents=True, exist_ok=True)

            name = Path(trace_path).name
            suffix = f".{output_format}"
            plot_types = ["time_series", "top_files", "top_procs", "io_breakdown", "heatmap"]

            results = [f"Plots for: {trace_path}", f"Output dir: {output_dir}", ""]
            for pt in plot_types:
                out = str(Path(output_dir) / f"{name}_{pt}{suffix}")
                res = _make_plot(
                    trace_path=trace_path,
                    plot_type=pt,
                    filter_expr=filter_expr,
                    analyzer_preset=analyzer_preset,
                    top_n=top_n,
                    n_buckets=20,
                    title=f"{pt.replace('_', ' ').title()} — {name}",
                    output_format=output_format,
                    output_path=out,
                    max_files=max_files,
                )
                # Extract just the file path line from the result
                path_line = next(
                    (l for l in res.splitlines() if l.startswith("Plot saved:") or l.startswith("Interactive plot")),
                    res.splitlines()[0] if res.splitlines() else "failed",
                )
                results.append(f"  [{pt}]  {path_line}")

            results.append("")
            results.append(f"Open with:  open {output_dir}  (macOS) / xdg-open {output_dir}  (Linux)")
            return "\n".join(results)

    def execute(self, data: dict) -> Optional[str]:
        return _make_plot(**{k: v for k, v in data.items() if k != "command"})

    @property
    def name(self) -> str:
        return "dftracer_plot"


MCPServiceFactory.register("dftracer_plot", DFTracerPlotService())


def run() -> None:
    """Run the standalone DFTracer Plot MCP server."""
    service = DFTracerPlotService()
    service.plot_subservice.run()


if __name__ == "__main__":
    run()
