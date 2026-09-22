"""Reject changed final type producers against the actual ordinarily emitted artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_final_failures import final_type_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding/final-type-failures"


@pytest.mark.parametrize("case", json.loads((SOURCE / "final-type-corruptions.json").read_text()))
def test_final_type_corruption_is_rejected(case: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep unsupported values distinct from changed syntax and release the real model graph."""
    actual = final_type_failure((SOURCE / "session-final-corruption.json").resolve(), case, monkeypatch)
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / f"{case}.txt")
