# Controller and request logging

For executable examples and captured logs from the main process, API handler,
Worker, hot reload and exception boundaries, see [logging practices](logging-practices.md).

## Record structure in unreleased source

The layouts, request-ID names, `log_event()` and runtime file controls below are
unreleased capabilities. Current source requires `lclang>=1.1.2`; installing the
published lcl-fastapi 0.5.0 release alone does not provide them.

With the native default formatter, a full business record has these columns:

```text
timestamp | level | thread_name(pid) | logger_name | module.py:line:function | message
```

A compact record omits the logger-name and caller columns:

```text
timestamp | level | thread_name(pid) | message
```

Within an HTTP request, the full record's `logger_name` is the server-generated
request ID. Compact and native TRACE records place that ID at the start of the
message, followed by ` | `. The ID appears once with either default layout.
An explicit `logger.format` chooses its own columns and takes precedence over
compact selection; including both `%(name)s` and a compact message can repeat the ID.

| Record | Default layout | Identity and diagnostic content |
| --- | --- | --- |
| HTTP start/completion | Compact | Request-ID message prefix, method, actual path and client; completion adds status and duration. |
| Business call | Full, or compact when explicitly selected | Request ID in the name column or message prefix; full records retain the business file, line and function. |
| Native verbose evaluation | Compact, displayed as TRACE | Request-ID message prefix and native evaluation/failure identifiers, with a tree when available. |
| Final HTTP exception | Compact | Request-ID message prefix and invocation title, followed by the native error tree and applicable Python traceback. |
| Controller event | Compact | Controller PID in the header and observed service/worker identity in the message; no HTTP request ID. |
| Managed Worker record | Full for business calls, compact for framework notices | Worker registration, attempt and entry identity; dedicated file routing without an HTTP request ID. |

