"""End-to-end generation with explicitly loaded project configuration."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from pydantic import ValidationError

from datamodel_code_generator import Error, GenerateConfig, chdir, load_pyproject_config
from tests.conftest import assert_inputs_not_mutated, assert_output, create_assert_file_content
from tests.main.conftest import run_generate_and_assert, run_main_and_assert

if TYPE_CHECKING:
    from collections.abc import Iterator

DATA_PATH = Path(__file__).parents[1] / "data" / "pyproject_config"
EXPECTED_PATH = Path(__file__).parents[1] / "data" / "expected" / "main" / "pyproject_config"
CASES = json.loads((DATA_PATH / "cases.json").read_text())
ERRORS = json.loads((DATA_PATH / "errors.json").read_text())


@pytest.fixture
def project(tmp_path: Path) -> Iterator[Path]:
    """Copy external project inputs and isolate project discovery."""
    project = shutil.copytree(DATA_PATH, tmp_path / "project")
    (project / ".git").mkdir()
    (project / "child").mkdir()
    with chdir(project):
        yield project


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_load_pyproject_config_generation(case: dict[str, Any], project: Path) -> None:
    """Apply project settings and either form of explicit override to real generation."""
    if filename := case.get("file"):
        shutil.copyfile(project / filename, project / "pyproject.toml")
    overrides = case.get("overrides")
    if case.get("kind") == "config":
        overrides = GenerateConfig.model_validate(overrides)
    cwd = project / "child" if case.get("child") else project
    path = {
        "directory": project,
        "file": project / "pyproject.toml",
        "relative": Path("..") / "pyproject.toml",
        "string_directory": str(project),
        "string_file": str(project / "pyproject.toml"),
        "string_relative": "../pyproject.toml",
    }.get(case.get("path"))
    with chdir(cwd), assert_inputs_not_mutated({"overrides": overrides}):
        config = load_pyproject_config(path, case.get("profile"), overrides=overrides)
        values = config.model_dump(mode="json", exclude_unset=True)
        for field in ("settings_path", "output"):
            if isinstance(value := getattr(config, field), Path):
                values[field] = value.as_posix().replace(project.as_posix(), "$PROJECT")
        normalized = json.dumps(values, indent=2, sort_keys=True)
        assert_output(normalized + "\n", EXPECTED_PATH / f"{case.get('expected_config', case['name'])}.txt")
        run_generate_and_assert(
            input_=project / "schema.json",
            config=config,
            expected_file=EXPECTED_PATH / f"{case.get('expected_code', case['name'])}.py",
            unchanged_inputs={"overrides": overrides},
        )


@pytest.mark.parametrize("case", ERRORS, ids=[case.get("file", case["type"]) for case in ERRORS])
def test_load_pyproject_config_errors(case: dict[str, Any], project: Path) -> None:
    """Reject invalid project settings and overrides before generation."""
    if filename := case.get("file"):
        shutil.copyfile(project / filename, project / "pyproject.toml")
    errors = {"Error": Error, "ValidationError": ValidationError, "ValueError": ValueError, "TypeError": TypeError}
    with pytest.raises(errors[case["type"]], match=case["match"]):
        load_pyproject_config(profile=case.get("profile"), overrides=case.get("overrides"))


def test_load_pyproject_config_cli_parity(project: Path) -> None:
    """The CLI shares project-relative JSON loading and produces the same models."""
    shutil.copyfile(project / "json_files.toml", project / "pyproject.toml")
    with chdir(project / "child"):
        run_main_and_assert(
            input_path=project / "schema.json",
            output_path=project / "cli.py",
            expected_file="json_files.py",
            assert_func=create_assert_file_content(EXPECTED_PATH),
            transform=str.rstrip,
        )
