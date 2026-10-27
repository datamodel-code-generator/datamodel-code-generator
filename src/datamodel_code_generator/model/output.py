"""Output model capabilities shared by schema parsers."""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING

from datamodel_code_generator._target_unicode import host_parsable
from datamodel_code_generator.model.base import _ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY, ALL_MODEL
from datamodel_code_generator.reference import ModelType

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Collection, Iterable, Mapping
    from typing import Any

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.enums import AliasGenerator, TargetPydanticVersion
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.python_literal import _TypeHintSource
    from datamodel_code_generator.types import DataTypeManager
    from datamodel_code_generator.validators import ModelValidators


def prepare_output_model_config(  # noqa: PLR0913, PLR0912
    extra_template_data: defaultdict[str, dict[str, Any]],
    *,
    validators: Mapping[str, ModelValidators] | None,
    use_generic_base_class: bool,
    allow_population_by_field_name: bool,
    alias_generator: AliasGenerator | None,
    no_alias: bool,
    allow_extra_fields: bool,
    extra_fields: str | None,
    enable_faux_immutability: bool,
    use_attribute_docstrings: bool,
    use_single_line_docstring: bool,
    target_pydantic_version: TargetPydanticVersion | None,
    schema_validator_base_class_name: str | None,
    generate_schema_validators: bool,
    pin_aliases_with_alias_generator: bool,
) -> dict[str, Any]:
    """Prepare output configuration while retaining template keys and insertion order."""
    if validators:
        for name, model_config in validators.items():
            extra_template_data[name]["validators"] = [
                value.model_dump(mode="json") for value in model_config.validators
            ]

    base_config: dict[str, Any] = {}
    if allow_population_by_field_name:
        (base_config if use_generic_base_class else extra_template_data[ALL_MODEL])[
            "allow_population_by_field_name"
        ] = True
    if alias_generator:
        target = base_config if use_generic_base_class else extra_template_data[ALL_MODEL]
        target["allow_population_by_field_name"] = True
        target["alias_generator"] = alias_generator
        if use_generic_base_class:
            extra_template_data[ALL_MODEL]["_alias_generator"] = alias_generator
    if no_alias:
        extra_template_data[ALL_MODEL]["_no_alias"] = True
    if pin_aliases_with_alias_generator:
        extra_template_data[ALL_MODEL]["_pin_aliases_with_alias_generator"] = True
    if allow_extra_fields:
        (base_config if use_generic_base_class else extra_template_data[ALL_MODEL])["allow_extra_fields"] = True
    if extra_fields:
        (base_config if use_generic_base_class else extra_template_data[ALL_MODEL])["extra_fields"] = extra_fields
    if enable_faux_immutability:
        (base_config if use_generic_base_class else extra_template_data[ALL_MODEL])["allow_mutation"] = False
    if use_attribute_docstrings:
        (base_config if use_generic_base_class else extra_template_data[ALL_MODEL])["use_attribute_docstrings"] = True
    if use_single_line_docstring:
        extra_template_data[ALL_MODEL]["use_single_line_docstring"] = True
    if target_pydantic_version:
        extra_template_data[ALL_MODEL]["target_pydantic_version"] = target_pydantic_version
        if use_generic_base_class:
            base_config["target_pydantic_version"] = target_pydantic_version
    if schema_validator_base_class_name:
        extra_template_data[ALL_MODEL]["schema_validator_base_class_name"] = schema_validator_base_class_name
    if generate_schema_validators:
        extra_template_data[ALL_MODEL]["schema_runtime_validation_enabled"] = True
    return base_config


def _expression_names(expression: ast.AST, restore_name: Callable[[str], str]) -> set[str]:
    """Find unqualified loads without treating literal or keyword text as bindings."""
    return {restore_name(node.id) for node in ast.walk(expression) if isinstance(node, ast.Name)}


def _model_field_name_collisions(model: DataModel, import_names: Collection[str]) -> set[str]:
    """Find imported names hidden by assignments in the emitted model body."""
    field_names = {field.name for field in model.fields if field.name is not None}
    candidates = field_names.intersection(import_names)
    if not candidates:
        return set()
    source, restore_name = host_parsable(model.render())
    class_body = next(
        (
            node.body
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef) and restore_name(node.name) == model.class_name
        ),
        None,
    )
    if class_body is None:
        return set()
    fields = [
        (restore_name(node.target.id), node)
        for node in class_body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]
    assigned_names = (
        field_names
        if model.FIELD_NAME_MODEL_TYPE is ModelType.MSGSPEC
        else {name for name, node in fields if node.value is not None}
    )
    candidates.intersection_update(assigned_names)
    collisions: set[str] = set()
    previous_names: set[str] = set()
    for name, node in fields:
        collisions.update(candidates.intersection(_expression_names(node.annotation, restore_name)))
        if node.value is not None:
            collisions.update(candidates.intersection(previous_names, _expression_names(node.value, restore_name)))
            previous_names.add(name)
    return collisions


