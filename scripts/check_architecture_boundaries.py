"""Reject cross-layer reads that bypass declared architecture capabilities.

Which module may import which is heki's (the ``import-boundaries`` of scripts/heki.toml, built from the layers of
`_classify_source_path` by scripts/build_heki_contracts.py); this check keeps what an import boundary cannot
name: what a module reads of another one (attributes, ``__module__``, ``sys.modules``, names of the root), the
private names of the parser modules it imports, and backend definitions in reference code.
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable

    from scripts._source_model import Import, Reference, Scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModelError, SourceModule, Unreadable, bases
from scripts._source_model import argument as call_argument

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "datamodel_code_generator"

Layer: TypeAlias = Literal[
    "entrypoint",
    "parser",
    "parser-passes",
    "parser-core",
    "facts",
    "schema-graph",
    "json-pointer",
    "python-syntax",
    "free-names",
    "name-words",
    "naming",
    "graph-links",
    "container-kinds",
    "context-scope",
    "json-schema",
    "warning-control",
    "message-text",
    "module-paths",
    "openapi-paths",
    "config",
    "input-model",
    "reference",
    "model-composition",
    "output-model",
    "shared-model",
    "target",
    "requirements",
    "constraints",
    "regex",
    "runtime",
    "shared",
]

CONCRETE_BACKEND_ROOTS: Final = frozenset({
    "dataclass",
    "field_name",
    "msgspec",
    "pydantic_base",
    "pydantic_v2",
    "typed_dict",
})
PARSER_BACKEND_ATTRIBUTES: Final = frozenset({
    "PLAIN_PATTERN_ROOT_TYPES",
    "SCHEMA_RUNTIME_VALIDATION_HELPERS_TEMPLATE_FILE_PATH",
    "_append_internal_template_data",
    "_internal_template_data",
    "has_typed_dict_closed",
    "has_typed_dict_non_required",
    "is_pydantic_extra_field",
})
PARSER_BACKEND_REQUIREMENT_READERS: Final = frozenset({"_meets", "python_target_meets"})
PARSER_BACKEND_REQUIREMENT_PREFIX: Final = "python.typed_dict_"
REFERENCE_BACKEND_DEFINITION_MARKERS: Final = ("dataclass", "msgspec", "pydantic", "typeddict")
DYNAMIC_ATTRIBUTE_MIN_ARGS: Final = 2
_BACKEND_MODULE_PART_COUNT: Final = 3
_ROOT_PACKAGE: Final = "datamodel_code_generator"
_MAIN_MODULE: Final = "datamodel_code_generator.__main__"
_ROOT_READERS: Final = frozenset({_ROOT_PACKAGE, _MAIN_MODULE})
"""The modules that may read names of the package root: the root itself and the command line above it."""
_ROOT_IMPORT_MESSAGE: Final = (
    "no module of the package imports the package root, and none but __main__ imports __main__: import the name from "
    "the module that defines it (the root only re-exports it for users)"
)
_PRIVATE_PARSER_IMPORT_MESSAGE: Final = "code outside parser must move reusable helpers to a neutral package module"
_UNREADABLE_IMPORT_MESSAGE: Final = (
    "the import leaves the top-level package, so no module name can be read from it: import by an absolute name"
)
_UNJUDGED_DYNAMIC_IMPORT_MESSAGE: Final = (
    "heki judges a dynamic import only where the callee is importlib.import_module or __import__ read through an "
    "import: call importlib.import_module (or __import__) by its own name, not through a star import or another object"
)
_DYNAMIC_IMPORT_NAMES: Final = frozenset({"__import__", "import_module"})
_DYNAMIC_IMPORT_ORIGINS: Final = frozenset({"builtins.__import__", "importlib.__import__", "importlib.import_module"})
_SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE: Final = (
    "shared model code must not inspect a concrete backend through sys.modules; "
    "move backend lifecycle or cache management to the backend or composition root"
)
_TARGET_MODULES: Final = frozenset({
    "datamodel_code_generator._api_generation",
    "datamodel_code_generator._api_types",
    "datamodel_code_generator._client",
    "datamodel_code_generator._fastapi",
    "datamodel_code_generator._target_cli",
    "datamodel_code_generator._target_config",
    "datamodel_code_generator._target_documents",
    "datamodel_code_generator._target_format",
    "datamodel_code_generator._target_operations",
    "datamodel_code_generator._target_render",
    "datamodel_code_generator._target_selection",
    "datamodel_code_generator._target_templates",
})
_PARSER_LAYERS: Final = frozenset({"parser", "parser-passes"})
"""Layers of parser code: the parser modules and the pass modules under ``parser/_passes/``."""
_PARSER_CORE_FILENAMES: Final = frozenset({
    "_intersection.py",
    "_model_shapes.py",
    "_null_facts.py",
    "_schema_dispatch.py",
})
"""Pure parser modules: decisions over dumped schema dicts or frozen schema facts that the parser adapters call."""
_NEUTRAL_MODEL_FILENAMES: Final = frozenset({
    "base.py",
    "enum.py",
    "imports.py",
    "output.py",
    "_field_imports.py",
    "_render_plan.py",
    "_rendered_names.py",
    "runtime_validation.py",
    "scalar.py",
    "type_alias.py",
    "types.py",
    "union.py",
})


@dataclass(frozen=True, order=True)
class BoundaryKey:
    """Stable allowlist identity independent from source line movement."""

    path: str
    qualname: str
    rule: str
    target: str


@dataclass(frozen=True)
class AllowlistEntry:
    """Document a bounded legacy dependency while preventing new copies."""

    reason: str
    count: int = 1

    def __post_init__(self) -> None:
        """Reject entries that cannot explain or bound legacy debt."""
        if not self.reason.strip():
            msg = "architecture boundary allowlist entries require a reason"
            raise ValueError(msg)
        if self.count < 1:
            msg = "architecture boundary allowlist counts must be positive"
            raise ValueError(msg)


@dataclass(frozen=True)
class Violation:
    """One actionable architecture boundary violation."""

    path: str
    line: int
    column: int
    qualname: str
    rule: str
    target: str
    message: str

    @property
    def key(self) -> BoundaryKey:
        """Stable allowlist identity for this violation."""
        return BoundaryKey(self.path, self.qualname, self.rule, self.target)

    def format(self) -> str:
        """Format a compiler-style error with a concrete remediation."""
        return (
            f"{self.path}:{self.line}:{self.column}: [{self.rule}] {self.target}: {self.message} (in {self.qualname})"
        )


DEFAULT_ALLOWLIST: Final[dict[BoundaryKey, AllowlistEntry]] = {
    BoundaryKey(
        "src/datamodel_code_generator/config.py",
        "ParserConfig",
        "config-backend-reference",
        "datamodel_code_generator.model.pydantic_v2.BaseModel",
    ): AllowlistEntry("Preserve the public ParserConfig default model class"),
    BoundaryKey(
        "src/datamodel_code_generator/config.py",
        "ParserConfig",
        "config-backend-reference",
        "datamodel_code_generator.model.pydantic_v2.RootModel",
    ): AllowlistEntry("Preserve the public ParserConfig default root model class"),
    BoundaryKey(
        "src/datamodel_code_generator/config.py",
        "ParserConfig",
        "config-backend-reference",
        "datamodel_code_generator.model.pydantic_v2.DataTypeManager",
    ): AllowlistEntry("Preserve the public ParserConfig default type manager"),
    BoundaryKey(
        "src/datamodel_code_generator/config.py",
        "ParserConfig",
        "config-backend-reference",
        "datamodel_code_generator.model.pydantic_v2.DataModelField",
    ): AllowlistEntry("Preserve the public ParserConfig default field model"),
}


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _module_name(path: Path, layer: Layer) -> str:
    try:
        relative = path.resolve().relative_to(SOURCE_ROOT)
    except ValueError:
        return {
            "entrypoint": "datamodel_code_generator.entrypoint_fixture",
            "parser": "datamodel_code_generator.parser.fixture",
            "parser-passes": "datamodel_code_generator.parser._passes.fixture",
            "parser-core": "datamodel_code_generator.parser.core_fixture",
            "facts": "datamodel_code_generator.parser.facts_fixture",
            "schema-graph": "datamodel_code_generator.parser.schema_graph_fixture",
            "config": "datamodel_code_generator.config_fixture",
            "input-model": "datamodel_code_generator.input_model_fixture",
            "reference": "datamodel_code_generator.reference_fixture",
            "model-composition": "datamodel_code_generator.model.composition_fixture",
            "output-model": "datamodel_code_generator.model.fixture",
            "shared-model": "datamodel_code_generator.model.shared_fixture",
            "target": "datamodel_code_generator.target_fixture",
            "requirements": "datamodel_code_generator.requirements_fixture",
            "constraints": "datamodel_code_generator.constraints_fixture",
            "regex": "datamodel_code_generator.regex_fixture",
            "runtime": "datamodel_code_generator._runtime.fixture",
            "shared": "datamodel_code_generator.shared_fixture",
        }[layer]

    parts = ["datamodel_code_generator", *relative.with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _is_below(module: str, package: str) -> bool:
    return module == package or module.startswith(f"{package}.")


def _concrete_backend_module(target: str) -> str | None:
    parts = target.split(".")
    prefix = ["datamodel_code_generator", "model"]
    if parts[:2] != prefix or len(parts) < _BACKEND_MODULE_PART_COUNT or parts[2] not in CONCRETE_BACKEND_ROOTS:
        return None
    return ".".join((*prefix, parts[2]))


_DATA_MODEL_TYPE_ORIGINS: Final = frozenset({
    "datamodel_code_generator.DataModelType",
    "datamodel_code_generator.enums.DataModelType",
})
_MODEL_TYPE_ORIGIN: Final = "datamodel_code_generator.reference.ModelType"
_PROTOCOL_ORIGINS: Final = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


class _Reading:
    """One module read through the source model: what the names of its expressions spell, read and may hold."""

    def __init__(self, model: SourceModule, package: PackageIndex) -> None:
        """Index the name occurrences that read a name; ``package`` follows the names the package re-exports."""
        self.model = model
        self.package = package
        self.names: dict[ast.AST, Reference] = {
            reference.node: reference for reference in model.references if reference.context == "load"
        }

    def spelled(self, node: ast.AST) -> str | None:
        """Return the dotted text of a name or attribute chain as written, None for any other expression."""
        parts: list[str] = []
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        reference = self.names.get(node)
        return None if reference is None else ".".join([reference.name, *reversed(parts)])

    def last_name(self, node: ast.AST) -> str:
        """Return the last name of a name or attribute chain, the empty string for any other expression."""
        if isinstance(node, ast.Attribute):
            return node.attr
        reference = self.names.get(node)
        return "" if reference is None else reference.name

    def head(self, node: ast.AST) -> Reference | None:
        """Return the name an attribute chain starts at, None where it starts at another expression."""
        while isinstance(node, ast.Attribute):
            node = node.value
        return self.names.get(node)

    def origins(self, node: ast.expr) -> set[str]:
        """Return every dotted path a name or attribute chain may name.

        A path through a name the package re-exports from another module counts as that module's path too.
        """
        found = {path for path, _ in self.model.origins(node)}
        if (single := self.model.origin(node)) is not None:
            found.add(single)
        return found | {self.package.resolve(path) for path in found}

    def strings(self, node: ast.AST | None, seen: frozenset[ast.AST] = frozenset()) -> list[str]:
        """Return the strings an expression may hold: a literal, or the literals of every binding of the name.

        A name holds what every assignment of its scope gives it, whatever the order: the source model does not
        answer which binding reaches a read, and a string that a rule leaves out would be missed silently.
        """
        if isinstance(node, ast.Constant):
            return [node.value] if isinstance(node.value, str) else []
        reference = None if node is None else self.names.get(node)
        if reference is None:
            return []
        found: list[str] = []
        for binding in self.model.resolve(reference.node).bindings:
            if binding.kind in {"assign", "annassign"} and binding.value is not None and binding.node not in seen:
                found += self.strings(binding.value, seen | {binding.node})
        return list(dict.fromkeys(found))

    def matching(self, node: ast.AST | None, accepts: Callable[[str], object]) -> str | None:
        """Return the first string an expression may hold that a predicate accepts."""
        return next((value for value in self.strings(node) if accepts(value)), None)

    def qualname(self, node: ast.AST) -> str:
        """Return the functions and classes around a node, innermost last, as ``Class.method``."""
        return _scope_qualname(self.model.scope_of(node))


def _scope_qualname(scope: Scope | None) -> str:
    """Return the dotted names of the functions and classes a scope is in, itself included."""
    parts: list[str] = []
    while scope is not None:
        if scope.kind in {"function", "class"}:
            parts.append(scope.code_name)
        scope = scope.parent
    return ".".join(reversed(parts)) or "<module>"


class ArchitectureBoundaryReader:
    """Find semantic backend inspection and private parser names for one layer, from the source model's answers."""

    def __init__(self, path: Path, layer: Layer, reading: _Reading) -> None:
        """Create a reader for one file; the model knows its module name, so relative imports read as absolute."""
        self.path = path
        self.display_path = _display_path(path)
        self.layer = layer
        self.reading = reading
        self.model = reading.model
        self.violations: list[Violation] = []

    def run(self) -> list[Violation]:
        """Return the violations of the file: the names it imports, its definitions and what its expressions read."""
        for item in self.model.imports:
            self._imported(item)
        if self.layer == "reference":
            self._definitions()
        for node in ast.walk(self.model.tree):
            if isinstance(node, ast.Call):
                self._call(node)
            elif isinstance(node, ast.Compare):
                self._compare(node)
            elif isinstance(node, ast.Subscript):
                self._subscript(node)
            elif isinstance(node, ast.Attribute):
                self._attribute(node)
        return self.violations

    def _definitions(self) -> None:
        """Reject output-specific definitions in reference code."""
        for scope in self.model.scopes:
            if scope.kind in {"function", "class"} and any(
                marker in scope.code_name.replace("_", "").casefold() for marker in REFERENCE_BACKEND_DEFINITION_MARKERS
            ):
                self._add(
                    scope.node,
                    "reference-backend-definition",
                    scope.code_name,
                    "reference code must not define concrete output-backend policy; move it to the output model",
                    qualname=_scope_qualname(scope),
                )

    def _imported(self, item: Import) -> None:
        """Refuse an import source no module name can be read from, and a private name of a parser module.

        heki's import boundaries judge the module an import loads: a relative import that leaves the top-level package
        names none, and heki's ``private-parser-import`` does not see the name an import takes; both are this check's.
        """
        if isinstance(item.source, Unreadable):
            self._add(item.node, "unreadable-import", str(item.source), _UNREADABLE_IMPORT_MESSAGE)
            return
        name = item.alias.name if item.kind == "from" and item.alias is not None else ""
        if (
            name.startswith("_")
            and self.layer not in _PARSER_LAYERS
            and isinstance(item.source, str)
            and _is_below(item.source, "datamodel_code_generator.parser")
        ):
            self._add(
                item.node,
                "private-parser-import",
                f"{item.source}.{name}",
                _PRIVATE_PARSER_IMPORT_MESSAGE,
            )

    def _call(self, node: ast.Call) -> None:
        """Check semantic getattr, backend module identity helpers and module registry reads."""
        reading = self.reading
        self._check_unjudged_dynamic_import(node)
        if (
            self.layer == "shared-model"
            and node.args
            and self._is_sys_modules_get(node)
            and (target := reading.matching(node.args[0], _concrete_backend_module))
        ):
            self._add(
                node,
                "shared-model-backend-module-access",
                target,
                _SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE,
            )

        callee = reading.last_name(node.func)
        self._check_entrypoint_private_call(node, callee)

        if (
            self.layer in _PARSER_LAYERS
            and callee in PARSER_BACKEND_REQUIREMENT_READERS
            and (
                construct := reading.matching(
                    call_argument(node, 0, "construct"),
                    lambda value: value.startswith(PARSER_BACKEND_REQUIREMENT_PREFIX),
                )
            )
            is not None
        ):
            self._add(
                node,
                "backend-semantic-inspection",
                construct,
                "parser code must query a neutral DataModel or DataModelFieldBase capability",
            )

        if (
            callee in {"getattr", "hasattr"}
            and len(node.args) > 1
            and self.layer in _PARSER_LAYERS
            and (attribute := reading.matching(node.args[1], PARSER_BACKEND_ATTRIBUTES.__contains__)) is not None
        ):
            self._add(
                node,
                "backend-semantic-inspection",
                attribute,
                "parser code must query a neutral DataModel or DataModelFieldBase capability",
            )

        module_keyword = call_argument(node, None, "module")
        if (
            self.layer in _PARSER_LAYERS
            and module_keyword is not None
            and (target := reading.matching(module_keyword, _concrete_backend_module))
        ):
            if name := next(iter(reading.strings(call_argument(node, None, "name"))), None):
                target = f"{target}#{name}"
            self._add(
                node,
                "backend-module-identity",
                target,
                "parser code must not identify an output backend by module/name; declare a neutral capability",
            )

    def _check_unjudged_dynamic_import(self, node: ast.Call) -> None:
        """Refuse a dynamic import call that heki cannot read as ``importlib.import_module`` or ``__import__``.

        heki reads a call only when its name resolves to one of them alone. A name, under any spelling, that may
        resolve to one on some path but not on all (bound to importlib on one branch, or rebound later), a name only a
        star import may bind and, outside shared model code, an attribute of that name on any other object count as
        dynamic imports; heki's import boundaries see none of them, so they stay out of the code unjudged.
        """
        reading, func = self.reading, node.func
        single = reading.model.origin(func)
        if single is not None and {single, reading.package.resolve(single)} & _DYNAMIC_IMPORT_ORIGINS:
            return
        partly = bool(reading.origins(func) & _DYNAMIC_IMPORT_ORIGINS)
        if not partly and reading.last_name(func) not in _DYNAMIC_IMPORT_NAMES:
            return
        head = reading.head(func)
        through_star = head is not None and head.node is func and reading.model.resolve(head.node).kind == "star"
        if partly or through_star or (isinstance(func, ast.Attribute) and self.layer != "shared-model"):
            self._add(
                node,
                "unjudged-dynamic-import",
                reading.spelled(func) or reading.last_name(func),
                _UNJUDGED_DYNAMIC_IMPORT_MESSAGE,
            )

    def _compare(self, node: ast.Compare) -> None:
        """Check direct ``__module__`` comparisons to concrete backends."""
        operands = (node.left, *node.comparators)
        if (
            self.layer in _PARSER_LAYERS
            and any(isinstance(operand, ast.Attribute) and operand.attr == "__module__" for operand in operands)
            and (
                target := next(
                    (
                        value
                        for operand in operands
                        if (value := self.reading.matching(operand, _concrete_backend_module))
                    ),
                    None,
                )
            )
        ):
            self._add(
                node,
                "backend-module-identity",
                target,
                "parser code must not identify an output backend by module/name; declare a neutral capability",
            )

    def _subscript(self, node: ast.Subscript) -> None:
        """Reject concrete backend lookups through the module registry."""
        if (
            self.layer == "shared-model"
            and self._is_sys_modules_registry(node.value)
            and (target := self.reading.matching(node.slice, _concrete_backend_module))
        ):
            self._add(
                node,
                "shared-model-backend-module-access",
                target,
                _SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE,
            )

    def _attribute(self, node: ast.Attribute) -> None:
        """Check backend-specific semantic attributes and output policy references."""
        reading = self.reading
        if (
            node.value in reading.names
            and _ROOT_PACKAGE in reading.origins(node.value)
            and (root_name := self._root_attribute(node.attr)) is not None
        ):
            self._add(node, "root-import", root_name, _ROOT_IMPORT_MESSAGE)
        if self.layer == "entrypoint":
            if node.attr.startswith("_") and self._is_parser_instance(node.value):
                self._add(
                    node,
                    "entrypoint-parser-private-access",
                    node.attr,
                    "entrypoint code must use the Parser public lifecycle and run-context API",
                )
        elif self.layer in _PARSER_LAYERS:
            self._parser_attribute(node)
        elif self.layer == "config":
            if backend := next(iter(sorted(filter(_concrete_backend_module, reading.origins(node)))), None):
                self._add(
                    node,
                    "config-backend-reference",
                    backend,
                    "configuration must receive output implementations through a neutral registry or injection",
                )
        elif self.layer == "input-model":
            head = reading.head(node)
            if (
                head is not None
                and (reading.origins(head.node) | reading.origins(node.value)) & _DATA_MODEL_TYPE_ORIGINS
            ):
                self._add(
                    node,
                    "input-model-output-policy",
                    reading.spelled(node) or "",
                    "input-model conversion must obtain output-family policy from the output selection registry",
                )

    def _parser_attribute(self, node: ast.Attribute) -> None:
        reading = self.reading
        receiver = node.value
        origins = reading.origins(receiver) if reading.head(receiver) is not None else set()
        if node.attr in PARSER_BACKEND_ATTRIBUTES:
            self._add(
                node,
                "backend-semantic-inspection",
                node.attr,
                "parser code must query a neutral DataModel or DataModelFieldBase capability",
            )
        elif origins & _DATA_MODEL_TYPE_ORIGINS or (
            _MODEL_TYPE_ORIGIN in origins and node.attr in {"PYDANTIC", "MSGSPEC"}
        ):
            self._add(
                node,
                "parser-output-policy",
                f"{reading.spelled(receiver)}.{node.attr}",
                "parser code must query output capabilities instead of selecting concrete backend policy",
            )

    def _is_parser_instance(self, node: ast.AST) -> bool:
        """Recognize local Parser instances without following arbitrary object graphs."""
        name = self.reading.last_name(node)
        return name == "parser" or name.endswith(("_parser", "_parser_instance"))

    def _check_entrypoint_private_call(self, node: ast.Call, callee: str) -> None:
        """Reject indirect private access on a Parser instance from the entrypoint."""
        if self.layer != "entrypoint" or callee not in {"getattr", "hasattr", "setattr", "delattr"}:
            return
        if len(node.args) < DYNAMIC_ATTRIBUTE_MIN_ARGS or not self._is_parser_instance(node.args[0]):
            return
        if (attribute := self.reading.matching(node.args[1], lambda value: value.startswith("_"))) is None:
            return
        self._add(
            node,
            "entrypoint-parser-private-access",
            attribute,
            "entrypoint code must use the Parser public lifecycle and run-context API",
        )

    def _is_sys_modules_registry(self, node: ast.expr) -> bool:
        return "sys.modules" in self.reading.origins(node)

    def _is_sys_modules_get(self, node: ast.Call) -> bool:
        return (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and self._is_sys_modules_registry(node.func.value)
        )

    def _entry_module(self, module: str) -> str | None:
        """Return a module this module may not import because it is the package root or ``__main__``, else None."""
        if _is_below(module, _MAIN_MODULE):
            return None if self.model.module == _MAIN_MODULE else module
        return module if module == _ROOT_PACKAGE and self.model.module not in _ROOT_READERS else None

    def _root_attribute(self, name: str) -> str | None:
        """Return ``<root>.<name>`` where reading ``name`` from the package root reads a name of the root, else None.

        ``from <root> import name`` and ``<root>.name`` read the submodule ``<root>.name`` only where the root binds no
        other ``name``; every other name is the root's, also one its ``__getattr__`` serves.
        """
        if name == "__main__":
            return self._entry_module(_MAIN_MODULE)
        if self.model.module in _ROOT_READERS or self.reading.package.reads_submodule(_ROOT_PACKAGE, name):
            return None
        return f"{_ROOT_PACKAGE}.{name}"

    def _add(self, node: ast.AST, rule: str, target: str, message: str, *, qualname: str = "") -> None:
        self.violations.append(
            Violation(
                path=self.display_path,
                line=getattr(node, "lineno", 0),
                column=getattr(node, "col_offset", 0) + 1,
                qualname=qualname or self.reading.qualname(node),
                rule=rule,
                target=target,
                message=message,
            )
        )


