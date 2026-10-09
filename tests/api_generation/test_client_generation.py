"""Render client targets: resources, methods, parameters, media, responses, servers, and the package files."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from datamodel_code_generator import get_version
from datamodel_code_generator.util import get_yaml_backend
from tests.conftest import assert_generated_modules_output, assert_output
from tests.data.python.client_generation import (
    client_api_report,
    client_config_report,
    client_documentation_report,
    client_helper_spelling_report,
    client_input_report,
    client_model_parity_report,
    client_render,
    generate_client,
)

EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/client"
DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "client"


@pytest.mark.parametrize(
    "case", ["input-cycle-dict", "input-cycle-list", "input-cycle-mutual", "input-cycle-reference", "input-aliases"]
)
def test_client_input(case: str, tmp_path: Path) -> None:
    """Accept cyclic input the loader keeps, which nothing traverses, and shared aliases; ryaml refuses cycles."""
    report = client_input_report(case, tmp_path)
    expected = f"{case}.txt"
    if case.startswith("input-cycle"):
        expected = {"pyyaml": f"{case}.txt", "ryaml": f"{case}-ryaml.txt"}[get_yaml_backend()]
    assert_output(report, EXPECTED / expected)


@pytest.mark.parametrize(
    "case",
    [
        "allowreserved-path-30",
        "allowreserved-path-31",
        "allowreserved-path-32",
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
        "names-unpack",
        "name-errors",
        "method-collisions",
        "child-collision",
        "media-errors",
        "media-item-parts",
        "parameter-content-defaults-30",
        "parameter-content-defaults-31",
        "multipart-referenced-items",
        "server-errors",
        "codec-errors",
        "cookie-names",
        "reference-errors",
        "setting-errors",
        "empty",
        "querystring",
        "references",
        "evolution",
        "validation-ambiguous",
        "discriminator-copies",
        "discriminator-variants",
        "discriminator-reuse",
        "fields",
        "fields-structural",
        "fields-unpack",
        "fields-errors",
        "fields-both",
        "fields-cycle",
        "fields-optional-models",
        "type-spellings",
        "type-spellings-exact",
        "type-spellings-legacy",
        "type-spellings-reuse",
        "type-spellings-cycle",
        "helpers",
        "pagination",
        "pagination-counts",
        "fingerprint-nested",
        "fingerprint-nested-changed",
        "pagination-links",
        "pagination-plans",
        "pagination-querystring",
        "pagination-targets",
        "querystring-targets",
        "polling",
        "polling-plans",
        "uploads",
        "uploads-compression",
        "uploads-plans",
        "webhooks",
        "webhooks-schema",
        "webhooks-public-keys",
        "webhooks-adapters",
        "webhooks-unsigned",
        "streams",
        "streams-mixed",
        "ndjson",
        "stream-resume",
        "sockets",
        "caching",
        "compression",
        "templates",
        "templates-invalid",
        "templates-not-found",
        "api-scope-required",
    ],
)
def test_client_render(case: str, tmp_path: Path) -> None:
    """Plan each operation's names, arguments, media, responses, and servers, and render the package they need."""
    report, rendered = client_render(case, tmp_path)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(
            {parts: content for parts, content in modules.items() if parts[0] != "client"}
            if case in {"auth-no-root", "default-server", "implicit-server", "servers"}
            or (case == "pets" and backend != "pydantic_v2_BaseModel")
            else modules,
            EXPECTED / "packages" / case / backend,
        )
    if case in {
        "auth",
        "auth-context",
        "compatibility",
        "empty",
        "evolution",
        "fields",
        "fields-arguments",
        "fields-optional-models",
        "fields-structural",
        "fields-unpack",
        "helpers",
        "media",
        "names",
        "pagination",
        "pagination-querystring",
        "pets",
        "pets-unpack",
        "querystring",
        "retries",
        "retry-headers",
        "validation-arguments",
    }:
        assert_output(
            client_model_parity_report(
                case,
                tmp_path / "ordinary",
                {
                    backend: {parts: content for parts, content in modules.items() if parts[0] != "client"}
                    for backend, modules in rendered.items()
                },
            ),
            EXPECTED / "bindings" / "capture-parity.txt",
        )


