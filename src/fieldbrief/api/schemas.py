"""Request/response models for the API (design.md §8)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from fieldbrief.agents.field_checklist import Tier


class ResolveRequest(BaseModel):
    query: str


class CompanyOut(BaseModel):
    id: UUID
    name: str
    domain: str | None
    industry: str | None


class CreateReportRequest(BaseModel):
    company_id: UUID
    tier: Tier = "standard"
    competitor_count: int | None = None  # None -> the tier's own default count


class CreateReportResponse(BaseModel):
    report_id: UUID


class ReportOut(BaseModel):
    id: UUID
    status: str
    tier: str
    target_company_id: UUID
    report_json: dict[str, Any] | None
    cost_usd: float | None
    created_at: datetime
    completed_at: datetime | None
