# 1. Build a product catalog service

[Series overview](../build-a-service.md) · Next: [Directory monitor](02-directory-monitor.md)

Build a small catalog API that a shop website can call to list products, filter
available stock, and look up one product. Start with an empty directory and end
with typed responses, validated business configuration, worker startup/cleanup,
request logs, a branded console command, reusable defaults, command-line overrides,
a repeatable HTTP check, and deployment configuration.

You need CPython 3.14 and either Windows or Linux. Installation needs package-index
access; running the finished service needs no external account or database.
Run one lcl-fastapi service per machine, and reserve local port `18083` for this guide.

This is a read-only catalog whose source is reviewed configuration. It deliberately
has no checkout, authentication, inventory writes, or database. That makes it
useful for a small published catalog while keeping data ownership clear: changing
products requires stopping and starting the complete service.

## 1. Create the project

In PowerShell on Windows:

```powershell
mkdir catalog-api
cd catalog-api
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install lcl-fastapi==0.3.0
```

In a Linux shell:

```sh
mkdir catalog-api
cd catalog-api
python3.14 -m venv .venv
.venv/bin/python -m pip install lcl-fastapi==0.3.0
```

Keep all subsequent files and commands in this directory. The explicit executable
paths avoid depending on shell activation. On Linux, replace
`.venv\Scripts\python.exe` with `.venv/bin/python` and
`.venv\Scripts\catalog.exe` with `.venv/bin/catalog` in later commands.

## 2. Serve one route

Create `app.py`:

<!-- python-doc-exec -->
```python
from lcl_fastapi import LclFastAPI

service = LclFastAPI()


@service.get("/hello")
async def hello() -> dict[str, str]:
    return {"message": "Hello from the catalog"}
```

Create `service.lclcfg` with this complete configuration. The business fields will
be used in the next step; leave them in place for now.

<!-- tutorial-file: service.lclcfg -->
```text
__LCL_VERSION__: 1
using f"{lcl_fastapi_defaults}"

app.name: "catalog-api"
app.version: "1.0.0"
app.target: "app:service"
server.host: "127.0.0.1"
server.port: 18083
server.workers: 1
logger.file.default.directory: "./logs"
logger.file.controller.filename: f"{app.name}.controller.log"
logger.file.service.filename: f"{app.name}.{worker_pid}.log"
logger.level: "INFO"
business.shop_name: "Corner Shop"
business.products: [{"sku": "tea", "name": "Green tea", "price_cents": 650, "in_stock": True}, {"sku": "mug", "name": "Ceramic mug", "price_cents": 1200, "in_stock": False}]
```

`using` imports the installed universal defaults. Local fields replace inherited
values, so this service uses port 18083 while retaining default health/docs routes,
request IDs, runtime paths, and graceful shutdown. `worker_pid` is supplied by
each real worker; never define it yourself. The two patterns share `./logs`.
You can put shared organization settings in another `.lclcfg` file and `using`
that file instead; see [configuration layering](../configuration.md).

Create a small installable project so operators can type `catalog`.
Save `pyproject.toml`:

<!-- tutorial-file: pyproject.toml -->
```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "corner-shop-catalog"
version = "1.0.0"
requires-python = ">=3.14"
dependencies = ["lcl-fastapi==0.3.0"]

[project.scripts]
catalog = "catalog_cli:main"

[tool.hatch.build.targets.wheel]
only-include = ["app.py", "catalog_cli.py"]
```

Save `catalog_cli.py`:

<!-- tutorial-file: catalog_cli.py -->
<!-- python-doc-exec -->
```python
from lcl_fastapi.cli import run_cli


def main() -> int:
    return run_cli(config_path="service.lclcfg", prog="catalog", version_text="1.0.0")
```

Install it with `.venv\Scripts\python.exe -m pip install -e .`.
`catalog --version` prints `1.0.0`; `catalog --help` lists the framework commands.
The wrapper supplies a default file relative to the working directory and returns
the framework exit code. Continue running from the project directory.
`catalog status -c another.lclcfg` selects a different file explicitly.
The configuration stays outside the wheel so the operator can maintain it.
For a command with no wrapper defaults, the script target can simply be
`lcl_fastapi.cli:run_cli`.

