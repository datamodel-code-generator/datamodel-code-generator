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
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--extra-fields", "allow"], id="extra_allow"),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--extra-fields", "forbid"], id="extra_forbid"),
        pytest.param(
            ["--output-model-type", "pydantic_v2.dataclass", "--allow-population-by-field-name"],
            id="population_by_name",
        ),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass"], id="dataclass"),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--frozen-dataclasses"], id="frozen"),
        pytest.param(["--output-model-type", "pydantic_v2.dataclass", "--field-constraints"], id="field_constraints"),
        pytest.param(["--output-model-type", "pydantic_v2.BaseModel", "--extra-fields", "allow"], id="base_model"),
    ],
)
def test_inherited_lookaround_selects_the_engine_of_the_subclass_config(
    output_file: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Select ``python-re`` in a dataclass whose config builds a lookahead of a base without it (#7452).

    Pydantic builds the fields of every base again with the config a dataclass applies: its own, else its first
    base's. ``Child``, ``Middle`` and ``Grandchild`` with a config of their own, ``Mixed``, which takes the empty
    config of ``Labelled``, and the dataclasses built on it once failed at import. ``Mixed`` and ``Wide`` redeclare the
    inherited ``\p{L}`` and ``\p{Lu}`` fields without the pattern, ``Tagged`` inherits ``Mixed``'s, and ``Labelled``
    and ``Titled`` keep theirs; ``Stamped`` builds ``Marked``, which ``Other`` already builds with ``python-re``.
    ``Sealed`` took the ``extra='forbid'`` of ``Closed`` and keeps it in the config it gets. A dataclass that takes the
    config of ``Base`` and every BaseModel, which merges the config of its bases, keep their output. The lookahead
    rejects what it rejects in each.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "inherited_lookaround_dataclass_config.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*extra_args, "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    payloads = {
        "Child": {"valid": {"code": "abc", "serial": "12"}, "lookahead": {"code": "tmpx"}, "serial": {"serial": "x"}},
        "Grandchild": {"valid": {"code": "abc", "level": 1}, "lookahead": {"code": "tmpx"}},
        "Mixed": {"valid": {"label": "l", "code": "abc"}, "lookahead": {"code": "tmpx"}, "digit label": {"label": "1"}},
        "Labelled": {"valid": {"label": "Zo\u00eb"}, "digit label": {"label": "1"}},
        "Tagged": {"valid": {"label": "l", "tag": "t"}, "lookahead": {"code": "tmpx"}, "digit label": {"label": "1"}},
        "Wide": {"valid": {"title": "T", "code": "abc"}, "lookahead": {"code": "tmpx"}, "lower title": {"title": "t"}},
        "Titled": {"valid": {"title": "T"}, "lower title": {"title": "t"}},
        "Stamped": {"valid": {"mark": "m", "code": "abc"}, "lookahead": {"code": "tmpx"}},
        "Sealed": {"valid": {"name": "n", "code": "abc"}, "lookahead": {"code": "tmpx"}, "extra": {"other": "o"}},
    }
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    for model_name, cases in payloads.items():
        with _generated_model(output_file, "inherited_lookaround_dataclass_config", model_name) as model:
            outcomes += f"{model_name}\n{_outcomes(model, cases)}"
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
        pytest.param(["--extra-fields", "allow"], id="extra_allow"),
    ],
)
def test_inherited_unicode_property_is_redeclared_in_the_module_of_its_base(
    output_dir: Path, recwarn: pytest.WarningsRecorder, extra_args: list[str]
) -> None:
    r"""Redeclare a base's ``\p{...}`` field without it where the dataclass selects ``python-re`` for a base (#7452).

    ``d.Local`` and ``d.Wide`` build the lookahead of ``b.Coded`` from another module, so they select ``python-re`` and
    redeclare the fields of the bases of their module without the pattern Python's ``re`` rejects: ``owner``, whose
    type names ``d.Person``, and the required ``title`` that ``d.Titled`` writes keyword-only. The bases, which
    imported before, keep their output and reject what they rejected. ``e.Recoded`` redeclares the lookahead it
    inherits, so its config keeps its engine.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "inherited_lookaround_dataclass_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "Named": {"valid": {"name": "Zo\u00eb"}, "digits": {"name": "12"}},
        "Owned": {"valid": {"owner": "X"}, "lower owner": {"owner": "x"}},
        "Local": {"valid": {"name": "12", "owner": "x", "code": "ab"}, "lookahead": {"code": "xa"}},
        "Titled": {"valid": {"title": "T"}, "lower title": {"title": "t"}},
        "Wide": {"valid": {"title": "t", "code": "ab"}, "lookahead": {"title": "T", "code": "xa"}},
    }
    with _generated_package_module(output_dir, "d") as generated:
        for model_name, cases in payloads.items():
            outcomes += f"{model_name}\n{_outcomes(getattr(generated, model_name), cases)}"
    with _generated_package_module(output_dir, "e") as generated:
        outcomes += f"Recoded\n{_outcomes(generated.Recoded, {'valid': {'code': 'xa'}, 'digits': {'name': '12'}})}"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    ("output_model_type", "extra_args"),
    [
        pytest.param("pydantic_v2.dataclass", [], id="dataclass"),
        pytest.param("pydantic_v2.dataclass", ["--field-constraints"], id="field_constraints"),
        pytest.param("pydantic_v2.dataclass", ["--use-annotated"], id="use_annotated"),
        pytest.param("pydantic_v2.dataclass", ["--target-pydantic-version", "2.11"], id="target_2_11"),
        pytest.param("pydantic_v2.BaseModel", [], id="base_model"),
    ],
)
def test_inherited_alias_pattern_python_re_rejects_is_left_out_of_the_subclass(
    output_file: Path, recwarn: pytest.WarningsRecorder, output_model_type: str, extra_args: list[str]
) -> None:
    r"""Write a type alias whose ``\p{L}`` the subclass's ``python-re`` builds as its body without it (#7487).

    ``Both`` selects ``python-re`` for the lookahead of ``Coded``, and builds the aliases its own field and the fields
    of ``Named`` name with it, which failed at import. Its fields write the alias body without the pattern Python's
    ``re`` rejects, keeping the other constraints of the alias (``maxLength``, and ``minItems`` and ``maxItems`` beside
    a pattern of the items) and the members of its type (``Maybe`` stays a nullable ``str``), and warn; ``Named`` and
    the aliases keep their output and still reject what they rejected. A model merges the configs of its bases and
    marks every alias it builds, so it keeps its output.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "python_engine_beside_rejected_in_an_alias.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            output_model_type,
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    payloads = {
        "Named": {
            "valid": {"name": "Zo\u00eb", "maybe": None},
            "digits": {"name": "12"},
            "long": {"short": "abcdefghij"},
            "no items": {"bounded": []},
        },
        "Both": {
            "valid": {
                "name": "12",
                "names": ["12"],
                "nick": "12",
                "short": "ab",
                "bounded": ["12"],
                "maybe": None,
                "code": "ab",
            },
            "maybe": {"maybe": "12"},
            "short maybe": {"maybe": "a"},
            "long": {"short": "abcdefghij"},
            "no items": {"bounded": []},
            "four items": {"bounded": ["a", "b", "c", "d"]},
            "lookahead": {"code": "xa"},
        },
    }
    for model_name, cases in payloads.items():
        with _generated_model(output_file, "python_engine_beside_rejected_in_an_alias", model_name) as model:
            outcomes += f"{model_name}\n{_outcomes(model, cases)}"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize("taken_alias_generator", [False, True], ids=["plain", "alias_generator"])
