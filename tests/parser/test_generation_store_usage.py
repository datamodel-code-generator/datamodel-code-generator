"""Tests for the GenerationStore usage guard."""

from __future__ import annotations

import importlib.util
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from inline_snapshot import snapshot

from tests.conftest import assert_output

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

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


def _toml_counts(text: str) -> Counter[tuple[str, str, str]]:
    return Counter({
        (source, function, target): count
        for source, functions in tomllib.loads(text).items()
        for function, targets in functions.items()
        for target, count in targets.items()
    })


def _report(violations: Iterable[Any]) -> str:
    return "".join(
        f"{line} {function} {target}: {message}\n" for line, function, target, message in _findings(violations)
    )


def test_parser_sources_use_generation_store_for_mutations() -> None:
    """Parser code should not bypass GenerationStore mutation helpers beyond the allowlist."""
    checker = _load_checker()

    violations = checker.check_paths([Path("src/datamodel_code_generator/parser")])

    assert violations == snapshot([])


def test_generation_sources_match_the_allowlist() -> None:
    """The default targets, the table and the allowlist agree exactly, and every parser-called hook is listed."""
    checker = _load_checker()

    violations, scan = checker.check_default_targets()

    assert [violation.format() for violation in violations] == snapshot([])
    assert scan.counts == checker.load_allowlist()
    assert _toml_counts((checker.ROOT / checker.DEFAULT_ALLOWLIST).read_text()) == scan.counts
    assert checker.unlisted_parser_hooks() == snapshot([])


def test_checker_uses_generation_store_api_surface() -> None:
    """Checker messages should stay aligned with the store mutation API."""
    checker = _load_checker()

    assert sorted(checker.GENERATION_STORE_MUTATION_METHODS) == snapshot(
        [
            "append_field",
            "collapse_root_data_type",
            "defer_refresh",
            "detach_data_type_ref",
            "detach_model_data_type_refs",
            "discard_derived_facts",
            "insert_field",
            "move_model",
            "note_changed_models",
            "redirect_model_reference_users",
            "redirect_reference_users",
            "register_model",
            "remove_field",
            "rename_model",
            "replace_data_type_ref",
            "replace_field_type",
            "replace_nested_data_type",
            "reset_base_classes",
            "set_base_classes",
            "set_fields",
            "set_nested_data_types",
            "update_model_reference",
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
        ),
        "FreezePoint": ("B1", "B2", "RENDER"),
        "Access": ("ATTRIBUTE", "CONTAINER", "ITEM", "DICT_KEY", "CACHED_PROPERTY"),
    })
    assert Counter(row.kind for row in checker.ATTRIBUTE_FACTS) == snapshot(
        Counter({"FACT": 65, "PRESENTATION": 56, "BOOKKEEPING": 16, "CACHE": 19})
    )
    assert sorted({row.owner for row in checker.ATTRIBUTE_FACTS}) == snapshot([
        "AnnotatedConstraintDataType",
        "BaseModel",
        "ConstraintsBase",
        "DataClass",
        "DataModel",
        "DataModelFieldBase",
        "DataType",
        "Enum",
        "ModuleContext",
        "Parser",
        "Reference",
        "RootModel",
        "TemplateBase",
        "TypedDict",
        "_RenderedDataModelField",
    ])


def test_generation_store_usage_checker_rejects_direct_mutations(tmp_path: Path) -> None:
    """The checker reports reference, field, and base-class mutations."""
    checker = _load_checker()
    bad_source = tmp_path / "bad_parser.py"
    bad_source.write_text(
        """
def mutate(model, field, data_type, ref):
    data_type.reference = ref
    model.reference.name = "Renamed"
    model.fields.append(field)
    self.results.append(model)
    model.base_classes = []
    model.reference.children
    field.replace_data_type(data_type)
""",
    )

    violations = checker.check_paths([bad_source])

    assert [(violation.line, violation.column, violation.message) for violation in violations] == snapshot(
        [
            (3, 5, "use GenerationStore API instead of assigning data_type.reference"),
            (4, 5, "use GenerationStore API instead of assigning reference.name"),
            (5, 5, "use GenerationStore.append_field() instead of mutating fields"),
            (6, 5, "use GenerationStore.register_model() instead of mutating results"),
            (7, 5, "use GenerationStore.set_base_classes() instead of assigning base_classes"),
            (8, 5, "use GenerationIndex reverse-edge queries instead of Reference.children"),
            (9, 5, "use GenerationStore API instead of replace_data_type()"),
        ],
    )


