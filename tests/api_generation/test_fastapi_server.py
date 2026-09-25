"""Serve generated FastAPI packages over HTTP: parameters, bodies, results, and handler checks."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from tests.conftest import assert_output
from tests.data.python.fastapi_server import fastapi_server_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/fastapi/servers"


@pytest.mark.parametrize("case", ["pets", "parameters", "bodies", "results", "security"])
def test_fastapi_server(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Build the router from handwritten handlers and exchange requests with the generated endpoints."""
    assert_output(fastapi_server_report(case, tmp_path, monkeypatch), EXPECTED / f"{case}.txt")
