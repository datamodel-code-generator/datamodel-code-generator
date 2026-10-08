"""Generate the FastAPI server target from the command line: publish, check, report, and refuse conflicts."""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import pytest

from datamodel_code_generator import get_version
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_directory_content, assert_output, create_assert_file_content, freeze_time
from tests.main.conftest import TIMESTAMP, run_main_and_assert, run_main_with_args

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "fastapi"
CLI = SOURCE / "cli"
EXPECTED = DATA / "expected" / "main" / "generation_platform" / "fastapi"
PACKAGE = EXPECTED / "packages" / "pets" / "pydantic_v2_BaseModel"
DEPENDENCIES = EXPECTED / "cli" / "dependencies.txt"
PYTHON = ["--target-python-version", "3.11"]
SCOPES = ["--openapi-scopes", "schemas", "api"]
BACKEND = ["--output-model-type", "pydantic_v2.BaseModel"]
FORMATTERS = ["--formatters", "builtin"]
MODEL_OPTIONS = [*PYTHON, *SCOPES, *BACKEND, *FORMATTERS]
OPTIONS = [*MODEL_OPTIONS, "--disable-timestamp"]
PACKAGES = ["--server-package", "server", "--server-model-package", "models"]
SERVER = ["--generate-server", "fastapi", "--server-output", "server", *PACKAGES]
DOC_OPTIONS = ["--input-file-type", "openapi", "--output", "models.py", *OPTIONS, *SERVER]
DOC_INPUT = "generation_platform/fastapi/cli/options.yaml"
DOC_OUTPUT = "main/generation_platform/fastapi/cli/options"
UNSUPPORTED = (
    "Error: --output-model-type: The fastapi target does not "
    "support 'msgspec.Struct'; use 'pydantic_v2.BaseModel' or 'pydantic_v2.dataclass'\n"
)
CONFLICT = "Error: --generate-server cannot be used with"
NOT_WRITABLE = "Error: --diagnostics-json cannot be written: it is not a file in an existing directory\n"
READ_OR_WRITTEN = "Error: --diagnostics-json names a file the generation reads or writes\n"
OTHER_FILE = "Error: --diagnostics-json names an existing file that is not a report\n"
CHECK_CASES = json.loads((CLI / "check-cases.json").read_text(encoding="utf-8"))
CONFIGURED = [
    *("--server-layout", "routers", "--server-handler-mode", "async", "--server-include-request"),
    *("--server-body-mode", "request", "--server-router-names", '{"tag:pets": "animals"}'),
    *("--server-body-modes", '{"/paths/~1pets/post": "typed"}', "--server-primary-responses", "responses.json"),
    *("--server-operation-names", '{"/paths/~1pets/get": "list_all"}'),
    "--server-parameter-names",
    '{"/paths/~1pets/get": {"query:limit": "page_size", "header:X-Request-Id": "trace"}}',
    *("--server-handler-modes", '{"/paths/~1pets/get": "sync"}'),
]

assert_file_content = create_assert_file_content(EXPECTED)


def _server(*extra: str, output: str = "server") -> list[str]:
    return [*OPTIONS, "--generate-server", "fastapi", "--server-output", output, *PACKAGES, *extra]


def _inputs(root: Path, *pyproject: str) -> list[tuple[Path, Path]]:
    return [(SOURCE / "pets.yaml", root / "pets.yaml"), *((CLI / name, root / "pyproject.toml") for name in pyproject)]


def _copy(root: Path, *pyproject: str) -> None:
    for source, destination in _inputs(root, *pyproject):
        shutil.copy2(source, destination)


def _methods(services: Path) -> str:
    lines = services.read_text(encoding="utf-8").splitlines(keepends=True)
    methods = (line.strip().partition("(")[0] for line in lines if "def " in line)
    return f"# {services.parent.name}/services.py\n" + "".join(f"{method}\n" for method in methods)


class _ReadOnlyPath(type(Path())):
    def write_text(self, *_args: object, **_kwargs: object) -> int:
        raise PermissionError(13, "Permission denied")


def test_fastapi_cli_generate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models and the server package, check an edit and a missing owned file, then rewrite both."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stdout_path=DEPENDENCIES,
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_directory_content(tmp_path / "server", PACKAGE / "server")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check"),
        capsys=capsys,
        assert_no_stderr=True,
    )
    monkeypatch.chdir(tmp_path / "server")
    run_main_and_assert(
        input_path=tmp_path / "pets.yaml",
        output_path=tmp_path / "models.py",
        input_file_type="openapi",
        extra_args=_server("--check", output=str(tmp_path / "server")),
        capsys=capsys,
        assert_no_stderr=True,
    )
    monkeypatch.chdir(tmp_path)
    (tmp_path / "models.py").write_text("# edited\n", encoding="utf-8")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check"),
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-model.txt",
        assert_no_stderr=True,
    )
    (tmp_path / "server" / "README.md").unlink()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check"),
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-model-readme.txt",
        assert_no_stderr=True,
    )
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        capsys=capsys,
        expected_stdout_path=DEPENDENCIES,
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_directory_content(tmp_path / "server", PACKAGE / "server")


