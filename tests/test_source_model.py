"""The source model's answers on every form of `tests/data/source_forms`, its compiler check, and the rule verdicts.

Every form is a small module (or a package directory) that holds one Python form a repository check must read right.
The model's answers on it are stored once, whatever the Python version: a per-version difference is a model defect.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
import typing
from collections import Counter
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import tomllib
from coverage.config import DEFAULT_EXCLUDE
from coverage.python import PythonParser

from scripts import _import_cycles as import_cycles
from scripts import _source_model as source_model
from scripts import (
    check_architecture_boundaries,
    check_generation_store_usage,
    check_pass_pipeline,
    check_ratchets,
    check_source_rules,
    coverage_exclusions,
    legacy_row_reads,
    where,
)
from tests import expected_access
from tests import test_assert_helper_usage as assert_helper_usage
from tests import test_checks_are_run as checks_are_run
from tests import test_ci_workflow as ci_workflow
from tests import test_generated_import_closure as import_closure
from tests import test_pre_commit_hooks as pre_commit_hooks
from tests.checks.template_assignments import field_lines
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).parents[1]
FORMS = Path(__file__).parent / "data" / "source_forms"
VERDICTS = FORMS / "verdicts.toml"
CHUNKS = 16
PYTHON = (sys.version_info.major, sys.version_info.minor)
OLDEST_PYTHON = (3, 11)
"""The oldest Python the package supports, the ``min-python`` of a form that names none."""
_DIRECTIVE = re.compile(
    r"^(questions|min-python|module|path|keywords|constants|codes|consumers|binding-nodes|citations|layers|bases"
    r"|nested-names|assignments|annotations|segments)"
    r": *(.*)$",
    re.MULTILINE,
)
MODEL_NAMES = frozenset({"scripts._source_model", "_source_model"})
"""The names a module imports the model by: as part of the ``scripts`` package, or beside it when run as a script."""


@pytest.fixture
def compare_with_the_compiler(monkeypatch: pytest.MonkeyPatch) -> None:
    """Compare every model a test builds without an explicit ``verify`` with the compiler (`VERIFY_ENV`)."""
    monkeypatch.setenv(source_model.VERIFY_ENV, "1")


pytestmark = pytest.mark.usefixtures("compare_with_the_compiler")


def _form_paths() -> list[Path]:
    """Return every form: a ``.py`` (or grammar-gated ``.py.txt``) file or a package directory under a question."""
    return sorted(
        path
        for question in source_model.QUESTIONS
        for path in (FORMS / question).iterdir()
        if path.name.endswith((".py", ".py.txt")) or (path / "__init__.py").is_file()
    )


FORM_PATHS = _form_paths()


def _form_id(path: Path) -> str:
    return f"{path.parent.name}/{path.name.removesuffix('.txt').removesuffix('.py')}"


def _form_text(path: Path) -> str:
    return (path / "__init__.py" if path.is_dir() else path).read_text(encoding="utf-8")


def _directives(path: Path) -> dict[str, str]:
    """Read the directives of the form's docstring: ``questions:``, ``min-python:``, ``module:``, ``path:``, ..."""
    text = _form_text(path)
    docstring = text.split('"""', 2)[1] if text.startswith('"""') else ""
    return {match[1]: match[2].strip() for match in _DIRECTIVE.finditer(docstring)}


def _questions(path: Path) -> set[str]:
    listed = _directives(path).get("questions", "")
    return {path.parent.name, *(item.strip() for item in listed.split(",") if item.strip())}


def _min_python(path: Path) -> tuple[int, int]:
    if (named := _directives(path).get("min-python")) is None:
        return OLDEST_PYTHON
    major, _, minor = named.partition(".")
    return int(major), int(minor)


def _guard(guard: source_model.Guard) -> str:
    return "|".join(map(str, guard)) or "always"


def _resolution(resolution: source_model.Resolution) -> str:
    where = f" {resolution.scope.qualname}" if resolution.scope is not None else ""
    lines = ",".join(str(binding.line) for binding in resolution.bindings)
    fallback = f", else {_resolution(resolution.fallback)}" if resolution.fallback is not None else ""
    return f"{resolution.kind}{where}{f' @{lines}' if lines else ''}{fallback}"


def _lines(lines: frozenset[int] | range) -> str:
    """Render line numbers as compact ranges: ``1-3,7``."""
    ordered = sorted(lines)
    runs: list[list[int]] = []
    for line in ordered:
        runs.append([line, line]) if not runs or line != runs[-1][1] + 1 else runs[-1].__setitem__(1, line)
    return ",".join(f"{start}-{end}" if start != end else str(start) for start, end in runs) or "-"


def _value(value: object) -> str:
    """Render a constant with sets and dicts in sorted order, so the text does not depend on hash seeds."""
    if isinstance(value, (set, frozenset)):
        items = ", ".join(sorted(map(_value, value)))
        return f"{type(value).__name__}({{{items}}})" if value else f"{type(value).__name__}()"
    if isinstance(value, dict):
        return "{" + ", ".join(sorted(f"{_value(key)}: {_value(item)}" for key, item in value.items())) + "}"
    if isinstance(value, (list, tuple)):
        return type(value)().__class__.__name__ + "(" + ", ".join(map(_value, value)) + ")"
    return repr(value)


def _name(scope: source_model.Scope) -> str:
    return f"{scope.qualname}#{scope.ordinal}"


def _expression(model: source_model.SourceModule, node: ast.expr | None) -> str:
    return "None" if node is None else "UNKNOWN" if node is source_model.UNKNOWN else model.source(node)


def _binding_of(model: source_model.SourceModule, node: ast.AST) -> str:
    binding = model.binding_at(node)
    return "none" if binding is None else f"{binding.scope.qualname}:{binding.name}@{binding.line} ({binding.kind})"


def _bases_and_annotations(model: source_model.SourceModule) -> list[str]:
    """Render the bases of every class scope and the annotation of every parameter, with the origin of each name."""
    out = []
    for scope in model.scopes:
        listed = [f"{model.source(base)} = {model.origin(base)}" for base in source_model.bases(scope)]
        out += [f"bases of {scope.qualname}: {', '.join(listed) or '-'}"] * (scope.kind == "class")
    for binding in model.bindings():
        annotation = source_model.annotation(binding) if binding.kind == "param" else None
        out += [f"annotation of {binding.scope.qualname}:{binding.name}: {_expression(model, annotation)}"] * (
            binding.kind == "param"
        )
    return out


def _assignments(model: source_model.SourceModule) -> list[str]:
    """Render what each target of the module takes: its paired item, the whole value and its place in an unpacking."""
    targets = sorted(
        (node for node in ast.walk(model.tree) if model.assignment(node) is not None),
        key=lambda node: (getattr(node, "lineno", 0), getattr(node, "col_offset", 0)),
    )
    return [
        f"{model.source(node)} @{node.lineno}:{node.col_offset} takes ({found.kind}) {model.source(found.element)}"
        f" of {model.source(found.value)}{f' at {found.position}' if found.position else ''}"
        for node in targets
        if (found := model.assignment(node)) is not None
    ]


