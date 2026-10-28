"""Runtime annotations that must retain structure through --input-model."""

from __future__ import annotations

from collections.abc import Callable  # noqa: TC003  # Pydantic resolves this annotation at runtime.
from typing import Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class Wrapper(Generic[T]):
    """Arbitrary generic used to force x-python-type transport."""


class StructuredAnnotations(BaseModel):
    """Model containing a nested Callable and runtime symbols."""

    model_config = {"arbitrary_types_allowed": True}

    handler: Wrapper[Callable[[int, str], bool]]


class StructuredBase(BaseModel):
    """Base model whose field is transported into a generated $defs entry."""

    model_config = {"arbitrary_types_allowed": True}

    callback: Wrapper[Callable[[str], int]]


class StructuredChild(StructuredBase):
    """Child model combining inherited and array-nested runtime expressions."""

    handlers: list[Wrapper[Callable[[int], bool]]]


class RuntimeNamespace:
    """Namespace proving that a nested runtime class is not an import module."""

    class Nested:
        """Arbitrary nested runtime type."""


class NestedRuntimeAnnotations(BaseModel):
    """Model whose annotation must retain its runtime module and qualname."""

    model_config = {"arbitrary_types_allowed": True}

    value: RuntimeNamespace.Nested
