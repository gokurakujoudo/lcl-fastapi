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
Overriding built-in routes is discouraged unless you understand and accept these
consequences.

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

## Scoped LCL frames

Use `async with use_lcl_frame(module=None, values=None) as frame` inside an active
lifespan or asynchronous handler. Import the helper from `lcl_fastapi`. It derives
a native LCL Frame from the current configuration and binds `get_config()` to it
until exit. Optional `module` supplies local definitions; `values` supplies copied
host bindings. Nested scopes restore their parent, including after exceptions and
cancellation. Exiting closes the child, not its borrowed parent. Calling outside
an active scope raises `RuntimeError`.

Each Frame belongs to its event loop. Do not share it across threads or keep it in
detached tasks after scope exit. Native parent-owned definitions still evaluate and
cache in the parent: overriding a dependency locally does not recompute an inherited
definition. This is a configuration scope, not a deep copy or a sandbox.

<!-- python-doc-exec -->
```python
from lcl_fastapi import LclFastAPI, get_config, use_lcl_frame

service = LclFastAPI()


@service.get("/scoped")
async def scoped() -> dict[str, object]:
    original = await get_config("app.name")
    async with use_lcl_frame(values={"app.name": "local"}) as frame:
        assert await frame.get("app.name") == "local"
        async with use_lcl_frame(values={"app.name": "nested"}):
            assert await get_config("app.name") == "nested"
        assert await get_config("app.name") == "local"
    assert await get_config("app.name") == original
    return {"name": original}
```

## Uncaught HTTP exceptions

`LclFastAPI(uncaught_exception_handler=callback)` accepts an asynchronous
`callback(err, request, *, context) -> Response`. The exported
`UncaughtExceptionContext` borrows `request_id`, `logger`, the current `frame`,
the selected `endpoint` (or None), and public `service` identity. Access application
resources through `request.app.state` and lifespan state through `request.state`.
Do not retain these borrowed resources beyond the callback.

Specific FastAPI exception handlers, HTTPException, and request validation retain
their normal priority. Without an explicit callback, an existing Exception/500
handler remains authoritative. Explicitly combining both generic interfaces raises
`ValueError` when the middleware stack is built, including handlers registered after
construction. Without a native generic handler, the framework callback applies even
when `debug=True`.

The default callback logs the original traceback and returns HTTP 500 with
`{"detail": "Internal Server Error"}`. A custom callback can return any Response.
If it raises or returns another type, the framework first logs its failure, then
uses the default callback to log the original exception and return 500. The default
callback catches its own diagnostic failures, records a short summary without
recursion, and still attempts the generic response. Cancellation and process-exit
signals propagate. Request IDs and the single access record remain intact.

The exported `default_uncaught_exception_handler` lets a custom callback delegate
selected errors explicitly. See the composed example's default/custom/callback
endpoints and [traceback logging](logging.md#uncaught-exception-diagnostics).
The callback handles HTTP failures, not WebSocket, lifespan or independent job
failures. Once response headers have started, failures can only be logged and
propagated: no replacement response is sent. Native ASGI propagation is retained,
so TestClient can re-raise server errors unless `raise_server_exceptions=False`.

## Offline API documentation

With `docs.enabled: True`, the framework provides a Swagger page at `docs.path`
and an OpenAPI schema at `docs.openapi_path`, defaulting to `/docs` and
`/openapi.json`. Its JavaScript, CSS, and favicon are packaged under
`lcl_fastapi/static/swagger` and served from `/_lcl/static/swagger`. The HTML
uses local URLs and disables the remote validator, so loading the schema and
executing requests requires no CDN. ReDoc is not provided.

Setting `docs.enabled: False` removes all framework documentation routes and
static resources; explicitly registered business routes remain available. The
internal shutdown operation is excluded from OpenAPI. Both distributions carry
the bundled assets and their [upstream provenance and licenses](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/src/lcl_fastapi/static/swagger/NOTICE.md).
