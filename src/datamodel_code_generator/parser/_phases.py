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

from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar

from typing_extensions import ReadOnly, TypedDict, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
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

    def drop_held_null(self, data_type: Any) -> None:
        """Drop the None a type restates of the model it names, which holds null itself."""

    def replace_field_type(self, field_: Any, new_data_type: Any) -> None:
        """Replace a field's data type."""


@final
@record
class ModuleFacts:
    """One output module at B1: its path and the models it renders."""

    module: tuple[str, ...]
    live_models: tuple[ModelView, ...]


@final
@record
class FinalFacts:
    """The facts final at B1, built once per parse by ``GenerationStore.seal_facts``: what decisions read.

    ``live_models`` are the models of every module that render, in module order; unused models, which leave their
    modules later, are not among them.
    """

    modules: tuple[ModuleFacts, ...]
    live_models: tuple[ModelView, ...]


@final
@record
class DecisionOutcome:
    """What the decision phase changed: the live models a decision wrote, in model order."""

    changed_models: tuple[DataModel, ...]


@final
@record
class DecisionPass(Generic[W]):
    """One decision: ``run`` reads ``FinalFacts`` and writes through ``writer``, the protocol of its writes.

    A decision behind an option names it in ``when``; the phase seals no facts when no decision runs.
    """

    spec: DecisionSpec
    writer: type[W]
    run: Callable[[FinalFacts, W], None]
    when: Callable[[Any], bool] | None = None
    """Whether the decision runs for a parser, from its options; it always does without one."""


@final
class _DecisionWriter:
    """The one implementation of the writer protocols, over the generation store; it records the models it changes."""

    __slots__ = ("_changed", "_copy", "_store", "_viewed")

    def __init__(self, store: DecisionStore, viewed: Callable[[Any], Any], copy: Callable[[Any], Any]) -> None:
        self._store = store
        self._viewed = viewed
        self._copy = copy
        self._changed: dict[int, DataModel] = {}

    def drop_held_null(self, model: ModelView, field: FieldView, data_types: Sequence[DataTypeView]) -> None:
        """Drop the None each of ``data_types``, types of ``field`` of ``model``, restates (``HeldNullWriter``).

        Fields share type nodes, so the field gets a copy of its type tree that loses the Nones, and the nodes
        stay as the other fields see them.
        """
        changed = self._viewed(model)
        field_ = self._viewed(field)
        dropped = {id(self._viewed(data_type)) for data_type in data_types}
        original = field_.data_type
        copied = self._copy(original)
        for source, target in zip(original.all_data_types, copied.all_data_types, strict=True):
            if id(source) in dropped:
                self._store.drop_held_null(target)
        self._store.replace_field_type(field_, copied)
        self._changed[id(changed)] = changed

    def outcome(self, live_models: Iterable[DataModel]) -> DecisionOutcome:
        """Return the models of ``live_models`` that the writes changed, in their order."""
        changed = self._changed
        return DecisionOutcome(tuple(model for model in live_models if id(model) in changed))
