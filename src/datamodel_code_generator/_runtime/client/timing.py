"""Deadline, cancellation, and session-limit values and the option checks shared by options and protocol helpers."""

from __future__ import annotations

import math
import time as _time
from collections.abc import Callable  # noqa: TC003 - Public annotations support get_type_hints().
from dataclasses import dataclass, field
from typing import Final, final

from ..model_codecs.unset import UNSET, Unset
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
        raise ConfigurationError(field_path=path, reason="out_of_range")
    return number


_seconds = seconds


def checked_count(value: object, path: tuple[str, ...], *, minimum: int = 0, maximum: int | None = None) -> None:
    """Refuse an option count that is not an integer in its range, including booleans."""
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ConfigurationError(field_path=path, reason="out_of_range")


def checked_instance(value: object, kinds: tuple[type, ...], path: tuple[str, ...]) -> None:
    """Refuse an option value of another type."""
    if not isinstance(value, kinds):
        raise ConfigurationError(field_path=path, reason="invalid_type")


def _random() -> float:
    """Return a uniform float in [0, 1), loading the random source only once a full jitter needs a value."""
    from secrets import randbits  # noqa: PLC0415

    return randbits(53) / (1 << 53)


@final
@dataclass(frozen=True, slots=True, kw_only=True)
class Clock:
    """The time and jitter sources of a client, an OAuth provider or flow, and a deadline; the system's by default.

    monotonic returns seconds on one never-decreasing scale, which measures every elapsed time, expiry, and wait. time
    returns POSIX wall-clock seconds, read only to place a wall-clock instant on that scale. random returns a float in
    [0, 1) for full-jitter backoff. A wait ends once this clock reaches its target or once the real time it measured
    when it began has passed, so a fake clock that should skip a wait advances itself. Hashing ignores the sources.
    """

    monotonic: Callable[[], float] = field(default=_time.monotonic, hash=False)
    time: Callable[[], float] = field(default=_time.time, hash=False)
    random: Callable[[], float] = field(default=_random, hash=False)

    def __post_init__(self) -> None:
        """Refuse a source that cannot be called."""
        for name in ("monotonic", "time", "random"):
            if not callable(getattr(self, name)):
                raise ConfigurationError(field_path=("clock", name), reason="invalid_type")


SYSTEM_CLOCK: Final = Clock()


@dataclass(frozen=True, slots=True, init=False)
class Deadline:
    """An immutable absolute deadline on a clock's monotonic scale, created with Deadline.after(seconds)."""

    _at: float
    _clock: Clock = field(repr=False, hash=False)

    def __init__(self) -> None:
        """Require the relative factory so a wall-clock timestamp cannot become a deadline."""
        msg = "Use Deadline.after(seconds) to create a deadline"
        raise TypeError(msg)

    @staticmethod
    def after(seconds: float, *, clock: Clock | None = None) -> Deadline:
        """Create a deadline that expires after finite, nonnegative seconds on the clock, the system's by default."""
        if clock is None:
            clock = SYSTEM_CLOCK
        else:
            checked_instance(clock, (Clock,), ("clock",))
        return absolute_deadline(clock.monotonic() + _seconds(seconds, ("deadline",)), clock=clock)

    @property
    def at(self) -> float:
        """Return the absolute expiry in seconds on the monotonic scale of the deadline's clock."""
        return self._at

    @property
    def clock(self) -> Clock:
        """Return the clock the deadline was created on, whose monotonic scale its expiry is on."""
        return self._clock

    def remaining(self) -> float:
        """Return the time left in seconds on the deadline's clock, or zero once the deadline has expired."""
        return max(0.0, self._at - self._clock.monotonic())


def absolute_deadline(at: float, *, clock: Clock) -> Deadline:
    """Build the private absolute deadline on a clock's scale used when a call or stream resolves its budget."""
    value = object.__new__(Deadline)
    object.__setattr__(value, "_at", at)  # noqa: PLC2801 - Initialize the frozen value without a public constructor.
    object.__setattr__(value, "_clock", clock)  # noqa: PLC2801
    return value


def on_clock(deadline: Deadline, clock: Clock) -> Deadline:
    """Return the deadline on the clock's scale: itself when made on that Clock, or moved by its remaining time."""
    if deadline.clock is clock:
        return deadline
    return absolute_deadline(clock.monotonic() + deadline.remaining(), clock=clock)


def real_end(seconds: float) -> float:
    """Return the real monotonic time by which a wait of seconds, measured on a clock as it begins, ends."""
    return _time.monotonic() + seconds


def wait_left(left: float, end: float) -> float:
    """Return how long a wait lasts: until its clock has no time left or real time reaches the wait's end.

    A NaN read from the clock stays NaN, which every wait treats as no time left.
    """
    return min(left, end - _time.monotonic())


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

    def __post_init__(self) -> None:
        """Refuse booleans, negative or nonfinite durations, and deadlines of other types."""
        if (timeout := self.total_timeout) is not None and not isinstance(timeout, Unset):
            object.__setattr__(self, "total_timeout", seconds(timeout, ("session_options", "total_timeout")))
        checked_instance(self.deadline, (Deadline, Unset, type(None)), ("session_options", "deadline"))
