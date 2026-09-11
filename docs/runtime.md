# Local runtime and process ownership

`lcl-fastapi serve -o config service.lclcfg` owns one foreground service. The
configuration file is trusted application configuration. Keep its state directory
private to the service account and dedicate it to this service. The framework
coordinates one service on one machine; it does not coordinate separate services
or machines.

On [Windows](windows.md), Uvicorn owns worker creation and recovery. On
[Linux](linux.md), Gunicorn's native ASGI worker and arbiter own them. A worker
loads the current configuration and creates its own Frame, logger, Snowflake
generator, and application lifespan. The framework does not add a supervisor.
The configuration file's directory is available on each worker's Python import
path, so an adjacent `app.py` can be the target `app:service` even when the
installed console command is invoked from a different working directory.

The master reads listener and worker-management settings once. Replacement
workers read current source files. Do not edit configuration while a service is
running unless you understand that existing and replacement workers can differ.
Apply coordinated changes by stopping the entire service and starting it again.

`server.root_path` is optional complete external-origin metadata, such as
`https://api.example.com:8443`. It is never an ASGI path prefix. Business paths
come from Router registration; the ASGI root path remains empty.

## Local observations

`runtime.state_dir/runtime.json` records the actual master PID, its operating
system creation time, a fresh complete-start identifier, launch-time listener,
worker count, observation interval, and graceful deadline. `runtime.pid_file`
contains that master's PID. Windows virtual-environment launchers can introduce
an additional parent process; their PID is not the Uvicorn master PID.

Each worker atomically replaces `runtime.worker_state_dir/<pid>.json`. Its
observation includes PID, process creation time, complete-start identifier,
Snowflake worker number, actual lclang log paths, and observation time. The
local commands verify both PID and creation time and require the current start's
identifier. A reused PID or a previous start's state cannot become a live worker.

Runtime JSON, PID files, control tokens, and worker-ID leases coordinate the
current process lifecycle; they are not persistent business data. Leases assign
distinct IDs in the configured range to live concurrent workers and reclaim an
exited worker's ID only after validating its process identity. This coordination
does not extend upstream Snowflake guarantees across crashes or rapid ID reuse.

```console
lcl-fastapi status -o config service.lclcfg -o json
lcl-fastapi logs -o config service.lclcfg -o json
```

Status reports `RUNNING`, `STOPPED`, or `STALE`, along with the verified service
and worker observations. Missing or malformed JSON is treated as absent; an
existing process identity that no longer matches is stale. Access failures are
reported as errors rather than disguised as a stopped service.

Logs JSON contains `paths`, `observed_at`, and `stale`. Paths are deduplicated
absolute names reported by live workers, never guessed from file modification
times. `observed_at` is the earliest participating observation in Unix seconds,
or `null` when none exists. `stale` identifies live-worker observations older
than the configured sampling interval. This age flag does not mean that the
worker has stopped. A healthy worker can temporarily report `stale: true`
during sampling, state publication, or CLI identity checks. Dead workers never
reenter the result because their observation is stale.

Log paths are eventually consistent. Under normal operation a rotation appears
within `health.sample_interval_seconds` plus the time needed to collect metrics
and write one state observation. The refresh loop sleeps for the interval before collecting and
publishing its next observation; freshness is not guaranteed for every CLI
invocation. Upstream metrics can report the current or last path; this is not proof that a file is
still writable after a sink failure. Disabling the health HTTP endpoint does
not disable observation refresh.

## Shutdown and cleanup

```console
lcl-fastapi stop -o config service.lclcfg
```

The master creates a random `control.token` for every complete start. On Linux
the token file uses mode `0600`; on Windows its inherited ACL is removed and the
current service-account SID receives access using the native `whoami` and
`icacls` tools. The secret is written after permissions are restricted. Do not
publish this file or its containing directory.

The CLI reads current state, verifies the master identity, and submits a local
`POST /_lcl/shutdown` with `X-LCL-Control-Token`. Missing or incorrect tokens
receive HTTP 403. HTTP 202 accepts shutdown; the response is sent before the
native runtime is asked to stop. The CLI then waits for the verified master to
exit and reports failure when its configured graceful deadline expires. It does
not send emergency signals to an unknown PID.

Status, logs, and stop locate state through the supplied configuration and use
launch-time listener information from that state. A later port edit does not
redirect stop to another service. If the state directory changes, use a
configuration that still locates the original state to manage the old service.

Business lifespan exits before framework logging is flushed and closed. Normal
worker cleanup removes its observation and lease. Kernel file locks release
after a process crash; the next allocation can reclaim a lease only after its
old process identity is no longer live. Master exit removes abandoned worker
state and leases, its PID/state files, and its control token. Lock files remain
as stable synchronization locations.

Only the process that acquired a resource releases its ownership. On Linux,
a gracefully exiting forked worker preserves the master's state, PID file,
control token, and service lock. It closes inherited lock descriptors without
unlocking the master's shared file description. Its own worker observation and
lease are still cleaned before Gunicorn creates a replacement.

Users may override predefined routes with the same HTTP method and exact path.
This is not recommended unless you understand the consequences. In particular,
overriding `POST /_lcl/shutdown` can prevent the stop command from working.

## Worker number scope

The lease pool uses `snowflake.worker_id_base` and `snowflake.worker_id_count`.
Concurrent live leases in this local service receive different numbers. This
does not enlarge lclang's historical ID guarantees across rapid reuse, crashes,
or clock rollback. The framework delegates Snowflake generation to
`lclang==1.0.10` and does not change its algorithm.

For multiple machines, assign nonoverlapping ranges through LCL's environment
configuration facilities. The runtime does not read environment variables as an
alternative source of public service settings. Its `LCL_FASTAPI_CONFIG` and
`LCL_FASTAPI_STATE` variables are internal parent-to-worker launch coordinates.
