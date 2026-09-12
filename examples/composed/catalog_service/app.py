"""Compose business configuration, a worker resource, routes, and request logs."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request

from catalog_service.errors import catalog_error
from lcl_fastapi import LclFastAPI, get_config, get_logger, get_request_context, use_lcl_frame


@asynccontextmanager
async def catalog_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create and release a catalog owned by this worker's lifespan.

    :param app: Application whose state holds the worker-local catalog.
    :returns: An asynchronous context managing the catalog.
    :raises ValueError: If the business catalog configuration is invalid.
    """
    items = await get_config("business.items")
    if not isinstance(items, list) or not all(isinstance(item, str) for item in items):
        raise ValueError("business.items must be a list of strings")
    app.state.catalog = list(items)
    logger = await get_logger("catalog.lifecycle")
    logger.info(f"catalog startup pid={os.getpid()} items={len(app.state.catalog)}")
    try:
        yield
    finally:
        app.state.catalog.clear()
        logger.info(f"catalog shutdown pid={os.getpid()} items={len(app.state.catalog)}")


service = LclFastAPI(lifespan=catalog_lifespan, uncaught_exception_handler=catalog_error)
router = APIRouter()


@router.get("/catalog")
async def catalog(request: Request) -> dict[str, object]:
    """Read the initialized catalog and evaluate request-time business settings.

    :param request: Request providing this worker's application state and ASGI scope.
    :returns: Catalog, greeting, external origin, and server-issued request identity.
    :raises RuntimeError: If the route is called without framework request context.
    """
    context = get_request_context()
    if context is None:
        raise RuntimeError("catalog requires an active request")
    logger = await get_logger("catalog.request")
    logger.info(f"catalog read pid={os.getpid()}")
    return {
        "items": list(request.app.state.catalog),
        "greeting": await get_config("business.greeting"),
        "public_origin": await get_config("server.root_path"),
        "asgi_root_path": request.scope.get("root_path", ""),
        "request_id": context.request_id,
        "pid": os.getpid(),
    }


@service.get("/about")
async def about() -> dict[str, str]:
    """Describe the independent service.

    :returns: A stable business application name.
    """
    return {"application": "composed-catalog"}


@router.get("/scope")
async def scoped_configuration() -> dict[str, object]:
    """Demonstrate nested local configuration without changing runtime bindings.

    :returns: Original, local and restored names.
    """
    original = await get_config("business.name")
    async with use_lcl_frame(values={"business.name": "Scoped Reader"}) as frame:
        assert await frame.get("business.name") == "Scoped Reader"
        async with use_lcl_frame(values={"business.name": "Nested Reader"}):
            assert await get_config("business.name") == "Nested Reader"
        local = await get_config("business.name")
        assert local == "Scoped Reader"
    assert await get_config("business.name") == original
    return {"original": original, "local": local, "restored": await get_config("business.name")}


@router.get("/errors/{mode}")
async def error_example(mode: str, quantity: int = 3) -> None:
    """Raise a controlled error to exercise each callback path.

    :param mode: default, custom or callback demonstration path.
    :param quantity: Example argument included in the original traceback log.
    :raises ValueError: Deliberate demonstration failure.
    """
    raise ValueError(f"demonstration route failure: {mode}, quantity={quantity}")


service.include_router(router, prefix="/api/v1")
