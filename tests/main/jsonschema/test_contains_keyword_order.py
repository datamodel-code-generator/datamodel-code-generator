"""The contains keywords beside a ``$ref`` are written in the order the schema writes them (#6866).

``--field-include-all-keys`` writes the keywords that no constraint takes into ``json_schema_extra``. The contains
keywords beside a ``$ref`` to an array schema were collected as a set, so their order depended on the hash seed of the
process and the same command gave different files from one run to the next. A schema that writes them in each of two
orders makes the test decide in any process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from tests.main.conftest import JSON_SCHEMA_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path


def test_contains_keywords_beside_ref_keep_the_order_of_the_schema(output_file: Path) -> None:
    """Write ``contains`` and ``minContains`` (``maxContains``) in the order the schema writes them."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_DATA_PATH / "contains_keyword_order_beside_ref.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--field-include-all-keys"],
    )
