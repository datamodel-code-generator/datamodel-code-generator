"""``--union-mode`` on a union that holds one choice beside None.

Pydantic validates ``X | None`` as ``X`` and applies ``union_mode`` only to a union of two or more choices, so the
argument on a field of one choice beside None failed at import (#6815): a nullable reference to a union model, whose
``RootModel`` is one choice, and any other single type (a scalar, a constrained type, a ``Literal``, a list). Such a
field is written without ``union_mode``; a type alias whose value is a union and a type written as a union by its own
text keep it, also when the union is inside ``Annotated``. ``Literal`` types of the same values are one choice.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

DATA_PATH = JSON_SCHEMA_DATA_PATH / "union_mode_single_choice"


@pytest.mark.parametrize(
    ("extra_args", "expected_repr"),
    [
        (
            [],
            (
                "Doc(value=Value(root=1), id=Id(root=2), nested=1, count=3, code='ab', kind='x', items=[4], written=5, "
                "writtenSingle=None, tagged=None, sameLiterals=None, writtenLiterals=None)"
            ),
        ),
        (
            ["--use-type-alias"],
            (
                "Doc(value=1, id=2, nested=1, count=3, code='ab', kind='x', items=[4], written=5, writtenSingle=None, "
                "tagged=None, sameLiterals=None, writtenLiterals=None)"
            ),
        ),
    ],
    ids=["root-model", "type-alias"],
)
def test_union_mode_on_one_choice_beside_none(extra_args: list[str], expected_repr: str, output_file: Path) -> None:
    """Write ``union_mode`` only on the fields Pydantic validates as a union of two or more choices (#6815)."""
    run_main_and_assert(
        input_path=DATA_PATH / "nullable_choices.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--union-mode",
            "left_to_right",
            "--enum-field-as-literal",
            "all",
            "--formatters",
            "builtin",
            *extra_args,
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name=f"union_mode_single_choice_{'_'.join(arg.strip('-').replace('-', '_') for arg in extra_args)}",
        model_name="Doc",
        valid_json=(
            '{"value": "1", "id": 2, "nested": "1", "count": 3, "code": "ab", "kind": "x", "items": [4], '
            '"written": "5"}'
        ),
        invalid_json='{"count": "x"}',
        expected_error_type="int_parsing",
        expected_repr=expected_repr,
    )
