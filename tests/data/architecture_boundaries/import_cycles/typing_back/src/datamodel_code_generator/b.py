"""Import a back for type checkers only."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from datamodel_code_generator import a

VALUE = 1


def show(module: "a") -> object:
    return module
