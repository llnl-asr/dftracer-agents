#!/bin/bash
# Render final_report/REPORT.md -> final_report/REPORT.pdf.
# Saved here (not just run ad hoc) so the PDF is reproducible without this
# tool: `bash scripts/render_pdf.sh` from inside final_report/ regenerates it
# from whatever REPORT.md currently contains.
set -e
cd "$(dirname "$0")/.."   # final_report/
SRC="REPORT.md"
DST="REPORT.pdf"

if command -v pandoc >/dev/null 2>&1; then
    pandoc "$SRC" -o "$DST" --pdf-engine=xelatex 2>/dev/null \
        || pandoc "$SRC" -o "$DST"
    exit $?
fi

# No pandoc (common on locked-down HPC login/compute nodes, no sudo): pure
# Python fallback, installed locally with no privilege escalation.
PYBIN="${PYTHON_BIN:-python3}"
"$PYBIN" - "$SRC" "$DST" <<'PYEOF'
import subprocess, sys, os

def ensure(pkg, import_name=None):
    import_name = import_name or pkg
    try:
        __import__(import_name)
    except ImportError:
        # --user fails inside a venv ("user site-packages are not visible");
        # a venv's own pip already installs to its own site-packages without
        # it, so only pass --user when NOT running inside a venv.
        in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
        cmd = [sys.executable, "-m", "pip", "install", "-q"]
        if not in_venv:
            cmd.append("--user")
        cmd.append(pkg)
        subprocess.run(cmd, check=True)

ensure("markdown-it-py", "markdown_it")
ensure("xhtml2pdf")

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

src, dst = sys.argv[1], sys.argv[2]
md_text = open(src, encoding="utf-8").read()
html_body = MarkdownIt("commonmark").enable(["table", "strikethrough"]).render(md_text)
STYLE = (
    "body { font-family: Helvetica, Arial, sans-serif; font-size: 10pt; } "
    "table { border-collapse: collapse; width: 100%; margin: 8px 0; } "
    "th, td { border: 1px solid #999; padding: 4px 6px; font-size: 9pt; } "
    "th { background: #eee; } "
    "pre { background: #f5f5f5; padding: 6px; font-size: 8pt; white-space: pre-wrap; } "
    "code { font-family: monospace; } "
    "h1, h2, h3 { color: #222; }"
)
html = (
    "<html><head><meta charset=\"utf-8\"><style>" + STYLE + "</style></head>"
    "<body>" + html_body + "</body></html>"
)

with open(dst, "wb") as fh:
    result = pisa.CreatePDF(html, dest=fh)
if result.err:
    sys.exit(f"xhtml2pdf reported {result.err} error(s)")
PYEOF
