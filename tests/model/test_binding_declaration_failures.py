"""Reject inconsistent accepted-attempt identities and final declaration expectations."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.binding_declaration_failures import declaration_failure

DATA = Path(__file__).parents[1] / "data"


@pytest.mark.parametrize("case", ["foreign-attempt", "duplicate-consumer-slot", "nested-metadata"])
def test_inconsistent_final_declaration(case: str) -> None:
    """Use real generated fields to reject mixed attempts and unsupported declaration contracts."""
    actual = declaration_failure(DATA / "generation_platform/binding/session-final-corruption.json", case)
    assert_output(actual, DATA / "expected/main/generation_platform/binding/declaration-failures" / f"{case}.txt")
