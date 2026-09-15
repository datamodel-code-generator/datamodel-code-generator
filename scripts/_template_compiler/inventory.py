"""Feature inventory and compatibility guard for built-in Jinja templates."""

from __future__ import annotations

import os
import stat
from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from jinja2 import Environment, nodes

if TYPE_CHECKING:
    from pathlib import Path

SUPPORTED_NODE_TYPES = frozenset({
    "And",
    "Assign",
    "Call",
    "Compare",
    "Concat",
    "Const",
    "Dict",
    "Filter",
    "FilterBlock",
    "For",
    "Getattr",
    "Getitem",
    "If",
    "Include",
    "Keyword",
    "List",
    "Macro",
    "NSRef",
    "Name",
    "Not",
    "Operand",
    "Or",
    "Output",
    "Pair",
    "Template",
    "TemplateData",
    "Test",
    "Tuple",
})
SUPPORTED_FILTERS = frozenset({
    "default",
    "indent",
    "join",
    "length",
    "list",
    "pprint",
    "replace",
    "repr",
    "selectattr",
})
SUPPORTED_TESTS = frozenset({"defined", "equalto", "false", "none"})
SUPPORTED_GLOBALS = frozenset({"namespace"})
SUPPORTED_LOOP_ATTRIBUTES = frozenset({"last"})
SUPPORTED_ASSIGNMENTS = frozenset({"Name", "NSRef"})
SUPPORTED_CALLS = frozenset({"append", "get", "items", "get_type_annotation", "get_type_hint", "namespace"})
_SELECTATTR_TEST_ARGUMENT_INDEX = 1
_SELECTATTR_TEST_MINIMUM_ARGUMENTS = 2
_SCOPED_ASSIGNMENT_SITES = frozenset({
    ("dataclass.jinja2", 7, "Name", "_", "Call"),
    ("msgspec.jinja2", 19, "NSRef", "ns.has_rendered_field", "Const"),
    ("msgspec.jinja2", 22, "NSRef", "ns.has_rendered_field", "Const"),
    ("pydantic_v2/RootModel.jinja2", 37, "Name", "field", "Getitem"),
    ("pydantic_v2/dataclass.jinja2", 7, "Name", "_", "Call"),
    ("pydantic_v2/dataclass.jinja2", 11, "Name", "config_items", "List"),
    ("pydantic_v2/dataclass.jinja2", 13, "Name", "_", "Call"),
    ("pydantic_v2/dataclass.jinja2", 15, "Name", "_", "Call"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 2, "Name", "schema_validator_state", "Call"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 12, "Name", "pattern_property", "Getitem"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 23, "NSRef", "schema_validator_state.has_prior", "Const"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 25, "Name", "one_of_required_groups", "Filter"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 36, "NSRef", "schema_validator_state.has_prior", "Const"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 38, "Name", "any_of_required_groups", "Filter"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 49, "NSRef", "schema_validator_state.has_prior", "Const"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 51, "Name", "not_required_groups", "Filter"),
    ("pydantic_v2/schema_runtime_validation.jinja2", 62, "NSRef", "schema_validator_state.has_prior", "Const"),
})
_SCOPED_ASSIGNMENT_CONTAINERS = (nodes.FilterBlock, nodes.For, nodes.If, nodes.Macro)
_SUPPORTED_GETATTR_ROOT_NAMES = frozenset({
    "_field",
    "_fields",
    "args",
    "config",
    "config_items",
    "data_type",
    "field",
    "fields",
    "loop",
    "ns",
    "pattern_property",
    "rule",
    "schema_runtime_validation",
    "schema_validator_state",
    "v",
})
# ``prepared_validators`` is built as dictionaries in pydantic_v2/base_model.py.
# The template intentionally uses Jinja's mapping-dot fallback for these keys.
KNOWN_MAPPING_DOT_ACCESSES = frozenset({
    ("pydantic_v2/BaseModel.jinja2", "v", "fields_str"),
    ("pydantic_v2/BaseModel.jinja2", "v", "mode"),
    ("pydantic_v2/BaseModel.jinja2", "v", "mode_str"),
    ("pydantic_v2/BaseModel.jinja2", "v", "method_name"),
    ("pydantic_v2/BaseModel.jinja2", "v", "function_name"),
})
_TEMPLATE_SOURCE_GUIDANCE = "update the standalone template compiler or move the source under the template directory"
_MISSING_TEMPLATE_SOURCE_GUIDANCE = "update the standalone template compiler or remove the broken template path"
_INVALID_TEMPLATE_SOURCE_GUIDANCE = "update the standalone template compiler or remove the invalid template path"
_TEMPLATE_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0)