def test_inherited_unicode_property_is_redeclared_in_another_module(
    output_dir: Path, tmp_path: Path, recwarn: pytest.WarningsRecorder, *, taken_alias_generator: bool
) -> None:
    r"""Redeclare the ``\p{L}`` fields of a base in another module where the subclass selects ``python-re`` (#7487).

    ``c.Both`` builds the lookahead of ``b.Coded``, so it redeclares the fields of ``a.Named`` without the pattern
    Python's ``re`` rejects, which failed at import: its copies name ``a.Color`` and a field ``date`` that hides the
    ``date`` its own field ``when`` names, so ``c`` imports both, under a name that no
    redeclared field (``date_aliased``) takes. ``c.Shut`` takes the config of ``a.Closed``; with an ``alias_generator``
    in it, ``c`` imports the generator too, which failed with ``NameError`` (#7548). ``a`` and ``b`` keep their output
    and reject what they rejected.
    """
    extra_args = []
    if taken_alias_generator:
        extra_template_data = tmp_path / "extra-template-data.json"
        config = {"config": {"alias_generator": "to_camel"}}
        extra_template_data.write_text(
            json.dumps({"a.Closed": config, "#/components/schemas/a.Closed": config}), encoding="utf-8"
        )
        extra_args = ["--extra-template-data", str(extra_template_data)]
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "python_engine_beside_rejected_in_other_modules.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            *extra_args,
            "--formatters",
            "builtin",
            "--disable-timestamp",
        ],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    named = {"valid": {"name": "Zo\u00eb"}, "digits": {"name": "12"}}
    with _generated_package_module(output_dir, "a") as generated:
        outcomes += f"Named\n{_outcomes(generated.Named, named)}"
    user_name = "userName" if taken_alias_generator else "user_name"
    payloads = {
        "Both": {
            "valid": {
                "name": "12",
                "tint": "12",
                "nickname": "12",
                "date": "12",
                "date_aliased": "12",
                "when": "2020-01-01",
                "code": "ab",
            },
            "color": {"tint": "red"},
            "lookahead": {"code": "xa"},
        },
        "Shut": {"valid": {user_name: "u", "code": "ab"}, "extra": {"other": 1}},
    }
    with _generated_package_module(output_dir, "c") as generated:
        for model_name, cases in payloads.items():
            outcomes += f"{model_name}\n{_outcomes(getattr(generated, model_name), cases)}"
    assert_output(outcomes, name="outcomes")


