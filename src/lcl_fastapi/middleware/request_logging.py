"""Emit REST boundaries or one static GET summary through native compact logging."""

from time import perf_counter_ns

from starlette.types import Scope

from lcl_fastapi.log_context import http_request_description
from lcl_fastapi.logging import RequestLogger


def log_request_event(
    logger: RequestLogger,
    scope: Scope,
    started_ns: int,
    *,
    status_code: int | None = None,
    failure_detail: str = "",
    static_resource: bool = False,
) -> None:
    """Record one invocation boundary without reading request headers or its body.

    :param logger: Borrowed worker logger with producer-captured request identity.
    :param scope: HTTP scope with actual path and optional client endpoint.
    :param started_ns: Monotonic start time in nanoseconds.
    :param status_code: Observed response status, or None for the start boundary.
    :param failure_detail: Native allocation failure fields for a completion record.
    :param static_resource: Whether this request uses a single static GET completion summary.
    :raises LclStateError: If the service logger runtime has closed.
    """
    if static_resource and status_code is None:
        return
    request = http_request_description(scope)
    if static_resource:
        message = f"Static Resource({status_code}): {request}"
    elif status_code is not None:
        duration_ms = (perf_counter_ns() - started_ns) / 1_000_000
        message = f"REST API Completed({status_code}): {request} [duration_ms={duration_ms:.3f}]"
    else:
        message = f"REST API Started: {request}"
    if status_code is not None and failure_detail:
        message += f" [{failure_detail.strip()}]"
    logger.info("%s", message, compact=True)
