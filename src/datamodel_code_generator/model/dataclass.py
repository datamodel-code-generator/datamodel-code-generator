"""Python dataclass model generator.

Generates Python dataclasses using the @dataclass decorator.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, Optional, cast

from datamodel_code_generator._format_types import (
    DateClassType,
    DatetimeClassType,
    PythonVersion,
    PythonVersionMin,
)
from datamodel_code_generator.model import DataModel, DataModelFieldBase, _rebuild_model_with_datamodel_namespace
from datamodel_code_generator.model._constraints import Constraints  # noqa: TC001 # needed for pydantic
from datamodel_code_generator.model.base import (
    UNDEFINED,
    _has_field_assignment,
    _nested_model_default_factory,
    get_effective_fields,
)
from datamodel_code_generator.model.imports import IMPORT_DATACLASS, IMPORT_FIELD
from datamodel_code_generator.model.types import DataTypeManager as _DataTypeManager
from datamodel_code_generator.python_literal import represent_python_value
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType, StrictTypes, chain_as_tuple

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Collection, Sequence
    from pathlib import Path

    from datamodel_code_generator.enums import DataclassArguments
    from datamodel_code_generator.imports import Import


def has_field_assignment(field: DataModelFieldBase) -> bool:
    """Check if a dataclass field renders with an assignment or default value."""
    return _has_field_assignment(field)


def get_field_default_info(field: DataModelFieldBase) -> tuple[bool, bool]:
    """Return Python dataclass constructor-default semantics."""
    return field._get_constructor_default_info()  # noqa: SLF001  # output-owned field policy hook


def field_participates_in_constructor(field: DataModelFieldBase) -> bool:
    """Return whether a Python dataclass field participates in __init__."""
    return field.extras.get("init") is not False


def _has_constructor_default(field: DataModelFieldBase) -> bool:
    """Classify a constructor default without recursively rendering structured values."""
    if field.required and not field.use_default_with_required:
        return False
    if field.default is not UNDEFINED and field.default is not None:
        return True
    return get_field_default_info(field)[0]


def _nested_dataclass_sources(data_type: DataType) -> tuple[DataClass, ...]:
    """Return dataclass models referenced by one non-mapping annotation branch."""
    sources: list[DataClass] = []
    for nested_data_type in data_type.data_types or (data_type,):
        if (
            nested_data_type.reference
            and isinstance(source := nested_data_type.reference.source, DataClass)
            and all(source is not existing_source for existing_source in sources)
        ):
            sources.append(source)
    return tuple(sources)


def _has_recursive_nested_mapping_default(
    model: DataClass,
    active_paths: frozenset[str] = frozenset(),
) -> bool:
    """Return whether materializing model defaults would recurse through mapping factories."""
    if model.path in active_paths:
        return True
    active_paths |= {model.path}
    effective_fields = model.fields if not model.base_classes else get_effective_fields(model)
    for field in effective_fields:
        if not field_participates_in_constructor(field) or not isinstance(field.default, dict):
            continue
        data_types = field.data_type.data_types or (field.data_type,)
        if (
            field.data_type.is_dict
            or field.data_type.is_mapping
            or any(data_type.is_dict or data_type.is_mapping for data_type in data_types)
        ):
            continue
        if any(
            _has_recursive_nested_mapping_default(source, active_paths)
            for source in _nested_dataclass_sources(field.data_type)
        ):
            return True
    return False


def _build_nested_dataclass_default_factory(data_type: DataType, default: dict[Any, Any]) -> str | None:
    """Build one valid dataclass constructor factory candidate."""
    if not (data_type.reference and isinstance(source := data_type.reference.source, DataClass)):
        return None
    effective_fields = source.fields if not source.base_classes else get_effective_fields(source)
    field_names: dict[str, str] = {}
    original_names: dict[str, str] | None = None
    required_names: set[str] = set()
    has_nested_mapping_default = False
    for model_field in effective_fields:
        if model_field.name is None or not field_participates_in_constructor(model_field):
            continue
        field_names[model_field.name] = model_field.name
        if model_field.original_name is not None:
            if original_names is None:
                original_names = {}
            original_names[model_field.original_name] = model_field.name
        if not _has_constructor_default(model_field):
            required_names.add(model_field.name)
        has_nested_mapping_default = has_nested_mapping_default or (
            isinstance(model_field.default, dict) and bool(_nested_dataclass_sources(model_field.data_type))
        )
    arguments: list[str] = []
    used_field_names: set[str] | None = set() if len(default) > 1 else None
    for name, value in default.items():
        field_name = original_names.get(name) if original_names is not None else None
        if field_name is None and (field_name := field_names.get(name)) is None:
            return None
        if used_field_names is not None:
            if field_name in used_field_names:
                return None
            used_field_names.add(field_name)
        required_names.discard(field_name)
        arguments.append(f"{field_name}={represent_python_value(value)}")
    if required_names:
        return None
    if has_nested_mapping_default and _has_recursive_nested_mapping_default(source):
        return None
    return f"lambda: {data_type.alias or source.class_name}({', '.join(arguments)})"


_REQUIRED_INHERITED_INIT_KEY = "_required_inherited_init"


class _DataclassReuseMixin:
    def has_keyword_only_definition(self) -> bool:
        """Return whether a dataclass declaration enables keyword-only fields."""
        return bool(cast("DataModel", self).dataclass_arguments.get("kw_only"))

    def create_reuse_model(self, base_ref: Reference) -> DataModel:
        """Create inherited model with empty fields pointing to base reference."""
        model = cast("DataModel", self)
        model_cls = cast("Any", self.__class__)
        model_attrs = vars(model)
        return cast(
            "DataModel",
            model_cls(
                fields=[],
                base_classes=[base_ref],
                description=model.description,
                reference=Reference(
                    name=model.name,
                    path=model.reference.path + "/reuse",
                ),
                custom_template_dir=model_attrs["_custom_template_dir"],
                custom_base_class=model.custom_base_class,
                keyword_only=model.keyword_only,
                frozen=model.frozen,
                treat_dot_as_module=model_attrs["_treat_dot_as_module"],
                dataclass_arguments=model.dataclass_arguments,
            ),
        )


class DataClass(_DataclassReuseMixin, DataModel):
    """DataModel implementation for Python dataclasses."""

    TEMPLATE_FILE_PATH: ClassVar[str] = "dataclass.jinja2"
    DEFAULT_IMPORTS: ClassVar[tuple[Import, ...]] = (IMPORT_DATACLASS,)
    FIELD_DEFAULT_CLASSIFIER = staticmethod(get_field_default_info)
    FIELD_PARTICIPATES_IN_CONSTRUCTOR = staticmethod(field_participates_in_constructor)
    SUPPORTS_TREE_SCOPE_REUSE_MODEL_INHERITANCE: ClassVar[bool] = True
    USES_DATACLASS_ARGUMENTS: ClassVar[bool] = True
    SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT: ClassVar[bool] = True
    SUPPORTS_DISCRIMINATOR: ClassVar[bool] = True
    SUPPORTS_INHERITED_DISCRIMINATOR_ENUM: ClassVar[bool] = True
    SUPPORTS_KW_ONLY: ClassVar[bool] = True

    @classmethod
    def prepare_required_inherited_field(
        cls,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        explicit_extras: Collection[str] = (),
    ) -> None:
        """Preserve inherited init=False until requiredness is final."""
        super().prepare_required_inherited_field(
            field,
            inherited_field,
            explicit_extras=explicit_extras,
        )
        if inherited_field.extras.get("init") is False and "init" not in explicit_extras:
            field.__dict__[_REQUIRED_INHERITED_INIT_KEY] = True

    @classmethod
    def restore_required_inherited_field_state(cls, field: DataModelFieldBase) -> bool:
        """Restore a required field excluded from the inherited constructor."""
        if not (field.required and field.__dict__.pop(_REQUIRED_INHERITED_INIT_KEY, False)):
            return False
        return field.extras.pop("init", None) is False

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        methods: list[str] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        frozen: bool = False,
        treat_dot_as_module: bool | None = None,
        dataclass_arguments: DataclassArguments | None = None,
    ) -> None:
        """Initialize dataclass with fields sorted by field assignment requirement."""
        super().__init__(
            reference=reference,
            fields=sorted(fields, key=_has_field_assignment),
            decorators=decorators,
            base_classes=base_classes,
            custom_base_class=custom_base_class,
            custom_template_dir=custom_template_dir,
            extra_template_data=extra_template_data,
            methods=methods,
            path=path,
            description=description,
            default=default,
            nullable=nullable,
            keyword_only=keyword_only,
            frozen=frozen,
            treat_dot_as_module=treat_dot_as_module,
        )
        self.dataclass_arguments = self.resolve_dataclass_arguments(
            dataclass_arguments, frozen=frozen, keyword_only=keyword_only
        )
        self._set_deprecated_decorator()


class DataModelField(DataModelFieldBase):
    """Field implementation for dataclass models."""

    _FIELD_KEYS: ClassVar[set[str]] = {
        "default_factory",
        "init",
        "repr",
        "hash",
        "compare",
        "metadata",
        "kw_only",
    }
    constraints: Optional[Constraints] = None  # noqa: UP045

    def process_const(self) -> None:
        """Process const field constraint using literal type."""
        self._process_const_as_literal()

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get imports including field() if needed."""
        field = self.field
        if field and field.startswith("field("):
            return chain_as_tuple(super().imports, (IMPORT_FIELD,))
        return super().imports

    @property
    def field(self) -> str | None:
        """For backwards compatibility."""
        result = str(self)
        if not result:
            return None
        return result

    def _get_default_factory_for_nested_model(self) -> str | None:
        """Get default_factory for nested dataclass model fields.

        Returns the class name if the field type references a DataClass,
        otherwise returns None.
        """
        return _nested_model_default_factory(self, DataClass)

    def _get_default_factory_for_nested_value(self, default: dict[Any, Any]) -> str | None:
        """Render a mapping default as a nested dataclass constructor."""
        if self.data_type.is_dict or self.data_type.is_mapping:
            return None

        data_types = self.data_type.data_types or (self.data_type,)
        if any(data_type.is_dict or data_type.is_mapping for data_type in data_types):
            return None

        default_factory: str | None = None
        for data_type in data_types:
            if (factory := _build_nested_dataclass_default_factory(data_type, default)) is None:
                continue
            if default_factory is not None:
                return None
            default_factory = factory
        return default_factory

    def _get_field_data(self) -> dict[str, Any]:
        """Return structured field() arguments before rendering."""
        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k in self._FIELD_KEYS}

        needs_nested_factory = (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
            and "default_factory" not in data
        )
        if needs_nested_factory and (nested_model_name := self._get_default_factory_for_nested_model()):
            data["default_factory"] = nested_model_name

        if self.default is None:
            if data and "default_factory" not in data and (not self.strip_default_none or data.get("init") is False):
                data["default"] = None
        elif self.default != UNDEFINED and "default_factory" not in data:
            data["default"] = self.default

        if self.required and not self.use_default_with_required:
            data = {
                k: v
                for k, v in data.items()
                if k
                not in {
                    "default",
                    "default_factory",
                }
            }

        match data.get("default", UNDEFINED):
            case dict() as default if nested_factory := self._get_default_factory_for_nested_value(default):
                data.pop("default")
                data["default_factory"] = nested_factory
            case list() | dict() | set() as default:
                data.pop("default")
                data["default_factory"] = (
                    f"lambda: {represent_python_value(default)}" if default else type(default).__name__
                )

        return data

    def _get_constructor_default_info(self) -> tuple[bool, bool]:
        """Return constructor-default semantics from structured field data."""
        if self.required and not self.use_default_with_required:
            return False, False
        data = self._get_field_data()
        has_rendered_assignment = bool(data) or self._has_forced_field_assignment
        if (not data and self._has_forced_field_assignment) or not (
            (has_rendered_assignment and not self.use_annotated) or not self.should_strip_default_none()
        ):
            return False, False
        if "default_factory" in data:
            return True, False
        if data and "default" not in data:
            return False, False
        return True, True

    def __str__(self) -> str:
        """Generate field() call or default value representation."""
        data = self._get_field_data()
        if not data:
            return "field()" if self._has_forced_field_assignment else ""

        if len(data) == 1 and "default" in data:
            return represent_python_value(data["default"])
        kwargs = [f"{k}={v if k == 'default_factory' else represent_python_value(v)}" for k, v in data.items()]
        return f"field({', '.join(kwargs)})"


