# Generate an optional systemd unit

The service runs without systemd. For a Linux deployment that uses it, place
these deployment fields in the same `.lclcfg`:

```text
systemd.service_name: "example-service"
systemd.description: "Example REST Service"
systemd.user: "example"
systemd.group: "example"
systemd.working_directory: "/opt/example-service"
systemd.config_path: "/etc/example-service/service.lclcfg"
systemd.restart: "on-failure"
systemd.restart_seconds: 5
```

```sh
lcl-fastapi systemd render -o config service.lclcfg
lcl-fastapi systemd render -o config service.lclcfg -o output example.service
```

The generated `ExecStart` invokes
`<working_directory>/.venv/bin/lcl-fastapi serve -o config <config_path>`.
Deploy the package into that virtual environment and arrange the selected user,
group, working directory, and readable service configuration separately.
Deployment paths are absolute POSIX paths even when rendering on Windows.
Argument quoting preserves spaces, literal percent characters, and dollar signs.

The unit uses a foreground `Type=simple` service and `KillMode=mixed`, allowing
the process manager to coordinate worker shutdown. Configure the surrounding
systemd shutdown timeout to leave sufficient time for the application's graceful
timeout and log draining. `Restart=on-failure` is the default; runtime shutdown
must exit successfully to avoid an unwanted automatic restart.

Rendering only returns text or writes the explicitly selected output file.
It never calls `systemctl`, installs units, creates users, enables services,
or performs start, stop, restart, or daemon-reload operations. Review and deploy
the result separately.

<!-- python-doc-exec -->
```python
from lcl_fastapi.render import SystemdSettings, render_systemd

settings = SystemdSettings(
    service_name="example-service",
    description="Example REST Service",
    user="example",
    group="example",
    working_directory="/opt/example-service",
    config_path="/etc/example-service/service.lclcfg",
)
unit = render_systemd(settings)
assert "WorkingDirectory=/opt/example-service\n" in unit
assert 'serve -o config "/etc/example-service/service.lclcfg"' in unit
assert "Restart=on-failure\n" in unit
```

[CLI contract](cli.md)
