"""Serve generated FastAPI packages over HTTP: parameters, bodies, forms, results, responses, and handler checks."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.fastapi_charsets import text_charsets
from tests.data.python.fastapi_openapi import SCENARIOS, fastapi_openapi_report
from tests.data.python.fastapi_server import fastapi_server_report
from tests.data.python.fastapi_upstream import fastapi_upstream_report
from tests.data.python.model_codec_builtin import builtin_codec_report, builtin_codec_startup_report

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs/pydantic"
CODEC_EXPECTED = DATA / "expected/main/generation_platform/codecs/pydantic"
EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/fastapi/servers"


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "unbound",
        "parameters",
        "kinds",
        "kinds-annotated",
        "kinds-decimal",
        "kinds-literal",
        "bodies",
        "results",
        "security",
        "customized",
        "variants",
        "variants-request-response",
        "variants-modular",
        "variants-reuse",
        "forms",
        "responses",
        "stale",
    ],
)
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


def test_fastapi_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Close uploaded files and end parse errors and anyio cancellations as FastAPI and Starlette do."""
    assert_output(fastapi_upstream_report(tmp_path, monkeypatch), EXPECTED / "uploads.txt")


def test_text_charsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the selected Content-Type for both decoding requests and encoding responses."""
    assert_output(text_charsets(tmp_path, monkeypatch), EXPECTED.parent.parent / "http_boundaries/charsets.txt")


@pytest.mark.parametrize(
    ("source", "cases"),
    [
        ("pets", "pets-basemodel"),
        ("pets", "pets-dataclass"),
        ("pets", "pets-aliases"),
        ("pets", "pets-noalias"),
        ("pets", "pets-noalias-forbid"),
        ("pets", "pets-generator"),
        ("pets", "pets-serialization"),
        ("pets", "pets-missing"),
        ("pets", "pets-strict"),
        ("pets", "pets-forbid"),
        ("pets", "pets-allow"),
        ("pets", "pets-custom"),
        ("pets", "pets-variants"),
        ("pets", "pets-schemas-scope"),
        ("pets", "pets-paths-scope"),
        ("shapes", "shapes-basemodel"),
        ("shapes", "shapes-dataclass"),
        ("shapes", "shapes-variants"),
        ("shapes", "shapes-annotated"),
        ("shapes", "shapes-choices"),
        ("loose", "loose-basemodel"),
        ("collisions", "collisions"),
        ("extras", "extras-basemodel"),
        ("extras", "extras-dataclass"),
        ("zoo", "zoo-collapsed"),
        ("choice", "choice"),
        ("ids", "ids"),
        ("containers", "containers-noalias"),
        ("types", "types-constrained"),
        ("types", "types-annotated"),
        ("legacy", "legacy"),
    ],
)
def test_pydantic_codecs(source: str, cases: str, tmp_path: Path) -> None:
    """Decode and encode native models through the generated FastAPI server."""
    assert_output(
        builtin_codec_report(CODECS / f"{source}.yaml", CODECS / f"{cases}.json", tmp_path, server=True),
        CODEC_EXPECTED / f"{cases}.txt",
    )


@pytest.mark.abnormal_path("generated models or bindings edited out of sync after generation")
@pytest.mark.parametrize("cases", ["pets-startup", "pets-startup-dataclass", "pets-startup-noalias"])
def test_pydantic_codec_startup(cases: str, tmp_path: Path) -> None:
    """Start native server apps after the retained generated model substitutions."""
    assert_output(
        builtin_codec_startup_report(CODECS / "pets.yaml", CODECS / f"{cases}.json", tmp_path, server=True),
        CODEC_EXPECTED / f"{cases}.txt",
    )
