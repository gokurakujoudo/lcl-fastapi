# Logging practices with a running service

Use one logger at the boundary that owns an operation. Log a caught exception
with `logger.exception()` while its `except` block is active. Let the framework
report errors that escape a handler or a registered Worker. This keeps the
original traceback and avoids logging the same failure at several business layers.

This complete service exercises startup, a successful API handler, an explicitly
handled LCL error, unhandled HTTP and Worker errors, and development hot reload.
The main process writes controller events, the API process writes service records,
and the `job` Worker writes its own file. A Worker is a managed thread inside the
API process; hot reload replaces that whole process and starts a new Worker.

## Install and create the service

Use a Python 3.14 virtual environment on Windows or Linux. Install from PyPI:

```console
python -m pip install "lcl-fastapi>=0.5.0"
```

Save these files in an empty directory. Run one service at a time on the machine.
The application uses no credentials or external network services.

`revision.py` provides a visible reload result:

<!-- tutorial-file: revision.py -->
<!-- python-doc-exec -->
```python
VALUE = 1
```

`app.py` owns its logger and business state within the service lifespan:

<!-- tutorial-file: app.py -->
<!-- python-doc-exec -->
```python
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from lclang.error import LclError
from lclang.logger import use_verbose

import revision
from lcl_fastapi import BackgroundWorkerContext, LclFastAPI, get_config, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger = await get_logger("business")
    logger.info("business startup revision=%s", revision.VALUE)
    try:
        yield
    finally:
        logger.info("business teardown revision=%s", revision.VALUE)


async def job(context: BackgroundWorkerContext) -> None:
    note = await context.get_config("business.worker_note")
    context.logger.info("job starting note=%s", note)
    raise RuntimeError("demonstration Worker failure")


service = LclFastAPI(lifespan=lifespan, background_workers={"job": job})


@service.get("/hello")
async def hello() -> dict[str, object]:
    logger = await get_logger("business")
    greeting = await get_config("business.greeting")
    logger.info("hello revision=%s", revision.VALUE)
    return {
        "greeting": greeting, "revision": revision.VALUE, "pid": os.getpid(),
        "verbose": await get_config("business.trace_enabled"),
    }


@service.get("/handled")
async def handled() -> dict[str, bool]:
    logger = await get_logger("business")
    # The final exception report is enough for this expected failure.
    with use_verbose(logger, enabled=False):
        try:
            await get_config("business.failure")
        except LclError:
            logger.exception("handled calculation failure")
    return {"recovered": True}


@service.get("/unhandled")
async def unhandled() -> None:
    raise RuntimeError("demonstration HTTP failure")
```

`service.lclcfg` selects the diagnostic policy and keeps the failed Worker from
restarting continuously. The native CLI `--verbose` flag enables LCL evaluation
diagnostics and lowers enabled sink thresholds to DEBUG. The framework carries
its Boolean `__verbose__` binding into downstream configuration, API processes
and background threads, including replacement processes after reload.
`logger.traceback_style` controls exception rendering independently.
Use compact reports during normal operation and full reports when investigating
upstream Python frames. Keep secrets out of exception messages: named LCL masks
do not redact arbitrary business strings.

<!-- tutorial-file: service.lclcfg -->
```text
__LCL_VERSION__: 1
using f"{lcl_fastapi_defaults}"

app.name: "logging-demo"
app.version: "1.0.0"
app.target: "app:service"
server.workers: 1
server.reload_dirs: ["."]
server.graceful_timeout_seconds: 10
health.sample_interval_seconds: 0.1
docs.enabled: False

logger.timezone: "utc"
logger.traceback_style: "compact"
logger.console.enabled: False
logger.file.default.directory: "./logs"
background_worker.job.auto_restart: False

business.greeting: "hello"
business.trace_enabled: __verbose__
business.worker_note: "inventory"
business.failure: 1 / 0
```

For interactive use, run `lcl-fastapi serve -c service.lclcfg --verbose -o hot_reload`.
Visit `/hello`, `/handled` and `/unhandled` on port 8080. Change `VALUE` in
`revision.py` to 222, visit `/hello` again, then run
`lcl-fastapi stop -c service.lclcfg`. The handled route returns 200; the unhandled
route returns a generic JSON 500. Both responses and their access logs carry the
server-generated request ID. Use `lcl-fastapi logs -c service.lclcfg` to locate
currently observed files; it reports paths without reading their content.

Omit `--verbose` to disable tracing, or use `use_verbose(logger, enabled=False)`
inside a business scope. Use `-o logger.traceback_style full` for full reports.
Disabling verbose keeps business/access records and exception reports enabled. It does
not remove the diagnostic trees attached to LCL exceptions.

## Reproduce and verify the log content

