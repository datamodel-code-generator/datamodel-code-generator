"""Reject incomplete or foreign observations after actual model generation."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_record_failures import record_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding/record-failures"


@pytest.mark.parametrize("case", json.loads((SOURCE / "record-failures.json").read_text()), ids=itemgetter("id"))
def test_corrupted_final_record(case: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unsupported contracts unavailable without retaining the real parser or model graph."""
    actual = record_failure((SOURCE / case["source"]).resolve(), case, monkeypatch)
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / f"{case['id']}.txt")
