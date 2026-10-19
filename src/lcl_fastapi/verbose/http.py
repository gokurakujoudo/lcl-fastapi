"""Token-authenticated operational HTTP access to file-diagnostic targets."""

import secrets
from typing import Annotated

from fastapi import Body, HTTPException, Request

from lcl_fastapi.verbose.control import get_file_verbose, set_file_verbose
from lcl_fastapi.verbose.types import FileVerboseState, FileVerboseStatus


def authorize_verbose(request: Request) -> None:
    """Reuse the current start's local control token without reading request content.

    :param request: Incoming operational request.
    :raises HTTPException: With status 403 if the current token is absent or wrong.
    """
    runtime = getattr(request.app, "worker_identity", None)
    expected = getattr(runtime, "control_token", None)
    supplied = request.headers.get("X-LCL-Control-Token")
    if (
        not isinstance(expected, str)
        or supplied is None
        or not secrets.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))
    ):
        raise HTTPException(status_code=403)


async def inspect_file_verbose(request: Request) -> FileVerboseStatus:
    """Query the target and identity-verified process observations.

    :param request: Token-bearing operational HTTP request.
    :returns: Immutable accepted target and eventually consistent observations.
    :raises HTTPException: With 403 for credentials or 503 for inaccessible control storage.
    """
    authorize_verbose(request)
    try:
        return await get_file_verbose()
    except OSError as error:
        raise HTTPException(status_code=503, detail="File verbose control unavailable") from error


async def update_file_verbose(
    request: Request, enabled: Annotated[bool, Body(embed=True, strict=True)]
) -> FileVerboseState:
    """Accept an idempotent service-wide target without waiting for process synchronization.

    :param request: Token-bearing operational HTTP request.
    :param enabled: Strict JSON Boolean target.
    :returns: Accepted target and revision, serialized with HTTP 202 by the registered route.
    :raises HTTPException: With 403 for credentials or 503 for inaccessible control storage.
    """
    authorize_verbose(request)
    try:
        return await set_file_verbose(enabled)
    except OSError as error:
        raise HTTPException(status_code=503, detail="File verbose control unavailable") from error
