# 3. Build an interactive LCL playground

[Series overview](../build-a-service.md) · Previous: [Directory monitor](02-directory-monitor.md)

The [complete working code](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/playground)
is available for reference. This guide starts from an empty directory and provides
every file you need; create and extend them in the order shown.

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

Use CPython 3.14 on Windows or Linux and basic async Python. Stop any other
lcl-fastapi service: run one service at a time on the machine. Installation needs
package-index access, but the finished application needs no external account.
Keep the listener on loopback and use trusted local input.

## 1. Create and configure the project

Create an empty project directory. No repository checkout is needed.

On Windows:

```powershell
mkdir lcl-workbench
cd lcl-workbench
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install lcl-fastapi==0.3.0
mkdir playground_service
mkdir playground_service\static
```

On Linux:

```sh
mkdir lcl-workbench
cd lcl-workbench
python3.14 -m venv .venv
.venv/bin/python -m pip install lcl-fastapi==0.3.0
mkdir -p playground_service/static
```

Keep subsequent files and commands in this directory. Later commands use
`lcl-fastapi` and `python` as shorthand for `.venv\Scripts\lcl-fastapi.exe` and
`.venv\Scripts\python.exe` on Windows, or `.venv/bin/lcl-fastapi` and
`.venv/bin/python` on Linux. In PowerShell use `curl.exe` instead of `curl`.
Use a second terminal for HTTP and management commands while the server runs.

Create `playground_service/__init__.py` to make an importable package:

<!-- tutorial-file: playground_service/__init__.py -->
<!-- python-doc-exec -->
```python
"""Session-based native LCL playground tutorial package."""
```

Create `pyproject.toml` so the application and its static files can be installed together:

<!-- tutorial-file: pyproject.toml -->
```toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "lcl-fastapi-playground-example"
version = "1.0.0"
requires-python = ">=3.14"
dependencies = ["lcl-fastapi==0.3.0"]

[tool.hatch.build.targets.wheel]
packages = ["playground_service"]
```

Install this local project with `python -m pip install -e .`.
The editable install lets later Python changes take effect after a service restart.
The dependency installs the framework; the new package contains your application.
Do not start the service until the step that creates its `service` object.

Create `service.lclcfg` with functional groups:

<!-- tutorial-file: service.lclcfg -->
```text
__LCL_VERSION__: 1

# Shared configuration
using f"{lcl_fastapi_defaults}"

# Application identity and import target
app.name: "lcl-playground"
app.version: "1.0.0"
app.target: "playground_service.app:service"

# HTTP serving and shutdown
server.host: "127.0.0.1"
server.port: 18086
server.workers: 1

# Logging and file destinations
logger.file.default.directory: "./logs"

# Business settings
business.sessions.max_count: 32
business.sessions.idle_seconds: 900
```

`app.target` will load the service you create in step 6. The default import
retains lcl-fastapi's health, docs, request-ID and runtime behavior; your local
bindings choose the port and session policy. `business.sessions.*` remains native
LCL configuration, read through `get_config()` during business lifespan.

Use `server.workers: 1`: an in-memory session dictionary belongs to one API
process. More processes could receive requests for sessions they do not own.
This example validates and rejects a larger count; it does not register a managed
background worker that would force the count for you. Session state is not durable.

## 2. Turn native language objects into inspection reports

Create `playground_service/inspection.py`. Keep these language-specific helpers
outside the HTTP routes. `tree()` recursively serializes native node classes,
source and one-based spans. `dependencies()` preserves native dependency kinds;
an edge says “may read”, not “executed”. `Trace` collects actual log events into a
bounded list. All returned data can be serialized; live Frames cannot.

<!-- tutorial-file: playground_service/inspection.py -->
<!-- python-doc-exec -->
```python
"""Render native AST and capture real diagnostics from pinned lclang 1.0.10."""

import logging
from dataclasses import asdict

from lclang import LclAstNode, LclError, to_source
from lclang.runtime import analyze_dependencies


def tree(node: LclAstNode) -> dict[str, object]:
    return {
        "kind": type(node).__name__,
        "source": to_source(node),
        "span": asdict(node.span),
        "children": [tree(child) for child in node.children()],
    }


def dependencies(definitions: dict[str, LclAstNode]) -> list[dict[str, object]]:
    return [
        {
            "source": name,
            "target": str(reference.name),
            "kind": reference.kind.value,
            "span": asdict(reference.span),
        }
        for name, node in definitions.items()
        for reference in analyze_dependencies(node, scoped_names=definitions)
    ]


def diagnostic(error: Exception) -> dict[str, object]:
    return {
        "type": type(error).__name__,
        "message": str(error),
        "span": asdict(error.span) if isinstance(error, LclError) and error.span else None,
    }


class Trace(logging.Handler):
    """Bound actual diagnostic events; replay never evaluates a subtree again."""

    def __init__(self) -> None:
        super().__init__()
        self.events: list[str] = []
        self.truncated = False

    def emit(self, record: logging.LogRecord) -> None:
        if len(self.events) < 2000:
            self.events.append(record.getMessage())
        else:
            self.truncated = True
```

**Checkpoint:** run the following standalone check. Static analysis includes both
conditional alternatives without executing either:

<!-- python-doc-exec -->
```python
from lclang import parse_expression
from lclang.runtime import analyze_dependencies

node = parse_expression("left if flag else right")
assert type(node).__name__ == "LclConditional"
assert {(str(ref.name), ref.kind.value) for ref in analyze_dependencies(node)} == {
    ("flag", "eager"), ("left", "conditional"), ("right", "conditional")
}
```

## 3. Give session commands one evaluator thread and loop

Create `playground_service/engine.py` with this first part. You will append methods
to `Engine` in the next two steps; preserve their four-space class indentation.

