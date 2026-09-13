"""Input model loading and schema transformation for --input-model option."""

from __future__ import annotations

import importlib.util
import sys
import types
from collections import ChainMap, Counter, OrderedDict, defaultdict, deque
from collections.abc import (
    Mapping as ABCMapping,
)
from collections.abc import (
    MutableMapping as ABCMutableMapping,
)
from collections.abc import (
    MutableSequence as ABCMutableSequence,
)
from collections.abc import (
    MutableSet as ABCMutableSet,
)
from collections.abc import (
    Sequence as ABCSequence,
)
from collections.abc import (
    Set as AbstractSet,
)
from copy import deepcopy
from dataclasses import is_dataclass
from enum import Enum as PyEnum
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from typing_extensions import Self

from datamodel_code_generator._input_model_transport import PythonTypeExpressionCollector
from datamodel_code_generator._process_state import PROCESS_STATE_LOCK
from datamodel_code_generator._python_type_annotation import (
    PythonTypeExpr,
    PythonTypeName,
    PythonTypeOpaqueText,
    PythonTypeQualifiedName,
    PythonTypeRuntimeSymbol,
    PythonTypeSubscript,
    PythonTypeUnion,
    render_python_type_expr,
    rewrite_python_type_expr,
)
from datamodel_code_generator.enums import InputModelRefStrategy, _get_output_model_family

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

    from pydantic.json_schema import GenerateJsonSchema
    from typing_extensions import Sentinel as sentinel  # noqa: N813  # ty uses the pre-4.16 name

    from datamodel_code_generator import DataModelType, InputFileType
    from datamodel_code_generator.enums import _OutputModelFamily
    from datamodel_code_generator.input_model_result import LoadedInputModelSchema
else:
    from typing_extensions import sentinel


class Error(Exception):
    """Error raised during input model loading."""


_MISSING_MODULE = object()
_ModuleRestoreState = tuple[str, object]
_ParentSchema = tuple[dict[str, object], dict[str, object], frozenset[str]]


def _path_is_within(path: str | Path, directory: Path) -> bool:
    try:
        return Path(path).resolve().is_relative_to(directory)
    except OSError:
        return False


def _module_is_from_directory(
    module: object,
    directory: Path,
    excluded_directory: Path | None = None,
) -> bool:
    if not isinstance(module, types.ModuleType):
        return False

    def is_owned_path(path: str | Path) -> bool:
        return _path_is_within(path, directory) and (
            excluded_directory is None or not _path_is_within(path, excluded_directory)
        )

    if isinstance(module_file := getattr(module, "__file__", None), str):
        return is_owned_path(module_file)
    return any(is_owned_path(path) for path in getattr(module, "__path__", ()))


def _remove_local_module(module_name: str, module: types.ModuleType) -> None:
    if sys.modules.get(module_name) is not module:
        return
    sys.modules.pop(module_name, None)
    parent_name, separator, child_name = module_name.rpartition(".")
    if (
        separator
        and isinstance(parent := sys.modules.get(parent_name), types.ModuleType)
        and vars(parent).get(child_name) is module
    ):
        vars(parent).pop(child_name, None)


def _remove_input_model_path(cwd_entry: str) -> None:
    if (index := next((index for index, entry in enumerate(sys.path) if entry is cwd_entry), None)) is not None:
        sys.path.pop(index)


def _module_depth(module_name: str) -> int:
    return module_name.count(".")


def _load_model_schema_isolated(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_model_type: DataModelType | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, object]:
    """Load a schema while restoring cwd-local import state afterwards."""
    with PROCESS_STATE_LOCK:
        cwd_entry = str(Path.cwd())
        added_path = cwd_entry not in sys.path
        if not added_path:
            return _load_model_schema(
                input_models,
                input_file_type,
                ref_strategy,
                output_model_type,
                expression_collector,
            )

        directory = Path(cwd_entry).resolve()
        environment_directory = Path(sys.prefix).resolve()
        importer_cache_entry = sys.path_importer_cache.get(cwd_entry, _MISSING_MODULE)
        baseline_modules = sys.modules.copy()
        sys.path.insert(0, cwd_entry)
        try:
            return _load_model_schema(
                input_models,
                input_file_type,
                ref_strategy,
                output_model_type,
                expression_collector,
            )
        finally:
            current_modules = sys.modules.copy()
            local_module_names = sorted(
                (
                    module_name
                    for module_name, module in current_modules.items()
                    if module_name not in baseline_modules
                    and _module_is_from_directory(module, directory, environment_directory)
                ),
                key=_module_depth,
                reverse=True,
            )
            for module_name in local_module_names:
                _remove_local_module(module_name, current_modules[module_name])
            _remove_input_model_path(cwd_entry)
            if importer_cache_entry is _MISSING_MODULE:
                sys.path_importer_cache.pop(cwd_entry, None)
            else:
                sys.path_importer_cache[cwd_entry] = cast("Any", importer_cache_entry)


def _restore_path_module(state: _ModuleRestoreState) -> None:
    module_name, previous_module = state
    if previous_module is _MISSING_MODULE:
        sys.modules.pop(module_name, None)
        return
    sys.modules[module_name] = cast("types.ModuleType", previous_module)


def _get_path_module_name(file_path: Path) -> str:
    module_name = file_path.stem
    previous_module = sys.modules.get(module_name)
    if not isinstance(previous_module, types.ModuleType):
        return module_name
    if not (module_file := getattr(previous_module, "__file__", None)):
        return module_name
    try:
        previous_file = Path(module_file).resolve()
    except OSError:
        return module_name
    if previous_file == file_path:
        return module_name

    from hashlib import sha256  # noqa: PLC0415

    digest = sha256(str(file_path).encode()).hexdigest()[:16]
    return f"_datamodel_code_generator_input_model_{digest}"


