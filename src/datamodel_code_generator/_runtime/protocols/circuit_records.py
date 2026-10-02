"""The keys, permits, and snapshots that circuit stores exchange with the client."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, TypeAlias

from .origins import Origin
from .records import record_string

__all__ = ("CircuitKey", "CircuitOutcome", "CircuitPermit", "CircuitSnapshot", "CircuitState")


def _record_value(value: object, kind: type | tuple[type, ...], name: str) -> None:
    if not isinstance(value, kind) or isinstance(value, bool) is not (kind is bool):
        msg = f"{name} has the wrong type"
        raise TypeError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitKey:
    """The circuit of one group at one origin for one credential partition, which repr leaves out with the origin."""

    origin: Origin = field(repr=False)
    credential_partition: str = field(repr=False)
    group: str

    def __post_init__(self) -> None:
        """Require an Origin, a partition string, and a group string."""
        _record_value(self.origin, Origin, "origin")
        record_string(self.credential_partition, "credential_partition")
        record_string(self.group, "group")


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitPermit:
    """A store's admission of one call: the generation it belongs to and whether it is the half-open probe."""

    key: CircuitKey
    generation: int
    probe: bool
    permit_id: str = field(repr=False)

    def __post_init__(self) -> None:
        """Require a key, a nonnegative generation, a probe flag, and an identifier string."""
        _record_value(self.key, CircuitKey, "key")
        _record_value(self.generation, int, "generation")
        _record_value(self.probe, bool, "probe")
        record_string(self.permit_id, "permit_id")
        if self.generation < 0:
            msg = "generation must be nonnegative"
            raise ValueError(msg)


CircuitState: TypeAlias = Literal["closed", "open", "half_open"]
CircuitOutcome: TypeAlias = Literal["success", "failure", "neutral"]


@dataclass(frozen=True, slots=True, kw_only=True)
class CircuitSnapshot:
    """The state of one circuit: its consecutive failures, when an open one admits again, and its generation."""

    state: CircuitState
    consecutive_failures: int
    retry_at: float | None
    generation: int

    def __post_init__(self) -> None:
        """Require a known state, nonnegative counts, and a numeric retry time or None."""
        if self.state not in {"closed", "open", "half_open"}:
            msg = "state must be closed, open, or half_open"
            raise ValueError(msg)
        _record_value(self.consecutive_failures, int, "consecutive_failures")
        _record_value(self.generation, int, "generation")
        if self.retry_at is not None:
            _record_value(self.retry_at, (int, float), "retry_at")
        if self.consecutive_failures < 0 or self.generation < 0:
            msg = "counts must be nonnegative"
            raise ValueError(msg)