@pytest.mark.parametrize(
    "case",
    [
        "protocols-disabled",
        "protocols-python",
        "protocols-relative",
        "protocols-empty",
        "protocols-unsupported",
        "protocols-unsupported-kinds",
        "protocols-file-missing",
        "protocols-not-json",
        "protocols-not-object",
        "protocols-deep",
        "protocols-deep-limit",
        "protocols-deep-huge",
        "protocols-names",
        "protocols-shared-errors",
        "protocols-pagination-errors",
        "protocols-polling-errors",
        "protocols-stream-errors",
        "protocols-references",
        "protocols-querystring",
        "protocols-pagination-checks",
        "protocols-pagination-querystring-checks",
        "protocols-querystring-target-checks",
        "protocols-pagination-count-checks",
        "protocols-pagination-wrapper-checks",
        "protocols-pagination-strict-checks",
        "protocols-polling-checks",
        "protocols-upload-errors",
        "protocols-upload-checks",
        "protocols-pagination-link-checks",
        "protocols-pagination-resume-checks",
        "protocols-webhook-errors",
        "protocols-webhook-checks",
        "protocols-stream-checks",
        "protocols-stream-scope",
        "protocols-ndjson-checks",
        "protocols-stream-resume-checks",
        "protocols-socket-checks",
        "protocols-socket-errors",
        "protocols-socket-scope",
        "protocols-unmodeled",
        "protocols-cache-errors",
        "protocols-cache-checks",
    ],
)
def test_client_protocols(case: str, tmp_path: Path) -> None:
    """Load helper settings from JSON, TOML, or Python records, validate and record them, and keep the package.

    Cases that give the same settings another way report under the name of the case they equal.
    """
    report, rendered = client_render(case, tmp_path)
    assert_output(report, EXPECTED / "protocols" / f"{report.splitlines()[0].removeprefix('# ')}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(
            {parts: content for parts, content in modules.items() if parts[0] != "client"},
            EXPECTED / "packages" / "helpers" / backend,
        )


@pytest.mark.parametrize(
    ("case", "builtin_sources"),
    [
        ("auth", True),
        ("caching", True),
        ("compression", True),
        ("empty", True),
        ("fields-both", True),
        ("media", True),
        ("ndjson", True),
        ("pagination", True),
        ("pagination-counts", True),
        ("pagination-links", True),
        ("pets-unpack", True),
        ("polling", True),
        ("retries", True),
        ("sockets", True),
        ("stream-resume", True),
        ("streams", True),
        ("uploads", True),
        ("templates-missing", False),
    ],
)
def test_client_template_fallback(case: str, *, builtin_sources: bool, tmp_path: Path) -> None:
    """Render a package from copies of the builtin templates' Jinja sources, or without client overrides, unchanged.

    The copies live in the client directory of a custom template directory; a custom template directory without one
    falls back to the builtin roles. Each case reports under the name of the case it equals.
    """
    report, rendered = client_render(case, tmp_path / "render", builtin_sources=builtin_sources)
    expected = report.splitlines()[0].removeprefix("# ")
    assert_output(report, EXPECTED / f"{expected}.txt")
    for backend, modules in rendered.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / expected / backend)
    assert_output(
        client_documentation_report(case, tmp_path / "documentation", builtin_sources=builtin_sources),
        EXPECTED / "documentation" / f"{expected}.txt",
    )


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        ("pagination-root", "pagination-documents", "helper-documents"),
        ("webhooks", "webhooks-python", "webhook-records"),
        ("webhooks-public-keys", "webhooks-public-keys-python", "webhook-public-key-records"),
        ("webhooks-adapters", "webhooks-adapters-python", "webhook-adapter-records"),
        ("caching", "caching-python", "cache-records"),
        ("references", "self-references", "reference-spellings"),
    ],
)
def test_client_helper_spellings(first: str, second: str, expected: str, tmp_path: Path) -> None:
    """Render equivalent helper settings, such as file references and Python records, into the same package."""
    assert_output(client_helper_spelling_report(first, second, tmp_path), EXPECTED / "spellings" / f"{expected}.txt")


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
        "uploads",
        "streams",
        "ndjson",
        "stream-resume",
        "sockets",
        "caching",
        "compression",
        "fields-both",
        "pets-unpack",
        "media",
        "templates",
    ],
)
def test_client_documentation(case: str, tmp_path: Path) -> None:
    """Keep metadata, explicit retry overrides, and documentation ownership visible."""
    assert_output(client_documentation_report(case, tmp_path), EXPECTED / "documentation" / f"{case}.txt")


