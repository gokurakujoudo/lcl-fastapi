# Local command-line operations

`lcl-fastapi` uses `lclang.cli.CliEntrance`. The service file is an ordinary
`config` parameter, selected with `-o config service.lclcfg`. It is loaded
independently from the short-lived CLI logger, so worker-dependent logger
expressions are resolved only in an actual server worker.

| Command | Operation | Optional parameters |
| --- | --- | --- |
| `serve` | Run the platform process manager in the foreground | None |
| `status` | Read verified local service and worker state | `-o json` |
| `logs` | Read live workers' observed log paths | `-o json` |
| `stop` | Request authenticated loopback graceful shutdown | None |
| `nginx render` | Generate an HTTPS reverse-proxy configuration | `-o output example.conf` |
| `systemd render` | Generate a Linux service unit | `-o output example.service` |

Every operation requires `-o config service.lclcfg`. Use `--help` on the root,
command, or group to inspect the upstream command help; `--version` reports the
installed distribution version. The help label ends in `.py` because lclang's
argv adapter expects a Python script label; the installed command remains
`lcl-fastapi`.

```sh
lcl-fastapi serve -o config service.lclcfg
lcl-fastapi status -o config service.lclcfg -o json
lcl-fastapi logs -o config service.lclcfg
lcl-fastapi stop -o config service.lclcfg
lcl-fastapi nginx render -o config service.lclcfg -o output example.conf
lcl-fastapi systemd render -o config service.lclcfg -o output example.service
```

Place a valueless `-o json` last. For an explicit Boolean, use
`-o json "LCL[True]"` or `-o json "LCL[False]"`; plain `True` is a string and is
rejected. Paths containing spaces must be quoted using the calling shell's
normal rules. Relative `config` and `output` CLI paths use the calling working
directory; relative paths *inside* the service file use that file's directory.

`-c` and `--config` are rejected before lclang can open a CLI logger. Their
upstream meaning would load the service file into the wrong lifecycle.
Overrides are limited to `config`, plus `json` or `output` on the commands shown
above. In particular `-o server.port`, `-o logger.level`, `--host`, `--port`,
`--workers`, and `--json` are rejected. The CLI does not implement dry-run,
restart, configuration reload, or direct process-kill fallbacks.

Successful JSON output consists of exactly one JSON object on stdout.
Diagnostics go to stderr. Plain `logs` output contains one absolute path per
line; JSON additionally contains `observed_at` (Unix seconds, or null for an
empty observation) and `stale`. These paths are eventually consistent snapshots
from live workers, not a filesystem-mtime guess. An exited worker's paths are
excluded. `stale` means that a participating observation is older than the
configured interval, not that its worker is dead. It can temporarily be true
while a healthy worker samples or publishes its next observation. See the
runtime documentation for sampling and failure semantics.

## Status JSON contract

`status -o config service.lclcfg -o json` returns these three top-level keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `status` | string | `RUNNING`, `STOPPED`, or `STALE`. |
| `service` | object or null | Verified master-start identity and launch-time settings, or null when no usable live service exists. |
| `workers` | array of objects | Currently live, identity-verified workers belonging to this service start. |

Example running response (identifiers and times are illustrative):

```json
{
  "status": "RUNNING",
  "service": {
    "pid": 28140,
    "process_create_time": 1789021230.0,
    "service_id": "6e4ca00c60d347f3aef86b96b3412145",
    "name": "example-service",
    "version": "1.0.0",
    "runtime": "gunicorn",
    "host": "127.0.0.1",
    "port": 8080,
    "configured_workers": 1,
    "worker_state_dir": "/opt/example/run/workers",
    "sample_interval_seconds": 1.0,
    "graceful_timeout_seconds": 30,
    "started_at": 1789021230.1
  },
  "workers": [
    {
      "pid": 28146,
      "process_create_time": 1789021231.0,
      "service_id": "6e4ca00c60d347f3aef86b96b3412145",
      "snowflake_worker_id": 0,
      "log_files": ["/opt/example/logs/example-service.28146.000001.log"],
      "observed_at": 1789021232.0
    }
  ]
}
```

`pid` and worker counts are integers. Process-creation, observation, and start
times are Unix seconds; intervals and deadlines are seconds. `service_id` is a
non-secret opaque identifier for this complete service start. `runtime` is
`uvicorn` on Windows and `gunicorn` on Linux. The service `pid` identifies the
runtime's master/parent, and each worker has its own PID and creation time.
Paths use the host platform's absolute path syntax. Control tokens never appear
in this response. To count currently running workers, use the length of
`workers`; compare it with `service.configured_workers` rather than assuming
startup/recovery has already reached the configured count.

An absent or unusable runtime record returns
`{"status": "STOPPED", "service": null, "workers": []}`. A present record that
cannot be verified as the current live service returns
`{"status": "STALE", "service": null, "workers": []}`. Neither result performs
network requests or sends termination signals. Keep health API fields distinct:
the health response uses `service_pid` and `gunicorn_pid`, while this local state
schema uses the service identity's `pid`.

Renderer output goes to stdout unless `output` is supplied. Writing a file
produces no success banner on stdout and does not create parent directories.
An existing explicitly selected output file is replaced. The renderers never
install configuration or invoke Nginx, systemd, or a subprocess.

The serve handler returns before the process manager starts. The CLI's Frame,
logger writer, and event loop are closed first; each actual worker owns its own
configuration Frame and logger runtime. Other CLI commands never evaluate a
worker's `logger.file.service.filename` expression themselves.

See [Nginx rendering](nginx.md) and [systemd rendering](systemd.md).
