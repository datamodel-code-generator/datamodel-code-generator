"""Generated Pydantic models use only forms that Pydantic accepts: typed extras, regex engines and model configs."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from pydantic import ValidationError

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

VERBOSE_PAYLOADS = {
    "Backreference": ("aa", "ab"),
    "ClassComment": ("a#a", "b"),
    "CommentedSyntax": ("abc", "ABC"),
    "EscapedHash": ("a#b", "ab"),
    "ClassEscapedHash": ("a#b", "c"),
    "AcceptedVerbose": ("abc", "ABC"),
    "MidPatternFlag": ("ab", "ac"),
    "ScopedVerbose": ("abc", "ABC"),
}

REPETITION_PAYLOADS = {
    "CommentAfterComma": ("aaa", "aaaa"),
    "CommentsAroundNumbers": ("aa", "a"),
    "CommentBeforeUpperBound": ("a{,3}", "aaa"),
}

REVERSED_REPETITION_PAYLOADS = {
    "SpacedReversed": ("a{ 3 , 2 }", "aaa"),
    "VerboseSpacedReversed": ("a{3,2}", "aaa"),
    "VerboseCommentedReversed": ("a{3,2}", "aaa"),
    "SpacedOrdered": ("aa", "a{ 2 , 3 }"),
}

CODE_POINT_PAYLOADS = {
    "SurrogateRanges": ("tab\tand text", "bell\u0007"),
    "SurrogateOutsideClass": ("a", "b"),
    "BracedCode": ("AA", "B"),
}


def _outcome(validate: Callable[[str], object], payload: object) -> str:
    """Return ``valid``, or the types of the errors that the validator reports for a JSON payload."""
    try:
        validate(json.dumps(payload))
    except ValidationError as error:
        return ", ".join(sorted({item["type"] for item in error.errors()}))
    return "valid"


def _outcomes(model: object, payloads: Mapping[str, object]) -> str:
    """Return a line per JSON payload with its outcome."""
    validate = _model_json_validator(model)
    return "".join(f"{label}: {_outcome(validate, payload)}\n" for label, payload in payloads.items())


@pytest.mark.parametrize("target_python_version", ["3.11", "3.14"])
def test_typed_extras_stay_a_dict_with_generic_container_types(output_file: Path, target_python_version: str) -> None:
    """Annotate ``__pydantic_extra__`` with ``dict`` although ``--use-generic-container-types`` writes ``Mapping``.

    Pydantic accepts only ``dict[str, ...]`` there and fails at import on any other annotation. The class body
    ``__annotations__`` of a target before Python 3.14 and the plain annotation of 3.14 follow the same rule, and the
    generic containers inside the values stay.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "typed_extras_generic_containers.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-generic-container-types",
            "--target-python-version",
            target_python_version,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    with _generated_model(output_file, "typed_extras_generic_containers", "Payload") as model:
        assert_output(
            _outcomes(
                model,
                {
                    "declared and extra values": {"name": "n", "tags": ["a"], "extra": [1, 9]},
                    "extra value above the maximum": {"extra": [10]},
                    "extra value that is no array": {"extra": "text"},
                },
            ),
            name="payloads",
        )


