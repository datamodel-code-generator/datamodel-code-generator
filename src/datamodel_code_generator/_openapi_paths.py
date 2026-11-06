"""What the keys of an OpenAPI document's paths say: operation fields, selected paths and the operation in effect.

``PATH_ITEM_METHODS`` is the one table of the fields of a path item that each hold the operation of one HTTP method.
A reader that takes another set keeps it as a named view here: ``OPERATION_METHODS``, the fields before OpenAPI 3.2
added ``QUERY_METHOD``, which the paths scope, the API walk of an earlier document and the recommender read (the public
``parser.openapi.OPERATION_NAMES`` lists them in this order). ``path_item_operations`` reads the operations of a path
item as the API walk does, ``additionalOperations`` of OpenAPI 3.2 included. ``holds_path_item`` tells which key of
``paths`` holds a path item; ``path_selected`` is the test of ``--openapi-include-paths`` that generation and the
recommender apply to such a key, and ``operation_responses`` tells which entries of an operation's Responses Object are
responses. ``effective_parameters``, ``inherited_security`` and ``effective_operation`` give an operation as the paths
scope's walk and the API walk hand it on: with the parameters of its path item it does not override and the document's
security requirement unless it declares its own. This module imports only the standard library, so every layer may use
it.
"""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Iterator, Mapping, Sequence

__all__ = [
    "ADDITIONAL_OPERATIONS",
    "FIXED_HTTP_METHODS",
    "OPERATION_METHODS",
    "PATHS_PARAMETERS",
    "PATH_ITEM_METHODS",
    "QUERY_METHOD",
    "PathKeyRow",
    "effective_operation",
    "effective_parameters",
    "holds_path_item",
    "inherited_security",
    "operation_responses",
    "path_item_operations",
    "path_selected",
    "rooted_path",
]

_T = TypeVar("_T")
_K = TypeVar("_K")

OPERATION_METHODS: Final = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
"""The fields of a path item that hold an operation before OpenAPI 3.2."""
QUERY_METHOD: Final = "query"
"""The field of a path item that holds the operation of the QUERY method, added by OpenAPI 3.2."""
PATH_ITEM_METHODS: Final = (*OPERATION_METHODS, QUERY_METHOD)
"""Every field of a path item that holds the operation of one HTTP method."""
ADDITIONAL_OPERATIONS: Final = "additionalOperations"
"""The field of a path item that maps the name of a method OpenAPI 3.2 does not fix to its operation."""
FIXED_HTTP_METHODS: Final = frozenset(method.upper() for method in PATH_ITEM_METHODS)
"""The names ``ADDITIONAL_OPERATIONS`` may not use: the fixed methods, upper-cased."""
PATHS_PARAMETERS: Final = "parameters"
"""The key of ``paths`` that holds the parameters of every path; it is not a path item."""
_EXTENSION_PREFIX: Final = "x-"
PathKeyRow: TypeAlias = Literal["openapi", "legacy-object-extension-paths"]


def _is_extension(key: object) -> bool:
    """Return whether a key of an object is a specification extension: a string that begins with ``x-``."""
    return isinstance(key, str) and key.startswith(_EXTENSION_PREFIX)


def holds_path_item(key: str, value: object, row: PathKeyRow = "openapi") -> bool:
    """Return whether a key of ``paths`` holds a path item: not ``PATHS_PARAMETERS`` and not a specification extension.

    A path item is under a path template. OpenAPI reads a key that begins with ``x-`` as an extension of ``paths``, and
    ``PATHS_PARAMETERS`` holds the parameters of every path, so row ``openapi`` (the API walk) skips both. Row
    ``legacy-object-extension-paths`` (the paths scope's walk) reads such a key that holds an object as a path, as it
    always has, and skips the rest, which no path item can be.
    """
    return not (key == PATHS_PARAMETERS or _is_extension(key)) or (
        row == "legacy-object-extension-paths" and isinstance(value, dict)
    )


