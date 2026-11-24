"""Tests for the attribute-to-fact table check.

heki checks the writes against the contracts built from the table (``tests/test_build_heki_contracts_script.py``).
"""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from inline_snapshot import snapshot

from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Iterable
    from types import ModuleType

FACTS_TEMPLATE = """
from enum import Enum


class Phase(Enum):
    PARSE = 0
    RENDER = 9


class FactKind(Enum):
    FACT = "fact"
    PRESENTATION = "presentation"
    CACHE = "cache"
    BOOKKEEPING = "bookkeeping"


class Fact(Enum):
    FIELD_TYPES = "field_types"
    NULLABLE = "nullable"


class RenderFact(Enum):
    QUOTING = "quoting"
    PLANS = "plans"


class FreezePoint(Enum):
    B1 = "facts-final"
    B2 = "render-ready"


class Access(Enum):
    ATTRIBUTE = "attribute"
    ITEM = "item"


class AttributeFact:
    owner: str
    attribute: str
    kind: FactKind
    facts: tuple
    final_after: Phase
    frozen_at: FreezePoint | None
    store_ops: tuple = ()
    access: Access = Access.ATTRIBUTE


RENDER_FACT_FREEZE_POINTS = {freeze}

{table}
"""
VALID_FREEZE = "{RenderFact.QUOTING: FreezePoint.B2, RenderFact.PLANS: FreezePoint.B1}"
VALID_ROW = 'AttributeFact("DataType", "type", FactKind.FACT, (Fact.FIELD_TYPES,), Phase.PARSE, FreezePoint.B1)'


