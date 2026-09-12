"""Compose native LCL defaults, using imports, and lazy invocation overrides."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

from lclang.ast import LclConstant
from lclang.cli.parser import override_expression
from lclang.config import Config, load_config
from lclang.masking import normalize_masked_mapping
from lclang.runtime import Frame, Module
from lclang.types import ModuleName

from lcl_fastapi.overrides import current_overrides

# Installed package resource; the reserved binding makes native dynamic using portable.
DEFAULT_CONFIG = Path(__file__).parent / "static" / "defaults.lclcfg"


@asynccontextmanager
async def configuration_frame(
    config_path: Path,
    worker_pid: int | None = None,
    *,
    logger_role: Literal["controller", "worker"] | None = None,
) -> AsyncIterator[Frame]:
    """Load fresh sources with native inheritance and command-line precedence.

    :param config_path: Trusted service file whose directory anchors application paths.
    :param worker_pid: Actual worker PID, or None outside a worker.
    :param logger_role: Restrict file sinks to the controller or worker role when selected.
    :returns: Context manager owning every created Frame in reverse cleanup order.
    :raises LclError: If loading, using expansion, or evaluation fails.
    :raises ValueError: If configuration replaces framework-owned bindings.
    """
    raw, masks = normalize_masked_mapping(current_overrides())
    reserved = {"worker_pid", "lcl_fastapi_defaults"}
    if raw.keys() & reserved:
        raise ValueError("worker_pid and lcl_fastapi_defaults are provided by the framework")
    expressions = {
        key: override_expression(value) if isinstance(value, str) else LclConstant(value=value)
        for key, value in raw.items()
    }
    values: dict[str, object] = {"lcl_fastapi_defaults": str(DEFAULT_CONFIG)}
    if worker_pid is not None:
        values["worker_pid"] = worker_pid
    defaults = await load_config(DEFAULT_CONFIG)
    loaded = await load_config(config_path, overrides=expressions | values)
    if loaded.definitions.keys() & reserved:
        raise ValueError("worker_pid and lcl_fastapi_defaults are provided by the framework")
    combined = Config(loaded.version, loaded.root_origin, defaults.expanded + loaded.expanded)
    definitions = dict(combined.to_module().definitions) | expressions
    if logger_role is not None:
        definitions = {
            key: value
            for key, value in definitions.items()
            if not key.startswith("logger.file.")
            or key.split(".")[2] == "default"
            or (key.split(".")[2] == "controller") == (logger_role == "controller")
        }
    module = Module(
        ModuleName("service_configuration"),
        definitions,
        masked_names=(combined.masked_names | masks) & definitions.keys(),
    )
    factory = replace(combined.frame_factory(), module=module)
    async with factory.create(values=values) as frame:
        yield frame
