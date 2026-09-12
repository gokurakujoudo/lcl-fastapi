# 3. Build an interactive LCL playground

[Series overview](../build-a-service.md) · Previous: [Directory monitor](02-directory-monitor.md)

Build a local workbench for writing LCL definitions and an expression, inspecting
the native AST, visualizing dependencies, and replaying an actual evaluation one
event at a time. Each browser tab keeps an opaque session identifier; session
Frames and caches stay in the service until deletion, idle expiry or shutdown.

## 1. Run the workbench

From `examples/playground` in the cloned repository:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\lcl-fastapi.exe serve -o config service.lclcfg
```

On Linux:

```sh
python3.14 -m venv .venv
.venv/bin/python -m pip install .
.venv/bin/lcl-fastapi serve -o config service.lclcfg
```

Open `http://127.0.0.1:18086/`. Choose **A small calculation**, select **Parse &
analyze**, inspect AST nodes and dependency arrows, then **Evaluate & record**.
Use Next step, Previous, Play or the position slider to explore real native events.
The local static assets are packaged with the example; there is no frontend build.

## 2. Own sessions on one worker

This application requires `server.workers: 1` and rejects a larger effective count
during lifespan initialization. Process-local sessions cannot be shared between
API processes. `business.sessions.max_count` defaults to 32;
`business.sessions.idle_seconds` defaults to 900. A full session store returns 429.
Expired/missing identifiers return 404. New session explicitly deletes the old
tab session. A complete service restart discards all sessions.

The API uses one dedicated executor thread with one `asyncio.Runner`. Every native
Frame is constructed, used and closed there. API requests await serialized commands
without moving Frames across threads or loops. No service configuration, logger,
control credentials or parent runtime Frame is inherited by a playground Frame.
Shutdown queues Frame cleanup, closes the Runner, then joins the thread.

Session expiry is checked lazily when commands arrive, not by a timer. Expired
Frames close before capacity is reused. An active command completes before another
command can expire its session. This deliberately simple executor serializes users;
CPU-heavy trusted expressions can delay other users and contend for the GIL.

## 3. Parse without evaluating

The definitions editor accepts an in-memory `.lclcfg` document. The native
`parse_config` parser preserves source coordinates; this example rejects `using`
and duplicate definitions. It does not load files or import Python modules.
The expression editor uses native `parse_expression`. Syntax errors return 422
with the native diagnostic type, message and source span. An unsuccessful parse
leaves the previous session Frame intact; the UI requires a successful new parse
before evaluating edited text.

The supported public AST and dependency APIs can be exercised directly:

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

## 4. Evaluate and replay the real execution

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

## 5. API and verification

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

Stop a manual run with `lcl-fastapi stop -o config service.lclcfg`, then run
`.venv\Scripts\python.exe verify.py` (Linux: `.venv/bin/python verify.py`). The
verifier starts the installed package in a temporary working directory, fetches
static assets, checks parsing/AST/dependencies, evaluates a skipped failing branch,
checks cache reuse and session isolation, exercises diagnostics/deletion, and
stops the native service. CI runs the exact verifier with a built framework wheel
on Windows and Linux. The [full source](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/playground)
is independent of the framework's production package.
