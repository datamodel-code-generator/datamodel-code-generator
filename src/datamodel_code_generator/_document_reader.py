"""Read the mapping keys of an OpenAPI or AsyncAPI document: the one place that decides what a key may be.

A YAML key such as ``1`` or ``200`` loads as a number. A key that names a component, path, webhook, callback, method,
header, media type, encoding property, link, scheme or message part must be a string, and the keys of a response map
must give each status once as text; otherwise it is a schema error. Each reader of a document asks here and passes the
path its error names, which only the reader can locate, as a function called when the error is raised. The walk of
JSON pointer tokens through a loaded document is here too, with the rows that say how each reader reads one member.
"""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import Sentinel

from datamodel_code_generator import Error, SchemaParseError
from datamodel_code_generator._json_pointer import is_array_index

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping

    from datamodel_code_generator._source import YamlValue

__all__ = [
    "MISSING",
    "ArrayIndexOutOfRangeError",
    "MemberRow",
    "as_mapping",
    "descend",
    "document_statuses",
    "member",
    "string_key",
    "string_keys",
]

MISSING: Final = cast("YamlValue", Sentinel("MISSING"))
_T = TypeVar("_T")

MemberRow: TypeAlias = Literal["canonical", "refusing", "legacy-int-tokens", "legacy-decimal-tokens"]
"""How a reader reads one token's member of a loaded value (``member``)."""
_ENDS_WALK: Final[MappingProxyType[MemberRow, tuple[type[Exception], ...]]] = MappingProxyType({
    "canonical": (LookupError, TypeError),
    "refusing": (KeyError,),
    "legacy-int-tokens": (LookupError, TypeError, ValueError),
    "legacy-decimal-tokens": (LookupError,),
})
"""The errors of each row that mean the value has no such member; any other error propagates."""


class ArrayIndexOutOfRangeError(Error):
    """A syntactically valid JSON Pointer array index beyond a list's length (row ``refusing``)."""


def _array_item(value: list[YamlValue], token: str) -> YamlValue:
    """Return a list's item by an RFC 6901 array index; any other token is an error (row ``refusing``)."""
    if not is_array_index(token):
        msg = f"Invalid JSON pointer array index {token!r}: expected a non-negative integer."
        raise Error(msg)
    try:
        index = int(token)
    except ValueError as exc:
        msg = f"Invalid JSON pointer array index {token!r}: integer string is too long to parse."
        raise Error(msg) from exc
    if index >= len(value):
        msg = f"JSON pointer array index {index} is out of range (array length {len(value)})."
        raise ArrayIndexOutOfRangeError(msg)
    return value[index]


def as_mapping(value: object) -> dict[str, YamlValue]:
    """Return a loaded value that is a mapping, or an empty mapping for any other value."""
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def member(value: YamlValue, token: str, row: MemberRow) -> YamlValue:  # noqa: PLR0911
    """Return one token's member of a loaded value; raise one of the row's ``_ENDS_WALK`` errors when it has none.

    ``canonical``: a list item by an RFC 6901 array index; a mapping member by its integer YAML key when the token is
    such an index and the key exists, else by the token. ``refusing`` (the parser's ``$ref`` targets) reads a mapping
    member by the token and a list item by an RFC 6901 array index; a token that is no index, or that is too long,
    is an ``Error``, an index past the end an ``ArrayIndexOutOfRangeError``, and any other value a
    ``NotImplementedError``. The other rows keep the readers' earlier grammars:
    ``legacy-int-tokens`` (target binding) reads an item of any non-mapping by whatever ``int()`` accepts (``-1``,
    ``01``, ``1_0``, a character of a string), a mapping as ``canonical``; ``legacy-decimal-tokens`` (served document)
    reads a list item by any decimal token below the length (``01``, non-ASCII digits), a mapping member by the token
    first, then by a decimal integer key.
    """
    match row:
        case "canonical":
            index = is_array_index(token)
            mapping = cast("dict[object, YamlValue]", value)
            return (
                value[int(token)]
                if isinstance(value, list) and index
                else mapping[int(token)]
                if index and int(token) in mapping
                else mapping[token]
            )
        case "refusing":
            if isinstance(value, dict):
                return value[token]
            if isinstance(value, list):
                return _array_item(value, token)
            msg = f"Cannot traverse non-container schema. schema={value}, key={token}"
            raise NotImplementedError(msg)
        case "legacy-int-tokens":
            if not isinstance(value, dict):
                return cast("list[YamlValue]", value)[int(token)]
            numbered = cast("dict[object, YamlValue]", value)
            return numbered[int(token)] if is_array_index(token) and int(token) in numbered else value[token]
        case _:  # legacy-decimal-tokens
            if isinstance(value, list) and token.isdecimal() and int(token) < len(value):
                return value[int(token)]
            if isinstance(value, dict) and (token in value or (token.isdecimal() and int(token) in value)):
                return value[token] if token in value else cast("dict[object, YamlValue]", value)[int(token)]
    raise LookupError(token)


def descend(value: YamlValue, tokens: Iterable[str], row: MemberRow, missing: YamlValue = None) -> YamlValue:
    """Return the node pointer tokens name below a loaded value, or ``missing`` (such as ``MISSING``) when it has none.

    Each token's member is read by ``row`` (``member``).
    """
    try:
        for token in tokens:
            value = member(value, token, row)
    except _ENDS_WALK[row]:
        return missing
    return value


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


def document_statuses(responses: Mapping[Any, _T], error_path: Callable[[str], list[str]]) -> dict[str, _T]:
    """Return ``responses`` by their status, the text of their key.

    Two keys such as ``200`` and ``'200'`` give one status twice: it is an error at ``error_path(status)``.
    """
    keys: dict[str, object] = {}
    statuses: dict[str, _T] = {}
    for key, value in responses.items():
        if (status := str(key)) in keys:
            msg = f"response status {status} is given by two keys, {keys[status]!r} and {key!r}"
            raise SchemaParseError(msg, path=error_path(status))
        keys[status] = key
        statuses[status] = value
    return statuses
