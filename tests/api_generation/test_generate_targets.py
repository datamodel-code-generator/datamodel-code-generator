"""Generate the server and client targets through generate(): the options, the files, the errors, and the warnings."""

from __future__ import annotations

import re
import shutil
import warnings
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from datamodel_code_generator import (
    ClientType,
    DataModelType,
    DocumentationAnnotationWarning,
    Error,
    GenerateConfig,
    InputFileType,
    OpenAPIScope,
    PythonVersion,
    ServerType,
    generate,
    load_pyproject_config,
)
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.remote_lock import RemoteReferenceLock
from tests.conftest import (
    assert_directory_content,
    assert_generated_modules_output,
    assert_output,
    create_assert_file_content,
)
from tests.main.conftest import run_generate_and_assert, run_generate_file_and_assert

DATA = Path(__file__).parents[1] / "data"
SERVER_SOURCE = DATA / "generation_platform" / "fastapi"
CLIENT_SOURCE = DATA / "generation_platform" / "client" / "cli"
EXPECTED = DATA / "expected" / "main" / "generation_platform"
SERVER_PACKAGE = EXPECTED / "fastapi" / "packages" / "pets" / "pydantic_v2_BaseModel"
CLIENT_OPTIONS = EXPECTED / "client" / "cli" / "options"
GENERATE = EXPECTED / "generate"
MODELS: dict[str, Any] = {
    "input_file_type": InputFileType.OpenAPI,
    "target_python_version": PythonVersion.PY_311,
    "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
    "output_model_type": DataModelType.PydanticV2BaseModel,
    "formatters": [Formatter.BUILTIN],
    "disable_timestamp": True,
}
STRINGS: dict[str, Any] = {
    "input_file_type": "openapi",
    "target_python_version": "3.11",
    "openapi_scopes": ["schemas", "api"],
    "output_model_type": "pydantic_v2.BaseModel",
    "formatters": ["builtin"],
    "disable_timestamp": True,
}
SERVER: dict[str, Any] = {
    "generate_server": ServerType.FastAPI,
    "server_package": "server",
    "server_model_package": "models",
}
CLIENT: dict[str, Any] = {
    "generate_client": ClientType.HTTPX2,
    "client_package": "client",
    "client_model_package": "models",
}

assert_file_content = create_assert_file_content(EXPECTED)


def _server(root: Path, source: str = "pets.yaml") -> Path:
    shutil.copy2(SERVER_SOURCE / source, root / source)
    return Path(source)


def _client(root: Path) -> Path:
    for name in ("options.yaml", "protocols.json"):
        shutil.copy2(CLIENT_SOURCE / name, root / name)
    return Path("options.yaml")


def _split(modules: dict[tuple[str, ...], str]) -> tuple[dict[tuple[str, ...], str], dict[tuple[str, ...], str]]:
    """Separate the Python modules of a result from its other files."""
    python = {path: text for path, text in modules.items() if path[-1].endswith(".py")}
    return python, {path: text for path, text in modules.items() if path not in python}


def _as_text(root: Path, *path: str) -> Path:
    """Copy a written file that is not a module to a text file under root, which the comparison reads as text."""
    (copies := root / "text").mkdir(exist_ok=True)
    return Path(shutil.copyfile(root.joinpath(*path), copies / f"{'-'.join(path)}.txt"))


def _compare_written(modules: Any, root: Path) -> None:
    """Compare every returned file with the file a write run left at the same path under root."""
    python, other = _split(modules)
    assert_generated_modules_output(python, root)
    for path, text in other.items():
        assert_output(text, _as_text(root, *path))


