# Development plan and acceptance

The reviewed requirements are maintained in the existing reference pages below.
This plan records how to change and verify the project; it is not a second API
specification or a claim that an unrun check passed. [AGENTS.md](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/AGENTS.md)
owns engineering and delivery policy, and [the feature inventory](features.md)
describes implemented capability.

## Scope and responsibility

Deliver a Python 3.14+ MIT distribution named `lcl-fastapi`, imported as
`lcl_fastapi`. Use the dependency versions in [pyproject.toml](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/pyproject.toml)
and the native integrations described in [engineering](engineering.md).
Downstream applications supply business routes, trusted LCL configuration, and
an optional async lifespan. The framework supplies application assembly,
request context, worker resources, local operations, and deployment rendering.

The supported deployment is one service per machine on Windows or Linux.
Authentication systems, a separate management service or port, HTTP log APIs or
streams, configuration watching/reload/snapshots, and automatic
deployment are outside the first-version scope. Snowflake generation and native
worker supervision remain upstream responsibilities.

## Contract ownership and acceptance

| Area | Maintained contract | Required evidence |
| --- | --- | --- |
| Background workers | [Background workers](background-workers.md) | Code-only registration, one API worker, independent Frames/loops, resource bridges, cancellation-cleanup barriers before restart/teardown, runtime journal fault supervision, independent retirement deadlines and native reload. |
| Uncaught HTTP errors | [Application](application.md#uncaught-http-exceptions), [logging](logging.md#uncaught-exception-diagnostics) | Original traceback arguments, callback precedence/fallback including native generic handlers in debug mode, response IDs, streaming and cancellation. |
| Scoped configuration | [Application](application.md#scoped-lcl-frames) | Derived Frame ownership, nested lookup restoration, concurrency isolation, cancellation and parent caching. |
| Application and resource scopes | [Application](application.md) | Native decorators and Routers, business configuration and lifespan, startup failure/cancellation cleanup, and import without runtime side effects. |
| Route precedence and origin | [Application](application.md), [configuration](configuration.md) | Exact method/path business overrides work both directly and through Routers. Origin validation does not change routes, ASGI root paths, or listener ports. |
| LCL configuration | [Configuration](configuration.md) | Native using with bundled defaults, command-line precedence through worker replacement, config-relative paths, framework-owned worker PID, supported IPv4 binds, and LCL environment expressions for non-overlapping machine ID ranges. |
| Request IDs and logging | [Logging](logging.md) | Real upstream Snowflake IDs reach context, request state, headers, and business logs. Concurrent requests remain isolated and logs preserve business call sites. |
| ID-generation failures | [Logging](logging.md) | Controlled upstream clock rollback and exhaustion return 503 without business dispatch or a fabricated ID, produce one failure access record, and restore context after send failures/cancellation. |
| Worker coordination | [Runtime](runtime.md), [Windows](windows.md), [Linux](linux.md) | Unique concurrent worker leases, PID/creation-time and service-start identity checks, atomic observations, crash recovery, and owned-resource cleanup. |
| Development reload | [CLI](cli.md), [configuration](configuration.md), [runtime](runtime.md) | Exact Python watch roots on both platforms, one effective worker and warning, repeated graceful replacement, failure exits, and stop during reload. |
| Configuration changes | [Configuration](configuration.md), [runtime](runtime.md) | New workers read current files; existing workers and master listener settings do not reload. Local stop uses recorded identity/listener data after later configuration edits. |
| Health and observations | [Health](health.md) | Service/worker/Gunicorn identities, sampled CPU/memory/disk metrics, partial unavailable fields, idle publication, and cancellation that waits for in-flight writes. |
| Active log files | [Logging](logging.md), [CLI](cli.md) | Controller and worker files are isolated; lifecycle/heartbeat/rotation events are recorded without logger threads across forks. Live-worker paths come from upstream sink metrics, update after rollover, and expose documented observation age/staleness without treating dead workers as active. |
| CLI and shutdown | [CLI](cli.md), [runtime](runtime.md) | All six operations use lclang syntax, preserve native options and command-line override precedence, keep diagnostics off structured stdout, reject wrong control tokens, and complete graceful whole-service shutdown. |
| Offline Swagger | [Application](application.md) | HTML, OpenAPI, JavaScript, CSS, favicon, and request execution use local resources; shutdown is absent from OpenAPI; disabled docs register no framework documentation resources. |
| Deployment rendering | [Nginx](nginx.md), [systemd](systemd.md) | Correct independently configured listener/TLS fields, loopback upstream, shutdown blocking, quoted values, and no installation or system-command side effects. |
| Packaging and documentation | [Engineering](engineering.md), [release procedure](releasing.md) | Executable canonical examples, valid links, accurate metadata/license/typing/resources, and source/wheel contents without secrets, environments, reports, or downstream projects in the wheel. |

Behavioral test ownership follows these subsystems in [tests](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/tests).
Requirements establish the acceptance target; the latest revision's actual
test reports establish compliance.

## Development sequence

1. Reconcile public behavior and compatibility in the relevant reference pages.
   Keep upstream CLI, Frame, logger, active-file metrics, and Snowflake contracts
   explicit. Resolve ambiguity before changing an externally visible contract.
2. Maintain packaging and the authoritative quality gate. Keep production code
   small, typed, documented, and layered according to the root policy.
3. Implement resource ownership before connecting adapters. Each actual worker
   owns its Frame, logger, lease/generator, sampler, and business lifespan.
   Failure at any acquisition stage must release already-owned resources;
   business teardown precedes framework shutdown and logger flush.
4. Connect native platform runtimes, the lclang CLI, and pure deployment
   renderers. The CLI's async scope and event loop close before the platform
   process manager starts. Do not introduce another supervisor or parser.
5. Exercise public workflows through the independent downstream projects,
   correct documentation gaps, and execute the exact marked Markdown examples.
6. Review implementation, tests, documentation, and artifact contents together.
   Run affected checks while editing and the complete required gate before
   delivery. Follow the user's current delegation instructions; agent assignment
   is not part of the product contract.

## Downstream and platform acceptance

The [minimal](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/minimal/README.md) and
[composed](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/composed/README.md) projects are independent downstream
applications. The first teaches a small business route and configuration; the
second composes Router prefixes, business configuration, lifespan, and logging.
Their instructions must remain sufficient without private implementation
knowledge, external accounts, production credentials, or live business services.

CI first builds the current source wheel in isolation. Each example is then
installed with that exact wheel in its own environment on Windows/Uvicorn and
Linux/Gunicorn, producing four independent jobs.
The example and wheel are installed in one dependency-resolution transaction;
incompatible example pins fail instead of replacing the selected wheel.
Source imports through `PYTHONPATH` do not replace package installation. Every job checks availability,
business and built-in HTTP APIs, all CLI commands, native worker operation,
and graceful cleanup, retaining commands and failure diagnostics. Neither
example project may enter the framework wheel.

Real-process tests run on their corresponding operating systems. Windows
checks cover operation without an attached console, idle sampling, both
workers serving requests, crash replacement, and shutdown. Linux checks also
verify the Gunicorn master identity and parent resource ownership after worker
retirement. Combine both platforms' coverage to require 100% production
statements and branches without excluding platform modules.

## Review and publication

Deliver authorized changes through an issue, feature branch, and reviewed PR.
All applicable checks must pass on the exact latest head, including push and PR
quality, both native platforms, all four downstream examples, artifact checks,
and strict aggregate coverage. Record actual revisions and results rather than
equating configured checks with successful execution.

When merge is authorized, squash with an expected-head guard and verify the
merged content. Publication is a separately authorized operation following
[the release procedure](releasing.md): select the verified merged source,
advance the persistent release branch, publish its tested artifacts to PyPI,
then publish the matching tag and GitHub Release. A failed final stage retains
the original published source and artifact bytes during recovery.

## Tutorial-series acceptance

The [Build a service series](build-a-service.md) has one ordered chapter index.
Discovery tests match its published chapter files and preserve executable catalog
source. The directory-monitor example validates recursive snapshots, upload/download
boundaries, SSE delivery/coalescing and cleanup. The playground validates native
AST spans, dependency classes, real trace/cache behavior, session isolation, expiry
and cleanup. Both package static UIs and run installed-wheel native-process
verifiers on Windows/Linux; browser interaction checks supplement API evidence.
