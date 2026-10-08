"""Lightweight output-owned field-name policies."""

from __future__ import annotations

from keyword import iskeyword
from typing import Final

from datamodel_code_generator.reference import FieldNameResolver

PYDANTIC_BASE_MODEL_ATTRIBUTES: Final = frozenset({
    "__abstractmethods__",
    "__annotations__",
    "__base__",
    "__bases__",
    "__basicsize__",
    "__call__",
    "__class__",
    "__class_getitem__",
    "__copy__",
    "__dataclass_transform__",
    "__deepcopy__",
    "__delattr__",
    "__dict__",
    "__dictoffset__",
    "__dir__",
    "__doc__",
    "__eq__",
    "__fields__",
    "__fields_set__",
    "__flags__",
    "__format__",
    "__ge__",
    "__get_pydantic_core_schema__",
    "__get_pydantic_json_schema__",
    "__getattr__",
    "__getattribute__",
    "__getstate__",
    "__gt__",
    "__hash__",
    "__init__",
    "__init_subclass__",
    "__instancecheck__",
    "__itemsize__",
    "__iter__",
    "__le__",
    "__lt__",
    "__module__",
    "__mro__",
    "__name__",
    "__ne__",
    "__new__",
    "__or__",
    "__prepare__",
    "__pretty__",
    "__pydantic_complete__",
    "__pydantic_core_schema__",
    "__pydantic_decorators__",
    "__pydantic_extra__",
    "__pydantic_fields_complete__",
    "__pydantic_fields_set__",
    "__pydantic_init_subclass__",
    "__pydantic_on_complete__",
    "__pydantic_parent_namespace__",
    "__pydantic_private__",
    "__pydantic_root_model__",
    "__pydantic_serializer__",
    "__pydantic_validator__",
    "__qualname__",
    "__reduce__",
    "__reduce_ex__",
    "__replace__",
    "__repr__",
    "__repr_args__",
    "__repr_name__",
    "__repr_recursion__",
    "__repr_str__",
    "__rich_repr__",
    "__ror__",
    "__setattr__",
    "__setstate__",
    "__sizeof__",
    "__slots__",
    "__str__",
    "__subclasscheck__",
    "__subclasses__",
    "__subclasshook__",
    "__text_signature__",
    "__weakrefoffset__",
    "_abc_caches_clear",
    "_abc_impl",
    "_abc_registry_clear",
    "_calculate_keys",
    "_collect_bases_data",
    "_copy_and_set_values",
    "_dump_registry",
    "_get_value",
    "_iter",
    "_setattr_handler",
    "construct",
    "copy",
    "dict",
    "from_orm",
    "json",
    "model_computed_fields",
    "model_config",
    "model_construct",
    "model_copy",
    "model_dump",
    "model_dump_json",
    "model_extra",
    "model_fields",
    "model_fields_set",
    "model_json_schema",
    "model_parametrized_name",
    "model_post_init",
    "model_rebuild",
    "model_validate",
    "model_validate_json",
    "model_validate_strings",
    "mro",
    "parse_file",
    "parse_obj",
    "parse_raw",
    "register",
    "schema",
    "schema_json",
    "update_forward_refs",
    "validate",
})
"""Names ``hasattr(pydantic.BaseModel, name)`` accepts on the newest Pydantic under every supported Python."""


class PydanticFieldNameResolver(FieldNameResolver):
    """Resolve field names according to Pydantic BaseModel ownership rules."""

    def get_valid_name(
        self,
        name: str,
        excludes: set[str] | None = None,
        ignore_snake_case_field: bool = False,  # noqa: FBT001, FBT002
        upper_camel: bool = False,  # noqa: FBT001, FBT002
    ) -> str:
        """Convert a name to a valid Pydantic field name."""
        if (
            fast_name := self._get_valid_name_fast_path(
                name,
                excludes,
                ignore_snake_case_field,
                upper_camel,
            )
        ) is not None:
            return fast_name
        return super().get_valid_name(name, excludes, ignore_snake_case_field, upper_camel)

    def _get_valid_name_fast_path(
        self,
        name: str,
        excludes: set[str] | None,
        ignore_snake_case_field: bool,  # noqa: FBT001
        upper_camel: bool,  # noqa: FBT001
    ) -> str | None:
        """Skip normalization for ordinary Pydantic field names."""
        if type(self) is not PydanticFieldNameResolver:
            return None
        if not name.isascii() or not name.isidentifier() or name.startswith("_"):
            return None
        if iskeyword(name) or self.capitalise_enum_members or upper_camel:
            return None
        if self.snake_case_field and not ignore_snake_case_field:
            return None
        if excludes and name in excludes:
            return None
        return name if self._validate_field_name(name) else None

    def _validate_field_name(self, field_name: str) -> bool:  # noqa: PLR6301
        """Check whether a field would shadow a Pydantic BaseModel attribute."""
        return field_name not in PYDANTIC_BASE_MODEL_ATTRIBUTES


class MsgspecFieldNameResolver(FieldNameResolver):
    """Avoid shadowing the output-owned msgspec ``field`` import."""

    FIELD_ASSIGNMENT_HELPER = "field"

    def _validate_field_name(self, field_name: str) -> bool:  # noqa: PLR6301
        return field_name != "field"


PydanticFieldNameResolver.__module__ = "datamodel_code_generator.reference"
MsgspecFieldNameResolver.__module__ = "datamodel_code_generator.reference"
