# 0007. Tagged claims for multi-dimension fields (Five Forces, VRIO)

- **Status:** Accepted
- **Date:** 2026-10-21

## Context
Two checklist fields feed frameworks with multiple required sub-items: `market.forces` must populate all five Five Forces dimensions (buyer power, supplier power, substitutes, entry barriers, rivalry); `resources.advantages` must populate one VRIO entry per distinct resource (brand, IP, network effects, data, switching costs, ...). `report_json` needs `claim_ids` scoped to each sub-item (one force, one resource), not to the whole field.

## Options considered
1. **Split into per-dimension fields** (`market.forces.buyer_power`, `market.forces.rivalry`, ..., one field per resource type) — gives each sub-item its own cache entry and freshness clock, but multiplies the research fan-out and LLM/search cost for this slot by ~5x, and most sources (industry reports, 10-Ks) discuss several forces or resources in the same page anyway.
2. **One field per framework, untagged claims** — cheap to research, but the report builder has no way to attribute a claim to a specific force or resource; Five Forces and VRIO sections would have to share one undifferentiated claim pool per company, losing per-dimension citations.
3. **One field per framework, tagged claims** — single research pass and cache entry; the extraction step tags each claim's `value` with which sub-dimension it supports.

## Decision
Option 3. `market.forces` and `resources.advantages` stay single fields in `field-checklist.md` (one researcher task, one freshness window each). Claim extraction for these fields must set a tag in `claims.value`:
- `market.forces` claims: `value.force` ∈ `buyer_power | supplier_power | substitutes | entry_barriers | rivalry`
- `resources.advantages` claims: `value.resource` — a short resource name (e.g. `"network effects"`), stable enough to group claims about the same resource across sources

The report builder groups claims by this tag when building `FiveForces.forces[].claim_ids` and each `VRIOResource.claim_ids` (see `design.md` §4 "Report schema").

## Consequences
- One research/cache/freshness unit per field, matching the cost model in `design.md` §1-2 — no fan-out multiplier for this slot.
- The verifier and extractor prompts for these two fields must instruct the model to emit the tag; an untagged claim can't be placed in its framework section (becomes a gap, per `field-checklist.md` rule 1).
- Rivalry has no dedicated checklist row (it's assessed from the competitor set as a whole, not one company's claims); the extractor may tag rivalry claims only when a source explicitly discusses competitive intensity.
- If a framework later needs independent freshness per dimension (e.g. entry barriers change far less often than rivalry), revisit and split at that time.
