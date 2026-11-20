"""Entries of each form, a name only type checkers bind, a base bound on two branches and two __module__ stores."""

from typing import TYPE_CHECKING

from ._lazy_exports import module_hooks
from ._values import LIMIT, Base, Other

if TYPE_CHECKING:
    from json import JSONDecoder as Hidden

__all__ = ["Hidden", "Public", "made"]


def _load_class():
    class Built(Base):
        size: int = LIMIT

        def describe(self) -> str:
            return "built"

    def helper():
        return Built

    return helper()


def _load_module():
    from . import _values as values

    return values


_TABLE = {
    "made": _load_class,
    "values": _load_module,
    "Remote": "form.a6_lazy_exports._values",
    "Missing": "form.a6_lazy_exports._values",
    "alias": LIMIT,
    "computed": lambda: LIMIT * 2,
    "broken": len("x"),
    "dumps": "json",
}
__getattr__, __dir__ = module_hooks(__name__, _TABLE)

if LIMIT:
    Chosen = Base
else:
    Chosen = Other


class Public(Chosen):
    pass


Public.__module__ = "form.elsewhere"
Other.__module__ = str(LIMIT)
