"""Run lclang CLI scopes before entering the platform's process manager."""

import asyncio
import sys
from collections.abc import Sequence
from importlib.metadata import version
from pathlib import Path

from lclang.cli import CliConfig, CliEntrance
from lclang.errors import LclError
from lclang.logger import LoggerHandlerConfig

from lcl_fastapi.cli.arguments import entrance_arguments, validate_arguments
from lcl_fastapi.cli.commands import command_group
from lcl_fastapi.overrides import override_scope


def main(
    arguments: Sequence[str] | None = None,
    *,
    config_path: str | Path | None = None,
    prog: str = "lcl-fastapi",
    version_text: str | None = None,
) -> int:
    """Execute an operational command and then any deferred foreground server.

    :param arguments: Tokens after the console command, or None for process argv.
    :param config_path: Downstream default file, overridden by explicit CLI selection.
    :param prog: Downstream console-script name used in help.
    :param version_text: Downstream version, or the installed framework version.
    :returns: Zero for success and a nonzero command or startup error status.
    """
    tokens = list(sys.argv[1:] if arguments is None else arguments)
    full_arguments = [sys.executable, f"{prog}.py", *tokens]
    pending: list[tuple[Path, bool]] = []
    try:
        params = validate_arguments(full_arguments)
        selected = entrance_arguments(full_arguments, params, config_path)
        overrides = (
            {}
            if params is None
            else {
                (f"{key}!" if key in params.masked_names else key): value
                for key, value in params.overrides.items()
                if key != "config"
            }
        )
        entrance = CliEntrance(
            command_group(pending),
            version=version_text or version("lcl-fastapi"),
            cli_config=CliConfig(LoggerHandlerConfig(console={"stream": "stderr"}, file={})),
        )
        with override_scope(overrides):
            result = asyncio.run(entrance.run(selected))
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
