"""Positive codec typing cases, checked with both pinned checkers."""

from __future__ import annotations

from typing_extensions import assert_type

from datamodel_code_generator._runtime.model_codecs.media import JSONValue, json_bytes, json_value
from datamodel_code_generator._runtime.model_codecs.parameter_reads import RawParameter, decode_parameter
from datamodel_code_generator._runtime.model_codecs.parameters import (
    ParameterLocation,
    ParameterPlan,
    pairs,
    querystring,
)
from datamodel_code_generator._runtime.model_codecs.unset import UNSET, Unset

value: JSONValue = {"items": [1, 2.5, None, True, "text", ["list"]], "empty": {}}
assert_type(json_value(b'{"a": 1}'), JSONValue)
assert_type(json_bytes(value, ascii_only=True), bytes)
plan = ParameterPlan(location="query", name="limit", style="form", kind="integer")
location: ParameterLocation = plan.location
decoded = decode_parameter(plan, RawParameter(location="query"))
assert_type(decoded, JSONValue | Unset)
assert_type(pairs(plan, 1), list[tuple[str | None, str]])
content = ParameterPlan(location="querystring", name="filter", content_media_type="application/json")
assert_type(querystring(content, value), str)
missing: JSONValue | Unset = UNSET
