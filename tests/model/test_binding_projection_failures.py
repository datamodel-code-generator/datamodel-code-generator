"""Corroborate rejected type producers before accepted-session assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_projection_failures import rejected_projection

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("case", json.loads((SOURCE / "projection-corruptions.json").read_text()))
def test_corrupted_type_producer(case: str) -> None:
    """Keep fixed ordinary output and reject unsupported or contradictory final type state."""
    body, result = rejected_projection(SOURCE / "model-facts.json", case)
    assert_output(body, EXPECTED / "model-facts-PydanticV2BaseModel-False.py")
    assert_output(json.dumps(result, indent=2) + "\n", EXPECTED / "projection-failures" / f"{case}.txt")
