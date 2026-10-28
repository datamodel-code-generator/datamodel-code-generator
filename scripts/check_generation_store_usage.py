"""Reject writes of generation facts that bypass GenerationStore.

The checker reads two tables with ``ast``, so it never imports the package: the store mutation API
(``GENERATION_STORE_MUTATION_METHODS`` in ``parser/generation.py``) and the attribute-to-fact table
(``ATTRIBUTE_FACTS`` in ``parser/_facts.py``). It scans

* ``parser/`` (except the store and its runtime write guard) for writes of fact and presentation attributes, list
  and dict mutators on them, and direct reference and model-list mutations;
* the parser-facing hooks of ``model/`` and ``types.py`` (``PARSER_HOOK_METHODS``, ``RENDER_STATE_WRITERS`` and the
  helpers they call on ``self`` or ``cls``) for the same writes, including writes to ``self``;
* all three for caches kept on model objects: ``__dict__`` entries, cache attributes and ``cached_property``.

Every finding is keyed by (path, function, target) without line numbers. ``scripts/generation_store_allowlist.toml``
lists the findings that existed when the checker learned them; a new finding fails, and so does an entry that no
longer matches (the list only shrinks). ``--write-allowlist`` creates the file, or lowers it after writes were routed.

The same list serves the runtime write guard (``DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES``, ``parser/_finality.py``),
which reports writes by file and line: ``runtime_writes`` names them as the scan does, and ``runtime_problems`` returns
the writes of the package that the list does not name. A write only the guard sees has an entry whose value is its
witness, the arguments of one ``datamodel-codegen`` run that makes it; ``--witnesses`` runs them and reports the
entries whose witness no longer writes them. When the scan learns to find such a write, ``--write-allowlist`` turns its
witness into a count entry, the only move that may raise the counted total.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field, replace
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import TYPE_CHECKING, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOT = Path("src/datamodel_code_generator")
DEFAULT_TARGET = SOURCE_ROOT / "parser"
DEFAULT_TARGETS = (DEFAULT_TARGET, SOURCE_ROOT / "model", SOURCE_ROOT / "types.py")
GENERATION_STORE_MODULE = SOURCE_ROOT / "parser" / "generation.py"
WRITE_GUARD_MODULE = SOURCE_ROOT / "parser" / "_finality.py"
FACTS_MODULE = SOURCE_ROOT / "parser" / "_facts.py"
DECLARATION_MODULES = (SOURCE_ROOT / "types.py", SOURCE_ROOT / "reference.py", SOURCE_ROOT / "model")
DEFAULT_ALLOWLIST = Path("scripts/generation_store_allowlist.toml")
STORE_WRITES_VARIABLE = "DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES"
ALLOWLIST_PATH = DEFAULT_ALLOWLIST
COMPILED_TEMPLATE_DIRECTORY = "_compiled_templates"

Scope: TypeAlias = Literal["parser", "model"]
StoreKey: TypeAlias = tuple[str, str, str]
SequenceOwner: TypeAlias = str

MUTATION_ATTRIBUTES = frozenset({"base_classes", "data_type", "fields", "reference"})
MODEL_COLLECTION_ATTRIBUTES = frozenset({"models", "results"})
MODEL_COLLECTION_HELPERS = {"append": "register_model"}
REFERENCE_METHODS = frozenset(
    {
        "_invalidate_after_mutation",
        "_refresh_after_mutation",
        "remove_reference",
        "replace_children_in_models",
        "replace_children_references",
        "replace_data_type",
        "replace_reference",
        "refresh_after_mutation",
        "set_reference_path",
    },
)
SEQUENCE_OWNERS = frozenset({"base_classes", "fields"})
SEQUENCE_MUTATORS = frozenset({"append", "clear", "extend", "insert", "pop", "remove", "reverse", "sort"})
CONTAINER_MUTATORS = frozenset({
    "add",
    "append",
    "clear",
    "discard",
    "extend",
    "insert",
    "pop",
    "popitem",
    "remove",
    "reverse",
    "setdefault",
    "sort",
    "update",
})
KEYED_MUTATORS = frozenset({"pop", "setdefault"})
SETATTR_FUNCTIONS = frozenset({"setattr", "object.__setattr__"})

PARSER_HOOK_METHODS = frozenset({
    "_apply_literal_default",
    "_copy_data_type",
    "_prepare_dedup_key",
    "add_literal_validation",
    "add_sequence_interface",
    "apply_discriminator_tag",
    "enable_identity_hash",
    "enable_model_keyword_only",
    "enable_structured_default_validation",
    "finalize_required_inherited_field",
    "finalize_sequence_interface",
    "finalize_type_alias_users",
    "force_field_assignment",
    "mark_as_keyword_only",
    "opt_out_of_inherited_tag",
    "prepare_required_inherited_field",
    "refresh_field_config",
    "restore_required_inherited_field_state",
    "set_field_name_bindings",
    "unbind_fields",
})
"""Model, field and data-type methods that parser passes call by name and that change model state.

