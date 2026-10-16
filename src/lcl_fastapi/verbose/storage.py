"""Persist only revisioned diagnostic control, separate from service identity."""

from pathlib import Path
from typing import cast

from lcl_fastapi.runtime.state import atomic_write, is_live, read_state
from lcl_fastapi.verbose.types import FileVerboseProcess, FileVerboseState

# Unitless private runtime filenames belong to one service start; identity stays unchanged.
CONTROL_FILENAME = "logging-control.json"
OBSERVATION_DIRECTORY = "logging-observations"


def read_target(directory: Path, service_id: str) -> FileVerboseState:
    """Require valid state from the current complete service start.

    :param directory: Master-owned runtime directory.
    :param service_id: Expected complete-start identity.
    :returns: Validated target copied from storage.
    :raises OSError: If state is inaccessible, absent, malformed or belongs to another start.
    """
    record = read_state(directory / CONTROL_FILENAME)
    enabled, revision = record.get("enabled"), record.get("revision")
    if (
        record.get("service_id") != service_id
        or type(enabled) is not bool
        or type(revision) is not int
        or revision < 0
    ):
        raise OSError("file verbose control state is unavailable for this service start")
    return FileVerboseState(enabled, revision)


def write_target(directory: Path, service_id: str, state: FileVerboseState) -> None:
    """Atomically replace the target while preserving complete-start identity.

    :param directory: Master-owned runtime directory.
    :param service_id: Complete-start identity.
    :param state: Validated new target.
    :raises OSError: If writing or replacing state fails.
    """
    atomic_write(
        directory / CONTROL_FILENAME,
        {
            "service_id": service_id,
            "enabled": state.enabled,
            "revision": state.revision,
        },
    )


def process_observations(directory: Path, service_id: str) -> tuple[FileVerboseProcess, ...]:
    """Select matching live observations without claiming unobserved work has switched.

    :param directory: Master-owned runtime directory.
    :param service_id: Complete-start identity.
    :returns: Immutable live observations, ordered by PID.
    :raises OSError: If observation storage is inaccessible.
    """
    result: list[FileVerboseProcess] = []
    for path in (directory / OBSERVATION_DIRECTORY).glob("*.json"):
        record = read_state(path)
        if record.get("service_id") != service_id or not is_live(record):
            continue
        role = record.get("role")
        enabled, revision, observed_at = (
            record.get("enabled"),
            record.get("revision"),
            record.get("observed_at"),
        )
        if (
            role not in ("controller", "api")
            or type(enabled) is not bool
            or type(revision) is not int
            or revision < 0
            or type(observed_at) not in (int, float)
        ):
            continue
        result.append(
            FileVerboseProcess(
                role,
                int(str(record["pid"])),
                enabled,
                revision,
                float(cast(float, observed_at)),
            )
        )
    return tuple(sorted(result, key=lambda item: item.pid))
