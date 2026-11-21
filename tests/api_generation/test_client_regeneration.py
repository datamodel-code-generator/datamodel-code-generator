"""Regenerate a client package from a changed API next to the application's own code."""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.api_generation.support.client_generation import client_regeneration_report
from tests.conftest import assert_output

if TYPE_CHECKING:
    from pathlib import Path


def test_client_regeneration(tmp_path: Path) -> None:
    """Keep user modules, overwrite an edited and a foreign file, leave stale modules, and follow the API."""
    assert_output(client_regeneration_report(tmp_path))
