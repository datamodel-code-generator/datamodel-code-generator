"""Serve generated FastAPI packages over HTTP: parameters, bodies, forms, results, responses, and handler checks.

The cross-target cases call each server through the client generated from the same document.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from tests.capabilities import needs

pytest.importorskip("fastapi")

from scripts.build_docs_examples import (
    CLIENT_HOWTO,
    CLIENT_HOWTO_SCRIPTS,
    DOCS_PROJECTS,
    TUTORIAL,
    api_quick_start_arguments,
    docs_project_arguments,
)
from tests.api_generation.support.cross_targets import cross_report
from tests.api_generation.support.docs_tutorial import generate_project, run_python, run_tests, serve
from tests.api_generation.support.fastapi_charsets import text_charsets
from tests.api_generation.support.fastapi_entrypoint import (
    fastapi_entrypoint_report,
    readme_examples_report,
    run_commands_report,
)
from tests.api_generation.support.fastapi_openapi import SCENARIOS, fastapi_openapi_report
from tests.api_generation.support.fastapi_server import fastapi_server_report
from tests.api_generation.support.fastapi_upstream import fastapi_upstream_report
from tests.api_generation.support.model_codec_builtin import builtin_codec_report, builtin_codec_startup_report
from tests.conftest import (
    assert_generated_modules_output,
    assert_output,
)
from tests.main.conftest import run_main_with_args

DATA = Path(__file__).parents[1] / "data"
CODECS = DATA / "generation_platform/codecs/pydantic"
PRESET = needs("type-statements")


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
        "patterns",
        "patterns-annotated",
        "errors",
        "bodies",
        "nullable",
        "nullable-3-0",
        "nullable-3-0-strict",
        "nullable-alias",
        "nullable-force-optional",
        "nullable-parameters",
        "nullable-parameters-31",
        "results",
        "nullable-results",
        "nullable-results-3-0",
        "nullable-results-alias",
        "security",
        "security-templates",
        "unaccepted",
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
        "forms-by-name",
        "forms-by-alias",
        "held-extras",
        "held-extras-ignore",
        "held-extras-forbid",
        "held-extras-allow",
        "held-extras-base",
        "held-extras-base-dataclass",
        "held-bases",
        "responses",
        "nested-models",
        "stale",
        "spellings",
        "spellings-exact",
    ],
)
def test_fastapi_server(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate each server, then build its router from handwritten services and exchange requests with it.

    The snapshotted packages pin their Python modules and the served OpenAPI document.
    """
    report, packages, documents = fastapi_server_report(case, tmp_path, monkeypatch)
    assert_output(report, name="report")
    package = {(backend, *parts): body for backend, modules in packages.items() for parts, body in modules.items()}
    assert_generated_modules_output({**package, **documents}, pattern="*", name="package")


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
        "merged_nullable",
        "pagination",
        "streams",
        "ndjson",
        "raw",
        "raw_strict",
        "content_media_type",
        "content_media_type_bytes",
        "extras",
        "reused_forms",
        "forms",
        "forms_30",
        "forms_read_only",
        "bound_texts",
        "bound_texts_reuse",
        "bound_parts",
        "bound_parts_variants",
        "tag_forms",
        "required",
        "upper_alike",
        "overridden_containers",
        "ref_siblings",
        "ref_siblings_30",
        "ref_siblings_30_ignored",
        "binary_json",
        "union_parts",
    ],
)
def test_generated_client(case: str, tmp_path: Path) -> None:
    """Generate a server and a client from one document, then call every operation of the server through the client."""
    assert_output(cross_report(case, tmp_path))