@pytest.mark.parametrize("case_name", CHECK_CASES)
@pytest.mark.parametrize("structured", [False, True], ids=["text", "json"])
def test_fastapi_cli_check_outputs(
    case_name: str,
    structured: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare real model and target text without publishing or changing any output file."""
    case = CHECK_CASES[case_name]
    if case.get("json_only") and not structured:
        pytest.skip("This conflict requires JSON output")
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    output = Path("models" if case.get("modular") else "models.py")
    source = Path("pets.yaml")
    if case.get("modular") or case.get("source"):
        shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
        source = Path("spec") / case.get("source", "modular.yaml")
    encoding = case.get("encoding", "utf-8")
    options: list[str] = ["--encoding", encoding]
    if case.get("templates"):
        shutil.copytree(CLI / "check-templates", tmp_path / "templates")
        options.extend(["--custom-template-dir", "templates"])
    run_main_and_assert(
        input_path=source,
        input_file_type="openapi",
        output_path=output,
        extra_args=_server(*options),
        capsys=capsys,
        expected_stdout_path=DEPENDENCIES,
    )
    for name in case.get("remove", ()):
        if (path := tmp_path / name).is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
    for name, text in case.get("append", {}).items():
        path = tmp_path / name
        path.write_bytes(path.read_bytes() + text.encode(encoding))
    for name, text in case.get("write", {}).items():
        (path := tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
    for name in case.get("crlf", ()):
        (path := tmp_path / name).write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))
    for name in case.get("binary", ()):
        (tmp_path / name).write_bytes((CLI / "non-text.dat").read_bytes())
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if case.get("nested"):
        monkeypatch.chdir(tmp_path / "server")
    base = Path("..") if case.get("nested") else Path()
    changed = any(key in case for key in ("remove", "append", "binary", "changed"))
    changed |= "write" in case and any(name.endswith(".py") and "__pycache__" not in name for name in case["write"])
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("error", UserWarning)
        run_main_and_assert(
            input_path=base / source,
            input_file_type="openapi",
            output_path=base / output,
            extra_args=_server(
                "--check",
                *options,
                *case.get("options", ()),
                *(["--output-format", "json"] if structured else []),
                output=str(base / "server"),
            ),
            capsys=capsys,
            expected_exit=Exit.ERROR if "error" in case else Exit.DIFF if changed else Exit.OK,
            expected_stderr_contains=f"Error: {case['error']}" if "error" in case else None,
            assert_no_stderr="error" not in case,
            expected_stdout_path=(
                EXPECTED / "cli" / "check" / f"{case_name}.{'json' if structured else 'txt'}"
                if "error" not in case
                else None
            ),
            skip_code_validation=encoding != "utf-8",
        )
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert_output(
        f"files unchanged {before == after}; warnings {len(recorded)}\n", EXPECTED / "cli" / "check-unchanged.txt"
    )


def test_fastapi_cli_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the same models and package from pyproject.toml alone as from the options, then check them."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-server.toml")
    run_main_with_args([], capsys=capsys, expected_stdout_path=DEPENDENCIES)
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")
    assert_directory_content(tmp_path / "server", PACKAGE / "server")
    run_main_with_args(["--check"], capsys=capsys, assert_no_stderr=True)


def test_fastapi_cli_ignore_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Generate only the models when --ignore-pyproject leaves out the server pyproject.toml selects."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--ignore-pyproject"],
        copy_files=_inputs(tmp_path, "pyproject-server.toml"),
        file_should_not_exist=tmp_path / "server",
    )
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")


def test_fastapi_cli_unselected_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ignore server settings that only pyproject.toml holds while no server is selected."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "pyproject.toml").write_text(
        (CLI / "pyproject-configured.toml").read_text(encoding="utf-8").replace('generate-server = "fastapi"\n', ""),
        encoding="utf-8",
    )
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=_inputs(tmp_path),
        file_should_not_exist=tmp_path / "server",
    )
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("base", []),
        ("profile", ["--profile", "async"]),
        ("cli-over-profile", ["--profile", "async", "--server-handler-mode", "sync"]),
        ("cli-table", ["--server-handler-modes", '{"/paths/~1store~1inventory/get": "async"}']),
        ("cli-empty-table", ["--profile", "async", "--server-handler-modes", "{}"]),
        ("profile-output", ["--profile", "elsewhere"]),
        ("cli-output", ["--profile", "elsewhere", "--server-output", "service"]),
    ],
)
def test_fastapi_cli_precedence(
    name: str,
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Take each server setting from the command line, then the profile, then the base table.

    An operation's own entry beats the global setting, and a command-line table replaces the pyproject.toml one.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-precedence.toml")
    run_main_with_args(arguments, capsys=capsys, expected_stdout_path=DEPENDENCIES)
    report = "".join(_methods(path) for path in sorted(tmp_path.glob("*/services.py")))
    assert_output(
        f"$ datamodel-codegen {' '.join(arguments)}\n{report}", EXPECTED / "cli" / "precedence" / f"{name}.txt"
    )


def test_fastapi_cli_pyproject_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve pyproject.toml paths, JSON files and operation documents against its directory.

    Command-line paths and JSON files resolve against the working directory.
    """
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    _copy(project, "pyproject-paths.toml")
    shutil.copy2(CLI / "names.json", project / "names.json")
    (work / "names.json").write_text('{"/paths/~1pets/get": "find_pets"}', encoding="utf-8")
    monkeypatch.chdir(work)
    run_main_with_args([], capsys=capsys, expected_stdout_path=DEPENDENCIES)
    run_main_with_args(
        ["--server-output", "service", "--server-operation-names", "names.json"],
        capsys=capsys,
        expected_stdout_path=DEPENDENCIES,
    )
    services = sorted(tmp_path.rglob("services.py"), key=lambda path: path.relative_to(tmp_path).as_posix())
    assert_output(
        "".join(f"# in {path.parent.parent.relative_to(tmp_path).as_posix()}\n{_methods(path)}" for path in services),
        EXPECTED / "cli" / "pyproject-paths.txt",
    )


def test_fastapi_cli_generate_pyproject_config(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the server options of a command line as [tool.datamodel-codegen] keys, like model options."""
    run_main_with_args(
        ["--input", "pets.yaml", "--output", "models.py", *SERVER, *CONFIGURED, "--generate-pyproject-config"],
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "pyproject-config.txt",
    )


def test_fastapi_cli_generate_cli_command(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print the server keys of pyproject.toml as command-line options, like model keys."""
    monkeypatch.chdir(tmp_path)
    shutil.copy2(CLI / "pyproject-configured.toml", tmp_path / "pyproject.toml")
    run_main_with_args(
        ["--generate-cli-command"], capsys=capsys, expected_stdout_path=EXPECTED / "cli" / "cli-command.txt"
    )


@pytest.mark.parametrize(
    ("options", "lockfile"),
    [(["--update-lock"], "datamodel-codegen.lock"), (["--update-lock", "--lockfile", "api.lock"], "api.lock")],
    ids=["default", "explicit"],
)
def test_fastapi_cli_lockfile(
    options: list[str],
    lockfile: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Publish the remote lock an update asks for at its default path next to the run or at the explicit one."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*options),
        copy_files=_inputs(tmp_path),
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_output(
        (tmp_path / lockfile).read_text(encoding="utf-8"), DATA / "expected" / "http" / "remote_lock_empty.txt"
    )


def test_fastapi_cli_dependencies(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print the runtime dependencies of the generated package as requirements lines when asked to."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--dependency-format", "requirements", output="service"),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "requirements.txt",
        file_should_not_exist=tmp_path / "server",
    )


def test_fastapi_cli_include_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the package where --server-output points without the paths --openapi-include-paths leaves out."""
    monkeypatch.chdir(tmp_path)
    include = ["--openapi-include-paths", "/pets*"]
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--diagnostics-json", "-", *include, output="service"),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "empty-report.txt",
        assert_no_stderr=True,
        file_should_not_exist=tmp_path / "server",
    )
    assert_output(
        "\n".join(sorted(path.name for path in (tmp_path / "service" / "routers").iterdir())) + "\n",
        EXPECTED / "cli" / "include-paths-routers.txt",
    )
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check", "--diagnostics-json", "diagnostics.json", *include, output="service"),
        capsys=capsys,
        assert_no_stderr=True,
    )
    assert_file_content(tmp_path / "diagnostics.json", "cli/empty-report.txt")


def test_fastapi_cli_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the OpenAPI document from standard input, check the result, and refuse an unsupported backend."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        stdin_path=SOURCE / "pets.yaml",
        monkeypatch=monkeypatch,
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        assert_func=assert_file_content,
        expected_file="cli/stdin-models.py",
    )
    run_main_and_assert(
        stdin_path=SOURCE / "pets.yaml",
        monkeypatch=monkeypatch,
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check"),
        capsys=capsys,
        assert_no_stderr=True,
    )
    run_main_and_assert(
        stdin_path=SOURCE / "pets.yaml",
        monkeypatch=monkeypatch,
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--output-model-type", "msgspec.Struct"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=UNSUPPORTED,
    )


@pytest.mark.parametrize(
    ("arguments", "message"),
    [
        ([*OPTIONS, "--server-layout", "single"], "--server-layout requires --generate-server"),
        ([*OPTIONS, "--no-server-include-request"], "--no-server-include-request requires --generate-server"),
        (
            [*OPTIONS, *PACKAGES, "--server-include-request", "--diagnostics-json", "-", "--dependency-format", "uv"],
            (
                "--server-package, --server-model-package, --server-include-request, --diagnostics-json and "
                "--dependency-format require --generate-server"
            ),
        ),
        (
            [*OPTIONS, "--generate-server", "fastapi"],
            "--generate-server requires --server-output, --server-package and --server-model-package",
        ),
        (
            [*OPTIONS, "--generate-server", "fastapi", "--server-package", "server"],
            "--generate-server requires --server-output and --server-model-package",
        ),
        (
            _server("--check", "--emit-model-metadata", "metadata.json"),
            "--check cannot be used with --emit-model-metadata",
        ),
    ],
    ids=[
        "server-option",
        "negative-option",
        "server-options",
        "no-server-settings",
        "missing-server-settings",
        "metadata",
    ],
)
def test_fastapi_cli_usage(
    arguments: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse server options without the server, and a server without the settings it requires."""
    run_main_and_assert(
        input_path=SOURCE / "pets.yaml",
        output_path=tmp_path / "models.py",
        input_file_type="openapi",
        extra_args=arguments,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"Error: {message}\n",
        output_should_not_exist=True,
    )


def test_fastapi_cli_invalid_unselected_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse an invalid server key of pyproject.toml like an invalid model key, though no server is selected."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=OPTIONS,
        copy_files=_inputs(tmp_path, "pyproject-bogus.toml"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="Invalid configuration: 1 validation error for Config\nserver_layout\n",
        output_should_not_exist=True,
    )


def test_fastapi_cli_information(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run an information command as it runs without the server options, writing nothing."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=SOURCE / "pets.yaml",
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*SERVER, "--list-experimental"],
        capsys=capsys,
        expected_stdout_path=DATA / "expected" / "main" / "list_experimental.txt",
        file_should_not_exist=[tmp_path / "models.py", tmp_path / "server"],
    )


@pytest.mark.parametrize("server", [[], SERVER], ids=["models", "server"])
@pytest.mark.parametrize(
    ("options", "expected"),
    [([], "cli/timestamp-models.py"), (["--disable-timestamp"], PACKAGE / "models.py")],
    ids=["timestamp", "no-timestamp"],
)
def test_fastapi_cli_models_as_given(
    server: list[str], options: list[str], expected: str | Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models a model-only run writes, with the generation timestamp exactly when the options keep it."""
    monkeypatch.chdir(tmp_path)
    with freeze_time(TIMESTAMP):
        run_main_and_assert(
            input_path=Path("pets.yaml"),
            output_path=Path("models.py"),
            input_file_type="openapi",
            extra_args=[*MODEL_OPTIONS, *options, *server],
            copy_files=_inputs(tmp_path),
            assert_func=assert_file_content,
            expected_file=expected,
        )


@pytest.mark.parametrize(
    "formatters", [[], ["--formatters", "ruff-check", "ruff-format"]], ids=["default", "ruff-isort-rules"]
)
def test_fastapi_cli_check_after_generate(
    formatters: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find nothing to change in a copy of a fresh generation, though its models were still staged when formatted.

    The copy also gives isort, which caches where it places a module per configuration, a configuration of its own.
    """
    generated, copy = tmp_path / "generated", tmp_path / "copy"
    generated.mkdir()
    monkeypatch.chdir(generated)
    _copy(generated)
    (generated / "pyproject.toml").write_text('[tool.ruff.lint]\nselect = ["I"]\n', encoding="utf-8")
    arguments = [
        *("--input", "pets.yaml", "--input-file-type", "openapi", "--output", "models.py"),
        *PYTHON,
        *SCOPES,
        *BACKEND,
        "--disable-timestamp",
        *formatters,
        *SERVER,
    ]
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FutureWarning)
        run_main_with_args(arguments, use_builtin_default_formatter=False)
        shutil.copytree(generated, copy)
        monkeypatch.chdir(copy)
        run_main_with_args([*arguments, "--check"], use_builtin_default_formatter=False)


@pytest.mark.parametrize(
    ("case", "options"),
    [
        (
            "header-quotes",
            [
                *("--custom-file-header-path", str(DATA / "custom_file_header.txt")),
                *("--custom-file-header-mode", "prepend", "--enable-version-header", "--use-double-quotes"),
            ],
        ),
        ("encoding", ["--encoding", "latin-1", "--custom-file-header", "# -*- coding: latin-1 -*-\n# Café"]),
        ("formatters", ["--formatters", "ruff-format", "--enable-command-header"]),
    ],
)
def test_fastapi_cli_output_options(
    case: str, options: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Head, format, and encode the server files with the model output options, exactly like the models."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*options),
        copy_files=_inputs(tmp_path),
        skip_code_validation="--encoding" in options,
    )
    encoding = options[options.index("--encoding") + 1] if "--encoding" in options else "utf-8"
    text = (tmp_path / "server" / "routers" / "store.py").read_bytes().decode(encoding)
    assert_output(
        text.replace(f"#   version:   {get_version()}", "#   version:   0.0.0"),
        EXPECTED / "cli" / "output-options" / f"{case}.py",
    )


@pytest.mark.parametrize(
    "scopes",
    [["--openapi-scopes", "schemas"], ["--openapi-scopes", "schemas", "paths"], []],
    ids=["schemas", "paths", "default"],
)
def test_fastapi_cli_api_scope_required(
    scopes: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a server whose model options leave out the api scope, instead of adding it to the models."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*PYTHON, *BACKEND, *FORMATTERS, "--disable-timestamp", *scopes, *SERVER],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="Error: --generate-server requires --openapi-scopes to include api\n",
        file_should_not_exist=[tmp_path / "models.py", tmp_path / "server"],
    )


@pytest.mark.parametrize(
    ("options", "stderr"),
    [
        (
            ["--collapse-root-models-name-strategy", "child"],
            "Error: --collapse-root-models-name-strategy requires --collapse-root-models\n",
        ),
        (
            ["--use-specialized-enum", "--target-python-version", "3.10"],
            (
                "Error: --use-specialized-enum requires --target-python-version 3.11 or later.\n"
                "Current target version: 3.10\n"
                "StrEnum is only available in Python 3.11+.\n"
            ),
        ),
    ],
    ids=["collapse-root-models-name-strategy", "use-specialized-enum"],
)
def test_fastapi_cli_model_option_errors(
    options: list[str], stderr: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse model options a model-only run refuses, before the server runs."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*options),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr,
        file_should_not_exist=[tmp_path / "models.py", tmp_path / "server"],
    )


def test_fastapi_cli_model_option_warning(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Warn of a model option without effect as a model-only run does, and write the same files."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--reuse-scope", "tree"),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stdout_path=DEPENDENCIES,
        expected_stderr="Warning: --reuse-scope=tree has no effect without --reuse-model\n",
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_directory_content(tmp_path / "server", PACKAGE / "server")


@pytest.mark.parametrize(
    ("options", "expected"),
    [([], "warning.txt"), (["--disable-warnings"], "no_warning.txt")],
    ids=["shown", "disabled"],
)
def test_fastapi_cli_disable_warnings(
    options: list[str], expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silence the model warnings of a server run with --disable-warnings, as for a model-only run."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", FutureWarning)
        run_main_with_args(
            [
                *("--input", "pets.yaml", "--input-file-type", "openapi", "--output", "models.py"),
                *PYTHON,
                *SCOPES,
                *BACKEND,
                "--disable-timestamp",
                *SERVER,
                *options,
            ],
            use_builtin_default_formatter=False,
        )
    assert_output(
        "\n".join(str(item.message) for item in recorded if "Default formatters" in str(item.message)),
        DATA / "expected" / "main" / "formatter_policy" / expected,
    )


def test_fastapi_cli_target_python(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a server generation left at the default target Python version, before writing anything."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*SCOPES, *BACKEND, *FORMATTERS, "--disable-timestamp", *SERVER],
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=(
            "Error: --target-python-version: The fastapi target needs a target Python version of 3.11 or later\n"
        ),
        output_should_not_exist=True,
    )


def test_fastapi_cli_target_python_pyproject(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the target Python version from pyproject.toml when the command line leaves it out."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*SCOPES, *BACKEND, *FORMATTERS, "--disable-timestamp", *SERVER],
        copy_files=_inputs(tmp_path, "pyproject-target.toml"),
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )


def test_fastapi_cli_input_model(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse a server generated from Python models, which carry no API operations."""
    run_main_with_args(
        ["--input-model", "models:Pet", "--output", str(tmp_path / "models.py"), *_server()],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"{CONFLICT} --input-model\n",
    )


@pytest.mark.parametrize(
    ("arguments", "stderr", "stdout"),
    [
        (
            ["--watch", "--output-format", "json", "--diagnostics-json", "-"],
            (
                "Error: --generate-server cannot be used with --watch; "
                "--generate-server cannot be used with --output-format json\n"
            ),
            "conflicts-report.txt",
        ),
        (["--diff-against", "pets.yaml"], f"{CONFLICT} --diff-against\n", None),
        (["--diagnostics-json", "pets.yaml"], READ_OR_WRITTEN, None),
        (["--diagnostics-json", "pyproject.toml"], READ_OR_WRITTEN, None),
        (["--diagnostics-json", "server/diagnostics.json"], READ_OR_WRITTEN, None),
        (["--diagnostics-json", "absent/diagnostics.json"], NOT_WRITABLE, None),
        (["--diagnostics-json", "reports"], NOT_WRITABLE, None),
    ],
    ids=["watch", "diff", "input", "pyproject", "server", "missing", "directory"],
)
def test_fastapi_cli_conflicts(
    arguments: list[str],
    stderr: str,
    stdout: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse options the target cannot honor and reports that would replace inputs, before writing anything."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "reports").mkdir()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path, "pyproject-target.toml"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stdout_path=None if stdout is None else EXPECTED / "cli" / stdout,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )
    assert_output(
        (tmp_path / "pyproject.toml").read_text(encoding="utf-8"),
        EXPECTED / "cli" / "kept" / "pyproject-target.toml.txt",
    )


@pytest.mark.parametrize(
    ("pyproject", "arguments", "stderr"),
    [
        ("pyproject-jobs.toml", ["--all-jobs"], f"{CONFLICT} --all-jobs\n"),
        ("pyproject-jobs.toml", ["--job", "server"], f"{CONFLICT} --job\n"),
        ("pyproject-jobs.toml", ["--all-jobs", "--diagnostics-json", "pyproject.toml"], READ_OR_WRITTEN),
        (
            "pyproject-model-jobs.toml",
            ["--all-jobs", "--server-layout", "single"],
            "Error: --server-layout requires --generate-server\n",
        ),
    ],
    ids=["all-jobs", "job", "jobs-pyproject", "model-job-server-option"],
)
def test_fastapi_cli_jobs(
    pyproject: str,
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse server jobs and server options of model-only jobs, writing nothing.

    A job cannot select the server until the job runner stages target packages.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, pyproject)
    run_main_with_args(arguments, expected_exit=Exit.ERROR, capsys=capsys, expected_stderr=stderr)
    assert_output(
        "".join(f"{path.name}\n" for path in sorted(tmp_path.iterdir())), EXPECTED / "cli" / "jobs-unwritten.txt"
    )


@pytest.mark.parametrize(
    ("fixture", "name", "stderr"),
    [
        ("notes.md", "notes.md", OTHER_FILE),
        ("settings.json", "settings.json", OTHER_FILE),
        ("client.json", "client.json", OTHER_FILE),
        ("entries.json", "entries.json", OTHER_FILE),
        ("tool-settings.toml", "pyproject.toml", READ_OR_WRITTEN),
    ],
)
def test_fastapi_cli_report_other_file(
    fixture: str,
    name: str,
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep an existing file that is not a diagnostics report of this target instead of replacing it."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--diagnostics-json", name),
        copy_files=[*_inputs(tmp_path), (CLI / "reports" / fixture, tmp_path / name)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )
    assert_output((tmp_path / name).read_text(encoding="utf-8"), EXPECTED / "cli" / "kept" / f"{name}.txt")


def test_fastapi_cli_report_replaced(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replace an earlier report of this target, and refuse a report that cannot be written."""
    monkeypatch.chdir(tmp_path)
    for arguments in (
        ["--diagnostics-json", "diagnostics.json"],
        ["--check", "--diagnostics-json", "diagnostics.json"],
    ):
        run_main_and_assert(
            input_path=Path("pets.yaml"),
            output_path=Path("models.py"),
            input_file_type="openapi",
            extra_args=_server(*arguments),
            copy_files=_inputs(tmp_path),
            capsys=capsys,
            assert_no_stderr=True,
        )
    assert_file_content(tmp_path / "diagnostics.json", "cli/empty-report.txt")
    monkeypatch.setattr("datamodel_code_generator._target_cli.Path", _ReadOnlyPath)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check", "--diagnostics-json", "diagnostics.json"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="Error: --diagnostics-json cannot be written: Permission denied\n",
    )


@pytest.mark.parametrize(
    ("arguments", "stderr", "stdout"),
    [
        (["--output-model-type", "msgspec.Struct"], UNSUPPORTED, None),
        (
            ["--emit-model-metadata", "metadata"],
            "Error: Model metadata output requires a file path, not a directory\n",
            None,
        ),
        (
            ["--dependency-format", "requirements", "--diagnostics-json", "-"],
            "Error: --dependency-format cannot be used with --diagnostics-json -\n",
            "format-report.txt",
        ),
    ],
    ids=["backend", "metadata", "format"],
)
def test_fastapi_cli_config_errors(
    arguments: list[str],
    stderr: str,
    stdout: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report model settings and run options that fail before any file is written."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "metadata").mkdir()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stdout_path=None if stdout is None else EXPECTED / "cli" / stdout,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("server", "pyproject"),
    [([], ["pyproject-configured.toml"]), ([*SERVER, *CONFIGURED], [])],
    ids=["pyproject", "options"],
)
def test_fastapi_cli_config_values(
    server: list[str], pyproject: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the same package from every server setting in pyproject.toml as from the same options.

    The server templates come from the custom template directory.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "templates" / "roles", tmp_path / "templates")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, "--custom-template-dir", "templates", *server],
        copy_files=[*_inputs(tmp_path, *pyproject), (CLI / "primary-responses.json", tmp_path / "responses.json")],
    )
    sources = sorted(
        path for path in (tmp_path / "server").rglob("*.py") if not {"_runtime", "_generated"} & set(path.parts)
    )
    assert_output(
        "".join(f"# {path.relative_to(tmp_path).as_posix()}\n{path.read_text(encoding='utf-8')}" for path in sources),
        EXPECTED / "cli" / "configured.txt",
    )


