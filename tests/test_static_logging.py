"""Verify mounted static GET summaries without losing request diagnostics."""

import asyncio
from pathlib import Path

import httpx
import lclang.utils.snowflake_id as upstream_snowflake
import pytest
from starlette.requests import Request
from starlette.responses import FileResponse, PlainTextResponse, Response
from starlette.routing import Host, Match, Mount, Route, Router
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import LclFastAPI, set_file_verbose
from lcl_fastapi.log_context import log_metadata
from lcl_fastapi.overrides import override_scope


def content(configuration: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.glob("logs/*.log")
    )


@pytest.mark.parametrize("cli_verbose", [False, True])
async def test_static_statuses_have_one_compact_file_and_console_summary(
    configuration: Path,
    observed_runtime: ObservedRuntime,
    capsys: pytest.CaptureFixture[str],
    cli_verbose: bool,
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write("logger.console.enabled: True\n")
    directory = configuration.parent / "assets"
    directory.mkdir()
    (directory / "app.js").write_text("asset-content", encoding="utf-8")
    (directory / "folder").mkdir()
    (directory / "folder/index.html").write_text("index", encoding="utf-8")
    app = LclFastAPI(config_path=configuration)
    app.mount("/assets", StaticFiles(directory=directory, html=True))
    replies: list[httpx.Response] = []
    with override_scope({"__verbose__": cli_verbose}):
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 65311)),
                base_url="http://local",
            ) as client:
                for enabled in (False, True, False):
                    await set_file_verbose(enabled)
                    first = await client.get(
                        "/assets/app.js?secret=query-marker",
                        headers={"Authorization": "header-marker"},
                    )
                    cached = await client.get(
                        "/assets/app.js", headers={"If-None-Match": first.headers["etag"]}
                    )
                    partial = await client.get("/assets/app.js", headers={"Range": "bytes=0-3"})
                    missing = await client.get("/assets/missing.css")
                    redirect = await client.get("/assets/folder")
                    assert [
                        reply.status_code for reply in (first, cached, partial, missing, redirect)
                    ] == [
                        200,
                        304,
                        206,
                        404,
                        307,
                    ]
                    assert first.text == "asset-content" and partial.text == "asse"
                    replies.extend((first, cached, partial, missing, redirect))
    captured = capsys.readouterr()
    assert len({reply.headers["X-Request-ID"] for reply in replies}) == len(replies)
    for text in (content(configuration), captured.out + captured.err):
        for reply in replies:
            identifier = reply.headers["X-Request-ID"]
            lines = [line for line in text.splitlines() if identifier in line]
            assert len(lines) == 1
            assert lines[0].endswith(
                f"{identifier} | Static Resource({reply.status_code}): "
                f"HTTP GET {reply.request.url.path} from 127.0.0.1:65311"
            )
            assert lines[0].split(" | ")[1].strip() == "INFO"
            assert "duration_ms=" not in lines[0]
        assert "REST API Started:" not in text and "REST API Completed(" not in text
        summaries = "\n".join(line for line in text.splitlines() if "Static Resource(" in line)
        assert "query-marker" not in summaries and "header-marker" not in summaries
        assert str(directory) not in summaries


