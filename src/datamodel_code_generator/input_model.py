"""Input model loading and schema transformation for --input-model option."""

from __future__ import annotations

import importlib.util
import json
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
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import is_dataclass
from enum import Enum as PyEnum
from functools import partial
from hashlib import sha256
from operator import itemgetter, methodcaller
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, Union, cast, get_args, get_origin, get_type_hints

from pydantic import BaseModel
from pydantic.json_schema import GenerateJsonSchema
from typing_extensions import Self, Sentinel, TypeIs

from datamodel_code_generator._all_or_none import all_or_none
from datamodel_code_generator._context_scope import context_values
from datamodel_code_generator._free_names import first_free
from datamodel_code_generator._graph import reach
from datamodel_code_generator._input_model_transport import PythonTypeExpressionCollector
from datamodel_code_generator._json_schema import (
    MODEL_VALUE_POSITIONS,
    SCHEMA_MAP_KEYS,
    SCHEMA_VALUE_KEYS,
    _rewrite_schema_refs,
    definition_ref,
)
from datamodel_code_generator._process_state import ABSENT, PROCESS_STATE_LOCK, restore_entry
from datamodel_code_generator._python_type_annotation import (
    PythonTypeEllipsis,
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
from datamodel_code_generator._shared_types import Error as GenerationError
from datamodel_code_generator.enums import (
    InputFileType,
    InputModelRefStrategy,
    _get_output_model_family,
    _OutputModelFamily,
)
from datamodel_code_generator.input_model_result import PythonModelBases
from datamodel_code_generator.python_literal import stable_repr

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Sequence

    from datamodel_code_generator.enums import DataModelType
    from datamodel_code_generator.input_model_result import LoadedInputModelSchema


class Error(Exception):
    """Error raised during input model loading."""


_ModuleRestoreState = tuple[str, object]
_ParentSchema = tuple[dict[str, object], dict[str, object], frozenset[str], dict[str, object]]


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


@contextmanager
def _cwd_import_scope(cwd_entry: str) -> Generator[None]:
    """Import from the working directory, then drop the modules imported from there."""
    directory = Path(cwd_entry).resolve()
    environment_directory = Path(sys.prefix).resolve()
    importer_cache_entry = sys.path_importer_cache.get(cwd_entry, ABSENT)
    baseline_modules = sys.modules.copy()
    sys.path.insert(0, cwd_entry)
    try:
        yield
    finally:
        current_modules = sys.modules.copy()
        local_module_names = sorted(
            (
                module_name
                for module_name, module in current_modules.items()
                if module_name not in baseline_modules
                and _module_is_from_directory(module, directory, environment_directory)
            ),
            key=methodcaller("count", "."),
            reverse=True,
        )
        for module_name in local_module_names:
            _remove_local_module(module_name, current_modules[module_name])
        _remove_input_model_path(cwd_entry)
        restore_entry(sys.path_importer_cache, cwd_entry, importer_cache_entry)


_READ_DATACLASS_FIELDS_BY_ALIAS: ContextVar[bool] = ContextVar("_READ_DATACLASS_FIELDS_BY_ALIAS", default=False)
"""Whether a load reads the fields of a Pydantic dataclass, as a model's, by the names of their properties."""
_IMPORTED_TYPES: ContextVar[set[type] | None] = ContextVar("_IMPORTED_TYPES", default=None)
"""The classes the Python types of fields name by their import, collected under --reuse-every-reached-input-model-type.

None without the option; the descent of reuse follows the fields of every model kind, not of models only, while set.
"""


def _load_model_schema_isolated(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_model_type: DataModelType | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
    *,
    read_dataclass_fields_by_alias: bool = False,
    reuse_every_reached_type: bool = False,
) -> dict[str, object]:
    """Load a schema while restoring cwd-local import state afterwards."""
    with (
        PROCESS_STATE_LOCK,
        context_values({
            _READ_DATACLASS_FIELDS_BY_ALIAS: read_dataclass_fields_by_alias,
            _IMPORTED_TYPES: set() if reuse_every_reached_type else None,
        }),
    ):
        cwd_entry = str(Path.cwd())
        with nullcontext() if cwd_entry in sys.path else _cwd_import_scope(cwd_entry):
            return _load_model_schema(
                input_models, input_file_type, ref_strategy, output_model_type, expression_collector
            )


def _restore_path_module(state: _ModuleRestoreState) -> None:
    module_name, previous_module = state
    restore_entry(sys.modules, module_name, previous_module)


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

    digest = sha256(str(file_path).encode()).hexdigest()[:16]
    return f"_datamodel_code_generator_input_model_{digest}"


def _load_module_from_path(file_path: Path, modname: str) -> tuple[types.ModuleType, _ModuleRestoreState]:
    module_name = _get_path_module_name(file_path)
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    if spec is None or spec.loader is None:
        msg = f"Cannot load module from {stable_repr(modname)}"
        raise Error(msg)

    previous_module = sys.modules.get(module_name, ABSENT)
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

    msg = (
        f"Invalid --input-model format: {stable_repr(input_model)}. Expected 'module:Object' or "
        "'path/to/file.py:Object'."
    )
    raise Error(msg)


def _is_path_input_model_module(modname: str) -> bool:
    return "/" in modname or "\\" in modname or (modname.endswith(".py") and Path(modname).exists())


def _load_input_model_module(modname: str) -> tuple[types.ModuleType, _ModuleRestoreState | None]:
    if _is_path_input_model_module(modname):
        file_path = Path(modname).resolve()
        if not file_path.exists():
            msg = f"File not found: {stable_repr(modname)}"
            raise Error(msg)
        return _load_module_from_path(file_path, modname)

    try:
        if importlib.util.find_spec(modname) is None:
            msg = f"Cannot find module {stable_repr(modname)}"
            raise Error(msg)
        return importlib.import_module(modname), None
    except ImportError as e:
        msg = f"Cannot import module {stable_repr(modname)}: {e}"
        raise Error(msg) from e


def _load_input_model_object(
    input_model: str,
    loaded_modules: dict[str, types.ModuleType],
    path_module_states: list[_ModuleRestoreState],
) -> tuple[str, object]:
    """Return the qualname and object of one import path, importing each module once."""
    modname, qualname = _split_input_model(input_model)
    if (module := loaded_modules.get(modname)) is None:
        module, module_state = _load_input_model_module(modname)
        if module_state is not None:
            path_module_states.append(module_state)
        loaded_modules[modname] = module
    try:
        return qualname, getattr(module, qualname)
    except AttributeError as e:
        msg = f"Module {stable_repr(modname)} has no attribute {stable_repr(qualname)}"
        raise Error(msg) from e


def _require_json_schema_input(input_file_type: InputFileType, source: str) -> None:
    """Refuse an --input-file-type other than jsonschema for a model source."""
    if input_file_type not in {InputFileType.Auto, InputFileType.JsonSchema}:
        msg = (
            f"--input-file-type must be 'jsonschema' (or omitted) "
            f"when --input-model points to {source}, "
            f"got '{input_file_type.value}'"
        )
        raise Error(msg)


def _is_input_model_base_schema(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    return bool(cast("dict[str, object]", value).get("x-is-base-class"))


_PRESERVED_TYPE_ORIGINS: dict[type, str] = {
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
"""Origins whose type JSON Schema loses, keyed to the name x-python-type spells them with."""

_VARIADIC_TUPLE_ARGS = 2

# Marker for types that Pydantic cannot serialize to JSON Schema
_UNSERIALIZABLE_MARKER = "x-python-unserializable"
_UNION_BRANCH_MARKER = "x-python-union-branch"
_UNSERIALIZABLE_SCHEMA_KEYS = SCHEMA_VALUE_KEYS.intersection(MODEL_VALUE_POSITIONS)
"""The positions Pydantic writes the schema of a type expression at: branches and items, no named members."""
_UNSERIALIZABLE_PART_KEYS = _UNSERIALIZABLE_SCHEMA_KEYS.difference(("anyOf", "oneOf"))
"""Those positions without the union branches: the items, the additional properties and the ``allOf`` parts."""
_FIELD_SCHEMA_NAME = "x-datamodel-code-generator-field-name"
_MISSING_FIELD_SCHEMA_NAME = Sentinel("_MISSING_FIELD_SCHEMA_NAME")


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


_TYPE_FAMILY_ENUM = "enum"
_TYPE_FAMILY_OTHER = "other"
"""Source families beside the output families of ``_OutputModelFamily``."""


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
                for item in _iter_schema_nodes(branch)
                if item.get(_UNSERIALIZABLE_MARKER)
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
        """Retain Pydantic's definition-to-runtime-type correspondence when merging.

        ``merged_definition_names`` are the definitions of the last schema that Pydantic merged from distinct types,
        which name no one class.
        """

        merged_definition_names: frozenset[str] = frozenset()

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
            merged: set[str] = set()
            for defs_ref in self.definitions:
                core_ref, _mode = self.defs_to_core_refs[defs_ref]
                name, model = remapping.remap_defs_ref(defs_ref), core_types.get(core_ref, core_ref)
                if definition_types.setdefault(name, model) is not model:
                    merged.add(name)
                definition_types[name] = model
            type(self).merged_definition_names = frozenset(merged)
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


def _plain_class(tp: object) -> TypeIs[type]:
    """Return whether an annotation names a class itself, not a generic alias or another special form."""
    return get_origin(tp) is None and isinstance(tp, type)


def _model_class(tp: object) -> TypeIs[type[BaseModel]]:
    """Return whether an object is a Pydantic model class (`BaseModel` itself included)."""
    return isinstance(tp, type) and issubclass(tp, BaseModel)


def _annotation_nodes(annotation: object) -> Iterator[tuple[Any, tuple[int, ...]]]:
    """Yield an annotation, then each argument below it in written order, with the argument indexes that lead to it.

    Every walk of an annotation reads this descent (``get_args``); what it records of the nodes is its own.
    """
    pending: list[tuple[Any, tuple[int, ...]]] = [(annotation, ())]
    while pending:
        node, indexes = pending.pop()
        yield node, indexes
        if arguments := get_args(node):
            pending.extend(reversed([(argument, (*indexes, index)) for index, argument in enumerate(arguments)]))


def _reached_classes(
    annotations: Iterable[object], visited: set[type], *, every_kind: bool
) -> Iterator[tuple[type, str]]:
    """Yield each class the annotations name with its family, a followed class before the classes its fields reach.

    The family is read once for each class met. The walk follows a Pydantic model, with ``every_kind`` every class
    with typed fields (a dataclass, a TypedDict, a msgspec Struct), where it first meets it: in written order, right
    after the class itself. ``visited`` takes the classes followed, each of which is yielded once; any other class is
    yielded wherever an annotation names it.
    """
    for annotation in annotations:
        for node, _ in _annotation_nodes(annotation):
            if _plain_class(node) and node not in visited:
                family = _get_type_family(node)
                yield node, family
                # A class of no family has no fields to follow.
                if family != _TYPE_FAMILY_OTHER and (
                    _model_class(node) or (every_kind and _typed_fields_family(node) is not None)
                ):
                    visited.add(node)
                    yield from _reached_classes(_model_field_annotations(node).values(), visited, every_kind=every_kind)


def _collect_core_type_identities(models: Iterable[type], core_types: dict[str, type]) -> None:
    """Recover legacy core-ref identities without discarding same-name types."""
    for model, _ in _reached_classes(models, set(), every_kind=True):
        core_types[f"{model.__module__}.{model.__qualname__}:{id(model)}"] = model


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


def _iter_schema_nodes(value: Any) -> Iterator[dict[str, Any]]:
    """Yield each schema of a type expression before its branches and items are visited."""
    if isinstance(value, dict):
        yield value
        for key, item in value.items():
            if key in _UNSERIALIZABLE_SCHEMA_KEYS:
                yield from _iter_schema_nodes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_schema_nodes(item)


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
    return any(node.get(_UNSERIALIZABLE_MARKER) for node in _iter_schema_nodes(value))


def _remove_unserializable_markers(value: Any) -> None:
    for node in _iter_schema_nodes(value):
        node.pop(_UNSERIALIZABLE_MARKER, None)
        _consume_union_branch(node)


def _process_unserializable_property(
    prop: dict[str, Any],
    annotation: object,
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
        prop["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)
        prop.pop(_UNSERIALIZABLE_MARKER, None)
    elif not _UNSERIALIZABLE_PART_KEYS.isdisjoint(prop) and _has_unserializable_schema(prop):
        prop["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)
        _remove_unserializable_markers(prop)
    elif not in_union and get_origin(annotation) is type:
        prop["x-python-type"] = _serialize_python_type_full(annotation, expression_collector)


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


def _iter_owned_properties(
    properties: dict[str, Any], model_fields: dict[str, Any]
) -> Iterator[tuple[Any, dict[str, Any]]]:
    """Pair each field with its property, skipping a property that another field of the same wire name owns.

    The owner is the field the schema marks (``InputModelJsonSchema``), else the last field of the wire name, whose
    schema Pydantic writes for the property.
    """
    wire_names = {name: _input_model_field_wire_name(name, field_info) for name, field_info in model_fields.items()}
    last_fields = {wire_name: name for name, wire_name in wire_names.items()}
    for field_name, field_info in model_fields.items():
        if (prop := properties.get(wire_name := wire_names[field_name])) is None:
            continue
        owner = prop.get(_FIELD_SCHEMA_NAME)
        if (owner.field_name if isinstance(owner, _FieldSchemaOwner) else last_fields[wire_name]) == field_name:
            yield field_info, prop


def _add_python_type_for_unserializable(
    schema: dict[str, Any],
    model: type,
    schema_types: ABCMapping[str, type | str],
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, Any]:
    """Add x-python-type to ALL fields marked as unserializable.

    The root is ``model``; a definition is the class the schema generation recorded for its name in ``schema_types``
    (``_get_input_model_json_schema_class``), whatever its kind and whatever other class shares its name. A marker no
    field is paired with is removed: it never leaves the schema. Those are the fields of a Pydantic dataclass read by
    name, not by alias, and of a generic dataclass or TypedDict, whose recorded class is the unspecialised one, with
    type variables in its annotations.
    """
    definitions = schema.get("$defs", {})
    classes = [
        (definition, definition_type)
        for name, definition in definitions.items()
        if isinstance(definition_type := schema_types.get(name), type)
        and (_model_class(definition_type) or not getattr(definition_type, "__parameters__", ()))
    ]
    for definition, definition_type in [(schema, model), *classes]:
        if properties := definition.get("properties"):
            for annotation, prop in _model_properties(properties, definition_type):
                _process_unserializable_property(prop, annotation, expression_collector)
        elif _model_class(definition_type) and definition_type.__pydantic_root_model__:
            _process_unserializable_property(
                definition, definition_type.model_fields["root"].annotation, expression_collector
            )
    for definition in (schema, *definitions.values()):
        for node in (definition, *definition.get("properties", {}).values()):
            _remove_unserializable_markers(node)
    return schema


def _serialize_python_type(
    tp: type,
    expression_collector: PythonTypeExpressionCollector | None = None,
    generic_names: dict[type, str] | None = None,
) -> str | None:
    """Serialize Python type to a string for x-python-type field."""
    expression = _preserved_python_type_expr(tp)
    if expression is not None and generic_names:
        expression = _preserved_python_type_expr(tp, generic_names)
    expression = expression or _variadic_tuple_expr(tp, generic_names)
    return _transport_python_type_expr(expression, expression_collector) if expression is not None else None


def _variadic_tuple_expr(tp: type, generic_names: dict[type, str] | None = None) -> PythonTypeExpr | None:
    """Spell a field typed as a variadic tuple, which JSON Schema writes as a list, for --use-x-python-type-as-written.

    Only the field itself is spelled: a tuple inside a union stays a list.
    """
    args = get_args(tp)
    if get_origin(tp) is not tuple or len(args) != _VARIADIC_TUPLE_ARGS or args[1] is not Ellipsis:
        return None
    item = _preserved_python_type_expr(args[0], generic_names) or _imported_python_type_expr(args[0])
    return PythonTypeSubscript(PythonTypeName("tuple"), (item, PythonTypeEllipsis()))


def _preserved_python_type_expr(tp: type, generic_names: dict[type, str] | None = None) -> PythonTypeExpr | None:  # ruff: ignore[too-many-return-statements]
    """Build IR only when JSON Schema loses relevant runtime type structure."""
    if generic_names and isinstance(tp, type) and (name := generic_names.get(tp)):
        return PythonTypeName(name)
    origin: type | None = get_origin(tp)
    args = get_args(tp)

    if origin is Union or origin is types.UnionType:
        return PythonTypeUnion(arguments) if (arguments := _with_full_fallback(args, generic_names)) else None

    if origin is Annotated:
        if args:
            return _preserved_python_type_expr(args[0], generic_names) or _imported_python_type_expr(args[0])
        return None  # pragma: no cover - not proven unreachable

    type_name: str | None = None
    if origin is not None:
        type_name = _PRESERVED_TYPE_ORIGINS.get(origin)
        if type_name is None and getattr(origin, "__module__", None) == "collections":
            type_name = _simple_type_name(origin)
    if type_name is not None:
        if arguments := _with_full_fallback(args, generic_names, optional=False):
            return PythonTypeSubscript(PythonTypeName(type_name), arguments)
        return PythonTypeName(type_name)

    if arguments := _with_full_fallback(args, generic_names):
        return PythonTypeSubscript(PythonTypeName(_simple_type_name(origin or tp)), arguments)
    return None


def _with_full_fallback(
    args: tuple[Any, ...], generic_names: dict[type, str] | None, *, optional: bool = True
) -> tuple[PythonTypeExpr, ...] | None:
    """Keep each preserved argument and spell the others with their full runtime name.

    With ``optional``, return None when no argument is preserved; otherwise resolve each argument before the next.
    """
    nested: Iterable[PythonTypeExpr | None] = (
        _preserved_python_type_expr(argument, generic_names) for argument in args
    )
    if optional:
        nested = tuple(nested)
        if all(expression is None for expression in nested):
            return None
    return tuple(
        expression or _imported_python_type_expr(argument) for expression, argument in zip(nested, args, strict=True)
    )


def _imported_python_type_expr(tp: object) -> PythonTypeExpr:
    """Spell a type of a field by its full runtime name, which the output imports, and note the classes it names."""
    if (imported := _IMPORTED_TYPES.get()) is not None:
        imported.update(node for node, _ in _annotation_nodes(tp) if _plain_class(node))
    return _runtime_python_type_expr(tp, full_name=True)


def _simple_type_name(tp: type) -> str:
    """Get a simple string representation of a type."""
    if tp is type(None):
        return "None"
    if get_origin(tp) is not None:
        return str(tp).replace("typing.", "")
    if hasattr(tp, "__name__"):
        return tp.__name__
    return str(tp).replace("typing.", "")  # pragma: no cover - not proven unreachable


def _full_type_name(tp: type) -> str:
    """Get a full qualified name representation of a type for type arguments.

    For generic types, keeps outer type as short name but FQN-izes the type arguments.
    For non-generic types, returns FQN for non-builtin types.
    """
    return render_python_type_expr(_runtime_python_type_expr(tp, full_name=True))


def _collect_nested_models(
    model: type, visited: set[type] | None = None, *, every_kind: bool = False
) -> dict[str, type]:
    """Collect by name the nested types (BaseModel, Enum, dataclass, TypedDict, msgspec Struct) a model's fields reach.

    The fields of nested models are followed; with ``every_kind`` those of dataclasses, TypedDicts and msgspec Structs
    too. Of two classes of one name the one the walk meets later stays. ``visited`` takes the model and the classes
    followed.
    """
    if visited is None:
        visited = set()
    visited.add(model)
    return {
        nested.__name__: nested
        for nested, family in _reached_classes(_model_field_annotations(model).values(), visited, every_kind=every_kind)
        if family != _TYPE_FAMILY_OTHER
    }


def _get_type_hints_safe(obj: type) -> dict[str, Any]:
    """Safely get type hints from a class, handling forward references."""
    try:
        return get_type_hints(obj)
    except Exception:  # noqa: BLE001  # pragma: no cover - get_type_hints of a class whose hints do not resolve
        return getattr(obj, "__annotations__", {})


def _pydantic_fields(model: object, *, dataclass_fields: bool = False) -> dict[str, Any] | None:
    """Return the fields Pydantic holds for a class by name, None for a class it holds none for.

    They are the ``model_fields`` of a model and, with ``dataclass_fields``, the fields of a Pydantic dataclass. A
    ``model_fields`` attribute of another class is no such fields: a dataclass may name a field of its own so.
    """
    if _model_class(model):
        return model.model_fields
    return getattr(model, "__pydantic_fields__", None) if dataclass_fields else None


def _model_field_annotations(model: type, *, raw: bool = False) -> dict[str, Any]:
    """Return the annotation of each field of a class: of the fields of a Pydantic model, else its type hints.

    ``raw`` reads the annotations as the class itself holds them, which is what a native field path evaluates: its
    own annotations only, ``Annotated`` kept, and strings under ``from __future__ import annotations``.
    """
    if (fields := _pydantic_fields(model)) is not None:
        return {name: field_info.annotation for name, field_info in fields.items()}
    return getattr(model, "__annotations__", {}) if raw else _get_type_hints_safe(model)


def _model_properties(properties: dict[str, Any], model: type) -> Iterator[tuple[type, dict[str, Any]]]:
    """Pair each field of a class with the property its schema writes, yielding the annotation and the property.

    A Pydantic model is read by its fields, a Pydantic dataclass too when the load reads dataclass fields by alias;
    any other class by its type hints, keyed by field name.
    """
    if (model_fields := _pydantic_fields(model, dataclass_fields=_READ_DATACLASS_FIELDS_BY_ALIAS.get())) is not None:
        for field_info, prop in _iter_owned_properties(properties, model_fields):
            yield field_info.annotation, prop
        return
    for field_name, annotation in _get_type_hints_safe(model).items():
        if (prop := properties.get(field_name)) is not None:
            yield annotation, prop


def _clear_field_schema_names(schema: dict[str, Any]) -> None:
    """Remove temporary owners from schema positions, including custom inlined models."""
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
    for annotation, prop in _model_properties(properties, model):
        if serialized := _serialize_python_type(annotation, expression_collector, generic_names):
            prop["x-python-type"] = serialized


def _add_python_type_info(
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


def _typed_fields_family(tp: object) -> _OutputModelFamily | None:
    """Return the family of a class with typed fields that is no Pydantic model, None for anything else.

    Such a class is a dataclass (one of Pydantic is of the Pydantic family), a TypedDict or a msgspec Struct. An
    instance of one is no class, as an instance of a Pydantic model is none.
    """
    if not isinstance(tp, type):
        return None
    if is_dataclass(tp):
        return _OutputModelFamily.PYDANTIC if hasattr(tp, "__pydantic_fields__") else _OutputModelFamily.DATACLASS
    if hasattr(tp, "__required_keys__"):
        return _OutputModelFamily.TYPEDDICT
    if hasattr(tp, "__struct_fields__"):
        return _OutputModelFamily.MSGSPEC
    return None


def _get_type_family(tp: type) -> str:
    """Determine the type family of a class: an enum, a Pydantic model, a class with typed fields or another."""
    if issubclass(tp, PyEnum):
        return _TYPE_FAMILY_ENUM
    if _model_class(tp):
        return _OutputModelFamily.PYDANTIC
    return _typed_fields_family(tp) or _TYPE_FAMILY_OTHER


def _qualified_type_export(nested_type: type) -> str | None:
    """Return the dotted qualname of a nested type when its module exports it only through its owners, else None.

    The static owner path must resolve to the same runtime type.
    """
    if "." not in (qualname := nested_type.__qualname__) or not issubclass(
        type(module := sys.modules.get(nested_type.__module__)), types.ModuleType
    ):
        return None
    namespace = vars(types.ModuleType)["__dict__"].__get__(module)
    if namespace.get(nested_type.__name__) is nested_type:
        return None
    owner_path, _, name = qualname.rpartition(".")
    parent: object = module
    try:
        for owner_name in owner_path.split("."):
            owner = namespace.get(owner_name)
            if not issubclass(type(owner), type) or getattr(parent, owner_name, None) is not owner:
                return None
            parent = owner
            namespace = type.__dict__["__dict__"].__get__(owner)
        return qualname if namespace.get(name) is nested_type and getattr(parent, name, None) is nested_type else None
    except Exception:  # ruff: ignore[blind-except]
        # A user attribute hook may reject this optional path while the short export still works.
        return None


def _pydantic_generic_metadata(tp: object) -> dict[str, Any] | None:
    """Read specialization metadata without executing a metaclass attribute hook."""
    if _plain_class(tp):
        metadata = type.__dict__["__dict__"].__get__(tp).get("__pydantic_generic_metadata__")
        if metadata and metadata.get("origin") is not None:
            return metadata
    return None


def _reuses_pydantic_generics(strategy: InputModelRefStrategy | None, family: _OutputModelFamily | None) -> bool:
    """Apply specialization references only when Pydantic types are reused."""
    return strategy == InputModelRefStrategy.ReuseAll or (
        strategy == InputModelRefStrategy.ReuseForeign and family == _OutputModelFamily.PYDANTIC
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
    nested = _collect_nested_models(model, visited, every_kind=_IMPORTED_TYPES.get() is not None)
    identities = (
        {}
        if (strategy is InputModelRefStrategy.ReuseAll and nested)
        or (_reuses_pydantic_generics(strategy, family) and any(_pydantic_generic_metadata(item) for item in visited))
        else None
    )
    return nested, identities


def _add_reused_definitions(
    nested_models: dict[str, type] | None,
    definition_types: dict[str, type | str] | None,
    *,
    identify: bool = False,
) -> None:
    """Let reuse find the specialized types (and, when identifying, the same-named ones) of the definitions.

    Call it before another schema is generated: that refills ``definition_types``.
    """
    if nested_models is None or definition_types is None:
        return
    nested_models.update(
        (name, cast("type", model)) for name, model in definition_types.items() if _pydantic_generic_metadata(model)
    )
    if identify and (identified_types := _colliding_definition_types(definition_types)):
        nested_models.update(identified_types)


def _identify_reused_definitions(
    nested_models: dict[str, type] | None,
    schema_types: ABCMapping[str, type | str],
    merged_names: AbstractSet[str],
    renames: ABCMapping[str, str] | None = None,
) -> None:
    """Make each definition of the last schema that reuse finds by name the class the generation recorded for it.

    ``schema_types`` holds that class by identity, where the names hold the last class of a name the fields reach. A
    definition that Pydantic merged from distinct classes (``merged_names``) names no one class and keeps the name's.
    A definition renamed by its class (``renames``) is found by its new name.
    """
    if nested_models is not None:
        renames = renames or {}
        nested_models.update(
            (renames.get(name, name), model)
            for name, model in schema_types.items()
            if (name in nested_models or name in renames) and name not in merged_names and isinstance(model, type)
        )


def _reuses_type(
    nested_type: type, strategy: InputModelRefStrategy | None, output_family: _OutputModelFamily | None
) -> bool:
    """Return whether a reuse strategy imports a reached type instead of generating it.

    ReuseForeign reuses enums and the types of the output family, and under --reuse-every-reached-input-model-type
    also a type the Python type of a field names by its import.
    """
    return (
        strategy is InputModelRefStrategy.ReuseAll
        or _get_type_family(nested_type) in {_TYPE_FAMILY_ENUM, output_family}
        or ((imported := _IMPORTED_TYPES.get()) is not None and nested_type in imported)
    )


def _allocate_reused_definitions(
    schema: dict[str, object],
    *,
    definitions: _InputModelDefinitions,
    nested_models: dict[str, type],
    schema_generator: type[GenerateJsonSchema],
    reuses: Callable[[type], bool],
) -> dict[str, object]:
    """Name the imported definitions of one schema of a model and its parents by their class, then identify them.

    Two classes of one name that reuse imports get distinct definition names across the schemas
    (``_InputModelDefinitions``), so equal references name one class and an inherited field is one of the same class.
    A generated definition keeps its name, which is a class name of the output. ``reuses`` says whether reuse imports
    a class (``_reuses_type``).
    """
    merged_names = getattr(schema_generator, "merged_definition_names", frozenset())

    def imported(name: str, model: type | str) -> bool:
        return name in nested_models and isinstance(model, type) and reuses(model)

    schema, renames = definitions.identify(
        schema,
        lambda name, model: (
            name not in merged_names and imported(name, model) and imported(name, definitions.owners.get(name, model))
        ),
    )
    _identify_reused_definitions(nested_models, definitions.current_types, merged_names, renames)
    return schema


def _reused_python_type_expression(tp: object) -> PythonTypeExpr:
    """Retain specialized models and the actual export paths of their arguments."""
    if metadata := _pydantic_generic_metadata(tp):
        return PythonTypeSubscript(
            _reused_python_type_expression(metadata["origin"]),
            tuple(_reused_python_type_expression(argument) for argument in metadata["args"]),
        )
    expression = _runtime_python_type_expr(tp)
    if isinstance(tp, type) and isinstance(expression, PythonTypeRuntimeSymbol):
        parts = tuple(qualname.split(".")) if (qualname := _qualified_type_export(tp)) else (tp.__name__,)
        return PythonTypeRuntimeSymbol(tp.__module__, parts)
    replacements: dict[PythonTypeExpr, PythonTypeExpr] = {}
    arguments = _annotation_nodes(tp)
    next(arguments)  # the type itself
    for argument, _ in arguments:
        # Of two arguments that are spelled alike, the one written first gives the replacement.
        if isinstance(argument, type):
            replacements.setdefault(_runtime_python_type_expr(argument), _reused_python_type_expression(argument))
        elif isinstance(argument, PyEnum):
            owner = _reused_python_type_expression(type(argument))
            if not isinstance(owner, PythonTypeRuntimeSymbol):
                msg = (
                    f"Cannot safely reuse enum member {stable_repr(argument)}: no importable type expression. Use "
                    "regenerate-all."
                )
                raise Error(msg)
            replacements.setdefault(
                PythonTypeOpaqueText(str(argument)),
                PythonTypeRuntimeSymbol(owner.module, (*owner.qualname_parts, argument.name)),
            )
    return rewrite_python_type_expr(expression, lambda item: replacements.get(item, item))


def _has_annotated_generic_argument(tp: object) -> bool:
    """Detect arguments whose metadata cannot be reconstructed by the type serializer."""
    if get_origin(tp) is Annotated:
        return True
    metadata = _pydantic_generic_metadata(tp)
    return any(_has_annotated_generic_argument(arg) for arg in (metadata["args"] if metadata else get_args(tp)))


def _native_generic_field_paths(models: Sequence[type]) -> dict[type, dict[str, Any]]:
    """Capture only observed native model_fields paths, retaining exact specialization identity.

    A path is what generated code evaluates, so a class is read as that code reads it: by its raw annotations. The
    first path found stays: a class's own fields are read before those of the classes they name, the fields in their
    order and each field from its last argument to its first (row legacy-native-path-last-argument-first), and of
    the classes a class names the one found last is read first.
    """
    paths: dict[type, dict[str, Any]] = {}

    def visit(item: tuple[type, dict[str, Any]]) -> list[tuple[type, dict[str, Any]]]:
        model, path = item
        found: list[tuple[type, dict[str, Any]]] = []
        pydantic = _pydantic_fields(model) is not None
        for name, field_annotation in _model_field_annotations(model, raw=True).items():
            for annotation, indexes in reversed(tuple(_annotation_nodes(field_annotation))):
                if _plain_class(annotation):
                    field_path = {
                        **path,
                        "fields": [*path["fields"], {"name": name, "arguments": indexes, "pydantic": pydantic}],
                    }
                    if _pydantic_generic_metadata(annotation):
                        paths.setdefault(annotation, field_path)
                    found.append((annotation, field_path))
        return found

    reach(
        [
            (model, {"module": symbol.module, "qualname": ".".join(symbol.qualname_parts), "fields": []})
            for model in models
            if (symbol := _exported_runtime_symbol(model, [model.__module__])) is not None
        ],
        visit,
        key=itemgetter(0),
    )
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
    if _plain_class(tp) and (qualname := _qualified_type_export(tp)):
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
    if (resolved := all_or_none(arguments)) is None:
        return None
    if metadata:
        return PythonTypeSubscript(_reused_python_type_expression(metadata["origin"]), resolved)
    if isinstance(expression, PythonTypeUnion):
        return PythonTypeUnion(resolved)
    return PythonTypeSubscript(cast("PythonTypeSubscript", expression).base, resolved)


def _reused_definition(module: str, name: str, **fields: Any) -> dict[str, Any]:
    """Return the definition that reuses an importable type: its module and name, then how to locate or spell it."""
    return {"x-python-import": {"module": module, "name": name, **fields}}


def _reused_generic_definition(
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
    return _reused_definition(
        origin.__module__,
        origin.__name__,
        **{"x-python-type": _transport_python_type_expr(expression, expression_collector), "symbols": symbols},
    )


def _filter_defs_by_strategy(  # ruff: ignore[too-many-positional-arguments]
    schema: dict[str, Any],
    nested_models: dict[str, type],
    output_family: _OutputModelFamily | None,
    strategy: InputModelRefStrategy,
    expression_collector: PythonTypeExpressionCollector | None = None,
    model_classes: Sequence[type] | None = None,
) -> dict[str, Any]:
    """Filter $defs of a reuse strategy, marking reused types with x-python-import.

    Only ReuseForeign has an output family; it reuses enums and the types of that family, and under
    --reuse-every-reached-input-model-type also a type the Python type of a field names by its import, of which the
    output would otherwise hold an unused copy.
    """
    if "$defs" not in schema:
        return schema

    new_defs: dict[str, Any] = {}
    native_paths = None

    for def_name, def_schema in schema["$defs"].items():
        if def_name not in nested_models:
            new_defs[def_name] = def_schema
            continue

        nested_type = nested_models[def_name]

        if _reuses_type(nested_type, strategy, output_family):
            if metadata := _pydantic_generic_metadata(nested_type):
                if (expression := _exported_generic_expression(nested_type, None)) is None:
                    if native_paths is None:
                        native_paths = _native_generic_field_paths(model_classes or list(nested_models.values()))
                    if path := native_paths.get(nested_type):
                        new_defs[def_name] = _reused_definition(
                            path["module"], path["qualname"].split(".")[0], **{"native-field": path}
                        )
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
                new_defs[def_name] = _reused_generic_definition(metadata["origin"], expression_collector, expression)
                continue
            qualname = _qualified_type_export(nested_type)
            new_defs[def_name] = _reused_definition(
                nested_type.__module__, nested_type.__name__, **({"qualname": qualname} if qualname else {})
            )
        else:
            new_defs[def_name] = def_schema

    return {**schema, "$defs": new_defs}


_CONFIG_MODULE_MODELS = (
    frozenset({"GenerateConfig", "ParserConfig", "ParseConfig"}),
    ("DataModel", "DataModelFieldBase", "DataTypeManager", "StrictTypes", "UnionMode"),
)
_CONFIG_MODEL_NAMES: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "datamodel_code_generator.config": _CONFIG_MODULE_MODELS,
    "config": _CONFIG_MODULE_MODELS,
    "datamodel_code_generator.__main__": (frozenset({"Config"}), ("UnionMode", "StrictTypes")),
}
"""The deferred config models of each module and the config type names they are rebuilt with."""


def _try_rebuild_model(obj: type) -> None:
    """Try to rebuild a Pydantic model, handling config models specially."""
    classes, names = _CONFIG_MODEL_NAMES.get(getattr(obj, "__module__", ""), (frozenset(), ()))
    if getattr(obj, "__name__", "") in classes:
        from datamodel_code_generator.config import _config_types_namespace, _rebuild_config_model  # noqa: PLC0415

        _rebuild_config_model(obj, _config_types_namespace(*names))  # ty: ignore[invalid-argument-type]
    else:
        obj.model_rebuild()  # ty: ignore[unresolved-attribute]


def _model_schema_with_python_types(
    model: type[BaseModel],
    schema_generator: type[GenerateJsonSchema],
    expression_collector: PythonTypeExpressionCollector | None,
    generic_definitions: dict[str, type | str] | None,
    schema_types: dict[str, type | str],
) -> dict[str, Any]:
    """Generate a model's JSON Schema and add the Python types it cannot express."""
    schema = _add_python_type_for_unserializable(
        model.model_json_schema(schema_generator=schema_generator), model, schema_types, expression_collector
    )
    return _add_python_type_info(
        schema,
        model,
        expression_collector,
        generic_definitions,
        schema_types,
        clear_field_names=getattr(schema_generator, "has_field_schema_owners", False),
    )


def _get_base_model_parents(model_class: type) -> list[type[BaseModel]]:
    """Get parent classes that are BaseModel subclasses (excluding BaseModel itself)."""
    return [p for p in model_class.__bases__ if _model_class(p) and p is not BaseModel]


def _partition_inherited_fields(
    schema: dict[str, object],
    model_class: type[BaseModel],
    parent_fields: dict[str, Any],
    parent: _ParentSchema,
    released_schema: dict[str, object],
) -> tuple[dict[str, object], list[str], dict[str, str]]:
    """Keep effective replacements separate from unchanged inherited properties.

    ``released_schema`` and the last item of ``parent`` are the schemas of the model and of the parent as 0.100.0
    named their definitions, before reuse named them by class (``_allocate_reused_definitions``). A field they name by
    different definitions, which Pydantic does where the model reaches another class of the name, stays redeclared as
    in 0.100.0, so the module reaches the classes in the same order and binds each plain import name to the same class.
    """
    _, parent_properties, parent_required, released_parent_schema = parent
    original_props, released_props, released_parent_props = (
        cast("dict[str, object]", properties.get("properties", {}))
        for properties in (schema, released_schema, released_parent_schema)
    )
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
            or released_props.get(child_wire) != released_parent_props.get(parent_wire)
            or (child_wire in required_names) != (parent_wire in parent_required)
        ):
            overrides[child_wire] = parent_wire
        else:
            inherited_names.add(child_wire)
    inherited_names -= overrides.keys()
    return (
        {k: v for k, v in original_props.items() if k not in inherited_names},
        [name for name in original_required if name not in inherited_names],
        overrides,
    )


def _transform_single_model_to_inheritance(  # noqa: PLR0917
    schema: dict[str, object],
    model_class: type[BaseModel],
    schema_generator: type[GenerateJsonSchema],
    processed_parents: dict[type[BaseModel], _ParentSchema] | None = None,
    expression_collector: PythonTypeExpressionCollector | None = None,
    definitions: _InputModelDefinitions | None = None,
    generic_definitions: dict[str, type | str] | None = None,
    *,
    schema_types: dict[str, type | str],
    identify_definitions: Callable[[dict[str, object]], dict[str, object]] | None = None,
    released_schema: dict[str, object] | None = None,
) -> dict[str, object]:
    """Transform a single model's schema to use allOf inheritance structure.

    ``identify_definitions`` names the definitions of each parent's schema as the root's are named;
    ``released_schema`` is ``schema`` before that (``_partition_inherited_fields``), ``schema`` when it is not given.
    """
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
        parent_schema = _model_schema_with_python_types(
            parent, schema_generator, expression_collector, generic_definitions, schema_types
        )
        released_parent_schema = parent_schema
        if identify_definitions is not None:
            parent_schema = identify_definitions(parent_schema)
        if definitions is not None:
            parent_schema = released_parent_schema = definitions.normalize(parent_schema, parent)
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
            schema_types=schema_types,
            identify_definitions=identify_definitions,
            released_schema=released_parent_schema,
        )
        processed_parents[parent] = parent_schema, parent_properties, parent_required, released_parent_schema
    parent_schema = processed_parents[parent][0]

    if "$defs" in parent_schema:
        parent_defs = cast("dict[str, object]", parent_schema["$defs"])
        defs.update(parent_defs)

    parent_def = {k: v for k, v in parent_schema.items() if k != "$defs"}
    parent_def["x-is-base-class"] = True
    defs[parent_name] = parent_def

    child_props, child_required, overrides = _partition_inherited_fields(
        schema,
        model_class,
        parent.model_fields,
        processed_parents[parent],
        schema if released_schema is None else released_schema,
    )

    new_schema: dict[str, object] = {"$defs": defs, "allOf": [{"$ref": definition_ref("$defs", parent_name)}]}
    if child_props:
        new_schema["properties"] = child_props
    if child_required:
        new_schema["required"] = child_required
    new_schema["title"] = schema.get("title")
    new_schema["type"] = "object"
    if overrides:
        new_schema["allOf"] = PythonModelBases(definition_ref("$defs", parent_name), overrides)

    new_schema.update({
        key: value
        for key, value in schema.items()
        if key not in {"$defs", "properties", "required", "title", "type", "allOf"}
    })

    return new_schema


class _InputModelDefinitions:
    """Allocate merged definition names by runtime identity, preserving ordinary names."""

    def __init__(self, models: list[type[BaseModel]], current_types: dict[str, type | str] | None = None) -> None:
        self.current_types: dict[str, type | str] = {} if current_types is None else current_types
        self.nested_types: dict[str, type] = {}
        self.names: dict[type | str, str] = {}
        self.owners: dict[str, type | str] = {}
        self.next_positions: dict[str, int] | None = None
        self.reserved = {model.__name__ for model in models}
        for model in models:
            self.reserved.update(_collect_nested_models(model))
            self.reserved.update(parent.__name__ for parent in model.__mro__)

    def name(self, proposed: str, model: type | str) -> str:
        if name := self.names.get(model):
            return name
        name = proposed
        if name in self.owners:
            if self.next_positions is None:
                self.next_positions = {}
            name, position = first_free(
                proposed,
                lambda name: name in self.owners or name in self.reserved,
                "underscore-from-2",
                start=self.next_positions.get(proposed, 1),
            )
            # Owners and reservations only grow, so earlier suffixes stay occupied.
            self.next_positions[proposed] = position + 1
        self.names[model] = name
        self.owners[name] = model
        return name

    def identify(
        self, schema: dict[str, object], allocates: Callable[[str, type | str], bool] | None = None
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Name the definitions of a schema by their runtime type and rewrite the references of the renamed ones.

        ``allocates`` picks the definitions named so (all by default); the others keep their name. Returns the schema
        and the renames.
        """
        if not (isinstance(defs := schema.get("$defs"), dict) and defs):
            return schema, {}
        self.reserved.update(defs)
        renames: dict[str, str] = {}
        for old_name in defs:
            if (model := self.current_types.get(old_name)) and (allocates is None or allocates(old_name, model)):
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

            rewritten = _rewrite_schema_refs(schema, renames, set())
            rewritten["$defs"] = {renames.get(name, name): value for name, value in rewritten["$defs"].items()}
            return rewritten, renames
        return schema, renames

    def normalize(self, schema: dict[str, Any], model_class: type) -> dict[str, Any]:
        if not schema.get("$defs"):
            return schema
        schema, _ = self.identify(schema)
        if "$ref" in schema or "allOf" in schema:
            name = self.name(model_class.__name__, model_class)
            root_ref = {"$ref": definition_ref("$defs", name)}
            wrapper = {key: value for key, value in schema.items() if key != "$defs"}
            if wrapper in (root_ref, {"allOf": [root_ref]}) and isinstance(defs := schema["$defs"], dict):
                # A recursive root's wrapper must not overwrite its real definition.
                schema = {**defs[name], "$defs": defs}
        return schema


def load_model_schema(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None = None,
    output_model_type: DataModelType | None = None,
    *,
    read_dataclass_fields_by_alias: bool = False,
    reuse_every_reached_type: bool = False,
) -> dict[str, object]:
    """Load and merge schemas from 'module.path:ObjectName' import paths with inheritance support.

    Several import paths merge into one schema whose anyOf references every root model.
    ``read_dataclass_fields_by_alias`` gives an aliased field of a Pydantic dataclass its Python type too.
    ``reuse_every_reached_type`` makes a reuse strategy reach types through the fields of every model kind and reuse a
    type the Python type of a field names by its import instead of writing a copy of it.
    """
    return _load_model_schema_isolated(
        input_models,
        input_file_type,
        ref_strategy,
        output_model_type,
        read_dataclass_fields_by_alias=read_dataclass_fields_by_alias,
        reuse_every_reached_type=reuse_every_reached_type,
    )


def _load_model_schema_with_python_type_expressions(
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None = None,
    output_model_type: DataModelType | None = None,
    *,
    read_dataclass_fields_by_alias: bool = False,
    reuse_every_reached_type: bool = False,
) -> LoadedInputModelSchema:
    """Load the CLI-only schema transport without rendering runtime expressions."""
    expression_collector = PythonTypeExpressionCollector()
    schema = _load_model_schema_isolated(
        input_models,
        input_file_type,
        ref_strategy,
        output_model_type,
        expression_collector,
        read_dataclass_fields_by_alias=read_dataclass_fields_by_alias,
        reuse_every_reached_type=reuse_every_reached_type,
    )
    return expression_collector.loaded_schema(schema)


def _load_config_input_model(config: Any) -> LoadedInputModelSchema:
    """Load the ``--input-model`` schema of a command-line configuration, with the settings that change how it loads.

    An error loading it is a ``datamodel_code_generator.Error``.
    """
    try:
        return _load_model_schema_with_python_type_expressions(
            config.input_model,
            config.input_file_type,
            config.input_model_ref_strategy,
            config.output_model_type,
            read_dataclass_fields_by_alias=config.read_input_model_dataclass_fields_by_alias,
            reuse_every_reached_type=config.reuse_every_reached_input_model_type,
        )
    except Error as e:
        raise GenerationError(str(e)) from e


def _load_model_schema(  # noqa: PLR0914
    input_models: list[str],
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_model_type: DataModelType | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, object]:
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
            _, obj = _load_input_model_object(input_model, loaded_modules, path_module_states)
            if not _model_class(obj):
                msg = f"Multiple --input-model only supports Pydantic v2 BaseModel classes, got {type(obj).__name__}"
                raise Error(msg)
            model_classes.append(obj)

        _require_json_schema_input(input_file_type, "Pydantic models")
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
            schema = _model_schema_with_python_types(
                model_class, schema_generator, expression_collector, generic_definitions, definitions.current_types
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
                schema_types=definitions.current_types,
            )

            if "$defs" in schema:
                schema_defs = cast("dict[str, object]", schema["$defs"])
                for k, v in schema_defs.items():
                    if k not in merged_defs or (
                        _is_input_model_base_schema(v) and not _is_input_model_base_schema(merged_defs[k])
                    ):
                        merged_defs[k] = v

            model_def = {k: v for k, v in schema.items() if k != "$defs"}
            merged_defs[model_name] = model_def

            root_refs.append({"$ref": definition_ref("$defs", model_name)})

        final_schema: dict[str, object] = {"$defs": merged_defs, "anyOf": root_refs}

        if ref_strategy and ref_strategy != InputModelRefStrategy.RegenerateAll:
            final_schema = _filter_defs_by_strategy(
                final_schema, definitions.nested_types, output_family, ref_strategy, expression_collector, model_classes
            )

        return final_schema
    finally:
        for state in reversed(path_module_states):
            _restore_path_module(state)


def _load_single_model_schema(
    input_model: str,
    input_file_type: InputFileType,
    ref_strategy: InputModelRefStrategy | None,
    output_family: _OutputModelFamily | None,
    expression_collector: PythonTypeExpressionCollector | None = None,
) -> dict[str, object]:
    """Load the schema of one dict, Pydantic model, dataclass or TypedDict import path."""
    path_module_states: list[_ModuleRestoreState] = []
    try:
        qualname, obj = _load_input_model_object(input_model, {}, path_module_states)

        if isinstance(obj, dict):
            if input_file_type == InputFileType.Auto:
                msg = "--input-file-type is required when --input-model points to a dict"
                raise Error(msg)
            if expression_collector is None:
                return obj
            # Dict schemas contain no runtime IR. Preserve the former CLI JSON
            # round trip exactly: it both isolates module state and performs
            # JSON key coercion/validation before parser construction.

            return cast("dict[str, object]", json.loads(json.dumps(obj)))

        model = cast("type", obj)
        if _model_class(obj):
            _require_json_schema_input(input_file_type, "a Pydantic model")
            _try_rebuild_model(obj)
            nested_models, definition_types = _prepare_reused_model_types(obj, ref_strategy, output_family)
            schema_types = definition_types if definition_types is not None else {}
            schema_generator = _get_input_model_json_schema_class(
                schema_types,
                [obj],
                preserve_type_identity=ref_strategy is InputModelRefStrategy.ReuseAll,
                preserve_generic_identity=_reuses_pydantic_generics(ref_strategy, output_family),
            )
            schema = _model_schema_with_python_types(
                obj, schema_generator, expression_collector, definition_types, schema_types
            )
            _add_reused_definitions(nested_models, definition_types, identify=True)
            released_schema = schema
            identify_definitions = (
                None
                if nested_models is None
                else partial(
                    _allocate_reused_definitions,
                    definitions=_InputModelDefinitions([obj], schema_types),
                    nested_models=nested_models,
                    schema_generator=schema_generator,
                    reuses=partial(_reuses_type, strategy=ref_strategy, output_family=output_family),
                )
            )
            if identify_definitions is not None:
                schema = identify_definitions(schema)
            schema = _transform_single_model_to_inheritance(
                schema,
                obj,
                schema_generator,
                expression_collector=expression_collector,
                generic_definitions=definition_types,
                schema_types=schema_types,
                identify_definitions=identify_definitions,
                released_schema=released_schema,
            )
        elif _typed_fields_family(obj) not in {None, _OutputModelFamily.MSGSPEC}:
            _require_json_schema_input(input_file_type, "a dataclass or TypedDict")
            from pydantic import TypeAdapter  # noqa: PLC0415

            nested_models, definition_types = _prepare_reused_model_types(model, ref_strategy, output_family)
            schema_types = definition_types if definition_types is not None else {}
            schema_generator = _get_input_model_json_schema_class(
                schema_types,
                [model],
                use_standard_schema=definition_types is None,
                preserve_generic_identity=definition_types is not None,
            )
            schema = TypeAdapter(model).json_schema(schema_generator=schema_generator)
            schema = _add_python_type_info(schema, model, expression_collector, definition_types, schema_types)
            _add_reused_definitions(nested_models, definition_types)
            _identify_reused_definitions(
                nested_models, schema_types, getattr(schema_generator, "merged_definition_names", frozenset())
            )
        else:
            msg = (
                f"{stable_repr(qualname)} is not a supported type. "
                "Supported: dict, Pydantic v2 BaseModel, dataclass, TypedDict"
            )
            raise Error(msg)

        if ref_strategy and nested_models is not None:
            model_name = getattr(obj, "__name__", None)
            defs = cast("dict[str, object]", schema.get("$defs", {}))
            if model_name and model_name in defs:
                nested_models[model_name] = model
            schema = _filter_defs_by_strategy(
                schema, nested_models, output_family, ref_strategy, expression_collector, [model]
            )
        return schema
    finally:
        for state in path_module_states:
            _restore_path_module(state)
