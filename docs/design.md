# Fieldbrief — Design

> AI-powered competitive analysis, built on real strategy frameworks.

Fieldbrief turns a company name into a cited strategy report in minutes. AI agents research the company and its competitors, then apply proven frameworks to derive a SWOT and recommendations.

**Status:** Stage 0 (design). Sections marked **DRAFT** or listed in `open-questions.md` are not final.

---

## 1. Requirements

### Functional
- Analyze company -> cited, framework-based report
- Confirm / edit company and competitors
- Track analysis progress live
- Export & revisit reports (PDF / JSON)

**Out of scope:** private data sourcing, chat with report, customize report sections.
**Later (v2):** refine a report with added context, re-run and diff over time, notifications when the tab is closed, Hebrew output.

### Non-functional
| Quality | Target | Measured by |
|---|---|---|
| Accuracy | >= 90% of claims supported by cited sources | Eval harness |
| Cost | <= $0.50 per standard report | Gateway cost log |
| Latency | <= 200 ms acknowledgment, progress every <= 2 s, first results <= 60 s, full report <= 5 min (p95) | Traces |
| Reliability | >= 99% jobs complete (>= 95% fully) | Job status |
| Scalability | Horizontally scalable, modular components | Design |
| Observability | 100% of runs traced with cost/latency breakdown | Langfuse |

### Effort tiers
| Tier | Competitors | Approx. cost | Approx. time | Notes |
|---|---|---|---|---|
| Quick | 2 | ~$0.10 | ~1 min | Cheap models, cache-first |
| Standard | 4 | ~$0.50 | ~4 min | The full report |
| Deep | 6 | ~$2 | ~10 min | More sources, strong model in more stages |

A cached research set satisfies any request at or below its tier; higher tiers research only the delta.

---

## 2. Back of the envelope (future scaling)

*Theoretical sizing. Current deployment: single user.*

**Assumptions:** 1M DAU · 3 reports/user/day · peak 3x average · tier mix 60/35/5 (quick/standard/deep) · job duration 1/4/10 min · companies per report 3/5/7 · standard report ~250K tokens, ~30 searches, ~50 KB stored · ~50K hot companies, cached <= 30 days, user-triggered refresh.

| | Calculation | Result |
|---|---|---|
| Traffic | 1M x 3 = 3M/day; / 86,400 | ~35/s avg, ~100/s peak |
| Concurrency | avg job 0.6x1 + 0.35x4 + 0.05x10 = 2.5 min; 100/s x 150 s | ~15K concurrent jobs |
| LLM tokens | avg ~170K/report (50K/250K/1M) x 100/s | ~17M tokens/s peak |
| Search | avg ~20/report (10/30/80) x 100/s | ~2K searches/s peak |
| Cost | avg ~$0.34/report ($0.10/$0.50/$2) | ~$1M/day, ~$30M/month before cache |
| Storage | 3M x 50 KB/day | ~150 GB/day, ~4.5 TB/month |
| Cache | 50K x 750 KB | ~40 GB hot research set (Postgres; in-memory cache layer at scale) |

**Key numbers:** the bottleneck is LLM and search capacity, not compute -> queue + cache + rate limiting. Unit cost ~$30/user/month before cache, so the shared research cache is the main economic lever.

---

## 3. Core entities
- **User**
- **Company** — resolved identity (name, aliases, domain, ticker, CIK)
- **Report** — target, competitors, tier, status, `report_json`; immutable, versioned by refresh
- **Claim** — one fact about one company from one source, with relevance and verification
- **Source** — a fetched document, global and deduped by URL

**Relationships**
- User 1—* Report
- Report -> target Company + competitor Companies
- Report -> Claims (evidence used, frozen per report)
- Claim -> Company + Source
- Company *—* Company (competitor graph)

**Findings** (SWOT points, recommendations) live inside `report_json`, each with `claim_ids`. Promote to a table only if cross-report queries are needed.