def _bindings(model: source_model.SourceModule, directives: dict[str, str]) -> list[str]:
    out = []
    for scope in model.scopes:
        marks = [f"code {scope.code_object}", f"first line {scope.first_line}"]
        marks += [f"in {scope.annotation} annotation"] * bool(scope.annotation)
        marks += [f"global {','.join(sorted(scope.declared_global))}"] * bool(scope.declared_global)
        marks += [f"nonlocal {','.join(sorted(scope.declared_nonlocal))}"] * bool(scope.declared_nonlocal)
        marks += ["star import"] * scope.star_import
        marks += [f"nested name {source_model.nested_name(scope)!r}"] * bool(directives.get("nested-names"))
        out.append(f"scope {scope.kind} {scope.qualname}#{scope.ordinal} {scope.code_name!r}: {'; '.join(marks)}")
        defined = scope.kind not in {"module", "type-params"} and model.scope_defined_by(scope.node) is scope
        out += [f"  opened by a node evaluated in {_name(model.scope_of(scope.node))}"] if defined else []
        for binding in model.bindings(scope=scope):
            name = binding.name if binding.name == binding.mangled else f"{binding.name} (as {binding.mangled})"
            extra = [f"origin {binding.origin}"] * (binding.origin is not None)
            extra += [f"re-export {binding.reexport_form!r}"] * (binding.reexport_form is not None)
            extra += [f"value {model.source(binding.value)}"] if binding.value is not None else []
            found = model.binding_at(binding.node)
            extra += [f"its node binds {_binding_of(model, binding.node)}"] if found is not binding else []
            out.append(
                f"  {name}: {binding.kind}@{binding.line} [{_guard(binding.guard)}]{''.join(f'; {e}' for e in extra)}"
            )
        members = model.class_members(scope) if scope.kind == "class" else {}
        out += [f"  members: {', '.join(sorted(members))}"] * (scope.kind == "class")
        spelled = model.class_members(scope, spelled=True) if scope.kind == "class" else {}
        out += [f"  members as spelled: {', '.join(sorted(spelled))}"] * (sorted(spelled) != sorted(members))
        receiver = model.receiver(scope)
        out += [f"  receiver: {receiver.name}@{receiver.line}"] if receiver is not None else []
    out += [
        f"dynamic {site.kind}@{site.line} in {site.scope.qualname} [{_guard(site.guard)}]"
        for site in model.dynamic_sites
    ]
    out += _bases_and_annotations(model) if directives.get("bases") else []
    out += _assignments(model) if directives.get("assignments") else []
    kinds = (
        ast.Name,
        ast.alias,
        ast.arg,
        ast.excepthandler,
        ast.pattern,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
        ast.ClassDef,
    )
    nodes = (
        [node for node in ast.walk(model.tree) if isinstance(node, kinds)] if directives.get("binding-nodes") else []
    )
    out += [
        f"binding at {type(node).__name__}@{node.lineno}:{node.col_offset}: {_binding_of(model, node)}"
        for node in sorted(nodes, key=lambda node: (node.lineno, node.col_offset))
    ]
    return out


def _type_positions(model: source_model.SourceModule) -> list[str]:
    """Render the type positions of every annotation and string assignment: names, and attribute chains by origin."""
    out = []
    for node in ast.walk(model.tree):
        roots = [node.annotation] if isinstance(node, (ast.AnnAssign, ast.arg)) and node.annotation else []
        roots += [node.returns] if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.returns else []
        value = node.value if isinstance(node, ast.Assign) else None
        roots += [value] if isinstance(value, ast.Constant) and isinstance(value.value, str) else []
        for root in roots:
            names = ", ".join(item.name for item in model.type_positions(root)) or "-"
            chains = [
                f"{ast.unparse(item)} = {model.origin(item)}"
                for item in model.type_expressions(root)
                if isinstance(item, ast.Attribute)
            ]
            listed = f"; chains {', '.join(chains)}" if chains else ""
            out.append(f"type positions @{root.lineno}: {names}{listed}")
    return out


