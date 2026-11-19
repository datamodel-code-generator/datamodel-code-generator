"""A deep module with a function-level import of its parent and a TYPE_CHECKING import."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .. import leaf


def load():
    from .. import inner

    return inner