def test_verbose_patterns_select_the_python_engine_for_syntax_rust_rejects(output_file: Path) -> None:
    """Read a verbose pattern that starts with ``(?x)``: the Rust engine rejects a backreference or an unclosed class.

    Whitespace is ignored and a ``#`` starts a comment that runs to the end of the line, in a character class too,
    where Rust reports the class unclosed. A comment or an escaped ``#`` that holds Rust-rejected syntax keeps the
    default engine, as does a pattern whose flag groups the scan does not read (``(?x)`` after the start, ``(?x:``).
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_verbose.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = ""
    for name, payloads in VERBOSE_PAYLOADS.items():
        with _generated_model(output_file, "rust_unsupported_verbose", name) as model:
            outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
    assert_output(outcomes, name="payloads")


def test_verbose_comments_inside_a_counted_repetition_keep_the_rust_engine(output_file: Path) -> None:
    """Keep the default engine for a verbose pattern with comments inside a counted repetition, as Rust reads it.

    The Rust engine skips whitespace and a comment after the ``{``, a number and the ``,`` of a counted repetition,
    where Python's ``re`` reads the braces as text. A repetition without a lower bound is rejected by Rust either way.
    ``--field-constraints`` writes the pattern with the newline that ends a comment.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_verbose_repetition.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--field-constraints",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = ""
    for name, payloads in REPETITION_PAYLOADS.items():
        with _generated_model(output_file, "rust_unsupported_verbose_repetition", name) as model:
            outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
    assert_output(outcomes, name="payloads")


def test_counted_repetitions_with_a_minimum_above_the_maximum_select_the_python_engine(output_file: Path) -> None:
    """Select ``python-re`` for a counted repetition whose minimum exceeds its maximum, which Rust rejects.

    Python's ``re`` reads the braces as text where whitespace or a verbose comment is inside, so only that engine
    imports the model. The same repetition in order stays on the default engine, which repeats.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_reversed_repetition.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--field-constraints",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = ""
    for name, payloads in REVERSED_REPETITION_PAYLOADS.items():
        with _generated_model(output_file, "rust_unsupported_reversed_repetition", name) as model:
            outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
    assert_output(outcomes, name="payloads")


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass"])
def test_surrogate_escapes_select_the_python_engine(output_file: Path, output_model_type: str) -> None:
    r"""Select ``python-re`` for a pattern escaping a surrogate, which Rust rejects and Python's ``re`` reads (#6593).

    AWS documents write ``\\uD800`` inside a class, outside a class it is the same, and only Python's ``re`` imports
    the model. ``\\x{41}``, which only Rust reads, stays on the default engine.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_code_points.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_model_type,
            "--field-constraints",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = ""
    for name, payloads in CODE_POINT_PAYLOADS.items():
        with _generated_model(output_file, "rust_unsupported_code_points", name) as model:
            outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
    assert_output(outcomes, name="payloads")


def test_verbose_pattern_properties_select_the_python_engine_alike(output_file: Path) -> None:
    """Select ``python-re`` for a verbose backreference in the model and in the adapter of ``patternProperties``.

    The model and the schema validator once read the pattern differently, and the module failed at import.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_verbose_pattern_properties.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--generate-schema-validators",
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    with _generated_model(output_file, "rust_unsupported_verbose_pattern_properties", "Model") as model:
        assert_output(
            _outcomes(
                model,
                {
                    "declared and pattern property": {"code": "aa", "xz": "aa"},
                    "declared property": {"code": "ab"},
                    "pattern property": {"xz": "ab"},
                },
            ),
            name="payloads",
        )


def test_same_named_dataclasses_keep_their_own_config(output_dir: Path, tmp_path: Path) -> None:
    """Write ``regex_engine`` into the config of the dataclass that needs it, not of the ones named alike.

    ``--extra-template-data`` names one config for every ``Model`` of the run, so the two modules once shared it: the
    dataclass of ``b`` then wrote ``ConfigDict`` without importing it, and importing ``b`` raised ``NameError``.
    """
    extra_template_data = tmp_path / "extra-template-data.json"
    extra_template_data.write_text(json.dumps({"Model": {"config": {}}}), encoding="utf-8")
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "same_named_dataclass_configs",
        output_path=output_dir,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--extra-template-data",
            str(extra_template_data),
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )


@pytest.mark.parametrize("config", ["not a config", [], None, 1], ids=["string", "list", "null", "integer"])
def test_dataclass_regex_engine_ignores_a_config_that_is_no_mapping(
    output_file: Path, tmp_path: Path, config: object
) -> None:
    """Select Python's regex engine for a dataclass whose template-data ``config`` is no mapping, as for a model.

    A model drops such a config and writes ``regex_engine`` alone; the dataclass spread it into a dict and raised
    ``TypeError: ... is not a mapping`` (#5637).
    """
    extra_template_data = tmp_path / "extra-template-data.json"
    extra_template_data.write_text(json.dumps({"#all#": {"config": config}}), encoding="utf-8")
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_pattern.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--extra-template-data",
            str(extra_template_data),
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