def _annotations_and_string_reads(model: source_model.SourceModule) -> list[str]:
    """Render every annotation with its mode, and the names each string reads, looked up where it stands."""
    out = []
    for item in model.annotations:
        target = "return" if item.target is None else getattr(item.target, "arg", None) or ast.unparse(item.target)
        value = f"; value {model.source(item.value)}" if item.value is not None else ""
        out.append(
            f"annotation {model.source(item.node)} @{item.line} ({item.mode}) in {item.scope.qualname}: {target}{value}"
        )
    strings = sorted(
        (node for node in ast.walk(model.tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in strings:
        parsed = source_model.string_type(node)
        reads = [
            f"{name.id} -> {_resolution(model.lookup(name.id, model.scope_of(node)))}"
            for name in (source_model.name_reads(parsed) if parsed is not None else [])
        ]
        found = "does not parse" if parsed is None else f"reads {', '.join(reads) or 'nothing'}"
        out.append(f"string {node.value!r} @{node.lineno}:{node.col_offset} {found}")
    return out


def _references(model: source_model.SourceModule, directives: dict[str, str]) -> list[str]:
    out = []
    for reference in model.references:
        mode = f" ({reference.annotation} annotation)" if reference.annotation else ""
        origin = model.origin(reference.node) if reference.context == "load" else None
        alternatives = model.origins(reference.node) if reference.context == "load" else []
        where = f"{reference.line}:{reference.column}"
        text = f"{where} {reference.name} {reference.context}{mode} in {reference.scope.qualname}"
        out.append(f"{text} -> {_resolution(model.resolve(reference.node))}" + (f"; origin {origin}" if origin else ""))
        out += [
            f"  may be {path} [{_guard(guard)}]"
            for path, guard in alternatives
            if len(alternatives) > 1 or origin is None
        ]
    for binding in model.bindings():
        uses = ", ".join(f"{use.kind}@{use.reference.line}" for use in model.uses(binding))
        out += [f"uses of {binding.scope.qualname}:{binding.name}@{binding.line}: {uses}"] if uses else []
    out += _type_positions(model)
    out += _annotations_and_string_reads(model) if directives.get("annotations") else []
    out += [f"constant {name}: {_value(model.constant(name))}" for name in directives.get("constants", "").split()]
    if directives.get("consumers"):
        out += [
            f"use of {model.source(node)} @{node.lineno}:{node.col_offset}: {model.use_of(node)}"
            for node in ast.walk(model.tree)
            if isinstance(node, ast.Call)
        ]
    keywords = directives.get("keywords", "").split()
    calls = [node for node in ast.walk(model.tree) if isinstance(node, ast.Call)] if keywords else []
    for call in calls:
        given = [(position, None) for position in range(3)] + [(None, name) for name in keywords]
        found = [_expression(model, source_model.argument(call, *where)) for where in given]
        labels = [str(position) if position is not None else str(name) for position, name in given]
        callee = model.origin(call.func) or model.source(call.func)
        out.append(f"call @{call.lineno} {callee}: {' '.join(f'{a}={b}' for a, b in zip(labels, found, strict=True))}")
    return out


def _imports(model: source_model.SourceModule) -> list[str]:
    out = []
    for item in model.imports:
        flags = [f"level {item.level}"] * bool(item.level) + ["lazy"] * item.lazy
        flags += ["typing only"] * item.typing_only + ["unconditional"] * item.unconditional
        bound = f" as {item.bound}" if item.bound is not None else ""
        line = item.node.lineno
        binding = model.binding_at(item.alias) if item.alias is not None else None
        flags += (
            [f"binds in {binding.scope.qualname}"] if binding is not None and binding.scope is not item.scope else []
        )
        out.append(
            f"{item.kind}@{line} {item.target}{bound} from {item.source} in {item.scope.qualname} "
            f"[{_guard(item.guard)}]{''.join(f'; {flag}' for flag in flags)}"
        )
    return out


def _suppressions(model: source_model.SourceModule, directives: dict[str, str]) -> list[str]:
    out = [f"comment @{line}: {comment}" for line, comment in model.comments.items()]
    for item in model.suppressions():
        codes = ",".join(item.codes) if item.codes is not None else "every code"
        out.append(f"{item.tool} {item.form} @{item.line} ({codes}): lines {_lines(item.lines)}")
    codes = tuple(directives.get("codes", "").split())
    out += [f"suppressed ruff {' '.join(codes)}: {_lines(model.suppressed('ruff', codes))}"] * bool(codes)
    return out


def _segments(model: source_model.SourceModule) -> list[str]:
    """Render, per code token, its segment, the header that holds it, its innermost node and its typing-only form."""
    out = []
    for item in model.tokens():
        line, column = item.token.start
        segment = model.segment(line, column)
        spans = ",".join(f"{start[0]}:{start[1]}-{end[0]}:{end[1]}" for start, end in segment.spans)
        header = model.header_of(line, column)
        node = model.nodes_at(line, column)[0]
        start, end = model.span(node)
        out.append(
            f"token {line}:{column} {item.token.string!r}: {segment.kind} {spans}; header "
            f"{_name(header) if header is not None else '-'}; node {type(node).__name__} {start[0]}:{start[1]}-"
            f"{end[0]}:{end[1]}; typing-only {model.typing_only(line, column) or '-'}"
        )
    return out


def _extents(model: source_model.SourceModule, directives: dict[str, str]) -> list[str]:
    out = []
    statements = sorted(
        (node for node in ast.walk(model.tree) if isinstance(node, ast.stmt)),
        key=lambda node: (node.lineno, node.col_offset),
    )
    for node in statements:
        clauses = "; ".join(
            f"{clause.keyword} {_lines(clause.header)}/{_lines(clause.suite)}" for clause in model.clauses(node)
        )
        out.append(
            f"{type(node).__name__}@{node.lineno} lines {_lines(model.extent(node))}{f': {clauses}' if clauses else ''}"
        )
    out.append(f"code lines: {_lines(model.code_lines)}")
    for line in range(1, len(model.lines) + 1):
        statement = model.statement_at(line)
        text = model.lines[line - 1]
        column = len(text) - len(text.lstrip())
        roles = sorted({f"{_name(token.owner)}:{token.role}" for token in model.tokens([line])})
        held = f"{type(statement).__name__}@{statement.lineno}" if statement is not None else "-"
        out.append(f"line {line}: {held}; owner {_name(model.owner(line, column))}; tokens {', '.join(roles) or '-'}")
    out += [f"header {scope.qualname}: {_lines(model.header(scope))}" for scope in model.scopes if model.header(scope)]
    out += [f"source @{node.lineno}: {model.source(node)}" for node in statements if not model.clauses(node)]
    return out + (_segments(model) if directives.get("segments") else [])


def _render_module(model: source_model.SourceModule, questions: set[str], directives: dict[str, str]) -> list[str]:
    sections = {
        "bindings": lambda: _bindings(model, directives),
        "references": lambda: _references(model, directives),
        "imports": lambda: _imports(model),
        "suppressions": lambda: _suppressions(model, directives),
        "extents": lambda: _extents(model, directives),
    }
    return [
        line
        for question in source_model.QUESTIONS
        if question in questions
        for line in [f"== {question}", *sections[question]()]
    ]


def _render_tree(path: Path, questions: set[str]) -> list[str]:
    tree = source_model.SourceTree(path.parent, [path.name], verify=True)
    out = []
    for name in tree.paths:
        out += [f"=== module {name}", *_render_module(tree.module(name), questions, _directives(tree.paths[name]))]
        model = tree.module(name)
        for key in sorted(model.module_scope.bindings):
            hops = [
                f"{definition.kind} {definition.module}{f':{definition.name}' if definition.name else ''}"
                f"@{definition.line}"
                f" via {' > '.join(f'{module} [{_guard(guard)}]' for module, guard in definition.path) or '-'}"
                for definition in tree.definition(name, key)
            ]
            out.append(f"definition {name}:{key} -> {'; '.join(hops)}")
    out += [
        f"edge {edge.importer} -> {edge.imported} @{edge.line} via {edge.via} ({edge.level}) [{_guard(edge.guard)}]"
        for edge in tree.import_edges()
    ]
    return out


def render_form(path: Path) -> str:
    """Render the model's answers on a form, for the questions the form serves."""
    directives = _directives(path)
    questions = _questions(path)
    if path.is_dir():
        return "\n".join(_render_tree(path, questions)) + "\n"
    name = path.name.removesuffix(".txt")
    form_path = directives.get("path", f"{path.parent.name}/{name}")
    module = directives.get("module", f"forms.{path.parent.name}.{name.removesuffix('.py')}")
    model = source_model.SourceModule(_form_text(path), form_path, module, verify=True)
    expected = {scope for scope in model.scopes if scope.compiles_on(PYTHON)}
    differing = sorted(map(repr, set(model.compiled()) ^ expected))
    checked = [f"== code objects: {', '.join(differing) or 'one per scope that compiles on this Python'}"]
    return "\n".join([*_render_module(model, questions, directives), *checked]) + "\n"


@pytest.mark.parametrize("form", FORM_PATHS, ids=_form_id)
def test_model_answers_on_every_form(form: Path) -> None:
    """The model's answers on each form, the same on every supported Python (newer grammar from its version on)."""
    if _min_python(form) > PYTHON:
        pytest.skip(f"{_form_id(form)} needs Python {'.'.join(map(str, _min_python(form)))}")
    assert_output(render_form(form))


def _repository_files() -> list[Path]:
    return sorted(
        path
        for top in ("src", "scripts", "tests")
        for path in (ROOT / top).rglob("*.py")
        if "data" not in path.relative_to(ROOT / top).parts[:1]
    )


def _dotted(relative: str) -> str:
    """Return the dotted module name of a repository file (``src/`` dropped, a package ``__init__`` its package)."""
    return relative.removeprefix("src/").removesuffix(".py").removesuffix("/__init__").replace("/", ".")


@pytest.mark.parametrize("chunk", range(CHUNKS))
def test_model_agrees_with_the_compiler(chunk: int) -> None:
    """Every Python file of the repository (stored generated outputs aside) is read the compiler's way."""
    for path in _repository_files()[chunk::CHUNKS]:
        relative = path.relative_to(ROOT).as_posix()
        source_model.SourceModule(path.read_text(encoding="utf-8"), relative, _dotted(relative), verify=True).compiled()


def test_a_read_the_model_misses_stops_the_check() -> None:
    """The compiler check is two-way: a read the compiler counts and the model lacks is a disagreement."""
    model = source_model.SourceModule(
        "def f(key, rest):\n    return {key: 1, **rest}\n", "pkg/two_way.py", "pkg.two_way", verify=False
    )
    model.references.remove(next(item for item in model.references if item.name == "rest" and item.line == 2))
    with pytest.raises(source_model.SourceModelError) as error:
        model.verify()
    assert_output(str(error.value) + "\n")


def test_a_binding_the_model_misses_stops_the_check() -> None:
    """A name the compiler binds in a scope and the model does not is a disagreement."""
    model = source_model.SourceModule("y = 1\n", "pkg/binding.py", "pkg.binding", verify=False)
    model.module_scope.bindings.clear()
    assert_output("\n".join(model.disagreements()) + "\n")


CHECKED = "g = 1\n\n\ndef f(x):\n    y = x\n    return g, (lambda: y)\n"
"""The module every disagreement below starts from: a global, a parameter, a local and a lambda."""


def _ghost_binding(model: source_model.SourceModule) -> None:
    scope = model.module_scope
    scope.bindings["ghost"] = [replace(scope.bindings["g"][0], name="ghost", mangled="ghost")]


def _ghost_read(model: source_model.SourceModule) -> None:
    model.references.append(replace(model.references[-1], name="ghost", mangled="ghost"))


def _store_as_read(model: source_model.SourceModule) -> None:
    store = next(item for item in model.references if item.name == "y" and item.context == "store")
    model.references.append(replace(store, context="load"))


def _wrong_kind(model: source_model.SourceModule) -> None:
    local = next(item for item in model.references if item.name == "x" and item.context == "load")
    model.references.append(replace(local, name="g", mangled="g"))


DISAGREEMENTS: dict[str, Callable[[source_model.SourceModule], object]] = {
    "a binding the compiler lacks": _ghost_binding,
    "a global declaration the compiler lacks": lambda model: model.scopes[1].declared_global.add("y"),
    "a nonlocal declaration the compiler lacks": lambda model: model.scopes[1].declared_nonlocal.add("y"),
    "a declaration of a name the compiler does not know": lambda model: model.scopes[1].declared_global.add("other"),
    "a read of a name the compiler does not know": _ghost_read,
    "a read the compiler does not count": _store_as_read,
    "a read of the wrong kind": _wrong_kind,
    "a scope on the wrong line": lambda model: setattr(model.scopes[2], "line", 9),
    "a scope the model lacks": lambda model: model.scopes[1].children.clear(),
}
"""How each disagreement is made on a model of `CHECKED`; every rule of the compiler check stops on its own."""


def test_the_compiler_check_stops_on_every_disagreement() -> None:
    """Each kind of disagreement between the model and the compiler gives its own message."""
    found = []
    for label, change in DISAGREEMENTS.items():
        model = source_model.SourceModule(CHECKED, "pkg/checked.py", "pkg.checked", verify=False)
        change(model)
        found.append(f"{label}: {model.disagreements()}")
    assert_output("\n".join(found) + "\n")


def test_compiled_stops_on_a_scope_without_its_code_object() -> None:
    """A scope with no code object of its key, and a code object no scope claims, both stop `compiled()`."""
    renamed = source_model.SourceModule(CHECKED, "pkg/checked.py", "pkg.checked", verify=False)
    renamed.scopes[1].code_name = "other"
    dropped = source_model.SourceModule(CHECKED, "pkg/checked.py", "pkg.checked", verify=False)
    dropped.scopes.pop()
    messages = []
    for model in (renamed, dropped):
        with pytest.raises(source_model.SourceModelError) as error:
            model.compiled()
        messages.append(str(error.value))
    assert_output("\n".join(messages) + "\n")


PAIRED = (
    "pair = (lambda: (lambda: inner), lambda: right)\n"
    "table = {1: (lambda: first), (lambda: key): 2}\n"
    "default = lambda value=(lambda: given): value\n"
)
"""Lambdas that share a name and a line: nested in each other, side by side, and one in the other's default."""


def test_compiled_pairs_each_scope_with_its_own_code_object() -> None:
    """Each scope gets the code object compiled from its own node, whatever the order of nesting and evaluation."""
    model = source_model.SourceModule(PAIRED, "pkg/paired.py", "pkg.paired")
    paired = [
        f"{_name(scope)} at {scope.line}:{getattr(scope.node, 'col_offset', 0)} -> {code.co_qualname} {code.co_names}"
        for scope, code in model.compiled().items()
    ]
    assert_output("\n".join(paired) + "\n")


def test_a_module_the_compiler_rejects_stops_the_check() -> None:
    """A module `ast` reads and the compiler rejects (a late future import) stops both checks at its line."""
    model = source_model.SourceModule("x = 1\nfrom __future__ import annotations\n", "pkg/late.py", verify=False)
    with pytest.raises(source_model.SourceModelError) as error:
        model.compiled()
    assert_output("\n".join([*model.disagreements(), str(error.value)]) + "\n")


def test_question_captures_nest() -> None:
    """Nested captures each keep the questions asked inside them, also when they have recorded the same ones."""
    model = source_model.SourceModule("x = 1\n", verify=False)
    with source_model.questions_asked() as outer:
        model.bindings()
        with source_model.questions_asked() as inner:
            model.bindings()
        model.suppressions()
    assert_output(f"outer: {sorted(outer)}\ninner: {sorted(inner)}\n")


def test_the_model_refuses_what_it_has_no_rule_for() -> None:
    """A refusal names the file, the line, the form and the fix: a model rule and a form in the same pull request."""
    with pytest.raises(source_model.SourceModelError) as error:
        source_model.SourceModule("def f():\n    nonlocal x\n    x = 1\n", "pkg/refused.py", "pkg.refused")
    assert_output(str(error.value) + "\n")


def test_builtins_are_the_tables_on_this_python() -> None:
    """The builtins of this platform and Python are the floor set and the conditional names the table gives them.

    The names come from a fresh interpreter: code that earlier tests ran in this one may have added to `builtins`
    (``eval`` or ``exec`` with the builtins namespace as globals adds ``__builtins__`` to it).
    """
    command = [sys.executable, "-I", "-c", "import builtins; print(*dir(builtins))"]
    names = set(subprocess.run(command, capture_output=True, text=True, check=True).stdout.split())
    expected = source_model.BUILTINS.union(
        name
        for name, (platform, since) in source_model.CONDITIONAL_BUILTINS.items()
        if platform in {None, sys.platform} and since <= PYTHON
    )
    missing = sorted(expected - names)
    extra = sorted(names - expected)
    assert_output(f"missing here: {missing or 'none'}\nnot in the tables: {extra or 'none'}\n")


@dataclass(frozen=True)
class Finding:
    """One finding of a rule on a form: the rule (or the part of it) that reports it, its site, and the stored line.

    The site is a line of a one-module form, ``<module>:<line>`` in a package form (each prefixed by the folder a rule
    places the form in when it places it in several), or what a rule reports instead of a line.
    """

    rule: str
    site: str
    text: str


@dataclass(frozen=True)
class RuleRun:
    """A rule that reads Python through the model: its module, the questions it asks, and its run over one form."""

    module: str
    asks: frozenset[str]
    run: Callable[[Path], list[Finding]]


def _form_files(form: Path) -> list[tuple[str, str]]:
    """Return the name and text of each module of a form."""
    files = sorted(form.rglob("*.py")) if form.is_dir() else [form]
    return [(path.name.removesuffix(".txt"), path.read_text(encoding="utf-8")) for path in files]


def _site(form: Path, place: str, line: object) -> str:
    """Return the site of a finding at ``line`` of ``place``, a path that holds the form's name where the form is."""
    own = form.name.removesuffix(".txt")
    parts = [part for part in place.split("/") if part != own]
    return ":".join([*(["/".join(parts)] if parts else []), str(line)])


def _compiler_check(form: Path) -> list[Finding]:
    return [
        Finding("disagreement", _site(form, name, problem.split(":")[1]), problem)
        for name, text in _form_files(form)
        for problem in source_model.SourceModule(text, name, "forms.form", verify=False).disagreements()
    ]


def _private_scope_walk(form: Path) -> list[Finding]:
    return [
        Finding("private-scope-walk", _site(form, name, line), f"{name}:{line}")
        for name, text in _form_files(form)
        for line in check_ratchets.scope_walk_sites(text, name)
    ]


def _source_rules(form: Path) -> list[Finding]:
    """Run every source rule that needs no base over a form, placed as a module of the package, scripts and tests.

    A form that cites code (the directive ``citations: docstring``) stands in for the architecture page: its docstring
    is the page, and its module is a file of the package.
    """
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for area in (check_source_rules.PACKAGE, "scripts", "tests"):
            for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
                target = (
                    root
                    / area
                    / (form.name / path.relative_to(form) if form.is_dir() else path.name.removesuffix(".txt"))
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        if _directives(form).get("citations"):
            page = root / check_source_rules.ARCHITECTURE_DOCS
            page.parent.mkdir(parents=True)
            page.write_text(_form_text(form).split('"""', 2)[1], encoding="utf-8")
        found = check_source_rules.run(check_source_rules.Tree(root), check_source_rules.RULES, None)
        return [
            Finding(
                finding.rule,
                _site(form, place, finding.line),
                f"{place}:{finding.line}: {finding.rule}: {finding.detail}",
            )
            for findings in found.values()
            for finding in findings
            for place in [finding.path.replace(check_source_rules.PACKAGE, "src")]
        ]


ARCHITECTURE_LAYERS: tuple[check_architecture_boundaries.Layer, ...] = (
    "entrypoint",
    "parser",
    "config",
    "input-model",
    "reference",
    "shared-model",
)
"""The layers a form runs under unless it names its own: those with rules of their own on names."""


def _architecture_boundaries(form: Path) -> list[Finding]:
    """Run the layer rules of the layers above and the host rule over a form's modules, as files of a temporary tree."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        paths = []
        for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
            target = root / (path.relative_to(form) if form.is_dir() else path.name.removesuffix(".txt"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            paths.append(target)
        asked = [item.strip() for item in _directives(form).get("layers", "").split(",") if item.strip()]
        known = {*typing.get_args(check_architecture_boundaries.Layer), "host"}
        if unknown := sorted(set(asked) - known):
            msg = f"{form.name}: layers that are none of the guard's: {unknown}"
            raise ValueError(msg)
        layers = [name for name in asked if name != "host"] or ([] if asked else list(ARCHITECTURE_LAYERS))
        runs = [(layer, [(path, layer) for path in paths], []) for layer in layers]
        runs += [("host", [], paths)] if "host" in asked or not asked else []
        found = []
        for name, files, hosts in runs:
            violations = check_architecture_boundaries.check_files(files, allowlist={}, host_files=hosts)
            found += [
                Finding(
                    f"{name}/{violation.rule}",
                    _site(form, place, violation.line),
                    f"{name}: {place}:{violation.line}: {violation.rule}: {violation.target} (in {violation.qualname})",
                )
                for violation in violations
                for place in [violation.path.removeprefix(root.as_posix()).lstrip("/")]
            ]
        return found


def test_a_form_names_only_the_layers_the_guard_has(tmp_path: Path) -> None:
    """A layer the guard does not have stops the form, instead of selecting no run and finding nothing."""
    form = tmp_path / "form.py"
    form.write_text('"""A form.\nlayers: parser, shared_model\n"""\n', encoding="utf-8")
    with pytest.raises(ValueError, match="layers") as error:
        _architecture_boundaries(form)
    assert_output(str(error.value) + "\n")


def _where_disagreements(form: Path) -> list[Finding]:
    """Compare what `scripts.where` answers on a form's modules with the model's own answers and with every file read.

    The form keeps its layout under ``scripts/`` so that its imports between modules resolve. Each scope is defined
    where the model has it, each line belongs to the scope the model gives, and reading only the files whose text can
    hold an answer gives the same definitions and users as reading every file.
    """
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        (root / "src" / where.PACKAGE).mkdir(parents=True)
        (root / "scripts").mkdir()
        (root / where.RULES).write_text("rule = []\n", encoding="utf-8")
        for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
            target = (
                root
                / "scripts"
                / (form.name / path.relative_to(form) if form.is_dir() else path.name.removesuffix(".txt"))
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        repo = where.Repo(root, where.Page([], [], [], []))
        found: list[Finding] = []
        for path in sorted(repo.names):
            model = repo.model(path)
            place = str(path).removeprefix("scripts/")
            scopes = [scope for scope in model.scopes if scope.kind in where.DEFS and "<locals>" not in scope.qualname]
            names = {scope.qualname for scope in scopes}
            names |= {
                name
                for name, bindings in model.module_scope.bindings.items()
                if any(binding.kind in where.ASSIGNS for binding in bindings)
            }
            definitions = {name: repo.definitions(name) for name in sorted(names)}
            found += [
                Finding(
                    "definition",
                    _site(form, place, scope.line),
                    f"{path}:{scope.line}: where does not define {scope.qualname}",
                )
                for scope in scopes
                if (path, scope.line) not in {(site.path, site.line) for site in definitions[scope.qualname]}
            ]
            found += [
                Finding(
                    "definition-files",
                    _site(form, place, name),
                    f"{path}: {name} is defined in files where does not read: {sorted(set(every) - set(sites))}",
                )
                for name, sites in definitions.items()
                if sites != (every := repo.definitions(name, everywhere=True))
            ]
            found += [
                Finding(
                    "user-files",
                    _site(form, place, site.line),
                    f"{site.path}:{site.line}: {site.qualname} is read in files where does not read: {sorted(every)}",
                )
                for sites in definitions.values()
                for site in sites
                if site.path == path
                and site.parent == "module"
                and repo.module_users(site) != (every := repo.module_users(site, everywhere=True))
            ]
            found += [
                Finding(
                    "owner",
                    _site(form, place, line),
                    f"{path}:{line}: where reports {reported}, the model {expected.qualname!r}",
                )
                for line, text in enumerate(repo.text(path).splitlines(), 1)
                if text.strip()
                and (expected := model.owner(line, len(text) - len(text.lstrip()))).kind in where.SCOPES
                and (
                    reported := [
                        item.partition(" (")[0]
                        for item in where.line_block(repo, path, line, {where.Kind.LINE})[0].sections[0].items
                    ]
                )[-1:]
                != [expected.qualname or "<module>"]
            ]
    return found


def _import_cycle(form: Path) -> list[Finding]:
    """Return the import cycles of a package form by measure; a one-module form has no import between modules.

    The site of a cycle is its modules, named inside the form and joined by ``+``.
    """
    if not form.is_dir():
        return []
    found = import_cycles.tree_cycles(source_model.SourceTree(form.parent, [form.name]))
    return [
        Finding(
            measure, "+".join(name.removeprefix(f"{form.name}.") for name in cycle), f"{measure}: {', '.join(cycle)}"
        )
        for measure, cycles in found.items()
        for cycle in cycles
    ]


def _template_assignment_lines(form: Path) -> list[Finding]:
    return [
        Finding(
            kind.replace(" ", "-"), _site(form, name, binding.line), f"{name}:{binding.line}: {binding.name} {kind}"
        )
        for name, text in _form_files(form)
        for binding in field_lines(text) or ()
        for kind in ["no value" if binding.value is None else "with a value"]
    ]


def _check_runs(form: Path) -> list[Finding]:
    """Return the check calls a form's tests assert, with their arguments, as `tests.test_checks_are_run` finds them.

    Each module of the form stands as a test module of a repository that holds nothing else. A call is reported under
    the script it runs, at the test that runs it.
    """
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
            name = path.name.removesuffix(".txt")
            target = root / "tests" / (path.relative_to(form).parent if form.is_dir() else "") / f"test_{name}"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        return [
            Finding(script, node.rpartition("::")[2], f"{node}: {script} {list(argv)}")
            for node, script, argv in checks_are_run._tree_tests(root)
        ]


def _generation_store_usage(form: Path) -> list[Finding]:
    """Run the generation store check over a form's modules, as parser code and as model code."""
    paths = sorted(form.rglob("*.py")) if form.is_dir() else [form]
    found = []
    for scope in typing.get_args(check_generation_store_usage.Scope):
        for violation in check_generation_store_usage.scan_files([(path, scope) for path in paths]).violations:
            place = violation.path.relative_to(form.parent).as_posix().removesuffix(".txt")
            target = re.sub(r"[\s\"]", "", violation.target)
            found.append(
                Finding(
                    f"{scope}/{target}",
                    _site(form, place, violation.line),
                    f"{scope}: {place}:{violation.line}: {violation.message} (in {violation.function})",
                )
            )
    return found


def _pass_pipeline(form: Path) -> list[Finding]:
    """Run the pass pipeline check's readings over a form's modules, each one standing as the module of ``Parser``.

    The readings are the calls that build ``FinalFacts``, the places outside the body of the form's ``Parser`` that
    reach a mangled member of it, and the deep warnings.
    """
    found = []
    for name, text in _form_files(form):
        model = source_model.SourceModule(text, name, "forms.form")
        members = check_pass_pipeline.mangled_members(model)
        found += [
            Finding("final-facts", _site(form, name, line), f"{name}:{line}: FinalFacts built in {scope or '<module>'}")
            for scope, line in check_pass_pipeline.calls(model, "FinalFacts")
        ]
        found += [
            Finding("mangled-member", _site(form, name, line), f"{name}:{line}: reaches Parser.{attr}")
            for line, attr in check_pass_pipeline.reached_members(model, members, own=True)
        ]
        found += [
            Finding("deep-warning", _site(form, name, line), f"{name}:{line}: deep warning in {function}")
            for function, line in check_pass_pipeline.deep_warnings(model)
        ]
    return found


def _coverage_exclusions(form: Path) -> list[Finding]:
    """Run coverage_exclusions over a form's modules as if every statement ran, with coverage's default marks."""
    found = []
    for name, text in _form_files(form):
        parser = PythonParser(text=text, filename=name, exclude="|".join(DEFAULT_EXCLUDE))
        parser.parse_source()
        found += [
            Finding("ran", _site(form, name, mark), f"{name}:{mark}: {ran} statement(s) of the excluded region ran")
            for mark, ran in coverage_exclusions._executed_regions(parser, set(parser.raw_statements))
        ]
    return found


def _unbound_names(form: Path) -> list[Finding]:
    """Return the names each module of a form loads that nothing binds in time, as the import closure audit reads."""
    return [
        Finding("unbound", _site(form, name, line), f"{name}:{line}: {unbound}")
        for name, text in _form_files(form)
        for line, unbound in import_closure.unbound_sites(source_model.SourceModule(text, name))
    ]


def _placed_under_tests(form: Path, root: Path, folder: str) -> list[Path]:
    """Write a form's modules below ``tests/<folder>`` of a temporary tree; return the paths of the modules."""
    paths = []
    for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
        relative = form.name / path.relative_to(form) if form.is_dir() else Path(path.name.removesuffix(".txt"))
        target = root / "tests" / folder / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        paths.append(target)
    (root / "tests" / "data" / "expected").mkdir(parents=True, exist_ok=True)
    return paths


def _assert_helper_findings(form: Path) -> list[Finding]:
    """Run the assert helper guard over a form's modules, placed as a report helper module, which every rule reads."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        tests_root = root / "tests"
        found = []
        for path in _placed_under_tests(form, root, "api_generation/support"):
            place = path.relative_to(tests_root / "api_generation" / "support").as_posix()
            found += [
                Finding(finding.rule, _site(form, place, finding.lineno), f"{place}:{finding.lineno}: {finding}")
                for finding in assert_helper_usage._analyze(path, tests_root).findings
            ]
        return found


def _expected_reads(form: Path) -> list[Finding]:
    """Return the reads, copies and listings of stored expected output a form's modules make, placed below tests/."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        _placed_under_tests(form, root, "")
        return [
            Finding("expected-read", _site(form, place, access.lineno), f"{place}:{access.lineno}: {access.text}")
            for access in expected_access.expected_accesses(root, root / "tests")
            for place in [access.module.relative_to("tests").as_posix()]
        ]


def _hook_runs(form: Path) -> list[Finding]:
    """Return the other modules of a form each module runs through its imports, the form placed below ``scripts/``.

    The site is the module run, named inside the form.
    """
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder)
        paths = []
        for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
            relative = form.name / path.relative_to(form) if form.is_dir() else Path(path.name.removesuffix(".txt"))
            target = root / "scripts" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
            paths.append(target)
        found = []
        for path in paths:
            entry = path.relative_to(root).as_posix()
            found += [
                Finding("runs", ran.removeprefix(f"scripts/{form.name}/"), f"{entry} runs {ran}")
                for ran in pre_commit_hooks.runs(root, path)
                if ran != entry
            ]
        return found


def _defined_tests(form: Path) -> list[Finding]:
    """Return the node ids each module of a form defines below its path, as the CI test target check reads them."""
    return [
        Finding("defines", node, f"{name}::{node}")
        for name, text in _form_files(form)
        for node in sorted(ci_workflow.defined_tests(source_model.SourceModule(text, name, "forms.form")))
    ]


def _typing_only_parts(form: Path) -> list[Finding]:
    """Return the code tokens of a form that `scripts/_change_units.py` gives no unit: those only type checkers read.

    Every token counts as added, as in a new module; a header token is named with the scope whose header holds it.
    """
    found = []
    for name, text in _form_files(form):
        model = source_model.SourceModule(text, name, "forms.form", verify=False)
        for item in model.tokens():
            line, column = item.token.start
            model.segment(line, column)
            header = model.header_of(line, column)
            if form_of := model.typing_only(line, column):
                where = f" (header of {header.qualname})" if header is not None else ""
                found.append(Finding(form_of, _site(form, name, line), f"{name}:{line}:{column}: {form_of}{where}"))
    return found


def _legacy_row_reads(form: Path) -> list[Finding]:
    """Return the lines that read each row a form names, with their kinds, the form standing as a package.

    A module form stands inside one.
    """
    with tempfile.TemporaryDirectory() as folder:
        package = Path(folder) / "form"
        package.mkdir()
        for path in sorted(form.rglob("*.py")) if form.is_dir() else [form]:
            target = package / (path.relative_to(form) if form.is_dir() else path.name.removesuffix(".txt"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        (package / "__init__.py").touch()
        sites = legacy_row_reads.reading_sites_of(Path(folder), "form")
        return [
            Finding("reads", _site(form, place, line), f"{place}:{line}: {row} ({', '.join(kinds)})")
            for row, lines in sorted(legacy_row_reads.reading_lines_of(Path(folder), "form").items())
            for filename, line in sorted(lines)
            for place in [Path(filename).relative_to(package).as_posix()]
            for kinds in [sorted(kind for kind, held in sites[row].items() if (filename, line) in held)]
        ]


RULES: dict[str, RuleRun] = {
    "source-model-compiler-check": RuleRun(
        "scripts._source_model", frozenset({"bindings", "references"}), _compiler_check
    ),
    "private-scope-walk": RuleRun("scripts.check_ratchets", frozenset({"bindings", "references"}), _private_scope_walk),
    "source-rules": RuleRun(
        "scripts.check_source_rules",
        frozenset({"bindings", "references", "imports", "suppressions", "extents"}),
        _source_rules,
    ),
    "architecture-boundaries": RuleRun(
        "scripts.check_architecture_boundaries",
        frozenset({"bindings", "references", "imports"}),
        _architecture_boundaries,
    ),
    "where-agrees-with-the-model": RuleRun(
        "scripts.where", frozenset({"bindings", "references", "imports", "extents"}), _where_disagreements
    ),
    "import-cycle": RuleRun("scripts._import_cycles", frozenset({"imports", "references"}), _import_cycle),
    "template-assignments": RuleRun(
        "tests.checks.template_assignments", frozenset({"bindings", "references"}), _template_assignment_lines
    ),
    "check-run-on-the-tree": RuleRun("tests.test_checks_are_run", frozenset({"bindings", "references"}), _check_runs),
    "pass-pipeline": RuleRun("scripts.check_pass_pipeline", frozenset({"bindings", "references"}), _pass_pipeline),
    "coverage-exclusions": RuleRun("scripts.coverage_exclusions", frozenset({"extents"}), _coverage_exclusions),
    "legacy-row-reads": RuleRun(
        "scripts.legacy_row_reads",
        frozenset({"bindings", "references", "imports", "extents"}),
        _legacy_row_reads,
    ),
    "generation-store-usage": RuleRun(
        "scripts.check_generation_store_usage",
        frozenset({"bindings", "references", "imports"}),
        _generation_store_usage,
    ),
    "generated-import-closure": RuleRun(
        "tests.test_generated_import_closure", frozenset({"bindings", "references", "extents"}), _unbound_names
    ),
    "assert-helper-usage": RuleRun(
        "tests.test_assert_helper_usage", frozenset({"bindings", "references", "imports"}), _assert_helper_findings
    ),
    "pre-commit-hook-runs": RuleRun("tests.test_pre_commit_hooks", frozenset({"imports", "references"}), _hook_runs),
    "ci-test-targets": RuleRun("tests.test_ci_workflow", frozenset({"bindings", "references"}), _defined_tests),
    "expected-access": RuleRun(
        "tests.expected_access", frozenset({"bindings", "references", "imports"}), _expected_reads
    ),
    "change-units-typing-only": RuleRun(
        "scripts._change_units", frozenset({"references", "extents"}), _typing_only_parts
    ),
}
"""Every rule that reads Python through the model; `verdicts.toml` states its verdict on each form of its questions."""


def _sites(found: Counter[tuple[str, str]]) -> str:
    """Render sites the way a verdict lists them: ``<rule> <site> <site>; <rule> <site>``, rules and sites sorted."""
    rules = sorted({rule for rule, _ in found.elements()})
    return "; ".join(
        " ".join([rule, *sorted((site for name, site in found.elements() if name == rule), key=_site_key)])
        for rule in rules
    )


def _site_key(site: str) -> tuple[str, int, str]:
    """Order sites by place, then by line as a number."""
    place, _, line = site.rpartition(":")
    return (place, int(line), "") if line.isdigit() else (site, 0, site)


def _stated_sites(listed: str) -> Counter[tuple[str, str]]:
    """Read the sites a ``finding: <rule> <site> <site>; <rule> <site>`` verdict lists after its colon."""
    groups = [group.split() for group in listed.split(";") if group.strip()]
    return Counter((words[0], site) for words in groups for site in words[1:])


def verdict_problems(findings: dict[str, list[Finding]], entries: dict[str, str]) -> list[str]:
    """Compare a rule's findings per form with its verdict entries.

    A verdict is ``no finding: <reason>`` or ``finding: <rule> <site> <site>; <rule> <site>``, which lists every
    finding by the rule (or part of the rule) that reports it and its site, once per finding. A finding the verdict does
    not list and a listed one the rule no longer gives are both problems, so a rule that loses one site of a form fails
    here and not only in the stored findings, which a fix run accepts.
    """
    missing = [f"{form}: no verdict" for form in sorted(set(findings) - set(entries))]
    stale = [f"{form}: verdict for a form the rule does not read" for form in sorted(set(entries) - set(findings))]
    wrong = []
    for form in sorted(set(findings) & set(entries)):
        kind, _, listed = entries[form].partition(":")
        found = Counter((finding.rule, finding.site) for finding in findings[form])
        stated = _stated_sites(listed) if kind.strip() == "finding" else Counter()
        if kind.strip() not in {"finding", "no finding"} or (kind.strip() == "finding" and not stated):
            wrong.append(f"{form}: verdict {entries[form]!r} is neither 'finding: <rule> <site>' nor 'no finding: ...'")
            continue
        wrong += [f"{form}: the rule gives no finding at {_sites(stated - found)}"] * bool(stated - found)
        wrong += [f"{form}: the verdict does not list {_sites(found - stated)}"] * bool(found - stated)
    return [*missing, *stale, *wrong]


@pytest.mark.allow_direct_assert("a verdict gate fails on its own, never through a stored output a fix run accepts")
@pytest.mark.parametrize("rule", sorted(RULES))
def test_every_rule_states_its_verdicts(rule: str) -> None:
    """Each rule runs over every form of the questions it asks; each form has its verdict, and none is stale.

    The stored findings are those of the forms every supported Python reads: a form of a newer Python has its verdict
    checked where it runs, so the stored output is the same in every environment.
    """
    run = RULES[rule]
    forms = [form for form in FORM_PATHS if _questions(form) & run.asks and _min_python(form) <= PYTHON]
    with source_model.questions_asked() as asked:
        findings = {_form_id(form): run.run(form) for form in forms}
    entries = tomllib.loads(VERDICTS.read_text(encoding="utf-8")).get(rule, {})
    gated = {_form_id(form) for form in FORM_PATHS if _min_python(form) > PYTHON}
    problems = verdict_problems(findings, {form: verdict for form, verdict in entries.items() if form not in gated})
    undeclared = sorted(asked - run.asks)
    assert not problems, f"{rule}: {problems}; state the rule's verdict on each form in {VERDICTS.relative_to(ROOT)}"
    assert not undeclared, f"{rule} asks {undeclared}; add them to its RuleRun.asks"
    every_python = {_form_id(form) for form in forms if _min_python(form) == OLDEST_PYTHON}
    found = [f"{form}: {finding.text}" for form, items in findings.items() if form in every_python for finding in items]
    report = [f"verdict problems: {problems or 'none'}", f"undeclared questions: {undeclared or 'none'}", *found]
    assert_output("\n".join(report) + "\n")


def test_verdict_problems() -> None:
    """A missing, stale or wrong verdict is a problem, and so is a lost, added or moved site; a matching one is not.

    The form ``a/x`` has three findings: two of one rule (one line twice) and one of another.
    """
    three = [Finding("hit", "3", "a"), Finding("hit", "3", "b"), Finding("other", "src:7", "c")]
    cases = [
        ([], "no finding: clean"),
        (three, "finding: hit 3 3; other src:7"),
        (three, "finding: other src:7; hit 3 3"),
        ([], None),
        (None, "finding: hit 3"),
        ([], "finding: hit 3"),
        (three, "no finding: clean"),
        (three, "finding"),
        (three, "found: hit 3 3; other src:7"),
        (three[1:], "finding: hit 3 3; other src:7"),
        (three[:2], "finding: hit 3 3; other src:7"),
        (three, "finding: hit 3; other src:7"),
        ([*three[:2], Finding("other", "src:8", "c")], "finding: hit 3 3; other src:7"),
        ([*three[:2], Finding("hit", "src:7", "c")], "finding: hit 3 3; other src:7"),
    ]
    rendered = []
    for found, verdict in cases:
        findings = {} if found is None else {"a/x": found}
        entries = {} if verdict is None else {"a/x": verdict}
        sites = [f"{finding.rule}@{finding.site}" for finding in found or []]
        rendered.append(f"{sites} {entries}: {verdict_problems(findings, entries)}")
    assert_output("\n".join(rendered) + "\n")


@pytest.mark.allow_direct_assert("a verdict gate fails on its own, never through a stored output a fix run accepts")
def test_verdicts_name_registered_rules() -> None:
    """Every section of `verdicts.toml` is a registered rule."""
    unregistered = sorted(set(tomllib.loads(VERDICTS.read_text(encoding="utf-8"))) - set(RULES))
    assert not unregistered, f"sections of {VERDICTS.relative_to(ROOT)} without a rule in RULES: {unregistered}"
    assert_output(f"unregistered sections: {unregistered or 'none'}\n")


@pytest.mark.allow_direct_assert("a verdict gate fails on its own, never through a stored output a fix run accepts")
def test_rules_declare_what_they_ask() -> None:
    """Every module that imports the model (read with the model's own imports) has a rule in `RULES`."""
    importers = {
        _dotted(relative)
        for path in _repository_files()
        if "_source_model" in (text := path.read_text(encoding="utf-8"))
        for relative in [path.relative_to(ROOT).as_posix()]
        for item in source_model.SourceModule(text, relative, _dotted(relative), verify=False).imports
        if MODEL_NAMES & {item.source, item.target}
    }
    unregistered = sorted(importers - {__name__} - {run.module for run in RULES.values()})
    assert not unregistered, f"modules that read Python through the model without a rule in RULES: {unregistered}"
    assert_output(f"importers without a rule: {unregistered or 'none'}\n")
