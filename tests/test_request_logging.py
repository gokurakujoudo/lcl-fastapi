"""Observe paired request records with shared identities and safe metadata."""

import re
from pathlib import Path

import httpx
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import LclFastAPI, get_logger


async def test_request_start_and_end_are_paired_around_business_records(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)

    @app.get("/probe")
    async def probe() -> dict[str, bool]:
        (await get_logger("business")).info("business action")
        return {"ok": True}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            response = await client.get("/probe?secret=omit", headers={"Authorization": "omit"})
            missing = await client.get("/missing")
    logs = "\n".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    assert logs.count("REST API started") == logs.count("REST API completed") == 2
    assert (
        logs.index("REST API started")
        < logs.index("business action")
        < logs.index("REST API completed")
    )
    for reply, path, status in ((response, "/probe", 200), (missing, "/missing", 404)):
        identifier = reply.headers["X-Request-ID"]
        assert logs.count(f"request_id={identifier} REST API") == 2
        assert f"method=GET path={path} status_code={status}" in logs
    assert "├─" in logs and "worker_pid=" in logs and "client=" in logs
    assert "duration_ms=" in logs
    events = "\n".join(
        re.findall(r"^[^\n]*REST API (?:started|completed)\n.*?(?=^\d{4}-|\Z)", logs, re.M | re.S)
    )
    assert "secret=omit" not in events and "Authorization" not in events
