"""Preserve inherited field contracts independently of accepted-session assembly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output
from tests.data.python.binding_inherited_inputs import inherited_fields

DATA = Path(__file__).parents[1] / "data"
EXPECTED = DATA / "expected/main/generation_platform/session-review/inherited-no-merge"


@pytest.mark.parametrize("backend", list(DataModelType))
@pytest.mark.parametrize(
    "case", ["shapes", "tuple", "distributed", "ref-sibling", "ref-chain", "property-names", "x-property-names"]
)
def test_inherited_field_origins(case: str, backend: DataModelType) -> None:
    """Match literal per-consumer origins and release both model and validated-schema graphs."""
    stem = "nested" if case == "shapes" else case
    expected = EXPECTED / f"{stem}-fields.txt"
    body, facts = inherited_fields(
        (DATA / f"generation_platform/binding/session-inherited-{case}.json").resolve(),
        backend,
        json.loads(expected.read_text()),
    )
    assert_output(body, EXPECTED / f"parser-{case}-{backend.name}.py")
    assert_output(json.dumps(facts.pop("origins"), indent=2) + "\n", expected)
    assert_output(json.dumps(facts, indent=2) + "\n", EXPECTED / "parser-release.txt")
