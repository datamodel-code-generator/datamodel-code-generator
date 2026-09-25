"""Plan offline wire schemas and parameter codecs from real accepted OpenAPI generation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.model_codec_plans import wire_plan_report

DATA = Path(__file__).parents[1] / "data"
PLANS = DATA / "generation_platform/codecs/plan"
EXPECTED = DATA / "expected/main/generation_platform/codecs/plan"


@pytest.mark.parametrize("name", ["oas31", "oas30"])
def test_wire_plan_bundles_validate(name: str) -> None:
    """Normalize each document into offline resources that the runtime bundle validates directly."""
    assert_output(
        wire_plan_report((PLANS / f"{name}.yaml").resolve(), PLANS / f"{name}-instances.json"), EXPECTED / f"{name}.txt"
    )


@pytest.mark.parametrize("name", ["oas32", "dialects", "dialects32", "directions"])
def test_wire_plan_rules(name: str) -> None:
    """Plan OpenAPI 3.2 parameter forms and report every rule that needs an explicit adapter."""
    assert_output(wire_plan_report((PLANS / f"{name}.yaml").resolve()), EXPECTED / f"{name}.txt")


def test_wire_plan_legacy_scope_declarations() -> None:
    """Report parameter declarations that only the legacy paths scope admits."""
    assert_output(wire_plan_report((PLANS / "legacy.yaml").resolve(), legacy=True), EXPECTED / "legacy.txt")
