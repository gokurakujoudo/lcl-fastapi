# Managed background workers

Register Python callables in `LclFastAPI(background_workers={"name": callable})`.
The framework copies the mapping and validates its names and executables during
construction, without starting any resource. Names are Python identifiers;
`default`, `controller` and `service` are reserved. Configuration supplies parameters,
never executable entry points or external commands.

Each enabled callable owns a dedicated thread, asyncio Runner and configuration
Frame. An asynchronous entry executes on that thread's event loop. A synchronous
entry executes outside the loop and can use `context.run(awaitable)` for its local
asynchronous operations. Do not call `run()` from an asynchronous entry or another
thread. No work is placed in FastAPI's shared thread pool.

## Configuration

```text
background_worker.default.enabled: True
background_worker.default.auto_restart: True
background_worker.inventory.auto_restart: False
background_worker.heartbeat.schedule.seconds: 0.5
logger.file.inventory.filename: f"{app.name}.inventory.{worker_pid}.log"
logger.file.heartbeat.filename: f"{app.name}.heartbeat.{worker_pid}.log"
```

The two default switches are bundled defaults. Each named worker overrides an
individual leaf; both switches strictly require Boolean values. Other nested
settings are ordinary LCL bindings, read using their full qualified names.
Unregistered configuration cannot start a worker. Disabled registrations remain
visible in observations but do not start threads or open their dedicated file.

A nonempty registry requires one effective API worker, even when all registrations
are disabled. When a larger `server.workers` count is requested, startup validates
configuration, inspects the application in a short-lived import process, warns and
uses one worker. The probe does not enter business lifespan and does not preload
business modules into the master. A count already equal to one, or forced to one
by hot reload, needs no additional import probe. The file is never rewritten.
Status and health report the effective count. Import-time business side effects
can therefore run in a probe as well as an actual API worker; initialize resources
inside lifespan instead of module import.

Each thread loads its own configuration at startup with the launch-time CLI
overrides. Every execution attempt gets a fresh derived scope; automatic restarts
do not reload configuration. Nested `use_lcl_frame()` scopes temporarily change
`get_config()` lookup in that thread. Frames and their lazy caches never cross
event loops. Use a complete service restart to apply configuration changes.

## Resources and entry points

The exported `BackgroundWorkerContext` provides:

- `name`, `app` and the optional business-lifespan `state` mapping.
- `logger`, routed to this worker's named file, and `get_config(key)`.
- `stop_event`, a thread-safe cooperative stop signal.
- `run(awaitable)` for synchronous entries on the owning thread's Runner.
- `submit_to_service(async_callable)`, returning a concurrent Future whose factory
  runs on the API loop under the API's configuration context.

Use `app.state` for business resources and ordinary locks for thread-safe shared
objects. Loop-bound clients and connection pools must be used on their owning API
loop. Synchronous workers wait on the submitted Future; asynchronous workers use
`await asyncio.wrap_future(future)`. Await every submitted operation before leaving
the worker. The framework also drains actual submitted API Tasks, including their
cancellation cleanup, before closing an attempt's Frame, restarting that worker,
or tearing down business resources. Cancelling a Future alone is not that barrier.
An API-loop callback must not synchronously wait for the submitting
worker; that would deadlock. Submitted callbacks must themselves be nonblocking.

<!-- python-doc-exec -->
```python
import asyncio

from lcl_fastapi import BackgroundWorkerContext, LclFastAPI, use_lcl_frame


async def inventory(context: BackgroundWorkerContext) -> None:
    async def read_count() -> int:
        return len(context.app.state.catalog)

    count = await asyncio.wrap_future(context.submit_to_service(read_count))
    async with use_lcl_frame(values={"batch.count": count}) as frame:
        assert await frame.get("batch.count") == count
    context.logger.info("inventory count=%s", count)


def heartbeat(context: BackgroundWorkerContext) -> None:
    while not context.stop_event.wait(1):
        context.logger.info("heartbeat")


service = LclFastAPI(background_workers={"inventory": inventory, "heartbeat": heartbeat})
```

