"""Shared base classes for Pydantic model implementations.

Provides Constraints, DataModelField, and BaseModelBase used by Pydantic v2 models.
"""

from __future__ import annotations

from abc import ABC
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, NamedTuple, Optional

from pydantic import Field as _Field

from datamodel_code_generator import cached_path_exists
from datamodel_code_generator.model import (
    UNDEFINED,
    DataModel,
    DataModelFieldBase,
    _rebuild_model_with_datamodel_namespace,
)
from datamodel_code_generator.model import ConstraintsBase as _ConstraintsBase
from datamodel_code_generator.model._constraints import Constraints
from datamodel_code_generator.model._constraints import PatternConstraints as _PatternConstraints
from datamodel_code_generator.model._pydantic_imports import IMPORT_ANYURL, IMPORT_FIELD
from datamodel_code_generator.model.base import _nested_model_default_factory, _remember_custom_template_dependency
from datamodel_code_generator.python_literal import represent_python_value
from datamodel_code_generator.types import UnionIntFloat as _UnionIntFloat
from datamodel_code_generator.types import (
    chain_as_tuple,
    merge_normalized_constraint,
    normalize_integer_constraint,
)

if TYPE_CHECKING:
    from collections import defaultdict

    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

# Preserve names that were historically importable from this module.
Field = _Field
ConstraintsBase = _ConstraintsBase
PatternConstraints = _PatternConstraints
UnionIntFloat = _UnionIntFloat


class _PydanticFieldRenderPlan(NamedTuple):
    """Structured Field() output shared by render and compatibility properties."""

    rendered: str
    assignment: str | None
    arguments: tuple[str, ...]
    default_factory: Any