Save `verify.py` below. It selects an available loopback port, runs the native
server, waits for watcher readiness, exercises the routes, edits only this
directory's `revision.py`, and stops the service. It checks real files after
logger shutdown. Run it in this disposable example directory; it overwrites
`revision.py` and `console.log` and leaves the logs for inspection.

<!-- tutorial-file: verify.py -->
<!-- python-doc-exec -->
```python
import json
import os
import socket
import subprocess
import sysconfig
import time
from http.client import HTTPException
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import urlopen

import psutil


def logs(pattern: str = "*.log") -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(Path("logs").glob(pattern))
    )


def wait_for(predicate):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        assert process.poll() is None, Path("console.log").read_text(encoding="utf-8")
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("Service observation timed out")


def request(path: str, status: int = 200) -> dict:
    try:
        response = urlopen(f"http://127.0.0.1:{port}{path}", timeout=1)
    except HTTPError as error:
        response = error
    with response:
        assert response.code == status
        assert response.headers["X-Request-ID"].isdecimal()
        return json.load(response)


def ready(previous: int = 0) -> dict:
    try:
        data = request("/hello")
        return data if data["pid"] != previous else {}
    except (URLError, TimeoutError, HTTPException, ConnectionError):
        return {}


Path("revision.py").write_text("VALUE = 1\n", encoding="utf-8")
with socket.socket() as listener:
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
executable = Path(sysconfig.get_path("scripts")) / (
    "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
)
arguments = [str(executable), "-c", "service.lclcfg", "-o", "server.port", f"LCL[{port}]"]
with Path("console.log").open("wb") as output:
    process = subprocess.Popen(
        [arguments[0], "serve", *arguments[1:], "--verbose", "-o", "hot_reload"],
        stdout=output,
        stderr=subprocess.STDOUT,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        initial = wait_for(ready)
        assert initial["greeting"] == "hello" and initial["revision"] == 1
        assert initial["verbose"] is True
        wait_for(lambda: "hot-reload watcher ready" in logs("*controller*.log"))
        assert request("/handled") == {"recovered": True}
        assert request("/unhandled", 500) == {"detail": "Internal Server Error"}
        wait_for(lambda: "demonstration Worker failure" in logs("*job*.log"))
        Path("revision.py").write_text("VALUE = 222\n", encoding="utf-8")
        replacement = wait_for(lambda: ready(initial["pid"]))
        assert replacement["revision"] == 222
        assert replacement["verbose"] is True
        assert replacement["pid"] != initial["pid"]
        subprocess.run([arguments[0], "stop", *arguments[1:]], check=True, timeout=20)
        assert process.wait(timeout=20) == 0
    finally:
        if process.poll() is None:
            children = psutil.Process(process.pid).children(recursive=True)
            for child in reversed(children):
                if child.is_running():
                    child.kill()
            process.kill()
            process.wait(timeout=10)

controller = logs("*controller*.log")
worker = logs("*job*.log")
api = "\n".join(
    path.read_text(encoding="utf-8")
    for path in Path("logs").glob("*.log")
    if ".controller." not in path.name and ".job." not in path.name
)
for marker in ("service started", "background worker=job", "hot-reload retiring", "service stopped"):
    assert marker in controller
for marker in ("job starting", "background worker entry failed", "demonstration Worker failure"):
    assert marker in worker and marker not in api
for marker in ("business startup", "business teardown", "hello revision=222", "VERBOSE",
               "handled calculation failure", "business.failure", "ZeroDivisionError",
               "demonstration HTTP failure", "status_code=500"):
    assert marker in api
assert "request_id=" in api and "request_id=" not in worker
assert api.count("REST API started") == api.count("REST API completed")
assert api.count("REST API started") >= 4
assert "/fastapi/" not in api.replace("\\", "/")
assert "/starlette/" not in api.replace("\\", "/")
for line in controller.splitlines():
    if any(f" | {event}" in line for event in ("service ", "worker ", "background ", "hot-reload ")):
        assert "thread=" not in line and "write_events" not in line and "write_records" not in line
assert "service stopped" not in api
assert not Path("run/runtime.json").exists()
print("PASS native startup, handler, Worker, reload, caught and uncaught logs")
```

Run `python verify.py`. The documentation tests execute these exact files on
Windows and Linux in a separate temporary directory. The assertions check
behavior, routing and cleanup; timestamps, process IDs and file segment names
change on each run.

## Captured output

Service tracebacks hide FastAPI/Starlette frames while preserving business
frames and native cause/group diagnostics. Each REST call has a start and a
completion tree with its ID, path and connection context. Completion adds status
and duration. Controller event headers retain time, severity and process ID.

