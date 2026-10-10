"""Keep cyclic metadata diagnostics public even when YAML was loaded in memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_generation.support.client_generation import client_metadata_cycle_diagnostic_report
from tests.conftest import assert_output


@pytest.mark.parametrize(
    "backend",
    ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "dataclasses.dataclass", "typing.TypedDict", "msgspec.Struct"],
)
def test_metadata_cycle_diagnostic(backend: str, tmp_path: Path) -> None:
    """Report the exact cycle location and leave every publication destination empty."""
    expected = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings/metadata-cycle"
    assert_output(client_metadata_cycle_diagnostic_report(backend, tmp_path), expected / f"{backend}.txt")


def test_metadata_external_sequence_cycle(tmp_path: Path) -> None:
    """Report an external metadata sequence's cycle without publishing any generated files."""
    expected = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings/metadata-cycle"
    assert_output(
        client_metadata_cycle_diagnostic_report("pydantic_v2.BaseModel", tmp_path, external_sequence=True),
        expected / "external-sequence.txt",
    )
