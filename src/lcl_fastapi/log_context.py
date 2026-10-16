"""Snapshot scalar request and Worker identity before crossing the writer boundary."""

from contextvars import ContextVar
from typing import Any

from lclang.utils import safe_repr
from starlette.types import Scope

from lcl_fastapi.context import get_request_context

# Task/thread-local bindings retain live resources only inside their owning execution.
# A policy of None means no framework execution has pinned a file-diagnostic value.
REQUEST_SCOPE: ContextVar[Scope | None] = ContextVar("lcl_logging_scope", default=None)
WORKER_LOG_CONTEXT: ContextVar[tuple[str, int, str] | None] = ContextVar(
    "lcl_logging_worker", default=None
)
FILE_VERBOSE_CONTEXT: ContextVar[bool | None] = ContextVar("lcl_file_verbose", default=None)
# A report is first tied to its request and exception IDs, then confirmed by the
# escaping ASGI boundary as an integer for the runtime logger to consume once.
REPORTED_HTTP_FAILURE: ContextVar[tuple[str | None, int] | int | None] = ContextVar(
    "lcl_reported_http_failure", default=None
)


def callable_path(endpoint: object) -> str:
    """Read the registered callable's declared Python identity without retaining it.

    :param endpoint: Registered HTTP or Worker callable, possibly absent.
    :returns: Module-qualified callable name, or empty text without declared identity.
    """
    module, name = getattr(endpoint, "__module__", None), getattr(endpoint, "__qualname__", None)
    return f"{module}.{name}" if isinstance(module, str) and isinstance(name, str) else ""


def log_metadata() -> dict[str, Any]:
    """Capture primitive execution metadata while the producer still owns its context.

    :returns: Detached record extras without requests, Frames or callable objects.
    """
    values: dict[str, Any] = {}
    context = get_request_context()
    if context is not None:
        values.update(
            request_id=context.request_id, request_method=context.method, request_path=context.path
        )
        scope = REQUEST_SCOPE.get()
        handler = callable_path(None if scope is None else scope.get("endpoint"))
        identity = f"[{context.method} {safe_repr(context.path, renderer=str, max_length=None)}]"
        identity += f" request_id={context.request_id}"
        if handler:
            values["api_handler"] = handler
            identity += f" handler={handler}"
        values["lcl_execution_identity"] = identity
    worker = WORKER_LOG_CONTEXT.get()
    if worker is not None:
        name, attempt, handler = worker
        values.update(worker_name=name, worker_attempt=attempt)
        values["lcl_execution_identity"] = f"[Worker {name}] attempt={attempt} handler={handler}"
    return values
