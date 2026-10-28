"""In-memory facts and indexes for parser generation state.

Parser implementations keep creating and naming models through the existing
ModelResolver flow. The store records those objects and owns all parser-side
mutations that affect dependency facts.

Contributor guide:

* Keep ``ModelResolver`` as the only authority for generated names. Register
  models only after the existing parser flow has created and named them.
* Use ``GenerationStore.builder().register_model(model)`` instead of appending to
  ``Parser.results`` or ``GenerationStore.models`` directly.
* Use the mutation methods of ``GraphBuilder`` (``GenerationStore.builder()``) for parser-side changes that affect
  references, fields, base classes, model paths, or model names. Do not assign
  ``data_type.reference``, ``model.fields``, ``model.base_classes``, or
  ``model.reference.name`` directly in parser code.
* A refresh records again only the models that changed since the last one.
  Code that changes a tracked model's fields, base classes, or data types
  without the store, such as a model method that rewrites its own types, must
  report the model with ``GraphBuilder.note_changed_models()``.
* Read dependency facts through ``GenerationIndex``. Parser post-processing
  should not treat ``Reference.children`` as the source of truth.
* Treat output template metadata as opaque. Read model-owned dependency
  metadata through ``DataModel`` capability methods.
* Preserve output compatibility first. Store/index queries must reproduce the
  existing parse order, naming order, canonical model selection, and
  tie-break behavior before they replace a direct object traversal.
* Override every mutating ``list`` method on ``_GenerationModelList``. The
  list-method contract test deliberately fails when Python adds an unclassified
  method so new mutation paths cannot silently leave facts stale.

The pre-commit hook backed by ``scripts/check_generation_store_usage.py``
guards the parser package against common direct mutations. When adding a new
store mutation API, add it to ``GENERATION_STORE_MUTATION_METHODS`` so the
checker and its tests stay aligned with the public parser-facing surface, and
give ``GraphBuilder`` its delegating method.
"""

# ruff: noqa: D105, FURB189

from __future__ import annotations

from bisect import insort
from collections import defaultdict
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from itertools import chain
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, SupportsIndex, TypeAlias, TypeVar, final, overload

from typing_extensions import deprecated, override

