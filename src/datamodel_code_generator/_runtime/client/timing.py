"""Budget, cancellation, and session-limit values and the option checks shared by options and protocol helpers."""

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
    """The time and jitter sources of a client, an OAuth provider or flow, the system's by default.

    monotonic returns seconds on one never-decreasing scale, which measures every elapsed time, expiry, and wait. time
    returns POSIX wall-clock seconds, read only to place a wall-clock instant on that scale. random returns a float in
    [0, 1) for full-jitter backoff. Hashing ignores the sources.
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


@dataclass(frozen=True, slots=True)
class Budget:
    """A private monotonic bound passed between calls of one operation."""

    at: float
    clock: Clock = field(repr=False)

    @classmethod
    def after(cls, duration: float, *, clock: Clock) -> Budget:
        """Start a relative budget on its owner's clock."""
        return cls(clock.monotonic() + duration, clock)

    def remaining(self) -> float:
        """Return the nonnegative time left."""
        return max(0.0, self.at - self.clock.monotonic())


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

    The total timeout is counted from the start of the session.
    """

    total_timeout: float | Unset | None = UNSET

    def __post_init__(self) -> None:
        """Refuse booleans, negative or nonfinite durations."""
        if (timeout := self.total_timeout) is not None and not isinstance(timeout, Unset):
            object.__setattr__(self, "total_timeout", seconds(timeout, ("session_options", "total_timeout")))
