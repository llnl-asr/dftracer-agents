import sys, os

here = sys.argv[1]
pdftools = sys.argv[2] if len(sys.argv) > 2 else ""
if pdftools:
    sys.path.insert(0, pdftools)

from markdown_it import MarkdownIt
from xhtml2pdf import pisa

md = MarkdownIt("commonmark", {"html": True}).enable("table")

STYLE = (
    "body { font-family: Helvetica, Arial, sans-serif; font-size: 9pt; }"
    "table { border-collapse: collapse; width: 100%; margin-bottom: 10px; }"
    "th, td { border: 1px solid #999; padding: 3px 5px; font-size: 7.5pt; }"
    "code, pre { background: #f4f4f4; font-family: Courier, monospace; font-size: 7.5pt; }"
    "pre { padding: 4px; white-space: pre-wrap; }"
    "h1 { font-size: 16pt; } h2 { font-size: 13pt; } h3 { font-size: 11pt; }"
)

def render(src_name, out_name):
    src = os.path.join(here, src_name)
    if not os.path.exists(src):
        print("skip " + src_name + ": not found")
        return
    text = open(src, encoding="utf-8").read()
    body = md.render(text)
    html = "<html><head><meta charset='utf-8'/><style>" + STYLE + "</style></head><body>" + body + "</body></html>"
    out = os.path.join(here, out_name)
    with open(out, "wb") as f:
        result = pisa.CreatePDF(html, dest=f)
    if result.err:
        print("WARNING: " + str(result.err) + " error(s) rendering " + src_name)
    else:
        size = os.path.getsize(out)
        print("wrote " + out + " (" + str(size) + " bytes)")

render("REPORT.md", "REPORT.pdf")
render("README.md", "README.pdf")