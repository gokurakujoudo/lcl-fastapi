"""Run lclang CLI scopes before entering the platform's process manager."""

import asyncio
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from lclang.cli import CliConfig, CliEntrance
from lclang.errors import LclError
from lclang.logger import LoggerHandlerConfig

from lcl_fastapi.cli.arguments import validate_arguments
from lcl_fastapi.cli.commands import command_group


def main(arguments: Sequence[str] | None = None) -> int:
    """Execute an operational command and then any deferred foreground server.

    :param arguments: Tokens after the console command, or None for process argv.
    :returns: Zero for success and a nonzero command or startup error status.
    """
    tokens = list(sys.argv[1:] if arguments is None else arguments)
    full_arguments = [sys.executable, "lcl-fastapi.py", *tokens]
    pending: list[tuple[Path, bool]] = []
    try:
        validate_arguments(full_arguments)
        entrance = CliEntrance(
            command_group(pending),
            version=version("lcl-fastapi"),
            cli_config=CliConfig(LoggerHandlerConfig(console={"stream": "stderr"}, file={})),
        )
        result = asyncio.run(entrance.run(full_arguments))
        if result == 0 and pending:
            from lcl_fastapi.runtime.common import serve

            path, hot_reload = pending[0]
            if hot_reload:
                serve(path, hot_reload=True)
            else:
                serve(path)
        return result
    except (ValueError, OSError, RuntimeError, LclError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
