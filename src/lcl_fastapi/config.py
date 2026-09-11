"""Read trusted LCL files into validated worker and service settings."""

import asyncio
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from lclang.config import Config, load_config
from lclang.runtime import Frame


@dataclass(frozen=True, slots=True)
class Settings:
    """Store validated values without retaining a configuration Frame.

    :param app_name: Service name recorded in runtime state.
    :param app_version: Downstream application's version text.
    :param app_target: Import target in module:attribute form.
    :param host: Loopback or all-IPv4 listener address.
    :param port: TCP port between 1 and 65535.
    :param workers: Configured worker count.
    :param origin: Optional external HTTP(S) origin, never an ASGI path.
    :param state_dir: Absolute service state directory.
    :param pid_file: Absolute service PID file.
    :param worker_state_dir: Absolute worker-state directory.
    :param backlog: Pending connection queue capacity.
    :param keep_alive_seconds: Idle HTTP connection timeout in seconds.
    :param graceful_timeout_seconds: Graceful shutdown deadline in seconds.
    :param health_enabled: Whether to register the default health endpoint.
    :param health_path: Absolute health route path.
    :param sample_interval_seconds: State and metric refresh interval in seconds.
    :param disk_paths: Absolute filesystem paths included in health sampling.
    :param docs_enabled: Whether to register built-in Swagger resources.
    :param docs_path: Absolute Swagger HTML route path.
    :param openapi_path: Absolute schema route path.
    :param id_header: ASCII HTTP response header carrying the request ID.
    :param worker_id_base: First allowed Snowflake worker ID.
    :param worker_id_count: Number of allowed consecutive worker IDs.
    :param reload_dirs: Resolved Python watch roots, used only in hot reload mode.
    """

    app_name: str
    app_version: str
    app_target: str
    host: str
    port: int
    workers: int
    origin: str
    state_dir: Path
    pid_file: Path
    worker_state_dir: Path
    backlog: int
    keep_alive_seconds: int
    graceful_timeout_seconds: int
    health_enabled: bool
    health_path: str
    sample_interval_seconds: float
    disk_paths: tuple[Path, ...]
    docs_enabled: bool
    docs_path: str
    openapi_path: str
    id_header: str
    worker_id_base: int
    worker_id_count: int
    reload_dirs: tuple[Path, ...] = ()


def text_value(value: object, name: str) -> str:
    """Validate nonempty text without control characters.

    :param value: Resolved configuration value.
    :param name: Qualified name used in diagnostics.
    :returns: Validated text.
    :raises ValueError: If text is absent, empty, or contains controls.
    """
    if not isinstance(value, str) or not value or any(ord(char) < 32 for char in value):
        raise ValueError(f"{name}: expected nonempty text without controls")
    return value


def integer_value(value: object, name: str, minimum: int, maximum: int) -> int:
    """Validate a bounded integer without accepting Boolean values.

    :param value: Resolved configuration value.
    :param name: Qualified name used in diagnostics.
    :param minimum: Inclusive lower bound.
    :param maximum: Inclusive upper bound.
    :returns: Validated integer.
    :raises ValueError: If the value has an invalid type or exceeds a bound.
    """
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name}: expected an integer from {minimum} to {maximum}")
    return value


def origin_value(value: object) -> str:
    """Validate external origin metadata without interpreting it as a path.

    :param value: Empty text or an HTTP(S) origin.
    :returns: Original validated origin.
    :raises ValueError: If the value contains credentials, a path, or URL suffixes.
    """
    if value == "":
        return ""
    origin = text_value(value, "server.root_path")
    parsed = urlsplit(origin)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or any(char.isspace() for char in origin)
        or "\\" in origin
        or "?" in origin
        or "#" in origin
        or parsed.netloc.endswith(":")
    ):
        raise ValueError("server.root_path: expected an HTTP(S) origin without a path")
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError("server.root_path: invalid port")
    return origin


@asynccontextmanager
async def configuration_frame(
    config_path: Path,
    worker_pid: int | None = None,
) -> AsyncIterator[Frame]:
    """Load fresh source files and own their combined LCL Frame.

    :param config_path: Trusted service configuration file.
    :param worker_pid: Actual worker PID, or None for non-worker settings inspection.
    :returns: Async context manager yielding a caller-scoped Frame.
    :raises LclConfigError: If a source cannot be loaded or parsed.
    :raises ValueError: If a service defines the framework-owned worker PID.
    """
    defaults = await load_config(Path(__file__).parent / "static" / "defaults.lclcfg")
    loaded = await load_config(config_path)
    if "worker_pid" in loaded.definitions:
        raise ValueError("worker_pid is provided by the framework")
    combined = Config(loaded.version, loaded.root_origin, defaults.expanded + loaded.expanded)
    values: dict[str, object] = {} if worker_pid is None else {"worker_pid": worker_pid}
    async with combined.frame_factory().create(values=values) as frame:
        yield frame