def test_checker_reports_fact_presentation_and_cache_writes_in_parser_code(tmp_path: Path) -> None:
    """Every write form of a table attribute, ``__dict__`` entry and cache counts, aliases included."""
    checker = _load_checker()
    source = tmp_path / "writes.py"
    source.write_text(
        """
_set = object.__setattr__


def write(field, data_type, model, name, broken: "not valid("):
    field.required = True
    field.default += 1
    del field.alias
    field.name, data_type.type = name, "str"
    [*model.decorators] = []
    setattr(field, "nullable", None)
    _set(field, "has_default", True)
    setattr(field, name, 1)
    field.extras["kw_only"] = True
    field.extras["title"] = "Title"
    field.extras.pop("discriminator", None)
    model._internal_template_data["config"]["key"] = 1
    model._internal_template_data["config"].update({})
    data_type.data_types.append(data_type)
    data_type.data_types[:] = []
    model.class_name = name
    data_type.use_generic_container = True
    model._dedup_key_cache.clear()
    field.__dict__["_forced_field_assignment"] = True
    field.__dict__[SCHEMA_OWNER_FRAMES_KEY] = "bookkeeping"
    field.__dict__["_unknown"] = 1
    field.__dict__[name] = 1
    vars(field)[_IMPORTS_CACHE_KEY] = ()
    field.__dict__.update({})
    values = field.__dict__
    values.setdefault("_forced_field_assignment", True)
    cache = model._dedup_key_cache
    cache[name] = ()
    if (extras := getattr(field, "extras", None)) is not None:
        extras["description"] = "text"
    model.reference.path = name
    broken.required = False
    field.extras.get("title")
    model.render()
""",
    )

    violations = checker.check_paths([source])

    assert_output(_report(violations))


def test_checker_skips_parser_state_and_schema_objects(tmp_path: Path) -> None:
    """Writes to the parser itself, to schema objects and to callables are not model-graph writes."""
    checker = _load_checker()
    source = tmp_path / "skipped.py"
    source.write_text(
        """
class Parser:
    def method(self, obj: JsonSchemaObject, action: Callable[[], str], quoted: "JsonSchemaObject | None") -> None:
        self.required = True
        self.results = []
        self.__dict__["state"] = 1
        setattr(self, name, 1)
        obj.type = "string"
        obj.extras["discriminator"] = {}
        quoted.default = 1
        action.__dict__["marker"] = True
        merged = self.SCHEMA_OBJECT_TYPE.model_validate({})
        merged.__dict__["key"] = 1
        copied = obj.model_copy()
        copied.required = []
        obj = build()
        obj.nullable = True
""",
    )

    violations = checker.check_paths([source])

    assert _findings(violations) == snapshot([
        (
            5,
            "Parser.method",
            "results",
            "use GenerationStore.register_model() instead of assigning results",
        ),
        (
            17,
            "Parser.method",
            "nullable",
            "nullable changes Fact.NULLABLE outside GenerationStore operations",
        ),
    ])


def test_checker_reports_model_list_writes_on_parser_self(tmp_path: Path) -> None:
    """The direct-mutation rules apply to ``self`` too, as before the fact table."""
    checker = _load_checker()
    source = tmp_path / "self_writes.py"
    source.write_text(
        """
class P:
    def m(self, model):
        self.results = []
        self.results[:] = []
        self.fields = []
        self.data_type = None
        self.reference = None
        self.base_classes = []
        self.models = []
        del self.results[0]
""",
    )

    violations = checker.check_paths([source])

    assert [(violation.line, violation.target) for violation in violations] == snapshot([
        (4, "results"),
        (5, "results"),
        (6, "fields"),
        (7, "data_type"),
        (8, "reference"),
        (9, "base_classes"),
        (10, "models"),
        (11, "results"),
    ])


