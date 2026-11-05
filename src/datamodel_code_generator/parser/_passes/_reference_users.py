"""Type the users of a reference by another type, in every place a type names it, and find the loops of type aliases.

A ``$ref`` that an external ref mapping or a base stand-in types by an import (L1), a root model that deduplication
dropped because its root names it (L5) and a loop of type aliases that only name each other (``alias_loops``) leave
users that name a class no module writes or a name that never resolves. Each place takes the type of its own through
the generation store: a field or a member of another type by swapping it in, a dict key in its place in the dict, and
the typed additional properties of a model by writing them again. A base keeps naming the reference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from typing_extensions import Protocol

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, reference_graph
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.python_literal import _make_referencing_type_expression

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.model import DataModel
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "ReferenceUsersHost",
    "alias_loops",
    "type_additional_properties_users",
    "type_reference_users",
]


class ReferenceUsersHost(Protocol):
    """The generation store, the output model capabilities and the target of the parser that retype users."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse."""

    @property
    def _output_model_context(self) -> OutputModelContext:
        """The capabilities of the output model types."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""


def type_reference_users(
    host: ReferenceUsersHost,
    reference: Reference,
    new_type: Callable[[], DataType],
    *,
    kept_owners: Collection[int] = (),
) -> None:
    """Type each user of ``reference`` in a field, a type or a dict key by its own ``new_type()``.

    Bases keep it, and so do the types of the models whose ids ``kept_owners`` holds.
    """
    builder = host._graph_builder  # noqa: SLF001
    index = host.generation_store.index
    for fact in index.data_type_facts_for_reference(reference):
        if fact.role == "base" or id(index.owner_of(fact)) in kept_owners:
            continue
        builder.detach_data_type_ref(fact.data_type)
        if fact.role == "dict_key":
            builder.replace_nested_data_type(cast("DataType", fact.data_type.parent), fact.data_type, new_type())
        else:
            builder.swap_data_type(fact.data_type, new_type())


def type_additional_properties_users(
    host: ReferenceUsersHost,
    model: DataModel,
    reference: Reference,
    new_type: Callable[[], DataType],
    reference_classes: set[str],
) -> None:
    """Type the users of ``reference`` in the model's typed additional properties by their own ``new_type()``.

    The other classes they reference stay ``reference_classes``; ``extra_items`` is written again.
    """
    context = host._output_model_context  # noqa: SLF001
    source = cast("DataType", context.additional_properties_type_source(model.extra_template_data))
    for data_type in [data_type for data_type in source.all_data_types if data_type.reference is reference]:
        host._graph_builder.detach_data_type_ref(data_type)  # noqa: SLF001
        if data_type is source:
            source = new_type()
        else:
            host._graph_builder.swap_data_type(data_type, new_type())  # noqa: SLF001
    context.store_additional_properties_type(
        model.extra_template_data,
        _make_referencing_type_expression(source),
        reference_classes,
        imports=tuple(source.all_imports),
        target_python_version=host.target_python_version,
    )
    model._name_final_classes()  # noqa: SLF001


def _named_alias(model: DataModel) -> TypeAliasBase | None:
    """Return the type alias that a built-in type alias writes as its whole value, by nothing but its name."""
    if (target := _referenced_alias(model)) is None or model._uses_custom_root_template:  # noqa: SLF001
        return None
    field = model.fields[0]
    data_type = field.data_type
    if (
        field.annotated
        or field.field
        or field.type_hint.strip("\"'")
        != (data_type.alias or cast("Reference", data_type.reference).short_name).strip("\"'")
    ):
        return None
    return target


def _referenced_alias(model: DataModel) -> TypeAliasBase | None:
    """Return the type alias that the one field of a type alias names by its type, written as it may be."""
    if (
        not isinstance(model, TypeAliasBase)
        or len(model.fields) != 1
        or (reference := model.fields[0].data_type.reference) is None
        or not isinstance(target := reference.source, TypeAliasBase)
    ):
        return None
    return target


def alias_loops(models: Sequence[DataModel], self_named: Sequence[DataModel]) -> dict[int, tuple[DataModel, ...]]:
    """Map each model of a ``$ref`` chain that loops back without reaching a schema to the loop, starting at it.

    The loops are those of the alias graph (`EdgeKind.ALIAS`): type aliases that only name the next alias of a cycle
    (``_named_alias``), and the root models of ``self_named``, whose root names the model itself. A type alias names at
    most one other by its type, so the cycles are those of the aliases whose type names one (``_referenced_alias``),
    and only their members are read as they are written: reading every alias so would read the constraints of each
    chain of aliases, which recurses as deep as the chain.
    """
    snapshot = GraphSnapshot.ALIAS_LOOPS
    keyed = keyed_models((*models, *self_named), snapshot.value)
    named = {model_key_of(model.reference) for model in self_named}
    edges = {
        model_key_of(model.reference): (model_key_of(target.reference),)
        for model in models
        if (target := _referenced_alias(model)) is not None
    }
    edges.update((key, (key,)) for key in named)
    loops = reference_graph(snapshot, keyed.models, {EdgeKind.ALIAS: edges}).loops(EdgeKind.ALIAS)
    named.update(key for key in {key for loop in loops.values() for key in loop} if _named_alias(keyed.models[key]))
    return {
        id(keyed.models[key]): tuple(map(keyed.models.__getitem__, loop))
        for key, loop in loops.items()
        if named.issuperset(loop)
    }