@dataclass(frozen=True)
class TemplateInventory:
    """The parsed feature set, kept useful for both checks and tests."""

    nodes: dict[str, tuple[str, ...]]
    filters: dict[str, tuple[str, ...]]
    tests: dict[str, tuple[str, ...]]
    globals: dict[str, tuple[str, ...]]
    loop_attributes: dict[str, tuple[str, ...]]
    assignments: dict[str, tuple[str, ...]]
    for_targets: dict[str, tuple[str, ...]]
    macros: dict[str, tuple[str, ...]]
    includes: dict[str, tuple[str, ...]]
    imports_and_inheritance: dict[str, tuple[str, ...]]
    undefined_usage: dict[str, tuple[str, ...]]
    namespaces: dict[str, tuple[str, ...]]
    mapping_dot_accesses: dict[str, tuple[str, ...]]


def iter_template_paths(template_dir: Path) -> tuple[Path, ...]:
    paths = tuple(sorted(template_dir.rglob("*.jinja2"), key=lambda path: path.relative_to(template_dir).as_posix()))
    for path in paths:
        _resolve_template_path(template_dir, path)
    return paths


def _template_source_error(
    display_path: str,
    message: str,
    guidance: str = _TEMPLATE_SOURCE_GUIDANCE,
) -> ValueError:
    return ValueError(f"{display_path}: {message}; {guidance}")


def _template_display_path(template_dir: Path, path: Path) -> str:
    try:
        return path.relative_to(template_dir).as_posix()
    except ValueError:
        return path.as_posix()


def _resolve_template_path(template_dir: Path, path: Path) -> Path:
    """Resolve a template source while keeping it inside the template root."""
    display_path = _template_display_path(template_dir, path)

    try:
        resolved_path = path.resolve(strict=True)
        resolved_template_dir = template_dir.resolve(strict=True)
    except FileNotFoundError:
        raise _template_source_error(
            display_path,
            "template source does not exist",
            _MISSING_TEMPLATE_SOURCE_GUIDANCE,
        ) from None
    except (OSError, RuntimeError) as error:
        detail = repr(error)
        if isinstance(error, OSError) and (strerror := error.strerror):
            detail = strerror
        raise _template_source_error(
            display_path,
            f"template source cannot be resolved ({detail})",
        ) from None

    try:
        resolved_path.relative_to(resolved_template_dir)
    except ValueError:
        raise _template_source_error(
            display_path,
            "template source resolves outside the template root",
        ) from None

    if not resolved_path.is_file():
        raise _template_source_error(
            display_path,
            "template source is not a file",
            _INVALID_TEMPLATE_SOURCE_GUIDANCE,
        )
    return resolved_path


def _read_template_source(template_dir: Path, path: Path) -> str:
    """Read a template through a descriptor matching a verified source.

    A source path can be replaced after canonical containment validation.  The
    validated target's identity is captured before opening the original path,
    then compared with the descriptor before any bytes are read.  Reading that
    descriptor keeps a later symlink swap from changing the source.
    """
    display_path = _template_display_path(template_dir, path)
    resolved_path = _resolve_template_path(template_dir, path)
    try:
        source_stat = resolved_path.lstat()
    except FileNotFoundError:
        raise _template_source_error(display_path, "template source changed during validation; retry") from None
    if not stat.S_ISREG(source_stat.st_mode):
        raise _template_source_error(display_path, "template source changed during validation; retry")

    try:
        # A race may still replace the source with a FIFO.  Non-blocking mode
        # is available on POSIX; it is a no-op where the flag is unavailable.
        descriptor = os.open(path, _TEMPLATE_OPEN_FLAGS)
    except OSError as error:
        raise _template_source_error(display_path, f"template source cannot be opened ({error.strerror})") from None

    with os.fdopen(descriptor, encoding="utf-8") as source_file:
        if not os.path.samestat(source_stat, os.fstat(source_file.fileno())):
            raise _template_source_error(display_path, "template source changed during validation; retry")
        return source_file.read()


def build_environment() -> Environment:
    """Match production's Jinja configuration for the parser frontend."""
    from jinja2 import select_autoescape  # noqa: PLC0415

    from datamodel_code_generator.model.base import escape_docstring, format_docstring  # noqa: PLC0415

    environment = Environment(autoescape=select_autoescape(["html", "xml"]))
    environment.filters["escape_docstring"] = escape_docstring
    environment.filters["format_docstring"] = format_docstring
    environment.filters["repr"] = repr
    return environment


