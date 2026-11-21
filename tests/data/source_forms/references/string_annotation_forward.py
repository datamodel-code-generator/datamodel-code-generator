"""Review R14: string annotations name later classes and TYPE_CHECKING-only imports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from m import Later


def f(x: "Later", y: Optional["Fwd"]) -> "list[Fwd]":
    return []


class Fwd:
    children: list["Fwd"]
    broken: "not a type ("
