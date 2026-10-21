---
name: run-evals
description: Run and interpret the Fieldbrief eval harness (golden set of companies) to measure accuracy, citation validity, cost, and latency against the NFRs. Use after changing prompts, models, agent nodes, or the fetch layer, or when asked how good the reports are.
---

# Run evals

The eval harness lives in `evals/` (built in Stage 5; until then, say it doesn't exist yet and offer to scope it).

## Metrics (map to NFRs in docs/design.md)
| Metric | Definition | Target |
|---|---|---|
| Claim precision | % of claims supported by their cited snippet | >= 90% |
| Citation validity | % of citations that resolve and contain the claim | >= 95% |
| Entity correctness | % of runs that analyzed the right company | >= 99% |
| Field coverage | % of required fields filled for the tier | track |
| Cost per report | $ from gateway logs, by tier | <= $0.50 standard |
| Latency | submit -> report, p50 / p95 | p95 <= 5 min |

## Steps
1. Decide scope: full golden set, or a subset relevant to the change (cheaper — say which and why).
2. Estimate cost before running (companies x tier x ~$/report) and tell the user. Ask before any run over ~$2.
3. Run the harness; it writes a results file under `evals/results/` with the git SHA.
4. Compare with the last committed results. Report a short table: metric, before, after, target, pass/fail.
5. For any regression: show 2–3 concrete failing examples (claim, snippet, why it fails) and the likely cause.
6. Commit the results file when the change is merged, so the README table stays honest.

Never tune prompts against the golden set until they pass while ignoring other cases — note overfitting risk if the set is small.
