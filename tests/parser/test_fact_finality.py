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

from datamodel_code_generator import DataModelType, Error, PythonVersion
from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.types import DataType
from scripts import check_fact_finality, check_pass_pipeline
from tests.conftest import (
    CHECK_FACT_FINALITY_ENV,
    CHECK_FACT_FINALITY_SWEEP_ENV,
    CHECK_RENDER_PLANS_ENV,
    CHECK_STORE_WRITES_ENV,
    assert_output,
)

if TYPE_CHECKING:
    from collections.abc import Callable

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


class SealedWriteParser(JsonSchemaParser):
    """Changes every model through the store's builder once its modules are finalized: after B2, outside every pass.

    Moving a model to its own path and forgetting a target null write nothing frozen; removing a field does.
    """

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        builder = self.generation_store.builder()
        for model in self.results:
            builder.move_model(model, new_path=model.path)
            builder.clear_target_null(model.fields[0].data_type)
            builder.remove_field(model, model.fields[-1])


class RewritingParser(JsonSchemaParser):
    """Writes the lists of every model back through the store's builder once its modules are finalized."""

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        builder = self.generation_store.builder()
        for model in self.results:
            builder.set_fields(model, list(model.fields))
            builder.set_base_classes(model, list(model.base_classes))


class GrowingParser(JsonSchemaParser):
    """Gives a field a new type with a new member list once the modules are finalized, then appends to that list."""

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        model = self.results[0]
        new = DataType(data_types=[DataType(type="int")])
        self.generation_store.builder().replace_field_type(model.fields[0], new)
        store = self.generation_store
        store.enter_phase(store.phase)
        new.data_types.append(DataType(type="str"))


class NoOpMutatingParser(JsonSchemaParser):
    """Calls list mutators that leave the lists of every model as they were once its modules are finalized."""

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        for model in self.results:
            model.fields.extend([])
            model.fields.sort(key=lambda _field: 0)
            model.base_classes.sort(key=lambda _base: 0)


def _reported(report: Path) -> str:
    """Return the reported violations without what depends on the checkout."""
    lines = report.read_text(encoding="utf-8").splitlines() if report.exists() else []
    return "".join(json.dumps({key: violation[key] for key in REPORTED}) + "\n" for violation in map(json.loads, lines))


def _finality_only(monkeypatch: pytest.MonkeyPatch, value: str, *, sweep: bool = False) -> None:
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, value)
    monkeypatch.setenv(CHECK_FACT_FINALITY_SWEEP_ENV, "1" if sweep else "0")
    monkeypatch.setenv(CHECK_STORE_WRITES_ENV, "0")
    monkeypatch.setenv(CHECK_RENDER_PLANS_ENV, "0")


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


@pytest.mark.parametrize("flag", ["check", "report"])
def test_fact_finality_reports_a_frozen_write_where_it_is_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, flag: str
) -> None:
    """After a freeze point, a store operation that changes a frozen fact raises at the write (``1``) or is reported."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, {"check": "1", "report": f"report:{report}"}[flag])
    parser = SealedWriteParser(SCHEMA, formatters=[Formatter.BUILTIN])
    if flag == "check":
        with pytest.raises(
            Error,
            match=rf"^{CHECK_FACT_FINALITY_ENV}: <IMPORTS> frozen: DataModel\.fields \(FIELD_ORDER, FIELD_SET\) "
            r"of Person during IMPORTS$",
        ):
            parser.parse()
        return
    assert_output(parser.parse(), name="output")
    assert_output(_reported(report), name="violations")


def test_fact_finality_does_not_judge_a_list_write_that_changes_no_item(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writing back a list of the same items after a freeze point changes no value, so the check stays silent."""
    _finality_only(monkeypatch, "1")

    assert_output(RewritingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse())


