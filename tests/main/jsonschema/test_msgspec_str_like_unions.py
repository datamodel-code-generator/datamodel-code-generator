"""Write one str for the str-like members of a msgspec union that holds more than one of them."""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

import msgspec
import pytest
from jsonschema import Draft202012Validator, FormatChecker
from jsonschema import ValidationError as SchemaValidationError

from datamodel_code_generator.__main__ import main
from tests.conftest import assert_output
from tests.main.conftest import (
    DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_str_like_unions.json"
PAYLOADS = DATA_PATH / "payloads" / "msgspec_str_like_unions.json"
OPTION_SETS = {
    "released": (),
    "merge": ("--merge-msgspec-str-like-union-members",),
    "merge_collapse_root_models": ("--merge-msgspec-str-like-union-members", "--collapse-root-models"),
    "merge_enum_field_as_literal": ("--merge-msgspec-str-like-union-members", "--enum-field-as-literal", "all"),
}
ARGS = ("--output-model-type", "msgspec.Struct", "--formatters", "builtin", "--disable-timestamp")
ARGS += ("--use-standard-primitive-types",)


@pytest.mark.cli_doc(
    options=["--merge-msgspec-str-like-union-members"],
    option_description="""Write one str in place of the str-like members of a msgspec union that holds more than one.

msgspec decodes a union only if it holds at most one str-like member: `str` (with the str values of a `Literal`), an
Enum of str values, `bytes`, the dates and times, `UUID` and `Decimal`. A schema whose `anyOf` or `oneOf` holds two
of them, such as a `format: uuid` string beside a plain string, writes `UUID | str`, which msgspec refuses with
`TypeError` for every document, and the generator warns about it. With this option such a union is written with one
`str` in place of those members, also through the type aliases of one str-like type or of a union of only str-like
members, so it decodes every string they accept, as `str`, without their formats and constraints; a warning names the
members each union loses. A merged member that admits null leaves `None` in the union, and a merged `Literal` keeps
its values that are no str. A union whose str-like members include an Enum of str values, a member typed by
`x-python-type`, a member built for an `integer` or `number` schema (a number of `format: decimal`, `time-delta` or
`date-time`), whose JSON numbers one `str` would refuse, an alias of a union that mixes str-like and other members or
an alias of a `Literal` of str and other values is left as it is, with its warning.""",
    input_schema="jsonschema/msgspec_str_like_unions.json",
    cli_args=[
        "--output-model-type",
        "msgspec.Struct",
        "--use-standard-primitive-types",
        "--merge-msgspec-str-like-union-members",
    ],
    golden_output="main/jsonschema/msgspec_str_like_unions/msgspec_str_like_unions[merge].py",
    comparison_output="main/jsonschema/msgspec_str_like_unions/msgspec_str_like_unions[released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_msgspec_str_like_unions(output_file: Path, option_set: str) -> None:
    """Write one ``str`` for colliding str-like union members under --merge-msgspec-str-like-union-members.

    Without it ``Model`` holds unions such as ``UUID | str``, which msgspec refuses to decode, as in 0.99.0. With it the
    unions of a property, of list items, of map values, of a ``Literal`` beside ``UUID``, of a nested ``anyOf`` and of
    a type alias decode every valid document as ``str``, while a lone ``UUID`` keeps its check, nullable list items keep
    ``None``, an Enum of int values and a list alias stay members, and a ``Literal`` of str and int values keeps its
    int values (``Code`` under ``--enum-field-as-literal all``); a Struct member and an alias of a union of other kinds
    stay members, the keys of a map and a member that aliases a union of only str-like members merge too, a nested
    union that loses every member leaves its union, and the positions of a tuple, which form no union, keep their
    types. The union of ``UUID`` and an Enum of str values in ``Paint`` stays as it is, as do the unions of ``Kept``
    with an alias of a union of str and other members and an alias of a ``Literal`` of str and int values (under
    ``--enum-field-as-literal all``; an Enum of str and int values otherwise). A schema-invalid string that one merged
    member rejects is accepted, as the warnings say. The documents are decoded by the model generated for the running
    Python, which writes the type aliases of Python 3.12 in another form before it.
    """
    with pytest.warns(UserWarning, match="msgspec") as warned:
        run_main_and_assert(
            input_path=INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*ARGS, "--target-python-version", "3.12", *OPTION_SETS[option_set]],
        )
    runtime_file = output_file
    if sys.version_info < (3, 12):
        runtime_file = output_file.with_name("runtime_model.py")
        with pytest.warns(UserWarning, match="msgspec"):
            main([
                *("--input", str(INPUT), "--input-file-type", "jsonschema", "--output", str(runtime_file)),
                *ARGS,
                *("--target-python-version", f"{sys.version_info[0]}.{sys.version_info[1]}"),
                *OPTION_SETS[option_set],
            ])
    payloads = json.loads(PAYLOADS.read_text())
    validator = Draft202012Validator(json.loads(INPUT.read_text()), format_checker=FormatChecker())
    for payload in payloads["valid"]:
        validator.validate(payload)
    for change in payloads["invalid"]:
        with pytest.raises(SchemaValidationError):
            validator.validate({**payloads["valid"][0], **change})
    documents = [
        *(("valid", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(change)}", {**payloads["valid"][0], **change}) for change in payloads["invalid"]),
    ]
    with _generated_model(runtime_file, "msgspec_str_like_unions_model", "Model") as model:
        verdicts = "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents)
    messages = sorted(str(warning.message) for warning in warned if "union" in str(warning.message))
    assert_output(verdicts + "".join(f"warning: {message}\n" for message in messages), name="payloads")


def _verdict(model: type, document: object) -> str:
    """Return whether the generated model decodes a JSON document, or that msgspec refuses the model's types."""
    try:
        msgspec.json.decode(json.dumps(document), type=model)
    except msgspec.ValidationError:
        return "rejected"
    except TypeError:
        return "type refused by msgspec"
    return "accepted"


def test_msgspec_str_like_union_kept_members(output_file: Path) -> None:
    """Leave the unions as they are whose str-like members --merge-msgspec-str-like-union-members cannot merge.

    ``python_typed`` holds a member that ``x-python-type`` types, whose class the merge would lose, and ``looped``
    names ``LoopA`` and ``LoopB``, aliases of unions that name each other through no Struct, which msgspec refuses on
    its own; the decision reads each alias once on the way rather than following them without end. Both unions keep
    their warnings.
    """
    with pytest.warns(UserWarning, match="msgspec"):
        run_main_and_assert(
            input_path=JSON_SCHEMA_DATA_PATH / "msgspec_str_like_union_kept_members.json",
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*ARGS, "--target-python-version", "3.12", *OPTION_SETS["merge"]],
        )


NUMBER_INPUT = JSON_SCHEMA_DATA_PATH / "msgspec_str_like_number_members.json"
NUMBER_OPTION_SETS = {
    "merge": OPTION_SETS["merge"],
    "merge_collapse_root_models": OPTION_SETS["merge_collapse_root_models"],
    "merge_datetime_type_mappings": (
        *OPTION_SETS["merge"],
        *("--output-datetime-class", "datetime", "--type-mappings", "number+double=decimal"),
    ),
}


@pytest.mark.parametrize("option_set", NUMBER_OPTION_SETS)
def test_msgspec_str_like_number_members(output_file: Path, option_set: str) -> None:
    """Leave the unions unmerged whose str-like members are built for an integer or number schema.

    In ``Model`` a ``number`` of ``format: decimal`` is ``Decimal``, of ``format: time-delta`` ``timedelta``, an
    ``integer`` of ``format: date-time`` ``str`` (``datetime`` under ``--output-datetime-class datetime``) and, under
    ``--type-mappings number+double=decimal``, a ``number`` of ``format: double`` ``Decimal``. Each decodes a JSON
    number, which one ``str`` would refuse, so the unions that hold them stay as the schema gives them, with the
    refusal warning: directly, through the alias ``NumberDecimal`` (inlined by ``--collapse-root-models``), as list
    items and map values, from a type array, beside null and in the field ``Child`` inherits from ``Base``; msgspec
    refuses ``Model`` as it does without the option. The same members built for ``string`` schemas in ``Controls``
    merge, so it decodes every string and rejects the numbers the schema rejects.
    """
    with pytest.warns(UserWarning, match="msgspec") as warned:
        run_main_and_assert(
            input_path=NUMBER_INPUT,
            output_path=output_file,
            input_file_type="jsonschema",
            extra_args=[*ARGS, "--target-python-version", "3.11", *NUMBER_OPTION_SETS[option_set]],
        )
    schema = json.loads(NUMBER_INPUT.read_text())
    payloads = json.loads((DATA_PATH / "payloads" / "msgspec_str_like_number_members.json").read_text())
    verdicts = ""
    for name, documents in payloads.items():
        validator = Draft202012Validator({**schema, "$ref": f"#/$defs/{name}"} if name != "Model" else schema)
        for document in documents["valid"]:
            validator.validate(document)
        for document in documents.get("invalid", []):
            with pytest.raises(SchemaValidationError):
                validator.validate(document)
        with _generated_model(output_file, f"msgspec_str_like_number_members_{name}", name) as model:
            verdicts += "".join(
                f"{name} {label} {json.dumps(document)}: {_verdict(model, document)}\n"
                for label in ("valid", "invalid")
                for document in documents.get(label, [])
            )
    messages = sorted(str(warning.message) for warning in warned if "union" in str(warning.message))
    assert_output(verdicts + "".join(f"warning: {message}\n" for message in messages), name="payloads")


def test_msgspec_str_like_number_members_openapi(output_file: Path) -> None:
    """Leave an OpenAPI 3.0 union unmerged whose ``Decimal`` is built for a number schema.

    It stays so also through ``nullable: true`` and an alias, while a string ``format: decimal`` beside a plain string
    merges.
    """
    with pytest.warns(UserWarning, match="msgspec"):
        run_main_and_assert(
            input_path=OPEN_API_DATA_PATH / "msgspec_str_like_number_members.yaml",
            output_path=output_file,
            input_file_type="openapi",
            extra_args=[*ARGS, "--target-python-version", "3.11", *OPTION_SETS["merge"]],
        )