from datamodel_code_generator import Error
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._constraint_applicability import (
    DROPPED_MULTIPLE_OF_KEY,
    TYPE_DEPENDENT_CONSTRAINTS_KEY,
    VALUES_AS_WRITTEN_KEY,
)
from datamodel_code_generator._model_keys import FieldKey, ModelKey, NodeKey, model_key_of
from datamodel_code_generator._null_markers import (
    HELD_NULL_DROPPED_KEY,
    NAMED_NULL_REJECTED_KEY,
    REFERENCE_NULL_KEY,
    RENDER_PREPARATION_NULL_KEY,
    TARGET_NULL_KEY,
)
from datamodel_code_generator._parse_memo import ParseMemo, bump_field, bump_model, bump_node
from datamodel_code_generator._records import record
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.model._decisions.union_assembly import MEMBER, MemberFacts, UnionPolicy, assemble_union
from datamodel_code_generator.parser._facts import Phase
from datamodel_code_generator.parser._graph_ids import identity_of
from datamodel_code_generator.parser._holders import (
    KNOWN_PARENT_MISMATCHES,
    ChildList,
    HolderEdge,
    InlinedReplacement,
    ParentMismatch,
    node_in_cycle,
    parent_is_holder,
)
from datamodel_code_generator.parser._phases import FinalFacts, ModuleFacts
from datamodel_code_generator.parser._sites import (
    COPIED,
    PARSED,
    Provenance,
    RefSite,
    SiteKey,
    SiteOccurrence,
    position_step,
    site_of,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Mapping, Sequence
    from contextlib import AbstractContextManager
    from pathlib import Path

    from typing_extensions import Self

    from datamodel_code_generator._checks import CheckMode
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model._decisions.literal_default import LiteralDefault
    from datamodel_code_generator.model._views import ModelView
    from datamodel_code_generator.model.base import BaseClassDataType, DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._sites import Position, ProvenanceHow, RefKind
    from datamodel_code_generator.types import DataType

    OwnerModels: TypeAlias = DataModel | tuple[DataModel, ...] | None
    CacheOwners: TypeAlias = tuple[DataModelFieldBase | None, OwnerModels]

else:
    BaseClassDataType: TypeAlias = Any
    DataModel: TypeAlias = Any
    DataModelFieldBase: TypeAlias = Any
    DataType: TypeAlias = Any
    OwnerModels: TypeAlias = Any
    CacheOwners: TypeAlias = Any

Reference: TypeAlias = Any
ModelId: TypeAlias = int
DataTypeId: TypeAlias = int
DataTypeRole = Literal["field", "base", "nested", "dict_key"]
DataTypeOccurrence: TypeAlias = tuple[DataType, int | None, DataTypeRole]
Refresh: TypeAlias = Literal["now", "deferred"]
"""When a store operation marks the facts stale: ``now``, or ``deferred`` until another mutation marks them; the
models it noted are recorded again at the next refresh either way."""
Universe: TypeAlias = Literal["parsed", "emitted"]
"""The models an index query answers over: ``parsed``, every model the parsers registered (``Parser.results``), the
removed ones included; ``emitted``, the models the output writes: the parsed ones and those later passes build, less
the ones passes retired."""
_OrderedSetItem = TypeVar("_OrderedSetItem")
_REPLACED = MemberFacts("replaced")
_REPLACEMENT = MemberFacts("replacement")
_REPLACEMENT_LAST = UnionPolicy("legacy-replacement-last")
OrderedSet: TypeAlias = dict[_OrderedSetItem, None]

GENERATION_STORE_MUTATION_METHODS: frozenset[str] = frozenset({
    "add_model_import",
    "append_field",
    "apply_const",
    "apply_literal_default",
    "clear_target_null",
    "collapse_root_data_type",
    "defer_refresh",
    "detach_data_type_ref",
    "detach_model_data_type_refs",
    "discard_derived_facts",
    "drop_field_discriminator",
    "drop_held_null",
    "drop_union_member_constraints",
    "drop_union_operator",
    "force_field_assignment",
    "inline_shared",
    "insert_field",
    "mark_forward_references",
    "mark_target_null",
    "move_model",
    "note_changed_models",
    "override_type_import",
    "name_reference",
    "record_dropped_multiple_of",
    "record_values_as_written",
    "redirect_model_reference_users",
    "redirect_reference_users",
    "register_model",
    "remove_field",
    "rename_field",
    "rename_model",
    "replace_data_type_ref",
    "replace_field_type",
    "replace_model",
    "replace_nested_data_type",
    "require_field",
    "reset_base_classes",
    "restore_required_inherited_field",
    "retire_model",
    "set_base_classes",
    "set_field_constraints",
    "set_field_default",
    "set_field_extra",
    "set_field_extras",
    "set_fields",
    "set_nested_data_types",
    "set_type_discriminator",
    "set_type_expressions",
    "swap_data_type",
    "update_model_reference",
    "use_assignment_for_forward_references",
    "use_typing_generics",
    "write_rendered_null",
})

SITE_EFFECTS: Mapping[str, ProvenanceHow] = MappingProxyType({
    "append_field": "copied",
    "apply_const": "overridden",
    "collapse_root_data_type": "collapsed",
    "drop_union_member_constraints": "overridden",
    "inline_shared": "collapsed",
    "insert_field": "copied",
    "note_changed_models": "overridden",
    "redirect_model_reference_users": "redirected",
    "redirect_reference_users": "redirected",
    "register_model": "parsed",
    "replace_data_type_ref": "redirected",
    "replace_field_type": "overridden",
    "replace_nested_data_type": "overridden",
    "reset_base_classes": "overridden",
    "set_base_classes": "overridden",
    "set_fields": "copied",
    "set_nested_data_types": "overridden",
    "swap_data_type": "overridden",
})
"""The provenance each store mutation gives the new reference sites it makes (`parser/_sites.py`).

A redirect names the model the site named before (``via``) and the site that was at its place (``source``); a collapse
names the root model collapsed and its site that names the same model. The sites of the models the parsers build are
``parsed``, and so are the sites of the parsed models no mutation stated an effect for; the sites of the models later
passes build are ``copied``."""

NO_SITE_EFFECT: frozenset[str] = frozenset({
    "add_model_import",
    "apply_literal_default",
    "clear_target_null",
    "defer_refresh",
    "detach_data_type_ref",
    "detach_model_data_type_refs",
    "discard_derived_facts",
    "drop_field_discriminator",
    "drop_held_null",
    "drop_union_operator",
    "force_field_assignment",
    "mark_forward_references",
    "mark_target_null",
    "move_model",
    "override_type_import",
    "name_reference",
    "record_dropped_multiple_of",
    "record_values_as_written",
    "remove_field",
    "rename_field",
    "rename_model",
    "replace_model",
    "require_field",
    "restore_required_inherited_field",
    "retire_model",
    "set_field_constraints",
    "set_field_default",
    "set_field_extra",
    "set_field_extras",
    "set_type_discriminator",
    "set_type_expressions",
    "update_model_reference",
    "use_assignment_for_forward_references",
    "use_typing_generics",
    "write_rendered_null",
})
"""The store mutations that make no reference site: they only remove sites, or change what sites do not record, such
as names, defaults and imports. With `SITE_EFFECTS`, they list every mutation of
``GENERATION_STORE_MUTATION_METHODS``."""


def set_model_base_classes(
    model: DataModel,
    base_classes: Iterable[BaseClassDataType],
    generation_store: GenerationStore | None = None,
) -> None:
    """Replace model base classes, refreshing ``generation_store`` when supplied."""
    if generation_store is None:
        model.base_classes = list(base_classes)
    else:
        generation_store.builder().set_base_classes(model, base_classes)


def _bump_field_tree(field_: DataModelFieldBase) -> None:
    """Bump a field and every node of its type, before a hook that may write anywhere in them (`bump_node`)."""
    bump_field(field_)
    for data_type in field_.data_type.all_data_types:
        bump_node(data_type)


def _detach_data_type_tree(root: DataType) -> None:
    """Sever one data-type tree iteratively so cleanup cannot hit recursion limits."""
    stack = [root]
    while stack:
        data_type = stack.pop()
        stack.extend(data_type.data_types)
        data_type.data_types.clear()
        if dict_key := data_type.dict_key:
            stack.append(dict_key)
            data_type.dict_key = None
        data_type.parent = None
        data_type.reference = None


@record
class ModelFact:
    """A parsed model and the stable facts derived from its reference.

    The names are read from the model, since parser code renames models without the store.
    """

    id: ModelId
    parse_order: int
    model: DataModel
    reference: Reference
    path: str
    file_path: Path | None

    @property
    def name(self) -> str:
        """The model's reference name."""
        return self.model.reference.name

    @property
    def class_name(self) -> str:
        """The model's class name."""
        return self.model.class_name


@record
class DataTypeFact:
    """A data type occurrence owned by a parsed model."""

    id: DataTypeId
    data_type: DataType
    owner_model: ModelId
    owner_field_index: int | None
    role: DataTypeRole
    reference: Reference | None


@final
@dataclass(slots=True)
class GenerationFacts:
    """A complete snapshot of derived generation facts.

    A model's data type ids increase in traversal order, so ``(owner parse order, id)`` orders occurrences
    as a full rebuild traverses them. The reference sites of the type trees (`RefSite`) take the id of their
    occurrence as their key, and ``sites_by_target`` lists them per target in that order.
    """

    model_facts: dict[ModelId, ModelFact] = field(default_factory=dict)
    data_type_facts: dict[DataTypeId, DataTypeFact] = field(default_factory=dict)
    data_type_fact_by_object: dict[int, DataTypeFact] = field(default_factory=dict)
    base_owner_model_ids_by_object: dict[int, ModelId | list[ModelId]] = field(default_factory=dict)
    model_by_path: dict[str, ModelId] = field(default_factory=dict)
    model_by_ref_id: dict[ModelKey, ModelId] = field(default_factory=dict)
    data_types_by_model: dict[ModelId, tuple[DataTypeId, ...]] = field(default_factory=dict)
    sites_by_target: defaultdict[ModelKey, OrderedSet[SiteKey]] = field(default_factory=lambda: defaultdict(dict))
    shared_data_type_fact_ids: dict[int, list[DataTypeId]] = field(default_factory=dict)
    sites: dict[SiteKey, RefSite] = field(default_factory=dict)

    @property
    def sites_by_owner(self) -> dict[ModelKey, tuple[SiteKey, ...]]:
        """The sites of each owner model, in the order a full rebuild's traversal reaches them."""
        owners: dict[ModelKey, list[SiteKey]] = {}
        for key in sorted(self.sites, key=self.order_key):
            owners.setdefault(self.sites[key].owner, []).append(key)
        return {owner: tuple(keys) for owner, keys in owners.items()}

    def parse_order(self, model_id: ModelId) -> int:
        """Return the position of a model in the model list."""
        return self.model_facts[model_id].parse_order

    def order_key(self, data_type_id: DataTypeId) -> tuple[int, DataTypeId]:
        """Return the position of an occurrence in a full rebuild's traversal order."""
        return self.parse_order(self.data_type_facts[data_type_id].owner_model), data_type_id

    @property
    def holders_by_node(self) -> dict[NodeKey, tuple[HolderEdge, ...]]:
        """The holders of every recorded node, by node in the order a full rebuild's traversal first reaches them."""
        holders = _HolderIndex(self)
        node_keys = dict.fromkeys(NodeKey(id(fact.data_type)) for fact in self._facts_in_order())
        return {node_key: holders.holders_of(node_key) for node_key in node_keys}

    @property
    def child_list_owners(self) -> dict[int, dict[NodeKey, list[tuple[int, DataTypeId]]]]:
        """The ``order_key`` of every recorded occurrence of each node that holds a list, by the ``id`` of the list.

        Lists and nodes come in the order a full rebuild's traversal first reaches them.
        """
        owners: dict[int, dict[NodeKey, list[tuple[int, DataTypeId]]]] = {}
        for fact in self._facts_in_order():
            owners.setdefault(id(fact.data_type.data_types), {}).setdefault(NodeKey(id(fact.data_type)), []).append(
                self.order_key(fact.id)
            )
        return owners

    @property
    def child_lists(self) -> dict[int, ChildList]:
        """The lists of ``child_list_owners`` that more than one node holds."""
        return {
            list_id: ChildList(list_id, tuple(owners))
            for list_id, owners in self.child_list_owners.items()
            if len(owners) > 1
        }

    def _facts_in_order(self) -> list[DataTypeFact]:
        return sorted(self.data_type_facts.values(), key=lambda fact: self.order_key(fact.id))


@final
class _HolderIndex:
    """The holders of the nodes that a facts snapshot records, read from the models the first time they are asked for.

    The edges of a model are walked from the model as the store recorded it, so the index is read right after the
    refresh that recorded the facts.
    """

    __slots__ = ("_facts", "_holder_by_id")

    def __init__(self, facts: GenerationFacts) -> None:
        """Read the holders of ``facts`` on demand."""
        self._facts = facts
        self._holder_by_id: dict[DataTypeId, HolderEdge] = {}

    def holders_of(self, node_key: NodeKey) -> tuple[HolderEdge, ...]:
        """Return the holder of every recorded occurrence of a node in ``order_key`` order.

        An edge repeats when its holder is shared too.
        """
        facts = self._facts
        if (data_type_ids := facts.shared_data_type_fact_ids.get(node_key)) is None:
            return (self._holder(facts.data_type_fact_by_object[node_key].id),)
        return tuple(self._holder(data_type_id) for data_type_id in sorted(data_type_ids, key=facts.order_key))

    def _holder(self, data_type_id: DataTypeId) -> HolderEdge:
        if (holder := self._holder_by_id.get(data_type_id)) is not None:
            return holder
        facts = self._facts
        model_id = facts.data_type_facts[data_type_id].owner_model
        self._holder_by_id.update(
            zip(facts.data_types_by_model[model_id], _holder_edges(facts.model_facts[model_id]), strict=True)
        )
        return self._holder_by_id[data_type_id]


@record
class GenerationBuildResult:
    """Facts and stable id state produced by an index rebuild."""

    facts: GenerationFacts
    model_ids_by_object: dict[int, ModelId]
    next_model_id: int
    next_data_type_id: int


@final
@record
class _UniverseFacts:
    """The facts of one universe and the ids they record each model under."""

    universe: Universe
    facts: GenerationFacts
    model_ids_by_object: Mapping[int, ModelId]

    def model_id(self, model: DataModel) -> ModelId | None:
        """Return the id the facts record ``model`` under, if they record it."""
        return self.model_ids_by_object.get(id(model))


@final
class _GenerationModelList(list["DataModel"]):
    """List-compatible model collection that invalidates generation facts."""

    def __init__(self, invalidate: Callable[[], None], invalidate_append: Callable[[Any], None]) -> None:
        """Create a model list with invalidation callbacks for any change and for an appended model."""
        super().__init__()
        self._invalidate = invalidate
        self._invalidate_append = invalidate_append

    @override
    def append(self, item: Any) -> None:
        """Append a model and invalidate derived facts."""
        super().append(item)
        self._invalidate_append(item)

    @override
    def extend(self, items: Iterable[Any]) -> None:
        """Extend the model list and invalidate derived facts."""
        try:
            super().extend(items)
        finally:
            self._invalidate()

    @override
    def insert(self, index: SupportsIndex, item: Any) -> None:
        """Insert a model and invalidate derived facts."""
        super().insert(index, item)
        self._invalidate()

    @overload
    def __setitem__(self, index: SupportsIndex, item: Any) -> None:  # pragma: no cover
        pass

    @overload
    def __setitem__(self, index: slice, item: Iterable[Any]) -> None:  # pragma: no cover
        pass

    @override
    def __setitem__(
        self,
        index: SupportsIndex | slice,
        item: Any | Iterable[Any],
    ) -> None:
        super().__setitem__(index, item)  # ty: ignore[no-matching-overload]
        self._invalidate()

    @overload
    def __delitem__(self, index: SupportsIndex) -> None:  # pragma: no cover
        pass

    @overload
    def __delitem__(self, index: slice) -> None:  # pragma: no cover
        pass

    @override
    def __delitem__(self, index: SupportsIndex | slice) -> None:  # ty: ignore[invalid-method-override]
        super().__delitem__(index)
        self._invalidate()

    @override
    def clear(self) -> None:
        """Remove all models and invalidate derived facts."""
        super().clear()
        self._invalidate()

    @override
    def pop(self, index: SupportsIndex = -1) -> Any:
        """Remove and return a model while invalidating derived facts."""
        item = super().pop(index)
        self._invalidate()
        return item

    @override
    def remove(self, item: Any) -> None:
        """Remove a model and invalidate derived facts."""
        super().remove(item)
        self._invalidate()

    @override
    def reverse(self, /) -> None:
        """Reverse the model list and invalidate derived facts."""
        super().reverse()
        self._invalidate()

    @override
    def sort(
        self,
        /,
        *,
        key: Callable[[Any], Any] | None = None,
        reverse: bool = False,
    ) -> None:
        """Sort the model list and invalidate derived facts."""
        try:
            if key is None:
                super().sort(reverse=reverse)  # ty: ignore[invalid-argument-type]
                return
            super().sort(key=key, reverse=reverse)
        finally:
            self._invalidate()

    @override
    def __iadd__(self, items: Iterable[Any], /) -> Self:
        """Extend the model list in place and invalidate derived facts."""
        try:
            super().__iadd__(items)
        finally:
            self._invalidate()
        return self

    @override
    def __imul__(self, value: SupportsIndex, /) -> Self:
        """Repeat the model list in place and invalidate derived facts."""
        super().__imul__(value)
        self._invalidate()
        return self


@final
class _SiteEffects:
    """The site effects that store operations stated since the last refresh, for the new sites the next one records.

    An effect stated for a node gives the sites at that node their provenance; one stated for a model, the other new
    sites of the model. The first effect stated for a node or a model since the last refresh holds.
    """

    __slots__ = ("by_model", "by_node")

    def __init__(self) -> None:
        """Start with no effect stated."""
        self.by_model: dict[ModelId, Provenance] = {}
        self.by_node: dict[NodeKey, tuple[object, Provenance]] = {}
        """The node is kept with its effect, so its id is not reused before the refresh."""

    def clear(self) -> None:
        """Forget every effect, once a refresh recorded them."""
        self.by_model.clear()
        self.by_node.clear()


@final
class _OldSites:
    """The sites a refresh replaces, by occurrence and, when a redirect asks for it, by place."""

    __slots__ = ("_by_place", "by_occurrence")

    def __init__(self, sites: Iterable[RefSite]) -> None:
        """Index ``sites`` by occurrence."""
        self.by_occurrence: dict[SiteOccurrence, RefSite] = {site.occurrence: site for site in sites}
        self._by_place: dict[tuple[object, ...], RefSite] | None = None

    def at_place(self, occurrence: SiteOccurrence) -> RefSite | None:
        """Return the site that was at the place of ``occurrence``: its owner, field, position, node and kind."""
        if (by_place := self._by_place) is None:
            by_place = self._by_place = {_place(site.occurrence): site for site in self.by_occurrence.values()}
        return by_place.get(_place(occurrence))


def _place(occurrence: SiteOccurrence) -> tuple[object, ...]:
    return occurrence.owner, occurrence.field, occurrence.path, occurrence.node, occurrence.kind


@final
class _SiteHistory:
    """What a refresh gives new sites their provenance from: the sites before it and the effects stated since."""

    __slots__ = ("_default", "_effects", "_previous")

    def __init__(self, previous: Iterable[RefSite], effects: _SiteEffects, default: Provenance) -> None:
        """Take provenance from ``effects``, else ``default``; a full rebuild carries it from ``previous``."""
        self._previous = previous
        self._effects = effects
        self._default = default

    def previous_sites(self) -> _OldSites:
        """Return the sites a full rebuild replaces."""
        return _OldSites(self._previous)

    def provenance(
        self, facts: GenerationFacts, model_id: ModelId, occurrence: SiteOccurrence, old_sites: _OldSites
    ) -> Provenance:
        """Return the provenance of a new site of the model ``model_id``.

        A redirected site comes from the site that was at its place; a collapsed one from the collapsed root's site
        that names the same model.
        """
        effects = self._effects
        if occurrence.node is not None and (stated := effects.by_node.get(occurrence.node)) is not None:
            provenance = stated[1]
        else:
            provenance = effects.by_model.get(model_id, self._default)
        source: SiteKey | None = None
        if provenance.how == "redirected":
            if (old := old_sites.at_place(occurrence)) is not None:
                source = old.key
        elif provenance.how == "collapsed":
            source = next(
                (
                    key
                    for key in facts.sites_by_target.get(occurrence.target, ())
                    if (site := facts.sites.get(key)) is not None and site.owner == provenance.via
                ),
                None,
            )
        return provenance if source is None else replace(provenance, source=source)


_NO_HISTORY: Final = _SiteHistory((), _SiteEffects(), PARSED)


class GenerationIndexBuilder:
    """Record generation facts from live parser objects, in full or for changed models."""

    def __init__(
        self, model_key: Callable[[Reference], ModelKey] = model_key_of, history: _SiteHistory | None = None
    ) -> None:
        """Create a builder that keys references by ``model_key`` and takes the provenance of sites from ``history``.

        Without a history, every new site is ``parsed``.
        """
        self._model_key = model_key
        self._history = _NO_HISTORY if history is None else history

    def build(
        self,
        models: Iterable[DataModel],
        *,
        previous_model_ids_by_object: dict[int, ModelId],
        next_model_id: int,
    ) -> GenerationBuildResult:
        """Build facts while preserving stable model ids for surviving objects."""
        self._models = list(models)
        self._facts = GenerationFacts()
        self._model_ids_by_object = {
            id(model): previous_model_ids_by_object[id(model)]
            for model in self._models
            if id(model) in previous_model_ids_by_object
        }
        self._next_model_id = next_model_id
        self._next_data_type_id = 0

        self._record_models()
        previous_sites = self._history.previous_sites()
        for model_id, model_fact in self._facts.model_facts.items():
            occurrences, sites = self._occurrences(model_fact.model)
            self._record_data_types(model_id, occurrences)
            self._record_sites(model_id, sites, previous_sites)

        return GenerationBuildResult(
            facts=self._facts,
            model_ids_by_object=self._model_ids_by_object,
            next_model_id=self._next_model_id,
            next_data_type_id=self._next_data_type_id,
        )

    def update(  # noqa: PLR0913
        self,
        facts: GenerationFacts,
        models: Sequence[DataModel],
        *,
        appended_count: int,
        changed_model_ids: Iterable[ModelId],
        renamed_model_ids: Iterable[ModelId],
        model_ids_by_object: dict[int, ModelId],
        next_model_id: int,
        next_data_type_id: int,
    ) -> GenerationBuildResult:
        """Record the models appended to ``models`` and the changed and renamed ones in ``facts``.

        Every model is read before ``facts`` changes, so a traversal that raises leaves the facts as they were.
        """
        self._facts = facts
        self._next_data_type_id = next_data_type_id
        model_facts = facts.model_facts
        first_appended = len(models) - appended_count
        appended = [
            (parse_order, model, *self._occurrences(model))
            for parse_order, model in enumerate(models[first_appended:], first_appended)
        ]
        renamed_facts = [
            _model_fact(model_id, (model_fact := model_facts[model_id]).parse_order, model_fact.model)
            for model_id in renamed_model_ids
        ]
        changes = [
            (model_id, *self._occurrences(model_facts[model_id].model))
            for model_id in sorted(changed_model_ids, key=facts.parse_order)
        ]
        for model_id, (parse_order, model, occurrences, sites) in enumerate(appended, next_model_id):
            model_ids_by_object[id(model)] = model_id
            model_facts[model_id] = _model_fact(model_id, parse_order, model)
            facts.model_by_path[model.path] = model_id
            facts.model_by_ref_id[self._model_key(model.reference)] = model_id
            self._record_data_types_again(model_id, occurrences, sites)
        if renamed_facts:
            model_facts.update((model_fact.id, model_fact) for model_fact in renamed_facts)
            facts.model_by_path = {model_fact.path: model_id for model_id, model_fact in model_facts.items()}
            facts.model_by_ref_id = {
                self._model_key(model_fact.reference): model_id for model_id, model_fact in model_facts.items()
            }
        for model_id, occurrences, sites in changes:
            self._record_data_types_again(model_id, occurrences, sites)
        return GenerationBuildResult(
            facts=facts,
            model_ids_by_object=model_ids_by_object,
            next_model_id=next_model_id + len(appended),
            next_data_type_id=self._next_data_type_id,
        )

    def _record_models(self) -> None:
        for parse_order, model in enumerate(self._models):
            model_id = self._model_ids_by_object.get(id(model))
            if model_id is None:
                model_id = self._next_model_id
                self._next_model_id += 1
                self._model_ids_by_object[id(model)] = model_id

            self._facts.model_facts[model_id] = _model_fact(model_id, parse_order, model)
            self._facts.model_by_path[model.path] = model_id
            self._facts.model_by_ref_id[self._model_key(model.reference)] = model_id

    def _occurrences(self, model: DataModel) -> tuple[list[DataTypeOccurrence], list[tuple[int, SiteOccurrence]]]:
        """Return the data type occurrences of ``model`` and its reference sites, from one walk of its type trees.

        Each site comes with the position of its node among the occurrences.
        """
        return model_occurrences(model, self._model_key(model.reference), self._model_key)

    def _record_data_types(self, model_id: ModelId, occurrences: list[DataTypeOccurrence]) -> None:
        """Record the data type occurrences of a model in traversal order, as a full build visits them."""
        facts = self._facts
        data_type_ids: list[DataTypeId] = []
        for occurrence in occurrences:
            data_type, _, role = occurrence
            if role == "base":
                self._add_base_owner(id(data_type), model_id)
            data_type_id = self._next_data_type_id
            self._next_data_type_id += 1
            fact = facts.data_type_facts[data_type_id] = _data_type_fact(data_type_id, model_id, occurrence)
            if (current := facts.data_type_fact_by_object.get(object_id := id(data_type))) is not None:
                facts.shared_data_type_fact_ids.setdefault(object_id, [current.id]).append(data_type_id)
            facts.data_type_fact_by_object[object_id] = fact
            data_type_ids.append(data_type_id)
            if fact.reference:
                facts.sites_by_target[self._model_key(fact.reference)][SiteKey(data_type_id)] = None
        facts.data_types_by_model[model_id] = tuple(data_type_ids)

    def _record_data_types_again(
        self, model_id: ModelId, occurrences: list[DataTypeOccurrence], sites: list[tuple[int, SiteOccurrence]]
    ) -> None:
        """Record a changed or appended model in the ids it had, replacing only the occurrences that differ.

        Keeping the ids keeps the dictionaries from growing with every change; occurrences beyond the old
        count get new ids, which still follow every id the model keeps. The model's ``sites`` replace its old ones.
        """
        data_type_facts = self._facts.data_type_facts
        old_ids = self._facts.data_types_by_model.get(model_id, ())
        all_sites = self._facts.sites
        old_sites = _OldSites(
            site for data_type_id in old_ids if (site := all_sites.pop(SiteKey(data_type_id), None)) is not None
        )
        data_type_ids = list(old_ids[: len(occurrences)])
        for _ in range(len(occurrences) - len(old_ids)):
            data_type_ids.append(self._next_data_type_id)
            self._next_data_type_id += 1
        stale_facts = [data_type_facts[data_type_id] for data_type_id in old_ids[len(occurrences) :]]
        fresh_facts: list[DataTypeFact] = []
        for data_type_id, occurrence in zip(data_type_ids, occurrences, strict=True):
            data_type, owner_field_index, role = occurrence
            if (old_fact := data_type_facts.get(data_type_id)) is not None:
                if (
                    old_fact.data_type is data_type
                    and old_fact.reference is data_type.reference
                    and old_fact.owner_field_index == owner_field_index
                    and old_fact.role == role
                ):
                    continue
                stale_facts.append(old_fact)
            fresh_facts.append(_data_type_fact(data_type_id, model_id, occurrence))
        if any(fact.role == "base" for fact in chain(stale_facts, fresh_facts)):
            self._update_base_owners(model_id, old_ids, occurrences)
        for fact in stale_facts:
            self._forget_fact(fact)
        for data_type_id in old_ids[len(occurrences) :]:
            del data_type_facts[data_type_id]
        for fact in fresh_facts:
            self._record_fact(fact)
        self._facts.data_types_by_model[model_id] = tuple(data_type_ids)
        self._record_sites(model_id, sites, old_sites)

    def _record_sites(
        self,
        model_id: ModelId,
        sites: list[tuple[int, SiteOccurrence]],
        old_sites: _OldSites,
    ) -> None:
        """Record the sites of a model under the ids of their occurrences.

        A site whose occurrence ``old_sites`` holds keeps its provenance; a new one takes what the history gives it.
        """
        facts = self._facts
        data_type_ids = facts.data_types_by_model[model_id]
        for index, occurrence in sites:
            key = SiteKey(data_type_ids[index])
            if (old := old_sites.by_occurrence.get(occurrence)) is None:
                provenance = self._history.provenance(facts, model_id, occurrence, old_sites)
                facts.sites[key] = site_of(key, occurrence, provenance)
            elif old.key == key:
                facts.sites[key] = old
            else:
                facts.sites[key] = site_of(key, occurrence, old.provenance)

    def _record_fact(self, fact: DataTypeFact) -> None:
        self._facts.data_type_facts[fact.id] = fact
        self._index_data_type_object(fact)
        if fact.reference:
            self._add_target_site(self._model_key(fact.reference), SiteKey(fact.id))

    def _forget_fact(self, fact: DataTypeFact) -> None:
        self._forget_data_type_object(fact)
        if fact.reference:
            key = self._model_key(fact.reference)
            site_keys = self._facts.sites_by_target[key]
            del site_keys[SiteKey(fact.id)]
            if not site_keys:
                del self._facts.sites_by_target[key]

    def _update_base_owners(
        self,
        model_id: ModelId,
        old_ids: tuple[DataTypeId, ...],
        occurrences: list[DataTypeOccurrence],
    ) -> None:
        data_type_facts = self._facts.data_type_facts
        old_base_class_ids = {
            id(fact.data_type) for data_type_id in old_ids if (fact := data_type_facts[data_type_id]).role == "base"
        }
        new_base_class_ids = {id(data_type) for data_type, _, role in occurrences if role == "base"}
        for base_class_id in old_base_class_ids - new_base_class_ids:
            self._remove_base_owner(base_class_id, model_id)
        for base_class_id in new_base_class_ids - old_base_class_ids:
            self._add_base_owner(base_class_id, model_id)

    def _index_data_type_object(self, fact: DataTypeFact) -> None:
        facts = self._facts
        object_id = id(fact.data_type)
        if (current := facts.data_type_fact_by_object.get(object_id)) is None:
            facts.data_type_fact_by_object[object_id] = fact
            return
        facts.shared_data_type_fact_ids.setdefault(object_id, [current.id]).append(fact.id)
        if facts.order_key(fact.id) > facts.order_key(current.id):
            facts.data_type_fact_by_object[object_id] = fact

    def _forget_data_type_object(self, fact: DataTypeFact) -> None:
        facts = self._facts
        object_id = id(fact.data_type)
        if (data_type_ids := facts.shared_data_type_fact_ids.get(object_id)) is None:
            del facts.data_type_fact_by_object[object_id]
            return
        data_type_ids.remove(fact.id)
        if len(data_type_ids) == 1:
            del facts.shared_data_type_fact_ids[object_id]
        if facts.data_type_fact_by_object[object_id] is fact:
            facts.data_type_fact_by_object[object_id] = facts.data_type_facts[max(data_type_ids, key=facts.order_key)]

    def _add_target_site(self, key: ModelKey, site_key: SiteKey) -> None:
        facts = self._facts
        site_keys = facts.sites_by_target[key]
        if not site_keys or facts.order_key(next(reversed(site_keys))) < facts.order_key(site_key):
            site_keys[site_key] = None
            return
        ordered_keys = sorted((*site_keys, site_key), key=facts.order_key)
        site_keys.clear()
        site_keys.update(dict.fromkeys(ordered_keys))

    def _add_base_owner(self, base_class_id: int, model_id: ModelId) -> None:
        owners = self._facts.base_owner_model_ids_by_object
        if (current := owners.get(base_class_id)) is None:
            owners[base_class_id] = model_id
            return
        model_ids = [current] if isinstance(current, int) else current
        if model_id not in model_ids:
            insort(model_ids, model_id, key=self._facts.parse_order)
            owners[base_class_id] = model_ids

    def _remove_base_owner(self, base_class_id: int, model_id: ModelId) -> None:
        owners = self._facts.base_owner_model_ids_by_object
        if isinstance(model_ids := owners[base_class_id], int):
            del owners[base_class_id]
            return
        model_ids.remove(model_id)
        if len(model_ids) == 1:
            owners[base_class_id] = model_ids[0]


def model_occurrences(
    model: DataModel, owner: ModelKey, target_of: Callable[[Reference], ModelKey]
) -> tuple[list[DataTypeOccurrence], list[tuple[int, SiteOccurrence]]]:
    """Return the data type occurrences of ``model`` in the store's order and the reference sites among them.

    The nodes are walked once, in the store's order: each field's tree, then each base class's, every tree in preorder
    with the items of a node before its dict key. A site comes with the position of its node among the occurrences.
    """
    occurrences: list[DataTypeOccurrence] = []
    sites: list[tuple[int, SiteOccurrence]] = []
    kind: RefKind = "alias_body" if model.IS_ALIAS else "type"
    for field_index, field_ in enumerate(model.fields):
        field_key = FieldKey(id(field_))
        _walk_tree(field_.data_type, field_index, "field", field_key, kind, owner, target_of, occurrences, sites)
    for base_class in model.base_classes:
        _walk_tree(base_class, None, "base", None, "base", owner, target_of, occurrences, sites)
    return occurrences, sites


def _walk_tree(  # noqa: PLR0913, PLR0917
    root: DataType,
    field_index: int | None,
    root_role: DataTypeRole,
    field_key: FieldKey | None,
    kind: RefKind,
    owner: ModelKey,
    target_of: Callable[[Reference], ModelKey],
    occurrences: list[DataTypeOccurrence],
    sites: list[tuple[int, SiteOccurrence]],
) -> None:
    """Append the occurrences of the tree of ``root`` and the sites among them."""
    stack: list[tuple[DataType, DataTypeRole, tuple[Position, ...]]] = [(root, root_role, ())]
    while stack:
        data_type, role, path = stack.pop()
        if (reference := data_type.reference) is not None:
            sites.append((
                len(occurrences),
                SiteOccurrence(
                    owner, field_key, path, NodeKey(id(data_type)), target_of(reference), kind, eager=kind == "base"
                ),
            ))
        occurrences.append((data_type, field_index, role))
        if dict_key := data_type.dict_key:
            stack.append((dict_key, "dict_key", (*path, ("key", 0))))
        if nested := data_type.data_types:
            nested_role: DataTypeRole = "dict_key" if role == "dict_key" else "nested"
            step = position_step(data_type)
            stack.extend(
                (nested[position], nested_role, (*path, (step, position))) for position in reversed(range(len(nested)))
            )


def _holder_edges(model_fact: ModelFact) -> list[HolderEdge]:
    """Return the holder of every data type occurrence of a model, in the order the store records the occurrences."""
    model = model_fact.model
    edges: list[HolderEdge] = []
    for field_ in model.fields:
        _collect_holder_edges(field_.data_type, HolderEdge(FieldKey(id(field_)), "field", None), edges)
    model_key = model_key_of(model_fact.reference)
    for base_index, base_class in enumerate(model.base_classes):
        _collect_holder_edges(base_class, HolderEdge(model_key, "base", base_index), edges)
    return edges


def _collect_holder_edges(data_type: DataType, holder: HolderEdge, edges: list[HolderEdge]) -> None:
    edges.append(holder)
    node_key = NodeKey(id(data_type))
    for index, nested_data_type in enumerate(data_type.data_types):
        _collect_holder_edges(nested_data_type, HolderEdge(node_key, "nested", index), edges)
    if data_type.dict_key:
        _collect_holder_edges(data_type.dict_key, HolderEdge(node_key, "dict_key", None), edges)


def _data_type_fact(data_type_id: DataTypeId, model_id: ModelId, occurrence: DataTypeOccurrence) -> DataTypeFact:
    """Record one data type occurrence of a model."""
    data_type, owner_field_index, role = occurrence
    return DataTypeFact(
        id=data_type_id,
        data_type=data_type,
        owner_model=model_id,
        owner_field_index=owner_field_index,
        role=role,
        reference=data_type.reference,
    )


def _model_fact(model_id: ModelId, parse_order: int, model: DataModel) -> ModelFact:
    """Record a model's current name and location."""
    return ModelFact(
        id=model_id,
        parse_order=parse_order,
        model=model,
        reference=model.reference,
        path=model.path,
        file_path=model.file_path,
    )


def _fact_record(facts: GenerationFacts) -> dict[str, object]:
    """Return what ``facts`` record, with each id replaced by the parse order or occurrence position it stands for.

    An update keeps the ids a full rebuild would renumber, so two snapshots of the same models compare equal here.
    """
    parse_orders = {model_id: model_fact.parse_order for model_id, model_fact in facts.model_facts.items()}
    positions = {
        data_type_id: (parse_orders[model_id], index)
        for model_id, data_type_ids in facts.data_types_by_model.items()
        for index, data_type_id in enumerate(data_type_ids)
    }
    return {
        "models": sorted(
            (
                model_fact.parse_order,
                id(model_fact.model),
                id(model_fact.reference),
                model_fact.path,
                model_fact.file_path,
                tuple(
                    (id(fact.data_type), fact.owner_field_index, fact.role, id(fact.reference))
                    for fact in map(facts.data_type_facts.__getitem__, facts.data_types_by_model[model_fact.id])
                ),
            )
            for model_fact in facts.model_facts.values()
        ),
        "data types": {object_id: positions[fact.id] for object_id, fact in facts.data_type_fact_by_object.items()},
        "shared data types": {
            object_id: sorted(map(positions.__getitem__, data_type_ids))
            for object_id, data_type_ids in facts.shared_data_type_fact_ids.items()
        },
        "base owners": {
            object_id: [parse_orders[model_id] for model_id in ([owners] if isinstance(owners, int) else owners)]
            for object_id, owners in facts.base_owner_model_ids_by_object.items()
        },
        "paths": {path: parse_orders[model_id] for path, model_id in facts.model_by_path.items()},
        "model keys": {key: parse_orders[model_id] for key, model_id in facts.model_by_ref_id.items()},
        "sites by target": {
            key: [positions[site_key] for site_key in site_keys]
            for key, site_keys in facts.sites_by_target.items()
            if site_keys
        },
        "reference sites": {positions[key]: site.occurrence for key, site in facts.sites.items()},
    }


def _unrecorded_references(model: DataModel, *, all_dict_keys: bool) -> Iterator[Reference]:
    """Return the references whose paths ``DataModel.reference_classes`` lists, for a model the facts do not record.

    With ``all_dict_keys``, the dict keys of the field types count too.
    """
    data_types = chain(
        model.base_classes,
        *(
            field.data_type.all_data_types
            if all_dict_keys
            else chain(*(member.all_data_types for member in field.data_type.data_types), (field.data_type,))
            for field in model.fields
        ),
    )
    return (data_type.reference for data_type in data_types if data_type.reference)


@final
class GenerationIndex:
    """Query layer over the current generation facts.

    Each query answers over the parsed universe unless it is given ``universe="emitted"``.
    """

    def __init__(self, store: GenerationStore) -> None:
        """Create an index over ``store`` facts."""
        self._store = store
        self._recorded_references_cache: dict[tuple[ModelId, bool], tuple[Reference, ...]] = {}
        self._recorded_references_cache_version = -1

    def _view(self, universe: Universe) -> _UniverseFacts:
        return self._store._universe_facts(universe)  # noqa: SLF001

    def _reset_recorded_references_cache_if_needed(self) -> None:
        if self._recorded_references_cache_version == self._store.facts_version:
            return
        self._recorded_references_cache.clear()
        self._recorded_references_cache_version = self._store.facts_version

    def model_fact(self, model: DataModel, *, universe: Universe = "parsed") -> ModelFact | None:
        """Return the current fact for ``model`` if it is tracked."""
        view = self._view(universe)
        facts = view.facts
        model_id = view.model_id(model)
        if model_id is None:
            return None
        return facts.model_facts.get(model_id)

    def model_id_for_reference(self, reference: Reference, *, universe: Universe = "parsed") -> ModelId | None:
        """Return the tracked model id for ``reference`` if it points to a model."""
        return self._view(universe).facts.model_by_ref_id.get(self._store.model_key(reference))

    def model_for_reference(self, reference: Reference, *, universe: Universe = "parsed") -> DataModel | None:
        """Return the tracked model for ``reference`` if it points to a model."""
        facts = self._view(universe).facts
        model_id = facts.model_by_ref_id.get(self._store.model_key(reference))
        if model_id is None:
            return None
        return facts.model_facts[model_id].model

    def data_type_facts_for_reference(
        self, reference: Reference, *, universe: Universe = "parsed"
    ) -> tuple[DataTypeFact, ...]:
        """Return data type occurrences that currently point at ``reference``."""
        facts = self._view(universe).facts
        data_type_ids = facts.sites_by_target.get(self._store.model_key(reference))
        if not data_type_ids:
            return ()
        return tuple(facts.data_type_facts[data_type_id] for data_type_id in data_type_ids)

    def has_data_type_references(self, reference: Reference, *, universe: Universe = "parsed") -> bool:
        """Return whether any tracked data type currently points at ``reference``."""
        return bool(self._view(universe).facts.sites_by_target.get(self._store.model_key(reference)))

    def has_data_type_references_other_than(
        self, reference: Reference, excluded_data_type: DataType, *, universe: Universe = "parsed"
    ) -> bool:
        """Return whether tracked data types other than ``excluded_data_type`` point at ``reference``."""
        facts = self._view(universe).facts
        data_type_ids = facts.sites_by_target.get(self._store.model_key(reference))
        if not data_type_ids:
            return False
        excluded_data_type_id = id(excluded_data_type)
        return any(
            facts.data_type_facts[data_type_id].data_type is not excluded_data_type
            and id(facts.data_type_facts[data_type_id].data_type) != excluded_data_type_id
            for data_type_id in data_type_ids
        )

    def reference_classes_for_model(self, model: DataModel, *, universe: Universe = "parsed") -> frozenset[str]:
        """Return reference paths matching ``DataModel.reference_classes`` semantics."""
        view = self._view(universe)
        facts = view.facts
        model_id = view.model_id(model)
        if model_id is None:
            return frozenset(model.reference_classes)
        include_dict_key_references = (
            include_dict_key_reference_classes := model._INCLUDE_DICT_KEY_REFERENCE_CLASSES  # noqa: SLF001
        ) is not None and include_dict_key_reference_classes(model)
        reference_classes = frozenset(
            reference.path
            for data_type_id in facts.data_types_by_model.get(model_id, ())
            if (reference := (fact := facts.data_type_facts[data_type_id]).reference) is not None
            if include_dict_key_references or fact.role != "dict_key"
        )
        if len(additional_properties_references := model.additional_properties_reference_classes):
            reference_classes = reference_classes.union(additional_properties_references)
        return reference_classes

    def reference_classes_for_model_including_dict_keys(
        self, model: DataModel, *, universe: Universe = "parsed"
    ) -> frozenset[str]:
        """Return every referenced path for fallback-only dependency analysis."""
        view = self._view(universe)
        facts = view.facts
        model_id = view.model_id(model)
        if model_id is None:
            reference_classes = frozenset(model.reference_classes).union(
                data_type.reference.path
                for field in model.fields
                for data_type in field.data_type.all_data_types
                if data_type.reference is not None
            )
        else:
            reference_classes = frozenset(
                reference.path
                for data_type_id in facts.data_types_by_model.get(model_id, ())
                if (reference := facts.data_type_facts[data_type_id].reference) is not None
            )
        if len(additional_properties_references := model.additional_properties_reference_classes):
            return reference_classes.union(additional_properties_references)
        return reference_classes

    def referenced_keys_for_model(
        self, model: DataModel, keyed: KeyedModels, *, all_dict_keys: bool = False, universe: Universe = "parsed"
    ) -> frozenset[ModelKey]:
        """Return the keys of the models that ``model`` references, whose paths `reference_classes_for_model` returns.

        With ``all_dict_keys``, dict keys count as `reference_classes_for_model_including_dict_keys` counts them. The
        references of the model (those its recorded data types hold, or for a model the facts do not record, its bases
        and the data types of its fields) resolve against ``keyed``, and so do the paths of its additional-properties
        snapshot, which for an unrecorded model only `reference_classes_for_model_including_dict_keys` reads.
        """
        view = self._view(universe)
        if (model_id := view.model_id(model)) is None:
            keys = keyed.keys_of(_unrecorded_references(model, all_dict_keys=all_dict_keys))
            additional_properties_references = model.additional_properties_reference_classes if all_dict_keys else ()
        else:
            keys = self._recorded_reference_keys(model, model_id, view, keyed, all_dict_keys=all_dict_keys)
            additional_properties_references = model.additional_properties_reference_classes
        return keys | keyed.resolve(additional_properties_references) if additional_properties_references else keys

    def _recorded_reference_keys(
        self, model: DataModel, model_id: ModelId, view: _UniverseFacts, keyed: KeyedModels, *, all_dict_keys: bool
    ) -> frozenset[ModelKey]:
        """Return the keys of the models of ``keyed`` named by the references the data types of ``model`` hold.

        The references are cached per model for the parsed universe.
        """
        self._reset_recorded_references_cache_if_needed()
        cache = self._recorded_references_cache if view.universe == "parsed" else {}
        cache_key = (model_id, all_dict_keys)
        if (cached := cache.get(cache_key)) is None:
            facts = view.facts
            include_dict_keys = all_dict_keys or (
                (include_dict_key_reference_classes := model._INCLUDE_DICT_KEY_REFERENCE_CLASSES) is not None  # noqa: SLF001
                and include_dict_key_reference_classes(model)
            )
            references = tuple(
                reference
                for data_type_id in facts.data_types_by_model.get(model_id, ())
                if (reference := (fact := facts.data_type_facts[data_type_id]).reference) is not None
                if include_dict_keys or fact.role != "dict_key"
            )
            for reference in references:
                self._store.model_key(reference)
            cached = cache[cache_key] = references
        return keyed.keys_of(cached)

    def owner_model_for_data_type(self, data_type: DataType, *, universe: Universe = "parsed") -> DataModel | None:
        """Return the tracked model that owns ``data_type`` if known."""
        facts = self._view(universe).facts
        fact = facts.data_type_fact_by_object.get(id(data_type))
        if fact is None:
            return None
        return facts.model_facts[fact.owner_model].model

    def inlined_holders_of(self, data_type: DataType) -> tuple[DataType, ...]:
        """Return the types besides its ``parent`` that hold ``data_type``, sharing the parent's children with it.

        Root-model collapse makes them as copies of the root type for its sites (``GenerationStore.inline_shared``).
        """
        parent = data_type.parent
        if (owners := self._store._inlined_owners.get(NodeKey(id(parent)))) is None:  # noqa: SLF001
            return ()
        return tuple(
            owner
            for owner in owners
            if owner is not parent
            and (owner.dict_key is data_type or any(member is data_type for member in owner.data_types))
        )

    def root_model_wrappers_for_reference(
        self,
        reference: Reference,
        root_model_type: type[DataModel],
        *,
        universe: Universe = "parsed",
    ) -> list[DataModel]:
        """Return root-model wrappers whose field graph points at ``reference``."""
        facts = self._view(universe).facts
        wrappers: list[DataModel] = []
        seen: set[int] = set()
        data_type_ids = facts.sites_by_target.get(self._store.model_key(reference))
        if not data_type_ids:
            return wrappers
        for data_type_id in data_type_ids:
            fact = facts.data_type_facts[data_type_id]
            owner = facts.model_facts[fact.owner_model].model
            if isinstance(owner, root_model_type) and id(owner) not in seen:
                seen.add(id(owner))
                wrappers.append(owner)
        return wrappers

    def root_collapse_reference_usage(
        self,
        reference: Reference,
        *,
        excluded_model: DataModel,
        is_root_model: Callable[[DataModel], bool],
        universe: Universe = "parsed",
    ) -> tuple[list[DataModel], list[DataTypeFact]]:
        """Return wrapper models and direct non-wrapper refs for root collapse checks."""
        facts = self._view(universe).facts
        wrappers: list[DataModel] = []
        direct_refs: list[DataTypeFact] = []
        wrapper_ids: set[int] = set()
        data_type_ids = facts.sites_by_target.get(self._store.model_key(reference))
        if not data_type_ids:
            return wrappers, direct_refs

        for data_type_id in data_type_ids:
            fact = facts.data_type_facts[data_type_id]
            owner = facts.model_facts[fact.owner_model].model
            if is_root_model(owner):
                if id(owner) not in wrapper_ids:
                    wrapper_ids.add(id(owner))
                    wrappers.append(owner)
                continue
            if fact.role != "base" and owner is not excluded_model:
                direct_refs.append(fact)
        return wrappers, direct_refs

    def direct_non_root_refs_for_reference(
        self,
        reference: Reference,
        *,
        excluded_model: DataModel,
        root_model_type: type[DataModel],
        universe: Universe = "parsed",
    ) -> list[DataTypeFact]:
        """Return non-wrapper field references to ``reference`` outside ``excluded_model``."""
        facts = self._view(universe).facts
        direct_refs: list[DataTypeFact] = []
        data_type_ids = facts.sites_by_target.get(self._store.model_key(reference))
        if not data_type_ids:
            return direct_refs
        for data_type_id in data_type_ids:
            fact = facts.data_type_facts[data_type_id]
            if fact.role == "base":
                continue
            owner = facts.model_facts[fact.owner_model].model
            if owner is excluded_model or isinstance(owner, root_model_type):
                continue
            direct_refs.append(fact)
        return direct_refs


@dataclass(slots=True)
class _RootCollapseReferenceScope:
    """Lazy reference counts for one root-collapse mutation scope."""

    reference_counts: dict[ModelKey, int] = field(default_factory=dict)
    enabled: bool = True
    initialized: bool = False
    mutated: bool = False

    def invalidate(self) -> None:
        """Disable incremental updates after an unsupported mutation."""
        self.enabled = False
        self.reference_counts.clear()


@final
class _ReferenceCopies:
    """The Reference object that the store met first under each model key, kept under the ``MODEL_GRAPH`` check."""

    def __init__(self, mode: CheckMode) -> None:
        """Start with no References met."""
        self._mode = mode
        self._originals: dict[ModelKey, Reference] = {}
        self._reported: set[ModelKey] = set()

    def model_key(self, reference: Reference) -> ModelKey:
        """Return the model key of ``reference``; report the first copy met of each key, once."""
        key = model_key_of(reference)
        if self._originals.setdefault(key, reference) is not reference and key not in self._reported:
            self._reported.add(key)
            location = identity_of(reference).location
            path = redact_reference(reference.path)
            self._mode.violation(
                Error,
                f"a copy of the Reference of {reference.name} ({path}) reached the generation store beside the "
                "original, whose model key it shares",
                name=reference.name,
                path=path,
                location=None if location is None else redact_reference(str(location)),
            )
        return key


class GenerationStore:  # noqa: PLR0904
    """Parse-to-output generation facts for a Parser instance."""

    def __init__(self) -> None:
        """Initialize an empty generation store."""
        self.models: _GenerationModelList = _GenerationModelList(
            self._invalidate_models, self._invalidate_appended_model
        )
        self.index = GenerationIndex(self)
        self._facts = GenerationFacts()
        self._model_ids_by_object: dict[int, ModelId] = {}
        self._next_model_id = 0
        self._next_data_type_id = 0
        self._facts_version = 0
        self._dirty = True
        self._rebuild_required = True
        self._appended_model_ids: set[int] = set()
        self._changed_model_ids: set[ModelId] = set()
        self._renamed_model_ids: set[ModelId] = set()
        self._defer_refresh_depth = 0
        self._active_root_collapse_reference_scope: _RootCollapseReferenceScope | None = None
        self._root_collapse_internal_mutation_depth = 0
        self._reference_copies: _ReferenceCopies | None = None
        self._built_models: list[DataModel] = []
        self._retired_models: dict[int, DataModel] = {}
        self._site_effects = _SiteEffects()
        self._carried_sites: Iterable[RefSite] = ()
        self._inlined_owners: dict[NodeKey, list[DataType]] = {}
        self._memo = ParseMemo()
        """The memos of the parse, which ``Parser.parse`` makes current while it runs."""
        self.phase = Phase.PARSE
        """The phase of the pipeline that runs now, which the parser pipeline sets as it enters it."""
        self._builder = GraphBuilder(self, _BuilderToken())

    @classmethod
    def create_with_results(cls) -> tuple[GenerationStore, list[DataModel]]:
        """Create a store and the public ``Parser.results`` list view together."""
        store = cls()
        return store, store.models

    def _dispose(self, references: Iterable[Reference] = ()) -> None:
        """Drop all facts and model references so the parsed graph can be reclaimed.

        The store, its model list, and its facts hold strong references to every
        model and data type; clearing them removes the last anchors keeping the
        object graph alive once the parser is dropped. Models, fields, data
        types, and references also point at each other in cycles, so their back
        references are severed first to let ordinary reference counting reclaim
        the graph without waiting for a full garbage collection pass.
        """
        for model in self.models:
            for model_field in model.fields:
                _detach_data_type_tree(model_field.data_type)
                model_field.parent = None
            for base_class in model.base_classes:
                _detach_data_type_tree(base_class)
        for reference in references:
            reference.children.clear()
            reference.source = None
        self._facts = GenerationFacts()
        self._model_ids_by_object.clear()
        self.models.clear()
        self._appended_model_ids.clear()
        self._changed_model_ids.clear()
        self._renamed_model_ids.clear()
        self._reference_copies = None
        self._built_models.clear()
        self._retired_models.clear()
        self._site_effects.clear()
        self._carried_sites = ()
        self._inlined_owners.clear()
        self._memo = ParseMemo()
        self._dirty = True

    @property
    def facts(self) -> GenerationFacts:
        """Return the current facts snapshot."""
        return self.current_facts()

    @property
    def facts_version(self) -> int:
        """Return the current facts snapshot version."""
        return self._facts_version

    def current_facts(self) -> GenerationFacts:
        """Return current facts after rebuilding stale data."""
        self.refresh()
        return self._facts

    @contextmanager
    def _collapse_root_reference_scope(self) -> Generator[None, None, None]:
        """Track root-reference counts while one collapse pass mutates the store."""
        if self._active_root_collapse_reference_scope is not None:
            msg = "Root-collapse reference scopes cannot be nested."
            raise RuntimeError(msg)

        scope = _RootCollapseReferenceScope()
        self._active_root_collapse_reference_scope = scope
        completed = False
        try:
            yield
            completed = True
        finally:
            try:
                if completed and scope.mutated:
                    self.refresh_now()
            finally:
                scope.invalidate()
                self._active_root_collapse_reference_scope = None

    def _root_collapse_scope_if_ready(self) -> _RootCollapseReferenceScope | None:
        """Return an initialized tracker, or disable it when facts cannot be trusted."""
        scope = self._active_root_collapse_reference_scope
        if scope is None or not scope.enabled:
            return None
        if scope.initialized:
            return scope

        self.current_facts()
        scope = self._active_root_collapse_reference_scope
        if scope is None or not scope.enabled:
            return None
        if not scope.initialized:
            if self._dirty:
                scope.invalidate()
                return None
            scope.initialized = True
            scope.enabled = len(self._facts.data_type_fact_by_object) == len(self._facts.data_type_facts)
        return scope if scope.enabled else None

    def _root_collapse_has_data_type_references(
        self,
        reference: Reference,
        *,
        excluded_data_type: DataType | None = None,
    ) -> bool | None:
        """Return a scoped reference count, or ``None`` when incremental facts are unavailable."""
        scope = self._root_collapse_scope_if_ready()
        if scope is None:
            return None

        key = self.model_key(reference)
        if (count := scope.reference_counts.get(key)) is None:
            count = len(self._facts.sites_by_target.get(key, ()))
            scope.reference_counts[key] = count
        if excluded_data_type is not None:
            excluded_fact = self._facts.data_type_fact_by_object.get(id(excluded_data_type))
            if excluded_fact is None or excluded_fact.data_type is not excluded_data_type:
                scope.invalidate()
                return None
            count -= excluded_fact.reference is reference
        return count > 0

    def _root_collapse_prepare_data_type_replacement(
        self,
        data_type: DataType,
        new_data_type: DataType,
    ) -> dict[ModelKey, int] | None:
        """Prepare one replacement's reference deltas, or return ``None`` for fallback."""
        scope = self._root_collapse_scope_if_ready()
        if scope is None:
            return None

        data_type_fact = self._facts.data_type_fact_by_object.get(id(data_type))
        if data_type_fact is None or data_type_fact.data_type is not data_type:
            scope.invalidate()
            return None

        deltas: dict[ModelKey, int] = {}
        seen_data_type_ids: set[int] = set()
        for nested_data_type in new_data_type.all_data_types:
            nested_data_type_id = id(nested_data_type)
            if nested_data_type_id in seen_data_type_ids or nested_data_type_id in self._facts.data_type_fact_by_object:
                scope.invalidate()
                return None
            seen_data_type_ids.add(nested_data_type_id)
            if nested_data_type.reference:
                key = self.model_key(nested_data_type.reference)
                deltas[key] = deltas.get(key, 0) + 1
        for nested_data_type in data_type.all_data_types:
            if nested_data_type.reference:
                key = self.model_key(nested_data_type.reference)
                deltas[key] = deltas.get(key, 0) - 1
        return deltas

    def _root_collapse_apply_reference_deltas(self, deltas: dict[ModelKey, int]) -> bool:
        """Apply reference-count deltas when every affected reference is tracked."""
        scope = self._active_root_collapse_reference_scope
        if scope is None or not scope.enabled:
            return False

        for key, delta in deltas.items():
            if delta and (key not in self._facts.model_by_ref_id and key not in self._facts.sites_by_target):
                scope.invalidate()
                return False

        for key, delta in deltas.items():
            if (count := scope.reference_counts.get(key)) is None:
                count = len(self._facts.sites_by_target.get(key, ()))
            count += delta
            if count < 0:
                scope.invalidate()
                return False
            scope.reference_counts[key] = count
        return True

    def _root_collapse_record_reference_change(
        self,
        data_type: DataType,
        old_reference: Reference | None,
        new_reference: Reference | None,
    ) -> bool:
        """Update counts for one in-place reference replacement."""
        if old_reference is new_reference:
            return True
        scope = self._root_collapse_scope_if_ready()
        if scope is None:
            return False
        deltas: dict[ModelKey, int] = {}
        if old_reference is not None:
            deltas[self.model_key(old_reference)] = -1
        if new_reference is not None:
            new_key = self.model_key(new_reference)
            deltas[new_key] = deltas.get(new_key, 0) + 1
        if (fact := self._facts.data_type_fact_by_object.get(id(data_type))) is None or fact.data_type is not data_type:
            scope.invalidate()
            return False
        return self._root_collapse_apply_reference_deltas(deltas)

    @property
    def model_facts(self) -> dict[ModelId, ModelFact]:
        """Compatibility access to model facts."""
        self.refresh()
        return self._facts.model_facts

    @property
    def data_type_facts(self) -> dict[DataTypeId, DataTypeFact]:
        """Compatibility access to data type facts."""
        self.refresh()
        return self._facts.data_type_facts

    @property
    def data_type_fact_by_object(self) -> dict[int, DataTypeFact]:
        """Compatibility access to data type facts by object id."""
        self.refresh()
        return self._facts.data_type_fact_by_object

    @property
    def model_by_path(self) -> dict[str, ModelId]:
        """Compatibility access to model ids by path."""
        self.refresh()
        return self._facts.model_by_path

    @property
    def model_by_ref_id(self) -> dict[ModelKey, ModelId]:
        """Compatibility access to model ids by the model key of their reference."""
        self.refresh()
        return self._facts.model_by_ref_id

    @property
    def data_types_by_model(self) -> dict[ModelId, tuple[DataTypeId, ...]]:
        """Compatibility access to data type ids by owner model."""
        self.refresh()
        return self._facts.data_types_by_model

    @property
    def sites_by_target(self) -> defaultdict[ModelKey, OrderedSet[SiteKey]]:
        """The reference sites of the type trees, by the model key of their target."""
        self.refresh()
        return self._facts.sites_by_target

    def builder(self) -> GraphBuilder:
        """Return the capability to change the graph of this store, through which every mutation goes."""
        return self._builder

    def enter_phase(self, phase: Phase) -> None:
        """Mark that the pipeline enters ``phase``; in check mode, the finality checks compare every model here."""
        self.phase = phase

    def seal_facts(  # noqa: PLR6301
        self,
        modules: Iterable[tuple[tuple[str, ...], Iterable[DataModel]]],
        view: Callable[[DataModel], ModelView] | None = None,
    ) -> FinalFacts:
        """Return the facts final at B1 for the decision phase: the live models of ``modules``, in module order.

        ``view`` gives the view a decision reads of each model (the guarded view of the finality check); without it,
        the view is the model. This is the only construction of ``FinalFacts``.
        """
        module_facts = tuple(
            ModuleFacts(module, tuple(models if view is None else map(view, models))) for module, models in modules
        )
        return FinalFacts(module_facts, tuple(model for facts in module_facts for model in facts.live_models))

    @deprecated("Use GenerationStore.builder().register_model() instead.", category=None)
    def register_model(self, model: DataModel) -> None:
        """Register a model the output writes.

        A model registered while parsing joins the parsed models (``Parser.results``) in parser append order. One a
        later pass builds, such as a reuse subclass or the generic base class, joins the emitted models only, so the
        facts of the parsed universe stay as they were.
        """
        if self.phase is Phase.PARSE:
            self.models.append(model)
            return
        self._retired_models.pop(id(model), None)
        self._built_models.append(model)

    @deprecated("Use GenerationStore.builder().retire_model() instead.", category=None)
    def retire_model(self, model: DataModel) -> None:
        """Record that ``model`` leaves the output; the parsed universe keeps it, the emitted one drops it."""
        self._retired_models[id(model)] = model

    @deprecated("Use GenerationStore.builder().replace_model() instead.", category=None)
    def replace_model(self, model: DataModel, replacement: DataModel) -> None:
        """Record that ``replacement``, built after parsing under the same model key, takes the place of ``model``."""
        self._builder.retire_model(model)
        self._builder.register_model(replacement)

    def emitted_models(self) -> list[DataModel]:
        """Return the models of the emitted universe: the parsed and built ones that are not retired, in that order."""
        retired = self._retired_models
        return [model for model in chain(self.models, self._built_models) if id(model) not in retired]

    def _universe_facts(self, universe: Universe) -> _UniverseFacts:
        """Return the current facts of ``universe`` with the ids they record each model under.

        The facts of the emitted universe are built in full at each read; a model it shares with the parsed universe
        keeps its id.
        """
        facts = self.current_facts()
        if universe == "parsed":
            return _UniverseFacts("parsed", facts, self._model_ids_by_object)
        result = GenerationIndexBuilder(
            self.model_key, _SiteHistory(facts.sites.values(), _SiteEffects(), COPIED)
        ).build(
            self.emitted_models(),
            previous_model_ids_by_object=self._model_ids_by_object,
            next_model_id=self._next_model_id,
        )
        return _UniverseFacts("emitted", result.facts, result.model_ids_by_object)

    def check_emitted_models(self, mode: CheckMode, written: Iterable[DataModel]) -> None:
        """Report each model the output writes that is not in the emitted universe, and each one there it does not."""
        written_models = {id(model): model for model in written}
        emitted = {id(model): model for model in self.emitted_models()}
        for models, others, problem in (
            (written_models, emitted, "is written but is not an emitted model: it was never registered or was retired"),
            (
                emitted,
                written_models,
                "is an emitted model but is not written: it left the output without being retired",
            ),
        ):
            for model_id, model in models.items():
                if model_id not in others:
                    path = redact_reference(model.reference.path)
                    mode.violation(Error, f"the model {model.class_name} ({path}) {problem}", path=path)

    def model_id(self, model: DataModel) -> ModelId | None:
        """Return the stable store id for ``model`` if it is tracked."""
        return self._model_ids_by_object.get(id(model))

    def model_key(self, reference: Reference) -> ModelKey:
        """Return the model key that the facts record ``reference`` and its users under.

        Under the ``MODEL_GRAPH`` check, a Reference object that shares its key with another one the store met first
        (a copy of it) is a violation: the store would take both for one model.
        """
        if (copies := self._reference_copies) is None:
            return model_key_of(reference)
        return copies.model_key(reference)

    def _invalidate(self) -> None:
        """Invalidate derived facts after a store-managed mutation."""
        self._dirty = True
        if scope := self._active_root_collapse_reference_scope:
            scope.mutated = True
            if not self._root_collapse_internal_mutation_depth:
                scope.invalidate()

    def _invalidate_models(self) -> None:
        """Invalidate every fact after the model list changes."""
        self._rebuild_required = True
        self._invalidate()

    def _invalidate_appended_model(self, model: DataModel) -> None:
        """Record a model appended to the model list at the next refresh, or every model if it was listed already."""
        if self._rebuild_required or id(model) in self._model_ids_by_object or id(model) in self._appended_model_ids:
            self._invalidate_models()
            return
        self._appended_model_ids.add(id(model))
        self._invalidate()

    def _tracked_model_id(self, model: object) -> ModelId | None:
        """Return the id of ``model`` when the current facts record it."""
        if (
            (model_id := self._model_ids_by_object.get(id(model))) is not None
            and (model_fact := self._facts.model_facts.get(model_id)) is not None
            and model_fact.model is model
        ):
            return model_id
        return None

    @staticmethod
    @deprecated("Use GenerationStore.builder().record_values_as_written() instead.", category=None)
    def record_values_as_written(owner: DataModel | DataType, values: Sequence[Any]) -> None:
        """Keep on a new enum model or Literal type the values as written of the enum or const it holds some of.

        Duplicate models compare them besides their rendering (``DataModel._render_dedup_key``), so models the
        literal check narrows to the same values stay apart as they were written. Nothing the store derives reads them.
        """
        owner.__dict__[VALUES_AS_WRITTEN_KEY] = tuple(values)

    @staticmethod
    @deprecated("Use GenerationStore.builder().record_dropped_multiple_of() instead.", category=None)
    def record_dropped_multiple_of(data_type: DataType, value: float) -> None:
        """Keep on a new constrained type the ``multipleOf`` it is written without, as the backend refuses it.

        The field holding the type warns that it is generated without it (``dropped_constraints_warning``).
        """
        data_type.__dict__[DROPPED_MULTIPLE_OF_KEY] = value
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().restore_required_inherited_field() instead.", category=None)
    def restore_required_inherited_field(model: DataModel, field_: DataModelFieldBase) -> bool:
        """Run the model's ``restore_required_inherited_field_state`` hook for a field; return what it returns.

        The hook gives back to the constructor a required field the inherited constructor excluded, removing the
        marks it left on the field; the field is bumped when it did or when it removed any.
        """
        before = (len(field_.__dict__), len(field_.extras))
        restored = model.restore_required_inherited_field_state(field_)
        if restored or (len(field_.__dict__), len(field_.extras)) != before:
            bump_field(field_)
        return restored

    @staticmethod
    @deprecated("Use GenerationStore.builder().force_field_assignment() instead.", category=None)
    def force_field_assignment(field_: DataModelFieldBase) -> None:
        """Run the field's ``force_field_assignment`` hook: it renders an explicit required-field assignment."""
        field_.force_field_assignment()
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().mark_forward_references() instead.", category=None)
    def mark_forward_references(model: DataModel) -> None:
        """Make a model write its fields for the quoted forward references they hold: unions with ``Union``."""
        model.has_forward_reference = True
        bump_model(model)

    @staticmethod
    @deprecated("Use GenerationStore.builder().mark_target_null() instead.", category=None)
    def mark_target_null(data_type: DataType) -> None:
        """Mark the None of a new reference type as the one the schema it references admits.

        ``--omit-redundant-reference-null`` drops that None where the model the type names holds null itself, and
        duplicate models compare the marks besides their rendering (``DataModel._render_dedup_key``).
        """
        data_type.__dict__[TARGET_NULL_KEY] = True
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().clear_target_null() instead.", category=None)
    def clear_target_null(data_type: DataType) -> None:
        """Forget that the None of a type is its target's, after a null of another source joined it."""
        data_type.__dict__.pop(TARGET_NULL_KEY, None)
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().write_rendered_null() instead.", category=None)
    def write_rendered_null(data_type: DataType) -> None:
        """Write into ``is_optional`` the None that the hint of ``data_type`` renders, marked as render preparation's.

        Render preparation writes rendered nullability once for every node of every model. A None that is not the
        type's own comes from its nullable model (also marked as not the type's own) or from its union's members.
        """
        if (null := data_type._hint_null_source()) in {None, "own"}:  # noqa: SLF001
            return
        data_type.__dict__[RENDER_PREPARATION_NULL_KEY] = True
        data_type.is_optional = True
        if null == "reference":
            data_type.__dict__[REFERENCE_NULL_KEY] = True
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().drop_held_null() instead.", category=None)
    def drop_held_null(data_type: DataType) -> None:
        """Drop the None a reference type restates of the model it names, which holds null itself.

        The type loses its own None when that None is its target's or the one its nullable model gave it, and the None
        of its nullable model.
        """
        if data_type.__dict__.pop(REFERENCE_NULL_KEY, None) or TARGET_NULL_KEY in data_type.__dict__:
            data_type.is_optional = False
        data_type.__dict__[HELD_NULL_DROPPED_KEY] = True
        bump_node(data_type)

    @deprecated("Use GenerationStore.builder().note_changed_models() instead.", category=None)
    def note_changed_models(self, *models: object) -> None:
        """Record ``models`` again at the next refresh, after their types changed outside the store.

        The facts stay as they are until that refresh, as they did when every refresh rebuilt them all. The new sites of
        ``models`` are overridden, and their revisions move.
        """
        for model in models:
            bump_model(model)
        self._state_model_sites("note_changed_models", *models)
        if self._rebuild_required:
            return
        for model in models:
            if (model_id := self._tracked_model_id(model)) is not None:
                self._changed_model_ids.add(model_id)

    def _note_changed_data_types(self, data_types: Iterable[object]) -> None:
        """Record again, at the next refresh, the models whose recorded facts contain ``data_types``.

        A data type that joined a model's tree after the last refresh did so through a mutation that
        already noted that model, so the recorded owners are the only ones left to note. What reads the data types
        is bumped (`bump_node`).
        """
        data_types = tuple(data_types)
        for data_type in data_types:
            bump_node(data_type)
        if self._rebuild_required:
            return
        facts = self._facts
        for data_type in data_types:
            object_id = id(data_type)
            if (data_type_ids := facts.shared_data_type_fact_ids.get(object_id)) is not None:
                self._changed_model_ids.update(
                    facts.data_type_facts[data_type_id].owner_model for data_type_id in data_type_ids
                )
            elif (fact := facts.data_type_fact_by_object.get(object_id)) is not None and fact.data_type is data_type:
                self._changed_model_ids.add(fact.owner_model)

    def refresh(self) -> None:
        """Bring facts up to date with the live model list."""
        if self._defer_refresh_depth:
            return

        self.refresh_now()

    def refresh_now(self) -> None:
        """Bring facts up to date immediately, even inside a deferred mutation block.

        Appended models and the models that mutations noted are recorded on their own; any other change of the
        model list rebuilds every fact.
        """
        if not self._dirty:
            return

        recorded_model_ids: Iterable[ModelId] | None = None
        if (mode := check_mode(CheckArea.MODEL_GRAPH)) is not None:
            if self._reference_copies is None:
                self._reference_copies = _ReferenceCopies(mode)
            recorded_model_ids = self._check_no_data_type_cycles(mode)
        history = _SiteHistory(self._carried_sites or self._facts.sites.values(), self._site_effects, PARSED)
        if self._rebuild_required:
            result = GenerationIndexBuilder(self.model_key, history).build(
                self.models,
                previous_model_ids_by_object=self._model_ids_by_object,
                next_model_id=self._next_model_id,
            )
        else:
            try:
                result = GenerationIndexBuilder(self.model_key, history).update(
                    self._facts,
                    self.models,
                    appended_count=len(self._appended_model_ids),
                    changed_model_ids=self._changed_model_ids,
                    renamed_model_ids=self._renamed_model_ids,
                    model_ids_by_object=self._model_ids_by_object,
                    next_model_id=self._next_model_id,
                    next_data_type_id=self._next_data_type_id,
                )
            except BaseException:
                # The update may have recorded ids for appended models before it failed; never reuse them.
                self._rebuild_required = True
                self._next_model_id = max(self._next_model_id, max(self._model_ids_by_object.values(), default=-1) + 1)
                raise
            if mode is not None:
                self._check_update_matches_rebuild(mode, result)
        self._facts = result.facts
        self._model_ids_by_object = result.model_ids_by_object
        self._next_model_id = result.next_model_id
        self._next_data_type_id = result.next_data_type_id
        self._rebuild_required = False
        self._appended_model_ids.clear()
        self._changed_model_ids.clear()
        self._renamed_model_ids.clear()
        self._site_effects.clear()
        self._carried_sites = ()
        self._facts_version += 1
        self._dirty = False
        if mode is not None:
            self._check_parents_are_holders(
                mode, self._facts.model_facts if recorded_model_ids is None else recorded_model_ids
            )
        if scope := self._active_root_collapse_reference_scope:
            if scope.initialized:
                scope.invalidate()
            elif scope.enabled:
                scope.initialized = True
                scope.enabled = len(self._facts.data_type_fact_by_object) == len(self._facts.data_type_facts)

    def _check_update_matches_rebuild(self, mode: CheckMode, result: GenerationBuildResult) -> None:
        """Report facts that an update recorded differently from a full rebuild of the same models.

        An update records the models that operations noted since the last refresh; a model that changed without a
        note keeps its old facts, which a rebuild would not.
        """
        rebuilt = GenerationIndexBuilder(self.model_key).build(
            self.models,
            previous_model_ids_by_object=result.model_ids_by_object,
            next_model_id=result.next_model_id,
        )
        updated = _fact_record(result.facts)
        for part, recorded in _fact_record(rebuilt.facts).items():
            if updated[part] != recorded:
                mode.violation(
                    Error,
                    f"the {part} that a refresh recorded for the noted models differ from a full rebuild",
                    part=part,
                )

    def _check_no_data_type_cycles(self, mode: CheckMode) -> Iterable[ModelId] | None:
        """Report a data type that contains itself in any model, noted or not.

        The check runs before the refresh walks the trees, and before the comparison with a rebuild walks every tree;
        neither ends on a cycle. Return the ids the recorded models have after the refresh, or None when it records
        every model.
        """
        recorded_model_ids: Iterable[ModelId] | None = None
        if not self._rebuild_required:
            recorded_model_ids = (
                *range(self._next_model_id, self._next_model_id + len(self._appended_model_ids)),
                *self._changed_model_ids,
            )
        finished: set[int] = set()
        for model in self.models:
            roots = chain((field_.data_type for field_ in model.fields), model.base_classes)
            if (node := node_in_cycle(roots, finished)) is not None:
                mode.violation(
                    Error,
                    f"a data type of {model.class_name} contains itself",
                    model=model.class_name,
                    type=type(node).__name__,
                )
        return recorded_model_ids

    def _check_parents_are_holders(self, mode: CheckMode, model_ids: Iterable[ModelId]) -> None:
        """Report a node of the models with ``model_ids`` whose ``parent`` is not one of its recorded holders.

        A mismatch that `KNOWN_PARENT_MISMATCHES` lists is left alone.
        """
        facts = self._facts
        holders = _HolderIndex(facts)
        checked: set[int] = set()
        for model_id in model_ids:
            model_fact = facts.model_facts[model_id]
            for data_type_id in facts.data_types_by_model[model_id]:
                fact = facts.data_type_facts[data_type_id]
                if (node_key := id(fact.data_type)) in checked:
                    continue
                checked.add(node_key)
                parent = fact.data_type.parent
                if parent_is_holder(parent, holders.holders_of(NodeKey(node_key))):
                    continue
                if (mismatch := self._parent_mismatch(parent)) in KNOWN_PARENT_MISMATCHES:
                    continue
                mode.violation(
                    Error,
                    f"a {fact.role} data type of {model_fact.class_name} has a {mismatch} as its parent, which "
                    "does not hold it",
                    model=model_fact.class_name,
                    role=fact.role,
                    parent=mismatch,
                )

    def _parent_mismatch(self, parent: object) -> ParentMismatch:
        """Return what kind of object ``parent`` is, for a data type that ``parent`` does not hold."""
        from datamodel_code_generator.model.base import (  # noqa: PLC0415
            DataModelFieldBase as RuntimeDataModelFieldBase,
        )

        if not isinstance(parent, RuntimeDataModelFieldBase):
            return "data type"
        if (model := parent.parent) is not None and self._tracked_model_id(model) is not None:
            return "recorded field"
        return "dropped field"

    @deprecated("Use GenerationStore.builder().discard_derived_facts() instead.", category=None)
    def discard_derived_facts(self) -> None:
        """Release cached dependency facts while preserving stable model identities."""
        if self._defer_refresh_depth:
            msg = "Derived facts cannot be discarded during a deferred refresh."
            raise RuntimeError(msg)
        self._carried_sites = self._carried_sites or self._facts.sites.values()
        self._facts = GenerationFacts()
        self._appended_model_ids.clear()
        self._changed_model_ids.clear()
        self._renamed_model_ids.clear()
        self._rebuild_required = True
        self._dirty = True
        if scope := self._active_root_collapse_reference_scope:
            scope.mutated = True
            scope.invalidate()

    @deprecated("Use GenerationStore.builder().replace_data_type_ref() instead.", category=None)
    def replace_data_type_ref(self, data_type: DataType, new_reference: Reference | None) -> None:
        """Set ``data_type.reference`` while preserving reverse reference links; its new site is redirected."""
        if new_reference is not None:
            self._state_sites_at("replace_data_type_ref", (data_type,), via=data_type.reference)
        cache_owners = self._cache_owners_for_data_type(data_type)
        scope = self._active_root_collapse_reference_scope
        if self._root_collapse_internal_mutation_depth:
            if scope is not None and scope.enabled:
                self._root_collapse_record_reference_change(data_type, data_type.reference, new_reference)
        elif scope is not None:
            scope.invalidate()
        self._replace_data_type_reference(data_type, new_reference)
        self._note_changed_data_types((data_type,))
        self._invalidate_owner_caches(*cache_owners)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().detach_data_type_ref() instead.", category=None)
    def detach_data_type_ref(self, data_type: DataType) -> None:
        """Remove ``data_type`` from its reference and invalidate derived facts."""
        self._builder.replace_data_type_ref(data_type, None)

    @deprecated("Use GenerationStore.builder().update_model_reference() instead.", category=None)
    def update_model_reference(
        self,
        model: DataModel,
        *,
        new_path: str | None = None,
        new_file_path: Path | None = None,
    ) -> None:
        """Update a model's reference path or output file and invalidate facts; ``rename_model`` changes names."""
        self._update_model_reference(model, new_path=new_path, new_file_path=new_file_path)

    def _update_model_reference(
        self,
        model: DataModel,
        *,
        class_name: str | None = None,
        reference_name: str | None = None,
        new_path: str | None = None,
        new_file_path: Path | None = None,
    ) -> None:
        if class_name is not None:
            model.class_name = class_name
        if reference_name is not None:
            model.reference.name = reference_name
        if new_path is not None:
            model.set_reference_path(new_path)
        if new_file_path is not None:
            model.file_path = new_file_path
        if not self._rebuild_required and (model_id := self._tracked_model_id(model)) is not None:
            self._renamed_model_ids.add(model_id)
        bump_model(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().rename_model() instead.", category=None)
    def rename_model(  # noqa: PLR0913
        self,
        model: DataModel,
        *,
        class_name: str | None = None,
        reference_name: str | None = None,
        clear_duplicate_name: bool = False,
        new_path: str | None = None,
        new_file_path: Path | None = None,
    ) -> None:
        """Change a model's class or reference name after parsing, the writer of names after the L2 settle.

        The callers apply six schemes: ``module`` (unique among the classes and imports of one module, ``Model``
        for a taken name), ``flattened-module`` (unique among the live models, with the ``--duplicate-name-suffix``
        map and ``Model``), ``field-type`` (a type hidden by a field of its user), ``imported-name`` (a local model
        shadowed by an import), ``internal-module`` (``<name>_<n>`` in a module cycle's ``_internal`` module) and
        ``collapsed-root`` (the inner model takes the root model's name and path). A rename that also relocates the
        model passes the new path and output file.
        """
        self._update_model_reference(
            model, class_name=class_name, reference_name=reference_name, new_path=new_path, new_file_path=new_file_path
        )
        if clear_duplicate_name:
            model.reference.duplicate_name = None

    @deprecated("Use GenerationStore.builder().move_model() instead.", category=None)
    def move_model(self, model: DataModel, *, new_path: str, new_file_path: Path | None = None) -> None:
        """Update a model's reference path and optional output file path."""
        self._builder.update_model_reference(model, new_path=new_path, new_file_path=new_file_path)

    @deprecated("Use GenerationStore.builder().replace_field_type() instead.", category=None)
    def replace_field_type(
        self, field_: DataModelFieldBase, new_data_type: DataType, *, refresh: Refresh = "now"
    ) -> None:
        """Replace a field's data type and invalidate derived facts; the sites of the new type are overridden."""
        self._state_tree_sites("replace_field_type", (new_data_type,))
        old_data_type = field_.data_type
        field_.replace_data_type(new_data_type)
        self._note_changed_data_types((old_data_type,))
        self._builder.note_changed_models(field_.parent)
        self._refresh_after_write(refresh)

    @deprecated("Use GenerationStore.builder().swap_data_type() instead.", category=None)
    def swap_data_type(self, data_type: DataType, new_data_type: DataType, *, refresh: Refresh = "now") -> None:
        """Put ``new_data_type`` where ``data_type`` is in its parent: a field's type or a member of a data type.

        A parent data type gets a new member list, so another holder of its old list keeps ``data_type``. The sites of
        ``new_data_type`` are overridden.
        """
        self._state_tree_sites("swap_data_type", (new_data_type,))
        self._note_changed_data_types((data_type,))
        data_type.swap_with(new_data_type)
        self._refresh_after_write(refresh)

    @deprecated("Use GenerationStore.builder().apply_const() instead.", category=None)
    def apply_const(self, field_: DataModelFieldBase, *, refresh: Refresh = "now") -> None:
        """Run the field's ``process_const`` hook, which may type its const as a Literal and write its default.

        The new sites of the field's model are overridden.
        """
        self._state_model_sites("apply_const", field_.parent)
        _bump_field_tree(field_)
        field_.process_const()
        self._builder.note_changed_models(field_.parent)
        self._refresh_after_write(refresh)

    @staticmethod
    @deprecated("Use GenerationStore.builder().apply_literal_default() instead.", category=None)
    def apply_literal_default(field_: DataModelFieldBase, decision: LiteralDefault) -> None:
        """Write the default, requiredness and nullability ``decide_literal_default`` decides for a field."""
        from datamodel_code_generator.model.base import _apply_literal_default  # noqa: PLC0415

        _apply_literal_default(field_, decision)
        bump_field(field_)

    @deprecated("Use GenerationStore.builder().drop_union_member_constraints() instead.", category=None)
    def drop_union_member_constraints(self, field_: DataModelFieldBase, *, refresh: Refresh = "now") -> None:
        """Run the field's ``unconstrain_union_members`` hook, which writes members into the union lists it holds.

        The hook replaces members inside the lists, so every holder of a list sees them. When the hook changed the
        field, what reads its type is bumped and the new sites of the field's model are overridden.
        """
        if field_.unconstrain_union_members() is False:
            return
        self._state_model_sites("drop_union_member_constraints", field_.parent)
        _bump_field_tree(field_)
        self._builder.note_changed_models(field_.parent)
        self._refresh_after_write(refresh)

    @deprecated("Use GenerationStore.builder().use_assignment_for_forward_references() instead.", category=None)
    def use_assignment_for_forward_references(
        self, model: DataModel, positions: Mapping[str, int], index: int, *, refresh: Refresh = "now"
    ) -> bool:
        """Run the model's ``assign_forward_reference_fields`` hook; return whether it changed the model.

        A model that writes fields by assignment may reorder its field list in place, reading the fields it wrote, so
        the fields its policy writes (``assigns_forward_reference_field``) are bumped first.
        """
        for field_ in model.fields:
            if model.assigns_forward_reference_field(field_, positions, index):
                bump_field(field_)
        if not model.assign_forward_reference_fields(positions, index):
            return False
        self._builder.note_changed_models(model)
        self._refresh_after_write(refresh)
        return True

    @staticmethod
    @deprecated("Use GenerationStore.builder().rename_field() instead.", category=None)
    def rename_field(field_: DataModelFieldBase, name: str, *, alias: str | None = None) -> None:
        """Give a field a new Python name, and ``alias`` as its alias when it has none, so it keeps its wire name."""
        if alias is not None and field_.alias is None:
            field_.alias = alias
        field_.name = name
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_field_default() instead.", category=None)
    def set_field_default(field_: DataModelFieldBase, default: Any) -> None:
        """Write a field's default, which the facts do not record, so they stay current."""
        field_.default = default
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().require_field() instead.", category=None)
    def require_field(field_: DataModelFieldBase, *, use_default_with_required: bool) -> None:
        """Make a field required, and write its default beside it when ``use_default_with_required``."""
        field_.required = True
        if use_default_with_required:
            field_.use_default_with_required = True
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_field_constraints() instead.", category=None)
    def set_field_constraints(
        field_: DataModelFieldBase, constraints: Any, *, type_dependent: Mapping[str, Any] | None = None
    ) -> None:
        """Write a field's constraints and, when given, the constraints that its type decides."""
        field_.constraints = constraints
        if type_dependent is not None:
            field_.__dict__[TYPE_DEPENDENT_CONSTRAINTS_KEY] = type_dependent
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_field_extras() instead.", category=None)
    def set_field_extras(field_: DataModelFieldBase, extras: dict[str, Any]) -> None:
        """Give a field a new extras dict."""
        field_.extras = extras
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_field_extra() instead.", category=None)
    def set_field_extra(field_: DataModelFieldBase, key: str, value: Any) -> None:
        """Write one entry into a field's extras dict, which every holder of the dict sees."""
        field_.extras[key] = value
        bump_field(field_)

    @staticmethod
    @deprecated("Use GenerationStore.builder().drop_field_discriminator() instead.", category=None)
    def drop_field_discriminator(field_: DataModelFieldBase) -> None:
        """Drop a field's discriminator from its extras and from its type, which every holder of the type sees."""
        field_.extras.pop("discriminator", None)
        field_.data_type.discriminator = None
        bump_field(field_)
        bump_node(field_.data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_type_discriminator() instead.", category=None)
    def set_type_discriminator(data_type: DataType, discriminator: str | None) -> None:
        """Write the discriminator of a data type, or none, which every holder of the type sees."""
        data_type.discriminator = discriminator
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().drop_union_operator() instead.", category=None)
    def drop_union_operator(data_type: DataType) -> None:
        """Join the members of a data type with ``Union`` instead of ``|``, which every holder of the type sees."""
        data_type.use_union_operator = False
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().use_typing_generics() instead.", category=None)
    def use_typing_generics(data_type: DataType) -> None:
        """Write the containers of a data type with the ``typing`` generics, which every holder of the type sees."""
        data_type.use_standard_collections = False
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().override_type_import() instead.", category=None)
    def override_type_import(data_type: DataType, import_: Import) -> None:
        """Make a data type name ``import_``, which every holder of the type sees."""
        data_type.import_ = import_
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().set_type_expressions() instead.", category=None)
    def set_type_expressions(  # noqa: PLR0913
        data_type: DataType,
        *,
        type_: str | None = None,
        import_: Import | None = None,
        python_type: Any = None,
        kwargs: dict[str, Any] | None = None,
        runtime_expression_imports: tuple[Import, ...] | None = None,
    ) -> None:
        """Write what a data type renders by, which every holder of the type sees.

        ``type_`` is the name it renders, ``import_`` the import it names, ``python_type`` its bound Python type, and
        ``kwargs`` its arguments, with the imports their runtime expressions name (``runtime_expression_imports``).
        """
        if type_ is not None:
            data_type.type = type_
        if import_ is not None:
            data_type.import_ = import_
        if python_type is not None:
            data_type.python_type = python_type
        if kwargs is not None:
            data_type.kwargs = kwargs
        if runtime_expression_imports is not None:
            data_type._set_runtime_expression_imports(runtime_expression_imports)  # noqa: SLF001
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().name_reference() instead.", category=None)
    def name_reference(data_type: DataType, alias: str) -> None:
        """Write the expression a reference type names its model by, such as a quoted forward reference or an import.

        Every holder of the type sees it.
        """
        data_type.alias = alias
        bump_node(data_type)

    @staticmethod
    @deprecated("Use GenerationStore.builder().add_model_import() instead.", category=None)
    def add_model_import(model: DataModel, import_: Import) -> None:
        """Add an import to a model once."""
        if import_ in model.imports:
            return
        model._additional_imports.append(import_)  # noqa: SLF001

    @deprecated("Use GenerationStore.builder().replace_nested_data_type() instead.", category=None)
    def replace_nested_data_type(
        self,
        parent_data_type: DataType,
        old_data_type: DataType,
        new_data_type: DataType,
    ) -> None:
        """Replace a nested data type in its place in a tuple, last in a union (row ``legacy-replacement-last``).

        The sites of the replacement are overridden.
        """
        self._state_tree_sites("replace_nested_data_type", (new_data_type,))
        if parent_data_type.dict_key is old_data_type:
            cache_owners = self._cache_owners_for_data_type(parent_data_type)
            old_data_type.parent = None
            parent_data_type.dict_key = new_data_type
            new_data_type.parent = parent_data_type
            self._note_changed_data_types((parent_data_type,))
            self._invalidate_owner_caches(*cache_owners)
            self._invalidate_after_mutation()
            return
        if parent_data_type.is_tuple:
            self._builder.set_nested_data_types(
                parent_data_type,
                [new_data_type if member is old_data_type else member for member in parent_data_type.data_types],
            )
            return

        members = (*parent_data_type.data_types, new_data_type)
        plan = assemble_union(
            (
                *(_REPLACED if member is old_data_type else MEMBER for member in members[:-1]),
                _REPLACED if new_data_type is old_data_type else _REPLACEMENT,
            ),
            _REPLACEMENT_LAST,
        )
        self._builder.set_nested_data_types(parent_data_type, (members[index] for index in plan.members))

    @staticmethod
    def _root_collapse_supports_incremental_replacement(
        data_type: DataType,
        new_data_type: DataType,
        *,
        owner: Any,
        replacement_kind: Literal["field", "nested"],
    ) -> bool:
        """Return whether a replacement uses only built-in mutation/traversal hooks."""
        if replacement_kind == "field":
            from datamodel_code_generator.model.base import (  # noqa: PLC0415
                DataModelFieldBase as RuntimeDataModelFieldBase,
            )

            if type(owner).replace_data_type is not RuntimeDataModelFieldBase.replace_data_type:
                return False

        from datamodel_code_generator.types import DataType as RuntimeDataType  # noqa: PLC0415

        return (
            type(data_type).all_data_types.fget is RuntimeDataType.all_data_types.fget
            and type(new_data_type).all_data_types.fget is RuntimeDataType.all_data_types.fget
        )

    @contextmanager
    def _replace_data_type_and_detach_data_type_ref(  # noqa: PLR0912
        self,
        data_type: DataType,
        new_data_type: DataType,
        *,
        owner: Any,
        replacement_kind: Literal["field", "nested"],
    ) -> Generator[None, None, None]:
        """Replace a data type, then detach its old reference after surrounding work.

        The sites of ``new_data_type`` are collapsed from the root model ``data_type`` names.
        """
        self._state_tree_sites("collapse_root_data_type", (new_data_type,), via=data_type.reference)
        scope = self._active_root_collapse_reference_scope
        if (
            scope is not None
            and scope.enabled
            and not self._root_collapse_supports_incremental_replacement(
                data_type,
                new_data_type,
                owner=owner,
                replacement_kind=replacement_kind,
            )
        ):
            scope.invalidate()
        replacement_deltas = self._root_collapse_prepare_data_type_replacement(data_type, new_data_type)
        if scope is not None and scope.enabled:
            has_base_owner_alias = False
        else:
            facts = self._facts if not self._dirty else self.current_facts()
            has_base_owner_alias = id(data_type) in facts.base_owner_model_ids_by_object
        self._root_collapse_internal_mutation_depth += 1
        try:
            match replacement_kind:
                case "field":
                    self._builder.replace_field_type(owner, new_data_type)
                case "nested":  # pragma: no branch
                    if self._inlined_replacement(data_type, owner) == "replace-in-owner":
                        self._replace_in_owner(owner, data_type, new_data_type)
                    else:
                        self._builder.replace_nested_data_type(owner, data_type, new_data_type)
        finally:
            self._root_collapse_internal_mutation_depth -= 1
        try:
            yield
        except BaseException:
            if scope is not None:
                scope.invalidate()
            raise
        if data_type.parent is not None or has_base_owner_alias:
            if scope is not None:
                scope.invalidate()
            self._root_collapse_internal_mutation_depth += 1
            try:
                self._builder.detach_data_type_ref(data_type)
            finally:
                self._root_collapse_internal_mutation_depth -= 1
            return
        if replacement_deltas is not None:
            self._root_collapse_apply_reference_deltas(replacement_deltas)
        self._root_collapse_internal_mutation_depth += 1
        try:
            self._replace_data_type_reference(data_type, None)
            self._note_changed_data_types((data_type,))
            self._invalidate_after_mutation()
        finally:
            self._root_collapse_internal_mutation_depth -= 1

    @deprecated("Use GenerationStore.builder().inline_shared() instead.", category=None)
    def inline_shared(self, root_type: DataType, site: DataType) -> DataType:
        """Return a copy of ``root_type`` for collapse to put at ``site``, sharing the children of ``root_type``.

        The copy holds the same ``data_types`` list (a `ChildList`) and dict key as ``root_type`` and every other copy
        of it, so each child has a holder edge from each of them (`_inlined_holders`). The sites of the copy are
        collapsed from the root model ``site`` names.
        """
        copied = root_type.model_copy()
        if (owners := self._inlined_owners.get(NodeKey(id(root_type)))) is None:
            owners = self._inlined_owners[NodeKey(id(root_type))] = [root_type]
        owners.append(copied)
        self._inlined_owners[NodeKey(id(copied))] = owners
        self._state_tree_sites("inline_shared", (copied,), via=site.reference)
        return copied

    def _inlined_holders(self, node: DataType) -> tuple[HolderEdge, ...]:
        """Return the holder edges of ``node`` from its ``parent`` and the types sharing its children with it."""
        if (owners := self._inlined_owners.get(NodeKey(id(node.parent)))) is None:
            return ()
        return (
            *(
                HolderEdge(NodeKey(id(owner)), "nested", index)
                for owner in owners
                for index, member in enumerate(owner.data_types)
                if member is node
            ),
            *(HolderEdge(NodeKey(id(owner)), "dict_key", None) for owner in owners if owner.dict_key is node),
        )

    def _inlined_replacement(self, data_type: DataType, owner: DataType) -> InlinedReplacement:
        """Return the policy that replaces ``data_type`` under ``owner``, read from the holders `inline_shared` gave."""
        from datamodel_code_generator.types import DataType as RuntimeDataType  # noqa: PLC0415

        if len(self._inlined_holders(data_type)) > 1:
            return "replace-in-owner"
        node: object = owner
        seen: set[int] = set()
        while isinstance(node, RuntimeDataType) and id(node) not in seen:
            if len(self._inlined_holders(node)) > 1:
                return "legacy-shared-node-in-place"
            seen.add(id(node))
            node = node.parent
        return "sole-holder"

    def _replace_in_owner(self, owner: DataType, data_type: DataType, new_data_type: DataType) -> None:
        """Replace a direct member of a shared `ChildList` in ``owner`` alone (row ``replace-in-owner``).

        ``owner`` gets a new list; the other holders keep the old list and ``data_type``, whose reference is detached.
        """
        self._builder.replace_nested_data_type(owner, data_type, new_data_type)

    @deprecated("Use GenerationStore.builder().set_nested_data_types() instead.", category=None)
    def set_nested_data_types(self, data_type: DataType, nested_data_types: Iterable[DataType]) -> None:
        """Replace nested data types and invalidate derived facts; the new sites under ``data_type`` are overridden."""
        nested_data_types = list(nested_data_types)
        self._state_tree_sites("set_nested_data_types", nested_data_types)
        cache_owners = self._cache_owners_for_data_type(data_type)
        for nested_data_type in data_type.data_types:
            nested_data_type.parent = None
        data_type.data_types = nested_data_types
        for nested_data_type in data_type.data_types:
            nested_data_type.parent = data_type
        self._note_changed_data_types((data_type,))
        self._invalidate_owner_caches(*cache_owners)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().append_field() instead.", category=None)
    def append_field(self, model: DataModel, field_: DataModelFieldBase) -> None:
        """Append a field to ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._state_model_sites("append_field", model)
        field_.parent = model
        field_.invalidate_semantic_caches(invalidate_parent=False)
        model.fields.append(field_)
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().insert_field() instead.", category=None)
    def insert_field(self, model: DataModel, index: int, field_: DataModelFieldBase) -> None:
        """Insert a field into ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._state_model_sites("insert_field", model)
        field_.parent = model
        field_.invalidate_semantic_caches(invalidate_parent=False)
        model.fields.insert(index, field_)
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().remove_field() instead.", category=None)
    def remove_field(self, model: DataModel, field_: DataModelFieldBase) -> None:
        """Remove a field from ``model`` and invalidate derived facts."""
        model.fields.remove(field_)
        if field_.parent is model:
            field_.parent = None
            field_.invalidate_semantic_caches(invalidate_parent=False)
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().set_fields() instead.", category=None)
    def set_fields(self, model: DataModel, fields: Iterable[DataModelFieldBase]) -> None:
        """Replace all fields on ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._state_model_sites("set_fields", model)
        old_fields = model.fields
        model.fields = list(fields)
        new_field_ids = {id(field_) for field_ in model.fields}
        for field_ in old_fields:
            if field_.parent is model and id(field_) not in new_field_ids:
                field_.parent = None
                field_.invalidate_semantic_caches(invalidate_parent=False)
        for field_ in model.fields:
            field_.parent = model
            field_.invalidate_semantic_caches(invalidate_parent=False)
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().set_base_classes() instead.", category=None)
    def set_base_classes(self, model: DataModel, base_classes: Iterable[BaseClassDataType]) -> None:
        """Replace ``model`` base classes and invalidate derived facts; the new sites of ``model`` are overridden."""
        self._state_model_sites("set_base_classes", model)
        model.base_classes = list(base_classes)
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().reset_base_classes() instead.", category=None)
    def reset_base_classes(self, model: DataModel) -> None:
        """Reset ``model`` to its default base classes and invalidate derived facts; its new sites are overridden."""
        self._state_model_sites("reset_base_classes", model)
        model.set_base_class()
        self._builder.note_changed_models(model)
        self._invalidate_render_caches_for_model(model)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().redirect_reference_users() instead.", category=None)
    def redirect_reference_users(
        self, old_reference: Reference, new_reference: Reference, *, merged: bool = False
    ) -> list[DataType]:
        """Redirect every user of ``old_reference`` to ``new_reference`` and return the redirected users.

        Their sites are redirected from ``old_reference``. When the model of ``old_reference`` is ``merged`` into the
        equal model of ``new_reference``, each user keeps rejecting null as the schema it names does
        (``_keep_named_null_rejected``).
        """
        children = [child for child in old_reference.children[:] if hasattr(child, "replace_reference")]
        self._state_sites_at("redirect_reference_users", children, via=old_reference)
        cache_owners = [self._cache_owners_for_data_type(child) for child in children]
        if merged:
            self._keep_named_null_rejected(children)
        self._replace_reference_children(old_reference, new_reference)
        self._note_changed_data_types(children)
        self._invalidate_owner_caches_many(cache_owners)
        self._invalidate_after_mutation()
        return children

    def has_users_in_other_modules(self, model: DataModel) -> bool:
        """Return whether a model of another module uses ``model``, by a name that module already wrote."""
        module = model.module_name
        return any(
            hasattr(child, "replace_reference")
            and getattr(self._cache_owners_for_data_type(child)[1], "module_name", module) != module
            for child in model.reference.children
        )

    @deprecated("Use GenerationStore.builder().redirect_model_reference_users() instead.", category=None)
    def redirect_model_reference_users(
        self,
        model: DataModel,
        models: list[DataModel],
        new_reference: Reference,
    ) -> None:
        """Redirect ``model`` reference users owned by ``models`` to ``new_reference``; their sites are redirected.

        ``model`` is merged into the model of ``new_reference``, so each user keeps rejecting null as the schema it
        names does (``_keep_named_null_rejected``).
        """
        model_ids = {id(candidate) for candidate in models}
        cache_owners = []
        for child in model.reference.children[:]:
            if not hasattr(child, "replace_reference"):
                continue
            cache_owners_for_child = self._cache_owners_for_data_type(child)
            owner_models = cache_owners_for_child[1]
            if isinstance(owner_models, tuple):
                matches_owner = any(id(owner_model) in model_ids for owner_model in owner_models)
            else:
                matches_owner = owner_models is not None and id(owner_models) in model_ids
            if not matches_owner:
                continue
            cache_owners.append(cache_owners_for_child)
            self._state_sites_at("redirect_model_reference_users", (child,), via=model.reference)
            self._keep_named_null_rejected((child,))  # ty: ignore[invalid-argument-type]
            child.replace_reference(new_reference)  # ty: ignore[call-non-callable]
            self._note_changed_data_types((child,))
        self._invalidate_owner_caches_many(cache_owners)
        self._invalidate_after_mutation()

    @deprecated("Use GenerationStore.builder().collapse_root_data_type() instead.", category=None)
    def collapse_root_data_type(self, data_type: DataType, inner_reference: Reference) -> None:
        """Replace a root-model data type with its inner reference; its site is collapsed from the root model."""
        self._state_sites_at("collapse_root_data_type", (data_type,), via=data_type.reference)
        self._root_collapse_internal_mutation_depth += 1
        try:
            with self._builder.defer_refresh():
                if data_type.reference:
                    self._prune_reference_children(data_type.reference, excluded_child=data_type, require_parent=True)
                self._builder.replace_data_type_ref(data_type, inner_reference)
        finally:
            self._root_collapse_internal_mutation_depth -= 1

    @deprecated("Use GenerationStore.builder().detach_model_data_type_refs() instead.", category=None)
    def detach_model_data_type_refs(self, model: DataModel) -> None:
        """Detach every referenced data type currently owned by ``model``."""
        with self._builder.defer_refresh():
            for data_type in model.all_data_types:
                if data_type.reference:
                    self._builder.detach_data_type_ref(data_type)

    @deprecated("Use GenerationStore.builder().defer_refresh() instead.", category=None)
    @contextmanager
    def defer_refresh(self) -> Generator[None, None, None]:
        """Batch mutation invalidations and rebuild derived facts once on exit."""
        self._defer_refresh_depth += 1
        completed = False
        try:
            yield
            completed = True
        finally:
            try:
                if (
                    completed
                    and self._defer_refresh_depth == 1
                    and not ((scope := self._active_root_collapse_reference_scope) is not None and scope.enabled)
                ):
                    self.refresh_now()
            finally:
                self._defer_refresh_depth -= 1

    @staticmethod
    def _replace_data_type_reference(data_type: DataType, new_reference: Reference | None) -> None:
        if data_type.reference:
            data_type.replace_reference(new_reference)
        else:
            data_type.reference = new_reference
            if new_reference:
                new_reference.children.append(data_type)

    @staticmethod
    def _keep_named_null_rejected(data_types: Iterable[DataType]) -> None:
        """Mark the types naming a model that rejects null, before merging it points them at an equal model.

        A reference admits None from the schema it names, not from the model it names after the merge
        (``DataType.names_nullable_model``); the first merge marks it, as the type named that schema.
        """
        for data_type in data_types:
            if not data_type.names_nullable_model:
                data_type.__dict__[NAMED_NULL_REJECTED_KEY] = True

    @staticmethod
    def _replace_reference_children(old_reference: Reference, new_reference: Reference) -> None:
        old_reference.replace_children_references(new_reference)

    @staticmethod
    def _prune_reference_children(
        reference: Reference,
        *,
        excluded_child: object | None = None,
        require_parent: bool = False,
    ) -> None:
        reference.children = [
            child
            for child in reference.children
            if child is not excluded_child and (not require_parent or getattr(child, "parent", None))
        ]

    def _invalidate_after_mutation(self) -> None:
        self._invalidate()

    def _state_model_sites(self, operation: str, *models: object) -> None:
        """State that the new sites of ``models`` take the provenance of ``operation`` (`SITE_EFFECTS`)."""
        provenance = Provenance(SITE_EFFECTS[operation])
        for model in models:
            if (model_id := self._model_ids_by_object.get(id(model))) is not None:
                self._site_effects.by_model.setdefault(model_id, provenance)

    def _state_sites_at(self, operation: str, nodes: Iterable[object], *, via: Reference | None = None) -> None:
        """State that the new sites at ``nodes`` take the effect of ``operation``.

        ``via`` is the model the operation takes the sites from: the one they named, or the root model collapsed.
        """
        provenance = Provenance(SITE_EFFECTS[operation], via=None if via is None else model_key_of(via))
        by_node = self._site_effects.by_node
        for node in nodes:
            by_node.setdefault(NodeKey(id(node)), (node, provenance))

    def _state_tree_sites(self, operation: str, roots: Iterable[DataType], *, via: Reference | None = None) -> None:
        """State that the new sites in the trees of ``roots`` take the effect of ``operation`` (`_state_sites_at`)."""
        nodes: list[DataType] = []
        stack = list(roots)
        while stack:
            nodes.append(node := stack.pop())
            stack.extend(node.data_types)
            if dict_key := node.dict_key:
                stack.append(dict_key)
        self._state_sites_at(operation, nodes, via=via)

    def _refresh_after_write(self, refresh: Refresh) -> None:
        """Mark the facts stale after a write, unless the write's ``refresh`` is ``deferred``."""
        if refresh == "now":
            self._invalidate_after_mutation()

    def _cache_owners_for_data_type(self, data_type: object) -> CacheOwners:
        current: Any = data_type
        owner_field: DataModelFieldBase | None = None
        while (parent := getattr(current, "parent", None)) is not None:
            current = parent
            if owner_field is None and hasattr(current, "invalidate_semantic_caches"):
                owner_field = current
        owner_model = current if hasattr(current, "invalidate_render_caches") else None
        if owner_model is not None:
            return owner_field, owner_model
        if not self.models:
            return owner_field, None
        if self._dirty and self._defer_refresh_depth:
            return owner_field, tuple(
                model for model in self.models if any(base_class is current for base_class in model.base_classes)
            )
        facts = self.current_facts()
        match facts.base_owner_model_ids_by_object.get(id(current)):
            case None:
                owner_models = None
            case int() as model_id:
                owner_models = facts.model_facts[model_id].model
            case model_ids:
                owner_models = tuple(facts.model_facts[model_id].model for model_id in model_ids)
        return owner_field, owner_models

    def _invalidate_owner_caches(
        self,
        field_: DataModelFieldBase | None,
        owner_models: OwnerModels,
    ) -> None:
        if field_ is not None:
            field_.invalidate_semantic_caches(invalidate_parent=False)
        if isinstance(owner_models, tuple):
            for model in owner_models:
                self._invalidate_render_caches_for_model(model)
            return
        self._invalidate_render_caches_for_model(owner_models)

    def _invalidate_owner_caches_many(
        self,
        cache_owners: Iterable[CacheOwners],
    ) -> None:
        fields: dict[int, DataModelFieldBase] = {}
        models: dict[int, DataModel] = {}
        for field_, owner_models in cache_owners:
            if field_ is not None:
                fields[id(field_)] = field_
            if isinstance(owner_models, tuple):
                for model in owner_models:
                    models[id(model)] = model
            elif owner_models is not None:
                models[id(owner_models)] = owner_models
        for field_ in fields.values():
            field_.invalidate_semantic_caches(invalidate_parent=False)
        for model in models.values():
            self._invalidate_render_caches_for_model(model)

    @staticmethod
    def _invalidate_render_caches_for_model(model: DataModel | None) -> None:
        if model is None:
            return
        model.invalidate_render_caches()

    def __iter__(self) -> Iterator[DataModel]:
        return iter(self.models)


@final
class _BuilderToken:
    """What only ``GenerationStore`` holds, so that only ``GenerationStore.builder`` makes a ``GraphBuilder``."""

    __slots__ = ()


@final  # noqa: PLR0904
class GraphBuilder:
    """The capability to change the generation graph: one method per mutation of ``GENERATION_STORE_MUTATION_METHODS``.

    ``GenerationStore.builder()`` returns the store's one builder. Each method calls the store's operation of the same
    name, which subclasses may override and which the debug checks wrap on the store, and which is deprecated for every
    other caller. Decisions read ``FinalFacts`` instead.
    """

    __slots__ = ("_store",)

    def __init__(self, store: GenerationStore, token: _BuilderToken) -> None:  # noqa: ARG002
        """Hold ``store``; ``token`` is the store's own."""
        self._store = store

    def add_model_import(self, model: DataModel, import_: Import) -> None:
        """Add an import to a model once."""
        self._store.add_model_import(model, import_)  # ty: ignore[deprecated]

    def set_type_expressions(  # noqa: PLR0913
        self,
        data_type: DataType,
        *,
        type_: str | None = None,
        import_: Import | None = None,
        python_type: Any = None,
        kwargs: dict[str, Any] | None = None,
        runtime_expression_imports: tuple[Import, ...] | None = None,
    ) -> None:
        """Write what a data type renders by through ``GenerationStore.set_type_expressions``."""
        self._store.set_type_expressions(  # ty: ignore[deprecated]
            data_type,
            type_=type_,
            import_=import_,
            python_type=python_type,
            kwargs=kwargs,
            runtime_expression_imports=runtime_expression_imports,
        )

    def append_field(self, model: DataModel, field_: DataModelFieldBase) -> None:
        """Append a field to ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._store.append_field(model, field_)  # ty: ignore[deprecated]

    def apply_const(self, field_: DataModelFieldBase, *, refresh: Refresh = "now") -> None:
        """Run the field's ``process_const`` hook, which may type its const as a Literal and write its default."""
        self._store.apply_const(field_, refresh=refresh)  # ty: ignore[deprecated]

    def apply_literal_default(self, field_: DataModelFieldBase, decision: LiteralDefault) -> None:
        """Write what ``decide_literal_default`` decides for a field, through the store."""
        self._store.apply_literal_default(field_, decision)  # ty: ignore[deprecated]

    def clear_target_null(self, data_type: DataType) -> None:
        """Forget that the None of a type is its target's, after a null of another source joined it."""
        self._store.clear_target_null(data_type)  # ty: ignore[deprecated]

    def collapse_root_data_type(self, data_type: DataType, inner_reference: Reference) -> None:
        """Replace a root-model data type with its inner reference; its site is collapsed from the root model."""
        self._store.collapse_root_data_type(data_type, inner_reference)  # ty: ignore[deprecated]

    def defer_refresh(self) -> AbstractContextManager[None]:
        """Batch mutation invalidations and rebuild derived facts once on exit."""
        return self._store.defer_refresh()  # ty: ignore[deprecated]

    def detach_data_type_ref(self, data_type: DataType) -> None:
        """Remove ``data_type`` from its reference and invalidate derived facts."""
        self._store.detach_data_type_ref(data_type)  # ty: ignore[deprecated]

    def detach_model_data_type_refs(self, model: DataModel) -> None:
        """Detach every referenced data type currently owned by ``model``."""
        self._store.detach_model_data_type_refs(model)  # ty: ignore[deprecated]

    def discard_derived_facts(self) -> None:
        """Release cached dependency facts while preserving stable model identities."""
        self._store.discard_derived_facts()  # ty: ignore[deprecated]

    def drop_field_discriminator(self, field_: DataModelFieldBase) -> None:
        """Drop a field's discriminator from its extras and from its type, which every holder of the type sees."""
        self._store.drop_field_discriminator(field_)  # ty: ignore[deprecated]

    def drop_held_null(self, data_type: DataType) -> None:
        """Drop the None a reference type restates of the model it names, which holds null itself."""
        self._store.drop_held_null(data_type)  # ty: ignore[deprecated]

    def drop_union_member_constraints(self, field_: DataModelFieldBase, *, refresh: Refresh = "now") -> None:
        """Run the field's ``unconstrain_union_members`` hook, which writes members into the union lists it holds."""
        self._store.drop_union_member_constraints(field_, refresh=refresh)  # ty: ignore[deprecated]

    def inline_shared(self, root_type: DataType, site: DataType) -> DataType:
        """Return a copy of ``root_type`` for collapse to put at ``site``, sharing the children of ``root_type``."""
        return self._store.inline_shared(root_type, site)  # ty: ignore[deprecated]

    def insert_field(self, model: DataModel, index: int, field_: DataModelFieldBase) -> None:
        """Insert a field into ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._store.insert_field(model, index, field_)  # ty: ignore[deprecated]

    def force_field_assignment(self, field_: DataModelFieldBase) -> None:
        """Render an explicit required-field assignment for a field through the store."""
        self._store.force_field_assignment(field_)  # ty: ignore[deprecated]

    def mark_forward_references(self, model: DataModel) -> None:
        """Make a model write its fields for quoted forward references through the store."""
        self._store.mark_forward_references(model)  # ty: ignore[deprecated]

    def mark_target_null(self, data_type: DataType) -> None:
        """Mark the None of a new reference type as the one the schema it references admits."""
        self._store.mark_target_null(data_type)  # ty: ignore[deprecated]

    def move_model(self, model: DataModel, *, new_path: str, new_file_path: Path | None = None) -> None:
        """Update a model's reference path and optional output file path."""
        self._store.move_model(model, new_path=new_path, new_file_path=new_file_path)  # ty: ignore[deprecated]

    def note_changed_models(self, *models: object) -> None:
        """Record ``models`` again at the next refresh, after their types changed outside the store."""
        self._store.note_changed_models(*models)  # ty: ignore[deprecated]

    def drop_union_operator(self, data_type: DataType) -> None:
        """Join the members of a data type with ``Union`` instead of ``|``, which every holder of the type sees."""
        self._store.drop_union_operator(data_type)  # ty: ignore[deprecated]

    def use_typing_generics(self, data_type: DataType) -> None:
        """Write the containers of a data type with the ``typing`` generics, which every holder of the type sees."""
        self._store.use_typing_generics(data_type)  # ty: ignore[deprecated]

    def override_type_import(self, data_type: DataType, import_: Import) -> None:
        """Make a data type name ``import_``, which every holder of the type sees."""
        self._store.override_type_import(data_type, import_)  # ty: ignore[deprecated]

    def name_reference(self, data_type: DataType, alias: str) -> None:
        """Write the expression a reference type names its model by through ``GenerationStore.name_reference``."""
        self._store.name_reference(data_type, alias)  # ty: ignore[deprecated]

    def record_dropped_multiple_of(self, data_type: DataType, value: float) -> None:
        """Keep on a new constrained type the ``multipleOf`` it is written without, as the backend refuses it."""
        self._store.record_dropped_multiple_of(data_type, value)  # ty: ignore[deprecated]

    def record_values_as_written(self, owner: DataModel | DataType, values: Sequence[Any]) -> None:
        """Keep on a new enum model or Literal type the values as written of the enum or const it holds some of."""
        self._store.record_values_as_written(owner, values)  # ty: ignore[deprecated]

    def redirect_model_reference_users(
        self, model: DataModel, models: list[DataModel], new_reference: Reference
    ) -> None:
        """Redirect ``model`` reference users owned by ``models`` to ``new_reference``; their sites are redirected."""
        self._store.redirect_model_reference_users(model, models, new_reference)  # ty: ignore[deprecated]

    def redirect_reference_users(
        self, old_reference: Reference, new_reference: Reference, *, merged: bool = False
    ) -> list[DataType]:
        """Redirect every user of ``old_reference`` to ``new_reference`` and return the redirected users.

        ``merged`` says the model of ``old_reference`` is merged into the equal model of ``new_reference``.
        """
        return self._store.redirect_reference_users(old_reference, new_reference, merged=merged)  # ty: ignore[deprecated]

    def register_model(self, model: DataModel) -> None:
        """Register a model the output writes."""
        self._store.register_model(model)  # ty: ignore[deprecated]

    def remove_field(self, model: DataModel, field_: DataModelFieldBase) -> None:
        """Remove a field from ``model`` and invalidate derived facts."""
        self._store.remove_field(model, field_)  # ty: ignore[deprecated]

    def rename_field(self, field_: DataModelFieldBase, name: str, *, alias: str | None = None) -> None:
        """Rename a field through ``GenerationStore.rename_field``."""
        self._store.rename_field(field_, name, alias=alias)  # ty: ignore[deprecated]

    def rename_model(  # noqa: PLR0913
        self,
        model: DataModel,
        *,
        class_name: str | None = None,
        reference_name: str | None = None,
        clear_duplicate_name: bool = False,
        new_path: str | None = None,
        new_file_path: Path | None = None,
    ) -> None:
        """Change a model's class or reference name after parsing, the writer of names after the L2 settle."""
        self._store.rename_model(  # ty: ignore[deprecated]
            model,
            class_name=class_name,
            reference_name=reference_name,
            clear_duplicate_name=clear_duplicate_name,
            new_path=new_path,
            new_file_path=new_file_path,
        )

    def replace_data_type_ref(self, data_type: DataType, new_reference: Reference | None) -> None:
        """Set ``data_type.reference`` while preserving reverse reference links; its new site is redirected."""
        self._store.replace_data_type_ref(data_type, new_reference)  # ty: ignore[deprecated]

    def replace_field_type(
        self, field_: DataModelFieldBase, new_data_type: DataType, *, refresh: Refresh = "now"
    ) -> None:
        """Replace a field's data type and invalidate derived facts; the sites of the new type are overridden."""
        self._store.replace_field_type(field_, new_data_type, refresh=refresh)  # ty: ignore[deprecated]

    def replace_model(self, model: DataModel, replacement: DataModel) -> None:
        """Record that ``replacement``, built after parsing under the same model key, takes the place of ``model``."""
        self._store.replace_model(model, replacement)  # ty: ignore[deprecated]

    def replace_nested_data_type(
        self, parent_data_type: DataType, old_data_type: DataType, new_data_type: DataType
    ) -> None:
        """Replace a nested data type in its place in a tuple, last in a union (row ``legacy-replacement-last``)."""
        self._store.replace_nested_data_type(parent_data_type, old_data_type, new_data_type)  # ty: ignore[deprecated]

    def require_field(self, field_: DataModelFieldBase, *, use_default_with_required: bool) -> None:
        """Make a field required, and write its default beside it when ``use_default_with_required``."""
        self._store.require_field(field_, use_default_with_required=use_default_with_required)  # ty: ignore[deprecated]

    def reset_base_classes(self, model: DataModel) -> None:
        """Reset ``model`` to its default base classes and invalidate derived facts; its new sites are overridden."""
        self._store.reset_base_classes(model)  # ty: ignore[deprecated]

    def restore_required_inherited_field(self, model: DataModel, field_: DataModelFieldBase) -> bool:
        """Give a required inherited field back to the constructor through the store; return whether it was."""
        return self._store.restore_required_inherited_field(model, field_)  # ty: ignore[deprecated]

    def retire_model(self, model: DataModel) -> None:
        """Record that ``model`` leaves the output; the parsed universe keeps it, the emitted one drops it."""
        self._store.retire_model(model)  # ty: ignore[deprecated]

    def set_base_classes(self, model: DataModel, base_classes: Iterable[BaseClassDataType]) -> None:
        """Replace ``model`` base classes and invalidate derived facts; the new sites of ``model`` are overridden."""
        self._store.set_base_classes(model, base_classes)  # ty: ignore[deprecated]

    def set_field_constraints(
        self, field_: DataModelFieldBase, constraints: Any, *, type_dependent: Mapping[str, Any] | None = None
    ) -> None:
        """Write a field's constraints and, when given, the constraints that its type decides."""
        self._store.set_field_constraints(field_, constraints, type_dependent=type_dependent)  # ty: ignore[deprecated]

    def set_field_default(self, field_: DataModelFieldBase, default: Any) -> None:
        """Write a field's default, which the facts do not record, so they stay current."""
        self._store.set_field_default(field_, default)  # ty: ignore[deprecated]

    def set_field_extra(self, field_: DataModelFieldBase, key: str, value: Any) -> None:
        """Write one entry into a field's extras dict, which every holder of the dict sees."""
        self._store.set_field_extra(field_, key, value)  # ty: ignore[deprecated]

    def set_field_extras(self, field_: DataModelFieldBase, extras: dict[str, Any]) -> None:
        """Give a field a new extras dict."""
        self._store.set_field_extras(field_, extras)  # ty: ignore[deprecated]

    def set_fields(self, model: DataModel, fields: Iterable[DataModelFieldBase]) -> None:
        """Replace all fields on ``model`` and invalidate derived facts; the new sites of ``model`` are copied."""
        self._store.set_fields(model, fields)  # ty: ignore[deprecated]

    def set_nested_data_types(self, data_type: DataType, nested_data_types: Iterable[DataType]) -> None:
        """Replace nested data types and invalidate derived facts; the new sites under ``data_type`` are overridden."""
        self._store.set_nested_data_types(data_type, nested_data_types)  # ty: ignore[deprecated]

    def set_type_discriminator(self, data_type: DataType, discriminator: str | None) -> None:
        """Write the discriminator of a data type, or none, which every holder of the type sees."""
        self._store.set_type_discriminator(data_type, discriminator)  # ty: ignore[deprecated]

    def swap_data_type(self, data_type: DataType, new_data_type: DataType, *, refresh: Refresh = "now") -> None:
        """Put ``new_data_type`` where ``data_type`` is in its parent: a field's type or a member of a data type."""
        self._store.swap_data_type(data_type, new_data_type, refresh=refresh)  # ty: ignore[deprecated]

    def update_model_reference(
        self, model: DataModel, *, new_path: str | None = None, new_file_path: Path | None = None
    ) -> None:
        """Update a model's reference path or output file and invalidate facts; ``rename_model`` changes names."""
        self._store.update_model_reference(model, new_path=new_path, new_file_path=new_file_path)  # ty: ignore[deprecated]

    def use_assignment_for_forward_references(
        self, model: DataModel, positions: Mapping[str, int], index: int, *, refresh: Refresh = "now"
    ) -> bool:
        """Run the model's ``assign_forward_reference_fields`` hook; return whether it changed the model."""
        return self._store.use_assignment_for_forward_references(model, positions, index, refresh=refresh)  # ty: ignore[deprecated]

    def write_rendered_null(self, data_type: DataType) -> None:
        """Write into ``is_optional`` the None that the hint of ``data_type`` renders."""
        self._store.write_rendered_null(data_type)  # ty: ignore[deprecated]
