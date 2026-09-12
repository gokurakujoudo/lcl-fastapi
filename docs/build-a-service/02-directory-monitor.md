# 2. Monitor a directory with a live browser UI

[Series overview](../build-a-service.md) · Previous: [Product catalog](01-catalog.md) · Next: [LCL playground](03-lcl-playground.md)

## Background and objective

Imagine a local folder where an export job, a developer and a browser all create
files. A request can list that folder once, but it cannot tell an already-open
browser about the next change. Scanning the whole directory inside every request
also makes response time depend on disk speed and directory size.

The objective is to build a directory observatory with three cooperating parts:
a managed background worker discovers changes, the API owns the latest inventory,
and a static browser displays that inventory through server-sent events (SSE).
The same API accepts new files and serves downloads, including nested paths.
This is useful for inspecting a trusted local workspace without a database or a
frontend build tool. Polling intentionally reports current state, not every edit.

## What you will learn

| You will build | lcl-fastapi feature you will practice |
| --- | --- |
| An importable application with operator settings | `LclFastAPI`, `app.target`, `.lclcfg`, universal defaults and CLI overrides |
| One inventory shared by routes and the monitor | Business lifespan, `get_config()` and `app.state` resource ownership |
| A scanner that does not run on the API loop | `background_workers`, `BackgroundWorkerContext`, per-worker configuration and cooperative stop |
| Safe publication from a thread to SSE subscribers | `submit_to_service()` and the boundary between worker and API event loops |
| File routes and a browser served by the same service | Retained FastAPI request/response APIs, static mounting and framework route coexistence |
| An observable, stoppable service | Health/status, request IDs, separate worker logs, restart switches and the native CLI |

SSE framing, filesystem checks and the HTML/JavaScript are application code.
lcl-fastapi supplies configuration, lifecycle and supervision around them; it
does not supply a file-storage or SSE framework.

## Before you start

Use CPython 3.14 on Windows or Linux. Chapter 1 explains basic routes and the CLI;
you can still follow this chapter independently with basic async Python knowledge.
Stop other lcl-fastapi services first, reserve loopback port `18085`, and use a
dedicated folder containing disposable files. Installation needs package-index
access; the running application needs no external account.

Work through the following steps in the checked-out example. The repository gives
you the filesystem and frontend scaffolding, so you can concentrate on assembling
the framework integration. The Python modules shown below are executable, and
the final installed application has its own real-process verifier.

## 1. Set up the project and observe the finished workflow

Clone the repository, then work in `examples/directory_monitor`. The complete
[project source](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/directory_monitor)
includes the Python package, static HTML/CSS/JavaScript, configuration and verifier.

On Windows:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/lcl-fastapi serve -o config service.lclcfg
```

Open `http://127.0.0.1:18085/`. Start with the empty directory, upload a small file,
and edit it using your normal editor. Filter paths in the browser, then follow its
Download link. The bundled assets require no frontend build or CDN.

The editable installation makes subsequent Python edits visible to the installed
entry point. Keep commands in `examples/directory_monitor`; the configuration
path and your application-relative `./watched` path will then have the same base.
Use a second terminal for HTTP and management commands. In later shell examples,
`lcl-fastapi` means `.venv\Scripts\lcl-fastapi.exe` on Windows or
`.venv/bin/lcl-fastapi` on Linux; PowerShell users should call `curl.exe`.

Inspect the project responsibilities before changing it:

| File | Responsibility |
| --- | --- |
| `service.lclcfg` | Operator-selected paths, limits, address and logging |
| `directory_service/app.py` | Lifespan, registration, routes and static mount |
| `directory_service/events.py` | Inventory publication, monitor entry and stream lifecycle |
| `directory_service/files.py` | Traversal validation, recursive scan and non-overwriting save |
| `directory_service/static/` | Browser rendering, uploads and EventSource subscription |
| `verify.py` | Installed-service checks with a temporary directory |

**Checkpoint:** upload one file in the browser and download it again. The inventory
should update after a scan; the upload response itself does not publish an SSE
event. Close the browser and stop the service before the configuration exercises.

## 2. Connect the application to LCL configuration

Start with this complete `service.lclcfg`:

```text
__LCL_VERSION__: 1
using f"{lcl_fastapi_defaults}"
app.name: "directory-monitor"
app.version: "1.0.0"
app.target: "directory_service.app:service"
server.host: "127.0.0.1"
server.port: 18085
server.workers: 1
logger.file.default.directory: "./logs"
background_worker.monitor.directory: "./watched"
background_worker.monitor.interval_seconds: 0.5
background_worker.monitor.max_entries: 10000
business.max_upload_bytes: 10485760
```

`app.target` tells the CLI to import `directory_service.app` and retrieve its
`service` object. `app.name` identifies this downstream application; its version
is independent of the installed lcl-fastapi distribution. `using` imports the
framework defaults before your local overrides, retaining health, docs, request
IDs and runtime/logging settings you did not override.

