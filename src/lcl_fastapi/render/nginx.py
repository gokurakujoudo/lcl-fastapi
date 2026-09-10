"""Render an HTTPS reverse proxy without running Nginx or modifying deployment."""

import re
from dataclasses import dataclass

from lcl_fastapi.render.validation import absolute_path, integer_value, text_value


@dataclass(frozen=True, slots=True)
class NginxSettings:
    """Describe one HTTPS listener and its loopback service upstream.

    :param server_name: Single Nginx server name, optionally using a DNS wildcard.
    :param listen_port: External TCP port in 1 through 65535.
    :param service_port: Loopback service TCP port in 1 through 65535.
    :param certificate: Absolute POSIX certificate path; rendering never reads it.
    :param certificate_key: Absolute POSIX private-key path; rendering never reads it.
    """

    server_name: str
    listen_port: int
    service_port: int
    certificate: str
    certificate_key: str


def nginx_quote(value: str) -> str:
    """Quote a validated Nginx argument and preserve literal special characters.

    :param value: Text already checked for control characters.
    :returns: Double-quoted argument with escaped quote syntax.
    :raises ValueError: If the path contains Nginx variable expansion syntax.
    """
    if "$" in value:
        raise ValueError("Nginx certificate paths cannot contain variable syntax ($)")
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def render_nginx(settings: NginxSettings) -> str:
    """Generate an HTTPS proxy with explicit internal-shutdown blocking.

    :param settings: Listener, upstream, and certificate configuration.
    :returns: Complete Nginx server block ending in a newline.
    :raises ValueError: If a setting cannot be safely represented.
    """
    name = text_value(settings.server_name, "nginx.server_name")
    if re.fullmatch(r"[A-Za-z0-9_.*-]+", name) is None:
        raise ValueError("nginx.server_name must be a single DNS name or wildcard")
    listen = integer_value(settings.listen_port, "nginx.listen_port", 1, 65535)
    port = integer_value(settings.service_port, "server.port", 1, 65535)
    certificate = nginx_quote(absolute_path(settings.certificate, "nginx.ssl_certificate"))
    key = nginx_quote(absolute_path(settings.certificate_key, "nginx.ssl_certificate_key"))
    lines = [
        "server {",
        f"    listen {listen} ssl;",
        f"    server_name {name};",
        f"    ssl_certificate {certificate};",
        f"    ssl_certificate_key {key};",
        "",
        "    location = /_lcl/shutdown { return 404; }",
    ]
    lines.extend(
        [
            "",
            "    location / {",
            f"        proxy_pass http://127.0.0.1:{port};",
            "        proxy_http_version 1.1;",
            "        proxy_set_header Host              $host;",
            "        proxy_set_header X-Real-IP         $remote_addr;",
            "        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;",
            "        proxy_set_header X-Forwarded-Proto $scheme;",
            "        proxy_set_header X-Forwarded-Host  $host;",
            '        proxy_set_header Connection "";',
            "    }",
            "}",
            "",
        ]
    )
    return "\n".join(lines)
