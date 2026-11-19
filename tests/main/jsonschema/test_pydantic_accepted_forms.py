"""Generated Pydantic models use only forms that Pydantic accepts: typed extras, regex engines and model configs."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import msgspec
import pytest
from pydantic import ValidationError

from tests.capabilities import needs
from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    _generated_package_module,
    _model_json_validator,
    run_main_and_assert,
)

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

SIZE_AND_DIALECT_PAYLOADS = {
    "WordsBeyondTheSizeLimit": ("user.name@example (x)", "a" * 256),
    "TextBeyondTheSizeLimit": ("any text", "two\nlines"),
    "BracketInClass": ("[A1", "a1"),
    "Letters": ("Déjà", "abc1"),
    "DigitsWithinTheSizeLimit": ("abc-123", "abc-"),
    "NotAngleBracketsBeyondTheSizeLimit": ("a and b", "a<b"),
    "AnythingBeyondTheSizeLimit": ("two\nlines", "x" * 20001),
    "NoSpacesBeyondTheSizeLimit": ("abc", "a b"),
    "AsciiWordsWithinTheSizeLimit": ("abc_1", "Déjà"),
    "OneLetterPropertyDifference": ("a-b", "a_b"),
    "HyphenAfterRange": ("ab-1 2", "a_b"),
    "NegatedHyphenFirst": ("!?", "a-"),
    "DifferenceAfterRange": ("abc", "a1"),
    "HexEscapeRange": ("ab-1_", "a b"),
    "BracedEscapeRange": ("ab-1_", "a b"),
    "OctalEscapeRange": ("09-a_", "a b"),
    "AtLeastTwoLetters": ("ab", "a"),
    "AtLeastWordsBeyondTheSizeLimit": ("a" * 300, "a" * 299),
    "LettersBeyondTheSizeLimit": ("bbb", "bc"),
    "WordWithinBoundaries": ("abc", "a b"),
}

SIZE_LIMIT_PAYLOADS = {
    "repetitions": (
        "UserArn",
        {
            "user": "arn:aws:iam::123456789012:user/alice",
            "role session": "arn:aws:sts::123456789012:assumed-role/admin/alice@example.com",
            "group": "arn:aws:iam::123456789012:group/admins",
        },
    ),
    "negated_property": ("Description", {"text": "Déjà vu", "empty": ""}),
    "property_class": (
        "TranslatedTextString",
        {"text": "Déjà vu", "20,001 characters": "x" * 20001},
    ),
    "word_and_digit_class": ("ResourceName", {"name": "my_resource-01", "slash": "a/b"}),
    "word_and_space_class": ("Title", {"title": "Déjà vu - Part 2", "slash": "a/b"}),
    "decimal_number": ("Digits", {"digits": "١٢٣", "letters": "12a"}),
    "letters": ("Name", {"letters": "Déjà", "digit": "abc1"}),
    "category_union": ("TagValue", {"tag": "Déjà_vu-2", "punctuation": "!!!"}),
    "any_character": (
        "Comment",
        {"text": "any text", "10,241 characters": "x" * 10241, "two lines": "a\nb"},
    ),
    "negated_characters": ("Text", {"text": "a and b", "angle bracket": "a<b", "empty": ""}),
    "negated_newline": ("Line", {"text": "any text", "two lines": "a\nb"}),
    "alternation": ("Principal", {"user": "user-alice", "role": "role-admin"}),
    "counted_group": ("Address", {"address": "first.last@example.com", "no at": "example.com"}),
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


@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "pydantic_v2.dataclass", "msgspec.Struct"])
def test_patterns_no_engine_compiles_as_written(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str
) -> None:
    r"""Write each pattern so that an engine of the output compiles it, or leave it out with a warning (#6593).

    A repetition past Rust's compiled-size limit selects ``python-re``; a ``[`` inside a class that only Rust leaves
    open is escaped, which every engine reads alike. A pattern no engine of the output compiles, a Java property, an
    escaped brace after ``\\p`` or a class escape or property as the start of a range (``[\\w- ]``, ``[\\p{L}-_]``
    and ``[\\pL-_]``, #6918) or as its end (``[a-\\w]``, ``[a-\\pL]`` and ``[a-\\p{L}]``, #6922), and for msgspec, which
    only runs Python's ``re``, ``\\p{L}`` and a group that turns Unicode off, is left out with a warning; one both
    engines read stays as written, and so does one only Rust reads where Rust is the engine (``[\\pL\\pP--_]`` and
    ``[a-z0-9--\\p{N}]``, differences). A hyphen after a range or at the start of a class makes no range with the class
    escape after it (``[a-z0-9-\\s]``, ``[^-\\w]``), and so does an escape of one character that ends a range
    (``[a-\\x7a-\\w]``, ``[a-\\x{7a}-\\w]`` for Rust and ``[\\60-\\71-\\w]`` for Python's ``re``). Each valid payload
    keeps its value and each invalid one fails.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "rust_unsupported_sizes_and_dialects.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(
        f"{warning.message}\n" for warning in recwarn if "no regex engine of the output" in str(warning.message)
    )
    if output_model_type != "msgspec.Struct":
        for name, payloads in SIZE_AND_DIALECT_PAYLOADS.items():
            with _generated_model(output_file, "rust_unsupported_sizes_and_dialects", name) as model:
                validate = _model_json_validator(model)
                outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
                value = validate(json.dumps(payloads[0]))
                outcomes += f"{name} value: {getattr(value, 'root', value) == payloads[0]}\n"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize("document", SIZE_LIMIT_PAYLOADS)
def test_size_limit_of_repetitions_together_and_of_unicode_classes(
    output_file: Path, recwarn: pytest.WarningsRecorder, document: str
) -> None:
    r"""Write a pattern Rust refuses for several repetitions together or a class of a general category (#6877, #6908).

    AWS documents write them: four counted classes of ``\\w`` that pass Rust's compiled-size limit only together
    select ``python-re``; a counted negated class of ``\\p{C}`` and a counted class of ``\\P{M}`` and ``\\p{M}``,
    which Python's ``re`` rejects too, are left out with a warning, and the model checks the other keywords only
    (``maxLength``). A class of ``\\w`` and ``\\d`` counts as ``\\w``, so does one of ``\\w`` and ``\\s``, and
    ``\\p{Nd}`` counts as ``\\d``. The ASCII ranges and optional copies of a repetition count too (``\\p{L}``, ``.``,
    ``[^<>]``), and so do a union of general categories, the single copies of a counted group and the branches of an
    alternation that each repeat a class. Each module once failed at import with ``Compiled regex exceeds size limit``.
    """
    name, payloads = SIZE_LIMIT_PAYLOADS[document]
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"pattern_size_{document}.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(
        f"{warning.message}\n" for warning in recwarn if "no regex engine of the output" in str(warning.message)
    )
    with _generated_model(output_file, f"pattern_size_{document}", name) as model:
        outcomes += _outcomes(model, payloads)
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"], id="field_constraints"),
        pytest.param(
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated", "--collapse-root-models"],
            id="base_model_collapsed",
        ),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--use-annotated"], id="dataclass_alias"),
    ],
)
def test_patterns_the_python_engine_rejects_beside_one_it_needs(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Leave out with a warning a pattern Python's ``re`` rejects where its config selects ``python-re`` (#6593).

    A model selects ``python-re`` for a lookaround, so every other pattern its config builds runs on that engine:
    ``\\P{C}`` and a class of Unicode properties, which only the default engine reads, are left out of its fields,
    and of a type alias it builds, while a root model with a config of its own keeps its pattern. AWS documents, under
    the dated presets, wrote such models. The module imports, each valid payload keeps its value and the lookaround
    still rejects what it rejects.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_beside_unicode_properties.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*extra_args, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "valid": {
            "dateFieldFormat": "yyyy-MM-dd",
            "indexFieldName": "Name",
            "description": "Déjà vu",
            "tagKeys": ["k"],
        },
        "space before the date format": {"dateFieldFormat": " yyyy"},
        "control character in the name": {"indexFieldName": "a\u0007"},
    }
    with _generated_model(output_file, "python_engine_beside_unicode_properties", "FieldMapping") as model:
        outcomes += _outcomes(model, payloads)
        value = _model_json_validator(model)(json.dumps(payloads["valid"]))
        outcomes += f"value: {value.description!r} {[getattr(key, 'root', key) for key in value.tagKeys]!r}\n"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "output_model_type",
    [pytest.param("pydantic_v2.BaseModel", id="base_model"), pytest.param("pydantic_v2.dataclass", id="dataclass")],
)
def test_unicode_property_beside_a_lookahead_is_left_out_of_constr(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str
) -> None:
    r"""Leave out with a warning a ``\\p{L}`` that a ``constr`` writes where the model selects ``python-re`` (#6941).

    With the default options the fields write their patterns in ``constr`` calls; a lookahead selects ``python-re``
    for the model, and Python's ``re`` rejects ``\\p{L}``, so the module once failed at import. It imports, the
    lookahead still rejects what it rejects and the name is not checked.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_beside_unicode_property.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "valid": {"password": "Abcdefgh", "name": "D\u00e9j\u00e0"},
        "password without a capital": {"password": "abcdefgh"},
        "any name": {"name": "42"},
    }
    with _generated_model(output_file, "python_engine_beside_unicode_property", "Model") as model:
        outcomes += _outcomes(model, payloads)
        outcomes += f"value: {_model_json_validator(model)(json.dumps(payloads['valid'])).name!r}\n"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel"], id="constr"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"], id="annotated"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias"], id="type_alias"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--strict-types", "str"], id="strict_str"),
        pytest.param(
            [
                "--output-model-type",
                "pydantic_v2.BaseModel",
                "--field-constraints",
                "--use-encoded-str-for-constrained-base64",
            ],
            id="encoded_str",
        ),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass"], id="dataclass"),
        pytest.param(
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-type-alias", "--target-pydantic-version", "2"],
            id="type_alias_string_target",
        ),
        pytest.param(["--preset", "standard-py311-20260826"], id="preset"),
    ],
)
def test_patterns_python_re_rejects_in_every_form_of_a_type(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Leave out with a warning each pattern Python's ``re`` rejects in a type of a ``python-re`` model (#6941).

    A lookahead selects ``python-re`` for the model, and ``\\p{L}`` is written in the types of its fields: a
    ``constr`` or a ``StringConstraints`` of an item, a dict value, a dict key and a union member, the body of a type
    alias the model builds, a strict string, and the constraints in front of decoding base64 text. Each module once
    failed at import. It imports, the other constraints of those types stay (``maxLength``) and the lookahead still
    rejects what it rejects.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_beside_rejected_pattern_forms.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*extra_args, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "valid": {
            "password": "Abcdefgh",
            "name": "Zo\u00eb",
            "nicknames": ["Jo"],
            "labels": {"a": "B\u00e9"},
            "scores": {"\u00e9t\u00e9": 1},
            "handle": "Zo\u00eb",
        },
        "password without a capital": {"password": "abcdefgh"},
        "handle longer than 8": {"handle": "abcdefghij"},
        "names are not checked": {"name": "1", "nicknames": ["1"], "labels": {"a": "1"}, "scores": {"1": 1}},
    }
    with _generated_model(output_file, "python_engine_beside_rejected_pattern_forms", "Account") as model:
        outcomes += _outcomes(model, payloads)
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"], id="field_constraints"),
        pytest.param(
            ["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated", "--collapse-root-models"],
            id="base_model_collapsed",
        ),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--use-annotated"], id="dataclass_alias"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel"], id="base_model"),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass"], id="dataclass"),
    ],
)
def test_named_group_beside_a_lookaround_is_left_out(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Leave out with a warning a named group of ECMA-262 where the model selects ``python-re`` (#7088).

    A lookahead selects ``python-re`` for the model, and Python's ``re`` rejects ``(?<year>...)`` on every Python,
    which writes it ``(?P<year>...)``; the module once failed at import, with the default options also where the
    field writes the pattern in a ``constr`` (#6941). It imports, the lookahead still rejects what it rejects and the
    date is not checked.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_beside_named_group.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*extra_args, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {"valid": {"tag": "v1", "date": "2026-11"}, "draft tag": {"tag": "draft-1"}, "any date": {"date": "x"}}
    with _generated_model(output_file, "python_engine_beside_named_group", "Release") as model:
        outcomes += _outcomes(model, payloads)
        outcomes += f"value: {_model_json_validator(model)(json.dumps(payloads['valid'])).date!r}\n"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "output_model_type",
    [pytest.param("pydantic_v2.BaseModel", id="base_model"), pytest.param("pydantic_v2.dataclass", id="dataclass")],
)
@pytest.mark.parametrize(
    ("document", "model_name", "payloads"),
    [
        pytest.param(
            "python_engine_beside_rejected_root_union",
            "Token",
            {"valid": "Abcdefgh", "letters": "abc", "digits": "123"},
            id="root_union",
        ),
        pytest.param(
            "python_engine_beside_rejected_in_a_base",
            "Child",
            {"valid": {"name": "Zo\u00eb", "tags": ["a"], "pw": "Abcdefgh"}, "digits": {"name": "1", "tags": ["1"]}},
            id="base",
        ),
    ],
)
def test_patterns_python_re_rejects_in_a_root_union_and_in_a_base(
    output_file: Path,
    recwarn: pytest.WarningsRecorder,
    output_model_type: str,
    document: str,
    model_name: str,
    payloads: Mapping[str, object],
) -> None:
    r"""Leave out ``\p{L}`` of a root union and of a base whose child selects ``python-re`` (#6941).

    The module once failed at import and now imports; the stored outcomes record each payload's verdict per output.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / f"{document}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    with _generated_model(output_file, document, model_name) as model:
        outcomes += _outcomes(model, payloads)
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="constr"),
        pytest.param(
            ["--use-type-alias", "--target-python-version", "3.12"],
            id="type_alias",
            marks=needs("type-statements"),
        ),
    ],
)
def test_patterns_python_re_rejects_in_a_base_and_an_alias_of_another_module(
    output_dir: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Leave out ``\p{L}`` of a base and a type alias that a ``python-re`` model of another module builds (#6941).

    Modular output follows the rule ``Field()`` has followed since 0.92.0 (#6593): ``b.Child`` inherits ``a.Base`` and
    ``b.User`` reaches ``a.Name``, both with a lookahead that selects ``python-re``, so ``a`` writes the shared
    annotations without the pattern Python's ``re`` rejects (``maxLength`` stays). Both modules import; a module that
    imported before validates less, the owner's decision of 2026-11-18.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "python_engine_beside_rejected_in_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "module a": (
            "a",
            "Base",
            {"valid": {"handle": "abc"}, "digits": {"handle": "12"}, "too long": {"handle": "abcdefghij"}},
        ),
        "module b child": (
            "b",
            "Child",
            {"valid": {"handle": "abc", "password": "Abcdefgh"}, "no capital": {"password": "abcdefgh"}},
        ),
        "module b user": (
            "b",
            "User",
            {"valid": {"password": "Abcdefgh", "label": "Zo\u00eb"}, "no capital": {"password": "abcdefgh"}},
        ),
    }
    for label, (module, model_name, cases) in payloads.items():
        with _generated_package_module(output_dir, module) as generated:
            outcomes += f"{label}\n{_outcomes(getattr(generated, model_name), cases)}"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--field-constraints", "--collapse-root-models"], id="field_constraints"),
        pytest.param(["--preset", "practical-py311-20260826"], id="preset"),
    ],
)
def test_a_pattern_python_accepts_keeps_its_engine_beside_a_union(output_file: Path, extra_args: list[str]) -> None:
    r"""Keep ``python-re`` and the backreference ``(a)\1`` that Python accepts: nothing is left out of such a model."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_backreference_beside_union.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    with _generated_model(output_file, "python_engine_backreference_beside_union", "Model") as model:
        outcomes = _outcomes(model, {"aa": {"r": "aa"}, "ab": {"r": "ab"}})
    assert_output(outcomes, name="outcomes")


def _pattern_lines(
    output_file: Path, recwarn: pytest.WarningsRecorder, name: str, title: str, extra_args: list[str]
) -> str:
    """Generate the schema ``name``; return the engine warnings and, per property, what ``re`` and the model do."""
    input_path = JSON_SCHEMA_DATA_PATH / f"{name}.json"
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*extra_args, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    with _generated_model(output_file, name, title) as model:
        schema = (
            model.model_json_schema()
            if hasattr(model, "model_json_schema")
            else msgspec.json.schema(model)["$defs"][title]
        )
    lines = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    for key, keywords in json.loads(input_path.read_text(encoding="utf-8"))["properties"].items():
        try:
            re.compile(keywords["pattern"])
        except re.error:
            compiled = "rejects"
        else:
            compiled = "compiles"
        checked = "checks it" if "pattern" in schema["properties"][key] else "leaves it out"
        lines += f"{key} {keywords['pattern']!r}: Python's re {compiled} it, the model {checked}\n"
    return lines


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "msgspec.Struct"], id="msgspec"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"], id="python_re"),
    ],
)
def test_patterns_python_re_rejects_on_every_python(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Check each pattern on Python's ``re`` exactly where every Python from 3.10 on compiles it (#7088).

    msgspec runs only Python's ``re``, and the lookbehinds select ``python-re`` for the Pydantic model. ECMA-262 syntax
    that Python's ``re`` rejects is left out: a named group, a class that the first ``]`` leaves open (``[]``,
    ``[^]``), a reference to a group not closed before it or defined in its lookbehind, a lookbehind whose width
    varies, and so is a ``)`` that closes no group. A fixed lookbehind, a backward reference, Python's own named group,
    a ``]`` that starts a class, group text inside a class or after an escape, a lookahead, a comment, flags and a
    conditional group are kept. Each line holds what the running Python compiles and what the model checks, after the
    warnings.
    """
    assert_output(
        _pattern_lines(output_file, recwarn, "python_re_rejected_syntax", "Syntax", extra_args), name="patterns"
    )


