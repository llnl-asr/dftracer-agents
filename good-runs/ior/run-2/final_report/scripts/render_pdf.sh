#!/bin/bash
# Render final_report/REPORT.md (and README.md) to PDF, pure-Python, no sudo.
# Requires: markdown-it-py (already present) + xhtml2pdf (pulls in reportlab).
# Install once into a local, session-scoped target dir (never system-wide):
#   pip install --no-input --target "$WS/tmp/pdftools" xhtml2pdf
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"   # final_report/
PDFTOOLS="${PDFTOOLS_DIR:-}"   # optional override; falls back to sys path

python3 "$HERE/scripts/_render_pdf.py" "$HERE" "$PDFTOOLS"