def _bare_name_loads(models: Iterable[DataModel], names: Collection[str]) -> set[str]:
    """Find the names of ``names`` that the emitted models load bare, not as the module of an attribute (``name.X``)."""
    found: set[str] = set()
    for model in models:
        source, restore_name = host_parsable(model.render())
        tree = ast.parse(source)
        attribute_values = {id(node.value) for node in ast.walk(tree) if isinstance(node, ast.Attribute)}
        found.update(
            name
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
            and id(node) not in attribute_values
            and (name := restore_name(node.id)) in names
        )
    return found


class OutputModelContext:
    """Capabilities for a configured set of output model types."""

    __slots__ = (
        "_data_model_root_type",
        "_data_model_type",
        "checks_constraints",
        "decodes_mixed_enum_values",
        "decodes_value_unions",
        "layers_repeated_constraints",
        "literal_holds_enum_members",
        "requires_additional_properties_reference_classes",
        "requires_tagged_union_discriminator",
        "supports_annotated_constraints",
        "supports_boolean_literals",
        "supports_internal_annotated_constraints",
        "writes_constrained_types",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        data_model_type: type[DataModel],
        data_model_root_type: type[DataModel],
        supports_annotated_constraints: bool,
        supports_internal_annotated_constraints: bool,
        supports_boolean_literals: bool,
        requires_tagged_union_discriminator: bool,
        requires_additional_properties_reference_classes: bool,
        decodes_mixed_enum_values: bool = True,
        writes_constrained_types: bool = False,
        decodes_value_unions: bool = True,
        literal_holds_enum_members: bool = True,
        layers_repeated_constraints: bool = True,
        checks_constraints: bool = False,
    ) -> None:
        """Initialize capabilities with the established keyword signature."""
        self._data_model_type = data_model_type
        self._data_model_root_type = data_model_root_type
        self.supports_annotated_constraints = supports_annotated_constraints
        self.supports_internal_annotated_constraints = supports_internal_annotated_constraints
        self.supports_boolean_literals = supports_boolean_literals
        self.requires_tagged_union_discriminator = requires_tagged_union_discriminator
        self.requires_additional_properties_reference_classes = requires_additional_properties_reference_classes
        self.decodes_mixed_enum_values = decodes_mixed_enum_values
        self.writes_constrained_types = writes_constrained_types
        self.decodes_value_unions = decodes_value_unions
        self.literal_holds_enum_members = literal_holds_enum_members
        self.layers_repeated_constraints = layers_repeated_constraints
        self.checks_constraints = checks_constraints

    @classmethod
    def from_generation_types(  # noqa: PLR0913
        cls,
        *,
        data_model_type: type[DataModel],
        data_model_root_type: type[DataModel],
        data_model_field_type: type[DataModelFieldBase],
        data_type_manager_type: type[DataTypeManager],
        configured_types_are_builtin: bool,
        use_annotated: bool,
    ) -> OutputModelContext:
        """Resolve compatible capabilities without depending on an output backend."""
        context = data_model_type.ANNOTATED_CONSTRAINTS_CONTEXT
        root_context = data_model_root_type.ANNOTATED_CONSTRAINTS_CONTEXT
        supports_internal_annotated_constraints = (
            configured_types_are_builtin
            and context is not None
            and data_model_type.SUPPORTS_ANNOTATED_CONSTRAINTS
            and data_model_root_type.SUPPORTS_ANNOTATED_CONSTRAINTS
            and data_model_field_type.SUPPORTS_ANNOTATED_CONSTRAINTS
            and data_type_manager_type.SUPPORTS_ANNOTATED_CONSTRAINTS
            and data_model_field_type.ANNOTATED_CONSTRAINTS_CONTEXT is context
            and data_type_manager_type.ANNOTATED_CONSTRAINTS_CONTEXT is context
            and (root_context is None or root_context is context)
        )
        return cls(
            data_model_type=data_model_type,
            data_model_root_type=data_model_root_type,
            supports_annotated_constraints=use_annotated and supports_internal_annotated_constraints,
            supports_internal_annotated_constraints=supports_internal_annotated_constraints,
            supports_boolean_literals=data_model_type.SUPPORTS_BOOLEAN_LITERAL,
            requires_tagged_union_discriminator=data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
            requires_additional_properties_reference_classes=(
                data_model_type.REQUIRES_ADDITIONAL_PROPERTIES_REFERENCE_CLASSES
            ),
            decodes_mixed_enum_values=data_model_type._DECODES_MIXED_ENUM_VALUES,  # noqa: SLF001
            writes_constrained_types=data_type_manager_type._WRITES_CONSTRAINED_TYPES,  # noqa: SLF001
            decodes_value_unions=data_model_type._DECODES_VALUE_UNIONS,  # ruff: ignore[private-member-access]
            literal_holds_enum_members=data_model_type._LITERAL_HOLDS_ENUM_MEMBERS,  # noqa: SLF001
            layers_repeated_constraints=data_model_type._LAYERS_REPEATED_CONSTRAINTS,  # ruff: ignore[private-member-access]
            checks_constraints=data_model_type._CHECKS_CONSTRAINTS,  # ruff: ignore[private-member-access]
        )

    def resolve_nested_constrained_model_type(self) -> type[DataModel]:
        """Return the root model used for an inline constrained value."""
        return self._data_model_type.resolve_nested_constrained_model_type(self._data_model_root_type)

    def store_additional_properties_value(
        self,
        extra_template_data: dict[str, Any],
        *,
        value: bool,
        use_backport: bool = False,
        target_python_version: PythonVersion | None = None,
    ) -> None:
        """Store an additional-properties constraint through the output model."""
        if target_python_version is not None and not value:
            use_backport |= self._data_model_type.requires_extra_items_backport(target_python_version)
        self._data_model_type.store_additional_properties_value(
            extra_template_data,
            value=value,
            use_backport=use_backport,
        )

    def has_additional_properties_type(self, extra_template_data: dict[str, Any]) -> bool:
        """Return whether output metadata contains a typed additional-properties entry."""
        return self._data_model_type.has_additional_properties_type(extra_template_data)

    @staticmethod
    def additional_properties_type_source(extra_template_data: dict[str, Any]) -> _TypeHintSource | None:
        """Return the data type a typed additional-properties entry was rendered from, while it names classes."""
        return getattr(extra_template_data.get(_ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY), "source", None)

    def store_additional_properties_type(  # noqa: PLR0913
        self,
        extra_template_data: dict[str, Any],
        type_hint: str,
        reference_classes: set[str] | None = None,
        *,
        imports: tuple[Import, ...] = (),
        use_backport: bool = False,
        target_python_version: PythonVersion | None = None,
    ) -> None:
        """Store typed additional-properties metadata through the output model."""
        if target_python_version is not None:
            use_backport |= self._data_model_type.requires_extra_items_backport(target_python_version)
        self._data_model_type.store_additional_properties_type(
            extra_template_data,
            type_hint,
            reference_classes,
            root_model_type=self._data_model_root_type,
            imports=imports,
            use_backport=use_backport,
        )

    def store_additional_properties_reference_classes(
        self,
        extra_template_data: dict[str, Any],
        reference_classes: set[str],
    ) -> None:
        """Store dependencies through every possible output model type."""
        self._data_model_type.store_additional_properties_reference_classes(
            extra_template_data,
            reference_classes,
            root_model_type=self._data_model_root_type,
        )

    # Preserve the established private methods for custom integrations while
    # parser code migrates to the public output-model protocol above.
    @staticmethod
    def _has_additional_properties_type(extra_template_data: dict[str, Any]) -> bool:
        return "additionalPropertiesType" in extra_template_data

    def _store_additional_properties_type(
        self,
        extra_template_data: dict[str, Any],
        type_hint: str,
        reference_classes: set[str] | None = None,
    ) -> None:
        self.store_additional_properties_type(extra_template_data, type_hint, reference_classes)

    def _store_additional_properties_reference_classes(
        self,
        extra_template_data: dict[str, Any],
        reference_classes: set[str],
    ) -> None:
        self.store_additional_properties_reference_classes(extra_template_data, reference_classes)


# Preserve the established compatibility import identity for repr and pickling.
OutputModelContext.__module__ = "datamodel_code_generator.parser._output_context"
