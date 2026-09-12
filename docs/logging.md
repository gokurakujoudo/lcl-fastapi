# Controller and request logging

Configure one shared directory and two filename expressions using the native
`lclang.logger` schema:

```text
# Logging and file destinations
logger.file.default.directory: "./logs"
logger.file.controller.filename: f"{app.name}.controller.log"
logger.file.service.filename: f"{app.name}.{worker_pid}.log"
logger.level: "INFO"
logger.file.service.rotation.mode: "size"
logger.file.service.rotation.max_bytes: 10485760
```

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

Use `await get_logger(prefix="", emit_level=0)` inside an active lifespan or
request. The prefix names the underlying logger and prefixes its messages.
The returned wrapper reads request identity each time a message is emitted,
before the record reaches lclang's writer thread. A logger reused between
requests does not retain the ID of the request that created it.

<!-- python-doc-exec -->
```python
from lcl_fastapi import LclFastAPI, get_logger, get_request_context

service = LclFastAPI()


@service.get("/work")
async def work() -> dict[str, str]:
    context = get_request_context()
    assert context is not None
    logger = await get_logger("example.work")
    logger.info("work completed")
    return {"request_id": context.request_id}
```

`RequestContext` contains `request_id`, `method`, `path`, and monotonic
`started_ns`. Outside a request, `get_request_context()` returns `None`. During
requests, the ID is attached both to the log record's `request_id` extra and to
the message so the default lclang formatter displays it. `emit_level` skips that
many additional business wrapper frames; the normal file/function/line point to
the business call site rather than the framework wrapper.

The framework records one access line for each HTTP request, including failures,
with method, path, status code, duration in milliseconds, worker PID, and Request
ID. Uvicorn and Gunicorn access loggers are disabled inside the worker scope
after logger takeover to prevent duplicate access lines, and their prior flags
are restored on exit. Query strings, credentials, cookies, and request bodies are
not automatically logged.

If the upstream Snowflake generator reports clock rollback (`RuntimeError`) or
timestamp/sequence exhaustion (`OverflowError`), the framework returns HTTP 503
with `{"detail": "Request ID generation unavailable"}`. Business routing is not
entered. This response has no generated request-ID header or request-state ID.
The single access record explicitly contains `request_id=unavailable` and
`id_generation_error` naming the exception type; it never borrows a prior request's
ID. Configuration and request-context bindings are restored even if sending the
failure response is cancelled or fails. The framework does not retry, synthesize
a fallback ID, or change the upstream clock/sequence algorithm. A later request
can succeed once the upstream generator can issue an ID again.

## Uncaught exception diagnostics

The default HTTP error callback logs the original exception traceback, chained
causes/contexts and ExceptionGroup members. Every Python frame includes its file,
function, exact traceback line number and available source line. Each parameter
appears on its own tab-indented line as `parameter: repr(value)`, including `*args`
and `**kwargs`. Values are read from the traceback's argument slots at failure time;
historical values overwritten or mutated earlier cannot be reconstructed.

There is no additional masking or truncation: representation policy belongs to
each object's `repr`. A failing repr, deleted parameter or unavailable source gets
an explicit placeholder. The callback never collects its own current call stack,
and does not read the HTTP body for logging. User arguments may themselves contain
request or application data, so protect these detailed log files accordingly.

Formatting runs away from the API event loop. Custom callback failures are logged
before the original exception's default diagnostic. Logger or formatter failures
produce a short best-effort summary without recursive callback invocation.

## Rotation and permanent segments

The pinned `lclang==1.0.10` writer owns rotation. Without an explicit policy,
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
operations and have no HTTP request ID. A worker's catalog log contains:

```text
log file: "/opt/catalog-api/logs/catalog-api.28146.20260912T100000.123456Z.000001.log"
catalog catalog ready
catalog request_id=123456789 list products
lcl_fastapi request_id=123456789 method=GET path=/api/v1/products status_code=200 duration_ms=0.420 worker_pid=28146
continued in: "/opt/catalog-api/logs/catalog-api.28146.20260912T100010.123456Z.000002.log"
```

The business prefix is `catalog`; the matching response header is
`X-Request-ID: 123456789`. Search that ID to correlate the business and access
records. Startup `catalog ready` and teardown `catalog closed` run outside a
request and therefore have no request ID. Teardown may be in a later segment.
The default formatter includes the business call site for business messages.

## Request IDs

Each actual worker owns one `lclang.utils.SnowflakeGenerator` for its lifespan.
The pure ASGI middleware calls `next_id()` once per HTTP request and uses its
decimal string as the server ID. On success it binds that ID in the task-local
`RequestContext`, `request.state.request_id`, and the configured response header
(default `X-Request-ID`). A client-supplied ID never replaces this value. The
framework leases concurrent worker IDs but does not implement Snowflake's
timestamp, sequence, bit composition, clock handling, or locks.

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
