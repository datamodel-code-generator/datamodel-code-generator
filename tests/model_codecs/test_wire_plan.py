"""Generate client packages from wire plan fixtures, pinning their schema bundles or reporting each refused rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.model_codec_plans import wire_plan_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/codecs/plan"


@pytest.mark.parametrize("name", ["oas30", "oas31", "oas32", "parameters", "directions-accepted"])
def test_wire_plan_bundles_validate(name: str, tmp_path: Path) -> None:
    """Bundle each document's offline resources into the generated bindings, whose bundles validate instances."""
    report, modules = wire_plan_report(name, tmp_path)
    assert_output(report, EXPECTED / f"{name}.txt")
    assert_generated_modules_output(modules, EXPECTED / name)


@pytest.mark.parametrize("name", ["dialects", "dialects32", "directions"])
def test_wire_plan_rules(name: str, tmp_path: Path) -> None:
    """Refuse to generate a client while any schema or parameter rule needs an explicit adapter."""
    assert_output(wire_plan_report(name, tmp_path)[0], EXPECTED / f"{name}.txt")


def test_wire_plan_legacy_scope_declarations(tmp_path: Path) -> None:
    """Refuse parameter declarations that only the legacy paths scope admitted."""
    assert_output(wire_plan_report("legacy", tmp_path)[0], EXPECTED / "legacy.txt")
