"""Inline a constrained string root model into a union or a container as Annotated under --collapse-root-models."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import msgspec
import pydantic
import pytest

from tests.conftest import assert_output
from tests.main.conftest import DATA_PATH, OPEN_API_DATA_PATH, _generated_model, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT = OPEN_API_DATA_PATH / "collapse_constrained_string_members.yaml"
PAYLOADS = DATA_PATH / "payloads" / "collapse_constrained_string_members.json"
OPTION_SETS = {
    "released": (),
    "annotated": ("--collapse-constrained-root-models-as-annotated",),
    "annotated_every_container": (
        "--collapse-constrained-root-models-as-annotated",
        "--keep-constrained-root-models-in-every-container",
    ),
}


@pytest.mark.cli_doc(
    options=["--collapse-constrained-root-models-as-annotated"],
    option_description="""Inline a constrained string root model into a union or a container as `Annotated`.

Under `--use-annotated` (which turns on `--field-constraints`), a string with constraints inside a union, such as the
string branch of an "ID or object" property, or as the items or values of a container, becomes a root model of its own,
which `--collapse-root-models` keeps named there, as `Field()` arguments of the holder's field could not carry its
constraints: `account: Account | AccountModel` with `Account(RootModel[str])`, which also takes a name a component may
want. With this option collapse inlines it in place as a string type that writes its constraints in `Annotated`
(`account: AccountModel | Annotated[str, StringConstraints(max_length=8)]`,
`tags: list[Annotated[str, StringConstraints(max_length=3)]]`), so the member keeps its constraints and parses as a
`str`. Root models of other types, and output models whose types write no constraints (msgspec, dataclasses), keep the
root model.""",
    input_schema="openapi/collapse_constrained_string_members.yaml",
    cli_args=[
        "--use-annotated",
        "--collapse-root-models",
        "--use-unique-items-as-set",
        "--collapse-constrained-root-models-as-annotated",
    ],
    golden_output=(
        "main/openapi/collapse_constrained_string_members/collapse_constrained_string_members[pydantic_v2-annotated].py"
    ),
    comparison_output=(
        "main/openapi/collapse_constrained_string_members/collapse_constrained_string_members[pydantic_v2-released].py"
    ),
)
@pytest.mark.parametrize("option_set", OPTION_SETS)
@pytest.mark.parametrize(
    "output_model_type", ["pydantic_v2.BaseModel", "msgspec.Struct"], ids=["pydantic_v2", "msgspec"]
)
def test_collapse_constrained_string_members(output_file: Path, output_model_type: str, option_set: str) -> None:
    """Write a constrained string member, item or value in place under the option (#4846).

    Without it the string branch of a union, the items of a list and a set and the values of a map that a string with
    constraints types keep root models of their own under --use-annotated, as in 0.90.0. With it Pydantic writes them
    as `Annotated[str, StringConstraints(...)]` in place, also inside a set where
    --keep-constrained-root-models-in-every-container keeps the root model otherwise, and the pattern that needs the
    `python-re` engine moves the engine to the holder; an integer branch and a root that admits null keep their root
    models, and msgspec keeps its aliases. Every valid payload decodes and every invalid one is rejected under every
    option set, except the set items that collapse writes without their constraints where neither option keeps them
    (Pydantic without the option, msgspec without --keep-constrained-root-models-in-every-container), as in 0.90.0.
    """
    run_main_and_assert(
        input_path=INPUT,
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            *("--output-model-type", output_model_type, "--use-annotated", "--collapse-root-models"),
            *("--use-unique-items-as-set", "--formatters", "builtin", "--disable-timestamp"),
            *OPTION_SETS[option_set],
        ],
    )
    payloads = json.loads(PAYLOADS.read_text())
    documents = [
        *((f"valid {json.dumps(payload)}", payload) for payload in payloads["valid"]),
        *((f"invalid {json.dumps(payload)}", payload) for payload in payloads["invalid"]),
    ]
    with _generated_model(output_file, "collapse_constrained_string_members_model", "Fee") as model:
        assert_output(
            "".join(f"{label}: {_verdict(model, document)}\n" for label, document in documents), name="payloads"
        )


def _verdict(model: Any, document: object) -> str:
    """Return whether the generated model decodes a document."""
    try:
        if isinstance(model, type) and issubclass(model, msgspec.Struct):
            msgspec.convert(document, model)
        else:
            model.model_validate(document)
    except (msgspec.ValidationError, pydantic.ValidationError):
        return "rejected"
    return "accepted"
