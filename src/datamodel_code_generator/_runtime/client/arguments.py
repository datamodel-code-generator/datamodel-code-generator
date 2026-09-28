"""Check the keyword arguments of an operation method that takes them as one unpacked TypedDict.

An annotation binds nothing at runtime, so such a method checks the keywords it was given as Python binds the explicit
keyword-only parameters of the same method, then reads each one from them with the default it has.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping


class Keywords:
    """The keyword-only parameters of one operation method: every name it takes, and the ones it requires."""

    __slots__ = ("_names", "method", "required")

    def __init__(self, method: str, names: tuple[str, ...], required: tuple[str, ...]) -> None:
        """Keep the method name, which errors quote, the names it takes, and the required ones in order."""
        self.method = method
        self._names = frozenset(names)
        self.required = required

    def check(self, given: Mapping[str, object]) -> None:
        """Refuse an unknown keyword or a missing required one with TypeError, as Python refuses explicit ones."""
        if not self._names.issuperset(given):
            unexpected = next(name for name in given if name not in self._names)
            msg = f"{self.method}() got an unexpected keyword argument {unexpected!r}"
            raise TypeError(msg)
        if missing := [name for name in self.required if name not in given]:
            msg = f"{self.method}() missing required keyword-only arguments: {', '.join(map(repr, missing))}"
            raise TypeError(msg)
