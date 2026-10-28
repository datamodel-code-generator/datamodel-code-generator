"""Every attribute of the generator that the debug checks wrap, and how ``install`` wraps it.

Rules, which ``test_check_seams`` holds:

1. Wrap, never replace: every wrapper calls the original, so the test suite runs and covers every production line.
2. Prefer class attributes. A seam names the class that defines the attribute.
3. A module function is wrapped in its module and in every module whose globals hold it (``holders``): a module that
   imports it by name holds its own reference. ``test_check_seams`` fails for a loaded module of the package that
   holds the original elsewhere, so a new ``from X import f`` cannot escape the check silently.
4. The state of a parse lives in ``MODES`` (and the ``state`` of its modes), set when the pipeline of a parse starts:
   a wrapper does nothing outside a parse or with its area off.
5. Every area keeps at least one test that makes it report a violation: a seam that stops firing fails it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import cache
from importlib import import_module
from typing import TYPE_CHECKING, Any, Final, final

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import ModuleType

_MARK: Final = "__check_seam__"


@final
@dataclass(frozen=True)
class Seam:
    """One attribute that a wrapper of ``tests/checks`` wraps."""

    module: str
    owner: str | None
    """The class that defines the attribute, or None for a module function."""
    attribute: str
    wrapper: str
    """``<module of tests.checks>.<function>``: the function that takes the original and returns its wrapper."""
    holders: tuple[str, ...] = ()
    """The other modules whose globals hold the module function."""

    @property
    def name(self) -> str:
        """Name the attribute as ``module:owner.attribute``."""
        return f"{self.module}:{self.attribute if self.owner is None else f'{self.owner}.{self.attribute}'}"


_PACKAGE: Final = "datamodel_code_generator"
SEAMS: Final = (
    Seam(f"{_PACKAGE}.parser._pipeline", "Runner", "run_steps", "parse.run_steps"),
    Seam(f"{_PACKAGE}._parse_memo", "ParseMemo", "value", "memos.value"),
    Seam(f"{_PACKAGE}._parse_memo", "ParseMemo", "derived", "memos.derived"),
    Seam(f"{_PACKAGE}._parse_memo", "ParseMemo", "keep_union_members", "memos.keep_union_members"),
    Seam(f"{_PACKAGE}._parse_memo", "ParseMemo", "union_members", "memos.union_members"),
    Seam(f"{_PACKAGE}.types", None, "_spread_union_member_hints", "memos.spread_union_member_hints"),
)
MODULES: Final = frozenset(module for seam in SEAMS for module in (seam.module, *seam.holders))


@cache
def _wrapper(seam: Seam, original: Callable[..., Any]) -> Callable[..., Any]:
    module, function = seam.wrapper.rsplit(".", 1)
    wrapper = getattr(import_module(f"tests.checks.{module}"), function)(original)
    setattr(wrapper, _MARK, original)
    return wrapper


def _wrapped(value: object) -> bool:
    return hasattr(value, _MARK)


def apply(module: ModuleType) -> None:
    """Wrap the seams that ``module`` defines or holds, unless they are wrapped."""
    for seam in SEAMS:
        if seam.owner is not None and seam.module == module.__name__:
            owner = getattr(module, seam.owner)
            if not _wrapped(original := vars(owner)[seam.attribute]):
                setattr(owner, seam.attribute, _wrapper(seam, original))
        elif (
            seam.owner is None
            and module.__name__ in {seam.module, *seam.holders}
            and not _wrapped(original := vars(module)[seam.attribute])
        ):
            setattr(module, seam.attribute, _wrapper(seam, original))


def report() -> str:
    """Describe every seam as installed now, and each module of the package that holds an unwrapped seam function."""
    lines: list[str] = []
    originals: dict[int, Seam] = {}
    for seam in SEAMS:
        module = import_module(seam.module)
        value = vars(module if seam.owner is None else getattr(module, seam.owner))[seam.attribute]
        lines.append(f"{seam.name} <- tests.checks.{seam.wrapper}{'' if _wrapped(value) else ' (not wrapped)'}\n")
        if seam.owner is None:
            originals[id(getattr(value, _MARK, value))] = seam
    lines.extend(
        f"{originals[id(value)].name} is held unwrapped by {name}.{attribute}\n"
        for name, module in sorted(sys.modules.items())
        if name == _PACKAGE or name.startswith(f"{_PACKAGE}.")
        for attribute, value in vars(module).items()
        if id(value) in originals and not _wrapped(value)
    )
    return "".join(lines)
