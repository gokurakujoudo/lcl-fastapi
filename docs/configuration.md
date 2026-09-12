# Configuration reference

A trusted `.lclcfg` file is the formal configuration source. Framework defaults
are shipped as [a `.lclcfg` resource](https://github.com/gokurakujoudo/lcl-fastapi/blob/main/src/lcl_fastapi/static/defaults.lclcfg)
and combined using the upstream LCL configuration model. The downstream file
overrides these defaults, and command-line overrides win over both. This per-start parsing is not a persistent snapshot or
a file watcher. Never treat LCL expressions as a hostile-code sandbox.

## Reusable configuration with using

The bundled universal configuration is available to native LCL `using` through
`lcl_fastapi_defaults`, an absolute path supplied by the framework:

```text
__LCL_VERSION__: 1

# Shared configuration
using f"{lcl_fastapi_defaults}"
using "./team.lclcfg"

# Application identity and import target
app.name: "catalog"
app.version: "1.0.0"
app.target: "catalog.app:service"

# HTTP serving and shutdown
server.workers: 2
```

Each using path is resolved relative to its importing file. LCL expands imports
in source order; later definitions win. Dynamic using targets can use preceding
values and CLI overrides. Cycles and invalid targets retain native LCL errors.
Application filesystem settings remain anchored to the root service file, even
when their definitions came from another file. The defaults are also loaded
implicitly for compatibility; placing an explicit using later can reset earlier
definitions to universal defaults. Put it before application customizations.
The framework-owned `lcl_fastapi_defaults` and `worker_pid` cannot be redefined.

Command-line values use native lclang syntax, for example `-o server.port
"LCL[9000]"`, `-o docs.enabled "LCL[False]"`, or `-o business.region west`.
They apply to settings, renderers, business `get_config`, and logger resolution.
Raw expressions are transported privately to native workers and re-evaluated
against each worker's freshly loaded file, including after crash or hot reload.
Overrides are not written into configuration or runtime-state files. Run local
operations with the same state-directory overrides used at startup.

Three application fields are required: `app.name`, `app.version`, and
`app.target`, where the target is an importable `module:attribute` exposing the
`LclFastAPI` object. Relative runtime, log, and disk paths are anchored to the
configuration file's directory. No user-facing environment override layer exists.
The internal `LCL_FASTAPI_CONFIG`, `LCL_FASTAPI_STATE`, and
`LCL_FASTAPI_OVERRIDES` variables transport selected paths and raw overrides to
child workers; users should use the CLI configuration
option instead of setting those variables themselves.

| Setting | Default and contract |
| --- | --- |
| `server.host` | `127.0.0.1`; only this and `0.0.0.0` are accepted. |
| `server.port` | `8080`, an integer from 1 through 65535. |
| `server.workers` | `1`, bounded by the available Snowflake ID range. |
| `background_worker.default.enabled` | `True`; strict Boolean default for code-registered background workers. |
| `background_worker.default.auto_restart` | `True`; normal and exceptional returns restart after one second unless stopping. |
| `server.reload_dirs` | `["."]`; nonempty list of Python watch directories, relative to this configuration file or absolute. An explicit list replaces the default. Used by `serve -o hot_reload`. |
| `server.root_path` | Empty, or an HTTP(S) origin with hostname and optional port; no credentials, path, query, or fragment. |
| `server.backlog` | `2048` pending connections. |
| `server.keep_alive_seconds` | `5`; zero disables idle keep-alive waiting. |
| `server.graceful_timeout_seconds` | `30`, a positive shutdown deadline in seconds. |
| `runtime.state_dir` | `./run`. |
| `runtime.pid_file` | LCL expression yielding `<state_dir>/<app.name>.pid`. |
| `runtime.worker_state_dir` | LCL expression yielding `<state_dir>/workers`. |
| `health.enabled` | `True`; disabling the endpoint does not stop runtime-state refresh. |
| `health.path` | `/health`. |
| `health.sample_interval_seconds` | `1`, a positive finite number of seconds. |
| `health.disk_paths` | The configuration directory and configured default log directory. |
| `docs.enabled` | `True`; controls the built-in Swagger page, schema, and assets together. |
| `docs.path` | `/docs`. |
| `docs.openapi_path` | `/openapi.json`. |
| `request.id_header` | `X-Request-ID`, a valid ASCII header name. |
| `snowflake.worker_id_base` | `0`; first allowed ID in the range 0–1023. |
| `snowflake.worker_id_count` | `64`; at least the worker count, with base + count at most 1024. |

There is no `api.prefix`. Configure prefixes on business Routers.

`background_worker.<name>.enabled` and `.auto_restart` override those individual
defaults. Remaining nested worker fields are ordinary LCL parameters. Executables
are registered only in application code. A nonempty background registry forces one
effective API worker with a warning, including when every registration is disabled.
See [background workers](background-workers.md) for resources, logs and shutdown.
`server.root_path` is external origin metadata for code that needs a complete
public URL, not a FastAPI/ASGI mount path. It never changes routing. Read it with
`get_config` if the business needs it. The framework does not force that origin
into the local Swagger request target or local stop command.

`nginx.server_name`, `nginx.listen_port`, and certificate settings determine the
rendered Nginx listener independently. For example, an origin on public port 443
may map to a differently numbered Nginx listener. Deployment operators own that
mapping; the framework must not silently replace those fields with origin data.

## Per-machine Snowflake ranges

LCL's built-in `env` utility is the environment integration:

```text
snowflake.worker_id_base: int(env.get("LCL_WORKER_ID_BASE", "0"))
snowflake.worker_id_count: 64
```

Set `LCL_WORKER_ID_BASE=0` on one machine and `64` on another, for example. The
environment value is interpreted through the formal configuration expression.
Operators must choose non-overlapping ranges; there is no cross-machine allocator.
The supported scope is one service per machine. Concurrent worker leases do not
extend the upstream generator's historical guarantees across crashes and rapid ID
reuse.

## Configuration changes

Application code can temporarily override configuration using
[`use_lcl_frame`](application.md#scoped-lcl-frames). The scope changes local
`get_config()` lookup without editing files or changing other request scopes.
Inherited definitions retain native parent evaluation and caching.

For development, select one or more recursive Python watch roots:

```text
# HTTP serving and shutdown
server.reload_dirs: ["./src", "../shared"]
```

Then run `lcl-fastapi serve -o config service.lclcfg -o hot_reload`.
Include `"."` in the list to keep watching the configuration directory along with
additional directories. Resolved duplicates are watched once. Selected directories
must exist and be accessible when hot reload starts; invalid entries fail startup.
Only `.py` additions, modifications, and deletions inside the selected roots trigger
reload, including nested files. Configuration files, other extensions, and resolved
paths outside those roots do not. Watch roots are fixed for the complete start;
changing this list requires stopping and starting the service.

Hot reload forces one effective worker and warns once on stderr when the configured
count exceeds one. Configuration validation still applies before that override.
Status and health report the effective count. The file itself is never rewritten.
See [runtime lifecycle](runtime.md#development-hot-reload) for ownership and errors.

The master reads service settings at complete startup. Every worker, including a
replacement after a crash, independently loads the current file. Existing workers
do not reload; a replacement does not reconfigure the running master's listener
or process-management settings. Editing a running service's file can therefore
produce mixed worker settings. Prefer an externally managed complete restart.

Local operations first locate runtime state from the supplied configuration and
then use recorded identity and launch-time listener information. If you change
the runtime directory, retain a configuration that can locate the previous
instance until it has been stopped.
