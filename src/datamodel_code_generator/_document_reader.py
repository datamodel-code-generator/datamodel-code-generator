"""Read the mapping keys of an OpenAPI or AsyncAPI document: the one place that decides what a key may be.

A YAML key such as ``1`` or ``200`` loads as a number. A key that names a component, path, webhook, callback, method,
header, media type, encoding property, link, scheme or message part must be a string, and the keys of a response map
must give each status once as text; otherwise it is a schema error. Each reader of a document asks here and passes the
path its error names, which only the reader can locate, as a function called when the error is raised.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from datamodel_code_generator import SchemaParseError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from datamodel_code_generator._source import YamlValue

__all__ = ["document_statuses", "string_key", "string_keys"]


def string_key(key: object, kind: str, error_path: Callable[[object], list[str]]) -> str:
    """Return ``key``, which names a ``kind``; a key that is no string is an error at ``error_path(key)``."""
    if isinstance(key, str):
        return key
    msg = f"{kind} {key!r} is not a string"
    raise SchemaParseError(msg, path=error_path(key))


def string_keys(keys: Iterable[object], kind: str, error_path: Callable[[object], list[str]]) -> None:
    """Require each of ``keys``, which name a ``kind``, to be a string (``string_key``), in order."""
    for key in keys:
        string_key(key, kind, error_path)


def document_statuses(
    responses: Mapping[Any, YamlValue], error_path: Callable[[str], list[str]]
) -> dict[str, YamlValue]:
    """Return ``responses`` by their status, the text of their key.

    Two keys such as ``200`` and ``'200'`` give one status twice: it is an error at ``error_path(status)``.
    """
    keys: dict[str, object] = {}
    statuses: dict[str, YamlValue] = {}
    for key, value in responses.items():
        if (status := str(key)) in keys:
            msg = f"response status {status} is given by two keys, {keys[status]!r} and {key!r}"
            raise SchemaParseError(msg, path=error_path(status))
        keys[status] = key
        statuses[status] = value
    return statuses
