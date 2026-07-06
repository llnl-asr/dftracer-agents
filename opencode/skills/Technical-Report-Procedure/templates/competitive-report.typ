// Competitive Intelligence Report Template
// Copy this file, replace placeholders, and compile.
// Usage: typst compile /workspace/reports/my-report.typ /workspace/reports/my-report.pdf

#set page(paper: "a4", margin: (top: 2cm, bottom: 2cm, left: 2.2cm, right: 2.2cm))
#set text(font: "DejaVu Serif", size: 11pt)
#set heading(numbering: "1.")
#show heading.where(level: 1): it => [
  #v(12pt)
  #text(font: "DejaVu Sans", size: 18pt, weight: "bold", fill: rgb("#2c3e50"))[#it.body]
  #v(4pt)
  #line(length: 100%, stroke: 1.5pt + rgb("#d35400"))
  #v(8pt)
]
#show heading.where(level: 2): it => [
  #v(8pt)
  #text(font: "DejaVu Sans", size: 13pt, weight: "bold", fill: rgb("#34495e"))[#it.body]
  #v(4pt)
]

#let accent = rgb("#d35400")
#let dark = rgb("#2c3e50")

// ===================== PLACEHOLDERS =====================
#let REPORT_TITLE = "COMPANY_NAME vs. INTERNAL_PROJECT — Competitive Analysis"
#let SUBTITLE = "Strategic Intelligence Report"
#let PREPARED_FOR = "Your Name"
#let DATE = "Month DD, YYYY"
#let TARGET_COMPANY = "CompanyName"
#let TARGET_URL = "https://www.company.com"
#let FOUNDED_YEAR = "20XX"
#let FOUNDERS = "CEO Name, CTO Name"
#let HQ = "City, State"
#let EMPLOYEES = "XX"
#let TOTAL_FUNDING = "$XXM"
#let VALUATION = "$XM"
#let CUSTOMERS = "XXX"

// ===================== TITLE PAGE =====================
#align(center + top)[
  #v(3cm)
  #text(font: "DejaVu Sans", size: 26pt, weight: "bold", fill: dark)[#REPORT_TITLE]
  #v(8pt)
  #text(font: "DejaVu Sans", size: 16pt, fill: accent)[#SUBTITLE]
  #v(1.5cm)
  #text(size: 11pt)[
    Prepared for #PREPARED_FOR #parbreak()
    #DATE #parbreak()
    Classification: Internal — Strategic Intelligence
  ]
  #v(2cm)
  #line(length: 60%, stroke: 1pt + accent)
]

#pagebreak()
#outline(depth: 2, indent: auto)
#pagebreak()

= Executive Summary

*#TARGET_COMPANY* (#TARGET_URL) is a [description]. Founded in #FOUNDED_YEAR by #FOUNDERS, the company has raised #TOTAL_FUNDING and is valued at #VALUATION.

Bottom line: #TARGET_COMPANY is [positioning]. [INTERNAL_PROJECT] is [positioning].

= Company Deep-Dive

== Overview
#table(columns: (auto, 1fr), inset: 6pt, stroke: (bottom: 0.5pt + gray), fill: (x, y) => if y == 0 { accent } else { white },
  [*Attribute*], [*Detail*],
  [Legal Name], [#TARGET_COMPANY],
  [Website], [#TARGET_URL],
  [Founded], [#FOUNDED_YEAR],
  [Founders], [#FOUNDERS],
  [Headquarters], [#HQ],
  [Employees], [#EMPLOYEES],
)

== Funding Timeline
#table(columns: (auto, auto, auto, auto, 1fr), inset: 6pt, stroke: (bottom: 0.5pt + gray), fill: (x, y) => if y == 0 { accent } else { white },
  [*Round*], [*Date*], [*Amount*], [*Valuation*], [*Lead*],
  [Seed], [Year], [Amount], [—], [Investors],
)

== Product Architecture
=== Monitor
#table(columns: (1fr, 2fr), inset: 6pt, stroke: (bottom: 0.5pt + gray), fill: (x, y) => if y == 0 { accent } else { white },
  [*Feature*], [*Description*],
  [Feature 1], [Description],
)

== Pricing
#table(columns: (1fr, 1fr, 2fr, 2fr), inset: 6pt, stroke: (bottom: 0.5pt + gray), fill: (x, y) => if y == 0 { accent } else { white },
  [*Tier*], [*Price*], [*Coverage*], [*Notes*],
  [Starter], [Price], [Coverage], [Notes],
)

== Competitive Moat
*Strengths:*
+ First-mover advantage
+ Capital moat

*Weaknesses:*
+ Pricing escalation
+ Platform dependence

= Head-to-Head Comparison
#table(columns: (1.5fr, 1.25fr, 1.25fr), inset: 5pt, stroke: (bottom: 0.5pt + gray),
  fill: (x, y) => if y == 0 { accent } else { if calc.rem(y, 2) == 0 { rgb("#f8f9fa") } else { white } },
  [*Dimension*], [*Target*], [*Internal*],
  [Cost], [Price], [Cost],
  [Coverage], [Scope], [Scope],
)

= Strategic Implications

== The Threat
[Describe threat.]

== The Opportunity
[Describe opportunities.]

== Technical Recommendations
#table(columns: (auto, 2fr, auto, auto), inset: 5pt, stroke: (bottom: 0.5pt + gray),
  fill: (x, y) => if y == 0 { accent } else { if calc.rem(y, 2) == 0 { rgb("#f8f9fa") } else { white } },
  [*Priority*], [*Initiative*], [*Impact*], [*Effort*],
  [P0], [Action], [High], [Medium],
)

= Conclusion

== Final Assessment
#table(columns: (1fr, auto), inset: 6pt, stroke: (bottom: 0.5pt + gray), fill: (x, y) => if y == 0 { accent } else { white },
  [*Criterion*], [*Score*],
  [Market Timing], [Stars],
  [Competitive Threat], [Stars],
)

#v(1cm)
#line(length: 100%, stroke: 0.5pt + gray)
#text(size: 9pt, fill: gray)[
  Report compiled from public sources. For internal strategic planning only.
]
