"""The debug checks of ``tests/checks``: where they attach, and that they run in the Pythons a test starts."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import TYPE_CHECKING

from tests.checks.__main__ import main
from tests.checks.seams import report
from tests.conftest import CHECK_FACT_FINALITY_ENV, assert_output

if TYPE_CHECKING:
    from pathlib import Path

_EXTENSION_MAKES_A_MEMO_STALE = """
from datamodel_code_generator import Error
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser


class LateUnionAlias(JsonSchemaParser):
    def _check_model_inheritance(self, contexts):
        super()._check_model_inheritance(contexts)
        inner = contexts[0].models[0].fields[0].data_type.data_types[1]
        vars(inner)["alias"] = inner.type_hint


SCHEMA = (
    '{"title": "M", "type": "object", "properties": {"v": {"anyOf": [{"type": "integer"},'
    ' {"anyOf": [{"type": "string"}, {"type": "boolean"}]}]}}}'
)
try:
    LateUnionAlias(SCHEMA, formatters=[Formatter.BUILTIN]).parse()
except Error as error:
    print(error)
"""


def test_check_seams() -> None:
    """Every seam is wrapped, and no module of the package holds a seam function the table does not list."""
    assert_output(report())


def test_checks_run_in_a_python_a_test_starts() -> None:
    """A Python that a test starts runs the checks the variables turn on, as the test process does."""
    result = subprocess.run(
        [sys.executable, "-c", _EXTENSION_MAKES_A_MEMO_STALE],
        capture_output=True,
        check=False,
        text=True,
        env={**os.environ, CHECK_FACT_FINALITY_ENV: "0"},
    )
    assert_output(f"{result.stdout}{result.stderr}")


def test_checked_run_of_the_command_line(tmp_path: Path) -> None:
    """``python -m tests.checks`` runs ``datamodel-codegen`` with the checks installed."""
    schema = tmp_path / "schema.json"
    schema.write_text('{"title": "M", "type": "object", "properties": {"v": {"type": "string"}}}', encoding="utf-8")
    output = tmp_path / "model.py"
    args = ["--input", str(schema), "--input-file-type", "jsonschema", "--output", str(output), "--disable-timestamp"]
    status = main([*args, "--formatters", "builtin"])
    assert_output(f"exit status {status}\n{output.read_text(encoding='utf-8')}")