class PackageIndex:
    """The models of the package's modules, read once, and the names a module takes from another one."""

    def __init__(self) -> None:
        """Start with no module read."""
        self._models: dict[str, SourceModule | None] = {}
        self._resolved: dict[str, str] = {}
        self.refused: dict[str, str] = {}
        """The files the source model refuses when they are read as dependencies: display path -> its message."""

    def add(self, module: str, model: SourceModule) -> None:
        """Register a module's model, so that its own definitions and imports resolve."""
        self._models[module] = model

    @staticmethod
    def file(module: str) -> Path | None:
        """Return the file of a module of the package, None for any other module."""
        package, *parts = module.split(".")
        relative = SOURCE_ROOT.joinpath(*parts)
        paths = (relative.with_name(f"{relative.name}.py"), relative / "__init__.py")
        return next((path for path in paths if package == _ROOT_PACKAGE and path.is_file()), None)

    def model(self, module: str) -> SourceModule | None:
        """Return the model of a module of the package, read once; None for any other module."""
        if module not in self._models:
            self._models[module] = None
            if (path := self.file(module)) is not None:
                display = _display_path(path.resolve())
                try:
                    self._models[module] = SourceModule(path.read_text(encoding="utf-8"), display, module)
                except SourceModelError as error:
                    self.refused[display] = str(error)
        return self._models[module]

    def reads_submodule(self, package: str, name: str) -> bool:
        """Whether ``from <package> import <name>`` reads the submodule ``<package>.<name>``, not a name of the package.

        As the import system reads it (`SourceTree.definition`): the submodule exists and the package binds no other
        ``<name>``; a binding of the package, guarded or not, makes the name the package's.
        """
        submodule = f"{package}.{name}"
        model = self.model(package)
        bindings = [] if model is None else model.module_scope.bindings.get(name, [])
        return self.file(submodule) is not None and all(binding.origin == submodule for binding in bindings)

    def resolve(self, origin: str) -> str:
        """Follow a dotted path through the names a module of the package imports from another module.

        ``from datamodel_code_generator.format import import_module`` is ``importlib.import_module``, as the module
        ``format`` binds it; a path that leads nowhere in the package stays as it is.
        """
        if origin not in self._resolved:
            self._resolved[origin] = origin
            parts = origin.split(".")
            for count in range(len(parts) - 1, 0, -1):
                if (model := self.model(".".join(parts[:count]))) is None:
                    continue
                bindings = model.module_scope.bindings.get(parts[count], []) if count == len(parts) - 1 else []
                if len(bindings) == 1 and bindings[0].kind == "import-from" and bindings[0].origin:
                    self._resolved[origin] = self.resolve(bindings[0].origin)
                break
        return self._resolved[origin]


