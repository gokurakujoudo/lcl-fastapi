"""Verify error ownership through the real CLI and native ASGI server."""

import asyncio
import json
import os
import re
import subprocess
import sys
import sysconfig
from contextlib import suppress
from pathlib import Path

import httpx
import psutil
import pytest
from coverage import Coverage
from test_background_process import wait_ready
from test_runtime_process import available_port

from lcl_fastapi.runtime.common import stop

APP = """import asyncio
import json
import logging
import os
from pathlib import Path
from starlette.responses import JSONResponse
from lcl_fastapi import LclFastAPI, default_uncaught_exception_handler, get_logger

class BusinessConflict(Exception):
    pass

# Observe the public ASGI exit without intercepting native server logging.
class ObservedService(LclFastAPI):
    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        except Exception as error:
            if scope["type"] == "http":
                state = scope["state"]
                observation = {
                    "path": scope["path"], "request_id": state["request_id"],
                    "type": type(error).__name__, "message": str(error),
                    "same_error": error is state.get("callback_error"),
                }
                with Path(__file__).with_suffix(".jsonl").open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(observation) + "\\n")
            raise

def callback(error, request, *, context):
    request.state.callback_error = error
    mode = request.path_params["mode"]
    case_id = request.path_params["case_id"]
    if mode == "default":
        return default_uncaught_exception_handler(error, request, context=context)
    if mode == "raise":
        raise RuntimeError("callback:" + case_id)
    if mode == "invalid":
        return None
    context.logger.log_event(
        "custom callback selected", case_id=case_id, exception_type=type(error).__name__,
    )
    return JSONResponse(
        {"detail": "Custom error", "request_id": context.request_id}, status_code=500,
    )

async def async_callback(error, request, *, context):
    request.state.callback_error = error
    await asyncio.sleep(0)
    if request.path_params["mode"] == "default":
        return await default_uncaught_exception_handler(error, request, context=context)
    return callback(error, request, context=context)

service = ObservedService(uncaught_exception_handler=(
    callback if os.environ["LCL_TEST_CALLBACK_STYLE"] == "sync" else async_callback
))

@service.exception_handler(BusinessConflict)
async def conflict(request, error):
    logger = await get_logger()
    logger.log_event("business conflict handled", case_id=request.path_params["case_id"])
    return JSONResponse({"detail": "Expected conflict"}, status_code=409)

@service.get("/probe")
async def probe():
    return {"pid": os.getpid()}

@service.get("/failure/{mode}/{case_id}")
async def failure(mode: str, case_id: str):
    await asyncio.sleep(0)
    if mode == "handled":
        raise BusinessConflict("expected:" + case_id)
    raise RuntimeError("original:" + mode + ":" + case_id)

@service.get("/independent")
async def independent():
    error = RuntimeError("independent-runtime-marker")
    logging.getLogger("uvicorn.error" if os.name == "nt" else "gunicorn.error").error(
        "independent runtime report", exc_info=(type(error), error, None),
    )
    return {"ok": True}
"""

CONFIG = """__LCL_VERSION__: 1
app.name: "error-process-probe"
app.version: "1.0.0"
app.target: "error_app:service"
server.workers: 1
server.port: {port}
server.graceful_timeout_seconds: 10
health.sample_interval_seconds: 0.05
docs.enabled: False
logger.console.enabled: True
"""


def log_records(text: str) -> list[str]:
    return [
        part
        for part in re.split(r"(?m)(?=^\d{4}-\d{2}-\d{2}T)", text)
        if part and " | " in part.splitlines()[0]
    ]


async def mixed_requests(port: int) -> list[tuple[str, str, httpx.Response]]:
    cases = [
        (mode, str(index))
        for index in range(4)
        for mode in (
            "handled",
            "custom",
            "default",
            "raise",
            "invalid",
        )
    ]
    async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=10) as client:
        replies = await asyncio.gather(
            *(client.get(f"/failure/{mode}/{case_id}") for mode, case_id in cases)
        )
        independent = await client.get("/independent")
    return [
        (mode, case_id, response) for (mode, case_id), response in zip(cases, replies, strict=True)
    ] + [("independent", "", independent)]


def verify_propagation(text: str, replies: list[tuple[str, str, httpx.Response]]) -> None:
    observations = [json.loads(line) for line in text.splitlines()]
    expected = {
        response.request.url.path: {
            "path": response.request.url.path,
            "request_id": response.headers["X-Request-ID"],
            "type": "RuntimeError",
            "message": f"original:{mode}:{case_id}",
            "same_error": True,
        }
        for mode, case_id, response in replies
        if mode not in {"handled", "independent"}
    }
    assert len(observations) == len(expected)
    assert {observation["path"]: observation for observation in observations} == expected


