"""Adapt native lclang options while keeping service logging out of the CLI scope."""

from collections.abc import Sequence
from pathlib import Path

from lclang.cli import CliParams
from lclang.cli.parser import parse_cli_params, split_argv


def validate_arguments(arguments: Sequence[str]) -> CliParams | None:
    """Parse native options without restricting application configuration keys.

    :param arguments: Full Python-style argv with an internal script label.
    :returns: Native parameters, or None for root help/version and empty input.
    :raises LclCliUsageError: If native syntax or argument arity is invalid.
    :raises ValueError: If the invocation overrides framework-owned worker identity.
    """
    parts = split_argv(arguments)
    tokens = parts.tokens
    if not tokens or tokens in (("-v",), ("--version",), ("-h",), ("--help",)):
        return None
    option_start = next(
        (index for index, token in enumerate(tokens) if token.startswith("-")), len(tokens)
    )
    command = tokens[:option_start]
    params = parse_cli_params(parts, command or ("help",), tokens[option_start:])
    if params.overrides.keys() & {"worker_pid", "lcl_fastapi_defaults"}:
        raise ValueError("worker_pid and lcl_fastapi_defaults are provided by the framework")
    return params


def entrance_arguments(
    arguments: list[str], params: CliParams | None, config_path: str | Path | None
) -> list[str]:
    """Retain native routing and diagnostics with an isolated short-lived CLI logger.

    :param arguments: Full original argv, retained for help and version invocations.
    :param params: Parsed native parameters when a command is present.
    :param config_path: Downstream entrance default overridden by explicit CLI selection.
    :returns: Native entrance argv transporting the service path as ordinary data.
    """
    from lclang.cli.parser import help_requested, split_argv

    if params is None or help_requested(split_argv(arguments).tokens):
        return arguments
    selected = params.overrides.get("config", params.config_file_path or config_path)
    result = [*arguments[:2], *params.command]
    if selected is not None:
        result.extend(["-o", "config", str(selected) if selected is not True else "LCL[True]"])
    if params.dryrun:
        result.append("--dryrun")
    if params.verbose:
        result.append("--verbose")
    result.extend(["--as-of", params.as_of_date.strftime("%Y%m%d")])
    return result
