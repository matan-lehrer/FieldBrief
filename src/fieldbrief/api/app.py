"""FastAPI service. `POST /reports` only enqueues and returns — real work
happens in the worker (agents/orchestrator.py via worker/app.py).

Auth is a single shared API key; every request resolves to the same fixed
user as the CLI (db/repositories.get_or_create_cli_user).
"""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, cast
from uuid import UUID

import aio_pika
import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from faststream.rabbit import RabbitBroker
from sqlalchemy import select

from fieldbrief.agents.field_checklist import TIER_COMPETITOR_COUNT, Tier
from fieldbrief.agents.progress import PROGRESS_EXCHANGE, Stage, routing_key_for
from fieldbrief.api.schemas import (
    CompanyOut,
    CreateReportRequest,
    CreateReportResponse,
    ReportOut,
    ResolveRequest,
)
from fieldbrief.config import settings
from fieldbrief.consts import WIKIDATA_CANDIDATE_LIMIT
from fieldbrief.db.models import Company
from fieldbrief.db.repositories import (
    count_report_competitors,
    create_report,
    get_or_create_cli_user,
    get_or_create_company,
    get_report,
    get_report_by_idempotency_key,
    list_reports_for_user,
)
from fieldbrief.db.session import SessionFactory
from fieldbrief.fetch.http import DEFAULT_USER_AGENT
from fieldbrief.fetch.wikidata import get_company_details, pick_company_like, search_candidates
from fieldbrief.reports.pdf import render_report_pdf
from fieldbrief.reports.render import (
    build_claims_map,
    collect_claim_ids,
    render_failed_html,
    render_history_html,
    render_home_html,
    render_pdf_html,
    render_progress_html,
    render_report_html,
)
from fieldbrief.worker.jobs import REPORT_JOBS_QUEUE, ReportJob

STATIC_DIR = Path(__file__).parent.parent / "static"

job_broker = RabbitBroker(settings.rabbitmq_url)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await job_broker.start()
    try:
        yield
    finally:
        await job_broker.stop()


