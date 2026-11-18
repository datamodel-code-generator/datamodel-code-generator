"""Public API signature baselines from origin/main."""

from __future__ import annotations

import ast
import importlib
import inspect
import pkgutil
import sys
import types
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef, TypeVar, Union, get_args, get_origin

import pytest
from typing_extensions import NotRequired, TypeAliasType

from datamodel_code_generator import DEFAULT_SHARED_MODULE_NAME, generate
from datamodel_code_generator.enums import (
    AliasGenerator,
    AllExportsCollisionStrategy,
    AllExportsScope,
    AllOfClassHierarchy,
    AllOfMergeMode,
    ClassNameAffixScope,
    ClientAsyncAPIPerspective,
    ClientBodyArguments,
    ClientSignatureStyle,
    ClientType,
    CollapseRootModelsNameStrategy,
    CustomFileHeaderMode,
    DataModelType,
    DefaultValueType,
    FieldTypeCollisionStrategy,
    GraphQLScope,
    HTTPBackend,
    InputFileType,
    ModuleSplitMode,
    NamingStrategy,
    OpenAPIScope,
    OpenEnums,
    ReadOnlyWriteOnlyModelType,
    ReuseScope,
    SchemaValidatorType,
    ServerBodyMode,
    ServerHandlerMode,
    ServerLayout,
    ServerType,
    TargetPydanticVersion,
    VersionMode,
)
from datamodel_code_generator.format import PythonVersionMin
from datamodel_code_generator.model.pydantic_v2 import BaseModel, DataModelField, DataTypeManager, RootModel
from datamodel_code_generator.parser.base import YamlValue, title_to_class_name
from tests.conftest import assert_output

if TYPE_CHECKING:
    from collections import defaultdict
    from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
    from typing import Unpack
    from urllib.parse import ParseResult

    from datamodel_code_generator import GeneratedModules
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import ConstructorFieldOrder, OpenAPIAsDefinitions
    from datamodel_code_generator.format import DateClassType, DatetimeClassType, Formatter, PythonVersion
    from datamodel_code_generator.model import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.dataclass import DataclassArguments
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.parser import DefaultPutDict, LiteralType
    from datamodel_code_generator.parser.base import ModuleContext, ModulePath, ParseConfig, Result
    from datamodel_code_generator.preset_names import PresetName
    from datamodel_code_generator.types import StrictTypes
    from datamodel_code_generator.validators import ModelValidators


_PUBLIC_PACKAGE = "datamodel_code_generator"
_KIND_DECORATORS = frozenset({
    "staticmethod",
    "classmethod",
    "property",
    "abstractmethod",
    "overload",
    "cached_property",
})
_KIND_DECORATOR_SUFFIXES = (".setter", ".getter", ".deleter", ".abstractmethod", ".overload")
_SCALAR_TYPES = (bool, int, float, str, bytes, type(None))
_TYPE_FORMS = (type, TypeVar, TypeAliasType, getattr(sys.modules["typing"], "TypeAliasType", TypeAliasType))

_Definition = ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef


def _public_module_names(package: types.ModuleType) -> list[str]:
    """Name the package and every module below it whose path has no part with a leading underscore."""
    names = [package.__name__]
    for info in pkgutil.iter_modules(package.__path__):
        if info.name.startswith("_"):
            continue
        name = f"{package.__name__}.{info.name}"
        names.extend(_public_module_names(importlib.import_module(name)) if info.ispkg else [name])
    return names


def _in_package(module_name: str) -> bool:
    return module_name.partition(".")[0] == _PUBLIC_PACKAGE


def _is_public_member(name: str) -> bool:
    """Members without a leading underscore and special methods such as ``__str__`` are public."""
    return not name.startswith("_") or (name.startswith("__") and name.endswith("__"))


@cache
def _source_tree(module_name: str) -> ast.Module:
    return ast.parse(Path(inspect.getfile(sys.modules[module_name])).read_text(encoding="utf-8"))


def _runtime_statements(body: list[ast.stmt]) -> Iterator[ast.stmt]:
    """Yield the module-level statements an import runs, the branch under ``if TYPE_CHECKING:`` left out."""
    for node in body:
        match node:
            case ast.If(test=ast.Name(id="TYPE_CHECKING")):
                yield from _runtime_statements(node.orelse)
            case ast.If():
                yield from _runtime_statements([*node.body, *node.orelse])
            case ast.Try():
                handlers = [statement for handler in node.handlers for statement in handler.body]
                yield from _runtime_statements([*node.body, *handlers, *node.orelse])
            case _:
                yield node


def _bound_names(node: ast.stmt) -> list[str]:
    match node:
        case ast.FunctionDef() | ast.AsyncFunctionDef() | ast.ClassDef():
            return [node.name]
        case ast.Assign():
            return [target.id for target in node.targets if isinstance(target, ast.Name)]
        case ast.AnnAssign(target=ast.Name(id=target)) if node.value is not None:
            return [target]
        case ast.ImportFrom() | ast.Import():
            return [alias.asname or alias.name.partition(".")[0] for alias in node.names]
    return []


def _bindings(module_name: str, name: str) -> list[ast.stmt]:
    return [node for node in _runtime_statements(_source_tree(module_name).body) if name in _bound_names(node)]


def _public_names(module: types.ModuleType) -> tuple[list[str] | None, list[str]]:
    """Read ``__all__``, the names the module's ``__dir__`` lists, and the names its own statements bound on import.

    A name a lazy ``__getattr__`` answers counts once ``__all__`` or ``__dir__`` lists it: the names it stores in the
    module as it answers are not read, so the text does not depend on what ran before.
    """
    declared = getattr(module, "__all__", None)
    bound = (name for node in _runtime_statements(_source_tree(module.__name__).body) for name in _bound_names(node))
    listed = dir(module) if "__dir__" in vars(module) else (name for name in bound if name in vars(module))
    return declared, sorted({*(declared or ()), *(name for name in listed if not name.startswith("_"))})


def _qualified_definitions(node: ast.AST, prefix: str) -> Iterator[tuple[str, _Definition]]:
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            yield f"{prefix}{child.name}", child
            yield from _qualified_definitions(child, f"{prefix}{child.name}.")
        elif isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef):
            yield f"{prefix}{child.name}", child
            yield from _qualified_definitions(child, f"{prefix}{child.name}.<locals>.")
        else:
            yield from _qualified_definitions(child, prefix)


def _definition_nodes(module_name: str, qualname: str) -> list[_Definition]:
    """Find a class or function by its qualified name, or by its name where the code sets ``__qualname__``."""
    definitions = list(_qualified_definitions(_source_tree(module_name), ""))
    found = [node for qualified, node in definitions if qualified == qualname]
    return found or [node for _, node in definitions if node.name == qualname.rpartition(".")[2]]


def _is_definition(node: ast.stmt) -> bool:
    return not isinstance(node, ast.Import | ast.ImportFrom)


def _is_routine(value: object) -> bool:
    """Tell a class or a function, seen through the wrappers ``functools`` puts around it."""
    return isinstance(value, type) or inspect.isfunction(inspect.unwrap(value))


def _defining_site(value: object, name: str) -> tuple[str, str] | None:
    """Find the package statement that defines an object.

    It is where ``__module__`` and ``__qualname__`` point when a statement there defines a class or function, else a
    package module whose own statement binds the object under its name (``__module__`` may name a legacy module).
    """
    if _is_routine(value) and _in_package(value.__module__) and _definition_nodes(value.__module__, value.__qualname__):
        return value.__module__, value.__qualname__
    return next(
        (
            (other, name)
            for other in sorted(sys.modules)
            if _in_package(other)
            and vars(sys.modules[other]).get(name) is value
            and any(_is_definition(node) for node in _bindings(other, name))
        ),
        None,
    )


