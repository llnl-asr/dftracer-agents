# Typst Pitfalls from Session 2026-06-08

## 1. `$` Dollar Sign Triggers Math Mode

In Typst markup, `$` starts math mode. Dollar signs in plain text (prices, email addresses) must be escaped.

**Broken:**
```typst
Profound raised $35M in Series B.
```
Produces: `error: unknown variable: 35M` or `error: unclosed delimiter`.

**Correct:**
```typst
Profound raised \$35M in Series B.
```

**Note:** The backslash escape `\$` works inside `[...]` content brackets. Outside brackets, use `#"$"` or `#raw("$")`.

## 2. `/` Slash Triggers Fraction Mode

Between two expressions, `/` is fraction division in math mode. Text like `99/mo` inside brackets where a `$` already triggered math mode will produce `error: unknown variable: mo`.

**Broken (compound):**
```typst
Starter $99/mo
```
The `$` enters math mode, then `99/mo` is parsed as math, and `mo` is treated as an unknown variable.

**Correct:**
```typst
Starter \$99 per month
```
Avoid `/` in prices. Write "per month", "per year", or use `#raw("$99/mo")`.

## 3. `#text(weight: "bold")` Inside Table Cells Causes Cascading Delimiter Errors

When building table headers with styled text, do NOT pass `#text(weight: "bold")[Header]` as a table cell argument. Typst's parser sees the comma after `[Header]` as closing the `#text()` call, not as a table cell separator.

**Broken:**
```typst
#table(
  columns: 3,
  [#text(weight: "bold")[Skill], [#text(weight: "bold")[Lane], [#text(weight: "bold")[Output],
  ...
)
```
Produces: `error: unclosed delimiter` at the `#table(` line, followed by `error: the character # is not valid in code` cascading through the rest of the document.

**Correct:**
```typst
#table(
  columns: 3,
  [*Skill*], [*Lane*], [*Output*],
  ...
)
```
Use `*bold*` (emphasis syntax) or `[#strong[Header]]` instead. `*text*` is the idiomatic Typst way for bold in content mode.

**Why this happens:** `#text(weight: "bold")[Header]` is a function call in code mode. The parser sees `[Header]` as the argument to `text()`, then `,` as ending that call. The next token `[Lane]` is now outside the table argument list, leaving `table(` unclosed.

## 4. CLI vs Python Binding for Debugging

The Python `typst` package (pip-installable) masks error locations — it reports `typst.TypstError` with no line number.

The native CLI emits precise diagnostics:
```
error: unknown variable: mo
    ┌─ doc.typ:102:17
```

**Rule:** For any document with tables, custom macros, or math-mode risks, use the native CLI. The Python binding is for trivial one-pagers only.

## 5. Double-Star `**bold**` Markdown → Typst Renders as Literal `*`

When translating Markdown content into Typst, `**bold text**` (double star) does NOT become bold. Typst sees the outer `*` pair as emphasis markers and renders the inner `*` literally. The result is italic `*bold text*` with visible asterisks.

**Broken:**
```typst
This is **heavily skewed negative narrative**.
```
Renders as: *heavily skewed negative narrative* with visible asterisks, plus `warning: no text within stars`.

**Correct:**
```typst
This is *heavily skewed negative narrative*.
```
Typst uses single-star `*text*` for bold (or strong) emphasis. Double-star is not a valid Typst markup.

**Bulk fix:** If pasting large blocks of Markdown into a `.typ` file, run a pre-processor pass:
```bash
sed -i 's/\*\*\([^*]*\)\*\*/\*\1\*/g' doc.typ
```

## 6. Font Fallback for Headless Containers

"Liberation Serif" / "Liberation Sans" are standard on many Linux distros but may be missing in Docker images. Use "DejaVu Serif" / "DejaVu Sans" as the more universally available fallback.

## 7. Backslash Escapes from Markdown Do NOT Work in Typst Content

When converting Markdown content to Typst, backslash escapes that work in Markdown do NOT work in Typst content mode. The backslash is rendered literally.

**Broken:**
```typst
Coca-Cola \& Pepsi          // Renders as "Coca-Cola \& Pepsi"
Market share: 60\%          // Renders as "Market share: 60\%"
Price: \$99                 // Renders as "Price: \$99"
```

**Correct:**
```typst
Coca-Cola and Pepsi
Market share: 60%
Price: $99                  // or use "USD 99" to avoid math mode
```

**Rule:** Do NOT carry backslash escapes from Markdown into Typst. Typst content mode treats backslash as a literal character, not an escape sequence (except for `\$` which does work inside `[...]` brackets).

## 8. Markdown Pipe Tables Do NOT Work in Typst

Markdown-style pipe tables (`| A | B |\n|---|---|\n|1|2|`) are NOT valid Typst syntax. Typst uses programmatic `#table()` with explicit `columns:` and content arguments.

**Broken:**
```typst
| Dimension | Detail |
|---|---|
| Who | Ages 18-35 |
| Driver | "Coca-Cola is..." |
```

**Correct:**
```typst
#table(
  columns: (auto, 1fr),
  inset: 8pt,
  stroke: none,
  table.header([*Dimension*], [*Detail*]),
  [Who], [Ages 18-35],
  [Driver], ["Coca-Cola is..."],
)
```

**Always use `#table()` with `table.header()` instead of Markdown pipe tables.**

## 9. `table.header()` Must Match Column Count Exactly

When using `#table()` with `table.header(...)`, the number of cells in the header must exactly match the number of columns defined in `columns:`. Mismatched counts produce `error: unclosed delimiter`.

**Broken:**
```typst
#table(
  columns: 3,
  table.header([A], [B]),  // Only 2 cells for 3 columns
  [1], [2], [3],
)
```

**Correct:**
```typst
#table(
  columns: 3,
  table.header([A], [B], [C]),
  [1], [2], [3],
)
```

## 10. Font Warnings are Non-Fatal

Typst emits `warning: unknown font family` when a specified font is not installed, but the document still compiles. The fallback font (typically "Linux Libertine" or "New Computer Modern") is used automatically. These warnings are cosmetic, not errors.

**Verification:** After compilation, check the exit code. If it is 0, the PDF is valid regardless of font warnings.