The excerpts below come from one Windows/Uvicorn run of the files above with
lclang 1.1.0. They retain the actual timestamps, identifiers, source locations
and diagnostic text. Only unrelated records and repeated poll requests are
omitted. Linux uses Gunicorn's native process lifecycle and records different
process IDs and paths.

<!-- captured-output -->

### REST invocation boundaries

The start and completion records share one server-generated invocation ID. Their native context trees carry the method/path, worker PID and connection metadata; the completion adds HTTP status and elapsed milliseconds.

```text
2026-10-11T13:18:45.107094Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | request_logging.py:52 | log_request_event | lcl_fastapi request_id=367662308186390528 REST API started
├─ method=GET path=/hello
│  └─ request_id=367662308186390528
└─ context
   ├─ worker_pid=11028
   ├─ client=('127.0.0.1', 6340)
   └─ scheme=http
2026-10-11T13:18:45.108936Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | request_logging.py:52 | log_request_event | lcl_fastapi request_id=367662308186390528 REST API completed
├─ method=GET path=/hello status_code=200 duration_ms=1.877
│  └─ request_id=367662308186390528
└─ context
   ├─ worker_pid=11028
   ├─ client=('127.0.0.1', 6340)
   └─ scheme=http
```

### Main process and hot reload

Controller events retain the main PID while worker IDs change. The failed job is observed by the controller; its traceback stays in the job file.

```text
2026-10-11T13:18:44.216187Z | INFO     | pid=30696 | service started service_pid=30696 workers=1
2026-10-11T13:18:45.039250Z | INFO     | pid=30696 | hot-reload watcher ready
2026-10-11T13:18:45.564815Z | ERROR    | pid=30696 | background worker=job attempt=1 status=failed exception=RuntimeError
2026-10-11T13:18:45.568327Z | INFO     | pid=30696 | worker up worker_pid=11028
2026-10-11T13:18:46.348372Z | INFO     | pid=30696 | hot-reload retiring worker_pid=11028
2026-10-11T13:18:46.868703Z | INFO     | pid=30696 | worker down worker_pid=11028
2026-10-11T13:18:48.154036Z | ERROR    | pid=30696 | background worker=job attempt=1 status=failed exception=RuntimeError
2026-10-11T13:18:48.157143Z | INFO     | pid=30696 | worker up worker_pid=27212
2026-10-11T13:18:48.623549Z | INFO     | pid=30696 | worker down worker_pid=27212
2026-10-11T13:18:48.623758Z | INFO     | pid=30696 | service stopped
```

### API startup, handler and teardown

The greeting is evaluated once and later reads use its native cache. The reload replaces the API process after business teardown. Request IDs occur on handler and invocation records.

```text
2026-10-11T13:18:44.938594Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.business | app.py:16 | lifespan | business business startup revision=1
2026-10-11T13:18:45.107744Z | VERBOSE  | pid=11028 thread=MainThread | lcl_fastapi request_id=367662308186390528 Evaluated business.greeting: (str) 'hello' (evaluation E2)
2026-10-11T13:18:45.107980Z | VERBOSE  | pid=11028 thread=MainThread | lcl_fastapi request_id=367662308186390528 Evaluation tree for business.greeting (evaluation E2)
  Result: (str) 'hello'
business.greeting: 'hello' ("D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\service.lclcfg":19:20)
2026-10-11T13:18:45.108041Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.business | app.py:36 | hello | business request_id=367662308186390528 hello revision=1
2026-10-11T13:18:45.108936Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | request_logging.py:52 | log_request_event | lcl_fastapi request_id=367662308186390528 REST API completed
├─ method=GET path=/hello status_code=200 duration_ms=1.877
│  └─ request_id=367662308186390528
└─ context
   ├─ worker_pid=11028
   ├─ client=('127.0.0.1', 6340)
   └─ scheme=http
2026-10-11T13:18:46.574515Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.business | app.py:20 | lifespan | business business teardown revision=1
2026-10-11T13:18:47.587965Z | INFO     | pid=27212 thread=MainThread | lcl_fastapi.api.business | app.py:16 | lifespan | business business startup revision=222
2026-10-11T13:18:47.651626Z | INFO     | pid=27212 thread=MainThread | lcl_fastapi.api.business | app.py:36 | hello | business request_id=367662318852505600 hello revision=222
2026-10-11T13:18:48.569345Z | INFO     | pid=27212 thread=MainThread | lcl_fastapi.api.business | app.py:20 | lifespan | business business teardown revision=222
```

### Explicit except

The handler suppresses verbose tracing for the expected failing calculation and logs the original LCL exception once. Its completion record reports the recovered 200 response.