def _import_origin(module_name: str, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""
    parts = module_name.split(".")
    parts = parts[: len(parts) - node.level + hasattr(sys.modules[module_name], "__path__")]
    return ".".join([*parts, *([node.module] if node.module else [])])


def _value_site(module_name: str, name: str, value: object) -> tuple[str, str] | str | None:
    """Follow a value through the imports of the source to the module whose statement defines it.

    An import from outside the package ends the walk as ``origin.name``; a name no statement binds (a lazy export)
    is looked up among the package modules whose own statement binds the same object under that name.
    """
    texts: list[str] = []
    for node in _bindings(module_name, name):
        match node:
            case ast.ImportFrom():
                alias = next(alias for alias in node.names if (alias.asname or alias.name) == name)
                origin = _import_origin(module_name, node)
                if _in_package(origin):
                    return _value_site(origin, alias.name, value)
                texts.append(f"{origin}.{alias.name}")
            case _:
                return module_name, name
    if texts:
        return " | ".join(texts)
    return _defining_site(value, name)


def _decorator_texts(node: _Definition, *, every: bool) -> list[str]:
    texts = [ast.unparse(decorator) for decorator in node.decorator_list]
    return [
        f"@{text}" for text in texts if every or text in _KIND_DECORATORS or text.endswith(_KIND_DECORATOR_SUFFIXES)
    ]


def _function_text(node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
    keyword = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    signature = f"{keyword} {node.name}({ast.unparse(node.args)}){returns}"
    return " ".join([*_decorator_texts(node, every=False), signature])


def _is_type_expression(value: object) -> bool:
    return isinstance(value, _TYPE_FORMS) or get_origin(value) is not None


def _assignment_texts(node: ast.Assign | ast.AnnAssign, indent: str, *, value_shown: bool) -> list[str]:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    annotation = f": {ast.unparse(node.annotation)}" if isinstance(node, ast.AnnAssign) else ""
    value = f" = {ast.unparse(node.value)}" if node.value is not None and value_shown else ""
    return [
        f"{indent}{target.id}{annotation}{value}"
        for target in targets
        if isinstance(target, ast.Name) and _is_public_member(target.id)
    ]


def _statement_texts(node: ast.stmt, indent: str, *, value_shown: bool) -> list[str]:
    match node:
        case ast.ClassDef():
            return _class_texts(node, indent)
        case ast.FunctionDef() | ast.AsyncFunctionDef():
            return [f"{indent}{_function_text(node)}"]
        case ast.Assign() | ast.AnnAssign():
            return _assignment_texts(node, indent, value_shown=value_shown)
    return []


def _class_texts(node: ast.ClassDef, indent: str) -> list[str]:
    bases = ", ".join(ast.unparse(base) for base in [*node.bases, *node.keywords])
    texts = [f"{indent}class {node.name}({bases})"]
    texts.extend(f"{indent}  {decorator}" for decorator in _decorator_texts(node, every=True))
    for item in node.body:
        if not isinstance(item, _Definition) or _is_public_member(item.name):
            texts.extend(_statement_texts(item, f"{indent}  ", value_shown=True))
    return texts


class _Surface:
    """Render public names and the definitions they reach, each definition once, from its source."""

    def __init__(self) -> None:
        self.rendered: set[tuple[str, str]] = set()
        self.reached: dict[tuple[str, str], object] = {}

    def definition_texts(self, site: tuple[str, str], value: object) -> list[str]:
        self.rendered.add(site)
        nodes = [node for node in _bindings(*site) if _is_definition(node)] or _definition_nodes(*site)
        routine = _is_routine(value)
        shown = routine or isinstance(value, _SCALAR_TYPES) or _is_type_expression(value)
        followed = [value] if routine and isinstance(nodes[0], ast.Assign | ast.AnnAssign) else []
        self.reached.update(
            (target_site, target)
            for target in [*followed, *getattr(value, "__mro__", ())[1:]]
            if (target_site := _defining_site(target, target.__name__)) is not None
        )
        return [text for node in nodes for text in _statement_texts(node, "", value_shown=shown)]

    def entry_texts(self, module_name: str, name: str, value: object) -> list[str]:
        if isinstance(value, types.ModuleType):
            return [f"{name} = module {value.__name__}"]
        site = _value_site(module_name, name, value)
        if site is None:
            return [f"{name} = lazy {type(value).__name__}"]
        if isinstance(site, str):
            return [f"{name} = import {site}"]
        if site == (module_name, name):
            return self.definition_texts(site, value)
        self.reached[site] = value
        return [f"{name} = {'.'.join(site)}"]

    def module_texts(self, module: types.ModuleType) -> list[str]:
        module_name = module.__name__
        declared, names = _public_names(module)
        texts = [f"[{module_name}]"]
        if declared is not None:
            texts.append(f"__all__ = {sorted(set(declared))}")
        for name in names:
            texts.extend(self.entry_texts(module_name, name, getattr(module, name)))
        return [*texts, ""]

    def reached_texts(self) -> list[str]:
        texts: list[str] = []
        while pending := sorted(self.reached.keys() - self.rendered):
            for site in pending:
                texts.extend([f"[{site[0]}] reached {site[1]}", *self.definition_texts(site, self.reached[site]), ""])
        return texts


def _public_surface() -> str:
    """Write every public name of every public module, followed to the source that defines it, as one text."""
    module_names = sorted(_public_module_names(sys.modules[_PUBLIC_PACKAGE]))
    modules = [importlib.import_module(module_name) for module_name in module_names]
    surface = _Surface()
    texts = [text for module in modules for text in surface.module_texts(module)]
    return "\n".join([*texts, *surface.reached_texts()])


def _baseline_generate(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    input_filename: str | None = None,
    input_file_type: InputFileType = InputFileType.Auto,
    output: Path | None = None,
    emit_model_metadata: Path | None = None,
    output_model_type: DataModelType = DataModelType.PydanticV2BaseModel,
    preset: PresetName | None = None,
    read_implied_options_from_merged_values: bool = False,
    target_python_version: PythonVersion = PythonVersionMin,
    target_pydantic_version: TargetPydanticVersion | None = None,
    base_class: str = "",
    base_class_map: dict[str, str | list[str]] | None = None,
    model_name_map: dict[str, str] | None = None,
    additional_imports: list[str] | None = None,
    class_decorators: list[str] | None = None,
    custom_template_dir: Path | None = None,
    schema_validator_type: SchemaValidatorType | None = None,
    extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
    validators: Mapping[str, ModelValidators] | None = None,
    generate_schema_validators: bool = False,
    check_pattern_maps_strictly: bool = False,
    schema_validator_base_class_name: str | None = None,
    validation: bool = False,
    field_constraints: bool = False,
    alias_generator: AliasGenerator | None = None,
    pin_aliases_with_alias_generator: bool = False,
    snake_case_field: bool = False,
    strip_default_none: bool = False,
    aliases: Mapping[str, str | list[str]] | None = None,
    serialization_aliases: Mapping[str, str] | None = None,
    default_value_overrides: Mapping[str, Any] | None = None,
    disable_timestamp: bool = False,
    enable_version_header: bool = False,
    enable_command_header: bool = False,
    enable_generated_header_marker: bool = False,
    command_line: str | None = None,
    allow_population_by_field_name: bool = False,
    allow_extra_fields: bool = False,
    extra_fields: str | None = None,
    use_generic_base_class: bool = False,
    apply_default_values_for_required_fields: bool = False,
    force_optional_for_required_fields: bool = False,
    class_name: str | None = None,
    allow_leading_underscore_class_name: bool = False,
    class_name_prefix: str | None = None,
    class_name_suffix: str | None = None,
    class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All,
    use_standard_collections: bool = True,
    use_schema_description: bool = False,
    use_field_description: bool = False,
    use_field_description_example: bool = False,
    use_attribute_docstrings: bool = False,
    use_inline_field_description: bool = False,
    use_single_line_docstring: bool = False,
    use_default_kwarg: bool = False,
    apply_field_options_to_every_field: bool = False,
    deserialize_default_values: Sequence[DefaultValueType] = (),
    use_missing_sentinel: bool = False,
    reuse_model: bool = False,
    reuse_scope: ReuseScope = ReuseScope.Module,
    shared_module_name: str = DEFAULT_SHARED_MODULE_NAME,
    encoding: str = "utf-8",
    enum_field_as_literal: LiteralType | None = None,
    enum_field_as_literal_map: dict[str, str] | None = None,
    open_enums: OpenEnums | None = None,
    open_enum_base_class_map: dict[str, str] | None = None,
    ignore_enum_constraints: bool = False,
    use_one_literal_as_default: bool = False,
    use_enum_values_in_discriminator: bool = False,
    merge_shared_variant_tags: bool = False,
    use_unmapped_variant_tag_values: bool = False,
    read_schema_version_per_document: bool = False,
    read_escaped_ref_pointers: bool = False,
    read_dynamic_anchors_in_document_order: bool = False,
    read_ids_with_empty_fragment: bool = False,
    read_single_allof_refs_in_their_documents: bool = False,
    read_ref_siblings_in_closed_allof_members: bool = False,
    ignore_ref_siblings_by_schema_version: bool = False,
    read_yaml_1_1_numbers_as_text: bool = False,
    use_msgspec_tagged_unions: bool = False,
    tag_msgspec_by_payload_name: bool = False,
    use_msgspec_nullable_enum_alias: bool = False,
    use_msgspec_forbid_unknown_fields: bool = False,
    use_typed_nullable_map_values: bool = False,
    use_typed_map_values_beside_other_types: bool = False,
    use_single_dict_for_object_maps: bool = False,
    read_unevaluated_properties: bool = False,
    read_kubernetes_extensions: bool = False,
    use_single_value_enum_as_const: bool = False,
    read_const_beside_enum: bool = False,
    read_format_as_annotation: bool = False,
    filter_literal_values: bool = False,
    intersect_allof_keywords: bool = False,
    intersect_combined_schema_keywords: bool = False,
    distribute_allof_over_unions: bool = False,
    read_allof_parents_in_mro_order: bool = False,
    read_inherited_property_schemas_in_mro_order: bool = False,
    read_nullable_beside_type_list: bool = False,
    keep_field_defaults_off_branch_roots: bool = False,
    keep_enum_member_values: bool = False,
    set_default_enum_member: bool = False,
    use_subclass_enum: bool = False,
    use_specialized_enum: bool = True,
    strict_nullable: bool = False,
    omit_redundant_reference_null: bool = False,
    keep_reference_null_on_merged_models: bool = False,
    use_generic_container_types: bool = False,
    enable_faux_immutability: bool = False,
    disable_appending_item_suffix: bool = False,
    singularize_last_word_of_item_names: bool = False,
    strict_types: Sequence[StrictTypes] | None = None,
    empty_enum_field_name: str | None = None,
    custom_class_name_generator: Callable[[str], str] | None = None,
    field_extra_keys: set[str] | None = None,
    field_include_all_keys: bool = False,
    field_extra_keys_without_x_prefix: set[str] | None = None,
    model_extra_keys: set[str] | None = None,
    model_extra_keys_without_x_prefix: set[str] | None = None,
    openapi_scopes: list[OpenAPIScope] | None = None,
    include_path_parameters: bool = False,
    openapi_include_paths: list[str] | None = None,
    openapi_include_info_version: bool = False,
    openapi_as_definitions: Sequence[OpenAPIAsDefinitions] = (),
    graphql_scopes: list[GraphQLScope] | None = None,
    graphql_no_typename: bool = False,
    wrap_string_literal: bool | None = None,
    use_title_as_name: bool = False,
    infer_union_variant_names: bool = False,
    use_tag_as_union_variant_name: bool = False,
    infer_union_discriminators: bool = False,
    name_allof_union_members_after_branches: bool = False,
    use_operation_id_as_name: bool = False,
    use_unique_items_as_set: bool = False,
    use_unique_items_as_set_by_value: bool = False,
    use_tuple_for_fixed_items: bool = False,
    use_tuple_for_closed_arrays: bool = False,
    use_tuple_for_fixed_length_arrays: bool = False,
    use_total_false_for_typed_dict: bool = False,
    use_closed_typed_dict: bool = True,
    allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints,
    allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict,
    allow_remote_refs: bool | None = None,
    strict_refs: bool = False,
    untrusted_input: bool = False,
    allow_private_network: bool = False,
    http_backend: HTTPBackend = HTTPBackend.AUTO,
    http_headers: Sequence[tuple[str, str]] | None = None,
    http_local_ref_path: Path | None = None,
    http_ignore_tls: bool = False,
    http_timeout: float | None = None,
    lockfile: Path | None = None,
    update_lock: bool = False,
    locked: bool = False,
    use_annotated: bool = False,
    use_serialize_as_any: bool = False,
    use_serialize_as_any_for_every_subclass: bool = False,
    use_non_positive_negative_number_constrained_types: bool = False,
    use_decimal_for_multiple_of: bool = False,
    use_decimal_in_field_forms: bool = False,
    use_encoded_str_for_constrained_base64: bool = False,
    use_strict_str_for_base64: bool = False,
    use_msgspec_meta_for_map_keys: bool = False,
    omit_redundant_constraints: bool = False,
    read_length_keywords_by_type: bool = False,
    read_object_keywords_by_type: bool = False,
    read_msgspec_union_bounds_exactly: bool = False,
    read_input_model_dataclass_fields_by_alias: bool = False,
    reuse_every_reached_input_model_type: bool = False,
    use_site_models_for_constrained_refs: bool = False,
    use_one_model_per_ref_site: bool = False,
    original_field_name_delimiter: str | None = None,
    use_double_quotes: bool = False,
    use_union_operator: bool = True,
    collapse_root_models: bool = False,
    collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None,
    collapse_shared_parent_wrappers: bool = False,
    keep_constrained_root_models_in_every_container: bool = False,
    keep_enum_literal_aliases: bool = False,
    keep_reference_null_on_collapsed_root_models: bool = False,
    keep_shared_members_on_collapsed_root_models: bool = False,
    collapse_constrained_root_models_as_annotated: bool = False,
    remove_unused_models_beside_type_overrides: bool = False,
    keep_self_referencing_array_items: bool = False,
    keep_defaults_of_inherited_required_fields: bool = False,
    omit_incomplete_model_defaults: bool = False,
    collapse_reuse_models: bool = False,
    skip_root_model: bool = False,
    use_root_model_sequence_interface: bool = False,
    use_type_alias: bool = False,
    use_type_alias_type: bool = False,
    use_root_model_type_alias: bool = False,
    use_validate_default_for_root_model_type_aliases: bool = False,
    use_plain_alias_for_recursive_models: bool = False,
    special_field_name_prefix: str | None = None,
    remove_special_field_name_prefix: bool = False,
    capitalise_enum_members: bool = False,
    capitalise_numbered_enum_members: bool = False,
    keep_model_order: bool = False,
    order_constructor_fields_by: Sequence[ConstructorFieldOrder] = (),
    custom_file_header: str | None = None,
    custom_file_header_path: Path | None = None,
    custom_file_header_mode: CustomFileHeaderMode = CustomFileHeaderMode.Replace,
    custom_formatters: list[str] | None = None,
    custom_formatters_kwargs: dict[str, Any] | None = None,
    use_pendulum: bool = False,
    use_standard_primitive_types: bool = False,
    use_object_type: bool = False,
    untyped_value_type: str | None = None,
    http_query_parameters: Sequence[tuple[str, str]] | None = None,
    treat_dot_as_module: bool | None = None,
    strict_dotted_module_names: bool = False,
    rename_submodules_hidden_by_classes: bool = False,
    use_exact_imports: bool = False,
    use_type_checking_imports: bool | None = None,
    union_mode: UnionMode | None = None,
    output_datetime_class: DatetimeClassType | None = None,
    output_date_class: DateClassType | None = None,
    keyword_only: bool = False,
    frozen_dataclasses: bool = False,
    no_alias: bool = False,
    use_serialization_alias: bool = False,
    use_frozen_field: bool = False,
    use_default_factory_for_optional_nested_models: bool = False,
    formatters: list[Formatter] | None = None,
    builtin_format_line_length: int | None = None,
    settings_path: Path | None = None,
    parent_scoped_naming: bool = False,
    naming_strategy: NamingStrategy | None = None,
    duplicate_name_suffix: dict[str, str] | None = None,
    use_duplicate_name_suffix_for_every_collision: bool = False,
    dataclass_arguments: DataclassArguments | None = None,
    keep_false_dataclass_arguments: bool = False,
    disable_future_imports: bool = False,
    import_overrides: dict[str, str] | None = None,
    type_mappings: list[str] | None = None,
    type_overrides: dict[str, str] | None = None,
    type_overrides_replace_whole_type: bool = False,
    use_x_python_type_as_written: bool = False,
    use_x_python_import_for_every_ref: bool = False,
    use_bytes_for_content_media_type: bool = False,
    use_bounds_for_float_const: bool = False,
    read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None,
    dedupe_request_response_variants: bool = False,
    dedupe_numbered_request_response_variants: bool = False,
    use_status_code_in_response_name: bool = False,
    keep_first_character_of_path_keys: bool = False,
    keep_content_parameter_refs: bool = False,
    skip_path_extension_keys: bool = False,
    use_enum_field_as_literal_in_operations: bool = False,
    name_converted_payloads_by_message: bool = False,
    merge_message_trait_headers: bool = False,
    all_exports_scope: AllExportsScope | None = None,
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
    field_type_collision_strategy: FieldTypeCollisionStrategy | None = None,
    module_split_mode: ModuleSplitMode | None = None,
    schema_version: str | None = None,
    schema_version_mode: VersionMode | None = None,
    external_ref_mapping: dict[str, str] | None = None,
    generate_server: ServerType | None = None,
    server_output: Path | None = None,
    server_package: str | None = None,
    server_model_package: str | None = None,
    server_layout: ServerLayout | None = None,
    server_handler_mode: ServerHandlerMode | None = None,
    server_handler_modes: Mapping[str, ServerHandlerMode] | None = None,
    server_include_request: bool | None = None,
    server_body_mode: ServerBodyMode | None = None,
    server_body_modes: Mapping[str, ServerBodyMode] | None = None,
    server_primary_responses: Mapping[str, Any] | None = None,
    server_operation_names: Mapping[str, str] | None = None,
    server_router_names: Mapping[str, str] | None = None,
    server_parameter_names: Mapping[str, Mapping[str, str]] | None = None,
    generate_client: ClientType | None = None,
    client_output: Path | None = None,
    client_package: str | None = None,
    client_model_package: str | None = None,
    client_signature_style: ClientSignatureStyle | None = None,
    client_body_arguments: ClientBodyArguments | None = None,
    client_resource_names: Mapping[str, str] | None = None,
    client_operations: Mapping[str, Mapping[str, Any]] | None = None,
    client_default_base_url: str | None = None,
    client_server_base_url: str | None = None,
    client_deep_object_brackets: bool | None = None,
    client_asyncapi_perspective: ClientAsyncAPIPerspective | None = None,
    client_protocols: Mapping[str, Any] | None = None,
) -> str | object | None:
    raise NotImplementedError


def _baseline_generate_runtime_signature(
    input_: Path | str | ParseResult | Mapping[str, Any] | list[Any],
    *,
    config: GenerateConfig | None = None,
    **options: Unpack[GenerateConfigDict],
) -> str | GeneratedModules | None:
    raise NotImplementedError


def _baseline_parser_parse_runtime_signature(
    self,  # noqa: ANN001
    with_import: bool | None = True,
    format_: bool | None = True,
    settings_path: Path | None = None,
    disable_future_imports: bool = False,
    all_exports_scope: AllExportsScope | None = None,
    all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
    module_split_mode: ModuleSplitMode | None = None,
    collect_model_metadata: bool = False,
) -> str | dict[tuple[str, ...], Result]:
    raise NotImplementedError


def _baseline_process_single_module_runtime_signature(
    self,  # noqa: ANN001
    module_: ModulePath,
    models: list[DataModel],
    results: dict[ModulePath, Result],
    config: ParseConfig,
    internal_modules: set[ModulePath],
    model_path_to_module_name: dict[str, str],
    require_update_action_models: list[str],
    unused_models: list[DataModel],
) -> ModuleContext:
    raise NotImplementedError


def _baseline_openapi_parser_parse_runtime_signature(
    self,  # noqa: ANN001
    *args: Any,
    **kwargs: Any,
) -> str | dict[tuple[str, ...], Result]:
    raise NotImplementedError


class _BaselineParser:
    def __init__(
        self,
        source: str | Path | list[Path] | ParseResult | dict[str, YamlValue],
        *,
        data_model_type: type[DataModel] = BaseModel,
        data_model_root_type: type[DataModel] = RootModel,
        data_type_manager_type: type[DataTypeManager] = DataTypeManager,
        data_model_field_type: type[DataModelFieldBase] = DataModelField,
        base_class: str | None = None,
        base_class_map: dict[str, str | list[str]] | None = None,
        model_name_map: dict[str, str] | None = None,
        additional_imports: list[str] | None = None,
        class_decorators: list[str] | None = None,
        custom_template_dir: Path | None = None,
        extra_template_data: defaultdict[str, dict[str, Any]] | None = None,
        validators: Mapping[str, ModelValidators] | None = None,
        generate_schema_validators: bool = False,
        check_pattern_maps_strictly: bool = False,
        schema_validator_base_class_name: str | None = None,
        target_python_version: PythonVersion = PythonVersionMin,
        dump_resolve_reference_action: Callable[[Iterable[str]], str] | None = None,
        validation: bool = False,
        field_constraints: bool = False,
        alias_generator: AliasGenerator | None = None,
        pin_aliases_with_alias_generator: bool = False,
        snake_case_field: bool = False,
        strip_default_none: bool = False,
        aliases: Mapping[str, str | list[str]] | None = None,
        serialization_aliases: Mapping[str, str] | None = None,
        default_value_overrides: Mapping[str, Any] | None = None,
        allow_population_by_field_name: bool = False,
        apply_default_values_for_required_fields: bool = False,
        allow_extra_fields: bool = False,
        extra_fields: str | None = None,
        use_generic_base_class: bool = False,
        force_optional_for_required_fields: bool = False,
        class_name: str | None = None,
        allow_leading_underscore_class_name: bool = False,
        class_name_prefix: str | None = None,
        class_name_suffix: str | None = None,
        class_name_affix_scope: ClassNameAffixScope = ClassNameAffixScope.All,
        use_standard_collections: bool = False,
        base_path: Path | None = None,
        use_schema_description: bool = False,
        use_field_description: bool = False,
        use_field_description_example: bool = False,
        use_attribute_docstrings: bool = False,
        use_inline_field_description: bool = False,
        use_single_line_docstring: bool = False,
        use_default_kwarg: bool = False,
        apply_field_options_to_every_field: bool = False,
        deserialize_default_values: Sequence[DefaultValueType] = (),
        use_missing_sentinel: bool = False,
        reuse_model: bool = False,
        reuse_scope: ReuseScope | None = None,
        shared_module_name: str = DEFAULT_SHARED_MODULE_NAME,
        encoding: str = "utf-8",
        enum_field_as_literal: LiteralType | None = None,
        enum_field_as_literal_map: dict[str, str] | None = None,
        open_enums: OpenEnums | None = None,
        open_enum_base_class_map: dict[str, str] | None = None,
        ignore_enum_constraints: bool = False,
        set_default_enum_member: bool = False,
        use_subclass_enum: bool = False,
        use_specialized_enum: bool = True,
        strict_nullable: bool = False,
        omit_redundant_reference_null: bool = False,
        keep_reference_null_on_merged_models: bool = False,
        use_generic_container_types: bool = False,
        enable_faux_immutability: bool = False,
        remote_text_cache: DefaultPutDict[str, str] | None = None,
        disable_appending_item_suffix: bool = False,
        singularize_last_word_of_item_names: bool = False,
        strict_types: Sequence[StrictTypes] | None = None,
        empty_enum_field_name: str | None = None,
        custom_class_name_generator: Callable[[str], str] | None = title_to_class_name,
        field_extra_keys: set[str] | None = None,
        field_include_all_keys: bool = False,
        field_extra_keys_without_x_prefix: set[str] | None = None,
        model_extra_keys: set[str] | None = None,
        model_extra_keys_without_x_prefix: set[str] | None = None,
        wrap_string_literal: bool | None = None,
        use_title_as_name: bool = False,
        infer_union_variant_names: bool = False,
        use_tag_as_union_variant_name: bool = False,
        infer_union_discriminators: bool = False,
        name_allof_union_members_after_branches: bool = False,
        use_operation_id_as_name: bool = False,
        use_unique_items_as_set: bool = False,
        use_unique_items_as_set_by_value: bool = False,
        use_tuple_for_fixed_items: bool = False,
        use_tuple_for_closed_arrays: bool = False,
        use_tuple_for_fixed_length_arrays: bool = False,
        use_total_false_for_typed_dict: bool = False,
        use_closed_typed_dict: bool = True,
        allof_merge_mode: AllOfMergeMode = AllOfMergeMode.Constraints,
        allof_class_hierarchy: AllOfClassHierarchy = AllOfClassHierarchy.IfNoConflict,
        allow_remote_refs: bool | None = None,
        strict_refs: bool = False,
        untrusted_input: bool = False,
        allow_private_network: bool = False,
        http_backend: HTTPBackend = HTTPBackend.AUTO,
        http_headers: Sequence[tuple[str, str]] | None = None,
        http_local_ref_path: Path | None = None,
        http_ignore_tls: bool = False,
        http_timeout: float | None = None,
        use_annotated: bool = False,
        use_serialize_as_any: bool = False,
        use_serialize_as_any_for_every_subclass: bool = False,
        use_non_positive_negative_number_constrained_types: bool = False,
        use_decimal_for_multiple_of: bool = False,
        use_decimal_in_field_forms: bool = False,
        use_encoded_str_for_constrained_base64: bool = False,
        use_strict_str_for_base64: bool = False,
        use_msgspec_meta_for_map_keys: bool = False,
        omit_redundant_constraints: bool = False,
        read_length_keywords_by_type: bool = False,
        read_object_keywords_by_type: bool = False,
        read_msgspec_union_bounds_exactly: bool = False,
        use_site_models_for_constrained_refs: bool = False,
        use_one_model_per_ref_site: bool = False,
        original_field_name_delimiter: str | None = None,
        use_double_quotes: bool = False,
        use_union_operator: bool = False,
        allow_responses_without_content: bool = False,
        collapse_root_models: bool = False,
        collapse_root_models_name_strategy: CollapseRootModelsNameStrategy | None = None,
        collapse_shared_parent_wrappers: bool = False,
        keep_constrained_root_models_in_every_container: bool = False,
        keep_enum_literal_aliases: bool = False,
        keep_reference_null_on_collapsed_root_models: bool = False,
        keep_shared_members_on_collapsed_root_models: bool = False,
        collapse_constrained_root_models_as_annotated: bool = False,
        remove_unused_models_beside_type_overrides: bool = False,
        keep_self_referencing_array_items: bool = False,
        keep_defaults_of_inherited_required_fields: bool = False,
        omit_incomplete_model_defaults: bool = False,
        collapse_reuse_models: bool = False,
        skip_root_model: bool = False,
        use_root_model_sequence_interface: bool = False,
        use_type_alias: bool = False,
        use_validate_default_for_root_model_type_aliases: bool = False,
        use_plain_alias_for_recursive_models: bool = False,
        special_field_name_prefix: str | None = None,
        remove_special_field_name_prefix: bool = False,
        capitalise_enum_members: bool = False,
        capitalise_numbered_enum_members: bool = False,
        keep_model_order: bool = False,
        order_constructor_fields_by: Sequence[ConstructorFieldOrder] = (),
        use_one_literal_as_default: bool = False,
        use_enum_values_in_discriminator: bool = False,
        merge_shared_variant_tags: bool = False,
        use_unmapped_variant_tag_values: bool = False,
        read_schema_version_per_document: bool = False,
        read_escaped_ref_pointers: bool = False,
        read_dynamic_anchors_in_document_order: bool = False,
        read_ids_with_empty_fragment: bool = False,
        read_single_allof_refs_in_their_documents: bool = False,
        read_ref_siblings_in_closed_allof_members: bool = False,
        ignore_ref_siblings_by_schema_version: bool = False,
        read_yaml_1_1_numbers_as_text: bool = False,
        use_msgspec_tagged_unions: bool = False,
        tag_msgspec_by_payload_name: bool = False,
        use_msgspec_nullable_enum_alias: bool = False,
        use_msgspec_forbid_unknown_fields: bool = False,
        use_typed_nullable_map_values: bool = False,
        use_typed_map_values_beside_other_types: bool = False,
        use_single_dict_for_object_maps: bool = False,
        read_unevaluated_properties: bool = False,
        read_kubernetes_extensions: bool = False,
        use_single_value_enum_as_const: bool = False,
        read_const_beside_enum: bool = False,
        read_format_as_annotation: bool = False,
        filter_literal_values: bool = False,
        intersect_allof_keywords: bool = False,
        intersect_combined_schema_keywords: bool = False,
        distribute_allof_over_unions: bool = False,
        read_allof_parents_in_mro_order: bool = False,
        read_inherited_property_schemas_in_mro_order: bool = False,
        read_nullable_beside_type_list: bool = False,
        keep_field_defaults_off_branch_roots: bool = False,
        keep_enum_member_values: bool = False,
        known_third_party: list[str] | None = None,
        custom_formatters: list[str] | None = None,
        custom_formatters_kwargs: dict[str, Any] | None = None,
        use_pendulum: bool = False,
        use_standard_primitive_types: bool = False,
        use_object_type: bool = False,
        untyped_value_type: str | None = None,
        http_query_parameters: Sequence[tuple[str, str]] | None = None,
        treat_dot_as_module: bool | None = None,
        strict_dotted_module_names: bool = False,
        rename_submodules_hidden_by_classes: bool = False,
        use_exact_imports: bool = False,
        use_type_checking_imports: bool | None = None,
        default_field_extras: dict[str, Any] | None = None,
        target_datetime_class: DatetimeClassType | None = None,
        target_date_class: DateClassType | None = None,
        keyword_only: bool = False,
        frozen_dataclasses: bool = False,
        no_alias: bool = False,
        use_serialization_alias: bool = False,
        use_frozen_field: bool = False,
        use_default_factory_for_optional_nested_models: bool = False,
        formatters: list[Formatter] | None = None,
        builtin_format_line_length: int | None = None,
        defer_formatting: bool = False,
        parent_scoped_naming: bool = False,
        naming_strategy: NamingStrategy | None = None,
        duplicate_name_suffix: dict[str, str] | None = None,
        use_duplicate_name_suffix_for_every_collision: bool = False,
        dataclass_arguments: DataclassArguments | None = None,
        keep_false_dataclass_arguments: bool = False,
        import_overrides: dict[str, str] | None = None,
        type_mappings: list[str] | None = None,
        type_overrides: dict[str, str] | None = None,
        type_overrides_replace_whole_type: bool = False,
        use_x_python_type_as_written: bool = False,
        use_x_python_import_for_every_ref: bool = False,
        use_bytes_for_content_media_type: bool = False,
        use_bounds_for_float_const: bool = False,
        read_only_write_only_model_type: ReadOnlyWriteOnlyModelType | None = None,
        dedupe_request_response_variants: bool = False,
        dedupe_numbered_request_response_variants: bool = False,
        field_type_collision_strategy: FieldTypeCollisionStrategy | None = None,
        target_pydantic_version: TargetPydanticVersion | None = None,
        external_ref_mapping: dict[str, str] | None = None,
    ) -> None:
        raise NotImplementedError

    def parse(
        self,
        with_import: bool | None = True,
        format_: bool | None = True,
        settings_path: Path | None = None,
        disable_future_imports: bool = False,
        all_exports_scope: AllExportsScope | None = None,
        all_exports_collision_strategy: AllExportsCollisionStrategy | None = None,
        module_split_mode: ModuleSplitMode | None = None,
        collect_model_metadata: bool = False,
    ) -> str | dict[tuple[str, ...], Any]:
        raise NotImplementedError


def _kwonly_params(signature: inspect.Signature) -> list[inspect.Parameter]:
    return [param for param in signature.parameters.values() if param.kind is inspect.Parameter.KEYWORD_ONLY]


def _kwonly_by_name(signature: inspect.Signature) -> dict[str, inspect.Parameter]:
    return {param.name: param for param in _kwonly_params(signature)}


def _params_by_name(signature: inspect.Signature) -> dict[str, inspect.Parameter]:
    return {
        name: param
        for name, param in signature.parameters.items()
        if name != "self" and param.kind in {inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY}
    }


def _type_to_str(tp: Any) -> str:  # pragma: no cover
    """Convert type to normalized string."""
    if tp is type(None):
        return "None"
    if isinstance(tp, type):
        return tp.__name__
    if isinstance(tp, str):
        return tp
    return (
        str(tp)
        .replace("collections.abc.", "")
        .replace("collections.", "")
        .replace("typing.", "")
        .replace("pathlib.", "")
    )


def _normalize_union_str(type_str: str) -> str:
    """Normalize a union type string by sorting its components recursively."""
    try:
        tree = ast.parse(type_str, mode="eval")
    except SyntaxError:  # pragma: no cover
        return type_str

    def normalize_node(node: ast.expr) -> str:
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):

            def collect_union_parts(n: ast.expr) -> list[str]:
                if isinstance(n, ast.BinOp) and isinstance(n.op, ast.BitOr):
                    return collect_union_parts(n.left) + collect_union_parts(n.right)
                return [normalize_node(n)]

            parts = collect_union_parts(node)
            return " | ".join(sorted(parts))
        if isinstance(node, ast.Subscript):
            value = ast.unparse(node.value)
            if isinstance(node.slice, ast.Tuple):
                args = [normalize_node(elt) for elt in node.slice.elts]
                return f"{value}[{', '.join(args)}]"
            return f"{value}[{normalize_node(node.slice)}]"
        return ast.unparse(node)

    return normalize_node(tree.body)


@pytest.mark.parametrize(
    ("input_str", "expected"),
    [
        ("str | int", "int | str"),
        ("str | int | None", "None | int | str"),
        ("int", "int"),
        ("Mapping[str, str]", "Mapping[str, str]"),
        ("Mapping[str, str | list[str]]", "Mapping[str, list[str] | str]"),
        ("Mapping[str, list[str] | str]", "Mapping[str, list[str] | str]"),
        ("Mapping[str, str | list[str]] | None", "Mapping[str, list[str] | str] | None"),
        ("dict[str, int | str | None]", "dict[str, None | int | str]"),
        ("list[str | int]", "list[int | str]"),
        ("tuple[str | int, bool | None]", "tuple[int | str, None | bool]"),
    ],
)
def test_normalize_union_str(input_str: str, expected: str) -> None:
    """Test _normalize_union_str correctly normalizes union types recursively."""
    assert _normalize_union_str(input_str) == expected


@pytest.mark.parametrize(
    ("type_a", "type_b"),
    [
        ("Mapping[str, str | list[str]]", "Mapping[str, list[str] | str]"),
        ("str | int | None", "None | str | int"),
        ("dict[str, int | str]", "dict[str, str | int]"),
    ],
)
def test_normalize_union_str_equivalence(type_a: str, type_b: str) -> None:
    """Test that different orderings of the same union type normalize to the same string."""
    assert _normalize_union_str(type_a) == _normalize_union_str(type_b)


def _normalize_type(tp: Any) -> str:  # noqa: PLR0911  # pragma: no cover
    """Normalize type for comparison between Config and TypedDict."""
    if tp is None or tp is type(None):
        return "None"

    if isinstance(tp, str):
        return _normalize_union_str(tp)

    if isinstance(tp, ForwardRef):
        arg = tp.__forward_arg__
        if arg.startswith("NotRequired[") and arg.endswith("]"):
            arg = arg[12:-1]
        return _normalize_union_str(arg)

    if isinstance(tp, list):
        return f"[{', '.join(_normalize_type(t) for t in tp)}]"

    origin = get_origin(tp)
    args = get_args(tp)

    if origin in {Annotated, NotRequired}:
        return _normalize_type(args[0]) if args else _type_to_str(tp)

    if origin is Union or isinstance(tp, types.UnionType):
        if isinstance(tp, types.UnionType):
            args = get_args(tp)
        normalized_args = sorted(_normalize_type(a) for a in args)
        return _type_to_str(" | ".join(normalized_args))

    if origin is not None:
        if args:
            normalized_args = [_normalize_type(a) for a in args]
            origin_name = getattr(origin, "__name__", str(origin))
            return _type_to_str(f"{origin_name}[{', '.join(normalized_args)}]")
        return _type_to_str(origin)

    return _type_to_str(tp)


def _types_match(config_type: Any, dict_type: Any) -> bool:
    """Check if Config type and TypedDict type are equivalent."""
    return _normalize_type(config_type) == _normalize_type(dict_type)


def test_public_api_surface_matches_stored_output() -> None:
    """Store every public module's names, kinds and signatures: a diff of the stored file is a public change."""
    assert_output(_public_surface())


@pytest.mark.abnormal_path("downstream code assigns this module attribute, so the assignment is the contract pinned")
def test_import_secret_str_assignment_changes_the_generated_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the module attribute that downstream code assigns to replace the password string type."""
    from datamodel_code_generator.imports import Import
    from datamodel_code_generator.model import pydantic_v2

    monkeypatch.setattr(pydantic_v2.types, "IMPORT_SECRET_STR", Import.from_full_path("custom_types.CustomSecretStr"))
    schema = '{"type": "object", "properties": {"token": {"type": "string", "format": "password"}}}'

    assert_output(generate(schema, output_model_type=DataModelType.PydanticV2BaseModel))


def test_data_model_set_tuple_abi_matches_baseline() -> None:
    """Keep DataModelSet compatible with downstream tuple construction and unpacking."""
    from datamodel_code_generator.model import DataModelSet, get_data_model_types

    expected_fields = (
        "data_model",
        "root_model",
        "field_model",
        "data_type_manager",
        "dump_resolve_reference_action",
        "scalar_model",
        "union_model",
        "known_third_party",
    )
    model_types = get_data_model_types(DataModelType.PydanticV2BaseModel)
    (
        data_model,
        root_model,
        field_model,
        data_type_manager,
        dump_resolve_reference_action,
        scalar_model,
        union_model,
        known_third_party,
    ) = model_types

    defaulted_model_types = DataModelSet(
        data_model,
        root_model,
        field_model,
        data_type_manager,
        dump_resolve_reference_action,
        scalar_model,
        union_model,
    )

    assert issubclass(DataModelSet, tuple)
    assert DataModelSet._fields == expected_fields
    assert DataModelSet._field_defaults == {"known_third_party": None}
    assert len(model_types) == len(expected_fields)
    assert defaulted_model_types.known_third_party is None
    assert (
        DataModelSet(
            data_model,
            root_model,
            field_model,
            data_type_manager,
            dump_resolve_reference_action,
            scalar_model,
            union_model,
            known_third_party,
        )
        == model_types
    )


@pytest.mark.parametrize(
    ("context_type", "expected_fields"),
    [
        pytest.param(
            "ModuleContext",
            ("module", "module_key", "models", "is_init", "imports", "scoped_model_resolver"),
            id="module-context",
        ),
        pytest.param(
            "ParseConfig",
            (
                "with_import",
                "use_deferred_annotations",
                "code_formatter",
                "module_split_mode",
                "all_exports_scope",
                "all_exports_collision_strategy",
            ),
            id="parse-config",
        ),
    ],
)
def test_parser_named_tuple_context_abi_matches_baseline(
    context_type: str,
    expected_fields: tuple[str, ...],
) -> None:
    """Keep parser processing contexts compatible with tuple consumers."""
    from datamodel_code_generator.parser import base

    context_class = getattr(base, context_type)
    values = tuple(object() for _ in expected_fields)
    context = context_class(*values)

    assert context_class.__module__ == "datamodel_code_generator.parser.base"
    assert issubclass(context_class, tuple)
    assert context_class._fields == expected_fields
    assert context_class._field_defaults == {}
    assert tuple(context) == values
    assert tuple(getattr(context, field) for field in expected_fields) == values
    if context_type == "ParseConfig":
        replacement = object()
        updated_context = context._replace(with_import=replacement)

        assert updated_context.with_import is replacement
        assert tuple(updated_context)[1:] == values[1:]


def test_json_schema_parser_extension_method_signatures_match_baseline() -> None:
    """Keep parser subclass hooks compatible with their established signatures."""
    from datamodel_code_generator.model.base import UNDEFINED
    from datamodel_code_generator.parser.jsonschema import JsonSchemaParser

    parse_item = inspect.signature(JsonSchemaParser.parse_item).parameters
    parse_root_type = inspect.signature(JsonSchemaParser.parse_root_type).parameters
    register_root_model = inspect.signature(JsonSchemaParser._register_root_model).parameters

    assert tuple(parse_item) == ("self", "name", "item", "path", "singular_name", "parent")
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parse_item.values())
    assert parse_item["singular_name"].default is False
    assert parse_item["parent"].default is None

    assert tuple(parse_root_type) == ("self", "name", "obj", "path")
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in parse_root_type.values())

    assert tuple(register_root_model) == (
        "self",
        "reference",
        "fields",
        "obj",
        "custom_base_class_name",
        "description",
        "default",
    )
    assert register_root_model["self"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert all(
        parameter.kind is inspect.Parameter.KEYWORD_ONLY
        for name, parameter in register_root_model.items()
        if name != "self"
    )
    assert register_root_model["description"].default is None
    assert register_root_model["default"].default is UNDEFINED


def test_generate_signature_matches_baseline() -> None:
    """Ensure generate keeps backward compatibility via GenerateConfigDict.

    The new signature uses **options: Unpack[GenerateConfigDict], so we verify
    that GenerateConfigDict has all the same keys as the baseline function's
    keyword-only arguments (except 'config'), matching types and default values.
    """
    from datamodel_code_generator._types import GenerateConfigDict

    expected = inspect.signature(_baseline_generate)
    baseline_params = {k: v for k, v in _kwonly_by_name(expected).items() if k != "config"}
    dict_annotations = GenerateConfigDict.__annotations__

    # 1. Verify all baseline kwargs are in GenerateConfigDict (key names)
    baseline_kwargs = set(baseline_params.keys())
    dict_keys = set(dict_annotations.keys())
    assert baseline_kwargs == dict_keys, (
        f"Mismatch between baseline args and GenerateConfigDict keys:\n"
        f"  In baseline but not in dict: {baseline_kwargs - dict_keys}\n"
        f"  In dict but not in baseline: {dict_keys - baseline_kwargs}"
    )

    # 2. Verify types match between baseline and GenerateConfigDict
    for name, param in baseline_params.items():
        baseline_type = param.annotation
        dict_type = dict_annotations[name]
        assert _types_match(baseline_type, dict_type), (
            f"Type mismatch for '{name}':\n"
            f"  Baseline: {_normalize_type(baseline_type)}\n"
            f"  TypedDict: {_normalize_type(dict_type)}"
        )

    # 3. Verify default values match between baseline and GenerateConfig
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(
        _types_namespace={"DefaultValueType": DefaultValueType, "StrictTypes": StrictTypes, "UnionMode": UnionMode}
    )

    for name, param in baseline_params.items():
        config_default = GenerateConfig.model_fields[name].default
        assert config_default == param.default, (
            f"Default mismatch for '{name}':\n  Baseline: {param.default!r}\n  GenerateConfig: {config_default!r}"
        )


def test_generate_runtime_signature_matches_baseline() -> None:
    """Keep generate()'s introspected public callable shape stable."""
    assert generate.__module__ == "datamodel_code_generator"
    assert inspect.signature(generate) == inspect.signature(_baseline_generate_runtime_signature)


def test_parser_parse_runtime_signature_matches_baseline() -> None:
    """Keep Parser.parse()'s introspected public callable shape stable."""
    from datamodel_code_generator.parser.base import Parser

    assert Parser.parse.__module__ == "datamodel_code_generator.parser.base"
    assert inspect.signature(Parser.parse) == inspect.signature(_baseline_parser_parse_runtime_signature)


def test_parser_process_single_module_runtime_signature_matches_baseline() -> None:
    """Keep Parser._process_single_module()'s extension hook stable."""
    from datamodel_code_generator.parser.base import Parser

    assert Parser._process_single_module.__module__ == "datamodel_code_generator.parser.base"
    assert inspect.signature(Parser._process_single_module) == inspect.signature(
        _baseline_process_single_module_runtime_signature
    )


def test_openapi_and_asyncapi_parser_parse_runtime_signatures_match_baseline() -> None:
    """Keep OpenAPI's forwarding parse hook and AsyncAPI inheritance intact."""
    from datamodel_code_generator.parser.asyncapi import AsyncAPIParser
    from datamodel_code_generator.parser.openapi import OpenAPIParser

    expected = inspect.signature(_baseline_openapi_parser_parse_runtime_signature)

    assert OpenAPIParser.parse.__module__ == "datamodel_code_generator.parser.openapi"
    assert inspect.signature(OpenAPIParser.parse) == expected
    assert AsyncAPIParser.parse is OpenAPIParser.parse
    assert AsyncAPIParser.parse.__module__ == "datamodel_code_generator.parser.openapi"
    assert inspect.signature(AsyncAPIParser.parse) == expected


def test_parser_signature_matches_baseline() -> None:
    """Ensure Parser.__init__ keeps backward compatibility via ParserConfigDict.

    The new signature uses **options: Unpack[ParserConfigDict], so we verify
    that ParserConfigDict has all the same keys as the baseline function's
    keyword-only arguments (except 'config'), matching types and default values.
    """
    from datamodel_code_generator._types import ParserConfigDict

    expected = inspect.signature(_BaselineParser.__init__)
    baseline_params = {k: v for k, v in _kwonly_by_name(expected).items() if k != "config"}
    dict_annotations = ParserConfigDict.__annotations__

    baseline_kwargs = set(baseline_params.keys())
    dict_keys = set(dict_annotations.keys())
    assert baseline_kwargs == dict_keys, (
        f"Mismatch between baseline args and ParserConfigDict keys:\n"
        f"  In baseline but not in dict: {baseline_kwargs - dict_keys}\n"
        f"  In dict but not in baseline: {dict_keys - baseline_kwargs}"
    )

    for name, param in baseline_params.items():
        baseline_type = param.annotation
        dict_type = dict_annotations[name]
        assert _types_match(baseline_type, dict_type), (
            f"Type mismatch for '{name}':\n"
            f"  Baseline: {_normalize_type(baseline_type)}\n"
            f"  TypedDict: {_normalize_type(dict_type)}"
        )

    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.enums import DefaultValueType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    ParserConfig.model_rebuild(
        _types_namespace={
            "DefaultValueType": DefaultValueType,
            "StrictTypes": StrictTypes,
            "UnionMode": UnionMode,
            "DataModel": DataModel,
            "DataModelFieldBase": DataModelFieldBase,
            "DataTypeManager": DataTypeManager,
        }
    )

    for name, param in baseline_params.items():
        config_default = ParserConfig.model_fields[name].default
        if callable(param.default) and config_default is None:
            continue
        assert config_default == param.default, (
            f"Default mismatch for '{name}':\n  Baseline: {param.default!r}\n  ParserConfig: {config_default!r}"
        )


def test_pydantic_v2_compatibility_reexports_remain_importable() -> None:
    """Keep compatibility import paths for pydantic v2 model internals."""
    from datamodel_code_generator.model.pydantic_base import DataModelField as PydanticBaseDataModelField
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        Constraints as PydanticV2BaseConstraints,
    )
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        DataModelField as PydanticV2DataModelField,
    )
    from datamodel_code_generator.model.pydantic_v2.base_model import (
        DataModelFieldV1,
    )
    from datamodel_code_generator.model.pydantic_v2.dataclass import Constraints as PydanticV2DataclassConstraints

    assert DataModelFieldV1 is PydanticBaseDataModelField
    assert issubclass(PydanticV2DataModelField, DataModelFieldV1)
    assert PydanticV2DataclassConstraints is PydanticV2BaseConstraints


