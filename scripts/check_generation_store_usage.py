"""Check the attribute-to-fact table against the sources.

The checker reads Python through ``scripts/_source_model.py``, so it never imports the package. It reads two tables:
the store mutation API
(``GENERATION_STORE_MUTATION_METHODS``, the names of ``SITE_EFFECTS`` and ``NO_SITE_EFFECT`` in
``parser/generation.py``) and the attribute-to-fact table (``ATTRIBUTE_FACTS`` in ``scripts/fact_tables.py``, whose rows
name the ``Phase`` members of ``parser/_facts.py``). It reports a row whose kind, facts, freeze point or store
operations contradict each other, an attribute a model-graph class declares without a row, and an attribute or cached
property row that names nothing the classes declare: heki can judge a write only of an attribute some row names.

heki checks the writes themselves: ``scripts/build_heki_contracts.py`` turns the rows into the ``write-owners``
contracts of ``scripts/heki.toml``, and ``scripts/heki_baseline.toml`` records the writes outside store operations that
exist today. The ``__dict__`` keys are heki's alone: a write of a key no row names is a finding
(``unlisted-dict-keys``), and so is a row whose key nothing writes (``unmatched-contract-entries``). A
``cached_property``, which writes its own name into ``__dict__``, is heki's too: the cache contract forbids one on every
class the table describes and the classes deriving from them.
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

    from scripts._source_model import Reference
    from scripts._source_model import Scope as ModelScope

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModelError, SourceModule, Unreadable

ROOT = Path(__file__).resolve().parents[1]

SOURCE_ROOT = Path("src/datamodel_code_generator")
GENERATION_STORE_MODULE = SOURCE_ROOT / "parser" / "generation.py"
FACTS_MODULE = Path("scripts/fact_tables.py")
PHASE_MODULE = SOURCE_ROOT / "parser" / "_facts.py"
DECLARATION_MODULES = (SOURCE_ROOT / "types.py", SOURCE_ROOT / "reference.py", SOURCE_ROOT / "model")
COMPILED_TEMPLATE_DIRECTORY = "_compiled_templates"

SETATTR_ORIGINS = frozenset({"builtins.setattr", "builtins.object.__setattr__"})
"""What a call writes an attribute through, by origin: aliases of them, at any level, are the same call."""

PARSER_STATE_OWNERS = frozenset({"ModuleContext", "Parser"})
"""Owners whose rows describe parser run state: the declarations of the model graph skip them."""

GRAPH_ROOT_CLASSES = frozenset({"DataModel", "DataModelFieldBase", "DataType", "Reference"})
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


_MODELS: dict[tuple[Path, str], SourceModule] = {}


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _module_name(path: Path) -> str:
    """Return the dotted name of a repository module (``src/`` dropped), or none for a file outside the repository."""
    try:
        relative = path.resolve().relative_to(ROOT)
    except ValueError:
        return ""
    parts = relative.with_suffix("").parts
    parts = parts[1:] if parts[:1] == ("src",) else parts
    return ".".join(parts[:-1] if parts[-1:] == ("__init__",) else parts)


def read_model(path: Path) -> SourceModule:
    """Return the source model of a file; it raises `SourceModelError` for a module the model cannot read."""
    text = path.read_text(encoding="utf-8")
    key = (path.resolve(), text)
    if (model := _MODELS.get(key)) is None:
        model = _MODELS[key] = SourceModule(text, _display_path(path), _module_name(path))
    return model


def _model_or_error(path: Path) -> SourceModule | SourceModelError:
    try:
        return read_model(path)
    except SourceModelError as error:
        return error


def _module_value(model: SourceModule, name: str) -> ast.expr | None:
    """Return the value of the first module-level assignment of ``name``."""
    return next((item.value for item in model.bindings(name, model.module_scope) if item.value is not None), None)


def _literal_names(model: SourceModule, name: str) -> frozenset[str]:
    """Return the strings of the literal set, or the keys of the literal dict, that a module binds ``name`` to once.

    The literal may be built with ``frozenset(...)``, ``dict(...)`` or ``MappingProxyType(...)``.
    """
    if isinstance(value := model.constant(name), Unreadable) and not model.bindings(name, model.module_scope):
        msg = f"{name} was not found"
        raise RuntimeError(msg)
    if isinstance(value, Unreadable) or not isinstance(value, (set, frozenset, Mapping)):
        msg = f"{name} must be a literal set or dict of strings"
        raise TypeError(msg)
    if not all(isinstance(item, str) for item in value):
        msg = f"{name} must contain only literal strings"
        raise TypeError(msg)
    return frozenset(value)


def _load_generation_store_mutation_methods() -> frozenset[str]:
    """Return the store mutations: the keys of ``SITE_EFFECTS`` and the names of ``NO_SITE_EFFECT``."""
    model = read_model(ROOT / GENERATION_STORE_MODULE)
    return _literal_names(model, "SITE_EFFECTS") | _literal_names(model, "NO_SITE_EFFECT")


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


def _module_classes(model: SourceModule) -> list[ModelScope]:
    return [scope for scope in model.scopes if scope.kind == "class" and scope.parent is model.module_scope]


def _enum_members(model: SourceModule) -> dict[str, tuple[str, ...]]:
    """Return the members of each module-level ``enum.Enum`` subclass: the names its body assigns, in order."""
    return {
        scope.code_name: tuple(binding.name for binding in model.bindings(scope=scope) if binding.kind == "assign")
        for scope in _module_classes(model)
        if any(model.origin(base) == "enum.Enum" for base in scope.node.bases)
    }


def _row_fields(model: SourceModule) -> tuple[list[str], dict[str, ast.expr]]:
    for scope in _module_classes(model):
        if scope.code_name == "AttributeFact":
            fields = [
                binding for binding in model.bindings(scope=scope) if binding.kind in {"annassign", "annotation-only"}
            ]
            return [binding.name for binding in fields], {
                binding.name: binding.value for binding in fields if binding.value is not None
            }
    msg = "AttributeFact was not found"
    raise RuntimeError(msg)


def _row_value(model: SourceModule, node: ast.expr, field_name: str, enums: dict[str, tuple[str, ...]]) -> object:
    match node:
        case ast.Constant(value=str() | None as literal):
            return literal
        case ast.Tuple(elts=items):
            return tuple(_row_value(model, item, field_name, enums) for item in items)
        case ast.Attribute(value=receiver, attr=member) if (enum_name := model.source(receiver)) in ROW_ENUMS.get(
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


def _load_freeze_points(model: SourceModule, enums: dict[str, tuple[str, ...]]) -> dict[str, str]:
    if not isinstance(table := _module_value(model, "RENDER_FACT_FREEZE_POINTS"), ast.Dict):
        msg = "RENDER_FACT_FREEZE_POINTS must be a literal dict"
        raise TypeError(msg)
    points: dict[str, str] = {}
    for key, value in zip(table.keys, table.values, strict=True):
        fact = _row_value(model, key, "facts", enums) if key is not None else None
        point = _row_value(model, value, "frozen_at", enums) if isinstance(fact, str) else None
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


def _vocabulary(model: SourceModule) -> dict[str, tuple[str, ...]]:
    """Return the members of every enum of a module and of ``Phase`` in ``parser/_facts.py``, the module's first."""
    return {**_enum_members(read_model(ROOT / PHASE_MODULE)), **_enum_members(model)}


