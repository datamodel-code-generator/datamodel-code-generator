"""Helpers that passes of more than one layer call, as functions of explicit arguments.

A helper that reads parser state takes it as arguments, or takes the parser through one of the narrow protocols below,
which ``Parser`` satisfies structurally. This module imports no parser module but the generation store, the import
ledger, the finality checks, the model graph ids, the shared keys and the run state, so it never imports
``parser/base.py``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple, TypeAlias, cast

from typing_extensions import Protocol, final

from datamodel_code_generator import AllOfMergeMode
from datamodel_code_generator._constraint_applicability import TYPE_DEPENDENT_CONSTRAINTS_KEY
from datamodel_code_generator._graph import stable_toposort
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, reference_graph
from datamodel_code_generator._target_unicode import target_pattern
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model._decisions.tag_value import (
    enum_tag_literal,
    enum_tag_members,
    field_tag,
    mapped_tags,
    tag_field_kept,
    tag_field_matches,
)
from datamodel_code_generator.model.base import (
    BaseClassDataType,
    ConstraintsBase,
    DataModel,
    _find_base_classes,
    _keyed_answer,
    _ModelKeys,
    _path_identity_keys,
    direct_base_models,
    get_inherited_fields,
    linearize_data_models,
)
from datamodel_code_generator.model.enum import Enum, get_raw_enum_member_value
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._check_scopes import record_ordering_inputs
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _DERIVED_FROM_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    _RAW_SCHEMA_DEFAULT_UNDEFINED,
)
from datamodel_code_generator.parser._passes._state import RecursionFacts, _ModuleNames
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    rewrite_runtime_expressions,
    rewrite_runtime_imports,
)
from datamodel_code_generator.reference import ModelType, _source_path
from datamodel_code_generator.types import DataType, DefaultValueRecipe

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._decisions.tag_value import EnumTagMembers, TagFieldMatch
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._null_facts import NullRule
    from datamodel_code_generator.parser._passes._state import (
        ModuleContextView,
        ModulePath,
        _FinalizeState,
        _RenamedType,
    )
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataTypeManager, DefaultValueDescriptor

_SHADOWED_IMPORTS: Final = ImportProducer("shadowed-imports")


@record
class _InheritedTypeModifiers:
    """Compact deferred state for a partial inherited field."""

    null_rule: NullRule | None
    is_optional: bool
    is_dict: bool
    is_list: bool
    is_set: bool
    is_frozen_set: bool
    is_mapping: bool
    is_sequence: bool
    is_tuple: bool
    tuple_item_count: int | None
    kwargs: dict[str, Any] | None
    list_wrapper: DataType | None
    drops_null_members: bool = False

    @property
    def excludes_null(self) -> bool:
        """Return whether the overriding schema drops the inherited null."""
        return self.null_rule is not None


def iter_models_field_data_types(
    models: Iterable[DataModel],
) -> Iterator[tuple[DataModel, DataModelFieldBase, DataType]]:
    """Yield (model, field, data_type) for all models, fields, and nested data types."""
    for model in models:
        for field in model.fields:
            for data_type in field.data_type.all_data_types:
                yield model, field, data_type


_PythonTypeImportKey: TypeAlias = tuple[str | None, str]


def _ordinary_field_shadow_aliases(
    models: list[DataModel],
    all_model_field_names: set[str],
) -> tuple[dict[_PythonTypeImportKey, Import], bool, bool]:
    """Run the narrow fast path and report whether structured imports exist."""
    aliases: dict[_PythonTypeImportKey, Import] = {}
    has_python_type = False
    has_runtime_expressions = False
    for model in models:
        for field in model.fields:
            if not has_runtime_expressions and field.runtime_expression_imports:
                has_runtime_expressions = True
            for data_type in field.data_type.all_data_types:
                if data_type.python_type:
                    has_python_type = True
                if not has_runtime_expressions and data_type.runtime_expression_imports:
                    has_runtime_expressions = True
                if data_type.import_ and data_type.type in all_model_field_names:
                    key = (data_type.import_.from_, data_type.import_.import_)
                    aliases.setdefault(
                        key,
                        Import(
                            from_=data_type.import_.from_,
                            import_=data_type.import_.import_,
                            alias=f"{data_type.type}_aliased",
                            reference_path=data_type.import_.reference_path,
                        ),
                    )
    return aliases, has_python_type, has_runtime_expressions


def _apply_structured_import_aliases(
    builder: GraphBuilder,
    models: list[DataModel],
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Rewrite every identity-carrying structured consumer of selected imports."""
    for model in models:
        for field in model.fields:
            _alias_field_runtime_expressions(builder, field, aliased_imports)
            for data_type in field.data_type.all_data_types:
                _alias_data_type_structured_imports(builder, data_type, aliased_imports)
        _alias_additional_imports(model, aliased_imports)
    for model in models:
        _alias_base_class_imports(model, aliased_imports)


