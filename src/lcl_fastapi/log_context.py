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


def http_request_description(scope: Scope) -> str:
    """Render one safe HTTP invocation label without headers or query parameters.

    :param scope: Producer-owned ASGI scope with optional client address.
    :returns: Single-line method, actual path and client endpoint.
    """
    client = scope.get("client")
    address = "unknown"
    if client is not None:
        host, port = client
        address = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
    path = safe_repr(scope.get("path", "unknown"), renderer=str, max_length=None)
    address = safe_repr(address, renderer=str, max_length=None)
    return f"HTTP {scope.get('method', 'UNKNOWN')} {path} from {address}"


def log_metadata() -> dict[str, Any]:
    """Capture primitive execution metadata while the producer still owns its context.

    :returns: Detached record extras without requests, Frames or callable objects.
    """
    values: dict[str, Any] = {}
    context = get_request_context()
    scope = REQUEST_SCOPE.get()
    request_scope = {} if scope is None else scope
    if context is not None or scope is not None:
        values.update(
            request_id="unavailable" if context is None else context.request_id,
            request_method=request_scope["method"] if context is None else context.method,
            request_path=request_scope["path"] if context is None else context.path,
        )
        handler = callable_path(None if scope is None else scope.get("endpoint"))
        if handler:
            values["api_handler"] = handler
    worker = WORKER_LOG_CONTEXT.get()
    if worker is not None:
        name, attempt, handler = worker
        values.update(worker_name=name, worker_attempt=attempt)
        values["lcl_execution_identity"] = f"[Worker {name}] attempt={attempt} handler={handler}"
    return values