@pytest.mark.parametrize(
    ("arguments", "stderr"),
    [
        (["--server-handler-modes", '"async"'], "Invalid --server-handler-modes: Input should be a valid dictionary"),
        (
            ["--server-handler-modes", '{"/paths/~1pets/get": "parallel"}'],
            "Invalid --server-handler-modes: /paths/~1pets/get: Input should be 'sync' or 'async'",
        ),
        (
            ["--server-body-modes", "{"],
            (
                "Invalid JSON for --server-body-modes: Expecting property name enclosed in double quotes: "
                "line 1 column 2 (char 1)"
            ),
        ),
        (
            ["--server-primary-responses", '{"/paths/~1pets/post": {"status_code": "201"}}'],
            "Invalid --server-primary-responses: /paths/~1pets/post.status_code: Input should be a valid integer",
        ),
        (
            ["--server-primary-responses", '{"/paths/~1pets/post": {"status_code": 700}}'],
            (
                "Invalid --server-primary-responses: /paths/~1pets/post.status_code: "
                "Input should be less than or equal to 599"
            ),
        ),
        (
            ["--server-primary-responses", '{"/paths/~1pets/post": {"status": 201}}'],
            "Invalid --server-primary-responses: /paths/~1pets/post.status_code: Field required",
        ),
        (
            ["--server-operation-names", '{"/paths/~1pets/get": 7}'],
            "Invalid --server-operation-names: /paths/~1pets/get: Input should be a valid string",
        ),
        (
            ["--server-router-names", "missing.json"],
            "Invalid JSON for --server-router-names: Expecting value: line 1 column 1 (char 0)",
        ),
        (
            ["--server-parameter-names", '{"/paths/~1pets/get": "page_size"}'],
            "Invalid --server-parameter-names: /paths/~1pets/get: Input should be a valid dictionary",
        ),
    ],
    ids=[
        "not-object",
        "mode",
        "json",
        "status-type",
        "status-range",
        "unknown-key",
        "name-type",
        "missing-file",
        "names-type",
    ],
)
def test_fastapi_cli_json_errors(
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse a JSON server option whose value does not have the documented shape, like a JSON model option."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=f"{stderr}\n",
        output_should_not_exist=True,
    )


