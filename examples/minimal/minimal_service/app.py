"""A hello route with observable business startup and teardown."""

import faulthandler
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from lcl_fastapi import LclFastAPI, get_logger


@asynccontextmanager
async def business_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Log business lifecycle boundaries around request handling.

    :param app: Application whose lifetime this context manages.
    :returns: The active business lifetime.
    """
    logger = await get_logger("minimal.business")
    logger.info("minimal business startup")
    faulthandler.dump_traceback_later(2, repeat=True, file=sys.stderr)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()
        logger.info("minimal business shutdown")


service = LclFastAPI(lifespan=business_lifespan)


@service.get("/hello")
async def hello() -> dict[str, str]:
    """Log the request and return a fixed greeting.

    :returns: A JSON-compatible greeting.
    """
    logger = await get_logger("minimal.hello")
    logger.info("hello requested")
    return {"message": "hello"}
