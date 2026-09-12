"""Exercise the public application with real LCL and upstream logging resources."""

import asyncio
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

import httpx
import lclang.utils.snowflake as upstream_snowflake
import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message, Scope

import lcl_fastapi.service as service_module
from lcl_fastapi import LclFastAPI, get_config, get_logger, get_request_context
from lcl_fastapi.config import Settings, configuration_frame, load_settings, origin_value
from lcl_fastapi.context import CONFIG_FRAME, REQUEST_CONTEXT, RequestContext
from lcl_fastapi.runtime.common import WorkerRuntime

BASE_CONFIG = """__LCL_VERSION__: 1
app.name: "test-service"
app.version: "1.0.0"
app.target: "example:service"
business.value: "configured"
logger.console.enabled: False
docs.enabled: False
"""


class ObservedRuntime:
    directory: Path
    identity: dict[str, object]
    service: dict[str, object]
    worker_id = 3
    control_token = "test-secret"

    def __init__(self) -> None:
        self.observations: list[tuple[list[str], float]] = []
        self.stopped = False

    def service_info(self) -> dict[str, object]:
        return {"runtime": "test-fixture", "service_pid": 1, "worker_pid": 2}

    def publish(self, paths: list[str], observed_at: float) -> None:
        self.observations.append((paths, observed_at))

    def request_shutdown(self) -> None:
        self.stopped = True


@pytest.fixture
def configuration(request: pytest.FixtureRequest) -> Iterator[Path]:
    with TemporaryDirectory() as directory:
        path = Path(directory) / "service.lclcfg"
        suffix = cast(str, getattr(request, "param", ""))
        path.write_text(BASE_CONFIG + suffix, encoding="utf-8")
        yield path


@pytest.fixture
def observed_runtime(monkeypatch: pytest.MonkeyPatch) -> ObservedRuntime:
    runtime = ObservedRuntime()

    @contextmanager
    def worker_scope(settings: Settings) -> Iterator[WorkerRuntime]:
        assert settings.app_name == "test-service"
        yield cast(WorkerRuntime, runtime)

    monkeypatch.setattr(service_module, "worker_runtime", worker_scope)
    return runtime


async def test_formal_configuration_defaults_and_worker_scope(configuration: Path) -> None:
    settings = await load_settings(configuration)
    assert settings.host == "127.0.0.1"
    parent = configuration.parent.resolve()
    assert settings.state_dir == parent / "run"
    assert settings.disk_paths == (parent, parent / "logs")
    async with configuration_frame(configuration) as frame:
        assert await frame.get("worker_pid", fallback=None) is None
    async with configuration_frame(configuration, 123) as frame:
        assert await frame.get("logger.file.service.filename") == "test-service.123.log"
    with pytest.raises(RuntimeError, match="active worker"):
        await get_config("business.value")


@pytest.mark.parametrize("origin", ["", "http://example.test", "https://example.test:8443"])
def test_valid_origin(origin: str) -> None:
    assert origin_value(origin) == origin


@pytest.mark.parametrize(
    "origin",
    [
        "example.test",
        "https://example.test/path",
        "https://user@example.test",
        "https://example.test?x=1",
        "https://example.test#x",
        "ftp://example.test",
        "https://example.test:0",
        "https://example.test:99999",
        "https://example.test?",
        "https://example.test#",
        "https://example.test:",
    ],
)
def test_invalid_origin(origin: str) -> None:
    with pytest.raises(ValueError):
        origin_value(origin)


def test_lifespan_configuration_request_ids_and_logs(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def business(app: FastAPI) -> AsyncIterator[None]:
        assert app is service
        assert await get_config("business.value") == "configured"
        events.append("startup")
        yield
        logger = await get_logger("business")
        logger.info("business teardown flushed")
        events.append("shutdown")

    service = LclFastAPI(config_path=configuration, lifespan=business)

    @service.get("/hello")
    async def hello(request: Request) -> dict[str, object]:
        context = get_request_context()
        assert context is not None
        assert context.request_id == request.state.request_id
        logger = await get_logger("business")
        logger.info("business request")
        return {"request_id": context.request_id, "value": await get_config("business.value")}

    @service.get("/broken")
    async def broken() -> None:
        raise ValueError("expected failure")

    with TestClient(service, raise_server_exceptions=False) as client:
        assert logging.getLogger("uvicorn.access").disabled
        assert logging.getLogger("gunicorn.access").disabled
        first = client.get("/hello", headers={"X-Request-ID": "client-supplied"})
        second = client.get("/hello")
        assert first.status_code == 200
        assert first.json()["request_id"] == first.headers["X-Request-ID"]
        assert first.json()["value"] == "configured"
        assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]
        assert first.headers["X-Request-ID"] != "client-supplied"
        failure = client.get("/broken")
        assert failure.status_code == 500 and failure.headers["X-Request-ID"].isdigit()
        assert client.get("/health").json()["service"]["runtime"] == "test-fixture"
        assert client.post("/_lcl/shutdown").status_code == 403
        assert not observed_runtime.stopped
        assert (
            client.post(
                "/_lcl/shutdown",
                headers={"X-LCL-Control-Token": "test-secret"},
            ).status_code
            == 202
        )
        assert observed_runtime.stopped
    assert events == ["startup", "shutdown"]
    assert get_request_context() is None
    logs = "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.rglob("*.log")
    )
    assert "business teardown flushed" in logs
    assert f"request_id={first.headers['X-Request-ID']}" in logs
    assert "method=GET path=/broken status_code=500" in logs


