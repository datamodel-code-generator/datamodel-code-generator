"""The passes of ``Parser.parse()``: which run, in which order, for a few inputs that reach most of them.

A trace lists every pass that runs, named as in ``parser/_pass_decls.py``, with a ``-- module`` line where the pipeline
starts processing each output module; ``scripts/check_pass_pipeline.py`` records it.
"""

from __future__ import annotations

from itertools import starmap
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

import pytest

from datamodel_code_generator.format import Formatter
from datamodel_code_generator.model.pydantic_v2 import BaseModel
from datamodel_code_generator.parser.jsonschema import JsonSchemaParser
from datamodel_code_generator.parser.openapi import OpenAPIParser
from scripts import check_pass_pipeline
from tests.conftest import CHECK_FACT_FINALITY_ENV, assert_output

if TYPE_CHECKING:
    from collections.abc import Callable

DATA = Path(__file__).parents[1] / "data"

GOLDEN_RUNS = {
    "jsonschema": "--input tests/data/jsonschema/person.json --input-file-type jsonschema",
    "openapi-modules": (
        "--input tests/data/openapi/modular.yaml --input-file-type openapi --collapse-root-models --reuse-model "
        "--use-annotated --aliases tests/data/openapi/aliases.json --use-default-factory-for-optional-nested-models "
        "--use-root-model-sequence-interface --generate-schema-validators"
    ),
    "msgspec-tree-reuse": (
        "--input tests/data/openapi/discriminator.yaml --input-file-type openapi --output-model-type msgspec.Struct "
        "--collapse-root-models --reuse-model --reuse-scope tree"
    ),
    "typed-dict-import-overrides": (
        "--input tests/data/jsonschema/datetime.json --input-file-type jsonschema --output-model-type typing.TypedDict "
        "--target-python-version 3.10 "
        """--import-overrides '{"TypedDict": "my_project.typing_compat", "NotRequired": "my_project.typing_compat"}'"""
    ),
    "renamed-sequence-roots": (
        "--input tests/data/jsonschema/root_sequence_final/reuse.json --input-file-type jsonschema "
        "--use-root-model-sequence-interface --reuse-model --collapse-reuse-models --generate-schema-validators"
    ),
}
COVERAGE_RUNS = (
    (
        "--input tests/data/jsonschema/person.json --input-file-type jsonschema "
        "--custom-template-dir tests/data/templates --emit-model-metadata {tmp}/metadata.json"
    ),
    (
        "--input tests/data/jsonschema/type_overrides_target.json --input-file-type jsonschema "
        "--output-model-type typing.TypedDict --target-python-version 3.10 "
        """--type-overrides '{"Model.c": "typing.LiteralString", "Model.missing": "enum.StrEnum"}'"""
    ),
    (
        "--input tests/data/jsonschema/typed_dict_allof_extra_items.json --input-file-type jsonschema "
        "--output-model-type typing.TypedDict --target-python-version 3.10"
    ),
    (
        "--input tests/data/openapi/nullable_map_value_types.yaml --input-file-type openapi "
        "--use-typed-nullable-map-values"
    ),
    (
        "--input tests/data/jsonschema/serialized_decimal_default_collision.json --input-file-type jsonschema "
        "--deserialize-default-values decimal"
    ),
    (
        "--input tests/data/jsonschema/default_factory_nested_model_reference_cycle.json --input-file-type jsonschema "
        "--output-model-type pydantic_v2.dataclass --use-default-factory-for-optional-nested-models "
        "--strip-default-none --order-constructor-fields-by written-defaults"
    ),
    (
        "--input tests/data/jsonschema/object_maps/collapsed_rule_reference_root_modules.json --input-file-type "
        "jsonschema --generate-schema-validators --collapse-root-models --collapse-root-models-name-strategy parent"
    ),
    (
        "--input tests/data/openapi/external_ref_mapping_all_of/api.yaml --input-file-type openapi "
        "--external-ref-mapping shared.yaml=mapped.models"
    ),
    (
        "--input tests/data/openapi/collapse_root_models_name_strategy/two_holders.yaml --input-file-type openapi "
        "--collapse-root-models --collapse-root-models-name-strategy parent --field-type-collision-strategy rename-type"
    ),
)
"""Runs that reach the passes no golden run reaches, each under the condition it names."""


class OutsideModel(BaseModel):
    """A model class defined outside the package, whose imports a module cannot keep cached."""


@pytest.mark.parametrize("arguments", GOLDEN_RUNS.values(), ids=GOLDEN_RUNS)
def test_pipeline_trace_matches_golden(arguments: str) -> None:
    """The passes run in the stored order, under the conditions each run sets."""
    assert_output(check_pass_pipeline.pass_trace(f"{arguments} --disable-timestamp"))


