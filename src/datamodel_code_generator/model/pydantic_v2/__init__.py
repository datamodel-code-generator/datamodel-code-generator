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
from datamodel_code_generator.model.base import (
    declare_resolve_reference_action_capabilities as declare_resolve_reference_action_capabilities,
)

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
    """Pydantic v2 model_config options; ``validate_by_name`` and ``validate_by_alias`` exist from v2.11 on."""

    model_config = _PydanticConfigDict(defer_build=True)

    extra: Optional[str] = None
    title: Optional[str] = None
    populate_by_name: Optional[bool] = None
    validate_by_name: Optional[bool] = None
    validate_by_alias: Optional[bool] = None
    alias_generator: Optional[str] = None
    allow_extra_fields: Optional[bool] = None
    extra_fields: Optional[str] = None
    from_attributes: Optional[bool] = None
    frozen: Optional[bool] = None
    arbitrary_types_allowed: Optional[bool] = None
    protected_namespaces: Optional[tuple[str, ...]] = None
    regex_engine: Optional[str] = None
    use_enum_values: Optional[bool] = None
    coerce_numbers_to_str: Optional[bool] = None
    use_attribute_docstrings: Optional[bool] = None
    json_schema_extra: Optional[Dict[str, Any]] = None  # noqa: UP006

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