Start the service in terminal A:

```powershell
.venv\Scripts\catalog.exe serve
```

Open `http://127.0.0.1:18083/hello`: the response is
`{"message":"Hello from the catalog"}`. Also open `/health` and `/docs` on the
same origin. `app:service` resolves the object named `service` in `app.py` next
to the configuration. The CLI starts the platform's native worker runtime;
constructing `LclFastAPI()` alone does not start a server.

In terminal B, stop it before editing the application:

```powershell
.venv\Scripts\catalog.exe stop
```

Wait for terminal A to return to its prompt. Use this stop/edit/start sequence
for configuration changes. For Python development, use
`catalog serve -o hot_reload`. Set
`server.reload_dirs: [".", "../shared"]` to watch this directory and shared code,
or replace the list with your source directories. Reload uses one worker and
briefly interrupts service; see [the reload contract](../runtime.md#development-hot-reload).

## 3. Load validated products during startup

Replace **all** of `app.py` with the complete application below. Read the
`Product` model and `catalog_lifespan` first; the route section is explained in
the next step. There are no extra files or hidden helpers to copy.

<!-- tutorial-file: app.py -->
<!-- python-doc-exec -->
```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import APIRouter, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from lcl_fastapi import LclFastAPI, get_config, get_logger


class Product(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    sku: str = Field(min_length=1)
    name: str = Field(min_length=1)
    price_cents: int = Field(ge=0)
    in_stock: bool


@asynccontextmanager
async def catalog_lifespan(app: FastAPI) -> AsyncIterator[None]:
    shop_name = await get_config("business.shop_name")
    if not isinstance(shop_name, str) or not shop_name.strip():
        raise ValueError("business.shop_name must be a nonempty string")
    products = TypeAdapter(list[Product]).validate_python(
        await get_config("business.products")
    )
    catalog = {product.sku: product for product in products}
    if len(catalog) != len(products):
        raise ValueError("business.products must have unique SKUs")
    app.state.shop_name = shop_name
    app.state.catalog = catalog
    logger = await get_logger("catalog")
    logger.info("catalog ready")
    try:
        yield
    finally:
        catalog.clear()
        logger.info("catalog closed")


service = LclFastAPI(lifespan=catalog_lifespan)
router = APIRouter(prefix="/api/v1/products", tags=["products"])


@service.get("/about")
async def about(request: Request) -> dict[str, str]:
    return {"shop": str(request.app.state.shop_name)}


@router.get("", response_model=list[Product])
async def list_products(request: Request, in_stock: bool | None = None) -> list[Product]:
    catalog = cast(dict[str, Product], request.app.state.catalog)
    logger = await get_logger("catalog")
    logger.info("list products")
    return [
        product for product in catalog.values()
        if in_stock is None or product.in_stock == in_stock
    ]


@router.get("/{sku}", response_model=Product)
async def get_product(sku: str, request: Request) -> Product:
    catalog = cast(dict[str, Product], request.app.state.catalog)
    if sku not in catalog:
        raise HTTPException(status_code=404, detail="Product not found")
    return catalog[sku]


service.include_router(router)
```

`get_config` runs inside the worker lifespan, after the framework has opened its
LCL Frame and logger. The Pydantic model rejects missing/extra fields, wrong
types, and negative prices; the lifespan rejects duplicate SKUs. Bad catalog
configuration fails startup instead of serving partially accepted products.
Prices are integer cents in this shop's chosen currency, avoiding floating-point
currency arithmetic. Currency conversion and tax calculation are outside this API.

Each worker owns its own catalog dictionary, containing frozen product models.
Handlers only read it. Teardown clears it before framework logging is flushed.
This dictionary is not shared storage: if you later add writes, design durable
storage and concurrency first. Simply adding more workers would not synchronize
in-memory changes. The [application reference](../application.md) explains where
database/client acquisition and closing belong when those resources are needed.

## 4. Explore the business API

Start terminal A again with the same `serve` command. In `/docs`, expand
**products**, choose **Try it out**, and execute these requests:

| Request | Result and reason |
| --- | --- |
| `GET /about` | The configured shop name; direct routes keep their own paths. |
| `GET /api/v1/products` | Both products, with the declared response fields. |
| `GET /api/v1/products?in_stock=true` | Only the tea, because the handler filters by availability. |
| `GET /api/v1/products/tea` | The product keyed by SKU `tea`. |
| `GET /api/v1/products/missing` | HTTP 404 with `Product not found`. |
| `GET /api/v1/products?in_stock=maybe` | HTTP 422 because FastAPI cannot parse a Boolean. |

The Router owns `/api/v1/products`; `.lclcfg` does not add a prefix. The first
`/hello` route is gone because you replaced the entire file. `/health`, `/docs`,
and `/openapi.json` remain framework routes. Swagger assets are served locally
after installation, including when the machine cannot reach a CDN.

## 5. Make verification repeatable

Create `verify.py` below. It uses the standard library and only calls the local
service. Run it in terminal B with `.venv\Scripts\python.exe verify.py` while
terminal A is serving. Assertions document the observable contract directly.

<!-- tutorial-file: verify.py -->
<!-- python-doc-exec -->
```python
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


def verify() -> None:
    base = "http://127.0.0.1:18083"
    request = Request(base + "/api/v1/products", headers={"X-Request-ID": "client-id"})
    with urlopen(request, timeout=5) as response:
        products = json.load(response)
        request_id = response.headers["X-Request-ID"]
        assert response.status == 200
        assert request_id.isdecimal() and request_id != "client-id"
        assert [item["sku"] for item in products] == ["tea", "mug"]
    with urlopen(base + "/api/v1/products?in_stock=true", timeout=5) as response:
        assert [item["sku"] for item in json.load(response)] == ["tea"]
        assert response.headers["X-Request-ID"] != request_id
    with urlopen(base + "/api/v1/products/tea", timeout=5) as response:
        assert json.load(response)["price_cents"] == 650
    with urlopen(base + "/about", timeout=5) as response:
        assert json.load(response) == {"shop": "Corner Shop"}
    for path, expected in [("/api/v1/products/missing", 404),
                           ("/api/v1/products?in_stock=maybe", 422)]:
        try:
            with urlopen(base + path, timeout=5):
                raise AssertionError(f"Expected HTTP {expected}")
        except HTTPError as error:
            with error:
                assert error.code == expected
                if expected == 404:
                    assert json.load(error) == {"detail": "Product not found"}
    with urlopen(base + "/health", timeout=5) as response:
        assert response.status == 200
    with urlopen(base + "/openapi.json", timeout=5) as response:
        assert "/api/v1/products/{sku}" in json.load(response)["paths"]
    print("Catalog checks passed; list request ID:", request_id)


if __name__ == "__main__":
    verify()
```

Keep the printed ID to correlate the list request with logs. A connection error
usually means the service is not ready or startup failed; inspect terminal A
first. Do not remove failing assertions to make the check pass.

## 6. Inspect logs and stop cleanly

In terminal B:

```powershell
.venv\Scripts\catalog.exe status
.venv\Scripts\catalog.exe logs
```

Status should report `RUNNING` with one worker after startup settles. `logs`
returns a JSON object containing observed worker log-file paths, not log contents. Open a path from its `paths` array to find
`list products` with the ID printed by `verify.py`. Access records include method,
path, status, duration, and the same server-generated ID. Log-path observations
are eventually consistent, so allow a sampling interval after startup/rotation.

```powershell
.venv\Scripts\catalog.exe stop
.venv\Scripts\catalog.exe status
```

After `stop` finishes, status should report `STOPPED`. The worker's log contains
`catalog closed`; business cleanup ran before the logger flushed. Keep `run/`
private to the service account because it contains local control state. Runtime
and log paths resolve relative to `service.lclcfg`.

To publish a different catalog, edit the stopped service's `business.products`,
start again, and update your checks to match the intentional data change. For
this read-only API you can also change `server.workers` to `2` and restart;
each worker validates the same configuration independently.

## 7. Override settings and observe rotation

With the service stopped, start a development run in terminal A:

```powershell
.venv\Scripts\catalog.exe serve -o server.workers "LCL[2]" -o logger.file.service.rotation.mode size -o logger.file.service.rotation.max_bytes "LCL[2048]"
```

The file still says one worker, but `catalog status` eventually shows two live
workers and `service.configured_workers` equal to 2. Each worker evaluates its
own filename with its actual PID. The controller uses its separate filename.
CLI values win over local and inherited settings; integers use `LCL[...]`,
while `size` is a plain string. Replacement workers retain these overrides.
The overrides last for this service run and do not rewrite the file.

Run `verify.py` several times in terminal B, then run `catalog logs`. Both workers
serve the same catalog. As requests fill the small demonstration segments, old
files remain and new timestamped files appear. A typical result is shown below;
paths, PIDs, times, and the number of active paths vary:

```json
{"paths":["/opt/catalog-api/logs/catalog-api.28146.20260912T100000.123456Z.000003.log"],"observed_at":1789207201.0,"stale":false}
```

A snapshot can initially contain fewer workers' paths while startup/sampling
settles. `logs` lists active worker segments only; find controller segments under
`logs/catalog-api.controller.*.log`. The controller records `worker up`, heartbeat,
and later `worker log rotate` when it observes a changed path. Allow at least a
sampling interval and manager-loop iteration for the observation to catch up.
Read [rotation and file samples](../logging.md#rotation-and-permanent-segments) for
headers, continuation footers, retention, and request correlation.

Stop with `catalog stop`. A normal `catalog serve` now uses one worker again
because the previous overrides were scoped to that invocation. For a persistent
10 MiB policy, add `logger.file.service.rotation.mode: "size"` and
`logger.file.service.rotation.max_bytes: 10485760` to the stopped service's file.
The shared directory and both filename patterns can also be overridden with
`-o`; keep the worker pattern as a lazy LCL expression so it uses the worker PID.

For Python-only edits, stop and run `catalog serve -o hot_reload`. The inherited
`server.reload_dirs: ["."]` watches Python files below this project directory.
Change a route message: the worker is replaced gracefully and the controller
records `hot-reload retiring worker_pid=...`. Hot reload uses one worker even if a
higher count is configured. It does not reload `.lclcfg`; use stop/edit/start for
configuration changes. `catalog serve --dryrun -o server.port "LCL[18084]"`
validates the settings without opening that port or starting workers.

## 8. Prepare an operator-managed deployment

Deploy the same `app.py`, `.lclcfg`, and installed package on the target machine.
Keep the service bound to loopback behind your reverse proxy. Use a dedicated
service account with access to configuration and writable runtime/log directories.
For a public shop, decide authentication, rate limits, and exposure of operational
endpoints at the application/proxy boundary before opening access.

For example, append these fields to the **stopped** service's configuration on a
Linux host. Replace the domain, certificates, account, and directories with real
values; rendering does not create them:

<!-- tutorial-deployment -->
```text
server.root_path: "https://catalog.example.com"
nginx.server_name: "catalog.example.com"
nginx.listen_port: 443
nginx.ssl_certificate: "/etc/ssl/catalog/fullchain.pem"
nginx.ssl_certificate_key: "/etc/ssl/catalog/privkey.pem"
systemd.service_name: "catalog-api"
systemd.description: "Corner Shop catalog API"
systemd.user: "catalog"
systemd.group: "catalog"
systemd.working_directory: "/opt/catalog-api"
systemd.config_path: "/opt/catalog-api/service.lclcfg"
```

From `/opt/catalog-api`, with the package installed into its `.venv`:

```sh
.venv/bin/catalog nginx render -o output catalog.conf
.venv/bin/catalog systemd render -o output catalog.service
```

Review the output before installing it through your normal deployment process.
Nginx preserves business URLs and blocks the built-in shutdown path; systemd's
unit starts the foreground CLI using the deployment virtual environment. Neither
command installs files, starts Nginx, or enables a service. The public origin is
metadata, not an ASGI route prefix. See [Nginx](../nginx.md), [systemd](../systemd.md),
and [runtime ownership](../runtime.md) for the complete deployment contracts.

Your project now consists of `pyproject.toml`, `catalog_cli.py`, `app.py`,
`service.lclcfg`, and `verify.py`, plus its
virtual environment and generated `run/` and `logs/` directories. Keep application
files under version control; exclude local environments, runtime state, logs,
and any private deployment configuration.