def _alias_field_runtime_expressions(
    builder: GraphBuilder,
    field: DataModelFieldBase,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Rewrite a field only when its producer registered a runtime expression."""
    imports = field.runtime_expression_imports
    if not imports or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports:
        return
    builder.set_field_default(field, rewrite_runtime_expressions(field.default, aliased_imports))
    builder.set_field_extras(field, cast("dict[str, Any]", rewrite_runtime_expressions(field.extras, aliased_imports)))
    field._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001


def _alias_data_type_structured_imports(
    builder: GraphBuilder,
    data_type: DataType,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Apply aliases to type, annotation, and producer-registered kwargs expressions."""
    if (
        data_type.import_
        and (aliased_import := aliased_imports.get((data_type.import_.from_, data_type.import_.import_))) is not None
        and aliased_import is not data_type.import_
    ):
        builder.set_type_expressions(data_type, type_=aliased_import.alias, import_=aliased_import)
    if data_type.python_type:
        alias_bound_python_type, render_python_type_expr = _python_type_import_alias_helpers()
        bound_type = alias_bound_python_type(data_type.python_type, aliased_imports)
        if bound_type is not data_type.python_type:
            builder.set_type_expressions(
                data_type, type_=render_python_type_expr(bound_type.expression), python_type=bound_type
            )
    if (
        not (imports := data_type.runtime_expression_imports)
        or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports
    ):
        return
    builder.set_type_expressions(
        data_type,
        kwargs=cast("dict[str, Any]", rewrite_runtime_expressions(data_type.kwargs, aliased_imports)),
        runtime_expression_imports=rewritten_imports,
    )


@cache
def _python_type_import_alias_helpers() -> tuple[Any, Any]:
    """Load annotation alias helpers only for the structured annotation path."""
    from datamodel_code_generator._python_type_annotation import render_python_type_expr  # noqa: PLC0415
    from datamodel_code_generator._python_type_binding import alias_bound_python_type  # noqa: PLC0415

    return alias_bound_python_type, render_python_type_expr


def _unwrap_type_alias(data_type: DataType) -> DataType:
    """Follow type alias references to the underlying data type."""
    current = data_type
    seen: set[int] = set()
    while current.reference and isinstance(current.reference.source, TypeAliasBase):
        source = current.reference.source
        if id(source) in seen or not source.fields:
            break
        seen.add(id(source))
        current = source.fields[0].data_type
    return current


def _is_default_value_container(data_type: DataType) -> bool:
    """Return whether a data type wraps a collection default rather than one scalar."""
    return any((
        data_type.is_dict,
        data_type.is_list,
        data_type.is_set,
        data_type.is_frozen_set,
        data_type.is_mapping,
        data_type.is_sequence,
        data_type.is_tuple,
    ))


def _unwrap_default_scalar_data_type(data_type: DataType) -> DataType:
    """Unwrap aliases and nullable scalar wrappers without traversing unions or containers."""
    if data_type.reference and isinstance(data_type.reference.source, TypeAliasBase):
        data_type = _unwrap_type_alias(data_type)
    while (
        data_type.type is None
        and data_type.reference is None
        and len(data_type.data_types) == 1
        and not _is_default_value_container(data_type)
        and not data_type.literals
        and not data_type.enum_member_literals
    ):
        data_type = data_type.data_types[0]
        if data_type.reference and isinstance(data_type.reference.source, TypeAliasBase):
            data_type = _unwrap_type_alias(data_type)
    return data_type


def _resolve_default_scalar_data_type(data_type: DataType) -> DataType | None:
    """Resolve one backend-neutral scalar leaf without traversing unions or containers."""
    if _is_default_value_container(data_type):
        return None
    if data_type.reference or data_type.data_types:
        data_type = _unwrap_default_scalar_data_type(data_type)
    if (
        data_type.reference
        or data_type.data_types
        or _is_default_value_container(data_type)
        or data_type.literals
        or data_type.enum_member_literals
    ):
        return None
    return data_type


def _alias_base_class_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> None:
    """Apply aliased imports to a model's base classes."""
    for base_class in model.base_classes:
        if not base_class.import_:
            continue
        key = (base_class.import_.from_, base_class.import_.import_)
        if key not in aliased_imports:
            continue
        aliased_import = aliased_imports[key]
        if aliased_import is base_class.import_:
            continue
        base_class.type = aliased_import.alias
        base_class.import_ = aliased_import


def _alias_additional_imports(
    model: DataModel,
    aliased_imports: dict[tuple[str | None, str], Import],
) -> None:
    """Replace every additional import with the module's canonical identity binding."""
    for index, import_ in enumerate(model._additional_imports):  # noqa: SLF001
        if (
            aliased_import := aliased_imports.get((import_.from_, import_.import_))
        ) is None or aliased_import is import_:
            continue
        model._additional_imports[index] = aliased_import  # noqa: SLF001


def relative(
    current_module: str,
    reference: str,
    *,
    reference_is_module: bool = False,
    current_is_init: bool = False,
) -> tuple[str, str]:
    """Find relative module path.

    Args:
        current_module: Current module path (e.g., "foo.bar")
        reference: Reference path (e.g., "foo.baz.ClassName" or "foo.baz" if reference_is_module)
        reference_is_module: If True, treat reference as a module path (not module.class)
        current_is_init: If True, treat current_module as a package __init__.py (adds depth)

    Returns:
        Tuple of (from_path, import_name) for constructing import statements
    """
    if current_is_init:
        current_module_path = [*current_module.split("."), "__init__"] if current_module else ["__init__"]
    else:
        current_module_path = current_module.split(".") if current_module else []

    if reference_is_module:
        reference_path = reference.split(".") if reference else []
        name = reference_path[-1] if reference_path else ""
    else:
        *reference_path, name = reference.split(".")

    if current_module_path == reference_path:
        return "", ""

    i = 0
    for x, y in zip(current_module_path, reference_path, strict=False):
        if x != y:
            break
        i += 1

    left = "." * (len(current_module_path) - i)
    right = ".".join(reference_path[i:])

    if not left:
        left = "."
    if not right:
        right = name
    elif "." in right:
        extra, right = right.rsplit(".", 1)
        left += extra

    return left, right


def is_ancestor_package_reference(current_module: str, reference: str) -> bool:
    """Check if reference is in an ancestor package (__init__.py).

    When the reference's module path is an ancestor (prefix) of the current module,
    the reference is in an ancestor package's __init__.py file.

    Args:
        current_module: The current module path (e.g., "v0.mammal.canine")
        reference: The full reference path (e.g., "v0.Animal")

    Returns:
        True if the reference is in an ancestor package, False otherwise.

    Examples:
        - current="v0.animal", ref="v0.Animal" -> True (immediate parent)
        - current="v0.mammal.canine", ref="v0.Animal" -> True (grandparent)
        - current="v0.animal", ref="v0.animal.Dog" -> False (same or child)
        - current="pets", ref="Animal" -> True (root package is immediate parent)
        - current="v0.mammal.canine", ref="Animal" -> True (root package is an ancestor)
    """
    current_path = current_module.split(".") if current_module else []
    *reference_path, _ = reference.split(".")

    if not current_path:
        return False

    # Case 1: Direct parent package (includes root package when reference_path is empty)
    # e.g., current="pets", ref="Animal" -> current_path[:-1]=[] == reference_path=[]
    if current_path[:-1] == reference_path:
        return True

    # Case 2: Deeper ancestor package (reference_path must be a proper prefix)
    # e.g., current="v0.mammal.canine", ref="v0.Animal" -> ["v0"] is prefix of ["v0","mammal","canine"]
    # An empty reference_path is the root package, which is an ancestor of every nested module.
    return len(reference_path) < len(current_path) and current_path[: len(reference_path)] == reference_path


def exact_import(from_: str, import_: str, short_name: str) -> tuple[str, str]:
    """Create exact import path to avoid relative import issues."""
    if from_ == len(from_) * ".":
        # Prevents "from . import foo" becoming "from ..foo import Foo"
        # or "from .. import foo" becoming "from ...foo import Foo"
        # when our imported module has the same parent
        return f"{from_}{import_}", short_name
    return f"{from_}.{import_}", short_name


def _resolve_exact_import(
    current_module: str,
    target_full_name: str,
    from_: str,
    import_: str,
    short_name: str,
) -> tuple[str, str]:
    """Keep package imports intact while resolving exact module imports."""
    if is_ancestor_package_reference(current_module, target_full_name):
        return from_, import_
    return exact_import(from_, import_, short_name)


def _detach_deferred_inherited_field_parents(field: DataModelFieldBase) -> None:
    """Break parent cycles on a deferred field that is about to be discarded."""
    field.parent = None
    for data_type in field.data_type.all_data_types:
        data_type.parent = None
    match field.__dict__.get(_DEFERRED_INHERITED_TYPE_KEY):
        case DataType() as deferred_type:
            for data_type in deferred_type.all_data_types:
                data_type.parent = None
        case _InheritedTypeModifiers(list_wrapper=DataType() as list_wrapper):
            for data_type in list_wrapper.all_data_types:
                data_type.parent = None


def _restore_inherited_field_names_for_unique_aliases(
    builder: GraphBuilder,
    fields: list[DataModelFieldBase],
    inherited_fields: Mapping[str, DataModelFieldBase],
) -> bool:
    """Keep a required inherited field's Python name when wire aliases must be unique."""
    for field in fields:
        if not (original_name := field.original_name):  # pragma: no cover - msgspec fields come from named properties
            continue
        inherited_field = inherited_fields.get(original_name)
        if (
            inherited_field is None
            or inherited_field.original_name != original_name
            or not (inherited_name := inherited_field.name)
            or field.name == inherited_name
        ):
            continue
        for colliding_field in fields:
            if colliding_field is field or colliding_field.name != inherited_name:
                continue
            if (  # pragma: no cover - each schema object owns one field per wire name
                colliding_field.original_name == original_name or field.name is None
            ):
                continue
            builder.rename_field(colliding_field, field.name, alias=colliding_field.original_name)
            builder.rename_field(field, inherited_name)
            return True
    return False


class InheritedFieldHost(Protocol):
    """The options, store and hooks that resolving inherited placeholder fields reads from the parser."""

    allof_merge_mode: AllOfMergeMode
    apply_default_values_for_required_fields: bool
    force_optional_for_required_fields: bool
    generation_store: GenerationStore
    model_resolver: ModelResolver

    def _copy_inherited_field(
        self,
        field: DataModelFieldBase,
        inherited_field: DataModelFieldBase,
        *,
        force_optional: bool,
        partial_merge_mode: AllOfMergeMode,
        reserved_names: set[str],
    ) -> DataModelFieldBase | None:
        """Copy a deferred inherited field, or return None when it is not deferred."""

    def _copy_model_field(self, field: DataModelFieldBase) -> DataModelFieldBase:
        """Copy a field with its data types."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""


class RuntimeExpressionFlags(Protocol):
    """The shared imports and per-run flags that passes creating runtime expressions write on the parser."""

    imports: Imports
    _has_bound_python_types: bool
    _has_runtime_expressions: bool
    _import_ledger: ModuleImportLedger

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""


def register_runtime_expression(host: RuntimeExpressionFlags) -> None:
    """Mark a parser-owned expression created after the initial import scan."""
    host._has_runtime_expressions = True  # noqa: SLF001


def field_name_model_type_of(data_model_type: type[DataModel]) -> ModelType:
    """Return the ModelType that field names of ``data_model_type`` are validated for.

    ModelType.PYDANTIC for Pydantic models (which have reserved attributes like 'schema', 'model_fields', etc.),
    ModelType.MSGSPEC for msgspec Structs (whose imported ``field`` must not be shadowed by a field named ``field``),
    and ModelType.CLASS for other model types (TypedDict, dataclass) which don't have such constraints.
    """
    return model_type if (model_type := data_model_type.FIELD_NAME_MODEL_TYPE) is not None else ModelType.CLASS


def replace_model_in_list(
    models: list[DataModel],
    original: DataModel,
    replacement: DataModel,
) -> None:
    """Replace model at its position in list."""
    # Use direct assignment instead of insert+remove for O(n) instead of O(2n)
    idx = models.index(original)
    models[idx] = replacement


@final
@record
class ReferenceImportTarget:
    """How a module imports a model of another module: the resolver path of the import and its module and name.

    ``from_`` is the module as the expression rule reads it, ``written_from`` the module the import line writes (a
    package ``__init__`` reaches a model outside its package one level up).
    """

    full_path: tuple[str, str]
    from_: str
    import_: str
    written_from: str

    def expression(self, name: str, binding: str) -> str | None:
        """Return what the module writes for the model ``name`` under the binding, or None for the class name."""
        if not (self.from_ and self.import_) or binding == name:
            return None
        return binding if name == self.import_ else f"{binding}.{name}"


def reference_import_target(  # ruff: ignore[too-many-arguments]
    name: str,
    current_module_name: str,
    target_full_name: str,
    *,
    use_exact: bool,
    init: bool,
    internal_modules: Collection[tuple[str, ...]],
    base_class: bool,
    treat_dot_as_module: bool | None,
) -> ReferenceImportTarget:
    """Return how the module ``current_module_name`` imports the model ``name`` at ``target_full_name``."""
    if base_class:
        left, right = relative(current_module_name, target_full_name)
        from_ = (
            left
            if is_ancestor_package_reference(current_module_name, target_full_name)
            else (f"{left}{right}" if left.endswith(".") else f"{left}.{right}")
        )
        import_ = name
        full_path = from_, import_
    else:
        from_, import_ = full_path = relative(current_module_name, target_full_name)
        if use_exact:
            from_, import_ = full_path = _resolve_exact_import(
                current_module_name,
                target_full_name,
                from_,
                import_,
                name,
            )
        import_ = import_.replace("-", "_")
        current_module_path = tuple(current_module_name.split(".")) if current_module_name else ()
        if (  # pragma: no cover
            len(current_module_path) > 1 and current_module_path[-1].count(".") > 0 and not treat_dot_as_module
        ):
            rel_path_depth = current_module_path[-1].count(".")
            from_ = from_[rel_path_depth:]

        ref_module = tuple(target_full_name.split(".")[:-1])

        is_module_class_collision = ref_module and import_ == name and ref_module[-1] == import_

        if from_ and not use_exact and (ref_module in internal_modules or is_module_class_collision):
            from_ = f"{from_}{import_}" if from_.endswith(".") else f"{from_}.{import_}"
            import_ = name
            full_path = from_, import_
    written_from = "." + from_ if init and not target_full_name.startswith(current_module_name + ".") else from_
    return ReferenceImportTarget(full_path, from_, import_, written_from)


def register_reference_import(  # noqa: PLR0913
    reference: Reference,
    current_module_name: str,
    target_full_name: str,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    *,
    init: bool,
    internal_modules: set[tuple[str, ...]],
    base_class: bool = False,
    producer: ImportProducer,
    treat_dot_as_module: bool | None,
    import_ledger: ModuleImportLedger,
) -> str | None:
    """Import a reference from another module and return its expression when it is not the class name."""
    name = reference.short_name
    target = reference_import_target(
        name,
        current_module_name,
        target_full_name,
        use_exact=imports.use_exact,
        init=init,
        internal_modules=internal_modules,
        base_class=base_class,
        treat_dot_as_module=treat_dot_as_module,
    )
    alias = import_ledger.add_name(imports, scoped_model_resolver, target.full_path, target.import_).name
    import_ledger.append(
        imports,
        Import(
            from_=target.written_from,
            import_=target.import_,
            alias=alias,
            reference_path=reference.path,
        ),
        producer,
    )
    return target.expression(name, alias)


def redirect_collapsed_reuse_users(
    builder: GraphBuilder,
    collapsed_models: dict[int, list[DataModel]],
    collapsed_users: dict[int, tuple[DataType, Reference]],
    duplicate: DataModel,
    canonical: DataModel,
) -> list[DataType]:
    """Redirect every user of a duplicate that collapsed reuse removes to the kept model, remembering the duplicate.

    A discriminated union keeps mapping the duplicate to the tags of its own schema, so they are compared with
    the tags of the kept model.
    """
    users = builder.redirect_reference_users(duplicate.reference, canonical.reference, merged=True)
    for user in users:
        collapsed_users.setdefault(id(user), (user, duplicate.reference))
    collapsed_models.setdefault(id(canonical), []).append(duplicate)
    return users


def normalize_default_value_constraints(
    host: RuntimeExpressionFlags,
    models: list[DataModel],
    *,
    data_type_manager: DataTypeManager,
    import_overrides: dict[str, str] | None,
) -> bool:
    """Keep backend-declared runtime constraint values structured until alias resolution."""
    from decimal import Decimal  # noqa: PLC0415

    normalized = False
    for model, _, data_type in iter_models_field_data_types(models):
        if (
            not model.SUPPORTS_DESERIALIZED_DEFAULT_VALUES
            or not data_type.kwargs
            or (resolved := resolve_default_value_descriptor(data_type_manager, data_type)) is None
        ):
            continue
        annotation_import, descriptor = resolved
        if not descriptor.normalize_constraints or has_import_override(
            import_overrides, annotation_import, descriptor.constructor_import
        ):
            continue
        if descriptor.recipe is not DefaultValueRecipe.Decimal:  # pragma: no cover - future backend recipe
            continue
        kwargs = data_type.kwargs
        for key, value in kwargs.items():
            if not isinstance(value, Decimal):
                continue
            if kwargs is data_type.kwargs:
                kwargs = kwargs.copy()
            kwargs[key] = PythonRuntimeExpression.from_import_call(
                descriptor.constructor_import,
                repr(str(value)),
                value=str(value),
            )
        if kwargs is data_type.kwargs:
            continue
        host._graph_builder.set_type_expressions(  # noqa: SLF001
            data_type,
            kwargs=kwargs,
            runtime_expression_imports=(*data_type.runtime_expression_imports, descriptor.constructor_import),
        )
        register_runtime_expression(host)
        normalized = True
    return normalized


def resolve_default_value_descriptor(
    data_type_manager: DataTypeManager, data_type: DataType
) -> tuple[Import, DefaultValueDescriptor] | None:
    """Return a backend-declared scalar descriptor and its emitted annotation import."""
    if (scalar_data_type := _resolve_default_scalar_data_type(data_type)) is None:
        return None
    if (annotation_import := scalar_data_type.import_) is None:
        return None
    if (descriptor := data_type_manager.get_default_value_descriptor(scalar_data_type)) is None:
        return None
    return annotation_import, descriptor


def has_import_override(import_overrides: dict[str, str] | None, *imports: Import) -> bool:
    """Avoid changing generated defaults when an import policy redirects their bindings."""
    if not import_overrides:
        return False
    return any(import_overrides.get(import_.import_, import_.from_) != import_.from_ for import_ in imports)


def apply_inherited_field_default(
    field: DataModelFieldBase,
    inherited_field: DataModelFieldBase,
    *,
    class_name: str,
    model_resolver: ModelResolver,
    apply_default_values_for_required_fields: bool,
) -> None:
    """Resolve an inherited schema default in the derived class scope."""
    if model_resolver.default_value_overrides and _RAW_SCHEMA_DEFAULT_KEY in inherited_field.__dict__:
        raw_default = inherited_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY]
        has_default = raw_default is not _RAW_SCHEMA_DEFAULT_UNDEFINED
        field.default, field.has_default = model_resolver.resolve_default_value(
            field.original_name or field.name or "",
            None if not has_default else raw_default,
            has_default,
            class_name=class_name,
        )
        match field.default:
            case dict() | list() | set():
                field.default = deepcopy(field.default)
    field.use_default_with_required = apply_default_values_for_required_fields and field.required and field.has_default


def override_required_field(
    host: InheritedFieldHost,
    models: list[DataModel],
) -> None:
    """Replace the placeholder fields that name an inherited property by copies of the inherited fields."""
    pending_models = (
        model
        for model in models
        if not (isinstance(model, Enum) or host._is_root_model(model))  # noqa: SLF001
        and any(
            field.original_name is not None
            and not field.data_type.data_types
            and not field.data_type.reference
            and not field.data_type.type
            and not field.data_type.literals
            and not field.data_type.enum_member_literals
            and not field.data_type.dict_key
            for field in model.fields
        )
    )
    if (first_model := next(pending_models, None)) is None:
        return

    builder = host.generation_store.builder()
    builder.discard_derived_facts()
    for model in chain((first_model,), pending_models):
        resolved_fields: list[DataModelFieldBase] = []
        reserved_names = {field.name for field in model.fields if field.name}
        inherited_fields = get_inherited_fields(_find_base_classes(model))
        pending_fields = model.fields
        pending_fields.reverse()
        builder.set_fields(model, [])
        while pending_fields:
            model_field = pending_fields.pop()
            model_field.parent = None
            data_type = model_field.data_type
            if (
                model_field.original_name is None  # noqa: PLR0916
                or data_type.data_types
                or data_type.reference
                or data_type.type
                or data_type.literals
                or data_type.enum_member_literals
                or data_type.dict_key
            ):
                resolved_fields.append(model_field)
                continue

            _detach_deferred_inherited_field_parents(model_field)
            original_field = inherited_fields.get(model_field.original_name)
            if not original_field:
                continue
            if (
                copied_original_field := host._copy_inherited_field(  # noqa: SLF001
                    model_field,
                    original_field,
                    force_optional=host.force_optional_for_required_fields,
                    partial_merge_mode=host.allof_merge_mode,
                    reserved_names=reserved_names,
                )
            ) is None:
                copied_original_field = host._copy_model_field(original_field)  # noqa: SLF001
                copied_original_field.name = model_field.name
                copied_original_field.original_name = model_field.original_name
                copied_original_field.alias = model_field.alias
                copied_original_field.validation_aliases = (
                    list(model_field.validation_aliases) if model_field.validation_aliases is not None else None
                )
                copied_original_field.serialization_alias = model_field.serialization_alias
                copied_original_field.use_serialization_alias = model_field.use_serialization_alias
                copied_original_field.required = True
            if class_name := model_field.__dict__.get(_DEFERRED_INHERITED_FIELD_KEY):
                apply_inherited_field_default(
                    copied_original_field,
                    original_field,
                    class_name=class_name,
                    model_resolver=host.model_resolver,
                    apply_default_values_for_required_fields=host.apply_default_values_for_required_fields,
                )
            elif class_name := model_field.__dict__.get(_DEFERRED_INHERITED_CLASS_KEY):
                default_source = model_field
                if host.allof_merge_mode == AllOfMergeMode.All and not (
                    _RAW_SCHEMA_DEFAULT_KEY in model_field.__dict__
                    and model_field.__dict__[_RAW_SCHEMA_DEFAULT_KEY] is not _RAW_SCHEMA_DEFAULT_UNDEFINED
                ):
                    default_source = original_field
                apply_inherited_field_default(
                    copied_original_field,
                    default_source,
                    class_name=class_name,
                    model_resolver=host.model_resolver,
                    apply_default_values_for_required_fields=host.apply_default_values_for_required_fields,
                )
            elif host.apply_default_values_for_required_fields and copied_original_field.has_default:
                copied_original_field.use_default_with_required = True
            resolved_fields.append(copied_original_field)
        if model.REQUIRES_UNIQUE_FIELD_ALIASES:
            _restore_inherited_field_names_for_unique_aliases(builder, resolved_fields, inherited_fields)
        builder.set_fields(model, resolved_fields)


class ConstructorConflicts(NamedTuple):
    """The positional fields of a model's constructor that follow a positional default.

    ``own`` names the model's own fields that follow a constructor or signature default. ``inherited`` holds the
    inherited fields, with the model that declares each, that follow a constructor default without one: ``dataclasses``
    rejects the class, and no base sees the conflict, as only the model joins the bases that set it up.
    """

    own: frozenset[str]
    inherited: tuple[tuple[DataModelFieldBase, DataModel], ...]


def get_constructor_ordering_conflicts(
    model: DataModel,
    effective_fields: Mapping[str, tuple[DataModelFieldBase, DataModel, bool | None]],
    required_assignment_names: set[str],
) -> ConstructorConflicts:
    """Return the positional fields of the model's effective constructor that follow a positional default.

    A field of the model's own follows one as the class body writes the defaults, which the class rejects, or as
    each field takes its place (``AssignmentFacts.has_place_default``), which keeps 0.86.0's keyword-only fields; an
    inherited field, which only the copies fix, follows one as they are written.
    """
    seen_constructor_default = seen_signature_default = seen_place_default = False
    ordering_conflicts: set[str] = set()
    inherited_conflicts: list[tuple[DataModelFieldBase, DataModel]] = []
    for field_name, (field, declaring_model, inherited_has_default) in effective_fields.items():
        facts = model._field_assignment_facts(field)  # noqa: SLF001
        if not facts.participates_in_constructor:
            continue
        kw_only = field.constructor_keyword_only
        if kw_only is True or (kw_only is None and declaring_model.has_keyword_only_definition()):
            continue
        required_assignment_is_constructor_default = (
            model.REQUIRED_ASSIGNMENT_COUNTS_AS_CONSTRUCTOR_DEFAULT
            and field.required
            and not field.use_default_with_required
            and (field_name in required_assignment_names or facts.assigned)
        )
        field_has_signature_default = (
            False
            if required_assignment_is_constructor_default
            else (facts.has_default if inherited_has_default is None else inherited_has_default)
        )
        field_has_constructor_default = required_assignment_is_constructor_default or field_has_signature_default
        field_has_place_default = required_assignment_is_constructor_default or (
            field_has_signature_default and facts.has_place_default
        )
        if declaring_model is model:
            follows_written_default = (seen_constructor_default and not field_has_constructor_default) or (
                seen_signature_default and not field_has_signature_default
            )
            if follows_written_default or (seen_place_default and not field_has_place_default):
                ordering_conflicts.add(field_name)
        elif seen_constructor_default and not field_has_constructor_default:
            inherited_conflicts.append((field, declaring_model))
        seen_constructor_default = seen_constructor_default or field_has_constructor_default
        seen_signature_default = seen_signature_default or field_has_signature_default
        seen_place_default = seen_place_default or field_has_place_default
    return ConstructorConflicts(frozenset(ordering_conflicts), tuple(inherited_conflicts))


def alias_shadowed_imports(
    host: RuntimeExpressionFlags,
    models: list[DataModel],
    all_model_field_names: set[str],
    *,
    module_imports: Imports | None = None,
) -> None:
    """Alias the imports whose names a field name of the models hides."""
    ordinary_aliases, has_python_type, has_runtime_expressions = _ordinary_field_shadow_aliases(
        models,
        all_model_field_names,
    )
    if not (has_python_type or has_runtime_expressions):
        if ordinary_aliases:
            _apply_structured_import_aliases(host._graph_builder, models, ordinary_aliases)  # noqa: SLF001
        return
    host._has_bound_python_types = host._has_bound_python_types or has_python_type  # noqa: SLF001
    if has_runtime_expressions:
        register_runtime_expression(host)
    # Keep structured import machinery lazy: ordinary schemas must not
    # pay its import, allocation, or module-scan cost.
    from datamodel_code_generator.parser._python_type_imports import (  # noqa: PLC0415
        resolve_structured_import_aliases,
    )

    data_types = (data_type for _, _, data_type in iter_models_field_data_types(models))
    aliased_imports = resolve_structured_import_aliases(
        data_types,
        models,
        all_model_field_names,
        (host.imports,) if module_imports is None else (host.imports, module_imports),
    )
    if not aliased_imports:
        return
    _apply_structured_import_aliases(host._graph_builder, models, aliased_imports)  # noqa: SLF001
    if module_imports is not None:
        for aliased_import in aliased_imports.values():
            host._import_ledger.apply_alias(module_imports, aliased_import, _SHADOWED_IMPORTS)  # noqa: SLF001


def _is_pydantic_v2_root_model(model: DataModel, root_model_type: type[DataModel] | None) -> bool:
    """Return whether the model's fields need dependency ordering: the root type requires it and so does its class.

    A model the configured type keeps as its base class, such as a ``RootModel`` that ``--use-root-model-type-alias``
    keeps for a constrained root, is ordered like the configured type.
    """
    return root_model_type is not None and (
        isinstance(model, root_model_type) or model.REQUIRES_FIELD_DEPENDENCY_ORDERING
    )


@record
class _ReuseOptimizationContext:
    """Parser-owned semantic constraints for model reuse optimizations."""

    type_override_model_names: frozenset[str] = frozenset()
    base_stand_in_keys: frozenset[ModelKey] = frozenset()
    """The model keys of the stand-ins, which are never written and take part in no reuse."""

    @classmethod
    def from_type_overrides(cls, type_overrides: Mapping[str, str]) -> _ReuseOptimizationContext:
        """Build reuse constraints without importing output-backend policy."""
        if not type_overrides:
            return cls()

        return cls(frozenset(override_key.partition(".")[0] for override_key in type_overrides))

    @property
    def constrains(self) -> bool:
        """Return whether any model is kept out of reuse."""
        return bool(self.type_override_model_names or self.base_stand_in_keys)

    def allows_model(self, model: DataModel) -> bool:
        """Return whether reuse can preserve all pending parser semantics."""
        return (
            model.class_name not in self.type_override_model_names
            and model_key_of(model.reference) not in self.base_stand_in_keys
        )

    def eligible_models(self, models: Iterable[DataModel]) -> Iterable[DataModel]:
        """Return models eligible for reuse without allocating on the fast path."""
        if not self.constrains:
            return models
        return (model for model in models if self.allows_model(model))


def _sort_models_by_class_body_references(
    models: list[DataModel],
    pydantic_v2_root_model_type: type[DataModel] | None,
    runtime_validation_references: Mapping[ModelKey, frozenset[ModelKey]],
) -> list[DataModel]:
    """Define first the models a class body evaluates: base classes, root types and runtime rule types."""
    keyed = keyed_models(models, GraphSnapshot.CLASS_BODY_ORDER.value)
    order_index = {key: index for index, key in enumerate(keyed.models)}
    targets: dict[ModelKey, set[ModelKey]] = {}
    for model_key, model in keyed.models.items():
        model_targets = targets[model_key] = {
            keyed.key_of(base_class.reference) for base_class in model.base_classes if base_class.reference
        }
        model_targets.update(runtime_validation_references.get(model_key, ()))
        if _is_pydantic_v2_root_model(model, pydantic_v2_root_model_type):
            model_targets.update(
                keyed.key_of(data_type.reference)
                for field in model.fields
                for data_type in field.data_type.all_data_types
                if data_type.reference
            )
        model_targets.discard(model_key)
    graph = reference_graph(GraphSnapshot.CLASS_BODY_ORDER, keyed.models, {EdgeKind.CLASS_BODY: targets})

    def old() -> list[ModelKey]:
        edges: dict[ModelKey, set[ModelKey]] = {key: set() for key in keyed.models}
        for model_key, model_targets in targets.items():
            for reference_key in model_targets:
                if reference_key in edges:
                    edges[reference_key].add(model_key)
        return stable_toposort(list(order_index), edges, key=order_index.__getitem__)

    sorted_keys = graph_answer(
        GraphSnapshot.CLASS_BODY_ORDER,
        graph.dependency_order((EdgeKind.CLASS_BODY,), list(order_index), order_index.__getitem__),
        old,
    )
    return [keyed.models[key] for key in sorted_keys]


def _runtime_validation_reference_keys(
    models: Sequence[DataModel], *, same_module: bool
) -> dict[ModelKey, frozenset[ModelKey]]:
    """Map models to the listed models their runtime rules name; with ``same_module``, other modules are imported."""
    references_by_key: dict[ModelKey, frozenset[ModelKey]] = {}
    keyed: KeyedModels | None = None
    for model in models:
        if not (references := model.schema_runtime_validation_references):
            continue
        keyed = keyed or keyed_models(models, "runtime validation references")
        module_path = model.module_path if same_module else None
        if reference_keys := frozenset(
            reference_key
            for reference in references
            if (reference_key := keyed.key_of(reference)) in keyed.models
            and (
                module_path is None
                or (isinstance(source := reference.source, DataModel) and source.module_path == module_path)
            )
        ):
            references_by_key[model_key_of(model.reference)] = reference_keys
    return references_by_key


def _rule_data_types(model: DataModel) -> Iterator[DataType]:
    """Yield the data types of a model's schema validator rules, nested ones included."""
    if runtime_validation := model.schema_runtime_validation:
        for rule_data_type in runtime_validation.data_types:
            yield from rule_data_type.all_data_types


def _data_types_beside_fields(model: DataModel) -> Iterator[DataType]:
    """Yield the data types a model writes beside its fields and bases.

    Those are the types of its schema validator rules and of its backend settings, such as TypedDict ``extra_items``.
    """
    yield from _rule_data_types(model)
    yield from model._setting_data_types()  # ruff: ignore[private-member-access]


def _named_data_types(model: DataModel) -> Iterator[DataType]:
    """Yield the data types a model writes: those of its fields and bases, then those beside them.

    The class writes the types of its rules and backend settings as it writes its field types, so a model they name in
    another module is imported the same way.
    """
    yield from model.all_data_types
    yield from _data_types_beside_fields(model)


def add_model_path_to_list(
    paths: list[str] | None,
    model: DataModel,
    /,
) -> list[str]:
    """
    Auxiliary method which adds model path to list, provided the following hold.

    - model is not a type alias
    - path is not already in the list.

    """
    if paths is None:
        paths = []
    if model.is_alias:
        return paths
    if (path := model.path) in paths:
        return paths
    paths.append(path)
    return paths


def _get_schema_path(model: DataModel | Reference) -> str:
    """Return the path of the schema a model is generated from, through read and write variants and reuse."""
    source = model if isinstance(model, DataModel) else model.source
    reused = source.reused_reference if isinstance(source, DataModel) else None
    return _source_path((reused or model).path)


def _get_mapping_discriminator_values(discriminator_model: DataModel, mapping: dict[str, str]) -> list[str]:
    """Return the mapping values that select a variant directly or through its base classes (``mapped_tags``)."""
    if not mapping:
        return []
    return mapped_tags(
        mapping,
        schema_path=_get_schema_path(discriminator_model),
        base_schema_paths=(
            _get_schema_path(reference)
            for base_class in discriminator_model.base_classes
            if (reference := base_class.reference)
        ),
    )


class RecursionFactsHost(Protocol):
    """The recursion facts of the run, which the passes that add models needing a reference update action extend."""

    _recursion_facts: RecursionFacts


def require_update_action(
    host: RecursionFactsHost, models: Sequence[DataModel], require_update_action_models: list[str] | None = None
) -> None:
    """Add the models that are not type aliases to the models needing a reference update action.

    ``require_update_action_models`` is the list of their paths that ``_process_single_module`` takes.
    """
    recursion_facts = host._recursion_facts  # noqa: SLF001
    for model in models:
        add_model_path_to_list(require_update_action_models, model)
    host._recursion_facts = RecursionFacts(  # noqa: SLF001
        require_update=recursion_facts["require_update"].union(
            model_key_of(model.reference) for model in models if not model.is_alias
        ),
        circular_roots=recursion_facts["circular_roots"],
    )


DiscriminatorValue: TypeAlias = str | int | bool


def _module_name_of(model_path_to_module_name: Mapping[str, str], reference: Reference) -> str | None:
    """Return the module name scope planning gives the model ``reference`` names, or None."""
    if isinstance(model_path_to_module_name, _ModuleNames):
        return model_path_to_module_name.of(reference)
    return model_path_to_module_name.get(reference.path)


def _module_name_from_module_path(module: ModulePath) -> str:
    if module == ("__init__.py",):
        return ""

    parts = [*module]
    if parts and parts[-1].endswith(".py"):  # pragma: no branch - Module paths are Python files.
        parts[-1] = parts[-1][:-3]
    return ".".join(parts[:-1] if parts[-1:] == ["__init__"] else parts)


@final
@record
class _FieldTagFacts:
    """The facts of a field that ``field_tag`` reads: its ``const``, its ``Literal`` and the Enum its type names."""

    field: DataModelFieldBase

    def has_const(self) -> bool:
        """Return whether the field has a ``const`` other than None."""
        return self.field.extras.get("const") is not None

    def const(self) -> Any:
        """Return the field's ``const``."""
        return self.field.extras.get("const")

    def literals(self) -> list[DiscriminatorValue]:
        """Return the values of the ``Literal`` the field's type is."""
        return self.field.data_type.literals

    def enum_member(self) -> tuple[int, Any]:
        """Return how many members the Enum the field's type names has, and the first one's value.

        A union that wrote the field as ``Literal[<Enum>.<member>]`` detached its reference to the Enum, which is then
        found through the members, so a later union reads the tag the first one read.
        """
        fields = enum_source.fields if (enum_source := _annotated_enum(self.field.data_type)) else []
        return (len(fields), get_raw_enum_member_value(fields[0].default)) if fields else (0, None)

    def referenced_tag(self) -> None:
        """Return no tag: a field has no ``$ref`` of its own."""


def _is_discriminator_container(data_type: DataType) -> bool:
    return (
        data_type.is_dict
        or data_type.is_list
        or data_type.is_set
        or data_type.is_frozen_set
        or data_type.is_mapping
        or data_type.is_sequence
        or data_type.is_tuple
    )


def _is_discriminator_wrapper(model: DataModel) -> bool:
    return model.IS_ALIAS or model.IS_ROOT_MODEL


def _iter_discriminator_data_types(
    data_types: Iterable[DataType],
    active_union_models: set[int] | None = None,
    *,
    can_update_discriminator: bool = True,
    discriminator_owner: int | None = None,
    wrappers: list[DataModel] | None = None,
) -> Iterator[tuple[DataType, bool, int]]:
    for data_type in data_types:
        if data_type.is_union and not _is_discriminator_container(data_type):
            yield from _iter_discriminator_data_types(
                data_type.data_types,
                active_union_models,
                can_update_discriminator=False,
                discriminator_owner=discriminator_owner,
                wrappers=wrappers,
            )
        else:
            source = data_type.reference.source if data_type.reference else None
            owner = discriminator_owner or (id(source) if source is not None else id(data_type))
            if not isinstance(source, DataModel) or not _is_discriminator_wrapper(source):
                yield data_type, can_update_discriminator, owner
            else:
                source_id = id(source)
                if active_union_models is None:
                    active_union_models = set()
                if source_id in active_union_models or not source.fields:
                    yield data_type, can_update_discriminator, owner
                else:
                    active_union_models.add(source_id)
                    if wrappers is not None:
                        wrappers.append(source)
                    try:
                        yield from _iter_discriminator_data_types(
                            (source.fields[0].data_type,),
                            active_union_models,
                            can_update_discriminator=False,
                            discriminator_owner=owner,
                            wrappers=wrappers,
                        )
                    finally:
                        active_union_models.remove(source_id)


def _generic_base_class_module(module: ModulePath, base_module: ModulePath) -> str:
    """Return the module that ``module`` imports the generated base class from, which ``base_module`` writes."""
    from_ = relative(".".join(module[:-1]) if module else "", ".".join(base_module[:-1]) if base_module else "")[0]
    name = base_module[-1].replace(".py", "")
    return f"{from_}{name}" if from_.endswith(".") else f"{from_}.{name}"


def _get_model_module_name(model: DataModel, model_path_to_module_name: Mapping[str, str]) -> str:
    """Return the module name scope planning gives a model, or the model's own module name."""
    module_name = _module_name_of(model_path_to_module_name, model.reference)
    return model.module_name if module_name is None else module_name


def _get_data_type_target_full_name(
    data_type: DataType,
    reference: Reference,
    model_path_to_module_name: Mapping[str, str],
) -> str:
    if (ref_module_name := _module_name_of(model_path_to_module_name, reference)) is None:
        ref_module_name = data_type.full_name.rsplit(".", 1)[0] if "." in data_type.full_name else ""
    return f"{ref_module_name}.{reference.short_name}" if ref_module_name else reference.short_name


def _context_models(contexts: Iterable[ModuleContextView]) -> list[DataModel]:
    return [model for ctx in contexts for model in ctx.models]


class ReplacedRootsHost(Protocol):
    """The roots that collapse replaced by their inner models, which leave the modules that held them."""

    @property
    def _replaced_root_models(self) -> dict[int, tuple[DataModel, DataModel]]:
        """The replaced roots by id, with the root and the inner model that replaces it."""


class ReferenceExpressionHost(Protocol):
    """The option and the import ledger that writing how a data type names its reference reads."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""


def module_members(host: ReplacedRootsHost, models: list[DataModel]) -> list[DataModel]:
    """Return the models of a module that stay in it: a root replaced by its inner model leaves at the decision."""
    if not (replaced_root_models := host._replaced_root_models):  # noqa: SLF001
        return models
    return [model for model in models if id(model) not in replaced_root_models]


def names_left_by_replaced_roots(host: ReplacedRootsHost, models: list[DataModel], members: KeyedModels) -> set[str]:
    """Return the class names that roots replaced by their inner models leave free in a module.

    A root keeps its name for the inner model when the inner model, which takes the root's path, is in the same
    module.
    """
    replaced_root_models = host._replaced_root_models  # noqa: SLF001
    return {
        model.class_name
        for model in models
        if (replacement := replaced_root_models.get(id(model))) is not None
        and not _keyed_answer(
            "names left by replaced roots",
            model_key_of(replacement[1].reference) in members.models,
            lambda model=model: model.path in members.keys_by_path,
        )
    }


def set_reference_expression(  # noqa: PLR0913, PLR0917
    host: ReferenceExpressionHost,
    data_type: DataType,
    current_module_name: str,
    target_full_name: str,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    *,
    same_module: bool,
    init: bool,
    internal_modules: set[ModulePath],
    producer: ImportProducer,
) -> None:
    """Write how a data type names its reference in the current module.

    A model the module emits is written by its class name; any other is imported, and written by the expression
    the import gives when that is not the class name.
    """
    data_type.alias = (
        None
        if same_module
        else register_reference_import(
            cast("Reference", data_type.reference),
            current_module_name,
            target_full_name,
            imports,
            scoped_model_resolver,
            init=init,
            internal_modules=internal_modules,
            base_class=isinstance(data_type, BaseClassDataType),
            producer=producer,
            treat_dot_as_module=host.treat_dot_as_module,
            import_ledger=host._import_ledger,  # noqa: SLF001
        )
    )


def _copy_data_type(data_type: DataType, *, register_references: bool = True) -> DataType:
    """Copy a DataType tree without detaching its model references."""
    copied_data_type = data_type.model_copy()
    copied_data_type.parent = None
    copied_data_type.children = []
    copied_data_type.literals = list(data_type.literals)
    copied_data_type.enum_member_literals = list(data_type.enum_member_literals)
    if (kwargs := data_type.kwargs) is not None:
        copied_data_type.kwargs = deepcopy(kwargs)

    data_types = data_type.data_types
    dict_key = data_type.dict_key
    match data_types, dict_key:
        case [], None:
            copied_data_type.data_types = []
        case _:
            copied_data_type.data_types = _copy_data_types(data_types, register_references=register_references)
            for nested_data_type in copied_data_type.data_types:
                nested_data_type.parent = copied_data_type
            if dict_key is not None:
                copied_data_type.dict_key = _copy_data_type(dict_key, register_references=register_references)
                copied_data_type.dict_key.parent = copied_data_type

    if register_references:
        copied_data_type.register_reference()
    return copied_data_type


def _copy_data_types(data_types: list[DataType], *, register_references: bool = True) -> list[DataType]:
    """Copy DataType trees while preserving shared model references."""
    return [_copy_data_type(data_type, register_references=register_references) for data_type in data_types]


def _declares_unique_items(field: DataModelFieldBase) -> bool:
    """Return whether a field declares ``uniqueItems: true``."""
    match field.constraints:
        case ConstraintsBase(unique_items=True) | {"uniqueItems": True}:
            return True
    return False


def _unique_items_tuple(data_type: DataType) -> DataType | None:
    """Return the tuple a unique array is written as, where --use-unique-items-as-set writes a list as a set."""
    if data_type.is_tuple:
        return data_type
    if data_type.is_list or data_type.is_set or data_type.is_frozen_set:
        return None
    return next((found for nested in data_type.data_types if (found := _unique_items_tuple(nested)) is not None), None)


def _annotated_reference(data_type: DataType) -> Reference | None:
    """Return the type that the annotation of ``data_type`` names: its reference, or the enum of its enum members."""
    return data_type.reference or data_type._enum_member_literal_reference  # noqa: SLF001


def _annotated_enum(data_type: DataType) -> Enum | None:
    """Return the Enum the annotation of ``data_type`` or of a type inside it names (``_annotated_reference``)."""
    return next(
        (
            source
            for nested in data_type.all_data_types
            if (reference := _annotated_reference(nested)) is not None and isinstance(source := reference.source, Enum)
        ),
        None,
    )


@final
class _ModelPositions:
    """The position of each model in the model list of a module, found by model key."""

    __slots__ = ("_keyed", "_positions", "paths")

    def __init__(self, models: list[DataModel], site: str) -> None:
        """Index ``models``, which ``site`` names in a violation."""
        self._keyed = keyed_models(models, site)
        self._positions = {model_key_of(model.reference): index for index, model in enumerate(models)}
        self.paths: Mapping[str, int] = {model.path: index for index, model in enumerate(models)}
        """The position of each model by path, as the ``DataModel`` hooks that take positions read them."""

    def get(self, reference: Reference) -> int | None:
        """Return the position of the model of the list that ``reference`` names (`KeyedModels.key_of`), or None."""
        return self._positions.get(self._keyed.key_of(reference))

    def rank(self, reference: Reference) -> int:
        """Return the position of the model of the list that ``reference`` names, or -1."""
        return self._positions.get(self._keyed.key_of(reference), -1)


def _derived_models(models: Iterable[DataModel]) -> dict[ModelKey, list[DataModel]]:
    """Map the model key of each base model to the models of ``models`` derived from it directly."""
    keys = _ModelKeys("derived models")
    derived_models: defaultdict[ModelKey, list[DataModel]] = defaultdict(list)
    for derived_model in models:
        for base_model in _find_base_classes(derived_model):
            derived_models[keys.of(base_model)].append(derived_model)
    return derived_models


def _register_data_type_import(  # ruff: ignore[too-many-arguments]
    data_type: DataType,
    model: DataModel,
    imports: Imports,
    scoped_model_resolver: ModelResolver,
    model_path_to_module_name: dict[str, str] | None,
    *,
    ledger: ModuleImportLedger,
    producer: ImportProducer,
) -> None:
    match data_type.reference:
        case None:
            return
        case reference:
            pass

    model_path_to_module_name = model_path_to_module_name or {}
    current_module_name = _get_model_module_name(model, model_path_to_module_name)
    from_, import_ = full_path = relative(
        current_module_name,
        target_full_name := _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
    )
    if imports.use_exact:
        from_, import_ = full_path = _resolve_exact_import(
            current_module_name,
            target_full_name,
            from_,
            import_,
            reference.short_name,
        )
    if not (from_ and import_):
        return

    alias = ledger.add_name(imports, scoped_model_resolver, full_path, import_)
    data_type.alias = alias.name if reference.short_name == import_ else f"{alias.name}.{reference.short_name}"
    ledger.append(
        imports,
        Import(
            from_=from_,
            import_=import_,
            alias=alias.name,
            reference_path=reference.path,
        ),
        producer,
    )


class RenamedTypesHost(Protocol):
    """The class names renamed types had, which the passes that rename a type record."""

    @property
    def _renamed_type_paths(self) -> dict[str, list[_RenamedType]]:
        """The class names renamed types had, by the path imports of them were added under."""

    @property
    def _renamed_types(self) -> dict[ModelKey, list[_RenamedType]]:
        """The class names renamed types had, by model key."""


def record_renamed_type(host: RenamedTypesHost, model: DataModel, renamed_type: _RenamedType) -> None:
    """Record a class name that ``model`` had before a rename, under its model key and its current path.

    A model that shares its path with the root model it took the name of, or with the inner model that took its name,
    records it under the key of that model too (row ``legacy-replaced-root-shares-path``).
    """
    for key in _path_identity_keys(model):
        host._renamed_types.setdefault(key, []).append(renamed_type)  # noqa: SLF001
    host._renamed_type_paths.setdefault(model.path, []).append(renamed_type)  # noqa: SLF001


class BaseStandInHost(Protocol):
    """The stand-ins: models of schemas that bases name and the output types by an import, which are never written."""

    @property
    def _base_stand_in_imports(self) -> dict[ModelKey, Import]:
        """The imports of the classes the stand-in models stand for, by model key."""


class TypeOverrideHost(BaseStandInHost, Protocol):
    """The model-level type overrides, which replace the models they name, and the stand-ins, which imports replace."""

    @property
    def _model_type_override_imports(self) -> dict[str, Import]:
        """The imports of the types that override models, by model name."""


def enum_site_override_import(host: TypeOverrideHost, reference: Reference) -> Import | None:
    """Return the model-level override of the target an enum site model is built from, which replaces the model.

    An enum cannot take the type that replaces its target as its root, so it gives way to that type as the target
    does: the model is left out and its references take the type.
    """
    if (target := reference.__dict__.get(_DERIVED_FROM_KEY)) is None or not isinstance(reference.source, Enum):
        return None
    return host._model_type_override_imports.get(target.name)  # noqa: SLF001


_ConstructorFieldAdjustment: TypeAlias = Literal["assignment", "keyword_only"]


class _InheritedConstructorInfo(NamedTuple):
    required_assignment_names: frozenset[str]
    inherited_stripped_none_names: frozenset[str]
    ordering_conflicts: frozenset[str]
    inherited_conflicts: tuple[tuple[DataModelFieldBase, DataModel], ...]


class ConstructorInfoHost(Protocol):
    """The bases constructor ordering reads in place of others: the stand-ins of overridden bases, the generic base."""

    @property
    def _base_stand_ins(self) -> dict[int, Reference | None]:
        """The generated model each base that a model-level type override replaced stood for, by base."""

    @property
    def _generic_base_model(self) -> DataModel | None:
        """The generated base class of every model, if the output has one."""


def constructor_base_models(host: ConstructorInfoHost, model: DataModel, *, early: bool = False) -> list[DataModel]:
    """Return the generated models whose fields ``model``'s constructor takes, in MRO order, the generic base left out.

    ``early`` reads the bases as parsed; otherwise a base a model-level type override replaced is the generated
    model it stood for.
    """
    if not model.base_classes:
        return []
    stand_ins = {} if early else host._base_stand_ins  # noqa: SLF001
    return [
        base
        for base in linearize_data_models(direct_base_models(model, stand_ins), stand_ins)
        if base is not host._generic_base_model  # noqa: SLF001
    ]


def get_inherited_constructor_info(
    host: ConstructorInfoHost, model: DataModel, *, early: bool = False
) -> _InheritedConstructorInfo | None:
    """Return inherited value leaks and exact positional ordering conflicts.

    A required field that redeclares an inherited default is assigned ``Field(...)`` unless that default takes no
    place in the constructor (``AssignmentFacts.place_assigned``): a ``None`` that ``--strip-default-none`` strips but
    the base still writes, which the redeclared field inherits, as in 0.86.0.
    """
    if not model.SUPPORTS_KW_ONLY:
        return None
    if not (inherited_models := constructor_base_models(host, model, early=early)):
        return None
    if not early:
        record_ordering_inputs(model, inherited_models, host._base_stand_ins)  # noqa: SLF001

    supports_inherited_override = model.SUPPORTS_REQUIRED_INHERITED_FIELD_ASSIGNMENT
    required_assignment_names: set[str] = set()
    inherited_stripped_none_names: set[str] = set()
    effective_fields: dict[str, tuple[DataModelFieldBase, DataModel, bool | None]] = {}
    for inherited_model in reversed(inherited_models):
        for field in inherited_model.fields:
            if not (field_name := field.name):
                continue
            has_default: bool | None = None
            if supports_inherited_override:
                facts = model._field_assignment_facts(field)  # noqa: SLF001
                has_default, has_value_default = facts.default_info
                if has_value_default or (has_default and model.REQUIRES_EXPLICIT_INHERITED_FACTORY_OVERRIDE):
                    required_assignment_names.add(field_name)
                    if facts.place_assigned:
                        inherited_stripped_none_names.discard(field_name)
                    else:
                        inherited_stripped_none_names.add(field_name)
            effective_fields[field_name] = field, inherited_model, has_default
    for field in model.fields:
        if field.name:
            effective_fields[field.name] = field, model, None

    conflicts = get_constructor_ordering_conflicts(model, effective_fields, required_assignment_names)
    return _InheritedConstructorInfo(
        frozenset(required_assignment_names),
        frozenset(inherited_stripped_none_names),
        conflicts.own,
        conflicts.inherited,
    )


def get_constructor_field_adjustment(
    model: DataModel,
    field: DataModelFieldBase,
    inherited: _InheritedConstructorInfo,
    *,
    supports_inherited_override: bool,
) -> _ConstructorFieldAdjustment | None:
    """Return the exact explicit assignment needed for a required child field."""
    facts = model._field_assignment_facts(field)  # noqa: SLF001
    if not facts.participates_in_constructor:
        return None
    if field.name in inherited.ordering_conflicts:
        return "keyword_only"
    if facts.assigned:
        return None
    if (
        field.required
        and supports_inherited_override
        and field.name in inherited.required_assignment_names
        and field.name not in inherited.inherited_stripped_none_names
    ):
        return "assignment"
    return None


_TYPE_DEPENDENT_CONSTRAINTS_KEY: Final = TYPE_DEPENDENT_CONSTRAINTS_KEY


def _has_root_field_constraints(root_field: DataModelFieldBase) -> bool:
    """Return whether a root field has constraints, as Field() arguments or written inside its type."""
    return (isinstance(constraints := root_field.constraints, ConstraintsBase) and constraints.has_constraints) or any(
        data_type._consumed_constraints  # noqa: SLF001
        for data_type in (root_field.data_type, *root_field.data_type.data_types)
    )


def _assign_module_name(
    model_path_to_module_name: dict[str, str], model: DataModel, module_name: str, *, keep: bool = False
) -> None:
    """Give ``model`` the module name in the map, by model key too unless a subclass of the parser built the map."""
    if isinstance(model_path_to_module_name, _ModuleNames):
        model_path_to_module_name.assign(model, module_name, keep=keep)
    elif keep:
        model_path_to_module_name.setdefault(model.path, module_name)
    else:
        model_path_to_module_name[model.path] = module_name


def _iter_import_bindings(imports: Imports) -> Iterator[str]:
    """Yield names bound by one generated import block."""
    for from_, imported_names in imports.items():
        for imported_name in imported_names:
            effective_name = imports.get_effective_name(from_, imported_name)
            yield (
                imported_name.partition(".")[0] if from_ is None and effective_name == imported_name else effective_name
            )


def _parsed_rule_data_types(model: DataModel) -> Iterator[DataType]:
    """Yield the data types of the parser's schema validator rules of a model, nested ones included.

    A type alias holds them too until the parser writes it as a class that writes them.
    """
    if rules := model.schema_runtime_validation or model._unwritable_runtime_rules:  # noqa: SLF001
        for rule_data_type in rules.data_types:
            yield from rule_data_type.all_data_types


def _tag_field_matches(
    field: DataModelFieldBase, match: TagFieldMatch, wanted: str, *, python_name: str | None = None
) -> bool:
    """Return whether a field holds the discriminator's property ``wanted`` (``tag_field_matches``)."""
    return tag_field_matches(
        match, wanted, name=field.name, original_name=field.original_name, alias=field.alias, python_name=python_name
    )


def _keeps_instance_field(field: DataModelFieldBase, source_name: str) -> bool:
    """Return whether a field is an instance field that the document names ``source_name`` (``tag_field_kept``)."""
    return tag_field_kept(
        source_name,
        is_class_var=bool(field.extras.get("is_classvar")),
        name=field.name,
        original_name=field.original_name,
        alias=field.alias,
    )


def _bases_keep_instance_field(model: DataModel, source_name: str) -> bool:
    """Return whether a base of a model, or a base of that, keeps an instance field that the document names so."""
    return any(
        _keeps_instance_field(field, source_name)
        for base_model in _find_base_classes(model)
        for field in base_model.iter_all_fields()
    )


@dataclass(slots=True)
class _VariantDiscriminatorTags:
    """The tags a variant keeps from the first union that tagged it, and the other tags unions map it to."""

    variant: DataModel
    property_name: str
    kept: list[DiscriminatorValue]
    owner: str
    others: dict[str, list[DiscriminatorValue]]

    def warning_messages(self) -> Iterator[str]:
        """Describe the tags that the generated variant cannot carry."""
        if self.others:
            name = self.variant.class_name
            unions = ", ".join(f"{union} ({_format_tags(tags)})" for union, tags in self.others.items())
            yield (
                f"{name} is a variant of discriminated unions that map {self.property_name!r} to different tags: "
                f"it keeps {_format_tags(self.kept)} from {self.owner}, so these unions cannot decode it: {unions}."
            )


def _format_tags(tags: list[DiscriminatorValue]) -> str:
    """Spell discriminator tags as the document writes them."""
    return ", ".join(map(repr, tags))


def enum_discriminator_literal(enum_source: Enum, value: DiscriminatorValue) -> DiscriminatorValue:
    """Return the tag ``Literal`` value that a discriminator value takes from the member of its enum, if any."""
    member = enum_source.find_member(value, coerce_strings=True)
    return enum_tag_literal(value, member.value) if member else value


def enum_discriminator_members(enum_source: Enum, values: Iterable[DiscriminatorValue]) -> EnumTagMembers:
    """Return the members of an Enum that tag a variant whose tags are ``values`` (``enum_tag_members``)."""
    return enum_tag_members(
        (enum_source.find_member(value, coerce_strings=True) for value in values),
        lambda: [enum_source.get_member(field) for field in enum_source.fields],
    )


def _get_discriminator_field_value(discriminator_field: DataModelFieldBase) -> DiscriminatorValue | None:
    """Return the one tag a variant's tag field holds (``field_tag``)."""
    tag = field_tag("variant-field", _FieldTagFacts(discriminator_field))
    return None if tag is None else tag.value


def _get_enum_from_base(
    discriminator_model: DataModel, field_name: str, *, use_field_name: bool = False
) -> Enum | None:
    for base_class in discriminator_model.base_classes:
        if not base_class.reference or not base_class.reference.source:  # pragma: no cover
            continue
        base_model = base_class.reference.source
        if not isinstance(base_model, DataModel) or not base_model.SUPPORTS_INHERITED_DISCRIMINATOR_ENUM:
            continue
        for base_field in base_model.fields:  # pragma: no branch
            if not _tag_field_matches(
                base_field, "python-name" if use_field_name else "python-or-original-name", field_name
            ):
                continue
            if enum_from_base := base_field.data_type.find_source(Enum):  # pragma: no branch
                return enum_from_base
    return None


_STRING_LITERAL_SOURCE: Final = r"""'''[\s\S]*?'''|\"\"\"[\s\S]*?\"\"\"|'(?:\\.|[^'\\\n])*'|"(?:\\.|[^"\\\n])*"|"""
_STRING_OR_IDENTIFIER_PATTERN: Final = re.compile(
    _STRING_LITERAL_SOURCE + r"""^[ \t]*[A-Za-z_]\w*(?=:)|([A-Za-z_]\w*)""", re.MULTILINE
)
_STRING_OR_TARGET_IDENTIFIER_SOURCE: Final = _STRING_LITERAL_SOURCE + r"""^[ \t]*(?#identifier)(?=:)|((?#identifier))"""


def _string_or_identifier_pattern(text: str, target_python_version: PythonVersion) -> re.Pattern[str]:
    """Return the pattern that finds string literals, field declarations and names in text, as the target names them."""
    return (
        _STRING_OR_IDENTIFIER_PATTERN
        if text.isascii()
        else target_pattern(_STRING_OR_TARGET_IDENTIFIER_SOURCE, target_python_version, re.MULTILINE)
    )


def _normalize_result_module_path(module: ModulePath, *, treat_dot_as_module: bool | None) -> ModulePath:
    """Apply the module-key normalization used by the public parser result."""
    normalized = tuple(part.replace("-", "_") for part in module)
    if treat_dot_as_module:
        return normalized
    return tuple(part[: part.rfind(".")].replace(".", "_") + part[part.rfind(".") :] for part in normalized)


SPECIAL_PATH_FORMAT: str = "#-datamodel-code-generator-#-{}-#-special-#"


def get_special_path(keyword: str, path: list[str]) -> list[str]:
    """Create a special path marker for internal reference tracking."""
    return [*path, SPECIAL_PATH_FORMAT.format(keyword)]


def _forward_enum_member_literals(field: DataModelFieldBase, positions: _ModelPositions, index: int) -> list[DataType]:
    """Return the field's enum member literals whose enum the module defines at or after the model at ``index``.

    A member is a value, so a deferred annotation does not resolve it later: reading the class evaluates the member
    to a ``ForwardRef`` that stays inside the ``Literal``, unless the annotation is a string. ``positions`` gives each
    model of the module its order.
    """
    return [
        data_type
        for data_type in field.data_type.all_data_types
        if (reference := data_type._enum_member_literal_reference) is not None  # noqa: SLF001
        and positions.rank(reference) >= index
    ]


def _hidden_type_names(model: DataModel) -> Collection[str | None]:
    """Return the names of the model's fields that hide a type named by an annotation of the model.

    Python looks an annotation up in the class namespace first. A library that resolves string annotations
    from the module still finds such a type when it is written as a string, like a forward reference.
    """
    if not type(model).RESOLVES_STRING_ANNOTATIONS_FROM_MODULE:
        return ()
    return {
        reference.short_name
        for field in model.fields
        for data_type in field.data_type.all_data_types
        if (reference := _annotated_reference(data_type)) is not None and not data_type.alias
    } & {field.name for field in model.fields if model.field_name_hides_types(field)}


def unused_model_lists(
    host: ReplacedRootsHost, state: _FinalizeState[Any]
) -> Iterator[tuple[DataModel, ModulePath, list[DataModel]]]:
    """Yield each unused model with the module and the model list it leaves.

    A root model that collapsing replaced leaves the list of the module that writes it now.
    """
    replaced_root_modules = (
        {
            id(model): (ctx.module_key, ctx.models)
            for ctx in state.contexts
            for model in ctx.models
            if id(model) in replaced_root_models
        }
        if (replaced_root_models := host._replaced_root_models)  # noqa: SLF001
        else {}
    )
    for unused_model in state.unused_models:
        module, models = replaced_root_modules.pop(id(unused_model), None) or state.model_to_module_models[unused_model]
        yield unused_model, module, models
