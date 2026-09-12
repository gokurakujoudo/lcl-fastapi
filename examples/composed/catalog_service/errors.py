"""Demonstrate custom, default and failing HTTP error callbacks."""

from fastapi import Request
from starlette.responses import JSONResponse, Response

from lcl_fastapi import UncaughtExceptionContext, default_uncaught_exception_handler


async def catalog_error(
    err: Exception, request: Request, *, context: UncaughtExceptionContext
) -> Response:
    """Select a demonstration response from the requested error endpoint.

    :param err: Original route error.
    :param request: Request selecting the demonstration mode.
    :param context: Borrowed logger, Frame and request identity.
    :returns: Custom 502 or the default logged 500 response.
    :raises RuntimeError: Deliberately exercises the framework callback fallback.
    """
    if request.url.path.endswith("/custom"):
        context.logger.error("catalog custom error: %s", err)
        return JSONResponse({"error": "catalog unavailable", "request_id": context.request_id}, 502)
    if request.url.path.endswith("/callback"):
        raise RuntimeError("demonstration callback failure")
    return await default_uncaught_exception_handler(err, request, context=context)
