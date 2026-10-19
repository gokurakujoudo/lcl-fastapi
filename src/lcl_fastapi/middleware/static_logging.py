"""Identify native static mounts without executing their applications or opening files."""

from collections.abc import Sequence

from starlette.routing import BaseRoute, Host, Match, Mount, Router
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


def matches_static(routes: Sequence[BaseRoute], scope: Scope) -> bool:
    """Inspect the first full native match, descending through mounted route tables.

    :param routes: Current ordered routes, including lifespan-registered defaults.
    :param scope: Borrowed classification scope; child updates use a new mapping.
    :returns: Whether the selected native endpoint is a StaticFiles instance.
    :raises Exception: Propagates matcher failures to the classification fallback.
    :raises BaseException: Propagates matcher cancellation and process-control signals.
    """
    for route in routes:
        match, child = route.matches(scope)
        if match == Match.FULL:
            if isinstance(child.get("endpoint"), StaticFiles):
                return True
            if isinstance(route, (Mount, Host)):
                return matches_static(route.routes, {**scope, **child})
            return False
    return False


def is_static_get(router: Router, scope: Scope) -> bool:
    """Classify a GET conservatively while leaving real dispatch and failures untouched.

    :param router: Live native router whose ordered routes are read on each request.
    :param scope: Incoming HTTP scope, copied before route inspection.
    :returns: True for a selected static GET; False for other or failed classifications.
    :raises BaseException: Propagates matcher cancellation and process-control signals.
    """
    if scope["method"] != "GET":
        return False
    try:
        return matches_static(router.routes, dict(scope))
    except Exception:
        return False
