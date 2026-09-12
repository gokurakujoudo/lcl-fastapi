# lcl-fastapi

<p align="center">
  <img src="assets/logo.png" alt="lcl-fastapi logo" width="320">
</p>

**Business routes in. A managed FastAPI service out.**

A lightweight Python framework combining FastAPI with LCL configuration,
contextual logging, and Snowflake request IDs. Supply your routes, a trusted
`.lclcfg` file, and an optional async lifespan; the framework owns worker
startup, health sampling, local operations, and bundled offline Swagger UI.

## Start here

Follow [Build a service: catalog, directory monitor and LCL playground](build-a-service.md) to go
from an empty directory to a validated API with logs, HTTP checks, and deployment
configuration.

Install in a Python 3.14 virtual environment:

```console
python -m pip install lcl-fastapi==0.3.0
```

Follow the [quick start](https://github.com/gokurakujoudo/lcl-fastapi#start-a-service)
to run your first route, inspect `/health`, and open `/docs`. Then explore the
independent [minimal service](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/minimal)
or [composed application](https://github.com/gokurakujoudo/lcl-fastapi/tree/main/examples/composed)
with Routers, business configuration, and lifespan resources.

## Build and operate

| Your next step | Read |
| --- | --- |
| Compose routes and own startup resources | [Application and lifespan](application.md) |
| Run managed work outside the API loop | [Background workers](background-workers.md) |
| Configure the service and public origin | [Configuration](configuration.md) |
| Trace requests through business logs | [Request logging](logging.md) |
| Inspect service and worker observations | [Health](health.md) and [CLI](cli.md) |
| Run native workers | [Windows](windows.md) or [Linux](linux.md) |
| Prepare deployment files | [Nginx](nginx.md) and [systemd](systemd.md) |
| Understand cleanup and state | [Runtime ownership](runtime.md) |

## Know the boundaries

The initial validation matrix is CPython 3.14 on Windows and Linux. Deployment
supports one service per machine. Windows uses Uvicorn with Winloop; Linux uses
Gunicorn. Configuration is trusted and requires a complete restart for consistent
changes. Python hot reload with configurable watch directories is available for development.
Authentication and automatic deployment are outside the
framework's scope. See the [implemented features](features.md) for evidence and
limitations.

## Follow the project

[Source and issues](https://github.com/gokurakujoudo/lcl-fastapi) ·
[PyPI](https://pypi.org/project/lcl-fastapi/) ·
[Changelog](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/CHANGELOG.md) ·
[Wiki](https://github.com/gokurakujoudo/lcl-fastapi/wiki)

These pages are built from the repository's canonical `docs/` Markdown.
Use **Edit on GitHub** to view a page's source and propose improvements.
Contributors can start with [engineering](engineering.md) and the
[development plan](development-plan.md).
