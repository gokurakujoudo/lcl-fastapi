# 2. Monitor a directory with a live browser UI

[Series overview](../build-a-service.md) · Previous: [Product catalog](01-catalog.md) · Next: [LCL playground](03-lcl-playground.md)

Build a local directory observatory: a browser lists nested directories and files,
shows byte counts and modification times, downloads files, and uploads new ones.
Changes made outside the browser appear through server-sent events (SSE). A managed
background worker scans the filesystem; the API loop owns each browser's queue.

## 1. Install the independent project

Clone the repository, then work in `examples/directory_monitor`. The complete
[project source](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/directory_monitor)
includes the Python package, static HTML/CSS/JavaScript, configuration and verifier.

On Windows:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/lcl-fastapi serve -o config service.lclcfg
```

Open `http://127.0.0.1:18085/`. Start with the empty directory, upload a small file,
and edit it using your normal editor. Filter paths in the browser, then follow its
Download link. The bundled assets require no frontend build or CDN.

## 2. Choose the directory and scan policy

`service.lclcfg` sets `background_worker.monitor.directory: "./watched"`. Relative
paths resolve from the service working directory. The lifespan creates the root
if needed, validates entry/upload limits, and creates an `Inventory` on the API
loop before the background worker starts.

| Configuration | Default | Effect |
| --- | --- | --- |
| `background_worker.monitor.directory` | `"./watched"` | Root for scanning and file APIs |
| `background_worker.monitor.interval_seconds` | `0.5` | Delay after each completed scan; allowed 0.1–60 seconds |
| `background_worker.monitor.max_entries` | `10000` | Maximum entries in a snapshot |
| `business.max_upload_bytes` | `10485760` | Maximum bytes accepted for one upload |
| `server.workers` | `1` | One API process owns the inventory and subscribers |

Directory entries contain relative POSIX paths, file/directory kind, file byte
count (zero for directories), and `modified_ns` from filesystem metadata. The UI
counts files/directories and sums file bytes. Those totals describe the current
snapshot, not hidden or truncated entries. Scan errors and truncation are visible.

Polling keeps the example portable and does not require watcher-specific recovery.
Each scan is O(number of entries visited), up to the configured entry cap; short-lived
changes between scans can be missed. This is a current-state monitor, not an audit
log. Complete service restart applies configuration changes.

## 3. Cross the thread boundary once per snapshot

The registered synchronous `monitor(context)` entry runs outside the API loop in
its dedicated managed thread. It scans recursively, then submits an asynchronous
publication callback with `context.submit_to_service(...).result()`. Only that
callback touches asyncio queues. It waits using the cooperative `stop_event`, so
shutdown interrupts the delay. The framework drains callbacks before teardown.

The exact queue/worker implementation is maintained in the example:

<!-- example-source: examples/directory_monitor/directory_service/events.py -->
<!-- python-doc-exec -->
```python
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
```

Each subscriber has a one-element queue. If a browser is slow, publication replaces
its pending snapshot with the newest complete state. Memory cannot grow with the
number of missed revisions. This coalesces changes intentionally.

## 4. Add file APIs and SSE

| Endpoint | Observable behavior |
| --- | --- |
| `GET /api/files` | Latest recursive snapshot and revision |
| `PUT /api/files/{relative_path}` | Raw binary request body; 201 for a new file |
| `GET /api/files/{relative_path}` | Download as an attachment; 404 for missing/non-file entries |
| `GET /api/events` | `inventory` SSE events, each containing a full snapshot |

For example, from another terminal:

```sh
curl -X PUT --data-binary "hello" http://127.0.0.1:18085/api/files/notes/hello.txt
curl http://127.0.0.1:18085/api/files
curl -N http://127.0.0.1:18085/api/events
curl -OJ http://127.0.0.1:18085/api/files/notes/hello.txt
```

On PowerShell, use `curl.exe` for these commands. Duplicate uploads return 409,
oversized bodies 413, and rejected traversal/link paths 400. Nested destination
directories are created for new uploads. Raw request bodies avoid an additional
multipart dependency; browser `fetch` sends the selected File directly.

An SSE connection receives an initial snapshot, then new revisions and five-second
heartbeat comments. `EventSource` reconnects automatically; the server always
sends current state rather than retaining a replay log or honoring Last-Event-ID.
The generator unregisters its queue on disconnect/cancellation. Close the browser
tab or SSE client before stopping the service: open streams are long-lived HTTP
requests and otherwise depend on the native graceful timeout. Proxies must disable
buffering; the response includes `X-Accel-Buffering: no`.

Static assets are mounted at `/static`, with one explicit `/` HTML route. A catch-all
root mount would shadow built-in routes registered during lifespan, including
health and shutdown. Keeping the asset prefix separate preserves those routes. Dynamic filenames are inserted with DOM `textContent`,
not interpreted as HTML. Snapshot entries use relative paths; filesystem error diagnostics may include
absolute paths and are intended for the local operator.

## 5. Verify and stop

Stop your manually started service first. Then run the exact installed verifier:

```powershell
.venv\Scripts\python.exe verify.py
```

Use `.venv/bin/python verify.py` on Linux. It creates a temporary root, starts native
workers, checks static assets, upload/download, overwrite/traversal rejection,
external modification/deletion events, and graceful stop. CI runs this verifier
against the newly built framework wheel on both platforms.

To stop a manual run, use `lcl-fastapi stop -o config service.lclcfg` from the same
environment. `logs` reports API and monitor files; monitor lifecycle events are
in the controller log. The [background worker contract](../background-workers.md)
describes restart and timeout behavior.

## Filesystem boundary

Use a dedicated, trusted directory. Symlinks/junctions and special files are not
served; absolute paths and traversal are rejected. Checks assume no hostile local
process swaps filesystem entries between validation and I/O. This portable demo
is not a race-proof filesystem sandbox or an authenticated file-sharing server.
Uploads are bounded in memory and never overwrite existing files; a failed disk
write can leave an incomplete new file for the operator to remove. There is no
cross-file transactional snapshot. Keep the listener on loopback, and add reviewed
authentication/storage controls before exposing it to other users.
