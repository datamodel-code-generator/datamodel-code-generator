"""The finality check that ``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY`` turns on, seen through custom parsers.

A module pass changes only the models of its module, and the base fields that constructor ordering read stay as they
were until B2; a change outside module passes is taken as it is. The violations of today's pipeline are listed with a
witness, reported and never raised. What each pass may write is heki's check
(``tests/test_build_heki_contracts_script.py``).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from datamodel_code_generator import DataModelType, InputFileType, PythonVersion, generate
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.parser.openapi import OpenAPIParser
from scripts import check_fact_finality, check_pass_pipeline
from tests.checks.areas import CheckFailure
from tests.conftest import CHECK_FACT_FINALITY_ENV, CHECK_RENDER_PLANS_ENV, assert_output

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.parser.base import ModuleContext

SCHEMA = Path(__file__).parents[1] / "data" / "jsonschema" / "person.json"
MODULAR = Path(__file__).parents[1] / "data" / "openapi" / "modular.yaml"
"""An API whose models generate into several modules."""
REPORTED = ("pass_name", "rule", "row", "facts", "model", "phase", "known")
DECISIONS: dict[str, check_pass_pipeline.PassDecl] = {}
"""The declaration of each decision the parsers below run, by name: ``_finality_only`` adds them to the registry."""


class OtherModuleParser(OpenAPIParser):
    """Describes every model of the parse while a module pass of one module asks whether a model is a root model."""

    def _is_root_model(self, model: DataModel) -> bool:
        if self.generation_store.phase.name == "MODULES":
            for each in self.results:
                each.description = "described"
        return super()._is_root_model(model)


class LooseParser(OpenAPIParser):
    """Describes every model of the parse directly, outside every pass, before a module is processed."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in self.results:
            model.description = f"described before {module_}"
        return super()._process_single_module(module_, models, *args)


class InvalidatingParser(OpenAPIParser):
    """Titles every field of the parse in place, outside every pass, and invalidates it: no guarded write sees it."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        for model in self.results:
            for field in model.fields:
                field.extras["title"] = f"titled before {module_}"
                field.invalidate_semantic_caches()
        return super()._process_single_module(module_, models, *args)


def _reported(report: Path) -> str:
    """Return the reported violations without what depends on the checkout."""
    lines = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    return "".join(json.dumps({key: violation[key] for key in REPORTED}) + "\n" for violation in map(json.loads, lines))


def _finality_only(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, value)
    monkeypatch.setenv(CHECK_RENDER_PLANS_ENV, "0")
    for name, decl in DECISIONS.items():
        monkeypatch.setitem(check_pass_pipeline.DECISION_DECLS, name, decl)


@pytest.mark.parametrize("flag", ["check", "report", "off"])
def test_fact_finality_reports_a_module_pass_that_writes_another_module(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """A module pass that changes a model of another module raises (``1``), is reported or passes (``0``)."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, {"check": "1", "report": f"report:{report}", "off": "0"}[flag])
    parser = OtherModuleParser(MODULAR, formatters=[Formatter.BUILTIN])
    if flag == "check":
        with pytest.raises(
            CheckFailure,
            match=rf"^{CHECK_FACT_FINALITY_ENV}: override_required_field other-module: DataModel\.description "
            r"\(METADATA\) of \w+ during MODULES$",
        ):
            parser.parse()
        return
    modules = parser.parse()
    assert_output("".join(f"# {'/'.join(name)}\n{result.body}\n" for name, result in modules.items()), name="output")
    assert_output(_reported(report), name="violations")


@pytest.mark.parametrize("parser_type", [LooseParser, InvalidatingParser])
def test_fact_finality_blames_no_pass_for_a_change_outside_every_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parser_type: type[OpenAPIParser]
) -> None:
    """A change outside every pass, seen by a guard or noted by its invalidation, is no module pass's change."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")

    parser_type(MODULAR, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_known_violations_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A violation that ``KNOWN_WRITES`` lists is reported as known and does not stop the generation when raising."""
    report = tmp_path / "violations.jsonl"
    schema = Path(__file__).parents[1] / "data/openapi/schema_intersection/inherited_override_null_oas31.yaml"
    options: dict[str, Any] = {
        "input_file_type": InputFileType.OpenAPI,
        "output_model_type": DataModelType.MsgspecStruct,
        "formatters": [Formatter.BUILTIN],
    }
    _finality_only(monkeypatch, "1")
    raised = generate(schema, **options)
    _finality_only(monkeypatch, f"report:{report}")

    reported = generate(schema, **options)

    assert_output(raised, name="raised")
    assert_output(reported, name="reported")
    assert_output(_reported(report), name="violations")


@pytest.mark.allow_direct_assert("a soundness gate fails on its own, never through a stored output a fix run accepts")
def test_known_violations_are_made_by_their_witnesses(capsys: pytest.CaptureFixture[str]) -> None:
    """The declarations are sound and every known violation is still made by its witness, so the list only shrinks."""
    assert check_fact_finality.main([]) == 0, capsys.readouterr().err
    assert check_fact_finality.main(["--witnesses"]) == 0, capsys.readouterr().err


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


