"""Inspect code registrations in a disposable process before choosing API worker count."""

import asyncio
import json
import os
import subprocess
import sys
from contextlib import redirect_stdout
from pathlib import Path

from lcl_fastapi.overrides import OVERRIDES_ENV, current_overrides
from lcl_fastapi.runtime.application import load_application

# Seconds allowed for a trusted application import without entering its lifespan.
PROBE_TIMEOUT = 30


async def has_background_workers(path: Path) -> bool:
    """Import the target in a short-lived process with launch-time CLI overrides.

    :param path: Absolute formal service configuration source.
    :returns: Whether the code-registered application mapping is nonempty.
    :raises RuntimeError: If probing fails, times out, or returns invalid output.
    :raises OSError: If the interpreter cannot be launched.
    """
    environment = os.environ.copy()
    environment["LCL_FASTAPI_CONFIG"] = str(path)
    environment[OVERRIDES_ENV] = json.dumps(current_overrides())
    try:
        result = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, str(Path(__file__))],
            env=environment,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("background registration probe timed out") from error
    if result.returncode:
        raise RuntimeError(f"background registration probe failed:\n{result.stderr}")
    if result.stdout.strip() not in {"true", "false"}:
        raise RuntimeError("background registration probe returned invalid output")
    return result.stdout.strip() == "true"


def main() -> None:
    """Inspect code registration without entering lifespan or retaining parent imports.

    :raises BaseException: Preserve original import diagnostics on stderr and a nonzero exit.
    """
    with redirect_stdout(sys.stderr):
        application = load_application()
        registered = bool(getattr(application, "background_workers", {}))
    print(json.dumps(registered))


if __name__ == "__main__":
    main()
