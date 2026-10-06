"""Deliberately invalid wire typing cases; every error is checked by code and line."""

from __future__ import annotations

from types import MappingProxyType

from datamodel_code_generator._runtime.model_codecs.media import encode_json
from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue, freeze_wire

unknown: JSONValue = object()  # error
keys: JSONValue = {1: "invalid key"}  # error
nested: JSONValue = {"items": [object()]}  # error
native_array: WireValue = [1, 2]
wire_keys: WireValue = MappingProxyType({1: None})  # error
freeze_wire(object())  # error
encode_json(b"{}")  # error
ParameterPlan(location="body", name="value")  # error