### Confidence model
`confidence = f(source_reliability, relevance, verified, corroboration)` — derived, never stored.
- **Source reliability**: from `source_type` (filing/official = 1.0, news = 0.8, blog = 0.5).
- **Relevance**: is the source *about* this company, or a passing mention — `claims.relevance`, set at extraction.
- **Verified**: does the snippet actually support the claim — ×1.0 if verified, ×0.5 if not (unverified isn't discarded, just discounted; "unknown is acceptable" doesn't mean "unverified is worthless").
- **Corroboration**: number of independent sources (distinct `source_id`) agreeing on the same field — each one beyond the first adds +0.05, capped so the total never exceeds 1.0.

Concretely: `score = min(1.0, max(reliability·relevance·verified_mult over claims) + 0.05·(corroboration - 1))`. Implementation: `reports/confidence.py`.

---

## 4. Tables

```
USERS
  id                 uuid PK
  email              string UK
  created_at         timestamp

COMPANIES
  id                 uuid PK
  name               string
  aliases            text[]
  domain             string UK
  ticker             string
  cik                string
  industry           string

COMPANY_COMPETITORS
  company_id         uuid PK, FK -> companies
  competitor_id      uuid PK, FK -> companies
  relevance          float
  origin             enum (discovered, user)
  updated_at         timestamp

SOURCES
  id                 uuid PK
  url                string UK
  title              string
  source_type        enum (filing, official, news, blog)
  published_at       timestamp
  fetched_at         timestamp
  content_hash       string

CLAIMS
  id                 uuid PK
  company_id         uuid FK -> companies
  source_id          uuid FK -> sources
  field              string
  statement          text
  value              jsonb
  snippet            text
  relevance          float
  verified           bool
  extracted_at       timestamp

REPORTS
  id                 uuid PK
  user_id            uuid FK -> users
  target_company_id  uuid FK -> companies
  parent_report_id   uuid FK -> reports
  tier               enum (quick, standard, deep)
  status             enum (queued, running, done, partial, failed)
  report_json        jsonb
  cost_usd           numeric
  created_at         timestamp
  completed_at       timestamp

REPORT_COMPETITORS
  report_id          uuid PK, FK -> reports
  company_id         uuid PK, FK -> companies

REPORT_CLAIMS
  report_id          uuid PK, FK -> reports
  claim_id           uuid PK, FK -> claims
```

**Key indexes**
- `claims (company_id, field, extracted_at DESC)` — the cache lookup, hottest query
- `sources (url)` unique — dedup on fetch
- `reports (user_id, created_at DESC)` — history
- `reports (target_company_id, created_at DESC)` — latest report for a company

**Invariants:** claims append-only · reports immutable · scores derived · raw page content not stored (snippet is the evidence, `content_hash` detects change).

---

## 5. Report schema

`report_json` (stored on `REPORTS.report_json`) is the frozen output of one report build. Every section is a Pydantic v2 model; the API and UI read this directly, never the DB tables.

**Claim tagging convention** (ADR 0007): `market.forces` and `resources.advantages` are single checklist fields (one research/cache/freshness unit each), but every claim extracted for them carries a tag in `claims.value` — `value.force` or `value.resource` — identifying which Five Forces dimension or which VRIO resource it supports. The report builder groups claims by that tag to fill `ForceAssessment.claim_ids` and `VRIOResource.claim_ids` below. An untagged claim for these two fields can't be placed and becomes a gap.