def _location(path: Path, node: nodes.Node) -> str:
    line = getattr(node, "lineno", None)
    return f"{path}:{line}" if line is not None else str(path)


def _record(values: dict[str, list[str]], name: str, path: Path, node: nodes.Node) -> None:
    values[name].append(_location(path, node))


def _unsupported(path: Path, node: nodes.Node, kind: str, name: str) -> ValueError:
    return ValueError(
        f"{_location(path, node)}: unsupported Jinja {kind} {name!r}; "
        "update the standalone template compiler before using this feature"
    )


def _call_name(node: nodes.Call) -> str | None:
    if isinstance(node.node, nodes.Name):
        return node.node.name
    if isinstance(node.node, nodes.Getattr):
        return node.node.attr
    return None


def _assignment_name(node: nodes.Assign) -> str:
    if isinstance(node.target, nodes.Name):
        return node.target.name
    if isinstance(node.target, nodes.NSRef):
        return f"{node.target.name}.{node.target.attr}"
    return type(node.target).__name__


def _validate_scoped_assignments(path: Path, ast: nodes.Template) -> None:
    """Allow only the built-in scoped assignments the standalone compiler models.

    Jinja ``if`` blocks share their containing scope while loop and macro bodies
    do not.  The standalone compiler deliberately does not implement that full
    lexical-assignment model, so a new scoped ``set`` must be reviewed rather
    than silently receiving Python's different binding behaviour.
    """

    def visit(node: nodes.Node, containers: tuple[str, ...] = ()) -> None:
        if isinstance(node, nodes.Assign) and containers:
            assignment = (
                path.as_posix(),
                node.lineno,
                type(node.target).__name__,
                _assignment_name(node),
                type(node.node).__name__,
            )
            if assignment not in _SCOPED_ASSIGNMENT_SITES:
                location = "/".join(containers)
                raise _unsupported(path, node, f"scoped assignment in {location}", _assignment_name(node))
        child_containers = (
            (*containers, type(node).__name__) if isinstance(node, _SCOPED_ASSIGNMENT_CONTAINERS) else containers
        )
        for child in node.iter_child_nodes():
            visit(child, child_containers)

    visit(ast)


def _validate_for_target(path: Path, node: nodes.For) -> None:
    if not isinstance(node.target, nodes.Tuple):
        return
    if any(isinstance(item, nodes.Tuple) for item in node.target.items):
        raise _unsupported(path, node, "for target", "nested tuple")


def _getattr_root(node: nodes.Expr) -> nodes.Expr:
    while isinstance(node, (nodes.Getattr, nodes.Getitem)):
        node = node.node
    return node


def _validate_getattr_root(path: Path, node: nodes.Getattr) -> None:
    root = _getattr_root(node)
    if isinstance(root, nodes.Name):
        if root.name not in _SUPPORTED_GETATTR_ROOT_NAMES:
            raise _unsupported(path, node, "mapping dot access", node.attr)
        if root.name != "v":
            return
        mapping_access = (path.as_posix(), root.name, node.attr)
        if mapping_access in KNOWN_MAPPING_DOT_ACCESSES:
            return
        raise _unsupported(path, node, "mapping dot access", node.attr)
    if isinstance(root, (nodes.Filter, nodes.Or)) and node.attr == "items":
        return
    raise _unsupported(path, node, "mapping dot access", node.attr)


def _validate_macro_local_captures(path: Path, ast: nodes.Template) -> None:
    """Reject macros that close over template-local ``set`` bindings."""
    local_names = {
        assignment.target.name
        for assignment in ast.body
        if isinstance(assignment, nodes.Assign) and isinstance(assignment.target, nodes.Name)
    }
    if not local_names:
        return
    for macro in ast.find_all(nodes.Macro):
        parameters = {argument.name for argument in macro.args}
        if (
            captured := next(
                (
                    name
                    for name in macro.find_all(nodes.Name)
                    if name.ctx == "load" and name.name in local_names - parameters
                ),
                None,
            )
        ) is not None:
            raise _unsupported(path, captured, "macro local capture", captured.name)


def _validate_include_macro_captures(templates: dict[Path, nodes.Template], graph: dict[Path, set[Path]]) -> None:
    """Reject included templates which depend on a macro from an includer.

    Includes receive ordinary context bindings in generated code, but macros
    are compiler-local functions and cannot be captured across generated
    modules without a separate lexical-environment implementation.
    """
    macro_names = {path: {macro.name for macro in ast.find_all(nodes.Macro)} for path, ast in templates.items()}

    def visit(path: Path, inherited: frozenset[str]) -> None:
        visible = inherited | macro_names[path]
        for included_path in graph.get(path, ()):
            locally_defined = macro_names[included_path]
            captured = next(
                (
                    name
                    for name in templates[included_path].find_all(nodes.Name)
                    if name.ctx == "load" and name.name in visible - locally_defined
                ),
                None,
            )
            if captured is not None:
                raise _unsupported(included_path, captured, "include macro capture", captured.name)
            visit(included_path, visible)

    for path in templates:
        visit(path, frozenset())


