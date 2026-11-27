"""The architecture layers of the package's modules, which the heki contracts of scripts/heki.toml are built from.

`classify_source_path` gives each module of ``src/datamodel_code_generator`` its layer; scripts/build_heki_contracts.py
writes the import boundaries and the architecture contracts of the layers from it, and reads the package's modules
once through `PackageIndex`.
"""

from __future__ import annotations

import contextlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    import ast

    from scripts._source_model import Scope

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts._source_model import SourceModelError, SourceModule, bases

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
"""The modules below ``model`` that hold one output backend: a module of a backend is one of them or below one."""
ROOT_PACKAGE: Final = "datamodel_code_generator"
ROOT_IMPORT_MESSAGE: Final = (
    "no module of the package imports the package root, and none but __main__ imports __main__: import the name from "
    "the module that defines it (the root only re-exports it for users)"
)
PRIVATE_PARSER_IMPORT_MESSAGE: Final = "code outside parser must move reusable helpers to a neutral package module"
TARGET_MODULES: Final = frozenset({
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
PARSER_LAYERS: Final = frozenset({"parser", "parser-passes"})
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
_PROTOCOL_ORIGINS: Final = frozenset({"typing.Protocol", "typing_extensions.Protocol"})


class PackageIndex:
    """The models of the package's modules, read once, and the names a module takes from another one."""

    def __init__(self, source_root: Path = SOURCE_ROOT) -> None:
        """Start with no module read, in the package directory ``source_root``."""
        self._source_root = source_root.resolve()
        self._models: dict[str, SourceModule | None] = {}
        self._resolved: dict[str, str] = {}

    def file(self, module: str) -> Path | None:
        """Return the file of a module of the package, None for any other module."""
        package, *parts = module.split(".")
        relative = self._source_root.joinpath(*parts)
        paths = (relative.with_name(f"{relative.name}.py"), relative / "__init__.py")
        return next((path for path in paths if package == ROOT_PACKAGE and path.is_file()), None)

    def model(self, module: str) -> SourceModule | None:
        """Return the model of a module of the package, read once; None for any other module or one it refuses."""
        if module not in self._models:
            self._models[module] = None
            if (path := self.file(module)) is not None:
                display = path.resolve().relative_to(self._source_root.parents[1]).as_posix()
                with contextlib.suppress(SourceModelError):
                    self._models[module] = SourceModule(path.read_text(encoding="utf-8"), display, module)
        return self._models[module]

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


def protocol_bases(model: SourceModule, scope: Scope) -> list[ast.expr]:
    """Return the bases of a class scope that are ``Protocol`` (``Protocol[T]`` too), read by origin."""
    return [base for base in bases(scope) if model.origin(base) in _PROTOCOL_ORIGINS]


def classify_source_path(path: Path) -> Layer:
    """Return the layer of a module of the package from its path."""
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
        case (filename,) if f"datamodel_code_generator.{filename.removesuffix('.py')}" in TARGET_MODULES:
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
