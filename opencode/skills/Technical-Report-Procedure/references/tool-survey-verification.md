# Tool Survey Verification

When surveying software tools, libraries, or platforms for a report, license and feature claims must be explicitly cross-checked against primary sources before final delivery. This is not optional — errors in licensing propagate to procurement, compliance, and deployment decisions.

## Verification Checklist

For every tool mentioned in a survey report, verify at least:

1. **License** — Go to the GitHub repo's `LICENSE` file or the vendor's pricing page. Do not rely on memory or secondary sources.
2. **Open-source status** — Is it "open source" or "source available"? Is there a community build vs. a commercial build?
3. **Current version** — Check the latest release tag on GitHub or the vendor's changelog. Stating an outdated version undermines credibility.
4. **API/programmatic access** — Does the tool have a CLI, Python bindings, REST API, or is it GUI-only?

## Common License Pitfalls

| Claim | Reality | Impact |
|-------|---------|--------|
| "Open source" | Source available, but proprietary license | Cannot embed in open-source agentic pipelines |
| "Free" | Free for academic use, paid for commercial | Enterprise deployment requires paid license |
| "BSD" (from memory) | Actually GPL or LGPL | Copyleft requirements infect downstream code |
| "Open-source build exists" | Maintained by community, not vendor | Stability and feature parity with commercial version unknown |

## Verification Workflow

1. After writing the initial draft, scan every tool entry for a license or version claim.
2. For each claim, search: `<tool name> license GitHub` or visit `github.com/<org>/<repo>/blob/main/LICENSE` directly.
3. Correct any discrepancies in the report before compilation.
4. If a tool has a dual license (e.g., PyMOL: commercial Schrodinger vs. community open-source build), note both explicitly.

## Session Example: Bioinformatics Tool Survey

Initial draft stated:
- Open Babel: "Open-source" → Corrected to "GPL" after checking `github.com/openbabel/openbabel/blob/master/COPYING`
- CDK: "Open-source" → Corrected to "LGPL" after checking `github.com/cdk/cdk`
- PyMOL: "Open-source" → Corrected to "proprietary (Schrodinger), with community open-source build available" after checking current licensing
- RDKit: "BSD" → Verified via `github.com/rdkit/rdkit/blob/master/license.txt`
- AutoDock Vina: "Apache 2.0" → Verified via `github.com/ccsb-scripps/AutoDock-Vina`

These corrections prevented compliance errors in the final deliverable.
