"""Leave a ``$ref`` that --external-ref-mapping maps unread by --generate-schema-validators (#6756)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

MAPPED_REF_SCHEMA_VALIDATORS = JSON_SCHEMA_DATA_PATH / "mapped_ref_schema_validators"


@pytest.mark.parametrize("name", ["order", "order_unique_items", "order_all_of"])
def test_schema_validators_do_not_read_mapped_ref_under_any_of(output_file: Path, name: str) -> None:
    """Treat a mapped ``$ref`` as the class it imports: its document is absent and --strict-refs rejects a read."""
    run_main_and_assert(
        input_path=MAPPED_REF_SCHEMA_VALIDATORS / f"{name}.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=[
            "--output-model-type",
            "pydantic_v2.BaseModel",
            "--formatters",
            "builtin",
            "--strict-refs",
            "--external-ref-mapping",
            f"{MAPPED_REF_SCHEMA_VALIDATORS / 'absent_shared.json'}=mycompany.shared",
            "--generate-schema-validators",
        ],
    )
