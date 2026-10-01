"""Render client targets: resources, methods, parameters, media, responses, servers, and the package files."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.client_generation import (
    client_config_report,
    client_digest_report,
    client_documentation_report,
    client_helper_digest_report,
    client_render,
)

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client"


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "auth",
        "auth-no-root",
        "auth-errors",
        "auth-context",
        "auth-wire-conflicts",
        "pets-unpack",
        "retries",
        "retry-headers",
        "retry-header-conflicts",
        "retry-security-context",
        "retry-security-context-conflicts",
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
        "parameter-adapters",
        "validation-arguments",
        "validation-arguments-msgspec",
        "fields",
        "fields-structural",
        "fields-unpack",
        "fields-arguments",
        "fields-errors",
        "fields-cycle",
        "fields-optional-models",
        "helpers",
        "pagination",
        "pagination-counts",
        "pagination-links",
        "pagination-plans",
        "pagination-querystring",
        "pagination-targets",
        "polling",
        "polling-plans",
        "webhooks",
        "webhooks-schema",
        "webhooks-public-keys",
        "webhooks-adapters",
        "webhooks-unsigned",
        "streams",
        "streams-mixed",
        "ndjson",
        "sockets",
        "compatibility",
    ],
)
def test_client_render(case: str, tmp_path: Path) -> None:
    """Plan each operation's names, arguments, media, responses, and servers, and render the package they need."""
    report, rendered = client_render(case, tmp_path)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / case / backend)


@pytest.mark.parametrize(
    "case",
    [
        "protocols-disabled",
        "protocols-python",
        "protocols-toml",
        "protocols-relative",
        "protocols-empty",
        "protocols-unsupported",
        "protocols-unsupported-kinds",
        "protocols-file-missing",
        "protocols-not-utf8",
        "protocols-not-yaml",
        "protocols-control",
        "protocols-complex-key",
        "protocols-deep",
        "protocols-duplicate",
        "protocols-alias",
        "protocols-alias-key",
        "protocols-anchor",
        "protocols-merge",
        "protocols-not-mapping",
        "protocols-empty-file",
        "protocols-envelope",
        "protocols-envelope-missing",
        "protocols-names",
        "protocols-shared-errors",
        "protocols-pagination-errors",
        "protocols-polling-errors",
        "protocols-stream-errors",
        "protocols-references",
        "protocols-querystring",
        "protocols-pagination-checks",
        "protocols-pagination-querystring-checks",
        "protocols-pagination-count-checks",
        "protocols-polling-checks",
        "protocols-pagination-link-checks",
        "protocols-pagination-resume-checks",
        "protocols-webhook-errors",
        "protocols-webhook-checks",
        "protocols-stream-checks",
        "protocols-stream-scope",
        "protocols-ndjson-checks",
        "protocols-socket-checks",
        "protocols-socket-errors",
        "protocols-socket-scope",
    ],
)
def test_client_protocols(case: str, tmp_path: Path) -> None:
    """Load helper settings from YAML, TOML, or Python records, validate and record them, and keep the package.

    Cases that give the same settings another way report under the name of the case they equal.
    """
    report, rendered = client_render(case, tmp_path)
    assert_output(report, EXPECTED / "protocols" / f"{report.splitlines()[0].removeprefix('# ')}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / "helpers" / backend)


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("pagination", "pagination-documents", "helper-documents"),
        ("webhooks", "webhooks-python", "webhook-records"),
        ("webhooks-public-keys", "webhooks-public-keys-python", "webhook-public-key-records"),
        ("webhooks-adapters", "webhooks-adapters-python", "webhook-adapter-records"),
    ],
)
def test_client_helper_digests(first: str, second: str, expected: str, tmp_path: Path) -> None:
    """Digest a helper's normalized settings, so equivalent spellings and equal Python records digest alike."""
    assert_output(client_helper_digest_report(first, second, tmp_path), EXPECTED / "digests" / f"{expected}.txt")


def test_client_signature_style_digests(tmp_path: Path) -> None:
    """Change only each operation's signature digest when the same API renders with the other signature style."""
    assert_output(client_digest_report("pets", "pets-unpack", tmp_path), EXPECTED / "digests" / "signature-style.txt")


def test_client_retry_metadata_digests(tmp_path: Path) -> None:
    """Project retry declarations into only the corresponding request or response contracts."""
    assert_output(
        client_digest_report("retries-defaults", "retries", tmp_path), EXPECTED / "digests" / "retry-metadata.txt"
    )


@pytest.mark.parametrize(
    "case",
    [
        "retries",
        "default-server",
        "empty",
        "auth",
        "pagination",
        "pagination-counts",
        "pagination-links",
        "polling",
        "streams",
        "ndjson",
        "sockets",
    ],
)
def test_client_documentation(case: str, tmp_path: Path) -> None:
    """Keep metadata, explicit retry overrides, documentation ownership, and source distribution inputs visible."""
    assert_output(client_documentation_report(case, tmp_path), EXPECTED / "documentation" / f"{case}.txt")


@pytest.mark.parametrize("variant", ["auth-no-challenge", "auth-changed", "auth-cosmetic"])
def test_client_auth_digests(variant: str, tmp_path: Path) -> None:
    """Keep authentication semantics in the security digest and exclude descriptions and scope ordering."""
    assert_output(client_digest_report("auth", variant, tmp_path), EXPECTED / "digests" / f"{variant}.txt")


def test_client_parameter_adapter_digests(tmp_path: Path) -> None:
    """Change the request digest of an operation whose adapted parameter explodes or whose adapter differs."""
    assert_output(
        client_digest_report("parameter-adapters", "parameter-adapters-variant", tmp_path),
        EXPECTED / "digests" / "parameter-adapters.txt",
    )


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
        "auth-values",
        "auth-invalid",
        "auth-unknown",
        "toml-auth-values",
        "toml-auth-type",
        "toml-auth-location",
        "toml-auth-unknown",
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
        "retry-values",
        "retry-invalid",
        "retry-conflicting-controls",
        "retry-missing-field",
        "retry-unknown-field",
        "toml-retry-values",
        "toml-retry-safety",
        "toml-retry-type",
        "toml-retry-unknown",
        "toml-retry-idempotency-unknown",
        "toml-retry-idempotency-type",
        "toml-retry-retention",
        "toml-retry-retention-infinite",
        "toml-retry-retention-missing",
        "toml-retry-header-missing",
        "toml-retry-replay-missing",
        "toml-retry-scope-missing",
        "protocols-path",
        "protocols-type",
        "protocols-records",
        "protocols-records-invalid",
        "protocols-records-shape",
        "toml-protocols",
        "toml-protocols-type",
    ],
)
def test_client_config(case: str, tmp_path: Path) -> None:
    """Construct the client settings from Python values or a target file, reporting every invalid value."""
    assert_output(client_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")
