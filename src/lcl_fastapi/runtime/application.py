"""Load the trusted downstream import target independently in each worker."""

import asyncio
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import cast

from starlette.types import ASGIApp

from lcl_fastapi.config import load_settings


def load_application() -> ASGIApp:
    """Read fresh worker configuration and import its declared ASGI target.

    :returns: Downstream ASGI application, without entering its lifespan.
    :raises KeyError: If the framework launch configuration is absent.
    :raises ValueError: If the target does not use module:attribute syntax.
    :raises ImportError: If the trusted downstream module cannot be imported.
    :raises AttributeError: If the named application does not exist.
    :raises TypeError: If the target is not callable.
    """
    config_path = Path(os.environ["LCL_FASTAPI_CONFIG"]).resolve()
    with ThreadPoolExecutor(max_workers=1) as executor:
        settings = executor.submit(
            asyncio.run,
            load_settings(config_path),
        ).result()
    module_name, separator, attribute = settings.app_target.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError("app.target must use module:attribute syntax")
    directory = str(config_path.parent)
    if directory not in sys.path:
        sys.path.insert(0, directory)
    target: object = getattr(importlib.import_module(module_name), attribute)
    if not callable(target):
        raise TypeError("app.target must reference an ASGI application")
    return cast(ASGIApp, target)
