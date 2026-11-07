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
from collections import defaultdict
from itertools import chain, repeat
from keyword import iskeyword
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import Protocol

from datamodel_code_generator import DefaultValueTypeWarning, Error, ReuseScope
from datamodel_code_generator._constraint_applicability import MSGSPEC_OUTPUT, WIDENED_VALUES_KEY, int_beyond_values
from datamodel_code_generator._container_kinds import ARRAYS, ITEM_CONTAINERS, MAPPINGS, SLOT_CLASSES
from datamodel_code_generator._graph import reach
from datamodel_code_generator._message_text import counted, english_list, for_count, listed_with_more
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._module_paths import (
    dotted_module,
    dotted_name,
    expanded_path,
    is_package_init,
    is_within,
    is_within_name,
    module_parts,
    normalized_path,
    written_path,
)
from datamodel_code_generator._python_syntax import holds_lone_surrogate
from datamodel_code_generator._python_target_names import is_builtin_type_name
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, walk_graph
from datamodel_code_generator._requirements import pydantic_floor
from datamodel_code_generator._target_unicode import is_importable_part
from datamodel_code_generator._url_redaction import redact_reference
from datamodel_code_generator.enums import DefaultValueType
from datamodel_code_generator.imports import (
    IMPORT_ANNOTATIONS,
    IMPORT_LITERAL,
    IMPORT_TYPE_ALIAS_TYPE,
    Import,
    _aliased_name,
    _import_bindings,
    _import_key,
    _iter_import_bindings,
    _stored_import,
)
from datamodel_code_generator.model._decisions.enum_kind import enum_member_kinds, literal_value_kind
from datamodel_code_generator.model._decisions.tag_value import struct_union_refusal
from datamodel_code_generator.model._render_plan import RENDER_READY
from datamodel_code_generator.model._rendered_names import FieldNameBindings
from datamodel_code_generator.model.base import BaseClassDataType, DataModel, _bare_loads_of, _model_key_of_model
from datamodel_code_generator.model.enum import Enum, Member, get_raw_enum_member_value
from datamodel_code_generator.model.output import _model_field_name_collisions
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser._imports_plan import Added, Aliased, ImportProducer, Removed
from datamodel_code_generator.parser._passes._import_binding import (
    ReferenceExpressionHost,
    reference_import_target,
    register_reference_import,
    shared_import_bindings,
)
from datamodel_code_generator.parser._passes._import_edges import (
    GenericBaseClassPlacement,
    _written_package,
    failing_import_reads,
    import_cycle_seeds,
    import_plan,
    module_import_edges,
    relative_import_targets,
    runs_into_import_cycle,
    written_import_blocks,
)
from datamodel_code_generator.parser._passes._name_allocation import scheme_resolver
from datamodel_code_generator.parser._passes._reference_users import alias_loops
from datamodel_code_generator.parser._passes._shared import (
    BaseStandInHost,
    DataModelTypeHost,
    GraphBuilderHost,
    ImportOptionsHost,
    OutputOptionsHost,
    RecursionFactsHost,
    ReplacedRootsHost,
    ReuseOptionsHost,
    RootModelHost,
    _context_models,
    _format_tags,
    _get_data_type_target_full_name,
    _get_model_module_name,
    _is_discriminator_wrapper,
    _iter_discriminator_data_types,
    _named_data_types,
    _parsed_rule_data_types,
    _string_or_identifier_pattern,
    definition_reads,
    get_special_path,
    site_display_name,
    unique_items_plan,
    unused_model_lists,
)
from datamodel_code_generator.parser._passes._state import _PYDANTIC_FLOORS, ConfigT, ContextT, ResultT
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._tag_plan import TagPlanHost
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import field_origin_of, named_schema_location, type_origin_of
from datamodel_code_generator.types import union_members, written_union_members

if TYPE_CHECKING:
    from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator._format_types import PythonVersion
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._python_type_annotation import PythonTypeExpr
    from datamodel_code_generator._requirements import PydanticConstruct
    from datamodel_code_generator._target_unicode import ModulePartRow
    from datamodel_code_generator._user_keys import ConstructionKeys
    from datamodel_code_generator.config import ParserConfig
    from datamodel_code_generator.enums import AllExportsScope
    from datamodel_code_generator.imports import Imports, _ImportKey
    from datamodel_code_generator.model._render_plan import RenderReady
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.model_metadata import ModelMetadata
    from datamodel_code_generator.parser._imports_plan import ModuleImportLedger
    from datamodel_code_generator.parser._model_shapes import ReuseGroups
    from datamodel_code_generator.parser._passes._import_edges import ImportCycleSeeds, ImportPlan
    from datamodel_code_generator.parser._passes._shared import ModuleNamesView
    from datamodel_code_generator.parser._passes._state import (
        FinishRun,
        ForwarderMap,
        ModuleContextView,
        ModulePath,
        ParseConfigView,
        SortedDataModels,
        _FinalizeState,
    )
    from datamodel_code_generator.parser.generation import GenerationStore
    from datamodel_code_generator.reference import ModelResolver, ModelType, Reference
    from datamodel_code_generator.types import DataType

__all__ = [
    "FinishHost",
    "ImportsHost",
    "RenderHost",
    "add_root_init_result",
    "alias_builtin_shadowing_module_imports",
    "alias_builtins_hidden_by_submodules",
    "append_model_imports",
    "bind_module_field_names",
    "builtin_name_bindings",
    "change_imported_model_name",
    "check_import_reads",
    "decide_config_items",
    "import_models_of_hidden_submodules",
    "import_names_class",
    "modules_hidden_by_imports",
    "name_final_classes_of_additional_properties",
    "record_generated_class_names",
    "record_model_placement",
    "record_pydantic_floor",
    "refresh_sequence_interface_after_renames",
    "reject_class_named_like_submodule",
    "reject_lone_surrogates_pydantic_reads",
    "reject_unimportable_imports",
    "remap_import_overrides",
    "remove_unused_imports",
    "remove_unused_models",
    "render",
    "render_module_models",
    "replan_schema_validators_after_renames",
    "resolve_module_import_conflicts",
    "snapshot_model_imports",
    "submodules_hidden_by_packages",
    "translate_final_name_keys",
    "validate_explicit_field_aliases",
    "warn_about_decimal_defaults",
    "warn_about_dropped_constraints",
    "warn_about_msgspec_decoding",
    "warn_about_variant_discriminator_tags",
]


class ImportsHost(
    ReferenceExpressionHost,
    ReplacedRootsHost,
    GraphBuilderHost,
    DataModelTypeHost,
    ImportOptionsHost,
    TagPlanHost,
    Protocol[ContextT],
):
    """The options, run state and hooks of the parser that the import passes read and write."""

    @property
    def field_name_model_type(self) -> ModelType:
        """The model type that field names are validated for."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of model and field names."""

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

    def _writes_module_future_annotations(self, models: list[DataModel], config: Any) -> bool:
        """Return whether a module writes ``from __future__ import annotations`` of its own, beside shared imports."""


