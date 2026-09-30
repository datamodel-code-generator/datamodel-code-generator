"""Deadline, cancellation, and session-limit values and the option checks shared by options and protocol helpers."""

from __future__ import annotations

import math
from dataclasses import dataclass
from time import monotonic
from typing import Final

from ..model_codecs.unset import UNSET, Unset
from .errors import ConfigurationError

TOKEN_INTERVAL: Final = 0.05


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


def checked_count(value: object, path: tuple[str, ...], *, minimum: int = 0, maximum: int | None = None) -> None:
    """Refuse an option count that is not an integer in its range, including booleans."""
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ConfigurationError(field_path=path, condition="out_of_range")


def checked_instance(value: object, kinds: tuple[type, ...], path: tuple[str, ...]) -> None:
    """Refuse an option value of another type."""
    if not isinstance(value, kinds):
        raise ConfigurationError(field_path=path, condition="invalid_type")


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


@dataclass(frozen=True, slots=True, kw_only=True)
class SessionOptions:
    """Limits of a session spanning several requests: UNSET keeps the session's default and None removes that limit.

    The session ends at the earlier of its total timeout, counted from its start, and its absolute deadline.
    """

    total_timeout: float | Unset | None = UNSET
    deadline: Deadline | Unset | None = UNSET
    max_network_sends: int | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Refuse booleans, negative or nonfinite durations, negative counts, and deadlines of other types."""
        if (timeout := self.total_timeout) is not None and not isinstance(timeout, Unset):
            object.__setattr__(self, "total_timeout", seconds(timeout, ("session_options", "total_timeout")))
        checked_instance(self.deadline, (Deadline, Unset, type(None)), ("session_options", "deadline"))
        if (sends := self.max_network_sends) is not None and not isinstance(sends, Unset):
            checked_count(sends, ("session_options", "max_network_sends"))
