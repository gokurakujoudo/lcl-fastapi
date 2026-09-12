# 3. Build an interactive LCL playground

[Series overview](../build-a-service.md) · Previous: [Directory monitor](02-directory-monitor.md)

## Background and objective

Configuration authors often need to answer three different questions: how did the
parser understand this expression, which names might it read, and what happened
when it actually ran? A final value alone cannot explain a conditional branch or
a cached definition. Re-evaluating every AST child for display would be misleading:
it could execute a branch that the original expression skipped.

The objective is to build a local browser workbench that keeps these operations
separate. A user edits LCL definitions and an expression, inspects the native AST
and dependency graph, evaluates once, and then replays the recorded native events.
A session keeps its Frame and cache across HTTP requests so the user can compare
first evaluation with cache reuse. A new parse replaces that Frame deliberately.

This example extends the series from background-produced shared state to
request-driven, per-session state. Its service resources must outlive individual
requests, have a clear owner, and close when sessions or the service end.

## What you will learn

| You will build | Feature or ownership rule you will practice |
| --- | --- |
| A configured, single-process application | `LclFastAPI`, `app.target`, `get_config()` and lifespan validation |
| A bounded in-memory session service | Lifespan-owned `app.state`, explicit cleanup and the implications of `server.workers: 1` |
| Thin typed HTTP commands | FastAPI/Pydantic integration retained by lcl-fastapi, response codes and intentional errors |
| An evaluator with consistent Frame ownership | Async request boundaries, a dedicated thread/loop and separation from the framework's configuration Frame |
| AST, dependencies and a replay UI | Native lclang parsing/analysis/evaluation, cache semantics and recorded execution rather than simulated steps |
| A usable local application | Static assets alongside built-in docs/health, request logs, native start/stop and installed-wheel verification |

lcl-fastapi manages the service configuration, API workers, logging and lifespan.
lclang implements the language. The session store, evaluation executor, trace
adapter and browser panels are application code; the framework does not provide
a built-in playground, debugger or session database.

## Before you start

Use CPython 3.14 on Windows or Linux, basic async Python, and a browser. The
[first chapter](01-catalog.md) introduces routes and configuration; the
[second chapter](02-directory-monitor.md) explains loop ownership and static
hosting. Stop other lcl-fastapi services and reserve loopback port `18086`.
Use expressions you trust. Installation needs package-index access, but no
external service or account is needed to run the workbench.

The checked-out example supplies the language-inspection and frontend scaffolding.
The steps below show how to assemble and exercise its lcl-fastapi integration,
then follow one session from creation through parsing, replay and cleanup.

## 1. Set up the project and explore one calculation

From `examples/playground` in the cloned repository:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install -e .
.venv/bin/lcl-fastapi serve -o config service.lclcfg
```

Open `http://127.0.0.1:18086/`. Choose **A small calculation**, select **Parse &
analyze**, inspect AST nodes and dependency arrows, then **Evaluate & record**.
Use Next step, Previous, Play or the position slider to explore real native events.
The local static assets are packaged with the example; there is no frontend build.

Use the editable installation while following the code. Keep commands in
`examples/playground`; use a second terminal for management operations. Later
commands use `lcl-fastapi` as shorthand for `.venv\Scripts\lcl-fastapi.exe` on
Windows or `.venv/bin/lcl-fastapi` on Linux. Use `curl.exe` in PowerShell.

| File | Responsibility |
| --- | --- |
| `service.lclcfg` | Address, one-worker setting, session limits and log directory |
| `playground_service/app.py` | Resource lifespan, typed API and static hosting |
| `playground_service/engine.py` | Session commands and Frame/thread ownership |
| `playground_service/inspection.py` | Native AST/dependency serialization and bounded trace collection |
| `playground_service/static/` | Editors, graph and replay controls |
| `verify.py` | Real-process API/session checks against the installed project |

**Checkpoint:** the initial calculation evaluates to 41. Evaluate a second time
and look for cache events. Parsing creates a fresh Frame, so parse again before
comparing a new first execution. Stop the manual service before changing settings.

## 2. Configure the service and validate its process model

Read this complete `service.lclcfg` before wiring up the application:

