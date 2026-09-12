"""Declare the six operational commands using the supported lclang CLI surface."""

import asyncio
from pathlib import Path
from typing import Literal

from lclang.cli import CliContext, CliResult, Command, CommandGroup, ParameterDoc, cli

from lcl_fastapi.cli.output import format_logs, format_status
from lcl_fastapi.config import load_settings
from lcl_fastapi.render.config import render_config
from lcl_fastapi.render.validation import text_value
from lcl_fastapi.sources import configuration_frame


async def config_path(context: CliContext) -> Path:
    """Read the service file path as ordinary CLI data, never a CLI config layer.

    :param context: Active lclang command invocation.
    :returns: Absolute service configuration file path.
    :raises ValueError: If the config parameter is not nonempty path text.
    """
    value = await context.frame.get("config")
    return await asyncio.to_thread(Path(text_value(value, "config")).resolve)


async def command_value(context: CliContext, name: str, fallback: object) -> object:
    """Resolve an operational option with file and command-line precedence.

    :param context: Active invocation containing the selected configuration path.
    :param name: Qualified option name.
    :param fallback: Value used when neither file nor invocation defines the option.
    :returns: Native LCL value resolved in the complete service configuration.
    :raises LclError: If the file or expression is invalid.
    """
    async with configuration_frame(await config_path(context)) as frame:
        return await frame.get(name, fallback=fallback)


async def json_output(context: CliContext) -> bool:
    """Resolve the explicit LCL Boolean controlling operational JSON output.

    :param context: Active lclang command invocation.
    :returns: Whether the complete result should be emitted as JSON.
    :raises ValueError: If json is not a Boolean.
    """
    value = await command_value(context, "json", False)
    if not isinstance(value, bool):
        raise ValueError('json must be Boolean; use -o json or -o json "LCL[True]"')
    return value


def renderer_command(kind: Literal["nginx", "systemd"]) -> Command:
    """Declare a render leaf that only writes the requested output file.

    :param kind: Deployment artifact to render.
    :returns: A lclang command with config and optional output parameters.
    """

    async def render_command(context: CliContext) -> CliResult:
        """Generate deployment text and return it or write a caller-selected file.

        :param context: Validated invocation with an ordinary service path parameter.
        :returns: Rendered text or an empty success after writing a file.
        :raises ValueError: If renderer configuration or output path is invalid.
        :raises OSError: If reading configuration or writing the output fails.
        """
        result = await render_config(kind, await config_path(context))
        output = await command_value(context, "output", None)
        if output is None or context.dryrun:
            return CliResult.success(result.rstrip("\n"))
        path = Path(text_value(output, "output"))
        await asyncio.to_thread(path.write_text, result, encoding="utf-8", newline="\n")
        return CliResult.success("")

    return cli.command(
        "render",
        f"Generate {kind} configuration; never install or reload it.",
        parameter_docs=(
            ParameterDoc("config", str, True, "Service .lclcfg file path."),
            ParameterDoc("output", str, False, "Output file path; omit for stdout."),
        ),
    )(render_command)


def command_group(pending: list[tuple[Path, bool]]) -> CommandGroup:
    """Create an isolated CLI command tree and a deferred server-start handoff.

    :param pending: Caller-owned list receiving the serve path and reload flag.
    :returns: Root lclang group containing only supported first-version commands.
    """
    config_doc = ParameterDoc("config", str, True, "Service .lclcfg file path.")
    json_doc = ParameterDoc("json", bool, False, "Return JSON; use -o json.", False)

    @cli.command(
        "serve",
        "Run the service until graceful shutdown.",
        (
            config_doc,
            ParameterDoc(
                "hot_reload", bool, False, "Reload Python changes with one worker.", False
            ),
        ),
    )
    async def serve_command(context: CliContext) -> CliResult:
        """Defer server startup until the command logger and event loop are closed.

        :param context: Active command invocation.
        :returns: Empty success after retaining the service configuration path.
        :raises ValueError: If the config parameter is invalid.
        """
        hot_reload = await command_value(context, "hot_reload", False)
        if not isinstance(hot_reload, bool):
            raise ValueError('hot_reload must be Boolean; use -o hot_reload or "LCL[True]"')
        path = await config_path(context)
        if context.dryrun:
            await load_settings(path)
        else:
            pending.append((path, hot_reload))
        return CliResult.success("")

    @cli.command(
        "status", "Inspect the local service identity and workers.", (config_doc, json_doc)
    )
    async def status_command(context: CliContext) -> CliResult:
        """Read a runtime snapshot using PID and process-creation identity checks.

        :param context: Active command invocation.
        :returns: Plain text or JSON runtime status.
        :raises ValueError: If command configuration is invalid.
        :raises OSError: If local runtime inspection fails.
        """
        from lcl_fastapi.runtime.common import inspect_status

        result = await inspect_status(await config_path(context))
        return CliResult.success(format_status(result, as_json=await json_output(context)))

    @cli.command("logs", "List live workers' observed active log segments.", (config_doc, json_doc))
    async def logs_command(context: CliContext) -> CliResult:
        """Return observed log paths without querying a network endpoint.

        :param context: Active command invocation.
        :returns: Plain paths or a JSON observation snapshot.
        :raises ValueError: If command configuration or runtime output is invalid.
        :raises OSError: If local runtime inspection fails.
        """
        from lcl_fastapi.runtime.common import active_logs

        result = await active_logs(await config_path(context))
        return CliResult.success(format_logs(result, as_json=await json_output(context)))

    @cli.command("stop", "Request authenticated graceful shutdown over loopback.", (config_doc,))
    async def stop_command(context: CliContext) -> CliResult:
        """Invoke the identity-checked local control operation.

        :param context: Active command invocation.
        :returns: Empty success after the runtime accepts the stop request.
        :raises ValueError: If configuration or service identity is invalid.
        :raises OSError: If the verified local service cannot be reached.
        """
        path = await config_path(context)
        if context.dryrun:
            await load_settings(path)
        else:
            from lcl_fastapi.runtime.common import stop

            await stop(path)
        return CliResult.success("")

    return CommandGroup(
        "lcl_fastapi",
        "Local service operations; configuration comes from .lclcfg.",
        (
            serve_command,
            status_command,
            logs_command,
            stop_command,
            CommandGroup(
                "nginx", "Nginx deployment file generation.", (renderer_command("nginx"),)
            ),
            CommandGroup("systemd", "systemd unit generation.", (renderer_command("systemd"),)),
        ),
    )
