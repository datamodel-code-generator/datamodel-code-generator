"""The tag plan (home H11): the union sites the tag passes read, what they decide there and what they recorded.

A union site is a field whose extras carry a discriminator object naming a property; :func:`iter_union_sites` is the
one match on ``extras["discriminator"]`` that the tag passes share, and each caller passes its own models, so it keeps
its order. The decisions of the L5 tag passes are the functions here: whether a union can be tagged
(:func:`site_validity`), the Python name of its tag field (:func:`site_names`, :func:`shared_site_names`), the tags of
a variant (:func:`tags_of_variant`, :class:`UnionView`), the form each tag field takes
(:func:`iter_tag_field_decisions`), which msgspec unions take struct tags (:func:`tag_msgspec_unions`) and which
``discriminator=`` Pydantic cannot build (:func:`settle_site`, :func:`iter_root_default_unions`), and at L6 what
becomes of the msgspec structs below tagged bases (:func:`iter_struct_tag_decisions`). The passes keep their slots:
they call these where they read the facts before, write the result through the store, in the order they read it, as a
variant may read a tag field that the write for an earlier variant changed, and record it in :class:`TagPlan`, which
holds what the tag passes of one parse recorded; :meth:`TagPlan.reset` forgets it at the start of L4. The class
constants of a backend these decisions read are the rows of :class:`TagCapabilities`. The L3 reuse keys
(:func:`declared_union_tags`) and the L4 layout prediction (:func:`possible_tag_names`) are read here too.

Each pass keeps its slot in the pipeline, so today's timing stays; these rows name it until a decision after the module
loop replaces them:

- ``legacy-decide-in-union-module``: a union is tagged when its module is processed, reading the variants of later
  modules before their reuse, collapse and renames; msgspec unions that a struct of a later module may still tag wait
  in :attr:`TagPlan.deferred_msgspec` while :attr:`TagPlan.defers_related` holds.
- ``legacy-tag-from-earlier-union``: a variant keeps the tags of the first union that tags it, and its own tag includes
  a tag an earlier union wrote (:attr:`TagPlan.first_tags`).
- ``legacy-inline-variants-at-first-ask``: which inline variants take the tag they inherit is decided once, at the
  first collapse or apply that asks, over the parsed models as they are then (:attr:`TagPlan.inline_variants`).
- ``legacy-predicted-tag-names``: msgspec layout flattening at L4 reads a superset of the tag fields L5 writes
  (:func:`possible_tag_names`, with the mapping-path row ``legacy-any-key``).
- ``legacy-collapse-python-name-mode``: the union that ``--collapse-root-models`` inlines as list items is checked by
  the Python names of its variants' tag fields (``python-or-original-name``), where L5 checks a union that writes
  ``discriminator=`` by the names the schema gives them (``source-name``; :data:`ValidityMode`).
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cache
from itertools import chain, islice
from typing import TYPE_CHECKING, Any, Literal, TypeAlias, cast, final

from datamodel_code_generator import Error
from datamodel_code_generator._model_keys import model_key_of
from datamodel_code_generator._records import record
from datamodel_code_generator._reference_graph import EdgeKind, GraphSnapshot, walk_graph
from datamodel_code_generator.model._decisions.tag_value import (
    field_tag,
    is_tag_value,
    mapping_path_keys,
    redeclared_tag_field,
    same_tag,
    same_tags,
    schema_path_keys,
    struct_tag_conflicts,
    tag_key,
    variant_tags,
)
from datamodel_code_generator.model.base import (
    DataModel,
    _find_base_classes,
    _model_key_of_model,
    _ModelKeys,
    linearize_data_models,
)
from datamodel_code_generator.model.enum import Enum, IntEnum, StrEnum
from datamodel_code_generator.parser._passes._shared import (
    _FieldTagFacts,
    _format_tags,
    _get_discriminator_field_value,
    _get_enum_from_base,
    _get_mapping_discriminator_values,
    _get_schema_path,
    _is_discriminator_container,
    _is_discriminator_wrapper,
    _iter_discriminator_data_types,
    _keeps_instance_field,
    _tag_field_matches,
    enum_discriminator_literal,
    enum_discriminator_members,
)
from datamodel_code_generator.parser._passes.render_prep import validates_structured_default
from datamodel_code_generator.reference import SPECIAL_PATH_MARKER
from datamodel_code_generator.types import ANY, NONE

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Mapping, Sequence
    from collections.abc import Set as AbstractSet

    from datamodel_code_generator._internal_utils import HashableComparable
    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model._decisions.tag_value import FieldTag, TagFieldMatch, VariantTagSource
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.parser._passes._shared import DiscriminatorValue
    from datamodel_code_generator.reference import ModelResolver, ModelType
    from datamodel_code_generator.types import DataType

__all__ = [
    "DiscriminatorTag",
    "FieldNamer",
    "InlineVariantTags",
    "KeptTagField",
    "MsgspecOutcome",
    "MsgspecUnion",
    "RetypedTagField",
    "SiteNames",
    "SiteStatus",
    "StructTagDecision",
    "StructTagKind",
    "TagCapabilities",
    "TagPlan",
    "UnionSite",
    "UnionTagDecision",
    "UnionView",
    "ValidityMode",
    "VariantTags",
    "appended_takes_struct_tag",
    "declared_union_tags",
    "field_namer",
    "has_any_variant",
    "has_reused_variant",
    "iter_root_default_unions",
    "iter_struct_tag_decisions",
    "iter_tag_field_decisions",
    "iter_union_sites",
    "mapped_struct_tag",
    "nested_variant_tags",
    "possible_tag_names",
    "settle_site",
    "shared_site_names",
    "site_mode",
    "site_names",
    "site_validity",
    "tag_capabilities",
    "tag_msgspec_unions",
    "tags_of_variant",
    "union_site",
]

InlineVariantTags: TypeAlias = "Mapping[int, tuple[tuple[DataModelFieldBase, DiscriminatorValue], ...]]"
"""The fields with one tag of each inline variant that may take the tag it inherits, by variant id, with their tags."""

DiscriminatorTag: TypeAlias = "tuple[DataModel, DataModelFieldBase, DiscriminatorValue | None]"
"""A msgspec struct a union tags, its tag field and the tag."""

FieldNamer: TypeAlias = Callable[[str, str | None], tuple[str, str | list[str] | None]]
"""The Python name and alias a property takes in the class named, or in any class for None."""

ValidityMode: TypeAlias = Literal["source-name", "python-or-original-name"]
"""Which names a union's variants are checked by: those the schema gives their tag fields, or their Python names."""

