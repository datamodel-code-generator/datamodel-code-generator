"""Generate the FastAPI server target from the command line: publish, check, report, and overwrite like models."""

from __future__ import annotations

import json
import shutil
import warnings
from pathlib import Path

import pytest

from datamodel_code_generator import (
    DataModelType,
    InputFileType,
    OpenAPIScope,
    PythonVersion,
    ServerBodyMode,
    ServerHandlerMode,
    ServerLayout,
    ServerType,
    generate,
    get_version,
    load_pyproject_config,
)
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.format import Formatter
from tests.conftest import assert_directory_content, assert_output, create_assert_file_content, freeze_time
from tests.data.python.custom_formatters.shift_frozen_time import CodeFormatter
from tests.main.conftest import TIMESTAMP, run_main_and_assert, run_main_with_args, run_main_with_system_exit

DATA = Path(__file__).parents[1] / "data"
SOURCE = DATA / "generation_platform" / "fastapi"
CLI = SOURCE / "cli"
EXPECTED = DATA / "expected" / "main" / "generation_platform" / "fastapi"
PACKAGE = EXPECTED / "packages" / "pets" / "pydantic_v2_BaseModel"
DEPENDENCIES = EXPECTED / "cli" / "dependencies.txt"
SHARED_DEPENDENCIES = EXPECTED / "cli" / "shared-models-dependencies.txt"
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
DOCUMENT_BASE = (
    "A relative document of an operation reference resolves against the JSON file that holds the reference, or\n"
    "without a file against the working directory, and against the pyproject.toml directory for a table."
)
CHECK_CASES = json.loads((CLI / "check-cases.json").read_text(encoding="utf-8"))
JSON_CASES = json.loads((CLI / "json-cases.json").read_text(encoding="utf-8"))
REMOVED_OPTIONS = json.loads((CLI / "removed-options.json").read_text(encoding="utf-8"))
LAYOUT_ERRORS = json.loads((CLI / "layout-errors.json").read_text(encoding="utf-8"))
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


def test_fastapi_cli_generate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models and the server package, check an edit and a missing generated file, then rewrite both."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
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
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_directory_content(tmp_path / "server", PACKAGE / "server")


@pytest.mark.parametrize("case_name", ["unchanged", "models-in-package", "package-in-models"])
@pytest.mark.parametrize("structured", [False, True], ids=["text", "json"])
def test_fastapi_cli_check_read_only(
    case_name: str,
    structured: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Check outputs whose directories cannot be written to: a check creates nothing inside the outputs it compares.

    The outputs lie beside each other, or one inside the other. A model directory in such a nested layout is not
    importable under its own name, as the model checks of the helper import it, so they are left out for it.
    """
    case = CHECK_CASES[case_name]
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
    source = Path("spec/modular.yaml" if case.get("modular") else "pets.yaml")
    output = Path(case.get("model", "models" if case.get("modular") else "models.py"))
    server = case.get("server", "server")
    arguments = case.get("arguments", ())
    nested_package = bool(case.get("modular")) and bool({"model", "server"} & case.keys())
    run_main_and_assert(
        input_path=source,
        input_file_type="openapi",
        output_path=output,
        extra_args=_server(*arguments, output=server),
        capsys=capsys,
        expected_stderr=(EXPECTED / "cli" / case.get("notice", DEPENDENCIES.name)).read_text(encoding="utf-8"),
        skip_code_validation=nested_package,
    )
    roots = [root for root in (tmp_path / server, tmp_path / output) if root.is_dir()]
    directories = [*roots, *(path for root in roots for path in root.rglob("*") if path.is_dir())]
    for directory in directories:
        directory.chmod(0o555)
    try:
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=output,
            extra_args=_server(
                "--check", *arguments, *(["--output-format", "json"] if structured else []), output=server
            ),
            capsys=capsys,
            assert_no_stderr=True,
            expected_stdout_path=EXPECTED / "cli" / "check" / f"unchanged.{'json' if structured else 'txt'}",
            skip_code_validation=nested_package,
        )
    finally:
        for directory in directories:
            directory.chmod(0o755)


@pytest.mark.parametrize("case_name", CHECK_CASES)
@pytest.mark.parametrize("structured", [False, True], ids=["text", "json"])
def test_fastapi_cli_check_outputs(
    case_name: str,
    structured: bool,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Compare real model and target text without publishing or changing any output file.

    A fresh case checks a tree no generation wrote; a regenerate case generates over its edits before the check.
    A case can name its model and server outputs, with the arguments that give their import paths. A model
    directory in such a nested layout is not importable under its own name, as the model checks of the helper
    import it, so they are left out for it.
    """
    case = CHECK_CASES[case_name]
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    output = Path(case.get("model", "models" if case.get("modular") else "models.py"))
    server = case.get("server", "server")
    notice = EXPECTED / "cli" / case.get("notice", DEPENDENCIES.name)
    source = Path("pets.yaml")
    if case.get("modular") or case.get("source"):
        shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
        source = Path("spec") / case.get("source", "modular.yaml")
    encoding = case.get("encoding", "utf-8")
    options: list[str] = ["--encoding", encoding, *case.get("arguments", ())]
    unvalidated = encoding != "utf-8" or (bool(case.get("modular")) and bool({"model", "server"} & case.keys()))
    if case.get("templates"):
        shutil.copytree(CLI / "check-templates", tmp_path / "templates")
        options.extend(["--custom-template-dir", "templates"])
    if not case.get("fresh"):
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=output,
            extra_args=_server(*options, output=server),
            capsys=capsys,
            expected_stderr=notice.read_text(encoding="utf-8"),
            skip_code_validation=unvalidated,
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
    endings = [*((name, "\r\n") for name in case.get("crlf", ())), *((name, "\n") for name in case.get("lf", ()))]
    for name, newline in endings:
        (path := tmp_path / name).write_text(path.read_text(encoding=encoding), encoding=encoding, newline=newline)
    for name in case.get("binary", ()):
        (tmp_path / name).write_bytes((CLI / "non-text.dat").read_bytes())
    if case.get("regenerate"):
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=output,
            extra_args=_server(*options, *case.get("options", ()), output=server),
            capsys=capsys,
            expected_stderr=notice.read_text(encoding="utf-8"),
            skip_code_validation=unvalidated,
        )
    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    if case.get("nested"):
        monkeypatch.chdir(tmp_path / "server")
    base = Path("..") if case.get("nested") else Path()
    changed = any(key in case for key in ("remove", "append", "binary"))
    changed |= "write" in case and any(name.endswith(".py") and "__pycache__" not in name for name in case["write"])
    changed = case.get("changed", changed)
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
                output=str(base / server),
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
            skip_code_validation=unvalidated,
        )
    after = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert_output(
        f"files unchanged {before == after}; warnings {len(recorded)}\n", EXPECTED / "cli" / "check-unchanged.txt"
    )


