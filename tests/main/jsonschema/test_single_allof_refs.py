"""``--read-single-allof-refs-in-their-documents``: the target of an allOf of one ``$ref`` is read where it lives.

A schema whose allOf is one local ``$ref`` alone is typed by its target: a root model over the referenced enum, a root
model over a ``true`` target, or refused over a ``false`` one. Up to 0.96.0 the target is read at the same pointer of
the input document, so in a referenced file the form depends on the input document's definitions (#6924): without a
matching enum there the schema is written as a copy of the enum, and a ``false`` there refuses a valid file. A
plain-name anchor (``#code``) is read as the pointer its text after ``#c`` names, so its schema is a copy too (#6925).
Under the option the target is read through the reference's key, in its document, as every other ``$ref`` is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator.__main__ import Exit
from tests.main.conftest import (
    JSON_SCHEMA_DATA_PATH,
    OPEN_API_DATA_PATH,
    assert_generated_model_json_validation,
    run_main_and_assert,
)

if TYPE_CHECKING:
    from pathlib import Path

JSON_SCHEMA_PATH = JSON_SCHEMA_DATA_PATH / "single_allof_refs"
OPEN_API_PATH = OPEN_API_DATA_PATH / "single_allof_refs"
OPTION = "--read-single-allof-refs-in-their-documents"


@pytest.mark.cli_doc(
    options=[OPTION],
    option_description="""Read the target of an allOf of one `$ref` in the document that holds the schema.

A schema whose `allOf` is one local `$ref` alone (`Status: {allOf: [{$ref: "#/definitions/Code"}]}`) is typed by
its target: a root model over the referenced enum (`Status(RootModel[Code])`), a root model over a `true` target, or
refused when the target is `false`. Without this option the target is read at the same pointer of the input
document, not of the document that holds the schema, and a plain-name anchor such as `#code` is read as a pointer
that names nothing. In a file the input refers to, such a schema is then written as a copy of the enum
(`class Status(Enum)`), or as a root model when the input document happens to hold an enum at that pointer, and a
`false` there refuses the valid file; an anchor reference gives a copy of the enum in one file too, as in 0.96.0.
With `--read-single-allof-refs-in-their-documents` the target is read as every other `$ref` is read, so the schema
takes the form the same schemas take in one file. The copy and the root model accept the same values; the option
changes the classes written, and a referenced `true` target that was written as an empty model now accepts every
value, as the schema does.""",
    input_schema="jsonschema/single_allof_refs/main.json",
    cli_args=[OPTION],
    golden_output="main/jsonschema/single_allof_refs/read_in_their_documents[two-files-on].py",
    comparison_output="main/jsonschema/single_allof_refs/read_in_their_documents[two-files-off].py",
)
@pytest.mark.parametrize(
    ("input_path", "input_file_type", "extra_args", "model_name", "field"),
    [
        pytest.param(JSON_SCHEMA_PATH / "main.json", "jsonschema", (), "Main", "status", id="two-files-off"),
        pytest.param(JSON_SCHEMA_PATH / "main.json", "jsonschema", (OPTION,), "Main", "status", id="two-files-on"),
        pytest.param(JSON_SCHEMA_PATH / "single.json", "jsonschema", (), "Main", "status", id="one-file-off"),
        pytest.param(JSON_SCHEMA_PATH / "single.json", "jsonschema", (OPTION,), "Main", "status", id="one-file-on"),
        pytest.param(JSON_SCHEMA_PATH / "false_code.json", "jsonschema", (OPTION,), "Main", "status", id="false-on"),
        pytest.param(JSON_SCHEMA_PATH / "anchor.json", "jsonschema", (), "Main", "s", id="anchor-off"),
        pytest.param(JSON_SCHEMA_PATH / "anchor.json", "jsonschema", (OPTION,), "Main", "s", id="anchor-on"),
        pytest.param(OPEN_API_PATH / "api.yaml", "openapi", (), "Order", "status", id="openapi-off"),
        pytest.param(OPEN_API_PATH / "api.yaml", "openapi", (OPTION,), "Order", "status", id="openapi-on"),
    ],
)
def test_single_allof_refs_read_in_their_documents(
    output_file: Path,
    input_path: Path,
    input_file_type: str,
    extra_args: tuple[str, ...],
    model_name: str,
    field: str,
) -> None:
    """Type a schema whose allOf is one enum ``$ref`` as the root model one file gives (#6924, #6925).

    ``two-files``: ``Status`` and ``Code`` are in the file the input refers to (``one-file``: both in the input).
    ``false``: the input document holds ``Code: false``, which refuses the valid referenced file without the option.
    ``anchor``: ``Status`` names ``Code`` by its ``$anchor``. ``openapi``: the components are in a second file.
    Without the option ``Status`` is a copy of the enum, with it ``RootModel[Code]``; both accept ``a`` and reject
    ``c``.
    """
    run_main_and_assert(
        input_path=input_path,
        output_path=output_file,
        input_file_type=input_file_type,
        extra_args=["--output-model-type", "pydantic_v2.BaseModel", "--formatters", "builtin", *extra_args],
    )
    assert_generated_model_json_validation(
        output_file,
        module_name="single_allof_refs",
        model_name=model_name,
        valid_json=f'{{"{field}": "a"}}',
        invalid_json=f'{{"{field}": "c"}}',
        expected_error_type="enum",
    )


def test_single_allof_refs_false_in_the_input_document(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Refuse a valid referenced file without the option where the input document holds ``false`` at the pointer."""
    run_main_and_assert(
        input_path=JSON_SCHEMA_PATH / "false_code.json",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=["--output-model-type", "pydantic_v2.BaseModel"],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains="allOf contains a boolean false schema that makes the schema unsatisfiable",
        output_should_not_exist=True,
    )
