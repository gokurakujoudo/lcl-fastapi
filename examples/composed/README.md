# Composed catalog service

This independent downstream package combines a business Router, LCL expressions,
a worker-owned catalog resource, and request-associated logging. It uses only the
documented `lcl_fastapi` and FastAPI APIs. Install the built framework wheel and
this package into the example's own `.venv`; no framework source path is needed.

Use CPython 3.14 on Windows or Linux. The server listens on `127.0.0.1:18082`
with one effective API worker and two background threads: native Uvicorn on Windows
and native Gunicorn ASGI on Linux. The file deliberately requests two API workers;
the registered background callables produce one warning and force the count to one.
Run one example service at a time because the framework's supported deployment
scope is one service per machine.

If a background entry is cancelled while awaiting a service-resource operation,
the framework waits for that operation's API-loop cleanup before closing the
catalog or restarting the entry. Controller stop/reload deadlines remain active
even if lifecycle journal publication fails after startup; cooperative teardown
still runs, while a noncooperative task causes verified process termination.

## Install and run

From this directory, use these PowerShell commands on Windows:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install ..\..\dist\lcl_fastapi-0.3.0-py3-none-any.whl .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install ../../dist/lcl_fastapi-0.3.0-py3-none-any.whl .
.venv/bin/lcl-fastapi serve -o config service.lclcfg
```

These APIs are available in version 0.3.0. To install from PyPI, replace the wheel
path with `lcl-fastapi==0.3.0` while retaining `.` to install this example.
The application is installed as `lcl-fastapi-composed-example`, importing
`catalog_service`. When copying this project elsewhere, replace the wheel path
with its actual location. Dependencies require package-index access or an
operator-prepared wheelhouse during installation.

Open the local endpoints:

| Endpoint | What it demonstrates |
| --- | --- |
| `/api/v1/catalog` | Router prefix, initialized catalog, LCL greeting, public-origin metadata, request ID, and current worker PID. |
| `/about` | A directly registered route remains outside the Router prefix. |
| `/api/v1/scope` | Nested derived Frames, temporary name overrides, and restored runtime configuration. |
| `/api/v1/errors/default?quantity=7` | Default detailed traceback and generic JSON 500. |
| `/api/v1/errors/custom?quantity=7` | Custom JSON 502 containing the request ID. |
| `/api/v1/errors/callback?quantity=7` | A failing custom callback falls back to logged JSON 500. |
| `/health` | Framework service identity and cached host observations. |
| `/docs` | Bundled local Swagger JavaScript, CSS, favicon, and schema. |
| `/openapi.json` | OpenAPI containing the business routes. |

`business.greeting` evaluates `business.name` through an LCL expression. Each
worker reads `business.items` during its own lifespan, validates the value,
and creates an independent mutable catalog in `app.state`. Teardown clears that
resource and emits a final log before framework logging is flushed. This example
does not connect to a database or any external service.

The Router owns `/api/v1`; `/catalog` is therefore absent. The configured
`https://catalog.example.com:8443` origin is metadata returned by the business API.
It adds no route or ASGI prefix and does not change the local listener. Nginx's
independent listener is `9443` to illustrate an external port mapping.

The catalog handler calls `get_config` and `get_logger` inside request scope.
Its response ID matches the `X-Request-ID` header and the corresponding business
and access logs. Client-supplied IDs are replaced by server-generated IDs.
Do not edit `.lclcfg` during a running service: stop and start the complete
service to avoid workers retaining different configuration values.

## Background tasks

`catalog_service/workers.py` registers the asynchronous `inventory` finite task and
the synchronous `heartbeat` loop from application code. Inventory uses
`submit_to_service()` to read the initialized catalog on the API loop and demonstrates
nested local Frames. Its `auto_restart: False` makes normal completion final.
Heartbeat reads `background_worker.heartbeat.schedule.seconds` through `context.run`
and waits on `stop_event`, so it exits before catalog teardown.

Both inherit `background_worker.default.enabled: True` and default restart policy
unless overridden. To observe normal repeated task completion, set inventory's
auto_restart to True before starting the service. Every return then waits one second
before restart; controller events show INFO for normal completion/restart. Exceptions
use ERROR followed by WARNING for restart, with details in the task file. Application
code controls the executable; `.lclcfg` only controls parameters and policy.

