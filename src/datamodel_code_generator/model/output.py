"""Output model capabilities shared by schema parsers."""

from __future__ import annotations

import ast
from itertools import islice
from typing import TYPE_CHECKING, Literal, NamedTuple, TypeAlias

from datamodel_code_generator.model._rendered_names import _class_bodies, _expression_names, _parsed
from datamodel_code_generator.model.base import (
    _ADDITIONAL_PROPERTIES_TYPE_TEMPLATE_DATA_KEY,
    ALL_MODEL,
    _class_body_fields,
    _mark_forbids_unknown_fields,
)
from datamodel_code_generator.reference import ModelType

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Collection, Mapping, Sequence
    from typing import Any

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator.enums import AliasGenerator, TargetPydanticVersion
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.python_literal import _TypeHintSource
    from datamodel_code_generator.types import DataTypeManager
    from datamodel_code_generator.validators import ModelValidators


def prepare_output_model_config(  # noqa: PLR0912
    extra_template_data: defaultdict[str, dict[str, Any]],
    *,
    validators: Mapping[str, ModelValidators] | None,
    use_generic_base_class: bool,
    allow_population_by_field_name: bool,
    alias_generator: AliasGenerator | None,
    no_alias: bool,
    allow_extra_fields: bool,
    extra_fields: str | None,
    use_msgspec_forbid_unknown_fields: bool,
    enable_faux_immutability: bool,
    use_attribute_docstrings: bool,
    use_single_line_docstring: bool,
    target_pydantic_version: TargetPydanticVersion | None,
    schema_validator_base_class_name: str | None,
    generate_schema_validators: bool,
    pin_aliases_with_alias_generator: bool,
) -> dict[str, Any]:
    """Prepare output configuration while retaining template keys and insertion order.

    Under --use-msgspec-forbid-unknown-fields, --extra-fields forbid marks every model for msgspec's
    forbid_unknown_fields; with --use-generic-base-class the base class carries it already.
    """
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
        if extra_fields == "forbid" and use_msgspec_forbid_unknown_fields and not use_generic_base_class:
            _mark_forbids_unknown_fields(extra_template_data[ALL_MODEL])
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


def _model_field_name_collisions(model: DataModel, rendered: str | None, import_names: Collection[str]) -> set[str]:
    """Find imported names hidden by assignments in the emitted model body, ``rendered`` or rendered now.

    A field annotation loads its names wherever it stands; a default and a method decorator load the names of the
    fields assigned before them.
    """
    field_names = {field.name for field in _class_body_fields(model) if field.name is not None}
    candidates = field_names.intersection(import_names)
    if not candidates:
        return set()
    tree, restore_name = _parsed(model.render() if rendered is None else rendered)
    if (class_body := next(_class_bodies(tree, restore_name, model.class_name), None)) is None:
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
    for node in class_body:
        if isinstance(node, ast.FunctionDef):
            collisions.update(
                candidates.intersection(
                    previous_names,
                    {name for decorator in node.decorator_list for name in _expression_names(decorator, restore_name)},
                )
            )
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            collisions.update(candidates.intersection(_expression_names(node.annotation, restore_name)))
            if node.value is not None:
                collisions.update(candidates.intersection(previous_names, _expression_names(node.value, restore_name)))
                previous_names.add(restore_name(node.target.id))
    return collisions


_Kind: TypeAlias = Literal["type", "none", "object", "raise", "stop"]
"""What evaluating an annotation expression in a namespace gives.

``type`` a class or typing form, ``none`` the value ``None``, ``object`` any other value a class body binds,
``raise`` an error that is no ``NameError`` (``names``: the names whose value causes it), ``stop`` a ``NameError``,
which the library tolerates.
"""


class _Value(NamedTuple):
    kind: _Kind
    names: tuple[str, ...] = ()


class _Namespace(NamedTuple):
    """The names an annotation loads in the namespace of a base: its class body, its module, the class itself."""

    body: Mapping[str, Literal["none", "object"]]
    module_names: Collection[str]
    restore_name: Callable[[str], str]
    aliased: Collection[str]
    is_builtin: Callable[[str], bool]


_TYPE = _Value("type")
_NONE = _Value("none")
_STOP = _Value("stop")


def _is_none_default(value: ast.expr) -> bool:
    """Return whether a class attribute assigned ``value`` holds ``None``: ``None`` or ``Field(None, ...)``."""
    if isinstance(value, ast.Call):
        return any(isinstance(argument, ast.Constant) and argument.value is None for argument in value.args[:1])
    return isinstance(value, ast.Constant) and value.value is None