Initialize `app.state.catalog` in a business lifespan before running this service;
the [composed example](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/composed/README.md) provides the complete runnable
application, configuration and verifier. Configure inventory's auto_restart=False
for a finite task. The default True treats any return as a restartable worker exit.

Threads keep blocking I/O and worker event loops away from the API event loop.
They still share the process, GIL, CPU, memory and any application locks; arbitrary
CPU-bound or GIL-holding code can affect API responsiveness. This is not process
isolation. Background work starts without an HTTP request ID or request context.

## Restart, shutdown and observations

Business lifespan initializes first, then all enabled background thread resources
must initialize before API startup completes. A configuration/resource initialization
failure fails service startup and cleans already-started threads. Startup also
requires a successful initial journal write; inaccessible lifecycle storage fails
startup before any background entry runs. An entry failure
is recorded and governed by auto_restart. With that switch enabled, both normal
returns and Exceptions wait one second before another attempt. The wait is
interruptible. With it disabled, completed or failed status remains observable.
Process-exit BaseExceptions end the affected thread rather than becoming HTTP errors.

Normal completion and its restart use INFO. Exception exits use ERROR; their
scheduled restarts use WARNING. Controller events contain worker name, attempt,
status and reason, while detailed task tracebacks go to the background file.
Short-lived executions are journaled rather than inferred from sampling, so normal
rapid exits are not lost between health refreshes. The controller is the sole
writer of its controller log; the API publishes ordered, identity-scoped JSONL
events in the private runtime directory. Those journals are removed after the
controller consumes their final events and verifies process exit.

On stop or reload, the framework prevents further restarts, sets every stop_event,
and cancels active asynchronous entries. It waits for thread cleanup before business
lifespan teardown and finally logger flushing. Synchronous entries must cooperate;
asynchronous entries must not indefinitely suppress cancellation. The API loop
remains available for resource cleanup submissions during this wait.

After `server.graceful_timeout_seconds`, the controller logs the timeout and
terminates the **entire API process**, checking its creation-time identity before
signaling. A normal service stop does not restart it; hot reload replaces it.
The controller starts its own retirement deadline at stop/reload, independently
of journal delivery. Runtime journal publication failure is logged immediately,
stops further background executions and requests service shutdown. A failed
journal channel cannot disable the controller's process retirement deadline.
If stop/reload is already retiring the worker, publication failure preserves that
operation rather than converting reload into a whole-service stop. Controller
deadlines begin with the native retirement request and use a monotonic clock;
repeated requests and late journal records cannot extend them.
The already-reported publication error is not rethrown into business teardown;
cooperative shutdown still closes business resources normally.
Forced termination cannot guarantee worker cleanup, business teardown, transactions
or API log flushing. The local stop command can report its graceful deadline
before forced retirement has finished; inspect status afterward. Controller event
delivery is best effort after an abrupt process or machine crash.

`health.service.background_workers` and each status worker observation report
per-name status, attempts and restarts. Disabled entries have zero counters.
These are thread observations, not extra API workers or Snowflake leases. `logs`
includes their actual upstream file paths and follows the existing eventual
consistency and rotation contract.

## File routing

`logger.file.<worker_name>` configures a worker's file; absent filenames default to
`<app.name>.<worker_name>.<worker_pid>.log`. Files inherit `logger.file.default` and
retain native levels, rotation and directory semantics. API and background records
share the one process-wide lclang queue writer. Do not open another
`use_logger_handler` scope from a worker.

With background registration, records receive source namespaces
`lcl_fastapi.api.<original>` or `lcl_fastapi.background.<worker>.<original>` before
the writer. Native logger-name filters are qualified for the matching role, keeping
default API/background files separate while preserving business prefixes and call
locations. A worker's `get_logger()` calls follow its context automatically; service
bridge callbacks use API logging context. Unregistered additional sinks remain API
sinks. The controller file is never opened by an API/background thread.
