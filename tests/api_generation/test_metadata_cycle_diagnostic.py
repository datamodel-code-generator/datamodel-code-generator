"""Keep cyclic metadata diagnostics public even when YAML was loaded in memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.client_generation import client_metadata_cycle_diagnostic_report


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_metadata_cycle_diagnostic(backend: str, tmp_path: Path) -> None:
    """Report the exact cycle location and leave every publication destination empty."""
    expected = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings/metadata-cycle"
    assert_output(client_metadata_cycle_diagnostic_report(backend, tmp_path), expected / f"{backend}.txt")
