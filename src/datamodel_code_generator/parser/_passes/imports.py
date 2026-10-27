"""The import and finish passes (L6, ``IMPORTS``) and the rendering of the modules (L7).

The import passes run over every module once the render preparation is over (B2), on the state of
``_finalize_modules`` (``_FinalizeState``); the finish passes then record what the generation reports and warn once per
generation; ``render`` plans the imports of each module and renders the modules through the parser's rendering hooks.
Each is a function of the parser and its state, called from the pipeline tables of ``parser/_passes/_tables.py``; the
parser comes in through ``ImportsHost``, ``FinishHost`` and ``RenderHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

import dataclasses
import operator
from itertools import chain
from typing import TYPE_CHECKING, Any, Final, TypeAlias, cast

from typing_extensions import Protocol

from datamodel_code_generator import DefaultValueTypeWarning, Error, ReuseScope
from datamodel_code_generator._checks import CheckArea, check_mode
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._parse_memo import rendering_memo
from datamodel_code_generator._python_target_names import PYTHON_BUILTIN_TYPE_NAMES
from datamodel_code_generator._requirements import pydantic_floor
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import DefaultValueType
from datamodel_code_generator.imports import IMPORT_LITERAL, Import
from datamodel_code_generator.model._decisions.enum_kind import literal_value_kind
from datamodel_code_generator.model._decisions.tag_value import struct_tag_clash
from datamodel_code_generator.model._render_plan import RENDER_READY
from datamodel_code_generator.model.base import BaseClassDataType, DataModel
from datamodel_code_generator.model.enum import Enum, IntEnum, StrEnum, get_raw_enum_member_value
from datamodel_code_generator.model.output import _bare_name_loads, _model_field_name_collisions
from datamodel_code_generator.model.type_alias import TypeAliasBase, TypeStatement
from datamodel_code_generator.parser._graph_ids import keyed_models, unchecked
from datamodel_code_generator.parser._imports_plan import (
    BindingRequest,
    FinalName,
    ImportProducer,
    ModuleImportPlan,
    NameTable,
    ReferenceImport,
    ReferenceUse,
    allocate_bindings,
)
from datamodel_code_generator.parser._passes._shared import (
    BaseStandInHost,
    RecursionFactsHost,
    ReplacedRootsHost,
    _context_models,
    _declares_unique_items,
    _format_tags,
    _generic_base_class_module,
    _get_model_module_name,
    _is_discriminator_container,
    _is_discriminator_wrapper,
    _iter_discriminator_data_types,
    _module_name_from_module_path,
    _named_data_types,
    _normalize_result_module_path,
    _string_or_identifier_pattern,
    _unique_items_tuple,
    get_special_path,
    reference_import_target,
    unused_model_lists,
)
from datamodel_code_generator.parser._passes._state import _PYDANTIC_FLOORS, ConfigT, ContextT, ResultT
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._passes.render_prep import _DECIMAL_WARNING_EXAMPLE_LIMIT
from datamodel_code_generator.reference import ModelResolver
from datamodel_code_generator.types import DataType, render_type_expr

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator.enums import TargetPydanticVersion
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model._render_plan import FieldRenderPlan, PlannedField, RenderReady
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.model_metadata import ModelMetadata
    from datamodel_code_generator.parser._graph_ids import KeyedModels
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._passes._shared import ReferenceImportTarget, _VariantDiscriminatorTags
    from datamodel_code_generator.parser._passes._state import (
        FinishRun,
        ForwarderMap,
        ModuleContextView,
        ModulePath,
        SortedDataModels,
        _FinalizeState,
    )
    from datamodel_code_generator.parser.generation import GraphBuilder
    from datamodel_code_generator.reference import ModelType, Reference
    from datamodel_code_generator.types import TypeExpr

__all__ = [
    "FinishHost",
    "ImportsHost",
    "RenderHost",
    "add_root_init_result",
    "alias_builtin_shadowing_module_imports",
    "append_model_imports",
    "bind_module_field_names",
    "change_module_imported_model_names",
    "decide_config_items",
    "name_final_classes_of_additional_properties",
    "record_generated_class_names",
    "record_model_placement",
    "record_pydantic_floor",
    "refresh_sequence_interface_after_renames",
    "remap_import_overrides",
    "remove_unused_imports",
    "remove_unused_models",
    "render",
    "replan_schema_validators_after_renames",
    "resolve_module_import_conflicts",
    "snapshot_model_imports",
    "validate_explicit_field_aliases",
    "warn_about_decimal_defaults",
    "warn_about_dropped_constraints",
    "warn_about_msgspec_decoding",
    "warn_about_variant_discriminator_tags",
]


class ImportsHost(ReplacedRootsHost, Protocol[ContextT]):
    """The options, run state and hooks of the parser that the import passes read and write."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def _import_overrides(self) -> dict[str, str] | None:
        """The modules ``--import-overrides`` routes imports through, by imported name."""

    @property
    def _reused_variant_tag_fields(self) -> dict[int, DataModelFieldBase]:
        """The tag fields that a union with a reused variant annotates with a new tag, by field id."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def imports(self) -> Imports:
        """The imports every module shares."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of model and field names."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    def _collect_used_names_from_models(
        self,
        models: list[DataModel],
        model_imports: Mapping[DataModel, tuple[Import, ...]] | None = None,
    ) -> set[str]:
        """Return the names the models use, which keep their imports."""

    def _prepare_schema_runtime_validation_module_code(self, contexts: list[ContextT]) -> None:
        """Plan the schema validator helpers of each module before their imports are collected."""

    def _sync_schema_runtime_validation_module_imports(
        self,
        contexts: list[ContextT],
        model_imports: dict[DataModel, tuple[Import, ...]],
    ) -> None:
        """Merge the imports that planning the helpers added into the imports of each module."""


class FinishHost(Protocol):
    """The options and run state of the parser that the finish passes read and write."""

    _decimal_default_warning_count: int
    _decimal_default_warning_examples: list[str] | None
    _dropped_constraint_fields: frozenset[tuple[ModulePath, str]]
    generated_class_names: tuple[tuple[str, str], ...]
    model_placement: dict[ModulePath, frozenset[str]]
    typed_map_values_may_move_models: bool

    @property
    def _literal_under_all_enum_reference_ids(self) -> set[int]:
        """The ids of the references of the enums that ``--enum-field-as-literal all`` writes as Literal values."""

    @property
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models naming themselves that the deduplication of root models dropped, whose users name them."""

    @property
    def _typed_map_value_reference_ids(self) -> set[int]:
        """The ids of the references of typed map values."""

    @property
    def _variant_discriminator_tags(self) -> dict[tuple[int, str], _VariantDiscriminatorTags]:
        """The tags of discriminated union variants, by variant id and tag property."""

    @property
    def collapse_root_models(self) -> bool:
        """Whether root models are collapsed into their users."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def deserialize_default_value_types(self) -> frozenset[DefaultValueType]:
        """The kinds of default values deserialized into their types."""

    @property
    def generate_schema_validators(self) -> bool:
        """Whether models get the schema validator rules the types cannot express."""

    @property
    def reuse_model(self) -> bool:
        """Whether models that render the same are reused."""

    @property
    def reuse_scope(self) -> ReuseScope | None:
        """The scope of reuse."""

    @property
    def target_pydantic_version(self) -> TargetPydanticVersion | None:
        """The Pydantic version the output targets, if configured."""

    @property
    def typed_nullable_map_reference_ids(self) -> set[int]:
        """The ids of the references of the nullable maps whose values the parse typed."""

    @property
    def use_msgspec_nullable_enum_alias(self) -> bool:
        """Whether msgspec enums that admit null are written as aliases of the enum and None."""

    @property
    def use_msgspec_tagged_unions(self) -> bool:
        """Whether msgspec unions are tagged by their discriminators."""

    @property
    def use_tuple_for_closed_arrays(self) -> bool:
        """Whether closed arrays are written as tuples."""

    @property
    def use_unique_items_as_set(self) -> bool:
        """Whether arrays of unique items are written as sets."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""


class RenderHost(RecursionFactsHost, BaseStandInHost, Protocol[ConfigT, ContextT, ResultT]):
    """The options, run state and rendering hooks of the parser that planning imports and rendering read and call."""

    model_metadata: ModelMetadata | None
    model_metadata_identities: tuple[str, ...]

    @property
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models that name themselves, which deduplication dropped from every module."""

    @property
    def _generic_base_model(self) -> DataModel | None:
        """The generated base class of every model, if the output has one."""

    @property
    def _import_ledger(self) -> ModuleImportLedger:
        """The ledger of the imports each module adds."""

    @property
    def _import_overrides(self) -> dict[str, str] | None:
        """The modules ``--import-overrides`` routes imports through, by imported name."""

    @property
    def _result_modules_postprocessor(self) -> Callable[..., dict[tuple[str, ...], ResultT]]:
        """The function that lays out the results of the modules under ``--treat-dot-as-module``."""

    @property
    def imports(self) -> Imports:
        """The imports every module shares."""

    @property
    def target_python_version(self) -> PythonVersion:
        """The Python version the output targets."""

    @property
    def treat_dot_as_module(self) -> bool | None:
        """Whether dots in schema names make modules."""

    def _build_model_metadata(
        self, contexts: list[ContextT], model_identities: Mapping[DataModel, str]
    ) -> tuple[ModelMetadata, tuple[str, ...]]:
        """Return the metadata of the models the modules write, with the identity of each."""

    def _generate_empty_init_exports(
        self, results: dict[ModulePath, ResultT], contexts: list[ContextT], config: ConfigT, future_imports_str: str
    ) -> None:
        """Write the exports of the package ``__init__.py`` files that no module writes."""

    def _generate_module_output(  # noqa: PLR0913, PLR0917
        self,
        ctx: ContextT,
        config: ConfigT,
        contexts: list[ContextT],
        forwarder_map: ForwarderMap,
        require_update: Collection[ModelKey],
        future_imports_str: str,
    ) -> ResultT | None:
        """Return the result of one module, or None when it writes nothing."""

    def _inspect_invalid_dotted_stdout(
        self,
        contexts: list[ContextT],
        sorted_data_models: SortedDataModels,
        config: ConfigT,
        results: dict[ModulePath, ResultT],
    ) -> None:
        """Repair or reject the output whose dotted module names one stdout stream cannot hold."""


