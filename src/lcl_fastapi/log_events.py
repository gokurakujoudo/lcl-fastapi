"""Explicit business event selection with pre-read masking and native formatting."""

from collections.abc import Iterable, Mapping
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass

from lclang.utils import make_multi_log_lines, make_repr_lines, safe_repr


def event_message(
    event: str,
    record: object | None,
    fields: Iterable[str],
    masked_fields: Iterable[str],
    values: Mapping[str, object],
) -> str:
    """Select direct dataclass fields before native bounded value representation.

    :param event: Business event title, escaped and bounded by native safe_repr.
    :param record: Optional dataclass instance; whole records are never expanded.
    :param fields: Ordered direct field names to select.
    :param masked_fields: Names replaced before accessing their values.
    :param values: Additional values, retained in keyword order.
    :returns: One multiline message with four-space-indented aligned field lines.
    :raises TypeError: If record is not a dataclass instance or event is not text.
    :raises ValueError: If selected fields are unknown, repeated or conflict with values.
    :raises Exception: If an unmasked selected getter or input iterator fails.
    """
    if not isinstance(event, str):
        raise TypeError("event must be text")
    if record is not None and (not is_dataclass(record) or isinstance(record, type)):
        raise TypeError("event record must be a dataclass instance")
    selected, masks = tuple(fields), tuple(masked_fields)
    masked = frozenset(masks)
    declared = set() if record is None else {item.name for item in dataclass_fields(record)}
    if (
        set(selected) - declared
        or len(set(selected)) != len(selected)
        or set(selected) & values.keys()
        or len(masked) != len(masks)
        or masked - (set(selected) | values.keys())
    ):
        raise ValueError("event fields and masks must be known, unique and without conflicts")
    projected = {name: None if name in masked else getattr(record, name) for name in selected}
    projected.update(values)
    return make_multi_log_lines(
        safe_repr(event, renderer=str), make_repr_lines(projected, masked_keys=masked)
    )
