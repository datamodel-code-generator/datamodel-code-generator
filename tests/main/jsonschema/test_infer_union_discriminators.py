"""``--infer-union-discriminators``: a oneOf or anyOf whose branches each pin a tag is written as a discriminated union.

Typed languages write an internally tagged enum as a union whose every branch requires one property holding one value,
a ``const`` or a one-value ``enum``, without a ``discriminator`` keyword. Up to 0.96.0 Pydantic output wrote such a
union as a smart union, which tries every branch and reports an error for each; msgspec output tagged it when the tag
is written on the branch itself, and wrote untagged Structs it cannot decode when the tag is in an ``allOf`` member or a
branch is null. Under the option the union is captured as a declared discriminator is, so valid payloads parse to the
same values and an invalid one reports the errors of the branch its tag names.
"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import msgspec
import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from datamodel_code_generator.__main__ import Exit
from tests.conftest import assert_output
from tests.main.conftest import (
    ASYNC_API_DATA_PATH,
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    _generated_model,
    _model_json_validator,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = JSON_SCHEMA_DATA_PATH / "serde_internally_tagged.json"
DRAFT7_INPUT_PATH = JSON_SCHEMA_DATA_PATH / "serde_tagged_draft7.json"
ROOT_INPUT_PATH = JSON_SCHEMA_DATA_PATH / "serde_tagged_root.json"
ALL_OF_PARTS_INPUT_PATH = JSON_SCHEMA_DATA_PATH / "serde_tagged_all_of_parts.json"
OPTION = "--infer-union-discriminators"
OPTION_SETS = {"off": (), "on": (OPTION,)}
COMMON_ARGS = ("--formatters", "builtin", "--disable-timestamp", "--disable-warnings")
OUTPUT_MODEL_TYPES = (
    "pydantic_v2.BaseModel",
    "pydantic_v2.dataclass",
    "dataclasses.dataclass",
    pytest.param(
        "typing.TypedDict",
        marks=pytest.mark.skipif(
            sys.version_info < (3, 12), reason="Pydantic validates typing.TypedDict from Python 3.12 on"
        ),
    ),
    "msgspec.Struct",
)
DRAWING_PAYLOADS: tuple[dict[str, Any], ...] = (
    {"shape": {"kind": "circle", "radius": 1.5}, "shapes": []},
    {
        "shape": {"kind": "rect", "w": 1, "h": 2},
        "shapes": [{"kind": "polygon", "sides": 5}, {"kind": "empty"}, {"kind": "circle", "radius": 2}],
        "maybe_shape": None,
    },
    {"shape": {"kind": "empty"}, "shapes": [], "maybe_shape": {"kind": "polygon", "sides": 3}},
    {"shape": {"kind": "empty"}, "shapes": [], "message": {"t": "point", "c": {"x": 1, "y": 2}}},
    {"shape": {"kind": "empty"}, "shapes": [], "message": {"t": "ping"}, "version": {"version": 2, "names": ["a"]}},
    {"shape": {"kind": "empty"}, "shapes": [], "event": None, "pipeline": {"source": "db"}},
    {"shape": {"kind": "empty"}, "shapes": [], "event": {"type": "stopped", "code": 3}, "mixed": "text"},
    {"shape": {"kind": "empty"}, "shapes": [], "duplicate": {"kind": "circle", "diameter": 2}},
    {"shape": {"kind": "empty"}, "shapes": [], "declared": {"kind": "rect", "shape": "square", "w": 1}},
    {"shape": {"kind": "rect", "w": 1}, "shapes": []},
    {"shape": {"kind": "hexagon"}, "shapes": []},
    {"shape": {"radius": 1}, "shapes": []},
    {"shape": {"kind": "circle", "radius": 1}, "shapes": [{"kind": "polygon"}]},
    {"shape": {"kind": "empty"}, "shapes": [], "version": {"version": 3, "name": "a"}},
    {"shape": {"kind": "empty"}, "shapes": [], "event": {"type": "stopped", "code": "x"}},
    {"shape": {"kind": "empty"}, "shapes": [], "message": {"t": "point", "c": {"x": 1}}},
)
"""Documents for ``Drawing``, the first nine valid against the schema and the others not."""
SHAPE_PAYLOADS: tuple[dict[str, Any], ...] = (
    {"kind": "circle", "radius": 1.5},
    {"kind": "rect", "w": 1, "h": 2},
    {"kind": "polygon", "sides": 5},
    {"kind": "empty"},
    {"kind": "rect", "w": 1},
    {"kind": "polygon"},
    {"kind": "hexagon"},
    {"radius": 1},
)
"""Documents for ``Shape``, which msgspec decodes alone: ``Drawing`` also holds unions it cannot decode."""
NOTIFICATION_PAYLOADS: tuple[dict[str, Any], ...] = (
    {"channel": "email", "address": "a@example.com"},
    {"channel": "push"},
    {"channel": "sms", "number": 5},
    {"channel": "fax"},
    {"address": "a@example.com"},
)
"""Documents for the root union ``Notification``, the first two valid against the schema and the others not."""


def _parsed(model: Any, text: str, *, is_msgspec: bool) -> str:
    """Return what a generated model makes of a JSON document: the repr of the value it parses, or ``rejects``."""
    try:
        value = msgspec.json.decode(text, type=model) if is_msgspec else _model_json_validator(model)(text)
    except (ValidationError, msgspec.ValidationError):
        return "rejects"
    return repr(value)


def _payload_verdicts(
    output_file: Path, input_path: Path, model_name: str, payloads: tuple[dict[str, Any], ...], output_model_type: str
) -> str:
    """Return whether the schema accepts each document and what the generated model parses it to, one line each.

    A model named after a definition is checked against that definition, any other against the whole schema.
    """
    schema = json.loads(input_path.read_text(encoding="utf-8"))
    definitions = schema.get("$defs", {})
    validator = Draft202012Validator(
        {"$ref": f"#/$defs/{model_name}", "$defs": definitions} if model_name in definitions else schema
    )
    with _generated_model(output_file, f"infer_union_discriminators_{input_path.stem}", model_name) as model:
        return "".join(
            f"{json.dumps(payload)}: schema {'accepts' if validator.is_valid(payload) else 'rejects'}, model "
            f"{_parsed(model, json.dumps(payload), is_msgspec=output_model_type == 'msgspec.Struct')}\n"
            for payload in payloads
        )


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Write a oneOf or anyOf whose branches each pin a tag as a discriminated union.

Typed languages write an internally tagged enum as a `oneOf` whose every branch requires one property holding one
value, a `const` or a one-value `enum`, without a `discriminator` keyword. With this option such a union is written
as a discriminated union, as a `discriminator` keyword writes it: Pydantic `Field(discriminator=...)`, and msgspec
Structs tagged with `tag_field` and `tag`. It applies where every branch that is not null requires the same property
with one string or integer value, read from the branch, the schema its local `$ref` names and its `allOf` members,
and no two branches share a value; in Pydantic output a union that declares a `discriminator` keeps it. Valid
payloads parse to the same values, and an invalid payload reports the errors of the branch its tag names, or one
error for an unknown or missing tag, instead of an error for every branch. A tag written as a one-value `enum`
without `--use-single-value-enum-as-const` or `--enum-field-as-literal` is typed by its value (`Literal['email']`),
as a `discriminator` keyword types it; under `--use-one-literal-as-default` a payload without the tag is rejected, as
the schema rejects it. Without this option these unions are smart unions in Pydantic output, and msgspec output tags
only those whose tags are written on the branches themselves, as in 0.96.0. The option does not apply to
`dataclasses.dataclass` and `typing.TypedDict` output, which validate nothing.""",
    input_schema="jsonschema/serde_tagged_root.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/infer_union_discriminators/root_union[on].py",
    comparison_output="main/jsonschema/infer_union_discriminators/root_union[off].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_infer_union_discriminators_root_union(output_file: Path, option_set: str) -> None:
    """Write the root union of a document as a discriminated union under the option (#6819).

    Each branch of ``Notification`` requires ``channel`` with its own ``const``. Valid documents parse to the same
    branch and values with and without the option; invalid ones are rejected by both.
    """
    run_main_and_assert(
        input_path=ROOT_INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *COMMON_ARGS, *OPTION_SETS[option_set]],
    )
    assert_output(
        _payload_verdicts(output_file, ROOT_INPUT_PATH, "Notification", NOTIFICATION_PAYLOADS, "pydantic_v2.BaseModel"),
        name="payloads",
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize("output_model_type", OUTPUT_MODEL_TYPES)
def test_infer_union_discriminators(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Infer the discriminator of every union a property tags, for each output, and parse the same values (#6819).

    ``Shape`` holds an inline branch, a ``$ref`` with the tag beside it, a ``$ref`` in an ``allOf`` beside the tag and
    a branch with the tag alone; ``Message`` is adjacently tagged, ``Version`` has integer tags and ``Event`` a null
    branch, which the union keeps as its ``None``. ``Pipeline`` leaves the tag out of a branch, ``Duplicate`` shares a
    value, ``Mixed`` has a string branch, and ``Declared`` declares a discriminator on another property: those keep
    their output. Pydantic output writes ``discriminator=``, msgspec output also tags ``Shape`` and ``Event``, which it
    could not decode before; dataclasses and TypedDicts are left alone. Valid documents parse to the same values with
    and without the option in Pydantic output, invalid ones are rejected; msgspec decodes ``Shape`` only with it.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, *COMMON_ARGS, *OPTION_SETS[option_set]],
    )
    if output_model_type != "msgspec.Struct":
        assert_output(
            _payload_verdicts(output_file, INPUT_PATH, "Drawing", DRAWING_PAYLOADS, output_model_type),
            name="payloads",
        )
    elif option_set == "on":
        assert_output(
            _payload_verdicts(output_file, INPUT_PATH, "Shape", SHAPE_PAYLOADS, output_model_type), name="payloads"
        )


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param(("--collapse-root-models",), id="collapse-root-models"),
        pytest.param(("--collapse-root-models", "--use-annotated"), id="collapse-root-models-annotated"),
        pytest.param(("--use-tag-as-union-variant-name",), id="use-tag-as-union-variant-name"),
        pytest.param(("--use-one-literal-as-default",), id="use-one-literal-as-default"),
        pytest.param(("--use-type-alias",), id="use-type-alias"),
    ],
)
def test_infer_union_discriminators_with_options(
    output_file: Path, extra_args: tuple[str, ...], option_set: str
) -> None:
    """Infer the discriminator beside the options that write unions or tags otherwise.

    Under --collapse-root-models the fields that name ``Shape`` write the discriminator, a list's items in
    ``Annotated``; a field that is a union with null writes none, as for a declared discriminator. The branch names of
    --use-tag-as-union-variant-name do not change. Under --use-one-literal-as-default the tag fields have defaults, and
    a document without the tag, which the schema rejects, is rejected too.
    """
    run_main_and_assert(
        input_path=INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *COMMON_ARGS,
            *extra_args,
            *OPTION_SETS[option_set],
        ],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param((), id="enum"),
        pytest.param(("--use-single-value-enum-as-const",), id="use-single-value-enum-as-const"),
        pytest.param(("--snake-case-field",), id="snake-case-field"),
    ],
)
def test_infer_union_discriminators_one_value_enums(
    output_file: Path, extra_args: tuple[str, ...], option_set: str
) -> None:
    """Infer the discriminator of a draft-07 union whose camelCase tags are one-value enums.

    Without --use-single-value-enum-as-const the tag fields are typed by their values under the option, as a declared
    discriminator types them, and the one-member Enum classes stay; with it they are ``Literal`` already. Under
    --snake-case-field the discriminator names the field ``layer_kind``, whose alias stays ``layerKind``.
    """
    run_main_and_assert(
        input_path=DRAFT7_INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            *COMMON_ARGS,
            *extra_args,
            *OPTION_SETS[option_set],
        ],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_infer_union_discriminators_msgspec_tagged_unions(output_file: Path, option_set: str) -> None:
    """Tag the msgspec Structs by the payload's property names under --use-msgspec-tagged-unions and the option."""
    run_main_and_assert(
        input_path=DRAFT7_INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "msgspec.Struct",
            "--use-msgspec-tagged-unions",
            *COMMON_ARGS,
            *OPTION_SETS[option_set],
        ],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_infer_union_discriminators_request_response(output_file: Path, option_set: str) -> None:
    """Infer the discriminator of a union of ``$ref`` branches whose models split into request and response models."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "tagged_union_read_only.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--read-only-write-only-model-type",
            "all",
            *COMMON_ARGS,
            *OPTION_SETS[option_set],
        ],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_infer_union_discriminators_asyncapi(output_file: Path, option_set: str) -> None:
    """Infer the discriminator of a union a message payload names, which AsyncAPI parses as JSON Schema does."""
    run_main_and_assert(
        input_path=ASYNC_API_DATA_PATH / "tagged_payload_union.yaml",
        output_path=output_file,
        input_file_type="asyncapi",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *COMMON_ARGS, *OPTION_SETS[option_set]],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize("output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"])
def test_infer_union_discriminators_all_of_parts(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Read the tag of a branch from its allOf parts: the part that pins it wins over one that declares it unpinned.

    ``Animal`` pins ``petType`` in an allOf part after and before the base ``Pet`` that declares it as a string, and
    on the branch itself as well as in its part, so the union is tagged by it. A branch of ``Conflict`` pins the tag
    to two values in two parts and a part of ``Forbidden`` forbids it (false), which leaves those branches without a
    tag, so both unions keep their output.
    """
    run_main_and_assert(
        input_path=ALL_OF_PARTS_INPUT_PATH,
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", output_model_type, *COMMON_ARGS, *OPTION_SETS[option_set]],
    )


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_infer_union_discriminators_inheritance_cycle(
    output_file: Path, option_set: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refuse an inheritance cycle through a union branch as without the option; the tag walk stops at the visited A."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "allof_inheritance_errors" / "cycle_union_branch.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", *COMMON_ARGS, *OPTION_SETS[option_set]],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr=(
            "Generated model inheritance cycle: A (cycle_union_branch.json#/$defs/A) -> "
            "B (cycle_union_branch.json#/$defs/B) -> A (cycle_union_branch.json#/$defs/A)\n"
        ),
        output_should_not_exist=True,
    )
