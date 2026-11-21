"""SC (step 2, any by origin): types as whole chains, and aliases of Any read where they are used.

``typing.Any``, ``t.Any`` and ``"t.Any"`` are one type expression each whose origin is ``typing.Any``; a use of an
alias of ``Any`` has that origin too, a ``TYPE_CHECKING`` import beside an ``Any`` for run time has none, an alias of
``dict[str, Any]`` names itself, and ``Annotated`` metadata and ``Literal`` values are no types.
"""

from __future__ import annotations

import typing
import typing as t
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeAlias, cast

if TYPE_CHECKING:
    from collections.abc import Mapping as Table
else:
    Table: TypeAlias = Any

Config: TypeAlias = Any
Plain = Any
Payload: TypeAlias = dict[str, Any]


def read(
    first: typing.Any,
    second: "t.Any",
    third: list[typing.Any],
    fourth: Config,
    fifth: Plain,
    sixth: Table,
    seventh: Payload,
    eighth: Annotated[int, typing.Any],
    ninth: Literal["typing.Any"],
) -> typing.Mapping[str, t.Any]:
    held: Config = cast("t.Any", first)
    return cast(typing.Any, (held, second, third, fourth, fifth, sixth, seventh, eighth, ninth))
