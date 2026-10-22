"""Fast auth checks — no RabbitMQ/Postgres needed, since the X-API-Key
dependency rejects before any route body (or the job_broker it needs) runs."""

import httpx
import pytest

from fieldbrief.api.app import app


@pytest.mark.asyncio
async def test_missing_api_key_is_rejected() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/companies/resolve", json={"query": "Acme"})
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_wrong_api_key_is_rejected() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers={"X-API-Key": "wrong"}
    ) as client:
        response = await client.get("/reports/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 401
