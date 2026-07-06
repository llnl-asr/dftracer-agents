# Research Talk Deck Style

## History
Derived from a multi-revision session building a research talk deck. The deck went through 4+ iterations before converging on the rules below. Each iteration corrected a specific anti-pattern: too long, too much detail (project phases), tables making slides look empty, and naming conflicts with existing projects.

## Rules

### 1. Tight: 5–7 slides absolute max
For a concept talk — the kind you give at a conference, lab meeting, or reading group — 5 slides is generous. 7 is the ceiling. More than 7 and the audience tunes out.

**Before (bad):** 12 slides with Phase 1–4, references, "Where We Are Now" status table.
**After (good):** 7 slides: title, problem, concept, why-tools-fail, the gap, the core argument, takeaway.

### 2. No tables on content slides
Tables waste horizontal space and leave slides looking sparse. Convert table content to plain bullets with `#stress` emphasis.

**Before (bad):**
2-column table with 4 rows: Tool | Fundamental Blind Spot.
Result: tiny text, empty margins, slide feels unfinished.

**After (good):**
4 bullets, each with the tool in `#stress` and the blind spot after a dash. Same information, more readable.

### 3. 3–5 bullets per slide. ~40 words max per slide body.
One line per bullet. No nested sub-bullets. If a slide needs more, it's two slides.

### 4. Concept talk ≠ project proposal
Argue the idea. Do NOT include:
- Phase timelines (Phase 1, Phase 2...)
- Facility names ("at Polaris...")
- Deliverables or status tables
- "Where We Are Now" with Done/In-Progress/Blocked columns
- Risk matrices or Gantt charts

A concept talk answers: *What is the problem? What does existing work miss? What would the answer look like?*

### 4.5. No named citations of your own works
Do not name-drop your own papers, tools, or project names in the talk slides. Keep claims generic ("ML-driven optimizers," "existing profilers") even when the specific work is yours. The audience knows you wrote them; the slide is about the idea, not the bibliography.

### 5. No AI-speak or canned taglines
Avoid phrases that sound LLM-generated: "delve into", "holistic approach", "leveraging cutting-edge", "it is important to note". If a tagline feels like it could be on a stock photo, delete it.

**Case study:** The tagline "Correlation predicts. Causation explains." appeared on multiple slides. The user said it's "an X statement" (implying overused/generic). The core idea is valid, but the phrasing needs to be fresh and specific to the argument.
Before committing to an acronym or paper title, check that it doesn't already exist.

**Case study:** The deck started as "FOO: Explainable I/O in HPC." But FOO was already a project by another group (2025). The user then said "it's BAR as in why IO" — the why/causation framing, not explainable. Lesson: always do a quick existence check for acronyms in the target paper's domain.

**How to check:** Google Scholar, arXiv search, conference proceedings for the target venue. Ask the user if they know of an existing project with that name.

### 6. No filler slides
No "Agenda". No "Thank You". No "Questions?" No references slide unless the user explicitly asks for one.

### 7. Focus slide for the takeaway
End with `#focus-slide` (typslides) or `align(center + horizon)` (raw Typst) carrying one centered punchline. Not a wall of text.

**Example (good):**
```
BAR: the question every optimizer should be able to answer.
Not just what to tune.
Not just how much was lost.
But why the loss happened.
```

## Before vs. After Summary

| Dimension | Before (bad) | After (good) |
|-----------|-------------|--------------|
| Slide count | 12 | 7 |
| Tables | 3 (hypothesis, tools, status) | 0 |
| Project language | phases, facilities, deliverables | none |
| Subtitle | "Explainable I/O" | causation framing |
| Name | FOO (already taken) | BAR (why I/O) |
| Closing | references, risks | one punchline |

## When These Rules Apply
- Conference talks
- Research group presentations
- Reading group slides
- Conceptual position papers (as slides)

## When These Rules Do NOT Apply
- Full paper walkthroughs (those need detail)
- Dissertation proposals (status tables are expected)
- Technical tutorials (step-by-step tables are fine)
- Progress reports to a PI (project language is expected)
