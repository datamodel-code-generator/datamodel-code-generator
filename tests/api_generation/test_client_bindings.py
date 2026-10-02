"""Bind generated models in client packages: field identities, defaults, and edits made to the models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import GenerateConfig, generate
from datamodel_code_generator.fastapi import FastAPIConfig, render_fastapi
from tests.conftest import assert_output
from tests.data.python.client_bindings import (
    CASES,
    DATA,
    MODEL,
    _document,
    client_binding_report,
    client_binding_rewrite_report,
)

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings"
BINDING_CASES = json.loads(CASES.read_text(encoding="utf-8"))
PARITY_CASES = json.loads((DATA / "generation_platform/binding/capture-parity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", list(BINDING_CASES))
def test_client_model_bindings(case: str, tmp_path: Path) -> None:
    """Report the models and bindings a package ships, and what each formatter edit of the models changes.

    An edit the bindings cannot project drops the field's binding. Cases marked "pins" keep current behaviour that
    #4299 (binding diagnostics never reach a package) and #4300 (conflicting allOf base order) will change.
    """
    assert_output(client_binding_report(case, tmp_path), EXPECTED / f"{case}.txt")


@pytest.mark.parametrize(
    ("case", "backend", "variant"),
    [
        (name, backend, variant)
        for name in PARITY_CASES
        for backend in BINDING_CASES[name].get("backends", ["pydantic_v2.BaseModel"])
        for variant in BINDING_CASES[name].get("variants", {"default": {}})
    ],
)
def test_capture_preserves_ordinary_model_bytes(case: str, backend: str, variant: str, tmp_path: Path) -> None:
    """Compare all model bytes from public ordinary and captured generation with identical settings."""
    settings = BINDING_CASES[case]
    source = _document(DATA / settings["source"], tmp_path, settings)
    options = {
        "input_file_type": "openapi",
        "target_python_version": "3.11",
        "openapi_scopes": ["schemas", "api"],
        "output_model_type": backend,
        "disable_timestamp": True,
        **MODEL,
        **settings.get("model", {}),
        **settings.get("backend_model", {}).get(backend, {}),
        **settings.get("variants", {"default": {}})[variant],
    }
    models = settings.get("models", "models.py")
    ordinary = tmp_path / "ordinary"
    captured = tmp_path / "captured"
    generate(source, config=GenerateConfig(output=ordinary / models, **options))
    project = render_fastapi(
        source,
        model_config=GenerateConfig(output=captured / models, **options),
        config=FastAPIConfig(
            output=captured / "server",
            package="server",
            model_package="models",
            formatter_settings=tmp_path,
            formatters=(),
        ),
    )
    expected = {path.relative_to(ordinary): path.read_bytes() for path in ordinary.rglob("*.py")}
    actual = {
        artifact.path.relative_to(captured): artifact.content
        for artifact in project.artifacts
        if artifact.path.suffix == ".py" and not artifact.path.is_relative_to(captured / "server")
    }
    assert_output(f"{bool(expected) and actual == expected}\n", EXPECTED / "capture-parity.txt")


@pytest.mark.abnormal_path(
    "another process rewrites the staged models between their generation and their checks; "
    "the batch diagnostics are read from the render request because packages drop them (#4299)"
)
@pytest.mark.parametrize("case", [name for name, case in BINDING_CASES.items() if "rewrites" in case])
def test_client_model_bindings_rewritten(case: str, tmp_path: Path) -> None:
    """Report the binding diagnostics and package of models whose staged file changed after capture.

    The package still ships the old bindings without a diagnostic until #4299 is fixed.
    """
    assert_output(client_binding_rewrite_report(case, tmp_path), EXPECTED / "rewrites" / f"{case}.txt")