def fact_vocabulary(path: Path | None = None) -> dict[str, tuple[str, ...]]:
    """Return the members of every enum of the fact tables, ``Phase`` included, in declaration order."""
    return _vocabulary(read_model(path or ROOT / FACTS_MODULE))


def load_attribute_facts(path: Path | None = None) -> tuple[FactRow, ...]:
    """Read ``ATTRIBUTE_FACTS`` from ``scripts/fact_tables.py`` without importing it."""
    model = read_model(path or ROOT / FACTS_MODULE)
    enums = _vocabulary(model)
    names, defaults = _row_fields(model)
    freeze_points = _load_freeze_points(model, enums)
    if not isinstance(table := _module_value(model, "ATTRIBUTE_FACTS"), ast.Tuple):
        msg = "ATTRIBUTE_FACTS must be a literal tuple"
        raise TypeError(msg)
    rows: list[FactRow] = []
    seen: set[tuple[str, str]] = set()
    for entry in table.elts:
        if not (isinstance(entry, ast.Call) and model.source(entry.func) == "AttributeFact"):
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
            {name: _row_value(model, arguments.get(name) or defaults[name], name, enums) for name in names},
            freeze_points,
        )
        if (row.owner, row.attribute) in seen:
            msg = f"ATTRIBUTE_FACTS has two rows for {row.owner}.{row.attribute}"
            raise ValueError(msg)
        seen.add((row.owner, row.attribute))
        rows.append(row)
    return tuple(rows)


