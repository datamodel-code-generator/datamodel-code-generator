"""Resolve the recursive wire aliases through private import aliases in another module."""

from __future__ import annotations
from datamodel_code_generator._runtime.model_codecs.wire import JSONValue as _JSONValue
from datamodel_code_generator._runtime.model_codecs.wire import WireValue as _WireValue
from datamodel_code_generator._runtime.model_codecs.wire import freeze_wire


def snapshot(value: _JSONValue) -> _WireValue:
    """Carry both recursive aliases across a public annotation boundary."""
    return freeze_wire(value)
