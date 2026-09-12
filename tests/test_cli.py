"""The real lclang entrance preserves service configuration and clean stdout."""

import asyncio
import importlib
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from lclang.logger import LoggerHandlerConfig, use_logger_handler

from lcl_fastapi.cli.arguments import validate_arguments
from lcl_fastapi.cli.main import main
from lcl_fastapi.cli.output import format_logs, format_status

CONFIG = """__LCL_VERSION__: 1
logger.file.default.directory: "./logs"
logger.file.service.filename: f"service.{worker_pid}.log"
nginx.server_name: "api.example.com"
nginx.ssl_certificate: "/certs/service.crt"
nginx.ssl_certificate_key: "/certs/service.key"
"""


@pytest.fixture(autouse=True)
def installed_version(monkeypatch: pytest.MonkeyPatch) -> None:
    module = importlib.import_module("lcl_fastapi.cli.main")
    monkeypatch.setattr(module, "version", lambda distribution: "0.1.0")


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = ModuleType("lcl_fastapi.runtime.common")
    monkeypatch.setitem(sys.modules, "lcl_fastapi.runtime.common", module)
    return module


def test_render_stdout_is_exact_configuration_without_audit_preamble(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    assert main(["nginx", "render", "-o", "config", str(source)]) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith("server {\n")
    assert captured.out.endswith("}\n")
    assert "execution" not in captured.out
    assert captured.err == ""
    assert list(tmp_path.iterdir()) == [source]


def test_render_only_writes_explicit_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "service.lclcfg"
    destination = tmp_path / "example.conf"
    source.write_text(CONFIG, encoding="utf-8")
    assert (
        main(["nginx", "render", "-o", "config", str(source), "-o", "output", str(destination)])
        == 0
    )
    assert destination.read_text(encoding="utf-8").startswith("server {\n")
    assert capsys.readouterr().out == ""
    assert sorted(item.name for item in tmp_path.iterdir()) == ["example.conf", "service.lclcfg"]


@pytest.mark.parametrize(
    "tokens, message",
    [
        (["serve", "-c", "service.lclcfg"], "use -o config"),
        (["serve", "--config", "service.lclcfg"], "use -o config"),
        (["serve", "-o", "server.port", "9000"], "unsupported override"),
        (["serve", "-o", "logger.file.service.directory", "/bad"], "unsupported override"),
        (["serve", "-o", "json"], "unsupported override"),
        (["logs", "-o", "output", "bad"], "unsupported override"),
        (["serve", "--host", "0.0.0.0"], "unknown option"),
        (["serve", "--dryrun"], "dryrun is unavailable"),
        (["logs", "--json"], "unknown option"),
    ],
)
def test_forbidden_options_fail_before_service_or_logger_configuration(
    tokens: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(tokens) != 0
    captured = capsys.readouterr()
    assert captured.out == ""
    assert message in captured.err


@pytest.mark.parametrize(
    "tokens", [["--help"], ["nginx", "--help"], ["serve", "--help"], ["--version"], ["-v"]]
)
def test_upstream_help_and_version_work_without_service_config(
    tokens: list[str], capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(tokens) == 0
    assert capsys.readouterr().out


def test_missing_config_and_invalid_config_value_fail(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) != 0
    assert "missing command" in capsys.readouterr().err
    assert main(["serve"]) != 0
    assert "required parameter" in capsys.readouterr().err
    assert main(["serve", "-o", "config"]) != 0
    assert "nonempty text" in capsys.readouterr().err


def test_serve_begins_only_after_cli_logger_and_event_loop_exit(
    runtime: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    called: list[Path] = []

    async def independent_scope() -> None:
        async with use_logger_handler(LoggerHandlerConfig(console={"enabled": False})):
            pass

    def serve(path: Path) -> None:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        asyncio.run(independent_scope())
        called.append(path)

    monkeypatch.setattr(runtime, "serve", serve, raising=False)
    source = tmp_path / "service.lclcfg"
    assert main(["serve", "-o", "config", str(source)]) == 0
    assert called == [source]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "option, expected", [([], True), (["LCL[True]"], True), (["LCL[False]"], False)]
)
def test_serve_hot_reload_boolean_handoff(
    runtime: ModuleType, monkeypatch: pytest.MonkeyPatch, option: list[str], expected: bool
) -> None:
    calls: list[bool] = []

    def serve(path: Path, hot_reload: bool = False) -> None:
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()
        calls.append(hot_reload)

    monkeypatch.setattr(runtime, "serve", serve, raising=False)
    assert main(["serve", "-o", "config", "service.lclcfg", "-o", "hot_reload", *option]) == 0
    assert calls == [expected]


@pytest.mark.parametrize("value", ["True", "LCL[1]", "LCL[None]"])
def test_hot_reload_rejects_nonboolean(value: str, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["serve", "-o", "config", "service.lclcfg", "-o", "hot_reload", value]) != 0
    assert "Boolean" in capsys.readouterr().err


@pytest.mark.parametrize(
    "command", [["status"], ["logs"], ["stop"], ["nginx", "render"], ["systemd", "render"]]
)
def test_hot_reload_is_serve_only(command: list[str], capsys: pytest.CaptureFixture[str]) -> None:
    assert main([*command, "-o", "config", "service.lclcfg", "-o", "hot_reload"]) != 0
    assert "unsupported override" in capsys.readouterr().err


def test_startup_failure_becomes_stderr_error(
    runtime: ModuleType, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def serve(path: Path) -> None:
        raise OSError(f"cannot start {path.name}")

    monkeypatch.setattr(runtime, "serve", serve, raising=False)
    assert main(["serve", "-o", "config", "service.lclcfg"]) == 2
    assert "cannot start service.lclcfg" in capsys.readouterr().err


def test_status_logs_and_stop_call_runtime_with_parsed_config(
    runtime: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "service.lclcfg"
    called: list[Path] = []

    async def inspect_status(path: Path) -> dict[str, object]:
        called.append(path)
        return {"status": "RUNNING", "service_pid": 42}

    async def active_logs(path: Path) -> dict[str, object]:
        called.append(path)
        return {"paths": [str(tmp_path / "active.log")], "observed_at": 123.0, "stale": False}

    async def stop(path: Path) -> None:
        called.append(path)

    monkeypatch.setattr(runtime, "inspect_status", inspect_status, raising=False)
    monkeypatch.setattr(runtime, "active_logs", active_logs, raising=False)
    monkeypatch.setattr(runtime, "stop", stop, raising=False)
    for command in ("status", "logs"):
        assert main([command, "-o", "config", str(source), "-o", "json"]) == 0
        result = json.loads(capsys.readouterr().out)
        assert isinstance(result, dict)
    assert main(["logs", "-o", "config", str(source)]) == 0
    assert capsys.readouterr().out == f"{tmp_path / 'active.log'}\n"
    assert main(["stop", "-o", "config", str(source)]) == 0
    assert capsys.readouterr().out == ""
    assert called == [source] * 4
    assert main(["status", "-o", "config", str(source), "-o", "json", "True"]) != 0
    assert "json must be Boolean" in capsys.readouterr().err


def test_main_accepts_process_argv(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["lcl-fastapi", "--version"])
    assert main() == 0
    assert "0.1.0" in capsys.readouterr().out


def test_output_formats_preserve_json_metadata_and_reject_malformed_paths() -> None:
    assert "Service Pid" in format_status({"service_pid": 42}, as_json=False)
    assert json.loads(format_status({"status": "RUNNING"}, as_json=True)) == {"status": "RUNNING"}
    assert format_logs({"paths": []}, as_json=False) == ""
    with pytest.raises(ValueError, match="paths list"):
        format_logs({}, as_json=False)
    with pytest.raises(ValueError, match="paths list"):
        format_logs({"paths": [1]}, as_json=True)
    validate_arguments(
        [
            "python",
            "lcl-fastapi.py",
            "status",
            "-o",
            "config",
            "service.lclcfg",
            "-o",
            "json",
            "LCL[False]",
        ]
    )
