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

    gt: Optional[UnionIntFloat] = Field(None, alias="exclusiveMinimum")  # noqa: UP045
    ge: Optional[UnionIntFloat] = Field(None, alias="minimum")  # noqa: UP045
    lt: Optional[UnionIntFloat] = Field(None, alias="exclusiveMaximum")  # noqa: UP045
    le: Optional[UnionIntFloat] = Field(None, alias="maximum")  # noqa: UP045
    multiple_of: Optional[float] = _MULTIPLE_OF_FIELD  # noqa: UP045
    min_items: Optional[int] = Field(None, alias="minItems")  # noqa: UP045
    max_items: Optional[int] = Field(None, alias="maxItems")  # noqa: UP045
    min_length: Optional[int] = Field(None, alias="minLength")  # noqa: UP045
    max_length: Optional[int] = Field(None, alias="maxLength")  # noqa: UP045
    regex: Optional[str] = Field(None, alias="pattern")  # noqa: UP045


class PatternConstraints(Constraints):
    regex: Optional[str] = Field(None, alias="regex")  # noqa: UP045
    pattern: Optional[str] = Field(None, alias="pattern")  # noqa: UP045


Constraints.model_rebuild()
PatternConstraints.model_rebuild()


Constraints.__module__ = _LEGACY_MODULE
PatternConstraints.__module__ = _LEGACY_MODULE
