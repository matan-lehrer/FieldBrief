import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from fieldbrief.agents.llm_schemas import FrameworkSynthesisResult
from fieldbrief.agents.prompts import framework_synthesis_prompt
from fieldbrief.db.models import Claim
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway, Task
from fieldbrief.reports.schema import DEFAULT_AUDIENCE, Audience

COMPARISON_DIMENSIONS: list[tuple[str, str]] = [
    ("description", "What they do"),
    ("products", "Main products"),
    ("customer_segments", "Customer segments"),
    ("revenue_model", "Revenue model"),
    ("revenue", "Revenue"),
    ("funding_or_cap", "Funding / market cap"),
]


class CompanyRef:
    __slots__ = ("company_id", "is_target", "name", "snapshot")

    def __init__(
        self, company_id: uuid.UUID, name: str, is_target: bool, snapshot: dict[str, Any]
    ) -> None:
        self.company_id = company_id
        self.name = name
        self.is_target = is_target
        self.snapshot = snapshot


def _vrio_implication(valuable: bool, rare: bool, inimitable: bool, organized: bool) -> str:
    if not valuable:
        return "competitive_disadvantage"
    if not rare:
        return "parity"
    if not inimitable:
        return "temporary_advantage"
    if not organized:
        return "unexploited_advantage"
    return "sustained_advantage"


def _build_comparison_table(companies: list[CompanyRef]) -> dict[str, Any]:
    rows = []
    for key, label in COMPARISON_DIMENSIONS:
        cells = [
            {"company_id": str(c.company_id), "citation": c.snapshot.get(key)} for c in companies
        ]
        if not any(cell["citation"] for cell in cells):
            continue
        rows.append({"dimension": key, "label": label, "cells": cells})
    return {"rows": rows}


