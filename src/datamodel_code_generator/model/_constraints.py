"""Shared field-constraint models for output backends."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional, cast, get_args

from pydantic import Field

from datamodel_code_generator.model.base import ConstraintsBase
from datamodel_code_generator.types import FloatConstraint, UnionIntFloat

if TYPE_CHECKING:
    from pydantic.fields import FieldInfo

_LEGACY_MODULE = "datamodel_code_generator.model.pydantic_base"
# Keep the legacy public annotation while sharing validation and serialization.
_MULTIPLE_OF_FIELD = Field(None, alias="multipleOf")
cast("FieldInfo", _MULTIPLE_OF_FIELD).metadata.extend(get_args(FloatConstraint)[1:])


class Constraints(ConstraintsBase):
    """Pydantic field constraints (gt, ge, lt, le, regex, etc.)."""

    gt: Optional[UnionIntFloat] = Field(None, alias="exclusiveMinimum")
    ge: Optional[UnionIntFloat] = Field(None, alias="minimum")
    lt: Optional[UnionIntFloat] = Field(None, alias="exclusiveMaximum")
    le: Optional[UnionIntFloat] = Field(None, alias="maximum")
    multiple_of: Optional[float] = _MULTIPLE_OF_FIELD
    min_items: Optional[int] = Field(None, alias="minItems")
    max_items: Optional[int] = Field(None, alias="maxItems")
    min_length: Optional[int] = Field(None, alias="minLength")
    max_length: Optional[int] = Field(None, alias="maxLength")
    regex: Optional[str] = Field(None, alias="pattern")


class PatternConstraints(Constraints):
    regex: Optional[str] = Field(None, alias="regex")
    pattern: Optional[str] = Field(None, alias="pattern")


Constraints.model_rebuild()
PatternConstraints.model_rebuild()


Constraints.__module__ = _LEGACY_MODULE
PatternConstraints.__module__ = _LEGACY_MODULE
