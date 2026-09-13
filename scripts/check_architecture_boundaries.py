"""Reject cross-layer dependencies that bypass declared architecture capabilities."""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, TypeAlias

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src" / "datamodel_code_generator"

Layer: TypeAlias = Literal[
    "entrypoint",
    "parser",
    "config",
    "input-model",
    "reference",
    "model-composition",
    "output-model",
    "shared-model",
    "shared",
]
SharedModelAliasState: TypeAlias = tuple[set[str], set[str], set[str], set[str], dict[str, str]]

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
REFERENCE_BACKEND_DEFINITION_MARKERS: Final = ("dataclass", "msgspec", "pydantic", "typeddict")
REFERENCE_EXTERNAL_BACKEND_ROOTS: Final = frozenset({"msgspec"})
DYNAMIC_ATTRIBUTE_MIN_ARGS: Final = 2
_BACKEND_MODULE_PART_COUNT: Final = 3
_DYNAMIC_IMPORT_ATTRIBUTE_NAMES: Final = frozenset({"__import__", "import_module"})
_DYNAMIC_IMPORT_PROVIDER_ROOTS: Final = frozenset({"builtins", "importlib"})
_SHARED_MODEL_BACKEND_IMPORT_MESSAGE: Final = (
    "shared model code must not depend on a concrete backend; expose a neutral capability instead"
)
_SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE: Final = (
    "shared model code must not inspect a concrete backend through sys.modules; "
    "move backend lifecycle or cache management to the backend or composition root"
)
_NEUTRAL_MODEL_FILENAMES: Final = frozenset({
    "base.py",
    "enum.py",
    "imports.py",
    "output.py",
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
        "<module>",
        "config-backend-import",
        "datamodel_code_generator.model.pydantic_v2",
    ): AllowlistEntry("ParserConfig keeps its historical concrete default types until a separate public-API migration"),
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
    BoundaryKey(
        "src/datamodel_code_generator/parser/base.py",
        "_is_pydantic_v2_data_model_field",
        "backend-module-identity",
        "datamodel_code_generator.model.pydantic_v2.base_model#DataModelField",
    ): AllowlistEntry("Keep the production-unused private compatibility helper without eager backend imports"),
    BoundaryKey(
        "src/datamodel_code_generator/parser/base.py",
        "__getattr__",
        "backend-import",
        "datamodel_code_generator.model.dataclass",
    ): AllowlistEntry("Keep the lazy legacy parser.base.dataclass_model compatibility export"),
    BoundaryKey(
        "src/datamodel_code_generator/parser/base.py",
        "__getattr__",
        "backend-import",
        "datamodel_code_generator.model.msgspec",
    ): AllowlistEntry("Keep the lazy legacy parser.base.msgspec_model compatibility export"),
    BoundaryKey(
        "src/datamodel_code_generator/parser/jsonschema.py",
        "__getattr__",
        "backend-import",
        "datamodel_code_generator.model.typed_dict",
    ): AllowlistEntry("Keep the lazy legacy parser.jsonschema.TypedDictModel compatibility export"),
    BoundaryKey(
        "src/datamodel_code_generator/reference.py",
        "_default_field_name_resolver_class",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.PydanticFieldNameResolver",
    ): AllowlistEntry("Keep the lazy legacy default Pydantic field-name resolver lookup"),
    BoundaryKey(
        "src/datamodel_code_generator/reference.py",
        "_default_field_name_resolver_class",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.MsgspecFieldNameResolver",
    ): AllowlistEntry("Keep the lazy legacy default msgspec field-name resolver lookup"),
    BoundaryKey(
        "src/datamodel_code_generator/reference.py",
        "__getattr__",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.PydanticFieldNameResolver",
    ): AllowlistEntry("Keep the lazy legacy reference.PydanticFieldNameResolver compatibility export"),
    BoundaryKey(
        "src/datamodel_code_generator/reference.py",
        "__getattr__",
        "reference-backend-import",
        "datamodel_code_generator.model.field_name.MsgspecFieldNameResolver",
    ): AllowlistEntry("Keep the lazy legacy reference.MsgspecFieldNameResolver compatibility export"),
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
            "config": "datamodel_code_generator.config_fixture",
            "input-model": "datamodel_code_generator.input_model_fixture",
            "reference": "datamodel_code_generator.reference_fixture",
            "model-composition": "datamodel_code_generator.model.composition_fixture",
            "output-model": "datamodel_code_generator.model.fixture",
            "shared-model": "datamodel_code_generator.model.shared_fixture",
            "shared": "datamodel_code_generator.shared_fixture",
        }[layer]

    parts = ["datamodel_code_generator", *relative.with_suffix("").parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _normalize_import(module: str | None, level: int, current_module: str, path: Path) -> str:
    if level == 0:
        return module or ""

    module_parts = current_module.split(".")
    package_parts = module_parts if path.name == "__init__.py" else module_parts[:-1]
    keep = max(0, len(package_parts) - level + 1)
    return ".".join((*package_parts[:keep], *((module or "").split(".") if module else ())))


def _concrete_backend_module(target: str) -> str | None:
    parts = target.split(".")
    prefix = ["datamodel_code_generator", "model"]
    if parts[:2] != prefix or len(parts) < _BACKEND_MODULE_PART_COUNT or parts[2] not in CONCRETE_BACKEND_ROOTS:
        return None
    return ".".join((*prefix, parts[2]))


def _is_reference_backend_import(target: str) -> bool:
    """Return whether a reference import owns output-framework policy."""
    return bool(_concrete_backend_module(target) or target.partition(".")[0] in REFERENCE_EXTERNAL_BACKEND_ROOTS)


def _attribute_chain(node: ast.AST) -> tuple[str, ...]:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        parts.append(current.id)
    parts.reverse()
    return tuple(parts)


class ArchitectureBoundaryVisitor(ast.NodeVisitor):
    """Find forbidden imports and semantic backend inspection for one layer."""

    def __init__(self, path: Path, layer: Layer, tree: ast.Module) -> None:
        """Create a visitor and collect conservative module string constants."""
        self.path = path
        self.display_path = _display_path(path)
        self.layer = layer
        self.current_module = _module_name(path, layer)
        self.qualnames: list[str] = []
        self.violations: list[Violation] = []
        self.backend_aliases: dict[str, str] = {}
        self.package_root_aliases: set[str] = set()
        self.dynamic_import_aliases = set(_DYNAMIC_IMPORT_ATTRIBUTE_NAMES)
        self.dynamic_import_provider_aliases: set[str] = set()
        (
            self.module_dynamic_import_provider_aliases,
            self.module_dynamic_import_provider_alias_names,
        ) = self._collect_dynamic_import_provider_aliases(tree) if layer == "shared-model" else (set(), set())
        self.sys_aliases: set[str] = set()
        self.sys_modules_aliases: set[str] = set()
        self.shared_model_scope_contexts: list[tuple[bool, SharedModelAliasState]] = []
        self.data_model_type_aliases = {"DataModelType"}
        self.field_name_model_type_aliases: set[str] = set()
        self.module_string_constants, self.module_string_constant_names = self._collect_string_constants(
            tree,
            clear_reassigned=layer == "shared-model",
        )
        self.string_constants = {} if layer == "shared-model" else self.module_string_constants.copy()

    @staticmethod
    def _collect_string_constants(
        tree: ast.Module,
        *,
        clear_reassigned: bool,
    ) -> tuple[dict[str, str], set[str]]:
        constants: dict[str, str] = {}
        names: set[str] = set()
        for node in tree.body:
            match node:
                case (
                    ast.Assign(targets=[ast.Name(id=name)], value=value)
                    | ast.AnnAssign(target=ast.Name(id=name), value=value)
                ):
                    names.add(name)
                    match value:
                        case ast.Constant(value=str() as string_value):
                            constants[name] = string_value
                        case ast.Name(id=alias) if alias in constants:
                            constants[name] = constants[alias]
                        case _ if clear_reassigned:
                            constants.pop(name, None)
                        case _:
                            pass
        return constants, names

    @staticmethod
    def _import_alias_name(alias: ast.alias) -> str:
        return alias.asname or alias.name.partition(".")[0]

    @staticmethod
    def _is_dynamic_import_provider_alias(alias: ast.alias) -> bool:
        return alias.name in _DYNAMIC_IMPORT_PROVIDER_ROOTS or (
            alias.asname is None and alias.name.partition(".")[0] in _DYNAMIC_IMPORT_PROVIDER_ROOTS
        )

    @classmethod
    def _collect_dynamic_import_provider_aliases(cls, tree: ast.Module) -> tuple[set[str], set[str]]:
        aliases: set[str] = set()
        names: set[str] = set()
        for node in tree.body:
            match node:
                case ast.Import(names=imports):
                    for imported in imports:
                        alias_name = cls._import_alias_name(imported)
                        names.add(alias_name)
                        aliases.discard(alias_name)
                        if cls._is_dynamic_import_provider_alias(imported):
                            aliases.add(alias_name)
                case ast.ImportFrom(names=imports):
                    imported_names = {imported.asname or imported.name for imported in imports}
                    names.update(imported_names)
                    aliases.difference_update(imported_names)
                case ast.Assign(targets=targets, value=ast.Name(id=alias)):
                    assigned_names = {target.id for target in targets if isinstance(target, ast.Name)}
                    is_provider_alias = alias in aliases
                    names.update(assigned_names)
                    aliases.difference_update(assigned_names)
                    if is_provider_alias:
                        aliases.update(assigned_names)
                case ast.Assign(targets=targets):
                    assigned_names = {target.id for target in targets if isinstance(target, ast.Name)}
                    names.update(assigned_names)
                    aliases.difference_update(assigned_names)
                case ast.AnnAssign(target=ast.Name(id=name), value=ast.Name(id=alias)):
                    is_provider_alias = alias in aliases
                    names.add(name)
                    aliases.discard(name)
                    if is_provider_alias:
                        aliases.add(name)
                case ast.AnnAssign(target=ast.Name(id=name)):
                    names.add(name)
                    aliases.discard(name)
        return aliases, names

    @property
    def qualname(self) -> str:
        """Current enclosing symbol."""
        return ".".join(self.qualnames) or "<module>"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        """Track class context for stable allowlist entries."""
        self._visit_named_scope(node, class_scope=True)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        """Track function context for stable allowlist entries."""
        self._visit_named_scope(node, class_scope=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        """Track async function context for stable allowlist entries."""
        self._visit_named_scope(node, class_scope=False)

    def _visit_named_scope(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
        *,
        class_scope: bool,
    ) -> None:
        """Track one scope and reject output-specific definitions in reference code."""
        aliases = self._snapshot_shared_model_aliases()
        self.qualnames.append(node.name)
        scoped_aliases = False
        try:
            self._visit_outer_scope_expressions(node)
            if aliases is not None and self.shared_model_scope_contexts and self.shared_model_scope_contexts[-1][0]:
                class_aliases = self.shared_model_scope_contexts[-1][1]
                self._restore_shared_model_aliases(self._copy_shared_model_aliases(class_aliases))
            if (
                aliases is not None
                and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and all(class_scope for class_scope, _ in self.shared_model_scope_contexts)
            ):
                self.string_constants = {
                    name: value
                    for name, value in self.string_constants.items()
                    if name not in self.module_string_constant_names
                }
                self.string_constants.update(self.module_string_constants)
                self.dynamic_import_provider_aliases.difference_update(self.module_dynamic_import_provider_alias_names)
                self.dynamic_import_provider_aliases.update(self.module_dynamic_import_provider_aliases)
            if aliases is not None:
                self.shared_model_scope_contexts.append((class_scope, self._copy_current_shared_model_aliases()))
                scoped_aliases = True
            if aliases is not None:
                self._discard_function_parameter_aliases(node)
            if self.layer == "reference" and any(
                marker in node.name.replace("_", "").casefold() for marker in REFERENCE_BACKEND_DEFINITION_MARKERS
            ):
                self._add(
                    node,
                    "reference-backend-definition",
                    node.name,
                    "reference code must not define concrete output-backend policy; move it to the output model",
                )
            for statement in node.body:
                self.visit(statement)
        finally:
            if scoped_aliases:
                self.shared_model_scope_contexts.pop()
            self.qualnames.pop()
            self._restore_shared_model_aliases(aliases)

    def _visit_outer_scope_expressions(
        self,
        node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        for name, value in ast.iter_fields(node):
            if name in {"body", "decorator_list"}:
                continue
            if isinstance(value, ast.AST):
                self.visit(value)
            elif isinstance(value, list):
                for item in value:
                    self.visit(item)

    def visit_Import(self, node: ast.Import) -> None:
        """Check direct absolute backend and private-parser imports."""
        for alias in node.names:
            if alias.name == "datamodel_code_generator":
                self.package_root_aliases.add(alias.asname or alias.name)
            alias_name = self._import_alias_name(alias)
            if self.layer == "shared-model":
                self._discard_shared_model_aliases({alias_name})
                if self._is_dynamic_import_provider_alias(alias):
                    self.dynamic_import_provider_aliases.add(alias_name)
                if alias.name == "sys":
                    self.sys_aliases.add(alias_name)
            self._record_import_alias(alias.asname or alias.name.partition(".")[0], alias.name)
            self._check_import(node, alias.name)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """Check absolute, relative, and package-root import forms."""
        module = _normalize_import(node.module, node.level, self.current_module, self.path)
        for alias in node.names:
            target = f"{module}.{alias.name}" if module else alias.name
            alias_name = alias.asname or alias.name
            if self.layer == "shared-model":
                self._discard_shared_model_aliases({alias_name})
                if module == "sys" and alias.name == "modules":
                    self.sys_modules_aliases.add(alias_name)
            if module in {"builtins", "importlib"} and alias.name in _DYNAMIC_IMPORT_ATTRIBUTE_NAMES:
                self.dynamic_import_aliases.add(alias_name)
            if self.layer == "parser" and module == "datamodel_code_generator" and alias.name.startswith("_"):
                self._add(
                    node,
                    "parser-root-private-import",
                    target,
                    "parser code must import private helpers from their neutral module, not the package facade",
                )
            if (
                module in {"datamodel_code_generator", "datamodel_code_generator.enums"}
                and alias.name == "DataModelType"
            ):
                self.data_model_type_aliases.add(alias.asname or alias.name)
            if module == "datamodel_code_generator.reference" and alias.name == "ModelType":
                self.field_name_model_type_aliases.add(alias_name)
            if module == "datamodel_code_generator.model" and alias.name in CONCRETE_BACKEND_ROOTS:
                self._record_import_alias(alias.asname or alias.name, target)
                self._check_import(node, target)
                continue
            self._record_import_alias(alias.asname or alias.name, module)
            self._check_import(node, target if self.layer == "reference" or alias.name.startswith("_") else module)

    def visit_Call(self, node: ast.Call) -> None:
        """Check dynamic imports, semantic getattr, and backend module identity helpers."""
        chain = _attribute_chain(node.func)
        dynamic_target = None
        if (
            self.layer in {"parser", "config", "reference", "shared-model"}
            and self._is_dynamic_import(node.func)
            and (argument := self._dynamic_import_target_argument(node)) is not None
        ):
            dynamic_target = self._resolved_string(argument)
        if dynamic_target and (
            _is_reference_backend_import(dynamic_target)
            if self.layer == "reference"
            else _concrete_backend_module(dynamic_target)
        ):
            match self.layer:
                case "reference":
                    self._add(
                        node,
                        "reference-backend-import",
                        dynamic_target,
                        "reference code must not import concrete output backends except bounded compatibility exports",
                    )
                case "shared-model":
                    self._add(
                        node,
                        "shared-model-backend-import",
                        dynamic_target,
                        _SHARED_MODEL_BACKEND_IMPORT_MESSAGE,
                    )
                case _:
                    self._add(
                        node,
                        "backend-dynamic-import",
                        dynamic_target,
                        "parser/config layers must not dynamically import output backends; "
                        "declare a neutral capability",
                    )

        if (
            self.layer == "shared-model"
            and self._is_sys_modules_get(node)
            and (target := self._resolved_string(node.args[0]))
            and _concrete_backend_module(target)
        ):
            self._add(
                node,
                "shared-model-backend-module-access",
                target,
                _SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE,
            )

        self._check_entrypoint_private_call(node, chain)

        if (
            chain
            and chain[-1] in {"getattr", "hasattr"}
            and len(node.args) > 1
            and (
                self.layer == "parser"
                and (attribute := self._resolved_string(node.args[1])) in PARSER_BACKEND_ATTRIBUTES
            )
        ):
            self._add(
                node,
                "backend-semantic-inspection",
                attribute,
                "parser code must query a neutral DataModel or DataModelFieldBase capability",
            )

        module_keyword = next((keyword.value for keyword in node.keywords if keyword.arg == "module"), None)
        if (
            self.layer == "parser"
            and module_keyword is not None
            and (target := self._resolved_string(module_keyword))
            and _concrete_backend_module(target)
        ):
            name_keyword = next((keyword.value for keyword in node.keywords if keyword.arg == "name"), None)
            if name := self._resolved_string(name_keyword):
                target = f"{target}#{name}"
            self._add(
                node,
                "backend-module-identity",
                target,
                "parser code must not identify an output backend by module/name; declare a neutral capability",
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        """Check direct ``__module__`` comparisons to concrete backends."""
        operands = (node.left, *node.comparators)
        if (
            self.layer == "parser"
            and any(isinstance(operand, ast.Attribute) and operand.attr == "__module__" for operand in operands)
            and (
                target := next(
                    (
                        value
                        for operand in operands
                        if (value := self._resolved_string(operand)) and _concrete_backend_module(value)
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
        self.generic_visit(node)

    def visit_Assign(self, node: ast.Assign) -> None:
        """Track shared-model aliases for the module registry and concrete module strings."""
        self.visit(node.value)
        for target in node.targets:
            self.visit(target)
        if self.layer == "shared-model":
            self._record_shared_model_aliases(node.targets, node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        """Track annotated shared-model aliases for the module registry and concrete module strings."""
        self.visit(node.annotation)
        if node.value is not None:
            self.visit(node.value)
        self.visit(node.target)
        if self.layer == "shared-model":
            self._record_shared_model_aliases((node.target,), node.value)

    def visit_If(self, node: ast.If) -> None:
        """Retain shared-model aliases that remain possible after either branch."""
        if self.layer != "shared-model":
            self.generic_visit(node)
            return
        self.visit(node.test)
        initial = self._copy_current_shared_model_aliases()
        for statement in node.body:
            self.visit(statement)
        body_aliases = self._copy_current_shared_model_aliases()
        self._restore_shared_model_aliases(self._copy_shared_model_aliases(initial))
        for statement in node.orelse:
            self.visit(statement)
        orelse_aliases = self._copy_current_shared_model_aliases()
        self._restore_shared_model_aliases(self._merge_shared_model_aliases(body_aliases, orelse_aliases))

    def visit_Subscript(self, node: ast.Subscript) -> None:
        """Reject concrete backend lookups through the module registry."""
        if (
            self.layer == "shared-model"
            and self._is_sys_modules_registry(node.value)
            and (target := self._resolved_string(node.slice))
            and _concrete_backend_module(target)
        ):
            self._add(
                node,
                "shared-model-backend-module-access",
                target,
                _SHARED_MODEL_BACKEND_MODULE_ACCESS_MESSAGE,
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        """Check backend-specific semantic attributes and output policy references."""
        if self.layer == "entrypoint" and node.attr.startswith("_") and self._is_parser_instance(node.value):
            self._add(
                node,
                "entrypoint-parser-private-access",
                node.attr,
                "entrypoint code must use the Parser public lifecycle and run-context API",
            )
        elif self.layer == "parser":
            match node.value:
                case ast.Name(id=root_alias) if root_alias in self.package_root_aliases and node.attr.startswith("_"):
                    self._add(
                        node,
                        "parser-root-private-import",
                        f"datamodel_code_generator.{node.attr}",
                        "parser code must import private helpers from their neutral module, not the package facade",
                    )
                case _ if node.attr in PARSER_BACKEND_ATTRIBUTES:
                    self._add(
                        node,
                        "backend-semantic-inspection",
                        node.attr,
                        "parser code must query a neutral DataModel or DataModelFieldBase capability",
                    )
                case ast.Name(id=type_alias) if type_alias in self.data_model_type_aliases or (
                    type_alias in self.field_name_model_type_aliases and node.attr in {"PYDANTIC", "MSGSPEC"}
                ):
                    self._add(
                        node,
                        "parser-output-policy",
                        f"{type_alias}.{node.attr}",
                        "parser code must query output capabilities instead of selecting concrete backend policy",
                    )
                case _:
                    pass
        elif self.layer == "config":
            chain = _attribute_chain(node)
            if chain and (backend := self.backend_aliases.get(chain[0])):
                target = ".".join((backend, *chain[1:]))
                self._add(
                    node,
                    "config-backend-reference",
                    target,
                    "configuration must receive output implementations through a neutral registry or injection",
                )
        elif self.layer == "input-model":
            chain = _attribute_chain(node)
            if len(chain) > 1 and chain[0] in self.data_model_type_aliases:
                self._add(
                    node,
                    "input-model-output-policy",
                    ".".join(chain),
                    "input-model conversion must obtain output-family policy from the output selection registry",
                )
        self.generic_visit(node)

    @staticmethod
    def _is_parser_instance(node: ast.AST) -> bool:
        """Recognize local Parser instances without following arbitrary object graphs."""
        match node:
            case ast.Name(id=name) | ast.Attribute(attr=name):
                return name == "parser" or name.endswith(("_parser", "_parser_instance"))
        return False

    def _check_entrypoint_private_call(self, node: ast.Call, chain: tuple[str, ...]) -> None:
        """Reject indirect private access on a Parser instance from the entrypoint."""
        if self.layer != "entrypoint" or chain[-1:] not in {
            ("getattr",),
            ("hasattr",),
            ("setattr",),
            ("delattr",),
        }:
            return
        if len(node.args) < DYNAMIC_ATTRIBUTE_MIN_ARGS or not self._is_parser_instance(node.args[0]):
            return
        if (attribute := self._resolved_string(node.args[1])) is None or not attribute.startswith("_"):
            return
        self._add(
            node,
            "entrypoint-parser-private-access",
            attribute,
            "entrypoint code must use the Parser public lifecycle and run-context API",
        )

    def _is_dynamic_import(self, node: ast.AST) -> bool:
        match node:
            case ast.Name(id=name):
                return name in self.dynamic_import_aliases
            case ast.Attribute(attr=attribute):
                if attribute not in _DYNAMIC_IMPORT_ATTRIBUTE_NAMES:
                    return False
                if self.layer != "shared-model":
                    return True
                chain = _attribute_chain(node)
                return len(chain) > 1 and chain[0] in self.dynamic_import_provider_aliases
        return False

    @staticmethod
    def _dynamic_import_target_argument(node: ast.Call) -> ast.expr | None:
        if node.args:
            return node.args[0]
        return next((keyword.value for keyword in node.keywords if keyword.arg == "name"), None)

    def _is_sys_modules_registry(self, node: ast.AST) -> bool:
        match node:
            case ast.Name(id=name):
                return name in self.sys_modules_aliases
            case ast.Attribute(value=ast.Name(id=name), attr="modules"):
                return name in self.sys_aliases
        return False

    def _is_sys_modules_get(self, node: ast.Call) -> bool:
        match node.func:
            case ast.Attribute(value=value, attr="get"):
                return bool(node.args) and self._is_sys_modules_registry(value)
        return False

    def _snapshot_shared_model_aliases(self) -> SharedModelAliasState | None:
        if self.layer != "shared-model":
            return None
        return self._copy_current_shared_model_aliases()

    def _copy_current_shared_model_aliases(self) -> SharedModelAliasState:
        return (
            self.dynamic_import_aliases.copy(),
            self.dynamic_import_provider_aliases.copy(),
            self.sys_aliases.copy(),
            self.sys_modules_aliases.copy(),
            self.string_constants.copy(),
        )

    @staticmethod
    def _copy_shared_model_aliases(aliases: SharedModelAliasState) -> SharedModelAliasState:
        return (
            aliases[0].copy(),
            aliases[1].copy(),
            aliases[2].copy(),
            aliases[3].copy(),
            aliases[4].copy(),
        )

    @staticmethod
    def _merge_shared_model_aliases(
        body_aliases: SharedModelAliasState,
        orelse_aliases: SharedModelAliasState,
    ) -> SharedModelAliasState:
        string_constants: dict[str, str] = {}
        for name in body_aliases[4].keys() | orelse_aliases[4].keys():
            body_value = body_aliases[4].get(name)
            orelse_value = orelse_aliases[4].get(name)
            if body_value == orelse_value:
                string_constants[name] = body_aliases[4][name]
            elif concrete_value := next(
                (
                    value
                    for value in (body_value, orelse_value)
                    if value is not None and _concrete_backend_module(value)
                ),
                None,
            ):
                string_constants[name] = concrete_value
        return (
            body_aliases[0] | orelse_aliases[0],
            body_aliases[1] | orelse_aliases[1],
            body_aliases[2] | orelse_aliases[2],
            body_aliases[3] | orelse_aliases[3],
            string_constants,
        )

    def _restore_shared_model_aliases(
        self,
        aliases: SharedModelAliasState | None,
    ) -> None:
        if aliases is None:
            return
        (
            self.dynamic_import_aliases,
            self.dynamic_import_provider_aliases,
            self.sys_aliases,
            self.sys_modules_aliases,
            self.string_constants,
        ) = aliases

    def _discard_function_parameter_aliases(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        match node:
            case ast.FunctionDef(args=args) | ast.AsyncFunctionDef(args=args):
                arguments = (*args.posonlyargs, *args.args, *args.kwonlyargs)
                names = {argument.arg for argument in arguments}
                if args.vararg is not None:
                    names.add(args.vararg.arg)
                if args.kwarg is not None:
                    names.add(args.kwarg.arg)
                self._discard_shared_model_aliases(names)
            case _:
                pass

    def _discard_shared_model_aliases(self, names: set[str]) -> None:
        self.dynamic_import_aliases.difference_update(names)
        self.dynamic_import_provider_aliases.difference_update(names)
        self.sys_aliases.difference_update(names)
        self.sys_modules_aliases.difference_update(names)
        for name in names:
            self.string_constants.pop(name, None)

    def _record_shared_model_aliases(
        self,
        targets: list[ast.expr] | tuple[ast.expr, ...],
        value: ast.expr | None,
    ) -> None:
        names = {target.id for target in targets if isinstance(target, ast.Name)}
        if not names:
            return
        string_value = self._resolved_string(value)
        is_registry = value is not None and self._is_sys_modules_registry(value)
        is_sys = isinstance(value, ast.Name) and value.id in self.sys_aliases
        is_dynamic_import = value is not None and self._is_dynamic_import(value)
        is_dynamic_import_provider = isinstance(value, ast.Name) and value.id in self.dynamic_import_provider_aliases
        self._discard_shared_model_aliases(names)
        if string_value is not None:
            self.string_constants.update(dict.fromkeys(names, string_value))
        if is_sys:
            self.sys_aliases.update(names)
        if is_registry:
            self.sys_modules_aliases.update(names)
        if is_dynamic_import:
            self.dynamic_import_aliases.update(names)
        if is_dynamic_import_provider:
            self.dynamic_import_provider_aliases.update(names)

    def _record_import_alias(self, alias: str, target: str) -> None:
        if backend := _concrete_backend_module(target):
            self.backend_aliases[alias] = backend

    def _check_import(self, node: ast.AST, target: str) -> None:
        if self.layer == "reference" and _is_reference_backend_import(target):
            self._add(
                node,
                "reference-backend-import",
                target,
                "reference code must not import concrete output backends except bounded compatibility exports",
            )
            return
        if self.layer == "parser" and _concrete_backend_module(target):
            self._add(
                node,
                "backend-import",
                target,
                "parser code must depend on neutral model capabilities, not a concrete output backend",
            )
            return
        if self.layer == "config" and _concrete_backend_module(target):
            self._add(
                node,
                "config-backend-import",
                target,
                "configuration must receive output implementations through a neutral registry or injection",
            )
            return
        if self.layer == "shared-model" and _concrete_backend_module(target):
            self._add(
                node,
                "shared-model-backend-import",
                target,
                _SHARED_MODEL_BACKEND_IMPORT_MESSAGE,
            )
            return
        if (
            self.layer != "parser"
            and target.startswith("datamodel_code_generator.parser.")
            and any(part.startswith("_") for part in target.split(".")[2:])
        ):
            self._add(
                node,
                "private-parser-import",
                target,
                "code outside parser must move reusable helpers to a neutral package module",
            )
        elif self.layer == "config" and (
            target == "datamodel_code_generator.parser" or target.startswith("datamodel_code_generator.parser.")
        ):
            self._add(
                node,
                "config-parser-import",
                target,
                "configuration must not depend on parser implementation modules",
            )

    def _resolved_string(self, node: ast.AST | None) -> str | None:
        match node:
            case ast.Constant(value=str() as value):
                return value
            case ast.Name(id=name):
                return self.string_constants.get(name)
        return None

    def _add(self, node: ast.AST, rule: str, target: str, message: str) -> None:
        self.violations.append(
            Violation(
                path=self.display_path,
                line=getattr(node, "lineno", 0),
                column=getattr(node, "col_offset", 0) + 1,
                qualname=self.qualname,
                rule=rule,
                target=target,
                message=message,
            )
        )


def _classify_source_path(path: Path) -> Layer:
    relative = path.resolve().relative_to(SOURCE_ROOT)
    layer: Layer = "shared"
    match relative.parts:
        case ("__init__.py",):
            layer = "entrypoint"
        case ("parser", *_):
            layer = "parser"
        case ("config.py" | "input_model.py" as filename,):
            layer = "config" if filename == "config.py" else "input-model"
        case ("reference.py",):
            layer = "reference"
        case ("model", "__init__.py"):
            layer = "model-composition"
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
    raw_violations: list[Violation] = []
    scanned_paths: set[str] = set()
    for path, layer in files:
        display_path = _display_path(path)
        scanned_paths.add(display_path)
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = ArchitectureBoundaryVisitor(path, layer, tree)
        visitor.visit(tree)
        raw_violations.extend(visitor.violations)

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
