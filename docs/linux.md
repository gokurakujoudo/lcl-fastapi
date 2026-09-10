# Linux service runtime

Linux uses Gunicorn 26's native `asgi` worker and foreground arbiter. The
framework configures Gunicorn directly from `.lclcfg`; it does not invoke a
second command-line parser or use the deprecated external Uvicorn worker.
See the [upstream native ASGI documentation](https://gunicorn.org/asgi/).

```console
lcl-fastapi serve -o config service.lclcfg
```

The current process becomes the Gunicorn master, whose PID and operating-system
creation time are recorded before workers are forked. Application preload is
disabled. Each worker loads its application and fresh configuration, then owns
its Frame, logging, Snowflake generator, and FastAPI lifespan.

Gunicorn receives the configured IPv4 bind address, port, worker count, backlog,
keep-alive timeout, and graceful timeout. Native ASGI lifespan support is
required; its event loop is asyncio. Duplicate Gunicorn access logging is
disabled because the application emits request-correlated access records.

An authenticated shutdown response is sent before signaling the verified
Gunicorn master with SIGTERM. Gunicorn performs its native graceful shutdown
and worker joining. The CLI waits for that exact master identity to exit.
Worker replacement does not alter the master listener settings.

The control-token file uses mode `0600`. State and lease coordination use kernel
file locks, which release if a process crashes. Keep the service state directory
owned by and accessible only to the intended service account.

The shared real-process integration test runs on Linux in CI and verifies the
Gunicorn master identity, worker replacement, token rejection, and lifecycle
cleanup. Run the same test locally with:

```console
venv/bin/python -m pytest tests/test_runtime_process.py
```

Use generated systemd and Nginx configuration as deployment input. Rendering
does not install files, reload Nginx, or call systemctl. Review the generated
configuration under your deployment policy.

See [runtime ownership and observations](runtime.md) for state consistency,
configuration changes, deadlines, and predefined-route override consequences.