Put scanner-specific values under `background_worker.monitor`, matching the
Python registration name you will use in step 3. `business.max_upload_bytes`
belongs to the HTTP application. These arbitrary application values keep native
LCL expression semantics; the application must still validate its own ranges.
See the [configuration reference](../configuration.md) for precedence.

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

**Try a launch-only override:** start with
`lcl-fastapi serve -o config service.lclcfg -o background_worker.monitor.interval_seconds "LCL[2]"`.
Make two changes more than two seconds apart. Updates now follow the slower scan
cadence; `service.lclcfg` remains unchanged. Stop and start without the override
to restore the file's value. File edits require a complete service restart.

## 3. Initialize resources in lifespan and register the monitor

Build the core of `directory_service/app.py` around an asynchronous context manager.
The existing `Inventory` and `monitor` helpers are explained in the next step.
This is the complete lifecycle/registration portion; retain the file's routes
and static mount when working with the finished project.

<!-- python-doc-exec -->
```python
import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from directory_service.events import Inventory, monitor
from fastapi import FastAPI
from lcl_fastapi import LclFastAPI, get_config


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configured = Path(str(await get_config("background_worker.monitor.directory")))
    root = await asyncio.to_thread(configured.resolve)
    await asyncio.to_thread(root.mkdir, parents=True, exist_ok=True)
    limit = int(str(await get_config("background_worker.monitor.max_entries")))
    maximum = int(str(await get_config("business.max_upload_bytes")))
    if not 1 <= limit <= 100000 or not 1 <= maximum <= 104857600:
        raise ValueError("Invalid directory entry or upload limit")
    app.state.inventory = Inventory(root, limit)
    app.state.max_upload = maximum
    yield
    app.state.inventory.subscribers.clear()


service = LclFastAPI(lifespan=lifespan, background_workers={"monitor": monitor})
```

Follow the startup sequence in this code:

1. The CLI imports the application without opening the directory or reading
   business configuration. `LclFastAPI(...)` registers intent at this point.
2. The real API worker opens its framework configuration/logger, then enters your
   lifespan. `await get_config(...)` is now valid; it would fail at module import.
3. Resolve/create the root using `asyncio.to_thread`, validate limits, and assign
   `app.state.inventory`. Routes and the monitor borrow this lifespan-owned object.
4. Yield to let the framework start the registered monitor and serve requests.
   The string `"monitor"` connects registration, configuration, status and logs;
   configuration alone cannot supply executable code or start a new worker.
5. On stop, the framework signals and joins background workers before resuming
   business teardown. The subscriber collection is cleared while the API loop
   still exists; framework resources are closed afterwards.

The registry requires one API worker even if the monitor is disabled. When a
larger valid count is requested, lcl-fastapi warns and uses one; it does not edit
the configuration file. That rule also keeps this single inventory coherent.

**Checkpoint:** restart normally and call `GET /health` and `lcl-fastapi status -o
config service.lclcfg`. Check the effective worker count and the monitor's state.
Temporarily set an invalid entry limit, stop and restart, and observe startup
failure before the scanner starts. Restore the valid value before continuing.

## 4. Scan in the background and publish on the API loop

The registered synchronous `monitor(context)` entry runs outside the API loop in
its dedicated managed thread. It scans recursively, then submits an asynchronous
publication callback with `context.submit_to_service(...).result()`. Only that
callback touches asyncio queues. It waits using the cooperative `stop_event`, so
shutdown interrupts the delay. The framework drains callbacks before teardown.

Read `monitor()` first in the code below, then `Inventory.publish()`:

1. `context.app.state` retrieves the initialized inventory. Its root and entry
   limit are startup values; the thread must not mutate subscriber queues.
2. The synchronous entry uses `context.run(context.get_config(...))` to evaluate
   its configuration on its own event loop. An async entry would directly await
   `context.get_config(...)`; do not nest `run()` inside an async entry.
3. `scan()` performs blocking directory I/O on the dedicated worker thread.
4. `submit_to_service(publish).result()` waits for a callback on the API loop.
   That callback can safely use the API's asyncio queues. Do not make the callback
   wait synchronously for this worker, which would create a deadlock.
5. `stop_event.wait(interval)` pauses cooperatively. Unlike `time.sleep`, the
   wait ends promptly when the framework requests shutdown.

The response keeps one persistent disconnect listener. It never polls by cancelling
`receive()`: Gunicorn 26.2.0 treats that cancellation as a closed connection. For
ASGI versions before 2.4, Starlette already supplies the listener; newer versions
use the small response subclass below. A real disconnect cancels the stream and
removes its subscription.

The exact queue/worker implementation is maintained in the example:

