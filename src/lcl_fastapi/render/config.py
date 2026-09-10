"""Resolve only deployment fields from a service's independent LCL Frame."""

from pathlib import Path
from typing import Literal

from lclang.config import load_config
from lclang.runtime import Frame

from lcl_fastapi.render.nginx import NginxSettings, render_nginx
from lcl_fastapi.render.systemd import SystemdSettings, render_systemd
from lcl_fastapi.render.validation import integer_value, text_value


async def frame_text(frame: Frame, name: str, fallback: str | None = None) -> str:
    """Resolve one required or defaulted textual renderer field.

    :param frame: Caller-owned LCL service Frame.
    :param name: Qualified field name.
    :param fallback: Default text, or None when the field is required.
    :returns: Nonempty validated configuration text.
    :raises ValueError: If the value is missing, nontext, or contains controls.
    """
    return text_value(await frame.get(name, fallback=fallback), name)


async def nginx_settings(frame: Frame) -> NginxSettings:
    """Read the Nginx subtree without evaluating worker-dependent logging.

    :param frame: Caller-owned service configuration Frame.
    :returns: Validated settings for a loopback HTTPS proxy.
    :raises ValueError: If required deployment settings are invalid or absent.
    """
    host = await frame.get("server.host", fallback="127.0.0.1")
    if host not in {"127.0.0.1", "0.0.0.0"}:
        raise ValueError("server.host must be 127.0.0.1 or 0.0.0.0 for loopback proxying")
    return NginxSettings(
        server_name=await frame_text(frame, "nginx.server_name"),
        listen_port=integer_value(
            await frame.get("nginx.listen_port", fallback=443), "nginx.listen_port", 1, 65535
        ),
        service_port=integer_value(
            await frame.get("server.port", fallback=8080), "server.port", 1, 65535
        ),
        certificate=await frame_text(frame, "nginx.ssl_certificate"),
        certificate_key=await frame_text(frame, "nginx.ssl_certificate_key"),
    )


async def systemd_settings(frame: Frame) -> SystemdSettings:
    """Read systemd deployment fields without touching the target installation.

    :param frame: Caller-owned service configuration Frame.
    :returns: Settings for a foreground virtual-environment service.
    :raises ValueError: If required deployment settings are invalid or absent.
    """
    return SystemdSettings(
        service_name=await frame_text(frame, "systemd.service_name"),
        description=await frame_text(frame, "systemd.description"),
        user=await frame_text(frame, "systemd.user"),
        group=await frame_text(frame, "systemd.group"),
        working_directory=await frame_text(frame, "systemd.working_directory"),
        config_path=await frame_text(frame, "systemd.config_path"),
        restart=await frame_text(frame, "systemd.restart", "on-failure"),
        restart_seconds=integer_value(
            await frame.get("systemd.restart_seconds", fallback=5),
            "systemd.restart_seconds",
            0,
            86400,
        ),
    )


async def render_config(kind: Literal["nginx", "systemd"], config_path: Path) -> str:
    """Load one trusted LCL file and render the requested deployment artifact.

    :param kind: Supported renderer name.
    :param config_path: Service configuration path; relative to the current directory.
    :returns: Complete deployment configuration text.
    :raises ValueError: If required deployment settings are invalid or absent.
    :raises OSError: If the service configuration cannot be read.
    """
    loaded = await load_config(config_path)
    async with loaded.frame_factory().create() as frame:
        if kind == "nginx":
            return render_nginx(await nginx_settings(frame))
        return render_systemd(await systemd_settings(frame))
