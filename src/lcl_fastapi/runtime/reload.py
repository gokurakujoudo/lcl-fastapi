"""Watch explicit Python roots while native managers retain worker ownership."""

import os
import signal
from collections.abc import Generator
from pathlib import Path

import psutil
from watchfiles import Change, watch

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.controller import controller_event
from lcl_fastapi.runtime.service import shutdown_requested
from lcl_fastapi.runtime.state import atomic_write, is_live, live_workers


class ReloadWatcher:
    """Serialize change batches and graceful retirement in the master's loop.

    :param settings: Effective single-worker settings with explicit watch roots.
    :param identity: Complete service-start identity.

    Only the creating process may advance or close the watcher. Forked workers
    inherit the object but never operate its native filesystem resources.
    """

    def __init__(self, settings: Settings, identity: dict[str, object]) -> None:
        """Validate roots and prepare a bounded, recursive event iterator.

        :param settings: Effective master settings.
        :param identity: Published complete-start record.
        :raises OSError: If a selected directory is absent or inaccessible.
        """
        for directory in settings.reload_dirs:
            if not directory.is_dir():
                raise NotADirectoryError(f"server.reload_dirs: not a directory: {directory}")
            with os.scandir(directory) as entries:
                next(entries, None)
        self.settings = settings
        self.identity = identity
        self.owner = os.getpid()
        self.pending = False
        self.started = False
        self.retiring: dict[str, object] | None = None
        self.changes: Generator[set[tuple[Change, str]]] = watch(
            *settings.reload_dirs,
            watch_filter=None,
            recursive=True,
            yield_on_timeout=True,
            rust_timeout=100,
            debounce=200,
            step=50,
            debug=False,
            ignore_permission_denied=False,
        )
        # Millisecond bounds keep each native manager iteration responsive;
        # 200 ms groups editor bursts, and 100 ms bounds idle watch polling.

    def accept_change(self, change: Change, filename: str) -> bool:
        """Accept only Python paths contained in the explicitly selected roots.

        :param change: Native creation, modification, or deletion event.
        :param filename: Event path, including paths which no longer exist.
        :returns: Whether this event should trigger worker reload.
        :raises OSError: If the event path cannot be resolved.
        """
        path = Path(filename).resolve()
        return path.suffix == ".py" and any(
            path.is_relative_to(root) for root in self.settings.reload_dirs
        )

    def tick(self) -> None:
        """Consume a bounded batch and retire at most one verified worker.

        Changes during retirement remain pending until a new worker publishes
        its identity. Shutdown always takes priority over another retirement.

        :raises OSError: If watching, observation, or control storage fails.
        :raises RuntimeError: If the native watcher fails or terminates unexpectedly.
        :raises psutil.AccessDenied: If a verified worker cannot be signaled.
        """
        if os.getpid() != self.owner or shutdown_requested(self.settings.state_dir, self.identity):
            return
        try:
            changes = next(self.changes)
            if not self.started:
                controller_event("hot-reload watcher ready")
                self.started = True
            self.pending |= any(
                self.accept_change(change, filename) for change, filename in changes
            )
        except StopIteration as error:
            raise RuntimeError("hot reload watcher stopped unexpectedly") from error
        if self.retiring is not None and is_live(self.retiring):
            return
        workers = live_workers(self.settings.worker_state_dir, self.identity["service_id"])
        if not workers:
            return
        self.retiring = None
        if self.pending and not shutdown_requested(self.settings.state_dir, self.identity):
            worker = workers[0]
            if self.identity["runtime"] == "uvicorn":
                atomic_write(self.settings.state_dir / "reload.json", worker)
            else:
                try:
                    process = psutil.Process(int(str(worker["pid"])))
                    if process.create_time() != worker["process_create_time"]:
                        return
                    process.send_signal(signal.SIGTERM)
                except psutil.NoSuchProcess:
                    return
            controller_event(f"hot-reload retiring worker_pid={worker['pid']}")
            self.retiring = worker
            self.pending = False

    def close(self) -> None:
        """Release the native watch iterator in its owning master only."""
        if os.getpid() == self.owner:
            self.changes.close()