SiteStatus: TypeAlias = Literal[
    "tagged", "dropped-any", "dropped-invalid", "dropped-lone-untagged", "dropped-root-default"
]
"""Whether a union keeps its discriminator, else why it is dropped.

* ``dropped-any``: a variant admits any value.
* ``dropped-invalid``: a variant is no model that takes a tag, has no tag the backend reads as one, or shares a tag.
* ``dropped-lone-untagged``: the one variant a ``$ref`` names, or one its root models reach, has no tag Pydantic reads.
* ``dropped-root-default``: a variant is a root model, or a type alias, whose root has a default.
"""

MsgspecOutcome: TypeAlias = Literal["applied", "deferred", "untaggable", "related"]
"""What became of a msgspec union's struct tags.

* ``applied``: its structs take them.
* ``deferred``: a struct a union of a later module may tag keeps the tag field as an instance field, so it waits.
* ``untaggable``: a struct cannot take exactly one tag for it; its variants are left untagged.
* ``related``: a base or derived struct keeps the tag field as an instance field; its variants are left untagged.
"""

StructTagKind: TypeAlias = Literal["tagged-base", "opted-out", "flattened"]
"""What L6 decides for a msgspec struct below tagged bases (:class:`StructTagDecision`).

* ``tagged-base``: the fields that redeclare the tag field of a base with one literal become its tags.
* ``opted-out``: it opts out of the tag it inherits.
* ``flattened``: it declares the fields of its bases itself and keeps its tag.
"""


@final
@record
class TagCapabilities:
    """The class constants of a backend that the tag decisions read, one row each.

    A variant's rows come from its own class, which need not be the output's (an Enum or a root model of the run), and
    the rows of a union site from its holder and field classes.
    """

    variant_takes_tag: bool
    """A model of the class takes a tag field (``DataModel.SUPPORTS_DISCRIMINATOR``)."""
    writes_argument: bool
    """A union field of the class writes ``discriminator=``, which Pydantic builds and checks
    (``DataModelFieldBase.SUPPORTS_DISCRIMINATOR``)."""
    struct_tags: bool
    """Unions are tagged by msgspec struct tags (``REQUIRES_TAGGED_UNION_DISCRIMINATOR``)."""
    bool_tags: bool
    """A bool is a tag value (``SUPPORTS_BOOLEAN_LITERAL``)."""
    literal_holds_members: bool
    """A ``Literal`` holds an Enum member (``_LITERAL_HOLDS_ENUM_MEMBERS``)."""
    flattens_layouts: bool
    """Models whose bases have conflicting layouts declare every field (``FLATTENS_CONFLICTING_BASE_LAYOUTS``)."""


@cache
def tag_capabilities(model_type: type[DataModel], field_type: type[DataModelFieldBase]) -> TagCapabilities:
    """Return the rows of a model class and a field class, read once per pair."""
    return TagCapabilities(
        variant_takes_tag=model_type.SUPPORTS_DISCRIMINATOR,
        writes_argument=field_type.SUPPORTS_DISCRIMINATOR,
        struct_tags=model_type.REQUIRES_TAGGED_UNION_DISCRIMINATOR,
        bool_tags=model_type.SUPPORTS_BOOLEAN_LITERAL,
        literal_holds_members=model_type._LITERAL_HOLDS_ENUM_MEMBERS,  # noqa: SLF001
        flattens_layouts=model_type.FLATTENS_CONFLICTING_BASE_LAYOUTS,
    )


def site_mode(*, writes_argument: bool) -> ValidityMode:
    """Return the names a union is checked by: those of the schema where it writes ``discriminator=``."""
    return "source-name" if writes_argument else "python-or-original-name"


@final
@record(eq=False)
class UnionSite:
    """A field whose discriminator object names a property: the holder, the field and the discriminator."""

    model: DataModel
    field: DataModelFieldBase
    property_name: str
    """The property the discriminator names, as the schema writes it until L5 writes the Python name in its place."""
    discriminator: Mapping[str, Any]

    @property
    def mapping(self) -> dict[str, str]:
        """The mapping of the discriminator, empty where it declares none."""
        return self.discriminator.get("mapping", {})


def union_site(model: DataModel, field: DataModelFieldBase) -> UnionSite | None:
    """Return the site a field of a model is, or None where its extras carry no discriminator naming a property."""
    match field.extras.get("discriminator"):
        case {"propertyName": str() as property_name} as discriminator if property_name:
            return UnionSite(model, field, property_name, discriminator)
    return None


def iter_union_sites(models: Iterable[DataModel]) -> Iterator[UnionSite]:
    """Yield the union sites of the models, field by field in the order of ``models``."""
    for model in models:
        for field_ in model.fields:
            if (site := union_site(model, field_)) is not None:
                yield site


def field_namer(resolver: ModelResolver, model_type: ModelType) -> FieldNamer:
    """Return the Python name and alias of a property by the parse's resolver (``get_valid_field_name_and_alias``)."""

    def name(property_name: str, class_name: str | None) -> tuple[str, str | list[str] | None]:
        return resolver.get_valid_field_name_and_alias(
            field_name=property_name, model_type=model_type, class_name=class_name
        )

    return name


@final
@record(eq=False)
class SiteNames:
    """The property a union's discriminator names, the Python name of its tag field and the alias the field takes."""

    property_name: str
    field_name: str
    alias: str | list[str] | None


def site_names(property_name: str, namer: FieldNamer) -> SiteNames:
    """Return the names a union's property takes in any class."""
    return SiteNames(property_name, *namer(property_name, None))


def shared_site_names(names: SiteNames, data_types: Iterable[DataType], namer: FieldNamer) -> SiteNames:
    """Return the names of a union that writes ``discriminator=``: the one Python name its variants give the property.

    A variant names it by its tag field, else as the property resolves in its class; the wire name is then the alias
    the field takes, unless it has one. Variants that name it otherwise are an error, as ``discriminator=`` names one
    field.
    """
    property_name, default_name = names.property_name, names.field_name
    common_name: str | None = None
    for data_type, _, _ in _iter_discriminator_data_types(data_types):
        if not data_type.reference:
            continue
        variant = cast("DataModel", data_type.reference.source)
        for variant_field in chain(variant.fields, variant.iter_all_fields()):
            if _tag_field_matches(
                variant_field, "original-or-unaliased-python-name", property_name, python_name=default_name
            ):
                variant_name = variant_field.name
                break
        else:
            variant_name, _ = namer(property_name, variant.class_name)
        if common_name is not None and common_name != variant_name:
            msg = (
                f"Discriminator {property_name!r} resolves to different field names "
                f"{common_name!r} and {variant_name!r}; use the same alias for all variants."
            )
            raise Error(msg)
        common_name = variant_name
    field_name = common_name or default_name
    alias = property_name if field_name != property_name and names.alias is None else names.alias
    return SiteNames(property_name, field_name, alias)


