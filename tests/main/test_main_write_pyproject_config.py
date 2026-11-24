"""Tests for --write-pyproject-config: the table of --recommend-options or --generate-pyproject-config, written."""

from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import chdir
from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, run_main_with_args

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

API = DATA_PATH / "recommend_options" / "api.yaml"
RECOMMEND = ["--input", "api.yaml", "--input-file-type", "openapi", "--recommend-options", "quick"]
GENERATE = ["--input", "api.yaml", "--output", "models.py", "--generate-pyproject-config"]
WRITE = "--write-pyproject-config"
FORM = "[tool.datamodel-codegen] is written in a form this command does not edit: an inline table or dotted keys"


def _write(
    project: Path,
    args: Sequence[str],
    capsys: pytest.CaptureFixture[str],
    *,
    pyproject: bytes | None = None,
    expected_exit: Exit = Exit.OK,
    expected_stderr: str | None = None,
) -> bytes:
    """Run the command in a project holding api.yaml and the given pyproject.toml; return the pyproject.toml after it.

    The printed report is compared with the stored output; nothing else may be printed to stderr.
    """
    project.mkdir(exist_ok=True)
    shutil.copy(API, project / "api.yaml")
    if pyproject is not None:
        (project / "pyproject.toml").write_bytes(pyproject)
    with chdir(project):
        run_main_with_args(
            [*args, WRITE],
            capsys=capsys,
            expected_exit=expected_exit,
            expected_stderr=expected_stderr,
            assert_no_stderr=expected_stderr is None,
            derived_stdout=True,
            use_builtin_default_formatter=False,
        )
    return (project / "pyproject.toml").read_bytes()


