"""Helpers that passes of more than one layer call, as functions of explicit arguments.

A helper that reads parser state takes it as arguments, or takes the parser through one of the narrow protocols below,
which ``Parser`` satisfies structurally. This module imports no parser module but the generation store, the import
ledger and the shared keys, so it never imports ``parser/base.py``.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from functools import cache
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, TypeAlias, cast

from typing_extensions import Protocol, final

from datamodel_code_generator import AllOfMergeMode
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model.base import _find_base_classes, get_inherited_fields
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._keys import (
    _DEFERRED_INHERITED_CLASS_KEY,
    _DEFERRED_INHERITED_FIELD_KEY,
    _DEFERRED_INHERITED_TYPE_KEY,
    _RAW_SCHEMA_DEFAULT_KEY,
    _RAW_SCHEMA_DEFAULT_UNDEFINED,
)
from datamodel_code_generator.python_literal import (
    PythonRuntimeExpression,
    rewrite_runtime_expressions,
    rewrite_runtime_imports,
)
from datamodel_code_generator.reference import ModelType
from datamodel_code_generator.types import DataType, DefaultValueRecipe

if TYPE_CHECKING:
    from collections.abc import Collection, Iterable, Iterator, Mapping

    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._null_facts import NullRule
    from datamodel_code_generator.parser.generation import GenerationStore, GraphBuilder
    from datamodel_code_generator.reference import ModelResolver, Reference
    from datamodel_code_generator.types import DataTypeManager, DefaultValueDescriptor

_SHADOWED_IMPORTS: Final = ImportProducer("shadowed-imports")


@dataclass(frozen=True, slots=True)
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
    models: list[DataModel],
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Rewrite every identity-carrying structured consumer of selected imports."""
    for model in models:
        for field in model.fields:
            _alias_field_runtime_expressions(field, aliased_imports)
            for data_type in field.data_type.all_data_types:
                _alias_data_type_structured_imports(data_type, aliased_imports)
        _alias_additional_imports(model, aliased_imports)
    for model in models:
        _alias_base_class_imports(model, aliased_imports)


def _alias_field_runtime_expressions(
    field: DataModelFieldBase,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Rewrite a field only when its producer registered a runtime expression."""
    imports = field.runtime_expression_imports
    if not imports or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports:
        return
    field.default = rewrite_runtime_expressions(field.default, aliased_imports)
    field.extras = cast("dict[str, Any]", rewrite_runtime_expressions(field.extras, aliased_imports))
    field._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001


def _alias_data_type_structured_imports(
    data_type: DataType,
    aliased_imports: dict[_PythonTypeImportKey, Import],
) -> None:
    """Apply aliases to type, annotation, and producer-registered kwargs expressions."""
    if (
        data_type.import_
        and (aliased_import := aliased_imports.get((data_type.import_.from_, data_type.import_.import_))) is not None
        and aliased_import is not data_type.import_
    ):
        data_type.type = aliased_import.alias
        data_type.import_ = aliased_import
    if data_type.python_type:
        alias_bound_python_type, render_python_type_expr = _python_type_import_alias_helpers()
        bound_type = alias_bound_python_type(data_type.python_type, aliased_imports)
        if bound_type is not data_type.python_type:
            data_type.python_type = bound_type
            data_type.type = render_python_type_expr(bound_type.expression)
    if (
        not (imports := data_type.runtime_expression_imports)
        or (rewritten_imports := rewrite_runtime_imports(imports, aliased_imports)) is imports
    ):
        return
    data_type.kwargs = cast("dict[str, Any]", rewrite_runtime_expressions(data_type.kwargs, aliased_imports))
    data_type._set_runtime_expression_imports(rewritten_imports)  # noqa: SLF001


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
            colliding_field.name = field.name
            if colliding_field.alias is None:
                colliding_field.alias = colliding_field.original_name
            field.name = inherited_name
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
@dataclass(frozen=True, slots=True)
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
    users = builder.redirect_reference_users(duplicate.reference, canonical.reference)
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
        data_type.kwargs = kwargs
        runtime_imports = data_type.runtime_expression_imports
        data_type._set_runtime_expression_imports((*runtime_imports, descriptor.constructor_import))  # noqa: SLF001
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
            _restore_inherited_field_names_for_unique_aliases(resolved_fields, inherited_fields)
        builder.set_fields(model, resolved_fields)


def get_constructor_ordering_conflicts(
    model: DataModel,
    effective_fields: Mapping[str, tuple[DataModelFieldBase, DataModel, bool | None]],
    required_assignment_names: set[str],
) -> frozenset[str]:
    """Return child positional fields that follow an effective positional default."""
    seen_constructor_default = False
    seen_signature_default = False
    ordering_conflicts: set[str] = set()
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
        if declaring_model is model and (
            (seen_constructor_default and not field_has_constructor_default)
            or (seen_signature_default and not field_has_signature_default)
        ):
            ordering_conflicts.add(field_name)
        seen_constructor_default = seen_constructor_default or field_has_constructor_default
        seen_signature_default = seen_signature_default or field_has_signature_default
    return frozenset(ordering_conflicts)


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
            _apply_structured_import_aliases(models, ordinary_aliases)
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
    _apply_structured_import_aliases(models, aliased_imports)
    if module_imports is not None:
        for aliased_import in aliased_imports.values():
            host._import_ledger.apply_alias(module_imports, aliased_import, _SHADOWED_IMPORTS)  # noqa: SLF001
