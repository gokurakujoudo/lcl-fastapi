"""Validate generated configuration values before interpolating trusted inputs."""

import re
from pathlib import PurePosixPath


def text_value(value: object, name: str) -> str:
    """Require nonempty text without configuration line-control characters.

    :param value: Materialized LCL value.
    :param name: Qualified setting used in diagnostics.
    :returns: Validated text.
    :raises ValueError: If the value is empty, nontext, or contains controls.
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name} must be nonempty text without control characters")
    return value


def integer_value(value: object, name: str, minimum: int, maximum: int) -> int:
    """Require an integer in an inclusive configuration range.

    :param value: Materialized LCL value; Boolean values are rejected.
    :param name: Qualified setting used in diagnostics.
    :param minimum: Inclusive lower bound in the setting's documented units.
    :param maximum: Inclusive upper bound in the setting's documented units.
    :returns: Validated integer.
    :raises ValueError: If the value is not an integer within the range.
    """
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} must be an integer from {minimum} to {maximum}")
    return value


def identifier(value: object, name: str) -> str:
    """Validate a service, user, or group token without configuration syntax.

    :param value: Candidate identifier.
    :param name: Qualified setting used in diagnostics.
    :returns: Validated identifier.
    :raises ValueError: If the identifier contains unsupported characters.
    """
    result = text_value(value, name)
    if re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]*", result) is None:
        raise ValueError(f"{name} must contain only letters, digits, underscores, dots or hyphens")
    return result


def absolute_path(value: object, name: str) -> str:
    """Require an absolute POSIX deployment path even on Windows.

    :param value: Candidate path, which is never accessed by the renderer.
    :param name: Qualified setting used in diagnostics.
    :returns: Validated path text.
    :raises ValueError: If the path is relative or contains controls.
    """
    result = text_value(value, name)
    if not PurePosixPath(result).is_absolute():
        raise ValueError(f"{name} must be an absolute POSIX path")
    return result