```text
__LCL_VERSION__: 1
using f"{lcl_fastapi_defaults}"
app.name: "lcl-playground"
app.version: "1.0.0"
app.target: "playground_service.app:service"
server.host: "127.0.0.1"
server.port: 18086
server.workers: 1
logger.file.default.directory: "./logs"
business.sessions.max_count: 32
business.sessions.idle_seconds: 900
```

`using` imports lcl-fastapi's universal defaults, then these local bindings
select the downstream app, address and business policy. `app.target` imports
`playground_service.app:service`; `app.version` describes this example application,
not the framework distribution. `business.sessions.*` is an ordinary nested LCL
scope. Access it with `await get_config(...)` after the framework has opened its
active configuration context, and validate your business limits explicitly.

This application requires `server.workers: 1` and rejects a larger effective count
during lifespan initialization. Process-local sessions cannot be shared between
API processes. `business.sessions.max_count` defaults to 32;
`business.sessions.idle_seconds` defaults to 900. A full session store returns 429.
Expired/missing identifiers return 404. New session explicitly deletes the old
tab session. A complete service restart discards all sessions.

A browser can send consecutive requests to different API processes in a
multi-worker service. An in-memory dictionary in one process would therefore
lose the session from the next request's point of view. This application rejects
that deployment instead of pretending its store is shared. Unlike chapter 2, it
does not register a managed background worker, so no registry forces the count
to one for you.

**Checkpoint:** stop and launch with
`lcl-fastapi serve -o config service.lclcfg -o server.workers "LCL[2]"`.
Startup must reject the session configuration. Do not enable hot reload during
this exercise, because hot reload itself forces one effective worker. Stop the
failed service if needed and restart without the override; the file stays intact.

## 3. Give the session engine a lifespan owner

The lifecycle core in `playground_service/app.py` is small. Use this structure
before adding routes; retain the finished project's models and routes when
working with its existing file:

<!-- python-doc-exec -->
```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from lcl_fastapi import LclFastAPI, get_config
from playground_service.engine import Engine


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
```

Read it as an ownership sequence:

1. Constructing `LclFastAPI(lifespan=lifespan)` records the resource factory. It
   must not create evaluator threads, Frames or sessions at module import.
2. At startup, read the effective worker count and business limits through
   `get_config()`. Invalid settings make business startup fail before serving.
3. Create one `Engine`, store it on `app.state`, and yield. Routes borrow this
   engine through `request.app.state.engine`; they do not create one per request.
4. In `finally`, await `engine.close()` while the service loop still exists.
   That closes session Frames on their owner loop before joining the thread.
   Framework cleanup and logger shutdown happen after business teardown.

The API uses one dedicated executor thread with one `asyncio.Runner`. Every native
Frame is constructed, used and closed there. API requests await serialized commands
without moving Frames across threads or loops. No service configuration, logger,
control credentials or parent runtime Frame is inherited by a playground Frame.
Shutdown queues Frame cleanup, closes the Runner, then joins the thread.

Session expiry is checked lazily when commands arrive, not by a timer. Expired
Frames close before capacity is reused. An active command completes before another
command can expire its session. This deliberately simple executor serializes users;
CPU-heavy trusted expressions can delay other users and contend for the GIL.

The executor is application-owned, demand-driven work. It is not a
`background_workers` registration: commands originate from HTTP requests, and
the application needs serialization and long-lived session Frames rather than
an independently restarting job. The same ownership principle from chapter 2
still applies: loop-bound resources stay on the loop that created them.