<!-- tutorial-file: playground_service/engine.py -->
<!-- python-doc-exec -->
```python
"""Keep every session Frame on one dedicated thread and event loop."""

import asyncio
import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from fastapi import HTTPException
from lclang import EvaluationLimits, Frame, LclError, Module, ModuleName, parse_expression
from lclang.config import ConfigDefinition, parse_config
from lclang.diagnostics import internal_verbose_scope

from playground_service.inspection import Trace, dependencies, diagnostic, tree


@dataclass
class Session:
    frame: Frame | None = None
    touched: float = field(default_factory=time.monotonic)
    source: str = ""
    expression: str = ""
    report: dict[str, Any] = field(default_factory=dict)


class Engine:
    """Serialize session operations; Frames never leave the evaluation thread."""

    def __init__(self, maximum: int, ttl: float) -> None:
        self.maximum, self.ttl = maximum, ttl
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lcl-playground")
        self.runner: asyncio.Runner | None = None
        self.sessions: dict[str, Session] = {}

    async def submit(self, action: str, session_id: str = "", **payload: str) -> dict[str, Any]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self.executor, self.call, action, session_id, payload)

    def call(self, action: str, session_id: str, payload: dict[str, str]) -> dict[str, Any]:
        if self.runner is None:
            self.runner = asyncio.Runner()
        return self.runner.run(self.dispatch(action, session_id, payload))

    async def dispatch(
        self, action: str, session_id: str, payload: dict[str, str]
    ) -> dict[str, Any]:
        now = time.monotonic()
        for key, session in list(self.sessions.items()):
            if now - session.touched >= self.ttl:
                await self.remove(key)
        if action == "create":
            if len(self.sessions) >= self.maximum:
                raise HTTPException(
                    429, "Session limit reached; delete a session or wait for expiry"
                )
            key = secrets.token_urlsafe(24)
            self.sessions[key] = Session()
            return {"id": key, "idle_seconds": self.ttl}
        if session_id not in self.sessions:
            raise HTTPException(404, "Session missing or expired")
        session = self.sessions[session_id]
        session.touched = now
        if action == "delete":
            await self.remove(session_id)
            return {"closed": True}
        if action == "read":
            return {"source": session.source, "expression": session.expression, **session.report}
        if action == "parse":
            return await self.parse(session, payload["source"], payload["expression"])
        if action == "evaluate":
            return await self.evaluate(session)
        raise ValueError("Unknown session operation")

    async def remove(self, key: str) -> None:
        session = self.sessions.pop(key)
        if session.frame is not None:
            await session.frame.close()

```

`submit()` is the API's async boundary. It queues work on a dedicated one-thread
executor and awaits it, allowing the API loop to serve other requests. `call()`
creates one Runner on that thread, so every session Frame is created, evaluated
and closed on the same event loop. Commands serialize deliberately.

The dispatch method creates opaque IDs, enforces capacity, expires idle sessions
when a command arrives, and routes operations to a session. Removing a session
closes its Frame before capacity can be reused. Nothing runs an expiry timer.

This is application-owned request-driven work, not `background_workers`: it
requires persistent session Frames and ordered commands, not independent jobs
that restart automatically. lcl-fastapi will own the Engine through lifespan.
Do not keep `use_lcl_frame()` from a request open as a session: that scope closes
at exit and inherits service configuration. User programs need independent native
Frames without a parent service Frame or inherited service resources.

## 4. Parse a replacement program without evaluating it

Append this method inside `Engine` in `playground_service/engine.py`:

<!-- tutorial-append: playground_service/engine.py -->
<!-- python-doc-fragment -->
```python
    async def parse(self, session: Session, source: str, expression: str) -> dict[str, Any]:
        try:
            document = parse_config(source, source_name="session.lclcfg")
            definitions = {}
            for declaration in document.declarations:
                if not isinstance(declaration, ConfigDefinition):
                    raise ValueError("using is not enabled in this in-memory playground")
                name = str(declaration.name)
                if name in definitions:
                    raise ValueError(f"Duplicate definition: {name}")
                definitions[name] = declaration.expression
            node = parse_expression(expression)
            module = Module(ModuleName("playground"), definitions)
            report = {
                "ast": {name: tree(value) for name, value in definitions.items()},
                "expression_ast": tree(node),
                "dependencies": dependencies({**definitions, "<expression>": node}),
            }
            frame = Frame(
                module,
                values={"len": len, "sum": sum, "min": min, "max": max, "abs": abs, "round": round},
                limits=EvaluationLimits(max_depth=60, max_steps=10000, max_collection_items=1000),
            )
        except (LclError, ValueError, RecursionError) as error:
            return {"error": diagnostic(error)}
        if session.frame is not None:
            await session.frame.close()
        session.frame, session.source, session.expression = frame, source, expression
        session.report = report
        return report

```

Parse the configuration and expression, reject imports/duplicate definitions,
build reports, then construct a candidate Frame. Only after success do you close
and replace the previous Frame. Syntax errors therefore leave the last valid
session program intact. The explicit host-value set is small; no service config,
logger or control credentials are inherited by this Frame.

The source editor and service `.lclcfg` have different roles: service configuration
is operator-managed and resolved by lcl-fastapi; edited user programs are in-memory
documents parsed by this application. Do not feed browser programs into the
framework's configuration loader.

## 5. Record real evaluation and close resources on their owner loop

Append the remaining `Engine` methods:

<!-- tutorial-append: playground_service/engine.py -->
<!-- python-doc-fragment -->
```python
    async def evaluate(self, session: Session) -> dict[str, Any]:
        if session.frame is None:
            raise HTTPException(409, "Parse a program before evaluating")
        trace = Trace()
        logger = logging.Logger("playground.trace", level=logging.DEBUG)
        logger.addHandler(trace)
        report: dict[str, Any] = {}
        # This pinned-version adapter is the only internal lclang integration.
        # Native task-local diagnostics preserve branch skipping and cache hits.
        with internal_verbose_scope(logger):
            try:
                value = await session.frame.evaluate(session.expression)
                report["result"] = {"type": type(value).__name__, "repr": repr(value)[:8192]}
            except Exception as error:
                report["error"] = diagnostic(error)
        report.update(trace=trace.events, trace_truncated=trace.truncated)
        session.report["evaluation"] = report
        return report

    async def close(self) -> None:
        def finish() -> None:
            if self.runner is not None:
                for key in list(self.sessions):
                    self.runner.run(self.remove(key))
                self.runner.close()

        await asyncio.get_running_loop().run_in_executor(self.executor, finish)
        self.executor.shutdown(wait=True)
```

