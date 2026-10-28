"""Deliberately invalid codec typing cases; every error is checked by code and line."""

from __future__ import annotations

from types import MappingProxyType

from datamodel_code_generator._runtime.model_codecs.media import JSONValue, json_value
from datamodel_code_generator._runtime.model_codecs.parameter_reads import RawParameter, decode_parameter
from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
from datamodel_code_generator._runtime.model_codecs.unset import UNSET

unknown: JSONValue = object()  # error
keys: JSONValue = {1: "invalid key"}  # error
nested: JSONValue = {"items": [object()]}  # error
native_array: JSONValue = [1, 2]
frozen: JSONValue = MappingProxyType({"a": None})  # error
json_value(object())  # error
text: str | UNSET = decode_parameter(ParameterPlan(location="query", name="q"), RawParameter(location="query"))  # error
ParameterPlan(location="body", name="value")  # error
