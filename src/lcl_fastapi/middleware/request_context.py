"""Wrap the complete ASGI error stack with request identity and access logs."""

import os
from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter_ns

from lclang.runtime import Frame
from lclang.utils import SnowflakeGenerator
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from lcl_fastapi.context import CONFIG_FRAME, REQUEST_CONTEXT, RequestContext
from lcl_fastapi.logging import RequestLogger


@dataclass(frozen=True, slots=True)
class RequestRuntime:
    """Borrow resources owned by the application lifespan.

    :param frame: Worker Frame used by business configuration lookups.
    :param generator: Worker-long upstream Snowflake generator.
    :param header: Lowercase ASCII request-ID response header name.
    :param logger: Worker access logger without cached request identity.
    """

    frame: Frame
    generator: SnowflakeGenerator
    header: bytes
    logger: RequestLogger


class RequestContextMiddleware:
    """Assign HTTP request IDs or explicitly reject upstream allocation failures.

    :param app: Complete downstream ASGI application, including its error handler.
    :param runtime: Function returning resources for the current worker lifespan.
    """

    def __init__(self, app: ASGIApp, runtime: Callable[[], RequestRuntime]) -> None:
        """Retain downstream behavior without starting runtime resources.

        :param app: Downstream ASGI callable.
        :param runtime: Worker resource accessor.
        """
        self.app = app
        self.runtime = runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Propagate response headers and emit exactly one final access record.

        :param scope: ASGI connection scope.
        :param receive: Incoming ASGI messages.
        :param send: Outgoing ASGI messages.
        :raises BaseException: Propagates application errors after logging and reset.
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        runtime = self.runtime()
        started_ns = perf_counter_ns()
        context_token = REQUEST_CONTEXT.set(None)
        frame_token = CONFIG_FRAME.set(runtime.frame)
        scope.setdefault("state", {}).pop("request_id", None)
        status_code = 500
        failure_detail = ""

        async def send_response(message: Message) -> None:
            """Replace the configured identity header on the response start.

            :param message: Downstream ASGI response message.
            """
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != runtime.header
                ]
                message = {
                    **message,
                    "headers": [*headers, (runtime.header, context.request_id.encode("ascii"))],
                }
            await send(message)

        try:
            try:
                request_id = str(runtime.generator.next_id())
            except (RuntimeError, OverflowError) as error:
                status_code = 503
                failure_detail = (
                    f"request_id=unavailable id_generation_error={type(error).__name__} "
                )
                await JSONResponse(
                    {"detail": "Request ID generation unavailable"},
                    status_code=503,
                )(scope, receive, send)
                return
            context = RequestContext(request_id, scope["method"], scope["path"], started_ns)
            REQUEST_CONTEXT.set(context)
            scope["state"]["request_id"] = request_id
            await self.app(scope, receive, send_response)
        finally:
            try:
                runtime.logger.info(
                    "%smethod=%s path=%s status_code=%s duration_ms=%.3f worker_pid=%s",
                    failure_detail,
                    scope["method"],
                    scope["path"],
                    status_code,
                    (perf_counter_ns() - started_ns) / 1_000_000,
                    os.getpid(),
                )
            finally:
                CONFIG_FRAME.reset(frame_token)
                REQUEST_CONTEXT.reset(context_token)
