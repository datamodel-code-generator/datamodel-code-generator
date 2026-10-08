"""Rename fields that would shadow any Pydantic BaseModel attribute, metaclass attributes included."""

from __future__ import annotations

import json
import warnings
from typing import TYPE_CHECKING

import pytest
from pydantic import BaseModel

from datamodel_code_generator import DataModelType, GenerateConfig, InputFileType, generate
from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, run_main_and_assert
from tests.main.jsonschema.conftest import EXPECTED_JSON_SCHEMA_PATH, assert_file_content

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.parametrize(
    ("prefix", "case"), [(None, "default_prefix"), ("", "empty_prefix")], ids=["default-prefix", "empty-prefix"]
)
def test_basemodel_metaclass_attribute_names(output_file: Path, prefix: str | None, case: str) -> None:
    """Rename `mro`, `register` and `__name__`, which the BaseModel metaclass chain provides."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "basemodel_attribute_names.json",
        output_path=output_file,
        input_file_type="jsonschema",
        assert_func=assert_file_content,
        expected_file=f"basemodel_attribute_names_{case}.py",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
            *(["--special-field-name-prefix", prefix] if prefix is not None else []),
        ],
    )
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        with _generated_model(output_file, "basemodel_attribute_names", "AttributeNames") as model:
            assert_output(
                "".join(f"{name}={field.alias}\n" for name, field in model.model_fields.items()),
                EXPECTED_JSON_SCHEMA_PATH / f"basemodel_attribute_names_{case}_fields.txt",
            )


def test_installed_basemodel_attribute_names_are_renamed(output_file: Path) -> None:
    """Rename every public name the installed BaseModel answers, so newer Pydantic releases cannot drift silently."""
    names = sorted(
        name
        for name in {*dir(BaseModel), *dir(type(BaseModel))}
        if not name.startswith("_") and hasattr(BaseModel, name)
    )
    output_file.write_text(
        generate(
            json.dumps({
                "title": "Names",
                "type": "object",
                "properties": {name: {"type": "string"} for name in names},
            }),
            config=GenerateConfig(
                input_file_type=InputFileType.JsonSchema,
                output_model_type=DataModelType.PydanticV2BaseModel,
                disable_timestamp=True,
            ),
        ),
        encoding="utf-8",
    )
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        warnings.filterwarnings("error", message=".*shadows an attribute.*")
        with _generated_model(output_file, "installed_basemodel_attribute_names", "Names") as model:
            assert_output(
                f"kept: {sorted(name for name, field in model.model_fields.items() if name == field.alias)}\n",
                EXPECTED_JSON_SCHEMA_PATH / "basemodel_attribute_names_kept.txt",
            )
