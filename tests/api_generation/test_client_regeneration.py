"""Regenerate a client package from a changed API next to the application's own code."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import assert_output
from tests.data.python.client_generation import client_regeneration_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client"


def test_client_regeneration(tmp_path: Path) -> None:
    """Keep user modules, rewrite edited owned files, refuse an unmanaged file in the way, and follow the API."""
    assert_output(client_regeneration_report(tmp_path), EXPECTED / "regeneration.txt")
