"""Serve a local LCL workbench with opaque, bounded in-memory sessions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from lcl_fastapi import LclFastAPI, get_config
from playground_service.engine import Engine


class Program(BaseModel):
    source: str = Field(max_length=16000)
    expression: str = Field(min_length=1, max_length=4000)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    if await get_config("server.workers") != 1:
        raise ValueError("Playground sessions require server.workers: 1")
    maximum = int(str(await get_config("business.sessions.max_count")))
    ttl = float(str(await get_config("business.sessions.idle_seconds")))
    if not 1 <= maximum <= 100 or not 1 <= ttl <= 86400:
        raise ValueError("Invalid session count or idle duration")
    engine = Engine(maximum, ttl)
    app.state.engine = engine
    try:
        yield
    finally:
        await engine.close()


service = LclFastAPI(lifespan=lifespan)
service.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
)


def engine(request: Request) -> Engine:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin sessions are not allowed")
    result: Engine = request.app.state.engine
    return result


@service.post("/api/sessions", status_code=201)
async def create(request: Request) -> dict[str, Any]:
    return await engine(request).submit("create")


@service.get("/api/sessions/{session_id}")
async def read(session_id: str, request: Request) -> dict[str, Any]:
    return await engine(request).submit("read", session_id)


@service.delete("/api/sessions/{session_id}")
async def delete(session_id: str, request: Request) -> dict[str, Any]:
    return await engine(request).submit("delete", session_id)


@service.put("/api/sessions/{session_id}/program")
async def parse(session_id: str, program: Program, request: Request) -> JSONResponse:
    report = await engine(request).submit("parse", session_id, **program.model_dump())
    return JSONResponse(report, status_code=422 if "error" in report else 200)


@service.post("/api/sessions/{session_id}/evaluate")
async def evaluate(session_id: str, request: Request) -> JSONResponse:
    report = await engine(request).submit("evaluate", session_id)
    return JSONResponse(report, status_code=422 if "error" in report else 200)


@service.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static/index.html")


service.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend"
)