def _protocol_bases(model: SourceModule, scope: Scope) -> list[ast.expr]:
    """Return the bases of a class scope that are ``Protocol`` (``Protocol[T]`` too), read by origin."""
    return [base for base in bases(scope) if model.origin(base) in _PROTOCOL_ORIGINS]


def _classify_source_path(path: Path) -> Layer:
    relative = path.resolve().relative_to(SOURCE_ROOT)
    layer: Layer = "shared"
    match relative.parts:
        case ("__init__.py" | "_facade.py" | "_generation.py" | "_option_stages.py",):
            layer = "entrypoint"
        case ("parser", "_facts.py"):
            layer = "facts"
        case ("parser", "_schema_graph.py"):
            layer = "schema-graph"
        case ("parser", "_naming.py"):
            layer = "naming"
        case ("parser", "_passes", *_):
            layer = "parser-passes"
        case ("parser", filename) if filename in _PARSER_CORE_FILENAMES:
            layer = "parser-core"
        case ("parser", *_):
            layer = "parser"
        case ("config.py" | "input_model.py" as filename,):
            layer = "config" if filename == "config.py" else "input-model"
        case ("reference.py",):
            layer = "reference"
        case ("_requirements.py",):
            layer = "requirements"
        case ("_constraint_applicability.py",):
            layer = "constraints"
        case ("_regex_syntax.py",):
            layer = "regex"
        case ("_json_schema.py",):
            layer = "json-schema"
        case ("_json_pointer.py",):
            layer = "json-pointer"
        case ("_python_syntax.py",):
            layer = "python-syntax"
        case ("_free_names.py",):
            layer = "free-names"
        case ("_name_words.py",):
            layer = "name-words"
        case ("_graph_links.py",):
            layer = "graph-links"
        case ("_container_kinds.py",):
            layer = "container-kinds"
        case ("_context_scope.py",):
            layer = "context-scope"
        case ("_warning_control.py",):
            layer = "warning-control"
        case ("_message_text.py",):
            layer = "message-text"
        case ("_module_paths.py",):
            layer = "module-paths"
        case ("_openapi_paths.py",):
            layer = "openapi-paths"
        case ("_runtime", *_):
            layer = "runtime"
        case ("_client" | "_fastapi", *_):
            layer = "target"
        case (filename,) if f"datamodel_code_generator.{filename.removesuffix('.py')}" in _TARGET_MODULES:
            layer = "target"
        case ("model", "__init__.py"):
            layer = "model-composition"
        case ("model", "_decisions", *_):
            layer = "shared-model"
        case ("model", filename) if filename in _NEUTRAL_MODEL_FILENAMES:
            layer = "shared-model"
        case ("model", *_):
            layer = "output-model"
    return layer


