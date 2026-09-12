"""Real-process ASGI probe for native runtime lifecycle acceptance."""

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from lcl_fastapi import LclFastAPI, get_config, get_logger, get_request_context


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    directory = Path(os.environ["LCL_FASTAPI_STATE"])
    assert await get_config("app.name") == "runtime-probe"
    logger = await get_logger("runtime-probe")
    logger.info("business lifespan entered")
    if await get_config("app.fail_startup"):
        raise RuntimeError("intentional business startup failure")
    yield
    logger.info("business lifespan closed")
    (directory / f"{os.getpid()}.finished").write_text("lifespan closed", encoding="utf-8")


app = LclFastAPI(lifespan=lifespan)


@app.get("/hello")
async def hello() -> dict[str, str]:
    context = get_request_context()
    assert context is not None
    logger = await get_logger("business")
    logger.info("hello request")
    return {
        "request_id": context.request_id,
        "configured_port": str(await get_config("server.port")),
    }


@app.get("/slow")
async def slow() -> dict[str, str]:
    await asyncio.sleep(0.3)
    return {"status": "done"}
