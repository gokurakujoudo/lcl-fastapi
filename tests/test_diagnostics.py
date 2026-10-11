"""Keep native reports and original exception objects while focusing frame evidence."""

import logging
from typing import Literal

import pytest
from lclang.logger import LoggerHandlerConfig, use_logger_handler
from starlette.responses import JSONResponse

from lcl_fastapi.diagnostics import ServiceFormatter, install_service_formatters


@pytest.mark.parametrize("style", ["compact", "full"])
def test_service_formatter_borrows_causes_and_group_members(
    style: Literal["compact", "full"],
) -> None:
    formatter = ServiceFormatter("%(message)s", traceback_style=style)
    try:
        JSONResponse(object())
    except TypeError as original:
        original.add_note("serialization context")
        original_trace = original.__traceback__
        group = ExceptionGroup("combined", [original, ValueError("second failure")])
        group.__cause__ = RuntimeError("original cause")
        record = logging.LogRecord("business", logging.ERROR, __file__, 1, "failure", (), None)
        record.exc_info = (type(group), group, None)
        rendered = formatter.format(record)
        assert "test_diagnostics.py" in rendered and "/starlette/" not in rendered.replace(
            "\\", "/"
        )
        assert "original cause" in rendered and "second failure" in rendered
        assert "serialization context" in rendered
        assert record.exc_info[1] is group and group.exceptions[0] is original
        assert original.__traceback__ is original_trace
    else:
        pytest.fail("invalid JSON value unexpectedly serialized")


def test_absent_exception_and_diagnostic_without_stack_are_not_fabricated() -> None:
    formatter = ServiceFormatter("%(message)s")
    assert formatter.formatException((None, None, None)) == ""
    assert formatter.format(logging.makeLogRecord({"msg": "plain", "exc_info": False})) == "plain"
    record = logging.LogRecord("business", logging.ERROR, __file__, 1, "diagnostic", (), None)
    record.exc_info = (None, None, None)
    assert formatter.format(record) == "diagnostic"
    record.exc_info = (ValueError, ValueError("detached failure"), None)
    record.lclang_failure_diagnostic = True
    assert formatter.format(record) == "diagnostic"


async def test_formatter_installation_does_not_create_disabled_sinks() -> None:
    config = LoggerHandlerConfig(console={"enabled": False}, file={})
    async with use_logger_handler(config) as runtime:
        install_service_formatters(runtime, config)
        assert runtime.metrics.sinks == {}
