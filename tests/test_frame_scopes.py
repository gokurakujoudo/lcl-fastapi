"""Exercise owned configuration scopes through the public helpers."""

import asyncio
from collections.abc import AsyncIterator

import pytest
from lclang.ast import LclConstant
from lclang.errors import LclClosedFrameError
from lclang.runtime import Frame, Module
from lclang.types import ModuleName

from lcl_fastapi import get_config, use_lcl_frame
from lcl_fastapi.context import CONFIG_FRAME


@pytest.fixture
async def runtime_frame() -> AsyncIterator[Frame]:
    async with Frame(Module(ModuleName("runtime"), {}), values={"region": "global"}) as frame:
        token = CONFIG_FRAME.set(frame)
        try:
            yield frame
        finally:
            CONFIG_FRAME.reset(token)


async def test_scope_requires_runtime() -> None:
    with pytest.raises(RuntimeError, match="active worker"):
        async with use_lcl_frame():
            pytest.fail("scope must not open")


async def test_nested_scopes_restore_and_close(runtime_frame: Frame) -> None:
    module = Module(ModuleName("request"), {"answer": LclConstant(value=42)})
    async with use_lcl_frame(module, values={"region": "local"}) as child:
        assert child.parent is runtime_frame
        assert await get_config("answer") == 42
        assert await get_config("region") == "local"
        async with use_lcl_frame() as nested:
            nested.mixin({"region": "nested"})
            assert await get_config("region") == "nested"
        assert await get_config("region") == "local"
    assert await get_config("region") == "global"
    with pytest.raises(LclClosedFrameError):
        await child.get("region")


async def test_exception_and_cancel_restore(runtime_frame: Frame) -> None:
    with pytest.raises(ValueError, match="scope failure"):
        async with use_lcl_frame(values={"region": "failure"}):
            raise ValueError("scope failure")
    assert await get_config("region") == "global"
    opened = asyncio.Event()
    children: list[Frame] = []

    async def cancelled_scope() -> None:
        try:
            async with use_lcl_frame() as child:
                children.append(child)
                opened.set()
                await asyncio.Event().wait()
        finally:
            assert CONFIG_FRAME.get() is runtime_frame

    task = asyncio.create_task(cancelled_scope())
    await opened.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(LclClosedFrameError):
        await children[0].get("region")


async def test_concurrent_scopes_are_isolated(runtime_frame: Frame) -> None:
    barrier = asyncio.Barrier(20)

    async def query(index: int) -> object:
        async with use_lcl_frame(values={"region": index}):
            await barrier.wait()
            return await get_config("region")

    assert await asyncio.gather(*(query(index) for index in range(20))) == list(range(20))
    assert await runtime_frame.get("region") == "global"


async def test_parent_definition_cache_is_native(runtime_frame: Frame) -> None:
    module = Module(ModuleName("parent"), {"answer": LclConstant(value=[1, 2])})
    async with use_lcl_frame(module) as parent:
        original = await parent.get("answer")
        async with use_lcl_frame(values={"region": "child"}):
            assert await get_config("answer") is original
        assert await parent.get("answer") is original