The checker also scans every model method these reach through calls (any receiver, any depth), except construction,
reference bookkeeping and render entry points. A hook that a store operation runs (``process_const``,
``unconstrain_union_members``, ``assign_forward_reference_fields``) is not listed: it writes inside that operation.
"""

RENDER_STATE_WRITERS = frozenset({
    "_materialize_render_state",
    "_strip_null",
    "_write_pre_render_nullability",
})
"""Private helpers that write rendered state, or reach its one writer, when a pass reads ``type_hint`` or collects
imports."""

CONSTRUCTION_METHODS = frozenset({
    "__deepcopy__",
    "__init__",
    "class_name",
    "copy_deep",
    "create_base_class_model",
    "create_reuse_model",
    "create_typed_extra_field",
    "get_data_type",
    "register_reference",
    "unregister_reference",
})
"""Methods parser code calls that build objects, keep reverse-reference bookkeeping or implement ``class_name``."""

RENDER_ENTRY_POINTS = {
    "get_dedup_key": "renders a model to compare duplicates; its writes are caches, which the cache rules report",
    "prepare_module_code": "plans schema-validator module code at IMPORTS into a cache, which the cache rules report",
    "render": "renders a model's text; render-time writes move into the field render plans (RP14a)",
    "render_module_code": "renders a module's code at L7; render-time writes move into the field render plans (RP14a)",
    "render_type_expr": "renders a type expression frozen at B2 under the render plan check at L7, as `type_hint` does",
}
"""Render-time entry points that parser passes call; they are not hooks, and the hook closure stops at them."""

DYNAMIC_ATTRIBUTE_FUNCTIONS = frozenset({"getattr", "hasattr"})

PARSER_STATE_OWNERS = frozenset({"ModuleContext", "Parser"})
"""Owners whose rows describe parser run state; the model-list rules cover their lists."""

GRAPH_ROOT_CLASSES = frozenset({"DataModel", "DataModelFieldBase", "DataType", "Reference"})
NON_GRAPH_ANNOTATIONS = frozenset({"Callable", "JsonSchemaObject"})
GRAPH_ANNOTATIONS = frozenset({"Any", "BaseClassDataType", "DataModel", "DataModelFieldBase", "DataType", "object"})
SCHEMA_OBJECT_FACTORIES = frozenset({"JsonSchemaObject", "SCHEMA_OBJECT_TYPE"})
SCHEMA_OBJECT_COPIES = frozenset({"copy", "model_copy"})
PACKAGE = "datamodel_code_generator"

ROW_ENUMS = {
    "kind": frozenset({"FactKind"}),
    "facts": frozenset({"Fact", "RenderFact"}),
    "final_after": frozenset({"Phase"}),
    "frozen_at": frozenset({"FreezePoint"}),
    "access": frozenset({"Access"}),
}
WRITE_KINDS = frozenset({"FACT", "PRESENTATION"})
FREEZE_ORDER = ("B1", "B2", "RENDER")
PLAN_FACTS = ("RenderFact.PLANS",)
RENDERED_NULLABILITY = ("Fact.NULLABLE",)
ITEM_ATTRIBUTE = re.compile(r'(\w+)\["([^"]+)"\]')
BARE_TOML_KEY = re.compile(r"[A-Za-z0-9_-]+")
TOML_TABLE = re.compile(r'\[("(?:[^"\\]|\\.)*")\.("(?:[^"\\]|\\.)*")\]')
TOML_ENTRY = re.compile(r'([A-Za-z0-9_-]+|\'[^\']*\'|"(?:[^"\\]|\\.)*") = ("(?:[^"\\]|\\.)*"|\S+)')
ALLOWLIST_HEADER = """\
# Writes that bypass GenerationStore, as scripts/check_generation_store_usage.py finds them.
# Tables are ["path"."function"] with target = count; no line numbers, so unrelated edits do not move entries.
# A target = "arguments" entry is a write that only the runtime guard (DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES)
# sees; its witness is the datamodel-codegen arguments of one run that makes it (`--witnesses` runs them all). When the
# scan learns to find such a write, its witness becomes a count entry, the only move that may raise the counted total.
# Generated once by `python scripts/check_generation_store_usage.py --write-allowlist`; later changes only remove
# entries or lower counts, and a refactor that moves a write renames its entry without raising the total.
# A new write fails the check, and so does an entry that no longer matches.
"""


def _literal_string_set(node: ast.AST) -> frozenset[str]:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "frozenset" and node.args:
        node = node.args[0]
    if not isinstance(node, ast.Set):
        msg = "GENERATION_STORE_MUTATION_METHODS must be a literal frozenset of strings"
        raise TypeError(msg)

    values: set[str] = set()
    for item in node.elts:
        if not isinstance(item, ast.Constant) or not isinstance(item.value, str):
            msg = "GENERATION_STORE_MUTATION_METHODS must contain only literal strings"
            raise TypeError(msg)
        values.add(item.value)
    return frozenset(values)


def _module_assignment(tree: ast.Module, name: str) -> ast.expr | None:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == name:
            return node.value
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            return node.value
    return None


def _load_generation_store_mutation_methods() -> frozenset[str]:
    generation_store_module = ROOT / GENERATION_STORE_MODULE
    tree = ast.parse(generation_store_module.read_text(), filename=str(generation_store_module))
    if (value := _module_assignment(tree, "GENERATION_STORE_MUTATION_METHODS")) is None:
        msg = "GENERATION_STORE_MUTATION_METHODS was not found"
        raise RuntimeError(msg)
    return _literal_string_set(value)


GENERATION_STORE_MUTATION_METHODS = _load_generation_store_mutation_methods()


def _ensure_generation_store_api(method_name: str) -> str:
    if method_name not in GENERATION_STORE_MUTATION_METHODS:
        msg = f"{method_name!r} is not a registered GenerationStore mutation API"
        raise RuntimeError(msg)
    return method_name


@dataclass(frozen=True)
class FactRow:
    """One ``ATTRIBUTE_FACTS`` row; enum values keep their member names."""

    owner: str
    attribute: str
    kind: str
    facts: tuple[str, ...]
    final_after: str
    frozen_at: str | None
    store_ops: tuple[str, ...]
    access: str

    @property
    def item(self) -> tuple[str, str] | None:
        """Return ``(attribute, key)`` for a row of one dict entry."""
        match = ITEM_ATTRIBUTE.fullmatch(self.attribute)
        return (match.group(1), match.group(2)) if match else None

    @property
    def declared_name(self) -> str:
        """Return the attribute a class declares for this row."""
        return item[0] if (item := self.item) else self.attribute


def _enum_members(tree: ast.Module) -> dict[str, tuple[str, ...]]:
    return {
        node.name: tuple(
            target.id
            for statement in node.body
            if isinstance(statement, ast.Assign)
            for target in statement.targets
            if isinstance(target, ast.Name)
        )
        for node in tree.body
        if isinstance(node, ast.ClassDef)
        and any(isinstance(base, ast.Name) and base.id == "Enum" for base in node.bases)
    }


def _row_fields(tree: ast.Module) -> tuple[list[str], dict[str, ast.expr]]:
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == "AttributeFact":
            names: list[str] = []
            defaults: dict[str, ast.expr] = {}
            for statement in node.body:
                if isinstance(statement, ast.AnnAssign) and isinstance(statement.target, ast.Name):
                    names.append(statement.target.id)
                    if statement.value is not None:
                        defaults[statement.target.id] = statement.value
            return names, defaults
    msg = "AttributeFact was not found"
    raise RuntimeError(msg)


def _row_value(node: ast.expr, field_name: str, enums: dict[str, tuple[str, ...]]) -> object:
    match node:
        case ast.Constant(value=str() | None as literal):
            return literal
        case ast.Tuple(elts=items):
            return tuple(_row_value(item, field_name, enums) for item in items)
        case ast.Attribute(value=ast.Name(id=enum_name), attr=member) if enum_name in ROW_ENUMS.get(
            field_name, frozenset()
        ) and member in enums.get(enum_name, ()):
            return f"{enum_name}.{member}"
    msg = f"ATTRIBUTE_FACTS field {field_name!r} must be a literal of its type: {ast.unparse(node)}"
    raise TypeError(msg)


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, tuple) and all(isinstance(item, str) for item in value):
        return value
    msg = f"ATTRIBUTE_FACTS field {field_name!r} must be a tuple"
    raise TypeError(msg)


def _string(value: object, field_name: str) -> str:
    if isinstance(value, str):
        return value
    msg = f"ATTRIBUTE_FACTS field {field_name!r} must be a string or an enum member"
    raise TypeError(msg)


def _member(value: str) -> str:
    return value.partition(".")[2]


def _load_freeze_points(tree: ast.Module, enums: dict[str, tuple[str, ...]]) -> dict[str, str]:
    if not isinstance(table := _module_assignment(tree, "RENDER_FACT_FREEZE_POINTS"), ast.Dict):
        msg = "RENDER_FACT_FREEZE_POINTS must be a literal dict"
        raise TypeError(msg)
    points: dict[str, str] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        fact = _row_value(key, "facts", enums) if key is not None else None
        point = _row_value(value, "frozen_at", enums) if isinstance(fact, str) else None
        if not (isinstance(fact, str) and fact.startswith("RenderFact.") and isinstance(point, str)):
            msg = "RENDER_FACT_FREEZE_POINTS maps RenderFact members to FreezePoint members"
            raise TypeError(msg)
        points[_member(fact)] = _member(point)
    if missing := sorted(set(enums.get("RenderFact", ())) - points.keys()):
        msg = f"RENDER_FACT_FREEZE_POINTS misses {', '.join(missing)}"
        raise ValueError(msg)
    return points


def _expected_frozen_at(row: FactRow, freeze_points: dict[str, str]) -> str | None:
    """Return the latest freeze point among the row's facts; rendered nullability is written at B2."""
    if row.kind not in WRITE_KINDS:
        return None
    points = [freeze_points[_member(fact)] if fact.startswith("RenderFact.") else "B1" for fact in row.facts] or ["B1"]
    if row.facts == RENDERED_NULLABILITY and row.final_after in {"RENDER_PREPARATION", "RENDER"}:
        points.append("B2")
    return max(points, key=FREEZE_ORDER.index)


def _row_problem(row: FactRow, freeze_points: dict[str, str]) -> str | None:
    enums = {fact.partition(".")[0] for fact in row.facts}
    problem = None
    if row.kind == "BOOKKEEPING" and (row.facts or row.store_ops):
        problem = "bookkeeping rows name no facts and no store operations"
    elif row.kind == "FACT" and "Fact" not in enums and row.facts != PLAN_FACTS:
        problem = "fact rows name a Fact; only plan rows name RenderFact.PLANS alone"
    elif row.kind == "PRESENTATION" and "Fact" in enums and row.facts != RENDERED_NULLABILITY:
        problem = "presentation rows name RenderFacts; only rendered nullability names Fact.NULLABLE"
    elif row.kind == "CACHE" and "Fact" in enums:
        problem = "cache rows name no Fact"
    elif row.frozen_at != (expected := _expected_frozen_at(row, freeze_points)):
        problem = f"frozen_at must be {expected}, the latest freeze point among its facts"
    return problem


