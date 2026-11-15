"""Pin which refusal or warning a user sees first where two option stages each have one to show.

Each test gives an input where two conditions hold that belong to two different stages of the option resolution,
and stores everything the user sees: the exit status and the whole stderr of a command, or the warnings and the
raised exception of an API call. Warnings are written to stderr in the order they are shown, beside the lines the
command prints, so the stored text also pins the order of a warning and a printed refusal.
"""

from __future__ import annotations

import shutil
import sys
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import Error, chdir, generate
from datamodel_code_generator.__main__ import Exit
from datamodel_code_generator.config import GenerateConfig
from datamodel_code_generator.dynamic import generate_dynamic_models, generate_dynamic_module
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, JSON_SCHEMA_DATA_PATH, run_main_and_assert, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Callable

PERSON = JSON_SCHEMA_DATA_PATH / "person.json"
SERVER_SOURCE = DATA_PATH / "generation_platform" / "fastapi" / "pets.yaml"
PERSON_SCHEMA: dict[str, Any] = {
    "type": "object",
    "title": "Person",
    "properties": {"name": {"type": "string"}},
}


@pytest.fixture
def warnings_on_stderr(monkeypatch: pytest.MonkeyPatch) -> None:
    """Show every warning, as ``<category>: <message>`` on stderr, in the order the run shows it."""
    warnings.simplefilter("always")

    def show(message: Warning | str, category: type[Warning], *_: object, **__: object) -> None:
        sys.stderr.write(f"{category.__name__}: {message}\n")

    monkeypatch.setattr(warnings, "showwarning", show)


def _seen(
    capsys: pytest.CaptureFixture[str], exit_code: Exit | None = None, raised: BaseException | None = None
) -> str:
    """Return what the user saw: stderr, then the exit status or the raised exception."""
    seen = capsys.readouterr().err
    if exit_code is not None:
        seen += f"exit: {exit_code.name}\n"
    if raised is not None:
        seen += f"raised {type(raised).__name__}: {raised}\n"
    return seen


def _written(root: Path) -> str:
    """Return the files below root, which show what a refused run wrote."""
    written = sorted(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_file())
    return "written:\n" + "".join(f"  {name}\n" for name in written)


