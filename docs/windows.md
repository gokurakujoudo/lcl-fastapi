# Windows service runtime

Use CPython 3.14 and install the package in the downstream application's virtual
environment. Start the foreground service with its `.lclcfg` file:

```console
lcl-fastapi serve -o config service.lclcfg
```

`server.host` accepts `127.0.0.1` or `0.0.0.0`; the default is loopback. Set
`server.workers` in the configuration. The runtime uses Uvicorn's native
multiprocess manager even for one worker, giving the service one consistent
master identity and shutdown path. Uvicorn handles crashed-worker replacement.
Workers use Python's native `ProactorEventLoop` through Uvicorn's loop-factory
option. IOCP accepts let idle workers continue sampling without HTTP traffic.
The master only binds and shares the listener; it does not attach that socket
to an event loop. The real-process tests cover this arrangement with multiple
workers, replacement, and graceful shutdown. See the upstream
[loop-factory interface](https://uvicorn.dev/concepts/event-loop/) and Python's
[Windows event loops](https://docs.python.org/3.14/library/asyncio-platforms.html).
If worker configuration, application import, or lifespan initialization fails,
the service stops, preserves the original error diagnostic on stderr, cleans
its runtime files, and the console command returns a nonzero exit status. A
normal accepted shutdown remains a successful exit, including shortly after
startup.

The master records its actual PID, which can differ from the Windows virtual
environment's Python launcher PID. Each worker reads configuration independently
and owns its application lifespan and log sink.

The runtime supports starting without an attached console. The authenticated
shutdown endpoint publishes a marker belonging to the current service start.
A lightweight bridge wakes Uvicorn's normal manager exit event. Each worker
identifies Uvicorn 0.52's Server through its installed SIGTERM bound handler,
checks the instance type, and sets that server's public `should_exit` flag. An
unexpected handler fails startup explicitly. Uvicorn
drains requests and exits application lifespans before the master joins workers.
This does not use Windows `os.kill(pid, SIGTERM)`, which forcibly terminates a
process, or require a visible terminal or console control group.

The native `whoami` and `icacls` commands restrict the shutdown token to the
current service-account SID. If either permission operation fails, startup
fails instead of exposing a usable token with inherited permissions.

The integration test starts a real two-worker service using
`subprocess.CREATE_NO_WINDOW`, exercises a worker crash and replacement,
checks server-issued request IDs and token rejection, changes the configured
port before stop, and verifies business shutdown logs and cleanup. Run it from
the repository development environment:

```console
venv\Scripts\python.exe -m pytest tests/test_runtime_process.py
```

See [runtime ownership and observations](runtime.md) for state consistency,
configuration changes, graceful deadlines, and the route-override warning.