def _load_checker() -> ModuleType:
    script_path = Path(__file__).parents[2] / "scripts" / "check_generation_store_usage.py"
    spec = importlib.util.spec_from_file_location("check_generation_store_usage", script_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _findings(violations: Iterable[Any]) -> list[tuple[int, str, str, str]]:
    return [(item.line, item.function, item.target, item.message) for item in violations]


def _key_writes(source: Path, scope: str = "parser") -> str:
    checker = _load_checker()
    writes, problems = checker.dict_key_writes([(source, scope)])
    assert problems == []
    return "".join(f"{write.line} {write.function} {write.key}\n" for write in writes)


def test_generation_sources_match_the_table() -> None:
    """The table, the declarations of the model graph and the ``__dict__`` keys the package writes agree."""
    checker = _load_checker()

    assert [violation.format() for violation in checker.check_default_targets()] == snapshot([])
    assert checker.main([]) == 0


def test_cli_reports_table_problems(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """A key row the sources never write fails the command line run, which prints each problem."""
    checker = _load_checker()
    row = checker.FactRow("DataModel", "_NEVER_WRITTEN_KEY", "CACHE", (), "PARSE", None, (), "DICT_KEY")
    monkeypatch.setattr(checker, "ATTRIBUTE_FACTS", (*checker.ATTRIBUTE_FACTS, row))

    failing = checker.main([])

    assert (failing, capsys.readouterr().err.splitlines()) == snapshot((
        1,
        [
            "ATTRIBUTE_FACTS problems found:",
            (
                "scripts/fact_tables.py:0:0: ATTRIBUTE_FACTS row DataModel._NEVER_WRITTEN_KEY names nothing the "
                "sources declare or write (in ATTRIBUTE_FACTS)"
            ),
        ],
    ))


def test_a_module_the_model_cannot_read_is_a_problem(tmp_path: Path) -> None:
    """A module the source model refuses gives no writes and a problem of its own."""
    checker = _load_checker()
    source = tmp_path / "refused.py"
    source.write_text("def f():\n    nonlocal x\n    x = 1\n")

    writes, problems = checker.dict_key_writes([(source, "parser")])

    assert (writes, [problem.target for problem in problems]) == ([], ["unreadable-source"])


def test_dict_key_writes_name_every_write_form(tmp_path: Path) -> None:
    """Every form that writes a named ``__dict__`` key counts, through aliases and entries; a computed key does not."""
    source = tmp_path / "writes.py"
    source.write_text(
        """
def write(field, name, pair):
    field.__dict__["literal"] = 1
    field.__dict__[CONSTANT_KEY] = 1
    field.__dict__[module.NAMED_KEY] = 1
    field.__dict__[name] = 1
    field.__dict__[:] = 1
    field.__dict__["annotated"]: int = 1
    field.__dict__["bare"]: int
    field.__dict__["first"], other = pair
    [*field.__dict__["starred"]] = pair
    field.__dict__["added"] += 1
    del field.__dict__["deleted"]
    field.__dict__.pop("popped", None)
    field.__dict__.setdefault(DEFAULT_KEY, 1)
    field.__dict__.pop()
    field.__dict__.update({})
    field.__dict__["entry"].append(1)
    field.__dict__.get("read")
    field.extras["not_a_dict_key"] = 1
    vars(field)["vars"] = 1
    values = field.__dict__
    values["alias"] = 1
    values.pop("alias_popped")
    values |= {}
    copied = values
    copied["copied"] = 1
    entry = values["alias_entry"]
    entry.update({})
    entry |= {}
    computed = values[name]
    computed.clear()
    got = getattr(field, "__dict__")
    got["getattr"] = 1
    getattr(field, "__dict__")["getattr_direct"] = 1
    state = vars(field)
    state["vars_alias"] = 1
    if (walrus := field.__dict__) is not None:
        walrus["walrus"] = 1
    rebound = values
    rebound = []
    rebound["rebound"] = 1
    name: str
    other.attribute = name
""",
    )

    assert_output(_key_writes(source))


def test_dict_key_writes_skip_parser_state_and_schema_objects(tmp_path: Path) -> None:
    """Writes to the parser itself, to schema objects and to callables are no model-graph writes."""
    source = tmp_path / "skipped.py"
    source.write_text(
        """
class Parser:
    def method(self, obj: JsonSchemaObject, action: Callable[[], str], quoted: "JsonSchemaObject | None") -> None:
        self.__dict__["state"] = 1
        vars(self)["state"] = 1
        own = getattr(self, "__dict__")
        own["state"] = 1
        getattr(self, "__dict__")["state"] = 1
        state = vars(self)
        state["state"] = 1
        obj.__dict__["schema"] = 1
        quoted.__dict__["schema"] = 1
        action.__dict__["marker"] = True
        merged = self.SCHEMA_OBJECT_TYPE.model_validate({})
        merged.__dict__["merged"] = 1
        copied = obj.model_copy()
        copied.__dict__["copied"] = 1
        other = field.copy()
        other.__dict__["other_copy"] = 1
        obj = build()
        obj.__dict__["rebuilt"] = 1
        build().__dict__["built"] = 1


def concern_function(parser: Host) -> None:
    merged = parser._schema_object({})
    merged.__dict__["merged"] = 1
    unrelated = parser.make({})
    unrelated.__dict__["unrelated"] = 1
""",
    )

    assert _key_writes(source) == snapshot("""\
19 Parser.method other_copy
21 Parser.method rebuilt
22 Parser.method built
29 concern_function unrelated
""")


def test_dict_key_writes_follow_rebound_names(tmp_path: Path) -> None:
    """A rebound schema-object name writes model-graph keys again; an alias stays an alias inside nested scopes."""
    source = tmp_path / "rebound.py"
    source.write_text(
        """
def loop(obj: JsonSchemaObject, items):
    for obj in items:
        obj.__dict__["loop"] = 1


def context(obj: JsonSchemaObject, manager):
    with manager as obj:
        obj.__dict__["context"] = 1


def comprehension(obj: JsonSchemaObject, items):
    [obj for obj in items]
    obj.__dict__["comprehension"] = 1


def handler(obj: JsonSchemaObject):
    try:
        pass
    except ValueError as obj:
        obj.__dict__["handler"] = 1


def unpacking(obj: JsonSchemaObject, pair):
    obj, other = pair
    obj.__dict__["unpacking"] = 1


def augmented(obj: JsonSchemaObject, field):
    obj += field
    obj.__dict__["augmented"] = 1


async def asynchronous(obj: JsonSchemaObject, items):
    async for obj in items:
        obj.__dict__["asynchronous"] = 1


def enclosing(field):
    values = field.__dict__

    def inner():
        values["closure"] = 1

    values = []
    values["local"] = 1
    return inner
""",
    )

    assert _key_writes(source) == snapshot("""\
4 loop loop
9 context context
21 handler handler
26 unpacking unpacking
31 augmented augmented
36 asynchronous asynchronous
43 enclosing.inner closure
""")


def test_dict_key_writes_of_model_code(tmp_path: Path) -> None:
    """Model code writes its own keys through ``self``; a cached property writes its name."""
    source = tmp_path / "model.py"
    source.write_text(
        """
from functools import cached_property


class Field:
    def mark(self):
        self.__dict__["_marked"] = True

    @cached_property
    def path(self):
        return ""

    @property
    def plain(self):
        return ""

    class Inner:
        @functools.cached_property
        def nested(self):
            return ""
""",
    )

    assert _key_writes(source, "model") == snapshot("""\
10 Field.path path
7 Field.mark _marked
19 Field.Inner.nested nested
""")


def test_checker_uses_generation_store_api_surface() -> None:
    """Checker messages should stay aligned with the store mutation API."""
    checker = _load_checker()

    assert sorted(checker.GENERATION_STORE_MUTATION_METHODS) == snapshot(
        [
            "add_model_import",
            "adopt_base_options",
            "append_field",
            "apply_const",
            "apply_literal_default",
            "bind_builtin_names",
            "clear_target_null",
            "collapse_root_data_type",
            "defer_refresh",
            "detach_data_type_ref",
            "detach_model_data_type_refs",
            "discard_derived_facts",
            "drop_field_discriminator",
            "drop_field_extra",
            "drop_held_null",
            "drop_union_member_constraints",
            "drop_union_operator",
            "force_field_assignment",
            "inline_shared",
            "insert_field",
            "mark_forward_references",
            "mark_serialized_as_any",
            "mark_target_null",
            "name_additional_properties_classes",
            "name_enum_member",
            "name_enum_member_literal_class",
            "name_reference",
            "note_changed_models",
            "omit_field_default",
            "override_type_import",
            "record_dropped_multiple_of",
            "record_nullable_without_default",
            "record_raw_schema_default",
            "record_site_constraints",
            "record_values_as_written",
            "record_widened_values",
            "redirect_model_reference_users",
            "redirect_reference_users",
            "register_model",
            "rename_field",
            "rename_model",
            "replace_additional_properties_type",
            "replace_data_type_ref",
            "replace_field_type",
            "replace_model",
            "replace_nested_data_type",
            "require_field",
            "reset_base_classes",
            "restore_required_inherited_field",
            "retire_model",
            "set_base_classes",
            "set_field_constraints",
            "set_field_default",
            "set_field_extra",
            "set_field_extras",
            "set_field_identity",
            "set_field_runtime_default",
            "set_fields",
            "set_module_models",
            "set_nested_data_types",
            "set_type_discriminator",
            "set_type_expressions",
            "swap_data_type",
            "update_model_reference",
            "use_assignment_for_forward_references",
            "use_typing_generics",
            "write_field_call_in_annotated",
            "write_rendered_null",
        ],
    )


def test_fact_vocabulary_and_table_shape() -> None:
    """Pin the phase and fact vocabulary that the write guard, finality checks and pipeline build on."""
    checker = _load_checker()

    assert checker.fact_vocabulary() == snapshot({
        "Phase": (
            "PARSE",
            "SETTLE",
            "STRUCTURE",
            "PLAN",
            "MODULES",
            "CROSS_MODULE",
            "DECIDE",
            "RENDER_PREPARATION",
            "IMPORTS",
            "RENDER",
        ),
        "FactKind": ("FACT", "PRESENTATION", "CACHE", "BOOKKEEPING"),
        "Fact": (
            "MODELS_EXIST",
            "MODEL_KIND",
            "BASES",
            "FIELD_SET",
            "FIELD_NAMES",
            "FIELD_TYPES",
            "CONTAINERS",
            "REQUIRED",
            "NULLABLE",
            "DEFAULTS",
            "CONSTRAINTS",
            "DISCRIMINATOR_TAGS",
            "TYPE_OVERRIDES",
            "FIELD_ALIASES",
            "METADATA",
        ),
        "RenderFact": (
            "FIELD_ORDER",
            "KEYWORD_ONLY",
            "FORCED_ASSIGNMENT",
            "ASSIGNMENT_FORM",
            "QUOTING",
            "UNBOUND_FIELDS",
            "DEFAULT_VALIDATION",
            "DEFAULT_EXPRESSION",
            "IMPORT_ALIASES",
            "TYPE_ALIAS_BODY",
            "SET_ITEM_HASH",
            "CLASS_NAMES",
            "MODULE_MEMBERSHIP",
            "MODEL_ORDER",
            "MODULE_IMPORTS",
            "BACKEND_CONFIG",
            "MODULE_CODE",
            "PLANS",
            "STDERR",
        ),
        "FreezePoint": ("B1", "B2", "RENDER"),
        "Access": ("ATTRIBUTE", "CONTAINER", "ITEM", "DICT_KEY", "CACHED_PROPERTY"),
        "MemberUse": ("CALL", "READ", "WRITE"),
    })
    assert Counter(row.kind for row in checker.ATTRIBUTE_FACTS) == snapshot(
        Counter({"FACT": 75, "PRESENTATION": 60, "BOOKKEEPING": 33, "CACHE": 11})
    )
    assert sorted({row.owner for row in checker.ATTRIBUTE_FACTS}) == snapshot([
        "AnnotatedConstraintDataType",
        "BaseModel",
        "ConstraintsBase",
        "DataClass",
        "DataModel",
        "DataModelFieldBase",
        "DataType",
        "Member",
        "ModuleContext",
        "Parser",
        "Reference",
        "RootModel",
        "TemplateBase",
        "TypedDict",
        "_MetaAnnotatedType",
        "_MetaType",
        "_RenderedDataModelField",
    ])


@pytest.mark.parametrize(
    "table",
    [
        pytest.param(f"ATTRIBUTE_FACTS = ({VALID_ROW},)", id="assigned"),
        pytest.param(
            "ATTRIBUTE_FACTS: tuple = (AttributeFact('Reference', 'name', FactKind.PRESENTATION, "
            "(RenderFact.QUOTING,), Phase.PARSE, FreezePoint.B2, store_ops=('rename_model',)),)",
            id="annotated",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = ("
            "AttributeFact('Parser', '_plans', FactKind.FACT, (RenderFact.PLANS,), Phase.PARSE, FreezePoint.B1), "
            "AttributeFact('DataType', 'is_optional', FactKind.FACT, (Fact.NULLABLE,), Phase.RENDER, FreezePoint.B2), "
            "AttributeFact('DataType', '_NULL', FactKind.PRESENTATION, (Fact.NULLABLE,), Phase.RENDER, "
            "FreezePoint.B2), "
            "AttributeFact('DataModel', '_PLAN', FactKind.CACHE, (RenderFact.QUOTING,), Phase.PARSE, None), "
            "AttributeFact('DataType', 'flag', FactKind.PRESENTATION, (), Phase.PARSE, FreezePoint.B1))",
            id="kind-exceptions",
        ),
        pytest.param(f"ATTRIBUTE_FACTS = [{VALID_ROW}]", id="list"),
        pytest.param("OTHER = ()", id="missing"),
        pytest.param('ATTRIBUTE_FACTS = ("DataType",)', id="not-a-call"),
        pytest.param('ATTRIBUTE_FACTS = (AttributeFact("DataType", "type"),)', id="missing-fields"),
        pytest.param(
            'ATTRIBUTE_FACTS = (AttributeFact("DataType", NAME, FactKind.FACT, (), Phase.PARSE, None),)',
            id="not-literal",
        ),
        pytest.param(
            'ATTRIBUTE_FACTS = (AttributeFact("DataType", "type", Phase.PARSE, (), Phase.PARSE, None),)',
            id="wrong-enum",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact(None, 'type', FactKind.FACT, (), Phase.PARSE, None),)",
            id="owner-none",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'type', FactKind.FACT, 'facts', Phase.PARSE, None),)",
            id="facts-string",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'parent', FactKind.BOOKKEEPING, (Fact.FIELD_TYPES,), "
            "Phase.PARSE, None),)",
            id="bookkeeping-facts",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'parent', FactKind.BOOKKEEPING, (), Phase.PARSE, None, "
            "store_ops=('set_fields',)),)",
            id="bookkeeping-store-ops",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'type', FactKind.FACT, (RenderFact.QUOTING,), Phase.PARSE, "
            "FreezePoint.B2),)",
            id="fact-without-fact",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'alias', FactKind.PRESENTATION, (Fact.FIELD_TYPES,), "
            "Phase.PARSE, FreezePoint.B1),)",
            id="presentation-with-fact",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', '_MEMO', FactKind.CACHE, (Fact.FIELD_TYPES,), Phase.PARSE, "
            "None),)",
            id="cache-with-fact",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'alias', FactKind.PRESENTATION, (RenderFact.QUOTING,), "
            "Phase.PARSE, FreezePoint.B1),)",
            id="frozen-too-early",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataModelFieldBase', 'extras', FactKind.FACT, (Fact.FIELD_TYPES,), "
            "Phase.PARSE, FreezePoint.B1, access=Access.ITEM),)",
            id="item-without-key",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataModelFieldBase', 'extras[\"k\"]', FactKind.FACT, "
            "(Fact.FIELD_TYPES,), Phase.PARSE, FreezePoint.B1),)",
            id="key-without-item",
        ),
        pytest.param(
            "ATTRIBUTE_FACTS = (AttributeFact('DataType', 'type', FactKind.FACT, (Fact.FIELD_TYPES,), Phase.PARSE, "
            "FreezePoint.B1, store_ops=('write_directly',)),)",
            id="unknown-store-op",
        ),
        pytest.param(f"ATTRIBUTE_FACTS = ({VALID_ROW}, {VALID_ROW})", id="duplicate"),
    ],
)
def test_attribute_facts_rows_are_validated(tmp_path: Path, table: str) -> None:
    """The checker reads only literal rows and rejects contradictory ones."""
    checker = _load_checker()
    facts_module = tmp_path / "_facts.py"
    facts_module.write_text(FACTS_TEMPLATE.format(table=table, freeze=VALID_FREEZE))

    try:
        outcome = repr(checker.load_attribute_facts(facts_module))
    except (TypeError, ValueError, RuntimeError) as error:
        outcome = f"{type(error).__name__}: {error}"

    assert_output(f"{outcome}\n")


@pytest.mark.parametrize(
    "freeze",
    [
        pytest.param("[]", id="not-a-dict"),
        pytest.param("{RenderFact.QUOTING: FreezePoint.B2}", id="missing-entry"),
        pytest.param("{Fact.FIELD_TYPES: FreezePoint.B1, RenderFact.PLANS: FreezePoint.B1}", id="fact-key"),
        pytest.param("{RenderFact.QUOTING: None, RenderFact.PLANS: FreezePoint.B1}", id="no-freeze-point"),
        pytest.param("{**OTHER, RenderFact.PLANS: FreezePoint.B1}", id="unpacked"),
    ],
)
def test_render_fact_freeze_points_are_validated(tmp_path: Path, freeze: str, request: pytest.FixtureRequest) -> None:
    """Every render fact needs a literal freeze point, from which the rows' ``frozen_at`` follows."""
    checker = _load_checker()
    facts_module = tmp_path / "_facts.py"
    facts_module.write_text(FACTS_TEMPLATE.format(table=f"ATTRIBUTE_FACTS = ({VALID_ROW},)", freeze=freeze))

    with pytest.raises((TypeError, ValueError)) as error:
        checker.load_attribute_facts(facts_module)

    assert (
        f"{type(error.value).__name__}: {error.value}"
        == snapshot({
            "not-a-dict": "TypeError: RENDER_FACT_FREEZE_POINTS must be a literal dict",
            "missing-entry": "ValueError: RENDER_FACT_FREEZE_POINTS misses PLANS",
            "fact-key": "TypeError: RENDER_FACT_FREEZE_POINTS maps RenderFact members to FreezePoint members",
            "no-freeze-point": "TypeError: RENDER_FACT_FREEZE_POINTS maps RenderFact members to FreezePoint members",
            "unpacked": "TypeError: RENDER_FACT_FREEZE_POINTS maps RenderFact members to FreezePoint members",
        })[request.node.callspec.id]
    )


def test_attribute_facts_module_needs_the_row_class(tmp_path: Path) -> None:
    """A table without its record class cannot be read."""
    checker = _load_checker()
    facts_module = tmp_path / "_facts.py"
    facts_module.write_text("ATTRIBUTE_FACTS = ()\n")

    with pytest.raises(RuntimeError) as error:
        checker.load_attribute_facts(facts_module)

    assert str(error.value) == snapshot("AttributeFact was not found")


def test_table_problems_report_unclassified_attributes_and_stale_rows(tmp_path: Path) -> None:
    """Every declared attribute and written ``__dict__`` key of the model graph needs a row; every row names something.

    A key the store writes counts as written, and without the writes no key row is checked.
    """
    checker = _load_checker()
    declarations = tmp_path / "declarations.py"
    declarations.write_text(
        """
import typing
from typing import ClassVar

from pydantic import BaseModel

from datamodel_code_generator.model.base import DataModel as Base


class DataModel:
    covered: int
    LIMIT: int = 1
    cache: ClassVar[dict] = {}
    COUNT = 0

    def __init__(self):
        self.fresh = 1
        self.annotated: int = 2
        self.fresh.attribute = 3

    def method(self):
        self.later = 1
        setattr(self, "dynamic", 2)
        self.first, self.second = 1, 2
        self.head, *self.rest = 1, 2
        self.covered += 1
        self.__dict__["key"] = 1


class Child(Base):
    extra: int


class External(BaseModel):
    ignored: int


class Other(typing.NamedTuple):
    value: int


class Derived(Base):
    shadowed: int

    def method(self):
        from pydantic import BaseModel as Base
""",
    )

    def row(owner: str, attribute: str, access: str = "ATTRIBUTE") -> object:
        return checker.FactRow(owner, attribute, "FACT", (), "PARSE", None, (), access)

    rows = (
        row("DataModel", "covered"),
        row("DataModel", "method"),
        row("DataModel", 'covered["key"]', "ITEM"),
        row("DataModel", "gone"),
        row("DataModel", "_WRITTEN_KEY", "DICT_KEY"),
        row("DataModel", "_UNWRITTEN_KEY", "DICT_KEY"),
        row("DataModel", "_STORE_KEY", "DICT_KEY"),
        row("Parser", "results"),
        row("Parser", "_PARSER_KEY", "DICT_KEY"),
    )
    writes = [
        checker.DictKeyWrite(declarations, 26, 9, "DataModel.method", "_WRITTEN_KEY"),
        checker.DictKeyWrite(declarations, 27, 9, "DataModel.method", "_PARSER_KEY"),
    ]

    problems = checker.table_problems(
        rows=rows, declaration_paths=[declarations], key_writes=writes, store_keys=frozenset({"_STORE_KEY"})
    )
    unchecked = checker.table_problems(rows=rows[-3:-1], declaration_paths=[])

    assert _findings(problems) == snapshot([
        (
            31,
            "Child",
            "extra",
            "Child.extra has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            17,
            "DataModel",
            "fresh",
            "DataModel.fresh has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            18,
            "DataModel",
            "annotated",
            "DataModel.annotated has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            22,
            "DataModel",
            "later",
            "DataModel.later has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            24,
            "DataModel",
            "first",
            "DataModel.first has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            24,
            "DataModel",
            "second",
            "DataModel.second has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            25,
            "DataModel",
            "head",
            "DataModel.head has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            25,
            "DataModel",
            "rest",
            "DataModel.rest has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            23,
            "DataModel",
            "dynamic",
            "DataModel.dynamic has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            43,
            "Derived",
            "shadowed",
            "Derived.shadowed has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            27,
            "DataModel.method",
            "_PARSER_KEY",
            "__dict__[_PARSER_KEY] has no ATTRIBUTE_FACTS row in scripts/fact_tables.py",
        ),
        (
            0,
            "ATTRIBUTE_FACTS",
            "gone",
            "ATTRIBUTE_FACTS row DataModel.gone names nothing the sources declare or write",
        ),
        (
            0,
            "ATTRIBUTE_FACTS",
            "_UNWRITTEN_KEY",
            "ATTRIBUTE_FACTS row DataModel._UNWRITTEN_KEY names nothing the sources declare or write",
        ),
    ])
    assert unchecked == []


def test_checked_scopes_follow_the_source_layout(tmp_path: Path) -> None:
    """``model/`` and ``types.py`` are checked as model code, everything else as parser code."""
    checker = _load_checker()
    source_root = checker.ROOT / "src" / "datamodel_code_generator"
    paths = [
        source_root / "parser" / "base.py",
        source_root / "model" / "base.py",
        source_root / "model" / "pydantic_v2" / "base_model.py",
        source_root / "types.py",
        source_root / "reference.py",
        tmp_path / "outside.py",
    ]

    assert [checker.classify_path(path) for path in paths] == snapshot([
        "parser",
        "model",
        "model",
        "model",
        "parser",
        "parser",
    ])
    compiled = sorted((source_root / "model" / "_compiled_templates").glob("*.py"))[:1]
    assert checker.iter_python_files([tmp_path / "notes.txt", *compiled]) == []


@pytest.mark.parametrize(
    "assignment",
    [
        pytest.param('SITE_EFFECTS = {"set_fields": "copied"}\nNO_SITE_EFFECT = {"rename_field"}', id="literals"),
        pytest.param("SITE_EFFECTS = MappingProxyType(EFFECTS)\nNO_SITE_EFFECT = frozenset()", id="not-literal"),
        pytest.param("SITE_EFFECTS = {}\nNO_SITE_EFFECT = frozenset({1})", id="not-strings"),
        pytest.param("SITE_EFFECTS = {}", id="missing"),
    ],
)
def test_store_api_list_must_be_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assignment: str, request: pytest.FixtureRequest
) -> None:
    """The checker reads the store mutation API as the names of two literal tables."""
    checker = _load_checker()
    store_module = tmp_path / checker.GENERATION_STORE_MODULE
    store_module.parent.mkdir(parents=True)
    store_module.write_text(f"{assignment}\n")
    monkeypatch.setattr(checker, "ROOT", tmp_path)

    try:
        outcome = repr(sorted(checker._load_generation_store_mutation_methods()))
    except (TypeError, RuntimeError) as error:
        outcome = f"{type(error).__name__}: {error}"

    assert (
        outcome
        == snapshot({
            "literals": "['rename_field', 'set_fields']",
            "not-literal": "TypeError: SITE_EFFECTS must be a literal set or dict of strings",
            "not-strings": "TypeError: NO_SITE_EFFECT must contain only literal strings",
            "missing": "RuntimeError: NO_SITE_EFFECT was not found",
        })[request.node.callspec.id]
    )
