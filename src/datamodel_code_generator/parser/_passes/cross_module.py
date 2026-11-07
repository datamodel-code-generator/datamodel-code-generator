"""The cross-module passes (L6a): what the processed modules settle once every module is processed.

Each pass is a function of the parser and the run, called from the pipeline tables of ``parser/_passes/_tables.py``;
the parser comes in through ``CrossModuleHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator._model_keys import keyed_or_path
from datamodel_code_generator._module_paths import dotted_name
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model._decisions.tag_value import enum_tag_as_values
from datamodel_code_generator.model.base import (
    GENERIC_BASE_CLASS_NAME,
    GENERIC_BASE_CLASS_PATH,
    BaseClassDataType,
    DataModel,
    _find_base_classes,
)
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._import_binding import (
    ReimportHost,
    generic_base_class_import,
    register_reference_import,
    reimport_references,
    set_reference_expressions,
)
from datamodel_code_generator.parser._passes._import_edges import generic_base_class_module, inherits_one_of
from datamodel_code_generator.parser._passes._shared import (
    GenerationStoreHost,
    ModuleScopeView,
    _context_models,
    _get_model_module_name,
    _named_data_types,
    module_members,
)
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._passes.plans import FlattenHost, flatten_base_layouts
from datamodel_code_generator.parser._tag_plan import TagPlanHost, iter_struct_tag_decisions
from datamodel_code_generator.python_literal import stable_repr
from datamodel_code_generator.reference import Reference

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._imports_plan import ImportPass
    from datamodel_code_generator.parser._passes._import_edges import ImportCycleSeeds
    from datamodel_code_generator.parser._passes._state import ModuleContextView, ModulePath, _ConstNarrowing
    from datamodel_code_generator.parser.generation import ModelRename
    from datamodel_code_generator.types import DataType

__all__ = [
    "CrossModuleHost",
    "CrossModuleRun",
    "FlattenAcrossModulesHost",
    "TaggedBasesHost",
    "apply_generic_base_class",
    "flatten_narrowed_inherited_fields",
    "import_enum_member_literal_classes",
    "import_removed_reuse_canonicals",
    "import_renamed_types",
    "tag_fields_of_tagged_bases",
    "warn_enums_admitting_other_values",
]


class CrossModuleHost(ReimportHost, GenerationStoreHost, Protocol):
    """The options, run state and hooks of the parser that the cross-module passes read and write."""

    _generic_base_model: DataModel | None

    @property
    def _import_cycle_seeds(self) -> ImportCycleSeeds:
        """What an earlier attempt of the run found to fail as the output is imported."""

    @property
    def _enum_admits_other_values(self) -> list[tuple[DataModelFieldBase, _ConstNarrowing]]:
        """The values typed by an enum whose const admits only one of its values, with that const."""

    @property
    def _removed_reuse_users(self) -> dict[int, tuple[DataType, list[str]]]:
        """The data types whose duplicate reuse removed, by id, with the paths they named."""

    @property
    def custom_template_dir(self) -> Path | None:
        """The directory of custom templates, if configured."""

    @property
    def data_model_type(self) -> type[DataModel]:
        """The model type of the output."""

    @property
    def generic_base_class_config(self) -> dict[str, Any]:
        """The configuration of the generated base class."""

    @property
    def keyword_only(self) -> bool:
        """Whether the models take keyword-only arguments."""

    @property
    def use_generic_base_class(self) -> bool:
        """Whether the models share a generated base class."""

    def _is_root_model(self, model: DataModel) -> bool:
        """Return whether a model uses the configured root model representation."""

    def _new_model(self, factory: Callable[..., DataModel | None], /, **kwargs: Any) -> DataModel | None:
        """Build a model with ``factory``, a model class or a model factory, for the run's target Python."""


class FlattenAcrossModulesHost(CrossModuleHost, FlattenHost, Protocol):
    """What making a model of any module declare the fields of its bases reads and writes of the parser."""


class TaggedBasesHost(FlattenAcrossModulesHost, TagPlanHost, Protocol):
    """What tagging the structs below tagged bases reads and writes of the parser."""


class CrossModuleRun(ModuleScopeView, Protocol):
    """What the cross-module passes read and write of the run."""

    @property
    def contexts(self) -> Sequence[ModuleContextView]:
        """The processed modules, in module order."""


_GENERIC_BASE_CLASS: Final = ImportProducer("generic-base-class")


