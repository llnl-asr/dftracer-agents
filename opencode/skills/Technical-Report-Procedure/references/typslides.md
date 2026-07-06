# typslides — Typst Presentation Package

Reference card for the `typslides` package from Typst Universe, used for creating professional research/deck presentations.

## Package Info

- **Name:** `@preview/typslides`
- **Version:** `1.3.3`
- **Min Typst:** `0.14.0`
- **License:** GPL-3.0-or-later
- **Repo:** https://github.com/manjavacas/typslides
- **Universe:** https://typst.app/universe/package/typslides/

## Import

```typst
#import "@preview/typslides:1.3.3": *
```

## Global Configuration

```typst
#show: typslides.with(
  ratio: "16-9",       // or "4-3"
  theme: "dusky",       // "bluey", "reddy", "greeny", "yelly", "purply", "dusky", "darky", or rgb("hex")
  font: "Fira Sans",
  font-size: 20pt,
  link-style: "color",  // "color", "underline", "both"
  show-progress: true,
)
```

## Slide Commands

| Command | Purpose | Typical Use |
|---------|---------|-------------|
| `#front-slide(title, subtitle, authors, info)` | Title slide | Slide 1 |
| `#title-slide[content]` | Section divider / new section header | Major section breaks |
| `#slide[content]` | Regular slide, no title | Simple bullet lists |
| `#slide(title: "Title")[content]` | Slide with title | Most content slides |
| `#focus-slide[content]` | Centered emphasis, auto-sized | Key takeaway, big quote |
| `#blank-slide[content]` | Raw, no formatting | Custom layouts |
| `#bibliography-slide(bib)` | Auto-formatted references | Final slide |

## Utility Functions

| Function | Effect | Example |
|----------|--------|---------|
| `#stress[text]` | Bold + theme color | `#stress[what]` |
| `#framed[text]` | Colored background box | `#framed[Note: this is important]` |
| `#framed(title: "Label")[text]` | Box with title bar | `#framed(title: "Result")[...]` |
| `#grayed[text]` | Gray muted text | `#grayed[Secondary point]` |
| `#cols(columns: (2fr, 1fr), gutter: 2em)[col1][col2]` | Multi-column layout | Side-by-side content |
| `#table-of-contents()` | Auto-generated TOC | After front slide |

## Color Themes (also usable as color functions)

`#bluey("text")`, `#reddy("text")`, `#greeny("text")`, `#yelly("text")`, `#purply("text")`, `#dusky("text")`, `#darky("text")`

## Compilation

```bash
typst compile input.typ output.pdf
```

## Common Pitfalls

1. **Do not mix raw `#set page` with typslides.** The package manages pagination internally. Manual `#pagebreak()` and `#set page(margin:...)` will conflict with the theme.

2. **Font not installed.** If `Fira Sans` is unavailable, Typst falls back to the system default. Check with `typst fonts` and install the font if needed.

3. **Using `gray()` instead of `luma()`.** In newer Typst versions, `gray()` is deprecated for color interpolation. Use `luma(80%)` or `luma(50%)` for neutral grays.

4. **Content overflow on small-ratio slides.** `4-3` ratio with 20pt font can cause overflow. Reduce font-size to 18pt or use `16-9`.

## Minimal Working Example

```typst
#import "@preview/typslides:1.3.3": *
#show: typslides.with(ratio: "16-9", theme: "dusky", font: "Fira Sans", font-size: 20pt)

#front-slide(
  title: "FOO: Explainable I/O in HPC",
  subtitle: "From Correlation to Causation",
  authors: "Author Name",
  info: "Affiliation / Institution",
)

#slide(title: "The Problem")[
  - ML-driven I/O optimizers predict #stress[what] to tune
  - But #stress[no existing tool] tells us #stress[why]
]

#focus-slide[
  "Correlation predicts. Causation explains."
]
```
