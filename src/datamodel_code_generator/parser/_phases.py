"""The decision phase: decisions that read the facts final at B1 and write through their own writer.

``Parser`` enters ``DECIDE`` right after ``__apply_generic_base_class`` (B1). When its registry
(``Parser._DECISION_PASSES``) holds decisions, ``GenerationStore.seal_facts`` builds the one ``FinalFacts`` of the
parse: the live models of every module, typed as the read-only views of ``model/_views.py``. Each ``DecisionPass``
then runs, in registry order, with ``FinalFacts`` and the writer protocol it names, which ``_DecisionWriter``
implements over the store. Its ``DecisionSpec`` names its declaration in ``scripts/pass_decls.py``
(``DECISION_DECLS``), to which the finality check of the test suite (``tests/checks/finality.py``) holds its writes;
``scripts/check_pass_pipeline.py`` checks that the registry lists the decisions in the order the order rule computes
from their declarations: a decision that reads what another writes runs after it, every other conflict needs an
``After`` edge, and the registration order breaks ties.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Generic, Protocol, TypeVar, cast

from typing_extensions import ReadOnly, TypedDict, final

from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView, ReferenceView
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.types import DataType

__all__ = [
    "DecisionOptions",
    "DecisionOutcome",
    "DecisionPass",
    "DecisionSpec",
    "DecisionStore",
    "DecisionWriter",
    "FinalFacts",
    "ModuleFacts",
    "rewrite_copied_type",
]

W = TypeVar("W", bound="DecisionWriter")


class DecisionSpec(TypedDict, closed=True):
    """A decision: the name of its declaration (``scripts/pass_decls.py:DECISION_DECLS``) and the issue it fixes."""

    name: ReadOnly[str]
    issue: ReadOnly[str]


class DecisionWriter(Protocol):
    """The writes a decision may make; each decision names the protocol of its own writes, which extends this one."""


class DecisionStore(Protocol):
    """The generation store as the decision writer sees it."""

    def drop_held_null(self, data_type: Any) -> None:
        """Drop the None a type restates of the model it names, which holds null itself."""

    def replace_field_type(self, field_: Any, new_data_type: Any) -> None:
        """Replace a field's data type."""

    def set_field_constraints(self, field_: Any, constraints: Any) -> None:
        """Write a field's constraints."""

    def mark_serialized_as_any(self, model: Any, wrapped: bool) -> None:  # noqa: FBT001
        """Record whether a type naming the model is wrapped in ``SerializeAsAny``."""

    def swap_data_type(self, data_type: Any, new_data_type: Any, *, refresh: Any = "now") -> None:
        """Put ``new_data_type`` where ``data_type`` is in its parent."""

    def set_field_default(self, field_: Any, default: Any) -> None:
        """Write a field's default."""

    def replace_additional_properties_type(self, model: Any, data_type: Any) -> None:
        """Give a model the type of the additional properties its settings write (TypedDict ``extra_items``)."""

    def set_nested_data_types(self, data_type: Any, nested_data_types: Any) -> None:
        """Write the members of a data type."""

    def detach_data_type_ref(self, data_type: Any) -> None:
        """Remove a data type from the users of its reference."""