Do not keep a request's `use_lcl_frame()` context open as a user session. That
helper derives from service configuration and closes at scope exit. Here, user
programs need independent native Frames with no parent service Frame, so they
cannot inherit configuration names or service resource bindings accidentally.
See [scoped Frames](../application.md#scoped-lcl-frames) for the appropriate
request-local use of that framework helper.

**Checkpoint:** open a second browser tab and create a new session explicitly.
Parsing in one session must not populate the other session's definitions. Reload
the first tab to retrieve its saved program; a complete service restart discards
both sessions because this store is intentionally in memory.

## 4. Expose typed session commands through FastAPI routes

In `playground_service/app.py`, build routes on the lifespan-equipped `service`:

1. Define the Pydantic `Program` model with a `source` length limit and a nonempty
   `expression` length limit. FastAPI validates that JSON before the route runs.
2. Retrieve the engine from `request.app.state` and await `engine.submit(...)`.
   Keep the route asynchronous; waiting synchronously for the evaluator would
   block the API loop. The executor processes commands in order on its own loop.
3. Return dictionaries for ordinary success, and a `JSONResponse` for a language
   report whose HTTP status depends on whether it contains an `error`.
4. Use `HTTPException` for missing sessions, capacity and invalid operation order.
   These are expected API failures, handled before lcl-fastapi's unexpected-error
   fallback. A programming bug still receives the framework's detailed log and
   generic 500 response rather than a fabricated language diagnostic.

The concrete journey is POST a session, PUT its program, POST evaluation, GET
the saved report, then DELETE the session. Open `/docs` to inspect the typed
request model and try these operations before adding browser controls. Do not
return the live Engine or Frame as a response: serialize an inspection report.

## 5. Parse and analyze before allowing evaluation

The definitions editor accepts an in-memory `.lclcfg` document. The native
`parse_config` parser preserves source coordinates; this example rejects `using`
and duplicate definitions. It does not load files or import Python modules.
The expression editor uses native `parse_expression`. Syntax errors return 422
with the native diagnostic type, message and source span. An unsuccessful parse
leaves the previous session Frame intact; the UI requires a successful new parse
before evaluating edited text.

Follow the parse command in `engine.py` in this order: parse the document,
validate allowed definitions, parse the separate expression, build an inspection
report, construct a candidate Frame, and only then replace/close the previous
Frame. A failed parse must not destroy the last valid program. `inspection.py`
walks native children and source spans; it does not invent an AST from strings.

The supported public AST and dependency APIs can be exercised directly. Run this
small example to distinguish static potential reads from executed reads:

<!-- python-doc-exec -->
```python
from lclang import parse_expression
from lclang.runtime import analyze_dependencies

node = parse_expression("left if flag else right")
assert type(node).__name__ == "LclConditional"
assert node.span.start.line == 1
references = analyze_dependencies(node)
assert {(str(ref.name), ref.kind.value) for ref in references} == {
    ("flag", "eager"), ("left", "conditional"), ("right", "conditional")
}
```

The AST panel displays actual node classes, normalized LCL source and one-based
line/column spans. The dependency graph uses native scope-aware analysis, showing
eager, conditional and deferred occurrences. Arrows mean “may read”; a table
preserves every edge, including occurrences that share endpoints. Static analysis
does not promise a branch will run or a free name exists.

**Checkpoint:** enter a missing closing parenthesis and choose Parse & analyze.
Inspect the 422 diagnostic and its source location. Correct it and parse again.
Then inspect a conditional's two dependency edges: both may be present even
though only one branch will run in the next step.

## 6. Record one execution and replay its events

Choose **Conditional dependencies**. `right: 1 / 0` is present in the source and
dependency graph, but evaluating the selected left branch returns 42. Its replay
contains no lookup of `right`. Evaluate again: named definitions are cached, and
the trace reports cache hits. Parse again to create a fresh Frame and clear caches.
Choose **A circular dependency** to see native failure diagnostics and the events
leading up to the failure.

Replay is a recording of a completed evaluation, not an interactive paused
debugger. Moving its cursor never evaluates nodes again and cannot change program
state. It displays the events that lclang actually emits, rather than inventing
one step for every AST child. Short-circuited expressions remain unexecuted.

AST/Frame/dependency APIs are public. Native verbose recording currently requires
`lclang.diagnostics.internal_verbose_scope`, an internal hook in pinned lclang
1.0.10. Its use is isolated in the example engine and tested for event order,
conditional skipping and cache behavior. Revalidate this adapter before changing
lclang. This is not a newly promised lcl-fastapi tracing API.

Each evaluation captures at most 2,000 events and reports truncation. Result repr
display is limited to 8,192 characters. Native evaluation limits bound AST depth,
node visits and materialized collection sizes; they do not guarantee a hard CPU or
memory limit for every Python operation. The small explicit host-value set contains
`len`, `sum`, `min`, `max`, `abs` and `round`. These capabilities and language
reflection are not a hostile-code sandbox. Keep this trusted local learning tool
on loopback; use real process isolation and authentication for untrusted users.

## 7. Connect the editors, session storage and replay controls

Work through `playground_service/static/app.js` as a sequence of API clients:

1. On load, GET the session identifier saved in `sessionStorage`; if it no longer
   exists, POST a new session. Session storage survives reload within a tab but
   is not durable server storage. Duplicating a browser tab can copy its identifier;
   use **New session** when you want an independent experiment.
2. Parse & analyze sends a PUT with the current editors. On success, render the
   native AST and dependency report and enable evaluation. Editing invalidates
   that UI action until another successful parse.
3. Evaluate & record POSTs once, saves the returned trace array in the browser,
   and displays the result or language diagnostic.
4. Previous, Next, Play and the slider only move an index in that recorded array.
   They make no evaluation requests and cannot alter the Frame or execute skipped
   branches. Re-evaluation is a separate explicit action.

The route `@service.get("/", include_in_schema=False)` returns the installed
`index.html`; `service.mount("/static", StaticFiles(...))` serves its assets.
The browser uses same-origin relative API URLs, so no separate frontend server
or permissive CORS configuration is required. Keep the explicit home route and
asset prefix: a root catch-all mount can shadow lcl-fastapi's health/shutdown
routes registered during lifespan. Dynamic program/diagnostic text uses DOM text
nodes, not HTML injection.

**Checkpoint:** inspect the browser network panel while moving the replay slider.
Only a deliberate new evaluation should POST an evaluate request. Reload and
confirm the same session restores its source/report, then choose New session and
confirm its old definitions are unavailable. Check `/health` and `/docs` too.

## 8. Exercise the complete API and service lifecycle

| Endpoint | Behavior |
| --- | --- |
| `POST /api/sessions` | Create a session; return its opaque `id` and idle duration |
| `GET /api/sessions/{id}` | Read last parsed source, inspection and evaluation report |
| `PUT /api/sessions/{id}/program` | JSON `source` and `expression`; parse and replace Frame |
| `POST /api/sessions/{id}/evaluate` | Evaluate the parsed expression, preserving named caches |
| `DELETE /api/sessions/{id}` | Close the Frame and remove session state |

Evaluation failures return 422 with an `error` and captured trace. Evaluation
before parsing returns 409. Separate identifiers never share user definitions or
caches. The browser stores its identifier in sessionStorage, restores the source
after reload, and offers New session when an identifier has expired. Identifiers
are bearer-like access keys, not a substitute for user authentication. Static
values and diagnostic text are rendered as text, not executable HTML.

Before stopping, run `lcl-fastapi status -o config service.lclcfg` and
`lcl-fastapi logs -o config service.lclcfg`. The former reports the single API
worker; session/evaluator threads are application resources, not extra API
workers. The latter reports the service's actual log files. Use
`curl -i http://127.0.0.1:18086/health` to inspect the response request ID and
correlate it with the API access log. Parse errors are expected language reports;
they are different from an unhandled route exception in the framework error log.

Try the session policy with launch-only overrides after stopping the manual run:
`-o business.sessions.max_count "LCL[1]"` makes a second independent session return
429; deleting the first releases capacity. Separately use
`-o business.sessions.idle_seconds "LCL[2]"`, leave the session idle for over two
seconds, and send its next command to observe 404. Commands trigger expiry; merely
waiting does not run a cleanup timer. Restart normally to restore the defaults.

Stop a manual run with `lcl-fastapi stop -o config service.lclcfg`, then run
`.venv\Scripts\python.exe verify.py` (Linux: `.venv/bin/python verify.py`). The
verifier starts the installed package in a temporary working directory, fetches
static assets, checks parsing/AST/dependencies, evaluates a skipped failing branch,
checks cache reuse and session isolation, exercises diagnostics/deletion, and
stops the native service. CI runs the exact verifier with a built framework wheel
on Windows and Linux. The [full source](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/playground)
is independent of the framework's production package.
