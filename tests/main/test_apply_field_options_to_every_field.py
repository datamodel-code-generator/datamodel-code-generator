"""Give every kind of field the field options the other fields take, under --apply-field-options-to-every-field."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import GRAPHQL_DATA_PATH, OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

OPTION_SETS = {"released": (), "every-field": ("--apply-field-options-to-every-field",)}
COMMON_ARGS = (
    *("--output-model-type", "pydantic_v2.BaseModel", "--target-python-version", "3.12"),
    *("--formatters", "builtin", "--disable-timestamp"),
)
PAYLOADS: tuple[tuple[str, tuple[tuple[str, object], ...]], ...] = (
    ("Flags", (("valid", {"any-value": 1, "other-value": "x"}), ("valid", {}))),
    (
        "Child",
        (
            ("valid", {"display-name": "n", "owner-id": 1, "id": "x"}),
            ("invalid", {"display-name": "n", "id": "x"}),
            ("invalid", {"owner-id": 1, "id": "x"}),
        ),
    ),
    (
        "Pet",
        (
            ("valid", {"pet-type": "dog", "bark": "woof"}),
            ("valid", {"pet-type": "cat"}),
            ("invalid", {"pet-type": "bird"}),
            ("invalid", {}),
        ),
    ),
    ("Owner", (("valid", {}), ("valid", {"address": {"city": "c"}, "pet": {"pet-type": "cat"}}))),
)
"""Documents per model of ``field_options_every_field.yaml``, with the schema's verdict."""


def _verdict(model: Any, document: object) -> str:
    """Return the value a generated model parses a document to, else that it rejects it."""
    try:
        return repr(model.model_validate(document))
    except pydantic.ValidationError:
        return "rejected"


@pytest.mark.cli_doc(
    options=["--apply-field-options-to-every-field"],
    option_description="""Give the fields that leave out some field options the ones others take.

A GraphQL field, a property whose schema is `true` or `false`, a required name that no property declares and the tag
field a discriminator appends to a variant leave out some field options, such as `--use-default-kwarg`,
`--use-annotated` and `--use-default-factory-for-optional-nested-models`, which the other fields of their model take,
so one option gives different fields in one model. With this option they take them too:
`owner_id: Annotated[Any, Field(alias='owner-id')]` beside
`display_name: Annotated[str, Field(alias='display-name')]`. The root field of a root model is not one of them: under
`--force-optional` and `--use-default-kwarg` a discriminated union root still writes
`root: Cat | Dog | None = Field(None, discriminator='kind')`.
""",
    input_schema="openapi/field_options_every_field.yaml",
    cli_args=["--use-annotated", "--apply-field-options-to-every-field"],
    golden_output="main/apply_field_options_to_every_field/apply_field_options_to_every_field[use-annotated-every-field].py",
    comparison_output="main/apply_field_options_to_every_field/apply_field_options_to_every_field[use-annotated-released].py",
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "field_options",
    [("--use-default-kwarg",), ("--use-annotated",)],
    ids=["use-default-kwarg", "use-annotated"],
)
def test_apply_field_options_to_every_field(output_file: Path, field_options: tuple[str, ...], option_set: str) -> None:
    """Write a field option on a property whose schema is `true`, a required name and an appended tag (#5588).

    Without the option ``any_value`` (schema ``true``) leaves out ``--use-default-kwarg``, and the required name
    ``owner-id`` without a property and the tag field ``pet_type`` the discriminator appends leave out
    ``--use-annotated``, as in 0.90.0; with it they write them as ``other_value``, ``display_name`` and the other
    fields do. Each model parses every document the schema accepts to the same value either way and rejects the others.
    """
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "field_options_every_field.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*COMMON_ARGS, *field_options, *OPTION_SETS[option_set]],
    )
    lines: list[str] = []
    for name, documents in PAYLOADS:
        with _generated_model(output_file, "field_options_every_field", name) as model:
            lines.extend(f"{name} {kind} {json.dumps(doc)}: {_verdict(model, doc)}\n" for kind, doc in documents)
    assert_output("".join(lines), name="payloads")


@pytest.mark.parametrize("option_set", OPTION_SETS)
def test_apply_field_options_to_every_field_graphql(output_file: Path, option_set: str) -> None:
    """Give a GraphQL field of a nested type the default factory of `--use-default-factory-for-optional-nested-models`.

    Without the option ``address`` defaults to None, as in 0.90.0, where the field of the equivalent JSON Schema
    defaults to ``Address()``; with it the GraphQL field defaults to ``Address()`` too.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "field_options_every_field.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            *COMMON_ARGS,
            *("--target-python-version", "3.10", "--use-default-factory-for-optional-nested-models"),
            *OPTION_SETS[option_set],
        ],
    )
    with _generated_model(output_file, "field_options_every_field_graphql", "Owner") as model:
        assert_output(f"Owner {{}}: {_verdict(model, {})}\n", name="payloads")
