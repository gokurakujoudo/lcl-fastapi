# 2. Build a directory monitor with a live browser UI

[Series overview](../build-a-service.md) · Previous: [Product catalog](01-catalog.md) · Next: [LCL playground](03-lcl-playground.md)

The [complete working code](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/directory_monitor)
is available for reference. This guide starts from an empty directory and provides
every file you need; create and extend them in the order shown.

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

Use CPython 3.14 on Windows or Linux and basic async Python. Stop any other
lcl-fastapi service: run one service at a time on the machine. Installation needs
package-index access, but the finished application needs no external account.
Keep the listener on loopback and use trusted local input.

## 1. Create the application package

Create an empty project directory. No repository checkout is needed.

On Windows:

```powershell
mkdir directory-observatory
cd directory-observatory
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install lcl-fastapi==0.3.0
mkdir directory_service
mkdir directory_service\static
```

On Linux:

```sh
mkdir directory-observatory
cd directory-observatory
python3.14 -m venv .venv
.venv/bin/python -m pip install lcl-fastapi==0.3.0
mkdir -p directory_service/static
```

Keep subsequent files and commands in this directory. Later commands use
`lcl-fastapi` and `python` as shorthand for `.venv\Scripts\lcl-fastapi.exe` and
`.venv\Scripts\python.exe` on Windows, or `.venv/bin/lcl-fastapi` and
`.venv/bin/python` on Linux. In PowerShell use `curl.exe` instead of `curl`.
Use a second terminal for HTTP and management commands while the server runs.

Create `directory_service/__init__.py` to make an importable package:

<!-- tutorial-file: directory_service/__init__.py -->
<!-- python-doc-exec -->
```python
"""Recursive directory monitor tutorial package."""
```

Create `pyproject.toml` so the application and its static files can be installed together:

<!-- tutorial-file: pyproject.toml -->
```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "lcl-fastapi-directory-example"
version = "1.0.0"
requires-python = ">=3.14"
dependencies = ["lcl-fastapi==0.3.0"]

[tool.hatch.build.targets.wheel]
packages = ["directory_service"]
```

Install this local project with `python -m pip install -e .`.
The editable install lets later Python changes take effect after a service restart.
The dependency installs the framework; the new package contains your application.
Do not start the service until the step that creates its `service` object.

## 2. Define operator configuration

Create `service.lclcfg`. Comments and blank lines group settings by function;
the dotted keys are native LCL scopes, not JSON objects that you must assemble.

<!-- tutorial-file: service.lclcfg -->
```text
__LCL_VERSION__: 1

# Shared configuration
using f"{lcl_fastapi_defaults}"

# Application identity and import target
app.name: "directory-monitor"
app.version: "1.0.0"
app.target: "directory_service.app:service"

# HTTP serving and shutdown
server.host: "127.0.0.1"
server.port: 18085
server.workers: 1

# Logging and file destinations
logger.file.default.directory: "./logs"

# Managed background tasks
background_worker.monitor.directory: "./watched"
background_worker.monitor.interval_seconds: 0.5
background_worker.monitor.max_entries: 10000

# Business settings
business.max_upload_bytes: 10485760
```

`using` imports the framework defaults before your overrides. You keep the
default health/docs routes, request IDs and shutdown behavior while choosing a
local address. `app.target` means “import `directory_service.app`, then get its
`service` object”; you will create that object in step 5.

The monitor name will be the key in the Python worker registry, so its settings
live under `background_worker.monitor`. `business.max_upload_bytes` belongs to
the HTTP application. Both use ordinary LCL expressions, but your code must
validate application-specific ranges. Relative `./watched` resolves from the
service working directory; keep it alongside this configuration for the guide.

**Checkpoint:** inspect your files: the package, metadata and configuration now
exist. No folder scan, log file or thread should have started merely from creating
or installing the package. Resource initialization belongs in lifespan.

## 3. Implement bounded filesystem operations

Create `directory_service/files.py`. First, make file paths relative to a trusted
root and reject traversal, links and junctions. Then scan nested entries into a
bounded snapshot, keeping errors visible. Finally, create new files with `xb`
so an upload cannot silently overwrite an existing file.