@pytest.mark.parametrize(
    "case",
    [
        "defaults",
        "auth-values",
        "auth-invalid",
        "auth-unknown",
        "values",
        "invalid-values",
        "invalid-urls",
        "invalid-shapes",
        "invalid-parameter-names",
        "body-fields",
        "body-fields-invalid",
        "body-field-names-invalid",
        "retry-values",
        "retry-invalid",
        "retry-conflicting-controls",
        "retry-unknown-field",
        "compression-values",
        "compression-invalid",
        "protocols-path",
        "protocols-type",
        "protocols-records",
        "protocols-records-invalid",
        "protocols-records-shape",
    ],
)
def test_client_config(case: str, tmp_path: Path) -> None:
    """Construct the client settings from Python values or a target file, reporting every invalid value."""
    assert_output(client_config_report(case, tmp_path), EXPECTED / "configs" / f"{case}.txt")


@pytest.mark.parametrize(
    ("case", "options"),
    [
        (
            "header-quotes",
            {
                "custom_file_header_path": DATA / "custom_file_header_with_docstring.txt",
                "custom_file_header_mode": "prepend",
                "enable_version_header": True,
                "use_double_quotes": True,
            },
        ),
        ("encoding", {"encoding": "latin-1", "custom_file_header": "# -*- coding: latin-1 -*-\n# Café"}),
        ("formatters", {"formatters": ["ruff-format"]}),
    ],
)
def test_client_output_options(case: str, options: dict[str, object], tmp_path: Path) -> None:
    """Head, format, and encode the client files with the model output options, exactly like the models."""
    source = shutil.copy2(SOURCE / "pets.yaml", tmp_path / "pets.yaml")
    generate_client(source, tmp_path, "client", "pydantic_v2.BaseModel", model=options)
    encoding = str(options.get("encoding", "utf-8"))
    text = (tmp_path / "client" / "resources" / "pets" / "__init__.py").read_bytes().decode(encoding)
    assert_output(
        text.replace(f"#   version:   {get_version()}", "#   version:   0.0.0"),
        EXPECTED / "output-options" / f"{case}.py",
    )


@pytest.mark.parametrize("formatters", [None, ["ruff-check", "ruff-format"]], ids=["default", "ruff-isort-rules"])
def test_client_regenerate_unchanged(formatters: list[str] | None, tmp_path: Path) -> None:
    """Write the same client files again in a copy of a fresh generation, though its models were staged at first."""
    generated, copy = tmp_path / "generated", tmp_path / "copy"
    generated.mkdir()
    shutil.copy2(SOURCE / "pets.yaml", generated / "pets.yaml")
    (generated / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I", "N999"]\n', encoding="utf-8")
    model = {"formatters": formatters}
    generate_client(generated / "pets.yaml", generated, "client", "pydantic_v2.BaseModel", model=model)
    shutil.copytree(generated, copy)
    generate_client(copy / "pets.yaml", copy, "client", "pydantic_v2.BaseModel", model=model)
    changed = sorted(
        path.relative_to(copy).as_posix()
        for path in copy.rglob("*.py")
        if path.read_bytes() != (generated / path.relative_to(copy)).read_bytes()
    )
    assert_output(f"changed {changed}\n", EXPECTED / "regenerated-unchanged.txt")


def test_client_api(tmp_path: Path) -> None:
    """Resolve the public annotations, render and generate twice, then rewrite an edited owned file."""
    assert_output(client_api_report(tmp_path), EXPECTED / "api.txt")