_FUTURE_IMPORTS: Final = ImportProducer("future-imports")


_REMOVE_UNUSED: Final = ImportProducer("remove-unused")


_IMPORT_CONFLICTS: Final = ImportProducer("import-conflicts")


_FIELD_NAME_BINDINGS: Final = ImportProducer("field-name-bindings")


_BUILTIN_MODULE_BINDINGS: Final = ImportProducer("builtin-module-bindings")


_REUSED_VARIANT_LITERAL: Final = ImportProducer("reused-variant-literal")


_IMPORT_OVERRIDES: Final = ImportProducer("import-overrides")


def _reserve_import_alias(name: str, reserved_names: set[str]) -> str:
    """Reserve and return the first ``<name>_aliased`` name, with a suffix when taken."""
    alias = f"{name}_aliased"
    suffix = 1
    while alias in reserved_names:
        alias = f"{name}_aliased_{suffix}"
        suffix += 1
    reserved_names.add(alias)
    return alias


def _bind_module_field_names(models: list[DataModel], imports: Imports, ledger: ModuleImportLedger) -> None:
    """Allocate aliases only where a property hides an emitted expression name.

    Type aliases sit at module scope, so an import they name keeps its name beside the alias.
    """
    field_names = {field.name for model in models for field in model.fields if field.name is not None}
    shadowable_imports = {
        effective_name: (from_, name)
        for from_, names in imports.items()
        for name in names
        if (effective_name := imports.get_effective_name(from_, name)) in field_names
    }
    if "list" in field_names and "list" not in shadowable_imports:
        shadowable_imports["list"] = ("builtins", "list")
    if not shadowable_imports:
        return
    collisions: set[str] = set()
    reserved_names = field_names | {model.class_name for model in models}
    for model in models:
        collisions.update(_model_field_name_collisions(model, shadowable_imports))
    if not collisions:
        return
    reserved_names.update(imports.get_effective_name(from_, name) for from_, names in imports.items() for name in names)
    type_alias_imports = {
        (import_.from_, import_.import_)
        for model in models
        if isinstance(model, TypeAliasBase)
        for import_ in model.imports
    }
    bindings: dict[str, str] = {}
    for name, (from_, imported_name) in shadowable_imports.items():
        if name not in collisions:
            continue
        alias = _reserve_import_alias(name, reserved_names)
        keep_unaliased = (from_, imported_name) in type_alias_imports
        aliased_import = Import(from_=from_, import_=imported_name, alias=alias, keep_unaliased=keep_unaliased)
        bindings[name] = alias
        if from_ == "builtins" or keep_unaliased:
            ledger.append(imports, aliased_import, _FIELD_NAME_BINDINGS)
        else:
            ledger.apply_alias(imports, aliased_import, _FIELD_NAME_BINDINGS)
    for model in models:
        if not isinstance(model, TypeAliasBase):
            model.set_field_name_bindings(bindings)


def _expand_export_module_path(module: ModulePath) -> ModulePath:
    """Expand dotted module components while preserving the Python file suffix."""
    parts = ".".join(module).split(".")
    return (*parts[:-2], f"{parts[-2]}.{parts[-1]}")


def _forward_defined_models(contexts: Iterable[ModuleContextView], forwarder_map: ForwarderMap) -> ForwarderMap:
    """Keep the re-exports of the models an internal module still defines, dropping a module left without any.

    Import cycles are merged before reuse and root model collapsing drop models from the internal module.
    """
    defined = {ctx.module_key: {model.class_name for model in ctx.models} for ctx in contexts}
    return {
        module: dataclasses.replace(forwarder, names=kept)
        for module, forwarder in forwarder_map.items()
        if (kept := tuple(name for name in forwarder.names if name[1] in defined[name[0]]))
    }


def _released_name_imports(  # noqa: PLR0914
    parser: RenderHost[Any, Any, Any], contexts: Sequence[ModuleContextView], forwarder_map: ForwarderMap
) -> ForwarderMap:
    """Add to the forwarder of each module that a grown merge moved the models it imported by name before.

    Such a module binds in the released layout (``Forwarder.released``) its models and the models of other modules
    that it imports by name; the forwarder imports the latter, from the modules that write them now, under the same
    names. ``change_from_import`` names an import with ``reference_import_target`` and the module's resolver, in the
    order of its models and their data types: this replays it for the module's models with the place and class name
    each model has in the released layout, from the final names, so the names come out as that layout gave them. A
    module import (``from . import pets`` for ``pets.Pet``) takes its name and binds no model.
    """
    if not any(forwarder.released for forwarder in forwarder_map.values()):
        return forwarder_map
    contexts_by_module = {ctx.module_key: ctx for ctx in contexts}
    placement = {
        model_key_of(model.reference): (ctx.module_key, model.class_name) for ctx in contexts for model in ctx.models
    }
    released_placement = {
        (forwarder.internal_module, name): (module, released_name)
        for module, forwarder in forwarder_map.items()
        for released_name, name in forwarder.released
    }
    unimported = {
        *parser._base_stand_in_imports,  # noqa: SLF001
        *(model_key_of(model.reference) for model in parser._self_named_root_models),  # noqa: SLF001
    }
    names: dict[ModulePath, tuple[tuple[ModulePath, str, str], ...]] = {}
    for module, forwarder in forwarder_map.items():
        if not forwarder.released:
            continue
        defined = {model.class_name: model for model in contexts_by_module[forwarder.internal_module].models}
        models = [(released_name, model) for released_name, name in forwarder.released if (model := defined.get(name))]
        members = {model_key_of(model.reference) for _, model in models}
        resolver = ModelResolver(
            exclude_names={field.name for _, model in models for field in model.fields if field.name is not None},
            target_python_version=parser.target_python_version,
        )
        for released_name, model in models:
            resolver.add([model.path], released_name)
        ctx = contexts_by_module[module]
        module_name = _module_name_from_module_path(module)
        imported: dict[tuple[ModulePath, str, str], None] = {}
        for data_type in (data_type for _, model in models for data_type in _named_data_types(model)):
            if (
                (reference := data_type.reference) is None
                or (key := model_key_of(reference)) in members
                or key in unimported
                or (final := placement.get(key)) is None
            ):
                continue
            released_module, released_name = released_placement.get(final, final)
            released_module_name = _module_name_from_module_path(released_module)
            target = reference_import_target(
                released_name,
                module_name,
                f"{released_module_name}.{released_name}" if released_module_name else released_name,
                use_exact=ctx.imports.use_exact,
                init=ctx.is_init,
                internal_modules=forwarder.released_internal_modules,
                base_class=isinstance(data_type, BaseClassDataType),
                treat_dot_as_module=parser.treat_dot_as_module,
            )
            binding = resolver.add(target.full_path, target.import_).name
            if target.import_ == released_name:
                imported[(*final, binding)] = None
        names[module] = tuple(imported)
    return {
        module: dataclasses.replace(forwarder, names=(*forwarder.names, *names[module]))
        if module in names
        else forwarder
        for module, forwarder in forwarder_map.items()
    }