@pytest.mark.usefixtures("warnings_on_stderr")
def test_cli_preset_error_before_delimiter_error(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A preset for another Python target meets --original-field-name-delimiter without --snake-case-field."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=PERSON,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--preset",
                "standard-py312-20260619",
                "--target-python-version",
                "3.10",
                "--original-field-name-delimiter",
                "_",
            ],
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
        )
    assert_output(_seen(capsys, Exit.ERROR))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "validated_args",
    [
        pytest.param(["--custom-file-header", "# inline", "--custom-file-header-path", "header.txt"], id="refusal"),
        pytest.param(["--parent-scoped-naming"], id="deprecation"),
    ],
)
def test_cli_config_validation_before_preset_error(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str], validated_args: list[str]
) -> None:
    """Options a config validator refuses together, or deprecates, meet a preset for another Python target."""
    (tmp_path / "header.txt").write_text("# header\n", encoding="utf-8")
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=PERSON,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--preset",
                "standard-py312-20260619",
                "--target-python-version",
                "3.10",
                *validated_args,
            ],
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
        )
    assert_output(_seen(capsys, Exit.ERROR))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_cli_pyproject_read_error_before_validator_error(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --profile that pyproject.toml lacks meets two command-line options that a config validator refuses together."""
    (tmp_path / "pyproject.toml").write_text("[tool.datamodel-codegen]\ndisable-timestamp = true\n", encoding="utf-8")
    (tmp_path / "header.txt").write_text("# header\n", encoding="utf-8")
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=PERSON,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--profile",
                "missing",
                "--custom-file-header",
                "# inline",
                "--custom-file-header-path",
                "header.txt",
            ],
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
        )
    assert_output(_seen(capsys, Exit.ERROR))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_cli_use_annotated_deprecation_reuse_scope_warning_and_collapse_refusal(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A Pydantic v2 run without --use-annotated sets --reuse-scope tree and a collapse strategy without its switch."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=PERSON,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--reuse-scope",
                "tree",
                "--collapse-root-models-name-strategy",
                "child",
            ],
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
        )
    assert_output(_seen(capsys, Exit.ERROR))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_cli_run_option_refusal_before_generate_sentinel_refusal(
    tmp_path: Path, output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A collapse strategy without --collapse-root-models meets --use-missing-sentinel with an older pydantic target."""
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=PERSON,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--use-annotated",
                "--use-missing-sentinel",
                "--target-pydantic-version",
                "2.11",
                "--collapse-root-models-name-strategy",
                "child",
            ],
            expected_exit=Exit.ERROR,
            output_should_not_exist=True,
        )
    assert_output(_seen(capsys, Exit.ERROR))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_api_target_preset_error_before_sentinel_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A server target's preset for another Python target meets the sentinel with a too old pydantic target.

    The preset is refused with the `Error` a model-only run raises for it.
    """
    monkeypatch.chdir(tmp_path)
    shutil.copy2(SERVER_SOURCE, tmp_path / "pets.yaml")
    with pytest.raises(Error) as raised:
        generate(
            Path("pets.yaml"),
            input_file_type="openapi",
            openapi_scopes=["schemas", "api"],
            output_model_type="pydantic_v2.BaseModel",
            target_python_version="3.11",
            preset="standard-py312-20260619",
            use_missing_sentinel=True,
            target_pydantic_version="2.11",
            formatters=["builtin"],
            output=Path("models.py"),
            generate_server="fastapi",
            server_output=Path("server"),
            server_package="server",
            server_model_package="models",
        )
    assert_output(_seen(capsys, raised=raised.value) + _written(tmp_path))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "build",
    [generate_dynamic_models, generate_dynamic_module],
    ids=["models", "module"],
)
def test_dynamic_jinja2_deprecation_before_sentinel_refusal(
    build: Callable[..., object], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A custom template directory meets --use-missing-sentinel with an older pydantic target in dynamic generation."""
    config = GenerateConfig(
        output_model_type="pydantic_v2.BaseModel",
        custom_template_dir=tmp_path,
        use_missing_sentinel=True,
        target_pydantic_version="2.11",
    )
    with pytest.raises(Error) as raised:
        build(PERSON_SCHEMA, config=config, cache_size=0)
    assert_output(_seen(capsys, raised=raised.value))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_batch_job_refusal_after_an_earlier_job_generated(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A batch job that a run-option check refuses follows a job that generated its models and printed a warning.

    The earlier job's warning shows that it ran before the refusal; its output is staged and never published.
    """
    (tmp_path / "pyproject.toml").write_text(
        f"""\
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
output-model-type = "pydantic_v2.BaseModel"
use-annotated = true

[tool.datamodel-codegen.jobs.first]
input = "{PERSON.as_posix()}"
output = "first.py"
reuse-scope = "tree"

[tool.datamodel-codegen.jobs.second]
input = "{PERSON.as_posix()}"
output = "second.py"
collapse-root-models-name-strategy = "child"
""",
        encoding="utf-8",
    )
    with chdir(tmp_path):
        exit_code = run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


MODEL_ARGUMENTS = ["--input-file-type", "jsonschema", "--output-model-type", "pydantic_v2.BaseModel"]
REUSE_SCOPE_AND_COLLAPSE_STRATEGY = ["--reuse-scope", "tree", "--collapse-root-models-name-strategy", "child"]


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "pyproject"),
    [
        pytest.param(["--no-use-annotated"], None, id="argument"),
        pytest.param([], "[tool.datamodel-codegen]\nuse-annotated = false\n", id="pyproject"),
        pytest.param(
            ["--profile", "plain"],
            "[tool.datamodel-codegen]\ndisable-timestamp = true\n"
            "[tool.datamodel-codegen.profiles.plain]\nuse-annotated = false\n",
            id="profile",
        ),
        pytest.param(
            ["--profile", "plain"],
            "[tool.datamodel-codegen]\ndisable-timestamp = true\n"
            "[tool.datamodel-codegen.profiles.plain]\nsnake-case-field = true\n",
            id="profile-without",
        ),
    ],
)
def test_cli_use_annotated_deprecation_by_where_it_is_given(
    arguments: list[str], pyproject: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A --use-annotated value given by an argument, pyproject.toml or a profile meets the later run-option checks.

    The deprecation of the default shows only where no source gives the option, before the reuse-scope warning.
    """
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            [
                "--input",
                str(PERSON),
                "--output",
                "output.py",
                *MODEL_ARGUMENTS,
                *REUSE_SCOPE_AND_COLLAPSE_STRATEGY,
                *arguments,
            ],
            expected_exit=Exit.ERROR,
        )
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "preset", "expected_exit"),
    [
        pytest.param(["--preset", "standard-py312-20260619"], "", Exit.OK, id="argument"),
        pytest.param([], 'preset = "standard-py312-20260619"\n', Exit.ERROR, id="pyproject"),
    ],
)
def test_cli_preset_of_an_argument_replaces_pyproject_values_before_delimiter_check(
    arguments: list[str], preset: str, expected_exit: Exit, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pyproject.toml that turns --snake-case-field off and sets a delimiter meets a preset that turns it on.

    The preset an argument selects replaces the values of pyproject.toml, so the delimiter check that follows the
    preset passes; the preset pyproject.toml selects leaves them, and the check refuses the run.
    """
    (tmp_path / "pyproject.toml").write_text(
        f'[tool.datamodel-codegen]\n{preset}snake-case-field = false\noriginal-field-name-delimiter = "_"\n',
        encoding="utf-8",
    )
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            ["--input", str(PERSON), "--output", "output.py", *MODEL_ARGUMENTS, *arguments],
            expected_exit=expected_exit,
        )
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


JOBS = f"""\
[tool.datamodel-codegen]
disable-timestamp = true
input-file-type = "jsonschema"
output-model-type = "pydantic_v2.BaseModel"

[tool.datamodel-codegen.jobs.first]
input = "{PERSON.as_posix()}"
output = "first.py"
reuse-scope = "tree"

[tool.datamodel-codegen.jobs.second]
input = "{PERSON.as_posix()}"
output = "second.py"
use-annotated = false
reuse-scope = "tree"
"""


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "arguments",
    [pytest.param([], id="tables"), pytest.param(["--no-use-annotated"], id="argument")],
)
def test_batch_jobs_use_annotated_deprecation_and_specialized_enum_refusal(
    arguments: list[str], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Jobs that give --use-annotated or not are followed by a job whose own --use-specialized-enum is refused.

    Each job shows the deprecation of the default only where neither its tables nor an argument give the option, and
    the refusal of the last job reads the value its table gives.
    """
    (tmp_path / "pyproject.toml").write_text(
        JOBS
        + f"""
[tool.datamodel-codegen.jobs.third]
input = "{PERSON.as_posix()}"
output = "third.py"
use-specialized-enum = true
target-python-version = "3.10"
""",
        encoding="utf-8",
    )
    with chdir(tmp_path):
        exit_code = run_main_with_args(["--all-jobs", "--formatters", "builtin", *arguments], expected_exit=Exit.ERROR)
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_batch_job_preset_error_before_an_earlier_job_runs(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A job whose preset is for another Python target follows jobs that would generate and print warnings.

    Every job's options are resolved before the first job runs, so only the preset refusal shows.
    """
    (tmp_path / "pyproject.toml").write_text(
        JOBS
        + f"""
[tool.datamodel-codegen.jobs.third]
input = "{PERSON.as_posix()}"
output = "third.py"
preset = "standard-py312-20260619"
target-python-version = "3.10"
""",
        encoding="utf-8",
    )
    with chdir(tmp_path):
        exit_code = run_main_with_args(["--all-jobs", "--formatters", "builtin"], expected_exit=Exit.ERROR)
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "pyproject"),
    [
        pytest.param(REUSE_SCOPE_AND_COLLAPSE_STRATEGY, None, id="unset"),
        pytest.param(
            ["--target-python-version", "3.10"],
            "[tool.datamodel-codegen]\nuse-annotated = false\nuse-specialized-enum = true\n",
            id="pyproject",
        ),
        pytest.param(
            ["--no-use-annotated", "--use-specialized-enum", "--target-python-version", "3.10"], None, id="argument"
        ),
    ],
)
def test_cli_lock_update_run_option_checks_by_where_options_are_given(
    arguments: list[str], pyproject: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A run that updates its lock, and so generates through its staged transaction, meets the run-option checks.

    The checks of the staged run read where --use-annotated and --use-specialized-enum were given as a plain run does.
    """
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            ["--input", str(PERSON), "--output", "output.py", *MODEL_ARGUMENTS, "--update-lock", *arguments],
            expected_exit=Exit.ERROR,
        )
    assert_output(_seen(capsys, exit_code) + _written(tmp_path))


SERVER_ARGUMENTS = ["--generate-server", "fastapi", "--server-output", "server"]
SERVER_KEYS = 'generate-server = "fastapi"\nserver-output = "server"\n'


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "pyproject", "expected_exit"),
    [
        pytest.param(SERVER_ARGUMENTS, None, Exit.OK, id="argument"),
        pytest.param([], f"[tool.datamodel-codegen]\n{SERVER_KEYS}", Exit.OK, id="pyproject"),
        pytest.param(
            [*SERVER_ARGUMENTS, "--preset", "standard-py312-20260619"],
            f"[tool.datamodel-codegen]\n{SERVER_KEYS}use-annotated = false\n",
            Exit.OK,
            id="argument-preset-over-pyproject",
        ),
        pytest.param(
            [*SERVER_ARGUMENTS, "--preset", "standard-py310-20260619"], None, Exit.ERROR, id="preset-of-default-python"
        ),
    ],
)
def test_cli_target_use_annotated_deprecation_before_implied_settings_notice(
    arguments: list[str],
    pyproject: str | None,
    expected_exit: Exit,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A server target that sets the options it needs meets the deprecation of the --use-annotated default.

    The deprecation shows before the run, where no source gives the option and no preset turns it on, and the notice
    that names the settings the target set, and how to set them, after it. The target leaves the Python version a
    preset sets, also the one that is the default, which it then refuses.
    """
    shutil.copy2(SERVER_SOURCE, tmp_path / "pets.yaml")
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            [
                "--input",
                "pets.yaml",
                "--output",
                "models.py",
                "--input-file-type",
                "openapi",
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--disable-timestamp",
                "--formatters",
                "builtin",
                "--server-package",
                "server",
                "--server-model-package",
                "models",
                *arguments,
            ],
            expected_exit=expected_exit,
        )
    assert_output(_seen(capsys, exit_code).partition("Add the runtime dependencies")[0] + _written(tmp_path))