<!-- tutorial-file: directory_service/files.py -->
<!-- python-doc-exec -->
```python
"""Filesystem operations kept outside the API event loop."""

import os
import stat
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


def within(root: Path, name: str) -> Path:
    """Accept relative POSIX paths without links, junctions or traversal."""
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\\" in name or ":" in name:
        raise HTTPException(400, "Use a relative path with forward slashes")
    if any(part in {"..", "."} for part in name.split("/")):
        raise HTTPException(400, "Path traversal is not allowed")
    path = root
    for part in parts:
        path /= part
        if path.is_symlink() or path.is_junction():
            raise HTTPException(400, "Links and junctions are not served")
    if not path.resolve().is_relative_to(root):
        raise HTTPException(400, "Path is outside the configured directory")
    return path


def scan(root: Path, limit: int) -> dict[str, object]:
    """Return a bounded recursive snapshot, retaining errors as visible state."""
    entries: list[dict[str, object]] = []
    errors: list[str] = []
    if not root.is_dir():
        return {
            "entries": [],
            "errors": ["Configured directory is unavailable"],
            "truncated": False,
        }

    def failed(error: OSError) -> None:
        errors.append(str(error))

    for parent, directories, files in os.walk(root, followlinks=False, onerror=failed):
        directories[:] = sorted(
            name
            for name in directories
            if not (Path(parent) / name).is_symlink() and not (Path(parent) / name).is_junction()
        )
        for name in sorted([*directories, *files]):
            path = Path(parent) / name
            try:
                info = path.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    continue
                if path.is_junction():
                    continue
                if len(entries) >= limit:
                    return {"entries": entries, "errors": errors, "truncated": True}
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "kind": "directory" if path.is_dir() else "file",
                        "bytes": info.st_size if path.is_file() else 0,
                        "modified_ns": info.st_mtime_ns,
                    }
                )
            except OSError as error:
                failed(error)
    return {"entries": entries, "errors": errors, "truncated": False}


def save(root: Path, name: str, content: bytes) -> None:
    """Create a new file, refusing to overwrite an existing entry."""
    path = within(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError as error:
        raise HTTPException(409, "File already exists") from error
```

These functions are synchronous because filesystem work is blocking. You will
call `scan()` in a managed background thread, and route-triggered `save()` through
`asyncio.to_thread`. Merely adding `async def` would not make filesystem calls
nonblocking. Entries carry relative paths, kind, bytes and modification time.

**Checkpoint:** save this small check as a separate scratch file and run it with
`python`. It verifies nested paths and cleanup without starting the service:

<!-- python-doc-exec -->
```python
from pathlib import Path
from tempfile import TemporaryDirectory
from directory_service.files import save, scan

with TemporaryDirectory() as directory:
    root = Path(directory).resolve()
    save(root, "notes/hello.txt", b"hello")
    entries = scan(root, 100)["entries"]
    assert any(row["path"] == "notes/hello.txt" and row["bytes"] == 5 for row in entries)
```

## 4. Add a managed monitor and an API-owned inventory

Create `directory_service/events.py`. The `Inventory` stores the latest complete
snapshot and a queue for each browser. The synchronous `monitor(context)` entry
runs on its own lcl-fastapi background thread; it must not mutate asyncio queues
owned by the API loop.

Read the worker from bottom to top after entering it: `context.run(...)` resolves
its configuration on its own loop, `scan()` performs disk I/O in its thread, and
`submit_to_service(publish).result()` waits for publication on the API loop.
`stop_event.wait(interval)` makes the delay interruptible at shutdown.

<!-- tutorial-file: directory_service/events.py -->
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

`publish()` replaces a slow browser's pending item with the latest snapshot.
The queue cannot grow with missed revisions; this is current-state observation,
not an audit log. Polling can miss short-lived edits between scans.

`EventStream` will be used in step 7. It keeps one persistent disconnect listener.
Polling `request.is_disconnected()` cancels receive calls, which Gunicorn 26.2.0
can interpret as a closed connection. On ASGI before 2.4, Starlette already owns
that listener; on newer ASGI versions the small response class supplies it.

The API callback must remain nonblocking and must not wait synchronously for the
monitor that submitted it. That would deadlock. Threads still share the GIL,
locks and machine resources; see [background workers](../background-workers.md).

## 5. Initialize the inventory and serve its first route

Create `directory_service/app.py` with the following code. It contains the imports
needed by later steps, the lifespan, worker registration and one inventory route.
Do not create the inventory or read `get_config()` at module import: the framework
configuration context is only active once a real API worker enters lifespan.

