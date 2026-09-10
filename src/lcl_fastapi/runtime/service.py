"""Own service-start identity independently of worker configuration reloads."""

import os
import secrets
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.access import private_file
from lcl_fastapi.runtime.state import atomic_write, file_lock, is_live, process_identity, read_state


@contextmanager
def service_runtime(settings: Settings) -> Iterator[dict[str, object]]:
    """Hold exclusive service ownership and remove this start's state on exit.

    :param settings: Master settings loaded before launching native workers.
    :returns: Context manager yielding the non-secret service identity.
    :raises OSError: If another master owns the directory or storage fails.
    :raises subprocess.CalledProcessError: If Windows token permissions fail.

    Forked children never clean the master's inherited service scope.
    """
    directory = settings.state_dir
    with file_lock(directory / "service.lock", blocking=False):
        creator_pid = os.getpid()
        identity = process_identity()
        identity.update(
            {
                "service_id": secrets.token_hex(16),
                "name": settings.app_name,
                "version": settings.app_version,
                "runtime": "uvicorn" if os.name == "nt" else "gunicorn",
                "host": settings.host,
                "port": settings.port,
                "configured_workers": settings.workers,
                "worker_state_dir": str(settings.worker_state_dir),
                "sample_interval_seconds": settings.sample_interval_seconds,
                "graceful_timeout_seconds": settings.graceful_timeout_seconds,
                "started_at": time.time(),
            }
        )
        token_path = directory / "control.token"
        runtime_path = directory / "runtime.json"
        try:
            (directory / "shutdown.json").unlink(missing_ok=True)
            token_path.touch()
            private_file(token_path)
            token_path.write_text(secrets.token_urlsafe(32), encoding="ascii")
            atomic_write(runtime_path, identity)
            settings.pid_file.parent.mkdir(parents=True, exist_ok=True)
            settings.pid_file.write_text(str(identity["pid"]), encoding="ascii")
            yield identity
        finally:
            if os.getpid() == creator_pid:
                if read_state(runtime_path).get("service_id") == identity["service_id"]:
                    cleanup_stale(settings)
                    runtime_path.unlink(missing_ok=True)
                    (directory / "shutdown.json").unlink(missing_ok=True)
                    settings.pid_file.unlink(missing_ok=True)
                token_path.unlink(missing_ok=True)


def cleanup_stale(settings: Settings) -> None:
    """Remove dead worker observations and abandoned leases at master exit.

    :param settings: Master-owned local directories.
    :raises OSError: If storage cannot be inspected or cleaned.
    """
    with file_lock(settings.state_dir / "leases.lock"):
        for directory in (settings.worker_state_dir, settings.state_dir / "leases"):
            for path in directory.glob("*.json"):
                if not is_live(read_state(path)):
                    path.unlink(missing_ok=True)


def shutdown_requested(directory: Path, identity: dict[str, object]) -> bool:
    """Check whether an accepted shutdown belongs to this complete start.

    :param directory: Master-owned state directory.
    :param identity: Current complete-start record.
    :returns: Whether the matching shutdown marker has been published.
    :raises OSError: If the marker cannot be read.
    """
    return read_state(directory / "shutdown.json").get("service_id") == identity["service_id"]