def _decide_writing(name: str, run: Callable[[Any, Any], None], *written: check_pass_pipeline.Fact) -> tuple[Any, ...]:
    """Return a registry of one decision ``name`` that declares writing ``written`` and runs ``run``."""
    DECISIONS[name] = check_pass_pipeline.PassDecl(
        name,
        check_pass_pipeline.Phase.DECIDE,
        check_pass_pipeline.Scope.GLOBAL,
        check_pass_pipeline.PassKind.TRANSFORM,
        reads=frozenset({check_pass_pipeline.Fact.DEFAULTS}),
        writes=frozenset(written),
    )
    return (check_pass_pipeline.DecisionPass({"name": name, "issue": "#0"}, run),)


MODELS: list[DataModel] = []


class CapturingParser(JsonSchemaParser):
    """Keeps the models of the module it processes, for a decision that writes them."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        MODELS[:] = models
        return super()._process_single_module(module_, models, *args)


def _default_base_field(_facts: Any, _writer: Any) -> None:
    """Give the field of the base a default, which a field of the subclass without one then follows."""
    base = next(model for model in MODELS if model.class_name == "Base")
    base.fields[0].required = False
    base.fields[0].default = "x"


class BaseDefaultingParser(CapturingParser):
    """Runs a decision that makes a base field optional with a default, writing facts it declares."""

    _DECISION_PASSES = _decide_writing(
        "default_base_field", _default_base_field, check_pass_pipeline.Fact.DEFAULTS, check_pass_pipeline.Fact.REQUIRED
    )


def test_constructor_order_follows_a_decided_base_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Constructor ordering runs after the decisions: a default a decision gives a base field orders the subclass."""
    _finality_only(monkeypatch, "1")
    schema = tmp_path / "inheritance.json"
    schema.write_text(
        json.dumps({
            "$defs": {
                "Base": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
                "Child": {
                    "allOf": [
                        {"$ref": "#/$defs/Base"},
                        {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
                    ]
                },
            },
            "$ref": "#/$defs/Child",
        }),
        encoding="utf-8",
    )
    model_types = get_data_model_types(DataModelType.DataclassesDataclass, target_python_version=PythonVersion.PY_311)

    assert_output(
        BaseDefaultingParser(
            schema,
            data_model_type=model_types.data_model,
            data_model_root_type=model_types.root_model,
            data_model_field_type=model_types.field_model,
            data_type_manager_type=model_types.data_type_manager,
            dump_resolve_reference_action=model_types.dump_resolve_reference_action,
            target_python_version=PythonVersion.PY_311,
            formatters=[Formatter.BUILTIN],
        ).parse()
    )


INHERITANCE_SCHEMA = {
    "$defs": {
        "Base": {"type": "object", "properties": {"a": {"type": "string"}}, "required": ["a"]},
        "Child": {
            "allOf": [
                {"$ref": "#/$defs/Base"},
                {"type": "object", "properties": {"b": {"type": "string"}}, "required": ["b"]},
            ]
        },
    },
    "$ref": "#/$defs/Child",
}


class BaseDefaultingAfterOrderingParser(JsonSchemaParser):
    """Gives the field of a base a default after constructor ordering read it."""

    def _finalize_modules(self, *args: Any, **kwargs: object) -> None:
        base = next(model for model in self.results if model.class_name == "Base")
        base.fields[0].default = "x"
        base.fields[0].invalidate_semantic_caches()
        super()._finalize_modules(*args, **kwargs)


class BaseFieldDroppingAfterOrderingParser(JsonSchemaParser):
    """Removes the field of a base after constructor ordering read it."""

    def _finalize_modules(self, *args: Any, **kwargs: object) -> None:
        base = next(model for model in self.results if model.class_name == "Base")
        base.fields.clear()
        super()._finalize_modules(*args, **kwargs)


@pytest.mark.parametrize("parser_type", [BaseDefaultingAfterOrderingParser, BaseFieldDroppingAfterOrderingParser])
def test_fact_finality_reports_a_base_field_that_changes_after_constructor_ordering(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, parser_type: type[JsonSchemaParser]
) -> None:
    """A field of a base that constructor ordering read, or the set of them, must not change before the imports."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")
    schema = tmp_path / "inheritance.json"
    schema.write_text(json.dumps(INHERITANCE_SCHEMA), encoding="utf-8")
    model_types = get_data_model_types(DataModelType.DataclassesDataclass, target_python_version=PythonVersion.PY_311)

    parser_type(
        schema,
        data_model_type=model_types.data_model,
        data_model_root_type=model_types.root_model,
        data_model_field_type=model_types.field_model,
        data_type_manager_type=model_types.data_type_manager,
        dump_resolve_reference_action=model_types.dump_resolve_reference_action,
        target_python_version=PythonVersion.PY_311,
        formatters=[Formatter.BUILTIN],
    ).parse()

    assert_output(_reported(report))
