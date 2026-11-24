"""Import a back for type checkers only."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from scripts import a

VALUE = 1


def show(module: "a") -> object:
    return module
