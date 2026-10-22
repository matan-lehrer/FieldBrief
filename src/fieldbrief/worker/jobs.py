"""The job message the API publishes and the worker consumes.

Stage 6 simplification vs. design.md §8's literal `{company_id, competitor_ids,
tier}`: competitor discovery happens inside run_report (ADR 0012), not as a
separate pre-step the client resolves first — there's no UI yet (stage 7) to
let a user edit a discovered list before submitting, so `competitor_count`
stands in for `competitor_ids` until that exists.
"""

from pydantic import BaseModel

REPORT_JOBS_QUEUE = "fieldbrief.report_jobs"


class ReportJob(BaseModel):
    report_id: str
    competitor_count: int