def test_every_declared_pass_runs() -> None:
    """Some golden or coverage run runs each declared pass, so that every table entry is executed."""
    traces = [check_pass_pipeline.pass_trace(arguments) for arguments in [*GOLDEN_RUNS.values(), *COVERAGE_RUNS]]
    outside = JsonSchemaParser(
        DATA / "jsonschema" / "person.json", data_model_type=OutsideModel, formatters=[Formatter.BUILTIN]
    )
    traces.append("\n".join(check_pass_pipeline.call_trace(outside.parse)))

    assert_output("".join(f"{name}\n" for name in check_pass_pipeline.unrun_passes(traces)) or "every pass runs\n")


def test_pipeline_tables_follow_the_rules(capsys: pytest.CaptureFixture[str]) -> None:
    """The tables run the declarations in order, in their phases and scopes, ordered by the order rule.

    The provisional reads are the explicit edges that run a reader before a writer of what it reads.
    """
    exit_status = check_pass_pipeline.main([])

    assert_output(f"exit status: {exit_status}\n{capsys.readouterr().err}", name="checks")
    assert_output("".join(f"{read}\n" for read in check_pass_pipeline.provisional_reads()), name="provisional_reads")


WAVE_B_READS = {
    "G12b": {"REQUIRED", "DEFAULTS", "NULLABLE", "DISCRIMINATOR_TAGS", "FIELD_TYPES"},
    "G06": {"FIELD_TYPES", "MODEL_KIND", "BASES", "DISCRIMINATOR_TAGS", "CONSTRAINTS"},
    "G03b": {"FIELD_TYPES", "CONSTRAINTS", "TYPE_OVERRIDES"},
    "G08": {"DEFAULTS", "REQUIRED", "FIELD_TYPES", "BASES", "DISCRIMINATOR_TAGS", "TYPE_OVERRIDES"},
}
WAVE_B_WRITES = {
    "declared": {
        "G12b": {"NULLABLE", "DEFAULTS"},
        "G06": {"CONTAINERS", "DEFAULTS"},
        "G03b": {"CONSTRAINTS"},
        "G08": {"DEFAULTS"},
    },
    "measured": {
        "G12b": {"NULLABLE", "DEFAULTS", "REQUIRED"},
        "G06": {"FIELD_TYPES", "DEFAULTS"},
        "G03b": {"CONSTRAINTS", "FIELD_TYPES"},
        "G08": {"DEFAULTS", "REQUIRED"},
    },
}
WAVE_B_AFTER = {
    "declared": {
        "G06": [("G12b", "unique-item containers are decided on the final nullability and defaults")],
        "G03b": [("G06", "constraints apply to the final container type")],
        "G08": [("G06", "object defaults are dropped after the container decision")],
    },
    "measured": {
        "G06": [("G12b", "unique-item containers are decided on the final nullability and defaults")],
        "G03b": [
            ("G12b", "constrained forms are decided after the const nullability"),
            ("G06", "constraints apply to the final container type"),
        ],
    },
}


def _decl(name: str, reads: set[str], writes: set[str], after: list[tuple[str, str]]) -> check_pass_pipeline.PassDecl:
    return check_pass_pipeline.PassDecl(
        name,
        check_pass_pipeline.Phase.DECIDE,
        check_pass_pipeline.Scope.GLOBAL,
        check_pass_pipeline.PassKind.TRANSFORM,
        reads=frozenset(check_pass_pipeline.Fact[fact] for fact in reads),
        writes=frozenset(check_pass_pipeline.Fact[fact] for fact in writes),
        after=tuple(starmap(check_pass_pipeline.After, after)),
    )


def _wave_b(writes: str, *, edges: bool) -> list[check_pass_pipeline.PassDecl]:
    return [
        _decl(name, reads, WAVE_B_WRITES[writes][name], WAVE_B_AFTER[writes].get(name, []) if edges else [])
        for name, reads in WAVE_B_READS.items()
    ]


ORDER_CASES = {
    "wave-b-declared-without-edges": (_wave_b("declared", edges=False), True),
    "wave-b-declared": (_wave_b("declared", edges=True), True),
    "wave-b-measured-without-edges": (_wave_b("measured", edges=False), True),
    "wave-b-measured": (_wave_b("measured", edges=True), True),
    "edge-to-a-later-pass-of-a-table": (
        [
            _decl("a", {"DEFAULTS"}, {"REQUIRED"}, [("b", "reads what b writes")]),
            _decl("b", {"REQUIRED"}, {"DEFAULTS"}, []),
        ],
        False,
    ),
    "edge-to-an-unknown-or-unrelated-pass": (
        [
            _decl("a", {"DEFAULTS"}, {"REQUIRED"}, []),
            _decl("b", {"BASES"}, {"BASES"}, [("a", "decorative"), ("missing", "names no pass")]),
        ],
        False,
    ),
    "cycle": (
        [
            _decl("a", {"DEFAULTS"}, {"REQUIRED"}, [("b", "a first")]),
            _decl("b", {"REQUIRED"}, {"DEFAULTS"}, [("a", "b first")]),
        ],
        True,
    ),
}


