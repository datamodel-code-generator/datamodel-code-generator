"""End-to-end coverage for builtin formatter output that must parse, stay in bounds and not depend on the host."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import DataModelType, Formatter, InputFileType
from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    assert_generated_model_json_validation,
    run_generate_file_and_assert,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_wrap_string_literal_keeps_escape_sequences_whole(output_file: Path) -> None:
    r"""Split a long description between characters and escape sequences, so that the pieces join to the same text.

    A split used to fall inside ``\\`` and ``\x7f``, which wrote a literal that does not parse.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "builtin_wrap_string_escapes.json"
    run_generate_file_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=88,
        wrap_string_literal=True,
        disable_timestamp=True,
    )
    properties = json.loads(input_path.read_text(encoding="utf-8"))["properties"]
    with _generated_model(output_file, "builtin_wrap_string_escapes", "WrapEscapes") as model:
        assert_output(
            json.dumps(
                {
                    name: field.description == properties[name]["description"]
                    for name, field in model.model_fields.items()
                },
                indent=2,
            )
            + "\n",
            name="descriptions_read_back",
        )


@pytest.mark.parametrize("line_length", [18, 21])
def test_wrap_string_literal_with_a_line_length_that_leaves_no_room(output_file: Path, line_length: int) -> None:
    """Finish when the line length leaves no room for content, or room for less than one escape sequence.

    The literal stays whole when the content width is zero or less; otherwise each piece takes at least one character
    or escape sequence, so wrapping ends. It used to loop for ever, or raise IndexError.
    """
    input_path = JSON_SCHEMA_DATA_PATH / "builtin_wrap_string_narrow.json"
    run_generate_file_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=line_length,
        wrap_string_literal=True,
        disable_timestamp=True,
    )
    properties = json.loads(input_path.read_text(encoding="utf-8"))["properties"]
    with _generated_model(output_file, "builtin_wrap_string_narrow", "NarrowWrap") as model:
        assert_output(
            json.dumps(
                {
                    name: field.description == properties[name]["description"]
                    for name, field in model.model_fields.items()
                },
                indent=2,
            )
            + "\n",
            name="descriptions_read_back",
        )


def test_use_double_quotes_writes_f_strings_alike_on_every_python(output_file: Path) -> None:
    """Normalize the quotes of the f-strings of a generated validator on every Python that runs the generator.

    Python 3.12 and later read an f-string as several tokens, so the quotes of ``f'Property {key!r}...'`` were kept
    there and changed on 3.11 and earlier.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "schema_validators_custom_base_class_name.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--generate-schema-validators",
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--use-double-quotes",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="builtin_f_string_quotes",
        model_name="CustomBaseClassName",
        valid_json='{"item_1":7}',
        invalid_json='{"bad":7}',
        expected_error_type="value_error",
        expected_attribute_path=("item_1",),
        expected_attribute_value=7,
    )


@pytest.mark.parametrize(
    ("line_length", "options"),
    [
        pytest.param(88, {}, id="long-names"),
        pytest.param(20, {"use_default_kwarg": True, "strict_nullable": True}, id="narrow-default-kwarg"),
    ],
)
def test_empty_literal_defaults_are_not_split(output_file: Path, line_length: int, options: dict[str, bool]) -> None:
    """Keep an empty ``{}`` or ``[]`` default on one line, however long the line is.

    A line over the length used to be split at the brackets of a literal with no entries, which left a blank line
    between them.
    """
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "builtin_empty_literal_defaults.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=line_length,
        disable_timestamp=True,
        **options,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="builtin_empty_literal_defaults",
        model_name="EmptyLiteralDefaults",
        valid_json="{}",
        invalid_json='{"mapping":1}',
        expected_error_type="dict_type",
        expected_attribute_path=("values",),
        expected_attribute_value=[],
    )


def test_dataclass_field_call_moves_to_parentheses_when_its_head_overflows(output_file: Path) -> None:
    """Put a ``field(...)`` default in parentheses when the line up to its bracket is over the line length.

    The call used to be split at its own bracket, which left a head of 91 columns.
    """
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "builtin_field_call_head.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.DataclassesDataclass,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=88,
        disable_timestamp=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="builtin_field_call_head",
        model_name="CallHead",
        valid_json="{}",
        invalid_json='{"aCollectionFieldNameThatKeepsGoingAndGoingxxxxxxxxxxxxxxxxxx":1}',
        expected_error_type="list_type",
        expected_attribute_path=("aCollectionFieldNameThatKeepsGoingAndGoingxxxxxxxxxxxxxxxxxx",),
        expected_attribute_value=["a"],
    )


def test_union_annotation_over_the_line_length_is_split_at_its_brackets(output_file: Path) -> None:
    """Split a ``Union`` or ``Optional[Union]`` annotation of 92 columns at its brackets, as black does.

    A field without a default, and a ``Union`` inside ``Optional`` with a default, used to stay on one line.
    """
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "builtin_union_annotation_split.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=88,
        use_union_operator=False,
        disable_timestamp=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="builtin_union_annotation_split",
        model_name="UnionSplit",
        valid_json='{"values":{"a1":"x"}}',
        invalid_json='{"values":{"c":1}}',
        expected_error_type="string_pattern_mismatch",
        expected_attribute_path=("values",),
        expected_attribute_value={"a1": "x"},
    )


def test_empty_tuple_annotation_over_the_line_length_is_split_at_its_brackets(output_file: Path) -> None:
    """Split a ``tuple[()]`` annotation of 90 columns at its brackets with ``()`` as its element, as black does.

    The split wrote no element, ``tuple[`` and ``]`` around a blank line, which Python cannot parse.
    """
    run_generate_file_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "builtin_empty_tuple_annotation_split.json",
        output_path=output_file,
        input_file_type=InputFileType.JsonSchema,
        output_model_type=DataModelType.PydanticV2BaseModel,
        formatters=[Formatter.BUILTIN],
        builtin_format_line_length=88,
        disable_timestamp=True,
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="builtin_empty_tuple_annotation_split",
        model_name="EmptyTupleSplit",
        valid_json='{"anEmptyTupleFieldWhoseAnnotationRunsOverTheLineLengthxxxxxxxxxxxxxxxxxxxxxx":[]}',
        invalid_json='{"anEmptyTupleFieldWhoseAnnotationRunsOverTheLineLengthxxxxxxxxxxxxxxxxxxxxxx":[1]}',
        expected_error_type="too_long",
        expected_attribute_path=("anEmptyTupleFieldWhoseAnnotationRunsOverTheLineLengthxxxxxxxxxxxxxxxxxxxxxx",),
        expected_attribute_value=(),
    )