def test_encoded_str_roots_build_their_pattern_with_the_python_engine(output_file: Path) -> None:
    """Write a base64 root model whose pattern needs ``python-re`` with only the decoded type in its base (#6603).

    Under ``--use-encoded-str-for-constrained-base64`` the base ``RootModel[...]`` once wrote the pattern, which
    Pydantic built with the default engine before the class's config selected ``python-re``, and the module failed at
    import. Each valid payload keeps its decoded value and each invalid one fails.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "encoded_str_python_regex_engine.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            *("--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"),
            *("--use-encoded-str-for-constrained-base64", "--formatters", "builtin", "--disable-timestamp"),
        ],
        force_exec_validation=True,
    )
    outcomes = ""
    for name, payloads in {"LookaroundBlob": ("QUJDRA==", "-QUJD"), "LongBlob": ("QUJDRA==", "")}.items():
        with _generated_model(output_file, "encoded_str_python_regex_engine", name) as model:
            outcomes += _outcomes(model, {f"{name} {payload!r}": payload for payload in payloads})
            outcomes += f"{name} value: {_model_json_validator(model)(json.dumps(payloads[0])).root!r}\n"
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


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(["--output-model-type", "msgspec.Struct"], id="msgspec"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel"], id="base_model"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--field-constraints"], id="field_constraints"),
    ],
)
def test_escapes_the_rust_engine_rejects_are_counted(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Leave out or move to ``python-re`` a pattern with an escape pydantic-core's engine rejects (#7094).

    The engine rejects the letters ``\c``, ``\e``, ``\k`` and the others it reads after a backslash nowhere, in and out
    of a class, and a non-ASCII character there; the module failed at import. A named group with ``\k<q>`` and a
    control letter are left out, as Python's ``re`` rejects them too; ``\é`` is read by Python's ``re`` alone, which
    the model selects. A plain pattern is kept.
    """
    assert_output(_pattern_lines(output_file, recwarn, "rust_rejected_escapes", "Escapes", extra_args), name="patterns")
