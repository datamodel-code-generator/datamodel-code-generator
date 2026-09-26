"""Serve generated FastAPI packages over HTTP: parameters, bodies, results, and handler checks."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.fastapi_openapi import SCENARIOS, fastapi_openapi_report
from tests.data.python.fastapi_server import fastapi_server_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/fastapi/servers"


@pytest.mark.parametrize("case", ["pets", "parameters", "bodies", "results", "security", "customized"])
def test_fastapi_server(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate each server, then build its router from handwritten services and exchange requests with it."""
    report, packages = fastapi_server_report(case, tmp_path, monkeypatch)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in packages.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / case / backend)


@pytest.mark.parametrize("case", list(SCENARIOS))
def test_fastapi_openapi(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose the served OpenAPI document of generated packages in applications of every shape."""
    assert_output(fastapi_openapi_report(case, tmp_path, monkeypatch), EXPECTED.parent / "openapi" / f"{case}.txt")
