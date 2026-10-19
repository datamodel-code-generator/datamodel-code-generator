"""Pydantic v2 model generator.

Provides BaseModel, RootModel, and DataModelField for generating
Pydantic v2 compatible data models with ConfigDict support.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Dict, Optional  # noqa: UP035

from pydantic import BaseModel as _BaseModel
from pydantic import ConfigDict as _PydanticConfigDict
from typing_extensions import override

from datamodel_code_generator.enums import UnionMode
from datamodel_code_generator.model.base import declare_resolve_reference_action_capabilities

from .base_model import BaseModel, DataModelField
from .root_model import RootModel
from .root_model_type_alias import RootModelTypeAlias
from .types import DataTypeManager

if TYPE_CHECKING:
    from collections.abc import Iterable


def dump_resolve_reference_action(class_names: Iterable[str]) -> str:
    """Generate model_rebuild() calls for Pydantic v2 models."""
    return "\n".join(f"{class_name}.model_rebuild()" for class_name in class_names)


declare_resolve_reference_action_capabilities(
    dump_resolve_reference_action,
    filter_forward_references=True,
    generated_formatter_safe=True,
)


class ConfigDict(_BaseModel):
    """Pydantic v2 model_config options."""

    model_config = _PydanticConfigDict(defer_build=True)

    extra: Optional[str] = None  # noqa: UP045
    title: Optional[str] = None  # noqa: UP045
    populate_by_name: Optional[bool] = None  # noqa: UP045  # deprecated in v2.11+
    validate_by_name: Optional[bool] = None  # noqa: UP045  # v2.11+
    validate_by_alias: Optional[bool] = None  # noqa: UP045  # v2.11+
    alias_generator: Optional[str] = None  # noqa: UP045
    allow_extra_fields: Optional[bool] = None  # noqa: UP045
    extra_fields: Optional[str] = None  # noqa: UP045
    from_attributes: Optional[bool] = None  # noqa: UP045
    frozen: Optional[bool] = None  # noqa: UP045
    arbitrary_types_allowed: Optional[bool] = None  # noqa: UP045
    protected_namespaces: Optional[tuple[str, ...]] = None  # noqa: UP045
    regex_engine: Optional[str] = None  # noqa: UP045
    use_enum_values: Optional[bool] = None  # noqa: UP045
    coerce_numbers_to_str: Optional[bool] = None  # noqa: UP045
    use_attribute_docstrings: Optional[bool] = None  # noqa: UP045
    json_schema_extra: Optional[Dict[str, Any]] = None  # noqa: UP006, UP045

    @override
    def dict(self, **kwargs: Any) -> dict[str, Any]:  # ty: ignore[invalid-type-form]
        """Return dict for templates."""
        return self.model_dump(**kwargs)


__all__ = [
    "BaseModel",
    "DataModelField",
    "DataTypeManager",
    "RootModel",
    "RootModelTypeAlias",
    "UnionMode",
    "dump_resolve_reference_action",
]