Each evaluation runs once and records native events. Replaying their array later
must not evaluate AST children again: that could execute a skipped failing branch.
Named definitions remain cached until parse replacement, deletion, expiry or
shutdown closes the Frame. `close()` queues cleanup on the evaluator loop before
closing its Runner and joining the thread.

The pinned `lclang.diagnostics.internal_verbose_scope` adapter is internal to
lclang 1.0.10; AST/Frame/dependency APIs are public. Revalidate the trace adapter
before upgrading lclang. It is not a newly promised lcl-fastapi tracing API.

**Checkpoint:** run this exact script before adding HTTP. It proves the failing
right branch is skipped and the next evaluation uses a cache:

<!-- python-doc-exec -->
```python
import asyncio
from playground_service.engine import Engine

async def main():
    engine = Engine(2, 60)
    try:
        session = (await engine.submit("create"))["id"]
        source = """__LCL_VERSION__: 1

# Branch inputs
flag: True
left: 42
right: 1 / 0

# Selected value
answer: left if flag else right
"""
        await engine.submit("parse", session, source=source, expression="answer")
        first = await engine.submit("evaluate", session)
        assert first["result"]["repr"] == "42"
        assert not any("name='right'" in event for event in first["trace"])
        second = await engine.submit("evaluate", session)
        assert any("cached" in event for event in second["trace"])
    finally:
        await engine.close()

asyncio.run(main())
```

## 6. Attach the Engine to business lifespan and expose sessions

Create `playground_service/app.py` with this first part:

<!-- tutorial-file: playground_service/app.py -->
<!-- python-doc-exec -->
```python
"""Serve a local LCL workbench with opaque, bounded in-memory sessions."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import FileResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from lcl_fastapi import LclFastAPI, get_config
from playground_service.engine import Engine


class Program(BaseModel):
    source: str = Field(max_length=16000)
    expression: str = Field(min_length=1, max_length=4000)


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
service.add_middleware(
    TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "testserver"]
)


def engine(request: Request) -> Engine:
    origin = request.headers.get("origin")
    if origin and origin != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "Cross-origin sessions are not allowed")
    result: Engine = request.app.state.engine
    return result


@service.post("/api/sessions", status_code=201)
async def create(request: Request) -> dict[str, Any]:
    return await engine(request).submit("create")


@service.get("/api/sessions/{session_id}")
async def read(session_id: str, request: Request) -> dict[str, Any]:
    return await engine(request).submit("read", session_id)


@service.delete("/api/sessions/{session_id}")
async def delete(session_id: str, request: Request) -> dict[str, Any]:
    return await engine(request).submit("delete", session_id)


```

The framework opens its configuration/logger before calling your lifespan, so
`get_config()` now reads the effective worker count and business limits. Validate
them, create one Engine, store it on `app.state`, and yield. Routes borrow it from
`request.app.state` instead of constructing an executor per request.

`finally: await engine.close()` runs while the API loop is still available.
Business cleanup finishes before framework/logger teardown. Resource creation at
module import would bypass this ownership and could run during startup probes.

Start `lcl-fastapi serve -o config service.lclcfg`, then call:

```sh
curl -i -X POST http://127.0.0.1:18086/api/sessions
curl http://127.0.0.1:18086/health
```

**Checkpoint:** creation returns 201 with an opaque `id`; health returns 200.
Copy the ID and GET `/api/sessions/ID` to see the empty program. Stop with
`lcl-fastapi stop -o config service.lclcfg` before changing code; restart after
each following Python step.

## 7. Add typed parse and evaluation endpoints

Append these routes to `playground_service/app.py`:

<!-- tutorial-append: playground_service/app.py -->
<!-- python-doc-fragment -->
```python
@service.put("/api/sessions/{session_id}/program")
async def parse(session_id: str, program: Program, request: Request) -> JSONResponse:
    report = await engine(request).submit("parse", session_id, **program.model_dump())
    return JSONResponse(report, status_code=422 if "error" in report else 200)


@service.post("/api/sessions/{session_id}/evaluate")
async def evaluate(session_id: str, request: Request) -> JSONResponse:
    report = await engine(request).submit("evaluate", session_id)
    return JSONResponse(report, status_code=422 if "error" in report else 200)


```

The Pydantic `Program` model validates request sizes before the handler runs.
Routes await `Engine.submit()`; they never block the API loop on a synchronous
Future. A native language failure is an expected 422 report with diagnostics and
possibly a trace. Missing sessions return 404, capacity 429, and evaluation before
parse 409. FastAPI's intentional handlers stay ahead of lcl-fastapi's unexpected
exception logging and generic 500 fallback.

**Checkpoint:** restart, open `/docs`, create a session and use its ID to PUT a
program, POST evaluation, GET the saved report, then DELETE it. Try an invalid
expression to inspect its diagnostic span. A second session must not share the
first session's source or cache. The APIs are usable before the frontend exists.

## 8. Add reusable LCL syntax highlighting

Create `playground_service/static/lcl-highlight.js` and `lcl-highlight.css` below.
They are standalone MIT-licensed assets with no dependency on lcl-fastapi, this
app, a frontend framework or a CDN. The lexer preserves every source character,
accepts unfinished input and escapes source text before producing markup.

<details>
<summary>Complete playground_service/static/lcl-highlight.js</summary>

