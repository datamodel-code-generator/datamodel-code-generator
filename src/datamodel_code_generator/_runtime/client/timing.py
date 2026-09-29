"""Deadline and cancellation values shared by options and extension protocols."""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic

from .errors import ConfigurationError


def finite_number(value: object) -> float | None:
    """Return a number as a finite float, or None for booleans, other types, and values no finite float holds."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except OverflowError:
        return None
    return number if math.isfinite(number) else None


def seconds(value: object, path: tuple[str, ...]) -> float:
    """Validate and normalize a finite nonnegative duration at its option path."""
    if (number := finite_number(value)) is None or number < 0:
        raise ConfigurationError(field_path=path, condition="out_of_range")
    return number


_seconds = seconds


@dataclass(frozen=True, slots=True, init=False)
class Deadline:
    """An immutable absolute monotonic deadline created with Deadline.after(seconds)."""

    _at: float

    def __init__(self) -> None:
        """Require the relative factory so a wall-clock timestamp cannot become a deadline."""
        msg = "Use Deadline.after(seconds) to create a deadline"
        raise TypeError(msg)

    @staticmethod
    def after(seconds: float) -> Deadline:
        """Create a deadline that expires after finite, nonnegative seconds."""
        return absolute_deadline(monotonic() + _seconds(seconds, ("deadline",)))

    @property
    def at(self) -> float:
        """Return the absolute monotonic expiry in seconds."""
        return self._at

    def remaining(self) -> float:
        """Return the time left in seconds, or zero once the deadline has expired."""
        return max(0.0, self._at - monotonic())


def absolute_deadline(at: float) -> Deadline:
    """Build the private absolute deadline used when a call or stream resolves its budget."""
    value = object.__new__(Deadline)
    object.__setattr__(value, "_at", at)  # noqa: PLC2801 - Initialize the frozen value without a public constructor.
    return value


class CancelToken:
    """An explicit cancellation signal that can be set safely from any thread."""

    __slots__ = ("_event",)

    def __init__(self) -> None:
        """Start with no cancellation requested."""
        from threading import Event  # noqa: PLC0415

        self._event = Event()

    @property
    def cancelled(self) -> bool:
        """Return whether cancellation has been requested."""
        return self._event.is_set()

    def cancel(self) -> None:
        """Request cancellation; repeated calls leave the same signal set."""
        self._event.set()


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedTimeoutOptions:
    """The effective timeout of each I/O phase in seconds; None leaves that phase unlimited."""

    connect: float | None
    read: float | None
    write: float | None
    pool: float | None
