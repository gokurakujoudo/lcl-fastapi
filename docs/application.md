# Application and lifespan

`LclFastAPI` retains FastAPI route decorators, dependency injection, request
models, exception handlers, and `APIRouter` integration. Constructing it does not
open a configuration Frame, log file, or worker process. Start it using the CLI
so that the platform runtime creates the verified master and worker state.

Use Router prefixes explicitly:

<!-- python-doc-exec -->
```python
from fastapi import APIRouter
from lcl_fastapi import LclFastAPI

service = LclFastAPI()
router = APIRouter()


@router.get("/items")
async def items() -> list[str]:
    return ["first", "second"]


service.include_router(router, prefix="/api/v1")
```

The resulting route is `/api/v1/items`. A directly decorated `/hello` route
remains `/hello`. No `.lclcfg` field adds a business prefix. Register routes
before startup. Exact method/path business routes take precedence over framework
defaults, including when added through `include_router`. A GET route does not
replace a POST route on the same path.

Overriding `/health`, `/docs`, `/openapi.json`, or `POST /_lcl/shutdown` changes the
corresponding framework behavior. Replacing shutdown can make `stop` fail. The
framework does not assert its default endpoint guarantees for user replacements.

## Business resources

Pass an asynchronous context manager through `LclFastAPI(lifespan=...)`:

<!-- python-doc-exec -->
```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from lcl_fastapi import LclFastAPI, get_config, get_logger


@asynccontextmanager
async def business_lifespan(app: FastAPI) -> AsyncIterator[None]:
    app.state.greeting = await get_config("business.greeting")
    logger = await get_logger("example.business")
    logger.info("business startup")
    try:
        yield
    finally:
        logger.info("business shutdown")


service = LclFastAPI(lifespan=business_lifespan)


@service.get("/greeting")
async def greeting() -> dict[str, str]:
    return {"message": str(service.state.greeting)}
```

Add `business.greeting: "Hello"` to the application's `.lclcfg` file. The worker
opens its Frame and logger before business startup. Business teardown finishes
before the framework stops sampling, releases worker resources, and flushes the
logger. If startup fails, already-acquired framework resources are still closed.

`await get_config(key)` is valid inside the business lifespan and HTTP requests.
It evaluates the current worker's LCL Frame and returns an object whose concrete
type depends on the expression. Validate/cast business-specific values as needed.
Missing names and evaluation errors preserve LCL's diagnostics. Calls outside an
active worker scope raise `RuntimeError`; do not call it during module import.

The frame is process-local and follows the worker's async lifespan. Request
bindings are task-local and reset after completion. Framework request metadata
does not promise inheritance into detached jobs that outlive their request.