<!-- tutorial-file: playground_service/static/lcl-highlight.js -->
```javascript
/*
MIT License

Copyright (c) 2026 gokurakujoudo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
/* MIT License. Standalone lexical highlighting for LCL 1; no parser or execution. */
(function (root) {
  "use strict";
  const keywords = new Set("and or not if else for in is true false none True False None raise try except finally assert with as using".split(" "));
  const pattern = /(?<space>\s+)|(?<comment>#[^\r\n]*)|(?<string>(?:[rRbBfF]{1,2})?(?:"""(?:\\[\s\S]|(?!""")[^\\])*?(?:"""|$)|'''(?:\\[\s\S]|(?!''')[^\\])*?(?:'''|$)|"(?:\\[\s\S]|[^"\\\r\n])*(?:"|(?=\r|\n|$))|'(?:\\[\s\S]|[^'\\\r\n])*(?:'|(?=\r|\n|$))))|(?<number>0[xX][\da-fA-F_]+|0[bB][01_]+|0[oO][0-7_]+|(?:\d[\d_]*(?:\.[\d_]*)?|\.\d[\d_]*)(?:[eE][+-]?[\d_]+)?)|(?<name>[_\p{ID_Start}][_\p{ID_Continue}]*)|(?<operator>\*\*|\/\/|<<|>>|<=|>=|==|!=|\?\.|\?\?|->|[+\-*\/%@&|^~<>=])|(?<punctuation>[()[\]{},.:])|(?<text>[\s\S])/guy;

  /** Return lossless {kind, text} slices, including unfinished input and whitespace. */
  function tokenize(source) {
    if (typeof source !== "string") throw new TypeError("LCL source must be a string");
    const tokens = [];
    pattern.lastIndex = 0;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      let kind = Object.keys(match.groups).find(key => match.groups[key] !== undefined);
      if (kind === "name" && keywords.has(match[0])) kind = "keyword";
      tokens.push({kind, text: match[0]});
    }
    return tokens;
  }

  /** Return escaped markup. All source characters are text, never executable HTML. */
  function highlight(source) {
    return tokenize(source).map(({kind, text}) => {
      const escaped = text.replace(/[&<>"']/g, character => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"})[character]);
      return kind === "space" || kind === "text" ? escaped : `<span class="lcl-${kind}">${escaped}</span>`;
    }).join("");
  }

  /** Render a code/pre element without changing the source or evaluating it. */
  function render(element, source = element.textContent) {
    element.innerHTML = highlight(source);
    element.classList.add("lcl-code");
  }

  /** Enhance an existing textarea; call update after assigning value, destroy to detach. */
  function attach(textarea) {
    if (textarea.tagName !== "TEXTAREA") throw new TypeError("Expected a textarea");
    if (textarea.parentElement?.classList.contains("lcl-editor")) throw new Error("Textarea already highlighted");
    const document = textarea.ownerDocument;
    const wrapper = document.createElement("div"), backdrop = document.createElement("pre");
    const originalWrap = textarea.getAttribute("wrap");
    wrapper.className = "lcl-editor";
    backdrop.setAttribute("aria-hidden", "true");
    textarea.before(wrapper);
    wrapper.append(backdrop, textarea);
    textarea.wrap = "off";
    function sync() { backdrop.scrollTop = textarea.scrollTop; backdrop.scrollLeft = textarea.scrollLeft; }
    function update() { render(backdrop, textarea.value + "\n"); sync(); }
    textarea.addEventListener("input", update);
    textarea.addEventListener("scroll", sync);
    update();
    return {
      update,
      destroy() {
        textarea.removeEventListener("input", update);
        textarea.removeEventListener("scroll", sync);
        if (originalWrap === null) textarea.removeAttribute("wrap"); else textarea.setAttribute("wrap", originalWrap);
        wrapper.replaceWith(textarea);
      }
    };
  }

  const api = Object.freeze({tokenize, highlight, render, attach});
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.LCLHighlight = api;
})(globalThis);
```

</details>

<details>
<summary>Complete playground_service/static/lcl-highlight.css</summary>

<!-- tutorial-file: playground_service/static/lcl-highlight.css -->
```css
/*
MIT License

Copyright (c) 2026 gokurakujoudo

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
*/
/* MIT License. Copy with lcl-highlight.js; override these variables to theme it. */
.lcl-code{--lcl-comment:#657568;--lcl-string:#24663d;--lcl-number:#914706;--lcl-keyword:#713cad;--lcl-operator:#b22b43;color:#183049}
.lcl-comment{color:var(--lcl-comment)}.lcl-string{color:var(--lcl-string)}.lcl-number{color:var(--lcl-number)}.lcl-keyword{color:var(--lcl-keyword)}.lcl-operator{color:var(--lcl-operator)}
.lcl-editor{position:relative;width:100%;isolation:isolate}
.lcl-editor>textarea,.lcl-editor>pre{box-sizing:border-box;width:100%;margin:0;border:1px solid #bfcede;border-radius:7px;padding:10px;font:14px/1.65 Consolas,monospace;letter-spacing:normal;tab-size:2;white-space:pre;overflow-wrap:normal;text-align:left}
.lcl-editor>pre{position:absolute;inset:0;height:100%;overflow:hidden;pointer-events:none;background:#fcfdff}
.lcl-editor>textarea{position:relative;display:block;background:transparent;color:transparent;caret-color:#183049;resize:vertical;overflow:auto}
.lcl-editor>textarea::selection{background:#91bafa66;color:transparent}
@media(forced-colors:active){.lcl-editor>pre{display:none}.lcl-editor>textarea{color:CanvasText;caret-color:CanvasText;background:Canvas}}
```

</details>

For another application, copy these two files, include the stylesheet and script,
then call `LCLHighlight.render(codeElement, source)` for static code or
`LCLHighlight.attach(textarea)` for an editor. The latter returns `update()` and
`destroy()`; call `update()` after setting `.value` programmatically and `destroy()`
before removing an editor. No module bundler is required. CommonJS users can
require the JavaScript file for `tokenize()` and escaped `highlight()` markup.