async def run_synthesis(
    *,
    session: AsyncSession,
    gateway: LLMGateway,
    budget: Budget,
    report_id: uuid.UUID,
    tier: str,
    companies: list[CompanyRef],
    gaps: list[dict[str, Any]],
    audience: Audience = DEFAULT_AUDIENCE,
) -> dict[str, Any]:
    target = next(c for c in companies if c.is_target)
    company_ids = [c.company_id for c in companies]

    claims_result = await session.execute(
        select(Claim)
        .where(Claim.company_id.in_(company_ids), Claim.verified.is_(True))
        .order_by(Claim.extracted_at)
    )
    claims = list(claims_result.scalars().all())

    strengths: list[dict[str, Any]] = []
    weaknesses: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    threats: list[dict[str, Any]] = []
    vrio: list[dict[str, Any]] = []
    forces: list[dict[str, Any]] = []
    positioning: dict[str, Any] = {"x_axis": None, "y_axis": None, "companies": []}
    recommendations: list[dict[str, Any]] = []
    executive_brief: list[dict[str, Any]] = []
    audience_implications: dict[str, Any] | None = None

    if claims:
        company_tuples = [(c.company_id, c.name, c.is_target) for c in companies]
        result = await gateway.call(
            Task.SYNTHESIZE,
            framework_synthesis_prompt(company_tuples, claims, audience=audience),
            FrameworkSynthesisResult,
            budget,
            report_id=str(report_id),
            stage="synthesize",
        )

        def resolve_ids(indices: list[int]) -> list[str]:
            return [str(claims[i].id) for i in indices if 0 <= i < len(claims)]

        for finding in result.executive_brief:
            claim_ids = resolve_ids(finding.source_claim_indices)
            if not claim_ids:
                continue
            executive_brief.append(
                {
                    "statement": finding.statement,
                    "why_it_matters": finding.why_it_matters,
                    "claim_ids": claim_ids,
                }
            )

        if result.audience_implications is not None:
            claim_ids = resolve_ids(result.audience_implications.source_claim_indices)
            if claim_ids:
                audience_implications = {
                    "audience": audience,
                    "relevance": result.audience_implications.relevance,
                    "assumptions": result.audience_implications.assumptions,
                    "follow_up_questions": result.audience_implications.follow_up_questions,
                    "claim_ids": claim_ids,
                }

        for force in result.five_forces:
            claim_ids = resolve_ids(force.source_claim_indices)
            if not claim_ids:
                continue
            forces.append(
                {
                    "force": force.force,
                    "rating": force.rating,
                    "rationale": force.rationale,
                    "claim_ids": claim_ids,
                }
            )
            item = {
                "statement": force.rationale,
                "derived_from": "five_forces",
                "claim_ids": claim_ids,
            }
            if force.rating == "high":
                threats.append(item)
            elif force.rating == "low":
                opportunities.append(item)

        for resource in result.vrio:
            claim_ids = resolve_ids(resource.source_claim_indices)
            if not claim_ids:
                continue
            implication = _vrio_implication(
                resource.valuable, resource.rare, resource.inimitable, resource.organized
            )
            vrio.append(
                {
                    "resource": resource.resource,
                    "valuable": resource.valuable,
                    "rare": resource.rare,
                    "inimitable": resource.inimitable,
                    "organized": resource.organized,
                    "competitive_implication": implication,
                    "rationale": resource.rationale,
                    "claim_ids": claim_ids,
                }
            )
            item = {"statement": resource.rationale, "derived_from": "vrio", "claim_ids": claim_ids}
            if implication in (
                "sustained_advantage",
                "temporary_advantage",
                "unexploited_advantage",
            ):
                strengths.append(item)
            elif implication == "competitive_disadvantage":
                weaknesses.append(item)

        if result.positioning is not None and len(companies) > 1:
            placed = []
            for placement in result.positioning.companies:
                if not (0 <= placement.company_index < len(companies)):
                    continue
                claim_ids = resolve_ids(placement.source_claim_indices)
                if not claim_ids:
                    continue
                placed.append(
                    {
                        "company_id": str(companies[placement.company_index].company_id),
                        "x": placement.x,
                        "y": placement.y,
                        "claim_ids": claim_ids,
                    }
                )
            if len(placed) >= 2:
                positioning = {
                    "x_axis": {
                        "name": "Price",
                        "low_label": "Low-cost",
                        "high_label": "Premium",
                        "rationale": result.positioning.x_axis_rationale,
                    },
                    "y_axis": {
                        "name": "Differentiation breadth",
                        "low_label": "Niche",
                        "high_label": "Broad",
                        "rationale": result.positioning.y_axis_rationale,
                    },
                    "companies": placed,
                }

        for item_draft in result.other_strengths:
            claim_ids = resolve_ids(item_draft.source_claim_indices)
            if claim_ids:
                strengths.append(
                    {
                        "statement": item_draft.statement,
                        "derived_from": item_draft.derived_from,
                        "claim_ids": claim_ids,
                    }
                )
        for item_draft in result.other_weaknesses:
            claim_ids = resolve_ids(item_draft.source_claim_indices)
            if claim_ids:
                weaknesses.append(
                    {
                        "statement": item_draft.statement,
                        "derived_from": item_draft.derived_from,
                        "claim_ids": claim_ids,
                    }
                )
        for item_draft in result.other_opportunities:
            claim_ids = resolve_ids(item_draft.source_claim_indices)
            if claim_ids:
                opportunities.append(
                    {
                        "statement": item_draft.statement,
                        "derived_from": item_draft.derived_from,
                        "claim_ids": claim_ids,
                    }
                )
        for item_draft in result.other_threats:
            claim_ids = resolve_ids(item_draft.source_claim_indices)
            if claim_ids:
                threats.append(
                    {
                        "statement": item_draft.statement,
                        "derived_from": item_draft.derived_from,
                        "claim_ids": claim_ids,
                    }
                )

        swot_combined = strengths + weaknesses + opportunities + threats
        for rec in result.recommendations:
            rec_claim_ids = set(resolve_ids(rec.source_claim_indices))
            if not rec_claim_ids:
                continue
            swot_refs = [
                i
                for i, swot_item in enumerate(swot_combined)
                if rec_claim_ids & set(swot_item["claim_ids"])
            ]
            if not swot_refs:
                continue
            recommendations.append(
                {
                    "statement": rec.statement,
                    "rationale": rec.rationale,
                    "priority": rec.priority,
                    "swot_refs": swot_refs,
                    "claim_ids": sorted(rec_claim_ids),
                }
            )

    bmc_groups: dict[str, list[Claim]] = defaultdict(list)
    for claim in claims:
        if claim.company_id != target.company_id:
            continue
        tag = claim.value.get("bmc_block")
        if tag:
            bmc_groups[tag].append(claim)
    bmc_blocks = [
        {
            "block": block_name,
            "statement": max(block_claims, key=lambda c: c.relevance).statement,
            "claim_ids": [str(c.id) for c in block_claims],
        }
        for block_name, block_claims in bmc_groups.items()
    ]

    report_json = {
        "meta": {
            "target_company_id": str(target.company_id),
            "competitor_ids": [str(c.company_id) for c in companies if not c.is_target],
            "tier": tier,
            "audience": audience,
            "generated_at": datetime.now(UTC).isoformat(),
            "data_as_of": date.today().isoformat(),
        },
        "companies": [c.snapshot for c in companies],
        "executive_brief": executive_brief,
        "five_forces": {"forces": forces, "freshness": None},
        "bmc": {"blocks": bmc_blocks, "freshness": None},
        "vrio": vrio,
        "positioning": positioning,
        "swot": {
            "strengths": strengths,
            "weaknesses": weaknesses,
            "opportunities": opportunities,
            "threats": threats,
        },
        "comparison": _build_comparison_table(companies),
        "recommendations": recommendations,
        "audience_implications": audience_implications,
        "gaps": gaps,
    }
    return report_json
