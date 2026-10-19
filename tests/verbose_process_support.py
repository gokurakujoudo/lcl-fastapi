"""Shared native-process acceptance for service-wide diagnostic observations."""

import time
from pathlib import Path
from typing import Any

import httpx


def wait_file_verbose(
    port: int, directory: Path, enabled: bool, revision: int, api_workers: int
) -> None:
    token = (directory / "run/control.token").read_text(encoding="ascii")
    deadline = time.monotonic() + 20
    status: dict[str, Any] = {}
    with httpx.Client(timeout=2, headers={"Connection": "close"}) as client:
        while time.monotonic() < deadline:
            reply = client.get(
                f"http://127.0.0.1:{port}/_lcl/logging/verbose",
                headers={"X-LCL-Control-Token": token},
            )
            assert reply.status_code == 200
            status = reply.json()
            observations = status["processes"]
            roles = [item["role"] for item in observations]
            if (
                status["enabled"] is enabled
                and status["revision"] == revision
                and roles.count("api") == api_workers
                and roles.count("controller") == 1
                and all(
                    item["enabled"] is enabled and item["revision"] == revision
                    for item in observations
                )
            ):
                return
            time.sleep(0.05)
    raise AssertionError(f"file verbose target was not observed by every live process: {status}")


def submit_file_verbose(port: int, directory: Path, enabled: bool, api_workers: int) -> int:
    token = (directory / "run/control.token").read_text(encoding="ascii")
    with httpx.Client(timeout=2) as client:
        response = client.put(
            f"http://127.0.0.1:{port}/_lcl/logging/verbose",
            headers={"X-LCL-Control-Token": token},
            json={"enabled": enabled},
        )
    assert response.status_code == 202
    target = response.json()
    assert target["enabled"] is enabled
    revision = int(target["revision"])
    wait_file_verbose(port, directory, enabled, revision, api_workers)
    return revision
