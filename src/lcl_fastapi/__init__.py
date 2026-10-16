"""Public application, configuration, logging, and request-context interfaces."""

from lcl_fastapi.background import BackgroundWorker, BackgroundWorkerContext
from lcl_fastapi.context import RequestContext, get_config, get_request_context, use_lcl_frame
from lcl_fastapi.errors import (
    UncaughtExceptionContext,
    UncaughtExceptionHandler,
    default_uncaught_exception_handler,
)
from lcl_fastapi.logging import RequestLogger, get_logger
from lcl_fastapi.logging_defaults import DEFAULT_TAKEOVER_LOGGERS
from lcl_fastapi.service import LclFastAPI
from lcl_fastapi.verbose import (
    FileVerboseProcess,
    FileVerboseState,
    FileVerboseStatus,
    get_file_verbose,
    set_file_verbose,
)

# Unitless public names define the first-version import contract.
__all__ = [
    "DEFAULT_TAKEOVER_LOGGERS",
    "RequestLogger",
    "FileVerboseProcess",
    "FileVerboseState",
    "FileVerboseStatus",
    "get_file_verbose",
    "set_file_verbose",
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
