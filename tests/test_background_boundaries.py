"""Verify journal integrity, process identity guards and background failure cleanup."""

import asyncio
import json
import logging
import runpy
import subprocess
import threading
from pathlib import Path
from unittest.mock import Mock

import psutil
import pytest
from lclang.logger import LoggerHandlerConfig
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime
from test_background import background_runtime as background_runtime

from lcl_fastapi import BackgroundWorkerContext, LclFastAPI
from lcl_fastapi.background import thread
from lcl_fastapi.background.config import WorkerPolicy
from lcl_fastapi.background.journal import (
    BackgroundEvents,
    BackgroundJournal,
    terminate_background_process,
)
from lcl_fastapi.background.logging import background_log_config
from lcl_fastapi.background.manager import BackgroundManager
from lcl_fastapi.config import load_settings
from lcl_fastapi.runtime import probe
from lcl_fastapi.runtime.controller import controller_background_tick, controller_logging
from lcl_fastapi.runtime.controller_writer import ControllerWriter
from lcl_fastapi.runtime.state import process_identity


def test_journal_partial_lines_foreign_start_malformed_and_dead_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lcl_fastapi.background.journal as journal_module

    identity = process_identity() | {"service_id": "current"}
    journal = BackgroundJournal(tmp_path, identity)
    journal.flush()
    assert not journal.path.exists()
    journal.emit(logging.INFO, "first")
    journal.flush()
    reader = BackgroundEvents(tmp_path, "current")
    assert reader.read() == [(logging.INFO, "first")]
    assert reader.read() == []
    with journal.path.open("ab") as file:
        file.write(b'broken json\n[]\n{"service_id":"old"}\n{"partial":')
    assert reader.read() == []
    with journal.path.open("ab") as file:
        file.write(b"true}\n")
    journal.emit(logging.ERROR, "second", deadline=0)
    journal.flush()
    assert reader.read() == [(logging.ERROR, "second")]
    assert reader.expired()[0]["message"] == "second"
    journal.emit(logging.INFO, "waiting", deadline=float("inf"))
    journal.flush()
    reader.read()
    assert reader.expired() == []
    assert len(reader.expired(force=True)) == 1
    journal.emit(logging.INFO, "retiring", deadline=0)
    journal.emit(logging.INFO, "retired", retired=True)
    journal.flush()
    reader.read()
    assert reader.expired() == []
    journal.emit(logging.INFO, "dead", deadline=0)
    journal.flush()
    reader.read()
    monkeypatch.setattr(journal_module, "is_live", lambda record: False)
    assert reader.expired() == []
    assert reader.read() == []
    assert not journal.path.exists()


@pytest.mark.parametrize("state", ["matching", "reused", "gone", "denied"])
def test_timeout_termination_rechecks_process_identity(
    monkeypatch: pytest.MonkeyPatch, state: str
) -> None:

    process = Mock()
    process.create_time.return_value = 1 if state == "matching" else 2
    factory = Mock(return_value=process)
    if state == "gone":
        factory.side_effect = psutil.NoSuchProcess(123)
    if state == "denied":
        factory.side_effect = psutil.AccessDenied(123)
    monkeypatch.setattr(psutil, "Process", factory)
    if state == "denied":
        with pytest.raises(psutil.AccessDenied):
            terminate_background_process({"pid": 123, "process_create_time": 1})
    else:
        terminate_background_process({"pid": 123, "process_create_time": 1})
    assert process.kill.call_count == int(state == "matching")


