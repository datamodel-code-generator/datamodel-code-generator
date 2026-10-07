"""Generate the FastAPI server target from the command line: publish, check, report, and refuse conflicts."""

from __future__ import annotations

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
PYTHON = ["--target-python-version", "3.11"]
SCOPES = ["--openapi-scopes", "schemas", "api"]
BACKEND = ["--output-model-type", "pydantic_v2.BaseModel"]
FORMATTERS = ["--formatters", "builtin"]
MODEL_OPTIONS = [*PYTHON, *SCOPES, *BACKEND, *FORMATTERS]
OPTIONS = [*MODEL_OPTIONS, "--disable-timestamp"]
SERVER = ["--generate-server", "fastapi", "--target-config", "fastapi.toml"]
EXCLUDED = (
    "S_OPERATION_EXCLUDED info selection /paths/~1store~1inventory/get: "
    "GET /store/inventory is excluded by exclude_tags 'store': The store is internal\n"
)
UNSUPPORTED = (
    "E_FASTAPI_BACKEND_UNSUPPORTED error config model_config.output_model_type: The fastapi target does not "
    "support 'msgspec.Struct'; use 'pydantic_v2.BaseModel' or 'pydantic_v2.dataclass'\n"
)
CONFLICT = "E_CONFIG_CONFLICT error config: --generate-server cannot be used with"
NOT_WRITABLE = (
    "E_CONFIG_VALUE error config: --diagnostics-json cannot be written: it is not a file in an existing directory\n"
)
READ_OR_WRITTEN = "E_CONFIG_CONFLICT error config: --diagnostics-json names a file the generation reads or writes\n"
OTHER_FILE = "E_CONFIG_CONFLICT error config: --diagnostics-json names an existing file that is not a report\n"

assert_file_content = create_assert_file_content(EXPECTED)


def _server(*extra: str, config: str = "fastapi.toml") -> list[str]:
    return [*OPTIONS, "--generate-server", "fastapi", "--target-config", config, *extra]


def _inputs(root: Path, config: str = "fastapi.toml") -> list[tuple[Path, Path]]:
    return [(SOURCE / "pets.yaml", root / "pets.yaml"), (CLI / config, root / "fastapi.toml")]


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
        expected_stdout_path=EXPECTED / "cli" / "dependencies.txt",
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
        extra_args=_server("--check", config=str(tmp_path / "fastapi.toml")),
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
        expected_stderr="write models.py\n",
    )
    (tmp_path / "server" / "README.md").unlink()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--check"),
        expected_exit=Exit.DIFF,
        capsys=capsys,
        expected_stderr="write models.py\nwrite server/README.md\n",
    )
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "dependencies.txt",
        assert_func=assert_file_content,
        expected_file=PACKAGE / "models.py",
    )
    assert_directory_content(tmp_path / "server", PACKAGE / "server")


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


@pytest.mark.parametrize(
    ("config", "options", "stdout"),
    [
        ("standalone.toml", [], "standalone.txt"),
        ("standalone.toml", ["--target-output", "pets service"], "standalone-spaced.txt"),
        ("standalone.toml", ["--target-output", "$pets"], "standalone-expanded.txt"),
        ("standalone.toml", ["--dependency-format", "requirements"], "standalone-requirements.txt"),
        ("fastapi.toml", ["--target-output", "service", "--dependency-format", "requirements"], "requirements.txt"),
    ],
    ids=["standalone", "spaced", "expanded", "standalone-requirements", "requirements"],
)
def test_fastapi_cli_dependencies(
    config: str,
    options: list[str],
    stdout: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Print what adds the generated package to a project: a uv command quoted for any shell, or requirements."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*options),
        copy_files=_inputs(tmp_path, config),
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / stdout,
        file_should_not_exist=tmp_path / "server",
    )


def test_fastapi_cli_requirements_url(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Print a distribution path that needs quoting as a file URL, which pip and uv read alike in requirements."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--target-output", "pets service", "--dependency-format", "requirements"),
        copy_files=_inputs(tmp_path, "standalone.toml"),
    )
    assert_output(
        capsys.readouterr().out.replace(tmp_path.resolve().as_uri(), "file:///tmp"),
        EXPECTED / "cli" / "standalone-requirements-url.txt",
    )