An event's indented fields, evaluation tree or exception report belong to the
same record as its first line. They are formatted together by the native writer;
rotation does not split them. For practical choices, see
[choosing a logger](logging-practices.md#choose-a-logger) and
[layout and diagnostic policy](logging-practices.md#choose-layout-and-diagnostic-policy).

## Log content boundaries

The logger preserves diagnostic evidence; it does not enforce an application's
global secret-removal policy. The controls have separate purposes:

| Control | What it changes | What it does not redact |
| --- | --- | --- |
| Per-record `compact` | Default header columns and caller display. | Message text, fields or attached exceptions. |
| `logger.traceback_style` | Which Python helper frames appear. | Exception messages, business source excerpts, causes or group members. |
| Native verbose scopes and runtime file controls | Whether execution diagnostics are produced/admitted. | Ordinary business records or final exception reports. |
| `log_event(..., masked_fields=...)` and declared LCL masks | Explicitly selected values in that event or native diagnostic. | Other records, arbitrary exception text or third-party output. |

Access records contain the actual URL path and client endpoint. Omitting query
parameters, headers and bodies does not make every path safe for application
policy. Keep credentials out of paths and choose safe identifiers. Escaping
control characters prevents multiline text injection; bounded representations
and `safe_repr` do not identify secrets.

Applications with stricter requirements must own content selection across their
business records, exception boundaries and third-party logging. A generic HTTP
response protects the response body, not all later server diagnostics. Producer
and writer failures can also report diagnostic text to stderr. Neither compact
reports nor a custom HTTP callback establishes a process-wide redaction policy.
See [exception ownership](application.md#uncaught-http-exceptions).

## Native verbose control

### Unreleased source: runtime file diagnostics

`logging.file_verbose` is a strict Boolean with a default of False. The native
CLI `--verbose` flag also enables its initial value. Runtime file controls affect
native execution diagnostics and framework detail records; ordinary business
DEBUG records continue to follow the configured logging levels. Console
diagnostics retain the launch-time CLI policy.

`await set_file_verbose(enabled)` submits a service-wide target and returns its
immutable `FileVerboseState` (`enabled`, `revision`). Repeating the current value
does not advance the revision. `await get_file_verbose()` returns a
`FileVerboseStatus` with that target and immutable process observations: role,
PID, observed value, revision and Unix observation time. Both functions require
an active service logging scope and work from API and managed Worker threads.

HTTP clients can GET or PUT `/_lcl/logging/verbose`; PUT accepts an object with
one strict Boolean `enabled` and returns 202 with the accepted target. Both
operations require the current `X-LCL-Control-Token`, return 403 for invalid
credentials, and remain absent from OpenAPI. Generated Nginx configuration
blocks this path. Business routes retain the existing exact-method/path override
rule. Storage failures return 503 without claiming a successful change.

Processes observe changes through the existing sampling cycle. A request pins
the observed value at entry; a managed Worker pins it for each attempt, and
controller observations pin it for each batch. Child tasks inherit the pin.
Already running requests, streams and long-lived Worker attempts retain their
previous policy. A process observation confirms receipt of a target, not a
change to an execution already in progress. Queued records retain their policy.
Local `use_verbose(..., enabled=False)` still suppresses diagnostics.
Cancelling a Python update waits for its submitted atomic operation to finish,
then propagates cancellation. Query the target after an interrupted call; the
write may have committed. This keeps updates inside the service's cleanup scope.

The control state belongs to one service start. Replacement workers and hot
reload inherit it; a complete restart reads the configuration and CLI initial
value again. A failed refresh retains the last valid in-memory state; a new
process must obtain valid state before starting. This control does not reload
configuration or change exception reporting, rotation, disabled sinks, or source
filters.

Start with `lcl-fastapi serve -c service.lclcfg --verbose` to enable native LCL
diagnostics. The same `--verbose` flag controls the short-lived lclang CLI scope
and the deferred service's downstream scopes. The Boolean `__verbose__` binding
is available to `.lclcfg` expressions and `get_config("__verbose__")`; it is
framework-owned and cannot be redefined. Omitting the flag selects False.

The CLI `--verbose` flag also lowers enabled service sinks to DEBUG while preserving source filters,
timezone, exception style, and disabled sinks. Business lifespan, API requests
and background Worker evaluation use public
`use_verbose(logger, enabled=...)`. Requests retain their IDs on diagnostic
records; Workers use their own file routing. Spawned and replacement workers
receive the original flag. The playground passes it to its bounded trace capture.

A local `with use_verbose(logger, enabled=False):` silences tracing until scope
exit, including child tasks created inside that scope. In a synchronous Worker,
put that scope inside the coroutine passed to `context.run()` so it runs in the
Worker Runner's context. The caller owns child tasks and must finish them before
the logger closes. Turning off tracing preserves ordinary records and exception
diagnostics. `logger.traceback_style` remains an independent compact/full policy.

## Sinks and timestamps

Framework lifecycle and informational records use the native `compact=True`
flag. The default compact header contains timestamp, level and `thread_name(pid)`;
full business records also include logger name and the qualified caller location.
Business calls default to `compact=False` and can select either layout per record.
An explicit `logger.format` remains authoritative. Native display labels include
WARN, CRIT, UNSET and TRACE without changing numeric severities or filtering.

Each HTTP invocation emits one single-line start and one single-line completion:

```text
369400111685435392 | REST API Started: HTTP GET /orders/42 from 127.0.0.1:65311
369400111685435392 | REST API Completed(200): HTTP GET /orders/42 from 127.0.0.1:65311 [duration_ms=0.420]
```

These are message payloads; the header supplies the PID. Paths exclude queries,
control characters are escaped, IPv6 clients use `[address]:port`, and absent
clients appear as `unknown`. Headers and bodies are not read for logging.
Allocation failures retain a 503 and an `unavailable | ` prefix, with native error
type/code appended to the completion record. No request ID is invented.

Default compact Python tracebacks hide installed lclang, lcl_fastapi, FastAPI and
Starlette helper frames. `logger.traceback_style: "full"` retains lcl_fastapi and
lclang frames; FastAPI/Starlette filtering applies in both modes. Business frames,
causes, notes, groups and LCL diagnostic trees remain. Rendering never changes
the original exception or traceback.

Configure one shared directory and two filename expressions using the native
`lclang.logger` schema:

```text
# Logging and file destinations
logger.file.default.directory: "./logs"
logger.file.controller.filename: f"{app.name}.controller.log"
logger.file.service.filename: f"{app.name}.{worker_pid}.log"
logger.level: "INFO"
logger.timezone: "local"
logger.file.service.rotation.mode: "size"
logger.file.service.rotation.max_bytes: 10485760
```

Current source requires `lclang>=1.1.2`. Log timestamps default to the
server's local time with microseconds and a numeric offset, such as
`2026-10-08T15:30:00.123456+08:00`. Set `logger.timezone: "utc"` or pass
`-o logger.timezone utc` to use timestamps ending in `Z`. These settings apply
to controller, API and background logs. Verbose mode preserves the timezone.
Only `"local"` and `"utc"` are accepted; non-text values and named timezones
are rejected before service sinks open, with source-aware diagnostics.
Segment filenames and aligned rotation continue to use UTC. The separate
short-lived CLI stderr logger retains its own local-time default.

`logger.file.controller` belongs exclusively to the service controller.
`logger.file.service` retains its existing name and belongs exclusively to each
worker. The default directory is inherited by both; native per-sink level,
rotation, and directory settings remain available. Additional named sinks belong
to workers. Controller resolution never evaluates worker filenames. Workers do
not open the controller sink. Native masked bindings retain their masking in
the selected role.

When background callables are registered, their matching named sinks are reserved
for those background threads; other additional sinks remain API sinks. See
[background file routing](background-workers.md#file-routing) for shared-writer
ownership, source namespacing and controller lifecycle events.

Controller files record complete-service start/stop/failure, heartbeat, observed
worker up/down transitions, hot-reload retirement, and observed worker log segment
changes. Observations follow `health.sample_interval_seconds` and the native
manager loop, so they are eventually consistent and can miss workers that start
and exit between samples. Controller messages are file-only. The controller keeps
an upstream handler
scope open between events. Before Gunicorn forks a worker, it drains the writer
and closes its event loop and executor; only the parent reopens the scope after
the fork. This starts a new upstream segment per worker creation, rather than
per heartbeat. No logger thread or file handler survives into the fork. Windows
spawns workers and retains the controller scope until service exit. Worker request
and business logs retain their worker-long handler scope.

`worker_pid` is supplied inside the real worker. Defining it in the application
file is rejected. LCL computes the filename, while lclang owns permanent
segments, rotation suffixes, writes, and flush. There is no `logger.log_dir` or
`logger.filename` compatibility alias.

## Request-aware loggers

Use `await get_logger()` inside an active lifespan or request. No argument is
required to obtain request IDs. The full signature is
`get_logger(prefix="", emit_level=0)`; `prefix` selects both a stable source and
an optional message prefix:

| Call | Stable source | Message prefix | `logger.name` inside a request |
| --- | --- | --- | --- |
| `await get_logger()` | `lcl_fastapi` | None | Current request ID |
| `await get_logger("orders")` | `orders` | `orders` | Current request ID |

Choose a stable business prefix only when it helps source filtering, source
levels or human-readable classification. It does not name the request. Keep
request IDs, paths and user IDs out of this argument: source loggers are
registered for the process lifetime, whereas request IDs are record metadata.

Inside a request, `logger.name` and queued record names are the current request
ID. Outside requests, the wrapper's name returns to its stable source. A reused
logger reads identity on each emission, so concurrent requests remain isolated
without registering their IDs as loggers. `lcl_source_name` snapshots the stable
routing source before the queued name changes. When background workers are
registered, routing qualifies that source with `lcl_fastapi.api.` or
`lcl_fastapi.background.<worker>.`; configure file filters with the original
source, such as `logger.file.service.logger_names: ["orders"]`. The framework
applies the role namespace for the sink.

Compact request messages, including native TRACE records, receive a standard
`request_id | ` prefix. Full business messages use the name column instead.
Request method, actual path and the selected handler's module-qualified identity
remain scalar record extras (`request_method`, `request_path`, `api_handler`).
They are not repeated in every business message. Worker records retain their
registration, attempt and entry identity. Execution metadata adds no requests or
configuration Frames to queued records.

### Explicit business events in unreleased source

`RequestLogger.log_event(event, *, level=logging.INFO, record=None, fields=(),
masked_fields=(), compact=False, **values)` emits one record. The bounded, escaped title is
followed by four-space-indented `field: value` lines with aligned colons. Only
selected direct dataclass fields are read, followed by keyword values in order.
Names in `masked_fields` are replaced before reading a field or invoking repr.
Disabled levels return before inspecting the record. Ordinary message objects
and percent-style arguments remain deferred until the native writer formats them.

A wrong title or record type raises `TypeError`. Unknown, duplicate or conflicting
selected fields or masks raise `ValueError`; unmasked getters can propagate their own
errors. No whole-record expansion or automatic request body logging occurs.
Use the same API as `context.logger.log_event(...)` inside a managed Worker.

This isolated example uses the active native writer. The service adds its
request identity and routing metadata when it owns that writer:

<!-- python-doc-exec -->
```python
import asyncio
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from lclang.logger import LoggerHandlerConfig, use_logger_handler
from lcl_fastapi import get_logger


@dataclass
class Order:
    order_id: int
    token: str


async def demonstrate_event() -> None:
    with TemporaryDirectory() as directory:
        config = LoggerHandlerConfig(
            console={"enabled": False},
            file={"example": {"directory": directory}},
        )
        async with use_logger_handler(config):
            logger = await get_logger("orders")
            logger.info("short operational notice", compact=True)
            logger.log_event(
                "order accepted", record=Order(42, "private"),
                fields=("order_id", "token"), masked_fields=("token",), quantity=3,
            )
        text = "\n".join(path.read_text() for path in Path(directory).glob("*.log"))
        assert "order accepted" in text and "order_id: 42" in text
        assert "token   : *masked*" in text and "private" not in text
        assert "quantity: 3" in text


asyncio.run(demonstrate_event())
```

<!-- python-doc-exec -->
```python
from lcl_fastapi import LclFastAPI, get_logger, get_request_context

service = LclFastAPI()


@service.get("/work")
async def work() -> dict[str, str]:
    context = get_request_context()
    assert context is not None
    logger = await get_logger()
    assert logger.name == context.request_id
    logger.info("work completed")
    return {"request_id": context.request_id}
```

`RequestContext` contains `request_id`, `method`, `path`, and monotonic
`started_ns`. Outside a request, `get_request_context()` returns `None`. The ID is
also attached to request state and the configured response header. `emit_level`
skips additional business wrapper frames while preserving native caller attribution.

Uvicorn and Gunicorn access loggers are disabled during the worker scope to avoid
duplicate access records; prior flags are restored on exit. Completion retains
the status already sent, including when a stream fails after its headers.

If the upstream Snowflake generator reports an invalid epoch (`LclValidationError`),
clock rollback (`LclStateError`) or timestamp/sequence exhaustion (`LclUtilityError`),
the framework returns HTTP 503
with `{"detail": "Request ID generation unavailable"}`. Business routing is not
entered. This response has no generated request-ID header or request-state ID.
The completion record uses the `unavailable | ` prefix and
`id_generation_error` naming the native exception type and `id_generation_code`
containing its LCL error code; it never borrows a prior request's
ID. Configuration and request-context bindings are restored even if sending the
failure response is cancelled or fails. The framework does not retry, synthesize
a fallback ID, or change the upstream clock/sequence algorithm. A later request
can succeed once the upstream generator can issue an ID again.

## Uncaught exception diagnostics

The default HTTP error callback emits a compact record whose message starts with
`request_id | Uncaught Exception in HTTP METHOD /actual/path from client:`, followed
by a newline and the native error tree. It submits the original exception and its traceback
to the native logger. Reports retain causes, contexts and ordinary exception-group
members. LCL failures include their error code, failed dependency path, source
excerpt and correlation identifiers. Native diagnostics preserve declared masks.
After successful default reporting, the escaping ASGI boundary confirms that
same exception for one-time suppression of the runtime's duplicate error record.
Unrelated runtime errors and failures not reported by the framework still pass.
Returning a custom response alone does not confirm reporting or suppress the
server's original exception. When a callback fails, its report may contain the
original error as an exception-chain member before the default original-error
report; server deduplication does not remove evidence from those distinct failures.
Masks protect named LCL diagnostic values; exception messages and application
text follow their producers' policies.

`logger.traceback_style` accepts `"compact"` (default) or `"full"`. Both hide
installed FastAPI/Starlette frames. Compact reports retain business and other
external-library frames; full reports also include lclang and lcl_fastapi frames.
Select the same policy with `-o logger.traceback_style full`.
Controller, API and background logs share the selected policy. Invalid values
fail before service sinks open.

The callback does not inspect function arguments or read the HTTP body. The native
writer formats records away from the API event loop. Custom callback failures are
logged before the original exception's default diagnostic. Producer logging
failures receive a short best-effort stderr summary without recursive callbacks;
native writer failures follow the upstream sink-isolation and cleanup policy.

## Rotation and permanent segments

The upstream lclang writer owns rotation. Without an explicit policy,
rotation mode is `none`: opening a logger scope still creates a fresh segment,
but size and time do not switch it. Configure a shared policy, then specialize
either sink:

```text
# Logging and file destinations
logger.file.default.rotation.mode: "size_or_time"
logger.file.default.rotation.max_bytes: 10485760
logger.file.default.rotation.interval: "1d"
logger.file.default.rotation.align: True
logger.file.controller.rotation.max_bytes: 1048576
```

Both sinks rotate at midnight UTC or their size threshold, whichever happens
first. The controller uses 1 MiB and each worker uses 10 MiB. These are per-file
thresholds, not a directory quota. Explicit sink fields override shared fields.

| Mode | Required fields | Trigger |
| --- | --- | --- |
| `none` | None | No automatic rollover. |
| `size` | Positive integer `max_bytes` | Before the next record would exceed the encoded-byte threshold in a segment that already has a record. |
| `time` | `interval` | Writer timer, including while idle. |
| `size_or_time` | Both | Either trigger; size rollover does not reset the time schedule. |

Intervals are positive integers followed by `s`, `m`, `h`, or `d`, such as `30s`
or `6h`. `align: False` (default) measures elapsed intervals from scope startup;
`align: True` accepts only `1h` or `1d` and uses UTC boundaries. Records are never
split, so a large first record and metadata can exceed `max_bytes`.

A configured `catalog-api.28146.log` becomes, for example,
`catalog-api.28146.20260912T100000.123456Z.000001.log`: the writer inserts a UTC
timestamp and a process-wide sequence before the suffix. Sequence numbers can
have gaps because other sinks share the counter. Old segments are neither
renamed, overwritten, compressed, nor automatically deleted. Arrange retention
for closed files through your operator's archival process; rotation alone does
not bound total disk usage. Restarting a worker creates a new segment too.
On Linux, controller scope reopening around worker forks also creates segments;
this does not imply that a size/time threshold was reached.

Each file begins with `log file: "<absolute path>"`. On rollover the old file
ends with `continued in: "<successor absolute path>"`. Paths are JSON-escaped,
including backslashes on Windows. An ordinary scope close has no continuation
footer. Follow those links to read a rotated stream; use `logs` JSON to discover
the currently observed worker segments.

This isolated example exercises the same native writer without starting a
service. A deliberately tiny threshold forces intact records into new files;
exiting the scope flushes them before inspection:

<!-- python-doc-exec -->
```python
import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory

from lclang.logger import LoggerHandlerConfig, use_logger, use_logger_handler


async def demonstrate_rotation() -> None:
    with TemporaryDirectory() as directory:
        config = LoggerHandlerConfig(
            console={"enabled": False},
            file={"service": {
                "directory": directory,
                "filename": "demo.log",
                "rotation": {"mode": "size", "max_bytes": 1},
            }},
        )
        async with use_logger_handler(config):
            logger = await use_logger(name="rotation-demo")
            logger.info("first intact record")
            logger.info("second intact record")
        segments = sorted(Path(directory).glob("demo.*.log"))
        assert len(segments) == 2
        first, second = [path.read_text(encoding="utf-8") for path in segments]
        assert first.startswith("log file: ") and "continued in: " in first
        assert "first intact record" in first
        assert "second intact record" in second and "continued in: " not in second


asyncio.run(demonstrate_rotation())
```

## Log file samples

These excerpts show message payloads with the default formatter's timestamp,
level, PID/thread, logger, and source-location columns omitted for readability.
PIDs, timestamps, paths, and request IDs are illustrative. Controller files
contain messages such as:

```text
log file: "/opt/catalog-api/logs/catalog-api.controller.20260912T100000.123456Z.000001.log"
service started service_pid=28140 workers=1
heartbeat service_pid=28140 workers=1
worker up worker_pid=28146
worker log rotate worker_pid=28146 paths=['/opt/catalog-api/logs/catalog-api.28146.20260912T100010.123456Z.000002.log']
hot-reload retiring worker_pid=28146
worker down worker_pid=28146
service stopped
```

Transitions appear when observed, so their order relative to heartbeats varies.
Failure exits can include `service failed`. Controller records describe service
operations and have no HTTP request ID. API records use the single-line messages
shown above. Full business records retain their call sites and request-ID names;
compact business records retain the ID through their message prefix. Search the
ID from the matching response header to correlate the complete invocation.

## Request IDs

Each actual worker owns one `lclang.utils.SnowflakeGenerator` for its lifespan.
The pure ASGI middleware calls `next_id()` once per HTTP request and uses its
decimal string as the server ID. On success it binds that ID in the task-local
`RequestContext`, `request.state.request_id`, and the configured response header
(default `X-Request-ID`). A client-supplied ID never replaces this value. The
framework leases concurrent worker IDs but does not implement Snowflake's
timestamp, sequence, bit composition, clock handling, or locks.

HTTP middleware shallow-copies the incoming state dictionary before binding the
ID. Top-level request state stays independent even if a server supplies the same
dictionary to concurrent requests. Lifespan-owned resources inside that dictionary
remain shared by reference; this does not copy or take ownership of those resources.

## Observing active files

```console
lcl-fastapi logs -o config service.lclcfg
```

The result contains `paths`, `observed_at`, and `stale`. Paths come from lclang's
actual file-sink metrics published by live workers. They are never guessed from
directory modification times. `observed_at` is the oldest contributing Unix
timestamp, or null without an observation. `stale` identifies a live worker's
overdue view; exited workers' paths are excluded.

Rollover is eventually consistent: a normal change becomes visible by the next
`health.sample_interval_seconds` refresh plus metric sampling and the state write. Refresh continues
when the health endpoint is disabled. Inspecting paths does not stream or read
log content. Business teardown logs are flushed before worker shutdown finishes.