<!-- tutorial-file: directory_service/app.py -->
<!-- python-doc-exec -->
```python
"""Static browser UI, file APIs and background-driven server-sent events."""

import asyncio
import json
import stat
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, Response, StreamingResponse
from starlette.staticfiles import StaticFiles

from directory_service.events import EventStream, Inventory, monitor
from directory_service.files import save, within
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
service.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"]
)


@service.get("/api/files")
async def files(request: Request) -> dict[str, object]:
    inventory: Inventory = request.app.state.inventory
    return inventory.current()


```

Before `yield`, resolve/create the root, validate limits, and assign the shared
resource to `app.state`. `LclFastAPI(lifespan=..., background_workers={...})` starts
the registered monitor only after business initialization succeeds. Configuration
chooses parameters; it never supplies executable code. The nonempty registry
requires one effective API worker, even when disabled; a larger valid count is
warned about and overridden without rewriting the configuration.

After `yield`, business teardown runs after background workers have stopped.
That lets routes and the monitor borrow the same inventory without owning its
lifetime. The request obtains it from `request.app.state`, not a new global scan.

Start the service:

```sh
lcl-fastapi serve -o config service.lclcfg
```

In another terminal:

```sh
curl http://127.0.0.1:18085/health
curl http://127.0.0.1:18085/api/files
lcl-fastapi status -o config service.lclcfg
```

**Checkpoint:** health is 200, status shows one API worker and the monitor, and
the inventory eventually contains files you create in `watched`. Initially it
may be empty until the first scan. Stop with `lcl-fastapi stop -o config
service.lclcfg` before editing the app; restart after each following Python step.

## 6. Add uploads and downloads

Append these routes to `directory_service/app.py`:

<!-- tutorial-append: directory_service/app.py -->
<!-- python-doc-fragment -->
```python
@service.put("/api/files/{name:path}", status_code=201)
async def upload(name: str, request: Request) -> dict[str, str]:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin writes are not allowed")
    chunks = bytearray()
    async for chunk in request.stream():
        chunks.extend(chunk)
        if len(chunks) > request.app.state.max_upload:
            raise HTTPException(413, "Upload exceeds configured limit")
    inventory: Inventory = request.app.state.inventory
    await asyncio.to_thread(save, inventory.root, name, bytes(chunks))
    return {"path": name}


@service.get("/api/files/{name:path}")
async def download(name: str, request: Request) -> Response:
    inventory: Inventory = request.app.state.inventory

    def checked() -> Path:
        path = within(inventory.root, name)
        if not path.exists() or not stat.S_ISREG(path.stat().st_mode):
            raise HTTPException(404, "File not found")
        return path

    path = await asyncio.to_thread(checked)
    return FileResponse(path, filename=path.name, media_type="application/octet-stream")


```

The decorators and `Request`, `HTTPException` and `FileResponse` retain normal
FastAPI behavior. Uploads consume a bounded raw request body; no multipart package
is required. Intentional bad input returns its explicit HTTP status, while an
unexpected bug uses lcl-fastapi's detailed error log and generic 500 response.

Restart and try the first upload/download (PowerShell: `curl.exe`):

```sh
curl -i -X PUT --data-binary "hello" http://127.0.0.1:18085/api/files/notes/hello.txt
curl http://127.0.0.1:18085/api/files/notes/hello.txt
```

**Checkpoint:** the upload returns 201 and the download contains `hello`. Repeat
the PUT and expect 409, because this service never overwrites. Missing files return
404, oversized uploads 413, and rejected paths 400. The monitor, not the upload
handler, eventually refreshes the inventory. Use `curl -OJ` to save an attachment.

## 7. Stream background changes over SSE

Append the event route to `directory_service/app.py`:

<!-- tutorial-append: directory_service/app.py -->
<!-- python-doc-fragment -->
```python
@service.get("/api/events")
async def events(request: Request) -> StreamingResponse:
    inventory: Inventory = request.app.state.inventory

    async def stream() -> AsyncIterator[str]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
        inventory.subscribers.add(queue)
        queue.put_nowait(inventory.current())
        try:
            while True:
                try:
                    snapshot = await asyncio.wait_for(queue.get(), timeout=5)
                    yield (
                        f"id: {snapshot['revision']}\nevent: inventory\n"
                        f"data: {json.dumps(snapshot)}\n\n"
                    )
                except TimeoutError:
                    yield ": heartbeat\n\n"
        finally:
            inventory.subscribers.discard(queue)

    return EventStream(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


```