```python
from datetime import date, datetime
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class Tier(StrEnum):  # mirrors REPORTS.tier
    quick = "quick"
    standard = "standard"
    deep = "deep"


class Freshness(BaseModel):
    """'Data as of' + staleness for one section, per field-checklist.md freshness rules."""

    as_of: date
    stale: bool


class Confidence(BaseModel):
    """Derived at build time (design.md §3), frozen into the report — never recomputed after."""

    score: float = Field(ge=0, le=1)
    corroboration: int  # independent sources agreeing
    verified: bool


class Citation(BaseModel):
    """One field's answer for one company: the claims behind it plus their rolled-up confidence."""

    claim_ids: list[UUID]
    confidence: Confidence


class CompanySnapshot(BaseModel):
    """profile.* / offering.* / financials.* for one company. recent_events is target-only (field-checklist.md)."""

    company_id: UUID
    name: str
    is_target: bool
    description: (
        Citation | None
    )  # None is legitimate — even basic profile fields can be a clean gap
    hq: str | None
    founded: int | None
    employees: Citation | None
    products: Citation | None
    customer_segments: Citation | None
    revenue_model: Citation | None
    revenue: Citation | None
    profitability: Citation | None
    funding_or_cap: Citation | None
    recent_events: Citation | None
    freshness: Freshness


class ForceAssessment(BaseModel):
    """One Five Forces dimension. claim_ids come from claims tagged value.force == force (ADR 0007)."""

    force: Literal["buyer_power", "supplier_power", "substitutes", "entry_barriers", "rivalry"]
    rating: Literal["low", "medium", "high"]
    rationale: str
    claim_ids: list[UUID]


class FiveForces(BaseModel):
    forces: list[
        ForceAssessment
    ] = []  # up to 5; empty until Standard tier researches market.forces
    freshness: Freshness | None = None


class BMCBlock(BaseModel):
    block: Literal[
        "segments",
        "value_propositions",
        "channels",
        "customer_relationships",
        "revenue_streams",
        "key_resources",
        "key_activities",
        "key_partners",
        "cost_structure",
    ]
    statement: str
    claim_ids: list[UUID]


class BusinessModelCanvas(BaseModel):
    blocks: list[BMCBlock] = []  # up to 9, one per Literal value; omitted when no claims support it
    freshness: Freshness | None = None


class VRIOResource(BaseModel):
    """One resource/advantage. claim_ids come from claims tagged value.resource == resource (ADR 0007)."""

    resource: str
    valuable: bool
    rare: bool
    inimitable: bool
    organized: bool
    competitive_implication: Literal[
        "competitive_disadvantage", "parity", "temporary_advantage", "sustained_advantage"
    ]
    rationale: str
    claim_ids: list[UUID]


class PositioningAxis(BaseModel):
    name: str
    low_label: str
    high_label: str
    rationale: str


class PositionedCompany(BaseModel):
    company_id: UUID
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)
    claim_ids: list[UUID]


class PositioningMap(BaseModel):
    x_axis: PositioningAxis | None = (
        None  # None until positioning is computed (needs >=1 competitor)
    )
    y_axis: PositioningAxis | None = None
    companies: list[PositionedCompany] = []  # target + competitors


class SWOTItem(BaseModel):
    statement: str
    derived_from: Literal[
        "five_forces", "vrio", "events", "bmc", "market_size", "regulation", "other"
    ]
    claim_ids: list[UUID]


class SWOT(BaseModel):
    strengths: list[SWOTItem]
    weaknesses: list[SWOTItem]
    opportunities: list[SWOTItem]
    threats: list[SWOTItem]


class Recommendation(BaseModel):
    statement: str
    rationale: str
    priority: Literal["high", "medium", "low"]
    swot_refs: list[int] = Field(
        description="indices into the SWOT list(s) this recommendation addresses"
    )
    claim_ids: list[UUID]


class CoverageGap(BaseModel):
    """An explicit 'not found' or budget cutoff (ADR 0010), per field-checklist.md
    rule 1 — never a silently missing section."""

    company_id: UUID
    field: str
    attempts: int
    reason: Literal["not_found", "budget_cutoff"] = "not_found"


class ReportMeta(BaseModel):
    target_company_id: UUID
    competitor_ids: list[UUID]
    tier: Tier
    generated_at: datetime
    data_as_of: date  # oldest `as_of` across all section Freshness values


class ReportJSON(BaseModel):
    meta: ReportMeta
    companies: list[CompanySnapshot]  # target first, then competitors
    five_forces: FiveForces
    bmc: BusinessModelCanvas
    vrio: list[VRIOResource]
    positioning: PositioningMap
    swot: SWOT
    recommendations: list[Recommendation]
    gaps: list[CoverageGap]
```

**Notes**
- `Citation.confidence` and `CoverageGap` together mean every field in the report is either cited, marked low-confidence, or an explicit gap — never silently absent (hard constraint 5).
- `swot_refs` links recommendations back to SWOT items without duplicating `claim_ids`; a recommendation's own evidence trail is still its direct `claim_ids`.
- Quick-tier reports simply have thinner `companies` and omit `vrio`/`market.size_growth`-derived content for fields outside their tier (field-checklist.md); the schema does not special-case tier, callers just see fewer populated fields and more `gaps`.
- Framework sections (`five_forces`, `bmc`, `vrio`, `positioning`) default to empty/`None` rather than being required — a stage 3 (quick-tier, no competitors) report only populates `companies` and `swot`; stage 4 fills the rest. One schema, every tier and stage — never a separate "thin" schema that gets thrown away later.
- This schema is the contract for stage 4 (frameworks + synthesis) and stage 7 (report rendering) — implementation lives in `reports/` per the planned layout (§13). Real implementation: `src/fieldbrief/reports/schema.py`.

