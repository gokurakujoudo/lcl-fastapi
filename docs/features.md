# Implemented features and verification

The current implementation provides the following capabilities. The verification
column identifies the acceptance evidence that must remain green; it does not
claim a successful release or validation on an operating system without a run.

| Capability | Contract and evidence |
| --- | --- |
| Managed background workers | Code-only callables on dedicated threads/loops, service bridges, restart/shutdown, dedicated logs and controller events; unit and native-process tests. |
| Uncaught HTTP exceptions | Typed custom callbacks, default detailed argument tracebacks and generic 500 fallback; response, precedence, streaming and diagnostic-failure tests. |
| Scoped configuration Frames | `use_lcl_frame()` derives and owns nested task-local Frames; behavioral tests verify inheritance, restoration, concurrency and cancellation. |
| LCL configuration and typed application | Native using inheritance with portable bundled defaults and lazy CLI precedence; Router/lifespan composition; application behavior tests. |
| Request context and logs | Server-generated lclang Snowflake IDs, response headers, isolated context, and separate controller/worker upstream logging with lifecycle observations. Upstream clock/sequence failures return 503 without an ID and record one failure access log. |
| Windows and Linux service runtimes | Uvicorn with Winloop and Gunicorn respectively; real-process tests run separately on each platform. |
| Development Python reload | `serve -o hot_reload`, explicit recursive `server.reload_dirs`, one effective worker, graceful native replacement, and cross-platform process tests. |
| Local operational CLI | Downstream pyproject entrances, native CLI configuration overrides and dry-run; fixed JSON status and observed active log paths, identity validation, token-authenticated graceful stop. |
| Health and offline API documentation | Nonblocking sampled system metrics and bundled Swagger UI resources. |
| Deployment configuration rendering | Nginx and systemd text output; deterministic renderer and executable guide tests. |

Windows and Linux CI exercise the process lifecycle independently. The initial
release's [implementation PR checks](https://github.com/gokurakujoudo/lcl-fastapi/pull/2/checks)
retain its acceptance evidence; review the
[quality workflow](https://github.com/gokurakujoudo/lcl-fastapi/actions/workflows/quality.yml)
for later revisions. The [acceptance plan](development-plan.md) maps each area
to its maintained reference contract. The authoritative full gate and aggregate
100% production branch coverage remain requirements for every completed change.

The independently authored [minimal](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/minimal/README.md) and
[composed](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/composed/README.md) downstream projects have passed local
Windows checks against an installed wheel. Four GitHub Actions jobs validate
both projects on Windows/Uvicorn and Linux/Gunicorn in isolated environments.
Their full API/CLI and cleanup checks must pass on the final PR head. Both
projects are excluded from the wheel.

The framework does not implement authentication systems, a management service,
HTTP log streaming, configuration watching, or deployment commands.

The [from-scratch catalog guide](build-a-service.md) teaches an independent
read-only service. Its documentation test extracts the exact application,
configuration, and verifier from Markdown, runs native workers, checks HTTP
behavior and request logs, stops gracefully, and verifies deployment rendering.