def test_config_models_force_rebuild_after_defer_build() -> None:
    """Ensure deferred config model schemas can still be rebuilt at import-test time."""
    from pydantic import BaseModel as PydanticBaseModel

    from datamodel_code_generator.enums import DefaultValueType
    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import DataTypeManager, StrictTypes

    config_module = importlib.import_module("datamodel_code_generator.config")
    expected_model_names = {
        "AsyncAPIParserConfig",
        "AvroParserConfig",
        "BaseGenerateConfig",
        "GenerateConfig",
        "GraphQLParserConfig",
        "JSONSchemaParserConfig",
        "OpenAPIParserConfig",
        "ParseConfig",
        "ParserConfig",
        "ProtobufParserConfig",
        "XMLSchemaParserConfig",
    }
    config_model_types = {
        name: model_type
        for name, model_type in inspect.getmembers(config_module, inspect.isclass)
        if issubclass(model_type, PydanticBaseModel)
        and model_type.__module__ in {"datamodel_code_generator.base_config", config_module.__name__}
    }
    rebuild_namespace = {
        "DataModel": DataModel,
        "DataModelFieldBase": DataModelFieldBase,
        "DataTypeManager": DataTypeManager,
        "DefaultValueType": DefaultValueType,
        "StrictTypes": StrictTypes,
        "UnionMode": UnionMode,
    }

    assert set(config_model_types) == expected_model_names
    for model_type in config_model_types.values():
        assert model_type.model_rebuild(force=True, _types_namespace=rebuild_namespace)