@pytest.mark.parametrize("form", ["enums", "strings", "config", "loaded"])
def test_generate_server(form: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the models and the server package the command line writes, from keywords, a config, or pyproject.toml.

    Enum members and their values give the same package.
    """
    monkeypatch.chdir(tmp_path)
    source = _server(tmp_path)
    match form:
        case "enums":
            generate(source, **MODELS, **SERVER, output=Path("models.py"), server_output=Path("server"))
        case "strings":
            generate(
                source,
                **STRINGS,
                output="models.py",
                generate_server="fastapi",
                server_output="server",
                server_package="server",
                server_model_package="models",
            )
        case "config":
            generate(source, config=GenerateConfig(**MODELS, **SERVER, output="models.py", server_output="server"))
        case _:
            (tmp_path / "pyproject.toml").write_text(
                '[tool.datamodel-codegen]\noutput = "models.py"\ngenerate-server = "fastapi"\n'
                'server-output = "server"\nserver-package = "server"\nserver-model-package = "models"\n'
                "server-router-names = '{}'\n",
                encoding="utf-8",
            )
            (elsewhere := tmp_path / "elsewhere").mkdir()
            monkeypatch.chdir(elsewhere)
            generate(tmp_path / source, config=load_pyproject_config(tmp_path, overrides=MODELS))
    assert_file_content(tmp_path / "models.py", SERVER_PACKAGE / "models.py")
    assert_directory_content(tmp_path / "server", SERVER_PACKAGE / "server")


def test_generate_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the models and the client package the command line writes."""
    monkeypatch.chdir(tmp_path)
    generate(_client(tmp_path), **MODELS, **CLIENT, output=Path("models.py"), client_output=Path("client"))
    assert_file_content(tmp_path / "models.py", CLIENT_OPTIONS / "models.py")
    assert_file_content(tmp_path / "client" / "resources" / "pets" / "_sync.py", CLIENT_OPTIONS / "_sync.py")


def test_generate_server_options_without_selector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Server options without --generate-server have no effect: the models are those of a model-only run."""
    monkeypatch.chdir(tmp_path)
    run_generate_file_and_assert(
        input_path=_server(tmp_path),
        output_path=tmp_path / "models.py",
        assert_func=assert_file_content,
        expected_file=SERVER_PACKAGE / "models.py",
        **MODELS,
        server_output=Path("server"),
        server_layout="single",
        server_primary_responses={"/paths/~1pets/get": {"status_code": 999}},
        client_signature_style="unpack",
    )
    assert_output(f"server written: {(tmp_path / 'server').exists()}\n", GENERATE / "unselected.txt")


@pytest.mark.parametrize(
    ("options", "message"),
    [
        (
            {"generate_server": "fastapi", "server_output": None},
            "--generate-server requires --server-output, --server-package and --server-model-package",
        ),
        (
            {"generate_server": "fastapi", "server_package": "server", "output": None},
            "--generate-server requires --server-model-package",
        ),
        (
            {"generate_client": "httpx2", "output": None},
            "--generate-client requires --client-package and --client-model-package",
        ),
        ({**SERVER, **CLIENT}, "--generate-server and --generate-client cannot be used together"),
        ({**SERVER, "openapi_scopes": ["schemas"]}, "--generate-server requires --openapi-scopes to include api"),
        (
            {**SERVER, "output_model_type": "msgspec.Struct"},
            (
                "--output-model-type: The fastapi target does not support 'msgspec.Struct'; "
                "use 'pydantic_v2.BaseModel' or 'pydantic_v2.dataclass'"
            ),
        ),
        (
            {**SERVER, "target_python_version": "3.10"},
            "--target-python-version: The fastapi target needs a target Python version of 3.11 or later",
        ),
        (
            {**SERVER, "server_primary_responses": {"/paths/~1pets/post": {"status_code": 700}}},
            (
                "Invalid --server-primary-responses: /paths/~1pets/post.status_code: "
                "Input should be less than or equal to 599"
            ),
        ),
        (
            {**SERVER, "server_package": "server-package"},
            "--server-package must be a dotted Python import path",
        ),
        (
            {**CLIENT, "client_operations": {"/paths/~1pets/get": {"parameter_names": {"query:limit": "1st"}}}},
            (
                "--client-operations['/paths/~1pets/get'].parameter_names['query:limit']: "
                "A parameter name needs a location, a wire name, and an identifier"
            ),
        ),
    ],
    ids=[
        "requires-output",
        "requires-package",
        "client-requires-package",
        "both",
        "api-scope",
        "backend",
        "target-python",
        "json-value",
        "package",
        "keyed",
    ],
)
def test_generate_target_errors(options: dict[str, Any], message: str, tmp_path: Path) -> None:
    """Refuse a target the options cannot generate with the message the command line prints, writing nothing."""
    run_generate_and_assert(
        input_=SERVER_SOURCE / "pets.yaml",
        expected_error=Error,
        expected_error_match=f"^{re.escape(message)}$",
        **{
            **MODELS,
            "output": tmp_path / "models.py",
            "server_output": tmp_path / "server",
            "client_output": tmp_path / "client",
            **options,
        },
    )
    assert_output(f"written: {sorted(path.name for path in tmp_path.iterdir())}\n", GENERATE / "nothing-written.txt")


def test_generate_server_lock_overlap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse a lock update inside the server output, as a lock inside the model output is refused.

    A write run also refuses a lock update that another caller resolved, since it could not publish that update.
    """
    monkeypatch.chdir(tmp_path)
    source, server = _server(tmp_path), tmp_path / "server"
    run_generate_and_assert(
        input_=source,
        expected_error=Error,
        expected_error_match=f"^{re.escape(f'Server output and Remote lock paths must not overlap: {server}')}$",
        **MODELS,
        **SERVER,
        output=Path("models.py"),
        server_output=Path("server"),
        update_lock=True,
        lockfile=Path("server", "api.lock"),
    )
    config = GenerateConfig(**MODELS, **SERVER, output=Path("models.py"), server_output=Path("server"))
    config.resolve_remote_lock(RemoteReferenceLock.open(tmp_path / "api.lock", update=True, locked=False))
    run_generate_and_assert(
        input_=source,
        expected_error=Error,
        expected_error_match=(
            f"^{re.escape('--update-lock: A target run cannot publish a remote lock update that another caller owns')}$"
        ),
        config=config,
    )
    assert_output(f"written: {sorted(path.name for path in tmp_path.iterdir())}\n", GENERATE / "memory-written.txt")


@pytest.mark.parametrize(
    ("model_package", "output", "message"),
    [
        (
            "server.application",
            "server/application.py",
            "server/application.py: Two generated files resolve to the same path",
        ),
        ("server", "server.py", "server.py: The module has the name of a generated package directory beside it"),
        (
            "server.routers",
            "server/routers.py",
            "server/routers.py: The module has the name of a generated package directory beside it",
        ),
    ],
    ids=["same-path", "package-name", "subpackage-name"],
)
@pytest.mark.parametrize("written", [False, True])
def test_generate_server_collisions(
    model_package: str, output: str, message: str, written: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse models that collide with the package, naming them alike whether the run writes the files or not."""
    monkeypatch.chdir(tmp_path)
    run_generate_and_assert(
        input_=_server(tmp_path),
        expected_error=Error,
        expected_error_match=f"^{re.escape(message)}$",
        **MODELS,
        **{**SERVER, "server_model_package": model_package},
        **({"output": Path(output), "server_output": Path("server")} if written else {}),
    )
    assert_output(f"written: {sorted(path.name for path in tmp_path.iterdir())}\n", GENERATE / "memory-written.txt")


def test_generate_target_input_errors(tmp_path: Path) -> None:
    """Refuse a list of inputs and an unknown choice like the model options."""
    run_generate_and_assert(
        input_=[SERVER_SOURCE / "pets.yaml"],
        expected_error=Error,
        expected_error_match=f"^{re.escape('input: Target generation reads one root document, not a list')}$",
        **MODELS,
        **SERVER,
        output=tmp_path / "models.py",
        server_output=tmp_path / "server",
    )
    run_generate_and_assert(
        input_=SERVER_SOURCE / "pets.yaml",
        expected_error=ValidationError,
        expected_error_match="generate_server\n  Input should be 'fastapi'",
        **MODELS,
        generate_server="flask",
    )


@pytest.mark.parametrize("output", ["models.py", None])
def test_generate_server_warnings(output: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Warn once about documentation the served document leaves out, from the package, with or without an output."""
    monkeypatch.chdir(tmp_path)
    source = _server(tmp_path, "callbacks.yaml")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("ignore")
        warnings.filterwarnings("default", category=DocumentationAnnotationWarning, module="datamodel_code_generator")
        for _ in range(2):
            generate(source, **MODELS, **SERVER, output=output, server_output=Path("server"))
    assert_output(
        "".join(
            f"{item.category.__name__}: {item.message}\n"
            for item in recorded
            if issubclass(item.category, DocumentationAnnotationWarning)
        ),
        GENERATE / "documentation-warning.txt",
    )


@pytest.mark.parametrize("output", ["models.py", None])
def test_generate_server_lock(output: str | None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish a lock update with the files of a write run, and alone without an output."""
    monkeypatch.chdir(tmp_path)
    generate(_server(tmp_path), **MODELS, **SERVER, output=output, server_output=Path("server"), update_lock=True)
    assert_output(
        (tmp_path / "datamodel-codegen.lock").read_text(encoding="utf-8"),
        DATA / "expected" / "http" / "remote_lock_empty.txt",
    )
    assert_output(
        f"written: {sorted(path.name for path in tmp_path.iterdir())}\n",
        GENERATE / f"lock-{'write' if output else 'memory'}.txt",
    )


def test_generate_server_without_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return every model and server file under its import path, as the write run writes them, and write nothing."""
    monkeypatch.chdir(tmp_path)
    modules = generate(_server(tmp_path), **MODELS, **SERVER)
    assert_output(f"written: {sorted(path.name for path in tmp_path.iterdir())}\n", GENERATE / "memory-written.txt")
    python, _ = _split(modules)
    assert_generated_modules_output(python, SERVER_PACKAGE)
    generate(Path("pets.yaml"), **MODELS, **SERVER, output=Path("models.py"), server_output=Path("server"))
    _compare_written(modules, tmp_path)


@pytest.mark.parametrize(
    ("source", "options", "written"),
    [
        (
            "pets.yaml",
            {"server_package": "pkg.server", "server_model_package": "pkg.models"},
            {"output": Path("pkg", "models.py"), "server_output": Path("pkg", "server")},
        ),
        (
            "pets.yaml",
            {"server_package": "server", "server_model_package": "server.models"},
            {"output": Path("server", "models.py"), "server_output": Path("server")},
        ),
    ],
    ids=["dotted", "models-inside"],
)
def test_generate_server_without_output_layouts(
    source: str, options: dict[str, Any], written: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Place dotted packages and models inside the package where their import paths put them."""
    (root := tmp_path / "root").mkdir()
    monkeypatch.chdir(root)
    modules = generate(_server(root, source), **MODELS, **{**SERVER, **options})
    (write := tmp_path / "write").mkdir()
    monkeypatch.chdir(write)
    generate(_server(write, source), **MODELS, **{**SERVER, **options}, **written)
    _compare_written(modules, write)


def test_generate_server_without_output_modular(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return modular models as the package their import path names, with the side files a model run writes."""
    shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
    (root := tmp_path / "root").mkdir()
    monkeypatch.chdir(root)
    side = {"emit_model_metadata": Path("metadata.json")}
    modules = generate(tmp_path / "spec" / "modular.yaml", **MODELS, **SERVER, **side)
    assert_output(f"written: {sorted(path.name for path in root.iterdir())}\n", GENERATE / "memory-metadata.txt")
    (write := tmp_path / "write").mkdir()
    monkeypatch.chdir(write)
    generate(
        tmp_path / "spec" / "modular.yaml",
        **MODELS,
        **SERVER,
        **side,
        output=Path("models"),
        server_output=Path("server"),
    )
    _compare_written(modules, write)
    assert_output((root / "metadata.json").read_text(encoding="utf-8"), _as_text(write, "metadata.json"))


@pytest.mark.parametrize("form", ["keywords", "loaded", "settings-path"])
def test_generate_server_without_output_settings(form: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Format with the settings found from the working directory, as a write run into it does.

    Loading pyproject.toml with the output overridden to None returns the files of its write run, and a relative
    settings path names a directory under the working directory.
    """
    monkeypatch.chdir(tmp_path)
    (settings := tmp_path / ("settings" if form == "settings-path" else "")).mkdir(exist_ok=True)
    (settings / "pyproject.toml").write_text(
        '[tool.ruff]\nline-length = 60\n\n[tool.datamodel-codegen]\noutput = "models.py"\ngenerate-server = "fastapi"\n'
        'server-output = "server"\nserver-package = "server"\nserver-model-package = "models"\n',
        encoding="utf-8",
    )
    source = _server(tmp_path)
    options = {**MODELS, **SERVER, **({"settings_path": Path("settings")} if form == "settings-path" else {})}
    if form == "loaded":
        modules = generate(source, config=load_pyproject_config(overrides={**MODELS, "output": None}))
    else:
        modules = generate(source, **options)
    generate(source, **options, output=Path("models.py"), server_output=Path("server"))
    _compare_written(modules, tmp_path)
    assert_output(modules["server", "application.py"], GENERATE / "line-length" / "application.py")


def test_generate_client_without_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Return every model and client file as the write run writes them."""
    (root := tmp_path / "root").mkdir()
    monkeypatch.chdir(root)
    modules = generate(_client(root), **MODELS, **CLIENT)
    (write := tmp_path / "write").mkdir()
    monkeypatch.chdir(write)
    generate(_client(write), **MODELS, **CLIENT, output=Path("models.py"), client_output=Path("client"))
    _compare_written(modules, write)