def inventory_templates(template_dir: Path, environment: Environment | None = None) -> TemplateInventory:
    """Parse and validate every built-in template, returning its derived inventory."""
    env = environment or build_environment()
    values: dict[str, dict[str, list[str]]] = {
        name: defaultdict(list)
        for name in (
            "nodes",
            "filters",
            "tests",
            "globals",
            "loop_attributes",
            "assignments",
            "for_targets",
            "macros",
            "includes",
            "imports_and_inheritance",
            "undefined_usage",
            "namespaces",
            "mapping_dot_accesses",
        )
    }
    include_graph: dict[Path, set[Path]] = defaultdict(set)
    templates: dict[Path, nodes.Template] = {}
    for path in iter_template_paths(template_dir):
        relative_path = path.relative_to(template_dir)
        ast = env.parse(_read_template_source(template_dir, path))
        templates[relative_path] = ast
        _validate_scoped_assignments(relative_path, ast)
        _validate_macro_local_captures(relative_path, ast)
        _record(values["nodes"], type(ast).__name__, relative_path, ast)
        static_mapping_names = {
            node.target.name
            for node in ast.find_all(nodes.Assign)
            if isinstance(node.target, nodes.Name) and isinstance(node.node, nodes.Dict)
        }
        for node in ast.find_all(nodes.Node):
            node_name = type(node).__name__
            _record(values["nodes"], node_name, relative_path, node)
            if node_name not in SUPPORTED_NODE_TYPES and not isinstance(
                node, (nodes.Import, nodes.FromImport, nodes.Extends)
            ):
                raise _unsupported(relative_path, node, "AST node", node_name)
            if isinstance(node, nodes.Filter):
                _record(values["filters"], node.name, relative_path, node)
                if node.name not in SUPPORTED_FILTERS:
                    raise _unsupported(relative_path, node, "filter", node.name)
                if node.name == "selectattr":
                    _inventory_selectattr_test(values["tests"], relative_path, node)
                if node.name == "default":
                    _record(values["undefined_usage"], "default filter", relative_path, node)
            elif isinstance(node, nodes.Test):
                _record(values["tests"], node.name, relative_path, node)
                if node.name not in SUPPORTED_TESTS:
                    raise _unsupported(relative_path, node, "test", node.name)
                if node.name == "defined":
                    _record(values["undefined_usage"], "defined test", relative_path, node)
            elif isinstance(node, nodes.Name) and node.ctx == "load" and node.name == "namespace":
                _record(values["globals"], node.name, relative_path, node)
            elif isinstance(node, nodes.Getattr) and isinstance(node.node, nodes.Name) and node.node.name == "loop":
                _record(values["loop_attributes"], node.attr, relative_path, node)
                if node.attr not in SUPPORTED_LOOP_ATTRIBUTES:
                    raise _unsupported(relative_path, node, "loop attribute", node.attr)
            elif isinstance(node, nodes.Assign):
                assignment = type(node.target).__name__
                _record(values["assignments"], assignment, relative_path, node)
                if assignment not in SUPPORTED_ASSIGNMENTS:
                    raise _unsupported(relative_path, node, "assignment target", assignment)
                if isinstance(node.target, nodes.NSRef):
                    _record(values["namespaces"], "attribute mutation", relative_path, node)
            elif isinstance(node, nodes.For):
                _record(values["for_targets"], _target_form(node.target), relative_path, node)
                _validate_for_target(relative_path, node)
            elif isinstance(node, nodes.Macro):
                _inventory_macro(values["macros"], relative_path, node)
            elif isinstance(node, nodes.Include):
                if not isinstance(node.template, nodes.Const) or not isinstance(node.template.value, str):
                    raise _unsupported(relative_path, node, "include", "dynamic include")
                _record(values["includes"], node.template.value, relative_path, node)
                include_path = _resolve_template_path(
                    template_dir,
                    template_dir / relative_path.parent / node.template.value,
                )
                include_graph[relative_path].add(include_path.relative_to(template_dir.resolve(strict=True)))
            elif isinstance(node, (nodes.Import, nodes.FromImport, nodes.Extends)):
                construct = type(node).__name__
                _record(values["imports_and_inheritance"], construct, relative_path, node)
                raise _unsupported(relative_path, node, "template import/inheritance", construct)
            elif isinstance(node, nodes.Call):
                if (call_name := _call_name(node)) not in SUPPORTED_CALLS:
                    raise _unsupported(relative_path, node, "call", call_name or type(node.node).__name__)
                if call_name == "namespace":
                    _record(values["namespaces"], "creation", relative_path, node)
        # A mapping value's ``.foo`` would need Jinja's getattr/item fallback.
        # Built-ins may call mapping methods (``.items``/``.get``), but must use
        # bracket syntax for mapping values.
        for node in ast.find_all(nodes.Getattr):
            if isinstance(node.node, nodes.Dict) or (
                isinstance(node.node, nodes.Name)
                and node.node.name in static_mapping_names
                and node.attr not in {"get", "items"}
            ):
                raise _unsupported(relative_path, node, "mapping dot access", node.attr)
            _validate_getattr_root(relative_path, node)
            if isinstance(node.node, nodes.Name) and node.node.name == "v":
                _record(values["mapping_dot_accesses"], node.attr, relative_path, node)
    _check_include_cycles(include_graph)
    _validate_include_macro_captures(templates, include_graph)
    return TemplateInventory(**{
        name: {key: tuple(locations) for key, locations in mapping.items()} for name, mapping in values.items()
    })


