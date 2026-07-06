# Markdown-to-Typst Syntax Map — Common Conversion Errors

## When porting Markdown content to Typst

Typst markup mode (`[...]`) looks like Markdown but is NOT Markdown. The `#` character is the function-call prefix. These are the most common errors when pasting Markdown into Typst.

## The Big Errors

### 1. `#` at start of line inside content brackets → code mode

**Markdown source:**
```markdown
# Title
## Section
```

**Broken in Typst:**
```typst
[ # Title ]     // ERROR: expected expression
[ ## Section ]  // ERROR: the character # is not valid in code
```

**Correct:**
```typst
= Title
== Section
```

### 2. `##` and `###` headings anywhere → invalid # in code mode

If `##` appears inside a content block, Typst tries to parse it as code.

**Broken:**
```typst
#text(size: 14pt)[
  ## Subsection      // ERROR: the character # is not valid in code
]
```

**Correct:**
Inside content blocks, use styled text instead of heading syntax:

```typst
#text(size: 22pt, weight: "bold")[Subsection Title]
```

### 3. `**bold**` inside content → empty pairs (no-op)

**Broken:**
```typst
#text(size: 14pt)[ **bold text** ]   // Compiles but produces no bold
```

**Correct:**
```typst
#text(size: 14pt)[ *bold text* ]     // emphasis syntax inside content
```

Or explicitly:
```typst
#text(size: 14pt)[ #strong[bold text] ]
```

### 4. `<` in text → unclosed label

**Broken:**
```typst
#text(size: 14pt)[ less than 1MB ]   // ERROR: unclosed label
```

**Correct:**
Replace `<` with words in content:
```typst
#text(size: 14pt)[ less than 1MB ]
```

Or escape: `\<`

### 5. `#sym.arrow.down` → unknown symbol modifier

**Broken:**
```typst
#sym.arrow.down    // ERROR: unknown symbol modifier
```

**Correct:**
Use `#sym.arrow.t` or plain text with slashes:
```typst
`VFS` \\
/ \ \\
`Page Cache`
```

## Conversion Checklist

Before compiling a Typst file that was converted from Markdown:

- [ ] No `#` at start of lines inside `[...]` brackets (except Typst function calls)
- [ ] No `##` or `###` headings (replace with `=`, `==`, `===` at document level only)
- [ ] No `**bold**` in content (replace with `*bold*` or `#strong[...]`)
- [ ] No `<` in text content (replace with words or escape)
- [ ] No `#sym.arrow.down` (use `#sym.arrow.t` or plain text)
- [ ] No `#text(weight: "bold")[...]` as table cell content (use `*[Header]*`)
