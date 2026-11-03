"""Write a non-identifier alias of a Pydantic dataclass field in one form, whichever path sets the alias."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    installed_pydantic_runs_target,
    run_main_and_assert,
    target_pydantic_args,
)

if TYPE_CHECKING:
    from pathlib import Path

SCOPED_ALIASES = json.dumps({"ApiMerged.x-y": "renamed", "ApiDerived.p-q": "renamed_pq"})


@pytest.mark.parametrize(
    "target_pydantic_version",
    [
        pytest.param(
            None,
            id="target-unset",
            marks=pytest.mark.skipif(
                not installed_pydantic_runs_target("2.4"),
                reason="A dataclass field alias must be an identifier before Pydantic 2.4",
            ),
        ),
        pytest.param("2", id="target-2"),
    ],
)
def test_main_jsonschema_dataclass_alias_copies(output_file: Path, target_pydantic_version: str | None) -> None:
    """Write a late alias of a class without a base class in the split form, and keep ``alias=`` below a base class.

    Before Pydantic 2.4 a dataclass field alias must be an identifier where the class builds its own signature, so
    ``--target-pydantic-version 2`` writes the serialization alias and a validation alias for a field that takes its
    alias after it is built in a class without a base class (the final class name scopes ``--aliases``); it imported
    with ``ValueError: 'x-y' is not a valid parameter name``. A class below a base class takes the signature of that
    dataclass, so its required copy and its late alias keep ``alias=``.
    """
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "dataclass_alias_copies.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.dataclass",
            "--class-name-prefix",
            "Api",
            "--aliases",
            SCOPED_ALIASES,
            *target_pydantic_args(target_pydantic_version),
        ],
        force_exec_validation=True,
    )
