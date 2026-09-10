"""Allocate Snowflake worker numbers inside one local service directory."""

from dataclasses import dataclass
from pathlib import Path

from lcl_fastapi.runtime.state import atomic_write, file_lock, is_live, process_identity, read_state


@dataclass(slots=True)
class WorkerIdLease:
    """Own one live process lease; release is idempotent.

    :param worker_id: Integer within the configured Snowflake range.
    :param path: Persisted lease observation.
    :param identity: Owner's PID and process creation timestamp.
    """

    worker_id: int
    path: Path
    identity: dict[str, object]

    @classmethod
    def acquire(
        cls,
        *,
        state_dir: Path,
        worker_id_base: int,
        worker_id_count: int,
    ) -> WorkerIdLease:
        """Claim the first available number while holding the allocation lock.

        :param state_dir: State directory dedicated to one local service.
        :param worker_id_base: Inclusive starting integer from zero to 1023.
        :param worker_id_count: Positive number of IDs in the allowed range.
        :returns: Lease owned by the current process.
        :raises ValueError: If the range exceeds Snowflake's ten worker bits.
        :raises RuntimeError: If every number has a live owner.
        :raises OSError: If lease storage cannot be accessed.
        """
        if (
            type(worker_id_base) is not int
            or type(worker_id_count) is not int
            or worker_id_base < 0
            or worker_id_count < 1
            or worker_id_base + worker_id_count > 1024
        ):
            raise ValueError("invalid Snowflake worker ID range")
        with file_lock(state_dir / "leases.lock"):
            for worker_id in range(worker_id_base, worker_id_base + worker_id_count):
                path = state_dir / "leases" / f"{worker_id}.json"
                if not is_live(read_state(path)):
                    identity = process_identity()
                    atomic_write(path, identity)
                    return cls(worker_id, path, identity)
        raise RuntimeError("all configured Snowflake worker IDs are leased")

    def release(self) -> None:
        """Remove this lease only if its persisted identity is still ours.

        :raises OSError: If the lease cannot be inspected or removed.
        """
        with file_lock(self.path.parent.parent / "leases.lock"):
            if read_state(self.path) == self.identity:
                self.path.unlink(missing_ok=True)