def test_fastapi_cli_target_output(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write the package where --target-output points, reporting the excluded operation to stdout or a file."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--target-output", "service", "--diagnostics-json", "-"),
        copy_files=_inputs(tmp_path, "selection.toml"),
        capsys=capsys,
        expected_stdout_path=EXPECTED / "cli" / "excluded.txt",
        expected_stderr=EXCLUDED,
        file_should_not_exist=tmp_path / "server",
    )
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--target-output", "service", "--check", "--diagnostics-json", "diagnostics.json"),
        capsys=capsys,
        expected_stderr=EXCLUDED,
    )
    assert_file_content(tmp_path / "diagnostics.json", "cli/excluded.txt")


def test_fastapi_cli_stdin(tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch) -> None:
    """Read the OpenAPI document from standard input, check the result, and refuse an unsupported backend."""
    monkeypatch.chdir(tmp_path)
    shutil.copy2(CLI / "fastapi.toml", tmp_path / "fastapi.toml")
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
        ([*OPTIONS, "--target-config", "fastapi.toml"], "--target-config can only be used with --generate-server"),
        (
            [*OPTIONS, "--target-output", "service", "--diagnostics-json", "-", "--dependency-format", "uv"],
            "--target-output, --diagnostics-json, --dependency-format can only be used with --generate-server",
        ),
        ([*OPTIONS, "--generate-server", "fastapi"], "--generate-server requires --target-config"),
        (_server("--list-experimental"), "--generate-server cannot be used with --list-experimental"),
        (
            _server("--generate-prompt", "--install-skill", "codex"),
            "--generate-server cannot be used with --install-skill, --generate-prompt",
        ),
        (
            _server("--check", "--emit-model-metadata", "metadata.json"),
            "--check cannot be used with --emit-model-metadata",
        ),
    ],
    ids=["target-config", "target-options", "no-target-config", "info", "skill", "metadata"],
)
def test_fastapi_cli_usage(
    arguments: list[str], message: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse target options without a target, and target runs combined with commands that do not generate."""
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
    for source, destination in _inputs(generated):
        shutil.copy2(source, destination)
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
        expected_stdout_path=EXPECTED / "cli" / "dependencies.txt",
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
    for source, destination in _inputs(tmp_path):
        shutil.copy2(source, destination)
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
            "E_CONFIG_VALUE error config model_config.target_python_version: "
            "The fastapi target needs a target Python version of 3.11 or later\n"
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
        copy_files=[*_inputs(tmp_path), (CLI / "pyproject-target.toml", tmp_path / "pyproject.toml")],
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
            f"{CONFLICT} --watch\n{CONFLICT} --output-format json\n",
            "conflicts-report.txt",
        ),
        (["--diff-against", "pets.yaml"], f"{CONFLICT} --diff-against\n", None),
        (["--all-jobs"], f"{CONFLICT} --all-jobs\n", None),
        (["--job", "server"], f"{CONFLICT} --job\n", None),
        (["--diagnostics-json", "pets.yaml"], READ_OR_WRITTEN, None),
        (["--diagnostics-json", "fastapi.toml"], READ_OR_WRITTEN, None),
        (["--all-jobs", "--diagnostics-json", "fastapi.toml"], READ_OR_WRITTEN, None),
        (["--diagnostics-json", "server/diagnostics.json"], NOT_WRITABLE, None),
        (["--diagnostics-json", "reports"], NOT_WRITABLE, None),
    ],
    ids=["watch", "diff", "all-jobs", "job", "input", "target-config", "jobs-target-config", "missing", "directory"],
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
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stdout_path=None if stdout is None else EXPECTED / "cli" / stdout,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )
    assert_output(
        (tmp_path / "fastapi.toml").read_text(encoding="utf-8"), EXPECTED / "cli" / "kept" / "fastapi.toml.txt"
    )


@pytest.mark.parametrize(
    ("fixture", "name"),
    [
        ("notes.md", "notes.md"),
        ("settings.json", "settings.json"),
        ("client.json", "client.json"),
        ("entries.json", "entries.json"),
        ("tool-settings.toml", "pyproject.toml"),
    ],
)
def test_fastapi_cli_report_other_file(
    fixture: str, name: str, tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
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
        expected_stderr=OTHER_FILE,
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
        expected_stderr="E_CONFIG_VALUE error config: --diagnostics-json cannot be written: Permission denied\n",
    )


def test_fastapi_cli_missing_target_config(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report a target file that cannot be read."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=SOURCE / "pets.yaml",
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(config="missing.toml"),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr="E_CONFIG_VALUE error config: The target file cannot be read: No such file or directory\n",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("config", "arguments", "stderr", "stdout"),
    [
        (
            "unknown.toml",
            ["--diagnostics-json", "-"],
            "E_CONFIG_UNKNOWN error config packages: The target file has no setting 'packages'\n",
            "unknown-report.txt",
        ),
        ("fastapi.toml", ["--output-model-type", "msgspec.Struct"], UNSUPPORTED, None),
        (
            "fastapi.toml",
            ["--emit-model-metadata", "metadata"],
            "E_MODEL_CONFIG error config: Model metadata output requires a file path, not a directory\n",
            None,
        ),
        (
            "fastapi.toml",
            ["--dependency-format", "requirements", "--diagnostics-json", "-"],
            "E_CONFIG_CONFLICT error config: --dependency-format cannot be used with --diagnostics-json -\n",
            "format-report.txt",
        ),
    ],
    ids=["unknown", "backend", "metadata", "format"],
)
def test_fastapi_cli_config_errors(
    config: str,
    arguments: list[str],
    stderr: str,
    stdout: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Report target settings and model settings that fail before any file is written."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "metadata").mkdir()
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(*arguments),
        copy_files=_inputs(tmp_path, config),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stdout_path=None if stdout is None else EXPECTED / "cli" / stdout,
        expected_stderr=stderr,
        output_should_not_exist=True,
    )


def test_fastapi_cli_config_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Read every setting of a flat target file, and the server templates of the custom template directory."""
    monkeypatch.chdir(tmp_path)
    shutil.copytree(SOURCE / "templates" / "roles", tmp_path / "templates")
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--custom-template-dir", "templates"),
        copy_files=_inputs(tmp_path, "configured.toml"),
    )
    sources = sorted(
        path for path in (tmp_path / "server").rglob("*.py") if not {"_runtime", "_generated"} & set(path.parts)
    )
    assert_output(
        "".join(f"# {path.relative_to(tmp_path).as_posix()}\n{path.read_text(encoding='utf-8')}" for path in sources),
        EXPECTED / "cli" / "configured.txt",
    )