Each HTTP connection registers one queue, receives the current snapshot, then
waits for publications from the background worker. The `finally` block removes
that subscription on disconnect or cancellation. Five-second comments keep idle
streams active; each inventory event has a revision ID and complete JSON state.

Restart, run `curl -N http://127.0.0.1:18085/api/events`, and edit then delete
`watched/notes/hello.txt` in another terminal. **Checkpoint:** both changes arrive
on the existing connection without a refresh. Stop curl before stopping the
service: an open stream is a live HTTP request and otherwise uses the native
graceful timeout. Reconnecting always sends current state; it does not replay a
Last-Event-ID history. A proxy must not buffer this response.

## 8. Build the browser UI and serve static assets

Create the following three files under `directory_service/static`. The HTML
defines the upload controls, totals, table and connection status. The CSS is only
presentation; expand its complete source to copy it.

<details>
<summary>Complete directory_service/static/index.html</summary>

<!-- tutorial-file: directory_service/static/index.html -->
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Directory Observatory</title><link rel="stylesheet" href="/static/style.css"><script src="/static/app.js" defer></script></head>
<body><header><div><div class="eyebrow">Build a service · Example 02</div><h1>Directory Observatory</h1><p>A live view of your configured directory, down to the last file.</p></div><span class="badge" id="connection">Connecting to live updates…</span></header>
<main><section class="cards" aria-label="Directory statistics"><div class="card"><small>Files</small><strong id="file-count">0</strong></div><div class="card"><small>Directories</small><strong id="dir-count">0</strong></div><div class="card"><small>Total file bytes</small><strong id="bytes">0</strong></div><div class="card"><small>Snapshot revision</small><strong id="revision">0</strong></div></section>
<section class="panel"><h2>Directory contents</h2><div class="toolbar"><input class="search" id="filter" aria-label="Filter paths" placeholder="Filter paths, including nested folders…"><button id="refresh">Refresh snapshot</button></div><div class="scroll"><table><thead><tr><th>Relative path</th><th>Kind</th><th>Bytes</th><th>Last modified</th><th>Action</th></tr></thead><tbody id="entries"></tbody></table></div><p id="empty" class="muted">Waiting for the first snapshot…</p></section>
<section class="panel"><h2>Add a file</h2><p class="muted">Files are created under the configured root. Existing files are never overwritten.</p><form id="upload"><div class="toolbar"><input id="file" type="file" required aria-label="Choose a file"><label for="destination">Relative destination</label><input class="search" id="destination" required placeholder="notes/report.txt"><button class="primary" type="submit">Upload file</button></div></form><div id="message" class="notice" role="status">Choose a file, or edit the watched directory on disk to see live updates.</div></section>
<footer>Local, trusted-directory demo · Recursive polling runs in a managed background thread · SSE sends complete snapshots and reconnects automatically.</footer></main></body></html>
```

</details>

<details>
<summary>Complete directory_service/static/style.css</summary>

<!-- tutorial-file: directory_service/static/style.css -->
```css
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#182b43;background:#f4f6fa;font-size:15px}*{box-sizing:border-box}body{margin:0}header{background:#152a43;color:white;padding:26px max(5vw,20px);display:flex;justify-content:space-between;align-items:center;gap:20px}h1{font-size:27px;margin:6px 0}h2{font-size:18px;margin:0 0 16px}p{line-height:1.55}.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#9db7d6}header p{margin:0;color:#c5d4e4}.badge{border:1px solid #49647f;border-radius:30px;padding:7px 13px;white-space:nowrap;font-size:12px}main{max-width:1360px;margin:28px auto;padding:0 24px}.cards{display:flex;gap:16px;margin-bottom:24px}.card,.panel{border:1px solid #dce3ed;border-radius:12px;background:white;box-shadow:0 3px 10px #172b4305}.card{padding:18px 24px;flex:1}.card small{display:block;color:#64758a}.card strong{font-size:28px;display:block;margin-top:7px}.panel{padding:24px;margin-bottom:20px}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px}button,.button{border:1px solid #c6d3e2;border-radius:7px;padding:9px 15px;font:inherit;cursor:pointer;background:white;color:#223952;text-decoration:none}button.primary{background:#245bba;color:white;border-color:#245bba}button:hover,.button:hover{filter:brightness(.95)}button:disabled{opacity:.4;cursor:default}input,textarea{font:inherit;border:1px solid #bfcede;border-radius:7px;padding:10px;background:#fcfdff;color:#183049}input:focus,textarea:focus,button:focus-visible,a:focus-visible{outline:3px solid #9bc5ff;outline-offset:2px}.search{flex:1;min-width:180px}label{font-weight:600;font-size:13px}table{width:100%;border-collapse:collapse;text-align:left}th{font-size:11px;color:#64758a;text-transform:uppercase;letter-spacing:.08em}td,th{padding:13px 10px;border-bottom:1px solid #e8edf3}td small,.muted{color:#64758a}.scroll{overflow:auto}.notice{padding:12px 16px;border-left:3px solid #267761;background:#ebf7f1;margin:15px 0;min-height:42px;white-space:pre-wrap}.notice.error{border-color:#b63448;background:#fff0f1;color:#8c2435}.split{display:grid;grid-template-columns:minmax(280px, .85fr) minmax(380px,1.4fr);gap:20px}textarea{width:100%;resize:vertical;font:14px/1.65 Consolas,monospace;tab-size:2}.editor label{display:block;margin:16px 0 8px}.tabs{display:flex;gap:8px;border-bottom:1px solid #dce3ed;margin-bottom:18px;padding-bottom:12px}.tabs button[aria-selected=true]{background:#e7efff;color:#214f9e;border-color:#95b4ea}pre,code{font-family:Consolas,monospace}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:14px;border-radius:7px;line-height:1.6}.tree details{margin:6px 0 6px 17px;border-left:1px solid #d8e2ee;padding-left:10px}.tree summary{cursor:pointer;padding:5px}.tree code{font-size:12px;color:#3c649b}.graph{width:100%;min-height:180px}.step{border:1px solid #cad9eb;background:#f1f6ff;padding:18px;border-radius:8px;min-height:130px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 Consolas,monospace}footer{color:#6c7c90;font-size:12px;padding:12px 2px 25px}a{color:#245bba}[hidden]{display:none!important}@media(max-width:850px){.split{grid-template-columns:1fr}.cards{flex-wrap:wrap}.card{min-width:120px}header{align-items:flex-start;flex-direction:column}.panel{padding:17px}main{padding:0 14px}td,th{padding:10px 5px}}
```

</details>

Create `directory_service/static/app.js`. It subscribes to the named `inventory`
event, replaces displayed state, filters relative paths, and sends a selected
File directly as a PUT body. It waits for the monitor's next snapshot rather than
inventing a local revision after upload. User filenames are rendered as text.

<!-- tutorial-file: directory_service/static/app.js -->
```javascript
"use strict";
const $ = id => document.getElementById(id);
let snapshot = {entries: [], errors: [], revision: 0};
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
function render(data = snapshot) {
  snapshot = data;
  const entries = data.entries || [], files = entries.filter(e => e.kind === "file");
  $("file-count").textContent = files.length;
  $("dir-count").textContent = entries.length - files.length;
  $("bytes").textContent = files.reduce((sum, e) => sum + e.bytes, 0).toLocaleString();
  $("revision").textContent = data.revision;
  const visible = entries.filter(e => e.path.toLowerCase().includes($("filter").value.toLowerCase()));
  $("entries").replaceChildren();
  visible.forEach(entry => {
    const row = document.createElement("tr");
    [entry.path, entry.kind, entry.bytes.toLocaleString(), new Date(entry.modified_ns / 1e6).toLocaleString()].forEach(value => {
      const cell = row.insertCell(); cell.textContent = value;
    });
    const action = row.insertCell();
    if (entry.kind === "file") {
      const link = document.createElement("a"); link.textContent = "Download";
      link.href = "/api/files/" + entry.path.split("/").map(encodeURIComponent).join("/");
      action.append(link);
    }
    $("entries").append(row);
  });
  $("empty").hidden = visible.length > 0;
  $("empty").textContent = entries.length ? "No paths match this filter." : "This directory is empty. Upload a file to begin.";
  if (data.truncated || data.errors?.length) message([data.truncated ? "Entry limit reached: this snapshot is partial." : "", ...(data.errors || [])].filter(Boolean).join("\n"), true);
}
async function refresh() {
  try { const response = await fetch("/api/files"); if (!response.ok) throw Error("Snapshot unavailable"); render(await response.json()); }
  catch (error) { message(error.message, true); }
}
$("filter").addEventListener("input", () => render());
$("refresh").addEventListener("click", refresh);
$("file").addEventListener("change", () => { if ($("file").files[0]) $("destination").value = $("file").files[0].name; });
$("upload").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.submitter; button.disabled = true;
  try {
    const name = $("destination").value;
    const response = await fetch("/api/files/" + name.split("/").map(encodeURIComponent).join("/"), {method: "PUT", headers: {"Content-Type": "application/octet-stream"}, body: $("file").files[0]});
    const body = await response.json(); if (!response.ok) throw Error(body.detail || "Upload failed");
    message("Created " + name + ". The background scan will publish its statistics shortly.");
  } catch (error) { message(error.message, true); } finally { button.disabled = false; }
});
const events = new EventSource("/api/events");
events.addEventListener("open", () => { $("connection").textContent = "● Live updates connected"; });
events.addEventListener("inventory", event => render(JSON.parse(event.data)));
events.addEventListener("error", () => { $("connection").textContent = "Reconnecting…"; });
window.addEventListener("pagehide", () => events.close());
refresh();
```

Append the home route and static mount to `directory_service/app.py`:

<!-- tutorial-append: directory_service/app.py -->
<!-- python-doc-fragment -->
```python
@service.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static/index.html")