class DataModelField(DataModelFieldBase):
    """Field implementation for Pydantic models."""

    SUPPORTS_FIELD_CONSTRAINTS: ClassVar[bool] = True
    _EXCLUDE_FIELD_KEYS: ClassVar[set[str]] = {
        "alias",
        "default",
        "const",
        "gt",
        "ge",
        "lt",
        "le",
        "multiple_of",
        "min_items",
        "max_items",
        "min_length",
        "max_length",
        "regex",
    }
    _COMPARE_EXPRESSIONS: ClassVar[set[str]] = {"gt", "ge", "lt", "le"}
    _INTEGER_CONSTRAINTS: ClassVar[set[str]] = _COMPARE_EXPRESSIONS | {"multiple_of"}
    constraints: Optional[Constraints] = None  # noqa: UP045

    @property
    def has_default_factory_in_field(self) -> bool:
        """Check the current structured field state for an effective default factory."""
        return self._get_effective_default_factory() is not None

    @property
    def method(self) -> str | None:
        """Get the validation method name."""
        return self.validator

    @property
    def validator(self) -> str | None:
        """Get the validator name."""
        return None
        # TODO refactor this method for other validation logic

    @property
    def field(self) -> str | None:
        """For backwards compatibility."""
        return self._get_field_render_plan().assignment

    def _rendered_field_values(self) -> tuple[str | None, str | None]:
        """Render field and annotated values from one structured plan."""
        return self._rendered_field_values_from_plan(self._get_field_render_plan())

    def _rendered_field_values_from_plan(
        self,
        plan: _PydanticFieldRenderPlan,
    ) -> tuple[str | None, str | None]:
        """Render compatibility values from an already computed plan."""
        if self.is_class_var:
            return None, None
        if not self.use_annotated or not plan.rendered:
            return plan.assignment, None
        return plan.assignment, f"Annotated[{self.type_hint}, {plan.rendered}]"

    def _has_field_statement(self) -> bool:
        """Return whether rendering this field will require a Field() call."""
        if not self._may_have_field_statement():
            return False
        return bool(self._get_field_render_plan().rendered)

    def _may_have_field_statement(self) -> bool:
        """Return whether field metadata may require a Field() call."""
        if self.is_class_var:
            return False
        has_field_metadata = (
            self.extras
            or self.alias is not None
            or self.const
            or self.constraints is not None
            or (self.use_frozen_field and self.read_only)
            or self.validation_aliases
            or self.serialization_alias is not None
            or self.use_serialization_alias
        )
        needs_required_nullable_field = self.nullable and self.required and not self.use_default_with_required
        needs_optional_nested_factory = (
            self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
        )
        return bool(has_field_metadata or needs_required_nullable_field or needs_optional_nested_factory)

    def _has_numeric_data_type(self, type_name: str, strict_import_part: str) -> bool:
        """Return whether any field data type is the given builtin or strict numeric type."""
        return any(
            data_type.type == type_name
            or (data_type.strict and data_type.import_ and strict_import_part in data_type.import_.import_)
            for data_type in self.data_type.all_data_types
        )

    def _get_strict_field_constraint(
        self, constraint: str, value: Any, *, is_float_type: bool, is_int_type: bool
    ) -> tuple[str, Any] | None:
        if value is None or constraint not in self._INTEGER_CONSTRAINTS:
            return constraint, value
        if is_float_type:
            return constraint, float(value)
        if is_int_type:
            return normalize_integer_constraint(constraint, value)
        if isinstance(value, float) and value.is_integer():
            return constraint, int(value)
        return constraint, value

    def _get_normalized_constraint_data(self) -> dict[str, Any]:
        """Build constraint data with integer-safe values, merging colliding bounds."""
        assert self.constraints is not None
        dumped = self.constraints._exclude_unset_dump  # noqa: SLF001
        if (
            self.data_type.type == "None"
            and not self.data_type.is_custom_type
            and not ((parent := self.parent) and (parent.IS_ALIAS or parent._uses_custom_root_template))  # noqa: SLF001
        ):
            dumped = {key: value for key, value in dumped.items() if key not in self._INTEGER_CONSTRAINTS}
        has_integer_constraints = bool(self._INTEGER_CONSTRAINTS & dumped.keys())
        is_float_type = has_integer_constraints and self._has_numeric_data_type("float", "Float")
        is_int_type = has_integer_constraints and not is_float_type and self._has_numeric_data_type("int", "Int")
        constraint_data: dict[str, Any] = {}
        for k, v in dumped.items():
            if (
                normalized := self._get_strict_field_constraint(
                    k, v, is_float_type=is_float_type, is_int_type=is_int_type
                )
            ) is not None:
                merge_normalized_constraint(constraint_data, normalized[0], normalized[1])
        return constraint_data

    def _has_anyurl_outside_container(self) -> bool:
        def has_anyurl(data_type: DataType, *, inside_container: bool) -> bool:
            is_container = inside_container or (
                data_type.is_dict
                or data_type.is_list
                or data_type.is_set
                or data_type.is_frozen_set
                or data_type.is_mapping
                or data_type.is_sequence
                or data_type.is_tuple
            )
            if data_type.import_ == IMPORT_ANYURL:
                return not is_container
            return any(has_anyurl(child, inside_container=is_container) for child in data_type.data_types) or (
                data_type.dict_key is not None and has_anyurl(data_type.dict_key, inside_container=is_container)
            )

        return has_anyurl(self.data_type, inside_container=False)

    def _get_default_factory_for_optional_nested_model(self) -> str | None:
        """Get default_factory for optional nested Pydantic model fields.

        Returns the class name if the field type references a BaseModel,
        otherwise returns None.
        """
        return _nested_model_default_factory(self, BaseModelBase)

    def _has_default_for_nested_model_factory(self) -> bool:
        """Preserve optional None defaults retained by Pydantic model templates."""
        if self.default is None and self.data_type.is_optional:
            return True
        return super()._has_default_for_nested_model_factory()

    def enable_structured_default_validation(self) -> bool:
        """Enable Pydantic validation for a structured default exactly once."""
        if self.extras.get("validate_default") is True:
            return False
        self.extras["validate_default"] = True
        return True

    def _process_data_in_str(self, data: dict[str, Any]) -> None:  # pragma: no cover
        if self.const:
            data["const"] = True

        if self.use_frozen_field and self.read_only:
            data["allow_mutation"] = False

    def _process_annotated_field_arguments(self, field_arguments: list[str]) -> list[str]:  # noqa: PLR6301
        return field_arguments

    def _get_effective_default_factory(self, configured_default_factory: Any = UNDEFINED) -> Any:
        """Return the current effective factory without depending on a previous render."""
        if self.is_class_var:
            return None
        if configured_default_factory is UNDEFINED:
            has_default_factory = "default_factory" in self.extras
            configured_default_factory = self.extras.get("default_factory")
        else:
            has_default_factory = True
        if (self.required and not self.use_default_with_required) or (
            self.default is not UNDEFINED and self.default is not None and not has_default_factory
        ):
            default_factory = None
        else:
            default_factory = configured_default_factory

        if (
            default_factory is None
            and self.use_default_factory_for_optional_nested_models
            and not self.required
            and (self.default is None or self.default is UNDEFINED)
        ):
            default_factory = self._get_default_factory_for_optional_nested_model()

        return default_factory

    def _get_field_data_and_default_factory(self) -> tuple[dict[str, Any], Any]:
        """Build Field() keyword data and the effective default_factory."""
        if self.is_class_var:
            return {}, None

        data: dict[str, Any] = {k: v for k, v in self.extras.items() if k not in self._EXCLUDE_FIELD_KEYS}
        if self.alias is not None:
            data["alias"] = self.alias
        if self.constraints is not None and self._can_apply_constraints:
            constraint_data = {} if self._has_anyurl_outside_container() else self._get_normalized_constraint_data()
            data = {**data, **constraint_data}

        if self.use_field_description:
            data.pop("description", None)  # Description is part of field docstring

        self._process_data_in_str(data)

        match data.pop("discriminator", None):
            case str() as discriminator:
                data["discriminator"] = discriminator
            case dict() as discriminator:  # pragma: no cover
                data["discriminator"] = discriminator["propertyName"]

        configured_default_factory = data.pop("default_factory", UNDEFINED)
        default_factory = self._get_effective_default_factory(configured_default_factory)

        return data, default_factory

    @staticmethod
    def _render_field_call(arguments: tuple[str, ...]) -> str:
        return f"Field({', '.join(arguments)})"

    def _get_single_argument_field_render_plan(
        self,
        argument: str,
        *,
        assignment_argument: str | None = None,
    ) -> _PydanticFieldRenderPlan:
        """Build a Field() plan whose rendered and assignment views have one argument."""
        arguments = (argument,)
        rendered = self._render_field_call(arguments)
        return _PydanticFieldRenderPlan(
            rendered=rendered,
            assignment=(rendered if assignment_argument is None else self._render_field_call((assignment_argument,))),
            arguments=arguments,
            default_factory=None,
        )

    def _get_field_render_plan(self) -> _PydanticFieldRenderPlan:
        """Build all Field() views from structured arguments in one pass."""
        if self.is_class_var:
            rendered = "" if self.default is UNDEFINED else represent_python_value(self.default)
            return _PydanticFieldRenderPlan(
                rendered=rendered,
                assignment=None,
                arguments=(),
                default_factory=None,
            )

        data, default_factory = self._get_field_data_and_default_factory()
        field_arguments = sorted(f"{k}={represent_python_value(v)}" for k, v in data.items() if v is not None)

        if not field_arguments and not default_factory:
            if self.nullable and self.required and not self.use_default_with_required:
                return self._get_single_argument_field_render_plan("...")
            return _PydanticFieldRenderPlan(
                rendered="",
                assignment=None,
                arguments=(),
                default_factory=None,
            )

        if default_factory:
            field_arguments = [f"default_factory={default_factory}", *field_arguments]

        has_default_argument = False
        if self.use_annotated:
            field_arguments = self._process_annotated_field_arguments(field_arguments)
        elif (
            self.required
            and not self.use_default_with_required
            and not default_factory
            and not self.extras.get("validate_default")
        ):
            field_arguments = ["...", *field_arguments]
        elif (
            not default_factory
            and (default_repr := self.represented_default)
            and not self.should_strip_default_none(keep_optional=True)
        ):
            field_arguments = [default_repr, *field_arguments]

            has_default_argument = True

        arguments = tuple(field_arguments)
        rendered_arguments = ", ".join(arguments)
        rendered = f"Field({rendered_arguments})"
        assignment = (
            f"Field(default={rendered_arguments})" if self.use_default_kwarg and has_default_argument else rendered
        )
        return _PydanticFieldRenderPlan(
            rendered=rendered,
            assignment=assignment,
            arguments=arguments,
            default_factory=default_factory,
        )

    def __str__(self) -> str:
        """Return Field() call with all constraints and metadata."""
        return self._get_field_render_plan().rendered

    @property
    def is_class_var(self) -> bool:
        """Check if this field is a ClassVar."""
        return self.extras.get("x-is-classvar") is True

    @property
    def type_hint(self) -> str:
        """Get the type hint including ClassVar if applicable."""
        if self.is_class_var:
            return f"ClassVar[{super().type_hint}]"
        return super().type_hint

    @property
    def annotated(self) -> str | None:
        """Get the Annotated type hint if use_annotated is enabled."""
        if not self.use_annotated or self.is_class_var:
            return None
        if not (field := str(self)):
            return None
        return f"Annotated[{self.type_hint}, {field}]"

    @property
    def imports(self) -> tuple[Import, ...]:
        """Get all required imports including Field if needed.

        Computes _has_field_statement() once and derives needs_annotated from it:
        str(self) is non-empty exactly when _has_field_statement() is true (both
        come from the same Field() data), so this matches bool(self.annotated)
        without rebuilding the type hint and Field() string.
        """
        has_field_statement = self._has_field_statement()
        base_imports = self._collect_field_imports(
            needs_annotated=self.use_annotated and not self.is_class_var and has_field_statement
        )
        if has_field_statement:
            return chain_as_tuple(base_imports, (IMPORT_FIELD,))
        return base_imports