def test_checker_forgets_skipped_names_when_they_are_rebound(tmp_path: Path) -> None:
    """A rebound schema-object name is checked again; in-place operators on aliases and ``__dict__`` are writes."""
    checker = _load_checker()
    source = tmp_path / "rebound.py"
    source.write_text(
        """
def loop(obj: JsonSchemaObject, items):
    for obj in items:
        obj.required = True


def context(obj: JsonSchemaObject, manager):
    with manager as obj:
        obj.required = True


def comprehension(obj: JsonSchemaObject, items):
    [obj for obj in items]
    obj.required = True


def handler(obj: JsonSchemaObject):
    try:
        pass
    except ValueError as obj:
        obj.required = True


def unpacking(obj: JsonSchemaObject, pair):
    obj, other = pair
    obj.required = True


def augmented(obj: JsonSchemaObject, field):
    obj += field
    obj.required = True


async def asynchronous(obj: JsonSchemaObject, items):
    async for obj in items:
        obj.required = True


def in_place(field, model):
    values = field.__dict__
    values |= {}
    model.__dict__ |= {}
    cache = model._dedup_key_cache
    cache |= {}
    extras = field.extras
    extras |= {}
    field.extras["kept"] = values
""",
    )

    violations = checker.check_paths([source])

    assert_output(_report(violations))


def test_checker_follows_aliases_through_rebinding_and_entries(tmp_path: Path) -> None:
    """A name bound to an alias, or to an entry of a guarded container, writes that container."""
    checker = _load_checker()
    source = tmp_path / "aliases.py"
    source.write_text(
        """
def chained(field, model):
    extras = field.extras
    copied = extras
    copied.clear()
    config = model._internal_template_data["config"]
    config.update({})
    config["regex_engine"] = "python-re"
    nested = config
    nested |= {}
    values = field.__dict__
    entry = values["_forced_field_assignment"]
    entry.update({})
    computed = values[field.name]
    computed |= {}
    cache = getattr(model, "_dedup_key_cache")
    cache = cache
    cache.clear()
    extras = extras["discriminator"]
    extras.update({})
    rebound = copied
    rebound = []
    rebound.clear()
""",
    )

    violations = checker.check_paths([source])

    assert_output(_report(violations))


def test_default_check_reports_entries_of_missing_files() -> None:
    """An allowlist entry of a deleted or renamed file under the default targets fails; other paths are not checked."""
    checker = _load_checker()
    allowlist = checker.load_allowlist() + Counter({
        ("src/datamodel_code_generator/parser/gone.py", "write", "required"): 1,
        ("src/datamodel_code_generator/model", "write", "required"): 1,
        ("elsewhere/parser.py", "write", "required"): 1,
    })

    violations, _ = checker.check_default_targets(allowlist=allowlist)

    assert [(violation.key, violation.message.split(":")[0]) for violation in violations] == snapshot([
        (
            ("src/datamodel_code_generator/model", "write", "required"),
            "stale allowlist entry 'required'",
        ),
        (
            ("src/datamodel_code_generator/parser/gone.py", "write", "required"),
            "stale allowlist entry 'required'",
        ),
    ])


def test_checker_scans_model_hooks_helpers_and_caches(tmp_path: Path) -> None:
    """Model code is checked inside hooks and the helpers they call; caches are checked everywhere."""
    checker = _load_checker()
    source = tmp_path / "model.py"
    source.write_text(
        """
from functools import cached_property


class Field:
    def process_const(self, other):
        self.nullable = True
        self._helper()
        self.replace_data_type(None)
        self.fields[:] = []
        self.parent.children
        other.relay()
        self.render()
        self.create_reuse_model()

    def _helper(self):
        self.default = 1

    def relay(self):
        deeper(self)

    def render(self):
        self.required = False

    def create_reuse_model(self):
        self.required = False

    def unrelated(self):
        self.nullable = False
        self.replace_data_type(None)
        self.fields.append(None)
        self.__dict__["_self_reference_cache"] = True
        self._dedup_key_cache = {}

    @cached_property
    def path(self):
        return ""

    async def refresh_field_config(self):
        self.extras["kw_only"] = True


def deeper(field):
    field.alias = "alias"
""",
    )

    violations = checker.check_files([(source, "model")], allowlist=Counter())

    assert_output(_report(violations))


def test_allowlist_matches_counts_per_function_and_target(tmp_path: Path) -> None:
    """An entry allows exactly its count; extra findings and unmatched entries both fail."""
    checker = _load_checker()
    source = tmp_path / "counted.py"
    source.write_text(
        """
def write(field):
    field.required = True
    field.required = False
""",
    )
    path = source.as_posix()
    scan = checker.scan_paths([source])

    allowlists = {
        "exact": {(path, "write", "required"): 2},
        "one short": {(path, "write", "required"): 1},
        "one over, other path": {(path, "write", "required"): 3, ("elsewhere.py", "write", "required"): 1},
        "other function": {(path, "renamed", "required"): 2},
    }

    assert_output(
        "".join(
            f"[{name}]\n{_report(checker.apply_allowlist(scan, Counter(allowlist)))}"
            for name, allowlist in allowlists.items()
        )
    )


