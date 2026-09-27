"""Render client targets: resources, methods, parameters, media, responses, servers, and the package files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.client_generation import client_config_report, client_digest_report, client_render

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client"


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "pets-unpack",
        "media",
        "servers",
        "default-server",
        "implicit-server",
        "names",
        "name-errors",
        "method-collisions",
        "child-collision",
        "media-errors",
        "server-errors",
        "codec-errors",
        "reference-errors",
        "setting-errors",
        "empty",
        "querystring",
        "evolution",
        "validation-native-structural",
        "validation-ambiguous",
        "validation-adapters",
        "validation-adapters-schema",
        "validation-arguments",
        "validation-arguments-msgspec",
        "fields",
        "fields-structural",
        "fields-unpack",
        "fields-arguments",
        "fields-errors",
        "fields-cycle",
    ],
)
def test_client_render(case: str, tmp_path: Path) -> None:
    """Plan each operation's names, arguments, media, responses, and servers, and render the package they need."""
    report, rendered = client_render(case, tmp_path)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / case / backend)


def test_client_signature_style_digests(tmp_path: Path) -> None:
    """Change only each operation's signature digest when the same API renders with the other signature style."""
    assert_output(client_digest_report("pets", "pets-unpack", tmp_path), EXPECTED / "digests" / "signature-style.txt")


def test_client_validation_digests(tmp_path: Path) -> None:
    """Keep every operation's contract digests and the models when the same API renders with other validation modes."""
    assert_output(
        client_digest_report("validation", "validation-structural", tmp_path), EXPECTED / "digests" / "validation.txt"
    )


def test_client_body_arguments_digests(tmp_path: Path) -> None:
    """Change only the signature digests of operations whose calls may give fields when an API renders with them."""
    assert_output(
        client_digest_report("fields-body", "fields-both", tmp_path), EXPECTED / "digests" / "body-arguments.txt"
    )


@pytest.mark.parametrize(
    "case",
    [
        "defaults",
        "values",
        "invalid-values",
        "invalid-urls",
        "invalid-shapes",
        "invalid-parameter-names",
        "toml-values",
        "toml-invalid-location",
        "toml-invalid-statuses",
        "toml-unknown-keys",
        "validation-values",
        "validation-invalid",
        "validation-shape",
        "validation-pydantic",
        "toml-validation",
        "toml-validation-unknown",
        "toml-validation-types",
        "body-fields",
        "body-fields-invalid",
        "body-field-names-invalid",
        "toml-body-fields",
        "toml-body-field-names-unknown",
    ],
)
def test_client_config(case: str, tmp_path: Path) -> None:
    """Construct the client settings from Python values or a target file, reporting every invalid value."""
    assert_output(client_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")
