# lcl-fastapi

<p align="center">
  <img src="https://raw.githubusercontent.com/gokurakujoudo/lcl-fastapi/main/docs/assets/logo.png" alt="lcl-fastapi logo" width="320">
</p>

[Homepage](https://gokurakujoudo.github.io/lcl-fastapi/) ·
[Documentation](https://gokurakujoudo.github.io/lcl-fastapi/) ·
[Wiki](https://github.com/gokurakujoudo/lcl-fastapi/wiki) ·
[PyPI](https://pypi.org/project/lcl-fastapi/) ·
[Changelog](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/CHANGELOG.md)

`lcl-fastapi` is a small Python 3.14+ service framework combining FastAPI with
`lclang==1.0.10` configuration, logging, CLI infrastructure, and Snowflake IDs.
Write business routes, a trusted `.lclcfg` file, and an optional business lifespan.
The framework owns worker startup, request IDs, health sampling, local operations,
and bundled offline Swagger UI.

Development source also provides `use_lcl_frame()` for nested configuration scopes
and `uncaught_exception_handler` for custom HTTP error responses with detailed
default traceback logging. See the application reference and composed example;
this addition is not part of the published 0.2.0 artifact.

Install version 0.2.0 with `python -m pip install lcl-fastapi==0.2.0` in a
Python 3.14 virtual environment. See the [release process](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/releasing.md)
for version notes, publication requirements, and artifact verification.

## Start a service

New to the framework? Follow [Build a product catalog service from scratch](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/build-a-service.md)
for a complete step-by-step project with configuration, routes, logging, verification,
and deployment preparation.

Use a Python 3.14+ virtual environment. Install the built artifact with
`python -m pip install path/to/lcl_fastapi-0.2.0-py3-none-any.whl`. Dependencies must
also be installed; offline Swagger means that documentation serving does not
require a CDN after installation.

Save this application as `app.py` in your downstream project:

<!-- python-doc-exec -->
```python
from lcl_fastapi import LclFastAPI, get_logger

service = LclFastAPI()


@service.get("/hello")
async def hello() -> dict[str, str]:
    logger = await get_logger(__name__)
    logger.info("hello requested")
    return {"message": "hello"}
```

Save `service.lclcfg` alongside it:

```text
__LCL_VERSION__: 1
app.name: "example-service"
app.version: "1.0.0"
app.target: "app:service"
server.host: "127.0.0.1"
server.port: 8080
server.workers: 1
logger.file.default.directory: "./logs"
logger.file.controller.filename: f"{app.name}.controller.log"
logger.file.service.filename: f"{app.name}.{worker_pid}.log"
logger.level: "INFO"
```

From that project directory, run:

```console
lcl-fastapi serve -o config service.lclcfg
```

Open `http://127.0.0.1:8080/hello`, `/health`, or `/docs` in a browser. Swagger's
JavaScript, stylesheet, favicon, and OpenAPI schema are served locally. Its remote
validator is disabled. Responses normally carry a newly generated
`X-Request-ID`; incoming client IDs do not replace it. If the upstream generator
cannot issue an ID because of clock rollback or exhaustion, the response is 503
without a fabricated ID and the access log marks the failure. Business logs automatically
include the current ID, and each worker writes its own file.

Run operations in another terminal using the same configuration file:

```console
lcl-fastapi status -o config service.lclcfg
lcl-fastapi logs -o config service.lclcfg
lcl-fastapi stop -o config service.lclcfg
```

The CLI follows `lclang.cli` syntax: `-c`/`--config` and the existing `-o config`
select the service file. Any setting can be overridden, for example
`-o server.port "LCL[9000]"`; CLI values take precedence over the file.
`status` and `logs` always return JSON. Add your own `pyproject.toml` console command
with `catalog = "lcl_fastapi.cli:run_cli"`, or wrap `run_cli` to supply a default
configuration. See the [CLI reference](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/cli.md#downstream-console-entrances).
Configuration supports native `using`, including `using f"{lcl_fastapi_defaults}"`
to extend the bundled defaults. Controller lifecycle events and worker request
logs use separate filename patterns under the shared log directory.
Log paths are the most recently published live-worker observations, so rollover
updates are eventually consistent. Stop uses a local control token and waits for
graceful termination, including business teardown and logger flush.

## Application and deployment boundaries

Windows uses Uvicorn with automatically installed Winloop; Linux uses Gunicorn
to manage ASGI workers. You run the same
`lcl-fastapi serve` command on both. Only `127.0.0.1` and `0.0.0.0` are accepted
listener addresses; the default is loopback. Nginx can provide external HTTPS
and port forwarding, and systemd is optional. The render commands produce files
without installing them or running deployment commands.

Business Routers own their prefixes. There is no `api.prefix` setting.
`server.root_path` is optional external origin metadata such as
`https://api.example.com:8443`; it does not add a route prefix or set ASGI
`root_path`. Nginx's actual listener/TLS fields remain independent because a
public origin may describe an upstream proxy or port mapping.

The supported deployment scope is one service per machine. Configure separate
machines' Snowflake worker-ID ranges through LCL's environment-variable support.
Concurrent worker leases do not promise unlimited historical ID uniqueness
across crashes or rapid reuse. Do not edit configuration while the service is
running: a replacement worker reads the changed file while older workers retain
their original values. Use a complete externally managed restart for consistent
changes. Development Python reload is available with `serve -o config service.lclcfg
-o hot_reload`; configure one or more `server.reload_dirs` in the file. Reload forces
one worker and warns if the configured count is higher. See the
[runtime contract](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/runtime.md#development-hot-reload). There is no configuration
watching, snapshot distribution, or restart API.

User routes with the same HTTP method and exact path override built-in routes.
Overriding health or documentation replaces those defaults; overriding
`POST /_lcl/shutdown` can prevent the local stop command from working. The default
shutdown route is absent from OpenAPI and blocked by generated Nginx configuration.
There is no business authentication, management port, HTTP log endpoint, or log
streaming feature.

## Documentation

The reference pages below own the public contracts. The development plan maps
those contracts to acceptance requirements; AGENTS.md governs engineering policy.

Two independent downstream projects demonstrate the installed public package:
[minimal service](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/minimal/README.md) and
[composed Router, configuration, and lifespan](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/examples/composed/README.md).
GitHub Actions builds the current source wheel, then installs that artifact and
each example in a separate environment on Windows/Uvicorn and Linux/Gunicorn.
Each run checks service availability, business and built-in HTTP APIs, all CLI
commands, and graceful shutdown. The example projects are excluded from the wheel.

- [Application and lifespan](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/application.md)
- [Configuration](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/configuration.md)
- [Request logging](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/logging.md)
- [Health and observations](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/health.md)
- [CLI reference](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/cli.md)
- [Windows runtime](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/windows.md) and [Linux runtime](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/linux.md)
- [Nginx rendering](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/nginx.md) and [systemd rendering](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/systemd.md)
- [Runtime ownership and state](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/runtime.md)
- [Development plan and acceptance requirements](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/development-plan.md)
- [Implemented feature inventory](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/features.md) and [engineering checks](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/docs/engineering.md)

The Python package is MIT-licensed. Bundled Swagger UI retains its Apache 2.0
license and third-party notices; see
[asset provenance](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/src/lcl_fastapi/static/swagger/NOTICE.md).
