"""``MODEL_GRAPH``: the invariants of the model graph that the generation store records.

Collapse stops before it would build a data type cycle; the store never meets a copied Reference; every model parsed
from a JSON document has an owner or is a root at the end of L1; a refresh that records only the models store
operations noted records the facts and reference sites a full rebuild would; the ``parent`` of every node a refresh
records, when set, is one of its holders, and no node contains itself; the models the output writes are the store's
emitted models.
"""

from __future__ import annotations

from functools import wraps
from itertools import chain
from types import MappingProxyType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias

from tests.checks.areas import Area, Mode, mode

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

ParentMismatch: TypeAlias = Literal["recorded field", "dropped field", "data type"]
"""What the ``parent`` of a node is when it does not hold the node: a field of a recorded model, another field, or a
data type."""

KNOWN_PARENT_MISMATCHES: Final[Mapping[ParentMismatch, str]] = MappingProxyType({
    "dropped field": "JsonSchemaParser._build_field_array returns the data type of the field that "
    "parse_array_fields builds for an array item and drops the field, which stays the parent of that data type",
})
"""The mismatches the check leaves alone, with the code that makes them; entries are only removed."""

_COPIES: Final = "reference copies"
"""The key of the Reference objects each store met first under each model key, in the state of the parse."""


def _violation(graph_mode: Mode, message: str, **details: object) -> None:
    from datamodel_code_generator import Error

    graph_mode.violation(Error, message, **details)


def _is_root_or_owned(reference: Any) -> bool:
    """Return whether the model of ``reference`` was recorded and has an owner or is a root model.

    A root model is recorded with no builder frame open but its own builders'. A model recorded inside a builder that
    built no model, or never recorded, breaks the rule.
    """
    from datamodel_code_generator.parser._graph_ids import owner_of
    from datamodel_code_generator.reference import SCHEMA_OWNER_FRAMES_KEY

    if owner_of(reference) is not None:
        return True
    record = reference.__dict__.get(SCHEMA_OWNER_FRAMES_KEY)
    return record is not None and all(owner is not None for (owner,) in record.frames)


def check_model_owners(parser: Any) -> None:
    """Report the models parsed from a JSON document that have no owner and are not roots, at the end of L1."""
    if parser._schema_graph is None or (graph_mode := mode(Area.MODEL_GRAPH)) is None:
        return
    if unowned := [model for model in parser.results if not _is_root_or_owned(model.reference)]:
        from datamodel_code_generator._url_redaction import redact_reference

        paths = [(model.class_name, redact_reference(model.reference.path)) for model in unowned]
        names = ", ".join(f"{name} ({path})" for name, path in paths)
        _violation(
            graph_mode,
            f"models without an owner that are not roots: {names}",
            models=[{"class_name": name, "path": path} for name, path in paths],
        )


def check_emitted_models(store: Any, written: Iterable[Any]) -> None:
    """Report each model the output writes that is not in the emitted universe, and each one there it does not."""
    if (graph_mode := mode(Area.MODEL_GRAPH)) is None:
        return
    from datamodel_code_generator._url_redaction import redact_reference

    written_models = {id(model): model for model in written}
    emitted = {id(model): model for model in store.emitted_models()}
    for models, others, problem in (
        (written_models, emitted, "is written but is not an emitted model: it was never registered or was retired"),
        (emitted, written_models, "is an emitted model but is not written: it left the output without being retired"),
    ):
        for model_id, model in models.items():
            if model_id not in others:
                path = redact_reference(model.reference.path)
                _violation(graph_mode, f"the model {model.class_name} ({path}) {problem}", path=path)


def model_key(original: Callable[[Any, Any], Any]) -> Callable[[Any, Any], Any]:
    """Wrap ``GenerationStore.model_key``: a Reference that shares its key with one the store met first is reported.

    Such a copy would make the store take both for one model. The store meets References from its first refresh in a
    parse on; each key is reported once.
    """

    @wraps(original)
    def model_key(self: Any, reference: Any) -> Any:
        key = original(self, reference)
        if (graph_mode := mode(Area.MODEL_GRAPH)) is None or (
            copies := graph_mode.state.get((_COPIES, id(self)))
        ) is None:
            return key
        originals, reported = copies
        if originals.setdefault(key, reference) is not reference and key not in reported:
            from datamodel_code_generator._url_redaction import redact_reference
            from datamodel_code_generator.reference import location_of

            reported.add(key)
            location = location_of(reference)
            path = redact_reference(reference.path)
            _violation(
                graph_mode,
                f"a copy of the Reference of {reference.name} ({path}) reached the generation store beside the "
                "original, whose model key it shares",
                name=reference.name,
                path=path,
                location=None if location is None else redact_reference(str(location)),
            )
        return key

    return model_key


def refresh_now(original: Callable[[Any], None]) -> Callable[[Any], None]:
    """Wrap ``GenerationStore.refresh_now``: check the models a refresh records, before and after it records them.

    Before, no data type of any model may contain itself, since neither the refresh nor the rebuild below would end on
    a cycle. After, an update must record what a full rebuild of the same models would, and every node of the models
    it recorded must have one of its holders, or no node, as its ``parent``.
    """

    @wraps(original)
    def refresh_now(self: Any) -> None:
        if (graph_mode := mode(Area.MODEL_GRAPH)) is None or not self._dirty:
            return original(self)
        graph_mode.state.setdefault((_COPIES, id(self)), ({}, set()))
        recorded_model_ids = _check_no_data_type_cycles(graph_mode, self)
        original(self)
        if recorded_model_ids is not None:
            _check_update_matches_rebuild(graph_mode, self)
        _check_parents_are_holders(
            graph_mode, self, self._facts.model_facts if recorded_model_ids is None else recorded_model_ids
        )
        return None

    return refresh_now