def test_fastapi_cli_pyproject_json_error(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a pyproject.toml server table whose value does not have the documented shape."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        copy_files=_inputs(tmp_path, "pyproject-invalid.toml"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="Invalid --server-body-modes: /paths/~1pets/post: Input should be 'typed' or 'request'\n",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        (
            "values",
            [
                *("--server-operation-names", '{"/paths/~1pets/get": "class"}'),
                *("--server-router-names", '{"tag:pets": "1pets"}'),
                *("--server-parameter-names", '{"/paths/~1pets/get": {"limit": "page_size"}}'),
            ],
        ),
        (
            "operations",
            [
                *("--server-handler-modes", '{"/paths/~1cats/get": "async"}'),
                *("--server-body-modes", '{"other.yaml#/paths/~1pets/post": "request"}'),
            ],
        ),
    ],
)
def test_fastapi_cli_setting_errors(
    name: str, arguments: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report every server setting the server cannot use, before any file is written."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED / "cli" / "setting-errors" / f"{name}.txt")


@pytest.mark.parametrize(
    ("arguments", "generated", "expected"),
    [
        pytest.param(
            [],
            "services.py",
            "services.py",
            id="generate-server",
            marks=pytest.mark.cli_doc(
                options=["--generate-server"],
                option_description="""Generate a FastAPI server package for the models (experimental).

`--generate-server fastapi` generates the models of an OpenAPI document as usual and, in the same run, a FastAPI
server package at `--server-output`: routers, a service Protocol for each router group, and the application. The
models need the `api` scope (`--openapi-scopes schemas api`), a Pydantic v2 output model type, and a target Python
version of 3.11 or later. Like every server setting, it can also be set in `[tool.datamodel-codegen]` of
pyproject.toml, here as `generate-server = "fastapi"`.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/services.py",
            ),
        ),
        pytest.param(
            [],
            "services.py",
            "services.py",
            id="server-output",
            marks=pytest.mark.cli_doc(
                options=["--server-output"],
                option_description="""Write the server package to this directory (experimental).

`--server-output` is required with `--generate-server`. A path given on the command line is relative to the working
directory, and the `server-output` key of pyproject.toml is relative to the pyproject.toml directory, as for
`--output`.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/services.py",
            ),
        ),
        pytest.param(
            [],
            "services.py",
            "services.py",
            id="server-package",
            marks=pytest.mark.cli_doc(
                options=["--server-package"],
                option_description="""Name the import path of the server package (experimental).

`--server-package` is required with `--generate-server`. The generated README and the dependency command a
generation prints name the package by it; the package imports its own modules relatively.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/services.py",
            ),
        ),
        pytest.param(
            [],
            "services.py",
            "services.py",
            id="server-model-package",
            marks=pytest.mark.cli_doc(
                options=["--server-model-package"],
                option_description="""Name the import path of the models the server package imports (experimental).

`--server-model-package` is required with `--generate-server`, and names the module or package `--output`
generates.""",
                input_schema=DOC_INPUT,
                cli_args=DOC_OPTIONS,
                golden_output=f"{DOC_OUTPUT}/services.py",
            ),
        ),
        pytest.param(
            ["--server-layout", "single"],
            "routes.py",
            "layout/routes.py",
            id="server-layout",
            marks=pytest.mark.cli_doc(
                options=["--server-layout"],
                option_description="""Choose how the server package lays out its routes (experimental).

`routers` (the default) writes one router module per tag under `routers/`; `single` writes every route to one
`routes.py` module.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-layout", "single"],
                golden_output=f"{DOC_OUTPUT}/layout/routes.py",
            ),
        ),
        pytest.param(
            ["--server-handler-mode", "async"],
            "services.py",
            "handler-mode/services.py",
            id="server-handler-mode",
            marks=pytest.mark.cli_doc(
                options=["--server-handler-mode"],
                option_description="""Declare the service methods as plain or coroutine functions (experimental).

`sync` (the default) declares plain methods, which FastAPI runs in a thread pool; `async` declares coroutine
methods. `--server-handler-modes` overrides it for single operations.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-handler-mode", "async"],
                golden_output=f"{DOC_OUTPUT}/handler-mode/services.py",
            ),
        ),
        pytest.param(
            ["--server-handler-modes", '{"/paths/~1pets/post": "async"}'],
            "services.py",
            "handler-modes/services.py",
            id="server-handler-modes",
            marks=pytest.mark.cli_doc(
                options=["--server-handler-modes"],
                option_description="""Set the handler mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `sync` or `async`, and overrides
`--server-handler-mode` for those operations. An operation reference is the JSON pointer of the path item method,
such as `/paths/~1pets/get`, optionally after a document and `#`, such as `pets.yaml#/paths/~1pets/get`. In
pyproject.toml, `server-handler-modes` is a table, and a command-line value replaces the whole table.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-handler-modes", '{"/paths/~1pets/post": "async"}'],
                golden_output=f"{DOC_OUTPUT}/handler-modes/services.py",
            ),
        ),
        pytest.param(
            ["--server-include-request"],
            "services.py",
            "include-request/services.py",
            id="server-include-request",
            marks=pytest.mark.cli_doc(
                options=["--server-include-request"],
                option_description="""Pass the Starlette Request to every service method (experimental).

Each service method takes a `request` keyword argument as well as the operation's arguments.
`--no-server-include-request` turns off a `server-include-request = true` of pyproject.toml.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-include-request"],
                golden_output=f"{DOC_OUTPUT}/include-request/services.py",
            ),
        ),
        pytest.param(
            ["--server-body-mode", "request"],
            "services.py",
            "body-mode/services.py",
            id="server-body-mode",
            marks=pytest.mark.cli_doc(
                options=["--server-body-mode"],
                option_description="""Choose how service methods receive request bodies (experimental).

`typed` (the default) passes the body validated as its model; `request` passes the raw Starlette `Request` instead,
for methods that read the body themselves. `--server-body-modes` overrides it for single operations.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-body-mode", "request"],
                golden_output=f"{DOC_OUTPUT}/body-mode/services.py",
            ),
        ),
        pytest.param(
            ["--server-body-modes", '{"/paths/~1pets~1{name}/put": "request"}'],
            "services.py",
            "body-modes/services.py",
            id="server-body-modes",
            marks=pytest.mark.cli_doc(
                options=["--server-body-modes"],
                option_description="""Set the body mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `typed` or `request`, and overrides
`--server-body-mode` for those operations.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-body-modes", '{"/paths/~1pets~1{name}/put": "request"}'],
                golden_output=f"{DOC_OUTPUT}/body-modes/services.py",
            ),
        ),
        pytest.param(
            ["--server-primary-responses", '{"/paths/~1pets/post": {"status_code": 201}}'],
            "routers/pets.py",
            "primary-responses/pets.py",
            id="server-primary-responses",
            marks=pytest.mark.cli_doc(
                options=["--server-primary-responses"],
                option_description="""Choose the response a bare return value of an operation takes (experimental).

The JSON object, inline or in a file, maps operation references to an object with the `status_code` of a declared
response and, when that response has several media types, its `media_type`. Without an entry, the server infers the
primary response from the declared success responses.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-primary-responses", '{"/paths/~1pets/post": {"status_code": 201}}'],
                golden_output=f"{DOC_OUTPUT}/primary-responses/pets.py",
            ),
        ),
        pytest.param(
            ["--server-operation-names", '{"/paths/~1pets/get": "list_all"}'],
            "services.py",
            "operation-names/services.py",
            id="server-operation-names",
            marks=pytest.mark.cli_doc(
                options=["--server-operation-names"],
                option_description="""Name the service methods of single operations (experimental).

The JSON object, inline or in a file, maps operation references to method names. Other operations take the
snake_case form of their operationId, or of their method and path.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-operation-names", '{"/paths/~1pets/get": "list_all"}'],
                golden_output=f"{DOC_OUTPUT}/operation-names/services.py",
            ),
        ),
        pytest.param(
            ["--server-router-names", '{"tag:pets": "animals"}'],
            "services.py",
            "router-names/services.py",
            id="server-router-names",
            marks=pytest.mark.cli_doc(
                options=["--server-router-names"],
                option_description="""Name router groups (experimental).

The JSON object, inline or in a file, maps group keys, such as `tag:pets` for the operations whose first tag is
`pets`, to the name of the router module, the `create_app` argument, and the `<Name>Service` Protocol.""",
                input_schema=DOC_INPUT,
                cli_args=[*DOC_OPTIONS, "--server-router-names", '{"tag:pets": "animals"}'],
                golden_output=f"{DOC_OUTPUT}/router-names/services.py",
            ),
        ),
        pytest.param(
            ["--server-parameter-names", '{"/paths/~1pets/get": {"query:limit": "page_size"}}'],
            "services.py",
            "parameter-names/services.py",
            id="server-parameter-names",
            marks=pytest.mark.cli_doc(
                options=["--server-parameter-names"],
                option_description="""Name the method arguments of single operations (experimental).

The JSON object, inline or in a file, maps operation references to objects that map a parameter, written as its
location and name such as `query:limit` or `header:X-Request-Id`, to the argument name.""",
                input_schema=DOC_INPUT,
                cli_args=[
                    *DOC_OPTIONS,
                    "--server-parameter-names",
                    '{"/paths/~1pets/get": {"query:limit": "page_size"}}',
                ],
                golden_output=f"{DOC_OUTPUT}/parameter-names/services.py",
            ),
        ),
    ],
)
def test_fastapi_cli_options(
    arguments: list[str], generated: str, expected: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the server file each server option changes."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("options.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=[*OPTIONS, *SERVER, *arguments],
        copy_files=[(CLI / "options.yaml", tmp_path / "options.yaml")],
    )
    assert_file_content(tmp_path / "server" / generated, f"cli/options/{expected}")


@pytest.mark.parametrize("report", [False, True])
def test_fastapi_cli_failures(
    report: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unresolved reference and an unexpected formatter failure without publishing files."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("broken.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--strict-refs", *(["--diagnostics-json", "-"] if report else [])),
        copy_files=[(SOURCE / "unresolved-ref.yaml", tmp_path / "broken.yaml")],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    captured = capsys.readouterr()
    assert_output(captured.err, EXPECTED / "cli" / "unresolved.txt")
    if report:
        assert_output(captured.out, EXPECTED / "cli" / "unresolved-report.txt")

    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--custom-formatters", "tests.data.python.custom_formatters.stop"),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="RuntimeError: The formatter stopped\n",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("case", ["class-name", "encoding", "lock", "output-parent"])
def test_fastapi_cli_model_error_context(
    case: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep model error hints, decoding context, lock errors, and filesystem errors without publishing files."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    options: list[str] = []
    output = Path("models.py")
    stderr = "Error: "
    match case:
        case "class-name":
            options = [
                "--custom-formatters",
                "tests.data.python.custom_formatters.stop",
                "--custom-formatters-kwargs",
                '{"class_name": "1Xyz"}',
            ]
            stderr = "Error: title='1Xyz' is invalid class name. You have to set `--class-name` option\n"
        case "encoding":
            (tmp_path / "pets.yaml").write_bytes(b"\xff")
            stderr = "Error: Unable to decode input using encoding 'utf-8': "
        case "lock":
            (tmp_path / "api.lock").write_text("{broken", encoding="utf-8")
            options = ["--lockfile", "api.lock"]
            stderr = "Error: Unable to read remote lock "
        case _:
            (tmp_path / "occupied").write_text("occupied\n", encoding="utf-8")
            output = Path("occupied/models.py")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=output,
        input_file_type="openapi",
        extra_args=_server(*options),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=stderr,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("report", [False, True])
@pytest.mark.parametrize("disabled", [False, True])
def test_fastapi_cli_unowned_manifest(
    report: bool, disabled: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep the unowned-state warning separate from the error that refuses to overwrite existing files."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    (server := tmp_path / "server").mkdir()
    (server / ".dcg-target-manifest.json").write_text("{}", encoding="utf-8")
    (server / "application.py").write_text("# User application\n", encoding="utf-8")
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=Path("pets.yaml"),
            output_path=Path("models.py"),
            input_file_type="openapi",
            extra_args=_server(
                *(["--diagnostics-json", "-"] if report else []), *(["--disable-warnings"] if disabled else [])
            ),
            expected_exit=Exit.ERROR,
            capsys=capsys,
            expected_stdout_path=EXPECTED / "cli" / "unowned-report.txt" if report else None,
            expected_stderr="Error: application.py: An unmanaged file occupies a path the target owns\n",
            output_should_not_exist=True,
        )
    assert_output(
        "\n".join(f"{item.category.__name__}: {item.message}" for item in recorded),
        EXPECTED / "cli" / ("no-warning.txt" if disabled else "unowned-warning.txt"),
    )