def _iter_discriminator_fields(
    fields: Iterable[DataModelFieldBase], field_name: str, *, field_name_mode: TagFieldMatch
) -> Iterator[tuple[DataModelFieldBase, DiscriminatorValue]]:
    """Yield the fields of the discriminator's name that carry one discriminator value, with that value."""
    for field_ in fields:
        if (
            _tag_field_matches(field_, field_name_mode, field_name)
            and (value := _get_discriminator_field_value(field_)) is not None
        ):
            yield field_, value


def _find_discriminator_value(
    fields: Iterable[DataModelFieldBase], field_name: str, *, field_name_mode: TagFieldMatch
) -> DiscriminatorValue | None:
    return next(
        (value for _, value in _iter_discriminator_fields(fields, field_name, field_name_mode=field_name_mode)), None
    )


def _find_reused_discriminator_value(
    variant: DataModel, field_name: str, *, field_name_mode: TagFieldMatch
) -> DiscriminatorValue | None:
    """Return the tag that the schema of a model gives it when reuse replaced it with an empty subclass.

    The base class the subclass inherits from can carry the tag another union wrote, which is not its own.
    """
    reused = variant.reused_reference
    if reused is None or not isinstance(source := reused.source, DataModel):
        return None
    return _find_discriminator_value(source.fields, field_name, field_name_mode=field_name_mode)


def tags_of_variant(  # noqa: PLR0913
    source: VariantTagSource,
    variant: DataModel,
    field_name: str,
    mapping: dict[str, str],
    *,
    inline_variants: InlineVariantTags,
    mode: TagFieldMatch,
) -> list[DiscriminatorValue]:
    """Return the tags of a variant (``variant_tags``) from its tag fields, the mapping and its schema path."""
    return [*variant_tags(source, _VariantTagFacts(variant, field_name, mapping, mode, inline_variants)).values]


@final
@record
class _VariantTagFacts:
    """The facts of a variant that ``variant_tags`` reads, from its fields, the mapping and its schema path."""

    model: DataModel
    field_name: str
    mapping: dict[str, str]
    field_name_mode: TagFieldMatch
    inline_models: InlineVariantTags

    def own_tag(self) -> DiscriminatorValue | None:
        """Return the tag of the variant's own tag field."""
        return _find_discriminator_value(self.model.fields, self.field_name, field_name_mode=self.field_name_mode)

    def reused_tag(self) -> DiscriminatorValue | None:
        """Return the tag of the model reuse replaced with the variant."""
        return _find_reused_discriminator_value(self.model, self.field_name, field_name_mode=self.field_name_mode)

    def inherited_tag(self) -> DiscriminatorValue | None:
        """Return the tag of a tag field the variant inherits."""
        return _find_discriminator_value(
            self.model.iter_all_fields(), self.field_name, field_name_mode=self.field_name_mode
        )

    def inline_tag(self) -> DiscriminatorValue | None:
        """Return the tag an inline variant inherits as ``TagPlan.inline_variant_tags`` read it, where it takes it."""
        if (tag_fields := self.inline_models.get(id(self.model))) is None or SPECIAL_PATH_MARKER not in (
            self.path_segment()
        ):
            return None
        return next(
            (
                value
                for field_, value in tag_fields
                if _tag_field_matches(field_, self.field_name_mode, self.field_name)
            ),
            None,
        )

    def mapped_tags(self) -> list[str]:
        """Return the mapping keys naming the variant or its bases."""
        return _get_mapping_discriminator_values(self.model, self.mapping)

    def path_segment(self) -> str:
        """Return the last segment of the variant's schema path."""
        return _get_schema_path(self.model).split("/")[-1]


@final
@record(eq=False)
class UnionView:
    """What a union's variant tags are read by: its names, its mapping, a field match and the inline variants."""

    names: SiteNames
    mapping: dict[str, str]
    mode: TagFieldMatch
    """``python-name`` where the union writes ``discriminator=``, else ``python-or-original-name``."""
    inline_variants: InlineVariantTags

    def tags(
        self, source: VariantTagSource, variant: DataModel, mapping: dict[str, str] | None = None
    ) -> list[DiscriminatorValue]:
        """Return the tags of a variant by the union's mapping, or by ``mapping`` (``tags_of_variant``)."""
        return tags_of_variant(
            source,
            variant,
            self.names.field_name,
            self.mapping if mapping is None else mapping,
            inline_variants=self.inline_variants,
            mode=self.mode,
        )


def _is_any_variant(data_type: DataType) -> bool:
    return data_type.type == ANY or (
        not data_type.reference and not data_type.data_types and not data_type.literals and not data_type.type
    )


def has_any_variant(data_types: Iterable[DataType]) -> bool:
    """Return whether a union lists a variant that admits any value, which Pydantic rejects in a tagged union.

    ``--collapse-root-models`` asks it before it asks for the inline variants, which the first ask decides.
    """
    return any(map(_is_any_variant, data_types))


def _find_tag_enum(model: DataModel, field_name: str, tag_name: str, *, use_field_name: bool) -> Enum | None:
    """Return the Enum typing a variant's tag field, of the field itself or else of its bases."""
    return next(
        (
            enum_source
            for field_ in model.fields
            if _tag_field_matches(field_, "source-name" if use_field_name else "python-or-original-name", tag_name)
            and (enum_source := field_.data_type.find_source(Enum))
        ),
        None,
    ) or _get_enum_from_base(model, field_name, use_field_name=use_field_name)


def _is_enum_member_tag(
    model: DataModel, field_name: str, tag_name: str, value: DiscriminatorValue, *, use_field_name: bool
) -> bool:
    """Return whether the Enum typing a variant's tag field has a member for ``value``, written as that member."""
    enum_source = _find_tag_enum(model, field_name, tag_name, use_field_name=use_field_name)
    return enum_source is not None and enum_source.find_member(value, coerce_strings=True) is not None


def _members_equal_values(enum_source: Enum) -> bool:
    """Return whether the members of an Enum equal their values at runtime, as Pydantic compares discriminator choices.

    So do the members of an ``IntEnum``, a ``StrEnum`` and an Enum that subclasses its value type; a member of a plain
    Enum equals only itself.
    """
    return isinstance(enum_source, (IntEnum, StrEnum)) or any(
        base.reference is None and base.import_ is None for base in enum_source.base_classes
    )