@final
@record
class DecisionOptions:
    """The options of the parse that decisions read beside the models."""

    keeps_tuples: bool = False
    """--use-tuple-for-closed-arrays writes a closed array as a tuple, which keeps its positions."""
    user_imports: bool = False
    """--additional-imports may shadow the builtin annotations that hashability reads."""


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
    modules later, are not among them. ``member_keywords`` gives the type-dependent keywords the value of a union
    member takes in the output model, with the models a reference names (``DataTypeManager.member_constraint_keywords``
    at the target Python version).
    """

    modules: tuple[ModuleFacts, ...]
    live_models: tuple[ModelView, ...]
    member_keywords: Callable[[DataTypeView, Callable[[ReferenceView], ModelView | None]], frozenset[str] | None]
    options: DecisionOptions = DecisionOptions()


@final
@record
class DecisionOutcome:
    """What the decision phase changed: the live models a decision wrote, in model order.

    ``merged_str_like_unions`` holds each field whose str-like union members ``--merge-msgspec-str-like-union-members``
    wrote as one ``str``, with its model and the members it lost, which the msgspec decoding warnings name.
    """

    changed_models: tuple[DataModel, ...]
    merged_str_like_unions: tuple[tuple[DataModel, DataModelFieldBase, tuple[DataType, ...]], ...] = ()


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

    __slots__ = ("_changed", "_copy", "_merged_str_like_unions", "_store")

    def __init__(self, store: DecisionStore, copy: Callable[[Any], Any]) -> None:
        self._store = store
        self._copy = copy
        self._changed: dict[int, DataModel] = {}
        self._merged_str_like_unions: list[tuple[DataModel, DataModelFieldBase, tuple[DataType, ...]]] = []

    def drop_held_null(self, model: ModelView, field: FieldView, data_types: Sequence[DataTypeView]) -> None:
        """Drop the None each of ``data_types``, types of ``field`` of ``model``, restates (``HeldNullWriter``).

        Fields share type nodes, so the field gets a copy of its type tree that loses the Nones, and the nodes
        stay as the other fields see them.
        """
        dropped = {id(data_type) for data_type in data_types}
        original: Any = field.data_type
        copied = self._copy(original)
        for source, target in zip(original.all_data_types, copied.all_data_types, strict=True):
            if id(source) in dropped:
                self._store.drop_held_null(target)
        self._store.replace_field_type(field, copied)
        self._changed[id(model)] = cast("DataModel", model)

    def drop_field_constraints(self, model: ModelView, field: FieldView, keywords: frozenset[str]) -> None:
        """Write the constraints of ``field`` of ``model`` without ``keywords`` (``UnionConstraintsWriter``)."""
        constraints: Any = field.constraints
        values = {
            key: value
            for key, value in constraints.model_dump(by_alias=True).items()
            if value is not None and key not in keywords
        }
        self._store.set_field_constraints(field, type(constraints).model_validate(values))
        self._changed[id(model)] = cast("DataModel", model)

    def mark_serialized_as_any(self, model: ModelView, wrapped: bool) -> None:  # noqa: FBT001
        """Record whether a type naming ``model`` is wrapped in ``SerializeAsAny`` (``SerializeAsAnyWriter``)."""
        self._store.mark_serialized_as_any(model, wrapped)
        self._changed[id(model)] = cast("DataModel", model)

    def write_sets(
        self, model: ModelView, field: FieldView, lists: Sequence[DataTypeView], default: object | None
    ) -> None:
        """Write each of ``lists``, types of ``field`` of ``model``, as a set, and ``default`` (``UniqueSetsWriter``).

        Fields share type nodes, so the field gets a copy of its type tree, whose copies of the lists are then swapped
        for sets, inner ones first; the nodes stay as the other fields see them.
        """
        planned = {id(data_type) for data_type in lists}
        original: Any = field.data_type
        copied = self._copy(original)
        self._store.replace_field_type(field, copied)
        for source, target in zip(original.all_data_types, copied.all_data_types, strict=True):
            if id(source) in planned:
                self._store.swap_data_type(target, _set_of(target))
        if default is not None:
            self._store.set_field_default(field, default)
        self._changed[id(model)] = cast("DataModel", model)

    def write_additional_properties_sets(self, model: ModelView, lists: Sequence[DataTypeView]) -> None:
        """Write each of ``lists``, types of the additional properties of ``model``, as a set (``UniqueSetsWriter``).

        The model takes a copy of the type tree with the sets (``rewrite_copied_type``); the nodes stay as they are.
        """
        original: Any = model.additional_properties_type
        rewrites = dict.fromkeys(map(id, lists), _set_of)
        self._store.replace_additional_properties_type(
            model, rewrite_copied_type(self._store, self._copy, original, rewrites)
        )
        self._changed[id(model)] = cast("DataModel", model)

    def merge_str_like_members(
        self,
        model: ModelView,
        field: FieldView,
        rewrites: Sequence[tuple[DataTypeView, Sequence[DataTypeView | None]]],
        merged: Sequence[DataTypeView],
    ) -> None:
        """Write each union of ``rewrites``, a type of ``field`` of ``model``, with its members (StrLikeUnionWriter).

        Fields share type nodes, so the field gets a copy of its type tree, whose copies of the unions take the copies
        of their new members and a new ``str`` for None; the copies of ``merged`` leave the users of their references.
        """
        original: Any = field.data_type
        copied = self._copy(original)
        copies = {
            id(source): target for source, target in zip(original.all_data_types, copied.all_data_types, strict=True)
        }
        self._store.replace_field_type(field, copied)
        for member in merged:
            if (target := copies[id(member)]).reference is not None:
                self._store.detach_data_type_ref(target)
        for union, members in rewrites:
            target = copies[id(union)]
            self._store.set_nested_data_types(
                target, [type(target)(type="str") if member is None else copies[id(member)] for member in members]
            )
        self._merged_str_like_unions.append((
            cast("DataModel", model),
            cast("DataModelFieldBase", field),
            tuple(cast("DataType", member) for member in merged),
        ))
        self._changed[id(model)] = cast("DataModel", model)

    def outcome(self, live_models: Iterable[DataModel]) -> DecisionOutcome:
        """Return the models of ``live_models`` that the writes changed, in their order, and the merged unions."""
        changed = self._changed
        return DecisionOutcome(
            tuple(model for model in live_models if id(model) in changed), tuple(self._merged_str_like_unions)
        )


def _set_of(data_type: Any) -> Any:
    """Return the set copy of a list type, which holds its items."""
    set_type = data_type.model_copy(update={"is_list": False, "is_set": True})
    for item in set_type.data_types:
        item.parent = set_type
    return set_type


def rewrite_copied_type(
    store: DecisionStore, copy: Callable[[Any], Any], data_type: Any, rewrites: Mapping[int, Callable[[Any], Any]]
) -> Any:
    """Return a copy of the type tree of ``data_type`` whose copies of the nodes ``rewrites`` names are rewritten.

    ``rewrites`` maps a node below the root of the tree, by id, to what makes its rewrite of the node's copy; the nodes
    are rewritten inner ones first, so a rewrite holds the rewrites of the nodes it holds, and each is swapped into its
    copied parent through ``store``. The root is the wrapper the type of additional properties always is, no array.
    """
    copied = copy(data_type)
    for source, target in list(zip(data_type.all_data_types, copied.all_data_types, strict=True)):
        if (rewrite := rewrites.get(id(source))) is not None:
            store.swap_data_type(target, rewrite(target))
    return copied
