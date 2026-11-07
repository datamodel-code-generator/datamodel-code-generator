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
class allows is taken to, and the models a field contradicts are dropped until none is (a greatest fixed point).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast

from datamodel_code_generator._container_kinds import (
    CONTAINER_FLAGS,
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
    names_alias_beside_arrays,
    plan_unique_sets,
    without_own_unique_sets,
)
from datamodel_code_generator.parser._phases import DecisionPass, DecisionWriter

if TYPE_CHECKING:
    from collections.abc import Collection, Iterator, Sequence

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._decisions.hashability import SetItemHash
    from datamodel_code_generator.model._views import DataTypeView, FieldView, ModelView
    from datamodel_code_generator.parser._phases import FinalFacts

__all__ = ["WRITE_UNIQUE_ITEMS_AS_SETS", "UniqueSetsWriter"]

_ANY_TYPES = frozenset({"Any", "object"})
"""The types whose values are any value, some of which do not hash."""


class UniqueSetsWriter(DecisionWriter, Protocol):
    """The write of the decision: write lists of a field's type as sets, and its list default as a set."""

    def write_sets(
        self, model: ModelView, field: FieldView, lists: Sequence[DataTypeView], default: set[Any] | None
    ) -> None:
        """Write each of ``lists``, types of ``field`` of ``model``, as a set, and ``default`` as its default.

        The field gets a copy of its type tree with the sets; the nodes other fields share stay as they are.
        """


class _ByValue:
    """The reading of the decision: the class hash of each live model and the models taken to hash by value.

    ``aliases`` tells, by key, whether every value of a type alias hashes, read again with each reading of
    ``value_keys`` (``read_aliases``).
    """

    __slots__ = ("_alias_order", "_beside_arrays", "_keeps_tuples", "aliases", "class_hash", "live", "value_keys")

    def __init__(self, facts: FinalFacts) -> None:
        self.live: dict[ModelKey, ModelView] = {model_key_of(model.reference): model for model in facts.live_models}
        self.class_hash: dict[ModelKey, SetItemHash] = {
            key: model.set_item_hash(user_imports=facts.options.user_imports) for key, model in self.live.items()
        }
        self.value_keys = {key for key, class_hash in self.class_hash.items() if class_hash == "value"}
        self._keeps_tuples = facts.options.keeps_tuples
        self._alias_order = self._aliases_first()
        self.aliases: dict[ModelKey, bool] = {}
        self._beside_arrays = set() if facts.options.decodes_value_unions else self._aliases_beside_arrays()

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

    def _aliases_beside_arrays(self) -> set[int]:
        """Return, by id, the fields of the type aliases that every site names beside an array-like member.

        A site is a node of a field's type that names the alias (``names_alias_beside_arrays``); the lists of such an
        alias's own level stay lists, as a union of an output that decodes one member of each kind decodes there.
        """
        sites: dict[ModelKey, list[tuple[Any, Any]]] = {}
        for model in self.live.values():
            for field in model.fields:
                pending = [field.data_type]
                while pending:
                    if (reference := (node := pending.pop()).reference) is None:
                        pending.extend(node.data_types)
                    elif self.class_hash.get(key := model_key_of(reference)) == "alias":
                        sites.setdefault(key, []).append((field.data_type, node))
        return {
            id(self.live[key].fields[0]) for key in self._alias_order if names_alias_beside_arrays(sites.get(key, ()))
        }

    def read_aliases(self) -> None:
        """Read whether every value of each type alias hashes, as ``value_keys`` reads the models, in alias order.

        An alias is read as its value, whose lists become sets as its own plan says, after the aliases its value
        names; one read after it, the alias a cycle of aliases is entered at, takes values that may not hash, as a
        value that holds itself does.
        """
        self.aliases = {}
        for key in self._alias_order:
            value = self.live[key].fields[0]
            sets = {*map(id, self.plan(value).sets)}
            self.aliases[key] = every_value_hashes(self.item(value.data_type, sets))

    def plan(self, field: FieldView) -> UniqueItemsPlan:
        """Return the arrays of ``field`` that become sets, but those of the own level of an alias beside arrays."""
        live = cast("Any", field)
        plan = plan_unique_sets(
            live,
            (group for _, group in _declared_unique_arrays(live, "all")),
            default=list(default) if isinstance(default := field.default, (list, tuple)) else None,
            keeps_tuples=self._keeps_tuples,
            can_hold=lambda node, sets: all(
                every_value_hashes(self.item(item, sets)) for item in cast("Any", node).data_types
            ),
        )
        return without_own_unique_sets(live, plan) if id(field) in self._beside_arrays else plan

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

        An array default is a list or a set, which does not hash (a view reads a list as a tuple), and so is the value
        of each default factory a schema may name (``dict``, ``list`` and ``set``).
        """
        if (
            plan.default is not None
            or isinstance(default := field.default, (list, tuple))
            or "default_factory" in field.extras
        ):
            return False
        try:
            hash(default)
        except TypeError:
            return False
        return every_value_hashes(self.item(field.data_type, {*map(id, plan.sets)}))


def _write_unique_items_as_sets(facts: FinalFacts, writer: UniqueSetsWriter) -> None:
    """Write the uniqueItems lists of every live model whose final items hash by value as sets.

    Which models hash by value is read again until no field contradicts it, then the plans of that reading are
    written.
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
    for model in facts.live_models:
        for field in model.fields:
            if (plan := plans[id(field)]).sets:
                writer.write_sets(model, field, plan.sets, plan.default)


WRITE_UNIQUE_ITEMS_AS_SETS = DecisionPass(
    {
        "name": "write_unique_items_as_sets",
        "issue": "#4834",
    },
    UniqueSetsWriter,
    _write_unique_items_as_sets,
    when=lambda parser: parser.use_unique_items_as_set and parser.use_unique_items_as_set_by_value,
)
