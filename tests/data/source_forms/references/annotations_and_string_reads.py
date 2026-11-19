"""Annotations with their modes, and the names a string reads where it stands (as typing compiles a forward reference).

annotations: yes
"""

from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Mapping

Alias: TypeAlias = "Mapping[str, Later]"
plain: "Later | None" = None
chosen: Literal["value", "other"] = "value"
broken: "not valid(" = None
spaced: " Later" = None
letter: "Later͸" = None


class Later:
    field: "Later"
    pending: int

    def method(self, other: "Later", *, flag: bool = True) -> "Mapping[str, int]":
        local: "Missing" = 1
        return {"local": local, "flag": flag, "other": other}