```html
<link rel="stylesheet" href="lcl-highlight.css">
<textarea id="program" aria-label="LCL program" spellcheck="false">count: 3</textarea>
<script src="lcl-highlight.js"></script>
<script>
  const editor = LCLHighlight.attach(document.getElementById("program"));
</script>
```

This is lexical coloring, not validation or execution. It colors comments, strings
(including whole f-strings), numbers, names, keywords and operators. It does not
recursively color expressions inside f-strings; the native parser remains the
authority. Theme it with the `--lcl-*` CSS variables. The textarea remains native
for selection, keyboard input, undo and IME; a hidden-to-accessibility backdrop
provides color, and forced-colors mode falls back to plain readable text.

## 9. Build the workbench frontend and mount it

Create these HTML and stylesheet files. The form supplies source/expression
textareas, AST/dependency panels and replay controls. The highlighter script is
loaded before the app script, and its CSS after the page styles.

<details>
<summary>Complete playground_service/static/index.html</summary>

<!-- tutorial-file: playground_service/static/index.html -->
```html
<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>LCL Workbench</title><link rel="stylesheet" href="/static/style.css"><link rel="stylesheet" href="/static/lcl-highlight.css"><script src="/static/lcl-highlight.js" defer></script><script src="/static/app.js" defer></script></head>
<body><header><div><div class="eyebrow">Build a service · Example 03</div><h1>LCL Workbench</h1><p>Follow a configuration from source to syntax, dependencies and values.</p></div><span class="badge" id="session-status">Opening session…</span></header>
<main><div class="split"><section class="panel editor"><div class="toolbar"><h2>Your program</h2><button id="new-session">New session</button></div><label for="sample">Try a starting point</label><select id="sample"><option value="catalog">A small calculation</option><option value="conditional">Conditional dependencies</option><option value="error">A circular dependency</option></select><label for="source">LCL definitions</label><textarea id="source" rows="12" spellcheck="false" aria-describedby="source-help">__LCL_VERSION__: 1

# Inputs
price: 12
quantity: 3

# Derived value
total: price * quantity</textarea><p id="source-help" class="muted">In-memory definitions. No using/imports or service configuration is inherited.</p><label for="expression">Expression to evaluate</label><textarea id="expression" rows="2" spellcheck="false">total + 5</textarea><div class="toolbar" style="margin-top:18px"><button id="parse" class="primary">Parse &amp; analyze</button><button id="evaluate" disabled>Evaluate &amp; record</button></div><div id="message" class="notice" role="status">Parse your source to explore its structure.</div><p class="muted">Named values stay cached within this session. Parsing again replaces the Frame and clears its cache.</p></section>
<section class="panel"><div class="tabs" role="tablist" aria-label="Inspection views"><button role="tab" aria-selected="true" aria-controls="ast" id="tab-ast" data-tab="ast">AST</button><button role="tab" aria-selected="false" aria-controls="dependencies" id="tab-dependencies" data-tab="dependencies">Dependencies</button><button role="tab" aria-selected="false" aria-controls="trace" id="tab-trace" data-tab="trace">Evaluation replay</button></div>
<section id="ast" role="tabpanel" aria-labelledby="tab-ast"><h2>Native syntax tree</h2><p class="muted">Expand nodes to inspect the source and one-based line/column span.</p><div id="tree" class="tree">Parse a program to build its tree.</div></section>
<section id="dependencies" role="tabpanel" aria-labelledby="tab-dependencies" hidden><h2>Dependency map</h2><p class="muted">Arrows point from a definition to a name it may read. Conditional and deferred edges need not execute.</p><div id="graph"></div><div class="scroll"><table><thead><tr><th>From</th><th>Reads</th><th>Kind</th></tr></thead><tbody id="edges"></tbody></table></div></section>
<section id="trace" role="tabpanel" aria-labelledby="tab-trace" hidden><h2>Actual execution, one event at a time</h2><p class="muted">Replay of a completed native evaluation, not a paused debugger. Skipped branches are not evaluated for display.</p><pre id="result">No evaluation yet.</pre><div class="toolbar"><button id="previous" disabled>Previous</button><button id="next" disabled>Next step</button><button id="play" disabled>Play</button><span id="position" aria-live="polite">0 / 0</span></div><label for="cursor">Replay position</label><input id="cursor" type="range" min="0" max="0" value="0" style="width:100%" disabled><div id="step" class="step">Evaluate a parsed program to capture its trace.</div></section></section></div>
<footer>Trusted local learning tool · One API worker · Opaque sessions expire after inactivity · Native lclang 1.0.10 diagnostics, with bounded trace display.</footer></main></body></html>
```

</details>

<details>
<summary>Complete playground_service/static/style.css</summary>

