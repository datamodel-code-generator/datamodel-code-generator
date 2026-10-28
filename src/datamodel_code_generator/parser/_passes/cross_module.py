"""The cross-module passes (L6a): what the processed modules settle once every module is processed.

Each pass is a function of the parser and the run, called from the pipeline tables of ``parser/_passes/_tables.py``;
the parser comes in through ``CrossModuleHost``, which ``Parser`` satisfies structurally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from typing_extensions import Protocol

from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, graph_answer, walk_graph
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model._decisions.tag_value import (
    enum_tag_as_values,
    field_tag,
    redeclared_tag_field,
)
from datamodel_code_generator.model.base import (
    GENERIC_BASE_CLASS_NAME,
    GENERIC_BASE_CLASS_PATH,
    BaseClassDataType,
    DataModel,
    _find_base_classes,
    _keyed_answer,
    _model_key_of_model,
)
from datamodel_code_generator.model.enum import Enum
from datamodel_code_generator.parser._graph_ids import keyed_models
from datamodel_code_generator.parser._imports_plan import ImportProducer
from datamodel_code_generator.parser._passes._shared import (
    ReferenceExpressionHost,
    ReplacedRootsHost,
    _bases_keep_instance_field,
    _context_models,
    _FieldTagFacts,
    _generic_base_class_module,
    _get_data_type_target_full_name,
    _get_model_module_name,
    _iter_discriminator_data_types,
    _named_data_types,
    module_members,
    names_left_by_replaced_roots,
    register_reference_import,
    set_reference_expression,
)
from datamodel_code_generator.parser._passes._warnings import warn_as_parser_base
from datamodel_code_generator.parser._passes.plans import FlattenHost, flatten_base_layouts
from datamodel_code_generator.reference import Reference

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from pathlib import Path

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Imports
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._passes._state import (
        ModuleContextFields,
        ModuleContextView,
        ModulePath,
        _ConstNarrowing,
        _RenamedType,
    )
    from datamodel_code_generator.parser.generation import GraphBuilder
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


class CrossModuleHost(ReferenceExpressionHost, ReplacedRootsHost, Protocol):
    """The options, run state and hooks of the parser that the cross-module passes read and write."""

    _generic_base_model: DataModel | None

    @property
    def _enum_admits_other_values(self) -> list[tuple[DataModelFieldBase, _ConstNarrowing]]:
        """The values typed by an enum whose const admits only one of its values, with that const."""

    @property
    def _graph_builder(self) -> GraphBuilder:
        """The builder that changes the model graph through the generation store."""

    @property
    def _removed_reuse_users(self) -> dict[int, tuple[DataType, list[str]]]:
        """The data types whose duplicate reuse removed, by id, with the paths they named."""

    @property
    def _renamed_type_paths(self) -> dict[str, list[_RenamedType]]:
        """The class names renamed types had, by the path imports of them were added under."""

    @property
    def _renamed_types(self) -> dict[ModelKey, list[_RenamedType]]:
        """The class names renamed types had, by model key."""

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


class TaggedBasesHost(CrossModuleHost, FlattenHost, Protocol):
    """What tagging the structs below tagged bases reads and writes of the parser."""


class CrossModuleRun(Protocol):
    """What the cross-module passes read and write of the run."""

    opted_out: list[DataModel]

    @property
    def contexts(self) -> Sequence[ModuleContextView]:
        """The processed modules, in module order."""

    @property
    def internal_modules(self) -> set[ModulePath]:
        """The modules the output writes."""

    @property
    def model_path_to_module_name(self) -> dict[str, str]:
        """The module name of each model, by model path."""


_GENERIC_BASE_CLASS: Final = ImportProducer("generic-base-class")


def _has_cyclic_ancestry(model: DataModel) -> bool:
    """Return whether the base classes of a model, however distant, inherit from one another in a cycle."""
    walk = walk_graph(GraphSnapshot.CYCLIC_ANCESTRY, EdgeKind.BASE, (model,), _model_key_of_model, _find_base_classes)
    return graph_answer(
        GraphSnapshot.CYCLIC_ANCESTRY,
        walk.graph.acyclic_order((EdgeKind.BASE,)) is None,
        lambda: _walks_to_cyclic_ancestry(model),
    )


def _walks_to_cyclic_ancestry(model: DataModel) -> bool:
    """Return whether the base classes of a model inherit from one another in a cycle, by walking them."""
    visiting: dict[int, bool] = {id(model): True}
    stack = [(id(model), iter(_find_base_classes(model)))]
    while stack:
        if (base := next(stack[-1][1], None)) is None:
            visiting[stack.pop()[0]] = False
            continue
        if visiting.get(id(base)):
            return True
        if id(base) not in visiting:
            visiting[id(base)] = True
            stack.append((id(base), iter(_find_base_classes(base))))
    return False


def _redeclared_tag_field(tag_fields: AbstractSet[str], field: DataModelFieldBase) -> str | None:
    """Return the tag field of a tagged base that a field redeclares, by its Python or its source name, or None."""
    return redeclared_tag_field(tag_fields, name=field.name, original_name=field.original_name, alias=field.alias)


def _structs_of_union(data_type: DataType) -> set[int]:
    """Return the ids of the structs that a union lists, through the aliases it names."""
    return {
        id(source)
        for member, _, _ in _iter_discriminator_data_types(data_type.data_types)
        if member.reference
        and isinstance(source := member.reference.source, DataModel)
        and source.REQUIRES_TAGGED_UNION_DISCRIMINATOR
    }


def _structs_sharing_a_union(models: Iterable[DataModel]) -> set[int]:
    """Return the ids of the structs that a union lists beside another struct, which msgspec decodes only if tagged."""
    shared: set[int] = set()
    for model in models:
        for field in model.fields:
            for data_type in field.data_type.all_data_types:
                if data_type.is_union and len(structs := _structs_of_union(data_type)) > 1:
                    shared |= structs
    return shared


def _tag_fields_of_tagged_bases(
    models: Iterable[DataModel], builder: GraphBuilder, flatten: Callable[[DataModel], None]
) -> list[DataModel]:
    """Tag the structs that declare the tag field of a tagged base as a field, which msgspec rejects.

    A struct inherits the tag of its base, so its single literal becomes its own tag, as for a tagged variant, and a
    struct with any other such field opts out of the tag to keep the field. A struct whose tag field a base keeps as an
    instance field opts out too, and its tag field is a field of its own again, as msgspec refuses a tag field that a
    base declares; a struct that reuse made an empty subclass of that base declares the fields of its base itself
    (``flatten``) instead and keeps its tag, as it would without reuse, where a union lists it beside another struct:
    msgspec decodes such a union only if all its structs are tagged. Bases of other modules are tagged by then, as
    every module is processed first, and a base is handled before the structs inheriting from it, as its own tag
    decides theirs. Inheritance cycles are reported later.
    Return the structs that opted out.
    """
    models = list(models)
    pending = {id(model): model for model in models}
    opted_out: list[DataModel] = []
    sharing: set[int] | None = None

    def tag_redeclared_fields(model: DataModel, bases: Sequence[DataModel]) -> None:
        if not (tag_fields := {tag[0] for base in bases if (tag := base.discriminator_tag) is not None}):
            return
        tagged_fields = [
            (field, tag_field, field_tag("legacy-literal-only", _FieldTagFacts(field)))
            for field in model.fields
            if not field.extras.get("is_classvar")
            and (tag_field := _redeclared_tag_field(tag_fields, field)) is not None
        ]
        if len(tags := [(field, name, tag) for field, name, tag in tagged_fields if tag is not None]) != len(
            tagged_fields
        ):
            model.opt_out_of_inherited_tag()
            opted_out.append(model)
            return
        for field in [
            field for field, tag_field, tag in tags if model.apply_discriminator_tag(field, tag_field, tag.value)
        ]:
            builder.set_field_extra(field, "is_classvar", value=True)

    def untag_below_instance_field(model: DataModel) -> None:
        nonlocal sharing
        if (tag := model.discriminator_tag) is None or not _bases_keep_instance_field(model, tag[0]):
            return
        if model.reused_reference is not None:
            if sharing is None:
                sharing = _structs_sharing_a_union(models)
            if id(model) in sharing:
                flatten(model)
                return
        for field in model.fields:
            if field.extras.get("is_classvar") and _redeclared_tag_field({tag[0]}, field) is not None:
                builder.set_field_extras(
                    field, {key: value for key, value in field.extras.items() if key != "is_classvar"}
                )
                if not (field.data_type.literals or field.data_type.enum_member_literals):
                    builder.replace_field_type(field, field.data_type.__class__(literals=[field.extras["const"]]))
        model.opt_out_of_inherited_tag()
        opted_out.append(model)

    def tag_fields_of(model: DataModel) -> None:
        if pending.pop(id(model), None) is None:
            return
        if not model.base_classes or _has_cyclic_ancestry(model):
            return
        bases = _find_base_classes(model)
        for base in bases:
            tag_fields_of(base)
        tag_redeclared_fields(model, bases)
        untag_below_instance_field(model)

    while pending:
        tag_fields_of(next(iter(pending.values())))
    return opted_out


def tag_fields_of_tagged_bases(parser: TaggedBasesHost, run: CrossModuleRun) -> None:
    """Make the fields of tagged msgspec bases tags, and remember the models that opt out.

    The modules have bound the names of the models their fields name by then, so a struct that declares the fields
    of its bases instead names the models of each copied field from its own module, importing those of others.
    """
    contexts = {id(model): ctx for ctx in run.contexts for model in ctx.models}

    def flatten(model: DataModel) -> None:
        own_fields = {id(field) for field in model.fields}
        flatten_base_layouts(parser, model)
        ctx = contexts[id(model)]
        members = keyed_models(module_members(parser, ctx.models), "module members")
        module_name = _get_model_module_name(model, run.model_path_to_module_name)
        for data_type in (
            data_type
            for field in model.fields
            if id(field) not in own_fields
            for data_type in field.data_type.all_data_types
        ):
            if (reference := data_type.reference) is None:
                continue
            set_reference_expression(
                parser,
                data_type,
                module_name,
                _get_data_type_target_full_name(data_type, reference, run.model_path_to_module_name),
                ctx.imports,
                ctx.scoped_model_resolver,
                same_module=members.key_of(reference) in members.models,
                init=ctx.is_init,
                internal_modules=run.internal_modules,
                producer=ImportProducer("tag-fields-of-tagged-bases", model),
            )

    run.opted_out = _tag_fields_of_tagged_bases(
        _context_models(run.contexts),
        parser._graph_builder,  # noqa: SLF001
        flatten,
    )


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


def import_removed_reuse_canonicals(
    parser: CrossModuleHost,
    contexts: Sequence[ModuleContextView],
    *,
    internal_modules: set[ModulePath],
    model_path_to_module_name: dict[str, str],
) -> None:
    """Import the kept model where a module processed earlier imported a duplicate that reuse removed."""
    if not (users := parser._removed_reuse_users):  # noqa: SLF001
        return
    for ctx in contexts:
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
                reference = cast("Reference", data_type.reference)
                set_reference_expression(
                    parser,
                    data_type,
                    _get_model_module_name(model, model_path_to_module_name),
                    _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
                    ctx.imports,
                    ctx.scoped_model_resolver,
                    same_module=members.key_of(reference) in members.models,
                    init=ctx.is_init,
                    internal_modules=internal_modules,
                    producer=producer,
                )


def renamed_types_of(parser: CrossModuleHost, reference: Reference) -> list[_RenamedType] | None:
    """Return the class names that the model ``reference`` names had before it was renamed, or None."""
    by_path = parser._renamed_type_paths.get(reference.path)  # noqa: SLF001
    by_key = by_path if reference.source is None else parser._renamed_types.get(model_key_of(reference))  # noqa: SLF001
    return _keyed_answer("renamed types", by_key, lambda: by_path)


def import_renamed_types(
    parser: CrossModuleHost,
    contexts: Sequence[ModuleContextView],
    *,
    internal_modules: set[ModulePath],
    model_path_to_module_name: dict[str, str],
) -> None:
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
    for ctx in contexts:
        members = module_members(parser, ctx.models)
        keyed_members = keyed_models(members, "module members")
        stale_users = [
            (data_type, reference, model, import_)
            for model in members
            for data_type in _named_data_types(model)
            if (reference := data_type.reference) is not None
            and (renamed_types := renamed_types_of(parser, reference)) is not None
            and keyed_members.key_of(reference) not in keyed_members.models
            and (import_ := _stale_renamed_import(ctx.imports, data_type, renamed_types)) is not None
        ]
        if not stale_users:
            continue
        for _, _, model, import_ in stale_users:
            parser._import_ledger.remove(ctx.imports, import_, ImportProducer("import-renamed-types", model))  # noqa: SLF001
        parser._import_ledger.release_names(  # noqa: SLF001
            ctx.imports,
            ctx.scoped_model_resolver,
            {import_.binding_name for *_, import_ in stale_users}.union(
                names_left_by_replaced_roots(parser, ctx.models, keyed_members)
            ),
        )
        for data_type, reference, model, _ in stale_users:
            set_reference_expression(
                parser,
                data_type,
                _get_model_module_name(model, model_path_to_module_name),
                _get_data_type_target_full_name(data_type, reference, model_path_to_module_name),
                ctx.imports,
                ctx.scoped_model_resolver,
                same_module=False,
                init=ctx.is_init,
                internal_modules=internal_modules,
                producer=ImportProducer("import-renamed-types", model),
            )


def _module_qualified_name(reference: Reference, model_path_to_module_name: Mapping[str, str]) -> str:
    """Return the name of the referenced model qualified by its module, or its short name in the root module."""
    module_name = _get_model_module_name(cast("DataModel", reference.source), model_path_to_module_name)
    return f"{module_name}.{reference.short_name}" if module_name else reference.short_name


def import_enum_member_literal_classes(
    parser: CrossModuleHost,
    contexts: Sequence[ModuleContextView],
    *,
    internal_modules: set[ModulePath],
    model_path_to_module_name: dict[str, str],
) -> None:
    """Name the enum class of each enum member literal as its module writes it, importing it from another module.

    A literal of the enum's own module takes the enum's final name, which a type renamed for a field it hid
    changes after the literal is made.
    """
    for ctx in contexts:
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
                        _get_model_module_name(model, model_path_to_module_name),
                        _module_qualified_name(reference, model_path_to_module_name),
                        ctx.imports,
                        ctx.scoped_model_resolver,
                        init=ctx.is_init,
                        internal_modules=internal_modules,
                        producer=ImportProducer("import-enum-member-literal-classes", model),
                        treat_dot_as_module=parser.treat_dot_as_module,
                        import_ledger=parser._import_ledger,  # noqa: SLF001
                    )
                    or reference.short_name
                )
                data_type.enum_member_literals = [(enum_class, member) for _, member in data_type.enum_member_literals]


def apply_generic_base_class(  # noqa: PLR0912, PLR0914
    parser: CrossModuleHost,
    processed_models: Sequence[ModuleContextFields],
) -> None:
    """Add the configured generic base class to the first root module and make top-level target models inherit it."""
    if not parser.use_generic_base_class or not parser.generic_base_class_config:
        return

    all_target_models: set[DataModel] = set()
    modules_with_targets: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports]] = []

    for module, _mod_key, models, _init, imports, _scoped_model_resolver in processed_models:
        if not models:  # pragma: no cover
            continue

        target_models = [m for m in models if m.SUPPORTS_GENERIC_BASE_CLASS and not parser._is_root_model(m)]  # noqa: SLF001

        if target_models:
            modules_with_targets.append((module, models, target_models, imports))
            all_target_models.update(target_models)

    if not modules_with_targets:
        return

    root_modules: list[tuple[tuple[str, ...], list[DataModel], list[DataModel], Imports]] = []
    for module_entry in modules_with_targets:
        _module, _models, target_models, _imports = module_entry
        has_root_model = False
        for model in target_models:
            parent_refs = [bc.reference for bc in model.base_classes if bc.reference]
            has_target_model_parent = any(ref.source in all_target_models for ref in parent_refs)
            if not has_target_model_parent:
                has_root_model = True
                break
        if has_root_model:
            root_modules.append(module_entry)

    if not root_modules:  # pragma: no cover
        root_modules = [modules_with_targets[0]]

    first_root_module, first_root_models, first_root_target_models, _first_root_imports = root_modules[0]
    first_root_file_path = first_root_target_models[0].file_path if first_root_target_models else None

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
        is_first_root = module == first_root_module
        for model in target_models:
            if original_import:  # pragma: no branch
                additional_imports = model._additional_imports  # noqa: SLF001
                model._additional_imports = [i for i in additional_imports if i != original_import]  # noqa: SLF001
            parent_refs = [bc.reference for bc in model.base_classes if bc.reference]
            has_target_model_parent = any(ref.source in all_target_models for ref in parent_refs)
            if has_target_model_parent:
                pass
            elif parent_refs:  # pragma: no cover
                parser._graph_builder.set_base_classes(model, [base_class_dt, *model.base_classes])  # noqa: SLF001
            else:
                parser._graph_builder.set_base_classes(model, [base_class_dt])  # noqa: SLF001
        if not is_first_root and original_import:
            parser._import_ledger.remove(imports, original_import, _GENERIC_BASE_CLASS)  # noqa: SLF001
            parser._import_ledger.append(  # noqa: SLF001
                imports,
                Import(from_=_generic_base_class_module(module, first_root_module), import_=base_class_ref.name),
                _GENERIC_BASE_CLASS,
            )
