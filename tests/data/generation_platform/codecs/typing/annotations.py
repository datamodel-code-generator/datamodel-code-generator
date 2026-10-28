"""Resolve the recursive JSON aliases through private import aliases in another module."""

from __future__ import annotations
from datamodel_code_generator._runtime.model_codecs.media import JSONScalar as _JSONScalar
from datamodel_code_generator._runtime.model_codecs.media import JSONValue as _JSONValue
from datamodel_code_generator._runtime.model_codecs.media import json_bytes


def encoded(value: _JSONValue, scalar: _JSONScalar) -> bytes:
    """Carry both recursive aliases across a public annotation boundary."""
    return json_bytes([value, scalar])
