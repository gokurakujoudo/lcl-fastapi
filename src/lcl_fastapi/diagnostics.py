"""Refine detached native reports and controller event headers for service users."""

import copy
import logging
import os
from pathlib import Path
from types import TracebackType
from typing import cast

import fastapi
import starlette
from lclang.error import LclError
from lclang.error.diagnostic_rendering import render_failure
from lclang.error.failure_traceback import build_traceback_tree, contains_traceback_frames
from lclang.logger import LoggerHandlerConfig, LoggerRuntime
from lclang.logger.record_formatter import FILE_ONLY_ATTRIBUTE, RecordFormatter
from lclang.utils.text_tree import TextTree, render_text_tree

# Native StackSummary frame labels start with these installed-package locations.
# Unitless prefixes select only FastAPI/Starlette source frames, never message content.
FRAMEWORK_FRAME_PREFIXES = tuple(
    f'File "{Path(cast(str, package.__file__)).parent}{os.sep}' for package in (fastapi, starlette)
)
# Native traceback node label; removing empty nodes avoids a misleading empty stack.
TRACEBACK_LABEL = "Traceback (most recent call last)"
# Controller events have no business call site; retain native event time, level and PID.
CONTROLLER_EVENT_FORMAT = "%(asctime)s | %(levelname)-8s | pid=%(process)d | %(message)s"


def service_trace_tree(tree: TextTree) -> TextTree:
    """Filter native frame leaves while retaining exception relationships and diagnostics.

    :param tree: Detached native Python exception tree, never a live traceback.
    :returns: Equivalent tree without FastAPI/Starlette frames or empty stack nodes.
    """
    label, children = tree
    if label == TRACEBACK_LABEL:
        return label, tuple(
            child for child in children if not child[0].startswith(FRAMEWORK_FRAME_PREFIXES)
        )
    selected = (service_trace_tree(child) for child in children)
    return label, tuple(child for child in selected if child[0] != TRACEBACK_LABEL or child[1])


class ServiceFormatter(RecordFormatter):
    """Retain native layouts and protected reports with focused service stack evidence.

    :param fmt: Native percent-style record format.
    :param timezone: Native local/UTC timestamp policy.
    :param traceback_style: Compact lclang filtering or full lclang Python evidence.
    """

    def trace(self, error: BaseException, tb: TracebackType | None) -> str:
        """Render a filtered native exception tree when selected Python frames exist.

        :param error: Borrowed original exception, retaining causes and group members.
        :param tb: Traceback supplied by the original logging record.
        :returns: Python evidence with its heading, or empty text without selected frames.
        """
        tree = service_trace_tree(build_traceback_tree(error, tb, self.traceback_style, set()))
        if not contains_traceback_frames(tree):
            return ""
        return "Python traceback:\n" + "\n".join(render_text_tree(tree))

    def formatException(
        self,
        ei: tuple[type[BaseException], BaseException, TracebackType | None]
        | tuple[None, None, None],
    ) -> str:
        """Keep the native diagnostic and filter only its detached Python frame tree.

        :param ei: Borrowed standard-library exception tuple.
        :returns: Native diagnostic and focused traceback, or empty text without an error.
        """
        error = ei[1]
        if error is None:
            return ""
        diagnostic = render_failure(
            error, action="" if isinstance(error, LclError) else "handling failure"
        )
        trace = self.trace(error, ei[2])
        return diagnostic + ("\n" + trace if trace else "")

    def format(self, record: logging.LogRecord) -> str:
        """Apply concise controller headers and focused exception evidence on the writer.

        :param record: Borrowed original producer record, including native verbose metadata.
        :returns: Configured message and protected native diagnostic text.
        """
        if record.name == "lcl_fastapi.controller" and getattr(record, FILE_ONLY_ATTRIBUTE, False):
            return RecordFormatter(
                CONTROLLER_EVENT_FORMAT,
                timezone=self.timezone,
                traceback_style=self.traceback_style,
            ).format(record)
        if not record.exc_info or record.exc_info[1] is None:
            return super().format(record)
        detached = copy.copy(record)
        detached.exc_info, detached.exc_text = None, None
        formatted = super().format(detached)
        report = (
            self.trace(record.exc_info[1], record.exc_info[2])
            if getattr(record, "lclang_failure_diagnostic", False)
            else self.formatException(record.exc_info)
        )
        return formatted + ("\n" + report if report else "")


def install_service_formatters(runtime: LoggerRuntime, config: LoggerHandlerConfig) -> None:
    """Select service formatting before accepting business activity in an owned runtime.

    :param runtime: Initialized upstream runtime owned by this service or controller.
    :param config: Its resolved native layout, timezone and traceback policy.
    """
    formatter = ServiceFormatter(
        config.format, timezone=config.timezone, traceback_style=config.traceback_style
    )
    for sink in runtime.dispatcher.sinks:
        sink.formatter = formatter
