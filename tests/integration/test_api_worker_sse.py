"""Stage 6's literal "done when": the same report runs through the API with
live progress. Real Postgres + real RabbitMQ (both from docker compose);
network and LLM calls mocked. Exercises the full path: POST /companies/resolve
-> POST /reports (enqueue, <=200ms) -> worker picks up the job and runs the
orchestrator -> progress published to RabbitMQ -> GET /reports/{id}/events
(SSE) streams it back -> GET /reports/{id} shows the finished report.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from faststream.rabbit import RabbitBroker
from sqlalchemy import delete, select, update

import fieldbrief.worker.app as worker_app_module
from fieldbrief.agents.llm_schemas import (
    ClaimVerification,
    ExtractionResult,
    FrameworkSynthesisResult,
)
from fieldbrief.agents.progress import Stage
from fieldbrief.api.app import app as fastapi_app
from fieldbrief.api.app import job_broker, progress_event_stream
from fieldbrief.config import settings
from fieldbrief.db.models import Claim, Company, Report, ReportClaim, Source
from fieldbrief.db.repositories import create_report, get_or_create_cli_user, get_or_create_company
from fieldbrief.db.session import SessionFactory, engine
from fieldbrief.fetch.http import Fetcher
from fieldbrief.fetch.wikidata import CompanyCandidate, CompanyDetails
from fieldbrief.worker.progress import RabbitProgressSink

OVERVIEW_MARKER = "OVERVIEW_PAGE_MARKER_API_TEST"
OVERVIEW_HTML = (
    f"<html><body><article><p>{OVERVIEW_MARKER} — a long enough paragraph of filler "
    "content so that trafilatura's readability heuristics treat this as the main "
    "article body rather than boilerplate noise, since very short pages are rejected.</p>"
    "</article></body></html>"
)


async def fake_fetch(self: Fetcher, url: str) -> str:
    return OVERVIEW_HTML


def _route_llm_call(*args: object, **kwargs: object) -> SimpleNamespace:
    schema = kwargs["response_format"]
    if schema is ExtractionResult:
        result = ExtractionResult(
            claims=[
                {"field": "profile.description", "statement": "Makes widgets.", "relevance": 0.9},
            ]
        )
    elif schema is ClaimVerification:
        result = ClaimVerification(verified=True, reason="Matches snippet.")
    elif schema is FrameworkSynthesisResult:
        result = FrameworkSynthesisResult()  # no competitors/standard-tier claims -> all empty
    else:
        raise AssertionError(f"unexpected schema {schema}")
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=result.model_dump_json()))],
        usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5),
    )


@pytest.mark.asyncio
async def test_report_runs_through_api_with_live_sse_progress() -> None:
    company_domain = f"https://acme-api-{uuid.uuid4().hex[:8]}.example.com/"
    fake_details = CompanyDetails(
        qid="Q1",
        name="Acme Corp",
        aliases=[],
        domain=company_domain,
        ticker=None,
        cik=None,
        industry="widgets",
    )
    fake_candidates = [CompanyCandidate(qid="Q1", label="Acme Corp", description="widgets")]

    created_company_id: uuid.UUID | None = None

    try:
        with (
            patch.object(Fetcher, "fetch", fake_fetch),
            patch(
                "fieldbrief.api.app.search_candidates", new=AsyncMock(return_value=fake_candidates)
            ),
            patch(
                "fieldbrief.api.app.pick_company_like",
                new=AsyncMock(return_value=fake_candidates[0]),
            ),
            patch(
                "fieldbrief.api.app.get_company_details", new=AsyncMock(return_value=fake_details)
            ),
            patch(
                "fieldbrief.llm.gateway.litellm.acompletion",
                new=AsyncMock(side_effect=_route_llm_call),
            ),
            patch("fieldbrief.llm.gateway.litellm.completion_cost", return_value=0.001),
        ):
            await worker_app_module.broker.start()
            await job_broker.start()
            try:
                transport = httpx.ASGITransport(app=fastapi_app)
                async with httpx.AsyncClient(
                    transport=transport,
                    base_url="http://test",
                    headers={"X-API-Key": settings.api_key},
                ) as client:
                    resolve_resp = await client.post("/companies/resolve", json={"query": "Acme"})
                    assert resolve_resp.status_code == 200
                    company_id = resolve_resp.json()["id"]
                    created_company_id = uuid.UUID(company_id)

                    create_resp = await client.post(
                        "/reports",
                        json={"company_id": company_id, "tier": "quick", "competitor_count": 0},
                    )
                    assert create_resp.status_code == 200
                    report_id = create_resp.json()["report_id"]

                    stages: list[str] = []
                    async with client.stream("GET", f"/reports/{report_id}/events") as events_resp:
                        assert events_resp.status_code == 200
                        async for line in events_resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = json.loads(line[len("data: ") :])
                            stages.append(payload["stage"])
                            if payload["stage"] in ("done", "failed"):
                                break

                    # Mocked work is near-instant, so the worker may finish before
                    # this SSE connection's queue is even bound — the real
                    # guarantee under test is "never hangs, eventually terminal"
                    # (via the DB catch-up fallback), not "sees every live event."
                    assert len(stages) >= 1
                    assert stages[-1] == "done"

                    final = await client.get(f"/reports/{report_id}")
                    assert final.status_code == 200
                    body = final.json()
                    assert body["status"] in ("done", "partial")
                    assert body["report_json"]["companies"][0]["name"] == "Acme Corp"

                    view_resp = await client.get(f"/reports/{report_id}/view")
                    assert view_resp.status_code == 200
                    assert "Acme Corp" in view_resp.text
                    assert "Makes widgets." in view_resp.text

                    # refresh: a new, separate report (design.md §4: immutable,
                    # never mutated in place), linked via parent_report_id.
                    refresh_resp = await client.post(f"/reports/{report_id}/refresh")
                    assert refresh_resp.status_code == 200
                    refreshed_id = refresh_resp.json()["report_id"]
                    assert refreshed_id != report_id

                    refreshed_stages: list[str] = []
                    async with client.stream(
                        "GET", f"/reports/{refreshed_id}/events"
                    ) as refreshed_events:
                        async for line in refreshed_events.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            payload = json.loads(line[len("data: ") :])
                            refreshed_stages.append(payload["stage"])
                            if payload["stage"] in ("done", "failed"):
                                break
                    assert refreshed_stages[-1] == "done"

                    refreshed_report = await client.get(f"/reports/{refreshed_id}")
                    assert refreshed_report.status_code == 200

                    # idempotency: same key returns the same report, doesn't create another
                    dup_resp = await client.post(
                        "/reports",
                        json={"company_id": company_id, "tier": "quick", "competitor_count": 0},
                        headers={"Idempotency-Key": "test-key-1"},
                    )
                    dup_resp2 = await client.post(
                        "/reports",
                        json={"company_id": company_id, "tier": "quick", "competitor_count": 0},
                        headers={"Idempotency-Key": "test-key-1"},
                    )
                    assert dup_resp.json()["report_id"] == dup_resp2.json()["report_id"]
            finally:
                await job_broker.stop()
                await worker_app_module.broker.stop()
    finally:
        async with SessionFactory() as cleanup_session:
            if created_company_id is not None:
                reports_result = await cleanup_session.execute(
                    select(Report.id).where(Report.target_company_id == created_company_id)
                )
                report_ids = [row[0] for row in reports_result.all()]
                # A refresh's new report points back at the original via
                # parent_report_id — null that out first or deleting the
                # parent while a child still references it violates the FK.
                for rid in report_ids:
                    await cleanup_session.execute(
                        update(Report).where(Report.id == rid).values(parent_report_id=None)
                    )
                for rid in report_ids:
                    await cleanup_session.execute(
                        delete(ReportClaim).where(ReportClaim.report_id == rid)
                    )
                    await cleanup_session.execute(delete(Report).where(Report.id == rid))
                claims_result = await cleanup_session.execute(
                    select(Claim.source_id).where(Claim.company_id == created_company_id)
                )
                source_ids = {row[0] for row in claims_result.all()}
                await cleanup_session.execute(
                    delete(Claim).where(Claim.company_id == created_company_id)
                )
                if source_ids:
                    await cleanup_session.execute(delete(Source).where(Source.id.in_(source_ids)))
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_view_shows_progress_shell_for_an_unfinished_report() -> None:
    """The other branch of /view — a report still running gets the SSE-driven
    progress shell, not a 404 or a half-rendered report."""
    company_domain = f"https://pending-{uuid.uuid4().hex[:8]}.example.com/"
    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None

    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company = await get_or_create_company(session, name="Pending Co", domain=company_domain)
            created_company_id = company.id
            report = await create_report(
                session,
                user_id=user.id,
                target_company_id=company.id,
                tier="quick",
                status="running",
            )
            created_report_id = report.id
            await session.commit()

            transport = httpx.ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": settings.api_key},
            ) as client:
                resp = await client.get(f"/reports/{report.id}/view")
                assert resp.status_code == 200
                assert "Analyzing Pending Co" in resp.text
                assert str(report.id) in resp.text
                assert "/events" in resp.text
    finally:
        async with SessionFactory() as cleanup_session:
            if created_report_id is not None:
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
            if created_company_id is not None:
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_view_shows_failed_page_not_an_infinite_shell_loop() -> None:
    """Stage 9 bug fix: a failed report used to fall through to the same
    progress shell forever (its location.reload() on a terminal stage has
    nowhere finished to land for status="failed", so it just re-opens the
    shell, gets "failed" again via the DB catch-up fallback, and reloads
    again) — a real user hit this live. /view must now render a stable
    terminal page instead."""
    company_domain = f"https://failed-{uuid.uuid4().hex[:8]}.example.com/"
    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None

    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company = await get_or_create_company(session, name="Failed Co", domain=company_domain)
            created_company_id = company.id
            report = await create_report(
                session,
                user_id=user.id,
                target_company_id=company.id,
                tier="quick",
                status="failed",
            )
            created_report_id = report.id
            await session.commit()

            transport = httpx.ASGITransport(app=fastapi_app)
            async with httpx.AsyncClient(
                transport=transport,
                base_url="http://test",
                headers={"X-API-Key": settings.api_key},
            ) as client:
                resp = await client.get(f"/reports/{report.id}/view")
                assert resp.status_code == 200
                assert "failed to generate" in resp.text
                assert str(report.id) in resp.text
                # not the progress shell — no SSE connection to loop on
                assert "EventSource" not in resp.text
                assert "/refresh" in resp.text
    finally:
        async with SessionFactory() as cleanup_session:
            if created_report_id is not None:
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
            if created_company_id is not None:
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_progress_event_stream_survives_a_timeout_poll_before_a_message_arrives() -> None:
    """Regression test for a real bug hit live: asyncio.wait_for cancelling
    aio_pika's own queue iterator poisons it — the next __anext__() raises a
    bare StopAsyncIteration, which PEP 479 turns into a RuntimeError that
    kills the whole SSE response, so the browser's EventSource reconnects
    into the same crash forever ("connection lost — retrying," for any real
    report, every time, since the mocked round-trip test above is
    near-instant by design and never hits the 5s poll timeout at all).

    Iterates progress_event_stream() directly rather than going through
    FastAPI/httpx: httpx's ASGITransport buffers an entire StreamingResponse
    until its generator fully *completes* before delivering anything to the
    client (confirmed by hand — a real browser against real uvicorn flushes
    each chunk immediately, which is why the original bug was only ever
    visible live), so it can't exercise a stream that's still open several
    seconds in. No LLM calls — publishes one progress event directly via
    RabbitProgressSink, same as the worker does, after deliberately waiting
    past the SSE loop's 5s timeout so a real timeout-then-continue cycle
    happens before the message arrives."""
    company_domain = f"https://slow-{uuid.uuid4().hex[:8]}.example.com/"
    created_company_id: uuid.UUID | None = None
    created_report_id: uuid.UUID | None = None

    try:
        async with SessionFactory() as session:
            user = await get_or_create_cli_user(session)
            company = await get_or_create_company(session, name="Slow Co", domain=company_domain)
            created_company_id = company.id
            report = await create_report(
                session,
                user_id=user.id,
                target_company_id=company.id,
                tier="quick",
                status="running",
            )
            created_report_id = report.id
            await session.commit()

        broker = RabbitBroker(settings.rabbitmq_url)
        await broker.start()
        try:

            async def publish_after_delay() -> None:
                await asyncio.sleep(6.0)  # past the SSE loop's 5s poll timeout
                sink = RabbitProgressSink(report_id=str(created_report_id), broker=broker)
                await sink.emit(Stage.RESEARCHING, "still going")

            async def read_one_event() -> str:
                async for chunk in progress_event_stream(created_report_id):
                    assert chunk.startswith("data: ")
                    payload = json.loads(chunk[len("data: ") :].strip())
                    stage: str = payload["stage"]
                    return stage  # one real event proves the stream survived the timeout
                raise AssertionError("stream ended with no event")

            results = await asyncio.wait_for(
                asyncio.gather(read_one_event(), publish_after_delay()), timeout=15.0
            )
            assert results[0] == "researching"
        finally:
            await broker.stop()
    finally:
        async with SessionFactory() as cleanup_session:
            if created_report_id is not None:
                await cleanup_session.execute(delete(Report).where(Report.id == created_report_id))
            if created_company_id is not None:
                await cleanup_session.execute(
                    delete(Company).where(Company.id == created_company_id)
                )
            await cleanup_session.commit()
        await engine.dispose()


@pytest.mark.asyncio
async def test_home_page_renders_the_analyze_form() -> None:
    """Stage 9: the browser-only entry point. No DB/auth needed — it's a
    static form; the API calls it makes still go through require_api_key."""
    transport = httpx.ASGITransport(app=fastapi_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/")
        assert resp.status_code == 200
        assert "FieldBrief" in resp.text
        assert 'id="analyze-form"' in resp.text
        assert "/companies/resolve" in resp.text
        assert "/reports" in resp.text
