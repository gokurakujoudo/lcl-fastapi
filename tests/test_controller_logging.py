"""Verify separated upstream sinks, observations, flush, and controller ownership."""

import asyncio
import os
import threading
from pathlib import Path

import pytest
from lclang.logger import resolve_logger_config, use_logger, use_logger_handler

from lcl_fastapi.config import load_settings
from lcl_fastapi.logging import resolve_log_directories
from lcl_fastapi.runtime import controller
from lcl_fastapi.runtime.controller import controller_event, controller_logging, controller_tick
from lcl_fastapi.runtime.state import atomic_write, process_identity
from lcl_fastapi.sources import configuration_frame

CONFIG = """app.name: "logging"
app.version: "1"
app.target: "app:service"
logger.console.enabled: False
logger.file.default.directory: "./logs"
logger.file.controller.filename: "control.log"
logger.file.service.filename: f"worker.{worker_pid}.log"
health.sample_interval_seconds: 0.01
"""


def contents(root: Path, pattern: str) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in root.glob(pattern))


def test_controller_lifecycle_rotation_and_worker_file_separation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))
    identity = process_identity() | {"service_id": "test"}
    worker = identity | {"log_files": ["first.log"]}
    worker_path = settings.worker_state_dir / "worker.json"
    threads = {thread.ident for thread in threading.enumerate()}
    with controller_logging(source, settings, identity):
        atomic_write(worker_path, worker)
        controller_tick()
        active = controller.CONTROLLER.get()
        assert active is not None
        active.deadline = float("inf")
        controller_tick()
        controller_event("hot-reload retiring worker_pid=example")
        atomic_write(worker_path, worker | {"log_files": ["second.log"]})
        active.tick(force=True)
        worker_path.unlink()
        active.tick(force=True)
        with controller.controller_fork():
            assert {thread.ident for thread in threading.enumerate()} == threads
    assert controller.CONTROLLER.get() is None
    assert {thread.ident for thread in threading.enumerate()} == threads
    control = contents(tmp_path / "logs", "control*")
    for event in (
        "service started",
        "heartbeat",
        "worker up",
        "worker down",
        "worker log rotate",
        "hot-reload",
        "service stopped",
    ):
        assert event in control
    assert not list((tmp_path / "logs").glob("worker*"))
    assert len(list((tmp_path / "logs").glob("control*"))) == 2

    async def business_log() -> None:
        async with configuration_frame(source, os.getpid(), logger_role="worker") as frame:
            config = resolve_log_directories(await resolve_logger_config(frame), tmp_path)
            async with use_logger_handler(config):
                logger = await use_logger(name="business")
                logger.info("route handled")

    asyncio.run(business_log())
    assert "route handled" in contents(tmp_path / "logs", "worker*")
    assert "route handled" not in contents(tmp_path / "logs", "control*")
    assert "heartbeat" not in contents(tmp_path / "logs", "worker*")


def test_controller_failure_flushes_and_restores_scope(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))
    with pytest.raises(RuntimeError, match="native failed"):
        with controller_logging(source, settings, process_identity() | {"service_id": "test"}):
            raise RuntimeError("native failed")
    assert controller.CONTROLLER.get() is None
    assert "service failed" in contents(tmp_path / "logs", "control*")
    controller_tick()
    controller_event("outside scope")
    assert "outside scope" not in contents(tmp_path / "logs", "control*")


@pytest.mark.parametrize("code", [None, 0, 1])
def test_native_exit_status_is_recorded_without_mislabeling_success(
    tmp_path: Path,
    code: int | None,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))
    with pytest.raises(SystemExit):
        with controller_logging(source, settings, process_identity() | {"service_id": "test"}):
            raise SystemExit(code)
    assert ("service failed" in contents(tmp_path / "logs", "control*")) == (code == 1)


def test_writer_initialization_failure_releases_runtime_and_closed_writer_rejects_events(
    tmp_path: Path,
) -> None:
    from lclang.logger import LoggerHandlerConfig

    from lcl_fastapi.runtime.controller_writer import ControllerWriter

    destination = tmp_path / "not-a-directory"
    destination.write_text("file", encoding="utf-8")
    writer = ControllerWriter(
        LoggerHandlerConfig(
            console={"enabled": False},
            file={"controller": {"directory": str(destination), "filename": "controller.log"}},
        )
    )
    with pytest.raises(FileExistsError):
        writer.open()
    assert writer.runner is None
    writer.close()
    with pytest.raises(RuntimeError, match="not active"):
        writer.emit(["closed"])
    # Upstream's exclusive scope was released even though initialization failed.
    healthy = ControllerWriter(LoggerHandlerConfig(console={"enabled": False}))
    healthy.open()
    healthy.emit(["ready"])
    healthy.close()


def test_reopen_failure_preserves_cause_and_releases_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))

    def fail() -> None:
        raise OSError("cannot reopen controller sink")

    with pytest.raises(OSError, match="cannot reopen controller sink"):
        with controller_logging(source, settings, process_identity() | {"service_id": "test"}):
            active = controller.CONTROLLER.get()
            assert active is not None
            monkeypatch.setattr(active.writer, "open", fail)
            with controller.controller_fork():
                pass
    assert controller.CONTROLLER.get() is None
