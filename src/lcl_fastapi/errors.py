"""Typed HTTP failure callbacks with a nonrecursive default response."""

import asyncio
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from fastapi import Request
from lclang.runtime import Frame
from starlette.responses import JSONResponse, Response

from lcl_fastapi.logging import RequestLogger
from lcl_fastapi.tracebacks import exception_trace, safe_repr


@dataclass(frozen=True, slots=True)
class UncaughtExceptionContext:
    """Borrow useful request resources for the duration of an error callback.

    :param request_id: Generated request identifier, or None without a request binding.
    :param logger: Request-aware logger owned by the active worker.
    :param frame: Current configuration Frame, borrowed only during this callback.
    :param endpoint: Selected route callable, or None before route selection.
    :param service: Public service identity without control credentials.
    """

    request_id: str | None
    logger: RequestLogger
    frame: Frame
    endpoint: object
    service: Mapping[str, object]


class UncaughtExceptionHandler(Protocol):
    """Describe an asynchronous application-selected HTTP error callback."""

    async def __call__(
        self, err: Exception, request: Request, *, context: UncaughtExceptionContext
    ) -> Response:
        """Resolve an otherwise unhandled HTTP failure.

        :param err: Original escaping exception with its traceback.
        :param request: Current HTTP request and application state.
        :param context: Borrowed request and service resources.
        :returns: Response to send if response headers have not started.
        """


def report_error(message: str, context: UncaughtExceptionContext) -> None:
    """Attempt a diagnostic without allowing logger failure to replace the response.

    :param message: Already formatted error diagnostic.
    :param context: Borrowed logger and request resources.
    """
    try:
        context.logger.error(message)
    except Exception as error:
        try:
            if sys.__stderr__ is not None:
                sys.__stderr__.write(f"uncaught exception logging failed: {safe_repr(error)}\n")
        except Exception:
            pass


async def default_uncaught_exception_handler(
    err: Exception, request: Request, *, context: UncaughtExceptionContext
) -> Response:
    """Format the original exception away from the API loop and return a generic 500.

    :param err: Original escaping exception; no live handler stack is collected.
    :param request: HTTP request, whose body is never consumed for diagnostics.
    :param context: Borrowed configuration, logger and service information.
    :returns: JSON 500 without exception details.
    """
    try:
        detail = await asyncio.to_thread(exception_trace, err)
        report_error(detail, context)
    except Exception as error:
        report_error(f"default uncaught exception handler failed: {safe_repr(error)}", context)
    return JSONResponse({"detail": "Internal Server Error"}, status_code=500)


async def handle_uncaught_exception(
    handler: UncaughtExceptionHandler | None,
    err: Exception,
    request: Request,
    context: UncaughtExceptionContext,
) -> Response:
    """Invoke a custom callback once, falling back on both callback and type failures.

    :param handler: Custom callback, or None to use the framework default.
    :param err: Original HTTP exception.
    :param request: Current HTTP request.
    :param context: Resources borrowed for the duration of error handling.
    :returns: Custom response or the default JSON 500.
    """
    if handler is not None:
        try:
            response = await handler(err, request, context=context)
            if not isinstance(response, Response):
                raise TypeError("uncaught_exception_handler must return a Response")
            return response
        except Exception as error:
            report_error(f"uncaught_exception_handler failed: {safe_repr(error)}", context)
    return await default_uncaught_exception_handler(err, request, context=context)
