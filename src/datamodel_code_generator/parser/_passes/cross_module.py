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
)
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import (
    GraphBuilderHost,
    ModuleScopeView,
    ReferenceExpressionHost,
    RenamedTypesHost,
    ReplacedRootsHost,
    _context_models,
    _generic_base_class_module,
    _get_model_module_name,
    _named_data_types,
    module_members,
    names_left_by_replaced_roots,
    register_reference_import,
    set_reference_expressions,
)
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._passes.plans import FlattenHost, flatten_base_layouts
from datamodel_code_generator.parser._tag_plan import TagPlanHost, iter_struct_tag_decisions
from datamodel_code_generator.reference import Reference

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._passes._state import ModuleContextView, _ConstNarrowing, _RenamedType
    from datamodel_code_generator.types import DataType

__all__ = [
    "CrossModuleHost",
    "CrossModuleRun",
    "TaggedBasesHost",
    "apply_generic_base_class",
    "import_enum_member_literal_classes",
    "import_removed_reuse_canonicals",
    "import_renamed_types",
    "tag_fields_of_tagged_bases",
    "warn_enums_admitting_other_values",
]


class CrossModuleHost(ReferenceExpressionHost, RenamedTypesHost, ReplacedRootsHost, GraphBuilderHost, Protocol):
    """The options, run state and hooks of the parser that the cross-module passes read and write."""

    _generic_base_model: DataModel | None

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


class TaggedBasesHost(CrossModuleHost, FlattenHost, TagPlanHost, Protocol):
    """What tagging the structs below tagged bases reads and writes of the parser."""


class CrossModuleRun(ModuleScopeView, Protocol):
    """What the cross-module passes read and write of the run."""

    @property
    def contexts(self) -> Sequence[ModuleContextView]:
        """The processed modules, in module order."""


_GENERIC_BASE_CLASS: Final = ImportProducer("generic-base-class")


def tag_fields_of_tagged_bases(parser: TaggedBasesHost, run: CrossModuleRun) -> None:
    """Write what becomes of the msgspec structs below tagged bases (``iter_struct_tag_decisions``), and record it.

    The modules have bound the names of the models their fields name by then, so a struct that declares the fields
    of its bases instead names the models of each copied field from its own module, importing those of others.
    """
    contexts = {id(model): ctx for ctx in run.contexts for model in ctx.models}

    def flatten(model: DataModel) -> None:
        own_fields = {id(field) for field in model.fields}
        flatten_base_layouts(parser, model)
        set_reference_expressions(
            parser,
            ctx := contexts[id(model)],
            run,
            model,
            (
                data_type
                for field in model.fields
                if id(field) not in own_fields
                for data_type in field.data_type.all_data_types
            ),
            members=keyed_models(module_members(parser, ctx.models), "module members"),
            producer=ImportProducer("tag-fields-of-tagged-bases", model),
        )

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
            f"The schema at {narrowing.location} admits only its const {narrowing.const!r} of its enum values, but "
            "its values are typed as its enum class, which accepts the other values too.",
            UserWarning,
            stacklevel=2,
        )


def _stale_renamed_import(
    imports: Imports, data_type: DataType, renamed_types: Sequence[_RenamedType]
) -> Import | None:
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
                producer = ImportProducer("import-removed-reuse-canonicals", model)
                parser._import_ledger.remove(ctx.imports, stale_import, producer)  # noqa: SLF001
                set_reference_expressions(parser, ctx, run, model, (data_type,), members=members, producer=producer)


def renamed_types_of(parser: CrossModuleHost, reference: Reference) -> list[_RenamedType] | None:
    """Return the class names that the model ``reference`` names had before it was renamed, or None."""
    return keyed_or_path(reference, parser._renamed_types, parser._renamed_type_paths)  # noqa: SLF001


