"""A Swagger 2.0 document names itself in the messages of the input type (#6882).

A document with a root ``swagger`` key and no ``openapi`` key is no OpenAPI 3 document: without ``--input-file-type``
it is read as YAML or JSON data, and with ``--input-file-type openapi`` the reader finds no ``components/schemas`` in
it and stops. Neither said why. The notice of the inferred type now says that the document looks like Swagger 2.0, and
the refusal says what the reader reads and what to do. No inferred type changes and no document that generates today
generates anything else.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from datamodel_code_generator import inferred_message, inferred_swagger_2_message
from datamodel_code_generator.__main__ import Exit
from tests.main.conftest import DATA_PATH, run_main_and_assert

if TYPE_CHECKING:
    from pathlib import Path

INPUT_PATH = DATA_PATH / "swagger_2_input"
OPTIONS = ("--formatters", "builtin", "--disable-warnings")
MODELS_NOT_FOUND = (
    'Models not found in the input data: the input is a Swagger 2.0 document ("swagger: 2.0"), and the OpenAPI reader '
    'reads OpenAPI 3 "components/schemas", not "definitions". Convert it to OpenAPI 3, or read its definitions as '
    "JSON Schema with --input-file-type jsonschema."
)


@pytest.mark.parametrize(
    ("document", "inferred_type"),
    [
        pytest.param("petstore.yaml", "yaml", id="yaml"),
        pytest.param("petstore.json", "json", id="json"),
    ],
)
def test_input_type_notice_names_swagger_2(
    output_file: Path, capsys: pytest.CaptureFixture[str], document: str, inferred_type: str
) -> None:
    """Add a sentence to the notice of the inferred type; the document is still read as data, as before."""
    run_main_and_assert(
        input_path=INPUT_PATH / document,
        output_path=output_file,
        extra_args=OPTIONS,
        capsys=capsys,
        expected_stderr=f"{inferred_message.format(inferred_type)}\n{inferred_swagger_2_message}\n",
    )


@pytest.mark.parametrize(
    "extra_args",
    [
        pytest.param([], id="schemas"),
        pytest.param(["--openapi-scopes", "parameters"], id="parameters"),
    ],
)
def test_models_not_found_names_swagger_2(
    output_file: Path, capsys: pytest.CaptureFixture[str], extra_args: list[str]
) -> None:
    """Say that the OpenAPI reader reads ``components/schemas``, not ``definitions``, and what to do."""
    run_main_and_assert(
        input_path=INPUT_PATH / "petstore.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=[*extra_args, *OPTIONS],
        expected_exit=Exit.ERROR,
        capsys=capsys,
        expected_stderr_contains=MODELS_NOT_FOUND,
    )


def test_definitions_are_read_as_json_schema(output_file: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Read the ``definitions`` of the document as JSON Schema, as the message advises, without a notice."""
    run_main_and_assert(
        input_path=INPUT_PATH / "petstore.yaml",
        output_path=output_file,
        input_file_type="jsonschema",
        extra_args=OPTIONS,
        capsys=capsys,
        assert_no_stderr=True,
    )


def test_swagger_2_document_that_generates_keeps_its_output(
    output_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Change nothing for a document with a root ``swagger`` key that generates with the OpenAPI reader.

    This one holds an OpenAPI 3 response body, which ``--openapi-scopes paths`` reads.
    """
    run_main_and_assert(
        input_path=INPUT_PATH / "hybrid.yaml",
        output_path=output_file,
        input_file_type="openapi",
        extra_args=["--openapi-scopes", "paths", *OPTIONS],
        capsys=capsys,
        assert_no_stderr=True,
    )