def _written_enum_member_keys(
    model: DataModel, field_name: str, tag_name: str, values: list[DiscriminatorValue], *, use_field_name: bool
) -> list[object]:
    """Return the keys of the choices that the members a variant is written with make (``enum_discriminator_members``).

    A member is its value where members equal their values, else only itself (its Enum and its value). A variant tagged
    by no member takes every member, so its tags are their values whatever the Enum compares. A tag field that already
    is the one ``Literal`` of the variant's tag is kept as it is.
    """
    literal_kept = any(
        _tag_field_matches(field_, "source-name" if use_field_name else "python-or-original-name", tag_name)
        and len(literals := field_.data_type.literals) == 1
        and same_tag(literals[0], values[0], "legacy-python-equality")
        for field_ in model.fields
    )
    if (
        literal_kept
        or (enum_source := _find_tag_enum(model, field_name, tag_name, use_field_name=use_field_name)) is None
    ):
        return []
    tags = enum_discriminator_members(enum_source, values)
    values_of_members = [tag_key(member.value, "legacy-python-equality") for member in tags.members]
    keys: list[object] = (
        values_of_members if _members_equal_values(enum_source) else [(id(enum_source), v) for v in values_of_members]
    )
    return [*keys, *values_of_members] if tags.rule == "all-members" else keys


def site_validity(  # noqa: PLR0913
    data_types: Sequence[DataType],
    names: SiteNames,
    mapping: dict[str, str],
    *,
    field_type: type[DataModelFieldBase],
    mode: ValidityMode,
    inline_variants: InlineVariantTags,
    enum_member_tags: bool,
) -> SiteStatus:
    """Return whether a union's variants let it be tagged (``tagged``), else why its discriminator is dropped.

    Each variant is a model that takes a tag and has tags the backend reads as tags (or, with ``enum_member_tags``,
    that members of the Enum typing its tag field write), and no two variants share one. The variants' tag fields are
    found by the names ``mode`` gives (``ValidityMode``).
    """
    if has_any_variant(data_types):  # pragma: no cover
        return "dropped-any"
    use_field_name = mode == "source-name"
    field_name = names.field_name
    tag_name = names.property_name if use_field_name else field_name
    owners: dict[object, int] = {}
    for data_type, can_update_discriminator, owner in _iter_discriminator_data_types(data_types):
        if not data_type.reference and data_type.type == NONE:
            continue
        if _is_discriminator_container(data_type) or not data_type.reference:
            return "dropped-invalid"
        variant = data_type.reference.source
        if (
            not isinstance(variant, DataModel)
            or not tag_capabilities(type(variant), field_type).variant_takes_tag
            or _is_discriminator_wrapper(variant)
        ):
            return "dropped-invalid"
        capabilities = tag_capabilities(type(variant), field_type)
        values = tags_of_variant(
            "variant" if can_update_discriminator else "nested",
            variant,
            tag_name,
            mapping,
            inline_variants=inline_variants,
            mode=mode,
        )
        if not values or not all(
            is_tag_value(value, bool_literals=capabilities.bool_tags)
            or (
                enum_member_tags
                and not capabilities.struct_tags
                and _is_enum_member_tag(variant, field_name, tag_name, value, use_field_name=use_field_name)
            )
            for value in values
        ):
            return "dropped-invalid"
        keys = [tag_key(value, "legacy-python-equality") for value in values]
        if enum_member_tags and can_update_discriminator and not capabilities.struct_tags:
            keys += _written_enum_member_keys(variant, field_name, tag_name, values, use_field_name=use_field_name)
        for key in keys:
            if (previous_owner := owners.get(key)) is not None and previous_owner != owner:
                return "dropped-invalid"
            owners[key] = owner
    return "tagged"


def has_reused_variant(data_types: Iterable[DataType]) -> bool:
    """Return whether reuse replaced a variant of a union with an empty subclass."""
    return any(
        isinstance(source := data_type.reference.source, DataModel) and source.reused_reference is not None
        for data_type in data_types
        if data_type.reference
    )


def _iter_nested_discriminator_variants(
    data_types: Iterable[DataType], field_name: str, *, field_name_mode: ValidityMode
) -> Iterator[tuple[DataType, DataModelFieldBase, Sequence[DiscriminatorValue]]]:
    """Yield the variants that nested choices give a union, with their tag fields and tag values.

    Such a variant keeps the tag it declares or inherits, so the field giving it is its own one, else the inherited
    one, which the empty subclass of a reused model also reads. A union's lone variant is not one of them: its model
    keeps the tag field it writes, and :func:`settle_site` drops ``discriminator=`` where that field is no tag Pydantic
    reads.
    """
    for data_type, can_update, _ in _iter_discriminator_data_types(data_types):
        source = data_type.reference.source if data_type.reference else None
        if can_update or not isinstance(source, DataModel):
            continue
        for tag_field, value in islice(
            _iter_discriminator_fields(
                chain(source.fields, source.iter_all_fields()), field_name, field_name_mode=field_name_mode
            ),
            1,
        ):
            yield data_type, tag_field, (value,)


def _is_enum_tag(tag_field: DataModelFieldBase) -> bool:
    """Return whether a variant's tag field is typed by its Enum, which Pydantic does not read as a ``Literal``."""
    return not tag_field.data_type.literals and tag_field.data_type.find_source(Enum) is not None


def nested_variant_tags(
    data_types: Iterable[DataType], names: SiteNames, *, writes_argument: bool, enum_values: bool
) -> tuple[list[DataType], list[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]]]:
    """Return the variants nested choices give a union whose tag fields L5 writes again, and the tag fields of the rest.

    A union that writes ``discriminator=`` reads an Enum-typed tag field as no ``Literal``, so such a variant has its
    tag field written like a listed variant, unless the tags are Enum values; the others keep their tag fields.
    """
    enum_tagged: list[DataType] = []
    tag_fields: list[tuple[DataModelFieldBase, Sequence[DiscriminatorValue]]] = []
    for data_type, tag_field, values in _iter_nested_discriminator_variants(
        data_types,
        names.property_name if writes_argument else names.field_name,
        field_name_mode=site_mode(writes_argument=writes_argument),
    ):
        if writes_argument and not enum_values and _is_enum_tag(tag_field):
            enum_tagged.append(data_type)
        else:
            tag_fields.append((tag_field, values))
    return enum_tagged, tag_fields


@final
@record(eq=False)
class KeptTagField:
    """A tag field that holds the variant's tag already: as its one ``Literal`` (``kept-literal``) or its const."""

    field: DataModelFieldBase
    form: Literal["kept-literal", "const"]
    tag: DiscriminatorValue