app = FastAPI(title="FieldBrief API", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
async def home() -> HTMLResponse:
    """Not behind require_api_key — it's just a form; the API calls it
    makes from the browser still need the key."""
    return HTMLResponse(render_home_html())


async def require_api_key(
    x_api_key: Annotated[str | None, Header()] = None,
    api_key: Annotated[str | None, Query()] = None,
) -> None:
    """Header is the primary method; `?api_key=` is a fallback for
    browser-native EventSource, which can't send custom headers at all —
    `/view`'s embedded JS needs some way to authenticate its SSE call."""
    if settings.api_key not in (x_api_key, api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


@app.post("/companies/resolve", response_model=CompanyOut, dependencies=[Depends(require_api_key)])
async def resolve_company(body: ResolveRequest) -> CompanyOut:
    """Resolves and persists the best-matching company. Returns one
    resolved company rather than a candidate list to choose from."""
    async with httpx.AsyncClient(headers={"User-Agent": DEFAULT_USER_AGENT}) as wikidata_client:
        candidates = await search_candidates(
            wikidata_client, body.query, limit=WIKIDATA_CANDIDATE_LIMIT
        )
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
    """Enqueues and returns immediately; the worker does the real work."""
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
            audience=body.audience,
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
            audience=report.audience,
            target_company_id=report.target_company_id,
            report_json=report.report_json,
            cost_usd=float(report.cost_usd) if report.cost_usd is not None else None,
            created_at=report.created_at,
            completed_at=report.completed_at,
        )


@app.post(
    "/reports/{report_id}/refresh",
    response_model=CreateReportResponse,
    dependencies=[Depends(require_api_key)],
)
async def refresh_report(report_id: UUID) -> CreateReportResponse:
    """A new, separate report linked via parent_report_id — reports are
    immutable, never mutated in place."""
    async with SessionFactory() as session:
        old_report = await get_report(session, report_id)
        if old_report is None:
            raise HTTPException(status_code=404, detail="report not found")
        user_id = old_report.user_id
        target_company_id = old_report.target_company_id
        tier = cast(Tier, old_report.tier)
        # competitor_count isn't stored on Report itself — read back from
        # how many REPORT_COMPETITORS rows the original actually wrote.
        competitor_count = await count_report_competitors(session, old_report.id)

        new_report = await create_report(
            session,
            user_id=user_id,
            target_company_id=target_company_id,
            tier=tier,
            status="queued",
            parent_report_id=old_report.id,
            audience=old_report.audience,
        )
        await session.commit()
        new_report_id = new_report.id

    await job_broker.publish(
        ReportJob(report_id=str(new_report_id), competitor_count=competitor_count).model_dump(
            mode="json"
        ),
        queue=REPORT_JOBS_QUEUE,
    )
    return CreateReportResponse(report_id=new_report_id)


async def _terminal_event_from_db(report_id: UUID) -> str | None:
    """AMQP topic exchanges don't replay to late subscribers — a worker that
    finishes between this connection opening and the queue bind below would
    publish into a message that's simply gone. Falling back to the Report
    row's own status keeps a client from hanging on a "done" it missed."""
    async with SessionFactory() as session:
        report = await get_report(session, report_id)
    if report is None or report.status not in ("done", "partial", "failed"):
        return None
    stage = Stage.DONE.value if report.status != "failed" else Stage.FAILED.value
    return json.dumps({"report_id": str(report_id), "stage": stage, "message": report.status})


async def progress_event_stream(report_id: UUID) -> AsyncIterator[str]:
    """The SSE body. A module-level function, not a route-local closure, so
    it can be iterated directly in tests — httpx's ASGITransport buffers an
    entire StreamingResponse until the generator fully completes, which
    makes it useless for testing a stream still open seconds later."""
    connection = await aio_pika.connect_robust(settings.rabbitmq_url)
    # A plain queue, not aio_pika's own iterator: cancelling a wait on
    # RobustQueue's iterator poisons it (next __anext__() raises, which
    # PEP 479 turns into a RuntimeError that kills the SSE response for
    # good). Cancelling asyncio.Queue.get() is always safe.
    local_queue: asyncio.Queue[aio_pika.abc.AbstractIncomingMessage] = asyncio.Queue()
    try:
        channel = await connection.channel()
        exchange = await channel.declare_exchange(
            PROGRESS_EXCHANGE, aio_pika.ExchangeType.TOPIC, durable=True
        )
        queue = await channel.declare_queue(exclusive=True, auto_delete=True)
        await queue.bind(exchange, routing_key=routing_key_for(str(report_id)))
        await queue.consume(local_queue.put)

        # Bound first, so nothing published after this point is missed.
        catch_up = await _terminal_event_from_db(report_id)
        if catch_up is not None:
            yield f"data: {catch_up}\n\n"
            return

        while True:
            try:
                message = await asyncio.wait_for(local_queue.get(), timeout=5.0)
            except TimeoutError:
                catch_up = await _terminal_event_from_db(report_id)
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


@app.get("/reports/{report_id}/events", dependencies=[Depends(require_api_key)])
async def report_events(report_id: UUID) -> StreamingResponse:
    """One exclusive, auto-delete queue per connection, bound to this
    report's routing key — closes on DONE/FAILED or client disconnect."""
    return StreamingResponse(progress_event_stream(report_id), media_type="text/event-stream")


async def _render_finished_report(report_id: UUID, api_key: str | None = None) -> str | None:
    async with SessionFactory() as session:
        report = await get_report(session, report_id)
        if report is None or report.status not in ("done", "partial") or report.report_json is None:
            return None
        company = await session.get(Company, report.target_company_id)
        claim_ids = collect_claim_ids(report.report_json)
        claims = await build_claims_map(session, claim_ids)
        return render_report_html(
            report.report_json,
            claims,
            company.name if company else "Report",
            report_id,
            api_key,
        )


@app.get("/history", dependencies=[Depends(require_api_key)])
async def history(api_key: Annotated[str | None, Query()] = None) -> HTMLResponse:
    """Just this one user's reports, newest first."""
    async with SessionFactory() as session:
        user = await get_or_create_cli_user(session)
        reports = await list_reports_for_user(session, user.id)
        company_ids = {r.target_company_id for r in reports}
        companies: dict[UUID, Company] = {}
        if company_ids:
            result = await session.execute(select(Company).where(Company.id.in_(company_ids)))
            companies = {c.id: c for c in result.scalars().all()}
        rows = [
            {
                "report_id": r.id,
                "company_name": companies[r.target_company_id].name
                if r.target_company_id in companies
                else "Unknown",
                "tier": r.tier,
                "status": r.status,
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for r in reports
        ]
    return HTMLResponse(render_history_html(rows, api_key))


@app.get("/reports/{report_id}/view", dependencies=[Depends(require_api_key)])
async def view_report(
    report_id: UUID, api_key: Annotated[str | None, Query()] = None
) -> HTMLResponse:
    html = await _render_finished_report(report_id, api_key)
    if html is not None:
        return HTMLResponse(html)
    async with SessionFactory() as session:
        report = await get_report(session, report_id)
        if report is None:
            raise HTTPException(status_code=404, detail="report not found")
        if report.status == "failed":
            return HTMLResponse(render_failed_html(report_id, api_key))
        company = await session.get(Company, report.target_company_id)
        company_name = company.name if company else "this company"
    return HTMLResponse(render_progress_html(report_id, api_key, company_name))


@app.get("/reports/{report_id}/pdf", dependencies=[Depends(require_api_key)])
async def report_pdf(report_id: UUID) -> Response:
    """Rendered on demand every time, not cached — reports are immutable, so
    there's nothing to invalidate. Same report_json and resolved claims as
    /view, just a print-appropriate layout."""
    async with SessionFactory() as session:
        report = await get_report(session, report_id)
        if report is None or report.status not in ("done", "partial") or report.report_json is None:
            raise HTTPException(status_code=404, detail="report not found or not finished")
        company = await session.get(Company, report.target_company_id)
        claim_ids = collect_claim_ids(report.report_json)
        claims = await build_claims_map(session, claim_ids)
        html = render_pdf_html(report.report_json, claims, company.name if company else "Report")
    pdf_bytes = await render_report_pdf(html)
    return Response(content=pdf_bytes, media_type="application/pdf")