def iter_python_files(paths: list[Path]) -> list[Path]:
    """Return stable Python file paths below the requested targets."""
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(path.rglob("*.py"))
        elif path.suffix == ".py":
            files.append(path)
    return sorted({path.resolve() for path in files})


def check_files(
    files: list[tuple[Path, Layer]],
    *,
    allowlist: dict[BoundaryKey, AllowlistEntry] | None = None,
) -> list[Violation]:
    """Check explicitly classified files and apply a bounded, stale-aware allowlist."""
    active_allowlist = DEFAULT_ALLOWLIST if allowlist is None else allowlist
    raw_violations = _raw_violations(files)
    scanned_paths = {_display_path(path) for path, _ in files}

    found_counts = Counter(violation.key for violation in raw_violations)
    allowed_counts = Counter({key: entry.count for key, entry in active_allowlist.items()})
    emitted_counts: Counter[BoundaryKey] = Counter()
    violations: list[Violation] = []
    for violation in sorted(
        raw_violations,
        key=lambda item: (item.path, item.line, item.column, item.rule, item.target),
    ):
        emitted_counts[violation.key] += 1
        if emitted_counts[violation.key] <= allowed_counts[violation.key]:
            continue
        violations.append(violation)

    for key, entry in sorted(active_allowlist.items()):
        if key.path not in scanned_paths or found_counts[key] >= entry.count:
            continue
        violations.append(
            Violation(
                path=key.path,
                line=0,
                column=0,
                qualname=key.qualname,
                rule="stale-allowlist",
                target=f"{key.rule}:{key.target}",
                message=(
                    f"remove or reduce this allowlist entry; expected {entry.count} legacy occurrence(s), "
                    f"found {found_counts[key]}. Reason: {entry.reason}"
                ),
            )
        )
    return sorted(violations, key=lambda item: (item.path, item.line, item.column, item.rule, item.target))