KEEP_INHERITED_DATACLASS_CONFIG = "--keep-inherited-dataclass-config"
INHERITED_DATACLASS_CONFIG = OPEN_API_DATA_PATH / "inherited_dataclass_config" / "schema.yaml"
INHERITED_DATACLASS_CONFIG_ENGINES = JSON_SCHEMA_DATA_PATH / "inherited_dataclass_config_engines.json"
EXTRA_KEY = {"other": 1}
SURROGATE_TAKER_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$defs": {
        "Marked": {"type": "object", "properties": {"mark": {"type": "string", "pattern": "^a\ud800b$"}}},
        "Tailed": {
            "allOf": [
                {"$ref": "#/$defs/Marked"},
                {"type": "object", "properties": {"tail": {"type": "string", "pattern": "^[a-z]+$"}}},
            ]
        },
    },
}
"""Written here, as a YAML parser rejects the escape of a lone surrogate."""


@pytest.mark.cli_doc(
    options=[KEEP_INHERITED_DATACLASS_CONFIG],
    option_description="""Keep the config items of the first base in a Pydantic dataclass with a config of its own.

A Pydantic dataclass with no config of its own applies the config of its first dataclass base, such as
`extra='forbid'` from `additionalProperties: false`. Once it writes a config of its own, for example
`populate_by_name=True` for `--allow-population-by-field-name` or `regex_engine="python-re"` for a lookaround in
its own patterns, Pydantic applies that config alone: without this option the dataclass accepts the extra keys its
base rejects and drops the `--extra-template-data` items of its base, as 0.103.0 writes it. With it the config of
the dataclass also writes the items of its base that it does not set itself, base items first, as a Pydantic model
merges the config of every base (`ConfigDict(extra='forbid', regex_engine="python-re")`); an item the dataclass sets
itself wins.

The kept items can also select the base's `python-re` regex engine for the patterns of the dataclass: there `$`
also accepts a trailing newline, and a pattern Python's `re` rejects, such as `\\p{L}`, is left out with a warning,
as for a dataclass that takes the config of its base. A base the dataclass builds with that engine can change too: a
second base leaves out a pattern Python's `re` rejects, and a base with a lone surrogate in a pattern keeps the default
engine. A configured base class from `--base-class` that the
generator does not read gives no items. Pydantic model output is the same either way.""",
    input_schema="openapi/inherited_dataclass_config/schema.yaml",
    cli_args=["--output-model-type", "pydantic_v2.dataclass", KEEP_INHERITED_DATACLASS_CONFIG],
    golden_output="main/jsonschema/pydantic_accepted_forms/dataclass_config_keeps_the_items_of_its_first_base[engine].option/",
    comparison_output="main/jsonschema/pydantic_accepted_forms/dataclass_config_keeps_the_items_of_its_first_base[engine].default/",
    related_options=["--output-model-type", "--allow-population-by-field-name", "--extra-template-data"],
)
@pytest.mark.parametrize(
    ("schema", "extra_args", "template_data", "payloads", "values"),
    [
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            [],
            {},
            {
                "a": {
                    "Code": {"valid": {"id": 1, "code": "ab"}, "extra": EXTRA_KEY, "lookahead": {"code": "xa"}},
                    "Plain": {"valid": {"code": "ab"}, "extra": EXTRA_KEY},
                    "Tagged": {"valid": {"tag": "ab"}, "extra": EXTRA_KEY},
                    "Guard": {"valid": {"pin": "1", "code": "ab"}, "extra": EXTRA_KEY},
                    "Mid": {"valid": {"note": "n"}, "extra": EXTRA_KEY},
                    "Deep": {"valid": {"note": "n", "code": "ab"}, "extra": EXTRA_KEY},
                    "Leaf": {"valid": {"code": "ab", "tag": "ab"}, "extra": EXTRA_KEY, "lookahead": {"tag": "ya"}},
                    "Two": {"valid": {"two": "ab"}, "extra": EXTRA_KEY},
                },
                "b": {
                    "Remote": {"valid": {"code": "ab"}, "extra": EXTRA_KEY},
                    "Worded": {"valid": {"word": "ab"}, "extra": EXTRA_KEY, "lookahead": {"word": "xa"}},
                },
            },
            {},
            id="engine",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            ["--allow-population-by-field-name"],
            {},
            {
                "a": {"Mid": {"valid": {"note": "n"}, "extra": EXTRA_KEY}, "Code": {"extra": EXTRA_KEY}},
                "b": {"Remote": {"valid": {"code": "ab"}, "extra": EXTRA_KEY}},
            },
            {},
            id="population",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            [],
            {"a.Code": {"extra": "'allow'"}},
            {"a": {"Code": {"extra": EXTRA_KEY}, "Leaf": {"extra": EXTRA_KEY}}},
            {},
            id="own-wins",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            ["--extra-fields", "allow"],
            {},
            {"a": {"Code": {"extra": EXTRA_KEY}}, "b": {"Remote": {"extra": EXTRA_KEY}}},
            {},
            id="extra-fields",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            [],
            {"a.Closed": {"alias_generator": "to_camel"}},
            {"b": {"Remote": {"camel": {"userName": "u", "code": "ab"}, "extra": EXTRA_KEY}}},
            {"b": {"Remote": ({"userName": "u", "code": "ab"}, "user_name")}},
            id="alias-generator",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG,
            ["--extra-fields", "forbid", "--use-generic-base-class"],
            {},
            {"a": {"Code": {"extra": EXTRA_KEY}, "Mid": {"extra": EXTRA_KEY}}},
            {},
            id="generic-base",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG_ENGINES,
            ["--allow-population-by-field-name"],
            {},
            {
                "model": {
                    "Named": {
                        "valid": {"name": "Zo\u00eb", "tail": "ab"},
                        "digits": {"name": "12"},
                        "newline": {"tail": "ab\n"},
                    },
                    "Lettered": {"valid": {"letters": "Zo\u00eb"}, "digits": {"letters": "12"}},
                    "Both": {"valid": {"count": 1, "letters": "Zo\u00eb"}, "extra": EXTRA_KEY},
                }
            },
            {},
            id="engines",
        ),
        pytest.param(
            INHERITED_DATACLASS_CONFIG_ENGINES,
            ["--base-class", "mypkg.Base"],
            {},
            {},
            {},
            id="opaque-base",
        ),
        pytest.param(
            SURROGATE_TAKER_SCHEMA,
            ["--allow-population-by-field-name"],
            {},
            {
                "model": {
                    "Marked": {"replacements": {"mark": "a\ufffd\ufffd\ufffdb"}},
                    "Tailed": {"newline": {"tail": "ab\n"}},
                }
            },
            {},
            id="surrogate-taker",
        ),
        pytest.param(
            SURROGATE_TAKER_SCHEMA,
            ["--allow-population-by-field-name"],
            {"Tailed": {"regex_engine": '"python-re"'}},
            {
                "model": {
                    "Marked": {"replacements": {"mark": "a\ufffd\ufffd\ufffdb"}},
                    "Tailed": {"newline": {"tail": "ab\n"}},
                }
            },
            {},
            id="surrogate-taker-names-engine",
        ),
    ],
)
def test_dataclass_config_keeps_the_items_of_its_first_base(
    tmp_path: Path,
    recwarn: pytest.WarningsRecorder,
    monkeypatch: pytest.MonkeyPatch,
    schema: Path | dict[str, object],
    extra_args: list[str],
    template_data: dict[str, dict[str, str]],
    payloads: dict[str, dict[str, dict[str, object]]],
    values: dict[str, dict[str, tuple[dict[str, object], str]]],
) -> None:
    r"""Keep the config items of the first dataclass base with ``--keep-inherited-dataclass-config`` (#7497, #7560).

    Each case generates without the option (``default``) and with it (``option``). ``engine``: each subclass of
    ``a.Closed`` and ``a.Open`` selects ``python-re`` for its own lookaround or the type alias ``b.Word`` and writes
    a config of its own, which drops ``extra`` of its base; with the option ``a.Deep`` keeps the one ``a.Mid`` takes
    from ``a.Closed``, ``a.Leaf`` the one of ``a.Code``, ``a.Two`` that of ``a.Open``, its first base, and ``b.Remote``
    and ``b.Worded`` that of another module. ``population``: ``a.Mid`` has a config of its own only for
    ``--allow-population-by-field-name`` (#7560). ``own-wins`` and ``extra-fields``: an item the subclass sets itself
    stays. ``alias-generator``: ``b.Remote`` keeps the ``alias_generator`` of ``a.Closed`` and imports it, so
    ``userName`` fills ``user_name``. ``generic-base``: the generic base class of a dataclass holds no config, so no
    subclass has one to keep. ``engines``: ``Named`` takes the ``python-re`` that ``Labelled`` selects for a model it
    reaches, so its own ``\p{L}`` is left out with a warning and ``$`` accepts a trailing newline; ``Both`` takes the
    engine of ``Sealed``, its first base, and builds the ``\p{L}`` of ``Lettered`` with it, so ``Lettered`` leaves
    it out too, as for a subclass with no config of its own. ``opaque-base``: a configured base class the generator
    does not read gives no item. ``surrogate-taker``: ``Tailed`` applies the config of ``Marked``, so its default
    engine pattern keeps ``Marked`` on the default engine, which reads the lone surrogate as U+FFFD, with a warning.
    ``surrogate-taker-names-engine``: a ``Tailed`` that names ``python-re`` itself keeps it whatever ``Marked`` selects,
    so it does not keep ``Marked`` on the default engine.
    """
    monkeypatch.delenv("DATAMODEL_CODE_GENERATOR_CHECK_BUILTIN_FORMATTER_PARITY", raising=False)
    if isinstance(schema, dict):
        input_path = tmp_path / "surrogate_taker.json"
        input_path.write_text(json.dumps(schema), encoding="utf-8")
    else:
        input_path = schema
    input_file_type = "openapi" if input_path.suffix == ".yaml" else "jsonschema"
    if template_data:
        template_data_path = tmp_path / "extra-template-data.json"
        configs = {name: {"config": config} for name, config in template_data.items()}
        template_data_path.write_text(
            json.dumps({**configs, **{f"#/components/schemas/{name}": config for name, config in configs.items()}}),
            encoding="utf-8",
        )
        extra_args = [*extra_args, "--extra-template-data", str(template_data_path)]
    outcomes = ""
    for name, option_args in (("default", []), ("option", [KEEP_INHERITED_DATACLASS_CONFIG])):
        package = tmp_path / f"{name}_models"
        recwarn.clear()
        run_main_and_assert(
            input_path=input_path,
            output_path=package if input_file_type == "openapi" else package / "model.py",
            input_file_type=input_file_type,
            extra_args=[
                "--output-model-type",
                "pydantic_v2.dataclass",
                *extra_args,
                *option_args,
                "--formatters",
                "builtin",
                "--disable-timestamp",
            ],
            force_exec_validation=bool(payloads),
            skip_code_validation=not payloads,
            name=name,
        )
        outcomes += f"{name}\n"
        outcomes += "".join(
            f"{message}\n"
            for message in dict.fromkeys(str(warning.message) for warning in recwarn)
            if "regex" in message
        )
        for module, models in payloads.items():
            with _generated_package_module(package, module) as generated:
                for model_name, cases in models.items():
                    outcomes += f"{model_name}\n{_outcomes(getattr(generated, model_name), cases)}"
                for model_name, (payload, attribute) in values.get(module, {}).items():
                    parsed = _model_json_validator(getattr(generated, model_name))(json.dumps(payload))
                    outcomes += f"{model_name}.{attribute}: {getattr(parsed, attribute)!r}\n"
    assert_output(outcomes, name="outcomes")


