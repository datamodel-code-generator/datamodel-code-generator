"""Retain unknown backend settings as opaque facts after actual builtin emission."""

from __future__ import annotations

import json
from operator import itemgetter
from pathlib import Path

import pytest

from datamodel_code_generator import DataModelType
from datamodel_code_generator.model.binding_policies import constructor_policy
from tests.conftest import assert_output
from tests.data.python.binding_backend_failures import backend_failure

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform/binding"
EXPECTED = DATA / "expected/main/generation_platform/binding"


@pytest.mark.parametrize("case", json.loads((SOURCE / "backend-failures.json").read_text()), ids=itemgetter("id"))
@pytest.mark.parametrize("policies", [False, True])
def test_backend_fact_failure(case: dict[str, str], *, policies: bool) -> None:
    """Reject inconsistent extra values and avoid guessing unknown constructor policies."""
    actual = backend_failure(
        SOURCE / "model-facts.json",
        DataModelType(case["backend"]),
        case["id"],
        constructor_policy=constructor_policy if policies else None,
    )
    expected = EXPECTED / ("backend-failures" if policies else "backend-raw-failures")
    assert_output(json.dumps(actual, indent=2) + "\n", expected / f"{case['id']}.txt")
