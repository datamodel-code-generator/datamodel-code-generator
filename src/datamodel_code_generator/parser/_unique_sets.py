"""Write the uniqueItems arrays as sets on the final types (``--use-unique-items-as-set-by-value``, #4834, #4936).

Without the option, ``replace_unique_list_to_set`` and ``replace_merged_unique_list_to_set`` decide the sets in L5,
before root models collapse, type overrides apply and the other modules are processed, and a set item model hashes by
identity. With it they leave every array a list, deduplication and reuse keep apart models whose arrays differ in
``uniqueItems`` (``unique_items_positions`` in their keys), and this decision writes the sets at B1, on the final types:
a list a schema declares unique becomes a set where every value its items take hashes consistently with equality
(``every_value_hashes``): scalars, ``Literal`` values, enums, tuples and frozen sets of such values, types that name
no model, and models whose instances hash by value (``hashes_by_value``: a frozen class that compares by value, or a
frozen Pydantic class that keeps its builtin hash, whose field values hash). A model that hashes only by identity or
never, ``Any`` and ``object``, and the containers whose values do not hash keep the list. Which models hash by value is
decided for all of them together, as the sets of one model's fields decide whether its instances hash: every model its
class allows is taken to, and the models a field contradicts are dropped until none is (a greatest fixed point). The
type of the additional properties that a TypedDict writes as ``extra_items`` is decided as a field built from the
additionalProperties schema (``additional_properties_holder``).
"""

from __future__ import annotations

from collections.abc import Mapping
from contextlib import suppress
from typing import TYPE_CHECKING, Any, Protocol, cast

from datamodel_code_generator._container_kinds import (
    ARRAYS,
    CONTAINER_FLAGS,
    CONTAINERS,
    MAPPINGS,
    UNHASHABLE_CONTAINERS,
    WRITTEN_CONTAINERS,
    written_slot,
)
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator.model._decisions.hashability import (
    ValueHashItem,
    every_value_hashes,
    hashes_by_value,
    python_type_value_hash,
)
from datamodel_code_generator.parser._passes._shared import (
    UniqueItemsPlan,
    _declared_unique_arrays,
    additional_properties_holder,
    plan_unique_sets,
)
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._decisions.hashability import SetItemHash
    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
    from datamodel_code_generator.parser._passes._shared import AdditionalPropertiesHolder
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["WRITE_UNIQUE_ITEMS_AS_SETS", "UniqueSetsWriter"]

_ANY_TYPES = frozenset({"Any", "object"})
"""The types whose values are any value, some of which do not hash."""


class UniqueSetsWriter(DecisionWriter, Protocol):
    """The write of the decision: write lists of a field's type as sets, and its default as the sets write it."""

    def write_sets(
        self, model: ModelView, field: FieldView, lists: Sequence[DataTypeView], default: object | None
    ) -> None:
        """Write each of ``lists``, types of ``field`` of ``model``, as a set, and ``default`` but None as its default.

        The field gets a copy of its type tree with the sets; the nodes other fields share stay as they are.
        """

    def write_additional_properties_sets(self, model: ModelView, lists: Sequence[DataTypeView]) -> None:
        """Write each of ``lists``, types of the additional properties of ``model`` (``extra_items``), as a set.

        The model takes a copy of the type tree with the sets; the nodes stay as they are.
        """


