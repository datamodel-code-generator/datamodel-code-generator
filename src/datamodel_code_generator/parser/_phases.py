"""The decision phase: decisions that read the facts final at B1 and write through the decision writer.

``Parser`` enters ``DECIDE`` right after ``__apply_generic_base_class`` (B1). When its registry
(``Parser._DECISION_PASSES``) holds decisions, ``GenerationStore.seal_facts`` builds the one ``FinalFacts`` of the
parse: the live models of every module, of which a decision reads only the facts final at B1. Each ``DecisionPass``
then runs, in registry order, with ``FinalFacts`` and ``_DecisionWriter``, the one writer over the store; a decision
calls only its own writes, the methods that ``writer_methods`` of its declaration names (heki's contracts in
``scripts/heki.toml``, which ``scripts/build_heki_contracts.py`` writes, report any other call and the reads of a render
fact). Its ``DecisionSpec``
names its declaration in ``scripts/pass_decls.py`` (``DECISION_DECLS``), to which the finality check of the test suite
(``tests/checks/finality.py``) holds its writes; ``scripts/check_pass_pipeline.py`` checks that the registry lists the
decisions in the order the order rule computes from their declarations: a decision that reads what another writes runs
after it, every other conflict needs an ``After`` edge, and the registration order breaks ties.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol

from typing_extensions import ReadOnly, TypedDict, final

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Mapping, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "DecisionOptions",
    "DecisionOutcome",
    "DecisionPass",
    "DecisionSpec",
    "DecisionStore",
    "FinalFacts",
    "ModuleFacts",
    "container_copy",
    "live_model_of",
    "live_models_by_key",
    "rewrite_additional_properties",
    "rewrite_copied_type",
    "rewrite_field_copy",
]


class DecisionSpec(TypedDict, closed=True):
    """A decision: the name of its declaration (``scripts/pass_decls.py:DECISION_DECLS``) and the issue it fixes."""

    name: ReadOnly[str]
    issue: ReadOnly[str]


class DecisionStore(Protocol):
    """The generation store as the decision writer sees it."""

    def drop_held_null(self, data_type: Any) -> None:
        """Drop the None a type restates of the model it names, which holds null itself."""

    def replace_field_type(self, field_: Any, new_data_type: Any) -> None:
        """Replace a field's data type."""

    def set_field_constraints(self, field_: Any, constraints: Any) -> None:
        """Write a field's constraints."""

    def mark_serialized_as_any(self, model: Any, *, wrapped: bool) -> None:
        """Record whether a type naming the model is wrapped in ``SerializeAsAny``."""

    def swap_data_type(self, data_type: Any, new_data_type: Any, *, refresh: Any = "now") -> None:
        """Put ``new_data_type`` where ``data_type`` is in its parent."""

    def set_field_default(self, field_: Any, default: Any) -> None:
        """Write a field's default."""

    def omit_field_default(self, field_: Any) -> None:
        """Write a field without its default: the default None and the ``nullable`` it has without the default."""

    def replace_additional_properties_type(self, model: Any, data_type: Any) -> None:
        """Give a model the type of the additional properties its settings write (TypedDict ``extra_items``)."""

    def set_nested_data_types(self, data_type: DataType, nested_data_types: Iterable[DataType]) -> None:
        """Write the members of a data type."""

    def detach_data_type_ref(self, data_type: DataType) -> None:
        """Remove a data type from the users of its reference."""


@final
@record
class DecisionOptions:
    """The options of the parse that decisions read beside the models."""

    keeps_tuples: bool = False
    """--use-tuple-for-closed-arrays writes a closed array as a tuple, which keeps its positions."""
    user_imports: bool = False
    """--additional-imports may shadow the builtin annotations that hashability reads."""
    kept_rows: frozenset[str] = frozenset()
    """The ``legacy-*`` rows the decisions keep: those whose option the parse leaves off."""


@final
@record
class ModuleFacts:
    """One output module at B1: its path and the models it renders."""

    module: tuple[str, ...]
    live_models: tuple[DataModel, ...]


@final
@record
class FinalFacts:
    """The facts final at B1, built once per parse by ``GenerationStore.seal_facts``: what decisions read.

    ``live_models`` are the models of every module that render, in module order; unused models, which leave their
    modules later, are not among them. ``member_keywords`` gives the type-dependent keywords the value of a union
    member takes in the output model, with the models a reference names (``DataTypeManager.member_constraint_keywords``
    at the target Python version). ``constrained_member`` gives the type that holds keywords in place of a plain string
    member, as the output writes a constrained string inside a type, or None for any other member
    (``DataTypeManager.constrained_string_member``).
    """

    modules: tuple[ModuleFacts, ...]
    live_models: tuple[DataModel, ...]
    member_keywords: Callable[[DataType, Callable[[Reference], DataModel | None]], frozenset[str] | None]
    constrained_member: Callable[[DataType, Mapping[str, object]], object | None]
    options: DecisionOptions = DecisionOptions()


