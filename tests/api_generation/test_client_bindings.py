"""Bind generated models in client packages: field identities, defaults, and edits made to the models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.client_bindings import CASES, client_binding_report, client_binding_rewrite_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings"
BINDING_CASES = json.loads(CASES.read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", list(BINDING_CASES))
def test_client_model_bindings(case: str, tmp_path: Path) -> None:
    """Ship every model with bindings that match its declarations, and drop the bindings an edited model breaks."""
    assert_output(client_binding_report(case, tmp_path), EXPECTED / f"{case}.txt")


@pytest.mark.abnormal_path("another process rewrites the staged models between their generation and their checks")
@pytest.mark.parametrize("case", [name for name, case in BINDING_CASES.items() if "rewrites" in case])
def test_client_model_bindings_rewritten(case: str, tmp_path: Path) -> None:
    """Report what a package ships when its staged models change after their bindings were captured."""
    assert_output(client_binding_rewrite_report(case, tmp_path), EXPECTED / "rewrites" / f"{case}.txt")
