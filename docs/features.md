# Implemented features and verification

The current implementation provides the following capabilities. The verification
column identifies the acceptance evidence that must remain green; it does not
claim a successful release or validation on an operating system without a run.

| Capability | Contract and evidence |
| --- | --- |
| LCL configuration and typed application | Native Router/lifespan composition; application behavior tests. |
| Request context and logs | Server-generated lclang Snowflake IDs, response headers, isolated context, and worker-specific upstream logging. Upstream clock/sequence failures return 503 without an ID and record one failure access log. |
| Windows and Linux service runtimes | Uvicorn and Gunicorn respectively; real-process tests run separately on each platform. |
| Local operational CLI | Status, observed active log paths, identity validation, token-authenticated graceful stop. |
| Health and offline API documentation | Nonblocking sampled system metrics and bundled Swagger UI resources. |
| Deployment configuration rendering | Nginx and systemd text output; deterministic renderer and executable guide tests. |

Windows process lifecycle checks have been exercised locally during development.
Windows and Linux CI exercise the process lifecycle independently. Review the
[latest development PR checks](https://github.com/gokurakujoudo/lcl-fastapi/pull/2/checks)
for the exact tested revision and artifacts. The authoritative full gate and
aggregate 100% production branch coverage remain acceptance conditions, not
assumptions.

The independently authored [minimal](../examples/minimal/README.md) and
[composed](../examples/composed/README.md) downstream projects have passed local
Windows checks against an installed wheel. Four GitHub Actions jobs validate
both projects on Windows/Uvicorn and Linux/Gunicorn in isolated environments.
Their full API/CLI and cleanup checks must pass on the final PR head. Both
projects are excluded from the wheel.

The framework does not implement authentication systems, a management service,
HTTP log streaming, configuration watching, hot restart, or deployment commands.