<!-- example-source: examples/directory_monitor/directory_service/events.py -->
<!-- python-doc-exec -->
```python
"""Publish thread-produced snapshots using queues owned by the API loop."""

import asyncio
from dataclasses import dataclass, field
from pathlib import Path

from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from directory_service.files import scan
from lcl_fastapi import BackgroundWorkerContext


class EventStream(StreamingResponse):
    """Wait for real disconnects without cancelling receive between snapshots."""

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        version = tuple(map(int, scope.get("asgi", {}).get("spec_version", "2.0").split(".")))
        if version < (2, 4):
            # Starlette already owns a persistent disconnect listener here.
            await super().__call__(scope, receive, send)
            return
        async with asyncio.TaskGroup() as tasks:
            response = tasks.create_task(super().__call__(scope, receive, send))
            disconnected = tasks.create_task(self.listen_for_disconnect(receive))
            _, pending = await asyncio.wait(
                (response, disconnected), return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending:
                task.cancel()


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

**Checkpoint:** create and then delete a nested file outside the browser. Verify
both revisions arrive without a browser refresh. The scan may block on disk, but
its I/O does not run in the API event loop. Threads can still contend for the GIL,
shared locks and machine resources; this is not an independent process sandbox.

## 5. Add HTTP routes around the shared inventory

In `directory_service/app.py`, follow how the route decorators use the same
`service` created above. lcl-fastapi retains FastAPI's route and Request APIs:

1. `@service.get("/api/files")` reads `request.app.state.inventory` and returns
   its current snapshot. It does not rescan the disk on every request.
2. The upload route consumes `request.stream()`, checks the configured byte limit,
   then awaits `asyncio.to_thread(save, ...)`. Its raw body avoids multipart
   dependencies, while file I/O stays outside the API event loop.
3. The download route validates a relative path and returns `FileResponse` with
   an attachment filename. Use framework-compatible response objects directly.
4. The event route gives each request a queue, immediately inserts the current
   snapshot, then yields `id`, `event` and JSON `data` fields as SSE frames.
   Its `finally` block removes the queue even if cancellation interrupts a wait.

Intentional input failures use `HTTPException` (400/403/404/409/413). They retain
FastAPI handling instead of becoming unexpected server errors. Unhandled bugs
use lcl-fastapi's default detailed error logging and generic 500 response; see
[HTTP error handling](../application.md).

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

## 6. Connect the static frontend to the service

Open `directory_service/static/app.js` and follow this browser sequence:

1. Construct an `EventSource("/api/events")` on the same origin as the page.
2. Listen for the named `inventory` event, parse its JSON, and replace the displayed
   snapshot. Render totals and apply the path filter to that latest snapshot.
3. Send the selected File as a raw `fetch` PUT body to its chosen relative path.
   Let the next background scan update the list instead of inventing a local
   revision that could disagree with the server.
4. Build download links to the GET file route and show reconnect/scan errors so
   stale state is visible to the operator.

In `app.py`, `@service.get("/", include_in_schema=False)` serves `index.html` with
`FileResponse`, and `service.mount("/static", StaticFiles(...))` serves its assets.
Paths are based on the installed package's `__file__`, not the caller's shell.
Static assets are mounted at `/static`, with one explicit `/` HTML route. A catch-all
root mount would shadow built-in routes registered during lifespan, including
health and shutdown. Keeping the asset prefix separate preserves those routes. Dynamic filenames are inserted with DOM `textContent`,
not interpreted as HTML. Snapshot entries use relative paths; filesystem error diagnostics may include
absolute paths and are intended for the local operator.

**Checkpoint:** open `/docs` and `/health` after loading the UI. Both framework
endpoints must still work. Reload the browser during filesystem changes; its new
SSE connection should receive a complete current snapshot without replaying old
edits. The browser UI and these built-in endpoints share one service.

## 7. Operate, inspect logs and test worker switches

Use a second terminal while the service is running:

```sh
lcl-fastapi status -o config service.lclcfg
lcl-fastapi logs -o config service.lclcfg
curl -i http://127.0.0.1:18085/api/files
```

`status` reports API and background observations, including execution/restart
counts. `logs` lists actual file paths, including rotated files. The monitor's
named source uses `logger.file.monitor`, inheriting `logger.file.default`; API
request logs and monitor logs are separate. Monitor lifecycle events also appear
in the controller log. The `X-Request-ID` response header lets you find the file
request in the API log; a long-lived SSE request's access entry completes when
that request finishes.

Close browser/SSE clients, stop, and try one switch at a time by adding it to the
configuration before restarting:

| Exercise | What to observe and why |
| --- | --- |
| `background_worker.monitor.enabled: False` | File APIs still work, but the inventory does not track disk changes; a disabled worker opens no dedicated log file. |
| `background_worker.monitor.auto_restart: False` with an invalid interval | The monitor records failure once and stays failed; resource initialization succeeded, but the entry failed. Restore a valid interval afterwards. |
| The same invalid interval with `auto_restart: True` | Execution/restart counts advance with a one-second restart delay and logged errors; stop interrupts the cycle. |

Remove these exercise overrides and restart with the valid defaults. `enabled`
and `auto_restart` must be LCL Boolean values, not quoted strings. Normal returns
also restart by default; this cooperative monitor normally returns only on stop.
No worker restart reloads the config file. See the
[background worker contract](../background-workers.md) for all lifecycle states.

## 8. Verify the installed composition and shut down

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