@pytest.mark.parametrize("case", list(SCENARIOS))
def test_fastapi_openapi(case: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Compose the served OpenAPI document of generated packages in applications of every shape."""
    assert_output(fastapi_openapi_report(case, tmp_path, monkeypatch))


def test_fastapi_upstream(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Close uploaded files and end parse errors and anyio cancellations as FastAPI and Starlette do."""
    assert_output(fastapi_upstream_report(tmp_path, monkeypatch))


def test_text_charsets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Use the selected Content-Type for both decoding requests and encoding responses."""
    assert_output(text_charsets(tmp_path, monkeypatch))


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
    assert_output(builtin_codec_report(CODECS / f"{source}.yaml", CODECS / f"{cases}.json", tmp_path, server=True))


@pytest.mark.abnormal_path("generated models or bindings edited out of sync after generation")
@pytest.mark.parametrize("cases", ["pets-startup", "pets-startup-dataclass", "pets-startup-noalias"])
def test_pydantic_codec_startup(cases: str, tmp_path: Path) -> None:
    """Start native server apps after the retained generated model substitutions."""
    assert_output(builtin_codec_startup_report(CODECS / "pets.yaml", CODECS / f"{cases}.json", tmp_path, server=True))


@PRESET
@pytest.mark.parametrize("project", ["tutorial", "howto"])
def test_docs_project(
    project: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Generate a documentation project from the pyproject.toml its guide shows, then run the project's own tests."""
    generated = generate_project(
        DOCS_PROJECTS / project, tmp_path / project, docs_project_arguments(project), monkeypatch, capsys
    )
    assert_output(generated.notice, name="notice")
    assert_output(generated.files, name="files")
    for path, role in (("app/models.py", "models"), ("app/server/services.py", "services")):
        assert_output((generated.root / path).read_text(encoding="utf-8"), name=role, suffix=".py")
    assert_output(run_tests(generated.root, "test_app.py"), name="pytest")


@PRESET
def test_docs_tutorial_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Serve the tutorial with uvicorn and call it with the client generated from the same document."""
    server = generate_project(TUTORIAL, tmp_path / "server", docs_project_arguments("tutorial"), monkeypatch, capsys)
    with serve(server.root, "app.server.main:app") as url:
        client = generate_project(
            TUTORIAL, tmp_path / "client", api_quick_start_arguments("client"), monkeypatch, capsys, server_url=url
        )
        assert_output(client.notice, name="notice")
        assert_output(client.files, name="files")
        sync = client.root / "app/client/resources/pets/_sync.py"
        assert_output(sync.read_text(encoding="utf-8"), name="sync", suffix=".py")
        for script, role in (("call.py", "call"), ("call_async.py", "call_async")):
            assert_output(run_python(client.root, script), name=role)


@PRESET
def test_docs_client_howto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """Run the client how-to scripts against their stand-in API, then the tests of code that uses the client."""
    project = generate_project(
        CLIENT_HOWTO, tmp_path / "client-howto", docs_project_arguments("client-howto"), monkeypatch, capsys
    )
    assert_output(project.notice, name="notice")
    for script in (*CLIENT_HOWTO_SCRIPTS, "generate"):
        assert_output(run_python(project.root, f"{script}.py"), name=script)
    assert_output(run_tests(project.root, "test_client.py"), name="pytest")


def test_fastapi_entrypoint(tmp_path: Path) -> None:
    """Serve the generated entrypoint with the stubs generation writes once, an implementation, and broken modules.

    Generation keeps the modules of the user's; the entrypoint names a missing module or class, lets the error of a
    missing dependency through, and refuses a method that cannot take its route's arguments as it starts.
    """
    report, stubs = fastapi_entrypoint_report(tmp_path)
    assert_output(report, name="report")
    assert_generated_modules_output(stubs, name="stubs")


def test_fastapi_readme_examples(tmp_path: Path) -> None:
    """Run the test and router examples of the READMEs of an unsecured and a secured server as a reader copies them."""
    assert_output(readme_examples_report(tmp_path))


@pytest.mark.parametrize("output", ["src/app/server", "my src/app/server"], ids=["src", "src-with-space"])
def test_fastapi_run_commands(
    output: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Serve a server generated into a `src` layout with the commands its run notice prints, from the same directory."""
    monkeypatch.chdir(tmp_path)
    shutil.copy2(DATA / "generation_platform/fastapi/pets.yaml", tmp_path / "pets.yaml")
    capsys.readouterr()
    run_main_with_args([
        *("--input", "pets.yaml", "--input-file-type", "openapi", "--output", f"{output.rpartition('/')[0]}/models.py"),
        *("--target-python-version", "3.11", "--openapi-scopes", "schemas", "api", "--disable-timestamp"),
        *("--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", "--generate-server", "fastapi"),
        *("--server-output", output, "--server-package", "app.server", "--server-model-package", "app.models"),
    ])
    report = run_commands_report(tmp_path, capsys.readouterr().err)
    assert_output(report)
