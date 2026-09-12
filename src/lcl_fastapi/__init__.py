"""Public application, configuration, logging, and request-context interfaces."""

from lcl_fastapi.background import BackgroundWorker, BackgroundWorkerContext
from lcl_fastapi.context import RequestContext, get_config, get_request_context, use_lcl_frame
from lcl_fastapi.errors import (
    UncaughtExceptionContext,
    UncaughtExceptionHandler,
    default_uncaught_exception_handler,
)
from lcl_fastapi.logging import get_logger
from lcl_fastapi.service import LclFastAPI

# Unitless public names define the first-version import contract.
__all__ = [
    "LclFastAPI",
    "BackgroundWorker",
    "BackgroundWorkerContext",
    "UncaughtExceptionContext",
    "UncaughtExceptionHandler",
    "default_uncaught_exception_handler",
    "RequestContext",
    "get_config",
    "get_logger",
    "get_request_context",
    "use_lcl_frame",
]
