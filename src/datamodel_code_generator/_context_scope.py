"""Run a block with context variables set: the scopes of an invocation, a parse or a rendering.

The module imports only the standard library, so every layer can import it.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Generator, Mapping
    from contextvars import ContextVar

__all__ = ["context_values"]


@contextmanager
def context_values(values: Mapping[ContextVar[Any], Any]) -> Generator[None, None, None]:
    """Set each context variable to its value, in order, while the block runs; reset them in reverse order after it."""
    tokens = [(variable, variable.set(value)) for variable, value in values.items()]
    try:
        yield
    finally:
        for variable, token in reversed(tokens):
            variable.reset(token)