async def test_routing_precedence_nested_mounts_and_nonstatic_responses(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    file = configuration.parent / "item.txt"
    file.write_text("static item", encoding="utf-8")
    app = LclFastAPI(config_path=configuration)

    @app.get("/assets/business")
    async def business(request: Request) -> PlainTextResponse:
        return PlainTextResponse("business")

    @app.post("/assets/item.txt")
    async def post() -> PlainTextResponse:
        return PlainTextResponse("posted")

    @app.get("/download")
    async def download() -> FileResponse:
        return FileResponse(file)

    class ChildStatic(StaticFiles):
        pass

    static = ChildStatic(directory=configuration.parent)
    nested = Router(routes=[Mount("/files", app=static)])
    app.mount("/nested", nested)
    app.router.routes.append(Host("assets.local", app=nested))
    app.mount("/assets", static)
    app.add_api_route("/assets/shadowed", business)
    app.mount("/ordinary", Router(routes=[Route("/item", business)]))
    static_replies: list[httpx.Response] = []
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            static_replies.extend(
                await asyncio.gather(*(client.get("/nested/files/item.txt") for _ in range(20)))
            )
            static_replies.extend(
                (
                    await client.get("http://assets.local/files/item.txt"),
                    await client.get("/assets/item.txt"),
                    await client.get("/assets/shadowed"),
                )
            )
            ordinary = [
                await client.get("/assets/business"),
                await client.get("/download"),
                await client.get("/ordinary/item"),
                await client.head("/assets/item.txt"),
                await client.post("/assets/missing.txt"),
                await client.get("/outside/missing.txt"),
            ]
    assert [reply.status_code for reply in static_replies] == [200] * 22 + [404]
    assert [reply.status_code for reply in ordinary] == [200, 200, 200, 200, 405, 404]
    assert ordinary[0].text == ordinary[2].text == "business"
    text = content(configuration)
    assert len({reply.headers["X-Request-ID"] for reply in static_replies + ordinary}) == 29
    for reply in static_replies:
        lines = [line for line in text.splitlines() if reply.headers["X-Request-ID"] in line]
        assert len(lines) == 1 and f"Static Resource({reply.status_code})" in lines[0]
    for reply in ordinary:
        lines = [line for line in text.splitlines() if reply.headers["X-Request-ID"] in line]
        assert len(lines) == 2
        assert "REST API Started:" in lines[0]
        assert f"REST API Completed({reply.status_code})" in lines[1]


@pytest.mark.parametrize("custom_format", [False, True])
async def test_lifespan_registered_swagger_assets_and_explicit_format(
    configuration: Path, observed_runtime: ObservedRuntime, custom_format: bool
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write("docs.enabled: True\n")
        if custom_format:
            stream.write('logger.format: "CUSTOM %(message)s"\n')
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            asset = await client.get("/_lcl/static/swagger/favicon-32x32.png")
            ordinary = [await client.get("/docs"), await client.get("/openapi.json")]
    assert asset.status_code == 200 and all(reply.status_code == 200 for reply in ordinary)
    text = content(configuration)
    lines = [line for line in text.splitlines() if asset.headers["X-Request-ID"] in line]
    assert len(lines) == 1 and "Static Resource(200)" in lines[0]
    assert lines[0].startswith("CUSTOM ") is custom_format
    for reply in ordinary:
        assert sum(reply.headers["X-Request-ID"] in line for line in text.splitlines()) == 2


@pytest.mark.parametrize("client,expected", [(None, "unknown"), (("::1", 42), "[::1]:42")])
async def test_static_summary_escapes_path_and_formats_client(
    configuration: Path,
    observed_runtime: ObservedRuntime,
    client: tuple[str, int] | None,
    expected: str,
) -> None:
    class MissingStatic(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            return Response(status_code=404)

    app = LclFastAPI(config_path=configuration)
    app.mount("/assets", MissingStatic(directory=configuration.parent))

    async def incoming(scope: Scope, receive: Receive, send: Send) -> None:
        scope["client"] = client
        await app(scope, receive, send)

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=incoming), base_url="http://local"
        ) as session:
            reply = await session.get("/assets/missing%0Dname.css")
    assert reply.status_code == 404
    lines = [line for line in content(configuration).splitlines() if "Static Resource(" in line]
    assert len(lines) == 1
    assert lines[0].endswith(
        f"Static Resource(404): HTTP GET /assets/missing\\rname.css from {expected}"
    )


async def test_classification_uses_a_separate_scope_and_leaves_dispatch_untouched(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    class ProbeRoute(Route):
        def matches(self, scope: Scope) -> tuple[Match, Scope]:
            assert "probe_marker" not in scope
            scope["probe_marker"] = True
            return super().matches(scope)

    async def endpoint(request: Request) -> PlainTextResponse:
        assert request.scope["probe_marker"] is True
        return PlainTextResponse("dispatched")

    app = LclFastAPI(config_path=configuration)
    app.router.routes.append(ProbeRoute("/probe", endpoint))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            reply = await client.get("/probe")
    assert reply.status_code == 200 and reply.text == "dispatched"
    text = content(configuration)
    assert text.count("REST API Started:") == text.count("REST API Completed(200):") == 1
    assert "Static Resource(" not in text


async def test_static_internal_failure_retains_final_diagnostics(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    class BrokenStatic(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            raise RuntimeError("static-internal-marker")

    app = LclFastAPI(config_path=configuration)
    app.mount("/assets", BrokenStatic(directory=configuration.parent))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://local",
        ) as client:
            reply = await client.get("/assets/broken.txt")
    assert reply.status_code == 500 and reply.json() == {"detail": "Internal Server Error"}
    text = content(configuration)
    assert text.count("Uncaught Exception in HTTP GET /assets/broken.txt") == 1
    assert "static-internal-marker" in text and "Python traceback:" in text
    assert text.count(f"{reply.headers['X-Request-ID']} | Static Resource(500)") == 1
    assert "REST API Started:" not in text and "REST API Completed(" not in text


async def test_matcher_failure_falls_back_to_rest_and_normal_error_boundary(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    class BrokenRoute(Route):
        def matches(self, scope: Scope) -> tuple[Match, Scope]:
            scope["classification_probe"] = True
            raise ValueError("native-matcher-failure")

    app = LclFastAPI(config_path=configuration)

    async def unused() -> PlainTextResponse:
        raise AssertionError("matcher failure must prevent endpoint execution")

    app.router.routes.insert(0, BrokenRoute("/broken", unused))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://local",
        ) as client:
            reply = await client.get("/broken")
    assert reply.status_code == 500
    text = content(configuration)
    assert "native-matcher-failure" in text and text.count("Uncaught Exception in HTTP") == 1
    assert text.count("REST API Started:") == text.count("REST API Completed(500):") == 1
    assert "Static Resource(" not in text


async def test_static_id_failure_keeps_one_503_summary_and_allocation_details(
    configuration: Path, observed_runtime: ObservedRuntime, monkeypatch: pytest.MonkeyPatch
) -> None:
    class UncalledStatic(StaticFiles):
        async def get_response(self, path: str, scope: Scope) -> Response:
            raise AssertionError("allocation failure must prevent static dispatch")

    app = LclFastAPI(config_path=configuration)
    app.mount("/assets", UncalledStatic(directory=configuration.parent))
    async with app.router.lifespan_context(app):
        runtime = app.current_request_runtime()
        monkeypatch.setattr(
            upstream_snowflake, "time_ns", lambda: runtime.generator.epoch_ms * 1_000_000 - 1
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            reply = await client.get("/assets/item.txt")
    assert reply.status_code == 503 and "X-Request-ID" not in reply.headers
    assert reply.json() == {"detail": "Request ID generation unavailable"}
    text = content(configuration)
    lines = [line for line in text.splitlines() if "Static Resource(" in line]
    assert len(lines) == 1 and "unavailable | Static Resource(503)" in lines[0]
    assert "id_generation_error=LclValidationError" in lines[0]
    assert "id_generation_code=" in lines[0]
    assert "REST API Started:" not in text and "REST API Completed(" not in text


async def test_cancelled_classification_restores_the_execution_metadata(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    class CancelledRoute(Route):
        def matches(self, scope: Scope) -> tuple[Match, Scope]:
            raise asyncio.CancelledError("classification cancellation")

    async def unused(request: Request) -> PlainTextResponse:
        raise AssertionError("cancelled routing must not execute a handler")

    app = LclFastAPI(config_path=configuration)
    app.router.routes.insert(0, CancelledRoute("/cancel", unused))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            with pytest.raises(asyncio.CancelledError, match="classification cancellation"):
                await client.get("/cancel")
        assert log_metadata() == {}