def _make_row(values: dict[str, object], freeze_points: dict[str, str]) -> FactRow:
    frozen_at = values["frozen_at"]
    row = FactRow(
        owner=_string(values["owner"], "owner"),
        attribute=_string(values["attribute"], "attribute"),
        kind=_member(_string(values["kind"], "kind")),
        facts=_strings(values["facts"], "facts"),
        final_after=_member(_string(values["final_after"], "final_after")),
        frozen_at=None if frozen_at is None else _member(_string(frozen_at, "frozen_at")),
        store_ops=_strings(values["store_ops"], "store_ops"),
        access=_member(_string(values["access"], "access")),
    )
    if problem := _row_problem(row, freeze_points):
        msg = f"row {row.owner}.{row.attribute}: {problem}"
        raise ValueError(msg)
    if (row.access == "ITEM") != (row.item is not None):
        msg = f'row {row.owner}.{row.attribute}: write a dict entry as name["key"] with access=Access.ITEM'
        raise ValueError(msg)
    for store_op in row.store_ops:
        _ensure_generation_store_api(store_op)
    return row


def fact_vocabulary(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Return the members of every enum in ``parser/_facts.py`` in declaration order."""
    facts_module = path or ROOT / FACTS_MODULE
    return _enum_members(ast.parse(facts_module.read_text(), filename=str(facts_module)))


def load_attribute_facts(path: Path | None = None) -> tuple[FactRow, ...]:
    """Read ``ATTRIBUTE_FACTS`` from ``parser/_facts.py`` without importing it."""
    facts_module = path or ROOT / FACTS_MODULE
    tree = ast.parse(facts_module.read_text(), filename=str(facts_module))
    enums = _enum_members(tree)
    names, defaults = _row_fields(tree)
    freeze_points = _load_freeze_points(tree, enums)
    if not isinstance(table := _module_assignment(tree, "ATTRIBUTE_FACTS"), ast.Tuple):
        msg = "ATTRIBUTE_FACTS must be a literal tuple"
        raise TypeError(msg)
    rows: list[FactRow] = []
    seen: set[tuple[str, str]] = set()
    for entry in table.elts:
        if not (isinstance(entry, ast.Call) and isinstance(entry.func, ast.Name) and entry.func.id == "AttributeFact"):
            msg = f"ATTRIBUTE_FACTS rows must be AttributeFact(...) calls: {ast.unparse(entry)}"
            raise TypeError(msg)
        arguments = {
            **dict(zip(names, entry.args, strict=False)),
            **{keyword.arg: keyword.value for keyword in entry.keywords if keyword.arg is not None},
        }
        if missing := [name for name in names if name not in arguments and name not in defaults]:
            msg = f"ATTRIBUTE_FACTS row {ast.unparse(entry)} misses {', '.join(missing)}"
            raise TypeError(msg)
        row = _make_row(
            {name: _row_value(arguments.get(name) or defaults[name], name, enums) for name in names}, freeze_points
        )
        if (row.owner, row.attribute) in seen:
            msg = f"ATTRIBUTE_FACTS has two rows for {row.owner}.{row.attribute}"
            raise ValueError(msg)
        seen.add((row.owner, row.attribute))
        rows.append(row)
    return tuple(rows)


@dataclass(frozen=True)
class Guards:
    """What the visitor checks, indexed from ``ATTRIBUTE_FACTS`` by the name a write uses."""

    attributes: dict[str, tuple[FactRow, ...]]
    containers: frozenset[str]
    caches: dict[str, tuple[FactRow, ...]]
    dict_keys: dict[str, tuple[FactRow, ...]]
    items: dict[tuple[str, str], FactRow]

    @classmethod
    def from_rows(cls, rows: Iterable[FactRow]) -> Guards:
        """Index the rows of model-graph owners."""
        attributes: dict[str, list[FactRow]] = {}
        caches: dict[str, list[FactRow]] = {}
        dict_keys: dict[str, list[FactRow]] = {}
        items: dict[tuple[str, str], FactRow] = {}
        containers: set[str] = set()
        for row in rows:
            if row.owner in PARSER_STATE_OWNERS:
                continue
            if row.access in {"DICT_KEY", "CACHED_PROPERTY"}:
                dict_keys.setdefault(row.attribute, []).append(row)
            elif item := row.item:
                items[item] = row
            elif row.kind == "CACHE":
                caches.setdefault(row.attribute, []).append(row)
            elif row.kind in WRITE_KINDS:
                attributes.setdefault(row.attribute, []).append(row)
                if row.access == "CONTAINER":
                    containers.add(row.attribute)
        return cls(
            attributes={name: tuple(value) for name, value in attributes.items()},
            containers=frozenset(containers),
            caches={name: tuple(value) for name, value in caches.items()},
            dict_keys={name: tuple(value) for name, value in dict_keys.items()},
            items=items,
        )


ATTRIBUTE_FACTS = load_attribute_facts()
GUARDS = Guards.from_rows(ATTRIBUTE_FACTS)


@dataclass(frozen=True)
class Violation:
    """A write that should go through GenerationStore."""

    path: Path
    line: int
    column: int
    message: str
    function: str = "<module>"
    target: str = ""

    @property
    def key(self) -> StoreKey:
        """Return the line-independent allowlist key."""
        return (_display_path(self.path), self.function, self.target)

    def format(self) -> str:
        """Format violation for CLI output."""
        return f"{self.path.as_posix()}:{self.line}:{self.column}: {self.message} (in {self.function})"


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    names: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        names.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        names.append(current.id)
    names.reverse()
    return tuple(names)


def _sequence_owner(node: ast.AST) -> SequenceOwner | None:
    if isinstance(node, ast.Attribute) and node.attr in SEQUENCE_OWNERS | MODEL_COLLECTION_ATTRIBUTES:
        return node.attr
    if isinstance(node, ast.Subscript):
        return _sequence_owner(node.value)
    return None


def _key_text(node: ast.AST | None) -> str:
    match node:
        case ast.Constant(value=str() as text):
            return text
        case ast.Name(id=name) | ast.Attribute(attr=name) if name.isupper():
            return name
        case ast.Slice():
            return ":"
    return "..."


def _item_target(attribute: str, method: str, key: ast.AST | None, *, entry: bool = False) -> str:
    quoted = isinstance(key, ast.Constant) and isinstance(key.value, str)
    text = f'"{_key_text(key)}"' if quoted else _key_text(key)
    if not method or entry:
        return f"{attribute}[{text}].{method}()" if method else f"{attribute}[{text}]"
    return f"{attribute}.{method}({text})" if key is not None else f"{attribute}.{method}()"


def _bound_targets(target: ast.AST) -> Iterator[ast.AST]:
    """Yield the targets an assignment binds, looking into tuple, list and starred targets only."""
    match target:
        case ast.Tuple(elts=items) | ast.List(elts=items):
            for item in items:
                yield from _bound_targets(item)
        case ast.Starred(value=starred):
            yield from _bound_targets(starred)
        case _:
            yield target


def _indexed_object(node: ast.expr) -> tuple[ast.expr, ast.AST | None]:
    """Return the object a subscript chain indexes and the key next to it: ``x.extras["a"]["b"]`` gives ``"a"``."""
    key = None
    while isinstance(node, ast.Subscript):
        node, key = node.value, node.slice
    return node, key


def _annotation_names(node: ast.expr | None) -> set[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        try:
            node = ast.parse(node.value, mode="eval").body
        except SyntaxError:
            return set()
    if node is None:
        return set()
    return {
        child.id if isinstance(child, ast.Name) else child.attr
        for child in ast.walk(node)
        if isinstance(child, (ast.Name, ast.Attribute))
    }


def _is_non_graph_annotation(node: ast.expr | None) -> bool:
    names = _annotation_names(node)
    return bool(names & NON_GRAPH_ANNOTATIONS) and not names & GRAPH_ANNOTATIONS


def _is_schema_object_value(node: ast.expr, schema_names: set[str]) -> bool:
    match node:
        case ast.Call(func=ast.Attribute(attr="model_validate", value=factory)):
            return bool(set(_attribute_chain(factory)) & SCHEMA_OBJECT_FACTORIES)
        case ast.Call(func=ast.Attribute(attr="_schema_object", value=ast.Name(id="self"))):
            return True
        case ast.Call(func=ast.Attribute(attr=method, value=ast.Name(id=name))) if method in SCHEMA_OBJECT_COPIES:
            return name in schema_names
    return False


def _facts_text(rows: Iterable[FactRow]) -> str:
    return ", ".join(sorted({fact for row in rows for fact in row.facts})) or "an option copied at construction"


def _write_message(target: str, rows: tuple[FactRow, ...]) -> str:
    if all(row.store_ops for row in rows):
        store_ops = sorted({store_op for row in rows for store_op in row.store_ops})
        return f"use GenerationStore.{'() or '.join(store_ops)}() instead of writing {target}"
    return f"{target} changes {_facts_text(rows)} outside GenerationStore operations"


def _cache_message(target: str, rows: Iterable[FactRow]) -> str:
    owners = ", ".join(sorted({row.owner for row in rows}))
    return f"{target} caches state on a mutable {owners}; keep derived values in a per-parse store memo"


@dataclass(frozen=True)
class _Alias:
    """A local name for a guarded container, or for one entry of it (``key``)."""

    attribute: str
    key: ast.AST | None = None


@dataclass
class _FunctionState:
    schema_names: set[str] = field(default_factory=set)
    aliases: dict[str, _Alias] = field(default_factory=dict)


class GenerationStoreUsageVisitor(ast.NodeVisitor):
    """AST visitor for code that must change generation facts through GenerationStore."""

    def __init__(
        self,
        path: Path,
        scope: Scope = "parser",
        *,
        hooks: frozenset[str] = frozenset(),
        setattr_aliases: frozenset[str] = frozenset(),
    ) -> None:
        """Create a visitor for ``path`` checked in ``scope``."""
        self.path = path
        self.scope = scope
        self.hooks = hooks
        self.setattr_functions = SETATTR_FUNCTIONS | setattr_aliases
        self.violations: list[Violation] = []
        self.dict_keys_written: set[str] = set()
        self._qualname: list[str] = []
        self._functions: list[str] = []
        self._states: list[_FunctionState] = [_FunctionState()]

    @property
    def _state(self) -> _FunctionState:
        return self._states[-1]

    @property
    def _checks_fact_writes(self) -> bool:
        return self.scope == "parser" or any(name in self.hooks for name in self._functions)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track the qualified name and report cached properties of model classes."""
        self._qualname.append(node.name)
        if self.scope == "model":
            for statement in node.body:
                if isinstance(statement, ast.FunctionDef) and any(
                    _attribute_chain(decorator)[-1:] == ("cached_property",) for decorator in statement.decorator_list
                ):
                    self._qualname.append(statement.name)
                    self._report_dict_key(statement, statement.name, "@cached_property")
                    self._qualname.pop()
        self.generic_visit(node)
        self._qualname.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        """Track the qualified name, schema-object parameters and local aliases of a function."""
        self._qualname.append(node.name)
        self._functions.append(node.name)
        state = _FunctionState(schema_names=set(self._state.schema_names))
        arguments = node.args
        for argument in (*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs):
            if _is_non_graph_annotation(argument.annotation):
                state.schema_names.add(argument.arg)
            else:
                state.schema_names.discard(argument.arg)
        self._states.append(state)
        self.generic_visit(node)
        self._states.pop()
        self._functions.pop()
        self._qualname.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track an async function like any other."""
        self.visit_FunctionDef(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Check assignment targets and record local aliases."""
        for target in node.targets:
            self._check_target(target)
            self._record_binding(target, node.value)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Check annotated assignment targets."""
        self._check_target(node.target)
        if node.value is not None:
            self._record_binding(node.target, node.value)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        """Check augmented assignment targets, including in-place operators on aliases and ``__dict__``."""
        operator = ast.unparse(node).removeprefix(ast.unparse(node.target)).split()[0]
        match node.target:
            case ast.Name(id=name) if (alias := self._state.aliases.get(name)) is not None:
                written = alias.attribute if alias.key is None else _item_target(alias.attribute, "", alias.key)
                if alias.attribute == "__dict__":
                    key = _key_text(alias.key)
                    self._report_dict_key(node, key, f"{written} {operator}", computed=key == "...")
                else:
                    self._check_container_write(node, alias.attribute, f"{written} {operator}", alias.key)
            case ast.Attribute(attr="__dict__", value=receiver) if not self._excluded(receiver):
                self._report_dict_key(node, "...", f"__dict__ {operator}", computed=True)
            case ast.Name():
                self._unbind(node.target)
            case _:
                self._check_target(node.target)
        self.generic_visit(node)

    def visit_For(self, node: ast.For | ast.AsyncFor) -> None:
        """Forget what the loop target named before."""
        self._unbind(node.target)
        self.generic_visit(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        """Forget what the loop target named before."""
        self.visit_For(node)

    def visit_withitem(self, node: ast.withitem) -> None:
        """Forget what a ``with ... as`` target named before."""
        if node.optional_vars is not None:
            self._unbind(node.optional_vars)
        self.generic_visit(node)

    def visit_comprehension(self, node: ast.comprehension) -> None:
        """Forget what a comprehension target named before."""
        self._unbind(node.target)
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        """Forget what an ``except ... as`` name named before."""
        if node.name:
            self._unbind(ast.Name(id=node.name))
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        """Check deleted attributes and items."""
        for target in node.targets:
            self._check_target(target)
        self.generic_visit(node)

    def visit_NamedExpr(self, node: ast.NamedExpr) -> None:
        """Record aliases bound by assignment expressions."""
        self._record_binding(node.target, node.value)
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        """Check mutating method calls and ``setattr`` with a literal name."""
        if isinstance(node.func, ast.Attribute):
            self._check_method_call(node, node.func.attr, node.func.value)
        if ".".join(_attribute_chain(node.func)) in self.setattr_functions and len(node.args) > 1:
            match node.args[1]:
                case ast.Constant(value=str() as attribute):
                    self._check_attribute_write(node, node.args[0], attribute)
                case _ if self._checks_fact_writes and not self._excluded(node.args[0]):
                    self._add(
                        node, "setattr(...)", "setattr with a computed name may write any fact; name the attribute"
                    )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check direct ``Reference.children`` reads."""
        if node.attr == "children" and isinstance(node.ctx, ast.Load) and self._checks_fact_writes:
            self._add(node, "children (read)", "use GenerationIndex reverse-edge queries instead of Reference.children")
        self.generic_visit(node)

    def _check_method_call(self, node: ast.Call, method: str, owner: ast.expr) -> None:
        if (sequence_owner := _sequence_owner(owner)) and method in SEQUENCE_MUTATORS:
            if self._checks_fact_writes:
                helper = self._sequence_helper(sequence_owner, method)
                self._add(
                    node,
                    f"{sequence_owner}.{method}()",
                    f"use GenerationStore.{helper}() instead of mutating {sequence_owner}",
                )
            return
        if method in REFERENCE_METHODS:
            if self._checks_fact_writes:
                self._add(node, f"{method}()", f"use GenerationStore API instead of {method}()")
            return
        if method not in CONTAINER_MUTATORS:
            return
        container, entry_key = _indexed_object(owner)
        if entry := container is not owner:
            key = entry_key
        else:
            key = node.args[0] if method in KEYED_MUTATORS and node.args else None
        self._check_container(node, container, method, key, entry=entry)

    def _check_target(self, target: ast.AST) -> None:
        if self._check_legacy_assignment(target):
            return
        match target:
            case ast.Tuple(elts=items) | ast.List(elts=items):
                for item in items:
                    self._check_target(item)
            case ast.Starred(value=starred):
                self._check_target(starred)
            case ast.Attribute(attr=attribute, value=receiver):
                self._check_attribute_write(target, receiver, attribute)
            case ast.Subscript():
                container, key = _indexed_object(target)
                self._check_container(target, container, "", key)

    def _check_container(
        self, node: ast.AST, container: ast.expr, method: str, key: ast.AST | None, *, entry: bool = False
    ) -> None:
        match container:
            case ast.Attribute(attr="__dict__", value=receiver) | ast.Call(func=ast.Name(id="vars"), args=[receiver]):
                if not self._excluded(receiver):
                    self._report_dict_entry(node, method, key, entry=entry)
            case ast.Attribute(attr=attribute, value=receiver) if not self._excluded(receiver):
                self._check_container_write(node, attribute, _item_target(attribute, method, key, entry=entry), key)
            case ast.Name(id=name) if (alias := self._state.aliases.get(name)) is not None:
                if alias.key is not None:
                    key, entry = alias.key, bool(method)
                if alias.attribute == "__dict__":
                    self._report_dict_entry(node, method, key, entry=entry)
                else:
                    target = _item_target(alias.attribute, method, key, entry=entry)
                    self._check_container_write(node, alias.attribute, target, key)

    def _check_legacy_assignment(self, target: ast.AST) -> bool:
        if not self._checks_fact_writes:
            return False
        if owner := _sequence_owner(target):
            helper = self._sequence_helper(owner, "assign")
            self._add(target, owner, f"use GenerationStore.{helper}() instead of assigning {owner}")
            return True
        if isinstance(target, ast.Attribute) and (
            target.attr in MUTATION_ATTRIBUTES
            or (isinstance(target.value, ast.Attribute) and target.value.attr == "reference")
        ):
            legacy_target = target.attr if target.attr in MUTATION_ATTRIBUTES else f"reference.{target.attr}"
            name = ".".join(_attribute_chain(target)[-2:])
            self._add(target, legacy_target, f"use GenerationStore API instead of assigning {name}")
            return True
        return False

    def _check_attribute_write(self, node: ast.AST, receiver: ast.expr, attribute: str) -> None:
        if self._excluded(receiver):
            return
        if rows := GUARDS.caches.get(attribute):
            self._add(node, attribute, _cache_message(attribute, rows))
        elif self._checks_fact_writes and (rows := GUARDS.attributes.get(attribute)):
            self._add(node, attribute, _write_message(attribute, rows))

    def _check_container_write(self, node: ast.AST, attribute: str, target: str, key: ast.AST | None) -> None:
        if rows := GUARDS.caches.get(attribute):
            self._add(node, target, _cache_message(target, rows))
        elif self._checks_fact_writes and attribute in GUARDS.containers:
            item = GUARDS.items.get((attribute, _key_text(key)))
            self._add(node, target, _write_message(target, (item,) if item else GUARDS.attributes[attribute]))

    def _report_dict_entry(self, node: ast.AST, method: str, key: ast.AST | None, *, entry: bool = False) -> None:
        computed = _key_text(key) == "..."
        target = _item_target("__dict__", method, key, entry=entry)
        self._report_dict_key(node, _key_text(key), target, computed=computed)

    def _report_dict_key(self, node: ast.AST, key: str, target: str, *, computed: bool = False) -> None:
        if not computed:
            self.dict_keys_written.add(key)
        rows = () if computed else GUARDS.dict_keys.get(key, ())
        if rows and all(row.kind == "BOOKKEEPING" for row in rows):
            return
        if computed:
            message = f"{target} writes a computed __dict__ key that ATTRIBUTE_FACTS cannot classify"
        elif not rows:
            message = f"{target} is a __dict__ entry that {FACTS_MODULE.name} ATTRIBUTE_FACTS does not classify"
        elif any(row.kind == "CACHE" for row in rows):
            message = _cache_message(target, rows)
        else:
            message = _write_message(target, rows)
        self._add(node, target, message)

    def _unbind(self, target: ast.AST) -> None:
        for node in _bound_targets(target):
            if isinstance(node, ast.Name):
                self._state.aliases.pop(node.id, None)
                self._state.schema_names.discard(node.id)

    def _record_binding(self, target: ast.AST, value: ast.expr) -> None:
        schema = _is_schema_object_value(value, self._state.schema_names)
        alias = None if schema else self._alias_of(value)
        self._unbind(target)
        if not isinstance(target, ast.Name):
            return
        if schema:
            self._state.schema_names.add(target.id)
        elif alias is not None:
            self._state.aliases[target.id] = alias

    def _alias_of(self, value: ast.expr) -> _Alias | None:
        """Return the guarded container, or the entry of one, that ``value`` names, following existing aliases."""
        match value:
            case ast.Name(id=name):
                return self._state.aliases.get(name)
            case ast.Subscript():
                container, key = _indexed_object(value)
                if (alias := self._alias_of(container)) is None:
                    return None
                return alias if alias.key is not None else _Alias(alias.attribute, key)
            case (
                ast.Attribute(attr=attribute, value=receiver)
                | ast.Call(func=ast.Name(id="getattr"), args=[receiver, ast.Constant(value=str() as attribute), *_])
            ) if self._is_guarded_container(attribute, receiver):
                return _Alias(attribute)
        return None

    def _is_guarded_container(self, attribute: str, receiver: ast.expr) -> bool:
        return not self._excluded(receiver) and (
            attribute in {"__dict__", *GUARDS.caches} or (self._checks_fact_writes and attribute in GUARDS.containers)
        )

    def _excluded(self, receiver: ast.expr) -> bool:
        match receiver:
            case ast.Name(id="self" | "cls"):
                return self.scope == "parser"
            case ast.Name(id=name):
                return name in self._state.schema_names
        return False

    def _add(self, node: ast.AST, target: str, message: str) -> None:
        self.violations.append(
            Violation(
                path=self.path,
                line=getattr(node, "lineno", 0),
                column=getattr(node, "col_offset", 0) + 1,
                message=message,
                function=".".join(self._qualname) or "<module>",
                target=target,
            )
        )

    @staticmethod
    def _sequence_helper(owner: SequenceOwner, method: str) -> str:
        if owner in MODEL_COLLECTION_ATTRIBUTES:
            helper = MODEL_COLLECTION_HELPERS.get(method, "register_model")
            return _ensure_generation_store_api(helper)
        if owner == "fields":
            helper = {
                "assign": "set_fields",
                "append": "append_field",
                "insert": "insert_field",
                "remove": "remove_field",
            }.get(method, "set_fields")
            return _ensure_generation_store_api(helper)
        return _ensure_generation_store_api("set_base_classes")


def iter_python_files(paths: Iterable[Path]) -> list[Path]:
    """Return checked Python files below ``paths``, without the store and its runtime write guard."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            files.append(path)
    store_modules = {(ROOT / GENERATION_STORE_MODULE).resolve(), (ROOT / WRITE_GUARD_MODULE).resolve()}
    return [
        path for path in files if path.resolve() not in store_modules and COMPILED_TEMPLATE_DIRECTORY not in path.parts
    ]


def classify_path(path: Path) -> Scope:
    """Return the scope a file is checked in: ``model`` for ``model/`` and ``types.py``, else ``parser``."""
    try:
        relative = path.resolve().relative_to((ROOT / SOURCE_ROOT).resolve())
    except ValueError:
        return "parser"
    return "model" if relative.parts[0] == "model" or relative.parts == ("types.py",) else "parser"


def _setattr_aliases(tree: ast.Module) -> frozenset[str]:
    return frozenset(
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign) and ".".join(_attribute_chain(node.value)) in SETATTR_FUNCTIONS
        for target in node.targets
        if isinstance(target, ast.Name)
    )


FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef
ModelFunctions: TypeAlias = dict[str, list[tuple[Path, FunctionNode]]]


def _parse_files(paths: Iterable[Path]) -> list[tuple[Path, ast.Module]]:
    return [(path, ast.parse(path.read_text(), filename=str(path))) for path in iter_python_files(paths)]


def _default_model_paths() -> list[Path]:
    return [ROOT / target for target in DEFAULT_TARGETS[1:]]


def _functions_by_name(modules: Iterable[tuple[Path, ast.Module]]) -> ModelFunctions:
    functions: ModelFunctions = {}
    seen: set[Path] = set()
    for path, tree in modules:
        if (resolved := path.resolve()) in seen:
            continue
        seen.add(resolved)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.setdefault(node.name, []).append((path, node))
    return functions


def _called_names(node: ast.AST) -> Iterator[str]:
    """Yield the names ``node`` calls, and the attribute names it reads through ``getattr``/``hasattr``."""
    for call in ast.walk(node):
        if not isinstance(call, ast.Call):
            continue
        match call.func:
            case ast.Attribute(attr=name) | ast.Name(id=name) if name not in DYNAMIC_ATTRIBUTE_FUNCTIONS:
                yield name
            case ast.Name():
                if (
                    len(call.args) > 1
                    and isinstance(call.args[1], ast.Constant)
                    and isinstance(call.args[1].value, str)
                ):
                    yield call.args[1].value


def _call_closure(roots: Iterable[str], functions: ModelFunctions) -> frozenset[str]:
    """Return ``roots`` and every model function they reach through calls, stopping at excluded methods."""
    stop = CONSTRUCTION_METHODS | REFERENCE_METHODS | RENDER_ENTRY_POINTS.keys()
    reached: set[str] = set()
    pending = list(roots)
    while pending:
        if (name := pending.pop()) in reached:
            continue
        reached.add(name)
        pending.extend(
            called
            for _, function in functions.get(name, ())
            for called in _called_names(function)
            if called in functions and called not in stop
        )
    return frozenset(reached)


def hook_names(modules: Iterable[tuple[Path, ast.Module]]) -> frozenset[str]:
    """Return the hook methods and every model function they reach through calls."""
    return _call_closure(PARSER_HOOK_METHODS | RENDER_STATE_WRITERS, _functions_by_name(modules))


def _writes_only_hooks_see(path: Path, function: FunctionNode) -> bool:
    counts = []
    for hooks in (frozenset(), frozenset({function.name})):
        visitor = GenerationStoreUsageVisitor(path, "model", hooks=hooks)
        visitor.visit(function)
        counts.append(len(visitor.violations))
    return counts[1] > counts[0]


def unlisted_parser_hooks(
    parser_paths: Iterable[Path] | None = None, model_paths: Iterable[Path] | None = None
) -> list[str]:
    """Return model methods that parser code calls and that reach a fact write no hook list covers."""
    called = {name for _, tree in _parse_files(parser_paths or [ROOT / DEFAULT_TARGET]) for name in _called_names(tree)}
    functions = _functions_by_name(_parse_files(model_paths or _default_model_paths()))
    hooks = _call_closure(PARSER_HOOK_METHODS | RENDER_STATE_WRITERS, functions)
    exempt = hooks | CONSTRUCTION_METHODS | REFERENCE_METHODS | RENDER_ENTRY_POINTS.keys()
    return sorted(
        name
        for name in called & functions.keys() - exempt
        if any(
            _writes_only_hooks_see(path, function)
            for reached in _call_closure([name], functions) - hooks
            for path, function in functions[reached]
        )
    )


def undefined_listed_names(model_paths: Iterable[Path] | None = None) -> list[str]:
    """Return names in the hook, construction and render-entry lists that no model module defines."""
    functions = _functions_by_name(_parse_files(model_paths or _default_model_paths()))
    listed = PARSER_HOOK_METHODS | RENDER_STATE_WRITERS | CONSTRUCTION_METHODS | RENDER_ENTRY_POINTS.keys()
    return sorted(listed - functions.keys())


@dataclass(frozen=True)
class ScanResult:
    """Findings of one scan before the allowlist, and the ``__dict__`` keys it saw written."""

    violations: list[Violation]
    dict_keys_written: frozenset[str]
    scanned_paths: frozenset[str]

    @property
    def counts(self) -> Counter[StoreKey]:
        """Return the findings as allowlist counts."""
        return Counter(violation.key for violation in self.violations)


def scan_files(files: Iterable[tuple[Path, Scope]]) -> ScanResult:
    """Scan explicitly scoped files; hooks come from every model module and the ``model`` files among them."""
    parsed = [(path, scope, ast.parse(path.read_text(), filename=str(path))) for path, scope in files]
    hooks = hook_names([
        *_parse_files(_default_model_paths()),
        *((path, tree) for path, scope, tree in parsed if scope == "model"),
    ])
    violations: list[Violation] = []
    dict_keys_written: set[str] = set()
    for path, scope, tree in parsed:
        visitor = GenerationStoreUsageVisitor(path, scope, hooks=hooks, setattr_aliases=_setattr_aliases(tree))
        visitor.visit(tree)
        violations.extend(visitor.violations)
        dict_keys_written |= visitor.dict_keys_written
    return ScanResult(
        violations=violations,
        dict_keys_written=frozenset(dict_keys_written),
        scanned_paths=frozenset(_display_path(path) for path, _, _ in parsed),
    )


def scan_paths(paths: Iterable[Path]) -> ScanResult:
    """Scan ``paths``, each file in the scope its location gives it."""
    return scan_files((path, classify_path(path)) for path in iter_python_files(paths))


def _toml_string(text: str) -> str:
    return text[1:-1] if text.startswith("'") else json.loads(text) if text.startswith('"') else text


def _allowlist_entries(text: str) -> Iterator[tuple[StoreKey, int | str]]:
    """Yield each entry of the allowlist with its count, or with its witness when only the runtime guard sees it."""
    section: tuple[str, str] | None = None
    for number, line in enumerate(text.splitlines(), start=1):
        if not (stripped := line.strip()) or stripped.startswith("#"):
            continue
        if table := TOML_TABLE.fullmatch(stripped):
            section = (_toml_string(table.group(1)), _toml_string(table.group(2)))
            continue
        if (entry := TOML_ENTRY.fullmatch(stripped)) is None or section is None:
            msg = (
                f'allowlist line {number} is not a ["path"."function"] table, a target = count or a '
                f'target = "witness" entry: {line}'
            )
            raise ValueError(msg)
        target, value = _toml_string(entry.group(1)), entry.group(2)
        if value.startswith('"'):
            yield (*section, target), _toml_string(value)
        elif not value.isdigit() or int(value) < 1:
            msg = f"allowlist count of {section[0]} {section[1]} {target} must be a positive integer"
            raise TypeError(msg)
        else:
            yield (*section, target), int(value)


def parse_allowlist(text: str) -> Counter[StoreKey]:
    """Parse the counted entries of the TOML subset ``format_allowlist`` writes, with the standard library only."""
    return Counter({key: value for key, value in _allowlist_entries(text) if isinstance(value, int)})


def parse_witnesses(text: str) -> dict[StoreKey, str]:
    """Parse the entries that only the runtime guard sees, with their witness arguments."""
    return {key: value for key, value in _allowlist_entries(text) if isinstance(value, str)}


def load_allowlist(path: Path | None = None) -> Counter[StoreKey]:
    """Read the allowlist as counts per (path, function, target)."""
    allowlist_path = path or ROOT / ALLOWLIST_PATH
    return parse_allowlist(allowlist_path.read_text()) if allowlist_path.exists() else Counter()


def load_witnesses(path: Path | None = None) -> dict[StoreKey, str]:
    """Read the entries that only the runtime guard sees, with their witness arguments."""
    allowlist_path = path or ROOT / ALLOWLIST_PATH
    return parse_witnesses(allowlist_path.read_text()) if allowlist_path.exists() else {}


def _toml_key(key: str) -> str:
    if BARE_TOML_KEY.fullmatch(key):
        return key
    return f"'{key}'" if "'" not in key else json.dumps(key)


def format_allowlist(counts: Counter[StoreKey], witnesses: dict[StoreKey, str] | None = None) -> str:
    """Return the allowlist file text for ``counts`` and the ``witnesses`` of the writes only the runtime guard sees."""
    entries: dict[StoreKey, int | str] = {**counts, **(witnesses or {})}
    lines = [ALLOWLIST_HEADER.rstrip("\n")]
    section: tuple[str, str] | None = None
    for (source, function, target), value in sorted(entries.items()):
        if section != (source, function):
            section = (source, function)
            lines.extend(("", f'["{source}"."{function}"]'))
        lines.append(f"{_toml_key(target)} = {json.dumps(value) if isinstance(value, str) else value}")
    return "\n".join(lines) + "\n"


def apply_allowlist(scan: ScanResult, allowlist: Counter[StoreKey]) -> list[Violation]:
    """Return findings beyond their allowlisted count and allowlist entries that no longer match."""
    found = scan.counts
    emitted: Counter[StoreKey] = Counter()
    violations: list[Violation] = []
    for violation in sorted(scan.violations, key=lambda item: (item.key[0], item.line, item.column, item.target)):
        emitted[violation.key] += 1
        if emitted[violation.key] > allowlist[violation.key]:
            violations.append(violation)
    violations.extend(
        Violation(
            path=Path(source),
            line=0,
            column=0,
            message=(
                f"stale allowlist entry {target!r}: expected {count}, found {found[source, function, target]}; "
                f"remove or lower it in {ALLOWLIST_PATH.as_posix()}"
            ),
            function=function,
            target=target,
        )
        for (source, function, target), count in sorted(allowlist.items())
        if source in scan.scanned_paths and found[source, function, target] < count
    )
    return violations


def check_files(files: list[tuple[Path, Scope]], *, allowlist: Counter[StoreKey] | None = None) -> list[Violation]:
    """Check explicitly scoped files against the allowlist."""
    return apply_allowlist(scan_files(files), load_allowlist() if allowlist is None else allowlist)


def check_paths(paths: list[Path], *, allowlist: Counter[StoreKey] | None = None) -> list[Violation]:
    """Check paths and return all GenerationStore usage violations."""
    return check_files([(path, classify_path(path)) for path in iter_python_files(paths)], allowlist=allowlist)


@dataclass(frozen=True)
class _ClassDeclaration:
    path: Path
    node: ast.ClassDef
    bases: tuple[str, ...]


def _imported_names(tree: ast.Module) -> tuple[set[str], dict[str, str]]:
    """Return the names a module imports from outside the package, and package imports by local name."""
    external: set[str] = set()
    internal: dict[str, str] = {}
    for node in ast.walk(tree):
        match node:
            case ast.ImportFrom(module=module, level=0, names=names) if not (module or "").startswith(PACKAGE):
                external.update(alias.asname or alias.name for alias in names)
            case ast.ImportFrom(names=names):
                internal.update((alias.asname or alias.name, alias.name) for alias in names)
            case ast.Import(names=names):
                external.update((alias.asname or alias.name).partition(".")[0] for alias in names)
    return external, internal


def _class_declarations(paths: Iterable[Path]) -> dict[str, list[_ClassDeclaration]]:
    declarations: dict[str, list[_ClassDeclaration]] = {}
    for path in iter_python_files(paths):
        tree = ast.parse(path.read_text(), filename=str(path))
        external, internal = _imported_names(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases = tuple(
                    internal.get(chain[-1], chain[-1])
                    for base in node.bases
                    if (chain := _attribute_chain(base)) and chain[0] not in external
                )
                declarations.setdefault(node.name, []).append(_ClassDeclaration(path, node, bases))
    return declarations


def _graph_ancestry(name: str, declarations: dict[str, list[_ClassDeclaration]]) -> tuple[str, ...]:
    ancestry: list[str] = []
    pending = [name]
    while pending:
        if (current := pending.pop()) in ancestry:
            continue
        ancestry.append(current)
        for declaration in declarations.get(current, ()):
            pending.extend(declaration.bases)
    return tuple(ancestry) if GRAPH_ROOT_CLASSES & set(ancestry) else ()


def _declared_attributes(node: ast.ClassDef) -> Iterator[tuple[str, ast.AST]]:
    for statement in node.body:
        match statement:
            case ast.AnnAssign(target=ast.Name(id=name), annotation=annotation) if (
                not name.isupper() and "ClassVar" not in _annotation_names(annotation)
            ):
                yield name, statement
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                yield from _self_attribute_writes(statement)


def _self_attribute_writes(function: FunctionNode) -> Iterator[tuple[str, ast.AST]]:
    """Yield the attributes a method sets on ``self`` by assignment or by ``setattr`` with a literal name."""
    for child in ast.walk(function):
        targets: list[ast.AST] = []
        match child:
            case ast.Assign(targets=assigned):
                targets = [node for target in assigned for node in _bound_targets(target)]
            case ast.AnnAssign(target=annotated) | ast.AugAssign(target=annotated):
                targets = [annotated]
            case ast.Call(func=func, args=[ast.Name(id="self"), ast.Constant(value=str() as name), *_]) if (
                ".".join(_attribute_chain(func)) in SETATTR_FUNCTIONS
            ):
                yield name, child
        for target in targets:
            if isinstance(target, ast.Attribute) and _attribute_chain(target.value) == ("self",):
                yield target.attr, target


def _class_members(node: ast.ClassDef) -> set[str]:
    members: set[str] = set()
    for statement in node.body:
        match statement:
            case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name) | ast.AnnAssign(target=ast.Name(id=name)):
                members.add(name)
            case ast.Assign(targets=targets):
                members.update(target.id for target in targets if isinstance(target, ast.Name))
    return members


def table_problems(
    *,
    rows: tuple[FactRow, ...] | None = None,
    declaration_paths: Iterable[Path] | None = None,
    dict_keys_written: frozenset[str] | None = None,
) -> list[Violation]:
    """Report model-graph attributes without a row, and rows that name nothing the sources declare or write."""
    table = ATTRIBUTE_FACTS if rows is None else rows
    paths = [ROOT / path for path in DECLARATION_MODULES] if declaration_paths is None else declaration_paths
    declarations = _class_declarations(paths)
    rows_by_owner: dict[str, set[str]] = {}
    for row in table:
        rows_by_owner.setdefault(row.owner, set()).add(row.declared_name)
    problems: list[Violation] = []
    declared_by_owner: dict[str, set[str]] = {}
    for name, entries in sorted(declarations.items()):
        ancestry = _graph_ancestry(name, declarations)
        declared = declared_by_owner.setdefault(name, set())
        for declaration in entries:
            declared |= _class_members(declaration.node)
            for attribute, node in _declared_attributes(declaration.node):
                declared.add(attribute)
                if ancestry and not any(attribute in rows_by_owner.get(owner, ()) for owner in ancestry):
                    problems.append(
                        Violation(
                            path=declaration.path,
                            line=getattr(node, "lineno", 0),
                            column=getattr(node, "col_offset", 0) + 1,
                            message=f"{name}.{attribute} has no ATTRIBUTE_FACTS row in {FACTS_MODULE.as_posix()}",
                            function=name,
                            target=attribute,
                        )
                    )
    for row in table:
        if row.owner in PARSER_STATE_OWNERS:
            continue
        if row.access == "DICT_KEY":
            stale = dict_keys_written is not None and row.attribute not in dict_keys_written
        else:
            stale = row.declared_name not in declared_by_owner.get(row.owner, ())
        if stale:
            problems.append(
                Violation(
                    path=FACTS_MODULE,
                    line=0,
                    column=0,
                    message=f"ATTRIBUTE_FACTS row {row.owner}.{row.attribute} names nothing the sources declare "
                    "or write",
                    function="ATTRIBUTE_FACTS",
                    target=row.attribute,
                )
            )
    return problems


def check_default_targets(
    *, allowlist: Counter[StoreKey] | None = None, witnesses: dict[StoreKey, str] | None = None
) -> tuple[list[Violation], ScanResult]:
    """Check the default targets and the table; also return the scan for ``--write-allowlist``.

    Every allowlist entry under the default targets must match, including entries of files that were deleted or renamed.
    A write the scan finds is listed with its count, never with a witness.
    """
    scan = scan_paths(ROOT / target for target in DEFAULT_TARGETS)
    active = load_allowlist() if allowlist is None else allowlist
    targets = tuple(target.as_posix() for target in DEFAULT_TARGETS)
    entry_paths = {
        source
        for source, _, _ in active
        if any(source == target or source.startswith(f"{target}/") for target in targets)
    }
    violations = apply_allowlist(replace(scan, scanned_paths=scan.scanned_paths | entry_paths), active)
    checker = Path(__file__)
    hook_problems = [
        Violation(checker, 0, 0, f"{name} is called by parser code and writes facts; add it to {lists}", lists, name)
        for name in unlisted_parser_hooks()
        if (lists := "PARSER_HOOK_METHODS")
    ]
    hook_problems.extend(
        Violation(checker, 0, 0, f"{name} is listed but no model module defines it; remove it", "hook lists", name)
        for name in undefined_listed_names()
    )
    found = scan.counts
    violations.extend(
        Violation(
            Path(source),
            0,
            0,
            f"the scan finds {target!r}; list it with its count {found[source, function, target]} instead of a witness",
            function,
            target,
        )
        for source, function, target in sorted(load_witnesses() if witnesses is None else witnesses)
        if found[source, function, target]
    )
    return [*table_problems(dict_keys_written=scan.dict_keys_written), *hook_problems, *violations], scan


def write_allowlist(found: Counter[StoreKey], path: Path | None = None) -> list[StoreKey]:
    """Write ``found`` as the allowlist and return no keys, or return the keys an existing allowlist would gain.

    A witness entry whose write the scan now finds becomes a count entry; that move is the only way a count may rise.
    """
    allowlist_path = path or ROOT / ALLOWLIST_PATH
    witnesses = load_witnesses(allowlist_path)
    if allowlist_path.exists():
        current = load_allowlist(allowlist_path)
        if grown := sorted(key for key, count in found.items() if count > current[key] and key not in witnesses):
            return grown
    kept = {key: witness for key, witness in witnesses.items() if not found[key]}
    allowlist_path.write_text(format_allowlist(found, kept))
    return []


@dataclass(frozen=True)
class RuntimeWrite:
    """A write the runtime guard reported, keyed as the scan keys its findings."""

    key: StoreKey
    message: str


_FUNCTION_SPANS: dict[Path, list[tuple[int, int, str]]] = {}
_FUNCTIONS_AT: dict[tuple[Path, int], str] = {}


def _function_spans(path: Path) -> list[tuple[int, int, str]]:
    """Return the line span and qualified name of every function in ``path``, named as the visitor names them."""
    if (spans := _FUNCTION_SPANS.get(path)) is None:
        spans = _FUNCTION_SPANS[path] = []
        pending: list[tuple[ast.AST, tuple[str, ...]]] = [
            (ast.parse(path.read_text(encoding="utf-8"), filename=str(path)), ())
        ]
        while pending:
            node, names = pending.pop()
            for child in ast.iter_child_nodes(node):
                match child:
                    case ast.FunctionDef(name=name) | ast.AsyncFunctionDef(name=name):
                        spans.append((child.lineno, child.end_lineno or child.lineno, ".".join((*names, name))))
                        pending.append((child, (*names, name)))
                    case ast.ClassDef(name=name):
                        pending.append((child, (*names, name)))
                    case _:
                        pending.append((child, names))
    return spans


def function_at(path: Path, line: int) -> str:
    """Return the qualified name of the innermost function of ``path`` that contains ``line``, or ``<module>``."""
    if (name := _FUNCTIONS_AT.get((path, line))) is None:
        enclosing = [(start, name) for start, end, name in _function_spans(path) if start <= line <= end]
        name = _FUNCTIONS_AT[path, line] = max(enclosing)[1] if enclosing else "<module>"
    return name


def runtime_target(attribute: str, operation: str, key: str | None) -> str:
    """Return the target the scan gives a write that the runtime guard reports as an operation on an attribute."""
    sequence = attribute in SEQUENCE_OWNERS
    if operation in {"=", "__iadd__", "__imul__"} or (sequence and operation in {"__setitem__", "__delitem__"}):
        return attribute
    if operation in {"__setitem__", "__delitem__"}:
        return f"{attribute}[:]" if key == "slice" else f"{attribute}[...]"
    if operation == "pop" and key is not None and not sequence:
        return f"{attribute}.pop(...)"
    return f"{attribute}.{operation}()"


def runtime_writes(lines: Iterable[str]) -> list[RuntimeWrite]:
    """Return the writes of the package in runtime guard report lines, each keyed as the scan keys its findings."""
    writes: list[RuntimeWrite] = []
    for line in lines:
        report = json.loads(line)
        if report.get("area") != "STORE_WRITES" or not (source := report["path"]).startswith(
            f"{SOURCE_ROOT.as_posix()}/"
        ):
            continue
        target = runtime_target(report["attribute"], report["operation"], report["key"])
        writes.append(RuntimeWrite((source, function_at(ROOT / source, report["line"]), target), report["message"]))
    return writes


def runtime_problems(
    lines: Iterable[str],
    *,
    allowlist: Counter[StoreKey] | None = None,
    witnesses: dict[StoreKey, str] | None = None,
) -> list[str]:
    """Return the messages of the package's runtime writes that the allowlist does not list, each once."""
    counts = load_allowlist() if allowlist is None else allowlist
    known = load_witnesses() if witnesses is None else witnesses
    problems = {
        write.key: write.message for write in runtime_writes(lines) if not counts[write.key] and write.key not in known
    }
    return [
        f"{message}; route it through a GenerationStore operation ({ALLOWLIST_PATH.as_posix()} does not list "
        f"{target!r} of {function} in {source})"
        for (source, function, target), message in problems.items()
    ]


def check_runtime_writes(
    lines: Iterable[str], *, allowlist: Counter[StoreKey] | None = None, witnesses: dict[StoreKey, str] | None = None
) -> None:
    """Raise ``AssertionError`` with the messages of the package's runtime writes that the allowlist does not list."""
    if problems := runtime_problems(lines, allowlist=allowlist, witnesses=witnesses):
        raise AssertionError("\n".join(problems))


WITNESS_RUNNER = """\
import json, os, shlex, sys
from datamodel_code_generator.__main__ import main
for witness, output, report in json.loads(sys.argv[1]):
    os.environ[sys.argv[2]] = f"report:{report}"
    if main([*shlex.split(witness), "--output", output]):
        raise SystemExit(f"witness {witness!r} fails")
"""
"""Runs every witness in one interpreter, each with its own output and report file."""


def witness_problems(witnesses: dict[StoreKey, str] | None = None) -> list[Violation]:
    """Run each witness with the runtime guard reporting and return the entries whose witness no longer writes them."""
    entries = load_witnesses() if witnesses is None else witnesses
    runs = sorted(set(entries.values()))
    with TemporaryDirectory() as directory:
        reports = [Path(directory) / f"writes-{index}.jsonl" for index in range(len(runs))]
        jobs = [
            (arguments, str(Path(directory) / f"output-{index}"), str(report))
            for index, (arguments, report) in enumerate(zip(runs, reports, strict=True))
        ]
        run = subprocess.run(
            [sys.executable, "-c", WITNESS_RUNNER, json.dumps(jobs), STORE_WRITES_VARIABLE],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if run.returncode:
            last_line = (run.stderr.strip().splitlines() or [f"exit status {run.returncode}"])[-1]
            msg = f"the witnesses could not run: {last_line}"
            raise RuntimeError(msg)
        written = {
            arguments: {write.key for write in runtime_writes(report.read_text(encoding="utf-8").splitlines())}
            if report.exists()
            else set()
            for arguments, report in zip(runs, reports, strict=True)
        }
    return [
        Violation(
            Path(key[0]),
            0,
            0,
            f"stale allowlist entry {key[2]!r}: its witness {arguments!r} no longer writes it; remove it from "
            f"{ALLOWLIST_PATH.as_posix()}",
            key[1],
            key[2],
        )
        for key, arguments in sorted(entries.items())
        if key not in written[arguments]
    ]


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path)
    parser.add_argument(
        "--write-allowlist",
        action="store_true",
        help="create the allowlist from the default targets, or lower it after writes were routed; never adds entries",
    )
    parser.add_argument(
        "--witnesses",
        action="store_true",
        help="run the witness of every entry only the runtime guard sees and report those it no longer writes",
    )
    args = parser.parse_args(argv)

    if args.witnesses:
        violations = witness_problems()
    elif args.paths:
        violations = check_paths(args.paths)
    else:
        violations, scan = check_default_targets()
        if args.write_allowlist:
            if grown := write_allowlist(scan.counts):
                print("Refusing to add allowlist entries; route these writes through GenerationStore:", file=sys.stderr)
                for source, function, target in grown:
                    print(f"{source}: {function}: {target}", file=sys.stderr)
                return 1
            violations, _ = check_default_targets()
    if violations:
        print("GenerationStore usage violations found:", file=sys.stderr)
        for violation in violations:
            print(violation.format(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