async def test_initialization_failure_and_process_exit_are_observed(
    configuration: Path, background_runtime: ObservedRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def initialize(*args: object) -> BackgroundWorkerContext:
        raise ValueError("background init failed")

    app = LclFastAPI(config_path=configuration, background_workers={"job": lambda ctx: None})
    with monkeypatch.context() as patch:
        patch.setattr(thread.WorkerThread, "initialize", initialize)
        with pytest.raises(ValueError, match="background init failed"):
            async with app.router.lifespan_context(app):
                pytest.fail("startup should fail")
    assert app.background_manager is None

    def exit_thread(context: BackgroundWorkerContext) -> None:
        raise SystemExit(5)

    app = LclFastAPI(config_path=configuration, background_workers={"job": exit_thread})
    async with app.router.lifespan_context(app):
        assert app.background_manager is not None
        worker = app.background_manager.workers["job"]
        await asyncio.wrap_future(worker.done)
        assert worker.snapshot()["status"] == "failed"


async def test_journal_initialization_failure_prevents_entries(
    configuration: Path, background_runtime: ObservedRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_flush(self: BackgroundJournal) -> None:
        raise OSError("journal unavailable")

    monkeypatch.setattr(BackgroundJournal, "flush", fail_flush)
    entry = Mock()
    app = LclFastAPI(config_path=configuration, background_workers={"job": entry})
    with pytest.raises(OSError, match="journal unavailable"):
        async with app.router.lifespan_context(app):
            pytest.fail("unavailable journal must fail startup")
    entry.assert_not_called()
    assert app.background_manager is None


async def test_cleanup_waits_through_repeated_cancellation(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    entered, cancelling, release = threading.Event(), threading.Event(), threading.Event()

    async def job(context: BackgroundWorkerContext) -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelling.set()
            assert await asyncio.to_thread(release.wait, 5)

    app = LclFastAPI(config_path=configuration, background_workers={"job": job})
    async with app.router.lifespan_context(app):
        assert await asyncio.to_thread(entered.wait, 5)
        assert app.background_manager is not None
        stop = asyncio.create_task(app.background_manager.stop())
        assert await asyncio.to_thread(cancelling.wait, 5)
        stop.cancel()
        await asyncio.sleep(0)
        stop.cancel()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await stop
        assert not app.background_manager.workers["job"].thread.is_alive()


async def test_registered_workers_reject_multi_api_runtime(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    background_runtime.service["configured_workers"] = 2
    app = LclFastAPI(config_path=configuration, background_workers={"job": lambda ctx: None})
    with pytest.raises(RuntimeError, match="one effective API worker"):
        async with app.router.lifespan_context(app):
            pytest.fail("unverified multi-worker runtime")


def test_log_templates_and_disabled_sinks() -> None:
    config = LoggerHandlerConfig(
        console={"enabled": False},
        file={
            "default": {"directory": ".", "logger_names": ["inherited"]},
            "service": {"logger_names": ["business"]},
            "off": {},
            "job": {"filename": "custom.log", "logger_names": ["nested"]},
            "inherited_job": {},
        },
    )
    resolved = background_log_config(
        config,
        {
            "job": WorkerPolicy(True, True),
            "off": WorkerPolicy(False, True),
            "inherited_job": WorkerPolicy(True, True),
        },
        "app",
        1,
    ).resolved_files()
    assert "off" not in resolved
    assert resolved["job"].filename == "custom.log"
    assert resolved["job"].logger_names == ("lcl_fastapi.background.job.nested",)
    assert resolved["service"].logger_names == ("lcl_fastapi.api.business",)
    assert resolved["inherited_job"].logger_names == (
        "lcl_fastapi.background.inherited_job.inherited",
    )


async def test_stop_before_start_and_before_async_entry(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)
    journal = BackgroundJournal(configuration.parent / "run", process_identity())
    manager = BackgroundManager(
        {"job": lambda ctx: pytest.fail("stopped entry ran")},
        {"job": WorkerPolicy(True, True)},
        app,
        None,
        configuration,
        journal,
        1,
    )
    await manager.stop()
    assert manager.snapshot()["job"] == {"status": "starting", "attempts": 0, "restarts": 0}
    async with app.router.lifespan_context(app):
        await manager.start()
        worker = manager.workers["job"]
        await asyncio.wrap_future(worker.done)
        await asyncio.to_thread(worker.thread.join)
        assert manager.pump is not None
        await manager.pump
        with pytest.raises(asyncio.CancelledError):
            await worker.await_entry(asyncio.sleep(0))
        assert worker.task is None


def test_probe_script_only_imports_registration(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import lcl_fastapi.runtime.application as application

    def load() -> LclFastAPI:
        print("application import output")
        return LclFastAPI(background_workers={"job": lambda ctx: None})

    monkeypatch.setattr(application, "load_application", load)
    runpy.run_path(str(Path(probe.__file__)), run_name="__main__")
    output = capsys.readouterr()
    assert output.out == "true\n"
    assert output.err == "application import output\n"


@pytest.mark.parametrize("outcome", ["true", "false", "invalid", "error", "timeout"])
async def test_registration_probe_results_and_failures(
    configuration: Path, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    call = Mock(
        return_value=subprocess.CompletedProcess(
            [], int(outcome == "error"), outcome, "import failed"
        )
    )
    if outcome == "timeout":
        call.side_effect = subprocess.TimeoutExpired("probe", 30)
    monkeypatch.setattr(subprocess, "run", call)
    if outcome in {"true", "false"}:
        assert await probe.has_background_workers(configuration) == (outcome == "true")
        assert json.loads(call.call_args.kwargs["env"]["LCL_FASTAPI_OVERRIDES"]) == {}
    else:
        with pytest.raises(RuntimeError, match="probe"):
            await probe.has_background_workers(configuration)


def test_controller_deadline_records_failure_before_kill(
    configuration: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import lcl_fastapi.runtime.controller as controller

    settings = asyncio.run(load_settings(configuration))
    identity = process_identity() | {"service_id": "test"}
    journal = BackgroundJournal(settings.state_dir, identity)
    journal.emit(logging.INFO, "background stopping", deadline=0)
    journal.flush()
    killed: list[dict[str, object]] = []
    monkeypatch.setattr(controller, "terminate_background_process", killed.append)
    with controller_logging(configuration, settings, identity):
        controller_background_tick()
        assert killed == [
            identity
            | {
                "level": logging.INFO,
                "message": "background stopping",
                "deadline": 0,
                "sequence": 1,
            }
        ]
    assert "background shutdown timeout" in "".join(
        p.read_text() for p in (configuration.parent / "logs").glob("*controller*")
    )
    writer = ControllerWriter(LoggerHandlerConfig(file={}))
    with pytest.raises(RuntimeError, match="not active"):
        writer.emit_records([])
