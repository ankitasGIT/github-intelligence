"""JSON-safe conversion shared by every report type the engine produces."""

from dataclasses import asdict, is_dataclass
from datetime import datetime
from typing import Any


def to_jsonable(value: Any) -> Any:
    """Datetimes become ISO-8601 strings, tuples become lists, dataclasses become dicts."""
    if is_dataclass(value) and not isinstance(value, type):
        return to_jsonable(asdict(value))
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [to_jsonable(v) for v in value]
    return value
