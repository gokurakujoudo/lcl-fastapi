# Request logging

The worker owns one `lclang.logger` handler scope. Configure its real upstream
schema without framework aliases:

```text
logger.file.default.directory: "./logs"
logger.file.service.filename: f"{app.name}.{worker_pid}.log"
logger.level: "INFO"
logger.file.service.rotation.mode: "size"
logger.file.service.rotation.max_bytes: 10485760
```

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
lcl-fastapi logs -o config service.lclcfg -o json
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