service.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend"
)
```

Use `/static` and an explicit `/` route. A catch-all root mount would shadow
framework health and shutdown routes registered during lifespan. Asset paths
come from the installed package's `__file__`, not the current shell directory.
Relative browser API URLs keep the UI on the same origin without a CORS service.

**Checkpoint:** restart and open `http://127.0.0.1:18085/`. Upload, filter, download
and edit a file outside the browser. The table must update through SSE. Confirm
`/health` and `/docs` still work, then reload the browser to get a current snapshot.

## 9. Operate and verify your completed service

Run `lcl-fastapi logs -o config service.lclcfg` to find actual worker log paths,
including rotated segments. `logger.file.monitor` inherits the default log
directory and has a dedicated named source; API request logs stay separate.
Monitor lifecycle events appear in controller logs. Use `curl -i` and the response
`X-Request-ID` to find an API call in its access log. An SSE access entry completes
when the long-lived request ends.

After closing streams and stopping, try one configuration exercise at a time:

| Change | Observe after restart |
| --- | --- |
| Add `-o background_worker.monitor.interval_seconds "LCL[2]"` to serve | Slower scanning without changing the file; the next launch without the override restores its value. |
| Set `background_worker.monitor.enabled: False` | File APIs still work; the inventory no longer tracks changes and the worker opens no dedicated file. |
| Set an invalid interval and `background_worker.monitor.auto_restart: False` | Entry failure is recorded once and stays failed. |
| Set an invalid interval and `background_worker.monitor.auto_restart: True` | Failed executions restart after one second and increase observed counters until stopped. |

