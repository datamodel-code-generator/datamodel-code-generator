"""End-to-end coverage for the string keywords beside a format under --keep-string-keywords-beside-formats."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

OPTION = "--keep-string-keywords-beside-formats"
COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp", "--target-python-version", "3.10")
SCHEMA = JSON_SCHEMA_DATA_PATH / "format_string_keywords.json"
ALL_OF_SCHEMA = JSON_SCHEMA_DATA_PATH / "format_string_keywords_all_of.json"
BASE_MODEL = ("--output-model-type", "pydantic_v2.BaseModel")
MSGSPEC = ("--output-model-type", "msgspec.Struct")
VALID = {
    "email": "a@b.io",
    "avatar": "YQ==",
    "home": "https://a.io",
    "emails": ["a@b.io"],
    "link": "https://a.io",
    "plainLink": "https://a.io/a/very/long/path/of/a/link",
    "plainEmail": "a-long-address@example.org",
}
INVALID: tuple[tuple[str, object], ...] = (
    ("email", "abcdefgh@example.org"),
    ("email", "a@b.org"),
    ("avatar", "YWJjZGVmZ2g="),
    ("home", "http://a.io"),
    ("home", "https://example.org/a/very/long/path"),
    ("emails", ["abcdefgh@example.org"]),
    ("link", "https://example.org/a/very/long/path"),
)
"""Values the schema rejects for ``Contact``: each keyword limits the text its format names."""


def _verdicts(model: type[pydantic.BaseModel]) -> str:
    """Return the verdict on the valid document, which the model must accept, and on each invalid value."""
    model.model_validate(VALID)
    lines = ["valid: accepted\n"]
    for name, value in INVALID:
        try:
            model.model_validate({**VALID, name: value})
        except pydantic.ValidationError:
            verdict = "rejected"
        else:
            verdict = "accepted"
        lines.append(f"invalid {name}={value!r}: {verdict}\n")
    return "".join(lines)


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Keep the string keywords beside a format Pydantic writes as a class `constr` cannot constrain.

`format: email` and `byte` are written as `EmailStr` and `Base64Str`, and `format: uri` as
`AnyUrl`, none of which `constr` constrains, so a `maxLength` or `pattern` beside them is left out of the model,
silently or with a warning, as in 0.100.0. With the option an email takes the keywords it takes in
`Annotated[EmailStr, Field(...)]`, a `byte` is written in front of its base64 decoding (`EncodedStr`, Pydantic 2.10
or later), so the keywords check the base64 text in the field forms too, and a `uri` with a keyword is
the plain string the keyword checks, as `AnyUrl` takes no `pattern` and checks a length on the normalised URL. A
`uri` without a keyword stays `AnyUrl`. An output that writes a date or UUID format as `str`, as msgspec does, keeps the
keywords an `allOf` member adds beside a `$ref` to it.""",
    input_schema="jsonschema/format_string_keywords.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/format_string_keywords/keep_string_keywords_beside_formats[kept].py",
    comparison_output="main/jsonschema/format_string_keywords/keep_string_keywords_beside_formats[released].py",
)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(BASE_MODEL, id="released"),
        pytest.param((*BASE_MODEL, OPTION), id="kept"),
        pytest.param((*BASE_MODEL, OPTION, "--field-constraints"), id="kept-field-constraints"),
        pytest.param((*BASE_MODEL, OPTION, "--use-annotated"), id="kept-annotated"),
        pytest.param(
            (*BASE_MODEL, OPTION, "--use-encoded-str-for-constrained-base64", "--field-constraints"), id="kept-encoded"
        ),
        pytest.param((*BASE_MODEL, OPTION, "--strict-types", "str"), id="kept-strict-str"),
        pytest.param(("--output-model-type", "pydantic_v2.dataclass", OPTION), id="kept-pydantic-dataclass"),
        pytest.param(("--output-model-type", "dataclasses.dataclass", OPTION), id="kept-dataclass"),
        pytest.param((*MSGSPEC, OPTION), id="kept-msgspec"),
    ],
)
def test_keep_string_keywords_beside_formats(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Keep the string keywords beside the formats of Pydantic classes, opt-in (#7198, #7345).

    Without the option the email, base64 and URL fields are written without their ``maxLength`` and
    ``pattern``. With it each keyword limits the text of its format in a field, an array item and a union member, in
    the default, ``--field-constraints`` and ``--use-annotated`` forms, and the models reject the invalid values.
    """
    run_main_and_assert(
        input_path=SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *extra_args],
        force_exec_validation=True,
    )
    if "pydantic_v2.BaseModel" in extra_args:
        with _generated_model(output_file, "format_string_keywords", "Contact") as model:
            assert_output(_verdicts(model), name="payloads")


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((*MSGSPEC,), id="released-msgspec"),
        pytest.param((*MSGSPEC, OPTION), id="kept-msgspec"),
        pytest.param((*MSGSPEC, OPTION, "--use-standard-primitive-types"), id="kept-msgspec-standard"),
        pytest.param((*BASE_MODEL, OPTION), id="kept-pydantic"),
    ],
)
def test_keep_string_keywords_beside_formats_all_of(output_file: Path, extra_args: tuple[str, ...]) -> None:
    """Keep the keywords an allOf member adds beside a `$ref` to a string that msgspec writes as ``str`` (#7181).

    A date or UUID format of the definition decided its keywords before its type did, so the lengths and the pattern
    were left out of the ``str``. With the option the type decides: msgspec keeps them, and Pydantic, whose
    ``date`` and ``UUID`` take none, writes the format type as before.
    """
    run_main_and_assert(
        input_path=ALL_OF_SCHEMA,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[*COMMON_ARGS, *extra_args],
        force_exec_validation=True,
    )
