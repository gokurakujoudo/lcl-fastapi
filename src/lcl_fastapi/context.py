"""Task-local request and configuration bindings for active worker scopes."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from lclang.runtime import Frame, Module
from lclang.types import ModuleName


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


@asynccontextmanager
async def use_lcl_frame(
    module: Module | None = None, *, values: dict[str, object] | None = None
) -> AsyncIterator[Frame]:
    """Borrow the active Frame as parent of a task-local configuration scope.

    :param module: Local LCL definitions, or an empty module when omitted.
    :param values: Local host bindings copied by the native derived Frame.
    :returns: Async context yielding the owned child Frame.
    :raises RuntimeError: If no active worker or request configuration exists.
    :raises TypeError: If native derivation rejects the module or values.
    :raises ValueError: If a local binding name is invalid.
    :raises BaseException: Propagates scope failures after restoring the parent.

    Parent definitions retain their native parent-owned evaluation and cache.
    The child belongs to this event loop and must not outlive the context.
    """
    parent = CONFIG_FRAME.get()
    if parent is None:
        raise RuntimeError("use_lcl_frame requires an active worker lifespan or request")
    selected = Module(ModuleName("local_configuration"), {}) if module is None else module
    async with parent.derive(selected, {} if values is None else values) as frame:
        token = CONFIG_FRAME.set(frame)
        try:
            yield frame
        finally:
            CONFIG_FRAME.reset(token)
