"""FastAPI service (design.md §8). `POST /reports` only enqueues and returns
— real work happens in the worker (agents/orchestrator.py via worker/app.py).

Auth: a single shared API key (MVP — design.md: "Single user now, designed
for scale"). Every request resolves to the same fixed user as the CLI
(db/repositories.get_or_create_cli_user) until there's a real multi-user
auth story (stage 8+).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

import aio_pika
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from faststream.rabbit import RabbitBroker

from fieldbrief.agents.field_checklist import TIER_COMPETITOR_COUNT
from fieldbrief.agents.progress import PROGRESS_EXCHANGE, Stage, routing_key_for
from fieldbrief.api.schemas import (
    CompanyOut,
    CreateReportRequest,
    CreateReportResponse,
    ReportOut,
    ResolveRequest,
)
from fieldbrief.config import settings
from fieldbrief.db.repositories import (
    create_report,
    get_or_create_cli_user,
    get_or_create_company,
    get_report,
    get_report_by_idempotency_key,
)
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.http import DEFAULT_USER_AGENT
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.worker.jobs import REPORT_JOBS_QUEUE, ReportJob

job_broker = RabbitBroker(settings.rabbitmq_url)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await job_broker.start()
    try:
        yield
    finally:
        await job_broker.stop()


app = FastAPI(title="FieldBrief API", lifespan=lifespan)


async def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
    if x_api_key != settings.api_key:
        raise HTTPException(status_code=401, detail="invalid or missing X-API-Key")


@app.post("/companies/resolve", response_model=CompanyOut, dependencies=[Depends(require_api_key)])
async def resolve_company(body: ResolveRequest) -> CompanyOut:
    """Resolves and persists the best-matching company (ADR 0011's
    Wix/Wiz-style disambiguation already applied by pick_company_like).
    Simplification vs. design.md's "candidates -> user confirms": with no UI
    yet (stage 7), this returns one resolved company rather than a candidate
    list to choose from."""
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as wikidata_client:
        candidates = await search_candidates(wikidata_client, body.query, limit=5)
        if not candidates:
            raise HTTPException(status_code=404, detail=f"no candidates found for '{body.query}'")
        best = await pick_company_like(wikidata_client, candidates) or candidates[0]
        details = await get_company_details(wikidata_client, best.qid)

    async with SessionFactory() as session:
        company = await get_or_create_company(
            session,
            name=details.name,
            domain=details.domain,
            aliases=details.aliases,
            ticker=details.ticker,
            cik=details.cik,
            industry=details.industry,
        )
        await session.commit()
        return CompanyOut(
            id=company.id, name=company.name, domain=company.domain, industry=company.industry
        )


@app.post("/reports", response_model=CreateReportResponse, dependencies=[Depends(require_api_key)])
async def create_report_endpoint(
    body: CreateReportRequest, idempotency_key: Annotated[str | None, Header()] = None
) -> CreateReportResponse:
    """Enqueues and returns — design.md's <=200ms ack. No LLM/fetch calls
    here; the worker does all of that."""
    competitor_count = (
        body.competitor_count
        if body.competitor_count is not None
        else TIER_COMPETITOR_COUNT[body.tier]
    )
    async with SessionFactory() as session:
        user = await get_or_create_cli_user(session)

        if idempotency_key is not None:
            existing = await get_report_by_idempotency_key(session, user.id, idempotency_key)
            if existing is not None:
                return CreateReportResponse(report_id=existing.id)

        report = await create_report(
            session,
            user_id=user.id,
            target_company_id=body.company_id,
            tier=body.tier,
            status="queued",
            idempotency_key=idempotency_key,
        )
        await session.commit()

    await job_broker.publish(
        ReportJob(report_id=str(report.id), competitor_count=competitor_count).model_dump(
            mode="json"
        ),
        queue=REPORT_JOBS_QUEUE,
    )
    return CreateReportResponse(report_id=report.id)


@app.get("/reports/{report_id}", response_model=ReportOut, dependencies=[Depends(require_api_key)])
async def get_report_endpoint(report_id: UUID) -> ReportOut:
    async with SessionFactory() as session:
        report = await get_report(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        return ReportOut(
            id=report.id,
            status=report.status,
            tier=report.tier,
            target_company_id=report.target_company_id,
            report_json=report.report_json,
            cost_usd=float(report.cost_usd) if report.cost_usd is not None else None,
            created_at=report.created_at,
            completed_at=report.completed_at,
        )


@app.get("/reports/{report_id}/events", dependencies=[Depends(require_api_key)])
async def report_events(report_id: UUID) -> StreamingResponse:
    """SSE progress stream (design.md: "Worker -> RabbitMQ topic exchange ->
    API -> SSE"). One exclusive, auto-delete queue per connection, bound to
    this report's routing key — closes on DONE/FAILED or client disconnect."""

    async def _terminal_event_from_db() -> str | None:
        """A worker that finishes (or dies) between this connection opening
        and the queue bind below would publish into an exchange with no
        bound queue yet — AMQP topic exchanges don't replay to late
        subscribers, so that message is simply gone. Falling back to the
        Report row's own status is what keeps a client from hanging forever
        on a "done" that already happened."""
        async with SessionFactory() as session:
            report = await get_report(session, report_id)
        if report is None or report.status not in ("done", "partial", "failed"):
            return None
        stage = Stage.DONE.value if report.status != "failed" else Stage.FAILED.value
        return json.dumps({"report_id": str(report_id), "stage": stage, "message": report.status})

    async def event_stream() -> AsyncIterator[str]:
        connection = await aio_pika.connect_robust(settings.rabbitmq_url)
        try:
            channel = await connection.channel()
            exchange = await channel.declare_exchange(
                PROGRESS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
            )
            queue = await channel.declare_queue(exclusive=True, auto_delete=True)
            await queue.bind(exchange, routing_key=routing_key_for(str(report_id)))

            # Bound first, so nothing published after this point is missed;
            # this catches anything that finished before the bind above.
            catch_up = await _terminal_event_from_db()
            if catch_up is not None:
                yield f"data: {catch_up}\n\n"
                return

            async with queue.iterator() as queue_iter:
                while True:
                    try:
                        message = await asyncio.wait_for(queue_iter.__anext__(), timeout=5.0)
                    except TimeoutError:
                        # No progress message in 5s — poll the Report row so a
                        # client never hangs past the job's actual completion.
                        catch_up = await _terminal_event_from_db()
                        if catch_up is not None:
                            yield f"data: {catch_up}\n\n"
                            return
                        continue
                    async with message.process():
                        payload = json.loads(message.body)
                        yield f"data: {json.dumps(payload)}\n\n"
                        if payload.get("stage") in (Stage.DONE.value, Stage.FAILED.value):
                            return
        finally:
            await connection.close()

    return StreamingResponse(event_stream(), media_type="text/event-stream")