class FinishHost(ReuseOptionsHost, RootModelHost, OutputOptionsHost, TagPlanHost, Protocol):
    """The options and run state of the parser that the finish passes read and write."""

    _decimal_default_warning_count: int
    _decimal_default_warning_examples: list[str] | None
    _dropped_constraint_fields: frozenset[tuple[ModulePath, str]]
    final_name_keys_to_assign: tuple[tuple[str, tuple[str, ...]], ...]
    generated_class_names: tuple[tuple[str, str], ...]
    model_placement: dict[ModulePath, frozenset[str]]
    typed_map_values_may_move_models: bool

    @property
    def _construction_keys(self) -> ConstructionKeys:
        """The class names that the options read while models are built key (``_user_keys.py``)."""

    @property
    def generation_store(self) -> GenerationStore:
        """The generation store of the parse, which records the renames after L2."""

    @property
    def model_resolver(self) -> ModelResolver:
        """The resolver of model and field names, which records the final names when the run records its names."""

    @property
    def _literal_under_all_enum_reference_ids(self) -> set[ModelKey]:
        """The model keys of the enums that ``--enum-field-as-literal all`` writes as Literal values.

        A type whose Reference is a deep copy names its enum by the key of the original (#5824).
        """

    @property
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models naming themselves that the deduplication of root models dropped, whose users name them."""

    @property
    def _folded_ref_chains(self) -> dict[str, tuple[tuple[str, str], ...]]:
        """The name and path of each definition parsed as its ``$ref`` alone that a model naming itself took in."""

    @property
    def _reuse_groups(self) -> ReuseGroups:
        """The plan of which model of a group of duplicates stays canonical."""

    @property
    def generate_schema_validators(self) -> bool:
        """Whether models get the schema validator rules the types cannot express."""

    @property
    def typed_nullable_map_reference_ids(self) -> set[ModelKey]:
        """The model keys of the nullable maps whose values the parse typed."""

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


class RenderHost(
    RecursionFactsHost,
    ReferenceExpressionHost,
    BaseStandInHost,
    ImportOptionsHost,
    Protocol[ConfigT, ContextT, ResultT],
):
    """The options, run state and rendering hooks of the parser that planning imports and rendering read and call."""

    model_metadata: ModelMetadata | None
    model_metadata_identities: tuple[str, ...]
    import_cycle_seeds_to_apply: ImportCycleSeeds | None
    fails_to_import: bool
    hidden_class_refusal: str | None
    _hidden_submodule_renames: tuple[tuple[ModulePath, str], ...]
    _written_imports: list[tuple[ModulePath, Imports]]

    @property
    def config(self) -> ParserConfig:
        """The parser configuration."""

    @property
    def _self_named_root_models(self) -> list[DataModel]:
        """The root models that name themselves, which deduplication dropped from every module."""

    @property
    def _import_cycle_seeds(self) -> ImportCycleSeeds:
        """What an earlier attempt of the run found to fail as the output is imported."""

    @property
    def custom_template_dir(self) -> Path | None:
        """The directory of the custom templates, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model class the output writes."""

    @property
    def pydantic_v2_root_model_type(self) -> type[DataModel] | None:
        """The root model type whose class body evaluates its root type, if the output has one."""

    @property
    def _generic_base_model(self) -> DataModel | None:
        """The generated base class of every model, if the output has one."""

    @property
    def _result_modules_postprocessor(self) -> Callable[..., dict[tuple[str, ...], ResultT]]:
        """The function that lays out the results of the modules under ``--treat-dot-as-module``."""

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
_BUILTIN_SUBMODULE_BINDINGS: Final = ImportProducer("builtin-submodule-bindings")
_SUBMODULE_IMPORT_BINDINGS: Final = ImportProducer("submodule-import-bindings")
_HIDDEN_SUBMODULES: Final = "hidden-submodules"
_HIDDEN_SUBMODULE_IMPORT_BINDINGS: Final = ImportProducer("hidden-submodule-import-bindings")
_REUSED_VARIANT_LITERAL: Final = ImportProducer("reused-variant-literal")
_IMPORT_OVERRIDES: Final = ImportProducer("import-overrides")


def _reserve_import_alias(name: str, reserved_names: set[str]) -> str:
    """Reserve and return the first ``<name>_aliased`` name, with a suffix when taken."""
    alias = _aliased_name(name, reserved_names.__contains__)
    reserved_names.add(alias)
    return alias


def _alias_reserved_names(
    models: Sequence[DataModel], imports: Imports, shared_imports: Imports, *, also: Iterable[str] = ()
) -> set[str]:
    """Return the names an alias that a module imports a name under takes none of.

    The names of its classes and fields, those its imports and the shared imports bind, and ``also``.
    """
    return {
        *also,
        *(model.class_name for model in models),
        *(field.name for model in models for field in model.fields if field.name is not None),
        *_iter_import_bindings(imports),
        *_iter_import_bindings(shared_imports),
    }


def _bind_module_field_names(  # noqa: PLR0913
    models: list[DataModel],
    imports: Imports,
    shared_imports: Imports,
    ledger: ModuleImportLedger,
    target: tuple[int, int],
    *,
    texts: Sequence[str] | None = None,
) -> bool:
    """Alias the names a property hides from the text of its class; return whether it aliased any.

    The names are those the module's imports bind and the builtin classes of the ``target`` that no import binds,
    which a builtin import binds under the alias. An alias avoids every name the module's and the shared imports
    bind. Type aliases sit at module scope, so an import they name keeps its name beside the alias. ``texts`` are the
    rendered models; without them a model renders where it holds a field of such a name.
    """
    field_names = {field.name for model in models for field in model.fields if field.name is not None}
    shadowable_imports = {binding: key for key, binding in _import_bindings(imports) if binding in field_names}
    shadowable_imports.update(
        (name, ("builtins", name))
        for name in sorted(field_names.difference(shadowable_imports))
        if is_builtin_type_name(name, target)
    )
    if not shadowable_imports:
        return False
    collisions: set[str] = set()
    for index, model in enumerate(models):
        collisions.update(
            _model_field_name_collisions(model, None if texts is None else texts[index], shadowable_imports)
        )
    if not collisions:
        return False
    reserved_names = _alias_reserved_names(models, imports, shared_imports)
    type_alias_imports = {
        _import_key(import_) for model in models if isinstance(model, TypeAliasBase) for import_ in model.imports
    }
    expressions: dict[str, str] = {}
    loads: dict[str, str] = {}
    for name, (from_, imported_name) in shadowable_imports.items():
        if name not in collisions:
            continue
        alias = _reserve_import_alias(name, reserved_names)
        keep_unaliased = (from_, imported_name) in type_alias_imports
        aliased_import = Import(from_=from_, import_=imported_name, alias=alias, keep_unaliased=keep_unaliased)
        if from_ == "builtins" or keep_unaliased:
            expressions[name] = alias
            ledger.append(imports, aliased_import, _FIELD_NAME_BINDINGS)
        else:
            loads[name] = alias
            ledger.apply_alias(imports, aliased_import, _FIELD_NAME_BINDINGS)
    bindings = FieldNameBindings(expressions, loads)
    for model in models:
        if not isinstance(model, TypeAliasBase):
            model.set_field_name_bindings(bindings)
    return True


def render_module_models(  # noqa: PLR0913, PLR0917
    models: list[DataModel],
    imports: Imports,
    shared_imports: Imports,
    ledger: ModuleImportLedger,
    target: tuple[int, int],
    overrides: Mapping[str, str] | None,
) -> list[str]:
    """Render the models of a module, alias the names a property hides in the texts and render the aliased again.

    Where ``bind_module_field_names`` bound the names before, the texts already write the aliases. The imports the
    aliases add go through ``--import-overrides`` (``overrides``), as ``remap_import_overrides`` routes the others.
    """
    texts = [model.render() for model in models]
    if not _bind_module_field_names(models, imports, shared_imports, ledger, target, texts=texts):
        return texts
    if overrides is not None:
        _remap_imports(ledger, imports, overrides)
    return [
        text if isinstance(model, TypeAliasBase) else model.render() for model, text in zip(models, texts, strict=True)
    ]


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
        resolver = scheme_resolver(
            "forwarder",
            target_python_version=parser.target_python_version,
            exclude_names={field.name for _, model in models for field in model.fields if field.name is not None},
        )
        for released_name, model in models:
            resolver.add([model.path], released_name)
        ctx = contexts_by_module[module]
        module_name = dotted_module(module)
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
            target = reference_import_target(
                released_name,
                module_name,
                dotted_name(dotted_module(released_module), released_name),
                use_exact=ctx.imports.use_exact,
                init=ctx.is_init,
                internal_modules=forwarder.released_internal_modules,
                base_class=isinstance(data_type, BaseClassDataType),
                shared_imports=parser.imports,
            )
            target.reserve_name(resolver)
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
    if (refusal := struct_union_refusal([struct.discriminator_tag for struct in structs])) is None:
        return _NO_MSGSPEC_REFUSAL
    kind, values = refusal
    if kind == "untagged":
        return structs, None
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


def _msgspec_union_members(data_type: DataType) -> Sequence[DataType]:
    """Return the members of the union a type writes: its own, or the items of a list, set or map of several types.

    Such a container writes its items as a union inside it; the items of a tuple are its positions instead.
    """
    if members := union_members(data_type):
        return members
    if len(data_type.data_types) > 1 and (data_type in ITEM_CONTAINERS or data_type in MAPPINGS):
        return data_type.data_types
    return ()


def _iter_union_alias_roots(data_type: DataType) -> Iterator[DataType]:
    """Yield the root types of the aliases a union names."""
    for member in written_union_members(data_type.data_types):
        if (
            member.reference
            and isinstance(source := member.reference.source, DataModel)
            and _is_discriminator_wrapper(source)
            and source.fields
        ):
            yield source.fields[0].data_type


def _msgspec_refused_union_structs(data_type: DataType) -> _MsgspecRefusal:
    """Return the Struct types of a union msgspec refuses, unless an alias it names is refused on its own."""
    if (
        not (members := _msgspec_union_members(data_type))
        or not (refusal := _msgspec_refused_member_structs(members))[0]
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


def _msgspec_enum_clashes(
    data_types: Iterable[DataType], literal_enum_ids: Collection[ModelKey]
) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind and its written members of a union that msgspec decodes once its Enums are written as Literal.

    msgspec decodes a union only if it holds an Enum of str values as its one str-like member and an Enum of int
    values as its one int-like member, while it takes Literal values beside each other and beside str or int. A
    member of another str-like type, such as bytes, refuses a Literal of str values too, so it leaves the str side be.
    ``literal_enum_ids`` holds the model keys of the Enums that ``--enum-field-as-literal all`` writes as a Literal; the
    Enums are told apart by model key, so a deep copy of a type names the Enum of its original.
    """
    enums: dict[str, set[ModelKey]] = {"str": set(), "int": set()}
    classes: dict[str, set[ModelKey]] = {"str": set(), "int": set()}
    names: dict[str, dict[str, None]] = {"str": {}, "int": {}}
    others: set[str] = set()
    refused: set[str] = set()
    for member in written_union_members(data_types):
        for leaf, _, _ in _iter_discriminator_data_types((member,)):
            if isinstance(source := leaf.reference.source if leaf.reference else None, Enum):
                if (kinds := _msgspec_enum_kinds(source)) in ({"str"}, {"int"}):
                    enums[kind := next(iter(kinds))].add(key := model_key_of(source.reference))
                    names[kind][member.type_hint] = None
                    if key not in literal_enum_ids:
                        classes[kind].add(key)
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
    data_type: DataType, literal_enum_ids: Collection[ModelKey]
) -> tuple[_MsgspecEnumClash, ...]:
    """Return each kind of a union msgspec refuses for an Enum, unless an alias it names is refused for that kind."""
    if not (members := _msgspec_union_members(data_type)) or not (
        clashes := _msgspec_enum_clashes(members, literal_enum_ids)
    ):
        return ()
    covered = {
        clash[0]
        for root in _iter_union_alias_roots(data_type)
        for clash in _msgspec_enum_clashes((root,), literal_enum_ids)
    }
    return tuple(clash for clash in clashes if clash[0] not in covered)


_MsgspecKindClash: TypeAlias = tuple[str, tuple[str, ...]]
"""A kind of the members of a union that msgspec refuses for holding more than one member of that kind, and its
written members."""


_ClashT = TypeVar("_ClashT", _MsgspecEnumClash, _MsgspecKindClash)


def _first_union_clashes(clashes: Iterable[_ClashT]) -> dict[str, _ClashT]:
    """Return the clash of each kind that the unions among the types of a field are refused for, the first union's."""
    first: dict[str, _ClashT] = {}
    for clash in clashes:
        first.setdefault(clash[0], clash)
    return first


_MSGSPEC_KIND_ASIDES: Final[Mapping[str, str]] = {
    "dict": " (its Struct types count as one)",
    "array": "",
    "str": " (str and Literal str values count as one)",
    "number": "",
}
"""The kinds of which a msgspec union holds one member, besides the Enums of ``_msgspec_enum_clashes``, with what
counts as one member of the kind."""


_MSGSPEC_ANNOTATION_KINDS: Final[Mapping[str, str]] = {
    **{
        name: "dict" if slot in MAPPINGS.slots else "array"
        for slot, written in SLOT_CLASSES.items()
        for name in (written.name, written.typing_name)
    },
    **dict.fromkeys(("str", *_MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS), "str"),
}
"""The kind that msgspec counts of each class a Python type annotation writes, by its name: the container classes
(``SLOT_CLASSES``), str and the other str-like types."""


