"""Serve Swagger HTML and assets exclusively from this application."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.responses import HTMLResponse, JSONResponse
from starlette.staticfiles import StaticFiles

from lcl_fastapi.config import Settings


def swagger_page(settings: Settings) -> HTMLResponse:
    """Produce offline Swagger HTML without a remote validator or favicon.

    :param settings: Validated application documentation settings.
    :returns: HTML referring only to local static and OpenAPI endpoints.
    """
    return get_swagger_ui_html(
        openapi_url=settings.openapi_path,
        title=f"{settings.app_name} - Swagger UI",
        swagger_js_url="/_lcl/static/swagger/swagger-ui-bundle.js",
        swagger_css_url="/_lcl/static/swagger/swagger-ui.css",
        swagger_favicon_url="/_lcl/static/swagger/favicon-32x32.png",
        swagger_ui_parameters={"validatorUrl": None},
    )


def register_documentation(app: FastAPI, settings: Settings) -> None:
    """Add fallback docs routes while preserving exact business overrides.

    :param app: Application whose existing routes take precedence.
    :param settings: Worker-resolved documentation paths and enable flag.
    """
    if not settings.docs_enabled:
        return

    async def docs_page() -> HTMLResponse:
        """Render the configured Swagger page.

        :returns: Locally served Swagger HTML.
        """
        return swagger_page(settings)

    async def schema_page() -> JSONResponse:
        """Render the current application schema.

        :returns: OpenAPI schema without the private shutdown endpoint.
        """
        return JSONResponse(app.openapi())

    for path, endpoint in ((settings.docs_path, docs_page), (settings.openapi_path, schema_page)):
        if not any(
            getattr(route, "path", None) == path and "GET" in getattr(route, "methods", set())
            for route in app.routes
        ):
            app.add_api_route(path, endpoint, methods=["GET"], include_in_schema=False)
    app.mount(
        "/_lcl/static/swagger",
        StaticFiles(directory=Path(__file__).parent.parent / "static" / "swagger"),
        name="lcl-swagger-static",
    )