def verify_reports(text: str, replies: list[tuple[str, str, httpx.Response]]) -> None:
    records = log_records(text)
    identifiers = {response.headers["X-Request-ID"] for _, _, response in replies}
    assert len(identifiers) == len(replies)
    for mode, case_id, response in replies:
        identifier = response.headers["X-Request-ID"]
        assert identifier.isdecimal()
        selected = [record for record in records if identifier in record.splitlines()[0]]
        starts = [record for record in selected if "REST API Started:" in record.splitlines()[0]]
        completions = [
            record for record in selected if "REST API Completed(" in record.splitlines()[0]
        ]
        assert len(starts) == len(completions) == 1
        assert (
            response.request.url.path in starts[0] and response.request.url.path in completions[0]
        )
        assert f"Completed({response.status_code})" in completions[0]
        if mode == "independent":
            assert response.status_code == 200 and response.json() == {"ok": True}
            assert sum("independent-runtime-marker" in record for record in selected) == 1
            continue
        original = f"original:{mode}:{case_id}"
        final_reports = [
            record for record in selected if "Uncaught Exception in HTTP" in record.splitlines()[0]
        ]
        if mode == "handled":
            assert response.status_code == 409 and response.json() == {
                "detail": "Expected conflict"
            }
            assert not final_reports and not any(
                f"expected:{case_id}" in record for record in selected
            )
            assert sum("business conflict handled" in record for record in selected) == 1
            continue
        assert response.status_code == 500 and original not in response.text
        original_reports = [
            record
            for record in records
            if original in record
            and "uncaught_exception_handler failed:" not in record.splitlines()[0]
        ]
        if mode == "custom":
            assert response.json() == {"detail": "Custom error", "request_id": identifier}
            assert not final_reports
            custom = [record for record in selected if "custom callback selected" in record]
            assert len(custom) == 1 and "RuntimeError" in custom[0]
            assert re.search(r"case_id\s*: '" + case_id + "'", custom[0])
            # Uvicorn reports escaping errors; Gunicorn can omit details once the
            # response is complete. The outer ASGI observation verifies propagation.
            if sys.platform == "win32":
                assert len(original_reports) == 1
            else:
                assert len(original_reports) <= 1
        else:
            assert len(original_reports) == 1
            assert response.json() == {"detail": "Internal Server Error"}
            assert len(final_reports) == 1 and original in final_reports[0]
            failures = [
                record for record in selected if "uncaught_exception_handler failed:" in record
            ]
            assert len(failures) == (0 if mode == "default" else 1)
            if failures:
                assert selected.index(failures[0]) < selected.index(final_reports[0])
                expected = f"callback:{case_id}" if mode == "raise" else "must return a Response"
                assert expected in failures[0] and original in failures[0]


@pytest.mark.integration
@pytest.mark.parametrize("style", ["sync", "async"])
def test_native_callback_responses_preserve_error_propagation_and_report_ownership(
    tmp_path: Path, style: str
) -> None:
    port = available_port()
    config = tmp_path / "service.lclcfg"
    config.write_text(CONFIG.format(port=port), encoding="utf-8")
    (tmp_path / "error_app.py").write_text(APP, encoding="utf-8")
    environment = dict(os.environ, LCL_TEST_CALLBACK_STYLE=style, PYTHONIOENCODING="utf-8")
    environment.pop("PYTHONPATH", None)
    if (coverage := Coverage.current()) is not None:
        environment["COVERAGE_FILE"] = str(Path(coverage.config.data_file).resolve())
    entrypoint = Path(sysconfig.get_path("scripts")) / (
        "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
    )
    output = tmp_path / "console.log"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    with output.open("wb") as stream:
        process = subprocess.Popen(
            [str(entrypoint), "serve", "-c", str(config)],
            cwd=tmp_path,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            wait_ready(port, process)
            replies = asyncio.run(mixed_requests(port))
            asyncio.run(stop(config))
            assert process.wait(timeout=15) == 0
        finally:
            if process.poll() is None:
                for child in psutil.Process(process.pid).children(recursive=True):
                    with suppress(psutil.NoSuchProcess):
                        child.kill()
                process.kill()
                process.wait(timeout=5)
    assert not (tmp_path / "run/runtime.json").exists()
    verify_propagation((tmp_path / "error_app.jsonl").read_text(encoding="utf-8"), replies)
    files = [path for path in (tmp_path / "logs").glob("*.log") if ".controller." not in path.name]
    assert files
    verify_reports("\n".join(path.read_text(encoding="utf-8") for path in files), replies)
    verify_reports(output.read_text(encoding="utf-8"), replies)