_MsgspecRefusal: TypeAlias = tuple[tuple[DataModel, ...], str | None]


_NO_MSGSPEC_REFUSAL: _MsgspecRefusal = ((), None)


def _msgspec_refused_structs(models: Iterable[object]) -> _MsgspecRefusal:
    """Return the distinct Struct types of a union msgspec refuses, and how their tags clash if all are tagged.

    msgspec decodes a union of Struct types only if all are tagged, by one tag field, with distinct tags of one type.
    """
    structs = (
        *{
            id(model): model
            for model in models
            if isinstance(model, DataModel) and model.REQUIRES_TAGGED_UNION_DISCRIMINATOR
        }.values(),
    )
    if len(structs) < 2:  # noqa: PLR2004
        return _NO_MSGSPEC_REFUSAL
    if len(tags := [tag for struct in structs if (tag := struct.discriminator_tag) is not None]) < len(structs):
        return structs, None
    if (clash := struct_tag_clash(tags, "legacy-python-equality")) is None:
        return _NO_MSGSPEC_REFUSAL
    kind, values = clash
    if kind == "tag-fields":
        return structs, f"are tagged by different fields {_format_tags(values)}"
    if kind == "tag-types":
        return structs, "mix int and str tags"
    return structs, f"share the tag {_format_tags(values)}"


def _msgspec_refused_member_structs(data_types: Iterable[DataType]) -> _MsgspecRefusal:
    """Return the refused Struct types of a union, through the aliases it names."""
    return _msgspec_refused_structs(
        member.reference.source for member, _, _ in _iter_discriminator_data_types(data_types) if member.reference
    )


def _iter_union_alias_roots(data_type: DataType) -> Iterator[DataType]:
    """Yield the root types of the aliases a union names."""
    for member in data_type.data_types:
        if member.is_union and not _is_discriminator_container(member):
            yield from _iter_union_alias_roots(member)
        elif (
            member.reference
            and isinstance(source := member.reference.source, DataModel)
            and _is_discriminator_wrapper(source)
            and source.fields
        ):
            yield source.fields[0].data_type


def _msgspec_refused_union_structs(data_type: DataType) -> _MsgspecRefusal:
    """Return the Struct types of a union msgspec refuses, unless an alias it names is refused on its own."""
    if (
        not data_type.is_union
        or _is_discriminator_container(data_type)
        or not (refusal := _msgspec_refused_member_structs(data_type.data_types))[0]
        or any(_msgspec_refused_member_structs((root,))[0] for root in _iter_union_alias_roots(data_type))
    ):
        return _NO_MSGSPEC_REFUSAL
    return refusal


def _msgspec_union_message(name: str, refusal: _MsgspecRefusal, *, discriminated: bool) -> str:
    """Describe a union of Struct types that msgspec refuses to decode."""
    structs, clash = refusal
    names = ", ".join(struct.class_name for struct in structs)
    union = f"the discriminated union {name}" if discriminated else f"the union {name}"
    if clash is not None:
        return f"msgspec cannot decode {union}: its Struct types {names} {clash}."
    if discriminated:
        return (
            f"msgspec cannot decode {union}: its Struct types {names} are not all tagged. "
            "Pass --use-msgspec-tagged-unions to tag them."
        )
    return (
        f"msgspec cannot decode {union}: its Struct types {names} are not all tagged, and only the "
        "variants of a discriminated union are tagged, with --use-msgspec-tagged-unions."
    )


_MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS: frozenset[str] = frozenset({
    "bytes",
    "bytearray",
    "datetime",
    "date",
    "time",
    "timedelta",
    "UUID",
    "Decimal",
})


_MsgspecEnumClash: TypeAlias = tuple[str, tuple[str, ...], bool]
"""A kind of a union that msgspec refuses for an Enum, its written members, and whether ``--enum-field-as-literal all``
writes every Enum among them as a Literal."""


def _iter_written_union_members(data_types: Iterable[DataType]) -> Iterator[DataType]:
    """Yield the members of a union as written, spreading the member unions that render as part of it."""
    for data_type in data_types:
        if data_type.is_union and not _is_discriminator_container(data_type):
            yield from _iter_written_union_members(data_type.data_types)
        else:
            yield data_type


def _msgspec_enum_clashes(
    data_types: Iterable[DataType], literal_enum_ids: Collection[int]
) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind and its written members of a union that msgspec decodes once its Enums are written as Literal.

    msgspec decodes a union only if it holds an Enum of str values as its one str-like member and an Enum of int
    values as its one int-like member, while it takes Literal values beside each other and beside str or int. A
    member of another str-like type, such as bytes, refuses a Literal of str values too, so it leaves the str side be.
    ``literal_enum_ids`` holds the ids of the references of the Enums that ``--enum-field-as-literal all`` writes as a
    Literal.
    """
    enums: dict[str, set[int]] = {"str": set(), "int": set()}
    classes: dict[str, set[int]] = {"str": set(), "int": set()}
    names: dict[str, dict[str, None]] = {"str": {}, "int": {}}
    others: set[str] = set()
    refused: set[str] = set()
    for member in _iter_written_union_members(data_types):
        for leaf, _, _ in _iter_discriminator_data_types((member,)):
            if isinstance(source := leaf.reference.source if leaf.reference else None, Enum):
                if (
                    kinds := {literal_value_kind(get_raw_enum_member_value(field.default)) for field in source.fields}
                ) in ({"str"}, {"int"}):
                    enums[kind := kinds.pop()].add(id(source))
                    names[kind][member.type_hint] = None
                    if id(source.reference) not in literal_enum_ids:
                        classes[kind].add(id(source))
            elif source is None and not leaf.data_types and not leaf.enum_member_literals:
                if leaf.type in _MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS:
                    refused.add("str")
                for kind in (
                    {literal_value_kind(literal) for literal in leaf.literals} or {leaf.type or ""}
                ) & names.keys():
                    others.add(kind)
                    names[kind][member.type_hint] = None
    return tuple(
        (kind, (*names[kind],), not classes[kind])
        for kind, ids in enums.items()
        if ids and len(ids) + (kind in others) > 1 and kind not in refused
    )


def _msgspec_refused_union_enums(
    data_type: DataType, literal_enum_ids: Collection[int]
) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind of a union msgspec refuses for an Enum, unless an alias it names is refused for that kind."""
    if (
        not data_type.is_union
        or _is_discriminator_container(data_type)
        or not (clashes := _msgspec_enum_clashes(data_type.data_types, literal_enum_ids))
    ):
        return ()
    covered = {
        clash[0]
        for root in _iter_union_alias_roots(data_type)
        for clash in _msgspec_enum_clashes((root,), literal_enum_ids)
    }
    return tuple(clash for clash in clashes if clash[0] not in covered)


def _msgspec_field_enum_clashes(
    data_types: Iterable[DataType], literal_enum_ids: Collection[int]
) -> dict[str, _MsgspecEnumClash]:
    """Return the clash of each kind that the unions among some types are refused for, the first union's."""
    clashes: dict[str, _MsgspecEnumClash] = {}
    for clash in chain.from_iterable(
        _msgspec_refused_union_enums(data_type, literal_enum_ids) for data_type in data_types
    ):
        clashes.setdefault(clash[0], clash)
    return clashes


def _msgspec_enum_union_message(name: str, clash: _MsgspecEnumClash) -> str:
    """Describe a union that msgspec refuses to decode because it holds an Enum beside another member of its kind.

    The message recommends ``--enum-field-as-literal all`` only where that option writes every such Enum as a Literal.
    """
    kind, members, literal_under_all = clash
    message = (
        f"msgspec cannot decode the union {name}: its {kind}-like members {', '.join(members)} include an Enum, "
        f"while a msgspec union holds an Enum only as its one {kind}-like member."
    )
    if not literal_under_all:
        return message
    return (
        f"{message} Pass --enum-field-as-literal all to write enums as Literal values, which msgspec decodes beside "
        f"{kind} and other Literal values."
    )