def _msgspec_annotation_slots(expression: PythonTypeExpr) -> Iterator[tuple[str, object]]:
    """Yield the kind msgspec counts of each member a Python type annotation writes, and the member it counts as.

    The items of a union annotation are members of their own; a class counts by its name
    (``_MSGSPEC_ANNOTATION_KINDS``), a container as its written type and a str-like type as its name.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        is_union_python_type_expr,
        python_type_expr_arguments,
        python_type_expr_base_name,
        render_python_type_expr,
    )

    if is_union_python_type_expr(expression):
        for item in python_type_expr_arguments(expression):
            yield from _msgspec_annotation_slots(item)
    elif (kind := _MSGSPEC_ANNOTATION_KINDS.get(name := python_type_expr_base_name(expression))) is not None:
        yield kind, name if kind == "str" else render_python_type_expr(expression)


def _msgspec_member_slots(leaf: DataType) -> tuple[tuple[str, object], ...]:
    """Return each kind of a union member that msgspec counts, with the member it counts as.

    The Struct types of a union count as one dict-like member, and str with the str values of a Literal as one
    str-like member; an Enum of str values is one str-like member of its own. A type that only holds one member
    counts as that member, and one a Python type annotation types as the members the annotation writes, where it
    writes any msgspec counts (``_msgspec_annotation_slots``).
    """
    while leaf._writes_nothing_of_its_own and len(leaf.data_types) == 1:  # noqa: SLF001
        leaf = leaf.data_types[0]
    if (
        leaf.python_type is not None
        and leaf.reference is None
        and (annotated := (*_msgspec_annotation_slots(leaf.python_type.expression),))
    ):
        return annotated
    slot: tuple[str, object] | None = None
    if (source := leaf.reference.source if leaf.reference else None) is not None:
        if isinstance(source, Enum):
            if _msgspec_enum_kinds(source) == {"str"}:
                slot = "str", model_key_of(source.reference)
        elif isinstance(source, DataModel) and source.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
            slot = "dict", None
    elif leaf in MAPPINGS:
        slot = "dict", leaf.type_hint
    elif leaf in ARRAYS:
        slot = "array", leaf.type_hint
    elif leaf.literals and not leaf.data_types and not leaf.enum_member_literals:
        if any(literal_value_kind(literal) == "str" for literal in leaf.literals):
            slot = "str", "str"
    elif (
        not leaf.data_types
        and not leaf.enum_member_literals
        and (leaf.type == "str" or leaf.type in _MSGSPEC_STR_TYPES_REFUSED_BESIDE_LITERALS)
    ):
        slot = "str", leaf.type
    number = _msgspec_number_slot(leaf)
    return (*(() if slot is None else (slot,)), *(() if number is None else (number,)))


def _msgspec_number_slot(leaf: DataType) -> tuple[str, object] | None:
    """Return the number kind of an int ``Literal``, of ``float`` and of ``int``, which msgspec reads in a union.

    msgspec sends every JSON integer to the int ``Literal`` member of a union that also holds ``float``, so an integer
    that is not one of the values is refused although ``float`` admits it; a union that also holds ``int`` sends them
    to ``int`` instead (``_msgspec_kind_clashes``).
    """
    if leaf.data_types or leaf.enum_member_literals:
        return None
    if leaf.literals:
        return ("number", "int Literal") if any(literal_value_kind(item) == "int" for item in leaf.literals) else None
    return ("number", leaf.type) if leaf.type in {"float", "int"} else None


def _msgspec_kind_clashes(
    data_types: Iterable[DataType], literal_enum_ids: Collection[ModelKey]
) -> tuple[_MsgspecKindClash, ...]:
    """Return each kind and its written members of a union that msgspec refuses for more than one member of the kind.

    msgspec decodes a union only if it holds at most one dict-like member (``dict`` or Struct types), one array-like
    member (``list``, ``tuple``, ``set``) and one str-like member (``str``, an Enum of str values, ``bytes``, the dates,
    times, ``UUID`` and ``Decimal``). A kind for which ``_msgspec_enum_clashes`` reports the union is left to it. An int
    ``Literal`` beside ``float`` loses the integers it does not list only where no ``int`` member takes them.
    """
    slots: dict[str, dict[object, None]] = {kind: {} for kind in _MSGSPEC_KIND_ASIDES}
    names: dict[str, dict[str, None]] = {kind: {} for kind in _MSGSPEC_KIND_ASIDES}
    members = (*written_union_members(data_types),)
    for member in members:
        for leaf, _, _ in _iter_discriminator_data_types((member,)):
            for kind, counted_as in _msgspec_member_slots(leaf):
                slots[kind][counted_as] = None
                names[kind][member.type_hint] = None
    covered = {clash[0] for clash in _msgspec_enum_clashes(members, literal_enum_ids)}
    if "int" in slots["number"]:
        covered.add("number")
    return tuple((kind, (*names[kind],)) for kind, counted in slots.items() if len(counted) > 1 and kind not in covered)


def _msgspec_refused_union_kinds(
    data_type: DataType, literal_enum_ids: Collection[ModelKey]
) -> tuple[_MsgspecKindClash, ...]:
    """Return each kind a union msgspec refuses for more than one member of, unless an alias it names is refused so."""
    if not (members := _msgspec_union_members(data_type)) or not (
        clashes := _msgspec_kind_clashes(members, literal_enum_ids)
    ):
        return ()
    covered = {
        clash[0]
        for root in _iter_union_alias_roots(data_type)
        for clash in _msgspec_kind_clashes((root,), literal_enum_ids)
    }
    return tuple(clash for clash in clashes if clash[0] not in covered)


def _msgspec_kind_union_message(name: str, clash: _MsgspecKindClash) -> str:
    """Describe a union that msgspec refuses to decode because it holds more than one member of a kind."""
    kind, members = clash
    if kind == "number":
        return (
            f"msgspec cannot decode every integer of the union {name}: its members {', '.join(members)} hold an int "
            "Literal beside float, and msgspec sends every integer to the Literal, so an integer that is not one of "
            "its values is refused although float admits it."
        )
    return (
        f"msgspec cannot decode the union {name}: it holds the {kind}-like members {', '.join(members)}, while a "
        f"msgspec union holds only one {kind}-like member{_MSGSPEC_KIND_ASIDES[kind]}."
    )


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


def _refuse_msgspec_bytes_and_str_literal(parser: FinishHost, model: DataModel, field: DataModelFieldBase) -> None:
    """Refuse a field of msgspec output whose ``Literal`` holds both bytes and str values, which no msgspec type holds.

    The values may also be those of the ``Literal`` members of a union (``Literal[b'ab'] | Literal['a']``). A msgspec
    ``Literal`` holds no bytes value, and ``bytes`` beside the str values is a union of two str-like members, which
    msgspec refuses too, so the bound annotation keeps such a ``Literal`` or union as written
    (``write_refused_literal_values``). ``str`` would take every string and ``bytes`` no str value.
    """
    from datamodel_code_generator._python_type_annotation import (  # noqa: PLC0415
        bytes_and_str_literal,
        literal_values_as_text,
        render_python_type_expr,
    )

    if (
        literal := next(
            (
                literal
                for nested in field.data_type.all_data_types
                if nested.python_type is not None
                and (literal := bytes_and_str_literal(nested.python_type.expression)) is not None
            ),
            None,
        )
    ) is None:
        return
    where = "type alias" if parser._is_root_model(model) else "field"  # noqa: SLF001
    msg = (
        f"Cannot write `{render_python_type_expr(literal)}` in the msgspec {where} "
        f"`{site_display_name(parser, model, field)}`: a msgspec union holds one str-like member, so no msgspec type "
        "holds both bytes and str values. Write values of one kind in the annotation (`x-python-type` or "
        f"`--type-overrides`), for example `{render_python_type_expr(literal_values_as_text(literal))}` or `bytes`, "
        "or generate another `--output-model-type`."
    )
    raise Error(msg)


def _msgspec_null_enum_message(model: Enum) -> str | None:
    """Describe an enum whose null member msgspec refuses, if the other members it reads are all str or all int.

    An enum of nothing but null members is described too.
    """
    if not (
        nulls := [field.name for field in model.fields if get_raw_enum_member_value(field.default) is None]
    ) or not (_msgspec_enum_kinds(model) - {"none"} in ({"str"}, {"int"}) or len(nulls) == len(model.fields)):
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


def _msgspec_enum_kinds(model: Enum) -> frozenset[str]:
    """Return the kinds of the members of an enum that msgspec reads (``enum_member_kinds``).

    msgspec skips a list or an object value, which has no hash.
    """
    return enum_member_kinds(
        (get_raw_enum_member_value(field.default) for field in model.fields), value_base=model.value_base
    ) - {"other"}


def _msgspec_mixed_enum_message(model: Enum, *, literal_under_all: bool) -> str | None:
    """Describe an enum whose values msgspec refuses: besides None, they are neither all str nor all int.

    The message recommends ``--enum-field-as-literal all`` only where that option writes the enum as a Literal
    (``literal_under_all``).

    The kinds are those of the members msgspec reads (``_msgspec_enum_kinds``); msgspec refuses an enum left without
    values.
    """
    if all(get_raw_enum_member_value(field.default) is None for field in model.fields):
        return None
    if (kinds := _msgspec_enum_kinds(model) - {"none"}) in ({"str"}, {"int"}):
        return None
    message = f"msgspec cannot decode the enum {model.class_name}: its values are neither all str nor all int."
    if not literal_under_all:
        return message
    floats = " A float value, which a msgspec Literal does not hold, is written as float." if "float" in kinds else ""
    return (
        f"{message} Pass --enum-field-as-literal all to write enums as Literal values, which msgspec decodes.{floats}"
    )


_BEYOND_MSGSPEC_INTS: Final = (
    "is beyond the 64-bit range, while msgspec decodes Enum and Literal ints only from -2**63 to 2**63 - 1"
)


def _msgspec_wide_int(values: Iterable[object]) -> int | None:
    """Return the first int of the values that msgspec refuses as an Enum or a Literal value (``int_values``).

    A bool, which is no int as a value, lies within the range.
    """
    return next(
        (value for value in values if isinstance(value, int) and int_beyond_values(MSGSPEC_OUTPUT, value)),
        None,
    )


def _msgspec_wide_int_enum_message(model: Enum) -> str | None:
    """Describe an enum holding an int that msgspec refuses (``_msgspec_wide_int``).

    The ``int`` value base of an ``IntEnum`` or an ``(int, Enum)`` (``Enum.value_base``) makes every value an int, an
    integral float such as ``1e+19`` too; a ``StrEnum`` or an enum on another value type holds no int.
    """
    if (value_base := model.value_base) not in {None, "int"}:
        return None
    values = [get_raw_enum_member_value(field.default) for field in model.fields]
    if value_base == "int":
        values = [int(value) if isinstance(value, float) and value.is_integer() else value for value in values]
    if (value := _msgspec_wide_int(values)) is None:
        return None
    return f"msgspec cannot decode the enum {model.class_name}: its value {value} {_BEYOND_MSGSPEC_INTS}."


def _alias_value_aliases(model: DataModel) -> Iterator[DataModel]:
    """Yield the type aliases that the value of a type alias names, at any nesting."""
    for data_type in model.all_data_types:
        if (reference := data_type.reference) is not None and isinstance(source := reference.source, TypeAliasBase):
            yield source


def _msgspec_recursive_alias_messages(models: Sequence[DataModel]) -> Iterator[str]:
    """Describe each cycle of type aliases whose values name each other through no Struct, in the order of the models.

    msgspec decodes a recursive type only through a Struct: it refuses such an alias in every form the output writes,
    and every type that uses it. The ``$ref`` chains that loop back without reaching a schema are
    ``reject_alias_cycles_without_schema``'s (``alias_loops``).
    """
    walk = walk_graph(
        GraphSnapshot.MSGSPEC_ALIAS_RECURSION,
        EdgeKind.ALIAS_VALUE,
        (model for model in models if isinstance(model, TypeAliasBase)),
        _model_key_of_model,
        _alias_value_aliases,
    )
    if not (cycles := walk.graph.circular_components((EdgeKind.ALIAS_VALUE,))):
        return
    loops = alias_loops(models, ())
    order = {id(model): index for index, model in enumerate(models)}

    def position(model: DataModel) -> int:
        return order.get(id(model), len(order))

    ordered = sorted(
        (sorted((walk.node_of[key] for key in cycle), key=position) for cycle in cycles),
        key=lambda cycle: position(cycle[0]),
    )
    for cycle in ordered:
        if id(cycle[0]) in loops:
            continue
        names = ", ".join(model.class_name for model in cycle)
        if len(cycle) == 1:
            yield (
                f"msgspec cannot decode the type alias {names}, or a type that uses it: its value names itself "
                "through no Struct, while msgspec decodes a recursive type only through a Struct."
            )
        else:
            yield (
                f"msgspec cannot decode the type aliases {names}, or a type that uses them: their values name each "
                "other through no Struct, while msgspec decodes a recursive type only through a Struct."
            )


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
    examples = listed_with_more(parser._decimal_default_warning_examples or (), count)  # noqa: SLF001
    values, verb = counted(count, "Decimal default value"), for_count(count, "was", "were")
    if DefaultValueType.Decimal in parser.deserialize_default_value_types:
        message = (
            f"{values} could not be deserialized and {verb} kept serialized "
            f"to keep generated modules importable: {examples}."
        )
    else:
        message = (
            f"{values} {verb} emitted as serialized data instead of Decimal: "
            f"{examples}. Generated output is unchanged. "
            "Use --deserialize-default-values decimal to enable Decimal deserialization."
        )
    parser._decimal_default_warning_count = 0  # noqa: SLF001
    parser._decimal_default_warning_examples = None  # noqa: SLF001
    warn_as_parser_base(message, DefaultValueTypeWarning, stacklevel=2)


def widened_values_warning(field: DataModelFieldBase) -> str | None:
    """Return a warning for the enum or const values a field's type is written in place of, as no Literal holds them.

    The parser keeps them on the type (``record_widened_values``), which admits other values of their types.
    """
    if not (
        widened := (
            *dict.fromkeys(
                stable_repr(value)
                for data_type in field.data_type.all_data_types
                for value in data_type.__dict__.get(WIDENED_VALUES_KEY, ())
            ),
        )
    ):
        return None
    count = len(widened)
    return field._generated_without_constraints(  # ruff: ignore[private-member-access]
        f"a Literal of the output model cannot hold the {for_count(count, 'value')} {english_list(widened, 'and')}, "
        f"so {for_count(count, 'it is', 'they are')} written as {for_count(count, 'its type', 'their types')}, "
        f"which {for_count(count, 'admits', 'admit')} other values",
        1,
    )


def warn_about_dropped_constraints(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:
    """Warn once per field whose constraints the output model leaves out on its type, and keep each warning."""
    fields: set[tuple[ModulePath, str]] = set()
    for ctx in contexts:
        for model in ctx.models:
            for field in model.fields:
                if message := " ".join(
                    filter(
                        None,
                        (
                            field.dropped_constraints_warning,
                            widened_values_warning(field),
                            tuple_unique_items_warning(parser, field),
                        ),
                    )
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
        or not (tuples := unique_items_plan(field, keeps_tuples=True).tuples)
    ):
        return None
    return field._generated_without_constraints(  # ruff: ignore[private-member-access]
        f"--use-unique-items-as-set writes a uniqueItems array as a set, which would lose the positions of "
        f"{', '.join(tuple_type.type_hint for tuple_type in tuples)}",
        len(tuples),
    )


def alias_loop_failure(data_model_type: type[DataModel]) -> str | None:
    """Return what fails on a model that reaches a type alias of a looping ``$ref`` chain in ``data_model_type`` output.

    Pydantic builds the model and msgspec decodes it; None when nothing reads its annotations.
    """
    if data_model_type._READ_BY_PYDANTIC:  # noqa: SLF001
        return "Pydantic cannot build"
    if data_model_type._READ_BY_MSGSPEC:  # noqa: SLF001
        return "msgspec cannot decode"
    return None


def _loop_error(
    model: DataModel, cycle: Sequence[DataModel], failure: str, folded: Mapping[str, tuple[tuple[str, str], ...]]
) -> Error:
    """Return the error for a model that uses a ``$ref`` chain looping back without reaching a schema.

    The chain names the definitions parsed as their ``$ref`` alone that each member took in (``folded``) after it.
    """
    members = [link for member in cycle for link in ((member.class_name, member.path), *folded.get(member.path, ()))]
    chain = " -> ".join(f"{name} ({redact_reference(path)})" for name, path in (*members, members[0]))
    return Error(
        f"{model.class_name} uses a $ref chain that loops back without reaching a schema, which {failure}: {chain}"
    )


def _evaluated_data_types(model: DataModel, *, evaluates_annotations: bool) -> Iterator[DataType]:
    """Yield the data types that the statements defining a model evaluate as the module runs.

    Where ``definition_reads`` reads the fields of the model (row ``legacy-evaluated-without-root-model``), they are
    every type of the model, its bases included. Only Pydantic and msgspec output reaches this check
    (``alias_loop_failure``), and there a definition reads the model's own fields or none: the subset a functional
    TypedDict evaluates never comes here.
    """
    if (
        definition_reads(model, row="legacy-evaluated-without-root-model", evaluates_annotations=evaluates_annotations)
        is not None
    ):
        yield from model.all_data_types


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
    alias or, with ``evaluates_annotations``, in a class: it then fails to import. Output that nothing builds from its
    annotations has no such user left, as ``type_users_of_looping_ref_chains_by_any`` typed them ``Any``.
    """
    self_named_models = parser._self_named_root_models  # noqa: SLF001
    self_named = {id(model): (model,) for model in self_named_models}
    models = _context_models(contexts)
    if (failure := alias_loop_failure(parser.data_model_type)) is not None and (
        cycles := alias_loops(models, self_named_models)
    ):
        for model in models:
            if isinstance(model, TypeAliasBase):
                continue
            pending = [*model.all_data_types]
            seen: set[int] = set()
            while pending:
                if (reference := pending.pop().reference) is None or id(source := reference.source) in seen:
                    continue
                if (cycle := cycles.get(id(source))) is not None:
                    raise _loop_error(model, cycle, failure, parser._folded_ref_chains)  # noqa: SLF001
                seen.add(id(source))
                if isinstance(source, TypeAliasBase):
                    pending.extend(source.all_data_types)
    if not self_named:
        return
    for model in models:
        for data_type in _evaluated_data_types(model, evaluates_annotations=evaluates_annotations):
            if (reference := data_type.reference) is not None and (cycle := self_named.get(id(reference.source))):
                raise _loop_error(
                    model,
                    cycle,
                    "Python cannot evaluate as the module runs",
                    parser._folded_ref_chains,  # noqa: SLF001
                )


