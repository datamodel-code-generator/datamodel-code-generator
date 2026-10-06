"""Positive calls that reach the recursive aliases through a function before any alias import."""

from __future__ import annotations

from types import MappingProxyType

from datamodel_code_generator._runtime.model_codecs.wire import freeze_wire

freeze_wire({"name": "A", "tag": None, "items": [1, {"nested": True}]})
freeze_wire(MappingProxyType({"items": (1, MappingProxyType({"nested": True}))}))