def operation_responses(responses: Mapping[_K, _T]) -> dict[_K, _T]:
    """Return the responses of an operation's Responses Object, which both walks read: its status codes and ``default``.

    OpenAPI: a Responses Object "MAY be extended with Specification Extensions", the fields that begin with ``x-``, so
    an extension that holds no object is no response. Row ``legacy-object-extension-responses``: an extension that holds
    an object is read as a response, as both walks always have.
    """
    return {
        status: response
        for status, response in responses.items()
        if not _is_extension(status) or isinstance(response, dict)
    }


def path_item_operations(
    item: Mapping[str, Any], *, openapi_32: bool, invalid: Callable[[str, tuple[str, ...]], Exception]
) -> Iterator[tuple[tuple[str, ...], Any]]:
    """Yield the tokens and the operation of each field of a path item that the API walk reads, in source order.

    The fields are the methods of ``OPERATION_METHODS``; from OpenAPI 3.2 (``openapi_32``) also ``QUERY_METHOD`` and
    each entry of ``ADDITIONAL_OPERATIONS``, whose tokens are the field and the name of the entry. An
    ``ADDITIONAL_OPERATIONS`` that is no mapping, or an entry named like a fixed method, is refused: the exception to
    raise is ``invalid(message, tokens)``, with the tokens of the refused field or entry.
    """
    for field, value in item.items():
        if field in OPERATION_METHODS or (field == QUERY_METHOD and openapi_32):
            yield (field,), value
        elif field == ADDITIONAL_OPERATIONS and openapi_32:
            if not isinstance(value, dict):
                msg = "API_INVALID_OBJECT: expected an object"
                raise invalid(msg, (field,))
            for name, operation in value.items():
                if name in FIXED_HTTP_METHODS:
                    msg = "API_INVALID_OBJECT: fixed methods cannot appear in additionalOperations"
                    raise invalid(msg, (field, name))
                yield (field, name), operation


def rooted_path(path: str) -> str:
    """Return a path or a pattern of ``--openapi-include-paths`` with a leading slash, as they are compared."""
    return path if path.startswith("/") else f"/{path}"


def path_selected(path: str, patterns: Sequence[str] | None, rooted: Callable[[str], str] = rooted_path) -> bool:
    """Return whether ``--openapi-include-paths`` keeps a path: every path without patterns, else one a pattern matches.

    The path and each pattern are compared with a leading slash (``rooted``), by ``fnmatch.fnmatchcase``: letter case
    and every character count on every OS, as in the path templates of OpenAPI.
    """
    path = rooted(path)
    return not patterns or any(fnmatchcase(path, rooted(pattern)) for pattern in patterns)


def effective_parameters(own: Sequence[_T], inherited: Iterable[_T], key: Callable[[_T], Hashable | None]) -> list[_T]:
    """Return the parameters in effect for an operation: its own in order, then each inherited one it does not override.

    ``inherited`` are the parameters of its path item (after those of ``paths``). An own parameter overrides each
    inherited one with the same ``key``, the identity the caller's walk gives a parameter: its location and name, a
    header's name without regard to case. A parameter whose key is None overrides none.
    """
    overridden = {found for parameter in own if (found := key(parameter)) is not None}
    return [*own, *(parameter for parameter in inherited if key(parameter) not in overridden)]


def inherited_security(operation: Mapping[str, object], security: _T | None) -> _T | None:
    """Return the document's security requirement ``security`` that an operation inherits, None when it has none.

    An operation that declares ``security`` itself, an empty list included, inherits nothing.
    """
    return None if "security" in operation else security


def effective_operation(operation: dict[str, Any], parameters: list[Any] | None, security: object) -> dict[str, Any]:
    """Return an operation as a walk hands it on, never changing the operation of the document.

    A copy carries ``parameters`` (the parameters in effect, None when its path item and ``paths`` declare none) and
    the security requirement it inherits; with neither, the operation itself. Hooks read both from it:
    fastapi-code-generator depends on the inherited security being written into the operation.
    """
    effective = operation
    if parameters is not None:
        effective = operation.copy()
        effective["parameters"] = parameters
    if (inherited := inherited_security(operation, security)) is not None:
        if effective is operation:
            effective = operation.copy()
        effective["security"] = inherited
    return effective
