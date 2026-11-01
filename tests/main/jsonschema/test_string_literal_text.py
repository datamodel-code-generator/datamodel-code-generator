"""Escape the text of the string literals and docstrings a module holds, so that every module parses."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "string_literal_text.json"


@pytest.mark.parametrize(
    "output_type",
    ["pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct", "typing.TypedDict"],
)
def test_inline_field_description_text(output_file: Path, output_type: str) -> None:
    """Write the one-line docstring of a description that ends with a quote, and one that holds a null byte.

    The docstring ended with the quote of the description, which closed the literal early, and a null byte was
    written as it is, which no module can hold.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type, "--use-inline-field-description"],
        force_exec_validation=True,
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--use-schema-description", "--use-field-description"], id="block-docstrings"),
        pytest.param(
            ["--use-schema-description", "--use-field-description", "--use-single-line-docstring"],
            id="single-line-docstrings",
        ),
    ],
)
def test_docstring_text(output_file: Path, extra_args: list[str]) -> None:
    """Write a null byte in the docstring of a model and of a field as an escape, in either docstring layout."""
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "dataclasses.dataclass", *extra_args],
        force_exec_validation=True,
    )


def test_typed_dict_key_text(output_file: Path) -> None:
    """Write a ``TypedDict`` key with a null byte, as the other output types and an enum value do.

    The null byte was written as it is, which no module can hold, while the quote, the backslash and the tab were
    already escaped.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "typed_dict_key_text.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "typing.TypedDict"],
        force_exec_validation=True,
    )
