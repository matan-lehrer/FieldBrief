"""FastStream worker: consumes report jobs, runs the full orchestrator.

Stateless (ADR 0001) — any worker instance can pick up any job; everything
it needs (target company, tier) is read from the Report row, not the job
message itself (which only carries report_id + competitor_count, the one
thing not yet persisted anywhere at submit time).
"""

import logging
import uuid
from typing import cast

import httpx
from faststream import FastStream
from faststream.rabbit import RabbitBroker, RabbitQueue

from fieldbrief.agents.field_checklist import Tier, budget_usd_for, time_budget_seconds_for
from fieldbrief.agents.orchestrator import run_report
from fieldbrief.agents.progress import Stage
from fieldbrief.config import settings
from fieldbrief.db.models import Company
from fieldbrief.db.repositories import get_report
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.http import DEFAULT_USER_AGENT, Fetcher
from fieldbrief.llm.budget import Budget
from fieldbrief.llm.gateway import LLMGateway
from fieldbrief.worker.jobs import REPORT_JOBS_QUEUE, ReportJob
from fieldbrief.worker.progress import RabbitProgressSink

logger = logging.getLogger("fieldbrief.worker")

broker = RabbitBroker(settings.rabbitmq_url)
app = FastStream(broker)

report_jobs_queue = RabbitQueue(REPORT_JOBS_QUEUE, durable=True)


@broker.subscriber(report_jobs_queue)
async def handle_report_job(job: ReportJob) -> None:
    report_id = uuid.UUID(job.report_id)
    progress = RabbitProgressSink(job.report_id, broker)

    async with SessionFactory() as session:
        report = await get_report(session, report_id)
        if report is None:
            await progress.emit(Stage.FAILED, "report not found")
            logger.error("report_job_not_found", extra={"report_id": job.report_id})
            return

        company = await session.get(Company, report.target_company_id)
        if company is None:
            await progress.emit(Stage.FAILED, "target company not found")
            logger.error("report_job_company_not_found", extra={"report_id": job.report_id})
            return

        report.status = "running"
        await session.commit()

        tier = cast(Tier, report.tier)
        budget = Budget(limit_usd=budget_usd_for(tier))
        gateway = LLMGateway()
        fetcher = Fetcher()
        http_client = httpx.AsyncClient()
        wikidata_client = httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT})
        edgar_client = httpx.AsyncClient(headers={"User-Agent": settings.sec_user_agent})

        try:
            await run_report(
                session=session,
                gateway=gateway,
                budget=budget,
                fetcher=fetcher,
                http_client=http_client,
                wikidata_client=wikidata_client,
                searxng_url=settings.searxng_url,
                report_id=report_id,
                target_company=company,
                tier=tier,
                competitor_count=job.competitor_count,
                progress=progress,
                edgar_client=edgar_client,
                time_budget_seconds=time_budget_seconds_for(tier),
            )
        except Exception as exc:
            logger.exception("report_job_failed", extra={"report_id": job.report_id})
            await progress.emit(Stage.FAILED, str(exc))
            failed_report = await get_report(session, report_id)
            if failed_report is not None:
                failed_report.status = "failed"
                await session.commit()
        finally:
            await fetcher.aclose()
            await http_client.aclose()
            await wikidata_client.aclose()
            await edgar_client.aclose()