def test_allowlist_file_round_trip(tmp_path: Path) -> None:
    """The file format keeps every key readable as TOML and rejects counts that are not positive integers."""
    checker = _load_checker()
    counts = Counter({
        ("src/a.py", "Parser.__pass", "required"): 2,
        ("src/a.py", "Parser.__pass", 'extras["kw_only"]'): 1,
        ("src/b.py", "<module>", "__dict__[it's]"): 1,
    })
    allowlist = tmp_path / "allowlist.toml"
    allowlist.write_text(checker.format_allowlist(counts))
    invalid = tmp_path / "invalid.toml"
    invalid.write_text('["src/a.py"."f"]\nrequired = 0\n')
    errors = []
    for text in ('["src/a.py"."f"]\nrequired = many\n', "required = 1\n", '["src/a.py"."f"]\nrequired: 1\n'):
        with pytest.raises((TypeError, ValueError)) as raised:
            checker.parse_allowlist(text)
        errors.append(f"{type(raised.value).__name__}: {raised.value}")

    with pytest.raises(TypeError) as error:
        checker.load_allowlist(invalid)

    assert checker.load_allowlist(allowlist) == counts
    assert _toml_counts(allowlist.read_text()) == counts
    assert errors == snapshot([
        "TypeError: allowlist count of src/a.py f required must be a positive integer",
        'ValueError: allowlist line 1 is not a ["path"."function"] table or a target = count entry: required = 1',
        'ValueError: allowlist line 2 is not a ["path"."function"] table or a target = count entry: required: 1',
    ])
    assert checker.load_allowlist(tmp_path / "missing.toml") == Counter()
    assert allowlist.read_text().splitlines()[-6:] == snapshot([
        '["src/a.py"."Parser.__pass"]',
        "'extras[\"kw_only\"]' = 1",
        "required = 2",
        "",
        '["src/b.py"."<module>"]',
        '"__dict__[it\'s]" = 1',
    ])
    assert str(error.value) == snapshot("allowlist count of src/a.py f required must be a positive integer")


def test_write_allowlist_never_adds_entries(tmp_path: Path) -> None:
    """The write mode creates the file once and afterwards may only lower it."""
    checker = _load_checker()
    allowlist = tmp_path / "allowlist.toml"
    first = Counter({("src/a.py", "f", "required"): 2, ("src/a.py", "f", "default"): 1})

    created = checker.write_allowlist(first, allowlist)
    grown = checker.write_allowlist(first + Counter({("src/a.py", "g", "alias"): 1}), allowlist)
    unchanged = checker.load_allowlist(allowlist) == first
    lowered = checker.write_allowlist(Counter({("src/a.py", "f", "required"): 1}), allowlist)

    assert (created, grown, unchanged, lowered) == snapshot(([], [("src/a.py", "g", "alias")], True, []))
    assert checker.load_allowlist(allowlist) == Counter({("src/a.py", "f", "required"): 1})


