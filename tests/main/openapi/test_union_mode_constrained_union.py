"""``--union-mode`` on a union whose field carries a constraint of its own.

Pydantic applies a length or bound constraint of ``Field()`` to a union as a validator around it, before
``union_mode``, which then fails at import (#7107): a property that refers to a type-list union, collapsed in place,
which an ``allOf`` member constrains (``contains``). Such a field is written without ``union_mode``; a field whose
choices carry the constraint (``maxItems``, ``minimum``) and an unconstrained one keep it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import OPEN_API_DATA_PATH, assert_generated_model_json_validation, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_union_mode_beside_a_field_constraint(output_file: Path) -> None:
    """Write no ``union_mode`` on a field whose constraint Pydantic applies around the union (#7107)."""
    run_main_and_assert(
        input_path=OPEN_API_DATA_PATH / "union_mode_constrained_union.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--union-mode",
            "left_to_right",
            "--collapse-root-models",
            "--formatters",
            "builtin",
        ],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="union_mode_constrained_union",
        model_name="Child",
        valid_json='{"tags": ["a"], "names": "ab", "count": "1", "plain": "2"}',
        invalid_json='{"tags": [], "names": "ab", "count": 1, "plain": 2}',
        expected_error_type="too_short",
        expected_repr="Child(tags=['a'], names='ab', count=1, plain=2)",
    )
