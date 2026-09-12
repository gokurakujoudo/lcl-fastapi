"""Transfer ordered background lifecycle events to the real service controller."""

import json
import time
from pathlib import Path
from queue import Empty, SimpleQueue

import psutil

from lcl_fastapi.runtime.state import is_live


class BackgroundJournal:
    """Collect thread events and write complete JSON lines from one serialized owner.

    :param directory: Service-owned runtime directory.
    :param identity: API process identity plus complete-start service_id.
    """

    def __init__(self, directory: Path, identity: dict[str, object]) -> None:
        """Allocate an in-memory queue without creating files.

        :param directory: Verified master runtime directory.
        :param identity: API identity and service-start identifier.
        """
        self.identity = identity
        self.path = directory / "background" / f"{identity['pid']}.jsonl"
        self.queue: SimpleQueue[dict[str, object]] = SimpleQueue()
        self.sequence = 0

    def emit(self, level: int, message: str, **fields: object) -> None:
        """Enqueue an event from any worker thread without doing I/O.

        :param level: Standard logging severity.
        :param message: Lifecycle diagnostic.
        :param fields: Additional stopping deadline or completion marker.
        """
        self.queue.put({**self.identity, "level": level, "message": message, **fields})

    def flush(self) -> None:
        """Append queued records; the manager serializes calls to this single writer.

        :raises OSError: If runtime storage cannot be created or written.
        """
        events: list[str] = []
        while True:
            try:
                event = self.queue.get_nowait()
            except Empty:
                break
            self.sequence += 1
            events.append(json.dumps(event | {"sequence": self.sequence}) + "\n")
        if events:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.writelines(events)


class BackgroundEvents:
    """Consume complete journal records and retain verified process stopping deadlines.

    :param directory: Master-owned runtime directory containing background journals.
    :param service_id: Current complete-start identity.
    """

    def __init__(self, directory: Path, service_id: object) -> None:
        """Initialize controller-only offsets without opening any worker resource.

        :param directory: Current service runtime directory.
        :param service_id: Identifier rejecting events from previous starts.
        """
        self.directory, self.service_id = directory / "background", service_id
        self.offsets: dict[Path, int] = {}
        self.identities: dict[Path, dict[str, object]] = {}
        self.stopping: dict[int, dict[str, object]] = {}

    def read(self) -> list[tuple[int, str]]:
        """Consume completed lines once while retaining partial writes for the next tick.

        :returns: Ordered severity/message pairs for the controller's own logger.
        :raises OSError: If journals cannot be read.
        """
        events: list[tuple[int, str]] = []
        for path in sorted(self.directory.glob("*.jsonl")):
            with path.open("rb") as stream:
                stream.seek(self.offsets.get(path, 0))
                while line := stream.readline():
                    if not line.endswith(b"\n"):
                        break
                    self.offsets[path] = stream.tell()
                    try:
                        record = json.loads(line)
                    except ValueError, UnicodeError:
                        continue
                    if not isinstance(record, dict) or record.get("service_id") != self.service_id:
                        continue
                    self.identities[path] = record
                    events.append((int(record["level"]), str(record["message"])))
                    pid = int(record["pid"])
                    if "deadline" in record:
                        self.stopping[pid] = record
                    if record.get("retired"):
                        self.stopping.pop(pid, None)
            last = self.identities.get(path)
            if last is not None and not is_live(last):
                path.unlink(missing_ok=True)
                self.offsets.pop(path, None)
                self.identities.pop(path, None)
        return events

    def expired(self, force: bool = False) -> list[dict[str, object]]:
        """Select live stopping processes whose deadline expired, without signaling yet.

        :param force: Select all stopping workers before a native final SIGKILL sweep.
        :returns: Verified identities to log and terminate.
        """
        result: list[dict[str, object]] = []
        for pid, record in list(self.stopping.items()):
            if not is_live(record):
                del self.stopping[pid]
            elif force or time.time() >= float(str(record["deadline"])):
                result.append(record)
                del self.stopping[pid]
        return result


def terminate_background_process(record: dict[str, object]) -> None:
    """Recheck creation time immediately before killing a deadline-expired API process.

    :param record: Verified process identity from the controller's current start.
    :raises psutil.AccessDenied: If the service account cannot terminate its worker.
    """
    try:
        process = psutil.Process(int(str(record["pid"])))
        if process.create_time() == record["process_create_time"]:
            process.kill()
    except psutil.NoSuchProcess:
        pass