def _inventory_selectattr_test(values: dict[str, list[str]], path: Path, node: nodes.Filter) -> None:
    if (
        len(node.args) < _SELECTATTR_TEST_MINIMUM_ARGUMENTS
        or not isinstance(node.args[_SELECTATTR_TEST_ARGUMENT_INDEX], nodes.Const)
        or not isinstance(node.args[_SELECTATTR_TEST_ARGUMENT_INDEX].value, str)
    ):
        raise _unsupported(path, node, "selectattr test", "dynamic or omitted test")
    test_name = node.args[_SELECTATTR_TEST_ARGUMENT_INDEX].value
    _record(values, test_name, path, node)
    if test_name not in SUPPORTED_TESTS:
        raise _unsupported(path, node, "test", test_name)


def _target_form(target: nodes.Expr) -> str:
    if isinstance(target, nodes.Name):
        return "Name"
    if isinstance(target, nodes.Tuple):
        return "Tuple[" + ", ".join(_target_form(item) for item in target.items) + "]"
    return type(target).__name__


def _inventory_macro(values: dict[str, list[str]], path: Path, node: nodes.Macro) -> None:
    signature = ", ".join(argument.name for argument in node.args)
    details = (
        f"{node.name}({signature}); defaults={len(node.defaults)}; "
        f"caller={getattr(node, 'caller', False)}; kwargs={getattr(node, 'catch_kwargs', False)}; "
        f"varargs={getattr(node, 'catch_varargs', False)}"
    )
    _record(values, details, path, node)
    if node.defaults:
        raise _unsupported(path, node, "macro defaults", node.name)
    if getattr(node, "caller", False) or getattr(node, "catch_kwargs", False) or getattr(node, "catch_varargs", False):
        raise _unsupported(path, node, "macro caller/kwargs/varargs", node.name)


def _check_include_cycles(graph: dict[Path, set[Path]]) -> None:
    visiting: list[Path] = []
    visited: set[Path] = set()

    def visit(path: Path) -> None:
        if path in visited:
            return
        if path in visiting:
            cycle = " -> ".join(item.as_posix() for item in [*visiting[visiting.index(path) :], path])
            error_message = (
                f"{path}: include cycle detected ({cycle}); update the standalone template compiler before using it"
            )
            raise ValueError(error_message)
        visiting.append(path)
        for included in graph.get(path, ()):
            visit(included)
        visiting.pop()
        visited.add(path)

    for path in graph:
        visit(path)


def formatted_inventory(inventory: TemplateInventory) -> str:
    """Provide deterministic diagnostic output for the generation command."""
    lines: list[str] = []
    for name in (
        "nodes",
        "filters",
        "tests",
        "globals",
        "loop_attributes",
        "assignments",
        "for_targets",
        "macros",
        "includes",
        "imports_and_inheritance",
        "undefined_usage",
        "namespaces",
        "mapping_dot_accesses",
    ):
        entries: dict[str, tuple[str, ...]] = getattr(inventory, name)
        lines.append(f"{name}:")
        lines.extend(f"  {key}: {', '.join(value)}" for key, value in sorted(entries.items()))
    return "\n".join(lines)
