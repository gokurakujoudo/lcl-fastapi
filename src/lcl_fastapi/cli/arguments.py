"""Preflight service CLI policy using lclang's public option parser."""

from collections.abc import Sequence

from lclang.cli.parser import parse_cli_params, split_argv


def validate_arguments(arguments: Sequence[str]) -> None:
    """Reject service overrides before lclang can construct a logger scope.

    :param arguments: Full Python-style argv with an internal ``.py`` script label.
    :raises ValueError: If the invocation supplies a forbidden configuration source or key.
    :raises LclCliUsageError: If lclang rejects option syntax or argument arity.
    """
    parts = split_argv(arguments)
    tokens = parts.tokens
    if not tokens or tokens in (("-v",), ("--version",)):
        return
    option_start = next(
        (index for index, token in enumerate(tokens) if token.startswith("-")), len(tokens)
    )
    command = tokens[:option_start]
    params = parse_cli_params(parts, command or ("help",), tokens[option_start:])
    if params.config_file_path is not None:
        raise ValueError("-c/--config is unavailable; use -o config service.lclcfg")
    if params.dryrun:
        raise ValueError("dryrun is unavailable; render commands already only generate files")
    allowed = {"config"}
    if command == ("serve",):
        allowed.add("hot_reload")
    if command in (("status",), ("logs",)):
        allowed.add("json")
    if command in (("nginx", "render"), ("systemd", "render")):
        allowed.add("output")
    unknown = params.overrides.keys() - allowed
    if unknown:
        raise ValueError(f"unsupported override: {', '.join(sorted(unknown))}")