---

## 6. Framework method

How each framework in §5 gets filled from claims, and how findings roll up into SWOT (ADR 0009).

**Five Forces.** One `ForceAssessment` per force, from claims tagged `value.force` (ADR 0007). `rating` is the force's *pressure intensity* on the company — `high` is bad (strong competitive pressure), `low` is good. A force with zero tagged claims is omitted, not guessed.

**Business Model Canvas.** One `BMCBlock` per populated block. 4 of the 9 blocks map directly to a checklist field (`segments` <- `customers.segments`, `value_propositions` <- `offering.value_prop`, `revenue_streams` <- `revenue.model`, `key_partners` <- `partners.key`); the rest (`channels`, `key_resources`, `customer_relationships`, `key_activities`, `cost_structure`) are filled from any claim the extractor tags `value.bmc_block`, regardless of which field produced it — extends the ADR 0007 convention beyond the two fields it introduced. A block with no tagged claims is omitted.

**VRIO.** One `VRIOResource` per distinct `value.resource` tag (ADR 0007). `competitive_implication` is a straight decision table on the three resource properties, `organized` is reported as its own field but does not change the implication:
| valuable | rare | inimitable | implication |
|---|---|---|---|
| no | — | — | `competitive_disadvantage` |
| yes | no | — | `parity` |
| yes | yes | no | `temporary_advantage` |
| yes | yes | yes | `sustained_advantage` |

An `organized: false` sustained-advantage resource is a strong candidate for a recommendation ("you have it, you're not capturing it") — that's carried in `rationale`, not in the enum.

**Positioning.** MVP fixes two axes for every report: **Price** (low-cost <-> premium, from `pricing.model` + `financials.*`) and **Differentiation breadth** (niche <-> broad, from `offering.products` + `customers.segments` + `offering.value_prop`). Per-industry or per-report axis choice is deferred (`open-questions.md` "Later").

**SWOT roll-up.**
| Source | Strength | Weakness | Opportunity | Threat |
|---|---|---|---|---|
| Five Forces | — | — | `rating: low` | `rating: high` |
| VRIO | `temporary_advantage`, `sustained_advantage` | `competitive_disadvantage` | — | — |
| `events.recent`, `market.size_growth`, `environment.regulation` | — | — | by model judgment | by model judgment |

`rating: medium` and VRIO `parity` are omitted from SWOT (still visible in their own section) unless the synthesis model has explicit evidence to the contrary. BMC occasionally seeds a finding (`derived_from: "bmc"`) but isn't a primary SWOT input.

**Recommendations.** 3-7 per report. Every recommendation cites its own `claim_ids` (not just inherited through `swot_refs`) and must reference at least one SWOT item — a recommendation with no `swot_refs` is a synthesis bug.

---

## 7. Agent graph

One LangGraph per report (ADR 0001); nodes, fan-out, and stop conditions are fixed by ADR 0010.

**Nodes:** `plan` -> `research` -> `verify` -> `gap_check` (loops back to `plan` or advances) -> `analyze` -> `synthesize` (join) -> `deliver`.

**Fan-out is two-level:** outer, over companies (target + competitors) — each company runs its own `plan -> research -> verify -> gap_check` loop in parallel and reaches `analyze` on its own schedule (pipelined, per §9 step 7); inner, over that company's checklist fields within `research`/`verify`. `synthesize` joins on every company's `analyze` finishing, since Five Forces and positioning need the full set.

**VRIO and BMC are target-only** — computed inside the target's `analyze` call; competitor `analyze` calls only build `CompanySnapshot` (comparison data, not a full breakdown).