class BaseModelBase(DataModel, ABC):
    """Abstract base class for Pydantic BaseModel implementations."""

    REQUIRES_RUNTIME_IMPORTS_WITH_RUFF_CHECK: ClassVar[bool] = True

    def __init__(  # noqa: PLR0913
        self,
        *,
        reference: Reference,
        fields: list[DataModelFieldBase],
        decorators: list[str] | None = None,
        base_classes: list[Reference] | None = None,
        custom_base_class: str | list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, Any] | None = None,
        path: Path | None = None,
        description: str | None = None,
        default: Any = UNDEFINED,
        nullable: bool = False,
        keyword_only: bool = False,
        treat_dot_as_module: bool | None = None,
    ) -> None:
        """Initialize the BaseModel with fields and configuration."""
        methods: list[str] = [field.method for field in fields if field.method]

        super().__init__(
            fields=fields,
            reference=reference,
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
            treat_dot_as_module=treat_dot_as_module,
        )

    @cached_property
    def template_file_path(self) -> Path:
        """Get the template file path with backward compatibility support."""
        # This property is for Backward compatibility
        # Current version supports '{custom_template_dir}/BaseModel.jinja'
        # But, Future version will support only '{custom_template_dir}/pydantic/BaseModel.jinja'
        if self._custom_template_dir is not None:
            custom_template_file_path = self._custom_template_dir / Path(self.TEMPLATE_FILE_PATH).name
            _remember_custom_template_dependency(self._custom_template_dir, custom_template_file_path)
            if cached_path_exists(custom_template_file_path):
                return custom_template_file_path.resolve()
        return super().template_file_path


_rebuild_model_with_datamodel_namespace(DataModelField)
