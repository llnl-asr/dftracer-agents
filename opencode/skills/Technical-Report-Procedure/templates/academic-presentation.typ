// Academic Presentation Starter — typslides-based research deck template
// Copy, replace placeholders, compile with native CLI for best diagnostics.
//
// Usage:
//   typst compile /workspace/my-talk.typ /workspace/my-talk.pdf

#import "@preview/typslides:1.3.3": *

#show: typslides.with(
  ratio: "16-9",
  theme: "dusky",
  font: "Fira Sans",
  font-size: 20pt,
  link-style: "color",
  show-progress: true,
)

// ========== SLIDE 1: TITLE ==========
#front-slide(
  title: "TITLE: Concept or System",
  subtitle: "One-line framing of the core idea",
  authors: "Author Name",
  info: "Affiliation",
)

// ========== SLIDE 2: THE PROBLEM ==========
#slide(title: "The Problem")[
  - Current tools report #stress[how much], not #stress[why]
  - Profilers give data; neither explains #stress[causality]
  - A 30-minute job with a 5-minute stall: operators see "5 min" with zero explanation

  #v(1em)
  #align(center)[
    #text(style: "italic")[
      "If you can't explain it, you can't optimize it."
    ]
  ]
]

// ========== SLIDE 3: HYPOTHESIS ==========
#slide(title: "Hypothesis: H1")[
  #stress[A multi-layer model can explain any anomaly as a causal chain.]

  #v(1em)
  #framed(title: "Layers")[
    #table(
      columns: (1fr, 1fr, 1fr),
      inset: 10pt,
      stroke: none,
      text(weight: "bold")[Layer],
      text(weight: "bold")[Probe],
      text(weight: "bold")[Causal Role],
      [User], [Wrapper], [Application intent],
      [Kernel], [eBPF probe], [OS behavior],
      [Storage], [NVMe trace], [Device response],
    )
  ]
]

// ========== SLIDE 4: WHY CURRENT TOOLS FAIL ==========
#slide(title: "Why Current Tools Fail")[
  #table(
    columns: (auto, 1fr, 1fr),
    inset: 8pt,
    stroke: 0.5pt + luma(80%),
    text(weight: "bold")[#stress[Tool]],
    text(weight: "bold")[#stress[Measures]],
    text(weight: "bold")[#stress[Blind Spot]],
    [Tool A], [Aggregated counters], [No temporal causality],
    [Tool B], [eBPF traces], [No cross-layer attribution],
    [Tool C], [System-wide streams], [No per-call context],
    [Tool D], [PMC aggregates], [No file-level granularity],
  )
]

// ========== SLIDE 5: SECTION DIVIDER ==========
#title-slide[
  Research Directions
]

// ========== SLIDE 6: PHASE 1 ==========
#slide(title: "Phase 1: Infrastructure")[
  - Deploy multi-layer probes: POSIX, VFS, block, NVMe
  - Maintain per-call context via eBPF maps
  - Emit correlated events with nanosecond timestamps
  - Handle collective vs. independent semantics
  - Target <2% overhead
]

// ========== SLIDE 7: PHASE 2 ==========
#slide(title: "Phase 2: Causal Model")[
  - Build latency-decomposition graph: user → kernel → device
  - Identify root cause via longest-path / anomaly detection
  - Map kernel states to symbolic labels
  - Validate on known anomalies

  #v(1em)
  #align(center)[
    #stress[Open Question:] Does the model transfer across filesystems and interconnects?
  ]
]

// ========== SLIDE 8: WHERE WE ARE NOW ==========
#slide(title: "Where We Are Now")[
  #framed(title: "Current Status")[
    #table(
      columns: (1fr, 1fr, 1fr, 1fr),
      inset: 8pt,
      stroke: none,
      text(weight: "bold")[Phase],
      text(weight: "bold")[Status],
      text(weight: "bold")[Artifacts],
      text(weight: "bold")[Blockers],
      [Attribution Infra], [#greeny[In Progress]], [eBPF probes], [Verifier limits],
      [Causal Model], [#yelly[Not Started]], [—], [Need ground-truth labels],
      [Explanation UI], [Not Started], [—], [Design space open],
      [Closed Loop], [Not Started], [—], [Policy / adoption],
    )
  ]
]

// ========== SLIDE 9: KEY TAKEAWAY ==========
#focus-slide[
  #text(size: 1.2em, weight: "bold")[
    Current tools measure.
  ]

  #v(1em)
  #stress[#text(size: 1.3em)[We explain.]]

  #v(1em)
  And explanation is the prerequisite for optimization.
]

// ========== SLIDE 10: REFERENCES ==========
#slide(title: "References")[
  #set text(size: 16pt)
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author et al. Title. Venue, Year. #link("https://doi.org/...")
  + Author. Book Title. Publisher, Year.
]

// End of template
