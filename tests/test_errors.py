"""Verify HTTP error responses, callback failures and original argument diagnostics."""

import asyncio
import logging
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import cast
from unittest.mock import Mock

import pytest
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
from lclang.runtime import Frame, Module
from lclang.types import ModuleName
from starlette.responses import JSONResponse, Response, StreamingResponse
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

import lcl_fastapi.errors as error_module
from lcl_fastapi import LclFastAPI, UncaughtExceptionContext, default_uncaught_exception_handler
from lcl_fastapi.errors import handle_uncaught_exception, report_error
from lcl_fastapi.logging import RequestLogger
from lcl_fastapi.tracebacks import exception_trace, safe_repr, traceback_lines


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("synchronous", [False, True])
def test_default_route_failure_has_id_and_arguments(
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
        "\tquantity: 3",
        "\ttags: ('sale',)",
        "\tcurrency: 'USD'",
        "\toptions: {'enabled': True}",
        'raise ValueError("catalog failure")',
        f"request_id={request_id}",
    ):
        assert text in logs
    assert logs.count("method=GET path=/broken status_code=500") == 1
    assert "in default_uncaught_exception_handler" not in logs


@pytest.mark.parametrize("failure", [None, "raise", "invalid"])
def test_custom_callback_context_and_fallback_order(
    configuration: Path, observed_runtime: ObservedRuntime, failure: str | None
) -> None:
    contexts: list[UncaughtExceptionContext] = []

    async def callback(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        contexts.append(context)
        assert isinstance(err, ValueError)
        assert request.app is app
        assert context.endpoint is broken
        assert context.service["worker_pid"]
        assert await context.frame.get("app.name") == "test-service"
        if failure == "raise":
            raise RuntimeError("callback failed deliberately")
        if failure == "invalid":
            return cast(Response, None)
        return JSONResponse({"request_id": context.request_id}, status_code=502)

    app = LclFastAPI(config_path=configuration, uncaught_exception_handler=callback)

    @app.get("/broken")
    async def broken() -> None:
        raise ValueError("original error")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/broken")
        assert len(contexts) == 1
        assert response.headers["X-Request-ID"] == contexts[0].request_id
        assert response.status_code == (502 if failure is None else 500)
    logs = "".join(p.read_text(encoding="utf-8") for p in configuration.parent.glob("logs/*.log"))
    if failure is not None:
        assert logs.index("uncaught_exception_handler failed:") < logs.index("original error")
        assert response.json() == {"detail": "Internal Server Error"}


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
def test_native_generic_handler_and_explicit_conflict(
    configuration: Path, observed_runtime: ObservedRuntime, key: type[Exception] | int
) -> None:
    async def native(request: Request, err: Exception) -> Response:
        return Response(status_code=503)

    app = LclFastAPI(config_path=configuration, exception_handlers={key: native})

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


async def test_default_formatter_failure_and_cancellation(
    error_context: UncaughtExceptionContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = Request({"type": "http"})

    def failed(error: BaseException) -> str:
        raise RuntimeError("formatter failure")

    monkeypatch.setattr(error_module, "exception_trace", failed)
    result = await default_uncaught_exception_handler(ValueError(), request, context=error_context)
    assert result.status_code == 500
    cast(Mock, error_context.logger.error).assert_called_once()

    async def cancelled(
        err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await handle_uncaught_exception(cancelled, ValueError(), request, error_context)


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
    report_error("original diagnostic", context)
    if stderr is not None:
        stream.write.assert_called_once()


def test_traceback_chains_groups_missing_arguments_and_repr() -> None:
    class BrokenRepresentation:
        def __repr__(self) -> str:
            raise ValueError("repr failure")

    assert safe_repr(BrokenRepresentation()) == "<repr unavailable: BrokenRepresentation>"
    assert traceback_lines(None) == []

    def missing(argument: str) -> None:
        del argument
        raise ValueError("missing")

    try:
        try:
            missing("original")
        except ValueError as error:
            raise RuntimeError("outer") from error
    except RuntimeError as error:
        rendered = exception_trace(error)
    assert "direct cause" in rendered and "\targument: <unavailable>" in rendered
    try:
        try:
            raise ValueError("inner")
        except ValueError:
            raise RuntimeError("context") from None
    except RuntimeError as error:
        assert "During handling" not in exception_trace(error)
        error.__suppress_context__ = False
        assert "During handling" in exception_trace(error)
    cycle = ValueError("cycle")
    cycle.__cause__ = cycle
    assert "already shown" in exception_trace(ExceptionGroup("group", [cycle, cycle]))
    code = compile("raise ValueError('dynamic')", "<unavailable-source>", "exec")
    with pytest.raises(ValueError) as caught:
        exec(code)
    assert "<source unavailable>" in exception_trace(caught.value)


async def test_direct_callback_without_request_binding(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        response = await app.uncaught_response(Request({"type": "http"}), ValueError("direct"))
        assert response.status_code == 500
    assert not logging.getLogger("uvicorn.access").disabled