ATTRIBUTE_FACTS = load_attribute_facts()


@dataclass(frozen=True)
class Violation:
    """A problem of the table: a row the sources contradict, or a declaration without a row."""

    path: Path
    line: int
    column: int
    message: str
    function: str = "<module>"
    target: str = ""

    def format(self) -> str:
        """Format violation for CLI output."""
        return f"{self.path.as_posix()}:{self.line}:{self.column}: {self.message} (in {self.function})"


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


def _type_names(model: SourceModule, node: ast.expr | None) -> set[str]:
    """Return the names an annotation gives in its type positions: each name, and the last part of each chain."""
    return set() if node is None else {ast.unparse(item).rpartition(".")[2] for item in model.type_expressions(node)}


@cache
def _spellings(model: SourceModule) -> dict[ast.AST, Reference]:
    """Return the name occurrence of each name node of a model's tree."""
    return {reference.node: reference for reference in model.references}


def _spelled(model: SourceModule, node: ast.AST) -> str | None:
    """Return the name a node spells when it is a name, else None."""
    return reference.name if (reference := _spellings(model).get(node)) is not None else None


def iter_python_files(paths: Iterable[Path]) -> list[Path]:
    """Return the Python files below ``paths``, without the compiled templates."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.py")))
        elif path.suffix == ".py":
            files.append(path)
    return [path for path in files if COMPILED_TEMPLATE_DIRECTORY not in path.parts]


def _unreadable(path: Path, error: SourceModelError) -> Violation:
    return Violation(path, 0, 0, f"the source model cannot read this module: {error}", target="unreadable-source")


@dataclass(frozen=True)
class _ClassDeclaration:
    path: Path
    model: SourceModule
    scope: ModelScope
    bases: tuple[str, ...]


def _scope_chain(scope: ModelScope | None) -> Iterator[ModelScope]:
    """Yield ``scope`` and the scopes around it, innermost first."""
    current = scope
    while current is not None:
        yield current
        current = current.parent


def _imported_names(model: SourceModule, declared: ModelScope) -> tuple[set[str], dict[str, str]]:
    """Return the names a class sees imported from outside the package, and package imports by local name.

    The bases of a class are read where the class is declared: the imports of the scopes around it count, an import
    inside one of its methods binds no name there.
    """
    visible = {id(item) for item in _scope_chain(declared.parent)}
    external: set[str] = set()
    internal: dict[str, str] = {}
    for item in model.imports:
        if id(item.scope) not in visible or item.bound is None:
            continue
        if item.kind == "import":
            external.add(item.bound)
        elif item.kind == "from" and item.alias is not None:
            if not item.level and not str(item.source).startswith(PACKAGE):
                external.add(item.bound)
            else:
                internal[item.bound] = item.alias.name
    return external, internal


def _class_declarations(models: Iterable[tuple[Path, SourceModule]]) -> dict[str, list[_ClassDeclaration]]:
    declarations: dict[str, list[_ClassDeclaration]] = {}
    for path, model in models:
        for scope in model.scopes:
            if scope.kind == "class":
                external, internal = _imported_names(model, scope)
                bases = tuple(
                    internal.get(chain[-1], chain[-1])
                    for base in scope.node.bases
                    if (chain := _attribute_chain(base)) and chain[0] not in external
                )
                declarations.setdefault(scope.code_name, []).append(_ClassDeclaration(path, model, scope, bases))
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


def _declared_attributes(declaration: _ClassDeclaration) -> Iterator[tuple[str, ast.AST]]:
    model = declaration.model
    for statement in declaration.scope.node.body:
        match statement:
            case ast.AnnAssign(target=target, annotation=annotation) if (
                (name := _spelled(model, target)) is not None
                and not name.isupper()
                and "ClassVar" not in _type_names(model, annotation)
            ):
                yield name, statement
            case ast.FunctionDef() | ast.AsyncFunctionDef():
                yield from _self_attribute_writes(model, statement)


def _self_attribute_writes(model: SourceModule, function: ast.AST) -> Iterator[tuple[str, ast.AST]]:
    """Yield the attributes a method sets on ``self`` by assignment or by ``setattr`` with a literal name."""
    for child in ast.walk(function):
        targets: list[ast.AST] = []
        match child:
            case ast.Assign(targets=assigned):
                targets = [node for target in assigned for node in _bound_targets(target)]
            case ast.AnnAssign(target=annotated) | ast.AugAssign(target=annotated):
                targets = [annotated]
            case ast.Call(func=func, args=[receiver, ast.Constant(value=str() as name), *_]) if (
                _spelled(model, receiver) == "self" and model.origin(func) in SETATTR_ORIGINS
            ):
                yield name, child
        for target in targets:
            if isinstance(target, ast.Attribute) and _attribute_chain(target.value) == ("self",):
                yield target.attr, target


def table_problems(
    *, rows: tuple[FactRow, ...] | None = None, declaration_paths: Iterable[Path] | None = None
) -> list[Violation]:
    """Report model-graph attributes without a row, and attribute and cached property rows the classes do not declare.

    A ``__dict__`` key row is heki's: ``unmatched-contract-entries`` reports one whose key nothing writes.
    """
    table = ATTRIBUTE_FACTS if rows is None else rows
    paths = [ROOT / path for path in DECLARATION_MODULES] if declaration_paths is None else declaration_paths
    read = [(path, _model_or_error(path)) for path in iter_python_files(paths)]
    problems = [_unreadable(path, model) for path, model in read if isinstance(model, SourceModelError)]
    declarations = _class_declarations((path, model) for path, model in read if isinstance(model, SourceModule))
    rows_by_owner: dict[str, set[str]] = {}
    for row in table:
        rows_by_owner.setdefault(row.owner, set()).add(row.declared_name)
    declared_by_owner: dict[str, set[str]] = {}
    for name, entries in sorted(declarations.items()):
        ancestry = _graph_ancestry(name, declarations)
        declared = declared_by_owner.setdefault(name, set())
        for declaration in entries:
            declared.update(declaration.model.class_members(declaration.scope, spelled=True))
            for attribute, node in _declared_attributes(declaration):
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
        if row.owner in PARSER_STATE_OWNERS or row.access == "DICT_KEY":
            continue
        if row.declared_name not in declared_by_owner.get(row.owner, ()):
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


def check_default_targets() -> list[Violation]:
    """Check the table against the declarations of the package."""
    return table_problems()


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    if violations := check_default_targets():
        print("ATTRIBUTE_FACTS problems found:", file=sys.stderr)
        for violation in violations:
            print(violation.format(), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