def live_models_by_key(facts: FinalFacts) -> dict[ModelKey, DataModel]:
    """Return the live models of ``facts`` by model key: the models a reference names."""
    return {model_key_of(model.reference): model for model in facts.live_models}


def live_model_of(facts: FinalFacts) -> Callable[[Reference], DataModel | None]:
    """Return the live model of ``facts`` a reference names, or None when it names no live model."""
    models = live_models_by_key(facts)

    def model_of(reference: Reference) -> DataModel | None:
        return models.get(model_key_of(reference))

    return model_of


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
class DecisionPass:
    """One decision: ``run`` reads ``FinalFacts`` and writes through the decision writer.

    A decision behind an option names it in ``when``; the phase seals no facts when no decision runs.
    """

    spec: DecisionSpec
    run: Callable[[FinalFacts, _DecisionWriter], None]
    when: Callable[[Any], bool] | None = None
    """Whether the decision runs for a parser, from its options; it always does without one."""


@final
class _DecisionWriter:
    """The writes of the decisions, over the generation store; it records the models it changes.

    Each write belongs to one decision, which names it in ``writer_methods`` of its declaration
    (``scripts/pass_decls.py``).
    """

    __slots__ = ("_changed", "_copy", "_merged_str_like_unions", "_store")

    def __init__(self, store: DecisionStore, copy: Callable[[Any], Any]) -> None:
        self._store = store
        self._copy = copy
        self._changed: dict[int, DataModel] = {}
        self._merged_str_like_unions: list[tuple[DataModel, DataModelFieldBase, tuple[DataType, ...]]] = []

    def drop_held_null(self, model: DataModel, field: DataModelFieldBase, data_types: Sequence[DataType]) -> None:
        """Drop the None each of ``data_types``, types of ``field`` of ``model``, restates (``_held_null.py``).

        Fields share type nodes, so the field gets a copy of its type tree that loses the Nones, and the nodes
        stay as the other fields see them.
        """
        dropped = {id(data_type) for data_type in data_types}
        copied, copies = self._copied_type(field)
        for source, target in copies.items():
            if source in dropped:
                self._store.drop_held_null(target)
        self._store.replace_field_type(field, copied)
        self._mark_changed(model)

    def drop_field_constraints(
        self,
        model: DataModel,
        field: DataModelFieldBase,
        keywords: frozenset[str],
        members: Sequence[tuple[DataType, object]],
    ) -> None:
        """Write the constraints of ``field`` of ``model`` without ``keywords`` (``_union_constraints.py``).

        Each member of ``members``, a type of the field, is written as the type paired with it, which holds the
        keywords in its place; the field takes a copy of its type tree with them (``rewrite_copied_type``), and the
        nodes stay as the other fields see them.
        """
        if members:
            rewrites = {id(member): lambda _target, written=written: written for member, written in members}
            self._store.replace_field_type(
                field, rewrite_copied_type(self._store, self._copy, field.data_type, rewrites)
            )
        constraints: Any = field.constraints
        values = {key: value for key, value in constraints.set_values().items() if key not in keywords}
        self._store.set_field_constraints(field, type(constraints).model_validate(values))
        self._mark_changed(model)

    def mark_serialized_as_any(self, model: DataModel, *, wrapped: bool) -> None:
        """Record whether a type naming ``model`` is wrapped in ``SerializeAsAny`` (``_serialize_as_any.py``)."""
        self._store.mark_serialized_as_any(model, wrapped=wrapped)
        self._mark_changed(model)

    def write_sets(
        self, model: DataModel, field: DataModelFieldBase, lists: Sequence[DataType], default: object | None
    ) -> None:
        """Write each of ``lists``, types of ``field`` of ``model``, as a set, and ``default`` (``_unique_sets.py``).

        Fields share type nodes, so the field gets a copy of its type tree, whose copies of the lists are then swapped
        for sets, inner ones first; the nodes stay as the other fields see them.
        """
        rewrite_field_copy(self._store, self._copy, field, container_copy, ids={id(data_type) for data_type in lists})
        if default is not None:
            self._store.set_field_default(field, default)
        self._mark_changed(model)

    def write_additional_properties_sets(self, model: DataModel, lists: Sequence[DataType]) -> None:
        """Write each of ``lists``, types of the additional properties of ``model``, as a set (``_unique_sets.py``).

        The model takes a copy of the type tree with the sets (``rewrite_copied_type``); the nodes stay as they are.
        """
        rewrite_additional_properties(self._store, self._copy, model, lists, container_copy)
        self._mark_changed(model)

    def omit_default(self, model: DataModel, field: DataModelFieldBase) -> None:
        """Write ``field`` of ``model`` without its default (``_incomplete_defaults.py``)."""
        self._store.omit_field_default(field)
        self._mark_changed(model)

    def merge_str_like_members(
        self,
        model: DataModel,
        field: DataModelFieldBase,
        rewrites: Sequence[tuple[DataType, Sequence[DataType | str | tuple[bool | int | str, ...]]]],
        merged: Sequence[DataType],
    ) -> None:
        """Write each union of ``rewrites``, a type of ``field`` of ``model``, with its members.

        Fields share type nodes, so the field gets a copy of its type tree, whose copies of the unions take the copies
        of their members, a new type for a name (``str``, ``None``) and a new ``Literal`` for a tuple of values; the
        copies of ``merged`` leave the users of their references.
        """
        copied, copies = self._copied_type(field)
        self._store.replace_field_type(field, copied)
        for member in merged:
            if (target := copies[id(member)]).reference is not None:
                self._store.detach_data_type_ref(target)
        for union, members in rewrites:
            target = copies[id(union)]
            self._store.set_nested_data_types(target, [_rewritten_member(target, member, copies) for member in members])
        self._merged_str_like_unions.append((model, field, tuple(merged)))
        self._mark_changed(model)

    def _copied_type(self, field: DataModelFieldBase) -> tuple[DataType, dict[int, DataType]]:
        """Return a copy of the type tree of ``field``, and the copy of each of its nodes by the id of the node.

        Fields share type nodes, so a write goes to the copy, which the field then takes; the nodes stay as the other
        fields see them.
        """
        original: Any = field.data_type
        copied = self._copy(original)
        return copied, {
            id(source): target for source, target in zip(original.all_data_types, copied.all_data_types, strict=True)
        }

    def _mark_changed(self, model: DataModel) -> None:
        """Record that a write changed ``model``, a live model."""
        self._changed[id(model)] = model

    def outcome(self, live_models: Iterable[DataModel]) -> DecisionOutcome:
        """Return the models of ``live_models`` that the writes changed, in their order, and the merged unions."""
        changed = self._changed
        return DecisionOutcome(
            tuple(model for model in live_models if id(model) in changed), tuple(self._merged_str_like_unions)
        )