class DataTypeManager(_DataTypeManager):
    """Type manager for dataclass models."""

    def __init__(  # noqa: PLR0913, PLR0917
        self,
        python_version: PythonVersion = PythonVersionMin,
        use_standard_collections: bool = False,  # noqa: FBT001, FBT002
        use_generic_container_types: bool = False,  # noqa: FBT001, FBT002
        strict_types: Sequence[StrictTypes] | None = None,
        use_non_positive_negative_number_constrained_types: bool = False,  # noqa: FBT001, FBT002
        use_decimal_for_multiple_of: bool = False,  # noqa: FBT001, FBT002
        use_union_operator: bool = False,  # noqa: FBT001, FBT002
        use_pendulum: bool = False,  # noqa: FBT001, FBT002
        use_standard_primitive_types: bool = False,  # noqa: FBT001, FBT002
        use_object_type: bool = False,  # noqa: FBT001, FBT002
        target_datetime_class: DatetimeClassType = DatetimeClassType.Datetime,
        target_date_class: DateClassType | None = None,
        treat_dot_as_module: bool | None = None,  # noqa: FBT001
        use_serialize_as_any: bool = False,  # noqa: FBT001, FBT002
    ) -> None:
        """Initialize type manager with dataclass datetime defaults."""
        super().__init__(
            python_version=python_version,
            use_standard_collections=use_standard_collections,
            use_generic_container_types=use_generic_container_types,
            strict_types=strict_types,
            use_non_positive_negative_number_constrained_types=use_non_positive_negative_number_constrained_types,
            use_decimal_for_multiple_of=use_decimal_for_multiple_of,
            use_union_operator=use_union_operator,
            use_pendulum=use_pendulum,
            use_standard_primitive_types=use_standard_primitive_types,
            use_object_type=use_object_type,
            target_datetime_class=target_datetime_class,
            target_date_class=target_date_class,
            treat_dot_as_module=treat_dot_as_module,
            use_serialize_as_any=use_serialize_as_any,
        )


_rebuild_model_with_datamodel_namespace(DataModelField)
