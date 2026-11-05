"""Escape the text of the string literals and docstrings a module holds, so that every module parses."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from jsonschema import Draft7Validator
from jsonschema import ValidationError as SchemaValidationError

from datamodel_code_generator.__main__ import Exit
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    assert_generated_model_json_invalid,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

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


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel"], id="model"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"], id="model-type-alias"),
        pytest.param(
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias", "--target-pydantic-version", "2"],
            id="model-type-alias-target-2",
            marks=pytest.mark.filterwarnings("ignore:The generated models need pydantic>=2.5:UserWarning"),
        ),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--use-type-alias"], id="dataclass-type-alias"),
    ],
)
def test_regex_surrogate_engine(output_file: Path, extra_args: list[str]) -> None:
    """Select Python's regex engine for a pattern with a lone surrogate, also one a type alias holds (#6320).

    The default engine replaced the surrogate by U+FFFD, so the model took a value with three of them that the
    schema rejects. An alias keeps the pattern a string, as no Pydantic builds a compiled one that holds a lone
    surrogate, and the model that uses the alias selects the engine. The schema is written here, as a YAML parser
    rejects the escape of a lone surrogate in a fixture file.
    """
    schema = {
        "title": "Surrogate",
        "type": "object",
        "$defs": {
            "Text": {"type": "string", "pattern": "^a\ud800b$"},
            "Holder": {"type": "object", "properties": {"named": {"$ref": "#/$defs/Text"}}},
        },
        "properties": {"inline": {"type": "string", "pattern": "^a\ud800b$"}, "holder": {"$ref": "#/$defs/Holder"}},
    }
    (input_path := output_file.parent / "regex_surrogate_engine.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin", *extra_args],
        force_exec_validation=True,
    )
    validator = Draft7Validator(schema)
    replaced = "a\ufffd\ufffd\ufffdb"
    for name, invalid in {"inline": {"inline": replaced}, "holder": {"holder": {"named": replaced}}}.items():
        with pytest.raises(SchemaValidationError):
            validator.validate(invalid)
        assert_generated_model_json_invalid(
            output_file,
            module_name=f"regex_surrogate_engine_{name}",
            model_name="Surrogate",
            invalid_json=json.dumps(invalid),
            expected_error_type="string_pattern_mismatch",
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


SURROGATE_DEFINITIONS = {
    "Kind": {"type": "string", "enum": ["v\ud800x", "p"]},
    "Tag": {"const": "t\ud800"},
    "Kinds": {"type": "array", "items": {"$ref": "#/$defs/Kind"}},
}
REFUSED_SURROGATES = {
    "alias": (
        {"k\ud800y": {"type": "string"}},
        [],
        "Surrogate.k_y: its alias 'k\\ud800y' at surrogate_refused.json#/properties/k\\ud800y",
    ),
    "enum": (
        {"kind": {"type": "string", "enum": ["v\ud800x", "p"]}},
        [],
        "Surrogate.kind: the value 'v\\ud800x' of the enum Kind at surrogate_refused.json#/properties/kind",
    ),
    "literal": (
        {"tag": {"const": "t\ud800"}},
        [],
        "Surrogate.tag: the Literal value 't\\ud800' at surrogate_refused.json#/properties/tag",
    ),
    "type_alias_literal": (
        {"tag": {"$ref": "#/$defs/Tag"}},
        ["--use-type-alias"],
        "Surrogate.tag: the Literal value 't\\ud800' at surrogate_refused.json#/$defs/Tag",
    ),
    "type_alias_enum": (
        {"kinds": {"$ref": "#/$defs/Kinds"}},
        ["--use-type-alias"],
        "Surrogate.kinds: the value 'v\\ud800x' of the enum Kind at surrogate_refused.json#/$defs/Kind",
    ),
}


@pytest.mark.parametrize("output_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
@pytest.mark.parametrize("case", REFUSED_SURROGATES)
def test_lone_surrogate_refused_by_pydantic(
    output_file: Path, capsys: pytest.CaptureFixture[str], output_type: str, case: str
) -> None:
    """Fail for a lone surrogate in an alias, a ``Literal`` value or an enum value of a Pydantic model (#5983).

    pydantic-core cannot build a validator that holds one, so the generated module failed to import. A type alias is
    read through, as Pydantic builds it into the model. The error names the field and the schema location, its lone
    surrogates written as escapes. The schema is written here, as a YAML parser rejects the escape of a lone surrogate
    in a fixture file.
    """
    properties, extra_args, site = REFUSED_SURROGATES[case]
    schema = {"title": "Surrogate", "type": "object", "properties": properties, "$defs": SURROGATE_DEFINITIONS}
    (input_path := output_file.parent / "surrogate_refused.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_type, *extra_args],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=f"Pydantic cannot build {site} holds a lone surrogate, which no Pydantic validator",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize(
    ("option", "aliases", "site"),
    [
        pytest.param("--aliases", {"name": ["name", "n\ud800"]}, "its validation alias 'n\\ud800'", id="alias_choices"),
        pytest.param(
            "--serialization-aliases", {"name": "s\ud800"}, "its serialization alias 's\\ud800'", id="serialization"
        ),
    ],
)
def test_lone_surrogate_configured_alias_refused_by_pydantic(
    output_file: Path, capsys: pytest.CaptureFixture[str], option: str, aliases: dict[str, object], site: str
) -> None:
    """Fail for a lone surrogate in an alias the options give a Pydantic field (#5983).

    Neither an ``AliasChoices`` nor a serialization alias that holds one builds, so the module failed to import.
    """
    schema = {"title": "Surrogate", "type": "object", "properties": {"name": {"type": "string"}}}
    (input_path := output_file.parent / "surrogate_alias.json").write_text(json.dumps(schema), encoding="utf-8")
    (aliases_path := output_file.parent / "surrogate_aliases.json").write_text(json.dumps(aliases), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[option, str(aliases_path)],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=f"Surrogate.name: {site} at surrogate_alias.json#/properties/name holds a lone",
        output_should_not_exist=True,
    )


@pytest.mark.parametrize("output_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_lone_surrogate_alias_replaced_by_serialization_alias(output_file: Path, output_type: str) -> None:
    r"""Keep the model whose only alias holding a lone surrogate the call does not write (#5983).

    ``--use-serialization-alias`` writes the serialization alias that ``--serialization-aliases`` gives in place of the
    property name ``k\ud800y``, so Pydantic reads no lone surrogate and the module builds, as in 0.89.0.
    """
    schema = {"title": "Surrogate", "type": "object", "properties": {"k\ud800y": {"type": "string"}}}
    (input_path := output_file.parent / "surrogate_replaced.json").write_text(json.dumps(schema), encoding="utf-8")
    (aliases_path := output_file.parent / "surrogate_replaced_aliases.json").write_text(
        json.dumps({"k\ud800y": "key"}), encoding="utf-8"
    )
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_type,
            "--use-serialization-alias",
            "--serialization-aliases",
            str(aliases_path),
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="surrogate_replaced",
        model_name="Surrogate",
        valid_json='{"k_y": "v"}',
        invalid_json='{"k_y": 1}',
        expected_error_type="string_type",
        expected_attribute_path=("k_y",),
        expected_attribute_value="v",
    )


def test_lone_surrogate_renamed_field_refused_by_msgspec(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Fail for a msgspec field renamed to a name that holds a lone surrogate (#5983).

    ``field(name=...)`` raised ``UnicodeEncodeError`` when the generated module was imported. An enum value and a
    ``Literal`` value that hold one stay as written: msgspec builds the module.
    """
    schema = {"title": "Surrogate", "type": "object", "properties": {"k\ud800y": {"type": "string"}}}
    (input_path := output_file.parent / "surrogate_renamed.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "msgspec.Struct"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=(
            "msgspec.Struct model 'Surrogate' cannot express its field 'k_y' named 'k\\ud800y' at "
            "surrogate_renamed.json#/properties/k\\ud800y: msgspec refuses a renamed field name that contains a "
            "backslash, a double quote, a control character or a lone surrogate."
        ),
        output_should_not_exist=True,
    )


def test_lone_surrogate_enum_unused_by_pydantic(output_file: Path) -> None:
    """Keep an enum whose value holds a lone surrogate where no Pydantic model reaches it (#5983).

    Pydantic builds no validator of the enum, so the module imports; its value is written as its escape.
    """
    schema = {"title": "Surrogate", "type": "string", "enum": ["v\ud800x", "p"]}
    (input_path := output_file.parent / "surrogate_enum.json").write_text(json.dumps(schema), encoding="utf-8")
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--formatters", "builtin"],
        force_exec_validation=True,
    )
