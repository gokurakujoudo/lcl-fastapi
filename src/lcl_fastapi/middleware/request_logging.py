"""Render paired HTTP invocation metadata with the native text-tree utility."""

import os
from time import perf_counter_ns

from lclang.utils import safe_repr
from lclang.utils.text_tree import TextTree, render_text_tree
from starlette.types import Scope

from lcl_fastapi.context import get_request_context
from lcl_fastapi.logging import RequestLogger


def log_request_event(
    logger: RequestLogger,
    scope: Scope,
    started_ns: int,
    *,
    status_code: int | None = None,
    failure_detail: str = "",
) -> None:
    """Record safe HTTP metadata before dispatch or after completion, without reading a body.

    :param logger: Request-aware worker logger, borrowed from the active runtime.
    :param scope: HTTP ASGI scope containing path and connection metadata.
    :param started_ns: Invocation start on the monotonic nanosecond clock.
    :param status_code: Final observed HTTP status, or None for a start record.
    :param failure_detail: Native ID allocation failure text, included only at completion.
    :raises LclStateError: If the service logger runtime has closed.
    """
    context = get_request_context()
    identifier = "unavailable" if context is None else context.request_id
    request = f"method={scope['method']} path={scope['path']}"
    if status_code is not None:
        duration_ms = (perf_counter_ns() - started_ns) / 1_000_000
        request += f" status_code={status_code} duration_ms={duration_ms:.3f}"
        request += " " + failure_detail.rstrip() if failure_detail else ""
    tree: TextTree = (
        "REST API started" if status_code is None else "REST API completed",
        (
            (request, ((f"request_id={identifier}", ()),)),
            (
                "context",
                (
                    (f"worker_pid={os.getpid()}", ()),
                    (f"client={safe_repr(scope.get('client'))}", ()),
                    (f"scheme={scope.get('scheme', 'http')}", ()),
                ),
            ),
        ),
    )
    logger.info("%s", "\n".join(render_text_tree(tree)))
