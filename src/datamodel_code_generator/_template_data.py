"""Copy JSON-like template data into one generation request."""

from __future__ import annotations

from collections import defaultdict
from typing import Any


def copy_extra_template_data(data: defaultdict[str, dict[str, Any]]) -> defaultdict[str, dict[str, Any]]:
    """Detach model template dictionaries with one memo for the parser lifetime."""
    memo: dict[int, Any] = {}
    factory = data.default_factory if isinstance(data, defaultdict) else dict
    detached: defaultdict[str, dict[str, Any]] = defaultdict(factory)
    memo[id(data)] = detached
    detached.update((key, copy_template_data(value, memo)) for key, value in data.items())
    return detached


def copy_template_data(value: Any, memo: dict[int, Any]) -> Any:
    """Copy mutable built-in containers without invoking user copy hooks."""
    value_id = id(value)
    if value_id in memo:
        return memo[value_id]
    detached: Any = None
    match value:
        case dict() if type(value) is dict:
            detached = {}
            memo[value_id] = detached
            detached.update((key, copy_template_data(item, memo)) for key, item in value.items())
        case list() if type(value) is list:
            detached = []
            memo[value_id] = detached
            detached.extend(copy_template_data(item, memo) for item in value)
        case _:
            return value
    return detached
