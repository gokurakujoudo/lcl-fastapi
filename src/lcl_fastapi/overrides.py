"""Carry raw invocation overrides through fresh configuration loads and workers."""

import json
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar

# Task-local raw CLI values preserve upstream lazy expressions until each worker loads them.
CONFIG_OVERRIDES: ContextVar[dict[str, str | bool] | None] = ContextVar(
    "lcl_fastapi_overrides", default=None
)
# Private process transport is inherited by native spawned and forked workers, never persisted.
OVERRIDES_ENV = "LCL_FASTAPI_OVERRIDES"


def current_overrides() -> dict[str, str | bool]:
    """Read invocation bindings, falling back to the native worker transport.

    :returns: Detached raw strings and valueless Boolean overrides.
    :raises ValueError: If inherited transport is not a raw override mapping.
    """
    selected = CONFIG_OVERRIDES.get()
    if selected is not None:
        return dict(selected)
    loaded = json.loads(os.environ.get(OVERRIDES_ENV, "{}"))
    if not isinstance(loaded, dict) or any(
        not isinstance(key, str) or not isinstance(value, (str, bool))
        for key, value in loaded.items()
    ):
        raise ValueError("invalid internal configuration override transport")
    return loaded


@contextmanager
def override_scope(overrides: Mapping[str, str | bool]) -> Iterator[None]:
    """Bind overrides to one synchronous entrance and its async descendants.

    :param overrides: Native raw override values, copied before use.
    :returns: Context manager restoring the caller's previous bindings on exit.
    """
    token = CONFIG_OVERRIDES.set(dict(overrides))
    try:
        yield
    finally:
        CONFIG_OVERRIDES.reset(token)
