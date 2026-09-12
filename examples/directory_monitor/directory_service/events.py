"""Publish thread-produced snapshots using queues owned by the API loop."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from directory_service.files import scan
from lcl_fastapi import BackgroundWorkerContext


@dataclass
class Inventory:
    root: Path
    limit: int
    snapshot: dict[str, object] = field(default_factory=lambda: {"entries": [], "errors": []})
    revision: int = 0
    subscribers: set[asyncio.Queue[dict[str, object]]] = field(default_factory=set)

    async def publish(self, snapshot: dict[str, object]) -> None:
        """Coalesce slow consumers to the newest complete snapshot."""
        if snapshot == self.snapshot:
            return
        self.snapshot = snapshot
        self.revision += 1
        for queue in self.subscribers:
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(self.current())

    def current(self) -> dict[str, object]:
        return {"revision": self.revision, **self.snapshot}


def monitor(context: BackgroundWorkerContext) -> None:
    """Scan recursively in the dedicated thread and bridge immutable snapshots."""
    inventory: Inventory = context.app.state.inventory
    interval = float(
        str(context.run(context.get_config("background_worker.monitor.interval_seconds")))
    )
    if not 0.1 <= interval <= 60:
        raise ValueError("monitor interval must be between 0.1 and 60 seconds")
    while not context.stop_event.is_set():
        snapshot = scan(inventory.root, inventory.limit)

        async def publish(snapshot: dict[str, object] = snapshot) -> None:
            await inventory.publish(snapshot)

        context.submit_to_service(publish).result()
        if context.stop_event.wait(interval):
            break
