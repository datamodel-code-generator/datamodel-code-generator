"""Keep generated client dependencies, public files and copied-runtime costs visible per capability."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.api_generation.support.client_publication import PROFILES, capability_arguments, client_capability_report
from tests.conftest import assert_output
from tests.main.conftest import run_main_with_args

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/capabilities"


@pytest.mark.parametrize("profile", PROFILES)
def test_client_capabilities(profile: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Generate each security/helper/backend profile from the command line and compare its publication report."""
    run_main_with_args(capability_arguments(profile, tmp_path))
    assert_output(client_capability_report(profile, tmp_path, capsys.readouterr().err), EXPECTED / f"{profile}.txt")