<!-- tutorial-file: playground_service/static/style.css -->
```css
:root{font-family:system-ui,-apple-system,Segoe UI,sans-serif;color:#182b43;background:#f4f6fa;font-size:15px}*{box-sizing:border-box}body{margin:0}header{background:#152a43;color:white;padding:26px max(5vw,20px);display:flex;justify-content:space-between;align-items:center;gap:20px}h1{font-size:27px;margin:6px 0}h2{font-size:18px;margin:0 0 16px}p{line-height:1.55}.eyebrow{font-size:11px;letter-spacing:.16em;text-transform:uppercase;color:#9db7d6}header p{margin:0;color:#c5d4e4}.badge{border:1px solid #49647f;border-radius:30px;padding:7px 13px;white-space:nowrap;font-size:12px}main{max-width:1360px;margin:28px auto;padding:0 24px}.cards{display:flex;gap:16px;margin-bottom:24px}.card,.panel{border:1px solid #dce3ed;border-radius:12px;background:white;box-shadow:0 3px 10px #172b4305}.card{padding:18px 24px;flex:1}.card small{display:block;color:#64758a}.card strong{font-size:28px;display:block;margin-top:7px}.panel{padding:24px;margin-bottom:20px}.toolbar{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin-bottom:18px}button,.button{border:1px solid #c6d3e2;border-radius:7px;padding:9px 15px;font:inherit;cursor:pointer;background:white;color:#223952;text-decoration:none}button.primary{background:#245bba;color:white;border-color:#245bba}button:hover,.button:hover{filter:brightness(.95)}button:disabled{opacity:.4;cursor:default}input,textarea{font:inherit;border:1px solid #bfcede;border-radius:7px;padding:10px;background:#fcfdff;color:#183049}input:focus,textarea:focus,button:focus-visible,a:focus-visible{outline:3px solid #9bc5ff;outline-offset:2px}.search{flex:1;min-width:180px}label{font-weight:600;font-size:13px}table{width:100%;border-collapse:collapse;text-align:left}th{font-size:11px;color:#64758a;text-transform:uppercase;letter-spacing:.08em}td,th{padding:13px 10px;border-bottom:1px solid #e8edf3}td small,.muted{color:#64758a}.scroll{overflow:auto}.notice{padding:12px 16px;border-left:3px solid #267761;background:#ebf7f1;margin:15px 0;min-height:42px;white-space:pre-wrap}.notice.error{border-color:#b63448;background:#fff0f1;color:#8c2435}.split{display:grid;grid-template-columns:minmax(280px, .85fr) minmax(380px,1.4fr);gap:20px}textarea{width:100%;resize:vertical;font:14px/1.65 Consolas,monospace;tab-size:2}.editor label{display:block;margin:16px 0 8px}.tabs{display:flex;gap:8px;border-bottom:1px solid #dce3ed;margin-bottom:18px;padding-bottom:12px}.tabs button[aria-selected=true]{background:#e7efff;color:#214f9e;border-color:#95b4ea}pre,code{font-family:Consolas,monospace}pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f6fa;padding:14px;border-radius:7px;line-height:1.6}.tree details{margin:6px 0 6px 17px;border-left:1px solid #d8e2ee;padding-left:10px}.tree summary{cursor:pointer;padding:5px}.tree code{font-size:12px;color:#3c649b}.graph{width:100%;min-height:180px}.step{border:1px solid #cad9eb;background:#f1f6ff;padding:18px;border-radius:8px;min-height:130px;white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.7 Consolas,monospace}footer{color:#6c7c90;font-size:12px;padding:12px 2px 25px}a{color:#245bba}[hidden]{display:none!important}@media(max-width:850px){.split{grid-template-columns:1fr}.cards{flex-wrap:wrap}.card{min-width:120px}header{align-items:flex-start;flex-direction:column}.panel{padding:17px}main{padding:0 14px}td,th{padding:10px 5px}}
```

</details>

Create `playground_service/static/app.js`. Attach the highlighter to both editors
and update it after loading samples or a saved session. The rest of the code uses
same-origin HTTP commands: create/restore a session, PUT a program, then POST one
evaluation. The replay buttons only move a cursor through the returned events.

