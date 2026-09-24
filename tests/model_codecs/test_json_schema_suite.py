"""Compare offline codec schema validation with the pinned JSON-Schema-Test-Suite corpus."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_suite import json_schema_suite_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/codecs"


def test_json_schema_suite_draft_2020_12() -> None:
    """Record every pinned-suite mismatch: asserted formats, upstream format limits, and adapter-only dialects."""
    if not (raw_path := os.environ.get("JSON_SCHEMA_TEST_SUITE_PATH")):
        pytest.skip("JSON_SCHEMA_TEST_SUITE_PATH is required for JSON-Schema-Test-Suite conformance")
    assert_output(json_schema_suite_report(Path(raw_path)), EXPECTED / "json-schema-suite.txt")
