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


@service.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static/index.html")


service.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend"
)