def collapse_module_root_models(original: Callable[[Any, Any], None]) -> Callable[[Any, Any], None]:
    """Wrap ``collapse_module_root_models``: a collapse that reached the recursion limit is reported.

    Collapse should stop at a cycle instead; the error it raises for a retry stays the error when the check records.
    """

    @wraps(original)
    def collapse_module_root_models(parser: Any, state: Any) -> None:
        from datamodel_code_generator._shared_types import _CollapseRootModelsRecursionError

        try:
            original(parser, state)
        except _CollapseRootModelsRecursionError as error:
            if (graph_mode := mode(Area.MODEL_GRAPH)) is not None and isinstance(error.__cause__, RecursionError):
                _violation(
                    graph_mode, "collapsing root models reached the recursion limit instead of stopping at a cycle"
                )
            raise

    return collapse_module_root_models


def _fact_record(facts: Any) -> dict[str, object]:
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


def _check_update_matches_rebuild(graph_mode: Mode, store: Any) -> None:
    """Report facts that an update recorded differently from a full rebuild of the same models.

    An update records the models that operations noted since the last refresh; a model that changed without a note
    keeps its old facts, which a rebuild would not.
    """
    from datamodel_code_generator.parser.generation import GenerationIndexBuilder

    rebuilt = GenerationIndexBuilder(store.model_key).build(
        store.models,
        previous_model_ids_by_object=store._model_ids_by_object,
        next_model_id=store._next_model_id,
    )
    updated = _fact_record(store._facts)
    for part, recorded in _fact_record(rebuilt.facts).items():
        if updated[part] != recorded:
            _violation(
                graph_mode,
                f"the {part} that a refresh recorded for the noted models differ from a full rebuild",
                part=part,
            )


def _check_no_data_type_cycles(graph_mode: Mode, store: Any) -> tuple[int, ...] | None:
    """Report a data type that contains itself in any model, noted or not.

    Return the ids the models the refresh records will have, or None when it records every model.
    """
    recorded_model_ids: tuple[int, ...] | None = None
    if not store._rebuild_required:
        recorded_model_ids = (
            *range(store._next_model_id, store._next_model_id + len(store._appended_model_ids)),
            *store._changed_model_ids,
        )
    finished: set[int] = set()
    for model in store.models:
        roots = chain((field_.data_type for field_ in model.fields), model.base_classes)
        if (node := _node_in_cycle(roots, finished)) is not None:
            _violation(
                graph_mode,
                f"a data type of {model.class_name} contains itself",
                model=model.class_name,
                type=type(node).__name__,
            )
    return recorded_model_ids


def _check_parents_are_holders(graph_mode: Mode, store: Any, model_ids: Iterable[int]) -> None:
    """Report a node of the models with ``model_ids`` whose ``parent`` is not one of its recorded holders.

    A mismatch that `KNOWN_PARENT_MISMATCHES` lists is left alone.
    """
    from datamodel_code_generator._model_keys import NodeKey
    from datamodel_code_generator.parser.generation import _HolderIndex

    facts = store._facts
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
            if _parent_is_holder(parent, holders.holders_of(NodeKey(node_key))):
                continue
            if (mismatch := _parent_mismatch(store, parent)) in KNOWN_PARENT_MISMATCHES:
                continue
            _violation(
                graph_mode,
                f"a {fact.role} data type of {model_fact.class_name} has a {mismatch} as its parent, which does not "
                "hold it",
                model=model_fact.class_name,
                role=fact.role,
                parent=mismatch,
            )


def _parent_mismatch(store: Any, parent: object) -> ParentMismatch:
    """Return what kind of object ``parent`` is, for a data type that ``parent`` does not hold."""
    from datamodel_code_generator.model.base import DataModelFieldBase

    if not isinstance(parent, DataModelFieldBase):
        return "data type"
    if (model := parent.parent) is not None and store._tracked_model_id(model) is not None:
        return "recorded field"
    return "dropped field"


def _parent_is_holder(parent: object, edges: Iterable[Any]) -> bool:
    """Return whether ``parent`` is unset or one of the field and node holders that ``edges`` name.

    Only nodes with a reference or nested types get a ``parent`` when they are built, and a model is never one.
    """
    if parent is None:
        return True
    parent_key = id(parent)
    return any(edge.holder == parent_key and edge.role != "base" for edge in edges)


def _node_in_cycle(roots: Iterable[Any], finished: set[int]) -> Any:
    """Return a node that contains itself among the trees of ``roots``, or None.

    ``finished`` holds the nodes already walked without a cycle; the walk adds the nodes it finishes, so the trees of
    several calls share it.
    """
    active: set[int] = set()
    for root in roots:
        active.add(id(root))
        stack = [(root, _children(root))]
        while stack:
            node, children = stack[-1]
            if (child := next(children, None)) is None:
                stack.pop()
                active.discard(id(node))
                finished.add(id(node))
                continue
            if id(child) in active:
                return child
            if id(child) not in finished:
                active.add(id(child))
                stack.append((child, _children(child)))
    return None


def _children(data_type: Any) -> Iterator[Any]:
    yield from data_type.data_types
    if dict_key := data_type.dict_key:
        yield dict_key