def test_config_star_import_lists_the_public_names() -> None:
    """Ensure a star import of config takes its public names, the parser config models it builds on access included."""
    importlib.import_module("datamodel_code_generator.config")
    config_module = sys.modules["datamodel_code_generator.config"]

    assert config_module.__all__ == [name for name in dir(config_module) if not name.startswith("_")]
    assert {"GraphQLParserConfig", "OpenAPIParserConfig", "ParserConfig"} <= set(config_module.__all__)


def test_generate_config_dict_fields_match_generate_config() -> None:
    """Ensure GenerateConfigDict has same field names as GenerateConfig."""
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig

    config_fields = set(GenerateConfig.model_fields.keys())
    dict_fields = set(GenerateConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_generate_config_dict_types_match_generate_config() -> None:
    """Ensure GenerateConfigDict field types match GenerateConfig."""
    from datamodel_code_generator._types import GenerateConfigDict
    from datamodel_code_generator.config import GenerateConfig

    for field_name, field_info in GenerateConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = GenerateConfigDict.__annotations__[field_name]
        if field_name == "preset":
            assert _normalize_type(config_type) == "None | str"
            assert _normalize_type(dict_type) == "None | PresetName"
            continue
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_parser_config_dict_fields_match_parser_config() -> None:
    """Ensure ParserConfigDict has same field names as ParserConfig."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    config_fields = set(ParserConfig.model_fields.keys())
    dict_fields = set(ParserConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_parser_config_dump_fields_match_public_typed_dict() -> None:
    """Keep internal parser context state out of the serialized public configuration."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    assert set(ParserConfig().model_dump()) == set(ParserConfigDict.__annotations__)


def test_parse_config_dict_fields_match_parse_config() -> None:
    """Ensure ParseConfigDict has same field names as ParseConfig."""
    from datamodel_code_generator._types import ParseConfigDict
    from datamodel_code_generator.config import ParseConfig

    config_fields = set(ParseConfig.model_fields.keys())
    dict_fields = set(ParseConfigDict.__annotations__.keys())
    assert config_fields == dict_fields, f"Mismatch: {config_fields ^ dict_fields}"


def test_parser_config_dict_types_match_parser_config() -> None:
    """Ensure ParserConfigDict field types match ParserConfig."""
    from datamodel_code_generator._types import ParserConfigDict
    from datamodel_code_generator.config import ParserConfig

    for field_name, field_info in ParserConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = ParserConfigDict.__annotations__[field_name]
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_parse_config_dict_types_match_parse_config() -> None:
    """Ensure ParseConfigDict field types match ParseConfig."""
    from datamodel_code_generator._types import ParseConfigDict
    from datamodel_code_generator.config import ParseConfig

    for field_name, field_info in ParseConfig.model_fields.items():
        config_type = field_info.annotation
        dict_type = ParseConfigDict.__annotations__[field_name]
        assert _types_match(config_type, dict_type), (
            f"Type mismatch for {field_name}: Config={_normalize_type(config_type)}, Dict={_normalize_type(dict_type)}"
        )


def test_generate_config_defaults_match_generate_signature() -> None:
    """Ensure GenerateConfig default values match generate() signature defaults."""
    from datamodel_code_generator.config import GenerateConfig

    expected_sig = inspect.signature(_baseline_generate)
    expected_params = _kwonly_by_name(expected_sig)

    for field_name, field_info in GenerateConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, generate()={param.default}"
        )