<!-- tutorial-file: playground_service/static/app.js -->
```javascript
"use strict";
const $ = id => document.getElementById(id);
let session = sessionStorage.getItem("lcl-session"), report = null, trace = [], cursor = 0, timer = null;
const editors = [$("source"), $("expression")].map(input => LCLHighlight.attach(input));
function refreshEditors() { editors.forEach(editor => editor.update()); }
const samples = {
  catalog: ["__LCL_VERSION__: 1\n\n# Inputs\nprice: 12\nquantity: 3\n\n# Derived value\ntotal: price * quantity", "total + 5"],
  conditional: ["__LCL_VERSION__: 1\n\n# Branch inputs\nchoose_left: True\nleft: 42\nright: 1 / 0\n\n# Lazy branch selection\nanswer: left if choose_left else right", "answer"],
  error: ["__LCL_VERSION__: 1\n\n# Circular definitions\nfirst: second + 1\nsecond: first + 1", "first"]
};
function message(text, error = false) { $("message").textContent = text; $("message").classList.toggle("error", error); }
async function api(path, method = "GET", data) {
  const response = await fetch("/api/sessions" + path, {method, headers: data ? {"Content-Type": "application/json"} : {}, body: data ? JSON.stringify(data) : undefined});
  const body = await response.json();
  if (!response.ok && !body.error) throw Error(typeof body.detail === "string" ? body.detail : "Request rejected");
  return body;
}
function tab(name) {
  document.querySelectorAll("[data-tab]").forEach(button => { const selected = button.dataset.tab === name; button.setAttribute("aria-selected", selected); $(button.dataset.tab).hidden = !selected; });
}
document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => tab(button.dataset.tab)));
function astNode(node) {
  const details = document.createElement("details"), summary = document.createElement("summary"), code = document.createElement("code");
  details.open = true; summary.textContent = node.kind + " · " + node.span.start.line + ":" + node.span.start.column;
  LCLHighlight.render(code, node.source); details.append(summary, code); node.children.forEach(child => details.append(astNode(child))); return details;
}
function drawGraph(edges) {
  const ns = "http://www.w3.org/2000/svg", svg = document.createElementNS(ns, "svg");
  const sources = [...new Set(edges.map(e => e.source))], targets = [...new Set(edges.map(e => e.target))];
  const height = Math.max(170, Math.max(sources.length, targets.length) * 50 + 30);
  svg.setAttribute("viewBox", `0 0 620 ${height}`); svg.classList.add("graph"); svg.setAttribute("role", "img"); svg.setAttribute("aria-label", "Dependency graph. Complete edges are listed in the following table.");
  edges.forEach(edge => { const y1 = 35 + sources.indexOf(edge.source) * 50, y2 = 35 + targets.indexOf(edge.target) * 50;
    const line = document.createElementNS(ns, "path"); line.setAttribute("d", `M 190 ${y1} C 280 ${y1}, 340 ${y2}, 420 ${y2} m -8 -5 l 8 5 l -8 5`); line.setAttribute("fill", "none"); line.setAttribute("stroke", edge.kind === "eager" ? "#3769b1" : "#b47b36"); if (edge.kind !== "eager") line.setAttribute("stroke-dasharray", "5 3"); svg.append(line); });
  [sources, targets].forEach((names, side) => names.forEach((name, i) => { const text = document.createElementNS(ns, "text"); text.setAttribute("x", side ? "430" : "10"); text.setAttribute("y", 40 + i * 50); text.setAttribute("font-size", "13"); text.setAttribute("fill", "#183049"); text.textContent = name; svg.append(text); }));
  $("graph").replaceChildren(svg);
}
function display(parsed) {
  report = parsed; $("tree").replaceChildren();
  Object.entries({...parsed.ast, "<expression>": parsed.expression_ast}).forEach(([name, node]) => { const label = document.createElement("h3"); label.textContent = name; $("tree").append(label, astNode(node)); });
  $("edges").replaceChildren(); parsed.dependencies.forEach(edge => { const row = document.createElement("tr"); [edge.source, edge.target, edge.kind].forEach(value => { row.insertCell().textContent = value; }); $("edges").append(row); });
  drawGraph(parsed.dependencies); $("evaluate").disabled = false;
}
function stop() { clearInterval(timer); timer = null; $("play").textContent = "Play"; }
function step() {
  $("position").textContent = trace.length ? `${cursor + 1} / ${trace.length}` : "0 / 0";
  $("step").textContent = trace[cursor] || "No events captured."; $("cursor").value = cursor;
  $("previous").disabled = cursor <= 0; $("next").disabled = cursor >= trace.length - 1;
  if (cursor >= trace.length - 1) stop();
}
function evaluation(data) {
  stop(); trace = data.trace || []; cursor = 0;
  $("result").textContent = data.error ? data.error.message : `${data.result.type}: ${data.result.repr}`;
  $("play").disabled = !trace.length; $("cursor").disabled = !trace.length; $("cursor").max = Math.max(0, trace.length - 1); step(); tab("trace");
  message(data.error ? data.error.message : (data.trace_truncated ? "Evaluation complete. Trace display capped at 2,000 events." : "Evaluation complete. Step through the actual native trace."), !!data.error);
}
async function start(fresh = false) {
  try {
    stop(); if (fresh && session) await api("/" + session, "DELETE").catch(() => {});
    let previous = null;
    if (!fresh && session) previous = await api("/" + session).catch(() => null);
    if (!previous) { session = (await api("", "POST")).id; sessionStorage.setItem("lcl-session", session); report = null; $("evaluate").disabled = true; }
    else if (previous.expression_ast) { $("source").value = previous.source; $("expression").value = previous.expression; display(previous); if (previous.evaluation) evaluation(previous.evaluation); }
    refreshEditors(); $("session-status").textContent = "Session · " + session.slice(0, 8); if (fresh) location.reload();
  } catch (error) { message(error.message, true); }
}
$("parse").addEventListener("click", async () => {
  stop(); $("parse").disabled = true; $("evaluate").disabled = true;
  try { const data = await api("/" + session + "/program", "PUT", {source: $("source").value, expression: $("expression").value});
    if (data.error) { message(data.error.message, true); return; }
    display(data); trace = []; step(); tab("ast"); message("Parsed successfully. Evaluate to record values and cache behavior.");
  } catch (error) { message(error.message, true); } finally { $("parse").disabled = false; }
});
$("evaluate").addEventListener("click", async () => { $("evaluate").disabled = true; try { evaluation(await api("/" + session + "/evaluate", "POST")); } catch (error) { message(error.message, true); } finally { $("evaluate").disabled = !report; } });
[$("source"), $("expression")].forEach(input => input.addEventListener("input", () => { $("evaluate").disabled = true; stop(); message("Source changed. Parse again before evaluating."); }));
$("sample").addEventListener("change", () => { [$("source").value, $("expression").value] = samples[$("sample").value]; refreshEditors(); $("evaluate").disabled = true; message("Sample loaded. Parse to start a new Frame."); });
$("new-session").addEventListener("click", () => start(true));
$("previous").addEventListener("click", () => { stop(); cursor--; step(); }); $("next").addEventListener("click", () => { stop(); cursor++; step(); });
$("cursor").addEventListener("input", () => { stop(); cursor = Number($("cursor").value); step(); });
$("play").addEventListener("click", () => { if (timer) { stop(); return; } if (cursor >= trace.length - 1) cursor = -1; $("play").textContent = "Pause"; timer = setInterval(() => { cursor++; step(); }, 650); });
start();
```

Append the home route and asset mount to `playground_service/app.py`:

<!-- tutorial-append: playground_service/app.py -->
<!-- python-doc-fragment -->
```python
@service.get("/", include_in_schema=False)
async def frontend() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static/index.html")


service.mount(
    "/static", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="frontend"
)
```

The explicit home route and `/static` prefix preserve lcl-fastapi's built-in
`/health`, `/docs` and shutdown routes. Static paths follow the installed package.
No independent frontend server or CORS layer is needed.

**Checkpoint:** restart and open `http://127.0.0.1:18086/`. Type and paste LCL,
including an unfinished string; coloring must not alter its text or cursor. Parse
the small calculation and evaluate to 41. Try Conditional dependencies: the graph
contains `right`, but its division by zero is skipped and the result is 42.
Evaluate again to see cache hits; parse again to reset the Frame.

Move the replay slider while watching the browser network panel: it must not POST
evaluation requests. Reload to restore the same session. A duplicated browser tab
may copy sessionStorage; use New session for a separate experiment. Program and
diagnostic text must be rendered as text, never executable HTML.

## 10. Verify session policy and graceful cleanup

