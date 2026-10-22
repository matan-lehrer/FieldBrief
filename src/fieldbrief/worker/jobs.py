from pydantic import BaseModel

REPORT_JOBS_QUEUE = "fieldbrief.report_jobs"


class ReportJob(BaseModel):
    report_id: str
    competitor_count: int
