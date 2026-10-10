"""Bind generated models in client packages: field identities, defaults, and edits made to the models."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from datamodel_code_generator import GenerateConfig, generate
from tests.conftest import assert_exact_directory_content, assert_output, write_generated_modules
from tests.data.python.client_bindings import (
    CASES,
    CLIENT,
    DATA,
    MODEL,
    _document,
    binding_model_options,
    client_binding_report,
    client_binding_rewrite_report,
)
from tests.data.python.client_generation import render_client
from tests.data.python.fastapi_generation import in_directory

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client/bindings"
BINDING_CASES = json.loads(CASES.read_text(encoding="utf-8"))
PARITY_CASES = json.loads((DATA / "generation_platform/binding/capture-parity.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", list(BINDING_CASES))
def test_client_model_bindings(case: str, tmp_path: Path) -> None:
    """Report the models and codecs a package ships, and what each formatter edit of the models changes.

    The models each package ships equal the models ordinary generation writes for the same options. An edit the
    bindings cannot project drops the field's binding. Cases marked "pins" keep current behaviour that #4299 (binding
    diagnostics never reach a package) and #4300 (conflicting allOf base order) will change.
    """
    report, packages = client_binding_report(case, tmp_path)
    assert_output(report, EXPECTED / f"{case}.txt")
    for index, (models, ordinary) in enumerate(packages):
        write_generated_modules(captured := tmp_path / "captured" / str(index), models)
        assert_exact_directory_content(captured, ordinary)


@pytest.mark.parametrize(
    ("case", "backend", "variant", "target"),
    [
        (name, backend, variant, target)
        for name in PARITY_CASES
        for backend in BINDING_CASES[name].get("backends", ["pydantic_v2.BaseModel"])
        for variant in BINDING_CASES[name].get("variants", {"default": {}})
        for target in ("client", "fastapi")
        if target == "client" or backend in {"pydantic_v2.BaseModel", "pydantic_v2.dataclass"}
    ],
)
def test_capture_preserves_ordinary_model_bytes(
    case: str, backend: str, variant: str, target: str, tmp_path: Path
) -> None:
    """Compare ordinary models with captured models for each target's supported backends."""
    settings = BINDING_CASES[case]
    source = _document(DATA / settings["source"], tmp_path, settings)
    options = binding_model_options(
        {
            "input_file_type": "openapi",
            "target_python_version": "3.11",
            "openapi_scopes": ["schemas", "api"],
            "output_model_type": backend,
            "disable_timestamp": True,
            **MODEL,
            **settings.get("model", {}),
            **settings.get("backend_model", {}).get(backend, {}),
            **settings.get("variants", {"default": {}})[variant],
        },
        tmp_path,
    )
    models = settings.get("models", "models.py")
    ordinary = tmp_path / "ordinary"
    rendered = tmp_path / "rendered"
    generate(source, config=GenerateConfig(output=ordinary / models, **options))
    package = "client"
    if target == "client":
        _, modules = render_client(source, rendered, backend, options, CLIENT, models=models)
    else:
        package = "server"
        rendered.mkdir()
        with in_directory(rendered):
            modules = generate(
                source,
                **options,
                generate_server="fastapi",
                server_package="server",
                server_model_package=Path(models).stem,
            )
    write_generated_modules(
        captured := tmp_path / "captured", {parts: text for parts, text in modules.items() if parts[0] != package}
    )
    assert_exact_directory_content(captured, ordinary)


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
