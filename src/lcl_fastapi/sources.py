"""Compose native LCL defaults, using imports, and lazy invocation overrides."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import Literal

from lclang.cli.option_parser import override_expression
from lclang.config import load_config
from lclang.lang import LCL_IMPORTS, Frame, LclConstant, Module, ModuleName
from lclang.utils import normalize_masked_mapping

from lcl_fastapi.overrides import current_overrides

# Installed package resource; the reserved binding makes native dynamic using portable.
DEFAULT_CONFIG = Path(__file__).parent / "static" / "defaults.lclcfg"


def logger_binding_for_role(name: str, role: Literal["controller", "worker"]) -> bool:
    """Keep shared logger namespaces and only sinks owned by the selected role.

    :param name: Definition or explicit namespace name from a native Module.
    :param role: Controller owns its sink; workers own every other named sink.
    :returns: Whether the binding belongs in this role's configuration.
    """
    if not name.startswith("logger.file."):
        return True
    sink = name.split(".")[2]
    return sink == "default" or (sink == "controller") == (role == "controller")


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
    :returns: Context manager owning one independent Frame above native standard imports.
    :raises LclError: If loading, composition, expansion, or evaluation fails.
    :raises ValueError: If configuration replaces framework-owned bindings or transport is invalid.
    """
    raw, masks = normalize_masked_mapping(current_overrides())
    verbose = raw.pop("__verbose__", False)
    if not isinstance(verbose, bool):
        raise ValueError("internal __verbose__ transport must be Boolean")
    reserved = {"worker_pid", "lcl_fastapi_defaults"}
    if raw.keys() & reserved:
        raise ValueError("worker_pid and lcl_fastapi_defaults are provided by the framework")
    expressions = {
        key: override_expression(value) if isinstance(value, str) else LclConstant(value=value)
        for key, value in raw.items()
    }
    values: dict[str, object] = {
        "lcl_fastapi_defaults": str(DEFAULT_CONFIG),
        "__verbose__": verbose,
    }
    if worker_pid is not None:
        values["worker_pid"] = worker_pid
    defaults = await load_config(DEFAULT_CONFIG)
    loaded = await load_config(config_path, overrides=expressions | values)
    if (loaded.definitions.keys() | loaded.namespace_names) & reserved:
        raise ValueError("worker_pid and lcl_fastapi_defaults are provided by the framework")
    module = defaults.to_module().mixin(loaded.to_module(), name="service_configuration")
    module = module.mixin(Module(ModuleName("cli_overrides"), expressions, masked_names=masks))
    if logger_role is not None:
        definitions = {
            key: value
            for key, value in module.definitions.items()
            if logger_binding_for_role(key, logger_role)
        }
        module = replace(
            module,
            definitions=definitions,
            masked_names=module.masked_names & definitions.keys(),
            namespace_names=frozenset(
                name
                for name in module.namespace_names
                if logger_binding_for_role(name, logger_role)
            ),
        )
    async with Frame(module, values=values, parent=LCL_IMPORTS) as frame:
        yield frame
