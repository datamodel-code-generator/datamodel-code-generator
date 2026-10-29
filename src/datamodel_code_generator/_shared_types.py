"""Small runtime types shared by configuration, API, and parser modules."""

from __future__ import annotations

from collections import UserDict
from collections.abc import Callable, Sequence
from enum import Enum
from typing import TypeAlias, TypeVar

TK = TypeVar("TK")
TV = TypeVar("TV")

ResponseObserver: TypeAlias = Callable[
    [str, Sequence[tuple[str, str]] | None, Sequence[tuple[str, str]] | None, bytes], None
]
"""Take a remote fetch's URL, headers and query parameters as requested, and the body that answered them."""

_LEGACY_MODULE = "datamodel_code_generator.parser"


class _CollapseRootModelsRecursionError(RecursionError):
    """Signal that collapsing root models needs its circular-reference fallback."""


class LiteralType(Enum):
    """Options for handling enum fields as literals."""

    All = "all"
    One = "one"
    Off = "none"


class DefaultPutDict(UserDict[TK, TV]):
    """Dict that can lazily compute and cache missing values."""

    def get_or_put(
        self,
        key: TK,
        default: TV | None = None,
        default_factory: Callable[[TK], TV] | None = None,
    ) -> TV:
        """Get value for key, or compute and store it if missing."""
        if key in self:
            return self[key]
        if default is not None:
            value = self[key] = default
            return value
        if default_factory is not None:
            value = self[key] = default_factory(key)
            return value
        msg = "Not found default and default_factory"
        raise ValueError(msg)


LiteralType.__module__ = _LEGACY_MODULE
DefaultPutDict.__module__ = _LEGACY_MODULE

__all__ = ["DefaultPutDict", "LiteralType", "ResponseObserver"]