@pytest.mark.cli_doc(
    options=[WRITE],
    option_description="""Write the recommended or generated table into pyproject.toml (experimental).

`--write-pyproject-config` writes the table that `--recommend-options` prints into the pyproject.toml that gives
the table, else into ./pyproject.toml, and the table of `--generate-pyproject-config` into ./pyproject.toml; it
creates a missing file with the table alone. It sets only the keys to add or change, keeps the rest of the file as
written, and prints the keys it wrote, the notes, and the command that generates next. An edit it cannot prove
with `tomllib`, such as one of an inline table or of dotted keys, writes nothing: the block is printed as before
and the command exits with an error that says why.""",
    input_schema="recommend_options/api.yaml",
    cli_args=[*RECOMMEND, WRITE],
    expected_stdout="main/main_write_pyproject_config/write_pyproject_config.stdout.txt",
    related_options=["--recommend-options", "--generate-pyproject-config"],
)
def test_write_pyproject_config(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Write the recommended or generated table into pyproject.toml (experimental).

    `--write-pyproject-config` writes the table that `--recommend-options` prints into the pyproject.toml that gives
    the table, else into ./pyproject.toml, and the table of `--generate-pyproject-config` into ./pyproject.toml; it
    creates a missing file with the table alone. It sets only the keys to add or change, keeps the rest of the file as
    written, and prints the keys it wrote, the notes, and the command that generates next. An edit it cannot prove
    with `tomllib`, such as one of an inline table or of dotted keys, writes nothing: the block is printed as before
    and the command exits with an error that says why.
    """
    assert_output(_write(tmp_path, RECOMMEND, capsys).decode(), name="pyproject")


@pytest.mark.parametrize(
    ("args", "pyproject", "expected_stderr"),
    [
        pytest.param(
            RECOMMEND,
            b'[project]\r\nrequires-python = ">=3.12"\r\n\r\n[tool.datamodel-codegen]\r\ninput = "api.yaml"\r\n'
            b"# formatting\r\n\r\n[tool.ruff]\r\nline-length = 100\r\n",
            None,
            id="crlf-table",
        ),
        pytest.param(
            [*RECOMMEND, "--profile", "api"],
            b'[tool.datamodel-codegen.profiles.api]\noutput-model-type = "typing.TypedDict"\n',
            None,
            id="profile",
        ),
        pytest.param(
            [*RECOMMEND, "--preset", "standard-py313-20261124"],
            b'[tool.datamodel-codegen]\ninput = "api.yaml"\n',
            "the recommendation has no pyproject.toml block to write",
            id="preset-on-the-command-line",
        ),
        pytest.param(
            [*GENERATE, "--url", "https://user:secret@example.com/api.yaml?token=1", "--http-headers", "Auth: x"],
            b'\xef\xbb\xbf[project]\r\nname = "app"\r\n',
            None,
            id="credentials-bom",
        ),
        pytest.param(GENERATE, None, None, id="created"),
        pytest.param([*GENERATE, "--output-format", "json"], None, None, id="json"),
        pytest.param(
            GENERATE,
            b"[project\n",
            "it is not valid TOML: Expected ']' at the end of a table declaration (at line 1, column 9)",
            id="invalid",
        ),
        pytest.param(GENERATE, b'[tool]\ndatamodel-codegen = { input = "api.yaml" }\n', FORM, id="inline-table"),
        pytest.param(GENERATE, b'[tool]\ndatamodel-codegen.input = "old.yaml"\n', FORM, id="dotted-keys"),
        pytest.param(GENERATE, b'[tool.datamodel-codegen]\nx = """\n[a]\n"""\n', FORM, id="string-holding-a-header"),
        pytest.param(GENERATE, b'[project]\nname = "app"', None, id="no-final-newline"),
        pytest.param(
            GENERATE,
            b'[tool."datamodel-codegen"]  # models\n"input" = "old.yaml"  # was\n[[tool.other]]\na = 1\n',
            None,
            id="quoted-keys",
        ),
        pytest.param(
            [*GENERATE, "--target-python-version", "3.12"],
            b'[tool.datamodel-codegen]\nformatters = [\n  "black",\n]\ntarget_python_version = "3.10"\n\n# next\n[x]\n',
            None,
            id="multi-line-value-and-other-spelling",
        ),
        pytest.param(GENERATE, b'[tool.datamodel-codegen.jobs.api]\ninput = "api.yaml"\n', None, id="only-a-subtable"),
        pytest.param(
            GENERATE, b'[tool.datamodel-codegen]\ninput = "api.yaml"\noutput = "models.py"\n', None, id="unchanged"
        ),
    ],
)
def test_write_pyproject_config_projects(
    args: list[str],
    pyproject: bytes | None,
    expected_stderr: str | None,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Merge only the keys to add or change into the table the command prints, keeping line endings and the BOM.

    An old line of a key, in either spelling and over several lines, is replaced; a new key follows the last key of the
    table, and a missing table is appended. A profile gets its own table; a preset on the command line keeps the
    recommendation there. Credentials are never
    written and a URL is written without user information, query and fragment. A file that is not TOML or a table this
    command cannot edit stays as it was, the block is printed as without the option, and the command fails.
    """
    written = _write(
        tmp_path,
        args,
        capsys,
        pyproject=pyproject,
        expected_exit=Exit.OK if expected_stderr is None else Exit.ERROR,
        expected_stderr=None
        if expected_stderr is None
        else f"Error: nothing was written to pyproject.toml: {expected_stderr}\n",
    )
    assert_output(repr(written.decode("utf-8")), name="pyproject")


def test_write_pyproject_config_keeps_written_values(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A second run finds every value written and writes nothing."""
    _write(tmp_path, RECOMMEND, capsys)
    assert_output(_write(tmp_path, RECOMMEND, capsys).decode(), name="pyproject")


def test_write_pyproject_config_outside_the_working_directory(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse to write relative paths of the command into the table of a pyproject.toml in a parent directory.

    The pyproject.toml of the working directory has no table, so the run reads the one of the parent.
    """
    (tmp_path / "pyproject.toml").write_bytes(b'[tool.datamodel-codegen.jobs.other]\ninput = "other.yaml"\n')
    _write(
        tmp_path / "api",
        RECOMMEND,
        capsys,
        pyproject=b"",
        expected_exit=Exit.ERROR,
        expected_stderr="Error: nothing was written to ../pyproject.toml: the paths of the command are relative to "
        "the working directory, not to ../pyproject.toml\n",
    )


@pytest.mark.abnormal_path("another process edits pyproject.toml between the command's read and its replace")
def test_write_pyproject_config_reads_the_file_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Write nothing when pyproject.toml changes while the command runs."""

    def edit_meanwhile(text: str, *_: object) -> tuple[str, list[str]]:
        (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
        return text, ["input"]

    monkeypatch.setattr("datamodel_code_generator._config_export._merged_table_text", edit_meanwhile)
    written = _write(
        tmp_path,
        GENERATE,
        capsys,
        expected_exit=Exit.ERROR,
        expected_stderr="Error: nothing was written to pyproject.toml: it changed while the command ran\n",
    )
    assert_output(written.decode(), name="pyproject")


@pytest.mark.parametrize(
    ("args", "expected_stderr"),
    [
        pytest.param([], "requires --recommend-options or --generate-pyproject-config", id="alone"),
        pytest.param(
            [*GENERATE, "--ignore-pyproject"], "cannot be used with --ignore-pyproject", id="ignore-pyproject"
        ),
    ],
)
def test_write_pyproject_config_needs_a_table(
    args: list[str], expected_stderr: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse the option without a command that prints a table, and beside --ignore-pyproject."""
    run_main_with_args(
        [*args, WRITE],
        capsys=capsys,
        expected_exit=Exit.ERROR,
        expected_stderr=f"Error: --write-pyproject-config {expected_stderr}\n",
        use_builtin_default_formatter=False,
    )


def test_write_pyproject_config_compares_values_by_type(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """An integer where a boolean is wanted is rewritten; the same boolean stays as it is."""
    args = ["--input", "api.yaml", "--input-file-type", "openapi", "--snake-case-field", "--generate-pyproject-config"]
    table = b"[tool.datamodel-codegen]\nsnake-case-field = 1\n"
    assert_output(_write(tmp_path, args, capsys, pyproject=table).decode(), name="pyproject")
    assert_output(_write(tmp_path, args, capsys).decode(), name="pyproject")


def test_write_pyproject_config_without_values_creates_nothing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A command whose only option is a credential has no values: no file is created and none is claimed."""
    tmp_path.mkdir(exist_ok=True)
    with chdir(tmp_path):
        run_main_with_args(
            ["--http-headers", "A: b", "--generate-pyproject-config", WRITE],
            capsys=capsys,
            assert_no_stderr=True,
            derived_stdout=True,
            use_builtin_default_formatter=False,
        )
    assert_output("".join(f"{path.name}\n" for path in sorted(tmp_path.iterdir())) or "no file\n", name="files")
