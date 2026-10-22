"""Prompt builders for each LLM call the graph makes. Kept separate from
nodes.py so prompt wording can change without touching call/control flow."""

import uuid

from fieldbrief.db.models import Claim
from fieldbrief.fetch.clean import select_relevant_passages

FIELD_DESCRIPTIONS: dict[str, str] = {
    "profile.description": "What the company does, in a sentence or two",
    "profile.hq_founded": (
        "Headquarters location and founding year — tag via value.hq (city, country) "
        "and value.founded_year (integer), whichever the page supports"
    ),
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
        "Buyer/supplier power, substitutes, threat of new entrants, rivalry — tag which force "
        "via value.force, one of "
        "buyer_power/supplier_power/substitutes/threat_of_new_entrants/rivalry"
    ),
    "environment.regulation": "Relevant regulation affecting the business",
    "market.size_growth": "Total addressable market size and growth rate",
    "partners.key": "Key partners or suppliers",
    "strategy.moves": "M&A, expansions, product launches",
    "risks.disclosed": "Risk factors disclosed in filings",
}


def _extraction_keywords(company_name: str, fields: list[str]) -> set[str]:
    keywords = set(company_name.split())
    for f in fields:
        keywords.update(f.replace(".", " ").replace("_", " ").split())
        keywords.update(FIELD_DESCRIPTIONS.get(f, "").replace(",", " ").replace("/", " ").split())
    return keywords


def extraction_prompt(company_name: str, page_text: str, fields: list[str]) -> list[dict[str, str]]:
    field_list = "\n".join(f"- {f}: {FIELD_DESCRIPTIONS.get(f, f)}" for f in fields)
    # Bounded, relevance-ranked passage selection instead of a blind head
    # slice (`page_text[:8000]`) — a claim three screens down the page used
    # to never reach the model at all, regardless of how clearly it answered
    # one of the requested fields.
    bounded_text = select_relevant_passages(
        page_text, _extraction_keywords(company_name, fields), max_chars=8000
    )
    system = (
        "You extract factual claims about ONE company from a web page. Only extract "
        "a claim if the page text actually supports it — never invent or guess. If "
        "the page doesn't address a field, simply omit it; do not fabricate a value. "
        "For each claim, also set excerpt to an EXACT, verbatim short quote (one or "
        "two sentences, copied character-for-character from the page text) that "
        "supports it — never paraphrase the excerpt, and omit it if no single short "
        "passage directly supports the claim. "
        "Separately, for ANY claim (not just the fields below) that also describes part "
        "of the company's business model, set bmc_block to the matching Business Model "
        "Canvas block (segments, value_propositions, channels, customer_relationships, "
        "revenue_streams, key_resources, key_activities, key_partners, cost_structure) — "
        "leave it unset if none fits."
    )
    user = (
        f"Company: {company_name}\n\n"
        f"Fields to look for (skip any the page doesn't support):\n{field_list}\n\n"
        f"Page text:\n{bounded_text}"
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


# How to frame the executive brief / audience-implications section for each
# selected audience (ADR 0014) — steers interpretation of the SAME shared
# evidence, never a second research pass.
AUDIENCE_FRAMING: dict[str, str] = {
    "analyst": "a competitive analyst building a market picture — frame around what's "
    "defensible, what's uncertain, and what a careful reader should verify further",
    "investor": "an investor evaluating the company — frame around growth, risk, "
    "defensibility of the business, and what would change an investment thesis",
    "leadership": "the target company's own leadership — frame around strategic "
    "priorities, competitive exposure, and what decisions this should inform",
    "competitor": "a rival company's strategy team — frame around where the target is "
    "vulnerable or strong, and what a competitive response might look like",
    "sales_partnership": "someone evaluating a sales or partnership opportunity with the "
    "target — frame around fit, leverage points, and open questions before engaging",
}


def framework_synthesis_prompt(
    companies: list[tuple[uuid.UUID, str, bool]],  # (company_id, name, is_target)
    claims: list[Claim],
    audience: str = "analyst",
) -> list[dict[str, str]]:
    """One call producing every cross-company judgment: an executive brief,
    Five Forces, VRIO (target only — the claims list simply won't have
    resources.advantages claims for competitors, per field_checklist's
    target_only fields), positioning, the "other" SWOT sources (events/
    market_size/regulation/bmc/financials/risks), recommendations, and
    audience-tailored implications. One shared research pass feeds every
    audience — `audience` only steers interpretation, never a second
    research pass (ADR 0014)."""
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
    audience_framing = AUDIENCE_FRAMING.get(audience, AUDIENCE_FRAMING["analyst"])
    system = (
        "You are a strategy analyst. Using ONLY the numbered claims below, produce: "
        "an executive brief of the 3-5 most material, well-supported findings across "
        "everything below, each with one sentence on why it matters; "
        "Five Forces ratings (one per force, only for forces with supporting claims — "
        "rating is always 'how much pressure this force puts on the company,' high is "
        "always bad. For threat_of_new_entrants specifically: rate it by how easy entry "
        "is, not by how high barriers are — low barriers to entry mean HIGH threat, high "
        "barriers mean LOW threat. Use insufficient_evidence instead of guessing a "
        "direction when the claims only mention a force in passing); "
        "VRIO assessment for each distinct resource named in resources.advantages claims "
        "(assess 'organized' honestly — whether the company is actually set up to "
        "capture this resource's value, not just whether the resource exists); "
        "a 2-axis positioning map (Price: low-cost vs premium; Differentiation breadth: "
        "niche vs broad) placing only the companies you have real claims to place — never "
        "guess a position with no evidence behind it, even if that means placing fewer "
        "than every company; "
        "SWOT strengths/weaknesses from financials/risks claims that reveal something "
        "about the company's OWN capabilities (e.g. shrinking margins, customer "
        "concentration, a disclosed risk) rather than the market — separate from VRIO, "
        "which is derived deterministically below, not authored directly; "
        "SWOT opportunities/threats from events/market_size/regulation/bmc claims only "
        "(never five_forces or vrio — those are derived separately); "
        "3-7 recommendations; and audience_implications written for "
        f"{audience_framing} — relevance (why these findings matter to this reader), "
        "explicit assumptions you're making, and open follow-up questions. Omit "
        "audience_implications entirely if the claims don't support it. "
        "Every item MUST cite at least one claim by its index — never invent an index, "
        "never cite a claim that doesn't support the point. If the claims don't support "
        "a force, a resource, a company's position, a SWOT item, a recommendation, an "
        "executive-brief finding, or the audience implications, omit it; fewer honest "
        "findings beat invented ones."
    )
    user = f"Companies:\n{numbered_companies}\n\nNumbered claims:\n{numbered_claims}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
