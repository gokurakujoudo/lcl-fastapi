"""Render original traceback locations and current argument representations."""

import inspect
import linecache
from types import TracebackType


def safe_repr(value: object) -> str:
    """Retain the object's own representation policy, tolerating a broken repr.

    :param value: Argument or exception to represent without additional redaction.
    :returns: Full repr, or a type-only placeholder when representation fails.
    """
    try:
        return repr(value)
    except Exception:
        return f"<repr unavailable: {type(value).__name__}>"


def traceback_lines(trace: TracebackType | None) -> list[str]:
    """Read traceback-owned line numbers and argument slots without inspecting callers.

    :param trace: Original traceback chain, or None for an unraised exception.
    :returns: Location, source and tab-indented argument lines.
    """
    lines: list[str] = []
    while trace is not None:
        frame, code = trace.tb_frame, trace.tb_frame.f_code
        source = linecache.getline(code.co_filename, trace.tb_lineno).strip()
        lines.append(f'File "{code.co_filename}", line {trace.tb_lineno}, in {code.co_name}')
        lines.append(f"\t{source or '<source unavailable>'}")
        count = code.co_argcount + code.co_kwonlyargcount
        count += bool(code.co_flags & inspect.CO_VARARGS)
        count += bool(code.co_flags & inspect.CO_VARKEYWORDS)
        for name in code.co_varnames[:count]:
            value = safe_repr(frame.f_locals[name]) if name in frame.f_locals else "<unavailable>"
            lines.append(f"\t{name}: {value}")
        trace = trace.tb_next
    return lines


def exception_trace(error: BaseException) -> str:
    """Render exception causes, contexts and group children without infinite cycles.

    :param error: Original exception whose traceback must be rendered.
    :returns: Complete diagnostic containing current argument values, without truncation.
    """
    seen: set[int] = set()

    def render(current: BaseException) -> list[str]:
        """Visit one exception exactly once while preserving native chain order.

        :param current: Exception or exception-group node.
        :returns: Its chain and traceback diagnostic lines.
        """
        if id(current) in seen:
            return ["<exception already shown>"]
        seen.add(id(current))
        lines: list[str] = []
        if current.__cause__ is not None:
            lines.extend(render(current.__cause__))
            lines.append("The above exception was the direct cause:")
        elif current.__context__ is not None and not current.__suppress_context__:
            lines.extend(render(current.__context__))
            lines.append("During handling of the above exception:")
        lines.extend(traceback_lines(current.__traceback__))
        lines.append(f"{type(current).__name__}: {safe_repr(current)}")
        if isinstance(current, BaseExceptionGroup):
            for index, child in enumerate(current.exceptions, 1):
                lines.append(f"Exception group member {index}:")
                lines.extend(render(child))
        return lines

    return "\n".join(render(error))
