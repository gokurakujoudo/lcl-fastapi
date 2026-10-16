"""Own thread-safe process caches and asynchronous service-wide control APIs."""

import asyncio
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from time import time
from typing import Literal

from lcl_fastapi.runtime.state import atomic_write, file_lock, process_identity
from lcl_fastapi.verbose.storage import (
    OBSERVATION_DIRECTORY,
    process_observations,
    read_target,
    write_target,
)
from lcl_fastapi.verbose.types import FileVerboseState, FileVerboseStatus


class FileVerboseControl:
    """Cache one service target for all threads in an actual owning process.

    :param directory: Complete service's runtime directory.
    :param service_id: Complete-start identity, never a control credential.
    :param role: Process observation role.
    """

    def __init__(
        self, directory: Path, service_id: str, role: Literal["controller", "api"]
    ) -> None:
        """Require valid shared state before admitting any new execution.

        :param directory: Complete service's runtime directory.
        :param service_id: Complete-start identity.
        :param role: Process observation role.
        :raises OSError: If current-start state cannot be loaded.
        """
        self.directory, self.service_id, self.role = directory, service_id, role
        self.identity = process_identity()
        self.lock = RLock()
        self.last_error = ""
        self.state = read_target(directory, service_id)
        self.refresh()

    def snapshot(self) -> FileVerboseState:
        """Read the last observed immutable value without filesystem access.

        :returns: Last successfully observed state.
        """
        with self.lock:
            return self.state

    def observe(self, state: FileVerboseState) -> None:
        """Publish receipt without allowing a concurrent older refresh to roll back state.

        :param state: Validated current-start state.
        :raises OSError: If observation publication fails, after retaining the cached state.
        """
        with self.lock:
            if state.revision >= self.state.revision:
                self.state = state
            current = self.state
        atomic_write(
            self.directory / OBSERVATION_DIRECTORY / f"{self.identity['pid']}.json",
            self.identity
            | {
                "service_id": self.service_id,
                "role": self.role,
                "enabled": current.enabled,
                "revision": current.revision,
                "observed_at": time(),
            },
        )

    def refresh(self) -> None:
        """Refresh through the existing sampler; failed reads leave the cache unchanged.

        :raises OSError: If reading or publishing state fails.
        """
        self.observe(read_target(self.directory, self.service_id))

    def update(self, enabled: bool) -> FileVerboseState:
        """Commit an idempotent target under the existing cross-process lock.

        :param enabled: Strict Boolean requested by the application.
        :returns: Accepted target; unchanged values retain their revision.
        :raises TypeError: If enabled is not Boolean.
        :raises OSError: If locking or shared target storage fails.
        """
        if type(enabled) is not bool:
            raise TypeError("enabled must be a Boolean")
        with file_lock(self.directory / "logging-control.lock"):
            state = read_target(self.directory, self.service_id)
            with self.lock:
                if state.revision < self.state.revision:
                    raise OSError("file verbose control revision moved backwards")
            if enabled != state.enabled:
                state = FileVerboseState(enabled, state.revision + 1)
                write_target(self.directory, self.service_id, state)
            with self.lock:
                self.state = state
        return state

    def refresh_warning(self) -> str:
        """Refresh on the owner's serialized cycle and report each changed failure once.

        :returns: A new warning, or empty text after success or an unchanged failure.
        """
        try:
            self.refresh()
        except OSError as error:
            message = f"file verbose refresh failed: {error}"
            changed = message != self.last_error
            self.last_error = message
            return message if changed else ""
        self.last_error = ""
        return ""

    def status(self) -> FileVerboseStatus:
        """Read the accepted target and identity-verified process observations.

        :returns: Target plus eventually consistent observations.
        :raises OSError: If target or observations cannot be read.
        """
        state = read_target(self.directory, self.service_id)
        return FileVerboseStatus(
            state.enabled, state.revision, process_observations(self.directory, self.service_id)
        )


# Unitless process slot; one native logger runtime owns it across all API/Worker threads.
CONTROL_LOCK = RLock()
active_control: FileVerboseControl | None = None


@contextmanager
def use_file_control(control: FileVerboseControl) -> Iterator[None]:
    """Bind a process-owned control while preserving any prior owner on exit.

    :param control: Current process's already initialized control object.
    :returns: Scope restoring the prior binding after all logging work finishes.
    """
    global active_control
    with CONTROL_LOCK:
        previous, active_control = active_control, control
    try:
        yield
    finally:
        with CONTROL_LOCK:
            active_control = previous


def current_file_control() -> FileVerboseControl:
    """Require an active owner in this actual process, including managed Worker threads.

    :returns: Shared service control object.
    :raises RuntimeError: If no current-process service scope exists.
    """
    with CONTROL_LOCK:
        control = active_control
    if control is None or control.identity["pid"] != os.getpid():
        raise RuntimeError("file verbose control requires an active service logging scope")
    return control


async def get_file_verbose() -> FileVerboseStatus:
    """Inspect the accepted service target without blocking the caller's event loop.

    :returns: Immutable target and process observations.
    :raises RuntimeError: If no service logging scope exists.
    :raises OSError: If shared state cannot be read.
    """
    return await asyncio.to_thread(current_file_control().status)


async def set_file_verbose(enabled: bool) -> FileVerboseState:
    """Submit a service-wide file target; new execution boundaries adopt observed changes.

    :param enabled: Strict Boolean desired value.
    :returns: Accepted target; this does not wait for other processes to observe it.
    :raises RuntimeError: If no service logging scope exists.
    :raises TypeError: If enabled is not Boolean.
    :raises OSError: If shared state cannot be updated.
    :raises CancelledError: After any submitted atomic update finishes.
    """
    from lcl_fastapi.verbose.submission import finish_file_update

    return await finish_file_update(current_file_control().update, enabled)
