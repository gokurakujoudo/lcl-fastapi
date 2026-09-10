"""Task-local request and configuration bindings for active worker scopes."""

from contextvars import ContextVar
from dataclasses import dataclass

from lclang.runtime import Frame


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Describe one HTTP request without mutable shared state.

    :param request_id: Decimal server-generated Snowflake identifier.
    :param method: HTTP request method.
    :param path: Request path without query parameters.
    :param started_ns: Monotonic start time in nanoseconds.
    """

    request_id: str
    method: str
    path: str
    started_ns: int


# Task-local bindings have no units; None means no active framework scope.
REQUEST_CONTEXT: ContextVar[RequestContext | None] = ContextVar("lcl_request", default=None)
CONFIG_FRAME: ContextVar[Frame | None] = ContextVar("lcl_config", default=None)


def get_request_context() -> RequestContext | None:
    """Read the current request binding without creating state.

    :returns: Immutable request context, or None outside an HTTP request.
    """
    return REQUEST_CONTEXT.get()


async def get_config(key: str) -> object:
    """Evaluate a business setting in the current worker Frame.

    :param key: Qualified LCL configuration name.
    :returns: Value resolved by the worker's existing LCL Frame.
    :raises RuntimeError: If called outside the worker lifespan or request scope.
    :raises LclNameError: If the configuration name is absent.
    :raises LclEvaluationError: If the configured expression cannot be evaluated.
    """
    frame = CONFIG_FRAME.get()
    if frame is None:
        raise RuntimeError("get_config requires an active worker lifespan or request")
    return await frame.get(key)