async def settings_from_frame(frame: Frame, config_path: Path) -> Settings:
    """Resolve settings while leaving logger expressions lazy.

    :param frame: Caller-owned active configuration Frame.
    :param config_path: File whose parent anchors relative filesystem values.
    :returns: Validated detached service settings.
    :raises ValueError: If a setting has an invalid type or combination.
    :raises LclError: If a required value is absent or fails evaluation.
    """

    async def text(name: str) -> str:
        """Resolve one nonempty string.

        :param name: Qualified configuration key.
        :returns: Validated string.
        :raises ValueError: If the resolved value is not valid text.
        """
        return text_value(await frame.get(name), name)

    async def number(name: str, minimum: int, maximum: int) -> int:
        """Resolve one integer with explicit bounds.

        :param name: Qualified configuration key.
        :param minimum: Inclusive lower bound.
        :param maximum: Inclusive upper bound.
        :returns: Validated integer.
        :raises ValueError: If the value is not a bounded integer.
        """
        return integer_value(await frame.get(name), name, minimum, maximum)

    async def flag(name: str) -> bool:
        """Resolve a strict Boolean.

        :param name: Qualified configuration key.
        :returns: Validated Boolean.
        :raises ValueError: If the value is not Boolean.
        """
        value = await frame.get(name)
        if not isinstance(value, bool):
            raise ValueError(f"{name}: expected a Boolean")
        return value

    async def path(name: str) -> str:
        """Resolve a route without query or fragment components.

        :param name: Qualified route configuration key.
        :returns: Absolute route path.
        :raises ValueError: If the route is not an absolute path.
        """
        value = await text(name)
        if not value.startswith("/") or value.startswith("//") or any(c in value for c in "?#"):
            raise ValueError(f"{name}: expected an absolute route path")
        return value

    base = (await asyncio.to_thread(config_path.resolve)).parent

    async def filesystem_path(name: str) -> Path:
        """Resolve a configured path outside the event-loop thread.

        :param name: Qualified filesystem configuration key.
        :returns: Absolute path anchored to the configuration directory.
        :raises ValueError: If the configured value is not text.
        """
        selected = base / await text(name)
        return await asyncio.to_thread(selected.resolve)

    host = await text("server.host")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("server.host: expected 127.0.0.1 or 0.0.0.0")
    workers = await number("server.workers", 1, 1024)
    worker_base = await number("snowflake.worker_id_base", 0, 1023)
    worker_count = await number("snowflake.worker_id_count", workers, 1024 - worker_base)
    interval = await frame.get("health.sample_interval_seconds")
    if (
        isinstance(interval, bool)
        or not isinstance(interval, (int, float))
        or not math.isfinite(interval)
        or interval <= 0
    ):
        raise ValueError("health.sample_interval_seconds: expected a positive number")
    disks = await frame.get("health.disk_paths")
    if not isinstance(disks, (list, tuple)):
        raise ValueError("health.disk_paths: expected a list of paths")
    disk_paths = []
    for item in disks:
        disk_path = base / text_value(item, "health.disk_paths")
        disk_paths.append(await asyncio.to_thread(disk_path.resolve))
    header = await text("request.id_header")
    directories = await frame.get("server.reload_dirs")
    if not isinstance(directories, list) or not directories:
        raise ValueError("server.reload_dirs: expected a nonempty list of directory paths")
    reload_dirs = []
    for item in directories:
        directory = base / text_value(item, "server.reload_dirs")
        reload_dirs.append(await asyncio.to_thread(directory.resolve))
    if not all(char.isascii() and (char.isalnum() or char in "!#$%&'*+-.^_`|~") for char in header):
        raise ValueError("request.id_header: expected an ASCII HTTP header name")
    return Settings(
        await text("app.name"),
        await text("app.version"),
        await text("app.target"),
        host,
        await number("server.port", 1, 65535),
        workers,
        origin_value(await frame.get("server.root_path")),
        await filesystem_path("runtime.state_dir"),
        await filesystem_path("runtime.pid_file"),
        await filesystem_path("runtime.worker_state_dir"),
        await number("server.backlog", 1, 65535),
        await number("server.keep_alive_seconds", 0, 86400),
        await number("server.graceful_timeout_seconds", 1, 86400),
        await flag("health.enabled"),
        await path("health.path"),
        float(interval),
        tuple(disk_paths),
        await flag("docs.enabled"),
        await path("docs.path"),
        await path("docs.openapi_path"),
        header,
        worker_base,
        worker_count,
        tuple(dict.fromkeys(reload_dirs)),
    )


async def load_settings(config_path: Path) -> Settings:
    """Load detached settings without initializing worker logging.

    :param config_path: Trusted service configuration path.
    :returns: Validated service settings.
    :raises LclError: If configuration loading or evaluation fails.
    :raises ValueError: If a setting is invalid.
    """
    async with configuration_frame(config_path) as frame:
        return await settings_from_frame(frame, config_path)