Restore valid values. Both switches are Boolean values, not quoted strings.
Normal entry returns also restart by default; complete service restart applies
configuration edits, not automatic entry restart. Startup resource failure is
different from an entry failure: invalid lifespan limits prevent service startup.

Create `verify.py` with this complete isolated check, then stop your manual server
and run `python verify.py`. It creates a temporary workspace, starts the installed
app, exercises nested upload/download, external modification/deletion, SSE and
shutdown, and removes its temporary files. It does not use your `watched` data.

<details>
<summary>Complete verify.py</summary>

<!-- tutorial-file: verify.py -->
<!-- python-doc-exec -->
```python
"""Verify the installed tutorial application using real native service processes."""

import json
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psutil

HERE = Path(__file__).resolve().parent
CLI = Path(sysconfig.get_path("scripts")) / (
    "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
)


def http(
    origin: str,
    path: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(origin + path, method=method, data=data, headers=headers or {})
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def json_http(origin: str, path: str, method: str = "GET", data: object = None) -> tuple[int, Any]:
    status, body = http(
        origin,
        path,
        method,
        None if data is None else json.dumps(data).encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(body)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = HERE.joinpath("service.lclcfg").read_text(encoding="utf-8")
        config = root / "service.lclcfg"
        config.write_text(source, encoding="utf-8")
        origin = ORIGIN

        def command(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(CLI), *args, "-o", "config", str(config)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )

        with (root / "console.log").open("w+", encoding="utf-8") as output:
            process = subprocess.Popen(
                [str(CLI), "serve", "-o", "config", str(config)],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, (root / "console.log").read_text(
                        encoding="utf-8"
                    )
                    try:
                        if http(origin, "/health")[0] == 200:
                            break
                    except URLError, TimeoutError:
                        pass
                    assert time.monotonic() < deadline, "Service startup timed out"
                    time.sleep(0.1)
                assert http(origin, "/")[0] == 200
                for asset in ["/static/app.js", "/static/style.css"]:
                    assert http(origin, asset)[0] == 200
                verify(origin, root)
                status = json.loads(command("status").stdout)
                assert status["service"]["configured_workers"] == 1
            except BaseException:
                print((root / "console.log").read_text(encoding="utf-8"), file=sys.stderr)
                for log in sorted(root.glob("logs/*.log*")):
                    print(log.read_text(encoding="utf-8"), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    owner = psutil.Process(process.pid)
                    try:
                        command("stop")
                    except BaseException:
                        for child in reversed(owner.children(recursive=True)):
                            child.kill()
                        owner.kill()
                        process.wait(timeout=10)
                        raise
                process.wait(timeout=45)
            assert process.returncode == 0, (root / "console.log").read_text(encoding="utf-8")
            assert json.loads(command("status").stdout)["status"] == "STOPPED"
        print("PASS " + HERE.name + ": installed static UI, native APIs, isolation and cleanup")


ORIGIN = "http://127.0.0.1:18085"


def verify(origin: str, root: Path) -> None:
    with urlopen(origin + "/api/events", timeout=8) as stream:

        def event() -> dict[str, Any]:
            for _ in range(100):
                line = stream.readline().decode()
                assert line, "SSE connection ended before the expected filesystem update"
                if line.startswith("data: "):
                    value: dict[str, Any] = json.loads(line[6:])
                    return value
            raise AssertionError("No SSE snapshot received")

        event()
        assert http(origin, "/api/files/nested/hello.txt", "PUT", b"hello")[0] == 201
        deadline = time.monotonic() + 8
        while True:
            value = event()
            if any(row["path"] == "nested/hello.txt" for row in value["entries"]):
                break
            assert time.monotonic() < deadline
        assert http(origin, "/api/files/nested/hello.txt") == (200, b"hello")
        assert http(origin, "/api/files/nested/hello.txt", "PUT", b"other")[0] == 409
        assert http(origin, "/api/files/%2e%2e/escape.txt", "PUT", b"bad")[0] == 400
        assert (
            http(
                origin,
                "/api/files/rejected.txt",
                "PUT",
                b"bad",
                {"Origin": "https://other.example"},
            )[0]
            == 403
        )
        assert not (root / "escape.txt").exists()
        (root / "watched/nested/hello.txt").write_bytes(b"changed on disk")
        while True:
            value = event()
            if any(
                row["path"] == "nested/hello.txt" and row["bytes"] == 15 for row in value["entries"]
            ):
                break
            assert time.monotonic() < deadline
        (root / "watched/nested/hello.txt").unlink()
        while any(row["path"] == "nested/hello.txt" for row in event()["entries"]):
            assert time.monotonic() < deadline
    assert json_http(origin, "/api/files")[0] == 200


if __name__ == "__main__":
    main()
```

</details>

The printed Python, configuration and static files form the complete installable
project. Keep them under version control; exclude `.venv`, `run`, `logs` and local
watched data. CI reconstructs this project from the guide's exact code and checks
its native service, as well as installing a built framework wheel on both platforms.

## Filesystem boundary

Use a trusted dedicated root. Links/junctions and special files are not served,
but checks assume no hostile local process swaps entries between validation and
I/O. This is not an authenticated file-sharing service or a race-proof filesystem
sandbox. Scans cost O(entries visited) up to the cap, are not transactional, and
can report partial state/errors. Upload memory is bounded; a failed disk write
can leave an incomplete new file for the operator to remove. Add reviewed storage
and authentication controls before exposing it outside your machine.