@pytest.mark.parametrize("case_name", JSON_CASES)
def test_fastapi_cli_generation_json(
    case_name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print one generation payload whose paths compose with its output, and name files the same way in a check.

    A case can write files before the run, which overwrites those at its paths and leaves the others. A stale case
    regenerates fewer files, so the check that follows lists what the generation left in place as extra. An
    unvalidated case leaves out the model checks of the helper, which import a model directory under its own name.
    """
    case = JSON_CASES[case_name]
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    source = Path(shutil.copy2(SOURCE / name, name)) if (name := case.get("input")) else Path("pets.yaml")
    if name := case.get("source"):
        shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
        source = Path("spec") / name
    absolute = case.get("absolute", ())
    model = (tmp_path if "model" in absolute else Path()) / case.get("model", "models.py")
    server = (tmp_path if "server" in absolute else Path()) / case.get("server", "server")
    encoding = case.get("encoding", "utf-8")
    notice = EXPECTED / "cli" / case.get("notice", DEPENDENCIES.name)
    unvalidated = encoding != "utf-8" or case.get("unvalidated", False)
    options = ["--output-format", "json", "--encoding", encoding, *case.get("options", ())]
    if case.get("templates"):
        shutil.copytree(CLI / "check-templates", tmp_path / "templates")
        options.extend(["--custom-template-dir", "templates"])
    if case.get("repeat"):
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=model,
            extra_args=_server("--encoding", encoding, output=str(server)),
            capsys=capsys,
            expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
        )
    for name, text in case.get("write", {}).items():
        (path := tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding=encoding)
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=model,
            extra_args=_server(*options, *case.get("publish", ()), output=str(server)),
            skip_code_validation=unvalidated,
        )
    captured = capsys.readouterr()
    assert_output(captured.err, notice)
    if payload := case.get("payload"):
        assert_output(captured.out.replace(tmp_path.as_posix(), "<root>"), EXPECTED / "cli" / "json" / f"{payload}.txt")
    if case.get("package"):
        assert_file_content(tmp_path / model, PACKAGE / "models.py")
        assert_directory_content(tmp_path / server, PACKAGE / "server")
    for name in case.get("published", ()):
        assert_file_content(tmp_path / name, EXPECTED / "cli" / "json" / "published" / f"{name}.txt")
    stale = case.get("stale")
    with warnings.catch_warnings(record=True) as checked:
        warnings.simplefilter("always", UserWarning)
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=model,
            extra_args=_server("--check", *options, output=str(server)),
            capsys=capsys,
            assert_no_stderr=True,
            expected_exit=Exit.DIFF if stale else Exit.OK,
            expected_stdout_path=EXPECTED
            / "cli"
            / (Path("json", "check", f"{stale}.txt") if stale else Path("check", "unchanged.json")),
            skip_code_validation=unvalidated,
        )
    assert_output(
        "\n".join([
            *(f"{item.category.__name__}: {item.message}" for item in recorded),
            *(f"check {item.category.__name__}: {item.message}" for item in checked),
        ]),
        EXPECTED / "cli" / case.get("warnings", "no-warning.txt"),
    )
    if removed := case.get("remove"):
        for name in removed:
            if (path := tmp_path / name).is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        run_main_and_assert(
            input_path=source,
            input_file_type="openapi",
            output_path=model,
            extra_args=_server("--check", *options, output=str(server)),
            expected_exit=Exit.DIFF,
            skip_code_validation=unvalidated,
        )
        assert_output(
            capsys.readouterr().out.replace(tmp_path.as_posix(), "<root>"),
            EXPECTED / "cli" / "json" / "check" / f"{case['check']}.txt",
        )


def test_fastapi_cli_pyproject(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the same models and package from pyproject.toml alone as from the options, then check them."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-server.toml")
    run_main_with_args([], capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
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
    run_main_with_args(arguments, capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    report = "".join(_methods(path) for path in sorted(tmp_path.glob("*/services.py")))
    assert_output(
        f"$ datamodel-codegen {' '.join(arguments)}\n{report}", EXPECTED / "cli" / "precedence" / f"{name}.txt"
    )


def test_fastapi_cli_pyproject_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Resolve pyproject.toml paths and JSON files, and the operation documents of its tables, against its directory.

    Command-line paths and JSON files resolve against the working directory. The operation documents that a JSON
    file names resolve against the file's directory.
    """
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    _copy(project, "pyproject-paths.toml")
    shutil.copy2(CLI / "names.json", project / "names.json")
    (work / "names.json").write_text('{"/paths/~1pets/get": "find_pets"}', encoding="utf-8")
    monkeypatch.chdir(work)
    run_main_with_args([], capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    run_main_with_args(
        ["--server-output", "service", "--server-operation-names", "names.json"],
        capsys=capsys,
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
    )
    services = sorted(tmp_path.rglob("services.py"), key=lambda path: path.relative_to(tmp_path).as_posix())
    assert_output(
        "".join(f"# in {path.parent.parent.relative_to(tmp_path).as_posix()}\n{_methods(path)}" for path in services),
        EXPECTED / "cli" / "pyproject-paths.txt",
    )


@pytest.mark.parametrize(
    ("pyproject", "arguments"),
    [
        (
            [],
            [
                *("--input", "../api/pets.yaml", "--input-file-type", "openapi", "--output", "../models.py"),
                *_server("--server-handler-modes", "../api/modes.json", output="../server"),
            ],
        ),
        (["pyproject-json-files.toml"], []),
    ],
    ids=["option", "pyproject"],
)
def test_fastapi_cli_json_file_documents(
    pyproject: list[str],
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resolve the documents that the operation references of a JSON file name against the file's directory."""
    project, work = tmp_path / "project", tmp_path / "project" / "work"
    work.mkdir(parents=True)
    shutil.copytree(CLI / "json-files", project / "api")
    shutil.copy2(SOURCE / "pets.yaml", project / "api" / "pets.yaml")
    for name in pyproject:
        shutil.copy2(CLI / name, project / "pyproject.toml")
    monkeypatch.chdir(work)
    run_main_with_args(arguments, capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    assert_output(_methods(project / "server" / "services.py"), EXPECTED / "cli" / "json-file-documents.txt")


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
        extra_args=_server(*include, output="service"),
        copy_files=_inputs(tmp_path),
        capsys=capsys,
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
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
        extra_args=_server("--check", *include, output="service"),
        capsys=capsys,
        assert_no_stderr=True,
    )


def test_fastapi_cli_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the OpenAPI document from standard input, check the result, and refuse an unsupported backend."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        stdin_path=SOURCE / "pets.yaml",
        monkeypatch=monkeypatch,
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        capsys=capsys,
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"),
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
            [*OPTIONS, *PACKAGES, "--server-include-request"],
            "--server-package, --server-model-package and --server-include-request require --generate-server",
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


@pytest.mark.parametrize("case_name", REMOVED_OPTIONS)
def test_fastapi_cli_removed_options(
    case_name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse the removed reporting options as unknown options, before anything is generated."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path)
    run_main_with_system_exit(
        [
            *("--input", "pets.yaml", "--input-file-type", "openapi", "--output", "models.py"),
            *_server(*REMOVED_OPTIONS[case_name]),
        ],
        expected_code=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(EXPECTED / "cli" / "removed-options" / f"{case_name}.txt").read_text(
            encoding="utf-8"
        ),
    )
    assert_output(
        "".join(f"{path.name}\n" for path in sorted(tmp_path.iterdir())),
        EXPECTED / "cli" / "removed-options" / "files.txt",
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
    "layout",
    [[], ["--output", "server/models.py", "--server-model-package", "server.models"]],
    ids=["beside", "models-in-package"],
)
@pytest.mark.parametrize(
    "formatters", [[], ["--formatters", "ruff-check", "ruff-format"]], ids=["default", "ruff-isort-rules"]
)
def test_fastapi_cli_check_after_generate(
    formatters: list[str], layout: list[str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Find nothing to change in a copy of a fresh generation, though its models were still staged when formatted.

    The copy also gives isort, which caches where it places a module per configuration, a configuration of its own.
    Ruff reads the server directory's name as the package name while the server files are still staged. The models
    lie beside the server package or inside it, where they are staged beside the package.
    """
    generated, copy = tmp_path / "generated", tmp_path / "copy"
    generated.mkdir()
    monkeypatch.chdir(generated)
    _copy(generated, "pyproject-ruff.toml")
    arguments = [
        *("--input", "pets.yaml", "--input-file-type", "openapi", "--output", "models.py"),
        *PYTHON,
        *SCOPES,
        *BACKEND,
        "--disable-timestamp",
        *formatters,
        *SERVER,
        *layout,
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
        expected_stderr="Warning: --reuse-scope=tree has no effect without --reuse-model\n"
        + DEPENDENCIES.read_text(encoding="utf-8"),
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
    ("arguments", "stderr"),
    [
        (["--watch", "--output-format", "json"], f"{CONFLICT} --watch\n"),
        (["--diff-against", "pets.yaml"], f"{CONFLICT} --diff-against\n"),
        (
            ["--update-lock", "--lockfile", "server/api.lock"],
            "Remote lock for 'command' ({lock}) overlaps server output for 'command': {server}\n",
        ),
    ],
    ids=["watch", "diff", "lock-in-server-output"],
)
def test_fastapi_cli_conflicts(
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse options the target cannot honor, and a remote lock inside the server output, before writing anything."""
    monkeypatch.chdir(tmp_path)
    root = tmp_path.resolve()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path, "pyproject-target.toml"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr.format(lock=root / "server" / "api.lock", server=root / "server"),
        output_should_not_exist=True,
    )
    assert_output(
        (tmp_path / "pyproject.toml").read_text(encoding="utf-8"),
        EXPECTED / "cli" / "kept" / "pyproject-target.toml.txt",
    )


def test_fastapi_cli_job(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the models and the package of a server job as a single run does, check them, then check a drift."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-jobs.toml")
    run_main_with_args(["--job", "server"], capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")
    assert_directory_content(tmp_path / "server", PACKAGE / "server")
    run_main_with_args(["--job", "server", "--check"], capsys=capsys, assert_no_stderr=True)
    (tmp_path / "models.py").write_text("# edited\n", encoding="utf-8")
    (tmp_path / "server" / "README.md").unlink()
    run_main_with_args(
        ["--all-jobs", "--check"],
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check-model-readme.txt",
        assert_no_stderr=True,
    )


@pytest.mark.parametrize("name", LAYOUT_ERRORS)
def test_fastapi_cli_layout_errors(
    name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Refuse a model file at a server file's path and a module beside a generated package directory of its name.

    Python would import the package directory instead of the module, so nothing is written.
    """
    case = LAYOUT_ERRORS[name]
    monkeypatch.chdir(tmp_path)
    source = case.get("input", "pets.yaml")
    shutil.copy2((CLI if "input" in case else SOURCE) / source, tmp_path / source)
    run_main_and_assert(
        input_path=Path(source),
        output_path=Path(case["output"]),
        input_file_type="openapi",
        extra_args=_server(*case["arguments"], output=case.get("server", "server")),
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    written = sorted(path.name for path in tmp_path.iterdir() if path.name != source)
    assert_output(f"{capsys.readouterr().err}written {written}\n", EXPECTED / "cli" / "layout-errors" / f"{name}.txt")


def test_fastapi_cli_nested_models_example(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run the documented command that keeps the models inside the server package, then find nothing to change.

    The command is run as documented: its preset chooses the formatters, so the helper adds no formatter settings.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copy2(SOURCE / "pets.yaml", tmp_path / "api.yaml")
    arguments = json.loads((CLI / "nested-models-example.json").read_text(encoding="utf-8"))
    run_main_with_args(
        arguments,
        capsys=capsys,
        expected_stderr=(EXPECTED / "cli" / "dependencies-app-server.txt").read_text(encoding="utf-8"),
        use_builtin_default_formatter=False,
    )
    run_main_with_args(
        [*arguments, "--check"], capsys=capsys, assert_no_stderr=True, use_builtin_default_formatter=False
    )
    assert_output(_methods(tmp_path / "app" / "server" / "services.py"), EXPECTED / "cli" / "nested-models-example.txt")


def test_fastapi_cli_nested_job(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models of a server job inside its own package, as a single run can, then check both as one tree."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-nested-jobs.toml")
    run_main_with_args(["--all-jobs"], capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    assert_file_content(tmp_path / "server" / "models.py", PACKAGE / "models.py")
    run_main_with_args(["--all-jobs", "--check"], capsys=capsys, assert_no_stderr=True)
    (tmp_path / "server" / "extensions.py").write_text("# User extension\n", encoding="utf-8")
    run_main_with_args(
        ["--all-jobs", "--check"],
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "check" / "extra-python.txt",
        assert_no_stderr=True,
    )


def test_fastapi_cli_mixed_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Run a model-only job and a server job together; both write the models a model-only run writes."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-mixed-jobs.toml")
    run_main_with_args(["--all-jobs"], capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    assert_file_content(tmp_path / "schemas.py", PACKAGE / "models.py")
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")
    assert_directory_content(tmp_path / "server", PACKAGE / "server")
    run_main_with_args(["--all-jobs", "--check"], capsys=capsys, assert_no_stderr=True)


@pytest.mark.parametrize(
    ("case", "pyproject", "arguments", "packages", "check"),
    [
        ("target-module", [], ["--input", "pets.yaml", *DOC_OPTIONS], ["server/services"], Exit.DIFF),
        ("models", [], ["--input", "pets.yaml", *DOC_OPTIONS], ["models"], Exit.OK),
        ("mixed-jobs", ["pyproject-mixed-jobs.toml"], ["--all-jobs"], ["schemas", "models"], Exit.OK),
    ],
    ids=["target-module", "models", "mixed-jobs"],
)
def test_fastapi_cli_shadowed_modules(
    case: str,
    pyproject: list[str],
    arguments: list[str],
    packages: list[str],
    check: Exit,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report each planned module beside a package of its name, on the run that writes it and on an unchanged one.

    The models of a server run, its target modules, and the model-only jobs of its batch are reported alike, and
    --check, which writes nothing, reports none.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, *pyproject)
    for package in packages:
        (tmp_path / package).mkdir(parents=True)
        (tmp_path / package / "__init__.py").touch()
    lines = []
    for run, options, expected_exit in (
        ("first", [], Exit.OK),
        ("unchanged", [], Exit.OK),
        ("check", ["--check"], check),
    ):
        with warnings.catch_warnings(record=True) as recorded:
            warnings.simplefilter("always", UserWarning)
            run_main_with_args([*arguments, *options], expected_exit=expected_exit)
        lines.append(f"# {run} run")
        lines.extend(
            f"{item.category.__name__}: {str(item.message).replace(tmp_path.as_posix(), '<root>')}" for item in recorded
        )
    assert_output("\n".join(lines) + "\n", EXPECTED / "cli" / "shadowed-modules" / f"{case}.txt")


def test_fastapi_cli_jobs_json(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a server job in the batch document with the payloads and the warnings of a single server run."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-mixed-jobs.toml")
    run_main_with_args(["--all-jobs", "--output-format", "json"])
    generated = capsys.readouterr()
    lines = []
    for job in json.loads(generated.out)["jobs"]:
        result = job["result"]
        lines.append(f"job {job['name']}; kind {result['kind']}; output {result['output']}")
        lines.extend(
            f"file {item['path']}; matches published {item['content'] == (tmp_path / item['path']).read_text('utf-8')}"
            for item in result["files"]
        )
    with (tmp_path / "server" / "services.py").open("a", encoding="utf-8") as services:
        services.write("# edited\n")
    (tmp_path / "server" / "README.md").unlink()
    with warnings.catch_warnings(record=True) as recorded:
        warnings.simplefilter("always", UserWarning)
        run_main_with_args(["--all-jobs", "--check", "--output-format", "json"], expected_exit=Exit.DIFF)
        checked = capsys.readouterr()
        lines.append(f"check warnings {len(recorded)}; stderr {checked.err!r}")
        for job in json.loads(checked.out)["jobs"]:
            result = job["result"]
            lines.append(f"job {job['name']}; kind {result['kind']}; success {result['success']}")
            lines.extend(f"difference {item['kind']} {item['path']}" for item in result["differences"])
        run_main_with_args(["--all-jobs", "--output-format", "json"])
    lines.extend(f"{item.category.__name__}: {item.message}" for item in recorded)
    assert_output(generated.err, DEPENDENCIES)
    assert_output(
        "\n".join(lines).replace(Path.cwd().as_posix(), "<root>") + "\n", EXPECTED / "cli" / "json" / "jobs.txt"
    )


def test_fastapi_cli_jobs_json_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Name the files of each server job in the batch document relative to that job's model output directory."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-layout-jobs.toml")
    shutil.copytree(DATA / "generation_platform" / "targets" / "spec", tmp_path / "spec")
    run_main_with_args(
        ["--all-jobs", "--output-format", "json"],
        capsys=capsys,
        expected_stderr=DEPENDENCIES.read_text(encoding="utf-8") * 2,
    )
    run_main_with_args(
        ["--all-jobs", "--check", "--output-format", "json"],
        capsys=capsys,
        assert_no_stderr=True,
        expected_stdout_path=EXPECTED / "cli" / "json" / "check" / "jobs-unchanged.txt",
    )
    for name in ("nested/models.py", "nested/server/README.md", "modular/models/pets.py", "modular/server/services.py"):
        (tmp_path / name).unlink()
    run_main_with_args(["--all-jobs", "--check", "--output-format", "json"], expected_exit=Exit.DIFF)
    assert_output(
        capsys.readouterr().out.replace(tmp_path.as_posix(), "<root>"),
        EXPECTED / "cli" / "json" / "check" / "jobs.txt",
    )


def test_fastapi_cli_shared_models_jobs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the models two server jobs share once, next to the package of each job, then check them."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-shared-models-jobs.toml")
    run_main_with_args(["--all-jobs"], capsys=capsys, expected_stderr=SHARED_DEPENDENCIES.read_text(encoding="utf-8"))
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")
    assert_directory_content(tmp_path / "server", PACKAGE / "server")
    assert_output(_methods(tmp_path / "admin" / "services.py"), EXPECTED / "cli" / "shared-models.txt")
    run_main_with_args(["--all-jobs", "--check"], capsys=capsys, assert_no_stderr=True)


def test_fastapi_cli_shared_models_timestamp(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stamp the files of server jobs that share models with one generation time, though the clock moves on.

    The formatter moves the frozen clock on by a second with every file it formats, so the second job starts later.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-shared-timestamp-jobs.toml")
    with freeze_time(TIMESTAMP) as clock:
        monkeypatch.setattr(CodeFormatter, "clock", clock, raising=False)
        run_main_with_args(
            ["--all-jobs"], capsys=capsys, expected_stderr=SHARED_DEPENDENCIES.read_text(encoding="utf-8")
        )
    assert_file_content(tmp_path / "models.py", "cli/timestamp-models.py")
    assert_file_content(tmp_path / "admin" / "services.py", "cli/timestamp-services.py")


@pytest.mark.parametrize(
    ("name", "arguments"),
    [
        ("job", ["--job", "server"]),
        ("cli-over-job", ["--job", "server", "--server-handler-mode", "async"]),
        ("cli-table-over-job", ["--job", "server", "--server-handler-modes", '{"/paths/~1pets/get": "async"}']),
    ],
)
def test_fastapi_cli_job_precedence(
    name: str,
    arguments: list[str],
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Take each server setting of a job from the command line, then the job table, then its profile."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-mixed-jobs.toml")
    run_main_with_args(arguments, capsys=capsys, expected_stderr=DEPENDENCIES.read_text(encoding="utf-8"))
    assert_output(
        f"$ datamodel-codegen {' '.join(arguments)}\n{_methods(tmp_path / 'server' / 'services.py')}",
        EXPECTED / "cli" / "precedence" / f"{name}.txt",
    )


def test_fastapi_cli_job_lockfile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Publish the remote lock a server job updates with the batch, next to pyproject.toml."""
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, "pyproject-jobs.toml")
    run_main_with_args(["--job", "server", "--update-lock"])
    assert_file_content(tmp_path / "models.py", PACKAGE / "models.py")
    assert_output(
        (tmp_path / "datamodel-codegen.lock").read_text(encoding="utf-8"),
        DATA / "expected" / "http" / "remote_lock_empty.txt",
    )


@pytest.mark.parametrize(
    ("pyproject", "arguments", "stderr"),
    [
        ("pyproject-jobs.toml", ["--all-jobs", "--watch"], "Error: --generate-server cannot be used with --watch\n"),
        (
            "pyproject-model-jobs.toml",
            ["--all-jobs", "--server-layout", "single"],
            "Error: --server-layout requires --generate-server\n",
        ),
        (
            "pyproject-failing-jobs.toml",
            ["--all-jobs"],
            "Error: --collapse-root-models-name-strategy requires --collapse-root-models\n",
        ),
        (
            "pyproject-overlap-jobs.toml",
            ["--all-jobs"],
            "Jobs 'schemas' (output: {root}) and 'server' (server output: {server}) have overlapping output paths\n",
        ),
        (
            "pyproject-shared-output-jobs.toml",
            ["--all-jobs"],
            "Jobs 'schemas' (output: {models}) and 'server' (output: {models}) have overlapping output paths\n",
        ),
        (
            "pyproject-differing-models-jobs.toml",
            ["--all-jobs"],
            "Error: could not publish batch output: {shared}: Jobs 'server' and 'admin' generate different models\n",
        ),
        (
            "pyproject-jobs.toml",
            ["--all-jobs", "--update-lock", "--lockfile", "server/README.md"],
            "Remote lock for 'server' ({lock}) overlaps server output for 'server': {server}\n",
        ),
    ],
    ids=[
        "watch",
        "model-job-server-option",
        "later-job-fails",
        "overlap",
        "model-job-shares-output",
        "differing-models",
        "lock-in-server-output",
    ],
)
def test_fastapi_cli_jobs(
    pyproject: str,
    arguments: list[str],
    stderr: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Refuse what a server job cannot honor, and publish nothing of a batch whose later job fails.

    Server jobs refuse the options a single server run refuses, and their outputs must not overlap other outputs or
    hold the remote lock. Only server jobs can share a models output, which they must generate identically.
    """
    monkeypatch.chdir(tmp_path)
    _copy(tmp_path, pyproject)
    root = tmp_path.resolve()
    run_main_with_args(
        arguments,
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=stderr.format(
            root=root / "server" / "schemas.py",
            server=root / "server",
            models=root / "models.py",
            shared=(Path.cwd() / "models.py").as_posix(),
            lock=root / "server" / "README.md",
        ),
    )
    assert_output(
        "".join(f"{path.name}\n" for path in sorted(tmp_path.iterdir())), EXPECTED / "cli" / "jobs-unwritten.txt"
    )


@pytest.mark.parametrize(
    ("arguments", "stderr"),
    [
        (["--output-model-type", "msgspec.Struct"], UNSUPPORTED),
        (["--emit-model-metadata", "metadata"], "Error: Model metadata output requires a file path, not a directory\n"),
    ],
    ids=["backend", "metadata"],
)
def test_fastapi_cli_config_errors(
    arguments: list[str],
    stderr: str,
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
        expected_stderr=stderr,
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("form", ["pyproject", "options", "python", "loaded"])
def test_fastapi_cli_config_values(form: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Write the same package from every server setting in pyproject.toml as from the same options or generate().

    generate() takes the options as keywords, or loads the pyproject.toml keys from another directory, where the
    paths and documents of the keys still resolve against the pyproject.toml directory. The server templates come
    from the custom template directory.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "templates" / "roles", tmp_path / "templates")
    pyproject = ["pyproject-configured.toml"] if form in {"pyproject", "loaded"} else []
    for source, destination in [
        *_inputs(tmp_path, *pyproject),
        (CLI / "primary-responses.json", tmp_path / "responses.json"),
    ]:
        shutil.copy2(source, destination)
    models = {
        "input_file_type": InputFileType.OpenAPI,
        "output": tmp_path / "models.py",
        "target_python_version": PythonVersion.PY_311,
        "openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api],
        "output_model_type": DataModelType.PydanticV2BaseModel,
        "formatters": [Formatter.BUILTIN],
        "disable_timestamp": True,
        "custom_template_dir": tmp_path / "templates",
    }
    match form:
        case "python":
            generate(
                Path("pets.yaml"),
                **models,
                generate_server=ServerType.FastAPI,
                server_output=Path("server"),
                server_package="server",
                server_model_package="models",
                server_layout=ServerLayout.Routers,
                server_handler_mode=ServerHandlerMode.Async,
                server_include_request=True,
                server_body_mode=ServerBodyMode.Request,
                server_router_names={"tag:pets": "animals"},
                server_body_modes={"/paths/~1pets/post": ServerBodyMode.Typed},
                server_primary_responses=json.loads((tmp_path / "responses.json").read_text(encoding="utf-8")),
                server_operation_names={"/paths/~1pets/get": "list_all"},
                server_parameter_names={
                    "/paths/~1pets/get": {"query:limit": "page_size", "header:X-Request-Id": "trace"}
                },
                server_handler_modes={"/paths/~1pets/get": "sync"},
            )
        case "loaded":
            (elsewhere := tmp_path / "elsewhere").mkdir()
            monkeypatch.chdir(elsewhere)
            generate(tmp_path / "pets.yaml", config=load_pyproject_config(tmp_path, overrides=models))
        case _:
            run_main_and_assert(
                input_path=Path("pets.yaml"),
                output_path=Path("models.py"),
                input_file_type="openapi",
                extra_args=[
                    *OPTIONS,
                    "--custom-template-dir",
                    "templates",
                    *([] if pyproject else [*SERVER, *CONFIGURED]),
                ],
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
        (
            "file-documents",
            [
                *("--server-handler-modes", "api/modes.json", "--server-body-modes", "api/body-modes.json"),
                *("--server-operation-names", "api/names.json", "--server-parameter-names", "api/parameters.json"),
                *("--server-primary-responses", "api/responses.json"),
            ],
        ),
    ],
)
def test_fastapi_cli_setting_errors(
    name: str, arguments: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report every server setting the server cannot use, before any file is written.

    An entry of a JSON file names the file its document resolved to: the file's sibling, not the input next to it.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copytree(CLI / "json-files", tmp_path / "api")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(
        capsys.readouterr().err.replace(tmp_path.resolve().as_posix(), "<root>"),
        EXPECTED / "cli" / "setting-errors" / f"{name}.txt",
    )


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
                option_description=f"""Set the handler mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `sync` or `async`, and overrides
`--server-handler-mode` for those operations. An operation reference is the JSON pointer of the path item method,
such as `/paths/~1pets/get`, optionally after a document and `#`, such as `pets.yaml#/paths/~1pets/get`.
{DOCUMENT_BASE} In pyproject.toml, `server-handler-modes` is a table, and a command-line value replaces the whole
table.""",
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
                option_description=f"""Set the body mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `typed` or `request`, and overrides
`--server-body-mode` for those operations. {DOCUMENT_BASE}""",
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
                option_description=f"""Choose the response a bare return value of an operation takes (experimental).

The JSON object, inline or in a file, maps operation references to an object with the `status_code` of a declared
response and, when that response has several media types, its `media_type`. Without an entry, the server infers the
primary response from the declared success responses. {DOCUMENT_BASE}""",
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
                option_description=f"""Name the service methods of single operations (experimental).

The JSON object, inline or in a file, maps operation references to method names. Other operations take the
snake_case form of their operationId, or of their method and path. {DOCUMENT_BASE}""",
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
                option_description=f"""Name the method arguments of single operations (experimental).

The JSON object, inline or in a file, maps operation references to objects that map a parameter, written as its
location and name such as `query:limit` or `header:X-Request-Id`, to the argument name. {DOCUMENT_BASE}""",
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


def test_fastapi_cli_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unresolved reference and an unexpected formatter failure without publishing files.

    The traceback of the unexpected failure ends with a blank line, as the model CLI prints it.
    """
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("broken.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--strict-refs"),
        copy_files=[(SOURCE / "unresolved-ref.yaml", tmp_path / "broken.yaml")],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    captured = capsys.readouterr()
    assert_output(captured.err, EXPECTED / "cli" / "unresolved.txt")
    assert_output(captured.out, EXPECTED / "cli" / "json" / "error-empty.txt")

    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--custom-formatters", "tests.data.python.custom_formatters.stop"),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="RuntimeError: The formatter stopped\n\n",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("case", ["class-name", "encoding", "lock", "output-parent", "publication"])
@pytest.mark.parametrize("structured", [False, True], ids=["text", "json"])
def test_fastapi_cli_model_error_context(
    case: str, structured: bool, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Keep model error hints, decoding context, lock errors, and filesystem errors without publishing files.

    A failed publication is reported as by the model runs that publish through the same journal.
    """
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
            (tmp_path / "pets.yaml").write_bytes((CLI / "non-text.dat").read_bytes())
            stderr = "Error: Unable to decode input using encoding 'utf-8': "
        case "lock":
            (tmp_path / "api.lock").write_text("{broken", encoding="utf-8")
            options = ["--lockfile", "api.lock"]
            stderr = "Error: Unable to read remote lock "
        case "publication":
            (tmp_path / "server" / "services.py").mkdir(parents=True)
            stderr = "Error: could not publish batch output: [Errno 21] Is a directory: "
        case _:
            (tmp_path / "occupied").write_text("occupied\n", encoding="utf-8")
            output = Path("occupied/models.py")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=output,
        input_file_type="openapi",
        extra_args=_server(*options, *(["--output-format", "json"] if structured else [])),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=stderr,
        expected_stdout_path=EXPECTED / "cli" / "json" / "error-empty.txt" if structured else None,
        output_should_not_exist=True,
    )
