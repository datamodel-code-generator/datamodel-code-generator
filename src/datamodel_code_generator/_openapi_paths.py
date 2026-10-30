"""What the keys of an OpenAPI document's paths say: the operation fields of a path item, and the selected paths.

``PATH_ITEM_METHODS`` is the one table of the fields of a path item that each hold the operation of one HTTP method.
A reader that takes another set keeps it as a named view here: ``OPERATION_METHODS``, the fields before OpenAPI 3.2
added ``QUERY_METHOD``, which the paths scope, the API walk of an earlier document and the recommender read (the public
``parser.openapi.OPERATION_NAMES`` lists them in this order). ``path_selected`` is the test of
``--openapi-include-paths`` that generation and the recommender apply to a key of ``paths``. This module imports only
the standard library, so every layer may use it.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["OPERATION_METHODS", "PATH_ITEM_METHODS", "QUERY_METHOD", "path_selected", "rooted_path"]

OPERATION_METHODS: Final = ("get", "put", "post", "delete", "patch", "head", "options", "trace")
"""The fields of a path item that hold an operation before OpenAPI 3.2."""
QUERY_METHOD: Final = "query"
"""The field of a path item that holds the operation of the QUERY method, added by OpenAPI 3.2."""
PATH_ITEM_METHODS: Final = (*OPERATION_METHODS, QUERY_METHOD)
"""Every field of a path item that holds the operation of one HTTP method."""


def rooted_path(path: str) -> str:
    """Return a path or a pattern of ``--openapi-include-paths`` with a leading slash, as they are compared."""
    return path if path.startswith("/") else f"/{path}"


def path_selected(path: str, patterns: Sequence[str] | None, rooted: Callable[[str], str] = rooted_path) -> bool:
    """Return whether ``--openapi-include-paths`` keeps a path: every path without patterns, else one a pattern matches.

    The path and each pattern are compared with a leading slash (``rooted``), by ``fnmatch.fnmatch``.
    """
    path = rooted(path)
    return not patterns or any(fnmatch(path, rooted(pattern)) for pattern in patterns)
