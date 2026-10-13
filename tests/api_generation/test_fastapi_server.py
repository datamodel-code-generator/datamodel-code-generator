"""Serve generated FastAPI packages over HTTP: parameters, bodies, forms, results, responses, and handler checks.

The cross-target cases call each server through the client generated from the same document.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

from scripts.build_docs_examples import (
    CLIENT_HOWTO,
    CLIENT_HOWTO_SCRIPTS,
    DOCS_PROJECTS,
    EXPECTED_CLIENT_HOWTO,
    EXPECTED_DOCS_PROJECTS,
    TUTORIAL,
    api_quick_start_arguments,
    docs_project_arguments,
)
from tests.api_generation.support.cross_targets import cross_report
from tests.api_generation.support.docs_tutorial import generate_project, run_python, run_tests, serve
from tests.api_generation.support.fastapi_charsets import text_charsets
from tests.api_generation.support.fastapi_openapi import SCENARIOS, fastapi_openapi_report
from tests.api_generation.support.fastapi_server import fastapi_server_report
from tests.api_generation.support.fastapi_upstream import fastapi_upstream_report
from tests.api_generation.support.model_codec_builtin import builtin_codec_report, builtin_codec_startup_report
from tests.conftest import assert_generated_modules_output, assert_output, create_assert_file_content

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs/pydantic"
CODEC_EXPECTED = DATA / "expected/main/generation_platform/codecs/pydantic"
EXPECTED = Path(__file__).parents[1] / "data/expected/main/generation_platform/fastapi/servers"
CROSS = Path(__file__).parents[1] / "data/expected/main/generation_platform/cross"
PRESET = pytest.mark.skipif(sys.version_info < (3, 12), reason="the quick-start preset emits type statements")
assert_docs_file = create_assert_file_content(EXPECTED_DOCS_PROJECTS)


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "pets-type-checking",
        "pets-type-checking-default",
        "pets-type-checking-off",
        "unbound",
        "parameters",
        "strict",
        "strict-annotated",
        pytest.param("strict-preset", marks=PRESET),
        "defaults",
        "defaults-annotated",
        pytest.param("defaults-preset", marks=PRESET),
        "kinds",
        "kinds-annotated",
        "kinds-decimal",
        "kinds-literal",
        "errors",
        "bodies",
        "nullable",
        "nullable-3-0",
        "nullable-3-0-strict",
        "nullable-alias",
        "nullable-force-optional",
        "results",
        "nullable-results",
        "nullable-results-3-0",
        "nullable-results-alias",
        "security",
        "security-templates",
        "customized",
        "locals",
        "naming",
        "naming-snake",
        "naming-parent-prefixed",
        "naming-full-path",
        "naming-primary-first",
        "unauthorized",
        "variants",
        "variants-request-response",
        "variants-modular",
        "variants-reuse",
        "forms",
        "responses",
        "nested-models",
        "stale",
        "spellings",
        "spellings-exact",
    ],
)
def test_fastapi_server(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate each server, then build its router from handwritten services and exchange requests with it."""
    report, packages = fastapi_server_report(case, tmp_path, monkeypatch)
    assert_output(report, EXPECTED / f"{case}.txt")
    for backend, modules in packages.items():
        assert_generated_modules_output(modules, EXPECTED / "packages" / case / backend)


@pytest.mark.parametrize(
    "case",
    [
        "pets",
        "parameters",
        "styles",
        "headers",
        "security",
        "bodies",
        "variants",
        "results",
        "nullable",
        "pagination",
        "streams",
        "ndjson",
        "raw",
        "extras",
    ],
)
def test_generated_client(case: str, tmp_path: Path) -> None:
    """Generate a server and a client from one document, then call every operation of the server through the client."""
    assert_output(cross_report(case, tmp_path), CROSS / f"{case}.txt")


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


@PRESET
@pytest.mark.parametrize("project", ["tutorial", "howto"])
def test_docs_project(
    project: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generate a documentation project from the pyproject.toml its guide shows, then run the project's own tests."""
    generated = generate_project(
        DOCS_PROJECTS / project, tmp_path / project, docs_project_arguments(project), monkeypatch, capsys
    )
    expected = EXPECTED_DOCS_PROJECTS / project
    assert_output(generated.notice, expected / "notice.txt")
    assert_output(generated.files, expected / "files.txt")
    for name in ("models.py", "server/services.py"):
        assert_docs_file(generated.root / name, f"{project}/{name}")
    assert_output(run_tests(generated.root, "test_app.py"), expected / "pytest.txt")


@PRESET
def test_docs_tutorial_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Serve the tutorial with uvicorn and call it with the client generated from the same document."""
    server = generate_project(TUTORIAL, tmp_path / "server", docs_project_arguments("tutorial"), monkeypatch, capsys)
    expected = EXPECTED_DOCS_PROJECTS / "tutorial"
    with serve(server.root) as url:
        client = generate_project(
            TUTORIAL, tmp_path / "client", api_quick_start_arguments("client"), monkeypatch, capsys, server_url=url
        )
        assert_output(client.notice, expected / "client-notice.txt")
        assert_output(client.files, expected / "client-files.txt")
        assert_docs_file(client.root / "client/resources/pets/_sync.py", "tutorial/client/resources/pets/_sync.py")
        for script in ("call.py", "call_async.py"):
            assert_output(run_python(client.root, script), expected / script.replace(".py", ".txt"))


@PRESET
def test_docs_client_howto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the client how-to scripts against their stand-in API, then the tests of code that uses the client."""
    project = generate_project(
        CLIENT_HOWTO, tmp_path / "client-howto", docs_project_arguments("client-howto"), monkeypatch, capsys
    )
    assert_output(project.notice, EXPECTED_CLIENT_HOWTO / "notice.txt")
    for script in (*CLIENT_HOWTO_SCRIPTS, "generate"):
        assert_output(run_python(project.root, f"{script}.py"), EXPECTED_CLIENT_HOWTO / f"{script}.txt")
    assert_output(run_tests(project.root, "test_client.py"), EXPECTED_CLIENT_HOWTO / "pytest.txt")
