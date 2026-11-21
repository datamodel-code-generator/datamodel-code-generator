"""#7017: strings in Literal and Annotated metadata are values; a string annotation elsewhere is a type."""

from __future__ import annotations

from typing import Annotated, Any, Literal


def f(a: Literal["Any"], b: Annotated[int, "Any"], c: "Any", d: list["Any"], e: "Literal['Any']") -> Any:
    return a, b, c, d, e