@final
@record(eq=False)
class RetypedTagField:
    """A tag field whose type L5 writes for the variant's tags, by the Enum typing it, with the variant's first tag."""

    field: DataModelFieldBase
    first_tag: DiscriminatorValue | None
    enum_source: Enum | None


def iter_tag_field_decisions(
    variant: DataModel,
    view: UnionView,
    values: list[DiscriminatorValue],
    *,
    const_tags: bool,
    enum_values: bool,
) -> Iterator[KeptTagField | RetypedTagField]:
    """Yield the form each tag field of a variant takes, as the pass asks for the next one after writing the last.

    A field whose one ``Literal`` is the variant's tag keeps it, as does, with ``const_tags``, a msgspec field whose
    const is; either ends the search. Any other tag field gets its type written, by its Enum or, for Enum values, by
    the Enum of the field its base declares.
    """
    for tag_field in variant.fields:
        if not _tag_field_matches(tag_field, view.mode, view.names.field_name):
            continue
        expected_value = values[0] if values else None
        if len(literals := tag_field.data_type.literals) == 1 and same_tag(
            literal := literals[0], expected_value, "legacy-python-equality"
        ):
            yield KeptTagField(tag_field, "kept-literal", literal)
            return
        if (
            const_tags
            and (const_value := tag_field.extras.get("const")) is not None
            and same_tag(const_value, expected_value, "legacy-python-equality")
        ):
            yield KeptTagField(tag_field, "const", const_value)
            return
        enum_source = tag_field.data_type.find_source(Enum)
        if enum_values:
            enum_source = enum_source or _get_enum_from_base(
                variant, view.names.field_name, use_field_name=view.mode == "python-name"
            )
        yield RetypedTagField(tag_field, expected_value, enum_source)


def mapped_struct_tag(values: list[DiscriminatorValue], enum_source: Enum | None) -> DiscriminatorValue | None:
    """Return the wire tag of a variant whose discriminator resolves to exactly one value."""
    if len(values) != 1:
        return None
    return enum_discriminator_literal(enum_source, values[0]) if enum_source else values[0]


def appended_takes_struct_tag(
    variant: DataModel, view: UnionView, *, union_size: int, tagged_unions: bool, struct_tags: bool
) -> bool:
    """Return whether the tag field appended to a msgspec variant is its struct tag.

    It is under ``--use-msgspec-tagged-unions``, and else where reuse replaced the variant of a union of more than one
    variant whose schema tags it.
    """
    return struct_tags and (
        tagged_unions
        or (
            union_size > 1
            and _find_reused_discriminator_value(variant, view.names.field_name, field_name_mode=view.mode) is not None
        )
    )


def _iter_mapping_discriminator_values(discriminator_model: DataModel, mapping: dict[str, str]) -> Iterator[str]:
    """Yield the mapping values that select a variant, computed when the first one is asked for."""
    yield from _get_mapping_discriminator_values(discriminator_model, mapping)


def _iter_derived_fields(
    model: DataModel, derived_models: Mapping[ModelKey, list[DataModel]], visited: _ModelKeys
) -> Iterator[DataModelFieldBase]:
    """Yield the fields that the classes derived from the model declare, each class once."""
    for derived_model in derived_models.get(model_key_of(model.reference), ()):
        if not visited.first(derived_model):
            continue
        yield from derived_model.fields
        yield from _iter_derived_fields(derived_model, derived_models, visited)


def _has_related_instance_field(
    model: DataModel,
    source_name: str,
    tagged_fields: set[int],
    derived_models: Mapping[ModelKey, list[DataModel]],
    *,
    flattens_reused: bool,
) -> bool:
    """Return whether a base or derived struct keeps an instance field of that name once the unions are tagged.

    With ``flattens_reused``, the base of a model that reuse made an empty subclass does not count: the model declares
    the fields of its base when it is tagged beside other structs (``tag_fields_of_tagged_bases``).
    """
    return any(
        id(field_) not in tagged_fields and _keeps_instance_field(field_, source_name)
        for field_ in chain(
            (
                field_
                for base_model in (
                    () if flattens_reused and model.reused_reference is not None else _find_base_classes(model)
                )
                for field_ in base_model.iter_all_fields()
            ),
            _iter_derived_fields(model, derived_models, _ModelKeys()),
        )
    )


@dataclass(slots=True)
class UnionTagDecision:
    """What L5 decided at a union site: its names, whether it keeps its discriminator and its msgspec struct tags."""

    site: UnionSite
    names: SiteNames | None
    """The final names of the union, None where the end of L5 drops the discriminator of a site L5 did not see."""
    status: SiteStatus
    msgspec: MsgspecOutcome | None = None


@final
@record(eq=False)
class MsgspecUnion:
    """A msgspec union to tag: its decision, the tags of its structs and the ``tag_field``."""

    decision: UnionTagDecision
    tags: list[DiscriminatorTag]
    tag_field: str


def tag_msgspec_unions(
    unions: list[MsgspecUnion],
    derived_models: Mapping[ModelKey, list[DataModel]],
    *,
    defers_related: bool,
) -> list[tuple[MsgspecUnion, MsgspecOutcome, str]]:
    """Return what becomes of each msgspec union's struct tags, whatever the order the unions are declared in, and why.

    msgspec refuses a tag field that a base or derived struct keeps as an instance field, so unions are left out
    until the remaining ones agree. A struct that two tag fields claim keeps the first one. While other modules
    remain (``defers_related``), the unions left out for such a struct wait, as a union of a later module may tag it.
    """
    related: dict[int, list[str]] = {}
    while True:
        tagged_fields: set[int] = set()
        untaggable: dict[int, list[str]] = {}
        for index, union in enumerate(unions):
            if index in related:
                continue
            mapping = union.decision.site.mapping
            if names := [
                model.class_name
                for model, tag_field, value in union.tags
                if struct_tag_conflicts(value, _iter_mapping_discriminator_values(model, mapping))
                or any(
                    other is not tag_field and (other.extras.get("is_classvar") or id(other) in tagged_fields)
                    for other in model.fields
                )
            ]:
                untaggable[index] = names
            else:
                tagged_fields.update(id(tag_field) for _, tag_field, _ in union.tags)
        if not (
            found := {
                index: names
                for index, union in enumerate(unions)
                if index not in related
                and index not in untaggable
                and (
                    names := [
                        model.class_name
                        for model, _, _ in union.tags
                        if _has_related_instance_field(
                            model, union.tag_field, tagged_fields, derived_models, flattens_reused=len(union.tags) > 1
                        )
                    ]
                )
            }
        ):
            break
        related.update(found)
    outcomes: list[tuple[MsgspecUnion, MsgspecOutcome, str]] = []
    for index, union in enumerate(unions):
        if names := untaggable.get(index):
            outcomes.append((union, "untaggable", f"{', '.join(names)} cannot take exactly one tag for it"))
        elif defers_related and index in related:
            outcomes.append((union, "deferred", ""))
        elif names := related.get(index):
            reason = f"a base or derived struct of {', '.join(names)} keeps {union.tag_field!r} as a field"
            outcomes.append((union, "related", reason))
        else:
            outcomes.append((union, "applied", ""))
    return outcomes