def _rewritten_member(
    owner: DataType, member: DataType | str | tuple[bool | int | str, ...], copies: Mapping[int, DataType]
) -> DataType:
    """Return the member a union of a copied type tree is written with (``merge_str_like_members``)."""
    if isinstance(member, str):
        return type(owner)(type=member)
    if isinstance(member, tuple):
        return type(owner)(literals=list(member))
    return copies[id(member)]


def container_copy(data_type: Any, *, is_set: bool = True) -> Any:
    """Return the set copy of a list type, or with ``is_set`` false the list copy of a set type.

    The copy holds the items of the type.
    """
    copied = data_type.model_copy(update={"is_list": not is_set, "is_set": is_set})
    for item in copied.data_types:
        item.parent = copied
    return copied


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


def rewrite_field_copy(
    store: DecisionStore,
    copy: Callable[[Any], Any],
    field: Any,
    rewrite: Callable[[Any], Any],
    *,
    ids: Collection[int] = (),
    paths: Iterable[tuple[int, ...]] = (),
) -> None:
    """Give ``field`` a copy of its type tree, whose copies of the chosen nodes are swapped for what ``rewrite`` makes.

    Fields share type nodes, so the nodes stay as the other fields see them. The nodes are chosen by id (``ids``, inner
    ones first) or by their paths of indexes from the root (``paths``, in their order): a node that the tree holds at
    two paths is chosen at both by its id, and at the paths listed by its paths.
    """
    original = field.data_type
    copied = copy(original)
    store.replace_field_type(field, copied)
    for source, target in zip(original.all_data_types, copied.all_data_types, strict=True):
        if id(source) in ids:
            store.swap_data_type(target, rewrite(target))
    for path in paths:
        target = copied
        for index in path:
            target = target.data_types[index]
        store.swap_data_type(target, rewrite(target))


def rewrite_additional_properties(
    store: DecisionStore, copy: Callable[[Any], Any], model: Any, nodes: Iterable[Any], rewrite: Callable[[Any], Any]
) -> None:
    """Write the type of the additional properties of ``model`` with each of ``nodes`` as ``rewrite`` makes it.

    The model takes a copy of the type tree with the rewrites (``rewrite_copied_type``); the nodes stay as they are.
    """
    rewrites = dict.fromkeys(map(id, nodes), rewrite)
    store.replace_additional_properties_type(
        model, rewrite_copied_type(store, copy, model.additional_properties_type, rewrites)
    )
