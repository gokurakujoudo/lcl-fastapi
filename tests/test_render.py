"""Deployment rendering validates inputs without touching installed services."""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from lcl_fastapi.render import NginxSettings, SystemdSettings, render_nginx, render_systemd
from lcl_fastapi.render.config import render_config
from lcl_fastapi.render.nginx import nginx_quote
from lcl_fastapi.render.systemd import systemd_quote
from lcl_fastapi.render.validation import absolute_path, identifier, integer_value, text_value

NGINX = NginxSettings("api.example.com", 443, 8080, "/certs/service.crt", "/certs/service.key")
SYSTEMD = SystemdSettings(
    "catalog",
    "Catalog service",
    "catalog",
    "catalog",
    "/opt/catalog",
    "/etc/catalog/service.lclcfg",
)
DEPLOYMENT_CONFIG = """__LCL_VERSION__: 1
server.root_path: "https://public.example.com:8443"
logger.file.default.directory: "./logs"
logger.file.service.filename: f"service.{worker_pid}.log"
nginx.server_name: "internal.example.com"
nginx.ssl_certificate: "/certs/service.crt"
nginx.ssl_certificate_key: "/certs/service.key"
systemd.service_name: "catalog"
systemd.description: "Catalog service"
systemd.user: "catalog"
systemd.group: "catalog"
systemd.working_directory: "/opt/catalog"
systemd.config_path: "/etc/catalog/service.lclcfg"
"""


def test_nginx_preserves_paths_and_blocks_shutdown() -> None:
    rendered = render_nginx(NGINX)
    assert "location = /_lcl/shutdown { return 404; }" in rendered
    assert "location / {" in rendered
    assert "proxy_pass http://127.0.0.1:8080;" in rendered
    assert "rewrite" not in rendered
    assert "listen 443 ssl;" in rendered
    assert "server_name api.example.com;" in rendered
    assert "X-Forwarded-Proto $scheme" in rendered
    assert "X-Forwarded-For   $proxy_add_x_forwarded_for" in rendered
    assert rendered.endswith("\n")


def test_renderer_quotes_literal_paths_and_systemd_expansion() -> None:
    assert nginx_quote('/a/"b\\c') == '"/a/\\"b\\\\c"'
    with pytest.raises(ValueError, match="variable syntax"):
        nginx_quote("/certs/$domain.crt")
    assert systemd_quote('/a/"b\\c%d') == '"/a/\\"b\\\\c%%d"'
    special = replace(SYSTEMD, working_directory="/opt/a %b $c", config_path="/etc/a %b $c.cfg")
    rendered = render_systemd(special)
    assert "WorkingDirectory=/opt/a %%b $c\n" in rendered
    assert (
        'ExecStart="/opt/a %%b $$c/.venv/bin/lcl-fastapi" serve -o config "/etc/a %%b $$c.cfg"'
        in rendered
    )


def test_systemd_invokes_public_cli_and_retains_lifecycle_policy() -> None:
    rendered = render_systemd(SYSTEMD)
    assert "# catalog.service" in rendered
    assert "Type=simple" in rendered
    assert 'serve -o config "/etc/catalog/service.lclcfg"' in rendered
    assert "Restart=on-failure" in rendered
    assert "KillMode=mixed" in rendered
    assert "systemctl" not in rendered
    assert "WantedBy=multi-user.target" in rendered


@pytest.mark.parametrize("value", [None, "", "\n", "a\x00b", "line\rbreak", 5])
def test_text_validation_rejects_missing_and_line_injection(value: object) -> None:
    with pytest.raises(ValueError, match="control"):
        text_value(value, "field")


@pytest.mark.parametrize("value", [True, False, "5", 0, 65536, 1.5])
def test_integer_validation_rejects_wrong_type_or_range(value: object) -> None:
    with pytest.raises(ValueError, match="integer"):
        integer_value(value, "port", 1, 65535)


def test_validation_retains_valid_boundary_values() -> None:
    assert integer_value(1, "port", 1, 65535) == 1
    assert integer_value(65535, "port", 1, 65535) == 65535
    assert identifier("service_1.2-test", "name") == "service_1.2-test"
    assert absolute_path("/opt/service space", "path") == "/opt/service space"


def test_renderer_rejects_unsafe_domain_identity_and_relative_deployment_paths() -> None:
    with pytest.raises(ValueError, match="single DNS"):
        render_nginx(replace(NGINX, server_name="example.com; include bad;"))
    with pytest.raises(ValueError, match="letters"):
        render_systemd(replace(SYSTEMD, user="bad user"))
    with pytest.raises(ValueError, match="absolute POSIX"):
        render_systemd(replace(SYSTEMD, config_path="service.lclcfg"))
    with pytest.raises(ValueError, match="restart policy"):
        render_systemd(replace(SYSTEMD, restart="sometimes"))
    for directory in ("/opt/trailing ", "/opt/trailing\\"):
        with pytest.raises(ValueError, match="cannot end"):
            render_systemd(replace(SYSTEMD, working_directory=directory))


def test_render_reads_only_its_fields_without_worker_logger_or_runtime_side_effects(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(DEPLOYMENT_CONFIG, encoding="utf-8")
    nginx = asyncio.run(render_config("nginx", source))
    systemd = asyncio.run(render_config("systemd", source))
    assert "listen 443 ssl" in nginx
    assert "internal.example.com" in nginx
    assert "8443" not in nginx
    assert "public.example.com" not in nginx
    assert "RestartSec=5" in systemd
    assert sorted(item.name for item in tmp_path.iterdir()) == ["service.lclcfg"]


def test_render_honors_explicit_deployment_fields(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(
        DEPLOYMENT_CONFIG
        + """
server.host: "0.0.0.0"
server.port: 9001
nginx.listen_port: 9443
systemd.restart: "always"
systemd.restart_seconds: 12
""",
        encoding="utf-8",
    )
    assert "127.0.0.1:9001;" in asyncio.run(render_config("nginx", source))
    assert "listen 9443 ssl" in asyncio.run(render_config("nginx", source))
    assert "Restart=always\nRestartSec=12" in asyncio.run(render_config("systemd", source))


def test_render_rejects_unreachable_loopback_and_missing_fields(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(DEPLOYMENT_CONFIG + '\nserver.host: "192.0.2.1"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="server.host"):
        asyncio.run(render_config("nginx", source))
    source.write_text("__LCL_VERSION__: 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="nginx.server_name"):
        asyncio.run(render_config("nginx", source))
