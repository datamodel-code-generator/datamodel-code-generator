"""Warn about a schema whose ``enum`` lists no value, which no value is valid against; the output stays as before."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.conftest import assert_output
from tests.main.conftest import JSON_SCHEMA_DATA_PATH, OPEN_API_DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

    from datamodel_code_generator.format import InputFileTypeLiteral

CASES: dict[str, tuple[Path, InputFileTypeLiteral, tuple[str, ...]]] = {
    "jsonschema": (JSON_SCHEMA_DATA_PATH / "empty_enum.json", "jsonschema", ()),
    "jsonschema-msgspec": (
        JSON_SCHEMA_DATA_PATH / "empty_enum.json",
        "jsonschema",
        ("--output-model-type", "msgspec.Struct"),
    ),
    "jsonschema-ignore-ref-siblings": (
        JSON_SCHEMA_DATA_PATH / "empty_enum.json",
        "jsonschema",
        ("--ignore-ref-siblings-by-schema-version",),
    ),
    "openapi": (OPEN_API_DATA_PATH / "empty_enum.yaml", "openapi", ()),
}


@pytest.mark.parametrize("case", CASES)
def test_empty_enum_warning(output_file: Path, recwarn: pytest.WarningsRecorder, case: str) -> None:
    """Warn once for each schema object of a document whose ``enum`` is ``[]``, and write the code written before.

    A schema the document writes in one place and references twice is reported once, at its location; the members of
    an ``anyOf`` beside an empty ``enum`` and an ``enum`` that lists values are not reported. An ``enum`` beside a
    ``$ref`` is reported where the generator reads it, not where the schema version ignores the keywords beside a
    ``$ref`` (``--ignore-ref-siblings-by-schema-version`` with draft 7).
    """
    input_path, input_file_type, options = CASES[case]
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=["--formatters", "builtin", "--disable-timestamp", *options],
    )
    warnings = dict.fromkeys(
        str(warning.message)
        for warning in recwarn
        if warning.category is UserWarning and "admits no value: its enum is empty" in str(warning.message)
    )
    assert_output("".join(f"{message}\n" for message in warnings))
