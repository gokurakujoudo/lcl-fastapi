"""Exercise native compact layouts and bounded request identity snapshots."""

import asyncio
import copy
import logging
import re
from pathlib import Path

import httpx
import pytest
from lclang.error import LclValidationError
from lclang.logger.runtime_registry import current_runtime
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import LclFastAPI, get_config, get_logger, get_request_context
from lcl_fastapi.context import REQUEST_CONTEXT, RequestContext
from lcl_fastapi.log_context import REQUEST_SCOPE, http_request_description, log_metadata


async def test_request_names_compact_prefixes_and_exact_http_messages(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    records: list[logging.LogRecord] = []

    class Capture(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            records.append(copy.copy(record))
            return True

    app = LclFastAPI(config_path=configuration)

    @app.get("/orders/42")
    async def order() -> dict[str, str]:
        context = get_request_context()
        assert context is not None
        assert logger.name == context.request_id
        logger.info("ordinary business")
        logger.info("compact business", compact=True)
        logger.log_event("compact event", compact=True, order_id=42)
        return {"id": context.request_id}

    @app.get("/failure")
    async def failure() -> None:
        raise ValueError("review failure")

    async with app.router.lifespan_context(app):
        logger = await get_logger("orders")
        assert logger.name == "orders"
        current_runtime().handler.addFilter(Capture())
        names = set(logging.Logger.manager.loggerDict)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=app, client=("127.0.0.1", 65311), raise_app_exceptions=False
            ),
            base_url="http://local",
        ) as client:
            replies = await asyncio.gather(*(client.get("/orders/42") for _ in range(20)))
            failed = await client.get("/failure")
        assert logger.name == "orders"
        ids = {reply.json()["id"] for reply in replies}
        assert len(ids) == 20
        assert not (ids & set(logging.Logger.manager.loggerDict))
        assert not {
            name for name in set(logging.Logger.manager.loggerDict) - names if name.isdigit()
        }
    assert failed.status_code == 500
    identifier = replies[0].headers["X-Request-ID"]
    selected = [record for record in records if record.name == identifier]
    assert any(
        record.getMessage() == "REST API Started: HTTP GET /orders/42 from 127.0.0.1:65311"
        for record in selected
    )
    assert any(
        re.fullmatch(
            r"REST API Completed\(200\): HTTP GET /orders/42 from 127\.0\.0\.1:65311 "
            r"\[duration_ms=\d+\.\d{3}\]",
            record.getMessage(),
        )
        for record in selected
    )
    assert all(
        getattr(record, "lcl_source_name", None) in {"orders", "lcl_fastapi"} for record in selected
    )
    assert any(getattr(record, "api_handler", "").endswith(".order") for record in selected)
    logs = "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.glob("logs/*.log")
    )
    assert f"{identifier} | REST API Started:" in logs
    assert f"{identifier} | orders compact business" in logs
    assert f"{identifier} | orders compact event\n    order_id: 42" in logs
    ordinary = next(
        line for line in logs.splitlines() if identifier in line and "ordinary business" in line
    )
    assert ordinary.count(identifier) == 1 and "test_request_compact.py:" in ordinary
    compact = next(
        line for line in logs.splitlines() if identifier in line and "compact business" in line
    )
    assert "test_request_compact.py:" not in compact
    assert (
        f"{failed.headers['X-Request-ID']} | "
        "Uncaught Exception in HTTP GET /failure from 127.0.0.1:65311:\n" in logs
    )
    assert logs.count("Uncaught Exception in HTTP") == 1
    assert "method=GET" not in logs and "handler=" not in logs


@pytest.mark.parametrize("style", ["compact", "full"])
async def test_framework_python_frames_follow_traceback_policy(
    configuration: Path, observed_runtime: ObservedRuntime, style: str
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write(f'logger.traceback_style: "{style}"\nbusiness.failure: 1 / 0\n')
    app = LclFastAPI(config_path=configuration)

    @app.get("/failure")
    async def failure() -> None:
        await get_config("business.failure")

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://local",
        ) as client:
            assert (await client.get("/failure")).status_code == 500
    logs = "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.glob("logs/*.log")
    )
    trace = logs.split("Python traceback:", 1)[1].replace("\\", "/")
    assert "test_request_compact.py" in trace
    assert ("/lcl_fastapi/context.py" in trace) is (style == "full")


@pytest.mark.parametrize("client,expected", [(None, "unknown"), (("::1", 42), "[::1]:42")])
def test_request_description_escapes_controls_and_formats_missing_or_ipv6_clients(
    client: tuple[str, int] | None, expected: str
) -> None:
    assert http_request_description({"method": "GET", "path": "/a\nb", "client": client}) == (
        f"HTTP GET /a\\nb from {expected}"
    )


async def test_source_filters_and_explicit_formats_survive_request_names_and_worker_routing(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write(
            'logger.format: "%(name)s | %(lcl_source_name)s | %(message)s"\n'
            'logger.file.service.logger_names: ["orders.allowed"]\n'
            "background_worker.off.enabled: False\n"
        )
    app = LclFastAPI(config_path=configuration, background_workers={"off": lambda ctx: None})

    @app.get("/work")
    async def work() -> None:
        allowed = await get_logger("orders.allowed")
        allowed.info("selected business")
        allowed.info("selected compact", compact=True)
        (await get_logger("orders.denied")).info("denied business")
        for method in (allowed.info, allowed.log_event):
            with pytest.raises(LclValidationError):
                method("invalid", compact=1)  # type: ignore[arg-type]

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            response = await client.get("/work")
            assert response.status_code == 200
    logs = "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.glob("logs/*.log")
    )
    identifier = response.headers["X-Request-ID"]
    source = "lcl_fastapi.api.orders.allowed"
    assert f"{identifier} | {source} | orders.allowed selected business" in logs
    assert f"{identifier} | {source} | {identifier} | orders.allowed selected compact" in logs
    assert "denied business" not in logs and "invalid" not in logs


async def test_logger_identity_supports_an_inherited_context_and_pending_id_allocation(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        logger = await get_logger("business")
        context_token = REQUEST_CONTEXT.set(RequestContext("child-id", "PATCH", "/child", 0))
        try:
            assert logger.name == "child-id"
            assert log_metadata() == {
                "request_id": "child-id",
                "request_method": "PATCH",
                "request_path": "/child",
            }
        finally:
            REQUEST_CONTEXT.reset(context_token)
        scope_token = REQUEST_SCOPE.set({"type": "http", "method": "GET", "path": "/pending"})
        try:
            assert logger.name == "unavailable"
            assert log_metadata()["request_id"] == "unavailable"
        finally:
            REQUEST_SCOPE.reset(scope_token)
        assert logger.name == "business"