def _is_tag_type(data_type: DataType) -> bool:
    """Return whether Pydantic reads the type of a tag field as ``Literal`` values.

    Every leaf of the type holds literal values, through the unions and the root models, or type aliases, it names.
    """
    return all(leaf.literals or leaf.enum_member_literals for leaf, _, _ in _iter_discriminator_data_types([data_type]))


def _variant_carries_tag(data_type: DataType, field_name: str) -> bool:
    """Return whether a variant has the tag field ``field_name``, of ``Literal`` values and not optional.

    The variant may inherit the field. Pydantic reads an optional tag as the ``Literal`` and None, which no tag is.
    The None that a root model of nullable variants holds is no variant.
    """
    if not data_type.reference:
        return data_type.type == NONE
    return isinstance(source := data_type.reference.source, DataModel) and any(
        _is_tag_type(tag_field.data_type) and not tag_field._admits_none()  # noqa: SLF001
        for tag_field in islice(
            (
                variant_field
                for variant_field in chain(source.fields, source.iter_all_fields())
                if _tag_field_matches(variant_field, "python-name", field_name)
            ),
            1,
        )
    )


def _root_writes_default(wrapper: DataModel) -> bool:
    """Return whether the root of a root model, or of a type alias, gets a default from what is written for it.

    A class gets it from the call of its root field or from the assignment after its annotation, a type alias from the
    call of its root field inside ``Annotated`` alone, and a root model written as an alias (``Foo = RootModel[T]``)
    has no root field to write one. The call of a type alias that holds only the default of its schema is written in
    render preparation: ``validate_default=True`` makes the call, which writes the default unless ``--use-annotated``
    leaves it out.
    """
    root = wrapper.fields[0]
    call = root._render_plan().call  # noqa: SLF001
    call_default = call is not None and (
        call.factory is not None or (call.positional is not None and call.positional.kind == "default")
    )
    if wrapper.IS_ALIAS:
        return not wrapper.IS_ROOT_MODEL and (
            call_default or (call is None and not root.use_annotated and validates_structured_default(root))
        )
    return call_default or wrapper._field_assignment_facts(root).has_default  # noqa: SLF001


def settle_site(site: UnionSite) -> SiteStatus:
    """Return whether Pydantic can build the ``discriminator=`` of a union once every module wrote its tags.

    The one variant a union names by its ``$ref`` alone, or that the ``$ref`` reaches through root models, takes no tag
    from L5, so it needs a tag field of ``Literal`` values; it must also be a model at all. No variant may be a root
    model, or a type alias, whose root has a default, however deep the root models it names reach: the default makes
    the schema of the root a ``default`` schema, which no tagged union holds.
    """
    field_ = site.field
    lone = tag_capabilities(type(site.model), type(field_)).writes_argument and not field_.data_type.data_types
    wrappers: list[DataModel] = []
    variants = list(
        _iter_discriminator_data_types([field_.data_type] if lone else field_.data_type.data_types, wrappers=wrappers)
    )
    if lone and not (
        any(data_type.reference for data_type, _, _ in variants)
        and all(_variant_carries_tag(data_type, site.property_name) for data_type, _, _ in variants)
    ):
        return "dropped-lone-untagged"
    return "dropped-root-default" if any(map(_root_writes_default, wrappers)) else "tagged"


def iter_root_default_unions(field_: DataModelFieldBase) -> Iterator[DataType]:
    """Yield the unions inside a field's type that take a root model with a default, as the pass drops each.

    ``--collapse-root-models`` writes the discriminator of the root model that a list names as its items onto the
    union it inlines there, a type of its own that the field's extras do not hold.
    """
    for nested in field_.data_type.all_data_types:
        if nested is field_.data_type or not nested.discriminator:
            continue
        wrappers: list[DataModel] = []
        deque(_iter_discriminator_data_types(nested.data_types, wrappers=wrappers), maxlen=0)
        if any(map(_root_writes_default, wrappers)):
            yield nested


@dataclass(slots=True)
class VariantTags:
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


@final
@record(eq=False)
class StructTagDecision:
    """What L6 decided for a msgspec struct below tagged bases (:data:`StructTagKind`)."""

    model: DataModel
    kind: StructTagKind
    tags: tuple[tuple[DataModelFieldBase, str, FieldTag], ...] = ()
    """``tagged-base``: the fields that become tags, the tag field of a base each redeclares and the tag it holds."""
    untagged: tuple[DataModelFieldBase, ...] = ()
    """``opted-out``: the tag fields that are instance fields again, typed by their const where no literal is."""


def _redeclared_tag_field(tag_fields: AbstractSet[str], field_: DataModelFieldBase) -> str | None:
    """Return the tag field of a tagged base that a field redeclares, by its Python or its source name, or None."""
    return redeclared_tag_field(tag_fields, name=field_.name, original_name=field_.original_name, alias=field_.alias)


def _bases_keep_instance_field(model: DataModel, source_name: str) -> bool:
    """Return whether a base of a model, or a base of that, keeps an instance field that the document names so."""
    return any(
        _keeps_instance_field(field_, source_name)
        for base_model in _find_base_classes(model)
        for field_ in base_model.iter_all_fields()
    )


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
        for field_ in model.fields:
            for data_type in field_.data_type.all_data_types:
                if data_type.is_union and len(structs := _structs_of_union(data_type)) > 1:
                    shared |= structs
    return shared


def _has_cyclic_ancestry(model: DataModel) -> bool:
    """Return whether the base classes of a model, however distant, inherit from one another in a cycle."""
    walk = walk_graph(GraphSnapshot.CYCLIC_ANCESTRY, EdgeKind.BASE, (model,), _model_key_of_model, _find_base_classes)
    return walk.graph.acyclic_order((EdgeKind.BASE,)) is None


