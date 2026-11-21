"""C3 (scripts/_change_units.py): the parts of a module only type checkers read, by origin.

segments: yes
"""

from __future__ import annotations

import typing
from typing import TYPE_CHECKING, final, overload

import typing_extensions

if TYPE_CHECKING:
    from os import PathLike
if typing.TYPE_CHECKING and PathLike:
    LIMIT = 1
if not TYPE_CHECKING:
    LIMIT = 2
else:
    LIMIT = 3


@overload
def convert(value: int) -> int: ...
@typing_extensions.overload
def convert(value: str) -> str: ...
def convert(value: object) -> object:
    local: int = 1
    return value, local


class Base:
    size: int = 1

    @typing_extensions.override
    @final
    @staticmethod
    def label(value: str) -> str:
        return value
