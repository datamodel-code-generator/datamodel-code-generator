"""The tag field the discriminator pass appends to a variant, under ``--alias-generator``.

Up to 0.88.0 the appended field had no wire name, so the alias generator renamed it (``to_snake`` gives ``pet_type``)
while a variant that declares the property kept ``alias='petType'``: Pydantic refused the discriminated union on
import, as the aliases of its discriminator differ between the variants (#4494). The appended field now keeps the
property name as its wire name, as a declared one does.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import OPEN_API_DATA_PATH, _generated_model, _model_json_validator, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DOCUMENTS = (
    {"petType": "cat", "firstName": "Tom"},
    {"petType": "dog", "firstName": "Rex"},
    {"petType": "bird"},
)
"""Documents of ``Pet``: one per variant and one whose tag names no variant."""


@pytest.mark.parametrize("extra_args", [[], ["--snake-case-field"]], ids=["default", "snake-case-field"])
@pytest.mark.parametrize("alias_generator", ["to_snake", "to_camel", "to_pascal"])
def test_appended_tag_field_keeps_wire_name(alias_generator: str, extra_args: list[str], output_file: Path) -> None:
    """Give the appended tag field of ``Cat`` the wire name ``petType``, which ``Dog`` declares, so ``Pet`` imports."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "discriminator_alias_generator.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--alias-generator",
            alias_generator,
            "--formatters",
            "builtin",
            *extra_args,
        ],
    )
    lines: list[str] = []
    with _generated_model(output_file, "discriminator_alias_generator", "Pet") as pet:
        validate = _model_json_validator(pet)
        for document in DOCUMENTS:
            text = json.dumps(document)
            try:
                dumped = validate(text).model_dump_json(by_alias=True)
            except ValueError as error:
                dumped = f"rejects ({type(error).__name__})"
            lines.append(f"{text}: {dumped}\n")
    assert_output("".join(lines), name="payloads")