def _bases_first(models: Sequence[DataModel]) -> list[DataModel]:
    """Return the models with base classes, each after the bases among ``models`` it inherits from, each once.

    A model whose bases, however distant, inherit from one another in a cycle is left out, and the bases only it
    reaches wait for their own turn. L6 changes the bases of a model only once its turn has come, so the order is
    that of the snapshot of ``models`` and the models they inherit from, taken before.
    """
    walk = walk_graph(GraphSnapshot.TAGGED_BASES, EdgeKind.BASE, models, _model_key_of_model, _find_base_classes)
    met = set(walk.node_of).difference(walk.starts)
    order: list[ModelKey] = []
    for model, key in zip(models, walk.starts, strict=True):
        if key in met or not model.base_classes or _has_cyclic_ancestry(model):
            met.add(key)
            continue
        order.extend(
            reached for reached in walk.graph.postorder(key, EdgeKind.BASE, met) if walk.node_of[reached].base_classes
        )
    return [walk.node_of[key] for key in order]


def iter_struct_tag_decisions(models: Sequence[DataModel]) -> Iterator[StructTagDecision]:
    """Yield what becomes of the msgspec structs below tagged bases, as the pass asks for each after writing the last.

    msgspec rejects a field that declares the tag field of a tagged base. A struct inherits the tag of its base, so
    its single literal becomes its own tag, as for a tagged variant, and a struct with any other such field opts out of
    the tag to keep the field. A struct whose tag field a base keeps as an instance field opts out too, and its tag
    field is a field of its own again, as msgspec refuses a tag field that a base declares; a struct that reuse made an
    empty subclass of that base declares the fields of its base itself instead and keeps its tag, as it would without
    reuse, where a union lists it beside another struct: msgspec decodes such a union only if all its structs are
    tagged. Bases of other modules are tagged by then, as every module is processed first, and a base is decided
    before the structs inheriting from it, as its own tag decides theirs. Inheritance cycles are reported later.
    """
    sharing: set[int] | None = None
    for model in _bases_first(models):
        if tag_fields := {tag[0] for base in _find_base_classes(model) if (tag := base.discriminator_tag) is not None}:
            redeclared = [
                (field_, tag_field, field_tag("legacy-literal-only", _FieldTagFacts(field_)))
                for field_ in model.fields
                if not field_.extras.get("is_classvar")
                and (tag_field := _redeclared_tag_field(tag_fields, field_)) is not None
            ]
            tags = tuple((field_, tag_field, tag) for field_, tag_field, tag in redeclared if tag is not None)
            if len(tags) != len(redeclared):
                yield StructTagDecision(model, "opted-out")
            elif tags:
                yield StructTagDecision(model, "tagged-base", tags)
        if (tag := model.discriminator_tag) is None or not _bases_keep_instance_field(model, tag[0]):
            continue
        if model.reused_reference is not None:
            if sharing is None:
                sharing = _structs_sharing_a_union(models)
            if id(model) in sharing:
                yield StructTagDecision(model, "flattened")
                continue
        yield StructTagDecision(
            model,
            "opted-out",
            untagged=tuple(
                field_
                for field_ in model.fields
                if field_.extras.get("is_classvar") and _redeclared_tag_field({tag[0]}, field_) is not None
            ),
        )


