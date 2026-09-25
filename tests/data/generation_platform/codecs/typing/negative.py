"""Deliberately invalid wire typing cases; every error is checked by code and line."""

from types import MappingProxyType
from datamodel_code_generator._runtime.model_codecs.media import encode_json
from datamodel_code_generator._runtime.model_codecs.parameters import ParameterPlan
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue, WireValue, freeze_wire

unknown: JSONValue = object()
keys: JSONValue = {1: "invalid key"}
nested: JSONValue = {"items": [object()]}
mutable: WireValue = [1, 2]
wire_keys: WireValue = MappingProxyType({1: None})
freeze_wire(object())
encode_json(b"{}")
ParameterPlan(location="body", name="value")