def _literal_member(data_type: DataType) -> tuple[str, bool] | None:
    """Return the first member of a class or module written as a ``Literal`` value in a type, as written.

    The flag tells an enum member that ``--input-model`` or ``x-python-import`` symbols transport, which is one for
    sure, from a dotted ``x-python-type`` value, which may also name a constant of a module, such as
    ``http.client.HTTP_PORT``, that the generator cannot tell from an enum member.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        PythonTypeRuntimeSymbol,
        iter_literal_members,
        render_python_type_expr,
    )

    return next(
        (
            (render_python_type_expr(member), isinstance(member, PythonTypeRuntimeSymbol))
            for nested in data_type.all_data_types
            if nested.python_type is not None
            for member in iter_literal_members(nested.python_type.expression)
        ),
        None,
    )


def _msgspec_literal_member_message(name: str, member: tuple[str, bool]) -> str:
    """Describe a ``Literal`` value written as a member, which msgspec decodes only as None, bool, int or str."""
    value, enum_member = member
    if enum_member:
        return (
            f"msgspec cannot decode {name}: its Literal holds the enum member {value}, while a msgspec Literal holds "
            "only None, bool, int and str values."
        )
    return (
        f"msgspec decodes {name} only if {value} is a plain None, bool, int or str value: a msgspec Literal holds "
        "no other value, such as an enum member."
    )


def _msgspec_null_enum_message(model: Enum) -> str | None:
    """Describe an enum whose null member msgspec refuses, if the other members are all str or all int."""
    values = {field.name: get_raw_enum_member_value(field.default) for field in model.fields}
    if not (nulls := [name for name, value in values.items() if value is None]) or not (
        (kinds := {literal_value_kind(value) for value in values.values() if value is not None}) <= {"str"}
        or kinds <= {"int"}
    ):
        return None
    member = f"its member {nulls[0]} = None"
    fix = (
        "Pass --use-msgspec-nullable-enum-alias to write it without the null member and with a nullable alias "
        "that msgspec decodes."
    )
    return (
        f"msgspec cannot decode the enum {model.class_name}: {member} makes its values neither all str nor all int. "
        f"{fix}"
    )


_WRITTEN_ENUM_VALUE: Final[Mapping[str, Callable[[Any], object]]] = {
    "bool": bool,
    "int": int.__index__,
    "float": float.__float__,
    "str": str.__str__,
}
"""The value an enum module writes for a member value of each kind: a plain ``bool``, ``int``, ``float`` or ``str``."""


def _msgspec_mixed_enum_message(model: Enum, *, literal_under_all: bool) -> str | None:
    """Describe an enum whose values msgspec refuses: besides None, they are neither all str nor all int.

    The message recommends ``--enum-field-as-literal all`` only where that option writes the enum as a Literal
    (``literal_under_all``).

    An ``IntEnum``, a ``StrEnum`` and an ``(int, Enum)`` or ``(str, Enum)`` make every value one of them, a
    ``(float, Enum)`` or ``(bytes, Enum)`` every value a float or bytes. A plain ``Enum`` keeps the values as the
    module writes them, where one equal to an earlier value is an alias of that member, as Python has it, so ``1`` and
    ``True`` make one int member; msgspec skips a list or an object value, which has no hash, and refuses an enum left
    without values.
    """
    values = [value for field in model.fields if (value := get_raw_enum_member_value(field.default)) is not None]
    value_types = {base.type for base in model.base_classes}
    if not values or isinstance(model, (IntEnum, StrEnum)) or value_types & {"int", "str"}:
        return None
    kinds: set[str] = set()
    if value_type := next((name for name in ("float", "bytes") if name in value_types), None):
        kinds.add(value_type)
    else:
        members: dict[object, None] = {}
        for value in values:
            if (written := _WRITTEN_ENUM_VALUE.get(kind := literal_value_kind(value))) is not None and (
                key := written(value)
            ) not in members:
                members[key] = None
                kinds.add(kind)
    if kinds in ({"str"}, {"int"}):
        return None
    message = f"msgspec cannot decode the enum {model.class_name}: its values are neither all str nor all int."
    if not literal_under_all:
        return message
    floats = " A float value, which a msgspec Literal does not hold, is written as float." if "float" in kinds else ""
    return (
        f"{message} Pass --enum-field-as-literal all to write enums as Literal values, which msgspec decodes.{floats}"
    )


_MSGSPEC_INT_MIN: Final = -(2**63)
"""The least int msgspec decodes as an Enum or a Literal value, that of the signed 64-bit range."""


_MSGSPEC_INT_MAX: Final = 2**63 - 1
"""The greatest int msgspec decodes as an Enum or a Literal value, that of the signed 64-bit range."""


_BEYOND_MSGSPEC_INTS: Final = (
    "is beyond the 64-bit range, while msgspec decodes Enum and Literal ints only from -2**63 to 2**63 - 1"
)


def _msgspec_wide_int(values: Iterable[object]) -> int | None:
    """Return the first int of the values that msgspec refuses as an Enum or a Literal value (``_MSGSPEC_INT_MIN``).

    A bool, which is no int as a value, lies within the range.
    """
    return next(
        (value for value in values if isinstance(value, int) and not _MSGSPEC_INT_MIN <= value <= _MSGSPEC_INT_MAX),
        None,
    )


def _msgspec_wide_int_enum_message(model: Enum) -> str | None:
    """Describe an enum holding an int that msgspec refuses (``_msgspec_wide_int``).

    An ``IntEnum`` or an ``(int, Enum)`` makes every value an int, an integral float such as ``1e+19`` too; a
    ``StrEnum`` or an enum on another value type holds no int.
    """
    value_types = {base.type for base in model.base_classes}
    if isinstance(model, StrEnum) or value_types & {"str", "float", "bytes"}:
        return None
    values = [get_raw_enum_member_value(field.default) for field in model.fields]
    if isinstance(model, IntEnum) or "int" in value_types:
        values = [int(value) if isinstance(value, float) and value.is_integer() else value for value in values]
    if (value := _msgspec_wide_int(values)) is None:
        return None
    return f"msgspec cannot decode the enum {model.class_name}: its value {value} {_BEYOND_MSGSPEC_INTS}."


def _written_key_of(keyed: KeyedModels, reused: Mapping[ModelKey, ModelKey], reference: Reference) -> ModelKey:
    """Return the key of the model among ``keyed`` that ``reference`` names: by its key, else by its path.

    A duplicate that reuse replaced by an empty subclass keeps its Reference in the types that named it; the reference
    names the subclass, which reuses it (``reused``: the key of each reused reference to the key of its subclass).
    """
    if (key := model_key_of(reference)) not in keyed.models and reused.get(key) in keyed.models:
        return reused[key]
    return keyed.key_of(reference)


def _written_model_names(model: DataModel) -> Iterator[tuple[Reference, str, bool]]:
    """Yield each model name a model writes: the reference, what the model writes for it, and whether as a base class.

    A type that names a model writes its import's expression (unquoted) or the class name; an enum member literal
    writes the class name of its enum.
    """
    for data_type in _named_data_types(model):
        if (reference := data_type.reference) is not None:
            yield (
                reference,
                (data_type.alias or reference.short_name).strip('"'),
                isinstance(data_type, BaseClassDataType),
            )
        literal_reference = data_type._enum_member_literal_reference  # ruff: ignore[private-member-access]
        if literal_reference is not None and not data_type.alias:
            yield from ((literal_reference, enum_class, False) for enum_class, _ in data_type.enum_member_literals)


def _remap_imports(ledger: ModuleImportLedger, imports: Imports, overrides: Mapping[str, str]) -> None:
    """Move overridden names to their modules, converting import override conflicts to a user-facing generator error."""
    try:
        ledger.remap_modules(imports, overrides, _IMPORT_OVERRIDES)
    except ValueError as e:
        raise Error(str(e)) from e


def warn_about_decimal_defaults(parser: FinishHost) -> None:
    """Emit one actionable warning for all Decimal defaults in this generation."""
    if not (count := parser._decimal_default_warning_count):  # noqa: SLF001
        return
    examples = ", ".join(parser._decimal_default_warning_examples or ())  # noqa: SLF001
    remainder = (
        f" and {count - len(parser._decimal_default_warning_examples or ())} more"  # noqa: SLF001
        if count > _DECIMAL_WARNING_EXAMPLE_LIMIT
        else ""
    )
    plural = "s" if count != 1 else ""
    verb = "were" if count != 1 else "was"
    if DefaultValueType.Decimal in parser.deserialize_default_value_types:
        message = (
            f"{count} Decimal default value{plural} could not be deserialized and {verb} kept serialized "
            f"to keep generated modules importable: {examples}{remainder}."
        )
    else:
        message = (
            f"{count} Decimal default value{plural} {verb} emitted as serialized data instead of Decimal: "
            f"{examples}{remainder}. Generated output is unchanged. "
            "Use --deserialize-default-values decimal to enable Decimal deserialization."
        )
    parser._decimal_default_warning_count = 0  # noqa: SLF001
    parser._decimal_default_warning_examples = None  # noqa: SLF001
    warn_as_parser_base(message, DefaultValueTypeWarning, stacklevel=2)


def warn_about_dropped_constraints(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:
    """Warn once per field whose constraints the output model leaves out on its type, and keep each warning."""
    fields: set[tuple[ModulePath, str]] = set()
    for ctx in contexts:
        for model in ctx.models:
            for field in model.fields:
                if message := " ".join(
                    filter(None, (field.dropped_constraints_warning, tuple_unique_items_warning(parser, field)))
                ):
                    warn_as_parser_base(message, UserWarning, stacklevel=2)
                    fields.add((ctx.module, message))
    parser._dropped_constraint_fields = frozenset(fields)  # noqa: SLF001


def tuple_unique_items_warning(parser: FinishHost, field: DataModelFieldBase) -> str | None:
    """Return a warning for a unique array written as a tuple, which --use-unique-items-as-set cannot make a set.

    Runtime schema validators check uniqueItems on tuples too, so nothing is left out with them.
    """
    if (
        not parser.use_tuple_for_closed_arrays
        or not parser.use_unique_items_as_set
        or parser.generate_schema_validators
        or not _declares_unique_items(field)
        or (tuple_type := _unique_items_tuple(field.data_type)) is None
    ):
        return None
    return field._generated_without_constraints(  # ruff: ignore[private-member-access]
        f"--use-unique-items-as-set writes a uniqueItems array as a set, which would lose the positions of "
        f"{tuple_type.type_hint}",
        1,
    )


def _named_alias(model: DataModel) -> TypeAliasBase | None:
    """Return the type alias that a built-in type alias writes as its whole value, by nothing but its name."""
    if not isinstance(model, TypeAliasBase) or model._uses_custom_root_template or len(model.fields) != 1:  # noqa: SLF001
        return None
    field = model.fields[0]
    if (
        (reference := (data_type := field.data_type).reference) is None
        or not isinstance(target := reference.source, TypeAliasBase)
        or field.annotated
        or field.field
        or field.type_hint.strip("\"'") != (data_type.alias or reference.short_name).strip("\"'")
    ):
        return None
    return target


def _alias_cycles(models: Sequence[DataModel]) -> dict[int, tuple[DataModel, ...]]:
    """Map each type alias that only names the next alias of a cycle to that cycle, starting at the alias."""
    named = {id(model): target for model in models if (target := _named_alias(model)) is not None}
    by_id = {id(model): model for model in models}
    cycles: dict[int, tuple[DataModel, ...]] = {}
    visited: set[int] = set()
    for start in named:
        path: dict[int, None] = {}
        current = start
        while current in named and current not in visited and current not in path:
            path[current] = None
            current = id(named[current])
        visited.update(path)
        if current in path:
            members = [*path][[*path].index(current) :]
            for index, member in enumerate(members):
                cycles[member] = tuple(by_id[item] for item in (*members[index:], *members[:index]))
    return cycles


def _loop_error(model: DataModel, cycle: Sequence[DataModel], failure: str) -> Error:
    """Return the error for a model that uses a ``$ref`` chain looping back without reaching a schema."""
    chain = " -> ".join(f"{member.class_name} ({redact_reference(member.path)})" for member in (*cycle, cycle[0]))
    return Error(
        f"{model.class_name} uses a $ref chain that loops back without reaching a schema, which {failure}: {chain}"
    )


def _evaluated_data_types(model: DataModel, *, evaluates_annotations: bool) -> Iterator[DataType]:
    """Yield the data types that the statements defining a model evaluate as the module runs.

    A ``type`` statement defers its value. Any other type alias evaluates it, and so does the functional TypedDict
    with its fields; every annotation of a class is evaluated unless the module defers annotations.
    """
    if isinstance(model, TypeStatement):
        return
    if evaluates_annotations or isinstance(model, TypeAliasBase):
        yield from model.all_data_types
        return
    for field in model.fields_evaluated_at_definition():
        yield from field.data_type.all_data_types


def reject_alias_cycles_without_schema(
    parser: FinishHost, contexts: Sequence[ModuleContextView], *, evaluates_annotations: bool
) -> None:
    """Reject output that Pydantic builds, msgspec decodes or Python imports when it names a looping ``$ref`` chain.

    Such a chain never reaches a schema. Its type aliases only name each other: Pydantic cannot build a type alias
    whose whole value is the next alias of such a cycle (``circular-reference-schema``), and msgspec resolves such an
    alias to a string naming the next one, which it cannot decode. A root model whose root names the model itself has
    no type either: deduplication dropped it while its users still name it (``_self_named_root_models``). A class
    whose fields reach one, directly or through type aliases, could never be built or decode a value. Aliases that no
    class reaches keep their output, unless the module evaluates the name of a dropped root model as it runs, in an
    alias or, with ``evaluates_annotations``, in a class: it then fails to import, whatever reads the output.
    """
    self_named = {id(model): (model,) for model in parser._self_named_root_models}  # noqa: SLF001
    reads_annotations = parser.data_model_type._READ_BY_PYDANTIC or parser.data_model_type._READ_BY_MSGSPEC  # noqa: SLF001
    models = [model for context in contexts for model in context.models]
    if reads_annotations and (cycles := _alias_cycles(models) | self_named):
        failure = "Pydantic cannot build" if parser.data_model_type._READ_BY_PYDANTIC else "msgspec cannot decode"  # noqa: SLF001
        for model in models:
            if isinstance(model, TypeAliasBase):
                continue
            pending = [*model.all_data_types]
            seen: set[int] = set()
            while pending:
                if (reference := pending.pop().reference) is None or id(source := reference.source) in seen:
                    continue
                if (cycle := cycles.get(id(source))) is not None:
                    raise _loop_error(model, cycle, failure)
                seen.add(id(source))
                if isinstance(source, TypeAliasBase):
                    pending.extend(source.all_data_types)
    if not self_named:
        return
    for model in models:
        for data_type in _evaluated_data_types(model, evaluates_annotations=evaluates_annotations):
            if (reference := data_type.reference) is not None and (cycle := self_named.get(id(reference.source))):
                raise _loop_error(model, cycle, "Python cannot evaluate as the module runs")


def record_pydantic_floor(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:
    """Record the Pydantic release that the emitted models need above the explicit target, for the generation.

    Every fact the constructs read is final here, after the last writer before rendering. A model or field owns
    each construct, named with its module when the parse writes more than one. Output that Pydantic does not
    build has no floor to state.
    """
    if (mode := check_mode(CheckArea.GENERATED_CODE_REQUIREMENTS)) is not None and (
        unlisted := parser.data_model_type._CONFIG_KEYS_WITHOUT_REQUIREMENT()  # noqa: SLF001
    ):
        mode.violation(Error, f"config keys without a row in _requirements.py: {', '.join(unlisted)}")
    if (record := _PYDANTIC_FLOORS.get()) is None or parser.target_pydantic_version is None:
        return
    if not parser.data_model_type._READ_BY_PYDANTIC:  # noqa: SLF001
        record.record(parser, None)
        return
    used: list[tuple[PydanticConstruct, str]] = []
    for ctx in contexts:
        prefix = (
            f"{module_name}."
            if len(contexts) > 1 and (module_name := _module_name_from_module_path(ctx.module))
            else ""
        )
        for model in ctx.models:
            owner = f"{prefix}{model.class_name}"
            used.extend(
                (construct, owner if field_name is None else f"{owner}.{field_name}")
                for construct, field_name in model._pydantic_constructs()  # noqa: SLF001
            )
    record.record(parser, pydantic_floor(parser.target_pydantic_version, used))


def warn_about_variant_discriminator_tags(parser: FinishHost) -> None:
    """Warn once per variant whose generated tag differs from a tag a union maps it to."""
    for recorded in parser._variant_discriminator_tags.values():  # noqa: SLF001
        for message in recorded.warning_messages():
            warn_as_parser_base(message, UserWarning, stacklevel=2)
    parser._variant_discriminator_tags.clear()  # noqa: SLF001


def warn_about_msgspec_decoding(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:  # noqa: PLR0912
    """Warn once per union and enum of msgspec output that msgspec refuses to decode."""
    if not parser.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
        return
    from datamodel_code_generator.model.union import _DataTypeUnionBase  # noqa: PLC0415

    models = [model for context in contexts for model in context.models]
    named = {model.class_name: model for model in models}
    literal_enum_ids = parser._literal_under_all_enum_reference_ids  # noqa: SLF001
    for model in models:
        if isinstance(model, Enum):
            for message in filter(
                None,
                (
                    None if parser.use_msgspec_nullable_enum_alias else _msgspec_null_enum_message(model),
                    _msgspec_mixed_enum_message(model, literal_under_all=id(model.reference) in literal_enum_ids),
                    _msgspec_wide_int_enum_message(model),
                ),
            ):
                warn_as_parser_base(message, UserWarning, stacklevel=2)
            continue
        if isinstance(model, _DataTypeUnionBase):
            if (refusal := _msgspec_refused_structs(named.get(field.name) for field in model.fields))[0]:
                warn_as_parser_base(
                    _msgspec_union_message(model.class_name, refusal, discriminated=False), UserWarning, stacklevel=2
                )
            continue
        for field in model.fields:
            if field.is_class_var:
                continue
            member = _literal_member(field.data_type)
            discriminated = bool(field.extras.get("discriminator"))
            nested = [*field.data_type.all_data_types][::-1]
            refusal = next(
                filter(operator.itemgetter(0), map(_msgspec_refused_union_structs, nested)), _NO_MSGSPEC_REFUSAL
            )
            refused = bool(refusal[0]) and (
                refusal[1] is not None or not (discriminated and parser.use_msgspec_tagged_unions)
            )
            clashes = _msgspec_field_enum_clashes(nested, literal_enum_ids)
            wide = _msgspec_wide_int(chain.from_iterable(data_type.literals for data_type in nested))
            if member is None and not refused and not clashes and wide is None:
                continue
            name = model.class_name if parser._is_root_model(model) else f"{model.class_name}.{field.name}"  # noqa: SLF001
            if member is not None:
                warn_as_parser_base(_msgspec_literal_member_message(name, member), UserWarning, stacklevel=2)
            if refused:
                warn_as_parser_base(
                    _msgspec_union_message(name, refusal, discriminated=discriminated), UserWarning, stacklevel=2
                )
            for clash in clashes.values():
                warn_as_parser_base(_msgspec_enum_union_message(name, clash), UserWarning, stacklevel=2)
            if wide is not None:
                warn_as_parser_base(
                    f"msgspec cannot decode {name}: its Literal value {wide} {_BEYOND_MSGSPEC_INTS}.",
                    UserWarning,
                    stacklevel=2,
                )


def change_imported_model_name(
    parser: ImportsHost[Any],
    models: list[DataModel],
    imports: Imports,
    scoped_model_resolver: ModelResolver,
) -> bool:
    """Rename local models shadowing imports and report whether a rename occurred."""
    imported_names = {
        imports.alias[from_][i] if i in imports.alias[from_] and i != imports.alias[from_][i] else i
        for from_, import_ in imports.items()
        for i in import_
    }
    renamed = False
    for model in models:
        if model.class_name not in imported_names:  # pragma: no cover
            continue
        if model.class_name == IMPORT_LITERAL.import_ and alias_reused_variant_literal(parser, models, imports):
            continue

        parser._graph_builder.rename_model(  # pragma: no cover  # noqa: SLF001
            model,
            reference_name=parser._import_ledger.add_name(  # noqa: SLF001
                imports,
                scoped_model_resolver,
                get_special_path("imported_name", model.path.split("/")),
                model.reference.name,
                class_name=True,
                unique=True,
            ).name,
        )
        renamed = True
    return renamed


def alias_reused_variant_literal(parser: ImportsHost[Any], models: list[DataModel], imports: Imports) -> bool:
    """Import ``typing.Literal`` under an alias when only the tags of unions with reused variants need it.

    Such unions were left untagged before, so a model named ``Literal`` kept its name, which importers rely on.
    The alias takes no name that the module binds or its class decorators name, additional imports included.
    """
    tag_fields = parser._reused_variant_tag_fields  # noqa: SLF001
    fields = [field for model in models for field in model.fields]
    annotated = [field for field in fields if tag_fields.get(id(field)) is field]
    bindings = {
        (from_, name): imports.get_effective_name(from_, name) for from_, names in imports.items() for name in names
    }
    if (
        not annotated
        or [key for key, binding in bindings.items() if binding == IMPORT_LITERAL.import_]
        != [(IMPORT_LITERAL.from_, IMPORT_LITERAL.import_)]
        or any(IMPORT_LITERAL in field.imports for field in fields if tag_fields.get(id(field)) is not field)
    ):
        return False
    reserved_names = {
        *(model.class_name for model in models),
        *(field.name for field in fields if field.name is not None),
        *bindings.values(),
        *(parser.imports.get_effective_name(from_, name) for from_, names in parser.imports.items() for name in names),
        *(
            name
            for model in models
            for decorator in model.decorators
            for name in _string_or_identifier_pattern(decorator, parser.target_python_version).findall(decorator)
            if name
        ),
    }
    aliased_import = Import(
        from_=IMPORT_LITERAL.from_,
        import_=IMPORT_LITERAL.import_,
        alias=_reserve_import_alias(IMPORT_LITERAL.import_, reserved_names),
    )
    parser._import_ledger.apply_alias(imports, aliased_import, _REUSED_VARIANT_LITERAL)  # noqa: SLF001
    for field in annotated:
        for data_type in field.data_type.all_data_types:
            data_type._set_literal_import(aliased_import)  # noqa: SLF001
        field.invalidate_semantic_caches()
    return True


def check_frozen_render_plans() -> None:
    """Report a frozen render plan, or the type expression frozen with it, that renders other text than now.

    The plan is compared with its field's plan rebuilt now, the type expression with the field's type hint now,
    both rendered with a memo of their own, so the check leaves the parse's memo as it was.
    """
    if (mode := check_mode(CheckArea.RENDER_PLANS)) is None:
        return
    ready = cast("RenderReady", RENDER_READY.get())
    for frozen_field, plan, type_expr in ready.frozen():
        field = cast("PlannedField", frozen_field)
        with rendering_memo():
            hint = cast("DataModelFieldBase", frozen_field).data_type.type_hint
            frozen_hint = render_type_expr(cast("TypeExpr", type_expr))
        if frozen_hint != hint:
            mode.violation(
                Error, f"the type expression frozen for field {field.name} renders {frozen_hint}, now {hint}"
            )
        with ready.rebuilding():
            rebuilt = cast("FieldRenderPlan", field._build_frozen_render_plan())  # noqa: SLF001
            rebuilt_text = field._render_plan_text(rebuilt)  # noqa: SLF001
        if (frozen_text := field._render_plan_text(plan)) != rebuilt_text:  # noqa: SLF001
            mode.violation(
                Error,
                f"the render plan frozen for field {field.name} renders {frozen_text}, rebuilt {rebuilt_text}",
            )


def snapshot_model_imports(state: _FinalizeState[Any]) -> None:
    """Collect the imports of every model once, reading the plans row ``legacy.L-NF`` keeps for imports."""
    with cast("RenderReady", RENDER_READY.get()).import_view():
        state.model_imports = {model: model.imports for ctx in state.contexts for model in ctx.models}


def append_model_imports(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Add the imports of every model to its module."""
    for ctx in state.contexts:
        for model in ctx.models:
            parser._import_ledger.append(  # noqa: SLF001
                ctx.imports, state.model_imports[model], ImportProducer("model-imports-snapshot", model)
            )


