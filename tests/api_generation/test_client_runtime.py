"""Call generated clients through HTTPX2: parameters, bodies, responses, headers, options, and failures."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("httpx2")

from tests.conftest import assert_output
from tests.data.python.client_scenarios import SCENARIOS, client_runtime_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/runtime"


@pytest.mark.parametrize("case", list(SCENARIOS))
def test_client_runtime(case: str, tmp_path: Path) -> None:
    """Generate each client and exercise real TLS exchanges and injected failures in both execution modes."""
    assert_output(client_runtime_report(case, tmp_path), EXPECTED / f"{case}.txt")
