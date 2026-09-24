"""Positive calls that reach the recursive aliases through a function before any alias import."""

from types import MappingProxyType
from datamodel_code_generator._runtime.model_codecs.wire import presence_of

presence_of({"name": "A", "tag": None, "items": [1, {"nested": True}]})
presence_of(MappingProxyType({"items": (1, MappingProxyType({"nested": True}))}))
