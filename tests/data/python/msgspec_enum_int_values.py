"""Public mapping input values whose integer rendering survives code generation."""

from __future__ import annotations


class IntValue(int):
    """An integer with ordinary equality and hashing."""


class UnhashableInt(int):
    """An integer that cannot be inserted in the diagnostic's alias set."""

    __hash__ = None


class CustomHashInt(int):
    """An equal integer with a deliberately different hash."""

    def __hash__(self) -> int:
        """Return a hash inconsistent with equal ordinary integers."""
        return 123456


class CustomEqualInt(int):
    """An integer with explicit ordinary equality and no hash."""

    __hash__ = None

    def __eq__(self, other: object) -> bool:
        """Compare using the underlying integer value."""
        return int.__eq__(self, other)


class HashRaisesInt(int):
    """An integer whose hash must never be consulted by the alias diagnostic."""

    def __hash__(self) -> int:
        """Expose an unwanted attempt to hash an API value."""
        message = "integer hash was called"
        raise RuntimeError(message)


class EqualityRaisesInt(int):
    """An integer preserving the public API's comparison exception behavior."""

    __hash__ = None

    def __eq__(self, other: object) -> bool:
        """Expose the original comparison error on floating point aliases."""
        if isinstance(other, float):
            message = "integer comparison was called"
            raise TypeError(message)
        return int.__eq__(self, other)