def test_fact_finality_does_not_judge_a_list_mutator_that_changes_no_item(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty extend and a stable sort after a freeze point change no value."""
    _finality_only(monkeypatch, "1")

    assert_output(NoOpMutatingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse())


def test_fact_finality_judges_a_write_to_a_list_created_after_arming_where_it_is_made(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A list of an object created after the parse was armed is guarded once a fingerprint holds the object."""
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")

    GrowingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output(_reported(report))


def test_fact_finality_reports_known_violations_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A violation that ``KNOWN_WRITES`` lists is reported as known and does not stop the generation when raising."""
    report = tmp_path / "violations.jsonl"
    schema = SCHEMA.with_name("collapse_root_models_empty_union.json")
    _finality_only(monkeypatch, "1")
    raised = JsonSchemaParser(schema, formatters=[Formatter.BUILTIN], collapse_root_models=True).parse()
    _finality_only(monkeypatch, f"report:{report}")

    reported = JsonSchemaParser(schema, formatters=[Formatter.BUILTIN], collapse_root_models=True).parse()

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


def _decide(name: str, run: Callable[[Any, Any], None]) -> tuple[Any, ...]:
    """Return a registry of one decision ``name`` that reads defaults and writes nothing, running ``run``."""
    decl = check_pass_pipeline.PassDecl(
        name,
        check_pass_pipeline.Phase.DECIDE,
        check_pass_pipeline.Scope.GLOBAL,
        check_pass_pipeline.PassKind.TRANSFORM,
        reads=frozenset({check_pass_pipeline.Fact.DEFAULTS}),
    )
    return (check_pass_pipeline.DecisionPass({"decl": decl, "issue": "#0"}, check_pass_pipeline.DecisionWriter, run),)


READ: list[str] = []
STASHED: list[Any] = []


def _read_facts_and_render_facts(facts: Any, _writer: Any) -> None:
    """Read facts of every field through the views, then render facts: a marker, a name, a key of ``__dict__``."""
    STASHED.append(facts)
    READ.append(f"{len(set(facts.live_models))} live models")
    for model in facts.live_models:
        READ.append(f"{len(model.fields)} fields in a {type(model.fields).__name__}")
        for field in model.fields:
            data_type = field.data_type
            READ.append(
                f"{field.name}: {data_type.type}, admits None {data_type.admits_none}, same view "
                f"{data_type == field.data_type}, equals its type name {data_type == data_type.type}, "
                f"default factory {field.extras.get('default_factory')}, extras {sorted(field.extras)}"
            )
            READ.append(f"is_optional {data_type.is_optional}, kw_only {field.extras.get('kw_only')}")
            READ.append(f"forced {field.__dict__.get('_forced_field_assignment')}")
        READ.append(f"{model.class_name} named {model.reference.name}")


class RenderReadingParser(JsonSchemaParser):
    """Runs a decision that reads render facts, and reads a name through its facts once the modules are finalized."""

    _DECISION_PASSES = _decide("read_render_facts", _read_facts_and_render_facts)

    def _finalize_modules(self, *args: Any) -> None:
        super()._finalize_modules(*args)
        READ.append(f"after the decisions: {STASHED[-1].live_models[0].class_name}")


class NameReadingParser(JsonSchemaParser):
    """Runs a decision that reads the name of each model."""

    _DECISION_PASSES = _decide(
        "read_names", lambda facts, _writer: READ.extend(m.class_name for m in facts.live_models)
    )


def test_fact_finality_reports_render_facts_that_decisions_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A decision reads facts through views; each render fact it reads is reported, and so is one read later.

    A render fact is an attribute, or a key of ``extras`` or ``__dict__``, that holds no fact final at B1.
    """
    report = tmp_path / "violations.jsonl"
    _finality_only(monkeypatch, f"report:{report}")
    READ.clear()

    RenderReadingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()

    assert_output("".join(f"{line}\n" for line in READ), name="read")
    assert_output(_reported(report), name="violations")


def test_fact_finality_raises_at_the_first_render_fact_a_decision_reads(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the check raises, the first render fact a decision reads stops the generation, naming the decision."""
    _finality_only(monkeypatch, "1")
    READ.clear()

    with pytest.raises(
        Error,
        match=rf"^{CHECK_FACT_FINALITY_ENV}: read_names render-read: DataModel\.class_name \(CLASS_NAMES\) of Person "
        r"during DECIDE$",
    ):
        NameReadingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()


MODELS: list[DataModel] = []


class CapturingParser(JsonSchemaParser):
    """Keeps the models of the module it processes, for a decision that writes them."""

    def _process_single_module(self, module_: Any, models: list[DataModel], *args: Any) -> ModuleContext:
        MODELS[:] = models
        return super()._process_single_module(module_, models, *args)


def _iterate_names(form: str) -> Callable[[Any, Any], None]:
    """Return a decision that reaches the keys of the ``__dict__`` of the first field only through ``form``."""

    def run(facts: Any, _writer: Any) -> None:
        MODELS[0].fields[0].__dict__["_forced_field_assignment"] = True
        keys = facts.live_models[0].fields[0].__dict__
        READ.append(
            {
                "iter": lambda: str(list(iter(keys))),
                "len": lambda: str(len(keys)),
                "keys": lambda: str(list(keys.keys())),
                "items": lambda: str(len(list(keys.items()))),
                "contains": lambda: str("_forced_field_assignment" in keys),
            }[form]()
        )

    return run


@pytest.mark.parametrize("form", ["iter", "len", "keys", "items", "contains"])
def test_fact_finality_reports_render_facts_that_decisions_iterate(monkeypatch: pytest.MonkeyPatch, form: str) -> None:
    """Iterating, counting, listing or testing the keys of a dict attribute reports its render-fact keys."""
    _finality_only(monkeypatch, "1")
    READ.clear()

    class IteratingParser(CapturingParser):
        _DECISION_PASSES = _decide("iterate_keys", _iterate_names(form))

    with pytest.raises(Error, match=rf"^{CHECK_FACT_FINALITY_ENV}: iterate_keys render-read: .* during DECIDE$"):
        IteratingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()


def _write_default(_facts: Any, _writer: Any) -> None:
    """Write the default of a field, a fact the decision declares."""
    MODELS[0].fields[0].default = "written"


def _write_description(_facts: Any, _writer: Any) -> None:
    """Write the description of a model, a fact the decision does not declare."""
    MODELS[0].description = "written"


def _decide_writing(name: str, run: Callable[[Any, Any], None], *written: check_pass_pipeline.Fact) -> tuple[Any, ...]:
    """Return a registry of one decision ``name`` that declares writing ``written`` and runs ``run``."""
    decl = check_pass_pipeline.PassDecl(
        name,
        check_pass_pipeline.Phase.DECIDE,
        check_pass_pipeline.Scope.GLOBAL,
        check_pass_pipeline.PassKind.TRANSFORM,
        reads=frozenset({check_pass_pipeline.Fact.DEFAULTS}),
        writes=frozenset(written),
    )
    return (check_pass_pipeline.DecisionPass({"decl": decl, "issue": "#0"}, check_pass_pipeline.DecisionWriter, run),)


class DeclaredWritingParser(CapturingParser):
    """Runs a decision that writes a fact it declares during DECIDE."""

    _DECISION_PASSES = _decide_writing("write_default", _write_default, check_pass_pipeline.Fact.DEFAULTS)


def _write_required(_facts: Any, _writer: Any) -> None:
    """Make a field required: the cross-module passes write requiredness last, and the decision declares it."""
    MODELS[0].fields[1].required = True


class RequiredWritingParser(CapturingParser):
    """Runs a decision that writes requiredness, which it declares, during DECIDE."""

    _DECISION_PASSES = _decide_writing("write_required", _write_required, check_pass_pipeline.Fact.REQUIRED)


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


class UndeclaredWritingParser(CapturingParser):
    """Runs a decision that declares one fact and writes another during DECIDE."""

    _DECISION_PASSES = _decide_writing("write_description", _write_description, check_pass_pipeline.Fact.DEFAULTS)


def test_fact_finality_lets_a_decision_write_the_facts_it_declares(monkeypatch: pytest.MonkeyPatch) -> None:
    """A fact is final at B1 for every pass but the decisions, which write what they declare during DECIDE."""
    _finality_only(monkeypatch, "1")

    assert_output(DeclaredWritingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse(), name="output")


def test_fact_finality_lets_a_decision_write_requiredness(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decision writes requiredness it declares, although the module passes are the last passes to write it."""
    _finality_only(monkeypatch, "1")

    assert_output(RequiredWritingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse())


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

    def _finalize_modules(self, *args: Any) -> None:
        base = next(model for model in self.results if model.class_name == "Base")
        base.fields[0].default = "x"
        base.fields[0].invalidate_semantic_caches()
        super()._finalize_modules(*args)


class BaseFieldDroppingAfterOrderingParser(JsonSchemaParser):
    """Removes the field of a base after constructor ordering read it."""

    def _finalize_modules(self, *args: Any) -> None:
        base = next(model for model in self.results if model.class_name == "Base")
        base.fields.clear()
        super()._finalize_modules(*args)


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


def test_fact_finality_reports_a_decision_write_it_does_not_declare(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decision that writes a fact other than the ones it declares is reported as any pass is."""
    _finality_only(monkeypatch, "1")

    with pytest.raises(
        Error,
        match=rf"^{CHECK_FACT_FINALITY_ENV}: write_description undeclared: DataModel\.description \(METADATA\) "
        r"of Person during DECIDE$",
    ):
        UndeclaredWritingParser(SCHEMA, formatters=[Formatter.BUILTIN]).parse()