def test_business_routes_override_builtins(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    service = LclFastAPI(config_path=configuration)
    router = APIRouter()

    @router.get("/health")
    async def business_health() -> dict[str, str]:
        return {"owner": "business"}

    @service.post("/_lcl/shutdown")
    async def business_shutdown() -> dict[str, str]:
        return {"owner": "business"}

    service.include_router(router)
    with TestClient(service) as client:
        assert client.get("/health").json() == {"owner": "business"}
        assert client.post("/_lcl/shutdown").json() == {"owner": "business"}
        assert not observed_runtime.stopped


def test_constructor_preserves_lazy_initialization() -> None:
    service = LclFastAPI()
    assert service.worker_requests is None
    assert service.root_path == ""
    with pytest.raises(ValueError, match="URL settings"):
        LclFastAPI(root_path="/unsupported")
    with pytest.raises(RuntimeError, match="start with"):
        service.current_request_runtime()
    with pytest.raises(RuntimeError, match="initialized worker"):
        asyncio.run(service.health())


def test_offline_swagger_assets_and_schema(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    configuration.write_text(BASE_CONFIG + "docs.enabled: True\n", encoding="utf-8")
    service = LclFastAPI(config_path=configuration)

    @service.get("/hello")
    async def hello() -> dict[str, str]:
        return {"message": "local"}

    with TestClient(service) as client:
        page = client.get("/docs")
        assert page.status_code == 200
        assert '"validatorUrl": null' in page.text
        assert "https://" not in page.text and "http://" not in page.text
        for asset in ("swagger-ui-bundle.js", "swagger-ui.css", "favicon-32x32.png"):
            response = client.get(f"/_lcl/static/swagger/{asset}")
            assert response.status_code == 200 and response.content
        schema = client.get("/openapi.json").json()
        assert "/hello" in schema["paths"]
        assert "/_lcl/shutdown" not in schema["paths"]
        assert client.get("/hello").json() == {"message": "local"}
        assert observed_runtime.observations


async def test_concurrent_http_requests_are_isolated(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    service = LclFastAPI(config_path=configuration)

    @service.get("/concurrent/{value}")
    async def concurrent(value: str) -> dict[str, str]:
        before = get_request_context()
        assert before is not None
        await asyncio.sleep(0)
        assert before is get_request_context()
        assert await get_config("business.value") == "configured"
        return {"id": before.request_id, "value": value}

    async with service.router.lifespan_context(service):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service),
            base_url="http://local.test",
        ) as client:
            responses = await asyncio.gather(
                *(client.get(f"/concurrent/{index}") for index in range(100))
            )
        assert len({response.headers["X-Request-ID"] for response in responses}) == 100
        assert [response.json()["value"] for response in responses] == [str(i) for i in range(100)]
    assert get_request_context() is None
    assert observed_runtime.observations


def test_business_startup_failure_cleans_worker_resources(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    @asynccontextmanager
    async def broken_startup(app: FastAPI) -> AsyncIterator[None]:
        if app.title:
            raise ValueError("business startup failed")
        yield

    service = LclFastAPI(config_path=configuration, lifespan=broken_startup)
    with pytest.raises(ValueError, match="business startup failed"), TestClient(service):
        pass
    assert service.worker_requests is None
    assert service.worker_identity is None
    assert service.health_sampler is not None and service.health_sampler.task is None
    assert observed_runtime.observations
    assert not logging.getLogger("uvicorn.access").disabled


@pytest.mark.parametrize(
    "configuration, field",
    [
        ('app.name: ""\n', "app.name"),
        ("server.workers: True\n", "server.workers"),
        ("worker_pid: 123\n", "worker_pid"),
        ("health.enabled: 1\n", "health.enabled"),
        ('health.path: "relative"\n', "health.path"),
        ('health.path: "//authority"\n', "health.path"),
        ('health.path: "/health?query"\n', "health.path"),
        ('server.host: "::1"\n', "server.host"),
        ("health.sample_interval_seconds: 0\n", "health.sample_interval_seconds"),
        ("health.sample_interval_seconds: True\n", "health.sample_interval_seconds"),
        ('health.sample_interval_seconds: "one"\n', "health.sample_interval_seconds"),
        ('health.sample_interval_seconds: float("nan")\n', "health.sample_interval_seconds"),
        ('health.sample_interval_seconds: float("inf")\n', "health.sample_interval_seconds"),
        ('health.disk_paths: "not-a-list"\n', "health.disk_paths"),
        ('request.id_header: "contains spaces"\n', "request.id_header"),
        ("snowflake.worker_id_base: 1023\nsnowflake.worker_id_count: 2\n", "worker_id_count"),
    ],
    indirect=["configuration"],
)
async def test_configuration_rejects_invalid_values(configuration: Path, field: str) -> None:
    with pytest.raises(ValueError, match=field):
        await load_settings(configuration)


def test_missing_configuration_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LCL_FASTAPI_CONFIG", raising=False)
    with (
        pytest.raises(RuntimeError, match="configuration path is required"),
        TestClient(LclFastAPI()),
    ):
        pass


def test_business_documentation_override(
    configuration: Path,
    observed_runtime: ObservedRuntime,
) -> None:
    configuration.write_text(BASE_CONFIG + "docs.enabled: True\n", encoding="utf-8")
    service = LclFastAPI(config_path=configuration)

    @service.get("/docs")
    async def custom_docs() -> dict[str, str]:
        return {"owner": "business"}

    with TestClient(service) as client:
        assert client.get("/docs").json() == {"owner": "business"}
        assert client.get("/openapi.json").status_code == 200
        assert observed_runtime.observations


@pytest.mark.parametrize("failure", ["rollback", "exhaustion"])
@pytest.mark.parametrize("send_failure", [None, OSError, asyncio.CancelledError])
async def test_expected_generator_failure_returns_503_without_borrowing_context(
    configuration: Path,
    observed_runtime: ObservedRuntime,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    send_failure: type[BaseException] | None,
) -> None:
    service = LclFastAPI(config_path=configuration)
    business_calls: list[str] = []

    @service.get("/failure-probe")
    async def probe() -> dict[str, str]:
        business_calls.append("called")
        return {"status": "ok"}

    async with service.router.lifespan_context(service):
        runtime = service.current_request_runtime()
        clock = runtime.generator.epoch_ms * 1_000_000 + 1_000_000_000
        monkeypatch.setattr(upstream_snowflake, "time_ns", lambda: clock)
        for _ in range(1 if failure == "rollback" else 4096):
            runtime.generator.next_id()
        if failure == "rollback":
            monkeypatch.setattr(upstream_snowflake, "time_ns", lambda: clock - 1_000_000)
        messages: list[Message] = []
        scope: Scope = {
            "type": "http",
            "method": "GET",
            "path": "/failure-probe",
            "headers": [],
            "query_string": b"",
            "state": {"request_id": "previous-request"},
        }
        inherited_context = RequestContext("previous-request", "POST", "/old", 0)
        async with configuration_frame(configuration) as inherited_frame:
            context_token = REQUEST_CONTEXT.set(inherited_context)
            frame_token = CONFIG_FRAME.set(inherited_frame)

            async def receive() -> Message:
                return {"type": "http.request"}

            async def send(message: Message) -> None:
                assert get_request_context() is None
                assert CONFIG_FRAME.get() is runtime.frame
                messages.append(message)
                if send_failure is not None:
                    raise send_failure("response interrupted")

            try:
                if send_failure is None:
                    await service(scope, receive, send)
                else:
                    with pytest.raises(send_failure):
                        await service(scope, receive, send)
                assert get_request_context() is inherited_context
                assert CONFIG_FRAME.get() is inherited_frame
                assert "request_id" not in scope["state"]
            finally:
                CONFIG_FRAME.reset(frame_token)
                REQUEST_CONTEXT.reset(context_token)
        assert messages[0]["status"] == 503
        assert b"x-request-id" not in dict(messages[0]["headers"])
        if send_failure is None:
            assert messages[1]["body"] == b'{"detail":"Request ID generation unavailable"}'
        else:
            assert len(messages) == 1
        assert business_calls == []
        monkeypatch.setattr(upstream_snowflake, "time_ns", lambda: clock + 1_000_000)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=service),
            base_url="http://local.test",
        ) as client:
            recovered = await client.get("/failure-probe")
            assert recovered.status_code == 200
            assert recovered.headers["X-Request-ID"].isdigit()
        assert business_calls == ["called"]
    logs = "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.rglob("*.log")
    )
    failures = [line for line in logs.splitlines() if "status_code=503" in line]
    assert len(failures) == 1
    error_name = "RuntimeError" if failure == "rollback" else "OverflowError"
    assert f"id_generation_error={error_name}" in failures[0]
    assert "request_id=unavailable" in failures[0]
    assert "previous-request" not in logs
    assert observed_runtime.observations
