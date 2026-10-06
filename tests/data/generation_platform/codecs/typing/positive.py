"""Positive wire typing cases, checked with both pinned checkers."""

from __future__ import annotations

from decimal import Decimal
from types import MappingProxyType

from typing_extensions import assert_type

from datamodel_code_generator._runtime.model_codecs.media import decode_json, encode_json
from datamodel_code_generator._runtime.model_codecs.parameters import (
    EncodedParameterContribution,
    ParameterLocation,
    ParameterPlan,
    RawParameters,
    decode_parameters,
    encode_parameters,
)
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset
from datamodel_code_generator._runtime.model_codecs.wire import (
    JSONValue,
    WireValue,
    freeze_wire,
    thaw_wire,
)

value: JSONValue = {"items": [1, 2.5, Decimal("0.1"), None, True, "text", ("tuple",)], "empty": {}}
wire: WireValue = MappingProxyType({"items": (1, None), "nested": MappingProxyType({"flag": False})})
assert_type(freeze_wire(value), WireValue)
assert_type(thaw_wire(wire), JSONValue)
assert_type(decode_json(b'{"a": 1}'), WireValue)
assert_type(encode_json(value, ascii_only=True), bytes)
plans = (ParameterPlan(location="query", name="limit", style="form", kind="integer"),)
decoded = decode_parameters(plans, RawParameters(query=b"limit=1"))
assert_type(decoded, dict[tuple[ParameterLocation, str], WireValue | Unset])
missing: WireValue | Unset = UNSET
assert_type(encode_parameters(plans, {("query", "limit"): missing}), tuple[EncodedParameterContribution, ...])