def _load_module_from_path(file_path: Path, modname: str) -> tuple[types.ModuleType, _ModuleRestoreState]:
    module_name = _get_path_module_name(file_path)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load module from {modname!r}"
        raise Error(msg)

    previous_module = sys.modules.get(module_name, _MISSING_MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        _restore_path_module((module_name, previous_module))
        raise
    return module, (module_name, previous_module)


def _split_input_model(input_model: str) -> tuple[str, str]:
    modname, separator, qualname = input_model.rpartition(":")
    if separator and modname:
        return modname, qualname

    msg = f"Invalid --input-model format: {input_model!r}. Expected 'module:Object' or 'path/to/file.py:Object'."
    raise Error(msg)


def _is_path_input_model_module(modname: str) -> bool:
    return "/" in modname or "\\" in modname or (modname.endswith(".py") and Path(modname).exists())


def _load_input_model_module(modname: str) -> tuple[types.ModuleType, _ModuleRestoreState | None]:
    if _is_path_input_model_module(modname):
        file_path = Path(modname).resolve()
        if not file_path.exists():
            msg = f"File not found: {modname!r}"
            raise Error(msg)
        return _load_module_from_path(file_path, modname)

    try:
        if importlib.util.find_spec(modname) is None:
            msg = f"Cannot find module {modname!r}"
            raise Error(msg)
        return importlib.import_module(modname), None
    except ImportError as e:
        msg = f"Cannot import module {modname!r}: {e}"
        raise Error(msg) from e


def _is_input_model_base_schema(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(cast("dict[str, object]", value).get("x-is-base-class"))


# Types that are lost during JSON Schema conversion and need to be preserved
_PRESERVED_TYPE_ORIGINS: dict[type, str] = {}

# Marker for types that Pydantic cannot serialize to JSON Schema
_UNSERIALIZABLE_MARKER = "x-python-unserializable"
_UNION_BRANCH_MARKER = "x-python-union-branch"
_UNSERIALIZABLE_SCHEMA_KEYS = frozenset({"anyOf", "oneOf", "allOf", "items", "prefixItems", "additionalProperties"})
_FIELD_SCHEMA_NAME = "x-datamodel-code-generator-field-name"
_MISSING_FIELD_SCHEMA_NAME = sentinel("_MISSING_FIELD_SCHEMA_NAME")


class _FieldSchemaOwner(int):
    """Owned scalar metadata survives Pydantic's recursive schema remapping."""

    field_name: str
    previous: Any

    def __new__(cls, field_name: str, previous: Any) -> Self:
        owner = super().__new__(cls, 0)
        owner.field_name = field_name
        owner.previous = previous
        return owner

    def __deepcopy__(self, memo: dict[int, Any]) -> _FieldSchemaOwner:
        previous = self.previous
        if previous is not _MISSING_FIELD_SCHEMA_NAME:
            previous = deepcopy(previous, memo)
        return type(self)(self.field_name, previous)


# Type family constants
_TYPE_FAMILY_ENUM = "enum"
_TYPE_FAMILY_PYDANTIC = "pydantic"
_TYPE_FAMILY_DATACLASS = "dataclass"
_TYPE_FAMILY_TYPEDDICT = "typeddict"
_TYPE_FAMILY_MSGSPEC = "msgspec"
_TYPE_FAMILY_OTHER = "other"


def _runtime_python_type_expr(tp: object, *, full_name: bool = False) -> PythonTypeExpr:
    """Bind a live type lazily so normal code-generation imports stay lightweight."""
    from datamodel_code_generator._python_type_runtime import (  # noqa: PLC0415
        python_type_expr_from_runtime,
        python_type_expr_from_runtime_full_name,
    )

    return python_type_expr_from_runtime_full_name(tp) if full_name else python_type_expr_from_runtime(tp)


def _transport_python_type_expr(
    expression: PythonTypeExpr,
    expression_collector: PythonTypeExpressionCollector | None,
) -> str:
    """Keep the public text contract while tokenizing the private parser path."""
    if expression_collector is not None:

        def bind_runtime_symbol_to_syntax(item: PythonTypeExpr) -> PythonTypeExpr:
            match item:
                case PythonTypeRuntimeSymbol() as runtime_symbol:
                    module = runtime_symbol.module
                    parts = runtime_symbol.qualname_parts
                    if len(parts) != 1:
                        return item if module else PythonTypeQualifiedName(parts)
                    if module:
                        # A top-level runtime symbol has the same import semantics as
                        # its historical dotted text. Nested qualnames must retain
                        # runtime provenance: their outer classes are not modules.
                        return PythonTypeQualifiedName((*module.split("."), parts[0]))
                    return PythonTypeName(parts[0])
            return item

        return expression_collector.add(rewrite_python_type_expr(expression, bind_runtime_symbol_to_syntax))
    return render_python_type_expr(expression)


def _serialize_python_type_full(
    tp: object,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> str:
    """Serialize ANY Python type to its string representation."""
    try:
        return _transport_python_type_expr(_runtime_python_type_expr(tp), expression_collector)
    except ValueError as exc:
        raise Error(str(exc)) from None


def _get_input_model_json_schema_class(
    definition_types: dict[str, type | str],
    model_classes: Sequence[type] | None = None,
    *,
    preserve_type_identity: bool = False,
    preserve_generic_identity: bool = False,
    use_standard_schema: bool = False,
) -> type[GenerateJsonSchema]:
    """Get the InputModelJsonSchema class lazily."""
    from pydantic.json_schema import GenerateJsonSchema  # noqa: PLC0415

    class InputModelJsonSchema(GenerateJsonSchema):
        """Custom schema generator that handles ALL unserializable types."""

        _unserializable_count = 0

        def union_schema(self, schema: Any) -> dict[str, Any]:
            """Bind original choices only when this union needs annotation recovery."""
            before = self._unserializable_count
            result = super().union_schema(schema)
            if before == self._unserializable_count:
                return result
            targets = {
                item[_UNSERIALIZABLE_MARKER]: branch
                for branch in result.get("anyOf", [result])
                for item in _iter_unserializable_schemas(branch)
            }
            for index, choice in enumerate(schema["choices"]):
                _bind_union_choice(choice, targets, index)
            return result

        has_field_schema_owners = False

        def generate(self, schema: Any, mode: Any = "validation") -> dict[str, Any]:
            self.has_field_schema_owners = False
            result = super().generate(schema, mode=mode)
            type(self).has_field_schema_owners = self.has_field_schema_owners
            return result

        def _named_required_fields_schema(self, named_required_fields: Any) -> dict[str, Any]:
            for _name, _required, field in named_required_fields:
                if self.by_alias and field.get("validation_alias") is not None:
                    break
            else:
                return super()._named_required_fields_schema(named_required_fields)

            schema_names: set[str] = set()
            duplicates: set[str] = set()
            for name, _required, field in named_required_fields:
                schema_name = self._get_alias_name(field, name)
                if schema_name in schema_names:
                    duplicates.add(schema_name)
                schema_names.add(schema_name)
            if not duplicates:
                return super()._named_required_fields_schema(named_required_fields)

            field_names = {
                id(field): name
                for name, _required, field in named_required_fields
                if self._get_alias_name(field, name) in duplicates
            }
            previous_generate_inner = self.generate_inner
            previous_override = self.__dict__.get("generate_inner")

            def generate_owned_field(schema: Any) -> dict[str, Any]:
                result = previous_generate_inner(schema)
                if (field_name := field_names.get(id(schema))) is None:
                    return result
                self.has_field_schema_owners = True
                return {
                    **result,
                    _FIELD_SCHEMA_NAME: _FieldSchemaOwner(
                        field_name, result.get(_FIELD_SCHEMA_NAME, _MISSING_FIELD_SCHEMA_NAME)
                    ),
                }

            # Keep any definition-identity hook in the delegate, and intercept only
            # this affected field group. Remove the instance binding afterwards.
            self.__dict__["generate_inner"] = generate_owned_field
            try:
                return super()._named_required_fields_schema(named_required_fields)
            finally:
                if previous_override is None:
                    del self.__dict__["generate_inner"]
                else:
                    self.__dict__["generate_inner"] = previous_override

        def handle_invalid_for_json_schema(
            self,
            schema: Any,
            error_info: Any,  # noqa: ARG002
        ) -> dict[str, Any]:
            """Catch ALL types that Pydantic can't serialize to JSON Schema."""
            self._unserializable_count += 1
            return {
                "type": "object",
                _UNSERIALIZABLE_MARKER: id(schema),
            }

        def callable_schema(
            self,
            schema: Any,
        ) -> dict[str, Any]:
            """Handle Callable types - these raise before handle_invalid_for_json_schema."""
            self._unserializable_count += 1
            return {
                "type": "string",
                _UNSERIALIZABLE_MARKER: id(schema),
            }

    class IdentifiedInputModelJsonSchema(
        cast("Any", GenerateJsonSchema if use_standard_schema else InputModelJsonSchema)
    ):
        """Retain Pydantic's definition-to-runtime-type correspondence when merging."""

        def generate_inner(self, schema: Any) -> dict[str, Any]:
            if (ref := schema.get("ref")) and (cls := schema.get("cls")):
                core_types[ref] = cls
            return super().generate_inner(schema)

        def _build_definitions_remapping(self) -> Any:
            collision_names = (
                _colliding_type_names(
                    core_types.get(self.defs_to_core_refs[defs_ref][0], "") for defs_ref in self.definitions
                )
                if (preserve_type_identity or preserve_generic_identity) and len(self.definitions) > 1
                else None
            )
            if collision_names and not preserve_type_identity:
                collision_names.intersection_update(
                    model.__name__ for model in core_types.values() if _pydantic_generic_metadata(model)
                )
            if collision_names:
                owners: dict[str, type] = {}
                collisions: set[str] = set()
                for defs_ref in self.definitions:
                    core_ref, _mode = self.defs_to_core_refs[defs_ref]
                    if (model := core_types.get(core_ref)) is None or model.__name__ not in collision_names:
                        continue
                    for choice in self._prioritized_defsref_choices[defs_ref]:
                        if choice in owners and owners[choice] is not model:
                            collisions.add(choice)
                        else:
                            owners[choice] = model
                if collisions:
                    # Equal JSON schemas can still belong to different runtime
                    # types. Reuse needs separate refs before native deduplication.
                    for defs_ref, choices in self._prioritized_defsref_choices.items():
                        if not collisions.isdisjoint(choices):
                            self._prioritized_defsref_choices[defs_ref] = [
                                choice for choice in choices if choice not in collisions
                            ]
            remapping = super()._build_definitions_remapping()
            definition_types.clear()
            for defs_ref in self.definitions:
                core_ref, _mode = self.defs_to_core_refs[defs_ref]
                definition_types[remapping.remap_defs_ref(defs_ref)] = core_types.get(core_ref, core_ref)
            return remapping

    core_types: dict[str, type] = {}
    if hasattr(GenerateJsonSchema, "_build_definitions_remapping"):
        return IdentifiedInputModelJsonSchema

    class LegacyIdentifiedInputModelJsonSchema(IdentifiedInputModelJsonSchema):
        """Pydantic 2.0 records its final names directly in defs_to_core_refs."""

        def generate(self, schema: Any, mode: Any = "validation") -> dict[str, Any]:
            nonlocal legacy_types_loaded
            result = super().generate(schema, mode=mode)
            definition_types.clear()
            for name in self.definitions:
                core_ref, _mode = self.defs_to_core_refs[name]
                if core_ref not in core_types and not legacy_types_loaded:
                    _collect_core_type_identities(model_classes or (), core_types)
                    legacy_types_loaded = True
                definition_types[name] = core_types.get(core_ref, core_ref)
            return result

    legacy_types_loaded = False
    return LegacyIdentifiedInputModelJsonSchema


def _collect_core_type_identities(models: Iterable[type], core_types: dict[str, type]) -> None:
    """Recover legacy core-ref identities without discarding same-name types."""
    pending: list[Any] = list(models)
    visited: set[type] = set()
    while pending:
        annotation = pending.pop()
        if get_origin(annotation) is None and isinstance(annotation, type):
            if annotation in visited:
                continue
            visited.add(annotation)
            core_types[f"{annotation.__module__}.{annotation.__qualname__}:{id(annotation)}"] = annotation
            if fields := getattr(annotation, "model_fields", None):
                pending.extend(field.annotation for field in fields.values())
            elif (
                is_dataclass(annotation)
                or hasattr(annotation, "__required_keys__")
                or hasattr(annotation, "__struct_fields__")
            ):
                pending.extend(_get_type_hints_safe(annotation).values())
        pending.extend(get_args(annotation))


def _colliding_type_names(definition_types: Iterable[type | str]) -> set[str]:
    """Find distinct runtime types sharing a short name in this schema only."""
    names: dict[str, type] = {}
    collisions: set[str] = set()
    for model in definition_types:
        if isinstance(model, type):
            if model.__name__ in names and names[model.__name__] is not model:
                collisions.add(model.__name__)
            else:
                names[model.__name__] = model
    return collisions


def _colliding_definition_types(definition_types: ABCMapping[str, type | str]) -> dict[str, type] | None:
    """Retain only identity corrections needed by a single root's reuse lookup."""
    if len(definition_types) <= 1 or not (collisions := _colliding_type_names(definition_types.values())):
        return None
    return {
        name: model
        for name, model in definition_types.items()
        if isinstance(model, type) and model.__name__ in collisions
    }


def _is_type_origin(annotation: type) -> bool:
    """Check if annotation is Type[X]."""
    origin = get_origin(annotation)
    return origin is type


def _iter_unserializable_schemas(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        if value.get(_UNSERIALIZABLE_MARKER):
            yield value
        for key, item in value.items():
            if key in _UNSERIALIZABLE_SCHEMA_KEYS:
                yield from _iter_unserializable_schemas(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_unserializable_schemas(item)


class _UnionBranchIndex(int):
    """Identify generated provenance while retaining a caller's same-named extension."""

    original: tuple[Any, ...] = ()


def _consume_union_branch(schema: dict[str, Any]) -> int | None:
    """Consume only provenance produced by the schema generator."""
    if not isinstance(index := schema.get(_UNION_BRANCH_MARKER), _UnionBranchIndex):
        return None
    if index.original:
        schema[_UNION_BRANCH_MARKER] = index.original[0]
    else:
        schema.pop(_UNION_BRANCH_MARKER)
    return index


def _bind_union_choice(value: Any, targets: dict[int, dict[str, Any]], index: int) -> None:
    if not targets:
        return
    if (branch := targets.pop(id(value), None)) is not None:
        marker = _UnionBranchIndex(index)
        if _UNION_BRANCH_MARKER in branch:
            previous = branch[_UNION_BRANCH_MARKER]
            marker.original = previous.original if isinstance(previous, _UnionBranchIndex) else (previous,)
        branch[_UNION_BRANCH_MARKER] = marker
        return
    if isinstance(value, dict):
        for item in value.values():
            _bind_union_choice(item, targets, index)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _bind_union_choice(item, targets, index)


def _has_unserializable_schema(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(value.get(_UNSERIALIZABLE_MARKER)) or any(
            _has_unserializable_schema(item) for key, item in value.items() if key in _UNSERIALIZABLE_SCHEMA_KEYS
        )
    return isinstance(value, list) and any(_has_unserializable_schema(item) for item in value)


def _remove_unserializable_markers(value: Any) -> None:
    if isinstance(value, dict):
        value.pop(_UNSERIALIZABLE_MARKER, None)
        _consume_union_branch(value)
        for key, item in value.items():
            if key in _UNSERIALIZABLE_SCHEMA_KEYS:
                _remove_unserializable_markers(item)
    elif isinstance(value, list):
        for item in value:
            _remove_unserializable_markers(item)


def _process_unserializable_property(
    prop: dict[str, Any],
    annotation: type,
    expression_collector: PythonTypeExpressionCollector | None = None,
    *,
    in_union: bool = False,
) -> None:
    """Process a single property, handling anyOf/oneOf/items structures."""
    if branches := prop.get("anyOf", prop.get("oneOf")):
        if not in_union and not _has_unserializable_schema(prop):
            return
        while (origin := get_origin(annotation)) is Annotated:
            annotation = get_args(annotation)[0]
        args = (
            tuple(arg for arg in get_args(annotation) if arg is not type(None))
            if origin is Union or origin is types.UnionType
            else ()
        )
        for item in branches:
            index = _consume_union_branch(item)
            branch_annotation = args[index] if args and index is not None else args[0] if len(args) == 1 else annotation
            _process_unserializable_property(item, branch_annotation, expression_collector, in_union=True)
    elif prop.get(_UNSERIALIZABLE_MARKER):
        _set_python_type_for_unserializable(prop, annotation, expression_collector)
    elif ("items" in prop or "prefixItems" in prop or "additionalProperties" in prop or "allOf" in prop) and (
        _has_unserializable_schema(prop)
    ):
        prop["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)
        _remove_unserializable_markers(prop)
    elif not in_union and _is_type_origin(annotation):
        prop["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)


def _set_python_type_for_unserializable(
    item: dict[str, Any],
    annotation: type,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> None:
    """Set x-python-type and clean up markers."""
    item["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)
    item.pop(_UNSERIALIZABLE_MARKER, None)


def _input_model_field_wire_name(field_name: str, field_info: Any) -> str:
    """Resolve the property name emitted by Pydantic's validation schema."""
    if (alias := field_info.validation_alias) is None:
        return field_name
    if isinstance(alias, str):
        return alias
    for path in alias.convert_to_aliases():
        if isinstance(path, list) and len(path) == 1 and isinstance(path[0], str):
            return path[0]
    return field_name


def _add_python_type_for_unserializable(
    schema: dict[str, Any],
    model: type,
    visited_defs: set[str] | None = None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, Any]:
    """Add x-python-type to ALL fields marked as unserializable."""
    if visited_defs is None:
        visited_defs = set()

    if "properties" in schema:
        model_fields = getattr(model, "model_fields", {})
        for field_name, field_info in model_fields.items():
            schema_name = _input_model_field_wire_name(field_name, field_info)
            if (prop := schema["properties"].get(schema_name)) is not None:
                owner = prop.get(_FIELD_SCHEMA_NAME)
                if isinstance(owner, _FieldSchemaOwner) and owner.field_name != field_name:
                    continue
                _process_unserializable_property(prop, field_info.annotation, expression_collector)

    if "$defs" in schema:
        nested_models = _collect_nested_models(model)
        model_name = getattr(model, "__name__", None)
        if model_name:  # pragma: no branch
            nested_models[model_name] = model
        for def_name, def_schema in schema["$defs"].items():
            if def_name in visited_defs:  # pragma: no cover
                continue
            visited_defs.add(def_name)
            if def_name in nested_models:  # pragma: no branch
                _add_python_type_for_unserializable(
                    def_schema,
                    nested_models[def_name],
                    visited_defs,
                    expression_collector,
                )

    return schema


def _init_preserved_type_origins() -> dict[type, str]:
    """Initialize preserved type origins mapping (lazy initialization)."""
    return {
        set: "set",
        frozenset: "frozenset",
        defaultdict: "defaultdict",
        OrderedDict: "OrderedDict",
        Counter: "Counter",
        deque: "deque",
        ChainMap: "ChainMap",
        AbstractSet: "AbstractSet",
        ABCMutableSet: "MutableSet",
        ABCMapping: "Mapping",
        ABCMutableMapping: "MutableMapping",
        ABCSequence: "Sequence",
        ABCMutableSequence: "MutableSequence",
    }


def _get_preserved_type_origins() -> dict[type, str]:
    """Get the preserved type origins mapping, initializing if needed."""
    global _PRESERVED_TYPE_ORIGINS  # noqa: PLW0603
    if not _PRESERVED_TYPE_ORIGINS:
        _PRESERVED_TYPE_ORIGINS = _init_preserved_type_origins()
    return _PRESERVED_TYPE_ORIGINS


def _serialize_python_type(
    tp: type,
    expression_collector: PythonTypeExpressionCollector | None = None,
    generic_names: dict[type, str] | None = None,
) -> str | None:
    """Serialize Python type to a string for x-python-type field."""
    expression = _preserved_python_type_expr(tp)
    if expression is not None and generic_names:
        expression = _preserved_python_type_expr(tp, generic_names)
    return _transport_python_type_expr(expression, expression_collector) if expression is not None else None


def _preserved_python_type_expr(tp: type, generic_names: dict[type, str] | None = None) -> PythonTypeExpr | None:  # ruff: ignore[too-many-return-statements]
    """Build IR only when JSON Schema loses relevant runtime type structure."""
    if generic_names and isinstance(tp, type) and (name := generic_names.get(tp)):
        return PythonTypeName(name)
    origin: type | None = get_origin(tp)
    args = get_args(tp)
    preserved_origins = _get_preserved_type_origins()

    is_union = origin is Union or (hasattr(types, "UnionType") and origin is types.UnionType)
    if is_union:
        if args:
            nested = tuple(_preserved_python_type_expr(argument, generic_names) for argument in args)
            if any(expression is not None for expression in nested):
                return PythonTypeUnion(
                    tuple(
                        expression or _runtime_python_type_expr(argument, full_name=True)
                        for expression, argument in zip(nested, args, strict=True)
                    )
                )
        return None  # pragma: no cover

    if origin is Annotated:
        if args:
            return _preserved_python_type_expr(args[0], generic_names) or _runtime_python_type_expr(
                args[0], full_name=True
            )
        return None  # pragma: no cover

    type_name: str | None = None
    if origin is not None:
        type_name = preserved_origins.get(origin)
        if type_name is None and getattr(origin, "__module__", None) == "collections":  # pragma: no cover
            type_name = _simple_type_name(origin)
    if type_name is not None:
        if args:
            return PythonTypeSubscript(
                PythonTypeName(type_name),
                tuple(
                    _preserved_python_type_expr(argument, generic_names)
                    or _runtime_python_type_expr(argument, full_name=True)
                    for argument in args
                ),
            )
        return PythonTypeName(type_name)  # pragma: no cover

    if args:
        nested = tuple(_preserved_python_type_expr(argument, generic_names) for argument in args)
        if any(expression is not None for expression in nested):
            return PythonTypeSubscript(
                PythonTypeName(_simple_type_name(origin or tp)),
                tuple(
                    expression or _runtime_python_type_expr(argument, full_name=True)
                    for expression, argument in zip(nested, args, strict=True)
                ),
            )

    return None


def _simple_type_name(tp: type) -> str:
    """Get a simple string representation of a type."""
    if tp is type(None):
        return "None"
    if get_origin(tp) is not None:
        return str(tp).replace("typing.", "")
    if hasattr(tp, "__name__"):
        return tp.__name__
    return str(tp).replace("typing.", "")  # pragma: no cover


def _full_type_name(tp: type) -> str:
    """Get a full qualified name representation of a type for type arguments.

    For generic types, keeps outer type as short name but FQN-izes the type arguments.
    For non-generic types, returns FQN for non-builtin types.
    """
    return render_python_type_expr(_runtime_python_type_expr(tp, full_name=True))


def _collect_nested_models(model: type, visited: set[type] | None = None) -> dict[str, type]:
    """Collect all nested types (BaseModel, Enum, dataclass) from a model's fields."""
    if visited is None:
        visited = set()

    if model in visited:  # pragma: no cover
        return {}
    visited.add(model)

    result: dict[str, type] = {}

    model_fields = getattr(model, "model_fields", None)
    if model_fields is not None:
        for field_info in model_fields.values():
            tp = field_info.annotation
            _find_models_in_type(tp, result, visited)
    else:
        type_hints = _get_type_hints_safe(model)
        for tp in type_hints.values():
            _find_models_in_type(tp, result, visited)

    return result


def _find_models_in_type(tp: type, result: dict[str, type], visited: set[type]) -> None:
    """Recursively find BaseModel, Enum, dataclass, TypedDict, and msgspec in a type annotation."""
    if get_origin(tp) is None and isinstance(tp, type) and tp not in visited:
        if issubclass(tp, BaseModel):
            result[tp.__name__] = tp
            result.update(_collect_nested_models(tp, visited))
        elif (
            issubclass(tp, PyEnum)
            or is_dataclass(tp)
            or hasattr(tp, "__required_keys__")
            or hasattr(tp, "__struct_fields__")
        ):
            result[tp.__name__] = tp

    for arg in get_args(tp):
        _find_models_in_type(arg, result, visited)


def _get_type_hints_safe(obj: type) -> dict[str, Any]:
    """Safely get type hints from a class, handling forward references."""
    try:
        return get_type_hints(obj)
    except Exception:  # noqa: BLE001  # pragma: no cover
        return getattr(obj, "__annotations__", {})


def _add_python_type_to_properties(
    properties: dict[str, Any],
    model_fields: dict[str, Any],
    expression_collector: PythonTypeExpressionCollector | None = None,
    generic_names: dict[type, str] | None = None,
) -> None:
    """Add x-python-type to properties dict for given model fields."""
    for field_name, field_info in model_fields.items():
        schema_name = _input_model_field_wire_name(field_name, field_info)
        if (prop := properties.get(schema_name)) is None:
            continue
        owner = prop.get(_FIELD_SCHEMA_NAME)
        if isinstance(owner, _FieldSchemaOwner) and owner.field_name != field_name:
            continue
        if serialized := _serialize_python_type(field_info.annotation, expression_collector, generic_names):
            prop["x-python-type"] = serialized


def _clear_field_schema_names(schema: dict[str, Any]) -> None:
    """Remove temporary owners from schema positions, including custom inlined models."""
    from datamodel_code_generator._json_schema import (  # ruff: ignore[import-outside-top-level]
        SCHEMA_MAP_KEYS,
        SCHEMA_VALUE_KEYS,
    )

    pending: list[Any] = [schema]
    while pending:
        node = pending.pop()
        if isinstance(node, dict):
            owner = node.get(_FIELD_SCHEMA_NAME)
            if isinstance(owner, _FieldSchemaOwner):
                if owner.previous is _MISSING_FIELD_SCHEMA_NAME:
                    del node[_FIELD_SCHEMA_NAME]
                else:
                    node[_FIELD_SCHEMA_NAME] = owner.previous
            for key, value in node.items():
                if key in SCHEMA_MAP_KEYS and isinstance(value, dict):
                    pending.extend(value.values())
                elif key in SCHEMA_VALUE_KEYS:
                    pending.append(value)
        elif isinstance(node, list):
            pending.extend(node)


def _add_python_type_to_model_properties(
    properties: dict[str, Any],
    model: type,
    expression_collector: PythonTypeExpressionCollector | None,
    generic_names: dict[type, str] | None,
) -> None:
    """Supplement one definition using the fields of its actual Python model."""
    if model_fields := getattr(model, "model_fields", None):
        _add_python_type_to_properties(properties, model_fields, expression_collector, generic_names)
        return
    for field_name, field_type in _get_type_hints_safe(model).items():
        if (prop := properties.get(field_name)) is not None and (
            serialized := _serialize_python_type(field_type, expression_collector, generic_names)
        ):
            prop["x-python-type"] = serialized


def _add_python_type_info(  # ruff: ignore[too-many-arguments]
    schema: dict[str, Any],
    model: type,
    expression_collector: PythonTypeExpressionCollector | None = None,
    generic_definitions: dict[str, type | str] | None = None,
    schema_types: dict[str, type | str] | None = None,
    *,
    clear_field_names: bool = False,
) -> dict[str, Any]:
    """Add Python type information to root properties and identified nested definitions."""
    generic_names = _generic_definition_names(generic_definitions)
    if properties := schema.get("properties"):
        _add_python_type_to_model_properties(properties, model, expression_collector, generic_names)

    if (definitions := schema.get("$defs")) and schema_types:
        for def_name, def_schema in definitions.items():
            if (
                isinstance(nested_model := schema_types.get(def_name), type)
                and (not _pydantic_generic_metadata(nested_model) or def_name == nested_model.__name__)
                and (properties := def_schema.get("properties"))
            ):
                _add_python_type_to_model_properties(properties, nested_model, expression_collector, generic_names)
    if clear_field_names:
        _clear_field_schema_names(schema)
    return schema


def _get_type_family(tp: type) -> str:  # noqa: PLR0911
    """Determine the type family of a Python type."""
    if isinstance(tp, type) and issubclass(tp, PyEnum):
        return _TYPE_FAMILY_ENUM

    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return _TYPE_FAMILY_PYDANTIC

    if hasattr(tp, "__pydantic_fields__") and is_dataclass(tp):  # pragma: no cover
        return _TYPE_FAMILY_PYDANTIC

    if is_dataclass(tp):
        return _TYPE_FAMILY_DATACLASS

    if isinstance(tp, type) and hasattr(tp, "__required_keys__"):
        return _TYPE_FAMILY_TYPEDDICT

    if isinstance(tp, type) and hasattr(tp, "__struct_fields__"):  # pragma: no cover
        return _TYPE_FAMILY_MSGSPEC

    return _TYPE_FAMILY_OTHER  # pragma: no cover


def _should_reuse_type(source_family: str, output_family: _OutputModelFamily) -> bool:
    """Determine if a source type can be reused without conversion."""
    if source_family == _TYPE_FAMILY_ENUM:
        return True
    return source_family == output_family


def _has_qualified_type_export(nested_type: type, qualname: str) -> bool:
    """Confirm that a static owner path resolves to the same runtime type."""
    if not issubclass(type(module := sys.modules.get(nested_type.__module__)), types.ModuleType):
        return False
    namespace = vars(types.ModuleType)["__dict__"].__get__(module)
    if namespace.get(nested_type.__name__) is nested_type:
        return False
    owner_path, _, name = qualname.rpartition(".")
    parent: object = module
    try:
        for owner_name in owner_path.split("."):
            owner = namespace.get(owner_name)
            if not issubclass(type(owner), type) or getattr(parent, owner_name, None) is not owner:
                return False
            parent = owner
            namespace = type.__dict__["__dict__"].__get__(owner)
        return namespace.get(name) is nested_type and getattr(parent, name, None) is nested_type
    except Exception:  # ruff: ignore[blind-except]
        # A user attribute hook may reject this optional path while the short export still works.
        return False


def _pydantic_generic_metadata(tp: object) -> dict[str, Any] | None:
    """Read specialization metadata without executing a metaclass attribute hook."""
    if get_origin(tp) is None and isinstance(tp, type):
        metadata = type.__dict__["__dict__"].__get__(tp).get("__pydantic_generic_metadata__")
        if metadata and metadata.get("origin") is not None:
            return metadata
    return None


def _reuses_pydantic_generics(strategy: InputModelRefStrategy | None, family: _OutputModelFamily | None) -> bool:
    """Apply specialization references only when Pydantic types are reused."""
    return strategy == InputModelRefStrategy.ReuseAll or (
        strategy == InputModelRefStrategy.ReuseForeign and family == _TYPE_FAMILY_PYDANTIC
    )


def _generic_definition_names(definitions: dict[str, type | str] | None) -> dict[type, str] | None:
    """Resolve generic annotation leaves through the same definitions as JSON references."""
    if definitions is None:
        return None
    return {cast("type", model): name for name, model in definitions.items() if _pydantic_generic_metadata(model)}


def _prepare_reused_model_types(
    model: type, strategy: InputModelRefStrategy | None, family: _OutputModelFamily | None
) -> tuple[dict[str, type] | None, dict[str, type | str] | None]:
    """Capture generic definition identities only when referenced types are reused."""
    if not strategy or strategy == InputModelRefStrategy.RegenerateAll:
        return None, None
    visited: set[type] = set()
    nested = _collect_nested_models(model, visited)
    identities = (
        {}
        if (strategy is InputModelRefStrategy.ReuseAll and nested)
        or (_reuses_pydantic_generics(strategy, family) and any(_pydantic_generic_metadata(item) for item in visited))
        else None
    )
    return nested, identities


def _reused_python_type_expression(tp: object) -> PythonTypeExpr:
    """Retain specialized models and the actual export paths of their arguments."""
    if metadata := _pydantic_generic_metadata(tp):
        return PythonTypeSubscript(
            _reused_python_type_expression(metadata["origin"]),
            tuple(_reused_python_type_expression(argument) for argument in metadata["args"]),
        )
    expression = _runtime_python_type_expr(tp)
    if isinstance(tp, type) and isinstance(expression, PythonTypeRuntimeSymbol):
        qualname = tp.__qualname__
        parts = (
            tuple(qualname.split("."))
            if "." in qualname and _has_qualified_type_export(tp, qualname)
            else (tp.__name__,)
        )
        return PythonTypeRuntimeSymbol(tp.__module__, parts)
    replacements: dict[PythonTypeExpr, PythonTypeExpr] = {}
    pending = list(get_args(tp))
    while pending:
        argument = pending.pop()
        if isinstance(argument, type):
            replacements[_runtime_python_type_expr(argument)] = _reused_python_type_expression(argument)
        elif isinstance(argument, PyEnum):
            owner = _reused_python_type_expression(type(argument))
            if not isinstance(owner, PythonTypeRuntimeSymbol):
                msg = (
                    f"Cannot safely reuse enum member {argument!r}: no importable type expression. Use regenerate-all."
                )
                raise Error(msg)
            replacements[PythonTypeOpaqueText(str(argument))] = PythonTypeRuntimeSymbol(
                owner.module, (*owner.qualname_parts, argument.name)
            )
        else:
            pending.extend(get_args(argument))
    return rewrite_python_type_expr(expression, lambda item: replacements.get(item, item))


def _has_annotated_generic_argument(tp: object) -> bool:
    """Detect arguments whose metadata cannot be reconstructed by the type serializer."""
    if get_origin(tp) is Annotated:
        return True
    metadata = _pydantic_generic_metadata(tp)
    return any(_has_annotated_generic_argument(arg) for arg in (metadata["args"] if metadata else get_args(tp)))


def _native_generic_field_paths(models: Sequence[type]) -> dict[type, dict[str, Any]]:
    """Capture only observed native model_fields paths, retaining exact specialization identity."""
    paths: dict[type, dict[str, Any]] = {}
    pending = [
        (model, {"module": symbol.module, "qualname": ".".join(symbol.qualname_parts), "fields": []})
        for model in models
        if (symbol := _exported_runtime_symbol(model, [model.__module__])) is not None
    ]
    visited: set[type] = set()
    while pending:
        model, path = pending.pop()
        if model in visited:
            continue
        visited.add(model)
        model_fields = getattr(model, "model_fields", None)
        annotations = (
            {name: info.annotation for name, info in model_fields.items()}
            if model_fields is not None
            else getattr(model, "__annotations__", {})
        )
        for name, field_annotation in annotations.items():
            arguments: list[tuple[object, tuple[int, ...]]] = [(field_annotation, ())]
            while arguments:
                annotation, indexes = arguments.pop()
                field_path = {
                    **path,
                    "fields": [
                        *path["fields"],
                        {"name": name, "arguments": indexes, "pydantic": model_fields is not None},
                    ],
                }
                if _pydantic_generic_metadata(annotation):
                    paths.setdefault(cast("type", annotation), field_path)
                if get_origin(annotation) is None and isinstance(annotation, type):
                    pending.append((annotation, field_path))
                else:
                    args = get_args(annotation)
                    if get_origin(annotation) is Annotated:
                        args = args[:1]
                    arguments.extend((argument, (*indexes, index)) for index, argument in enumerate(args))
    return paths


def _exported_runtime_symbol(tp: object, modules: list[str]) -> PythonTypeRuntimeSymbol | None:
    """Find a module export whose actual attribute access retains the supplied object."""
    for module_name in modules:
        module = sys.modules.get(module_name)
        if issubclass(type(module), types.ModuleType):
            for name, value in vars(module).items():
                if value is tp and name.isidentifier():
                    try:
                        exported = getattr(module, name)
                    except Exception:  # ruff: ignore[blind-except]
                        # A module hook can refuse an otherwise visible static export.
                        exported = None
                    if exported is tp:
                        return PythonTypeRuntimeSymbol(module_name, (name,))
    if (
        get_origin(tp) is None
        and isinstance(tp, type)
        and "." in (qualname := tp.__qualname__)
        and (_has_qualified_type_export(tp, qualname))
    ):
        return PythonTypeRuntimeSymbol(tp.__module__, tuple(qualname.split(".")))
    return None


def _exported_generic_expression(  # ruff: ignore[too-many-return-statements]
    tp: object, modules: list[str] | None
) -> PythonTypeExpr | None:
    """Try symbolic reuse, deferring metadata alias lookup until native paths are tried."""
    if not _has_annotated_generic_argument(tp):
        try:
            return _reused_python_type_expression(tp)
        except Error:
            return _exported_runtime_symbol(tp, modules) if modules is not None else None
    if modules is None:
        return None
    if exported := _exported_runtime_symbol(tp, modules):
        return exported
    if get_origin(tp) is Annotated:
        return None
    metadata = _pydantic_generic_metadata(tp)
    expression = _runtime_python_type_expr(tp) if metadata is None else None
    arguments = tuple(
        _exported_generic_expression(argument, modules) for argument in (metadata["args"] if metadata else get_args(tp))
    )
    if any(argument is None for argument in arguments):
        return None
    resolved = cast("tuple[PythonTypeExpr, ...]", arguments)
    if metadata:
        return PythonTypeSubscript(_reused_python_type_expression(metadata["origin"]), resolved)
    if isinstance(expression, PythonTypeUnion):
        return PythonTypeUnion(resolved)
    return PythonTypeSubscript(cast("PythonTypeSubscript", expression).base, resolved)


def _generic_python_import(
    origin: type,
    expression_collector: PythonTypeExpressionCollector | None,
    expression: PythonTypeExpr,
) -> dict[str, Any]:
    """Transport generic syntax and exact runtime leaves through the existing IR."""
    symbols: dict[str, dict[str, str]] = {}

    def remember_symbol(item: PythonTypeExpr) -> PythonTypeExpr:
        if isinstance(item, PythonTypeRuntimeSymbol) and len(item.qualname_parts) > 1:
            symbols[render_python_type_expr(item)] = {"module": item.module, "qualname": ".".join(item.qualname_parts)}
        return item

    rewrite_python_type_expr(expression, remember_symbol)
    return {
        "module": origin.__module__,
        "name": origin.__name__,
        "x-python-type": _transport_python_type_expr(expression, expression_collector),
        "symbols": symbols,
    }


def _filter_defs_by_strategy(  # ruff: ignore[too-many-branches, too-many-arguments, too-many-positional-arguments]
    schema: dict[str, Any],
    nested_models: dict[str, type],
    output_family: _OutputModelFamily | None,
    strategy: InputModelRefStrategy,
    expression_collector: PythonTypeExpressionCollector | None = None,
    model_classes: Sequence[type] | None = None,
) -> dict[str, Any]:
    """Filter $defs based on ref strategy, marking reused types with x-python-import."""
    if strategy == InputModelRefStrategy.RegenerateAll:  # pragma: no cover
        return schema

    if "$defs" not in schema:  # pragma: no cover
        return schema

    new_defs: dict[str, Any] = {}
    native_paths = None

    for def_name, def_schema in schema["$defs"].items():
        if def_name not in nested_models:  # pragma: no cover
            new_defs[def_name] = def_schema
            continue

        nested_type = nested_models[def_name]
        type_family = _get_type_family(nested_type)

        match strategy:
            case InputModelRefStrategy.ReuseAll:
                should_reuse = True
            case InputModelRefStrategy.ReuseForeign if output_family is not None:
                should_reuse = _should_reuse_type(type_family, output_family)
            case _:  # pragma: no cover
                should_reuse = False  # pragma: no cover

        if should_reuse:
            if metadata := _pydantic_generic_metadata(nested_type):
                if (expression := _exported_generic_expression(nested_type, None)) is None:
                    if native_paths is None:
                        native_paths = _native_generic_field_paths(model_classes or list(nested_models.values()))
                    if path := native_paths.get(nested_type):
                        new_defs[def_name] = {
                            "x-python-import": {
                                "module": path["module"],
                                "name": path["qualname"].split(".")[0],
                                "native-field": path,
                            }
                        }
                        continue
                    modules = list(
                        dict.fromkeys([nested_type.__module__, *(model.__module__ for model in model_classes or ())])
                    )
                    expression = _exported_generic_expression(nested_type, modules)
                    if expression is None:
                        # Keep this definition and its references on the legacy schema path.
                        # Unreachable Python validators cannot be recovered from JSON Schema.
                        new_defs[def_name] = def_schema
                        continue
                new_defs[def_name] = {
                    "x-python-import": _generic_python_import(metadata["origin"], expression_collector, expression)
                }
                continue
            new_defs[def_name] = {
                "x-python-import": {
                    "module": nested_type.__module__,
                    "name": nested_type.__name__,
                },
            }
            if "." in (qualname := nested_type.__qualname__) and _has_qualified_type_export(nested_type, qualname):
                new_defs[def_name]["x-python-import"]["qualname"] = qualname
        else:
            new_defs[def_name] = def_schema

    return {**schema, "$defs": new_defs}


def _try_rebuild_model(obj: type) -> None:
    """Try to rebuild a Pydantic model, handling config models specially."""
    module = getattr(obj, "__module__", "")
    class_name = getattr(obj, "__name__", "")
    config_classes = {"GenerateConfig", "ParserConfig", "ParseConfig"}
    main_config_classes = {"Config"}
    if module in {"datamodel_code_generator.config", "config"} and class_name in config_classes:
        from datamodel_code_generator.config import _rebuild_config_model  # noqa: PLC0415
        from datamodel_code_generator.enums import UnionMode  # noqa: PLC0415
        from datamodel_code_generator.model.base import DataModel, DataModelFieldBase  # noqa: PLC0415
        from datamodel_code_generator.types import DataTypeManager, StrictTypes  # noqa: PLC0415

        types_namespace = {
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
            "StrictTypes": StrictTypes,
            "UnionMode": UnionMode,
        }
        _rebuild_config_model(obj, types_namespace)  # ty: ignore[invalid-argument-type]
    elif module == "datamodel_code_generator.__main__" and class_name in main_config_classes:  # pragma: no cover
        from datamodel_code_generator.config import _rebuild_config_model  # noqa: PLC0415
        from datamodel_code_generator.enums import UnionMode  # noqa: PLC0415
        from datamodel_code_generator.types import StrictTypes  # noqa: PLC0415

        types_namespace = {
            "UnionMode": UnionMode,
            "StrictTypes": StrictTypes,
        }
        _rebuild_config_model(obj, types_namespace)  # ty: ignore[invalid-argument-type]
    else:
        obj.model_rebuild()  # ty: ignore[unresolved-attribute]


def _get_base_model_parents(model_class: type) -> list[type[BaseModel]]:
    """Get parent classes that are BaseModel subclasses (excluding BaseModel itself)."""
    return [p for p in model_class.__bases__ if isinstance(p, type) and issubclass(p, BaseModel) and p is not BaseModel]


def _partition_inherited_fields(
    schema: dict[str, object],
    model_class: type[BaseModel],
    parent_fields: dict[str, Any],
    parent_properties: dict[str, object],
    parent_required: frozenset[str],
) -> tuple[dict[str, object], list[str], dict[str, str]]:
    """Keep effective replacements separate from unchanged inherited properties."""
    original_props = cast("dict[str, object]", schema.get("properties", {}))
    original_required = cast("list[str]", schema.get("required", []))
    required_names = frozenset(original_required)
    inherited_names: set[str] = set()
    overrides: dict[str, str] = {}
    for field_name, parent_field in parent_fields.items():
        child_field = model_class.model_fields.get(field_name, parent_field)
        parent_wire = _input_model_field_wire_name(field_name, parent_field)
        child_wire = _input_model_field_wire_name(field_name, child_field)
        if child_wire in original_props and (
            parent_wire != child_wire
            or original_props[child_wire] != parent_properties.get(parent_wire)
            or (child_wire in required_names) != (parent_wire in parent_required)
        ):
            overrides[child_wire] = parent_wire
        elif child_wire in parent_fields:
            inherited_names.add(child_wire)
    return (
        {k: v for k, v in original_props.items() if k not in inherited_names},
        [name for name in original_required if name not in inherited_names],
        overrides,
    )


def _transform_single_model_to_inheritance(  # noqa: PLR0913, PLR0917
    schema: dict[str, object],
    model_class: type[BaseModel],
    schema_generator: type[GenerateJsonSchema],
    processed_parents: dict[type[BaseModel], _ParentSchema] | None = None,
    expression_collector: PythonTypeExpressionCollector | None = None,
    definitions: _InputModelDefinitions | None = None,
    generic_definitions: dict[str, type | str] | None = None,
    schema_types: dict[str, type | str] | None = None,
) -> dict[str, object]:
    """Transform a single model's schema to use allOf inheritance structure."""
    if getattr(model_class, "__pydantic_root_model__", False):
        return schema
    if processed_parents is None:
        processed_parents = {}

    direct_parents = _get_base_model_parents(model_class)

    if not direct_parents:
        return schema

    parent = direct_parents[0]
    parent_name = definitions.name(parent.__name__, parent) if definitions is not None else parent.__name__

    defs = dict(cast("dict[str, object]", schema.get("$defs", {})))

    if parent not in processed_parents:
        _try_rebuild_model(parent)
        parent_schema = parent.model_json_schema(schema_generator=schema_generator)
        parent_schema = _add_python_type_for_unserializable(
            parent_schema,
            parent,
            expression_collector=expression_collector,
        )
        parent_schema = _add_python_type_info(
            parent_schema,
            parent,
            expression_collector,
            generic_definitions,
            schema_types,
            clear_field_names=getattr(schema_generator, "has_field_schema_owners", False),
        )
        if definitions is not None:
            parent_schema = definitions.normalize(parent_schema, parent)
        parent_properties = cast("dict[str, object]", parent_schema.get("properties", {}))
        parent_required = frozenset(cast("list[str]", parent_schema.get("required", [])))
        parent_schema = _transform_single_model_to_inheritance(
            parent_schema,
            parent,
            schema_generator,
            processed_parents,
            expression_collector,
            definitions,
            generic_definitions,
            schema_types,
        )
        processed_parents[parent] = parent_schema, parent_properties, parent_required
    parent_schema, parent_properties, parent_required = processed_parents[parent]

    if "$defs" in parent_schema:
        parent_defs = cast("dict[str, object]", parent_schema["$defs"])
        defs.update(parent_defs)

    parent_def = {k: v for k, v in parent_schema.items() if k != "$defs"}
    parent_def["x-is-base-class"] = True
    defs[parent_name] = parent_def

    child_props, child_required, overrides = _partition_inherited_fields(
        schema, model_class, parent.model_fields, parent_properties, parent_required
    )

    new_schema: dict[str, object] = {"$defs": defs, "allOf": [{"$ref": f"#/$defs/{parent_name}"}]}
    if child_props:
        new_schema["properties"] = child_props
    if child_required:
        new_schema["required"] = child_required
    new_schema["title"] = schema.get("title")
    new_schema["type"] = "object"
    if overrides:
        from datamodel_code_generator.input_model_result import PythonModelBases  # noqa: PLC0415

        new_schema["allOf"] = PythonModelBases(f"#/$defs/{parent_name}", overrides)

    new_schema.update({
        key: value
        for key, value in schema.items()
        if key not in {"$defs", "properties", "required", "title", "type", "allOf"}
    })

    return new_schema


class _InputModelDefinitions:
    """Allocate merged definition names by runtime identity, preserving ordinary names."""

    def __init__(self, models: list[type[BaseModel]]) -> None:
        self.current_types: dict[str, type | str] = {}
        self.nested_types: dict[str, type] = {}
        self.names: dict[type | str, str] = {}
        self.owners: dict[str, type | str] = {}
        self.next_suffixes: dict[str, int] | None = None
        self.reserved = {model.__name__ for model in models}
        for model in models:
            self.reserved.update(_collect_nested_models(model))
            self.reserved.update(parent.__name__ for parent in model.__mro__)

    def name(self, proposed: str, model: type | str) -> str:
        if name := self.names.get(model):
            return name
        name = proposed
        if name in self.owners:
            if self.next_suffixes is None:
                self.next_suffixes = {}
            suffix = self.next_suffixes.get(proposed, 2)
            while (name := f"{proposed}_{suffix}") in self.owners or name in self.reserved:
                suffix += 1
            # Owners and reservations only grow, so earlier suffixes stay occupied.
            self.next_suffixes[proposed] = suffix + 1
        self.names[model] = name
        self.owners[name] = model
        return name

    def normalize(self, schema: dict[str, Any], model_class: type) -> dict[str, Any]:
        if not (defs := schema.get("$defs")):
            return schema
        self.reserved.update(defs)
        renames: dict[str, str] = {}
        for old_name in defs:
            if model := self.current_types.get(old_name):
                name = self.name(old_name, model)
                if (
                    isinstance(model, type)
                    and (model.__name__.isidentifier() or _pydantic_generic_metadata(model))
                    and _get_type_family(model) != _TYPE_FAMILY_OTHER
                ):
                    self.nested_types[name] = model
                if old_name != name:
                    renames[old_name] = name
        if renames:
            # Only schema positions contain references: defaults/examples are user data.
            from datamodel_code_generator._json_schema import _rewrite_schema_refs  # noqa: PLC0415

            schema = _rewrite_schema_refs(schema, renames, set())
            schema["$defs"] = {renames.get(name, name): value for name, value in schema["$defs"].items()}
        if "$ref" in schema or "allOf" in schema:
            name = self.name(model_class.__name__, model_class)
            root_ref = {"$ref": f"#/$defs/{name}"}
            wrapper = {key: value for key, value in schema.items() if key != "$defs"}
            if wrapper in (root_ref, {"allOf": [root_ref]}):
                # A recursive root's wrapper must not overwrite its real definition.
                schema = {**schema["$defs"][name], "$defs": schema["$defs"]}
        return schema


def load_model_schema(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None = None,
    output_model_type: DataModelType | None = None,
) -> dict[str, object]:
    """Load and merge schemas from Python import paths with inheritance support.

    Args:
        input_models: List of import paths in 'module.path:ObjectName' format
        input_file_type: Current input file type setting for validation
        ref_strategy: Strategy for handling referenced types
        output_model_type: Target output model type for reuse-foreign strategy

    Returns:
        Merged schema dict with anyOf referencing all root models
    """
    return _load_model_schema_isolated(input_models, input_file_type, ref_strategy, output_model_type)


def _load_model_schema_with_python_type_expressions(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None = None,
    output_model_type: DataModelType | None = None,
) -> LoadedInputModelSchema:
    """Load the CLI-only schema transport without rendering runtime expressions."""
    expression_collector = PythonTypeExpressionCollector()
    schema = _load_model_schema_isolated(
        input_models,
        input_file_type,
        ref_strategy,
        output_model_type,
        expression_collector,
    )
    return expression_collector.loaded_schema(schema)


def _load_model_schema(  # noqa: PLR0912, PLR0914, PLR0915
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_model_type: DataModelType | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, object]:
    from datamodel_code_generator import InputFileType  # noqa: PLC0415

    output_family: _OutputModelFamily | None = None
    if ref_strategy is InputModelRefStrategy.ReuseForeign:
        output_family = _get_output_model_family(output_model_type)

    if len(input_models) == 1:
        return _load_single_model_schema(
            input_models[0],
            input_file_type,
            ref_strategy,
            output_family,
            expression_collector,
        )

    model_classes: list[type[BaseModel]] = []
    loaded_modules: dict[str, types.ModuleType] = {}
    path_module_states: list[_ModuleRestoreState] = []

    try:
        for input_model in input_models:
            modname, qualname = _split_input_model(input_model)

            if modname not in loaded_modules:
                module, module_state = _load_input_model_module(modname)
                if module_state is not None:
                    path_module_states.append(module_state)
                loaded_modules[modname] = module
            else:
                module = loaded_modules[modname]

            try:
                obj = getattr(module, qualname)
            except AttributeError as e:
                msg = f"Module {modname!r} has no attribute {qualname!r}"
                raise Error(msg) from e

            if not (isinstance(obj, type) and issubclass(obj, BaseModel)):
                msg = f"Multiple --input-model only supports Pydantic v2 BaseModel classes, got {type(obj).__name__}"
                raise Error(msg)

            model_classes.append(obj)

        if input_file_type not in {InputFileType.Auto, InputFileType.JsonSchema}:
            msg = (
                f"--input-file-type must be 'jsonschema' (or omitted) "
                f"when --input-model points to Pydantic models, "
                f"got '{input_file_type.value}'"
            )
            raise Error(msg)

        for model_class in model_classes:
            _try_rebuild_model(model_class)
        definitions = _InputModelDefinitions(model_classes)
        schema_generator = _get_input_model_json_schema_class(
            definitions.current_types,
            model_classes,
            preserve_type_identity=ref_strategy is InputModelRefStrategy.ReuseAll,
            preserve_generic_identity=_reuses_pydantic_generics(ref_strategy, output_family),
        )
        generic_definitions = (
            definitions.current_types if _reuses_pydantic_generics(ref_strategy, output_family) else None
        )
        merged_defs: dict[str, object] = {}
        root_refs: list[dict[str, str]] = []
        processed_parents: dict[type[BaseModel], _ParentSchema] = {}

        for model_class in model_classes:
            schema = model_class.model_json_schema(schema_generator=schema_generator)
            schema = _add_python_type_for_unserializable(
                schema,
                model_class,
                expression_collector=expression_collector,
            )
            schema = _add_python_type_info(
                schema,
                model_class,
                expression_collector,
                generic_definitions,
                definitions.current_types,
                clear_field_names=getattr(schema_generator, "has_field_schema_owners", False),
            )
            schema = definitions.normalize(schema, model_class)
            model_name = definitions.name(model_class.__name__, model_class)

            schema = _transform_single_model_to_inheritance(
                schema,
                model_class,
                schema_generator,
                processed_parents,
                expression_collector,
                definitions,
                generic_definitions,
                definitions.current_types,
            )

            if "$defs" in schema:
                schema_defs = cast("dict[str, object]", schema["$defs"])
                for k, v in schema_defs.items():
                    new_is_base = _is_input_model_base_schema(v)
                    existing = merged_defs.get(k)
                    existing_is_base = _is_input_model_base_schema(existing) if existing is not None else False
                    if k not in merged_defs or (new_is_base and not existing_is_base):
                        merged_defs[k] = v

            model_def = {k: v for k, v in schema.items() if k != "$defs"}
            merged_defs[model_name] = model_def

            root_refs.append({"$ref": f"#/$defs/{model_name}"})

        final_schema: dict[str, object] = {"$defs": merged_defs, "anyOf": root_refs}

        if ref_strategy and ref_strategy != InputModelRefStrategy.RegenerateAll:
            final_schema = _filter_defs_by_strategy(
                final_schema, definitions.nested_types, output_family, ref_strategy, expression_collector, model_classes
            )

        return final_schema
    finally:
        for state in reversed(path_module_states):
            _restore_path_module(state)


def _load_single_model_schema(  # ruff: ignore[too-many-branches, too-many-locals, too-many-statements]
    input_model: str,
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_family: _OutputModelFamily | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, object]:
    """Load schema from a Python import path.

    Args:
        input_model: Import path in 'module.path:ObjectName' format
        input_file_type: Current input file type setting for validation
        ref_strategy: Strategy for handling referenced types
        output_family: Target output compatibility family for reuse-foreign strategy

    Returns:
        Schema dict

    Raises:
        Error: If format invalid, object cannot be loaded, or input_file_type invalid
    """
    from datamodel_code_generator import InputFileType  # noqa: PLC0415

    modname, qualname = _split_input_model(input_model)

    module, path_module_state = _load_input_model_module(modname)

    try:
        try:
            obj = getattr(module, qualname)
        except AttributeError as e:
            msg = f"Module {modname!r} has no attribute {qualname!r}"
            raise Error(msg) from e

        if isinstance(obj, dict):
            if input_file_type == InputFileType.Auto:
                msg = "--input-file-type is required when --input-model points to a dict"
                raise Error(msg)
            if expression_collector is None:
                return obj
            # Dict schemas contain no runtime IR. Preserve the former CLI JSON
            # round trip exactly: it both isolates module state and performs
            # JSON key coercion/validation before parser construction.
            import json  # noqa: PLC0415

            return cast("dict[str, object]", json.loads(json.dumps(obj)))

        if isinstance(obj, type) and issubclass(obj, BaseModel):
            if input_file_type not in {InputFileType.Auto, InputFileType.JsonSchema}:
                msg = (
                    f"--input-file-type must be 'jsonschema' (or omitted) "
                    f"when --input-model points to a Pydantic model, "
                    f"got '{input_file_type.value}'"
                )
                raise Error(msg)
            _try_rebuild_model(obj)
            nested_models, definition_types = _prepare_reused_model_types(obj, ref_strategy, output_family)
            schema_types = definition_types if definition_types is not None else {}
            schema_generator = _get_input_model_json_schema_class(
                schema_types,
                [obj],
                preserve_type_identity=ref_strategy is InputModelRefStrategy.ReuseAll,
                preserve_generic_identity=_reuses_pydantic_generics(ref_strategy, output_family),
            )
            schema = obj.model_json_schema(schema_generator=schema_generator)
            identified_types = _colliding_definition_types(definition_types) if definition_types else None
            if definition_types is not None:
                cast("dict[str, type]", nested_models).update(
                    (name, cast("type", model))
                    for name, model in definition_types.items()
                    if _pydantic_generic_metadata(model)
                )
            schema = _add_python_type_for_unserializable(
                schema,
                obj,
                expression_collector=expression_collector,
            )
            schema = _add_python_type_info(
                schema,
                obj,
                expression_collector,
                definition_types,
                schema_types,
                clear_field_names=getattr(schema_generator, "has_field_schema_owners", False),
            )

            schema = _transform_single_model_to_inheritance(
                schema,
                obj,
                schema_generator,
                expression_collector=expression_collector,
                generic_definitions=definition_types,
                schema_types=schema_types,
            )

            if ref_strategy and nested_models is not None:
                if identified_types:
                    nested_models.update(identified_types)
                model_name = getattr(obj, "__name__", None)
                if model_name and model_name in cast("dict[str, object]", schema.get("$defs", {})):  # pragma: no cover
                    nested_models[model_name] = obj
                schema = _filter_defs_by_strategy(
                    schema, nested_models, output_family, ref_strategy, expression_collector, [obj]
                )

            return schema

        is_typed_dict = isinstance(obj, type) and hasattr(obj, "__required_keys__")
        if is_dataclass(obj) or is_typed_dict:
            if input_file_type not in {InputFileType.Auto, InputFileType.JsonSchema}:
                msg = (
                    f"--input-file-type must be 'jsonschema' (or omitted) "
                    f"when --input-model points to a dataclass or TypedDict, "
                    f"got '{input_file_type.value}'"
                )
                raise Error(msg)
            from pydantic import TypeAdapter  # noqa: PLC0415

            obj_type = cast("type", obj)
            nested_models, definition_types = _prepare_reused_model_types(obj_type, ref_strategy, output_family)
            schema_types = definition_types if definition_types is not None else {}
            schema = TypeAdapter(obj).json_schema(
                schema_generator=_get_input_model_json_schema_class(
                    schema_types,
                    [obj_type],
                    use_standard_schema=definition_types is None,
                    preserve_generic_identity=definition_types is not None,
                )
            )
            schema = _add_python_type_info(schema, obj, expression_collector, definition_types, schema_types)

            if ref_strategy and ref_strategy != InputModelRefStrategy.RegenerateAll:
                nested_models = cast("dict[str, type]", nested_models)
                if definition_types is not None:
                    nested_models.update(
                        (name, cast("type", model))
                        for name, model in definition_types.items()
                        if _pydantic_generic_metadata(model)
                    )
                obj_name = getattr(obj, "__name__", None)
                if obj_name and "$defs" in schema and obj_name in schema["$defs"]:  # pragma: no cover
                    nested_models[obj_name] = obj
                schema = _filter_defs_by_strategy(
                    schema, nested_models, output_family, ref_strategy, expression_collector, [obj_type]
                )

            return schema

        msg = f"{qualname!r} is not a supported type. Supported: dict, Pydantic v2 BaseModel, dataclass, TypedDict"
        raise Error(msg)
    finally:
        if path_module_state is not None:
            _restore_path_module(path_module_state)
