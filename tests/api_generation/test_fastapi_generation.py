"""Render FastAPI server targets: names, routes, native or adapter boundaries, and the package files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_output
from tests.data.python.fastapi_generation import fastapi_config_report, fastapi_render_report

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/fastapi"


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "parameters",
        "bodies",
        "request",
        "responses",
        "names",
        "types",
        "adapters",
        "methods",
        "native",
        "native-settings",
        "native-readonly",
        "native-custom",
        "native-generator",
        "unbound",
        "single",
        "route-errors",
        "name-errors",
        "group-errors",
        "repeat-32",
        "media-errors",
        "selector-errors",
        "primary-errors",
        "backend-errors",
        "codec-errors",
        "unbound-body",
    ],
)
def test_fastapi_render(case: str, tmp_path: Path) -> None:
    """Plan each operation's parameters, body, and responses, and render the package they need."""
    assert_output(fastapi_render_report(case, tmp_path), EXPECTED / f"{case}.txt")


@pytest.mark.parametrize(
    "case",
    ["defaults", "values", "invalid-values", "invalid-shapes", "invalid-mappings", "toml-values", "toml-errors"],
)
def test_fastapi_config(case: str, tmp_path: Path) -> None:
    """Construct or load the server settings, freezing their mappings and reporting every invalid value."""
    assert_output(fastapi_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")
