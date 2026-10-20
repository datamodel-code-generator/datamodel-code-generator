"""The decision phase: decisions that read the facts final at B1 and write through their own writer.

``Parser`` enters ``DECIDE`` right after ``__apply_generic_base_class`` (B1). When its registry
(``Parser._DECISION_PASSES``) holds decisions, ``GenerationStore.seal_facts`` builds the one ``FinalFacts`` of the
parse: the live models of every module, as read-only views (``model/_views.py``). Each ``DecisionPass`` then runs
inside ``fact_pass`` with the ``PassDecl`` its ``DecisionSpec`` carries, so the finality check holds its writes to the
facts it declares; it gets ``FinalFacts`` and the writer protocol it names, which ``_DecisionWriter`` implements over
the store. The decisions run in the order ``parser/_pipeline.py:check_order`` computes from their declarations: a
decision that reads what another writes runs after it, every other conflict needs an ``After`` edge in ``decl.after``,
and the registration order breaks ties.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Generic, Protocol, TypeVar

from typing_extensions import ReadOnly, TypedDict, final

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from datamodel_code_generator.model._views import ModelView
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser._pass_decls import PassDecl

__all__ = [
    "DecisionOutcome",
    "DecisionPass",
    "DecisionSpec",
    "DecisionStore",
    "DecisionWriter",
    "FinalFacts",
    "ModuleFacts",
]

W = TypeVar("W", bound="DecisionWriter")


class DecisionSpec(TypedDict, closed=True):
    """A decision: its declaration, whose ``after`` edges are its only explicit order, and the issue it fixes."""

    decl: ReadOnly[PassDecl]
    issue: ReadOnly[str]


class DecisionWriter(Protocol):
    """The writes a decision may make; each decision names the protocol of its own writes, which extends this one."""


class DecisionStore(Protocol):
    """The generation store as the decision writer sees it."""

    def note_changed_models(self, *models: object) -> None:
        """Note that a change outside the store's operations reached ``models``."""


@final
@dataclass(frozen=True, slots=True)
class ModuleFacts:
    """One output module at B1: its path and the models it renders."""

    module: tuple[str, ...]
    live_models: tuple[ModelView, ...]


@final
@dataclass(frozen=True, slots=True)
class FinalFacts:
    """The facts final at B1, built once per parse by ``GenerationStore.seal_facts``: what decisions read.

    ``live_models`` are the models of every module that render, in module order; unused models, which leave their
    modules later, are not among them.
    """

    modules: tuple[ModuleFacts, ...]
    live_models: tuple[ModelView, ...]


@final
@dataclass(frozen=True, slots=True)
class DecisionOutcome:
    """What the decision phase changed: the live models a decision wrote, in model order."""

    changed_models: tuple[DataModel, ...]


@final
@dataclass(frozen=True, slots=True)
class DecisionPass(Generic[W]):
    """One decision: ``run`` reads ``FinalFacts`` and writes through ``writer``, the protocol of its writes."""

    spec: DecisionSpec
    writer: type[W]
    run: Callable[[FinalFacts, W], None]


@final
class _DecisionWriter:
    """The one implementation of the writer protocols, over the generation store; it records the models it changes."""

    __slots__ = ("_changed", "_store")

    def __init__(self, store: DecisionStore) -> None:
        self._store = store
        self._changed: dict[int, DataModel] = {}

    def outcome(self, live_models: Iterable[DataModel]) -> DecisionOutcome:
        """Return the models of ``live_models`` that the writes changed, in their order."""
        changed = self._changed
        return DecisionOutcome(tuple(model for model in live_models if id(model) in changed))