class _ByValue:
    """The reading of the decision: the class hash of each live model and the models taken to hash by value.

    ``aliases`` tells, by key, whether every value of a type alias hashes, read again with each reading of
    ``value_keys`` (``read_aliases``).
    """

    __slots__ = (
        "_alias_order",
        "_alias_sets",
        "_keeps_tuples",
        "aliases",
        "class_hash",
        "live",
        "value_keys",
    )

    def __init__(self, facts: FinalFacts) -> None:
        self.live: dict[ModelKey, ModelView] = {model_key_of(model.reference): model for model in facts.live_models}
        self.class_hash: dict[ModelKey, SetItemHash] = {
            key: model.set_item_hash(user_imports=facts.options.user_imports) for key, model in self.live.items()
        }
        self.value_keys = {key for key, class_hash in self.class_hash.items() if class_hash == "value"}
        self._keeps_tuples = facts.options.keeps_tuples
        self._alias_order = self._aliases_first()
        self.aliases: dict[ModelKey, bool] = {}
        self._alias_sets: dict[ModelKey, set[int]] = {}

    def _named_aliases(self, key: ModelKey) -> Iterator[ModelKey]:
        """Yield the type aliases with a value that the value of the alias ``key`` names, by key."""
        pending = [self.live[key].fields[0].data_type]
        while pending:
            if (reference := (data_type := pending.pop()).reference) is not None:
                if self.class_hash.get(named := model_key_of(reference)) == "alias" and self.live[named].fields:
                    yield named
            else:
                pending.extend(data_type.data_types)

    def _aliases_first(self) -> list[ModelKey]:
        """Return the type aliases with a value, by key, each after the aliases its value names.

        They are walked with a stack of their own, so a chain of aliases of any length is ordered; of a cycle of
        aliases, the alias the walk enters it at comes last.
        """
        ordered: dict[ModelKey, None] = {}
        visiting: set[ModelKey] = set()
        for alias in (key for key, class_hash in self.class_hash.items() if class_hash == "alias"):
            if alias in visiting or not self.live[alias].fields:
                continue
            visiting.add(alias)
            stack = [(alias, self._named_aliases(alias))]
            while stack:
                current, pending = stack[-1]
                if (target := next((key for key in pending if key not in visiting), None)) is None:
                    stack.pop()
                    ordered[current] = None
                else:
                    visiting.add(target)
                    stack.append((target, self._named_aliases(target)))
        return list(ordered)

    def read_aliases(self) -> None:
        """Read whether every value of each type alias hashes, as ``value_keys`` reads the models, in alias order.

        An alias is read as its value, whose lists become sets as its own plan says, after the aliases its value
        names; one read after it, the alias a cycle of aliases is entered at, takes values that may not hash, as a
        value that holds itself does.
        """
        self.aliases = {}
        for key in self._alias_order:
            value = self.live[key].fields[0]
            sets = self._alias_sets[key] = {*map(id, self.plan(value).sets)}
            self.aliases[key] = every_value_hashes(self.item(value.data_type, sets))

    def plan(self, field: FieldView | AdditionalPropertiesHolder) -> UniqueItemsPlan:
        """Return the arrays of ``field`` that become sets.

        The type of the additional properties of a model (``additional_properties_holder``) is read as a field.
        """
        live = cast("Any", field)
        return plan_unique_sets(
            live,
            (group for _, group in _declared_unique_arrays(live, "all")),
            default=list(default) if isinstance(default := field.default, (list, tuple)) else None,
            keeps_tuples=self._keeps_tuples,
            can_hold=lambda node, sets: all(
                every_value_hashes(self.item(item, sets)) for item in cast("Any", node).data_types
            ),
        )

    def item(self, data_type: DataTypeView, sets: Collection[int]) -> ValueHashItem:  # noqa: PLR0911
        """Return the facts of an item type that ``every_value_hashes`` reads.

        ``sets`` holds the lists of the type that become sets, by id. A container is read as it is written: a set
        under --use-generic-container-types is a frozen set. A type alias is read as ``aliases`` says, a type that a
        bound Python type annotation writes as that annotation (``python_type_value_hash``).
        """
        if (reference := data_type.reference) is not None and (key := model_key_of(reference)) in self.live:
            match self.class_hash[key]:
                case "alias":
                    return ValueHashItem(hashes=self.aliases.get(key, False))
                case "value":
                    return ValueHashItem(hashes=key in self.value_keys)
            return ValueHashItem(hashes=False)
        if (python_type := data_type.python_type) is not None:
            return python_type_value_hash(python_type.expression)

        def read(nested: DataTypeView) -> ValueHashItem:
            return self.item(nested, sets)

        if data_type.is_tuple:
            count = data_type.tuple_item_count
            positions = data_type.data_types if count is None else data_type.data_types[: min(count, 1)]
            return ValueHashItem(members=tuple(map(read, positions)))
        slot = (
            "set"
            if id(data_type) in sets
            else next((slot for slot in WRITTEN_CONTAINERS if getattr(data_type, CONTAINER_FLAGS[slot])), None)
        )
        if slot is None:
            if data_type.data_types:
                return ValueHashItem(members=tuple(map(read, data_type.data_types)))
            return ValueHashItem(hashes=data_type.type not in _ANY_TYPES)
        if written_slot(slot, generic=cast("Any", data_type).use_generic_container) in UNHASHABLE_CONTAINERS.slots:
            return ValueHashItem(hashes=False)
        return ValueHashItem(members=tuple(map(read, data_type.data_types)))

    def written_default(self, field: FieldView, plan: UniqueItemsPlan) -> object | None:
        """Return the array or object default of a field as its final types write it, or None where they write no set.

        An array of the default that sits where a type writes a set, a list of the field's type that ``plan`` makes a
        set or one of the value of a type alias that the alias's own plan makes one, is the set of its items, a frozen
        set where the set is written as one (--use-generic-container-types); its items are read first, and an array
        whose items do not hash stays a list. A union reads an array as its first array-like member and an object as
        its first mapping. The other arrays stay lists and the objects dicts, as the schema writes them.
        """
        if not isinstance(default := field.default, (list, tuple, Mapping)):
            return None
        written, changed = self._written_value(default, (field.data_type,), {*map(id, plan.sets)})
        return written if changed else None

    def _written_value(self, value: object, members: Sequence[Any], sets: Collection[int]) -> tuple[object, bool]:
        """Return a value of a default as the types ``members`` write it (``written_default``), and if it holds a set.

        ``sets`` holds, by id, the lists of the tree the members are in that become sets.
        """
        if (found := self._value_node(value, members, sets)) is None:
            return _plain(value), False
        node, node_sets = found
        if isinstance(value, Mapping):
            values = {key: self._written_value(item, node.data_types, node_sets) for key, item in value.items()}
            return {key: item for key, (item, _) in values.items()}, any(changed for _, changed in values.values())
        written = [
            self._written_value(item, _item_types(node, index), node_sets)
            for index, item in enumerate(cast("Sequence[object]", value))
        ]
        items, changed = [item for item, _ in written], any(changed for _, changed in written)
        if id(node) in node_sets:
            written_set = frozenset if written_slot("set", generic=node.use_generic_container) == "frozen_set" else set
            with suppress(TypeError):
                return written_set(items), True
        return items, changed

    def _value_node(
        self, value: object, members: Sequence[Any], sets: Collection[int]
    ) -> tuple[Any, Collection[int]] | None:
        """Return the array or mapping of ``members`` that takes an array or object value, with the sets of its tree.

        The members of a union are read in order, a type alias as its value, with the lists its plan makes sets.
        """
        kind = MAPPINGS if isinstance(value, Mapping) else ARRAYS if isinstance(value, (list, tuple)) else None
        if kind is None:
            return None
        pending: list[tuple[Any, Collection[int]]] = [(member, sets) for member in reversed(members)]
        followed: set[ModelKey] = set()
        while pending:
            node, node_sets = pending.pop()
            if (reference := node.reference) is not None:
                if (key := model_key_of(reference)) in self._alias_sets and key not in followed:
                    followed.add(key)
                    pending.append((self.live[key].fields[0].data_type, self._alias_sets[key]))
            elif node in kind:
                return node, node_sets
            elif node not in CONTAINERS:
                pending.extend((member, node_sets) for member in reversed(node.data_types))
        return None

    def fields(self, model: ModelView) -> Iterator[FieldView]:
        """Yield the fields of a model and of the live models it inherits from, each model once and each name once.

        A name is the field of the first model that declares it, the model before its bases, as a field a model
        declares again replaces the one it inherits in its instances. The members of an enum are no fields of its
        instances, which are the members themselves.
        """
        seen: dict[int, ModelView] = {}
        found = {} if model.form == "enum" else {id(model): model}
        while found:
            seen |= found
            found = {
                id(base): base for current in found.values() for base in self._bases(current) if id(base) not in seen
            }
        names: set[str | None] = set()
        for current in seen.values():
            for field in current.fields:
                if field.name is None or field.name not in names:
                    names.add(field.name)
                    yield field

    def _bases(self, model: ModelView) -> Iterator[ModelView]:
        """Return the live models a model names as its bases."""
        return (
            base
            for data_type in model.base_classes
            if (reference := data_type.reference) is not None and (base := self.live.get(model_key_of(reference)))
        )

    def field_values_hash(self, field: FieldView, plan: UniqueItemsPlan) -> bool:
        """Return whether every value a field holds hashes, once its planned lists are sets.

        An array default is a list or a set, which does not hash (a view reads a list as a tuple), unless it is written
        as a frozen set (``written_default``), and so is the value of each default factory a schema may name (``dict``,
        ``list`` and ``set``).
        """
        if (default := self.written_default(field, plan)) is None:
            default = field.default
        if isinstance(default, (list, tuple, set)) or "default_factory" in field.extras:
            return False
        try:
            hash(default)
        except TypeError:
            return False
        return every_value_hashes(self.item(field.data_type, {*map(id, plan.sets)}))