Run `status` to see inventory `completed` with one attempt and heartbeat `running`.
`logs` reports three files: API, inventory and heartbeat. Background files contain
their own messages without HTTP request IDs. Their filenames and native rotation
settings are controlled by `logger.file.inventory` and `logger.file.heartbeat`.

Callables must cooperate with stopping. If one cannot exit within the graceful
deadline, the controller logs the failure and terminates the entire API process;
business teardown and buffered API/background logs cannot then be guaranteed.
Normal stop prevents restart; development reload replaces the retired API process.
Do not use loop-bound API resources directly from the worker thread. Await all
submitted service operations; keep those callbacks nonblocking and never make them
wait synchronously for the background thread.

## Local operations

Exercise error handling with `curl -i http://127.0.0.1:18082/api/v1/errors/default?quantity=7`.
Replace `default` with `custom` or `callback` for the other modes. The default and
fallback responses contain `{"detail":"Internal Server Error"}`; the custom 502
contains `error` and `request_id`. Search the worker log paths reported by `logs`
for the response's `X-Request-ID`. Default diagnostics show the original raise
line and a tab-indented `quantity: 7`; callback failure is recorded first.
The verifier checks all three responses and these diagnostics after shutdown flush.

Visit `/api/v1/scope` to receive `{"original":"Reader","local":"Scoped Reader",
"restored":"Reader"}`. The handler asserts nested restoration and closes both
Frames before returning. `verify.py` checks this response from the installed wheel.
Inherited expressions remain evaluated in their parent Frame; a child override
does not implicitly recalculate `business.greeting`.

In another terminal in this directory, use the installed console command:

```powershell
.venv\Scripts\lcl-fastapi.exe status -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe status -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe logs -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe logs -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe stop -o config service.lclcfg
```

On Linux replace `.venv\Scripts\lcl-fastapi.exe` with `.venv/bin/lcl-fastapi`.
`stop` waits for business teardown and log flush. Runtime and log paths resolve
relative to `service.lclcfg`, even when invoking the command from another
directory. Log observations are eventually consistent at the configured
0.2-second sampling interval. Treat the `run` directory and its control token
as private service-account state.

Generate deployment text without installing or executing Nginx/systemd:

```powershell
.venv\Scripts\lcl-fastapi.exe nginx render -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe nginx render -o config service.lclcfg -o output catalog.conf
.venv\Scripts\lcl-fastapi.exe systemd render -o config service.lclcfg
.venv\Scripts\lcl-fastapi.exe systemd render -o config service.lclcfg -o output catalog.service
```

The example certificate paths, account, and Linux deployment directories are
illustrative inputs. They need not exist for rendering. Review and supply real
deployment values before separately installing generated configuration.

## Automated verification

Stop any manually launched example first, then run:

```powershell
.venv\Scripts\python.exe verify.py
```

On Linux:

```sh
.venv/bin/python verify.py
```

The same script is suitable for the Windows/Linux GitHub CI matrix. It requires
an unused port `18082` and permission to create system temporary directories and
local child processes. It installs nothing and never deploys renderer output.

The verifier copies the exact service configuration into a fresh temporary
directory whose name contains spaces and launches from its parent directory.
The business target resolves from the installed downstream package. It checks
one real API worker, two managed background tasks, master identity, catalog state, LCL values,
route prefixes, local documentation assets, OpenAPI, server IDs, wrong/missing
shutdown-token rejection, JSON/plain status and logs, and complete Nginx/systemd
render output in both stdout and explicit-file forms. After normal `stop`, it
checks successful process exit, resource teardown messages, flushed request logs,
and removal of owned runtime observations and secrets. Failure cleanup uses the
same private configuration and the public authenticated `stop` command.

On Windows, shared-listener scheduling can favor one worker indefinitely. To
verify both catalogs, the script briefly pauses the worker observed in the first
response, opens a bounded burst of independent connections, requires its sibling
to answer within three seconds, and resumes the paused worker in `finally`.
Every request must then succeed with a distinct server-generated ID. This is
controlled test scheduling; the service itself does not pause or balance workers.

Only a successful run on a platform proves its runtime behavior. A Windows pass
does not substitute for the Linux/Gunicorn CI result.

Public contracts used to author this example:
[application](../../docs/application.md), [configuration](../../docs/configuration.md),
[logging](../../docs/logging.md), [health](../../docs/health.md),
[CLI](../../docs/cli.md), and [runtime](../../docs/runtime.md).