def _unreadable(path: str, error: object) -> Violation:
    """Return the violation of a file the source model refuses: no rule can read it, so none is silently skipped."""
    return Violation(path, 0, 0, "<module>", "unreadable-source", "the source model refuses it", str(error))


def _raw_violations(files: list[tuple[Path, Layer]]) -> list[Violation]:
    """Return every finding of the layer rules on ``files``."""
    index = PackageIndex()
    raw_violations: list[Violation] = []
    reported: set[str] = set()
    for path, layer in dict(files).items():
        module = _module_name(path, layer)
        try:
            model = SourceModule(path.read_text(encoding="utf-8"), _display_path(path), module)
        except SourceModelError as error:
            raw_violations.append(_unreadable(_display_path(path), error))
            reported.add(_display_path(path))
            continue
        index.add(module, model)
        raw_violations.extend(ArchitectureBoundaryReader(path, layer, _Reading(model, index)).run())
    raw_violations.extend(_unreadable(path, message) for path, message in index.refused.items() if path not in reported)
    return raw_violations


def check_paths(
    paths: list[Path],
    *,
    allowlist: dict[BoundaryKey, AllowlistEntry] | None = None,
) -> list[Violation]:
    """Check source-tree paths using their architecture layer classification."""
    files = iter_python_files(paths)
    return check_files([(path, _classify_source_path(path)) for path in files], allowlist=allowlist)


def format_report(violations: list[Violation]) -> str:
    """Return a stable human- and LLM-readable report."""
    if not violations:
        return "No architecture boundary violations.\n"
    return "Architecture boundary violations found:\n" + "".join(f"{violation.format()}\n" for violation in violations)


def main(argv: list[str] | None = None) -> int:
    """Run the architecture boundary guard."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", type=Path, default=[SOURCE_ROOT])
    args = parser.parse_args(argv)

    violations = check_paths(args.paths)
    if violations:
        print(format_report(violations), end="", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
