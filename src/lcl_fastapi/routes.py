"""Register internal HTTP routes with the established business override rule."""

from collections.abc import Callable
from typing import Any

from fastapi import FastAPI

from lcl_fastapi.config import Settings
from lcl_fastapi.verbose.http import inspect_file_verbose, update_file_verbose


def register_internal_routes(
    app: FastAPI, settings: Settings, health: Callable[..., Any], shutdown: Callable[..., Any]
) -> None:
    """Add enabled internal routes only when no exact business method/path owns them.

    :param app: Application whose routes are restored at lifespan exit.
    :param settings: Validated route settings.
    :param health: Application-owned health endpoint.
    :param shutdown: Application-owned authenticated shutdown endpoint.
    """
    for path, method, endpoint, enabled in (
        (settings.health_path, "GET", health, settings.health_enabled),
        ("/_lcl/shutdown", "POST", shutdown, True),
        ("/_lcl/logging/verbose", "GET", inspect_file_verbose, True),
        ("/_lcl/logging/verbose", "PUT", update_file_verbose, True),
    ):
        if enabled and not any(
            getattr(route, "path", None) == path and method in getattr(route, "methods", set())
            for route in app.routes
        ):
            app.add_api_route(
                path,
                endpoint,
                methods=[method],
                include_in_schema=False,
                status_code=202 if endpoint is update_file_verbose else None,
            )