@pytest.mark.parametrize(("decls", "computed"), ORDER_CASES.values(), ids=ORDER_CASES)
def test_check_order(decls: list[check_pass_pipeline.PassDecl], computed: bool) -> None:
    """The order rule orders the passes, reports conflicting pairs no edge orders, and records provisional reads."""
    result = check_pass_pipeline.check_order(decls, computed=computed)

    assert_output(
        "".join([
            f"order: {' -> '.join(result.order)}\n",
            *(f"problem: {problem}\n" for problem in result.problems),
            *(f"provisional read: {read}\n" for read in result.provisional_reads),
        ])
    )


class WideWriter(check_pass_pipeline.DecisionWriter, Protocol):
    """A writer protocol with a write that the decision writer does not implement."""

    def drop_everything(self) -> None:
        """Drop every default."""


def _no_decision(_facts: check_pass_pipeline.FinalFacts, _writer: check_pass_pipeline.DecisionWriter) -> None:
    """Decide nothing."""


def _decision(
    decl: check_pass_pipeline.PassDecl,
    *,
    run: Callable[[check_pass_pipeline.FinalFacts, Any], None] = _no_decision,
    writer: type[Any] = check_pass_pipeline.DecisionWriter,
    issue: str = "#0",
) -> check_pass_pipeline.DecisionPass[Any]:
    return check_pass_pipeline.DecisionPass({"decl": decl, "issue": issue}, writer, run)


WAVE_B_REGISTRATION = ("G03b", "G08", "G06", "G12b")
"""The wave-B decisions in another order than they run."""


def _wave_b_decisions(
    writes: str,
    *,
    edges: bool = True,
    run: Callable[[check_pass_pipeline.FinalFacts, Any], None] = _no_decision,
) -> list[check_pass_pipeline.DecisionPass[Any]]:
    """Return the wave-B decisions by name only, in ``WAVE_B_REGISTRATION`` order."""
    decls = {decl.name: decl for decl in _wave_b(writes, edges=edges)}
    return [_decision(decls[name], run=run) for name in WAVE_B_REGISTRATION]


DECISION_REGISTRIES = {
    "wave-b-declared": _wave_b_decisions("declared"),
    "wave-b-measured": _wave_b_decisions("measured"),
    "wave-b-declared-without-edges": _wave_b_decisions("declared", edges=False),
    "broken": [
        _decision(_decl("G06", {"FIELD_TYPES"}, {"CONTAINERS"}, []), issue=""),
        _decision(_decl("G06", {"FIELD_TYPES"}, {"CONTAINERS"}, [])),
        _decision(
            check_pass_pipeline.PassDecl(
                "apply_generic_base_class",
                check_pass_pipeline.Phase.CROSS_MODULE,
                check_pass_pipeline.Scope.MODULE,
                check_pass_pipeline.PassKind.CHECK,
                reads=frozenset({check_pass_pipeline.RenderFact.CLASS_NAMES}),
            ),
            writer=WideWriter,
        ),
        _decision(_decl("G08", {"DEFAULTS"}, {"DEFAULTS"}, []), writer=BaseModel),
    ],
}


@pytest.mark.parametrize("decisions", DECISION_REGISTRIES.values(), ids=DECISION_REGISTRIES)
def test_decision_registry(decisions: list[check_pass_pipeline.DecisionPass[Any]]) -> None:
    """A registry runs its decisions in the order the order rule computes; its rules report what breaks them."""
    assert_output(
        "".join([
            f"order: {' -> '.join(item.spec['decl'].name for item in check_pass_pipeline.decision_order(decisions))}\n",
            *(f"problem: {problem}\n" for problem in check_pass_pipeline.decision_problems(decisions)),
        ])
    )


@pytest.mark.parametrize("finality", ["1", "0"], ids=["checked", "unchecked"])
def test_decision_phase_runs_the_registry_after_the_generic_base_class(
    monkeypatch: pytest.MonkeyPatch, finality: str
) -> None:
    """The decisions run once each, in their computed order, between the generic base class and render preparation.

    Each reads the field names of the models that render in every module; collapsed root models, which leave their
    modules later, are not among them.
    """
    monkeypatch.setenv(CHECK_FACT_FINALITY_ENV, finality)
    seen: list[check_pass_pipeline.FinalFacts] = []

    class DecidingParser(OpenAPIParser):
        _DECISION_PASSES = tuple(_wave_b_decisions("declared", run=lambda facts, _writer: seen.append(facts)))

    parser = DecidingParser(
        DATA / "openapi" / "modular.yaml", collapse_root_models=True, formatters=[Formatter.BUILTIN]
    )
    trace = check_pass_pipeline.call_trace(parser.parse)
    start = trace.index("apply_generic_base_class")
    facts = seen[0]

    assert_output(
        "".join([
            *(f"{name}\n" for name in trace[start : start + 6]),
            f"decisions that read these facts: {sum(item is facts for item in seen)}\n",
            *(
                f"{'/'.join(module.module)}: {[[field.name for field in m.fields] for m in module.live_models]}\n"
                for module in facts.modules
            ),
            f"live models: {len(facts.live_models)}\n",
        ])
    )
