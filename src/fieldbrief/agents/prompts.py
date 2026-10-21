"""Prompt builders for each LLM call the graph makes. Kept separate from
nodes.py so prompt wording can change without touching call/control flow."""

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
}


def extraction_prompt(company_name: str, page_text: str, fields: list[str]) -> list[dict[str, str]]:
    field_list = "\n".join(f"- {f}: {FIELD_DESCRIPTIONS.get(f, f)}" for f in fields)
    system = (
        "You extract factual claims about ONE company from a web page. Only extract "
        "a claim if the page text actually supports it — never invent or guess. If "
        "the page doesn't address a field, simply omit it; do not fabricate a value."
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


def synthesis_prompt(company_name: str, claims: list[Claim]) -> list[dict[str, str]]:
    numbered = "\n".join(f"[{i}] ({c.field}) {c.statement}" for i, c in enumerate(claims))
    system = (
        "You analyze a company's verified claims to find SWOT opportunities/threats "
        "and 3-7 recommendations. Every opportunity/threat MUST cite at least one "
        "claim by its index from the numbered list below — never invent an index, "
        "never cite a claim that doesn't support the point. Every recommendation MUST "
        "reference at least one opportunity/threat by index into the combined list "
        "(opportunities first, then threats, 0-indexed) — a recommendation that "
        "doesn't address any opportunity or threat you found is out of scope, skip it. "
        "If the claims don't support an opportunity, threat, or recommendation, simply "
        "don't produce one; fewer honest findings beat invented ones."
    )
    user = f"Company: {company_name}\n\nNumbered claims:\n{numbered}"
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
