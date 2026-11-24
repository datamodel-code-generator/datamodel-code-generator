"""The debug checks of ``tests/checks``: where they attach, and that they run in the Pythons a test starts."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import count
from pathlib import Path
from typing import Any

import pytest

from datamodel_code_generator import InputFileType, generate
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.parser.openapi import OpenAPIParser
from tests.checks.__main__ import main
from tests.checks.areas import Area, CheckFailure, Mode
from tests.checks.seams import report
from tests.conftest import (
    CHECK_FACT_FINALITY_ENV,
    CHECK_NAMING_ENV,
    CHECK_PASS_TRIGGERS_ENV,
    CHECK_RENDER_MEMOS_ENV,
    assert_output,
)

_EXTENSION_MAKES_A_MEMO_STALE = """
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from tests.checks.areas import CheckFailure


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
except CheckFailure as error:
    print(error)
"""


_APPENDS_ONCE_STARTED = """
import sys
from pathlib import Path

from tests.checks.areas import Area, Mode

print("ready", flush=True)
sys.stdin.read()
mode = Mode(Area.FACT_FINALITY, Path(sys.argv[1]))
for index in range(int(sys.argv[3])):
    mode.record("appended", writer=sys.argv[2], index=index, padding="x" * 4096)
"""
"""Says it is ready, then appends lines to a report once every writer has started: when the test closes the pipe of
its standard input."""


def test_concurrent_appends_write_whole_lines(tmp_path: Path) -> None:
    """Processes and threads that append to one report at once each write every line of theirs whole (#7374).

    The determinism tests ran two generator processes that reported into the file of their test process. The CRT's
    append on Windows is a seek to the end and then a write, so two writers wrote at the same offset and broke their
    lines.

    Each process reads a start pipe of its own (#7404). Windows runs the calls on a synchronous pipe one at a time,
    across every handle inherited from it: while one process waited in its read of a shared start pipe, the next one
    stopped in its startup's ``GetFileType`` on that pipe before it said it was ready, and the test waited for it.
    """
    report, lines = tmp_path / "report.jsonl", 100
    starts, readies = [os.pipe() for _ in range(4)], [os.pipe() for _ in range(4)]
    processes = [
        subprocess.Popen(
            [sys.executable, "-c", _APPENDS_ONCE_STARTED, str(report), f"process {number}", str(lines)],
            cwd=Path(__file__).parents[2],
            stdin=start_read,
            stdout=ready_write,
        )
        for number, ((start_read, _), (_, ready_write)) in enumerate(zip(starts, readies, strict=True))
    ]
    for (start_read, _), (_, ready_write) in zip(starts, readies, strict=True):
        os.close(start_read)
        os.close(ready_write)
    mode = Mode(Area.FACT_FINALITY, report)

    def append(writer: str) -> None:
        for index in range(lines):
            mode.record("appended", writer=writer, index=index, padding="x" * 4096)

    for ready_read, _ in readies:
        os.read(ready_read, 64)
        os.close(ready_read)
    for _, start_write in starts:
        os.close(start_write)
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(append, [f"thread {number}" for number in range(4)]))
    statuses = [process.wait() for process in processes]
    records = [json.loads(line) for line in report.read_text(encoding="utf-8").splitlines()]
    counts = Counter(record["writer"] for record in records)
    assert_output(f"exit statuses {statuses}\n" + "".join(f"{writer}: {counts[writer]}\n" for writer in sorted(counts)))


def test_check_seams() -> None:
    """Every seam is wrapped, and no module of the package holds a seam function the table does not list."""
    assert_output(report())


@pytest.mark.value_checks
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


def _run_with_check_mode(tmp_path: Path, value: str, monkeypatch: pytest.MonkeyPatch) -> int:
    schema = tmp_path / "schema.json"
    schema.write_text('{"title": "M", "type": "object", "properties": {"v": {"type": "string"}}}', encoding="utf-8")
    monkeypatch.setenv(CHECK_RENDER_MEMOS_ENV, value)
    args = ["--input", str(schema), "--input-file-type", "jsonschema", "--output", str(tmp_path / "model.py")]
    return main([*args, "--disable-timestamp", "--formatters", "builtin"])


def test_check_area_turned_off(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parse with an area set to 0 runs without that area's check."""
    status = _run_with_check_mode(tmp_path, "0", monkeypatch)
    assert_output(f"exit status {status}\n{(tmp_path / 'model.py').read_text(encoding='utf-8')}")


def test_check_area_with_unknown_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A value other than 0, 1 or report:<path> fails the run with an error that names the variable."""
    status = _run_with_check_mode(tmp_path, "report:", monkeypatch)
    assert_output(f"exit status {status}\n{capsys.readouterr().err.splitlines()[-1]}")


_RENAMED_DEFINITION = (
    '{"title": "M", "type": "object", "properties": {"pet": {"$ref": "#/definitions/Pet"}},'
    ' "definitions": {"Pet": {"type": "object", "properties": {"name": {"type": "string"}}}}}'
)


class _RenamingParser(JsonSchemaParser):
    """A parser extension that renames a model outside the resolver while L1 runs."""

    def parse_raw(self) -> None:
        super().parse_raw()
        self.model_resolver.references["#/definitions/Pet"].name = "Animal"


@pytest.mark.value_checks
def test_naming_check_reports_a_name_no_naming_call_gives() -> None:
    """A name that no naming call of the resolver gives fails the parse when ``name_models`` starts.

    The naming check is on (`1`, as the test suite sets it); the replay of the calls names the definition ``Pet``.
    """
    with pytest.raises(
        CheckFailure,
        match=(
            rf"^{CHECK_NAMING_ENV}: names the resolver's naming calls do not explain: "
            r"#/definitions/Pet: 'Animal' \(asked None\) where its calls give 'Pet' \(asked None\)$"
        ),
    ):
        _RenamingParser(_RENAMED_DEFINITION, formatters=[Formatter.BUILTIN]).parse()


def test_naming_check_replays_the_names_a_custom_generator_gave() -> None:
    """A class name generator that counts its calls names each model once: the replay reads the names it gave."""
    counter = count(1)
    assert_output(
        str(
            JsonSchemaParser(
                _RENAMED_DEFINITION,
                custom_class_name_generator=lambda name: f"{name}{next(counter)}",
                formatters=[Formatter.BUILTIN],
            ).parse()
        )
    )


def test_naming_check_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the naming check set to 0, the parse writes the name the extension gave and records no naming call."""
    monkeypatch.setenv(CHECK_NAMING_ENV, "0")
    assert_output(str(_RenamingParser(_RENAMED_DEFINITION, formatters=[Formatter.BUILTIN]).parse()))


class _EnumLiteralTags(OpenAPIParser):
    """A parser extension that tags discriminated variants by enum member literals without the option that does."""

    def _create_discriminator_data_type(
        self, enum_source: Any, discriminator_values: Any, discriminator_model: Any, imports: Any
    ) -> Any:
        self.use_enum_values_in_discriminator = True
        try:
            return super()._create_discriminator_data_type(
                enum_source, discriminator_values, discriminator_model, imports
            )
        finally:
            self.use_enum_values_in_discriminator = False


_ENUM_TAGGED_VARIANTS = Path(__file__).parents[1] / "data/openapi/discriminator_enum_modules.yaml"
"""Variants in the module ``pets`` whose tag field is typed by an enum of the root module."""


def test_trigger_check_reports_a_pass_that_writes_where_its_trigger_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """A pass that rewrites what its trigger does not name fails the parse when the trigger check runs it anyway.

    The trigger of ``import_enum_member_literal_classes`` names the option and the consts that make enum member
    literals; the extension makes them without either, so the pass, run while its trigger is false, imports their
    enum into the module of the variants.
    """
    monkeypatch.setenv(CHECK_PASS_TRIGGERS_ENV, "1")
    with pytest.raises(CheckFailure) as raised:
        _EnumLiteralTags(_ENUM_TAGGED_VARIANTS, formatters=[Formatter.BUILTIN]).parse()
    assert_output(f"{raised.value}\n")


def test_trigger_check_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """With the trigger check set to 0, the pass whose trigger is false is skipped, as outside the tests.

    The variants import the enum for the field of their base already, so the output is the one the pass would leave.
    """
    monkeypatch.setenv(CHECK_PASS_TRIGGERS_ENV, "0")
    modules = _EnumLiteralTags(_ENUM_TAGGED_VARIANTS, formatters=[Formatter.BUILTIN]).parse()
    assert_output("".join(f"# {'/'.join(name)}\n{result.body}\n" for name, result in modules.items()))


@pytest.mark.parametrize(
    "rendered",
    [
        pytest.param(
            "class Model:\n    x: int\n\n    def method(self) -> None:\n        class Inner:\n            x = 1\n",
            id="class-in-a-method",
        ),
        pytest.param("class Model:\n    x: int\n\n    class Config:\n        x = 1\n", id="class-in-a-class-body"),
    ],
)
def test_field_lines_read_only_module_level_class_bodies(rendered: str) -> None:
    """A class nested in a method or a class body binds its own names, never the fields of the model around it."""
    from tests.checks.template_assignments import field_lines

    lines = [
        f"{line.name}: {'no value' if line.value is None else 'with a value'}" for line in field_lines(rendered) or ()
    ]
    assert_output("\n".join(lines) + "\n")


_REFUSED_WHERE_YAML_NUMBERS_ARE_TEXT = """
openapi: 3.0.3
info: {title: t, version: "1"}
paths: {}
components:
  schemas:
    Pet:
      type: object
      properties:
        at: {type: string, pattern: 00:00:00.00}
"""


def test_a_failing_check_is_no_error_that_a_generation_catches() -> None:
    """A violation of every area fails the generation with its message, however the generation retries.

    The schema is refused with its YAML 1.1-only number read as a number, so the generation parses again with it read
    as text, and a package ``Error`` of that second parse is swallowed: the refusal of the first parse then stands. The
    class name generator makes each area's violation in the second parse, where it names the first class.
    """
    lines = []
    for area in Area:

        def violates(_name: str, area: Area = area) -> str:
            Mode(area, None).violation("seeded failure")
            raise NotImplementedError

        with pytest.raises(CheckFailure) as raised:
            generate(
                _REFUSED_WHERE_YAML_NUMBERS_ARE_TEXT,
                input_file_type=InputFileType.OpenAPI,
                custom_class_name_generator=violates,
                formatters=[Formatter.BUILTIN],
            )
        lines.append(str(raised.value))
    assert_output("\n".join(lines) + "\n")