def remove_unused_models(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Remove the unused models from their modules, and their imports from the module imports.

    A model leaves the output, and the store's emitted models, only when the list it leaves is the one its module
    writes; the list the module structure recorded may be an earlier one.
    """
    written_lists = {id(ctx.models) for ctx in state.contexts}
    with cast("RenderReady", RENDER_READY.get()).import_view():
        for unused_model, module, models in unused_model_lists(parser, state):
            if unused_model in models:  # pragma: no branch
                parser._import_ledger.remove(  # noqa: SLF001
                    state.module_to_import[module],
                    state.model_imports.get(unused_model, unused_model.imports),
                    ImportProducer("unused-models", unused_model),
                )
                models.remove(unused_model)
                if id(models) in written_lists:
                    parser._graph_builder.retire_model(unused_model)  # noqa: SLF001


def validate_explicit_field_aliases(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Reject the explicit field aliases that no field name can carry."""
    for ctx in state.contexts:
        for model in ctx.models:
            parser.model_resolver.validate_explicit_field_aliases(
                model.fields, parser.field_name_model_type, parser.target_python_version
            )


def remove_unused_imports(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Remove the module imports that no model of the module uses."""
    for ctx in state.contexts:
        used_names = parser._collect_used_names_from_models(ctx.models, state.model_imports)  # noqa: SLF001
        parser._import_ledger.remove_unused(ctx.imports, used_names, _REMOVE_UNUSED)  # noqa: SLF001


def resolve_module_import_conflicts(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Let the backend resolve the imports of each module that conflict with its names."""
    for ctx in state.contexts:
        with parser._import_ledger.external(ctx.imports, _IMPORT_CONFLICTS):  # noqa: SLF001
            parser.data_model_type.resolve_module_import_conflicts(ctx.models, state.model_imports, ctx.imports)


def _module_binding(expression: str | None) -> str | None:
    """Return the module binding of an expression ``<binding>.<Class>``, or None for another expression."""
    return expression.partition(".")[0] if expression and "." in expression else None


def _alias_builtin_shadowing_module_imports(parser: ImportsHost[Any], ctx: ModuleContextView) -> None:
    """Alias the module imports named like a builtin that the module also writes bare, e.g. ``list[str]``.

    ``from . import list``, which names the models of the module ``list`` (``list.Pop``), hides the builtin from the
    whole module. Such an import takes the name the module's resolver gives it once the builtin holds its own name
    (``list_1``), as module imports whose names clash with other names do, and every ``list.X`` the module writes
    follows. A name is a builtin by the static table of the target (``PYTHON_BUILTIN_TYPE_NAMES``), and the module
    writes it bare when a rendered model loads it other than as the module of an attribute. The class of an enum
    member literal is written before (``import_enum_member_literal_classes``), so a binding it names keeps its name.
    A package's own submodule keeps its name too: importing it binds the name in the package whatever the import
    is named.
    """
    references = [
        data_type for model in ctx.models for data_type in _named_data_types(model) if data_type.reference is not None
    ]
    if not (
        bindings := {
            binding
            for data_type in references
            if (binding := _module_binding(data_type.alias)) in PYTHON_BUILTIN_TYPE_NAMES
        }
    ):
        return
    bindings.difference_update(
        _module_binding(enum_class)
        for model in ctx.models
        for data_type in model.all_data_types
        for enum_class, _ in data_type.enum_member_literals
    )
    imported = {
        ctx.imports.get_effective_name(from_, name): (from_, name)
        for from_, names in ctx.imports.items()
        for name in names
    }
    resolver = ctx.scoped_model_resolver
    held = {reference.name: reference for reference in resolver.references.values()}
    if not (
        shadowing := {
            binding
            for binding in bindings
            if binding in imported and binding in held and not (ctx.is_init and imported[binding][0] == ".")
        }
    ):
        return
    ledger = parser._import_ledger  # noqa: SLF001
    aliases: dict[str, str] = {}
    for binding in sorted(_bare_name_loads(ctx.models, shadowing)):
        reference = held[binding]
        ledger.release_names(ctx.imports, resolver, {binding})
        ledger.add_name(ctx.imports, resolver, get_special_path("builtin", [binding]), binding)
        alias = aliases[binding] = ledger.add_name(
            ctx.imports, resolver, [reference.path], reference.original_name
        ).name
        from_, import_ = imported[binding]
        ledger.apply_alias(ctx.imports, Import(from_=from_, import_=import_, alias=alias), _BUILTIN_MODULE_BINDINGS)
    for data_type in references:
        if (binding := _module_binding(data_type.alias)) in aliases:
            parser._graph_builder.name_reference(  # noqa: SLF001
                data_type, f"{aliases[binding]}{cast('str', data_type.alias)[len(binding) :]}"
            )


def alias_builtin_shadowing_module_imports(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Alias the module imports named like a builtin that their module also writes bare."""
    for ctx in state.contexts:
        _alias_builtin_shadowing_module_imports(parser, ctx)


def bind_module_field_names(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Bind the field names of each module that its imports shadow."""
    for ctx in state.contexts:
        _bind_module_field_names(ctx.models, ctx.imports, parser._import_ledger)  # noqa: SLF001


def change_module_imported_model_names(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Rename the models whose names an import of their module takes; remember whether any was renamed."""
    for ctx in state.contexts:
        state.renamed_models = (
            change_imported_model_name(parser, ctx.models, ctx.imports, ctx.scoped_model_resolver)
            or state.renamed_models
        )


def name_final_classes_of_additional_properties(state: _FinalizeState[Any]) -> None:
    """Name the final classes that ``extra_items`` and additional properties reference."""
    for model in state.additional_properties_models:
        model._name_final_classes()  # noqa: SLF001


def refresh_sequence_interface_after_renames(state: _FinalizeState[Any]) -> None:
    """Decide the sequence interface again after models were renamed."""
    for model in state.all_models:
        model.finalize_sequence_interface()


def replan_schema_validators_after_renames(parser: ImportsHost[ContextT], state: _FinalizeState[ContextT]) -> None:
    """Plan the schema validator helpers of every module again after models were renamed.

    Helper-name reservations include referenced models from other modules, so a rare import collision
    invalidates every module plan; renaming only changes synthetic helper names, and the snapshots of their
    imports are kept synchronized defensively.
    """
    for ctx in state.contexts:
        parser.data_model_type.invalidate_module_code_cache(ctx.models)
    parser._prepare_schema_runtime_validation_module_code(state.contexts)  # noqa: SLF001
    parser._sync_schema_runtime_validation_module_imports(state.contexts, state.model_imports)  # noqa: SLF001


def decide_config_items(state: _FinalizeState[Any]) -> None:
    """Let each model that the modules write decide its config items, once the validator helpers are planned."""
    for model in _context_models(state.contexts):
        model._decide_config_items()  # noqa: SLF001


def remap_import_overrides(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Route the shared and module imports through the modules ``--import-overrides`` names."""
    overrides = cast("Mapping[str, str]", parser._import_overrides)  # noqa: SLF001
    _remap_imports(parser._import_ledger, parser.imports, overrides)  # noqa: SLF001
    for ctx in state.contexts:
        _remap_imports(parser._import_ledger, ctx.imports, overrides)  # noqa: SLF001


def plan_module_imports(parser: RenderHost[Any, Any, Any], run: FinishRun[Any, ContextT, Any]) -> None:
    """Compute the import plan of every module from the final names, for ``_check_module_imports`` to compare.

    It runs after the last rename (``change_imported_model_name``), under the ``MODULE_IMPORTS`` check only. A
    model's final module is the module that writes it.
    """
    if check_mode(CheckArea.MODULE_IMPORTS) is None:
        return
    emitted = unchecked(keyed_models((model for ctx in run.contexts for model in ctx.models), "module import plan"))
    names = NameTable({
        model_key_of(model.reference): FinalName(
            model.reference.short_name, _module_name_from_module_path(ctx.module_key), ctx.module
        )
        for ctx in run.contexts
        for model in ctx.models
    })
    reused = {
        model_key_of(model.reused_reference): key
        for key, model in emitted.models.items()
        if model.reused_reference is not None
    }
    for ctx in run.contexts:
        parser._import_ledger.record_plan(  # noqa: SLF001
            ctx.imports,
            module_import_plan(
                parser,
                ctx,
                names=names,
                emitted=emitted,
                reused=reused,
                module_names=run.model_path_to_module_name,
                internal_modules=run.internal_modules,
            ),
        )


def module_import_plan(  # noqa: PLR0913
    parser: RenderHost[Any, Any, Any],
    ctx: ModuleContextView,
    *,
    names: NameTable,
    emitted: KeyedModels,
    reused: Mapping[ModelKey, ModelKey],
    module_names: Mapping[str, str],
    internal_modules: set[ModulePath],
) -> ModuleImportPlan:
    """Plan the models a module names: its own by their final class names, the others through their imports.

    A reference names a model of the module first, then one of any module (``_written_key_of``); one that names
    no written model is written by its own name, without an import, as today. The generated base class is
    imported by its class name from the module that writes it, as ``cross_module.apply_generic_base_class``
    imports it; any other model of another module through the name ``allocate_bindings`` gives its import. An
    import of a name that ``--import-overrides`` lists comes from the module it names.
    """
    members = unchecked(keyed_models(ctx.models, "module import plan"))
    overrides = parser._import_overrides or {}  # noqa: SLF001
    generic_base = None if parser._generic_base_model is None else model_key_of(parser._generic_base_model.reference)  # noqa: SLF001
    uses: list[ReferenceUse] = []
    pending: list[tuple[ModelKey, str, str, str, ReferenceImportTarget, str]] = []
    references: dict[tuple[str | None, str], ReferenceImport] = {}

    def add_reference(target: ModelKey, import_: Import) -> None:
        if (module := overrides.get(import_.import_)) is not None:
            import_ = dataclasses.replace(import_, from_=module)
        references.setdefault(
            (import_.from_, import_.import_), ReferenceImport(target, names.names[target].module, import_)
        )

    for model in ctx.models:
        module_name = _get_model_module_name(model, module_names)
        for reference, written, base_class in _written_model_names(model):
            if (target := _written_key_of(members, reused, reference)) in members.models:
                uses.append(ReferenceUse(target, model.class_name, names.class_name(target, reference), written))
            elif (final := names.names.get(target := _written_key_of(emitted, reused, reference))) is None:
                uses.append(ReferenceUse(target, model.class_name, reference.short_name, written))
            elif target == generic_base:
                uses.append(ReferenceUse(target, model.class_name, final.class_name, written))
                add_reference(
                    target,
                    Import(from_=_generic_base_class_module(ctx.module, final.module_path), import_=final.class_name),
                )
            else:
                name = names.class_name(target, reference)
                import_target = reference_import_target(
                    name,
                    module_name,
                    f"{final.module}.{name}" if final.module else name,
                    use_exact=ctx.imports.use_exact,
                    init=ctx.is_init,
                    internal_modules=internal_modules,
                    base_class=base_class,
                    treat_dot_as_module=parser.treat_dot_as_module,
                )
                pending.append((target, model.class_name, name, written, import_target, reference.path))
    bindings = allocate_bindings(
        (BindingRequest(t.full_path, t.import_) for *_, t, _ in pending),
        parser._import_ledger.name_log(ctx.imports, ctx.scoped_model_resolver.exclude_names),  # noqa: SLF001
        target_python_version=parser.target_python_version,
    )
    for target, holder, name, written, import_target, reference_path in pending:
        if (binding := bindings.get(BindingRequest(import_target.full_path, import_target.import_))) is None:
            uses.append(ReferenceUse(target, holder, None, written))
            continue
        add_reference(
            target,
            Import(
                from_=import_target.written_from,
                import_=import_target.import_,
                alias=binding,
                reference_path=reference_path,
            ),
        )
        uses.append(ReferenceUse(target, holder, import_target.expression(name, binding) or name, written))
    return ModuleImportPlan(ctx.module_key, tuple(references.values()), tuple(uses))


def record_generated_class_names(parser: FinishHost, run: FinishRun[Any, ModuleContextView, Any]) -> None:
    """Record the class name of every generated model."""
    parser.generated_class_names = tuple(
        (model.class_name, model.duplicate_class_name) for ctx in run.contexts for model in ctx.models
    )


def add_root_init_result(run: FinishRun[Any, Any, Any]) -> None:
    """Add an empty root ``__init__.py`` when the output has several top-level packages."""
    root_init: ModulePath = ("__init__.py",)
    if root_init not in run.module_results:
        top_level_dirs = {k[0] for k in run.module_results if len(k) >= 2}  # noqa: PLR2004
        if len(top_level_dirs) > 1:
            run.module_results[root_init] = run.result_type(body="")


def render(parser: RenderHost[ConfigT, ContextT, ResultT], run: FinishRun[ConfigT, ContextT, ResultT]) -> None:
    """Render every module into the output of ``parse()``."""
    check_frozen_render_plans()
    plan_module_imports(parser, run)
    run.output = render_modules(
        parser,
        run.module_results,
        contexts=run.contexts,
        sorted_data_models=run.sorted_data_models,
        model_identities=run.model_identities,
        config=run.config,
        forwarder_map=run.forwarder_map,
        require_update=parser._recursion_facts["require_update"],  # noqa: SLF001
    )


def record_model_placement(
    parser: FinishHost,
    contexts: Sequence[ModuleContextView],
    has_internal_modules: bool,  # noqa: FBT001
) -> None:
    """Record where models other than typed map values are written, and whether the typed values could move them.

    Typed map values move other models only through a module of their own, an internal module of an import
    cycle, or a module shared by reused models, and keep a model only as a nullable map they make recursive,
    which ``--collapse-root-models`` would otherwise collapse.
    """
    typed_map_values = parser._typed_map_value_reference_ids  # noqa: SLF001
    placement = {ctx.module: ctx.models for ctx in contexts}
    parser.model_placement = {
        module: frozenset(model.class_name for model in models if id(model.reference) not in typed_map_values)
        for module, models in placement.items()
    }
    parser.typed_map_values_may_move_models = (
        has_internal_modules
        or (parser.reuse_model and parser.reuse_scope == ReuseScope.Tree)
        or any(
            models and all(id(model.reference) in typed_map_values for model in models) for models in placement.values()
        )
        or (
            parser.collapse_root_models
            and any(
                id(model.reference) in parser.typed_nullable_map_reference_ids
                for models in placement.values()
                for model in models
            )
        )
    )


def render_modules(  # noqa: PLR0913
    parser: RenderHost[ConfigT, ContextT, ResultT],
    results: dict[ModulePath, ResultT],
    *,
    contexts: list[ContextT],
    sorted_data_models: SortedDataModels,
    model_identities: Mapping[DataModel, str] | None,
    config: ConfigT,
    forwarder_map: ForwarderMap,
    require_update: Collection[ModelKey],
) -> str | dict[tuple[str, ...], ResultT]:
    """Render the shared result mapping and apply final output-only transformations."""
    future_imports = parser._import_ledger.extract_future(parser.imports, _FUTURE_IMPORTS)  # noqa: SLF001
    future_imports_str = str(future_imports)
    if forwarder_map:
        forwarder_map = _forward_defined_models(contexts, _released_name_imports(parser, contexts, forwarder_map))

    # Export depth and collision prefixes must follow the final package layout.
    if (
        parser.treat_dot_as_module
        and config.all_exports_scope is not None
        and any(_expand_export_module_path(ctx.module) != ctx.module for ctx in contexts)
    ):
        contexts = [ctx._replace(module=_expand_export_module_path(ctx.module)) for ctx in contexts]

    for ctx in contexts:
        result = parser._generate_module_output(  # noqa: SLF001
            ctx, config, contexts, forwarder_map, require_update, future_imports_str
        )
        if result is not None:
            results[ctx.module] = result

    if config.all_exports_scope is not None and not parser.treat_dot_as_module:
        parser._generate_empty_init_exports(results, contexts, config, future_imports_str)  # noqa: SLF001

    parser._inspect_invalid_dotted_stdout(contexts, sorted_data_models, config, results)  # noqa: SLF001

    if model_identities is not None:
        parser.model_metadata, parser.model_metadata_identities = parser._build_model_metadata(  # noqa: SLF001
            contexts, model_identities
        )
    else:
        parser.model_metadata, parser.model_metadata_identities = None, ()

    if [*results] == [("__init__.py",)]:
        single_result = results["__init__.py",]
        return single_result.body

    results = {
        _normalize_result_module_path(module, treat_dot_as_module=parser.treat_dot_as_module): result
        for module, result in results.items()
    }
    if parser.treat_dot_as_module:
        results = parser._result_modules_postprocessor(results, empty_init=config.all_exports_scope is not None)  # noqa: SLF001
        if config.all_exports_scope is not None:
            parser._generate_empty_init_exports(results, contexts, config, future_imports_str)  # noqa: SLF001
    return results
