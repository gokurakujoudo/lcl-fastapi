"""Verify native using, lazy precedence, and process override isolation."""

import asyncio
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from lclang.config.errors import LclConfigCycleError

from lcl_fastapi.config import load_settings
from lcl_fastapi.overrides import OVERRIDES_ENV, current_overrides, override_scope
from lcl_fastapi.sources import DEFAULT_CONFIG, configuration_frame

CONFIG = """__LCL_VERSION__: 1
using f"{lcl_fastapi_defaults}"
using "./shared/team.lclcfg"
app.name: "layers"
app.version: "1"
app.target: "app:service"
server.port: 8100
business.result: server.port + business.increment
logger.console.enabled: False
"""


async def test_using_precedence_and_fresh_worker_evaluation() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        (root / "shared").mkdir()
        team = root / "shared/team.lclcfg"
        team.write_text("server.port: 8000\nbusiness.increment: 1\n", encoding="utf-8")
        source = root / "service.lclcfg"
        source.write_text(CONFIG, encoding="utf-8")
        assert (await load_settings(source)).port == 8100
        with override_scope({"server.port": "LCL[9000]", "business.secret!": "private"}):
            async with configuration_frame(source, 123) as frame:
                assert await frame.get("business.result") == 9001
                assert await frame.get("logger.file.service.filename") == "layers.123.log"
                assert await frame.get("lcl_fastapi_defaults") == str(DEFAULT_CONFIG)
                assert frame.is_masked("business.secret")
            team.write_text("business.increment: 2\n", encoding="utf-8")
            async with configuration_frame(source, 456) as frame:
                assert await frame.get("business.result") == 9002
                assert await frame.get("logger.file.service.filename") == "layers.456.log"
            expected_state = await asyncio.to_thread((root / "run").resolve)
            assert (await load_settings(source)).state_dir == expected_state
        assert source.read_text(encoding="utf-8") == CONFIG
        assert current_overrides() == {}


async def test_dynamic_using_selects_cli_target_and_detects_cycles(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text('selected: "absent"\nusing f"{selected}.lclcfg"\n', encoding="utf-8")
    (tmp_path / "team.lclcfg").write_text("business.value: 42\n", encoding="utf-8")
    with override_scope({"selected": "team"}):
        async with configuration_frame(source) as frame:
            assert await frame.get("business.value") == 42
    (tmp_path / "cycle.lclcfg").write_text('using "service.lclcfg"\n', encoding="utf-8")
    with override_scope({"selected": "cycle"}), pytest.raises(LclConfigCycleError, match="cycle"):
        async with configuration_frame(source):
            pytest.fail("cyclic imports were accepted")


@pytest.mark.parametrize("name", ["worker_pid", "lcl_fastapi_defaults"])
async def test_reserved_names_cannot_be_overridden(tmp_path: Path, name: str) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(f'{name}: "invalid"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="provided by the framework"):
        async with configuration_frame(source):
            pytest.fail("reserved source binding accepted")
    with override_scope({name: "invalid"}), pytest.raises(ValueError, match="provided"):
        async with configuration_frame(source):
            pytest.fail("reserved override accepted")


@pytest.mark.parametrize("payload", ["[]", '{"key": 1}', '{"key": null}', "invalid"])
def test_invalid_worker_transport_is_rejected(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    monkeypatch.setenv(OVERRIDES_ENV, payload)
    with pytest.raises(ValueError):
        current_overrides()


async def test_task_scopes_and_inherited_transport_restore_after_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(OVERRIDES_ENV, json.dumps({"business.value": "inherited"}))

    async def selected(value: str) -> str | bool:
        with override_scope({"business.value": value}):
            await asyncio.sleep(0)
            return current_overrides()["business.value"]

    assert list(await asyncio.gather(selected("one"), selected("two"))) == ["one", "two"]
    with pytest.raises(RuntimeError), override_scope({}):
        assert current_overrides() == {}
        raise RuntimeError("scope failure")
    assert current_overrides() == {"business.value": "inherited"}


async def test_role_filter_preserves_masks_only_for_retained_definitions(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(
        'app.name: "masked"\nlogger.file.controller.filename!: "control.log"\n'
        'logger.file.service.filename!: f"worker.{worker_pid}.log"\n',
        encoding="utf-8",
    )
    async with configuration_frame(source, logger_role="controller") as frame:
        assert frame.is_masked("logger.file.controller.filename")
        assert await frame.get("logger.file.controller.filename") == "control.log"
    async with configuration_frame(source, 123, logger_role="worker") as frame:
        assert frame.is_masked("logger.file.service.filename")
        assert await frame.get("logger.file.service.filename") == "worker.123.log"


async def test_operational_json_default_does_not_shadow_native_json_import(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text('business.payload: json.encode({"value": 1})\n', encoding="utf-8")
    async with configuration_frame(source) as frame:
        assert json.loads(str(await frame.get("business.payload"))) == {"value": 1}