def test_fastapi_cli_config_value_errors(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report every invalid value of a flat target file in the order the file declares them."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        copy_files=_inputs(tmp_path, "errors.toml"),
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED / "cli" / "config-errors.txt")


@pytest.mark.abnormal_path("no input makes the FastAPI target raise while rendering")
def test_fastapi_cli_failures(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Report an unresolved reference and a renderer that stops, writing nothing either way."""
    monkeypatch.chdir(tmp_path)
    run_main_and_assert(
        input_path=Path("broken.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server("--strict-refs"),
        copy_files=[
            (SOURCE / "unresolved-ref.yaml", tmp_path / "broken.yaml"),
            (CLI / "fastapi.toml", tmp_path / "fastapi.toml"),
        ],
        expected_exit=Exit.ERROR,
        output_should_not_exist=True,
    )
    assert_output(capsys.readouterr().err, EXPECTED / "cli" / "unresolved.txt")

    def stop(*_args: object) -> None:
        msg = "The renderer stopped"
        raise RuntimeError(msg)

    monkeypatch.setattr("datamodel_code_generator._fastapi.target.FastAPITarget.render", stop)
    run_main_and_assert(
        input_path=Path("pets.yaml"),
        output_path=Path("models.py"),
        input_file_type="openapi",
        extra_args=_server(),
        copy_files=_inputs(tmp_path),
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="E_GENERATION_FAILURE error target: RuntimeError: The renderer stopped\n",
        output_should_not_exist=True,
    )