def _flattener(
    parser: FlattenAcrossModulesHost, run: CrossModuleRun, producer: ImportPass
) -> Callable[[DataModel], None]:
    """Return what makes a model of the run declare the fields of its generated bases and drop them.

    The modules have bound the names of the models their fields name by then, so the model names the models of each
    copied field from its own module, importing those of others under names that no field of its module takes, as
    the module's own imports are.
    """
    contexts = {id(model): ctx for ctx in run.contexts for model in ctx.models}

    def flatten(model: DataModel) -> None:
        own_fields = {id(field) for field in model.fields}
        flatten_base_layouts(parser, model)
        copied = [field for field in model.fields if id(field) not in own_fields]
        ctx = contexts[id(model)]
        ctx.scoped_model_resolver.exclude_names.update(field.name for field in copied if field.name is not None)
        set_reference_expressions(
            parser,
            ctx,
            run,
            model,
            (data_type for field in copied for data_type in field.data_type.all_data_types),
            members=keyed_models(module_members(parser, ctx.models), "module members"),
            producer=ImportProducer(producer, model),
        )

    return flatten


def tag_fields_of_tagged_bases(parser: TaggedBasesHost, run: CrossModuleRun) -> None:
    """Write what becomes of the msgspec structs below tagged bases (``iter_struct_tag_decisions``), and record it.

    A struct that declares the fields of its bases instead names the models of its copied fields as ``_flattener``
    names them.
    """
    flatten = _flattener(parser, run, "tag-fields-of-tagged-bases")
    builder = parser._graph_builder  # noqa: SLF001
    decisions = parser._tag_plan.struct_tags  # noqa: SLF001
    for decision in iter_struct_tag_decisions(_context_models(run.contexts)):
        model = decision.model
        match decision.kind:
            case "tagged-base":
                for field in [
                    field
                    for field, tag_field, tag in decision.tags
                    if model.apply_discriminator_tag(field, tag_field, tag.value)
                ]:
                    builder.set_field_extra(field, "is_classvar", value=True)
            case "flattened":
                flatten(model)
            case _:
                for field in decision.untagged:
                    builder.set_field_extras(
                        field, {key: value for key, value in field.extras.items() if key != "is_classvar"}
                    )
                    if not (field.data_type.literals or field.data_type.enum_member_literals):
                        builder.replace_field_type(field, field.data_type.__class__(literals=[field.extras["const"]]))
                model.opt_out_of_inherited_tag()
        decisions.append(decision)


def flatten_narrowed_inherited_fields(parser: FlattenAcrossModulesHost, run: CrossModuleRun) -> None:
    """Make each model whose field narrows an inherited one, which its subclass cannot write, drop its bases.

    The output holds an inherited field to the annotation of its bases (``DataModel._KEEPS_INHERITED_FIELD_TYPES``),
    so such a model declares the fields of its generated bases itself (``_flattener``), bases first: a model that
    inherits from one that narrows a field compares its fields with what that base declares then. A model whose form
    writes no base, a functional TypedDict, declares them too (``DataModel._declares_inherited_fields``).
    """
    flatten = _flattener(parser, run, "flatten-narrowed-inherited-fields")
    written = {id(model) for ctx in run.contexts for model in ctx.models}
    visits = [(model, False) for ctx in reversed(run.contexts) for model in reversed(ctx.models)]
    visited: set[int] = set()
    while visits:
        model, complete = visits.pop()
        if complete:
            if model._declares_inherited_fields():  # noqa: SLF001
                flatten(model)
        elif id(model) in written and id(model) not in visited:
            visited.add(id(model))
            visits.append((model, True))
            visits.extend((base, False) for base in reversed(_find_base_classes(model)))


