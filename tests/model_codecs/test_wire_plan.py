"""Generate server packages from wire plan fixtures, pinning their native plans or reporting each refused rule."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_generation.support.model_codec_plans import wire_plan_report
from tests.conftest import assert_generated_modules_output, assert_output

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/codecs/plan"


@pytest.mark.parametrize("name", ["oas30", "oas31", "oas32", "parameters", "directions-accepted"])
def test_wire_plan_bundles_validate(name: str, tmp_path: Path) -> None:
    """Pin the native application and route plans, validating instances with the generated models."""
    report, modules = wire_plan_report(name, tmp_path)
    assert_output(report, EXPECTED / f"{name}.txt")
    assert_generated_modules_output(modules, EXPECTED / name)


@pytest.mark.parametrize("name", ["dialects", "dialects32", "querystring-version"])
def test_wire_plan_rules(name: str, tmp_path: Path) -> None:
    """Report target-generation diagnostics for the fixed schema and parameter rule cases."""
    assert_output(wire_plan_report(name, tmp_path)[0], EXPECTED / f"{name}.txt")


def test_wire_plan_legacy_scope_declarations(tmp_path: Path) -> None:
    """Refuse parameter declarations that only the legacy paths scope admitted."""
    assert_output(wire_plan_report("legacy", tmp_path)[0], EXPECTED / "legacy.txt")
