"""The finality check that ``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` turns on, seen through custom parsers.

Each pass changes only the facts its declaration lists, on the models its scope allows, before they are final. A write
outside every pass is reported where the next comparison finds it; the violations of today's pipeline are listed with a
witness, reported and never raised.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import Error
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from scripts import check_fact_finality
from tests.conftest import (
    CHECK_FACT_FINALITY_ENV,
    CHECK_FACT_FINALITY_SWEEP_ENV,
    CHECK_STORE_WRITES_ENV,
    assert_output,
)

if TYPE_CHECKING:
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser.base import ModuleContext

SCHEMA = Path(__file__).parents[1] / "data" / "jsonschema" / "person.json"
REPORTED = ("pass_name", "rule", "row", "facts", "model", "phase", "known")


class DiagnosingParser(JsonSchemaParser):
    """Describes every model while it reports its parse diagnostics, a pass that writes no fact."""

    def _report_parse_diagnostics(self) -> None:
        super()._report_parse_diagnostics()
        for model in self.results:
            model.description = "described"


class LooseParser(JsonSchemaParser):
    """Describes every model of a module directly, outside every pass, before the module is processed."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            model.description = "described"
        return super()._process_single_module(module_, models, *args)


class InvalidatingParser(JsonSchemaParser):
    """Titles every field of a module in place, outside every pass, and invalidates it: no guarded write sees it."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in models:
            for field in model.fields:
                field.extras["title"] = "titled"
                field.invalidate_semantic_caches()
        return super()._process_single_module(module_, models, *args)


class RefinalizingParser(JsonSchemaParser):
    """Finalizes the modules twice, so that the passes of the second time run in a later phase than they declare."""

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        super()._finalize_modules(*args)


class RebasingParser(JsonSchemaParser):
    """Names a custom base class for each model while the generic base class is applied, after that fact is final."""

    def _is_root_model(self, model: DataModel) -> bool:
        if self.generation_store.phase.name == "CROSS_MODULE":
            model.custom_base_class = "custom.Base"
        return super()._is_root_model(model)


def _reported(report: Path) -> str:
    """Return the reported violations without what depends on the checkout."""
    lines = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    return "".join(json.dumps({key: violation[key] for key in REPORTED}) + "\n" for violation in map(json.loads, lines))


def _finality_only(monkeypatch: pytest.MonkeyPatch, value: str, *, sweep: bool = False) -> None:
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, value)
    monkeypatch.setenv(CHECK_FACT_FINALITY_SWEEP_ENV, "1" if sweep else "0")
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, "0")


@pytest.mark.parametrize("flag", ["check", "report", "off"])
def test_fact_finality_reports_a_write_the_pass_does_not_declare(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """A pass that writes a fact its declaration does not list raises (``1``), is reported or passes (``0``)."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, {"check": "1", "report": f"report:{report}", "off": "0"}[flag])
    parser = DiagnosingParser(SCHEMA, formatters=[Formatter.BUILTIN])
    if flag == "check":
        with pytest.raises(
            Error,
            match=rf"^{CHECK_FACT_FINALITY_ENV}: report_parse_diagnostics undeclared: DataModel\.description "
            r"\(METADATA\) of Person during SETTLE$",
        ):
            parser.parse()
        return
    assert_output(parser.parse(), name="output")
    assert_output(_reported(report), name="violations")


@pytest.mark.parametrize("sweep", [False, True], ids=["touched", "sweep"])
def test_fact_finality_reports_a_write_outside_every_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sweep: bool
) -> None:
    """A write that no pass makes is reported when the next pass starts, whether global passes compare every model."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}", sweep=sweep)

    LooseParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_an_invalidated_change_outside_every_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A change outside every pass that only its invalidation notes is reported when the next pass starts."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")

    InvalidatingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_passes_in_a_later_phase(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Passes that a parser subclass runs again after their phase are reported as running in the wrong phase."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")

    RefinalizingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_a_write_after_the_phase_the_table_names(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A declared write in a later phase than ``final_after`` of its row is reported, before the row is frozen."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")

    RebasingParser(
        SCHEMA, formatters=[Formatter.BUILTIN], use_generic_base_class=True, allow_population_by_field_name=True
    ).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_known_violations_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A violation that ``KNOWN_WRITES`` lists is reported as known and does not stop the generation when raising."""
    report = tmp_path / "violations.jsonl"
    schema = SCHEMA.with_name("ref_type_has_null.json")
    _finality_only(monkeypatch, "1")
    raised = JsonSchemaParser(schema, formatters=[Formatter.BUILTIN], use_union_operator=True).parse()
    _finality_only(monkeypatch, f"report:{report}")

    reported = JsonSchemaParser(schema, formatters=[Formatter.BUILTIN], use_union_operator=True).parse()

    assert_output(raised, name="raised")
    assert_output(reported, name="reported")
    assert_output(_reported(report), name="violations")


def test_known_violations_are_made_by_their_witnesses(capsys: pytest.CaptureFixture[str]) -> None:
    """The declarations are sound and every known violation is still made by its witness, so the list only shrinks."""
    exit_statuses = (check_fact_finality.main([]), check_fact_finality.main(["--witnesses"]))

    assert_output(f"exit statuses: {exit_statuses}\n{capsys.readouterr().err}")


def test_declaration_problems_report_unsound_declarations() -> None:
    """A repeated name, a phase that goes back, a repair without its removal and a check that writes are reported."""
    first, *_, last = check_fact_finality.PASS_DECLS
    problems = check_fact_finality.declaration_problems([
        last,
        first,
        replace(first, kind=check_fact_finality.PassKind.REPAIR),
        replace(last, name="checking", kind=check_fact_finality.PassKind.CHECK),
    ])

    assert_output("".join(f"{problem}\n" for problem in problems))


def test_witness_problems_report_stale_entries_and_broken_witnesses() -> None:
    """An entry its witness no longer makes is stale; a witness that fails stops the check."""
    known = check_fact_finality.KNOWN_WRITES[0]
    stale = replace(known, witness="--input tests/data/jsonschema/person.json --input-file-type jsonschema")

    problems = check_fact_finality.witness_problems([stale])
    with pytest.raises(RuntimeError) as error:
        check_fact_finality.witness_problems([replace(known, witness="--input missing.json")])

    assert_output("".join(f"{problem}\n" for problem in [*problems, str(error.value)]))
