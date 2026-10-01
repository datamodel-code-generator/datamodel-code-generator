"""Bind generated models in client packages: field identities, defaults, and edits a formatter makes to the models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.client_bindings import CASES, client_binding_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings"


@pytest.mark.parametrize("case", list(json.loads(CASES.read_text(encoding="utf-8"))))
def test_client_model_bindings(case: str, tmp_path: Path) -> None:
    """Ship every model with bindings that match its declarations, and drop the bindings an edited model breaks."""
    assert_output(client_binding_report(case, tmp_path), EXPECTED / f"{case}.txt")
