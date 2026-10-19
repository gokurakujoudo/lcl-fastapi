"""Observe paired request records with shared identities and safe metadata."""

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
    assert logs.count("REST API Started") == logs.count("REST API Completed") == 2
    assert (
        logs.index("REST API Started")
        < logs.index("business action")
        < logs.index("REST API Completed")
    )
    for reply, path, status in ((response, "/probe", 200), (missing, "/missing", 404)):
        identifier = reply.headers["X-Request-ID"]
        assert (
            sum(f"{identifier} |" in line and "REST API " in line for line in logs.splitlines())
            == 2
        )
        assert f"REST API Completed({status}): HTTP GET {path} from 127.0.0.1:123" in logs
    assert "duration_ms=" in logs
    events = "\n".join(line for line in logs.splitlines() if "REST API " in line)
    assert len(events.splitlines()) == 4
    assert "secret=omit" not in events and "Authorization" not in events
    assert "worker_pid=" not in events and "├─" not in events
