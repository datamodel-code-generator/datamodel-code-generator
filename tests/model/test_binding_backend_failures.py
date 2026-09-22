"""Retain unknown backend settings as opaque facts after actual builtin emission."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from tests.conftest import assert_output
from tests.data.python.binding_backend_failures import backend_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding/backend-raw-failures"


@pytest.mark.parametrize("case", json.loads((SOURCE / "backend-failures.json").read_text()), ids=itemgetter("id"))
def test_backend_fact_failure(case: dict[str, str]) -> None:
    """Reject inconsistent extra values and avoid guessing unknown constructor policies."""
    actual = backend_failure(SOURCE / "model-facts.json", DataModelType(case["backend"]), case["id"])
    assert_output(json.dumps(actual, indent=2) + "\n", EXPECTED / f"{case['id']}.txt")
