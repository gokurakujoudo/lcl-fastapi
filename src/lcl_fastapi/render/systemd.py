"""Render a systemd unit without accessing a deployed service installation."""

from dataclasses import dataclass

from lcl_fastapi.render.validation import absolute_path, identifier, integer_value, text_value


@dataclass(frozen=True, slots=True)
class SystemdSettings:
    """Describe an optional Linux service unit and virtual-environment entry point.

    :param service_name: Unit name without the ``.service`` extension.
    :param description: Human-readable one-line description.
    :param user: Existing deployment user; rendering does not create it.
    :param group: Existing deployment group; rendering does not create it.
    :param working_directory: Absolute POSIX application directory.
    :param config_path: Absolute POSIX service configuration file path.
    :param restart: systemd restart policy, defaulting to ``on-failure``.
    :param restart_seconds: Delay in seconds before an automatic restart.
    """

    service_name: str
    description: str
    user: str
    group: str
    working_directory: str
    config_path: str
    restart: str = "on-failure"
    restart_seconds: int = 5


def systemd_quote(value: str) -> str:
    """Quote an ExecStart argument while preserving literal specifiers and dollars.

    :param value: Validated one-line text.
    :returns: Quoted argument with literal percent, quote, and backslash characters.
    """
    escaped = value.replace("\\", "\\\\").replace('"', '\\"').replace("%", "%%")
    escaped = escaped.replace("$", "$$")
    return f'"{escaped}"'


def render_systemd(settings: SystemdSettings) -> str:
    """Generate a foreground systemd unit using the installed console entry point.

    :param settings: Unit identity and deployment paths.
    :returns: Complete systemd unit ending in a newline.
    :raises ValueError: If a setting cannot be safely represented.
    """
    name = identifier(settings.service_name, "systemd.service_name")
    description = text_value(settings.description, "systemd.description").replace("%", "%%")
    user = identifier(settings.user, "systemd.user")
    group = identifier(settings.group, "systemd.group")
    directory = absolute_path(settings.working_directory, "systemd.working_directory")
    if directory != directory.strip() or directory.endswith("\\"):
        raise ValueError("systemd.working_directory cannot end in whitespace or a backslash")
    config = absolute_path(settings.config_path, "systemd.config_path")
    restart = settings.restart
    if restart not in {
        "no",
        "always",
        "on-success",
        "on-failure",
        "on-abnormal",
        "on-watchdog",
        "on-abort",
    }:
        raise ValueError("systemd.restart must be a supported systemd restart policy")
    delay = integer_value(settings.restart_seconds, "systemd.restart_seconds", 0, 86400)
    executable = systemd_quote(f"{directory.rstrip('/')}/.venv/bin/lcl-fastapi")
    return "\n".join(
        [
            f"# {name}.service",
            "[Unit]",
            f"Description={description}",
            "After=network.target",
            "",
            "[Service]",
            "Type=simple",
            f"User={user}",
            f"Group={group}",
            f"WorkingDirectory={directory.replace('%', '%%')}",
            f"ExecStart={executable} serve -o config {systemd_quote(config)}",
            f"Restart={restart}",
            f"RestartSec={delay}",
            "KillMode=mixed",
            "",
            "[Install]",
            "WantedBy=multi-user.target",
            "",
        ]
    )
