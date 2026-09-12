"""Observable state and isolation in the two independent tutorial services."""

import asyncio
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import pytest
from directory_service.app import events
from directory_service.events import Inventory
from directory_service.files import save, scan, within
from fastapi import FastAPI, HTTPException
from playground_service import app as playground_app
from playground_service.engine import Engine
from starlette.requests import Request
from starlette.types import Message, Scope


@pytest.mark.asyncio
@pytest.mark.parametrize("version", ["2.3", "2.4"])
async def test_sse_survives_updates_and_releases_disconnected_subscriber(version: str) -> None:
    app = FastAPI()
    inventory = Inventory(Path("."), 100)
    app.state.inventory = inventory
    scope: Scope = {"type": "http", "asgi": {"spec_version": version}, "app": app}
    incoming: asyncio.Queue[Message] = asyncio.Queue()
    outgoing: asyncio.Queue[Message] = asyncio.Queue()
    cancelled_receive = False

    async def receive() -> Message:
        nonlocal cancelled_receive
        try:
            return await incoming.get()
        except asyncio.CancelledError:
            cancelled_receive = True
            raise

    response = await events(Request(scope, receive))
    task = asyncio.create_task(response(scope, receive, outgoing.put))
    try:
        async with asyncio.timeout(2):
            assert (await outgoing.get())["type"] == "http.response.start"
            for revision in range(3):
                body = await outgoing.get()
                assert f"id: {revision}\n".encode() in body["body"]
                assert body["more_body"] and not task.done()
                assert not cancelled_receive
                await inventory.publish({"entries": [revision]})
            await incoming.put({"type": "http.disconnect"})
            await task
        assert not inventory.subscribers
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.parametrize("name", ["../outside", "/absolute", "a/../../b", "C:/file", "a\\b"])
def test_directory_paths_stay_under_root(name: str) -> None:
    with TemporaryDirectory() as directory:
        with pytest.raises(HTTPException) as failure:
            within(Path(directory).resolve(), name)
        assert failure.value.status_code == 400


def test_directory_snapshot_and_non_overwriting_upload() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        save(root, "nested/file.txt", b"hello")
        snapshot = scan(root, 100)
        assert snapshot["entries"] == [
            {
                "path": "nested",
                "kind": "directory",
                "bytes": 0,
                "modified_ns": (root / "nested").stat().st_mtime_ns,
            },
            {
                "path": "nested/file.txt",
                "kind": "file",
                "bytes": 5,
                "modified_ns": (root / "nested/file.txt").stat().st_mtime_ns,
            },
        ]
        assert scan(root, 1)["truncated"] is True
        with pytest.raises(HTTPException) as failure:
            save(root, "nested/file.txt", b"replacement")
        assert failure.value.status_code == 409
        assert (root / "nested/file.txt").read_bytes() == b"hello"
        (root / "nested/file.txt").unlink()
        remaining = scan(root, 100)["entries"]
        assert isinstance(remaining, list) and len(remaining) == 1


@pytest.mark.asyncio
async def test_sse_coalesces_slow_consumers_to_newest_snapshot() -> None:
    inventory = Inventory(Path("."), 100)
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue(maxsize=1)
    inventory.subscribers.add(queue)
    await inventory.publish({"entries": ["first"]})
    await inventory.publish({"entries": ["second"]})
    assert queue.qsize() == 1
    assert (await queue.get())["entries"] == ["second"]
    await inventory.publish({"entries": ["second"]})
    assert queue.empty() and inventory.revision == 2


@pytest.mark.asyncio
async def test_playground_native_ast_dependencies_trace_and_session_isolation() -> None:
    engine = Engine(2, 60)
    try:
        first, second = await asyncio.gather(engine.submit("create"), engine.submit("create"))
        key = first["id"]
        source = (
            "__LCL_VERSION__: 1\nflag: True\nleft: 42\nright: 1 / 0\n"
            "answer: left if flag else right"
        )
        report = await engine.submit("parse", key, source=source, expression="answer")
        assert report["ast"]["answer"]["kind"] == "LclConditional"
        assert report["ast"]["answer"]["span"]["start"]["line"] == 5
        assert any(
            edge["target"] == "right" and edge["kind"] == "conditional"
            for edge in report["dependencies"]
        )
        result = await engine.submit("evaluate", key)
        assert result["result"]["repr"] == "42"
        assert not any("name='right'" in event for event in result["trace"])
        assert any("lcl-evaluated" in event for event in result["trace"])
        again = await engine.submit("evaluate", key)
        assert any("cached" in event for event in again["trace"])
        assert len(again["trace"]) < len(result["trace"])
        with pytest.raises(HTTPException) as failure:
            await engine.submit("evaluate", second["id"])
        assert failure.value.status_code == 409
        malformed = await engine.submit(
            "parse", key, source="__LCL_VERSION__: 1\nx: (", expression="x"
        )
        assert malformed["error"]["span"] is not None
        assert (await engine.submit("evaluate", key))["result"]["repr"] == "42"
        await engine.submit("parse", key, source="__LCL_VERSION__: 1\nx: y\ny: x", expression="x")
        cyclic = await engine.submit("evaluate", key)
        assert "Circular" in cyclic["error"]["type"]
        assert cyclic["trace"]
        await engine.submit("delete", key)
        with pytest.raises(HTTPException):
            await engine.submit("read", key)
    finally:
        await engine.close()
    assert not engine.sessions


@pytest.mark.asyncio
async def test_session_capacity_expiry_and_no_configuration_imports() -> None:
    engine = Engine(1, 0.05)
    try:
        key = (await engine.submit("create"))["id"]
        with pytest.raises(HTTPException) as failure:
            await engine.submit("create")
        assert failure.value.status_code == 429
        result = await engine.submit(
            "parse", key, source='__LCL_VERSION__: 1\nusing "private.lclcfg"', expression="1"
        )
        assert "using is not enabled" in result["error"]["message"]
        await asyncio.sleep(0.07)
        with pytest.raises(HTTPException) as expired:
            await engine.submit("read", key)
        assert expired.value.status_code == 404
        assert (await engine.submit("create"))["id"] != key
    finally:
        await engine.close()


@pytest.mark.asyncio
async def test_playground_rejects_multiple_api_workers(monkeypatch: pytest.MonkeyPatch) -> None:
    async def configuration(key: str) -> Any:
        assert key == "server.workers"
        return 2

    monkeypatch.setattr(playground_app, "get_config", configuration)
    with pytest.raises(ValueError, match="server.workers: 1"):
        async with playground_app.lifespan(FastAPI()):
            pytest.fail("Multi-worker playground started")
