# Minimal installed service

This independent downstream project uses the public `lcl-fastapi` API. It adds
one `/hello` route and logs business startup and teardown through a lifespan.
Two workers exercise the platform's actual process manager.
It needs Python 3.14 and an available local port 18081. Run only one example
service at a time, as the framework supports one service per machine.

## Install

Run these PowerShell commands from this directory after the framework wheel has
been built in the repository's `dist` directory:

```powershell
py -3.14 -m venv .venv
.venv\Scripts\python.exe -m pip install ..\..\dist\lcl_fastapi-0.2.0-py3-none-any.whl
.venv\Scripts\python.exe -m pip install .
.venv\Scripts\Activate.ps1
```

The second installation builds and installs this downstream package. Neither
installation is editable. Dependencies require package-index access or a local
wheel cache. The framework's development version is installed from its built
wheel; these instructions do not assume it is published on PyPI.

On Linux, create the environment with `python3.14 -m venv .venv`, use
`.venv/bin/python` for both installations, change the wheel path separators to
`/`, and activate with `source .venv/bin/activate`. The recorded validation below
was performed on Windows; Linux validation is separate.

## Run and inspect

With the environment active, keep the foreground server in the first terminal:

```console
minimal-service serve -o config service.lclcfg
```

Open [hello](http://127.0.0.1:18081/hello),
[health](http://127.0.0.1:18081/health), or
[Swagger UI](http://127.0.0.1:18081/docs). The greeting is
`{"message":"hello"}`. Every response has a new `X-Request-ID`, even when the
request supplies its own ID. The hello log includes that generated ID.

In a second terminal, activate the same environment and run from this directory:

```console
minimal-service status -o config service.lclcfg
minimal-service logs -o config service.lclcfg
minimal-service stop -o config service.lclcfg
```

Status reports the live service and workers. Logs prints each worker's actual
absolute log-file path; observations refresh asynchronously. Open those files to
see `minimal business startup`, request messages, and, after `stop` completes,
`minimal business shutdown`. Stop waits for business teardown and log flushing.

The configuration owns the listener and application target. Its relative `logs`
and default `run` directories resolve beside the configuration file. Change
configuration only while the service is stopped.

The separate `render.lclcfg` contains illustrative deployment settings, including
nonexistent certificate and Linux installation paths. Preview these without
deploying anything:

```console
lcl-fastapi nginx render -o config render.lclcfg
lcl-fastapi systemd render -o config render.lclcfg
```

These commands also accept `-o output filename` to write a selected file.

## Verify the installed application

Stop any manually started service first, then run:

```powershell
.venv\Scripts\python.exe verify.py
```

The standard-library check copies the unchanged configuration into a fresh
system `TemporaryDirectory`, clears `PYTHONPATH`, and runs the installed
`lcl-fastapi` command from that directory. The service therefore imports the
installed downstream package and framework. It checks the greeting, unique
server-generated request IDs, health and actual master/worker identities,
Swagger's local assets and OpenAPI, missing/wrong shutdown-token rejection,
JSON and plain status/log paths, graceful process exit, and flushed business
teardown in both worker logs. It checks Nginx/systemd stdout and file rendering
using the separate static deployment configuration. It removes
the isolated runtime files when complete. No external service or credentials
are used. The environment must permit system temporary files, child processes,
and loopback HTTP.

Run the built `lcl-fastapi==0.2.0` wheel in a clean independent environment on
Windows or Linux with CPython 3.14. Expected final output:

```text
PASS: nginx/systemd render to stdout and files
PASS: hello, Request-ID, health, docs, shutdown authorization, status, logs, stop, two-worker lifespan flush
```

The application was authored from the public README and application,
configuration, CLI, health, and logging references, without reading framework
implementation or test modules. This example is a downstream project and is
not part of the framework wheel.

The installed `minimal-service` command is declared in `pyproject.toml` and wraps
`lcl_fastapi.cli.run_cli`. Its default configuration is `service.lclcfg` in the
calling directory, so `minimal-service serve` is sufficient. Explicit `-c` or
`-o config` selects another file. Native overrides such as `-o server.port
"LCL[18082]"` take precedence over that file. The service extends the bundled
universal configuration with native `using` and separates controller and worker
logs under `./logs`.