_PYDANTIC_ALIAS_ARGUMENTS: Final[Mapping[str, str]] = {
    "alias": "alias",
    "validation_alias": "validation alias",
    "serialization_alias": "serialization alias",
}
"""What each alias argument of ``Field()`` is, as the message names it."""


def _pydantic_read_aliases(field: DataModelFieldBase) -> Iterator[tuple[str, str]]:
    """Yield each alias the field's ``Field()`` call writes, which Pydantic reads, with what it is.

    They are the arguments of the frozen render plan: an alias ``--use-serialization-alias`` writes as the serialization
    alias, or replaces by the one ``--serialization-aliases`` gives, is no alias Pydantic reads. ``validation_alias``
    writes the ``AliasChoices`` of the field's validation aliases.
    """
    plan = field._render_plan()  # noqa: SLF001
    for call in (plan.call, plan.annotated_call):
        for argument in () if call is None else call.kwargs:
            if (kind := _PYDANTIC_ALIAS_ARGUMENTS.get(argument.name)) is None:
                continue
            if argument.name == "validation_alias":
                yield from ((kind, alias) for alias in field.validation_aliases or ())
            else:
                yield kind, cast("str", argument.value)


def _lone_surrogate_held(model: DataModel, field: DataModelFieldBase) -> tuple[str, str] | None:
    """Return the first text of a field that Pydantic reads as UTF-8 and that holds a lone surrogate, and its location.

    The texts are the aliases the field's ``Field()`` call writes (``_pydantic_read_aliases``), the str values of its
    ``Literal`` types and the str values of the enums its types validate as classes, read through the type aliases they
    name; a ``Literal`` of enum members holds the members, not their values. The text is written as the message writes
    it (its ``repr``, after what it is), and the location as the message names it: that of the type that holds the
    value, else of the field, else of the model; an enum's own.
    """
    origin = None if (built := field_origin_of(field)) is None else built.location
    if held := next(
        ((kind, alias) for kind, alias in _pydantic_read_aliases(field) if holds_lone_surrogate(alias)), None
    ):
        return f"its {held[0]} {stable_repr(held[1])}", named_schema_location(origin, model.reference)
    pending = [*field.data_type.all_data_types]
    seen: set[int] = set()
    while pending:
        data_type = pending.pop()
        if value := next((v for v in data_type.literals if isinstance(v, str) and holds_lone_surrogate(v)), None):
            location = type_origin_of(data_type) or origin
            return f"the Literal value {stable_repr(value)}", named_schema_location(location, model.reference)
        if data_type.enum_member_literals or (reference := data_type.reference) is None:
            continue
        if id(source := reference.source) in seen:
            continue
        seen.add(id(source))
        if isinstance(source, Enum):
            values = (get_raw_enum_member_value(member.default) for member in source.fields)
            if value := next((v for v in values if isinstance(v, str) and holds_lone_surrogate(v)), None):
                return (
                    f"the value {stable_repr(value)} of the enum {source.class_name}",
                    named_schema_location(None, source.reference),
                )
        elif isinstance(source, TypeAliasBase):
            pending.extend(source.all_data_types)
    return None


