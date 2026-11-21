"""Pin which refusal or warning a user sees first where two option stages each have one to show.

Each test gives an input where two conditions hold that belong to two different stages of the option resolution,
and stores everything the user sees: the exit status and the whole stderr of a command, or the warnings and the
raised exception of an API call. Warnings are written to stderr in the order they are shown, beside the lines the
command prints, so the stored text also pins the order of a warning and a printed refusal.

The tests after them pin the warning of the resolution itself: an option that was turned off and that another
option turns on (``IMPLIED_OPTIONS``) is reported once, where the options are resolved, also where an argument turned
the other option off after pyproject.toml turned it on, which ``--read-implied-options-from-merged-values`` undoes.
The last tests pin each relation between two options (``_option_rules.py:OPTION_RELATIONS``) at its stage, what each
stage sees, and the order where two relations of one stage meet.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from pydantic import ValidationError

from datamodel_code_generator import Error, chdir, generate, load_pyproject_config
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


REF_WITH_SIBLING = """\
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$defs": {"A": {"type": "integer", "maximum": 5}},
  "type": "object",
  "properties": {"a": {"$ref": "#/$defs/A", "maximum": 10}}
}
"""
PYDANTIC = ["--output-model-type", "pydantic_v2.BaseModel"]
BUILTIN = ["--formatters", "builtin"]
PRESET_WITHOUT_EITHER = "standard-py312-20261017"
PRESET_BEFORE_COMBINED = "standard-py312-20261027"
PRESET_WITH_COMBINED = "standard-py312-20261104"
TABLE = "[tool.datamodel-codegen]\n"


def _resolved(code: str) -> str:
    """Return the lines of field ``a`` and of ``A``, which show what the options of the three rows resolved to."""
    shown = [line.strip() for line in code.splitlines() if line.strip().startswith(("a:", "A =", "A:", "class A"))]
    return "resolved:\n" + "".join(f"  {line}\n" for line in shown)


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "pyproject"),
    [
        pytest.param(
            [*PYDANTIC, "--preset", PRESET_BEFORE_COMBINED, "--no-intersect-allof-keywords"],
            None,
            id="preset-without-it",
        ),
        pytest.param([*PYDANTIC, "--preset", PRESET_WITH_COMBINED, "--no-intersect-allof-keywords"], None, id="preset"),
        pytest.param(
            [
                *PYDANTIC,
                "--preset",
                PRESET_WITH_COMBINED,
                "--no-intersect-allof-keywords",
                "--no-intersect-combined-schema-keywords",
            ],
            None,
            id="both-off",
        ),
        pytest.param(
            PYDANTIC, f'{TABLE}preset = "{PRESET_WITH_COMBINED}"\nintersect-allof-keywords = false\n', id="pyproject"
        ),
        pytest.param(
            [*PYDANTIC, "--preset", PRESET_WITH_COMBINED],
            f"{TABLE}intersect-allof-keywords = false\n",
            id="argument-preset-over-pyproject",
        ),
        pytest.param(
            [*PYDANTIC, "--preset", PRESET_WITHOUT_EITHER, "--intersect-combined-schema-keywords"],
            f"{TABLE}intersect-allof-keywords = false\n",
            id="argument-preset-without-it-over-pyproject",
        ),
        pytest.param(
            [*PYDANTIC, "--preset", PRESET_WITH_COMBINED, "--no-intersect-allof-keywords", "--disable-warnings"],
            None,
            id="disable-warnings",
        ),
        pytest.param(
            [*PYDANTIC, *BUILTIN, "--use-annotated", "--intersect-combined-schema-keywords"],
            f"{TABLE}intersect-allof-keywords = false\n",
            id="given-as-well",
        ),
        pytest.param(
            [*PYDANTIC, *BUILTIN, "--use-annotated"], f"{TABLE}field-constraints = false\n", id="field-constraints"
        ),
        pytest.param(
            [*PYDANTIC, *BUILTIN],
            f"{TABLE}use-annotated = true\nfield-constraints = false\n",
            id="field-constraints-pyproject",
        ),
        pytest.param(
            ["--output-model-type", "msgspec.Struct", *BUILTIN],
            f"{TABLE}field-constraints = false\n",
            id="field-constraints-msgspec",
        ),
        pytest.param(
            [*PYDANTIC, *BUILTIN, "--use-annotated", "--use-type-alias-type"],
            f"{TABLE}use-type-alias = false\n",
            id="use-type-alias",
        ),
    ],
)
def test_cli_warns_of_an_option_turned_off_that_another_option_turns_on(
    arguments: list[str], pyproject: str | None, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An option that an argument or pyproject.toml turns off meets an option that turns it on (#6850).

    The first three cases are the commands of the issue. The run warns once where a preset, an argument, a key of
    pyproject.toml or the output model type turns the other option on over the value given, and names how to turn
    both off: the argument, or the key where pyproject.toml alone turns the other option on. It does not where the
    other option is off, where a preset that an argument selects replaces the pyproject.toml value itself (a preset
    that sets neither option replaces nothing, so that value is reported), or under --disable-warnings. The
    generated field shows that the value each option resolves to stays as it was.
    """
    (tmp_path / "ref.json").write_text(REF_WITH_SIBLING, encoding="utf-8")
    if pyproject is not None:
        (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            [
                "--input",
                "ref.json",
                "--input-file-type",
                "jsonschema",
                "--disable-timestamp",
                "--output",
                "model.py",
                *arguments,
            ],
        )
    assert_output(_seen(capsys, exit_code) + _resolved((tmp_path / "model.py").read_text(encoding="utf-8")))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_cli_target_warns_once_of_an_option_turned_off_that_a_preset_turns_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A server target, whose options are resolved three times, meets --no-intersect-allof-keywords under a preset.

    The warning shows once, where the command's options are resolved, before the notice of the settings the target
    set.
    """
    shutil.copy2(SERVER_SOURCE, tmp_path / "pets.yaml")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            [
                "--input",
                "pets.yaml",
                "--output",
                "models.py",
                "--input-file-type",
                "openapi",
                *PYDANTIC,
                "--disable-timestamp",
                "--server-package",
                "server",
                "--server-model-package",
                "models",
                *SERVER_ARGUMENTS,
                "--preset",
                PRESET_WITH_COMBINED,
                "--no-intersect-allof-keywords",
                *BUILTIN,
            ],
        )
    assert_output(_seen(capsys, exit_code).partition("Add the runtime dependencies")[0])


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"preset": PRESET_WITH_COMBINED, "intersect_allof_keywords": False}, id="preset"),
        pytest.param(
            {
                "preset": PRESET_WITH_COMBINED,
                "intersect_allof_keywords": False,
                "intersect_combined_schema_keywords": False,
            },
            id="both-off",
        ),
        pytest.param(
            {"formatters": ["builtin"], "use_annotated": True, "field_constraints": False}, id="field-constraints"
        ),
    ],
)
def test_api_warns_of_an_option_turned_off_that_another_option_turns_on(
    options: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """An option that generate() is given as False meets an option, or a preset, that turns it on (#6850)."""
    code = generate(
        REF_WITH_SIBLING,
        input_file_type="jsonschema",
        output_model_type="pydantic_v2.BaseModel",
        disable_timestamp=True,
        **options,
    )
    assert_output(_seen(capsys) + _resolved(cast("str", code)))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "table",
    [
        pytest.param(
            "use-annotated = true\nintersect-combined-schema-keywords = true\nintersect-allof-keywords = false\n",
            id="table",
        ),
        pytest.param(f'preset = "{PRESET_WITH_COMBINED}"\n', id="preset"),
    ],
)
def test_loaded_config_warns_of_an_option_its_table_turns_off_and_on(
    table: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A table that load_pyproject_config() reads is resolved by the loader and again by generate().

    A key the table turns off and another key of it turns on is reported once, by the loader, with the key that
    turns both off. A table with a preset alone reports nothing: the loader marks the options it resolved as set,
    such as --field-constraints, off before the preset turns --use-annotated on, and generate() does not read them
    as given.
    """
    (tmp_path / "pyproject.toml").write_text(
        f'{TABLE}output-model-type = "pydantic_v2.BaseModel"\nformatters = ["builtin"]\n{table}', encoding="utf-8"
    )
    code = generate(
        REF_WITH_SIBLING, config=load_pyproject_config(tmp_path, overrides={"input_file_type": "jsonschema"})
    )
    assert_output(_seen(capsys) + _resolved(cast("str", code)))


INTEGER_MAXIMUM = JSON_SCHEMA_DATA_PATH / "integer_maximum.json"
MERGED_VALUES = "--read-implied-options-from-merged-values"
ANNOTATED_TABLE = f"{TABLE}use-annotated = true\nfield-constraints = false\n"


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "pyproject"),
    [
        pytest.param(["--no-use-annotated"], ANNOTATED_TABLE, id="argument"),
        pytest.param([], f"{TABLE}use-annotated = false\nfield-constraints = false\n", id="key"),
        pytest.param(["--no-use-annotated", MERGED_VALUES], ANNOTATED_TABLE, id="option"),
        pytest.param(
            ["--no-use-annotated"],
            f"{ANNOTATED_TABLE}read-implied-options-from-merged-values = true\n",
            id="option-key",
        ),
        pytest.param(
            ["--preset", "standard-py312-20261112", "--no-use-annotated"], ANNOTATED_TABLE, id="preset-without-option"
        ),
        pytest.param(["--preset", "standard-py312-20261113", "--no-use-annotated"], ANNOTATED_TABLE, id="preset"),
        pytest.param(
            ["--no-use-annotated", "--field-constraints", MERGED_VALUES], ANNOTATED_TABLE, id="option-argument-given"
        ),
        pytest.param(
            ["--use-annotated", MERGED_VALUES], f"{TABLE}field-constraints = false\n", id="option-with-the-other-on"
        ),
        pytest.param(["--no-use-annotated", "--disable-warnings"], ANNOTATED_TABLE, id="disable-warnings"),
    ],
)
def test_cli_argument_turns_off_an_option_that_pyproject_turned_on(
    arguments: list[str], pyproject: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """An argument turns off an option whose pyproject.toml value turned another option on (#6891).

    The first two cases are the commands of the issue. --no-use-annotated over use-annotated = true leaves
    --field-constraints on, which use-annotated = true turned on before the arguments applied, although
    field-constraints = false: the run warns once, names the key and the option, and writes the field constraint as
    before. use-annotated = false in pyproject.toml writes conint. Under --read-implied-options-from-merged-values,
    given as an argument, as a key or by a 20261113 preset, the argument writes what the key writes; a
    --field-constraints argument keeps the option on, and so does --use-annotated, with the warning of #6850.
    """
    (tmp_path / "schema.json").write_text(INTEGER_MAXIMUM.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(pyproject, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            [
                "--input",
                "schema.json",
                "--input-file-type",
                "jsonschema",
                *PYDANTIC,
                "--disable-timestamp",
                "--output",
                "model.py",
                *BUILTIN,
                *arguments,
            ],
        )
    assert_output(_seen(capsys, exit_code) + _resolved((tmp_path / "model.py").read_text(encoding="utf-8")))


@pytest.mark.cli_doc(
    options=[MERGED_VALUES],
    option_description="""Read the options that another option turns on from the arguments and pyproject.toml together.

`--use-annotated` turns `--field-constraints` on. The command line reads the values of `pyproject.toml` before it
applies the arguments, so with `use-annotated = true` and `field-constraints = false` in `pyproject.toml` the argument
`--no-use-annotated` turns `--use-annotated` off and `--field-constraints` stays on, which `use-annotated = true`
turned on: the field constraint is written and the run warns, as in 0.96.0, while `use-annotated = false` in
`pyproject.toml` writes `conint`. With `--read-implied-options-from-merged-values` the argument writes what the key
writes.""",
    input_schema="jsonschema/integer_maximum.json",
    cli_args=["--no-use-annotated", MERGED_VALUES],
    config_content=ANNOTATED_TABLE,
    golden_output="main/option_stage_order/read_implied_options_from_merged_values[option].py",
    comparison_output="main/option_stage_order/read_implied_options_from_merged_values[default].py",
    related_options=["--use-annotated", "--field-constraints"],
)
@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize("option", [False, True], ids=["default", "option"])
def test_read_implied_options_from_merged_values(*, option: bool, tmp_path: Path) -> None:
    """--no-use-annotated over use-annotated = true and field-constraints = false in pyproject.toml (#6891).

    Without the option the model has the field constraint; with it, the conint that use-annotated = false writes.
    """
    (tmp_path / "pyproject.toml").write_text(ANNOTATED_TABLE, encoding="utf-8")
    with chdir(tmp_path):
        run_main_and_assert(
            input_path=INTEGER_MAXIMUM,
            output_path=tmp_path / "model.py",
            input_file_type="jsonschema",
            extra_args=[*PYDANTIC, "--disable-timestamp", *BUILTIN, "--no-use-annotated"]
            + ([MERGED_VALUES] if option else []),
        )


OPEN_ENUM_MAP = '{"StrEnum": "tests.data.python.open_enum.OpenStrEnum", "Enum": "tests.data.python.open_enum.OpenEnum"}'
HEADER_PAIR = ["--custom-file-header", "# inline", "--custom-file-header-path", "header.txt"]
HEADER_KEYS = 'custom-file-header = "# inline"\ncustom-file-header-path = "header.txt"\n'
STRATEGY = ["--all-exports-collision-strategy", "minimal-prefix"]
DELIMITER = ["--original-field-name-delimiter", "_"]
COLLAPSE_STRATEGY = ["--collapse-root-models-name-strategy", "child"]
SPECIALIZED_ON_310 = ["--use-specialized-enum", "--target-python-version", "3.10"]
DATACLASS = ["--output-model-type", "dataclasses.dataclass"]
TARGET_KEYS = 'generate-server = "fastapi"\ngenerate-client = "httpx2"\n'
PYDANTIC_DOCS = re.compile(r"errors\.pydantic\.dev/[^/]+/")


def _refusal(raised: BaseException) -> str:
    """Return the class of a refusal and its text; pydantic's by its first message, which names no pydantic version."""
    text = raised.errors()[0]["msg"] if isinstance(raised, ValidationError) else str(raised)
    return f"raised {type(raised).__name__}: {text}\n"


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    ("arguments", "table", "expected_exit"),
    [
        pytest.param([], "update-lock = true\nlocked = true\n", Exit.ERROR, id="lock-modes"),
        pytest.param([], TARGET_KEYS, Exit.ERROR, id="targets"),
        pytest.param(HEADER_PAIR, None, Exit.ERROR, id="custom-file-header"),
        pytest.param(STRATEGY, None, Exit.ERROR, id="all-exports-collision-strategy"),
        pytest.param(DELIMITER, None, Exit.ERROR, id="original-field-name-delimiter"),
        pytest.param(["--reuse-scope", "tree"], None, Exit.OK, id="reuse-scope"),
        pytest.param(COLLAPSE_STRATEGY, None, Exit.ERROR, id="collapse-root-models-name-strategy"),
        pytest.param(
            ["--open-enum-base-class-map", OPEN_ENUM_MAP, "--target-python-version", "3.11"],
            None,
            Exit.ERROR,
            id="open-enum-base-class-map",
        ),
        pytest.param(
            ["--open-enums", "all", "--open-enum-base-class-map", OPEN_ENUM_MAP, "--target-python-version", "3.10"],
            None,
            Exit.ERROR,
            id="str-enum-entry",
        ),
        pytest.param(SPECIALIZED_ON_310, None, Exit.ERROR, id="use-specialized-enum"),
        pytest.param([*DATACLASS, "--use-missing-sentinel"], None, Exit.ERROR, id="use-missing-sentinel"),
        pytest.param([*DATACLASS, "--union-mode", "smart"], None, Exit.ERROR, id="union-mode"),
        pytest.param(
            ["--custom-file-header-path", "header.txt"], 'custom-file-header = "# inline"\n', Exit.OK, id="split"
        ),
        pytest.param(STRATEGY, 'all-exports-scope = "recursive"\n', Exit.ERROR, id="scope-in-pyproject"),
        pytest.param(DELIMITER, "snake-case-field = true\n", Exit.OK, id="snake-case-in-pyproject"),
        pytest.param([], TARGET_KEYS + HEADER_KEYS, Exit.ERROR, id="targets-before-custom-file-header"),
        pytest.param(
            ["--output-model-type", "dataclasses.dataclass", "--output-datetime-class", "AwareDatetime", *HEADER_PAIR],
            None,
            Exit.ERROR,
            id="output-datetime-class-before-custom-file-header",
        ),
        pytest.param([*STRATEGY, *HEADER_PAIR, *DELIMITER], None, Exit.ERROR, id="custom-file-header-before-others"),
        pytest.param(
            [*DELIMITER, *COLLAPSE_STRATEGY, "--intersect-combined-schema-keywords", "--no-intersect-allof-keywords"],
            None,
            Exit.ERROR,
            id="delimiter-before-run-options",
        ),
        pytest.param(
            [*COLLAPSE_STRATEGY, "--open-enum-base-class-map", OPEN_ENUM_MAP, *SPECIALIZED_ON_310],
            None,
            Exit.ERROR,
            id="collapse-before-open-enums",
        ),
        pytest.param(
            ["--open-enum-base-class-map", OPEN_ENUM_MAP, *SPECIALIZED_ON_310],
            None,
            Exit.ERROR,
            id="open-enums-before-str-enum-entry",
        ),
        pytest.param(
            ["--open-enums", "all", "--open-enum-base-class-map", OPEN_ENUM_MAP, *SPECIALIZED_ON_310],
            None,
            Exit.ERROR,
            id="str-enum-entry-before-specialized-enum",
        ),
        pytest.param(["--reuse-scope", "tree", *SPECIALIZED_ON_310], None, Exit.ERROR, id="reuse-scope-before-refusal"),
    ],
)
def test_cli_option_relation_rows(
    arguments: list[str],
    table: str | None,
    expected_exit: Exit,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Each relation between two options at its stage, and the pairs where two of one stage meet.

    The config validators see the pyproject.toml values alone and then the arguments alone, so a pair split between
    them is not refused by its validator, and an argument is refused without a value pyproject.toml gives; the
    delimiter check after the preset sees the merged values. The run-option checks follow the deprecation of the
    --use-annotated default and show the reuse-scope warning before a refusal.
    """
    (tmp_path / "header.txt").write_text("# header\n", encoding="utf-8")
    if table is not None:
        (tmp_path / "pyproject.toml").write_text(TABLE + table, encoding="utf-8")
    with chdir(tmp_path):
        exit_code = run_main_with_args(
            ["--input", str(PERSON), "--output", "output.py", *MODEL_ARGUMENTS, *BUILTIN, *arguments],
            expected_exit=expected_exit,
        )
    assert_output(PYDANTIC_DOCS.sub("errors.pydantic.dev/<version>/", _seen(capsys, exit_code)) + _written(tmp_path))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "options",
    [
        pytest.param({"update_lock": True, "locked": True}, id="lock-modes"),
        pytest.param({"generate_server": "fastapi", "generate_client": "httpx2"}, id="targets"),
        pytest.param(
            {"update_lock": True, "locked": True, "generate_server": "fastapi", "generate_client": "httpx2"},
            id="lock-modes-before-targets",
        ),
    ],
)
def test_api_config_refuses_option_relations(options: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    """A generate() config that sets both lock modes or both targets is refused while it is validated."""
    with pytest.raises((Error, ValidationError)) as raised:
        GenerateConfig(**options)
    assert_output(_seen(capsys) + _refusal(raised.value))


@pytest.mark.usefixtures("warnings_on_stderr")
@pytest.mark.parametrize(
    "table",
    [
        pytest.param(HEADER_KEYS, id="custom-file-header"),
        pytest.param('all-exports-collision-strategy = "minimal-prefix"\n', id="all-exports-collision-strategy"),
        pytest.param(TARGET_KEYS + HEADER_KEYS, id="targets-before-custom-file-header"),
    ],
)
def test_loaded_config_refuses_option_relations_of_its_table(
    table: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """load_pyproject_config() validates its table as the command line validates pyproject.toml."""
    (tmp_path / "pyproject.toml").write_text(TABLE + table, encoding="utf-8")
    with pytest.raises((Error, ValidationError)) as raised:
        load_pyproject_config(tmp_path)
    assert_output(_seen(capsys) + _refusal(raised.value))


@pytest.mark.usefixtures("warnings_on_stderr")
def test_api_runs_option_pairs_only_the_command_line_refuses(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """generate() and a loaded config run the pairs the command line's validator, delimiter and run checks refuse."""
    (tmp_path / "pyproject.toml").write_text(
        f'{TABLE}original-field-name-delimiter = "_"\ncollapse-root-models-name-strategy = "child"\n', encoding="utf-8"
    )
    loaded = load_pyproject_config(tmp_path)
    code = generate(
        PERSON_SCHEMA,
        input_file_type="jsonschema",
        output_model_type="pydantic_v2.BaseModel",
        formatters=["builtin"],
        disable_timestamp=True,
        custom_file_header="# inline",
        custom_file_header_path=tmp_path / "pyproject.toml",
        all_exports_collision_strategy="minimal-prefix",
        original_field_name_delimiter="_",
        reuse_scope="tree",
        collapse_root_models_name_strategy="child",
        open_enum_base_class_map={"StrEnum": "tests.data.python.open_enum.OpenStrEnum"},
        use_specialized_enum=True,
        target_python_version="3.10",
    )
    assert_output(_seen(capsys) + f"loaded {loaded.original_field_name_delimiter!r}\n" + cast("str", code))
