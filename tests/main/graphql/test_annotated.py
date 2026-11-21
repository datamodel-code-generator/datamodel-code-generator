"""Tests for GraphQL annotated types generation."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import GRAPHQL_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.cli_doc(
    options=["--use-annotated"],
    option_description="""Use typing.Annotated for Field() with constraints.

The `--use-annotated` flag generates Field definitions using typing.Annotated
syntax instead of default values. This also enables `--field-constraints`.

`--use-annotated` alone does not preserve `uniqueItems: true` as set semantics.
Use [`--use-unique-items-as-set`](#use-unique-items-as-set) when you need the
generated type to enforce uniqueness for array schemas.""",
    input_schema="graphql/annotated.graphql",
    cli_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"],
    golden_output="graphql/annotated/annotated.py",
)
def test_annotated(output_file: Path) -> None:
    """Use typing.Annotated for Field() with constraints.

    The `--use-annotated` flag generates Field definitions using typing.Annotated
    syntax instead of default values. This also enables `--field-constraints`.

    `--use-annotated` alone does not preserve `uniqueItems: true` as set semantics.
    Use `--use-unique-items-as-set` when you need the generated type to enforce
    uniqueness for array schemas.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "annotated.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated"],
    )


def test_annotated_use_standard_collections(output_file: Path) -> None:
    """Test GraphQL annotated types with standard collections."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "annotated.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--use-standard-collections",
        ],
    )


def test_annotated_use_standard_collections_use_union_operator(output_file: Path) -> None:
    """Test GraphQL annotated types with standard collections and union operator."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "annotated.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--use-standard-collections",
            "--use-union-operator",
        ],
    )


@pytest.mark.cli_doc(
    options=["--use-union-operator"],
    option_description="""Use | operator for Union types (PEP 604).

The `--use-union-operator` flag generates union types using the | operator
(e.g., `str | None`) instead of `Union[str, None]` or `Optional[str]`.
This is the default behavior.""",
    input_schema="graphql/annotated.graphql",
    cli_args=["--output-model-type", "pydantic_v2.BaseModel", "--use-annotated", "--use-union-operator"],
    golden_output="graphql/annotated/use_union_operator.py",
    related_options=["--no-use-union-operator"],
)
def test_annotated_use_union_operator(output_file: Path) -> None:
    """Use | operator for Union types (PEP 604).

    The `--use-union-operator` flag generates union types using the | operator
    (e.g., `str | None`) instead of `Union[str, None]` or `Optional[str]`.
    This is the default behavior.
    """
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "annotated.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--use-union-operator",
        ],
    )


def test_annotated_field_aliases(output_file: Path) -> None:
    """Test GraphQL annotated types with field aliases."""
    run_main_and_assert(
        input_path=GRAPHQL_DATA_PATH / "field-aliases.graphql",
        output_path=output_file,
        input_file_type="graphql",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--use-annotated",
            "--aliases",
            str(GRAPHQL_DATA_PATH / "field-aliases.json"),
        ],
    )