@final
@dataclass(slots=True)
class TagPlan:
    """What the tag passes of one parse recorded, by the ids of the models and fields they read.

    L3 replaces :attr:`reuse_snapshot` in every parse; :meth:`reset` forgets the rest at the start of L4.
    """

    reuse_snapshot: dict[DataModel, tuple[HashableComparable, ...]] = field(default_factory=dict)
    """The field index, property and tags of each discriminated union of each model, before reuse merges any."""
    inline_variants: InlineVariantTags | None = None
    """The tag fields of the inline variants that may take the tag they inherit, once the first union asks."""
    unions: dict[int, UnionTagDecision] = field(default_factory=dict)
    """What L5 decided at each union site, by field id."""
    first_tags: dict[tuple[int, str], VariantTags] = field(default_factory=dict)
    """The tags of discriminated union variants, by variant id and tag property."""
    reused_tag_fields: dict[int, DataModelFieldBase] = field(default_factory=dict)
    """The tag fields that a union with a reused variant annotates with a new tag, by field id."""
    deferred_msgspec: list[MsgspecUnion] = field(default_factory=list)
    """The msgspec unions whose tags wait until every module is processed."""
    defers_related: bool = False
    """Whether modules remain whose unions may tag a struct, so the unions such a struct blocks wait."""
    struct_tags: list[StructTagDecision] = field(default_factory=list)
    """What L6 decided for the msgspec structs below tagged bases, in the order it wrote them."""

    def reset(self) -> None:
        """Forget what the passes of an earlier parse recorded after L3."""
        self.inline_variants = None
        self.unions.clear()
        self.first_tags.clear()
        self.reused_tag_fields.clear()
        self.deferred_msgspec.clear()
        self.defers_related = False
        self.struct_tags.clear()

    @property
    def opted_out(self) -> list[DataModel]:
        """Return the structs below tagged bases that L6 leaves untagged."""
        return [decision.model for decision in self.struct_tags if decision.kind == "opted-out"]

    def inline_variant_tags(
        self, models: Iterable[DataModel], namer: FieldNamer, *, enum_member_tags: bool
    ) -> InlineVariantTags:
        """Return the tag fields of the inline variants that may take the tag they inherit, decided once for all unions.

        An inline variant has no name to be tagged by, so its schema path ends in an internal marker, which no payload
        carries. It takes the tag it inherits (``variant_tags``) unless a union that lists it would then list two
        variants with one tag, which turns that union into a plain union and takes the tags from its named variants.
        Every variant of such a union keeps the marker, and so does a variant that another union lists. The decision
        reads the unions before any of them writes a tag, as a variant takes its tag from the first union that writes
        one, and the variants take the tags it read: a tag another union writes into a base later is not one the
        variant inherits (row ``legacy-inline-variants-at-first-ask``).
        """
        if (decided := self.inline_variants) is not None:
            return decided
        inline: dict[int, tuple[tuple[DataModelFieldBase, DiscriminatorValue], ...]] = {}
        clashing: set[int] = set()
        for site in iter_union_sites(models):
            field_ = site.field
            variants = {
                id(source): tuple(
                    (tag_field, value)
                    for tag_field in source.iter_all_fields()
                    if (value := _get_discriminator_field_value(tag_field)) is not None
                )
                for data_type, _, _ in _iter_discriminator_data_types(field_.data_type.data_types)
                if data_type.reference
                and isinstance(source := data_type.reference.source, DataModel)
                and SPECIAL_PATH_MARKER in _get_schema_path(source).rpartition("/")[2]
            }
            if not variants:
                continue
            names = site_names(site.property_name, namer)
            inline |= variants
            mode = site_mode(writes_argument=tag_capabilities(type(site.model), type(field_)).writes_argument)
            if [
                site_validity(
                    field_.data_type.data_types,
                    names,
                    site.mapping,
                    field_type=type(field_),
                    mode=mode,
                    inline_variants=inline_models,
                    enum_member_tags=enum_member_tags,
                )
                == "tagged"
                for inline_models in ({}, variants)
            ] == [True, False]:
                clashing.update(variants)
        self.inline_variants = decided = {key: tag_fields for key, tag_fields in inline.items() if key not in clashing}
        return decided

    def record_reused_tag_field(self, field_: DataModelFieldBase, *, annotates_tag: bool) -> None:
        """Remember a field that a union with a reused variant annotates with a new tag, else forget it.

        Such unions were left untagged before, so the ``Literal`` that only their tags import must not rename a model.
        """
        if annotates_tag:
            self.reused_tag_fields[id(field_)] = field_
        else:
            self.reused_tag_fields.pop(id(field_), None)

    def record_variant_tags(
        self, variant: DataModel, union_name: str, view: UnionView, values: list[DiscriminatorValue]
    ) -> None:
        """Remember the tags a union maps a variant to, unless the variant declares its own tag.

        The values of a later union come from the tag an earlier union wrote, so the tags it maps the variant to are
        derived again from its mapping or the schema name (row ``legacy-tag-from-earlier-union``).
        """
        property_name = view.names.property_name
        if (recorded := self.first_tags.get(key := (id(variant), property_name))) is not None:
            if not same_tags(values, recorded.kept, "legacy-python-equality"):
                recorded.others.setdefault(recorded.owner, recorded.kept)
                recorded.kept, recorded.owner = values, union_name
                recorded.others = {
                    union: tags
                    for union, tags in recorded.others.items()
                    if not same_tags(tags, values, "legacy-python-equality")
                }
            elif not same_tags(tags := view.tags("union", variant), values, "legacy-python-equality"):
                recorded.others.setdefault(union_name, tags)
            return
        if _find_discriminator_value(variant.fields, view.names.field_name, field_name_mode=view.mode) is None:
            self.first_tags[key] = VariantTags(variant, property_name, values, union_name, {})

    def record_collapsed_tags(  # noqa: PLR0913
        self,
        variant: DataModel,
        union_name: str,
        view: UnionView,
        values: list[DiscriminatorValue],
        *,
        removed: DataModel,
        mapping: dict[str, str],
    ) -> None:
        """Remember the tags a union maps a variant to when collapsed reuse replaced the model the union names.

        The union maps the removed duplicate to the tags its own schema, the mapping or its name give, which the kept
        model need not carry. They replace the tags derived from the kept model for that union.
        """
        key = (id(variant), view.names.property_name)
        if not same_tags(tags := view.tags("variant", removed, mapping), values, "legacy-python-equality"):
            self.first_tags.setdefault(
                key, VariantTags(variant, view.names.property_name, values, union_name, {})
            ).others[union_name] = tags
        elif (recorded := self.first_tags.get(key)) is not None:
            recorded.others.pop(union_name, None)

    def record_settled(self, site: UnionSite, status: SiteStatus) -> None:
        """Remember that the end of L5 drops the discriminator of a union site."""
        self.unions.setdefault(id(site.field), UnionTagDecision(site, None, status)).status = status


def declared_union_tags(model: DataModel) -> tuple[HashableComparable, ...]:
    """Return the field index, property and the tags of each variant of each discriminated union of a model.

    Unions that render alike decode other tags when their discriminators name another property or map their
    variants to other tags. The tags a mapping gives the variants are listed by variant position, so unions that
    map the same tags to variants that reuse merges match whatever schemas their mappings name. Every discriminator
    object counts, whether it names a property or not.
    """
    if not (
        discriminators := [
            (index, discriminator, field_.data_type.data_types)
            for index, field_ in enumerate(model.fields)
            if isinstance(discriminator := field_.extras.get("discriminator"), dict)
        ]
    ):
        return ()
    from datamodel_code_generator._internal_utils import to_hashable  # noqa: PLC0415

    return tuple(
        (
            index,
            str(discriminator.get("propertyName")),
            tuple(
                to_hashable(_get_mapping_discriminator_values(source, mapping))
                if (mapping := discriminator.get("mapping"))
                and (reference := data_type.reference)
                and isinstance(source := reference.source, DataModel)
                else ()
                for data_type in data_types
            ),
        )
        for index, discriminator, data_types in discriminators
    )


def possible_tag_names(
    sites: Iterable[UnionSite], layout_models: list[DataModel], namer: FieldNamer
) -> dict[int, frozenset[str]]:
    """Return, by model whose layout counts, the names of the fields that the discriminator step may make tags.

    It tags the variants that the unions of ``sites`` list, however nested or aliased, or that their mappings name,
    by the property name or the field name it resolves to in the model's class or in any class. A struct inheriting
    from a tagged struct takes its tag field as a tag too. The mapped paths are indexed once by every key a model path
    can match them by (row ``legacy-any-key``).
    """
    listed: defaultdict[int, set[str]] = defaultdict(set)
    mapped: defaultdict[tuple[str, str], set[str]] = defaultdict(set)
    for site in sites:
        property_name = site.property_name
        for reference in (
            data_type.reference
            for data_type, _, _ in _iter_discriminator_data_types(site.field.data_type.all_data_types)
            if data_type.reference
        ):
            listed[id(reference.source)].add(property_name)
        if isinstance(mapping := site.discriminator.get("mapping"), dict):
            for path in mapping.values():
                for key in mapping_path_keys(str(path), rule="legacy-any-key"):
                    mapped[key].add(property_name)
    variant_names: dict[int, set[str]] = {}
    for model in layout_models:
        property_names = set(listed.get(id(model), ()))
        for reference in (model.reference, *(base.reference for base in model.base_classes if base.reference)):
            for key in schema_path_keys(_get_schema_path(reference)):
                property_names.update(mapped.get(key, ()))
        variant_names[id(model)] = {
            name
            for property_name in property_names
            for class_name in (None, model.class_name)
            for name in (property_name, namer(property_name, class_name)[0])
        }
    return {
        id(model): frozenset(
            chain.from_iterable(variant_names.get(id(ancestor), ()) for ancestor in linearize_data_models([model]))
        )
        for model in layout_models
    }
