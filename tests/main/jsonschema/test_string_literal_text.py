"""Escape the text of the string literals and docstrings a module holds, so that every module parses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft7Validator
from jsonschema import ValidationError as SchemaValidationError

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "string_literal_text.json"

REGEX_CONTROL_PATTERNS = {
    "line_feed": ("^a\nb$", "a\nb", "ab"),
    "backspace": ("^a\bb$", "a\bb", "ab"),
    "escaped_line_feed": ("^a\\\nb$", "a\nb", "a\\nb"),
    "escaped_tab": ("^a\\\tb$", "a\tb", "a\\tb"),
    "verbose_comment": ("(?x)^a # it's\n b$", "ab", "ac"),
    "verbose_tab": ("(?x)^a\tb$", "ab", "a\tb"),
}


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


@pytest.mark.parametrize("output_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_regex_literal_text(output_file: Path, output_type: str) -> None:
    """Write a pattern as a raw literal when one holds it, and as a plain literal otherwise, on every Python.

    The running Python's parser decided it: a null byte was a ``ValueError`` before Python 3.11, and a backslash
    before the escaped quote let the rest of the pattern read as an expression or a comment.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "regex_literal_text.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("output_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_regex_literal_surrogate(output_file: Path, output_type: str) -> None:
    """Write a pattern with a lone surrogate as a plain literal, as the ``Field`` form of the same pattern is.

    The running Python's parser raised a ``UnicodeEncodeError`` on a raw literal that holds one, on every Python.
    The schema is written here, as a YAML parser rejects the escape of a lone surrogate in a fixture file.
    """
    schema = {
        "title": "Surrogate",
        "type": "object",
        "properties": {"surrogate": {"type": "string", "pattern": "^a\ud800b$"}},
    }
    (input_path := output_file.parent / "regex_literal_surrogate.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("output_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_regex_literal_control_characters(output_file: Path, output_type: str) -> None:
    r"""Write a pattern as a raw literal only where its control characters keep their meaning as regex escapes.

    A raw literal read ``\b`` as the word boundary instead of a backspace and ``\n`` after a backslash as a
    backslash and a letter instead of the escaped line feed, and a verbose pattern took the escape of a tab or line
    feed as a character to match, where the character ended a comment or was ignored. Each pattern is checked against
    the schema's own verdict on a payload that matches it and one that does not.
    """
    schema = {
        "title": "Control",
        "type": "object",
        "properties": {name: {"type": "string", "pattern": row[0]} for name, row in REGEX_CONTROL_PATTERNS.items()},
    }
    (input_path := output_file.parent / "regex_literal_control.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type],
        force_exec_validation=True,
    )
    validator = Draft7Validator(schema)
    for name, (_, valid, invalid) in REGEX_CONTROL_PATTERNS.items():
        validator.validate({name: valid})
        with pytest.raises(SchemaValidationError):
            validator.validate({name: invalid})
        assert_generated_model_json_validation(
            output_file,
            module_name=f"regex_literal_control_{name}",
            model_name="Control",
            valid_json=json.dumps({name: valid}),
            invalid_json=json.dumps({name: invalid}),
            expected_error_type="string_pattern_mismatch",
            expected_attribute_path=(name,),
            expected_attribute_value=valid,
        )


@pytest.mark.parametrize(
    "output_type",
    ["pydantic_v2.BaseModel", "dataclasses.dataclass", "msgspec.Struct", "typing.TypedDict"],
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--use-schema-description", "--use-field-description"], id="docstrings"),
        pytest.param(["--use-inline-field-description"], id="inline-docstrings"),
    ],
)
def test_lone_surrogate_docstring_text(output_file: Path, output_type: str, extra_args: list[str]) -> None:
    """Replace a lone surrogate in a docstring and a comment by U+FFFD, as no module can hold one (#5983).

    Writing the module raised ``UnicodeEncodeError``; an escape would not do either, as Python 3.13 and later compile
    no docstring that holds a lone surrogate. A ``Field`` description keeps it as the escape of its literal. The
    schema is written here, as a YAML parser rejects the escape of a lone surrogate in a fixture file.
    """
    schema = {
        "title": "Surrogate",
        "type": "object",
        "description": "model \ud800 text",
        "properties": {
            "field": {"type": "string", "description": "field \udfff text"},
            "union": {"anyOf": [{"type": "string"}, {"type": "integer"}], "description": "union \ud800 text"},
            "kind": {"type": "string", "enum": ["a", "b"], "description": "enum \ud800 text"},
        },
    }
    (input_path := output_file.parent / "surrogate_docstring.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type, *extra_args],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("output_type", ["dataclasses.dataclass", "msgspec.Struct", "typing.TypedDict"])
def test_lone_surrogate_literal_text(output_file: Path, output_type: str) -> None:
    r"""Write a lone surrogate in an enum value as its escape, which keeps the value (#5983).

    Writing the module raised ``UnicodeEncodeError``, where the ``Literal`` of a const already wrote ``'\ud800'``.
    The schema is written here, as a YAML parser rejects the escape of a lone surrogate in a fixture file.
    """
    schema = {
        "title": "Surrogate",
        "type": "object",
        "properties": {"kind": {"type": "string", "enum": ["v\ud800x", "p"]}},
    }
    (input_path := output_file.parent / "surrogate_literal.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", output_type],
        force_exec_validation=True,
    )


def test_lone_surrogate_typed_dict_key_text(output_file: Path) -> None:
    """Write a lone surrogate in a ``TypedDict`` key as its escape, which keeps the key (#5983).

    Writing the module raised ``UnicodeEncodeError``. The schema is written here, as a YAML parser rejects the escape
    of a lone surrogate in a fixture file.
    """
    schema = {
        "title": "Surrogate",
        "type": "object",
        "properties": {"k\ud800y": {"type": "string"}, "class": {"type": "integer"}},
        "required": ["k\ud800y"],
    }
    (input_path := output_file.parent / "surrogate_key.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--output-model-type", "typing.TypedDict"],
        force_exec_validation=True,
    )


def test_lone_surrogate_comment_text(output_file: Path) -> None:
    """Replace a lone surrogate in a comment by U+FFFD, as no module can hold one (#5983).

    Writing the module raised ``UnicodeEncodeError`` on the comment that ``--extra-template-data`` gives a class. The
    schema and the template data are written here, as a YAML parser rejects the escape of a lone surrogate in a fixture
    file.
    """
    schema = {"title": "Surrogate", "type": "object", "properties": {"a": {"type": "string"}}}
    (input_path := output_file.parent / "surrogate_comment.json").write_text(json.dumps(schema), encoding="utf-8")
    (data_path := output_file.parent / "surrogate_comment_data.json").write_text(
        json.dumps({"Surrogate": {"comment": "class \ud800 comment"}}), encoding="utf-8"
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", "--extra-template-data", str(data_path)],
        force_exec_validation=True,
    )