@pytest.mark.parametrize(
    ("document", "payloads"),
    [
        pytest.param(
            "python_engine_beside_rejected_alias_in_other_modules",
            {
                "valid": {
                    "name": "12",
                    "nick": "12",
                    "label": {"hex": "1"},
                    "labels": [{"hex": "2"}, "12"],
                    "by_key": {"k": {"hex": "3"}},
                    "either": {"hex": "4"},
                    "tag": {"code": 5},
                    "own": {"hex": "6"},
                    "mine": {"rgb": 7},
                    "short": "12",
                    "count": 8,
                    "code": "ab",
                },
                "long": {"short": "abcdefg"},
                "lookahead": {"code": "xa"},
            },
            id="other_modules",
        ),
        pytest.param(
            "python_engine_beside_rejected_alias_in_a_builtin_named_module",
            {
                "valid": {"name": "12", "nick": "12", "label": {"hex": "1"}, "own": {"count": 2}, "code": "ab"},
                "lookahead": {"code": "xa"},
            },
            id="builtin_named_module",
        ),
    ],
)
def test_inherited_alias_of_another_module_names_its_models_from_the_subclass(
    output_dir: Path, recwarn: pytest.WarningsRecorder, document: str, payloads: Mapping[str, object]
) -> None:
    r"""Import the models that a type alias of another module names where a subclass writes its body (#7487).

    ``c.Both`` builds the lookahead of ``b.Coded`` with ``python-re``, so its fields write the bodies of the aliases of
    ``a`` without their ``\p{L}``: in items, values and unions, in an inherited field and in its own. The bodies name
    ``a.Color``, ``d.Color`` and the alias ``a.Hex``, which ``c`` did not import (``NameError``), and ``c`` has a
    ``Color`` and a ``Hex`` of its own, which the bare names bound; ``c`` now imports ``a`` and ``d`` and names them
    from there, so ``Hex`` keeps its ``maxLength``. A module named ``str`` is
    imported under another name once the hints write the builtin, and the own field, whose plan is rebuilt then, takes
    that name too. The parsed value shows the class each member is validated as.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / f"{document}.yaml",
        output_path=output_dir,
        input_file_type="openapi",
        extra_args=["--output-model-type", "pydantic_v2.dataclass", "--formatters", "builtin", "--disable-timestamp"],
        force_exec_validation=True,
    )
    outcomes = "".join(f"{warning.message}\n" for warning in recwarn if "regex engine" in str(warning.message))
    with _generated_package_module(output_dir, "c") as generated:
        outcomes += _outcomes(generated.Both, payloads)
        outcomes += f"value: {_model_json_validator(generated.Both)(json.dumps(payloads['valid']))!r}\n"
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