def test_cli_checks_paths_and_writes_the_allowlist(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exercise the default run, an explicit failing path, and both outcomes of the write mode."""
    checker = _load_checker()
    bad_source = tmp_path / "bad.py"
    bad_source.write_text("def write(field):\n    field.required = True\n")
    allowlist = tmp_path / "allowlist.toml"

    clean = checker.main([])
    failing = checker.main([str(bad_source)])
    failing_output = capsys.readouterr().err.replace(tmp_path.as_posix(), "<tmp>")
    monkeypatch.setattr(checker, "ALLOWLIST_PATH", allowlist)
    created = checker.main(["--write-allowlist"])
    created_output = capsys.readouterr().err
    created_matches = checker.load_allowlist() == checker.load_allowlist(checker.ROOT / checker.DEFAULT_ALLOWLIST)
    allowlist.write_text(allowlist.read_text().replace("\n'@cached_property' = 1\n", "\n", 1))
    refused = checker.main(["--write-allowlist"])
    refused_output = capsys.readouterr().err

    assert (clean, failing, created, created_output, created_matches, refused) == snapshot((0, 1, 0, "", True, 1))
    assert failing_output == snapshot("""\
GenerationStore usage violations found:
<tmp>/bad.py:2:5: required changes Fact.REQUIRED outside GenerationStore operations (in write)
""")
    assert refused_output.splitlines()[:2] == snapshot([
        "Refusing to add allowlist entries; route these writes through GenerationStore:",
        "src/datamodel_code_generator/model/base.py: ConstraintsBase._exclude_unset_dump: @cached_property",
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
    """Every attribute a model-graph class declares needs a row, and every row must name something real."""
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
        self.covered += 1
        self.__dict__["key"] = 1


class Child(Base):
    extra: int


class External(BaseModel):
    ignored: int


class Other(typing.NamedTuple):
    value: int
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
        row("Parser", "results"),
    )

    problems = checker.table_problems(
        rows=rows, declaration_paths=[declarations], dict_keys_written=frozenset({"_WRITTEN_KEY"})
    )

    assert _findings(problems) == snapshot([
        (
            30,
            "Child",
            "extra",
            "Child.extra has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            17,
            "DataModel",
            "fresh",
            "DataModel.fresh has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            18,
            "DataModel",
            "annotated",
            "DataModel.annotated has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            22,
            "DataModel",
            "later",
            "DataModel.later has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            24,
            "DataModel",
            "first",
            "DataModel.first has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            24,
            "DataModel",
            "second",
            "DataModel.second has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
        ),
        (
            23,
            "DataModel",
            "dynamic",
            "DataModel.dynamic has no ATTRIBUTE_FACTS row in src/datamodel_code_generator/parser/_facts.py",
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


def test_unlisted_parser_hooks_are_reported(tmp_path: Path) -> None:
    """A model method that parser code calls and that writes a fact must be listed as a hook."""
    checker = _load_checker()
    parser_source = tmp_path / "parser.py"
    parser_source.write_text(
        """
def run(model):
    model.new_hook()
    model.harmless()
    model.process_const()
    model.render()
    model.indirect()
    if hasattr(model, "probe"):
        getattr(model, "dynamic_hook")()
"""
    )
    model_source = tmp_path / "model.py"
    model_source.write_text(
        """
class Model:
    def new_hook(self):
        self.required = True

    def harmless(self):
        return self.required

    def process_const(self):
        self.nullable = True

    def unused(self):
        self.required = False

    def render(self):
        self.required = False

    def indirect(self):
        self._writes_later()

    def _writes_later(self):
        self.required = False

    def probe(self):
        self.required = False

    def dynamic_hook(self):
        self.required = False
""",
    )

    assert checker.unlisted_parser_hooks([parser_source], [model_source]) == snapshot([
        "dynamic_hook",
        "indirect",
        "new_hook",
        "probe",
    ])
    listed = (
        checker.PARSER_HOOK_METHODS
        | checker.RENDER_STATE_WRITERS
        | checker.CONSTRUCTION_METHODS
        | checker.RENDER_ENTRY_POINTS.keys()
    )
    assert checker.undefined_listed_names([model_source]) == sorted(listed - {"process_const", "render"})
    assert checker.undefined_listed_names() == []


def test_one_model_file_checks_against_the_whole_hook_set() -> None:
    """Checking a single model module uses every model module's hooks, so its entries are neither new nor stale."""
    checker = _load_checker()
    model_root = checker.ROOT / "src" / "datamodel_code_generator" / "model"

    violations = checker.check_paths([model_root / "pydantic_v2" / "root_model.py", model_root / "msgspec.py"])

    assert [violation.format() for violation in violations] == snapshot([])


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
        pytest.param('GENERATION_STORE_MUTATION_METHODS = {"set_fields"}', id="set"),
        pytest.param("GENERATION_STORE_MUTATION_METHODS = frozenset(NAMES)", id="not-literal"),
        pytest.param("GENERATION_STORE_MUTATION_METHODS = frozenset({1})", id="not-strings"),
        pytest.param("OTHER = frozenset()", id="missing"),
    ],
)
def test_store_api_list_must_be_literal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, assignment: str, request: pytest.FixtureRequest
) -> None:
    """The checker reads the store mutation API as a literal set of names."""
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
            "set": "['set_fields']",
            "not-literal": "TypeError: GENERATION_STORE_MUTATION_METHODS must be a literal frozenset of strings",
            "not-strings": "TypeError: GENERATION_STORE_MUTATION_METHODS must contain only literal strings",
            "missing": "RuntimeError: GENERATION_STORE_MUTATION_METHODS was not found",
        })[request.node.callspec.id]
    )
