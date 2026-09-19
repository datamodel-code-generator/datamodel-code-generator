"""Fail real generation when producer correspondence becomes internally inconsistent."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_provenance_failures import provenance_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding/provenance-failures"


@pytest.mark.parametrize("case", json.loads((SOURCE / "producer-failures.json").read_text()), ids=itemgetter("id"))
def test_inconsistent_producer_observation(case: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject missing, reordered or foreign observations and close the failed real attempt."""
    actual = provenance_failure((SOURCE / case["source"]).resolve(), case, monkeypatch)
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / f"{case['id']}.txt")
