"""Format local operational results without mixing diagnostics into stdout."""

import json
from collections.abc import Mapping


def format_status(result: Mapping[str, object]) -> str:
    """Format a runtime status snapshot as a JSON object.

    :param result: JSON-compatible runtime status fields.
    :returns: The complete snapshot encoded as JSON.
    """
    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)


def format_logs(result: Mapping[str, object]) -> str:
    """Format active paths with their observation metadata as JSON.

    :param result: Runtime snapshot with paths, observed_at, and stale fields.
    :returns: The complete observation encoded as JSON.
    :raises ValueError: If the runtime does not return a list of textual paths.
    """
    paths = result.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("runtime log snapshot must contain a paths list of strings")
    return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