def _base_body_bindings(base: DataModel) -> dict[str, Literal["none", "object"]]:
    """Return what the class namespace of ``base`` holds under the name of each field that its body assigns.

    A field a dataclass with slots declares is a ``member_descriptor`` attribute (``class_attribute_hides_named_type``
    of a field its body does not assign), one that is required too.
    """
    tree, restore_name = _parsed(base.render())
    bindings: dict[str, Literal["none", "object"]] = {
        restore_name(node.target.id): "none" if _is_none_default(node.value) else "object"
        for node in next(_class_bodies(tree, restore_name, base.class_name), [])
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None
    }
    bindings.update(
        dict.fromkeys(
            (
                field.name
                for field in _class_body_fields(base)
                if field.name
                and base.class_attribute_hides_named_type(field, assigned=False, resolved_late=True, as_string=False)
            ),
            "object",
        )
    )
    return bindings


def _evaluate_name(node: ast.Name, namespace: _Namespace) -> _Value:
    """Return the value of a name an annotation loads: the class body first, then the module and the builtins."""
    name = namespace.restore_name(node.id)
    if name in namespace.aliased:
        return _STOP
    if (kind := namespace.body.get(name)) is not None:
        return _Value(kind, (name,))
    if name in namespace.module_names or namespace.is_builtin(name):
        return _TYPE
    return _STOP


def _evaluate(node: ast.expr, namespace: _Namespace) -> _Value:
    """Evaluate an annotation expression in ``namespace`` in Python's order, as far as its values go.

    An attribute, a subscript or a call of ``None`` or of another value that a class body binds raises, and so does
    ``|`` of one of them. The arguments of a subscript or a call load their names and raise on an attribute of
    such a value; the check of their values (``typing`` of Python 3.10) is not modelled, so a miss keeps today's
    failure and never changes a working module.
    """
    if isinstance(node, ast.Name):
        return _evaluate_name(node, namespace)
    if isinstance(node, ast.Constant):
        return _NONE if node.value is None else _TYPE
    head = (
        node.value
        if isinstance(node, (ast.Attribute, ast.Subscript))
        else node.func
        if isinstance(node, ast.Call)
        else None
    )
    if head is not None and (value := _evaluate(head, namespace)).kind != "type":
        return _Value("raise", value.names) if value.kind in {"none", "object"} else value
    operands = (
        [*node.args, *(keyword.value for keyword in node.keywords)]
        if isinstance(node, ast.Call)
        else [child for child in ast.iter_child_nodes(node) if isinstance(child, ast.expr) and child is not head]
    )
    values: list[_Value] = []
    for operand in operands:
        value = _evaluate(operand, namespace)
        if value.kind in {"raise", "stop"}:
            return value
        values.append(value)
    return _union_value(values) if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr) else _TYPE


def _union_value(operands: Sequence[_Value]) -> _Value:
    """Return the value of ``a | b``: it raises for any other value that a class body binds and for two ``None``."""
    kinds = [operand.kind for operand in operands]
    if "object" in kinds or kinds == ["none", "none"]:
        return _Value("raise", tuple(name for operand in operands for name in operand.names))
    return _TYPE


class _ModuleScope(NamedTuple):
    """What the module of a model binds, as the namespace of a base reads it."""

    module_names_of: Callable[[DataModel], Collection[str]]
    aliased: Collection[str]
    is_builtin: Callable[[str], bool]


def _inherited_field_name_collisions(
    class_name: str,
    namespaces: Mapping[str, Sequence[DataModel]],
    rendered: str,
    import_names: Collection[str],
    scope: _ModuleScope,
) -> set[str]:
    """Find imported names that a base of the class binds in its class body to a value the annotations raise on.

    The library evaluates the annotation of a field that ``namespaces`` names (``annotation_namespaces``) in the
    namespace of each of its bases that declares it: the base's module and its class body. The first name an
    annotation loads whose value there makes the evaluation raise is the collision, where the module imports it or it
    is a builtin: its alias is a ``NameError`` in the base. The names the own fields alias (``aliased``) are loaded
    under their aliases already.
    """
    tree, restore_name = _parsed(rendered)
    scopes = {
        id(base): _Namespace(
            _base_body_bindings(base),
            {*scope.module_names_of(base), class_name},
            restore_name,
            scope.aliased,
            scope.is_builtin,
        )
        for bases in namespaces.values()
        for base in bases
    }
    collisions: set[str] = set()
    for node in next(_class_bodies(tree, restore_name, class_name), []):
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        for base in namespaces.get(restore_name(node.target.id), ()):
            if (value := _evaluate(node.annotation, scopes[id(base)])).kind == "raise":
                collisions.update(islice((name for name in value.names if name in import_names), 1))
    return collisions


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
        "writes_discriminator_argument",
    )

    def __init__(
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
        writes_discriminator_argument: bool = False,
    ) -> None:
        """Initialize capabilities with the established keyword signature.

        ``writes_discriminator_argument`` tells whether a union field writes ``discriminator=``, which Pydantic builds
        and checks (``DataModelFieldBase.SUPPORTS_DISCRIMINATOR``).
        """
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
        self.writes_discriminator_argument = writes_discriminator_argument

    @classmethod
    def from_generation_types(
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
            writes_discriminator_argument=data_model_field_type.SUPPORTS_DISCRIMINATOR,
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

    def store_additional_properties_type(
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