def write_enum_member_tags_as_values(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Write the values of the enum members that tag a variant, where the output's ``Literal`` holds no enum member.

    Every union is processed by then, so a tag that a later union wrote over keeps what it wrote. The tags read a member
    literal as no literal until the structs are tagged, so this runs after that. A tag takes the values when its
    ``Literal`` holds each one (``enum_tag_as_values``), else it keeps its members.
    """
    for model in _context_models(run.contexts):
        for field in model.fields:
            data_type = field.data_type
            if (
                (reference := data_type._enum_member_literal_reference) is None  # noqa: SLF001
                or data_type.alias
                or not isinstance(enum := reference.source, Enum)
            ):
                continue
            member_values = {member.name: enum.get_member(member).value for member in enum.fields}
            values = [member_values[member] for _, member in data_type.enum_member_literals]
            if enum_tag_as_values(values, bool_literals=parser.data_model_type.SUPPORTS_BOOLEAN_LITERAL):
                parser._graph_builder.replace_field_type(  # noqa: SLF001
                    field, data_type.__class__(literals=values, is_optional=data_type.is_optional)
                )


def warn_enums_admitting_other_values(parser: CrossModuleHost) -> None:
    """Warn once per schema location that a value typed by its enum accepts the values its const excludes.

    Runs once msgspec tags are final, as a field that becomes its struct's tag is no value and admits only its tag.
    """
    values = (narrowing for field, narrowing in parser._enum_admits_other_values if not field.is_class_var)  # noqa: SLF001
    for narrowing in {narrowing.location: narrowing for narrowing in values}.values():
        warn_as_parser_base(
            f"The schema at {narrowing.location} admits only its const {stable_repr(narrowing.const)} of its enum "
            "values, but its values are typed as its enum class, which accepts the other values too.",
            UserWarning,
            stacklevel=2,
        )


def _stale_renamed_import(imports: Imports, data_type: DataType, renamed_types: Sequence[ModelRename]) -> Import | None:
    """Return the import of a module that still names a data type's type by one of the names it had before."""
    return next(
        (
            import_
            for renamed_type in renamed_types
            if (import_ := imports.reference_paths.get(renamed_type.import_path)) is not None
            and (
                import_.import_ == renamed_type.class_name
                or (data_type.alias or "").endswith(f".{renamed_type.class_name}")
            )
        ),
        None,
    )


def import_removed_reuse_canonicals(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Import the kept model where a module processed earlier imported a duplicate that reuse removed."""
    if not (users := parser._removed_reuse_users):  # noqa: SLF001
        return
    for ctx in run.contexts:
        if not (
            stale_imports := {
                path: import_
                for _, paths in users.values()
                for path in paths
                if (import_ := ctx.imports.reference_paths.get(path))
            }
        ):
            continue
        members = keyed_models(ctx.models, "module members")
        for model in ctx.models:
            for data_type in _named_data_types(model):
                user, paths = users.get(id(data_type), (None, ()))
                if user is not data_type or not (
                    stale_import := next(filter(None, map(stale_imports.get, paths)), None)
                ):
                    continue
                reimport_references(
                    parser,
                    ctx,
                    run,
                    [(model, data_type, stale_import)],
                    row="import-removed-reuse-canonicals",
                    members=members,
                )


def _stale_import_renames(
    renames: Iterable[ModelRename],
) -> tuple[dict[ModelKey, list[ModelRename]], dict[str, list[ModelRename]]]:
    """Return the renames that modules processed before them may still import the old name of, by key and by path.

    These are every ``field-type`` rename and each ``collapsed-root`` rename that changed the class name, under the
    keys the model had when it was renamed and under its path then: row ``legacy-keyless-reference-by-path`` reads
    the path for a reference that no model was built with.
    """
    by_key: dict[ModelKey, list[ModelRename]] = {}
    by_path: dict[str, list[ModelRename]] = {}
    for rename in renames:
        if rename.scheme == "field-type" or (
            rename.scheme == "collapsed-root" and rename.new_class_name != rename.class_name
        ):
            for key in rename.keys:
                by_key.setdefault(key, []).append(rename)
            by_path.setdefault(rename.path, []).append(rename)
    return by_key, by_path


def import_renamed_types(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Import a type by its new name where a module processed before the type's module imported the old one.

    A field of the type's module can hide the type, which then gets a new name after modules that come first
    built their imports of it, and the ``parent`` strategy of root-model collapse gives an inner model the name
    and path of the root model it replaces. Every name the type had is recorded, as a module processed between two
    renames imported the name in between. The stale import is the one added under the type's path at that name.
    It releases the name it binds for the new one, found by name, as neither the import of a package ``__init__``
    nor a ``from .`` path leads back to the path it was added under.
    """
    by_key, by_path = _stale_import_renames(parser.generation_store.renames)
    if not by_key:
        return
    for ctx in run.contexts:
        members = module_members(parser, ctx.models)
        keyed_members = keyed_models(members, "module members")
        stale_users = [
            (model, data_type, import_)
            for model in members
            for data_type in _named_data_types(model)
            if (reference := data_type.reference) is not None
            and (renamed_types := keyed_or_path(reference, by_key, by_path)) is not None
            and keyed_members.key_of(reference) not in keyed_members.models
            and (import_ := _stale_renamed_import(ctx.imports, data_type, renamed_types)) is not None
        ]
        reimport_references(parser, ctx, run, stale_users, row="import-renamed-types")


def _module_qualified_name(reference: Reference, model_path_to_module_name: Mapping[str, str]) -> str:
    """Return the name of the referenced model qualified by its module, or its short name in the root module."""
    module_name = _get_model_module_name(cast("DataModel", reference.source), model_path_to_module_name)
    return dotted_name(module_name, reference.short_name)


def import_enum_member_literal_classes(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Name the enum class of each enum member literal as its module writes it, importing it from another module.

    A literal of the enum's own module takes the enum's final name, which a type renamed for a field it hid
    changes after the literal is made.
    """
    for ctx in run.contexts:
        members = keyed_models(ctx.models, "module members")
        for model in ctx.models:
            for data_type in model.all_data_types:
                if (reference := data_type._enum_member_literal_reference) is None or data_type.alias:  # noqa: SLF001
                    continue
                enum_class = (
                    reference.short_name
                    if members.key_of(reference) in members.models
                    else register_reference_import(
                        reference,
                        _get_model_module_name(model, run.model_path_to_module_name),
                        _module_qualified_name(reference, run.model_path_to_module_name),
                        ctx.imports,
                        ctx.scoped_model_resolver,
                        init=ctx.is_init,
                        internal_modules=run.internal_modules,
                        producer=ImportProducer("import-enum-member-literal-classes", model),
                        import_ledger=parser._import_ledger,  # noqa: SLF001
                        shared_imports=parser.imports,
                    )
                    or reference.short_name
                )
                parser._graph_builder.name_enum_member_literal_class(data_type, enum_class)  # noqa: SLF001


def apply_generic_base_class(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Add the configured generic base class to the first root module and make top-level target models inherit it.

    An earlier attempt of the run that found an import of the class to fail as the output is imported seeds the
    module it goes to (``ImportCycleSeeds.base_class_module``).
    """
    if not parser.use_generic_base_class or not parser.generic_base_class_config:
        return

    all_target_models: set[DataModel] = set()
    modules_with_targets: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports, bool]] = []
    module_keys: list[ModulePath] = []
    for ctx in run.contexts:
        if target_models := [m for m in ctx.models if m.SUPPORTS_GENERIC_BASE_CLASS and not parser._is_root_model(m)]:  # noqa: SLF001
            modules_with_targets.append((ctx.module, ctx.models, target_models, ctx.imports, ctx.is_init))
            module_keys.append(ctx.module_key)
            all_target_models.update(target_models)

    if not modules_with_targets:
        return

    first_root_module, first_root_models, first_root_target_models, _, _ = modules_with_targets[
        generic_base_class_module(
            [entry[2] for entry in modules_with_targets],
            module_keys,
            parser._import_cycle_seeds.base_class_module,  # noqa: SLF001
        )
    ]
    first_root_file_path = first_root_target_models[0].file_path

    base_class_ref = Reference(path=GENERIC_BASE_CLASS_PATH, name=GENERIC_BASE_CLASS_NAME)

    base_class_model = parser._new_model(  # noqa: SLF001
        parser.data_model_type.create_base_class_model,
        config=parser.generic_base_class_config,
        reference=base_class_ref,
        custom_template_dir=parser.custom_template_dir,
        keyword_only=parser.keyword_only,
        treat_dot_as_module=parser.treat_dot_as_module,
    )

    if base_class_model is None:
        return

    base_class_model.file_path = first_root_file_path
    parser._generic_base_model = base_class_model  # noqa: SLF001
    first_root_models.insert(0, base_class_model)
    parser._graph_builder.register_model(base_class_model)  # noqa: SLF001

    base_class_dt = BaseClassDataType(type=base_class_ref.name, reference=base_class_ref)

    original_base_class = parser.data_model_type.BASE_CLASS
    original_import = Import.from_full_path(original_base_class) if original_base_class else None
    for module, _, target_models, imports, init in modules_with_targets:
        for model in target_models:
            if original_import:  # pragma: no branch
                additional_imports = model._additional_imports  # noqa: SLF001
                model._additional_imports = [i for i in additional_imports if i != original_import]  # noqa: SLF001
            if inherits_one_of(model, all_target_models):
                continue
            if any(bc.reference for bc in model.base_classes):  # pragma: no cover
                parser._graph_builder.set_base_classes(model, [base_class_dt, *model.base_classes])  # noqa: SLF001
            else:
                parser._graph_builder.set_base_classes(model, [base_class_dt])  # noqa: SLF001
        if module != first_root_module and original_import:
            parser._import_ledger.remove(imports, original_import, _GENERIC_BASE_CLASS)  # noqa: SLF001
            parser._import_ledger.append(  # noqa: SLF001
                imports,
                generic_base_class_import(module, first_root_module, base_class_ref.name, init=init),
                _GENERIC_BASE_CLASS,
            )
