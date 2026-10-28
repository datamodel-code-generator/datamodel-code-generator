"""Independent native Pydantic types for numeric payload compatibility."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, TypeAlias

from pydantic import BaseModel, Field, TypeAdapter, ValidationError

if TYPE_CHECKING:
    from .models import SchemaCase

NATIVE_FLOAT_CONTRACT = json.loads(
    (Path(__file__).parents[2] / "data" / "payloads" / "native_float_multiple_contract.json").read_text()
)
NATIVE_FLOAT_ADAPTER = TypeAdapter(Annotated[float, Field(multiple_of=float(NATIVE_FLOAT_CONTRACT["native_multiple"]))])


FloatQuarter: TypeAlias = Annotated[float, Field(multiple_of=0.25)]
FloatHalf: TypeAlias = Annotated[float, Field(multiple_of=0.5)]


class NativeNumericFields(BaseModel):
    """Native counterpart of the schema-position sampling fixture."""

    default: FloatQuarter
    examples: Annotated[float, Field(multiple_of=2.5)]
    value: FloatHalf


class NativeNumericValue(BaseModel):
    """The intersection of multiples of one quarter and one half is multiples of one half."""

    value: FloatHalf


NATIVE_NUMERIC_SAMPLING_ADAPTERS: dict[str, TypeAdapter[Any]] = {
    name: TypeAdapter(annotation)
    for name, annotation in {
        "fractional": FloatQuarter,
        "decimal_fraction": Annotated[float, Field(multiple_of=0.3)],
        "negative": Annotated[float, Field(multiple_of=0.25, ge=-3, le=-0.25)],
        "draft4": Annotated[float, Field(multiple_of=0.25, gt=-0.5, lt=0.5)],
        "draft4_inclusive": Annotated[float, Field(multiple_of=0.25, ge=-0.5, le=0.5)],
        "integral_float_multiple": Annotated[float, Field(multiple_of=1.0, ge=-10, le=10)],
        "negative_large": Annotated[float, Field(multiple_of=0.25, ge=-2e100, le=-1e100)],
        "tiny_multiple": Annotated[float, Field(multiple_of=1e-300, ge=-1e-20, le=1e-20)],
        "schema_positions": NativeNumericFields,
        "array": Annotated[list[FloatQuarter], Field(min_length=1, max_length=3)],
        "allof": NativeNumericValue,
        "anyof": FloatQuarter | str,
        "negative_limit": Annotated[float, Field(multiple_of=0.25, le=-2.2471164185778946e307)],
        "negative_decimal_singleton": Annotated[float, Field(multiple_of=0.1, ge=-0.3, le=-0.3)],
    }.items()
}


def native_float_multiple_errors(case: SchemaCase, payload: Any) -> list[dict[str, Any]] | None:
    """Return expected native errors, or None when the case has no native contract."""
    if case.id != NATIVE_FLOAT_CONTRACT["case_id"]:
        return None
    field = NATIVE_FLOAT_CONTRACT["field"]
    if case.source_schema.get("properties", {}).get(field) != NATIVE_FLOAT_CONTRACT["source_fragment"]:
        msg = "Native float contract does not match the original source fragment"
        raise ValueError(msg)
    if field not in payload:
        return []
    try:
        NATIVE_FLOAT_ADAPTER.validate_python(payload[field])
    except ValidationError as exc:
        errors = exc.errors(include_url=False)
        for error in errors:
            if error["type"] != "multiple_of":
                msg = "Native float contract encountered an unrelated validation error"
                raise ValueError(msg) from exc
            error["loc"] = (field, *error["loc"])
        return errors
    return []


def pydantic_payload_result(adapter: TypeAdapter[Any], payload: Any) -> tuple[Any, list[dict[str, Any]]]:
    """Collect a native/generated runtime result or its complete validation errors."""
    try:
        return adapter.validate_python(payload), []
    except ValidationError as exc:
        return None, exc.errors(include_url=False)
