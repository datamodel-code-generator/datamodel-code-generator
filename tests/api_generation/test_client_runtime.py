"""Call generated clients through HTTPX2: parameters, bodies, responses, headers, options, and failures."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("httpx2")

from tests.conftest import assert_output
from tests.data.python.client_scenarios import SCENARIOS, client_runtime_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/runtime"


@pytest.mark.parametrize("case", list(SCENARIOS))
def test_client_runtime(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate each client and exercise real TLS exchanges and injected failures in both execution modes."""
    isolated = {"http_proxy", "https_proxy", "all_proxy", "no_proxy", "ssl_cert_file", "ssl_cert_dir", "request_method"}
    for key in tuple(os.environ):
        if key.casefold() in isolated:
            monkeypatch.delenv(key)
    monkeypatch.setenv("NO_PROXY", "*")
    assert_output(client_runtime_report(case, tmp_path), EXPECTED / f"{case}.txt")
