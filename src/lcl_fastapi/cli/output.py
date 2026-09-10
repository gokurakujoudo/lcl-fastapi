"""Format local operational results without mixing diagnostics into stdout."""

import json
from collections.abc import Mapping


def format_status(result: Mapping[str, object], *, as_json: bool) -> str:
    """Format a runtime status snapshot for a person or a JSON consumer.

    :param result: JSON-compatible runtime status fields.
    :param as_json: Whether to emit the complete structured snapshot.
    :returns: JSON or stable, readable field rows.
    """
    if as_json:
        return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
    return "\n".join(
        f"{name.replace('_', ' ').title():<20} {value}" for name, value in result.items()
    )


def format_logs(result: Mapping[str, object], *, as_json: bool) -> str:
    """Format active paths while retaining observation metadata in JSON mode.

    :param result: Runtime snapshot with paths, observed_at, and stale fields.
    :param as_json: Whether to include the complete observation metadata.
    :returns: JSON or one absolute path per line.
    :raises ValueError: If the runtime does not return a list of textual paths.
    """
    paths = result.get("paths")
    if not isinstance(paths, list) or not all(isinstance(path, str) for path in paths):
        raise ValueError("runtime log snapshot must contain a paths list of strings")
    if as_json:
        return json.dumps(dict(result), ensure_ascii=False, sort_keys=True)
    return "\n".join(str(path) for path in paths)
