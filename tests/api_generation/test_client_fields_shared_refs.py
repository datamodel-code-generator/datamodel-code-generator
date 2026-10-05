"""Generate client field arguments from a valid request body with repeated allOf ancestors."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import assert_output
from tests.data.python.client_fields_shared_refs import shared_fields_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/fields-shared-refs.txt"


def test_client_fields_shared_refs(tmp_path: Path) -> None:
    """Keep body and field signatures for the shared-ancestor model in both generated clients."""
    assert_output(shared_fields_report(tmp_path), EXPECTED)
