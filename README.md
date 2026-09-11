# lcl-fastapi

[Documentation](https://gokurakujoudo.github.io/lcl-fastapi/) ·
[Wiki](https://github.com/gokurakujoudo/lcl-fastapi/wiki) ·
[PyPI](https://pypi.org/project/lcl-fastapi/) ·
[Changelog](CHANGELOG.md)

`lcl-fastapi` is a small Python 3.14+ service framework combining FastAPI with
`lclang==1.0.10` configuration, logging, CLI infrastructure, and Snowflake IDs.
Write business routes, a trusted `.lclcfg` file, and an optional business lifespan.
The framework owns worker startup, request IDs, health sampling, local operations,
and bundled offline Swagger UI.

Install the first release with `python -m pip install lcl-fastapi==0.1.0` in a
Python 3.14 virtual environment. See the [release process](docs/releasing.md)
for version notes, publication requirements, and artifact verification.

## Start a service

Use a Python 3.14+ virtual environment. Install the built artifact with
`python -m pip install path/to/lcl_fastapi-0.1.0-py3-none-any.whl`. Dependencies must
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
lcl-fastapi status -o config service.lclcfg -o json
lcl-fastapi logs -o config service.lclcfg
lcl-fastapi stop -o config service.lclcfg
```

The CLI deliberately follows `lclang.cli` syntax: `-o config`, not `-c` or
`--config`; `-o json`, not `--json`. Listener settings have no CLI overrides.
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
changes. There is no file watcher, hot restart, snapshot distribution, or restart
API.

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
[minimal service](examples/minimal/README.md) and
[composed Router, configuration, and lifespan](examples/composed/README.md).
GitHub Actions builds the current source wheel, then installs that artifact and
each example in a separate environment on Windows/Uvicorn and Linux/Gunicorn.
Each run checks service availability, business and built-in HTTP APIs, all CLI
commands, and graceful shutdown. The example projects are excluded from the wheel.

- [Application and lifespan](docs/application.md)
- [Configuration](docs/configuration.md)
- [Request logging](docs/logging.md)
- [Health and observations](docs/health.md)
- [CLI reference](docs/cli.md)
- [Windows runtime](docs/windows.md) and [Linux runtime](docs/linux.md)
- [Nginx rendering](docs/nginx.md) and [systemd rendering](docs/systemd.md)
- [Runtime ownership and state](docs/runtime.md)
- [Development plan and acceptance requirements](docs/development-plan.md)
- [Implemented feature inventory](docs/features.md) and [engineering checks](docs/engineering.md)

The Python package is MIT-licensed. Bundled Swagger UI retains its Apache 2.0
license and third-party notices; see
[asset provenance](src/lcl_fastapi/static/swagger/NOTICE.md).
