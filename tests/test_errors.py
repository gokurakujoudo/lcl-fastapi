"""Verify HTTP error responses, callback failures and native exception diagnostics."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator, Awaitable
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from lclang.error import LclErrorGroup
from lclang.lang import Frame, Module, ModuleName
from starlette.responses import JSONResponse, Response, StreamingResponse
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import (
    LclFastAPI,
    UncaughtExceptionContext,
    UncaughtExceptionHandler,
    default_uncaught_exception_handler,
    get_config,
)
from lcl_fastapi.errors import handle_uncaught_exception, report_error
from lcl_fastapi.logging import RequestLogger
from lcl_fastapi.overrides import override_scope


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("synchronous", [False, True])
def test_default_route_failure_has_id_and_native_traceback(
    configuration: Path, observed_runtime: ObservedRuntime, debug: bool, synchronous: bool
) -> None:
    app = LclFastAPI(config_path=configuration, debug=debug)
    router = APIRouter()

    def inner(quantity: int, *tags: str, currency: str, **options: object) -> None:
        raise ValueError("catalog failure")

    def sync_route(quantity: int = 3) -> None:
        inner(quantity, "sale", currency="USD", enabled=True)

    async def async_route(quantity: int = 3) -> None:
        sync_route(quantity)

    router.add_api_route("/broken", sync_route if synchronous else async_route)
    app.include_router(router)
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/broken")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}
        request_id = response.headers["X-Request-ID"]
    logs = "".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    for text in (
        "Uncaught Exception in HTTP",
        "catalog failure",
        "Python traceback:",
        'raise ValueError("catalog failure")',
        f"{request_id} |",
    ):
        assert text in logs
    assert "\tquantity: 3" not in logs
    assert "\toptions:" not in logs
    assert logs.count("REST API Completed(500): HTTP GET /broken") == 1
    assert "in default_uncaught_exception_handler" not in logs
    portable = logs.replace("\\", "/")
    assert "/fastapi/" not in portable and "/starlette/" not in portable


@pytest.mark.parametrize("failure", [None, "raise", "invalid"])
@pytest.mark.parametrize("style", ["async", "sync", "awaitable"])
def test_custom_callback_context_and_fallback_order(
    configuration: Path, observed_runtime: ObservedRuntime, failure: str | None, style: str
) -> None:
    contexts: list[UncaughtExceptionContext] = []
    request_tasks: list[asyncio.Task[object] | None] = []

    def synchronous(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        contexts.append(context)
        assert isinstance(err, ValueError)
        assert request.app is app
        assert context.endpoint is broken
        assert context.service["worker_pid"]
        assert asyncio.current_task() is request_tasks[0]
        if failure == "raise":
            raise RuntimeError("callback failed deliberately")
        if failure == "invalid":
            return cast(Response, None)
        return JSONResponse({"request_id": context.request_id}, status_code=502)

    async def asynchronous(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        assert await context.frame.get("app.name") == "test-service"
        return synchronous(err, request, context=context)

    def awaitable(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Awaitable[Response]:
        return asynchronous(err, request, context=context)

    callbacks: dict[str, UncaughtExceptionHandler] = {
        "async": asynchronous,
        "sync": synchronous,
        "awaitable": awaitable,
    }
    callback = callbacks[style]
    app = LclFastAPI(config_path=configuration, uncaught_exception_handler=callback)

    @app.get("/broken")
    async def broken() -> None:
        request_tasks.append(asyncio.current_task())
        raise ValueError("original error")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/broken")
        assert len(contexts) == 1
        assert response.headers["X-Request-ID"] == contexts[0].request_id
        assert response.status_code == (502 if failure is None else 500)
        if failure is None:
            assert response.json() == {"request_id": contexts[0].request_id}
    logs = "".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    if failure is not None:
        assert logs.index("uncaught_exception_handler failed:") < logs.index("original error")
        assert response.json() == {"detail": "Internal Server Error"}
    assert (
        sum("REST API " in line and "HTTP GET /broken " in line for line in logs.splitlines()) == 2
    )


def test_specific_handlers_validation_and_dependencies(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)

    @app.exception_handler(KeyError)
    async def known(request: Request, err: Exception) -> Response:
        return Response(status_code=409)

    @app.get("/known")
    async def known_route() -> None:
        raise KeyError("handled")

    @app.get("/intentional")
    async def intentional() -> None:
        raise HTTPException(404, "not here")

    async def dependency(number: int) -> None:
        raise RuntimeError("dependency failure")

    @app.get("/dependency", dependencies=[Depends(dependency)])
    async def dependent() -> None:
        pytest.fail("dependency should fail first")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/known").status_code == 409
        assert client.get("/intentional").status_code == 404
        assert client.get("/dependency?number=no").status_code == 422
        assert client.get("/dependency?number=2").status_code == 500


@pytest.mark.parametrize("key", [Exception, 500])
@pytest.mark.parametrize("debug", [False, True])
def test_native_generic_handler_and_explicit_conflict(
    configuration: Path, observed_runtime: ObservedRuntime, key: type[Exception] | int, debug: bool
) -> None:
    async def native(request: Request, err: Exception) -> Response:
        return Response(status_code=503)

    app = LclFastAPI(config_path=configuration, exception_handlers={key: native}, debug=debug)

    @app.get("/broken")
    async def broken() -> None:
        raise ValueError("original")

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/broken").status_code == 503
    app.uncaught_exception_handler = default_uncaught_exception_handler
    with pytest.raises(ValueError, match="conflicts"):
        app.build_middleware_stack()


def test_upstream_middleware_shape_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FastAPI, "build_middleware_stack", lambda self: object())
    with pytest.raises(RuntimeError, match="ServerErrorMiddleware"):
        LclFastAPI().build_middleware_stack()


def test_streaming_failure_preserves_started_response(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)

    async def stream() -> AsyncIterator[bytes]:
        yield b"started"
        raise ValueError("stream failed")

    @app.get("/stream")
    async def streaming() -> Response:
        return StreamingResponse(stream())

    with TestClient(app) as client, pytest.raises(ValueError, match="stream failed"):
        client.get("/stream")


@pytest.fixture
async def error_context() -> AsyncIterator[UncaughtExceptionContext]:
    async with Frame(Module(ModuleName("errors"), {})) as frame:
        yield UncaughtExceptionContext(None, cast(RequestLogger, Mock()), frame, None, {})


async def test_default_logging_failure_and_cancellation(
    error_context: UncaughtExceptionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = Request({"type": "http"})

    cast(Mock, error_context.logger.error).side_effect = RuntimeError("producer failure")
    result = await default_uncaught_exception_handler(ValueError(), request, context=error_context)
    assert result.status_code == 500
    cast(Mock, error_context.logger.error).assert_called_once()

    def cancelled(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await handle_uncaught_exception(cancelled, ValueError(), request, error_context)

    async def cancelled_awaitable(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await handle_uncaught_exception(cancelled_awaitable, ValueError(), request, error_context)


@pytest.mark.parametrize("stderr", [None, "working", "broken"])
def test_logging_failure_is_nonrecursive(
    stderr: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = cast(UncaughtExceptionContext, Mock())
    cast(Mock, context.logger.error).side_effect = RuntimeError("writer closed")
    stream = Mock()
    if stderr == "broken":
        stream.write.side_effect = OSError("stderr closed")
    monkeypatch.setattr(sys, "__stderr__", None if stderr is None else stream)
    report_error("original diagnostic", context, ValueError("original failure"))
    if stderr is not None:
        stream.write.assert_called_once()


@pytest.mark.parametrize("style", ["compact", "full"])
@pytest.mark.parametrize("verbose", [False, True])
def test_lcl_failure_uses_native_tree_masks_and_traceback_policy(
    configuration: Path, observed_runtime: ObservedRuntime, style: str, verbose: bool
) -> None:
    with configuration.open("a", encoding="utf-8") as source:
        source.write(
            f'logger.traceback_style: "{style}"\n'
            'business.secret!: "do-not-display-this"\n'
            "business.failure: [business.secret, 1 / 0]\n"
        )
    app = LclFastAPI(config_path=configuration)

    @app.get("/native")
    async def native() -> object:
        return await get_config("business.failure")

    with (
        override_scope({"__verbose__": verbose}),
        TestClient(app, raise_server_exceptions=False) as client,
    ):
        response = client.get("/native")
        assert response.status_code == 500
        assert response.json() == {"detail": "Internal Server Error"}
        request_id = response.headers["X-Request-ID"]
    logs = "".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    assert "business.failure" in logs and "LCL" in logs and "Python traceback:" in logs
    assert "service.lclcfg" in logs and "*masked*" in logs
    assert "do-not-display-this" not in logs
    assert f"{request_id} |" in logs
    assert ("binding_evaluation.py" in logs) == (style == "full")
    assert ("TRACE" in logs) is verbose


@pytest.mark.parametrize("grouped", [False, True])
def test_native_reports_retain_causes_and_all_group_members(
    configuration: Path, observed_runtime: ObservedRuntime, grouped: bool
) -> None:
    app = LclFastAPI(config_path=configuration)

    @app.get("/chain")
    async def chain() -> None:
        try:
            raise ValueError("original cause")
        except ValueError as cause:
            if grouped:
                raise LclErrorGroup(
                    "combined failure", [RuntimeError("first member"), OSError("second member")]
                ) from cause
            raise RuntimeError("outer failure") from cause

    with TestClient(app, raise_server_exceptions=False) as client:
        assert client.get("/chain").status_code == 500
    logs = "".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    assert "original cause" in logs
    if grouped:
        assert "combined failure" in logs and "first member" in logs and "second member" in logs
    else:
        assert "outer failure" in logs


async def test_direct_callback_without_request_binding(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        response = await app.uncaught_response(Request({"type": "http"}), ValueError("direct"))
        assert response.status_code == 500
    assert not logging.getLogger("uvicorn.access").disabled
