"""Demonstrate a finite task and a cooperative background resource consumer."""

import asyncio

from lcl_fastapi import BackgroundWorkerContext, get_config, use_lcl_frame


async def inventory_once(context: BackgroundWorkerContext) -> None:
    """Read service resources on their owning loop and log a finite task result.

    :param context: Managed background configuration, logging and service bridge.
    """

    async def read_catalog() -> int:
        """Read application state on the API loop.

        :returns: Number of initialized catalog items.
        """
        return len(context.app.state.catalog)

    size = await asyncio.wrap_future(context.submit_to_service(read_catalog))
    async with use_lcl_frame(values={"inventory.batch": size}):
        assert await get_config("inventory.batch") == size
        async with use_lcl_frame(values={"inventory.batch": 1}):
            assert await get_config("inventory.batch") == 1
        assert await get_config("inventory.batch") == size
    context.logger.info("inventory finite task completed items=%s", size)


def heartbeat(context: BackgroundWorkerContext) -> None:
    """Wait cooperatively on a dedicated thread without blocking the API loop.

    :param context: Managed worker resources and thread-safe stop signal.
    :raises ValueError: If the configured heartbeat interval is not positive.
    """
    interval = context.run(context.get_config("background_worker.heartbeat.schedule.seconds"))
    if not isinstance(interval, (int, float)) or interval <= 0:
        raise ValueError("heartbeat schedule seconds must be positive")
    context.logger.info("background heartbeat started")
    while not context.stop_event.wait(interval):
        context.logger.info("background heartbeat tick")
    context.logger.info("background heartbeat stopped")
