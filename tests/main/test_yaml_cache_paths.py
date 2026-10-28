"""Regression coverage for public YAML file-cache path keys."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from datamodel_code_generator import InputFileType, load_yaml_dict_from_path
from datamodel_code_generator.watch_dependencies import WatchDependencies
from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, EXPECTED_MAIN_PATH, run_generate_and_assert

if TYPE_CHECKING:
    import pytest

_YAML_CACHE_DATA_PATH = DATA_PATH / "yaml" / "cache_paths"
_YAML_CACHE_EXPECTED_PATH = EXPECTED_MAIN_PATH / "yaml_cache_paths"
_SAME_MTIME_NS = 1_234_567_890_000_000_000


def test_load_yaml_dict_from_path_keeps_relative_cwd_cache_entries_distinct(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Parse same-named YAML schemas by their caller-relative locations."""
    schema_root = tmp_path / "schemas"
    for directory_name in ("a", "b"):
        shutil.copytree(_YAML_CACHE_DATA_PATH / directory_name, schema_root / directory_name)
        os.utime(schema_root / directory_name / "schema.yaml", ns=(_SAME_MTIME_NS, _SAME_MTIME_NS))

    monkeypatch.chdir(schema_root / "a")
    schema_from_a = load_yaml_dict_from_path(Path("schema.yaml"), "utf-8")
    repeated_schema_from_a = load_yaml_dict_from_path(Path("schema.yaml"), "utf-8")
    run_generate_and_assert(
        input_=schema_from_a,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        expected_file=_YAML_CACHE_EXPECTED_PATH / "a.py",
    )

    schema_from_b_path = schema_root / "b" / "schema.yaml"
    monkeypatch.chdir(schema_from_b_path.parent)
    schema_from_b = load_yaml_dict_from_path(Path("schema.yaml"), "utf-8")
    repeated_schema_from_b = load_yaml_dict_from_path(Path("schema.yaml"), "utf-8")
    absolute_schema_from_b = load_yaml_dict_from_path(schema_from_b_path, "utf-8")
    symlink_path = schema_from_b_path.with_name("schema-link.yaml")
    symlink_path.symlink_to(schema_from_b_path.name)
    dependencies = WatchDependencies()
    with dependencies.generation():
        symlinked_schema_from_b = load_yaml_dict_from_path(Path(symlink_path.name), "utf-8")
    run_generate_and_assert(
        input_=schema_from_b,
        input_file_type=InputFileType.JsonSchema,
        disable_timestamp=True,
        expected_file=_YAML_CACHE_EXPECTED_PATH / "b.py",
    )

    assert_output(
        "\n".join((
            f"relative A cache identity: {schema_from_a is repeated_schema_from_a}",
            f"relative B cache identity: {schema_from_b is repeated_schema_from_b}",
            f"relative and absolute B cache identity: {schema_from_b is absolute_schema_from_b}",
            f"symlink schema title: {symlinked_schema_from_b.get('title')}",
            f"symlink remains a watch dependency: {symlink_path in dependencies.files}",
        ))
        + "\n",
        _YAML_CACHE_EXPECTED_PATH / "cache_behavior.txt",
    )