def _write_unique_items_as_sets(facts: FinalFacts, writer: UniqueSetsWriter) -> None:
    """Write the uniqueItems lists of every live model whose final items hash by value as sets.

    Which models hash by value is read again until no field contradicts it, then the plans of that reading are
    written, with the defaults as the final types write them (``_ByValue.written_default``), read before any write
    replaces a type tree.
    """
    reading = _ByValue(facts)
    while True:
        reading.read_aliases()
        plans = {id(field): reading.plan(field) for model in facts.live_models for field in model.fields}
        kept = {
            key
            for key in reading.value_keys
            if hashes_by_value(
                reading.class_hash[key],
                (
                    reading.field_values_hash(field, plans.get(id(field)) or reading.plan(field))
                    for field in reading.fields(reading.live[key])
                ),
            )
        }
        if kept == reading.value_keys:
            break
        reading.value_keys = kept
    fields = [(model, field) for model in facts.live_models for field in model.fields]
    defaults = {id(field): reading.written_default(field, plans[id(field)]) for _, field in fields}
    additional = [
        (model, plan)
        for model in facts.live_models
        if (holder := additional_properties_holder(model)) is not None and (plan := reading.plan(holder)).sets
    ]
    for model, field in fields:
        default = defaults[id(field)]
        if (plan := plans[id(field)]).sets or default is not None:
            writer.write_sets(model, field, plan.sets, default)
    for model, plan in additional:
        writer.write_additional_properties_sets(model, plan.sets)


def _item_types(node: Any, index: int) -> Sequence[Any]:
    """Return the types that the item at ``index`` of an array of ``node`` takes: its position's of a tuple."""
    if node.is_tuple and node.tuple_item_count is None:
        return node.data_types[index : index + 1]
    return node.data_types


def _plain(value: object) -> object:
    """Return a value of a default with its arrays as lists and its objects as dicts, as the schema writes them."""
    match value:
        case list() | tuple():
            return [_plain(item) for item in value]
        case Mapping():
            return {key: _plain(item) for key, item in value.items()}
    return value


WRITE_UNIQUE_ITEMS_AS_SETS = DecisionPass(
    {
        "name": "write_unique_items_as_sets",
        "issue": "#4834",
    },
    UniqueSetsWriter,
    _write_unique_items_as_sets,
    when=lambda parser: parser.use_unique_items_as_set and parser.use_unique_items_as_set_by_value,
)