```text
2026-10-11T13:18:45.315950Z | ERROR    | pid=11028 thread=MainThread | lcl_fastapi.api.business | app.py:51 | handled | business request_id=367662309058805760 handled calculation failure
Error in evaluating business.failure [LCL131421: division by zero]
├─ Cause
│  └─ ZeroDivisionError: division by zero
└─ business.failure: 1 / 0 ("D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\service.lclcfg":22:19)
   └─ Source: "D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\service.lclcfg":22:19
      business.failure: 1 / 0
                        ^^^^^
Python traceback:
LclEvaluationError [LCL131421: division by zero]
├─ Direct cause
│  └─ ZeroDivisionError: division by zero
└─ Traceback (most recent call last)
   ├─ File "D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\app.py", line 49, in handled
   │      await get_config("business.failure")
   └─ File "D:\SRC\AI\lcl-fastapi\src\lcl_fastapi\context.py", line 52, in get_config
          return await frame.get(key)
                 ^^^^^^^^^^^^^^^^^^^^
2026-10-11T13:18:45.316155Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | request_logging.py:52 | log_request_event | lcl_fastapi request_id=367662309058805760 REST API completed
├─ method=GET path=/handled status_code=200 duration_ms=0.807
│  └─ request_id=367662309058805760
└─ context
   ├─ worker_pid=11028
   ├─ client=('127.0.0.1', 6345)
   └─ scheme=http
```

### Unhandled HTTP error

The default callback logs the original exception within the request scope. The report retains the business frame; the response remains a generic 500. The native ASGI server may also report the exception that FastAPI re-raises after sending that response.

```text
2026-10-11T13:18:45.320657Z | ERROR    | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | errors.py:59 | report_error | lcl_fastapi request_id=367662309079777280 uncaught HTTP exception
Error in handling failure [LCL022890: demonstration HTTP failure]: RuntimeError: demonstration HTTP failure
Python traceback:
RuntimeError: demonstration HTTP failure
└─ Traceback (most recent call last)
   └─ File "D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\app.py", line 57, in unhandled
          raise RuntimeError("demonstration HTTP failure")
2026-10-11T13:18:45.320812Z | INFO     | pid=11028 thread=MainThread | lcl_fastapi.api.lcl_fastapi | request_logging.py:52 | log_request_event | lcl_fastapi request_id=367662309079777280 REST API completed
├─ method=GET path=/unhandled status_code=500 duration_ms=0.458
│  └─ request_id=367662309079777280
└─ context
   ├─ worker_pid=11028
   ├─ client=('127.0.0.1', 6346)
   └─ scheme=http
```

### Worker diagnostics and unhandled error

These records share the API PID but run on lcl-background-job without an HTTP request ID. The framework reports the escaped entry error, and auto_restart: False leaves that job failed until the API process is replaced.

```text
2026-10-11T13:18:44.957411Z | VERBOSE  | pid=11028 thread=lcl-background-job | job Evaluated business.worker_note: (str) 'inventory' (evaluation E1)
2026-10-11T13:18:44.957652Z | VERBOSE  | pid=11028 thread=lcl-background-job | job Evaluation tree for business.worker_note (evaluation E1)
  Result: (str) 'inventory'
business.worker_note: 'inventory' ("D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\service.lclcfg":21:23)
2026-10-11T13:18:44.957717Z | INFO     | pid=11028 thread=lcl-background-job | lcl_fastapi.background.job.job | app.py:25 | job | job job starting note=inventory
2026-10-11T13:18:44.957918Z | ERROR    | pid=11028 thread=lcl-background-job | lcl_fastapi.background.job.job | thread.py:176 | run | job background worker entry failed
Error in handling failure [LCL022890: demonstration Worker failure]: RuntimeError: demonstration Worker failure
Python traceback:
RuntimeError: demonstration Worker failure
└─ Traceback (most recent call last)
   ├─ File "D:\SRC\AI\lcl-fastapi\src\lcl_fastapi\background\thread.py", line 170, in run
   │      runner.run(self.await_entry(result))
   │      ~~~~~~~~~~^^^^^^^^^^^^^^^^^^^^^^^^^^
   ├─ File "C:\Users\ym\anaconda3\envs\py314\Lib\asyncio\runners.py", line 127, in run
   │      return self._loop.run_until_complete(task)
   │             ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^^^^
   ├─ File "C:\Users\ym\anaconda3\envs\py314\Lib\asyncio\base_events.py", line 719, in run_until_complete
   │      return future.result()
   │             ~~~~~~~~~~~~~^^
   ├─ File "D:\SRC\AI\lcl-fastapi\src\lcl_fastapi\background\thread.py", line 118, in await_entry
   │      await operation
   └─ File "D:\SRC\AI\lcl-fastapi\reports\logging-focused-events\app.py", line 26, in job
          raise RuntimeError("demonstration Worker failure")
```