**Stop conditions** (checked in `gap_check`, every loop capped per `CLAUDE.md`): retry rounds per tier (quick 1 / standard 2 / deep 3), 2 attempts per field before it's an explicit "not found," the gateway's cost budget, and the tier's time budget (§1 Effort tiers). A per-field cap just resolves that field as "not found"; a report-wide cap (cost/time) advances every company branch immediately, short-circuiting fields that hadn't reached their own attempt cap yet.

**Partial reports:** `REPORTS.status` is `done` when every gap is a clean "not found" (attempt cap reached), `partial` when a report-wide budget/time cap cut off a field before its own attempt cap, `failed` only for infrastructure errors with nothing to resume — missing data is always `partial`, never `failed` (hard constraint 5).

**Speculative pre-warm** (§9 step 2) runs `plan -> research -> verify` per company with no `report_id` — it only needs to warm the shared claims cache (ADR 0004); the later full run's `plan` finds the warmed claims already fresh.

**Implementation notes (stage 3/4):**
- The outer, per-company fan-out is plain `asyncio.gather` over independent LangGraph runs (`agents/graph.py`'s per-company subgraph: `plan -> research -> gap_check -> analyze`), not a second LangGraph layer — there's no conditional looping at that level, just parallel dispatch and a join, so a state machine adds nothing there. `verify` happens inside `research`, not as its own node, since every extracted claim is verified immediately after extraction.
- `analyze` builds `CompanySnapshot` deterministically (grouping already-tagged claims by field) rather than as an LLM call — reconciling existing claims into a `Citation` is data wrangling, not judgment, so a call here would only add cost with nothing to decide.
- `synthesize` is one consolidated strong-model call producing Five Forces, VRIO, positioning, the "other" SWOT sources (events/market_size/regulation/bmc), and recommendations together — not four separate calls — matching the single-synthesis-call cost estimate in ADR 0008.

---

## 8. API

```
POST   /companies/resolve            {query} -> Company[]
GET    /companies/{id}/competitors?limit=    -> Company[]
POST   /reports    {company_id, competitor_ids, tier} -> {report_id}
GET    /reports/{id}?format=json|pdf          -> Report
GET    /reports/{id}/events                   -> SSE progress stream
POST   /reports/{id}/refresh                  -> {report_id}
GET    /reports?cursor=&limit=                -> Report[]
DELETE /reports/{id}                          -> cancel / delete
```

- User identity comes from auth (API key in MVP), never the request body.
- `POST /reports` accepts an `Idempotency-Key` header.
- `POST /reports` only enqueues and returns — that is the <= 200 ms acknowledgment.

**Implementation notes (stage 6):**
- Built: `POST /companies/resolve`, `POST /reports`, `GET /reports/{id}`, `GET /reports/{id}/events` (SSE), `GET /reports/{id}/view` (HTML), `GET /reports/{id}/pdf`, `POST /reports/{id}/refresh` (stage 8). `GET /companies/{id}/competitors`, list, and `DELETE` remain deferred — need the UI (a list/delete affordance) to be worth building against.
- Refresh creates a new report linked via `parent_report_id`, reusing whichever `competitor_count` the original report actually used — not stored on `REPORTS` itself, so this is read back from how many rows the original wrote to `REPORT_COMPETITORS` (ADR 0004's cache-by-company-x-field already means the new report's `plan` node only re-researches what's gone stale).
- `POST /reports` takes `{company_id, tier, competitor_count}`, not `{company_id, competitor_ids, tier}` — competitor discovery (ADR 0012) runs inside the worker's call to `run_report`, not as a client-driven pre-step with an edit UI, since that UI doesn't exist yet (stage 7). `competitor_ids` returns once it does.
- SSE reads the progress exchange, but a worker finishing (mocked LLM calls are near-instant; so can a very short real report) before a client's queue is bound means that message is simply gone — AMQP topic exchanges don't replay to late subscribers. `GET /reports/{id}/events` binds its queue first, then falls back to the `Report` row's own status (checked immediately after bind, and again on every 5s of silence) so a client is guaranteed to reach a terminal state rather than hang on a "done" it missed. Found via the stage-6 integration test, which hit this race immediately with mocked (near-instant) work.
- The worker declares the progress exchange itself on every `emit()` (idempotent, cheap) rather than assuming some consumer declared it first — a publish-only call never creates an exchange, so a worker with zero SSE clients connected would otherwise 404 on its very first progress event.

---

## 9. Data flow

1. **Resolve** — query -> candidates (Wikidata, aliases) -> user confirms
2. **Speculative start** — research the target + likely competitors right away
3. **Competitors** — graph lookup, else discover -> user edits -> job queued
4. **Plan** — find stale/missing claims per company x field
5. **Research** — fan out per company x field; fetch (dedup by URL/hash) -> extract claims for all known companies on the page
6. **Validate** — verify against snippets, score confidence; retry gaps (capped)
7. **Analyze** — per company as soon as its research is done (pipelined)
8. **Synthesize** — SWOT + recommendations linked to claims (waits for all)
9. **Deliver** — save report + `report_claims` (done / partial), sections streamed as ready

Refresh starts at Plan. Cost + time budget checked at every LLM call.

### Client -> report, by component
```
1. Search      User types "Wix" -> nginx -> FastAPI /companies/resolve
               -> Wikidata + Postgres (aliases) -> candidates -> user confirms
2. Pre-warm    FastAPI publishes a speculative research job (RabbitMQ)
3. Competitors FastAPI reads the competitor graph (Postgres), else discovers
               (SearXNG + cheap LLM) -> user edits the list
4. Submit      POST /reports -> report row "queued" in Postgres
               -> job on RabbitMQ -> report_id back in <= 200 ms -> page opens SSE
5. Plan        FastStream worker starts LangGraph (checkpoints in Postgres)
               -> planner checks fresh claims per field -> gap list
6. Research    Researchers fan out per company x field:
               SearXNG / EDGAR -> httpx + trafilatura (Playwright if JS)
               -> source deduped -> cheap LLM extracts claims -> Postgres
7. Verify      Cheap LLM checks each claim against its snippet
               -> gaps back to the planner (within cost + time budget)
8. Build       Strong LLM runs the frameworks -> SWOT + recommendations
               -> report_json + report_claims saved -> done / partial
9. Progress    Worker -> RabbitMQ topic exchange -> FastAPI -> SSE -> HTMX updates page
10. Read       Jinja2 renders the report; PDF via Playwright on demand
```

### Caching and refresh
- Research is cached per **company x field**, not per report. A company researched as a competitor is reused when it becomes a target.
- Reports show "data as of"; the user triggers refresh. Hard max age 30 days.
- Field freshness: `events.*` ~7 days, `financials.*` until the next filing, everything else 30 days.
- No cron refresh in MVP (pays for reports nobody reopens).

---

## 10. Architecture

```
client -> API gateway (nginx: TLS, LB, rate limit) -> API service (FastAPI: resolve, reports, SSE, auth)
                                                          |                     |                ^
                                                          v                     v                | progress (SSE)
                                                      Postgres              RabbitMQ  -----------+
                                          (reports, claims, research cache,  (jobs queue,
                                                    checkpoints)              progress exchange)
                                                          ^                     |        ^
                                                          | results             | jobs   | progress
                                                          |                     v        |
                                                   Worker, one LangGraph per report (FastStream)
                                                   planner -> researchers xN -> verifier -> report builder
                                                      ^______ gaps -> retry (capped) ______|
                                                          |                              |
                                                   LLM gateway (LiteLLM)           Fetch layer
                                                   routing, budget, limits         dedup, clean, rate limit
                                                          |                              |
                                                   LLM providers                   SearXNG, EDGAR, Wikidata, web
```

### LLM gateway
The only path to any LLM. Callers pass `task`, `messages`, output `schema`, `report_id`.

**Model picks (ADR 0008):** cheap tier = Gemini 2.5 Flash (extraction, verification); strong tier = Gemini 2.5 Pro (synthesis only, never in a loop). One provider/API key for MVP; swapping either tier is a gateway config change. Estimated ~$0.39/standard report at the §2 token assumption — re-check live prices before go-live and replace the estimate with real cost-log data once the eval harness (stage 5) runs.

1. Route task + tier -> model (config)
2. Response cache check (deterministic tasks)
3. Budget: estimate, reserve from report budget; near limit -> downgrade or stop
4. Rate limit: token bucket per provider (in-process with one worker, Postgres-backed with several); wait, don't fail
5. Call via LiteLLM; retry 429/5xx with backoff + jitter
6. Validate into the Pydantic schema; one repair retry
7. Settle actual cost; log tokens / $ / latency with `report_id` + stage
8. Return typed object

### Fetch layer
Search (SearXNG), fetch (httpx; Playwright for JS pages), clean (trafilatura), dedup by URL and `content_hash`, per-domain rate limits, timeouts, robots.txt respected, EDGAR with a declared User-Agent.

### PDF
Same Jinja2 HTML as the web view + print stylesheet, rendered by Playwright. Charts as server-side SVG. Reports are immutable, so a rendered PDF never goes stale.

**Implementation notes (stage 7):** `reports/render.py` resolves every `claim_id` referenced anywhere in `report_json` to its actual statement + source URL with one batched query (the report only ever stores IDs — design.md §4/§5), then hands off to `reports/templates/report.html`. Positioning is an inline-CSS scatter (no chart library, no external assets — print-safe). `GET /reports/{id}/pdf` calls `reports/pdf.py` (Playwright) on the same HTML every time; nothing is cached, since an immutable report has nothing to invalidate. **Not verified in this environment:** Playwright's Chromium needs system shared libraries (`libnspr4.so` and others) that `playwright install --with-deps` can't install without root, which isn't available here — the PDF code is written and type-checked but unexecuted; verify on a machine with those deps (or once stage 8 containerizes the app).

### Progress UX
Stage enum (`RESOLVING -> DISCOVERING -> RESEARCHING -> VALIDATING -> ANALYZING -> SYNTHESIZING -> RENDERING`) + real findings ("Found 4 competitors", "Revenue: $412M") + an optional playful sub-line. Sections render as they complete.

**Implementation notes (stage 7):** `GET /reports/{id}/view` serves either the finished, fully-rendered report, or — while a report is still running — a small shell page whose embedded JS opens the same `/events` SSE stream and reloads once it reaches a terminal stage. Swapping individual sections in via HTMX as each framework completes (rather than a full reload at the end) is the natural next step, deferred for now: there's no browser available in this dev environment to verify that interaction visually, and a full reload still satisfies the "live progress, then the finished report" contract. Browser-native `EventSource` can't send custom headers at all, so the API key for `/events` also accepts a `?api_key=` query param (header stays the primary method for every other endpoint) — `/view`'s shell embeds whichever one it itself was loaded with.

---

## 11. Design decisions (summary — details in `docs/adr/`)
- **Orchestrated, not choreographed:** one LangGraph per report, stateless nodes, state checkpointed in Postgres. (ADR 0001)
- **Job queue, not Kafka:** long discrete jobs need per-job ack and retry. Kafka only if event fan-out is added. (ADR 0002)
- **RabbitMQ for jobs and progress, no Valkey:** durable queue, priorities, native pub/sub; research cache is Postgres. (ADR 0006)
- **Free / open-source stack** except the LLM key. (ADR 0003)
- **Shared, append-only research** (claims per company x field) as the cache. (ADR 0004)
- **Model tiering + per-report budgets** in a single gateway. (ADR 0005)
- **Tagged claims for Five Forces, VRIO and BMC** instead of per-dimension fields — one research pass, claims tagged by sub-dimension. (ADR 0007)
- **Gemini 2.5 Flash (cheap) + Pro (strong)** as the model picks, one provider for MVP. (ADR 0008)
- **Fixed SWOT roll-up rules and positioning axes** for MVP; per-industry axes deferred. (ADR 0009)
- **Two-level fan-out** (company, then field within it); VRIO/BMC target-only; pre-warm needs no `report_id`. (ADR 0010)
- **Competitor discovery is search-grounded**, not the model's own knowledge — every competitor traces back to a fetched page. (ADR 0012)
- **Nodes pass IDs, not data** — any stage can become a service later; extract only stages with different resource needs (fetch, PDF).
- **Latency:** cache hits, speculative start, pipelined stages, per-fetch timeouts, cost + time budgets, progressive rendering.
- **Security:** fetched content is untrusted; strict-schema extraction only.
- **Reliability:** resume from checkpoint after a worker crash; idempotency keys.

### Scale path (not in MVP)
RabbitMQ cluster or SQS for the queue · in-memory cache layer for hot research · Temporal for durable execution · shard by `company_id` · self-hosted extraction models · multiple LLM providers with negotiated capacity · pre-warm hot companies.

---

## 12. MVP stack

| Layer | Pick | Why |
|---|---|---|
| Gateway | nginx | TLS, LB, rate limits; streams SSE with buffering off |
| API | FastAPI | Async, typed, auto docs |
| UI | Jinja2 + HTMX | Real UI, no JS build |
| Agents | LangGraph + Postgres checkpointer | Fan-out, loops, resume |
| LLM access | LiteLLM -> Gemini 2.5 Flash (cheap) / Pro (strong) | Swap models by config; **only paid item** (ADR 0008) |
| DB | Postgres | All durable state |
| Queue + progress | RabbitMQ + FastStream | Durable per-job acks, priorities, pub/sub for progress |
| Search | SearXNG (self-hosted) | Free, no quota |
| Fetch / clean | httpx + trafilatura, Playwright | Clean text = fewer tokens |
| Financials | SEC EDGAR | Free, structured, reliable |
| Entity resolution | Wikidata | Aliases, tickers, industry |
| PDF | Playwright | Same HTML as web |
| Tracing | Langfuse (self-hosted) | Cost/latency per run |
| Run | docker compose | One command |
| Optional | Ollama | Local extraction models to cut cost |

**MVP status note (2026-10-22):** everything above is the final design; not every row is wired in yet, and the gaps are deliberate deferrals, not reversed decisions (none need a new ADR). Not yet implemented: nginx (API/worker run via `uv run`, not containers — no Dockerfile for them yet), the LangGraph Postgres checkpointer (not needed for a single-worker MVP), and Langfuse (cost/latency is already captured per-report via `cost_usd` on the `Report` row; the three `langfuse_*` settings in `config.py` are unused placeholders). See `docs/roadmap.md` stage 6/8 for the reasoning on each.

---

## 13. Planned repo layout
```
src/fieldbrief/
  api/        FastAPI app, routes, SSE
  worker/     FastStream worker, job entrypoints
  agents/     LangGraph graph, nodes, state
  llm/        gateway: routing, budget, rate limit, schemas
  fetch/      search, fetch, clean, EDGAR, Wikidata
  db/         SQLAlchemy models, repositories
  reports/    report schema, frameworks, rendering, PDF
  cli/        Typer CLI
  config.py   settings (pydantic-settings)
migrations/   Alembic
evals/        golden set, harness, results
tests/        unit + integration
docs/         design, ADRs, roadmap, board
```

---

## 14. Eval plan

Golden-set harness (stage 5), spec'd in ADR 0011 so stage 5 implements against a fixed plan instead of designing it mid-build.

**Golden set:** 10 companies in `evals/golden/<slug>.yaml`, chosen to stress specific design risks — Wix/Wiz (near-duplicate names, entity resolution), Shopify/DoorDash (heavy filings vs. thin-margin marketplace), Stripe/Notion/Canva/Airtable (private, funding-not-market-cap), Figma (recently public), Oatly (non-tech, public financial distress). Full table and rationale in ADR 0011.

**Ground truth per company:** `facts` (checkable fields with a source URL and, for numbers, a tolerance band), `competitors` (expected set), `key_points` (a short checklist per framework, graded by LLM judge — not string match, since SWOT/VRIO/positioning are judgment), `known_gaps` (fields a careful human couldn't find either — a report that invents a value here fails precision, not coverage).

**Metrics -> NFR (design.md §1):** claim precision + citation validity -> Accuracy; framework-quality judge score -> tracked, not gated; cost per report (from the gateway's cost log, ADR 0005) -> Cost; time-to-milestone from the SSE stream -> Latency; 3x-repeated completion rate -> Reliability.

**MVP scope:** the harness produces a committed results table (stage 5's "Done when"); no CI regression gate yet — add one only if eval runs become frequent enough to justify it.

---

## 15. Glossary
- **Claim** — one fact about one company, from one source, for one field.
- **Field** — a slot in the research checklist (e.g. `financials.revenue`).
- **Tier** — effort level: quick / standard / deep.
- **Finding** — an analytical conclusion (SWOT point, recommendation) linked to claims.
- **BMC** — Business Model Canvas. **VRIO** — Valuable, Rare, Inimitable, Organized.
