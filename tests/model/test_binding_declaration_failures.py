"""Reject inconsistent accepted-attempt identities and final declaration expectations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_declaration_failures import declaration_failure

DATA = Path(__file__).parents[1] / "data"


@pytest.mark.parametrize(
    "case",
    [
        "foreign-attempt",
        "duplicate-consumer-slot",
        "nested-metadata",
        "absent-field",
        "absent-tag-model",
        "partitioned-model",
    ],
)
def test_inconsistent_final_declaration(case: str) -> None:
    """Use real generated fields to reject mixed attempts and unsupported declaration contracts."""
    source = "field-ownership.json" if case == "partitioned-model" else "session-final-corruption.json"
    actual = declaration_failure(DATA / "generation_platform/binding" / source, case)
    assert_output(actual, DATA / "expected/main/generation_platform/binding/declaration-failures" / f"{case}.txt")