Run `lcl-fastapi status -o config service.lclcfg` and `lcl-fastapi logs -o config
service.lclcfg`. There is one API worker; evaluator threads are business resources,
not extra API workers. Inspect `X-Request-ID` with `curl -i` and find the matching
access entry. An expected parse diagnostic differs from an unhandled route error.

After stopping, try one launch override at a time, then restore a normal launch:

| Add to the serve command | Expected behavior |
| --- | --- |
| `-o server.workers "LCL[2]"` | Lifespan rejects the process-local session design. Do not enable hot reload, which itself forces one worker. |
| `-o business.sessions.max_count "LCL[1]"` | A second independent session returns 429; deleting the first frees capacity. |
| `-o business.sessions.idle_seconds "LCL[2]"` | After more than two idle seconds, the next command expires the session and returns 404. |

Create `verify.py`, stop your manual server and run `python verify.py`. The script
starts the installed application in a temporary working directory and checks
assets, syntax/dependencies, skipped branches, cache reuse, isolation, deletion
and native shutdown. It does not depend on a repository checkout.

<details>
<summary>Complete verify.py</summary>

<!-- tutorial-file: verify.py -->
<!-- python-doc-exec -->
```python
"""Verify the installed tutorial application using real native service processes."""

import json
import os
import subprocess
import sysconfig
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psutil

HERE = Path(__file__).resolve().parent
CLI = Path(sysconfig.get_path("scripts")) / (
    "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
)


def http(
    origin: str,
    path: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(origin + path, method=method, data=data, headers=headers or {})
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def json_http(origin: str, path: str, method: str = "GET", data: object = None) -> tuple[int, Any]:
    status, body = http(
        origin,
        path,
        method,
        None if data is None else json.dumps(data).encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(body)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = HERE.joinpath("service.lclcfg").read_text(encoding="utf-8")
        config = root / "service.lclcfg"
        config.write_text(source, encoding="utf-8")
        origin = ORIGIN

        def command(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(CLI), *args, "-o", "config", str(config)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )

        with (root / "console.log").open("w+", encoding="utf-8") as output:
            process = subprocess.Popen(
                [str(CLI), "serve", "-o", "config", str(config)],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, (root / "console.log").read_text(
                        encoding="utf-8"
                    )
                    try:
                        if http(origin, "/health")[0] == 200:
                            break
                    except URLError, TimeoutError:
                        pass
                    assert time.monotonic() < deadline, "Service startup timed out"
                    time.sleep(0.1)
                assert http(origin, "/")[0] == 200
                for asset in [
                    "/static/app.js",
                    "/static/style.css",
                    "/static/lcl-highlight.js",
                    "/static/lcl-highlight.css",
                ]:
                    assert http(origin, asset)[0] == 200
                verify(origin, root)
                status = json.loads(command("status").stdout)
                assert status["service"]["configured_workers"] == 1
            finally:
                if process.poll() is None:
                    owner = psutil.Process(process.pid)
                    try:
                        command("stop")
                    except BaseException:
                        for child in reversed(owner.children(recursive=True)):
                            child.kill()
                        owner.kill()
                        process.wait(timeout=10)
                        raise
                process.wait(timeout=45)
            assert process.returncode == 0, (root / "console.log").read_text(encoding="utf-8")
            assert json.loads(command("status").stdout)["status"] == "STOPPED"
        print("PASS " + HERE.name + ": installed static UI, native APIs, isolation and cleanup")


ORIGIN = "http://127.0.0.1:18086"


def verify(origin: str, root: Path) -> None:
    status, session = json_http(origin, "/api/sessions", "POST")
    assert status == 201
    key = session["id"]
    path = "/api/sessions/" + key
    source = (
        "__LCL_VERSION__: 1\nflag: True\nleft: 42\nright: 1 / 0\nanswer: left if flag else right"
    )
    status, parsed = json_http(
        origin, path + "/program", "PUT", {"source": source, "expression": "answer"}
    )
    assert status == 200 and parsed["ast"]["answer"]["kind"] == "LclConditional"
    assert any(
        edge["target"] == "right" and edge["kind"] == "conditional"
        for edge in parsed["dependencies"]
    )
    status, result = json_http(origin, path + "/evaluate", "POST")
    assert status == 200 and result["result"]["repr"] == "42"
    assert not any("name='right'" in row for row in result["trace"])
    assert any("cached" in row for row in json_http(origin, path + "/evaluate", "POST")[1]["trace"])
    second = json_http(origin, "/api/sessions", "POST")[1]["id"]
    assert json_http(origin, "/api/sessions/" + second + "/evaluate", "POST")[0] == 409
    assert (
        json_http(
            origin,
            path + "/program",
            "PUT",
            {"source": "__LCL_VERSION__: 1\nx: (", "expression": "x"},
        )[0]
        == 422
    )
    assert json_http(origin, path)[1]["source"] == source
    assert json_http(origin, path, "DELETE")[0] == 200
    assert json_http(origin, path)[0] == 404
    assert (
        http(origin, "/api/sessions", "POST", headers={"Origin": "https://other.example"})[0] == 403
    )
    assert not (root / "session.lclcfg").exists()


if __name__ == "__main__":
    main()
```

</details>

Your project is now complete. Keep the printed package, metadata, configuration
and verifier under version control; exclude `.venv`, `run` and `logs`. The guide's
exact files are reconstructed and installed by documentation tests, and downstream
CI also verifies a built framework wheel on Windows and Linux.

## Execution boundary

Replay records a completed evaluation; it is not a paused debugger. Trace display
is capped at 2,000 events and result repr at 8,192 characters. Native evaluation
limits constrain depth, node visits and collection materialization, but are not
hard CPU/memory limits for every Python operation. One evaluator serializes users
and can contend for the GIL. Idle expiry is lazy and all sessions disappear on a
complete service restart. Opaque IDs are not authentication. This trusted local
learning tool is not a hostile-code sandbox; use real isolation and reviewed
authentication before accepting untrusted programs or exposing it to other users.