def test_parser_config_defaults_match_parser_signature() -> None:
    """Ensure ParserConfig default values match Parser.__init__ signature defaults."""
    from datamodel_code_generator.config import ParserConfig

    expected_sig = inspect.signature(_BaselineParser.__init__)
    expected_params = _kwonly_by_name(expected_sig)

    for field_name, field_info in ParserConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        if callable(param.default) and config_default is None:
            continue

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, Parser.__init__()={param.default}"
        )


def test_parse_config_defaults_match_parse_signature() -> None:
    """Ensure ParseConfig default values match Parser.parse() signature defaults."""
    from datamodel_code_generator.config import ParseConfig

    expected_sig = inspect.signature(_BaselineParser.parse)
    expected_params = _params_by_name(expected_sig)

    for field_name, field_info in ParseConfig.model_fields.items():
        param = expected_params[field_name]
        config_default = field_info.default

        assert config_default == param.default, (
            f"Default mismatch for {field_name}: Config={config_default}, Parser.parse()={param.default}"
        )


def test_generate_with_config_produces_same_result_as_kwargs(tmp_path: Path) -> None:
    """Ensure generate() with GenerateConfig produces same result as kwargs."""
    from datamodel_code_generator.config import GenerateConfig
    from datamodel_code_generator.enums import DataModelType, DefaultValueType
    from datamodel_code_generator.model.pydantic_v2 import UnionMode
    from datamodel_code_generator.types import StrictTypes

    GenerateConfig.model_rebuild(
        _types_namespace={"DefaultValueType": DefaultValueType, "StrictTypes": StrictTypes, "UnionMode": UnionMode}
    )

    schema = '{"type": "object", "properties": {"name": {"type": "string"}}}'
    output_kwargs = tmp_path / "output_kwargs.py"
    output_config = tmp_path / "output_config.py"

    # Generate with kwargs
    generate(
        input_=schema,
        output=output_kwargs,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )

    # Generate with config
    config = GenerateConfig(
        output=output_config,
        output_model_type=DataModelType.PydanticV2BaseModel,
    )
    generate(
        input_=schema,
        config=config,
    )

    # Compare results
    kwargs_content = output_kwargs.read_text(encoding="utf-8")
    config_content = output_config.read_text(encoding="utf-8")
    assert kwargs_content == config_content, "Output differs between kwargs and config"
