"""Small runtime types shared by configuration, API, and parser modules."""

from __future__ import annotations

from collections import UserDict
from enum import Enum
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Callable

TK = TypeVar("TK")
TV = TypeVar("TV")

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

__all__ = ["DefaultPutDict", "LiteralType"]