def reject_lone_surrogates_pydantic_reads(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:
    """Refuse output whose Pydantic models hold a lone surrogate in a text that Pydantic reads as UTF-8.

    pydantic-core cannot build the validator or serializer of such a text, so the module would fail to import: an
    alias of a field (``AliasChoices`` too), a str value of a ``Literal``, and a str value of an enum that a type
    validates as a class (``_lone_surrogate_held``). The module writes each text as its escape, which keeps it; an enum
    or a type alias that no model reaches, and a text Pydantic stores as it is (a description, a default), keep their
    output. The error names the field and the schema location of the text.
    """
    for model in _context_models(contexts):
        if not model._READ_BY_PYDANTIC:  # noqa: SLF001
            continue
        for field in model.fields:
            if not field.is_class_var and (held := _lone_surrogate_held(model, field)) is not None:
                text, location = held
                msg = (
                    f"Pydantic cannot build {site_display_name(parser, model, field)}: {text} at {location} holds a "
                    "lone surrogate, which no Pydantic validator holds, so the module would fail to import. Remove the "
                    "lone surrogate from the schema or use another --output-model-type."
                )
                raise Error(msg)


def record_pydantic_floor(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:
    """Record the Pydantic release that the emitted models need above the explicit target, for the generation.

    Every fact the constructs read is final here, after the last writer before rendering. A model or field owns
    each construct, named with its module when the parse writes more than one. Output that Pydantic does not
    build has no floor to state.
    """
    if (record := _PYDANTIC_FLOORS.get()) is None or parser.target_pydantic_version is None:
        return
    if not parser.data_model_type._READ_BY_PYDANTIC:  # noqa: SLF001
        record.record(parser, None)
        return
    used: list[tuple[PydanticConstruct, str]] = []
    for ctx in contexts:
        prefix = f"{module_name}." if len(contexts) > 1 and (module_name := dotted_module(ctx.module)) else ""
        for model in ctx.models:
            owner = f"{prefix}{model.class_name}"
            used.extend(
                (construct, owner if field_name is None else f"{owner}.{field_name}")
                for construct, field_name in model._pydantic_constructs()  # noqa: SLF001
            )
    record.record(parser, pydantic_floor(parser.target_pydantic_version, used))


def warn_about_variant_discriminator_tags(parser: FinishHost) -> None:
    """Warn once per variant whose generated tag differs from a tag a union maps it to."""
    for recorded in parser._tag_plan.first_tags.values():  # noqa: SLF001
        for message in recorded.warning_messages():
            warn_as_parser_base(message, UserWarning, stacklevel=2)


def warn_about_msgspec_decoding(parser: FinishHost, contexts: Sequence[ModuleContextView]) -> None:  # noqa: PLR0912
    """Warn once per union and enum of msgspec output that msgspec refuses to decode.

    A field whose ``Literal``, or union of ``Literal`` members, holds both bytes and str values, which no msgspec type
    holds, is refused instead (``_refuse_msgspec_bytes_and_str_literal``).
    """
    if not parser.data_model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR:
        return
    from datamodel_code_generator.model.union import _DataTypeUnionBase  # noqa: PLC0415

    models = _context_models(contexts)
    named = {model.class_name: model for model in models}
    literal_enum_ids = parser._literal_under_all_enum_reference_ids  # noqa: SLF001
    for model in models:
        if isinstance(model, Enum):
            for message in filter(
                None,
                (
                    None if parser.use_msgspec_nullable_enum_alias else _msgspec_null_enum_message(model),
                    _msgspec_mixed_enum_message(
                        model, literal_under_all=model_key_of(model.reference) in literal_enum_ids
                    ),
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
            _refuse_msgspec_bytes_and_str_literal(parser, model, field)
            member = _literal_member(field.data_type)
            discriminated = bool(field.extras.get("discriminator"))
            nested = [*field.data_type.all_data_types][::-1]
            refusal = next(
                filter(operator.itemgetter(0), map(_msgspec_refused_union_structs, nested)), _NO_MSGSPEC_REFUSAL
            )
            refused = bool(refusal[0]) and (
                refusal[1] is not None or not (discriminated and parser.use_msgspec_tagged_unions)
            )
            clashes = _first_union_clashes(
                chain.from_iterable(_msgspec_refused_union_enums(data_type, literal_enum_ids) for data_type in nested)
            )
            kind_clashes = _first_union_clashes(
                chain.from_iterable(_msgspec_refused_union_kinds(data_type, literal_enum_ids) for data_type in nested)
            )
            wide = _msgspec_wide_int(chain.from_iterable(data_type.literals for data_type in nested))
            if member is None and not refused and not clashes and not kind_clashes and wide is None:
                continue
            name = site_display_name(parser, model, field)
            if member is not None:
                warn_as_parser_base(_msgspec_literal_member_message(name, member), UserWarning, stacklevel=2)
            if refused:
                warn_as_parser_base(
                    _msgspec_union_message(name, refusal, discriminated=discriminated), UserWarning, stacklevel=2
                )
            for clash in clashes.values():
                warn_as_parser_base(_msgspec_enum_union_message(name, clash), UserWarning, stacklevel=2)
            for kind_clash in kind_clashes.values():
                warn_as_parser_base(_msgspec_kind_union_message(name, kind_clash), UserWarning, stacklevel=2)
            if wide is not None:
                warn_as_parser_base(
                    f"msgspec cannot decode {name}: its Literal value {wide} {_BEYOND_MSGSPEC_INTS}.",
                    UserWarning,
                    stacklevel=2,
                )
    for message in _msgspec_recursive_alias_messages(models):
        warn_as_parser_base(message, UserWarning, stacklevel=2)
    if message := _msgspec_backport_alias_message(models):
        warn_as_parser_base(message, UserWarning, stacklevel=2)


def _msgspec_backport_alias_message(models: Iterable[DataModel]) -> str | None:
    """Describe the type aliases written as ``typing_extensions.TypeAliasType``, which msgspec may not decode.

    msgspec decodes only ``typing.TypeAliasType``: Python 3.10 and 3.11 have none, and typing_extensions 4.13 and later
    write a class of their own before Python 3.15. A target of Python 3.12 or later writes ``type`` statements instead.
    """
    if not (names := [model.class_name for model in models if IMPORT_TYPE_ALIAS_TYPE in model.imports]):
        return None
    aliases = (
        f"type alias {names[0]}, or a type that uses it: it is"
        if len(names) == 1
        else f"type aliases {', '.join(names)}, or a type that uses them: they are"
    )
    return (
        f"msgspec may not decode the {aliases} written as typing_extensions.TypeAliasType, while msgspec decodes only "
        "typing.TypeAliasType, which Python 3.10 and 3.11 lack and which typing_extensions 4.13 and later replace with "
        "a class of their own before Python 3.15; --target-python-version 3.12 writes type statements instead, which "
        "msgspec decodes."
    )


def _names_written_as_text(models: Iterable[DataModel]) -> set[str]:
    """Return the names that the types of the models write as text: those of the types that name no model.

    A dotted name binds its first name where it is written (``ipaddress`` of ``ipaddress.IPv4Address``).
    """
    from datamodel_code_generator._python_type_annotation import iter_python_type_expr_head_names  # noqa: PLC0415

    names: set[str] = set()
    for model in models:
        for data_type in _named_data_types(model):
            if data_type.reference is not None:
                continue
            if data_type.python_type:
                names.update(iter_python_type_expr_head_names(data_type.python_type.expression))
            elif data_type.type:
                names.add(data_type.type.partition(".")[0])
    return names


def _imported_names(models: Sequence[DataModel], imports: Imports, shared_names: set[str]) -> set[str]:
    """Return the names the imports of a module bind that a class of the module may not take.

    The imports every module writes (``shared_names``) count only where a type of the module writes the name as text.
    """
    imported_names = {
        imports.alias[from_][i] if i in imports.alias[from_] and i != imports.alias[from_][i] else i
        for from_, import_ in imports.items()
        for i in import_
    }
    if shared_names and (shadowed := shared_names.intersection(model.class_name for model in models)):
        imported_names.update(shadowed.intersection(_names_written_as_text(models)))
    return imported_names


def import_names_class(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> bool:
    """Tell whether a class of a module takes a name the module imports, which ``change_imported_model_name`` reads."""
    shared_names = shared_import_bindings(parser.imports)
    return any(
        not _imported_names(ctx.models, ctx.imports, shared_names).isdisjoint(model.class_name for model in ctx.models)
        for ctx in state.contexts
    )


def bind_module_field_names(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Bind the field names of each module that its imports shadow, before the renames read the module imports."""
    for ctx in state.contexts:
        _bind_module_field_names(
            ctx.models,
            ctx.imports,
            parser.imports,
            parser._import_ledger,  # noqa: SLF001
            parser.target_python_version.version_key,
        )


def change_imported_model_name(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Rename the local models that an import of their module shadows; remember whether any was renamed.

    The imports every module writes (``--additional-imports``) shadow a model that a type of the module writes by
    their name as text (``x-python-type``); one that no type of the module writes keeps the model's name.
    """
    shared_names = shared_import_bindings(parser.imports)
    for ctx in state.contexts:
        models, imports = ctx.models, ctx.imports
        imported_names = _imported_names(models, imports, shared_names)
        for model in models:
            if model.class_name not in imported_names:  # pragma: no cover
                continue
            if model.class_name == IMPORT_LITERAL.import_ and alias_reused_variant_literal(parser, models, imports):
                continue

            ctx.scoped_model_resolver.exclude_names.update(shared_names)
            parser._graph_builder.rename_model(  # noqa: SLF001
                model,
                reference_name=parser._import_ledger.add_name(  # noqa: SLF001
                    imports,
                    ctx.scoped_model_resolver,
                    get_special_path("imported_name", model.path.split("/")),
                    model.reference.name,
                    class_name=True,
                    unique=True,
                ).name,
                scheme="imported-name",
            )
            state.renamed_models = True


def alias_reused_variant_literal(parser: ImportsHost[Any], models: list[DataModel], imports: Imports) -> bool:
    """Import ``typing.Literal`` under an alias when only the tags of unions with reused variants need it.

    Such unions were left untagged before, so a model named ``Literal`` kept its name, which importers rely on.
    The alias takes no name that the module binds or its class decorators name, additional imports included.
    """
    tag_fields = parser._tag_plan.reused_tag_fields  # noqa: SLF001
    fields = [field for model in models for field in model.fields]
    annotated = [field for field in fields if tag_fields.get(id(field)) is field]
    bindings = dict(_import_bindings(imports))
    if (
        not annotated
        or [key for key, binding in bindings.items() if binding == IMPORT_LITERAL.import_]
        != [_import_key(IMPORT_LITERAL)]
        or any(IMPORT_LITERAL in field.imports for field in fields if tag_fields.get(id(field)) is not field)
    ):
        return False
    reserved_names = _alias_reserved_names(
        models,
        imports,
        parser.imports,
        also=(
            name
            for model in models
            for decorator in model.decorators
            for name in _string_or_identifier_pattern(decorator, parser.target_python_version).findall(decorator)
            if name
        ),
    )
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
    with cast("RenderReady", RENDER_READY.get()).import_view():
        for unused_model, module, models, written in unused_model_lists(parser, state):
            if unused_model in models:  # pragma: no branch
                parser._import_ledger.remove(  # noqa: SLF001
                    state.module_to_import[module],
                    state.model_imports.get(unused_model, unused_model.imports),
                    ImportProducer("unused-models", unused_model),
                )
                models.remove(unused_model)
                if written:
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


def _rebind_import(  # noqa: PLR0913
    parser: ImportsHost[Any],
    ctx: ModuleContextView,
    binding: str,
    reference: Reference,
    import_key: _ImportKey,
    *,
    held_by: Literal["builtin", "submodule"],
    producer: ImportProducer,
) -> str:
    """Give the import ``import_key``, which loses its name ``binding``, the name the module's resolver gives next.

    The import releases ``binding`` to what keeps it (``held_by``: the builtin, a package's submodule), then takes the
    name the resolver gives ``reference`` (``list_1``) as its alias, which is returned.
    """
    imports, resolver, ledger = ctx.imports, ctx.scoped_model_resolver, parser._import_ledger  # noqa: SLF001
    ledger.release_names(imports, resolver, {binding})
    ledger.add_name(imports, resolver, get_special_path(held_by, [binding]), binding)
    alias = ledger.add_name(imports, resolver, [reference.path], reference.original_name).name
    from_, import_ = import_key
    ledger.apply_alias(imports, Import(from_=from_, import_=import_, alias=alias), producer)
    return alias


def _module_binding(expression: str | None) -> str | None:
    """Return the module binding of an expression ``<binding>.<Class>``, or None for another expression."""
    return expression.partition(".")[0] if expression and "." in expression else None


def _written_reference(data_type: DataType) -> tuple[Reference, str | None] | None:
    """Return the model a type names and the expression it writes for it, or None for a type that names none.

    A reference names its model by its alias (``annotations.Item``), and an enum member literal its enum by the class
    its members are written with (``annotations.Kind``), unless the literal is written as an alias.
    """
    if (reference := data_type.reference) is not None:
        return reference, data_type.alias
    if (
        (reference := data_type._enum_member_literal_reference) is None  # noqa: SLF001
        or not (literals := data_type.enum_member_literals)
        or data_type.alias
    ):
        return None
    return reference, literals[0][0]


def _follow_renamed_enum_references(
    parser: ImportsHost[Any], models: Iterable[DataModel], renamed: Iterable[DataType]
) -> None:
    """Write the enum members in the defaults of ``models`` by the new expression of their renamed enum types.

    ``set_default_enum_member`` runs before the imports are named anew and writes a member by the expression its type
    names the enum by then, so a default would keep naming the import that was renamed.
    """
    aliases = {
        id(source): written[1]
        for data_type in renamed
        if (written := _written_reference(data_type)) is not None and isinstance(source := written[0].source, Enum)
    }
    if not aliases:
        return
    for model in models:
        for field in model.fields:
            for value in field.default if isinstance(field.default, list) else [field.default]:
                if isinstance(value, Member) and id(value.enum) in aliases:
                    parser._graph_builder.name_enum_member(field, value, aliases[id(value.enum)])  # noqa: SLF001


def _alias_builtin_shadowing_module_imports(parser: ImportsHost[Any], ctx: ModuleContextView) -> None:
    """Alias the module imports named like a builtin that the module also writes bare, e.g. ``list[str]``.

    ``from . import list``, which names the models of the module ``list`` (``list.Pop``), hides the builtin from the
    whole module. Such an import takes the name the module's resolver gives it once the builtin holds its own name
    (``list_1``), as module imports whose names clash with other names do, and every ``list.X`` the module writes
    follows. A name is a builtin by the static table of the target, and the module writes it bare when a rendered model
    names it other than as the module of an attribute, a store or a name a function of a custom template binds included
    (row ``legacy-flat-bare-loads``). The class of an enum member literal is written before
    (``import_enum_member_literal_classes``), so a binding it names keeps its name. A package's own submodule keeps its
    name too: importing it binds the name in the package whatever the import is named. An import of the builtin itself
    (``--type-overrides`` with ``builtins.list``) is the name's own binding, not the module import that hides it.
    """
    references = [
        data_type for model in ctx.models for data_type in _named_data_types(model) if data_type.reference is not None
    ]
    target = parser.target_python_version.version_key
    if not (
        bindings := {
            binding
            for data_type in references
            if (binding := _module_binding(data_type.alias)) is not None and is_builtin_type_name(binding, target)
        }
    ):
        return
    bindings.difference_update(
        _module_binding(enum_class)
        for model in ctx.models
        for data_type in model.all_data_types
        for enum_class, _ in data_type.enum_member_literals
    )
    imported = {binding: key for key, binding in _import_bindings(ctx.imports) if key[0] != "builtins"}
    resolver = ctx.scoped_model_resolver
    held = {reference.name: reference for reference in resolver.references.values()}
    if not (
        shadowing := {
            binding
            for binding in bindings
            if binding in imported
            and binding in held
            and not (
                _written_package(ctx, treat_dot_as_module=parser.treat_dot_as_module) is not None
                and imported[binding][0] == "."
            )
        }
    ):
        return
    resolver.exclude_names.update(shared_import_bindings(parser.imports))
    written = {
        name
        for model in ctx.models
        for name in _bare_loads_of(model.render(), shadowing, rule="legacy-flat-bare-loads")
    }
    aliases = {
        binding: _rebind_import(
            parser,
            ctx,
            binding,
            held[binding],
            imported[binding],
            held_by="builtin",
            producer=_BUILTIN_MODULE_BINDINGS,
        )
        for binding in sorted(written)
    }
    renamed: list[DataType] = []
    for data_type in references:
        if (binding := _module_binding(data_type.alias)) in aliases:
            parser._graph_builder.name_reference(  # noqa: SLF001
                data_type, f"{aliases[binding]}{cast('str', data_type.alias)[len(binding) :]}"
            )
            renamed.append(data_type)
    _follow_renamed_enum_references(parser, ctx.models, renamed)


def alias_builtin_shadowing_module_imports(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Alias the module imports named like a builtin that their module also writes bare."""
    for ctx in state.contexts:
        _alias_builtin_shadowing_module_imports(parser, ctx)


def _package_submodule_names(
    contexts: Iterable[ModuleContextView],
    forwarder_modules: Collection[ModulePath],
    *,
    treat_dot_as_module: bool | None,
) -> dict[ModulePath, set[str]]:
    """Return the names of the written submodules of each package, by the package's path in the written layout.

    Importing a submodule, from any module, binds its name in its package.
    """
    names: dict[ModulePath, set[str]] = {}
    for ctx in contexts:
        if not (ctx.models or ctx.is_init or ctx.module_key in forwarder_modules):
            continue
        packages = module_parts(written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))
        for depth in range(len(packages)):
            names.setdefault(packages[:depth], set()).add(packages[depth])
    return names


def _alias_imports_hidden_by_submodules(
    parser: ImportsHost[Any], ctx: ModuleContextView, submodule_names: Collection[str]
) -> None:
    """Give the imports of models a new name where a package's submodules bind the name they import them by.

    ``pkg/Warning.py`` binds ``Warning`` in ``pkg/__init__.py`` once anything imports it, whatever the import is named,
    so a model the package imports by that name (``from ..other import Warning``) or a module it imports under it
    would name the submodule. The submodule keeps the name, as the builtin does for a module import named like it
    (``_alias_builtin_shadowing_module_imports``), and the import takes the name the module's resolver gives it next
    (``Warning_1``), which every use of the model follows. An import of the submodule itself keeps its name, and so
    does that of a model named like its submodule (``from .A import A``), which binds the name once the submodule is
    loaded.
    """
    imports, resolver = ctx.imports, ctx.scoped_model_resolver
    held = {reference.name: reference for reference in resolver.references.values()}
    users: dict[str, list[tuple[DataType, _ImportKey]]] = {}
    for model in ctx.models:
        for data_type in _named_data_types(model):
            if (
                (reference := data_type.reference) is None
                or (import_ := imports.reference_paths.get(reference.path)) is None
                or (binding := imports.get_effective_name(import_.from_, import_.import_)) not in submodule_names
                or binding not in held
                or _import_key(import_) in {(".", binding), (f".{binding}", binding)}
            ):
                continue
            users.setdefault(binding, []).append((data_type, _import_key(import_)))
    renamed: list[DataType] = []
    if users:
        resolver.exclude_names.update(shared_import_bindings(parser.imports))
    for binding, binding_users in sorted(users.items()):
        alias = _rebind_import(
            parser,
            ctx,
            binding,
            held[binding],
            binding_users[0][1],
            held_by="submodule",
            producer=_SUBMODULE_IMPORT_BINDINGS,
        )
        for data_type, _ in binding_users:
            parser._graph_builder.name_reference(  # noqa: SLF001
                data_type, alias + (data_type.alias or binding)[len(binding) :]
            )
            renamed.append(data_type)
    _follow_renamed_enum_references(parser, ctx.models, renamed)


def _alias_builtins_hidden_by_submodules(
    parser: ImportsHost[Any], ctx: ModuleContextView, submodule_names: Collection[str]
) -> None:
    """Import the builtins that a package's submodules hide, and that the package writes bare, under aliases.

    ``app/list.py`` binds ``list`` in ``app/__init__.py`` once anything imports it, whatever the import is named, so
    ``list[str]`` there would subscript the module. The package imports the builtin as ``list_aliased``, the alias
    form of field-name bindings, and every bare ``list`` its models and module code write follows; ``list.Pop``
    names the submodule and keeps its name. A builtin is a name of the static table of the target, and the output
    writes a bare load of one for nothing but the builtin, unless the package defines a class of that name
    (``Warning`` beside ``app/Warning.py``), which the load names.
    """
    target = parser.target_python_version.version_key
    class_names = {model.class_name for model in ctx.models}
    if not (hidden := {name for name in submodule_names if is_builtin_type_name(name, target)}.difference(class_names)):
        return
    loads = {model: found for model in ctx.models if (found := _bare_loads_of(model.render(), hidden))}
    module_code = parser.data_model_type.render_module_code(ctx.models)
    if not (written := {name for found in loads.values() for name in found} | _bare_loads_of(module_code, hidden)):
        return
    reserved = _alias_reserved_names(ctx.models, ctx.imports, parser.imports, also=submodule_names)
    bindings = {name: _reserve_import_alias(name, reserved) for name in sorted(written)}
    parser._import_ledger.append(  # noqa: SLF001
        ctx.imports,
        [Import(from_="builtins", import_=name, alias=alias) for name, alias in bindings.items()],
        _BUILTIN_SUBMODULE_BINDINGS,
    )
    for model, found in loads.items():
        parser._graph_builder.bind_builtin_names(model, {name: bindings[name] for name in sorted(found)})  # noqa: SLF001


def alias_builtins_hidden_by_submodules(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Import the builtins that each package's submodules hide, and that the package writes bare, under aliases."""
    submodule_names = _package_submodule_names(
        state.contexts, state.forwarder_modules, treat_dot_as_module=parser.treat_dot_as_module
    )
    for ctx in state.contexts:
        if (package := _written_package(ctx, treat_dot_as_module=parser.treat_dot_as_module)) is not None and (
            names := submodule_names.get(package)
        ):
            _alias_imports_hidden_by_submodules(parser, ctx, names)
            _alias_builtins_hidden_by_submodules(parser, ctx, names)


def builtin_name_bindings(ledger: ModuleImportLedger, imports: Imports) -> dict[str, str]:
    """Return the aliases that a module's bare names take where a submodule of its package hides them.

    ``alias_builtins_hidden_by_submodules`` imported the builtins under them, and ``import_models_of_hidden_submodules``
    aliased the imports (``_alias_imports_hidden_by_loaded_submodules``); the models of the module are bound to them
    as they are written (``bind_builtin_names``), and this is how the module code reads them.
    """
    return {
        import_.import_: cast("str", import_.alias)
        for entry in ledger.entries(imports)
        for import_ in (
            entry.operation.imports
            if entry.producer == _BUILTIN_SUBMODULE_BINDINGS and isinstance(entry.operation, Added)
            else (entry.operation.import_,)
            if entry.producer == _HIDDEN_SUBMODULE_IMPORT_BINDINGS and isinstance(entry.operation, Aliased)
            else ()
        )
    }


def submodules_hidden_by_packages(
    parser: ImportsHost[Any],
    contexts: Sequence[ModuleContextView],
    naming: ModuleNamesView,
    config: ParseConfigView,
) -> dict[ModulePath, frozenset[tuple[str, ...]]]:
    """Return, by the file of each module, the modules it imports models from that their package hides from it.

    ``from pkg import annotations`` gives what ``pkg/__init__.py`` binds as ``annotations`` once it binds the name
    itself, not the submodule ``pkg.annotations``, which the import then never loads. The ``__init__.py`` of a package
    that writes models binds the names of its ``__future__`` imports (``annotations``) before every other line, its
    shared ones and on a target with native deferred annotations its own (``_writes_module_future_annotations``):
    those hide the submodule from every module. It defines its classes after its imports, so a class hides the
    submodule from every module but the ``__init__.py`` itself, and only where the ``__init__.py`` imports from the
    submodule, for a type or for the class of an enum member literal: the submodule is then loaded before the class
    takes the name, and no later import of it replaces the class. A module names the hidden modules by their module
    names, as ``reference_import_target`` reads them.
    """
    treat_dot_as_module, module_names = parser.treat_dot_as_module, naming.model_path_to_module_name

    def named_modules(models: Iterable[DataModel], *, literals: bool) -> set[tuple[str, ...]]:
        return {
            tuple(_get_data_type_target_full_name(data_type, reference, module_names).split(".")[:-1])
            for model in models
            for data_type in (model.all_data_types if literals else _named_data_types(model))
            if (reference := data_type._enum_member_literal_reference if literals else data_type.reference)  # noqa: SLF001
            is not None
        }

    shared_future = {binding for (from_, _), binding in _import_bindings(parser.imports) if from_ == "__future__"}
    inits = {
        package: ctx
        for ctx in contexts
        if ctx.models and (package := _written_package(ctx, treat_dot_as_module=treat_dot_as_module)) is not None
    }
    hidden: dict[ModulePath, tuple[set[tuple[str, ...]], set[tuple[str, ...]]]] = {}
    for ctx in contexts:
        parts = module_parts(written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))
        if not (ctx.models and parts and (init := inits.get(package := parts[:-1])) is not None):
            continue
        name = parts[-1]
        modules = {tuple(_get_model_module_name(model, module_names).split(".")) for model in ctx.models}
        if name in shared_future or (
            name == IMPORT_ANNOTATIONS.import_ and parser._writes_module_future_annotations(init.models, config)  # noqa: SLF001
        ):
            for found in hidden.setdefault(package, (set(), set())):
                found.update(modules)
        elif any(model.class_name == name for model in init.models) and any(
            is_within(named, module)
            for named in named_modules(init.models, literals=False) | named_modules(init.models, literals=True)
            for module in modules
        ):
            hidden.setdefault(package, (set(), set()))[1].update(modules)
    if not hidden:
        return {}
    result: dict[ModulePath, frozenset[tuple[str, ...]]] = {}
    for ctx in contexts:
        package = _written_package(ctx, treat_dot_as_module=treat_dot_as_module)
        modules = {
            module
            for hidden_package, (from_init, from_others) in hidden.items()
            for module in (from_init if hidden_package == package else from_others)
        }
        if modules:
            result[ctx.module] = frozenset(modules)
    return result


def modules_hidden_by_imports(
    imports: Imports, module_imports: Iterable[tuple[tuple[str, ...], str]]
) -> frozenset[tuple[str, ...]]:
    """Return the modules of ``module_imports`` whose binding an import of ``imports`` that names no model binds too.

    ``from . import BaseModel``, which names the models of the module ``BaseModel`` (``BaseModel.Thing``), and
    ``from pydantic import BaseModel`` bind one name, so the later of the two names what the module writes for both.
    ``module_imports`` holds each module that the module imports by a binding, by its module name, with that binding.
    The imports of models never share a name, as the module's resolver names them all.
    """
    models = {_import_key(import_) for import_ in imports.reference_paths.values()}
    bound = {binding for key, binding in _import_bindings(imports) if key not in models}
    return frozenset(module for module, binding in module_imports if binding in bound)


def _submodules_hidden_by_imports(
    contexts: Iterable[ModuleContextView], module_names: Mapping[str, str]
) -> dict[ModulePath, frozenset[tuple[str, ...]]]:
    """Return, by the file of each module, the modules it imports models from by a name another of its imports binds.

    A module named like an import that every module shares is imported from at once (``reference_import_target``),
    but the other imports of a module are final only now (``modules_hidden_by_imports``).
    """
    result: dict[ModulePath, frozenset[tuple[str, ...]]] = {}
    for ctx in contexts:
        imports = ctx.imports
        if modules := modules_hidden_by_imports(
            imports,
            (
                (tuple(_get_data_type_target_full_name(data_type, reference, module_names).split(".")[:-1]), binding)
                for model in ctx.models
                for data_type in _named_data_types(model)
                if (written := _written_reference(data_type)) is not None
                and (stale := imports.reference_paths.get((reference := written[0]).path)) is not None
                and (binding := _module_binding(written[1])) == stale.binding_name
            ),
        ):
            result[ctx.module] = modules
    return result


def _loaded_modules(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> set[str]:
    """Return the dotted names of the modules that the written modules import or import from (``module_import_edges``).

    Under ``--all-exports-scope`` each written module with models counts too: an ``__init__.py`` imports the modules
    it exports from.
    """
    treat_dot_as_module = parser.treat_dot_as_module
    edges = module_import_edges(state.contexts, treat_dot_as_module=treat_dot_as_module)
    loaded = {target for targets in edges.values() for target in targets}
    if state.config.all_exports_scope is not None:
        loaded.update(
            dotted_module(written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))
            for ctx in state.contexts
            if ctx.models
        )
    return loaded


def _alias_imports_hidden_by_loaded_submodules(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Import under an alias what a package's ``__init__.py`` imports by the name of a submodule the output loads.

    Loading ``pkg/Any.py`` binds ``Any`` in ``pkg/__init__.py``, over what its ``from typing import Any`` bound, so
    the classes of the ``__init__.py`` that name ``Any`` once it is loaded name the module, and before that
    ``from . import Any`` gives the import, not the module. Where a written module imports the submodule or from it
    (``_loaded_modules``), the submodule keeps the name and the import takes the alias form of field-name bindings
    (``Any_aliased``), which every bare ``Any`` the models write follows, as the builtins a submodule hides do
    (``_alias_builtins_hidden_by_submodules``). An import of another module of the output is a model's, which
    ``_alias_imports_hidden_by_submodules`` names, a ``__future__`` import binds no name the code reads, and a module
    import (``import x``) is named as the module of an attribute, which a bare load does not follow.
    """
    treat_dot_as_module = parser.treat_dot_as_module
    submodule_names = _package_submodule_names(
        state.contexts, state.forwarder_modules, treat_dot_as_module=treat_dot_as_module
    )
    loaded: set[str] | None = None
    for ctx in state.contexts:
        if not (
            ctx.models
            and (package := _written_package(ctx, treat_dot_as_module=treat_dot_as_module)) is not None
            and (names := submodule_names.get(package))
            and (
                imported := {
                    binding: key
                    for key, binding in _import_bindings(ctx.imports)
                    if binding in names and key[0] and key[0] != "__future__" and not key[0].startswith(".")
                }
            )
        ):
            continue
        loaded = _loaded_modules(parser, state) if loaded is None else loaded
        package_name = ".".join(package)
        if not (
            hidden := sorted(
                binding
                for binding in imported
                if any(is_within_name(module, dotted_name(package_name, binding)) for module in loaded)
            )
        ):
            continue
        reserved = _alias_reserved_names(ctx.models, ctx.imports, parser.imports, also=names)
        bindings = {binding: _reserve_import_alias(binding, reserved) for binding in hidden}
        for binding, alias in bindings.items():
            from_, name = imported[binding]
            parser._import_ledger.apply_alias(  # noqa: SLF001
                ctx.imports,
                Import(from_=from_, import_=_stored_import(name)[0], alias=alias),
                _HIDDEN_SUBMODULE_IMPORT_BINDINGS,
            )
        for model in ctx.models:
            parser._graph_builder.bind_builtin_names(model, {**(model._builtin_name_bindings or {}), **bindings})  # noqa: SLF001


def import_models_of_hidden_submodules(parser: ImportsHost[Any], state: _FinalizeState[Any]) -> None:
    """Import the models of a submodule from the submodule where its package or another import hides it from the module.

    ``from . import annotations``, which names the models of the module ``annotations`` (``annotations.Item``), gives
    the ``__future__`` feature where the package's ``__init__.py`` writes ``from __future__ import annotations``, and a
    class where it defines one of that name (``submodules_hidden_by_packages``), and ``from pydantic import BaseModel``
    where the module imports ``from . import BaseModel`` too (``modules_hidden_by_imports``). Such a model is imported
    from its module (``from .annotations import Item``), as ``--use-exact-imports`` imports it, under the name the
    module's resolver gives it once the module import released its name, and every use of the model follows, the class
    of an enum member literal (``Literal[annotations.Kind.book]``, ``_written_reference``) and the enum members that
    ``set_default_enum_member`` wrote into defaults included (``_follow_renamed_enum_references``). A literal took the
    place of a type that named its enum, whose import of the module stays counted, so a literal's enum drops every count
    of its import of the module (``_held_count``). The name keeps
    off every name the module's imports bind already: the resolver holds those that no model import took through it,
    such as ``collections.Counter`` beside a model ``Counter``, and the shared ones are kept off as for a module named
    like a shared import (``ReferenceImportTarget.reserve_name``). The use keeps an expression, the class name where the
    import binds it, as the field render plans froze it written.
    """
    _alias_imports_hidden_by_loaded_submodules(parser, state)
    by_packages = submodules_hidden_by_packages(parser, state.contexts, state, state.config)
    by_imports = _submodules_hidden_by_imports(state.contexts, state.model_path_to_module_name)
    if not (
        hidden := {
            module: by_packages.get(module, frozenset()) | by_imports.get(module, frozenset())
            for module in by_packages.keys() | by_imports.keys()
        }
    ):
        return
    ledger, module_names = parser._import_ledger, state.model_path_to_module_name  # noqa: SLF001
    for ctx in state.contexts:
        if not (modules := hidden.get(ctx.module)):
            continue
        imports = ctx.imports
        users = [
            (model, data_type, reference, stale)
            for model in ctx.models
            for data_type in _named_data_types(model)
            if (written := _written_reference(data_type)) is not None
            and tuple(_get_data_type_target_full_name(data_type, reference := written[0], module_names).split(".")[:-1])
            in modules
            and (stale := imports.reference_paths.get(reference.path)) is not None
            and _module_binding(written[1]) == stale.binding_name
        ]
        if not users:
            continue
        for model, *_, stale in users:
            ledger.remove(imports, stale, ImportProducer(_HIDDEN_SUBMODULES, model))
        for model, stale in {
            stale.reference_path: (model, stale) for model, data_type, _, stale in users if data_type.reference is None
        }.values():
            ledger.remove(
                imports, repeat(stale, _held_count(ledger, imports, stale)), ImportProducer(_HIDDEN_SUBMODULES, model)
            )
        resolver = ctx.scoped_model_resolver
        ledger.release_names(
            imports, resolver, {stale.binding_name for *_, stale in users}.difference(_iter_import_bindings(imports))
        )
        for name in sorted(
            {reference.short_name for _, _, reference, _ in users}
            .intersection(_iter_import_bindings(imports))
            .difference(held.name for held in resolver.references.values())
            .difference(_iter_import_bindings(parser.imports))
        ):
            ledger.add_name(imports, resolver, get_special_path("import", [name]), name)
        for model, data_type, reference, _ in users:
            expression = register_reference_import(
                reference,
                _get_model_module_name(model, module_names),
                _get_data_type_target_full_name(data_type, reference, module_names),
                imports,
                resolver,
                init=ctx.is_init,
                internal_modules=state.internal_modules,
                producer=ImportProducer(_HIDDEN_SUBMODULES, model),
                import_ledger=ledger,
                shared_imports=parser.imports,
                hidden_modules=modules,
            )
            if data_type.reference is None:
                parser._graph_builder.name_enum_member_literal_class(data_type, expression or reference.short_name)  # noqa: SLF001
            else:
                parser._graph_builder.name_reference(data_type, expression or reference.short_name)  # noqa: SLF001
        _follow_renamed_enum_references(parser, ctx.models, [data_type for _, data_type, *_ in users])


def _held_count(ledger: ModuleImportLedger, imports: Imports, import_: Import) -> int:
    """Return how often the ledger of ``imports`` holds the import: the times it was added less the times removed."""
    return sum(
        (1 if isinstance(operation, Added) else -1) * operation.imports.count(import_)
        for entry in ledger.entries(imports)
        if isinstance(operation := entry.operation, Added | Removed)
    )


def name_final_classes_of_additional_properties(state: _FinalizeState[Any]) -> None:
    """Name the final classes that ``extra_items`` and additional properties reference.

    A setting names them unquoted where each is bound when the class statement runs (``_binds_named_classes``).
    """
    positions = {id(model): (ctx.module, index) for ctx in state.contexts for index, model in enumerate(ctx.models)}
    for model in state.additional_properties_models:
        model._name_final_classes(binds_named_classes=_binds_named_classes(model, positions))  # noqa: SLF001


def _binds_named_classes(model: DataModel, positions: Mapping[int, tuple[ModulePath, int]]) -> bool:
    """Return whether every class the additional properties type of ``model`` names is bound as the model is defined.

    ``positions`` gives the module of each written model and its place there. A class of another module is bound by
    the import at the top of the module, which ``check_import_reads`` reads as the module runs; one of the same module
    only where the module declares it before the model, and one that no module writes nowhere.
    """
    if (own := positions.get(id(model))) is None or (data_type := model.additional_properties_type) is None:
        return False
    for nested in data_type.all_data_types:
        if (reference := nested.reference) is None:
            continue
        if (position := positions.get(id(reference.source))) is None or (
            position[0] == own[0] and position[1] >= own[1]
        ):
            return False
    return True


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


def record_generated_class_names(parser: FinishHost, run: FinishRun[Any, ModuleContextView, Any]) -> None:
    """Record the class name of every generated model, and its final name where the run records what it names."""
    parser.generated_class_names = tuple(
        (model.class_name, model.duplicate_class_name) for ctx in run.contexts for model in ctx.models
    )
    if (record := parser.model_resolver.model_name_record) is not None:
        record.final = {model.path: model.reference.name for ctx in run.contexts for model in ctx.models}


def translate_final_name_keys(parser: FinishHost, run: FinishRun[Any, ModuleContextView, Any]) -> None:
    """Record the output names of the models renamed after L2 that keys of the options read at L1 name, by path.

    Such a name is none a lookup at L1 reads: the parse again (``__init__.py:_parse_generation_run``) gives it to the
    model built at the path the model had at L2 (``ModelResolver.final_name_keys``), whose keys then also name it. A
    name is recorded where a ``ConstructionKeys`` key names it or its class name, the part after its last dot, and it
    is not the name the model had at L2.
    """
    origins: dict[str, tuple[str, str]] = {}
    for rename in parser.generation_store.renames:
        origin = (
            origins.pop(rename.import_path, None) if rename.import_path != rename.path else origins.get(rename.path)
        )
        origins[rename.path] = origin or (rename.import_path, rename.reference_name)
    keys = parser._construction_keys  # noqa: SLF001
    names: dict[str, dict[str, None]] = {}
    for model in _context_models(run.contexts):
        if (origin := origins.get(model.path)) is None:
            continue
        path, settled_name = origin
        if (name := model.reference.name) != settled_name and keys.name_any({name, name.rpartition(".")[2]}):
            names.setdefault(path, {})[name] = None
    parser.final_name_keys_to_assign = tuple((path, tuple(found)) for path, found in names.items())


def add_root_init_result(run: FinishRun[Any, Any, Any]) -> None:
    """Add an empty root ``__init__.py`` when the output has several top-level packages."""
    root_init: ModulePath = ("__init__.py",)
    if root_init not in run.module_results:
        top_level_dirs = {k[0] for k in run.module_results if len(k) >= 2}  # noqa: PLR2004
        if len(top_level_dirs) > 1:
            run.module_results[root_init] = run.result_type(body="")


def render(parser: RenderHost[ConfigT, ContextT, ResultT], run: FinishRun[ConfigT, ContextT, ResultT]) -> None:
    """Render every module into the output of ``parse()``."""
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
    groups = parser._reuse_groups  # noqa: SLF001
    placement = {ctx.module: ctx.models for ctx in contexts}
    parser.model_placement = {
        module: frozenset(model.class_name for model in models if not groups.is_typed_map_value(model))
        for module, models in placement.items()
    }
    parser.typed_map_values_may_move_models = (
        has_internal_modules
        or (parser.reuse_model and parser.reuse_scope == ReuseScope.Tree)
        or any(models and all(map(groups.is_typed_map_value, models)) for models in placement.values())
        or (
            parser.collapse_root_models
            and any(
                model_key_of(model.reference) in parser.typed_nullable_map_reference_ids
                for models in placement.values()
                for model in models
            )
        )
    )


def _package_bindings(
    contexts: Iterable[ModuleContextView], forwarder_map: ForwarderMap, *, treat_dot_as_module: bool | None
) -> list[tuple[ModulePath, ModuleContextView, tuple[str, ...]]]:
    """Return each module that writes a package's ``__init__.py``, by its written path, with the names it binds.

    It binds the class names of its models, or where an import cycle merge moved them, the names its forwarder
    re-exports (``Forwarder.names``).
    """
    bindings: list[tuple[ModulePath, ModuleContextView, tuple[str, ...]]] = []
    for ctx in contexts:
        if not is_package_init((path := written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))[-1]):
            continue
        if ctx.models:
            bindings.append((path, ctx, tuple(model.class_name for model in ctx.models)))
        elif (forwarder := forwarder_map.get(ctx.module_key)) is not None:
            bindings.append((path, ctx, tuple(bound for _, _, bound in forwarder.names)))
    return sorted(bindings, key=operator.itemgetter(0))


def _class_named_like_submodule(
    contexts: Sequence[ModuleContextView],
    bindings: Sequence[tuple[ModulePath, ModuleContextView, tuple[str, ...]]],
    forwarder_map: ForwarderMap,
    *,
    treat_dot_as_module: bool | None,
    all_exports_scope: AllExportsScope | None,
) -> tuple[ModulePath, str, tuple[str, ...], tuple[str, ...]] | None:
    """Return the first class a package's ``__init__.py`` binds beside a module of its name that replaces it, or None.

    The module replaces the class where the ``__init__.py`` does not load it while its imports run, before its classes
    are defined (``reject_class_named_like_submodule``); a forwarder loads the modules it re-exports from
    (``Forwarder.names``) and what they load. The class comes with the written path of the ``__init__.py``, the
    module's parts and the path of its file.
    """
    written = sorted(
        ((written_path(ctx.module, treat_dot_as_module=treat_dot_as_module), ctx) for ctx in contexts if ctx.models),
        key=operator.itemgetter(0),
    )
    submodules: dict[tuple[str, ...], tuple[str, ...]] = {}
    for path, _ in written:
        parts = module_parts(path)
        for size in range(1, len(parts) + 1):
            submodules.setdefault(parts[:size], path if size == len(parts) else (*parts[:size], "__init__.py"))
    clashes = [
        (path, name, module, file)
        for path, _, names in bindings
        for name in names
        if (file := submodules.get(module := (*path[:-1], name))) is not None
    ]
    if not clashes:
        return None
    edges = module_import_edges(contexts, treat_dot_as_module=treat_dot_as_module, all_exports_scope=all_exports_scope)
    written_modules = {
        ctx.module_key: dotted_module(written_path(ctx.module, treat_dot_as_module=treat_dot_as_module))
        for ctx in contexts
    }
    for ctx in contexts:
        if not ctx.models and (forwarder := forwarder_map.get(ctx.module_key)) is not None:
            edges.setdefault(written_modules[ctx.module_key], set()).update(
                written_modules[source] for source, _, _ in forwarder.names
            )
    for path, name, module, file in clashes:
        loaded = reach([dotted_module(path)], lambda importer: edges.get(importer, ()))
        if not any(is_within_name(loaded_name, ".".join(module)) for loaded_name in loaded):
            return path, name, module, file
    return None


def _hidden_class_refusal(
    hidden: tuple[ModulePath, str, tuple[str, ...], tuple[str, ...]], config: ParserConfig
) -> str:
    """Return the refusal of a class that a module of its name replaces (``_class_named_like_submodule``).

    It names what renames the class or the module: --rename-submodules-hidden-by-classes renames the modules of the
    module structure, not the shared module that tree-scope reuse adds, which ``--shared-module-name`` names.
    """
    path, name, module, file = hidden
    package = ".".join(path[:-1])
    where = f"the package {stable_repr(package)}" if package else "the root package"
    if not config.rename_submodules_hidden_by_classes:
        hint = (
            "Rename the class or the module in the input, or pass --rename-submodules-hidden-by-classes to rename the "
            "module."
        )
    else:
        shared = config.reuse_model and config.reuse_scope == ReuseScope.Tree and module == (config.shared_module_name,)
        hint = (
            "--rename-submodules-hidden-by-classes renames only the modules that the schema names and the input files "
            "give: rename the class in the input or give the module another name"
            f"{' (--shared-module-name)' if shared else ''}."
        )
    return (
        f"Cannot write the class {stable_repr(name)} in {where} beside the module {stable_repr('.'.join(module))} "
        f"({'/'.join(file)}): importing the module binds its name in the package over the class, so the models that "
        f"use the class fail to import or decode. {hint}"
    )


def _final_class_seeds(
    parser: RenderHost[Any, Any, Any],
    contexts: Iterable[ModuleContextView],
    forwarder_map: ForwarderMap,
    bindings: Iterable[tuple[ModulePath, ModuleContextView, tuple[str, ...]]],
) -> ImportCycleSeeds | None:
    """Return the seeds of a parse again that renames the submodules of a package by the names it binds, or None.

    A package disagrees with the renames of --rename-submodules-hidden-by-classes where a name its ``__init__.py``
    binds in the output names a module below it, as a later pass gave a class that name, or where it no longer binds a
    name a submodule was renamed for (``Parser._hidden_submodule_renames``). Its names join the names it bound in the
    earlier attempts of the run (``ImportCycleSeeds.package_classes``, by the module path of the module structure):
    those only grow, and the run is parsed again only where they grow.
    """
    modules = {
        ctx.module_key[:size]
        for ctx in contexts
        if ctx.models or ctx.module_key in forwarder_map
        for size in range(1, len(ctx.module_key) + 1)
    }
    bound = {ctx.module_key: frozenset(names) for _, ctx, names in bindings}
    disagreeing = {package for package, names in bound.items() if any((*package, name) in modules for name in names)}
    disagreeing.update(
        package
        for package, hidden in parser._hidden_submodule_renames  # noqa: SLF001
        if hidden not in bound.get(package, ())
    )
    seeds = parser._import_cycle_seeds  # noqa: SLF001
    seeded = dict(seeds.package_classes)
    grown = {
        **seeded,
        **{package: seeded.get(package, frozenset()) | bound.get(package, frozenset()) for package in disagreeing},
    }
    if grown == seeded:
        return None
    return dataclasses.replace(seeds, package_classes=tuple(sorted(grown.items(), key=operator.itemgetter(0))))


def reject_class_named_like_submodule(
    parser: RenderHost[Any, Any, Any],
    contexts: Sequence[ModuleContextView],
    forwarder_map: ForwarderMap,
    *,
    all_exports_scope: AllExportsScope | None,
) -> None:
    """Refuse a package whose ``__init__.py`` binds a class named like a module or package below it.

    Loading ``pkg.Warn`` binds ``Warn`` in ``pkg`` over the class ``Warn`` of ``pkg/__init__.py``, so the models that
    name the class then fail to import or decode, and ``from . import Warn`` gives the class, so a model of the module
    (``Warn.Detail``) fails too; a class that an import cycle merge moved into ``_internal`` and the ``__init__.py``
    re-exports is bound alike. It is judged on the final class names and import plans, where the modules are
    written. It holds where the ``__init__.py`` loads the module while its imports run, before its classes are
    defined or re-exported (it imports from the module or below it, a module it imports does, or
    ``--all-exports-scope`` re-exports a module of it): the class takes the name after, and no later import rebinds it
    (``submodules_hidden_by_packages``). Renaming the class or the module changes names that the models and the files
    go by, so the run fails and names both. Under --rename-submodules-hidden-by-classes, where the renames of the
    submodules disagree with the names the packages bind here (``_final_class_seeds``), the run is parsed again with
    those names instead, and the refusal is kept for the run (``Parser.hidden_class_refusal``), which raises it if
    the parse again does not import.
    """
    config, treat_dot_as_module = parser.config, parser.treat_dot_as_module
    bindings = _package_bindings(contexts, forwarder_map, treat_dot_as_module=treat_dot_as_module)
    hidden = _class_named_like_submodule(
        contexts, bindings, forwarder_map, treat_dot_as_module=treat_dot_as_module, all_exports_scope=all_exports_scope
    )
    refusal = None if hidden is None else _hidden_class_refusal(hidden, config)
    if (
        config.rename_submodules_hidden_by_classes
        and (seeds := _final_class_seeds(parser, contexts, forwarder_map, bindings)) is not None
    ):
        parser.import_cycle_seeds_to_apply = seeds
        parser.hidden_class_refusal = refusal
        parser.fails_to_import = refusal is not None
    elif refusal is not None:
        raise Error(refusal)


def reject_unimportable_imports(
    module: ModulePath,
    imports: Imports,
    *,
    is_init: bool,
    target_python_version: PythonVersion,
    treat_dot_as_module: bool,
) -> None:
    """Refuse a relative import of ``module``'s block that is written with a part Python cannot import by.

    A part that is a keyword (a directory or file named ``class``) makes ``from .class import k`` a syntax error, and
    ``--treat-dot-as-module`` (``treat_dot_as_module``) writes each dot of a directory or file name as a package, so
    ``1.0`` is the packages ``_1`` and ``0``, and ``from ._1.0 import version`` is one too. The parts of the importing
    module's own path are not written, and a relative import of them is valid. Renaming the module would move the file
    and every other import of it, so the run fails and names the module and the part. An import is judged where its
    block is written: a reference the parser planned and a later pass dropped (a collapsed root model) writes none.
    Under ``--treat-dot-as-module`` a part is a name by the target's rules, as the name of a module is written; without
    it the dots are ``_`` and a part is refused only for a keyword (``is_importable_part``). ``is_init`` makes
    ``module`` a package ``__init__``.
    """
    row: ModulePartRow = "target" if treat_dot_as_module else "keyword"
    importer = dotted_module(module)
    for from_, target, names in relative_import_targets(importer, imports, is_init=is_init):
        written = from_.lstrip(".").split(".") if from_.strip(".") else []
        parts = (*written, *names)
        unimportable = (part for part in parts if not is_importable_part(part, target_python_version, row=row))
        if (part := next(unimportable, None)) is not None:
            path = target if part in written else dotted_name(target, part)
            where = f"module {stable_repr(importer)}" if importer else "the root package"
            reason, hint = (
                ("a Python keyword", "Rename the input.")
                if iskeyword(part)
                else (
                    "not a Python identifier",
                    (
                        "--treat-dot-as-module writes each dot of a directory or file name as a package. Use "
                        "--no-treat-dot-as-module, or rename the input."
                    ),
                )
            )
            msg = (
                f"Cannot import the module {stable_repr(path)} from {where}: {stable_repr(part)} is {reason}, so the "
                f"import is a syntax error. {hint}"
            )
            raise Error(msg)


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
    """Render the shared result mapping and apply final output-only transformations.

    Every relative import block that a module writes is kept (``_record_written_imports``) for the import check of a
    modular output (``check_import_reads``).
    """
    future_imports = parser._import_ledger.extract_future(parser.imports, _FUTURE_IMPORTS)  # noqa: SLF001
    future_imports_str = str(future_imports)
    merges = forwarder_map
    if forwarder_map:
        forwarder_map = _forward_defined_models(contexts, _released_name_imports(parser, contexts, forwarder_map))
    parser._written_imports = written = []  # noqa: SLF001

    # Export depth and collision prefixes must follow the final package layout.
    if (
        parser.treat_dot_as_module
        and config.all_exports_scope is not None
        and any(expanded_path(ctx.module) != ctx.module for ctx in contexts)
    ):
        contexts = [ctx._replace(module=expanded_path(ctx.module)) for ctx in contexts]

    reject_class_named_like_submodule(parser, contexts, forwarder_map, all_exports_scope=config.all_exports_scope)
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
        normalized_path(module, treat_dot_as_module=parser.treat_dot_as_module): result
        for module, result in results.items()
    }
    if parser.treat_dot_as_module:
        results = parser._result_modules_postprocessor(results, empty_init=config.all_exports_scope is not None)  # noqa: SLF001
        if config.all_exports_scope is not None:
            parser._generate_empty_init_exports(results, contexts, config, future_imports_str)  # noqa: SLF001
    check_import_reads(
        parser, results, contexts, merges, written, use_deferred_annotations=config.use_deferred_annotations
    )
    return results


def _module_read(text: str | None, submodules: Mapping[str, str]) -> tuple[str, str] | None:
    """Return the module and name that the expression ``text`` reads through a module it binds, as ``pets.Pet`` does."""
    binding, dot, rest = (text or "").strip("\"'").partition(".")
    return (submodules[binding], rest.partition(".")[0]) if dot and binding in submodules else None


def _definition_reads(
    models: Iterable[DataModel],
    submodules: Mapping[str, str],
    *,
    root_model_type: type[DataModel] | None,
    evaluates_annotations: bool,
) -> frozenset[tuple[str, str]]:
    """Return the names of other modules that the definitions of ``models`` read as their module runs.

    A definition reads a name through a module its module imports (``submodules``, by the name it binds each to) in
    the types of the fields it evaluates (``definition_reads``, where the class annotations count when
    ``evaluates_annotations`` tells that something evaluates them as the class is defined), its bases, the types of
    its schema validator rules and of the backend settings its class statement evaluates (TypedDict ``extra_items``),
    and the enum members its defaults name.
    """
    texts: list[str | None] = []
    for model in models:
        fields = (
            definition_reads(model, root_model_type=root_model_type, evaluates_annotations=evaluates_annotations) or ()
        )
        for data_type in chain(
            (data_type for field in fields for data_type in field.data_type.all_data_types),
            model.base_classes,
            _parsed_rule_data_types(model),
            model._setting_data_types_read_at_definition(),  # noqa: SLF001
        ):
            texts.append(data_type.alias)
            texts.extend(enum_class for enum_class, _ in data_type.enum_member_literals)
        for field in model.fields:
            values = field.default if isinstance(field.default, list) else [field.default]
            texts.extend(value.alias for value in values if isinstance(value, Member))
    return frozenset(read for text in texts if (read := _module_read(text, submodules)) is not None)


def _structure_modules(
    contexts: Iterable[ModuleContextView], forwarder_map: ForwarderMap, written_module: Callable[[ModulePath], str]
) -> dict[str, frozenset[ModulePath]]:
    """Return the modules of the module structure that each written module holds, by its dotted name.

    An ``_internal`` module holds the modules of its merge, a module with models or a forwarder itself; the
    ``_internal`` module of a merge that a grown merge holds, now a forwarder, and an empty package hold none.
    """
    released = {module for module, forwarder in forwarder_map.items() if module in forwarder.released_internal_modules}
    internal_modules = {forwarder.internal_module for forwarder in forwarder_map.values()}
    return {
        written_module(ctx.module): frozenset(
            {
                module
                for module, forwarder in forwarder_map.items()
                if forwarder.internal_module == ctx.module_key and module not in released
            }
            if ctx.module_key in internal_modules
            else {ctx.module_key}
        )
        for ctx in contexts
        if (ctx.models or ctx.module_key in forwarder_map) and ctx.module_key not in released
    }


def _generic_base_class_placement(
    base: DataModel | None,
    contexts: Sequence[ModuleContextView],
    plan: ImportPlan,
    written_module: Callable[[ModulePath], str],
) -> GenericBaseClassPlacement | None:
    """Return where the generic base class ``base`` is written, which modules take it and which import it."""
    if base is None or (holder := next((ctx for ctx in contexts if any(m is base for m in ctx.models)), None)) is None:
        return None
    module, key = written_module(holder.module), model_key_of(base.reference)
    return GenericBaseClassPlacement(
        module,
        base.class_name,
        tuple(
            (written_module(ctx.module), ctx.module_key)
            for ctx in contexts
            if any(
                model_key_of(reference) == key
                for model in ctx.models
                for base_class in model.base_classes
                if (reference := base_class.reference) is not None
            )
        ),
        frozenset(
            importer
            for importer, blocks in plan.blocks.items()
            for target, lines in blocks
            if target == module and any(name == base.class_name for name, _ in lines)
        ),
    )


def check_import_reads(  # noqa: PLR0913
    parser: RenderHost[Any, Any, Any],
    results: Mapping[ModulePath, object],
    contexts: Sequence[ModuleContextView],
    forwarder_map: ForwarderMap,
    written: Sequence[tuple[ModulePath, Imports]],
    *,
    use_deferred_annotations: bool,
) -> None:
    """Seed a parse again of a modular output where importing one of its modules reads a module that still runs.

    The plan is read from the final facts: the relative import blocks each written module runs (``written``, in the
    order the formatters write them) and the names its definitions read of the modules it imports as it runs
    (``_definition_reads``; with custom templates, which decide what a class body evaluates, none). Pydantic builds a
    class as it is defined, so its annotations count even where the module defers them. Where a module fails to
    import (``failing_import_reads``, ``fails_to_import``), the parse again moves the generic base class where its
    import ran into the failure, or merges the modules of that import cycle into an ``_internal`` module
    (``import_cycle_seeds``), beside the names of the packages that ``reject_class_named_like_submodule`` seeded; an
    output whose every module imports is parsed once.
    """
    treat_dot_as_module = parser.treat_dot_as_module

    def written_module(path: ModulePath) -> str:
        return dotted_module(written_path(path, treat_dot_as_module=treat_dot_as_module))

    imports_of: defaultdict[ModulePath, list[Imports]] = defaultdict(list)
    for path, imports in written:
        imports_of[path].append(imports)
    plan = import_plan(
        (written_module(path) for path in results if path[-1].endswith(".py")),
        {
            written_module(path): written_import_blocks(
                written_module(path),
                imports,
                is_init=is_package_init(written_path(path, treat_dot_as_module=treat_dot_as_module)[-1]),
            )
            for path, imports in imports_of.items()
        },
    )
    if not runs_into_import_cycle(plan):
        return
    evaluates_annotations = not use_deferred_annotations or parser.data_model_type._READ_BY_PYDANTIC  # noqa: SLF001
    reads: dict[str, frozenset[tuple[str, str]]] = {}
    for ctx in contexts if parser.custom_template_dir is None else ():
        module = written_module(ctx.module)
        submodules = {
            binding: submodule
            for target, lines in plan.blocks.get(module, ())
            for name, binding in lines
            if (submodule := dotted_name(target, name)) in plan.modules
        }
        if submodules and (
            names := _definition_reads(
                ctx.models,
                submodules,
                root_model_type=parser.pydantic_v2_root_model_type,
                evaluates_annotations=evaluates_annotations,
            )
        ):
            reads[module] = names
    if not (failures := failing_import_reads(dataclasses.replace(plan, reads=reads))):
        return
    parser.fails_to_import = True
    parser.import_cycle_seeds_to_apply = (
        import_cycle_seeds(
            failures,
            plan,
            parser.import_cycle_seeds_to_apply or parser._import_cycle_seeds,  # noqa: SLF001
            _structure_modules(contexts, forwarder_map, written_module),
            _generic_base_class_placement(parser._generic_base_model, contexts, plan, written_module),  # noqa: SLF001
        )
        or parser.import_cycle_seeds_to_apply
    )
