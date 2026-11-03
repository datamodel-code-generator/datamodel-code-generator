"""Read the mapping keys of an OpenAPI or AsyncAPI document: the one place that decides what a key may be.

A YAML key such as ``1`` or ``200`` loads as a number. A key that names a component, path, webhook, callback, method,
header, media type, encoding property, link, scheme or message part must be a string, and the keys of a response map
must give each response once as text; otherwise it is a schema error. Each reader of a document asks here and passes the
path its error names, which only the reader can locate, as a function called when the error is raised. The walk of
JSON pointer tokens through a loaded document is here too, with the rows that say how each reader reads one member,
the fields of an AsyncAPI channel that each hold an operation and the reference a discriminator mapping value names
(``discriminator_mapping_ref``).
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
    "CHANNEL_OPERATIONS",
    "MISSING",
    "ArrayIndexOutOfRangeError",
    "MemberRow",
    "as_mapping",
    "descend",
    "discriminator_mapping_ref",
    "document_statuses",
    "member",
    "string_key",
    "string_keys",
]

MISSING: Final = cast("YamlValue", Sentinel("MISSING"))
CHANNEL_OPERATIONS: Final = ("publish", "subscribe")
"""The fields of an AsyncAPI 2 channel that each hold an operation, in the order generation and the recommender read
them (the public ``parser.asyncapi.OPERATION_NAMES``)."""
_T = TypeVar("_T")

MemberRow: TypeAlias = Literal["canonical", "refusing"]
"""How a reader treats a token that names no member of a loaded value (``member``)."""
_ENDS_WALK: Final[MappingProxyType[MemberRow, tuple[type[Exception], ...]]] = MappingProxyType({
    "canonical": (LookupError, TypeError, ValueError),
    "refusing": (KeyError,),
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


def member(value: YamlValue, token: str, row: MemberRow) -> YamlValue:
    """Return one token's member of a loaded value; raise one of the row's ``_ENDS_WALK`` errors when it has none.

    A list item is read by an RFC 6901 array index only (``0``, or ASCII digits without a leading zero: never ``-1``,
    ``01`` or the character of a string), and any other value has no member. ``canonical`` (the locations the parser
    recorded and the target readers) reads a mapping member by its integer YAML key when the token is such an index and
    the key exists, else by the token, and every token that names no member is a miss. ``refusing`` (the targets of the
    parser's ``$ref``) reads a mapping member by the token only: a missing member is a ``KeyError``, a value that is no
    container a ``NotImplementedError``, a list token that is no index or is too long an ``Error``, and an index past
    the end an ``ArrayIndexOutOfRangeError``.
    """
    if row == "refusing":
        if isinstance(value, dict):
            return value[token]
        if isinstance(value, list):
            return _array_item(value, token)
        msg = f"Cannot traverse non-container schema. schema={value}, key={token}"
        raise NotImplementedError(msg)
    index = is_array_index(token)
    mapping = cast("dict[object, YamlValue]", value)
    return (
        value[int(token)]
        if isinstance(value, list) and index
        else mapping[int(token)]
        if index and int(token) in mapping
        else mapping[token]
    )


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


def discriminator_mapping_ref(value: str) -> str:
    """Return the reference a discriminator mapping value names.

    A value with a ``/`` or a ``#`` is a reference or a path (``#/components/schemas/Pet``, ``./other.yaml#/Pet``,
    ``schemas/Pet``) and stays as it is. Any other value, a dot included (``Pet``, ``Pet.V1``), names a schema of
    ``#/components/schemas/``; a bare file name such as ``other.yaml`` is a schema name, so a file is written
    ``./other.yaml``.
    """
    if "/" in value or "#" in value:
        return value
    return f"#/components/schemas/{value}"


def document_statuses(
    responses: Mapping[Any, _T], canonical: Callable[[str], str | None], error_path: Callable[[str], list[str]]
) -> dict[str, _T]:
    """Return ``responses`` by their status, the text of their key.

    Two keys that give one response twice are an error at ``error_path(status)`` of the second: ``200`` and ``'200'``,
    or keys that ``canonical`` maps to one response key, such as ``2XX`` and ``2xx``. A key it maps to none stands for
    itself.
    """
    keys: dict[str, object] = {}
    statuses: dict[str, _T] = {}
    for key, value in responses.items():
        status = str(key)
        name = canonical(status) or status
        if name in keys:
            msg = f"response status {name} is given by two keys, {keys[name]!r} and {key!r}"
            raise SchemaParseError(msg, path=error_path(status))
        keys[name] = key
        statuses[status] = value
    return statuses
