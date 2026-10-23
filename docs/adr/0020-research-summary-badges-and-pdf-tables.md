# 0020. Research summary, plain-English badge tooltips, PDF table continuation

- **Status:** Accepted
- **Date:** 2026-10-23

## Context
Three smaller presentation gaps from Matan's spec, grouped because they're all low-risk, deterministic polish rather than pipeline changes: (1) nothing in the report stated what research actually happened — which source types, and a plain account of what's missing — before the reader hits the detailed "Not found" list; (2) evidence/priority badges (`strong`, `supported`, `insufficient_evidence`, `high` priority, VRIO's `unexploited_advantage`, etc.) had no explanation anywhere on the page, and a float-looking confidence score sitting unexplained risks reading as more precise than it is; (3) the PDF's Five Forces/VRIO/Comparison tables had no `<thead>`, so a table spanning a page break lost its header on the next page.

## Decision
- `orchestrator.py` computes `meta.research_summary` deterministically (no LLM call) from data already in hand: distinct source types behind the report's claims (one DB query, counted by `Source.id` not by claim, so one source cited by five claims counts once) and a plain-English limitations list built from the target company's real gap reasons (`not_found`/`partial`/`budget_cutoff` counts — ADR 0017) and the real `competitor_coverage` outcome (ADR 0017). Rendered near the top of both HTML and PDF, right after the company introduction (ADR 0019) and before the detailed sections — readers see the methodology before the conclusions, and the conclusions before the itemized gaps.
- Every evidence-status and priority/implication badge in the HTML report now carries a `title` tooltip in plain, factual language ("Multiple independent sources agree on this", "Mentioned too briefly in the evidence to call a direction") — deliberately never phrased as a percentage or a certainty claim, matching Matan's "without implying mathematical certainty."
- PDF's Five Forces, BMC, VRIO, and Comparison tables gained `<thead>`/`<tbody>` plus `break-inside: avoid` on body rows, so Chromium's print renderer (Playwright/Chromium, not WeasyPrint — `reports/pdf.py`) repeats the header across a page break instead of losing it. The SWOT table (a fixed 2x2 header/body grid, not a long list) and the recommendation cards (already `page-break-inside: avoid` via `.card`) didn't need this.

## Consequences
- A reader gets an honest, source-grounded account of "what we actually looked at and what's still missing" before diving into frameworks — directly answers the "establish context first" and "explain sourcing briefly" asks.
- Badge tooltips are additive and HTML-only (print has no hover) — the PDF's existing plain-text labels remain its print-appropriate equivalent, not a regression.
- A long deep-tier VRIO or comparison table (many resources/competitors) now stays readable across a page break in the exported PDF.
- When we would revisit this: if `research_summary`'s limitations list grows noisy on reports with many partial fields, consider capping it with a "+N more, see below" instead of listing every count.
