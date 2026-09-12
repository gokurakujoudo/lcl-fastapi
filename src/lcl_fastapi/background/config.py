"""Validate executable registration separately from trusted LCL worker parameters."""

from collections.abc import Mapping
from dataclasses import dataclass

from lclang.runtime import Frame

from lcl_fastapi.background.context import BackgroundWorker


def worker_registry(workers: Mapping[str, BackgroundWorker] | None) -> dict[str, BackgroundWorker]:
    """Copy executable registrations, rejecting invalid or reserved sink identifiers.

    :param workers: Code-supplied named callables, or None for no background work.
    :returns: Independent registration dictionary.
    :raises ValueError: If a name is invalid or reserved.
    :raises TypeError: If an executable is not callable.
    """
    result = dict(workers or {})
    for name, executable in result.items():
        if (
            not isinstance(name, str)
            or not name.isidentifier()
            or name in {"default", "controller", "service"}
        ):
            raise ValueError(f"background worker name is invalid or reserved: {name!r}")
        if not callable(executable):
            raise TypeError(f"background worker {name}: expected a Python callable")
    return result


@dataclass(frozen=True, slots=True)
class WorkerPolicy:
    """Store effective restart switches for one registration.

    :param enabled: Whether to start this registered callable.
    :param auto_restart: Whether all completed executions are restarted until shutdown.
    """

    enabled: bool
    auto_restart: bool


async def worker_policies(
    frame: Frame, names: Mapping[str, BackgroundWorker]
) -> dict[str, WorkerPolicy]:
    """Resolve strict Boolean controls with leaf-wise default inheritance.

    :param frame: Active API configuration Frame.
    :param names: Validated code registrations.
    :returns: Effective switches for every registration, including disabled workers.
    :raises ValueError: If a control is not Boolean.
    :raises LclError: If a control expression cannot be evaluated.
    """

    async def flag(name: str, field: str) -> bool:
        """Resolve an explicit leaf or its default template.

        :param name: Registration identifier.
        :param field: enabled or auto_restart switch.
        :returns: Effective strict Boolean.
        :raises ValueError: If the selected value is not Boolean.
        :raises LclError: If evaluation fails.
        """
        key = f"background_worker.{name}.{field}"
        selected = key if frame.has(key) else f"background_worker.default.{field}"
        value = await frame.get(selected)
        if type(value) is not bool:
            raise ValueError(f"{selected}: expected a Boolean")
        return value

    return {
        name: WorkerPolicy(await flag(name, "enabled"), await flag(name, "auto_restart"))
        for name in names
    }
