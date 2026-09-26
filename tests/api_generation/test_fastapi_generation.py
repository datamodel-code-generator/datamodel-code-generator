"""Render FastAPI server targets: names, routes, native or adapter boundaries, and the package files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.fastapi_acceptance import fastapi_checkout_report, fastapi_scope_report
from tests.data.python.fastapi_generation import (
    fastapi_api_report,
    fastapi_config_report,
    fastapi_render,
    fastapi_scenario_report,
)

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
        "standalone",
        "encoding",
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
        "argument-errors",
        "security",
        "security-errors",
        "info",
        "callbacks",
        "scheme-names",
        "services",
        "empty",
        "hooks",
        "hook-file",
        "hook-invalid",
        "hook-unhashable",
        "hook-untyped",
        "hook-collide",
        "hook-failing",
        "hook-not-callable",
        "hook-broken-file",
        "templates",
        "template-roles",
        "template-invalid",
        "template-not-toml",
        "template-not-array",
        "template-not-tables",
        "template-conflict-builtin",
        "template-conflict-extras",
        "template-conflict-runtime",
        "template-bad-json",
        "template-missing",
    ],
)
def test_fastapi_render(case: str, tmp_path: Path) -> None:
    """Plan each operation's parameters, body, and responses, and render the package they need."""
    report, rendered = fastapi_render(case, tmp_path)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / case / backend)


@pytest.mark.parametrize(
    "case",
    [
        "update-initial",
        "update-invalid",
        "update-groups",
        "update-docs",
        "update-shared",
        "update-layout",
        "update-templates",
        "update-selection",
        "update-runtime",
    ],
)
def test_fastapi_regeneration(case: str, tmp_path: Path) -> None:
    """Regenerate after spec, setting, and runtime changes, updating only the groups a partial update names."""
    assert_output(fastapi_scenario_report(case, tmp_path), EXPECTED / "scenarios" / f"{case}.txt")


@pytest.mark.parametrize(
    "case",
    ["defaults", "values", "invalid-values", "invalid-shapes", "invalid-mappings"],
)
def test_fastapi_config(case: str, tmp_path: Path) -> None:
    """Construct the server settings, freezing their mappings and reporting every invalid value."""
    assert_output(fastapi_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")


def test_fastapi_api(tmp_path: Path) -> None:
    """Resolve the public annotations, render and generate twice, then refuse to render over an edited file."""
    assert_output(fastapi_api_report(tmp_path), EXPECTED / "api.txt")


def test_fastapi_scope(tmp_path: Path) -> None:
    """Stop unsupported settings before generating models, locking, publishing, or importing hooks and adapters."""
    assert_output(fastapi_scope_report(tmp_path), EXPECTED / "acceptance" / "scope.txt")


def test_fastapi_checkouts(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the same bytes from every checkout and directory, and nothing new after moving a checkout."""
    assert_output(fastapi_checkout_report(tmp_path, monkeypatch), EXPECTED / "acceptance" / "checkouts.txt")
