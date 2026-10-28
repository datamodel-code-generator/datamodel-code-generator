"""Positive calls that reach the recursive aliases through a function before any alias import."""

from __future__ import annotations

from datamodel_code_generator._runtime.model_codecs.media import json_value, plain

json_value('{"name": "A", "tag": null, "items": [1, {"nested": true}]}')
plain({"items": (1, {"nested": True})})
