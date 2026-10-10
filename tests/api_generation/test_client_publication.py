"""Keep generated client public files and copied-runtime costs visible per capability."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_generation.support.client_publication import PROFILES, client_capability_report
from tests.conftest import assert_output

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/capabilities"


@pytest.mark.parametrize("profile", PROFILES)
def test_client_capabilities(profile: str, tmp_path: Path) -> None:
    """Render each security/helper/backend profile and compare the complete publication report."""
    assert_output(client_capability_report(profile, tmp_path), EXPECTED / f"{profile}.txt")
