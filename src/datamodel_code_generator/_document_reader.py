"""Read the mapping keys of an OpenAPI or AsyncAPI document: the one place that decides what a key may be.

A YAML key such as ``1`` or ``200`` loads as a number. A key that names a component, path, webhook, callback, method,
header, media type, encoding property, link, scheme or message part must be a string, and the keys of a response map
must give each response once as text; otherwise it is a schema error. Each reader of a document asks here and passes the
path its error names, which only the reader can locate, as a function called when the error is raised. The walk of
JSON pointer tokens through a loaded document is here too, with the rows that say how each reader reads one member,
the fields of an AsyncAPI channel that each hold an operation, the value that wraps an AsyncAPI schema in a format
(``is_multi_format_schema_object``), the reference a discriminator mapping value names (``discriminator_mapping_ref``)
and the error a read of nested schemas ends in when it exceeds the Python recursion limit (``nesting_limit``).
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import Sentinel

from datamodel_code_generator import Error, SchemaParseError
from datamodel_code_generator._json_pointer import is_array_index
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping

    from datamodel_code_generator._source import YamlValue

__all__ = [
    "CHANNEL_OPERATIONS",
    "MISSING",
    "ArrayIndexOutOfRangeError",
    "MemberRow",
    "MergedRefSiteRecursionError",
    "as_mapping",
    "descend",
    "discriminator_mapping_ref",
    "document_statuses",
    "is_multi_format_schema_object",
    "member",
    "member_key",
    "nesting_limit",
    "string_key",
    "string_keyed",
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
        msg = f"Invalid JSON pointer array index {stable_repr(token)}: expected a non-negative integer."
        raise Error(msg)
    try:
        index = int(token)
    except ValueError as exc:
        msg = f"Invalid JSON pointer array index {stable_repr(token)}: integer string is too long to parse."
        raise Error(msg) from exc
    if index >= len(value):
        msg = f"JSON pointer array index {index} is out of range (array length {len(value)})."
        raise ArrayIndexOutOfRangeError(msg)
    return value[index]


def as_mapping(value: object) -> dict[str, YamlValue]:
    """Return a loaded value that is a mapping, or an empty mapping for any other value."""
    return value if isinstance(value, dict) else {}  # pyright: ignore[reportUnknownVariableType]


def member_key(value: YamlValue, token: str) -> str | int:
    """Return the key under which a list or a mapping holds the member a token names, as row ``canonical`` reads it.

    A list holds it under the index the token spells. A mapping holds it under the token, else under the integer YAML
    key the token is the index of; a boolean or float key equal to that integer (``true:``, ``1.0:``) is no such key.
    A token that names none gives itself.
    """
    if not is_array_index(token):
        return token
    index = int(token)
    if isinstance(value, list):
        return index
    mapping = cast("dict[object, YamlValue]", value)
    if token in mapping or index not in mapping:
        return token
    held = next(key for key in mapping if key == index)
    return index if type(held) is int else token


def member(value: YamlValue, token: str, row: MemberRow) -> YamlValue:
    """Return one token's member of a loaded value; raise one of the row's ``_ENDS_WALK`` errors when it has none.

    A list item is read by an RFC 6901 array index only (``0``, or ASCII digits without a leading zero: never ``-1``,
    ``01`` or the character of a string), and a mapping member by the token, else by the integer YAML key the token is
    the index of (``member_key``). ``canonical`` (the locations the parser recorded and the target readers) reads any
    other value as having no member, and every token that names no member is a miss. ``refusing`` (the targets of the
    parser's ``$ref``): a missing member is a ``KeyError``, a value that is no container a ``NotImplementedError``, a
    list token that is no index or is too long an ``Error``, and an index past the end an ``ArrayIndexOutOfRangeError``.
    """
    if row == "refusing" and not isinstance(value, dict):
        if isinstance(value, list):
            return _array_item(value, token)
        msg = f"Cannot traverse non-container schema. schema={value}, key={token}"
        raise NotImplementedError(msg)
    return cast("Any", value)[member_key(value, token)]


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


class MergedRefSiteRecursionError(RecursionError):
    """A recursion limit exceeded while a `$ref` with sibling keywords is parsed in place of the schema it names."""


@contextmanager
def nesting_limit() -> Generator[None]:
    """Read the nested schemas of a document in the block; a read that exceeds the Python recursion limit is an error.

    A document whose schemas nest too deep, or hold themselves through a YAML alias, ends in this error instead of a
    traceback or a partial result, whichever reader walks it. Where `$ref`s with sibling keywords nest one parse per
    hop (``MergedRefSiteRecursionError``), it names --use-one-model-per-ref-site, which parses them without nesting.
    """
    try:
        yield
    except RecursionError as error:
        msg = (
            "Schema nesting is too deep to generate: parsing the nested schemas exceeded the Python recursion "
            f"limit ({sys.getrecursionlimit()})."
        )
        if isinstance(error, MergedRefSiteRecursionError):
            msg += (
                " A chain of $refs with sibling keywords is parsed one nested model per hop; "
                "--use-one-model-per-ref-site parses it without nesting."
            )
        raise Error(msg) from None


def string_key(key: object, kind: str, error_path: Callable[[object], list[str]]) -> str:
    """Return ``key``, which names a ``kind``; a key that is no string is an error at ``error_path(key)``."""
    if isinstance(key, str):
        return key
    msg = f"{kind} {stable_repr(key)} is not a string"
    raise SchemaParseError(msg, path=error_path(key))


def string_keys(keys: Iterable[object], kind: str, error_path: Callable[[object], list[str]]) -> None:
    """Require each of ``keys``, which name a ``kind``, to be a string (``string_key``), in order."""
    for key in keys:
        string_key(key, kind, error_path)


def string_keyed(mapping: dict[Any, YamlValue]) -> dict[str, YamlValue]:
    """Return a loaded mapping read as an object, without the members whose key is no string.

    A key that is no string names no keyword of a schema, so the root of a document that a pointer enters, which is a
    schema only by the keywords it spells, is read without it. A mapping with none is returned as it is.
    """
    if all(isinstance(key, str) for key in mapping):
        return mapping
    return {key: value for key, value in mapping.items() if isinstance(key, str)}


def is_multi_format_schema_object(value: object) -> bool:
    """Return whether an AsyncAPI value is a Multi Format Schema Object, a wrapper of the schema in its ``schema``.

    A mapping with a ``schemaFormat`` is one, and so is a mapping whose keys, but the ``x-`` extensions and the keys
    that are no string, are ``schema`` alone. An OpenAPI or JSON Schema document has no such wrapper.
    """
    if not isinstance(value, dict):
        return False
    return "schemaFormat" in value or {key for key in value if isinstance(key, str) and not key.startswith("x-")} == {
        "schema"
    }


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
            msg = f"response status {name} is given by two keys, {stable_repr(keys[name])} and {stable_repr(key)}"
            raise SchemaParseError(msg, path=error_path(status))
        keys[name] = key
        statuses[status] = value
    return statuses
