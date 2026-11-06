"""Type the users of a reference by another type, in every place a type names it.

A ``$ref`` that an external ref mapping or a base stand-in types by an import (L1) and a root model that deduplication
dropped because its root names it (L5) leave users that name a class no module writes. Each place takes the type of
its own through the generation store: a field or a member of another type by swapping it in, a dict key in its place in
the dict, and the typed additional properties of a model by writing them again. A base keeps naming the reference.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from typing_extensions import Protocol

from datamodel_code_generator.python_literal import _make_referencing_type_expression

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.model import DataModel
    from datamodel_code_generator.model.output import OutputModelContext
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

__all__ = ["ReferenceUsersHost", "type_additional_properties_users", "type_reference_users"]


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


def type_reference_users(host: ReferenceUsersHost, reference: Reference, new_type: Callable[[], DataType]) -> None:
    """Type each user of ``reference`` in a field, a type or a dict key by its own ``new_type()``; bases keep it."""
    builder = host._graph_builder  # noqa: SLF001
    for fact in host.generation_store.index.data_type_facts_for_reference(reference):
        if fact.role == "base":
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
