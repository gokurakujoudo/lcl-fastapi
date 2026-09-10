# Health and observations

The default `GET /health` endpoint returns `status: "UP"`, a `service` summary,
and cached `server` metrics. It describes the framework's running process, not
the readiness of databases or other business dependencies. Replace the same GET
route explicitly when the application needs its own readiness policy.

Service data includes name/version, runtime, service PID, Gunicorn PID, current
worker PID, configured/running workers, worker PIDs, and uptime. Linux reports
the actual Gunicorn master PID in both `service_pid` and `gunicorn_pid`. Windows
uses the Uvicorn service PID and reports `gunicorn_pid: null`. Local process
identity checks use both PID and creation time and ignore stale observations.

Server data contains hostname, logical CPU count and usage percentage, memory
total/available bytes and percentage, and disk total/free bytes and percentage
for each configured path. Sampling runs outside request handling; `/health`
copies the most recent observation instead of performing blocking CPU sampling.
The first nonblocking CPU reading can be zero before an interval has elapsed.

A failed CPU, memory, or disk read is represented by that field's
`status: "unavailable"` and diagnostic `error`. It does not cause the whole
endpoint to return HTTP 500. Repeated warnings are suppressed while the observed
failure remains unchanged. Health responses never contain control tokens.

```text
health.enabled: True
health.path: "/health"
health.sample_interval_seconds: 1
health.disk_paths: [".", logger.file.default.directory]
```

`health.enabled: False` removes the framework endpoint. It does not stop the
worker-state and active-log refresh used by local `status`, `logs`, and `stop`.
An explicitly registered business route at the configured health path remains
the business application's responsibility.

## Successful response schema

This illustrative Linux response shows the exact keys. Numeric readings, PIDs,
paths, hostnames, and worker counts vary with the machine and configuration.
`uptime_seconds` is a nonnegative number; a worker may briefly observe fewer
running workers while another worker is starting or recovering.

```json
{
  "status": "UP",
  "service": {
    "name": "example-service",
    "version": "1.0.0",
    "runtime": "gunicorn",
    "service_pid": 28140,
    "gunicorn_pid": 28140,
    "worker_pid": 28146,
    "configured_workers": 2,
    "running_workers": 2,
    "worker_pids": [28146, 28147],
    "uptime_seconds": 3600.25
  },
  "server": {
    "hostname": "server01",
    "cpu": {"logical_count": 16, "usage_percent": 22.1},
    "memory": {
      "total_bytes": 68719476736,
      "available_bytes": 44122972160,
      "usage_percent": 35.8
    },
    "disk": [{
      "path": "/",
      "total_bytes": 536870912000,
      "free_bytes": 310420684800,
      "usage_percent": 42.2
    }]
  }
}
```

On Windows, `service.runtime` is `"uvicorn"` and `service.gunicorn_pid` is `null`;
the remaining keys retain the same meanings. `server.cpu.logical_count` can be
`null` when psutil cannot determine it. Disk entries use `path` and the suffixes
`_bytes` and `_percent`; there is no `server.disks`, `total`, or `percent` key.

An unavailable metric replaces its normal object with
`{"status": "unavailable", "error": "diagnostic text"}`. An unavailable disk
also retains its `path`. Hostname lookup failure uses that object in place of
the hostname string. Other successful metrics remain present.
