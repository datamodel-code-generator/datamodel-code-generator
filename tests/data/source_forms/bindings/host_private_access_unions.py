"""#7025 Host private access: a receiver that may be another type than the Host reaches none of the Host's members.

A parameter reassigned in its own scope stays the declared Host (ty checks the assignment); a union, written as an
expression or in a string, is not the Host.
layers: host
"""
from __future__ import annotations

from typing import Optional, Protocol


class FixtureHost(Protocol):
    _state: int


def read(
    plain: FixtureHost,
    optional: FixtureHost | None,
    quoted: "FixtureHost | None",
    wrapped: Optional[FixtureHost],
    kind: type[FixtureHost],
    kinds: type[FixtureHost | None],
) -> int:
    return plain._state + optional._state + quoted._state + wrapped._state + kind._state + kinds._state
