"""Prompt builders for each LLM call the graph makes. Kept separate from
nodes.py so prompt wording can change without touching call/control flow."""

import uuid

from fieldbrief.db.models import Claim

FIELD_DESCRIPTIONS: dict[str, str] = {
    "profile.description": "What the company does, in a sentence or two",
    "profile.hq_founded": "Headquarters location and founding year",
    "profile.employees": "Approximate headcount",
    "offering.products": "Main products or services offered",
    "customers.segments": "Who buys from this company (customer segments)",
    "revenue.model": "How the company makes money",
    "financials.revenue": "Latest reported revenue and growth, if public or disclosed",
    "events.recent": "Notable news or events in roughly the last 12 months",
    "pricing.model": "Price points, tiers, free plan",
    "offering.value_prop": "Claimed differentiation vs. competitors",
    "channels.go_to_market": "Sales motion (self-serve, sales-led, etc.)",
    "financials.profitability": "Margins, net income, or burn rate",
    "financials.funding_or_cap": "Funding raised (private) or market cap (public)",
    "resources.advantages": (
        "Brand, IP, network effects, data, or switching costs that are hard to copy — "
        "tag which specific resource via value.resource, e.g. {'resource': 'network effects'}"
    ),
    "market.forces": (
        "Buyer/supplier power, substitutes, entry barriers, rivalry — tag which force via "
        "value.force, one of buyer_power/supplier_power/substitutes/entry_barriers/rivalry"
    ),
    "environment.regulation": "Relevant regulation affecting the business",
    "market.size_growth": "Total addressable market size and growth rate",
    "partners.key": "Key partners or suppliers",
    "strategy.moves": "M&A, expansions, product launches",
    "risks.disclosed": "Risk factors disclosed in filings",
}


def extraction_prompt(company_name: str, page_text: str, fields: list[str]) -> list[dict[str, str]]:
    field_list = "\n".join(f"- {f}: {FIELD_DESCRIPTIONS.get(f, f)}" for f in fields)
    system = (
        "You extract factual claims about ONE company from a web page. Only extract "
        "a claim if the page text actually supports it — never invent or guess. If "
        "the page doesn't address a field, simply omit it; do not fabricate a value. "
        "Separately, for ANY claim (not just the fields below) that also describes part "
        "of the company's business model, set bmc_block to the matching Business Model "
        "Canvas block (segments, value_propositions, channels, customer_relationships, "
        "revenue_streams, key_resources, key_activities, key_partners, cost_structure) — "
        "leave it unset if none fits."
    )
    user = (
        f"Company: {company_name}\n\n"
        f"Fields to look for (skip any the page doesn't support):\n{field_list}\n\n"
        f"Page text:\n{page_text[:8000]}"
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def verification_prompt(statement: str, snippet: str) -> list[dict[str, str]]:
    system = (
        "You check whether a snippet of source text actually supports a claimed "
        "statement. Be strict: paraphrase and inference are fine, but the snippet "
        "must genuinely say this, not just be topically related."
    )
    user = f"Statement: {statement}\n\nSource snippet: {snippet}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def framework_synthesis_prompt(
    companies: list[tuple[uuid.UUID, str, bool]],  # (company_id, name, is_target)
    claims: list[Claim],
) -> list[dict[str, str]]:
    """One call producing every cross-company judgment: Five Forces, VRIO
    (target only — the claims list simply won't have resources.advantages
    claims for competitors, per field_checklist's target_only fields),
    positioning, the "other" SWOT sources (events/market_size/regulation/bmc),
    and recommendations."""
    name_by_id = {cid: name for cid, name, _ in companies}
    numbered_companies = "\n".join(
        f"[{i}] {name} ({'target' if is_target else 'competitor'})"
        for i, (_, name, is_target) in enumerate(companies)
    )
    numbered_claims = "\n".join(
        f"[{i}] ({name_by_id.get(c.company_id, '?')} / {c.field}"
        f"{f' / {c.value}' if c.value else ''}) {c.statement}"
        for i, c in enumerate(claims)
    )
    system = (
        "You are a strategy analyst. Using ONLY the numbered claims below, produce: "
        "Five Forces ratings (one per force, only for forces with supporting claims), "
        "VRIO assessment for each distinct resource named in resources.advantages claims, "
        "a 2-axis positioning map (Price: low-cost vs premium; Differentiation breadth: "
        "niche vs broad) placing every numbered company, SWOT opportunities/threats from "
        "events/market_size/regulation/bmc claims only (never five_forces or vrio — those "
        "are derived separately), and 3-7 recommendations. "
        "Every item MUST cite at least one claim by its index — never invent an index, "
        "never cite a claim that doesn't support the point. If the claims don't support "
        "a force, a resource, a SWOT item, or a recommendation, omit it; fewer honest "
        "findings beat invented ones."
    )
    user = f"Companies:\n{numbered_companies}\n\nNumbered claims:\n{numbered_claims}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
