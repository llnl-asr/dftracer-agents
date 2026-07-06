# Typst Template Catalog

Full catalog of Typst Universe templates for report production.

## Academic Papers

| Template | Source | Venues | Key Features |
|---|---|---|---|
| `bloated-neurips` | typst.app/universe | NeurIPS | Anonymous submission, camera-ready, line numbering |
| `lucky-icml` | typst.app/universe | ICML | Multi-column, BibLaTeX/Hayagriva |
| `clear-iclr` | typst.app/universe | ICLR | OpenReview-compatible PDF |
| `blind-cvpr` | typst.app/universe | CVPR 2022/2025 | Anonymous + camera-ready, arXiv preprint |
| `clean-acmart` | typst.app/universe | ACM (SOSP, EuroSys, ASPLOS) | ACM CCS, review mode, ORCID |
| `charged-ieee` | typst.app/universe | IEEE | Two-column, numeric citations, index terms |
| `fine-lncs` | typst.app/universe | Springer LNCS | LaTeX replica, theorem/proof environments |
| `arkheion` | typst.app/universe | arXiv, bio-arXiv | Single-column, ORCID, appendix, minimal |
| `daskol/typst-templates` | GitHub | Multi-venue | NeurIPS/ICML/ICLR/CVPR/JMLR/TMLR/RLJ collection |

**Init:**
```bash
/workspace/bin/init-report.sh --name <project> --theme <template>
```

## Technical Reports

| Template | Source | Best For | Key Features |
|---|---|---|---|
| `truss-report` | GitHub / Universe | Systems analysis, ADRs, benchmarks | Requirement blocks, ADRs, callouts, trade-off matrices, landscape pages |
| `basic-report` | Universe | Lab reports, manuals, assignments | Compact mode, clean TOC, Vollkorn+Ubuntu fonts |
| `modern-technique-report` | Universe | Course/methodology reports | Custom cover, theme colors, author grid |

**Init:**
```bash
/workspace/bin/init-report.sh --name <project> --theme truss-report
```

## Business / Client Reports

| Template | Source | Best For | Key Features |
|---|---|---|---|
| `biz-report` | Universe | Corporate reports, audits, proposals | Tables, drop caps, infoboxes, multi-chapter |
| `letterloom` | Universe | Formal proposals, business letters | Multiple signatures, letterheads, smart enclosures |
| `letter-pro` | Universe | DIN 5008 business letters (EU) | German/EU standard compliance |
| `pro-letter` | Universe | US-style business letters | Notary page, flexible styling |

**Init:**
```bash
/workspace/bin/init-report.sh --name <project> --theme biz-report
```

## Internal Memos

| Template | Source | Best For | Key Features |
|---|---|---|---|
| `tufte-memo` | Universe | Decision briefs, status updates | Sidenotes, wide blocks, dense info, Edward Tufte style |
| `minimal-note` | Universe | Quick notes, short briefs | Minimal, colorful, fast to compile |
| `quick-minutes` | Universe | Meeting minutes | Dialogue tracking, decisions, timestamps |
| `tonguetoquill-usaf-memo` | Universe | Formal structured memos | Paragraph numbering, classification banners |

**Init:**
```bash
/workspace/bin/init-report.sh --name <project> --theme tufte-memo
```

## Invoice / Proposals

| Template | Source | Key Features |
|---|---|---|
| `invoice-maker` | Universe | Automatic calculations, itemized billing, tax, QR code |
| `invoice-pro` | Universe | DIN 5008 compliant, EPC-QR-Code, GiroCode |

## Command Reference

```bash
# Initialize project with template
/workspace/bin/init-report.sh --name my-report --theme biz-report

# Compile to PDF
cd /workspace/reports/my-report
typst compile main.typ report.pdf

# Watch mode (auto-recompile on changes)
typst watch main.typ report.pdf
```

## Environment Workaround

If `typst init` fails with permission denied, ensure these env vars are set:
```bash
export TMPDIR=/workspace/tmp
export TYPST_PACKAGE_CACHE_PATH=/workspace/.typst/cache
mkdir -p "$TMPDIR" "$TYPST_PACKAGE_CACHE_PATH"
```

The `init-report.sh` script handles this automatically.
