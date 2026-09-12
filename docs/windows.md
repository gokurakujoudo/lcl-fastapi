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
Workers use Winloop through Uvicorn's supported `winloop:new_event_loop`
factory. The Windows-only dependency is installed automatically. This avoids
the stalled shared-listener accepts observed with the standard Selector loop
and the shared-socket IOCP errors observed with Proactor in multi-worker tests.
The master only binds and shares the listener; it does not run an event loop.
Real-process tests require idle sampling, crashed-worker replacement, and
graceful shutdown; the composed example requires both workers to serve HTTP.
See Uvicorn's [event-loop integration](https://uvicorn.dev/concepts/event-loop/).
Shared-listener connection assignment follows Windows scheduling; the framework
does not promise round-robin or evenly distributed requests. A lightly loaded
service may send all new connections to one worker while its siblings remain
ready. The composed verifier briefly pauses the observed worker under a bounded
connection burst, requires a sibling response, resumes it in `finally`, and
then validates every response. This checks both workers without assuming fair
assignment by the operating system.
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

## Development reload

Use `serve -o config service.lclcfg -o hot_reload` with
`server.reload_dirs: ["./src", "../shared"]` to select recursive Python roots.
Windows retires a reload worker through an identity-specific marker and its
existing graceful Server exit bridge, retaining console-free operation.
Hot reload forces one worker and warns when the configured count is higher.
Native automatic reload is disabled. See [the shared contract](runtime.md#development-hot-reload)
for errors, cleanup, and configuration boundaries.
