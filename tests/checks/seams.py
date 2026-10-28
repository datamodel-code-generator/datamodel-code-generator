"""Every attribute of the generator that the debug checks wrap, and how ``install`` wraps it.

Rules, which ``test_check_seams`` holds:

1. Wrap, never replace: every wrapper calls the original, so the test suite runs and covers every production line.
2. Prefer class attributes. A seam names the class that defines the attribute. A parser method that a parser
   subclass may override (``hook``) is wrapped on the class that defines it in the running parser's MRO, when that
   parser starts a parse, as the call site saw the override; its wrapper acts once per call from the pipeline.
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
    hook: bool = False
    """Whether a parser subclass may override the method, which is wrapped on the overriding class too."""

    @property
    def name(self) -> str:
        """Name the attribute as ``module:owner.attribute``."""
        return f"{self.module}:{self.attribute if self.owner is None else f'{self.owner}.{self.attribute}'}"


_PACKAGE: Final = "datamodel_code_generator"
_PASSES: Final = f"{_PACKAGE}.parser._passes"
SEAMS: Final = (
    Seam(f"{_PACKAGE}.parser._pipeline", "Runner", "run_steps", "parse.run_steps"),
    Seam(f"{_PACKAGE}.parser._pipeline", "Runner", "run_pass", "parse.run_pass"),
    Seam(f"{_PACKAGE}.parser._pipeline", "Runner", "run_decision", "parse.run_decision"),
    Seam(f"{_PACKAGE}.parser.base", "Parser", "_process_single_module", "parse.process_single_module", hook=True),
    Seam(f"{_PACKAGE}.parser.generation", "GenerationStore", "seal_facts", "parse.seal_facts"),
    Seam(f"{_PACKAGE}.parser.generation", "GenerationStore", "refresh_now", "model_graph.refresh_now"),
    Seam(f"{_PACKAGE}.parser.generation", "GenerationStore", "model_key", "model_graph.model_key"),
    Seam(f"{_PASSES}.modules", None, "collapse_module_root_models", "model_graph.collapse_module_root_models"),
    Seam(f"{_PACKAGE}.parser._phases", "_DecisionWriter", "drop_held_null", "parse.drop_held_null"),
    Seam(
        f"{_PASSES}._shared",
        None,
        "get_inherited_constructor_info",
        "parse.get_inherited_constructor_info",
        holders=(f"{_PASSES}.modules", f"{_PASSES}.render_prep"),
    ),
    Seam(f"{_PACKAGE}.parser.base", "Parser", "_get_module_future_imports", "module_imports.get_module_future_imports"),
    Seam(f"{_PACKAGE}.parser._imports_plan", "ModuleImportLedger", "add_name", "module_imports.add_name"),
    Seam(f"{_PACKAGE}.parser._imports_plan", "ModuleImportLedger", "release_names", "module_imports.release_names"),
    Seam(f"{_PASSES}.imports", None, "render", "module_imports.render"),
    Seam(f"{_PASSES}.render_prep", None, "freeze_field_render_plans", "render_plans.freeze_field_render_plans"),
    Seam(
        f"{_PACKAGE}.model.base", "DataModel", "_refresh_field_render_plans", "render_plans.refresh_field_render_plans"
    ),
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


def wrap_hook(owner: type, attribute: str) -> None:
    """Wrap the hook ``attribute`` on ``owner``, the class that defines it in the MRO of a parser, unless it is."""
    seam = next(seam for seam in SEAMS if seam.hook and seam.attribute == attribute)
    if not _wrapped(original := vars(owner)[attribute]):
        setattr(owner, attribute, _wrapper(seam, original))


def report() -> str:
    """Describe every seam as installed now, and each module of the package that holds an unwrapped seam function."""
    lines: list[str] = []
    originals: dict[int, Seam] = {}
    for seam in SEAMS:
        module = import_module(seam.module)
        value = vars(module if seam.owner is None else getattr(module, seam.owner))[seam.attribute]
        hook = " (and on each parser class that overrides it)" if seam.hook else ""
        lines.append(f"{seam.name} <- tests.checks.{seam.wrapper}{hook}{'' if _wrapped(value) else ' (not wrapped)'}\n")
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