def import_renamed_types(parser: CrossModuleHost, run: CrossModuleRun) -> None:
    """Import a type by its new name where a module processed before the type's module imported the old one.

    A field of the type's module can hide the type, which then gets a new name after modules that come first
    built their imports of it, and the ``parent`` strategy of root-model collapse gives an inner model the name
    and path of the root model it replaces. Every name the type had is recorded, as a module processed between two
    renames imported the name in between. The stale import is the one added under the type's path at that name.
    It releases the name it binds for the new one, found by name, as neither the import of a package ``__init__``
    nor a ``from .`` path leads back to the path it was added under.
    """
    if not parser._renamed_types:  # noqa: SLF001
        return
    for ctx in run.contexts:
        members = module_members(parser, ctx.models)
        keyed_members = keyed_models(members, "module members")
        stale_users = [
            (data_type, model, import_)
            for model in members
            for data_type in _named_data_types(model)
            if (reference := data_type.reference) is not None
            and (renamed_types := renamed_types_of(parser, reference)) is not None
            and keyed_members.key_of(reference) not in keyed_members.models
            and (import_ := _stale_renamed_import(ctx.imports, data_type, renamed_types)) is not None
        ]
        if not stale_users:
            continue
        for _, model, import_ in stale_users:
            parser._import_ledger.remove(ctx.imports, import_, ImportProducer("import-renamed-types", model))  # noqa: SLF001
        parser._import_ledger.release_names(  # noqa: SLF001
            ctx.imports,
            ctx.scoped_model_resolver,
            {import_.binding_name for *_, import_ in stale_users}.union(
                names_left_by_replaced_roots(parser, ctx.models, keyed_members)
            ),
        )
        for data_type, model, _ in stale_users:
            set_reference_expressions(
                parser,
                ctx,
                run,
                model,
                (data_type,),
                members=None,
                producer=ImportProducer("import-renamed-types", model),
            )


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
                        treat_dot_as_module=parser.treat_dot_as_module,
                        import_ledger=parser._import_ledger,  # noqa: SLF001
                    )
                    or reference.short_name
                )
                data_type.enum_member_literals = [(enum_class, member) for _, member in data_type.enum_member_literals]


def apply_generic_base_class(parser: CrossModuleHost, run: CrossModuleRun) -> None:  # noqa: PLR0912
    """Add the configured generic base class to the first root module and make top-level target models inherit it."""
    if not parser.use_generic_base_class or not parser.generic_base_class_config:
        return

    all_target_models: set[DataModel] = set()
    modules_with_targets: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports]] = []
    for ctx in run.contexts:
        if target_models := [m for m in ctx.models if m.SUPPORTS_GENERIC_BASE_CLASS and not parser._is_root_model(m)]:  # noqa: SLF001
            modules_with_targets.append((ctx.module, ctx.models, target_models, ctx.imports))
            all_target_models.update(target_models)

    if not modules_with_targets:
        return

    def has_target_model_parent(model: DataModel) -> bool:
        return any(ref.source in all_target_models for bc in model.base_classes if (ref := bc.reference))

    root_modules = [entry for entry in modules_with_targets if not all(map(has_target_model_parent, entry[2]))]
    if not root_modules:  # pragma: no cover
        root_modules = [modules_with_targets[0]]

    first_root_module, first_root_models, first_root_target_models, _ = root_modules[0]
    first_root_file_path = first_root_target_models[0].file_path

    base_class_ref = Reference(path=GENERIC_BASE_CLASS_PATH, name=GENERIC_BASE_CLASS_NAME)

    base_class_model = parser.data_model_type.create_base_class_model(
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
    for module, _, target_models, imports in modules_with_targets:
        for model in target_models:
            if original_import:  # pragma: no branch
                additional_imports = model._additional_imports  # noqa: SLF001
                model._additional_imports = [i for i in additional_imports if i != original_import]  # noqa: SLF001
            if has_target_model_parent(model):
                continue
            if any(bc.reference for bc in model.base_classes):  # pragma: no cover
                parser._graph_builder.set_base_classes(model, [base_class_dt, *model.base_classes])  # noqa: SLF001
            else:
                parser._graph_builder.set_base_classes(model, [base_class_dt])  # noqa: SLF001
        if module != first_root_module and original_import:
            parser._import_ledger.remove(imports, original_import, _GENERIC_BASE_CLASS)  # noqa: SLF001
            parser._import_ledger.append(  # noqa: SLF001
                imports,
                Import(from_=_generic_base_class_module(module, first_root_module), import_=base_class_ref.name),
                _GENERIC_BASE_CLASS,
            )
