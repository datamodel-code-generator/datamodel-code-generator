"""Discriminator tag values: which value tags a union variant, which field holds it, which schemas a mapping names.

The functions read only their arguments and change none of them (a mapping passed in is read, never copied).
``field_tag`` and ``variant_tags`` ask read-only views (``FieldTagFacts``, ``VariantTagFacts``) for the facts their
rows need, in their order, as an Enum lookup or a variant's mapped tags cost more than the decision. The adapters
read the facts from the live objects: ``parser/_passes/_shared.py`` (``_tag_field_matches``, ``_keeps_instance_field``,
``_FieldTagFacts``, ``_get_mapping_discriminator_values``, ``enum_discriminator_literal``,
``enum_discriminator_members``),
``parser/_passes/modules.py`` (``_VariantTagFacts`` and the passes that apply tags), ``parser/jsonschema.py``
(``_SchemaTagFacts`` behind ``JsonSchemaParser._get_single_literal_value``, ``_get_union_variant_literal_values``,
``_get_tagged_union_field_values``, ``_set_tagged_union_discriminator``) and ``model/msgspec.py``
(``Struct._tag_options``, ``Struct.discriminator_tag``).

The rows named ``legacy-*`` keep today's output where passes or backends disagree on a tag; the fix of each deletes
its row (G05): ``legacy-literal-only`` (``FieldTagSite``),
``legacy-python-equality`` (``TagIdentity``), ``legacy-any-key`` (``MappingPathRule``) and the rows in the docstrings
of ``redeclared_tag_field``, ``tagged_union_discriminator`` and ``struct_tag_field``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal, TypeAlias, final

from typing_extensions import assert_never

from datamodel_code_generator._records import record
from datamodel_code_generator.model._decisions.enum_kind import is_literal_kind, is_tag_kind, literal_value_kind

if TYPE_CHECKING:
    from collections.abc import Callable, Container, Iterable, Mapping, Sequence
    from collections.abc import Set as AbstractSet
    from typing import Protocol

    class FieldTagFacts(Protocol):
        """The facts of a field or schema that ``field_tag`` reads, each computed when it is asked for."""

        def has_const(self) -> bool:
            """Return whether it has a ``const``."""

        def const(self) -> Any:
            """Return its ``const``."""

        def literals(self) -> Sequence[TagValue]:
            """Return the values of the ``Literal`` its type is, if any."""

        def enum_member(self) -> tuple[int, Any]:
            """Return how many members its Enum has (0 for none) and the first one's value (None for none)."""

        def referenced_tag(self) -> FieldTag | None:
            """Return the tag of the schema its ``$ref`` names, or None."""

    class VariantTagFacts(Protocol):
        """The facts of a variant that ``variant_tags`` reads, each computed when it is asked for."""

        def own_tag(self) -> Any:
            """Return the tag of the variant's own tag field (``field_tag``), or None."""

        def reused_tag(self) -> Any:
            """Return the tag of the model reuse replaced with the variant, or None."""

        def inherited_tag(self) -> Any:
            """Return the tag of a tag field the variant inherits, or None."""

        def inline_tag(self) -> Any:
            """Return the tag an inline variant inherits, or None where it is no inline variant or may not take it."""

        def mapped_tags(self) -> Sequence[str]:
            """Return the mapping keys naming the variant or its bases (``mapped_tags``)."""

        def path_segment(self) -> str:
            """Return the last segment of the variant's schema path."""


__all__ = [
    "EnumTagMembers",
    "EnumTagRule",
    "FieldTag",
    "FieldTagRule",
    "FieldTagSite",
    "MappingPathKey",
    "MappingPathRule",
    "StructTagClash",
    "TagFieldMatch",
    "TagIdentity",
    "TagValue",
    "VariantTagRule",
    "VariantTagSource",
    "VariantTags",
    "distinct_tags",
    "enum_tag_as_values",
    "enum_tag_literal",
    "enum_tag_members",
    "field_source_name",
    "field_tag",
    "is_tag_value",
    "mapped_tags",
    "mapping_path_keys",
    "redeclared_tag_field",
    "same_tag",
    "same_tags",
    "schema_path_keys",
    "struct_discriminator_tag",
    "struct_tag_clash",
    "struct_tag_conflicts",
    "struct_tag_field",
    "struct_tag_options",
    "struct_tag_refused",
    "tag_field_kept",
    "tag_field_matches",
    "tag_key",
    "tagged_union_discriminator",
    "union_tag_values",
    "variant_tags",
]

TagValue: TypeAlias = str | int | bool
"""A value a ``Literal`` tag holds."""

TagFieldMatch: TypeAlias = Literal[
    "source-name",
    "python-name",
    "python-or-original-name",
    "original-or-unaliased-python-name",
]
"""How a field is found to hold the discriminator's property (``tag_field_matches``).

* ``source-name``: the name the document gives the field (``field_source_name``) is the property name.
* ``python-name``: the field's Python name is the Python name the property resolves to; for outputs that write the
  discriminator by Python name, as a resolved name can equal another property's name.
* ``python-or-original-name``: the field's Python name or its original name is the Python name the property resolves
  to; for outputs without a ``discriminator`` argument.
* ``original-or-unaliased-python-name``: the field's original name is the property name, or a field without one has
  the Python name the property resolves to by default; it finds the Python name the variants of a union share.
"""

FieldTagSite: TypeAlias = Literal["schema", "variant-field", "legacy-literal-only"]
"""Where ``field_tag`` asks for the tag of a field.

* ``schema``: L1, the property schema of a union branch (``JsonSchemaParser._get_single_literal_value``): its
  ``const``, else the one value of its ``enum``, else the schema its ``$ref`` names; a value no ``Literal`` holds is no
  tag, and such a ``const`` ends the search.
* ``variant-field``: L5, the tag field of a variant (``_get_discriminator_field_value``): its ``const``, its one
  ``Literal`` or the one member of its Enum, of any kind (``value-of-another-kind``).
* ``legacy-literal-only``: L6, a field of a msgspec Struct that redeclares the tag field of a tagged base
  (``_tag_fields_of_tagged_bases``): only its one ``Literal``, so a ``const``, a one-member Enum or a ``Literal``
  msgspec refuses as a tag (``struct_tag_refused``) there opts the Struct out of the inherited tag instead of
  tagging it.
"""

FieldTagRule: TypeAlias = Literal[
    "const", "one-literal", "one-member-enum", "referenced-schema", "value-of-another-kind"
]
"""The rows of ``field_tag``.

* ``const``: the ``const``, a value a ``Literal`` holds (str, int or bool).
* ``one-literal``: the type is a ``Literal`` of one value.
* ``one-member-enum``: the type is an Enum of one member, or the ``enum`` holds one value, a value a ``Literal`` holds.
* ``referenced-schema``: the tag of the schema the ``$ref`` names (``schema`` only).
* ``value-of-another-kind``: a ``const`` or one-member Enum value of another kind (a float, an array or an object)
  is the variant's tag too (``variant-field`` only), so no other row tags it; no ``Literal`` holds it
  (``is_tag_value``), so its union is not discriminated.
"""

VariantTagSource: TypeAlias = Literal["variant", "union", "nested"]
"""Which tags of a variant ``variant_tags`` returns.

* ``variant``: the tags a union it lists gives the variant.
* ``union``: the tags the union's mapping or the variant's schema gives it before its own tag field, which an earlier
  union may have written.
* ``nested``: the tag of a variant a nested choice gives the union, which keeps the tag it declares or inherits.
"""

VariantTagRule: TypeAlias = Literal[
    "own-field", "reused-schema-field", "inherited-field", "mapping", "inline-inherited-field", "path-segment", "none"
]
"""The rows of ``variant_tags``.

* ``own-field``: the variant's own tag field (``field_tag``).
* ``reused-schema-field``: the tag field of the model that reuse replaced with the variant, an empty subclass whose
  base can carry the tag another union wrote.
* ``inherited-field``: a tag field the variant inherits (``nested`` only).
* ``mapping``: the mapping keys that name the variant or, failing that, its bases (``mapped_tags``).
* ``inline-inherited-field``: the tag field an inline variant inherits, for a variant that has no name to be tagged by
  (#4829): the last segment of its schema path is an internal marker, and no union it belongs to lists another variant
  with that tag.
* ``path-segment``: the last segment of the variant's schema path: its name for a named schema.
* ``none``: a nested variant without a tag.
"""

EnumTagRule: TypeAlias = Literal["named-members", "all-members"]
"""The rows of ``enum_tag_members``.

* ``named-members``: the members its tags name.
* ``all-members``: no tag names a member (the schema name of the variant, a mapping key its Enum lacks), so the variant
  takes every member of its Enum, which are the only values its tag field can hold.
"""

TagIdentity: TypeAlias = Literal["type-and-value", "legacy-python-equality"]
"""How tags are told apart (``tag_key``).

* ``type-and-value``: by type and value, so ``1`` and ``true`` are two tags (L1: variant names, tagged unions).
* ``legacy-python-equality``: by Python's ``==``, so ``1 == True`` share a tag (L5: whether a union's variants have
  distinct tags, whether a variant's literal is its tag, which tags of a variant two unions disagree on; L6: which
  tags msgspec Structs share).
"""

MappingPathRule: TypeAlias = Literal["selects", "legacy-any-key"]
"""Which keys a discriminator mapping path is matched by (``mapping_path_keys``).

* ``selects``: a bare name only by the schema name; any other path by the part after ``#/``, by a file name unless the
  path starts with ``#/``, or by a path relative to the document.
* ``legacy-any-key``: by all four, which can name more schemas; the layout prediction of msgspec base flattening
  (``parser/_passes/plans.py:find_possible_tag_names``) reads it until a tag plan replaces the prediction.
"""

MappingPathKey: TypeAlias = tuple[Literal["name", "schema", "file", "relative"], str]
"""A key a mapping path and a schema path are matched by."""

StructTagClash: TypeAlias = Literal["tag-fields", "tag-types", "shared-tags"]
"""How the tags of a union's tagged msgspec Structs clash (``struct_tag_clash``): by different tag fields, by tags
of different types, or by tags more than one Struct takes."""


@final
@record
class FieldTag:
    """The tag a field holds, and the row of ``FieldTagRule`` it comes from."""

    value: Any
    rule: FieldTagRule


@final
@record
class VariantTags:
    """The tags of a variant, and the row of ``VariantTagRule`` they come from."""

    values: tuple[Any, ...]
    rule: VariantTagRule


@final
@record
class EnumTagMembers:
    """The members of an Enum that tag a variant, and the row of ``EnumTagRule`` they come from."""

    members: tuple[Any, ...]
    rule: EnumTagRule


def field_source_name(*, name: str | None, original_name: str | None, alias: str | None) -> str | None:
    """Return the name the document gives a field: its original name, else its alias, else its Python name."""
    return original_name if original_name is not None else alias or name


def tag_field_matches(  # noqa: PLR0913
    mode: TagFieldMatch,
    wanted: str,
    /,
    *,
    name: str | None,
    original_name: str | None,
    alias: str | None = None,
    python_name: str | None = None,
) -> bool:
    """Return whether a field holds the discriminator's property ``wanted`` (``TagFieldMatch`` names the modes).

    ``wanted`` is the property name for ``source-name`` and ``original-or-unaliased-python-name``, which also reads the
    default Python name ``python_name``, and the resolved Python name for the others.
    """
    match mode:
        case "source-name":
            return field_source_name(name=name, original_name=original_name, alias=alias) == wanted
        case "python-name":
            return name == wanted
        case "python-or-original-name":
            return wanted in {name, original_name}
        case "original-or-unaliased-python-name":
            return original_name == wanted or (original_name is None and name == python_name)
        case _:  # pragma: no cover
            return assert_never(mode)


def tag_field_kept(
    source_name: str, /, *, is_class_var: bool, name: str | None, original_name: str | None, alias: str | None
) -> bool:
    """Return whether a field is an instance field that the document names ``source_name``, which msgspec refuses.

    msgspec refuses a tag field that a struct or one of its bases declares as a field. A class variable is none, as the
    field of a tagged struct is, and the field is found by the name the document gives it (``tag_field_matches``).
    """
    return not is_class_var and tag_field_matches(
        "source-name", source_name, name=name, original_name=original_name, alias=alias
    )


def redeclared_tag_field(
    tag_fields: AbstractSet[str], /, *, name: str | None, original_name: str | None, alias: str | None
) -> str | None:
    """Return the tag field of a tagged base that a field redeclares, by its Python or its source name, or None.

    Row ``legacy-hash-order``: when both names are tag fields of its bases, it is the one a set intersection yields
    first, which follows string hashing.
    """
    return next(iter({name, field_source_name(name=name, original_name=original_name, alias=alias)} & tag_fields), None)


def field_tag(site: FieldTagSite, facts: FieldTagFacts, /) -> FieldTag | None:
    """Return the one tag a field or a schema holds, or None (``FieldTagSite``, ``FieldTagRule``).

    It holds its ``const``, else the value of its one ``Literal``, else that of its one Enum member, else the tag of
    the schema its ``$ref`` names.
    """
    if site != "legacy-literal-only" and facts.has_const():
        return _value_tag(site, facts.const(), "const")
    if len(literals := facts.literals()) == 1:
        if site == "legacy-literal-only" and struct_tag_refused(literals[0]):
            return None
        return FieldTag(literals[0], "one-literal")
    if site == "legacy-literal-only":
        return None
    members, value = facts.enum_member()
    if members == 1 and (tag := _value_tag(site, value, "one-member-enum")) is not None:
        return tag
    return facts.referenced_tag()


def _value_tag(site: FieldTagSite, value: Any, rule: Literal["const", "one-member-enum"]) -> FieldTag | None:
    """Return the tag a ``const`` or one-member Enum value gives: the value where a ``Literal`` holds it.

    At a variant's field a value of any other kind is its tag too (``value-of-another-kind``).
    """
    if is_literal_kind(literal_value_kind(value)):
        return FieldTag(value, rule)
    return FieldTag(value, "value-of-another-kind") if site == "variant-field" else None


def variant_tags(source: VariantTagSource, facts: VariantTagFacts, /) -> VariantTags:  # noqa: PLR0911
    """Return the tags of a variant (``VariantTagSource``, ``VariantTagRule``).

    A variant takes its own tag, else that of the model it replaces; a nested one then the tag it inherits, if any.
    Otherwise, and for ``union``, the mapping keys decide, else the replaced model's tag, else the tag an inline
    variant inherits, else the path segment.
    """
    if source != "union":
        if (own := facts.own_tag()) is not None:
            return VariantTags((own,), "own-field")
        if (reused := facts.reused_tag()) is not None:
            return VariantTags((reused,), "reused-schema-field")
        if source == "nested":
            inherited = facts.inherited_tag()
            return VariantTags((), "none") if inherited is None else VariantTags((inherited,), "inherited-field")
    if mapped := facts.mapped_tags():
        return VariantTags(tuple(mapped), "mapping")
    if (reused := facts.reused_tag()) is not None:
        return VariantTags((reused,), "reused-schema-field")
    if (inline := facts.inline_tag()) is not None:
        return VariantTags((inline,), "inline-inherited-field")
    return VariantTags((facts.path_segment(),), "path-segment")


def mapping_path_keys(path: str, /, *, rule: MappingPathRule = "selects") -> tuple[MappingPathKey, ...]:
    """Return the keys a discriminator mapping path names a schema by (``MappingPathRule`` names the rules).

    The keys are its name, the part after ``#/``, its file name and its path relative to the document.
    """
    if rule == "selects" and "/" not in path and "#" not in path:
        return (("name", path),)
    relative: MappingPathKey = ("relative", path[path.find("/") + 1 :])
    schema: MappingPathKey = ("schema", path.rsplit("#/", maxsplit=1)[-1])
    file: MappingPathKey = ("file", path.rsplit("/", maxsplit=1)[-1])
    if rule == "legacy-any-key":
        return ("name", path), schema, file, relative
    return (schema, relative) if path.startswith("#/") else (schema, file, relative)


def schema_path_keys(schema_path: str, /) -> tuple[MappingPathKey, ...]:
    """Return the keys the schema path of a model is named by in a discriminator mapping (``mapping_path_keys``)."""
    relative = schema_path[: schema_path.find("#")].lstrip("../")  # noqa: B005
    return (
        ("name", schema_path.rsplit("/", 1)[-1]),
        ("schema", schema_path.rsplit("#/", maxsplit=1)[-1]),
        ("file", schema_path[:-1]),
        ("relative", relative),
        ("relative", "/".join(relative.split("/")[1:])),
    )


def mapped_tags(mapping: Mapping[str, str], /, *, schema_path: str, base_schema_paths: Iterable[str] = ()) -> list[str]:
    """Return the keys of a discriminator mapping whose paths select a model, else those selecting its bases.

    A path selects the schema path it shares a key with (``mapping_path_keys``). Keys follow the mapping order, and the
    bases' keys the base order; ``base_schema_paths`` is read only when no path selects the model.
    """

    def selected(schema_path: str) -> list[str]:
        keys = set(schema_path_keys(schema_path))
        return [tag for tag, path in mapping.items() if not keys.isdisjoint(mapping_path_keys(path))]

    return selected(schema_path) or [
        tag for base_schema_path in base_schema_paths for tag in selected(base_schema_path)
    ]


def is_tag_value(value: Any, /, *, bool_literals: bool) -> bool:
    """Return whether a variant's tag is a value its ``Literal`` holds, which a discriminated union needs.

    ``bool_literals`` tells that the output writes bool literals (msgspec does not, and takes no bool tag).
    """
    return is_literal_kind(literal_value_kind(value), bool_literals=bool_literals)


def enum_tag_literal(value: TagValue, member_value: Any, /) -> TagValue:
    """Return the tag a value naming an Enum member writes: the member's value where a ``Literal`` holds it.

    A mapping key ``"1"`` naming the member ``1`` of an integer Enum so becomes ``Literal[1]``.
    """
    return member_value if is_literal_kind(literal_value_kind(member_value)) else value


def enum_tag_as_values(values: Iterable[Any], /, *, bool_literals: bool) -> bool:
    """Return whether the members that tag a variant are written as their values, ``Literal['v1']``.

    An output whose ``Literal`` holds no enum member (msgspec) writes the values when its ``Literal`` holds each of them
    (``is_literal_kind``); a tag with any other value keeps its members, as an enum of floats stays one that no msgspec
    decodes.
    """
    return all(is_literal_kind(literal_value_kind(value), bool_literals=bool_literals) for value in values)


def enum_tag_members(named: Iterable[Any], members: Callable[[], Iterable[Any]], /) -> EnumTagMembers:
    """Return the members of an Enum that tag a variant, written as members (``EnumTagRule``).

    ``named`` holds, for each tag of the variant, the member it names or None, and ``members`` yields every member of
    the Enum, which it is asked for only when no tag names one.
    """
    if found := tuple(member for member in named if member is not None):
        return EnumTagMembers(found, "named-members")
    return EnumTagMembers(tuple(members()), "all-members")


def tag_key(value: Any, /, identity: TagIdentity) -> object:
    """Return the key that tells a tag apart from others (``TagIdentity`` names the rules)."""
    return (type(value), value) if identity == "type-and-value" else value


def same_tag(left: Any, right: Any, /, identity: TagIdentity) -> bool:
    """Return whether two values are the same tag (``tag_key``)."""
    return tag_key(left, identity) == tag_key(right, identity)


def distinct_tags(values: Iterable[Any], /, identity: TagIdentity) -> bool:
    """Return whether no two of the tags are the same tag (``tag_key``)."""
    keys = [tag_key(value, identity) for value in values]
    return len(set(keys)) == len(keys)


def same_tags(left: Iterable[Any], right: Iterable[Any], /, identity: TagIdentity) -> bool:
    """Return whether two lists of tags hold the same tags (``tag_key``), in any order and number."""
    return {tag_key(value, identity) for value in left} == {tag_key(value, identity) for value in right}


def union_tag_values(
    literals: Mapping[int, Any], /, *, branch_count: int, required: Container[int]
) -> dict[int, Any] | None:
    """Return the msgspec tag of every branch of a union by branch index, or None when the property tags no union.

    ``literals`` are the property's literals by branch index and ``required`` the indexes of the branches requiring the
    property. It tags the union when every one of the ``branch_count`` branches holds a literal for it, requires it and
    holds a str or an int, which msgspec takes as a tag (``is_tag_kind``).
    """
    if len(literals) != branch_count or not all(
        index in required and is_tag_kind(literal_value_kind(literal)) for index, literal in literals.items()
    ):
        return None
    return dict(literals)


def tagged_union_discriminator(declared: object, /, *, literal_property: str) -> dict[str, Any]:
    """Return the discriminator a union of msgspec Structs is captured with: by the property tagging every branch.

    ``declared`` is the discriminator in the schema object's extras, whose other keys stay. The property replaces the
    one the schema's discriminator names, and its ``mapping`` goes with the property it names, as its keys are values
    of that property, not tags of the other (row ``legacy-literal-property-replaces-declared``: L5 then tags the
    variants by their literals, never by the declared property).
    """
    if not isinstance(declared, dict):
        return {"propertyName": literal_property}
    if declared.get("propertyName") == literal_property:
        return {**declared, "propertyName": literal_property}
    return {key: value for key, value in declared.items() if key != "mapping"} | {"propertyName": literal_property}


def struct_tag_field(*, property_name: str, field_name: str, by_payload_name: bool) -> str:
    """Return the ``tag_field`` a union's msgspec Structs are tagged with.

    Row ``legacy-python-name``: unless ``--tag-msgspec-by-payload-name`` or ``--use-msgspec-tagged-unions`` is given,
    the Structs whose tag fields hold one literal each take the Python name the property resolves to, which msgspec
    looks for in the payload instead, so a property named ``pet-kind`` (``pet_kind``) decodes nothing. With either
    option the tag field is the property name the payload carries.
    """
    return property_name if by_payload_name else field_name


_STRUCT_TAG_INTS = range(-(2**63), 2**63)
"""The ints msgspec takes as a Struct tag: those of the signed 64-bit range."""


def struct_tag_refused(value: object, /) -> bool:
    """Return whether msgspec refuses a value as a Struct tag: an int beyond the 64-bit range (``_STRUCT_TAG_INTS``).

    A bool is no int (``literal_value_kind``).
    """
    return literal_value_kind(value) == "int" and value not in _STRUCT_TAG_INTS


def struct_tag_conflicts(value: TagValue | None, mapped: Iterable[str], /) -> bool:
    """Return whether a variant cannot take ``value`` as its msgspec tag: none, a refused one or a differing key.

    msgspec refuses an int beyond the 64-bit range (``struct_tag_refused``). A mapping key is a string, so it names the
    tag whose text it is, as ``Enum.find_member`` reads keys: the key ``"1"`` names the tag ``1`` an integer Enum
    member gives it (``enum_tag_literal``), as for ``Literal[1]``.
    """
    return value is None or struct_tag_refused(value) or any(key != str(value) for key in mapped)


def struct_tag_clash(
    tags: Sequence[tuple[Any, Any]], /, identity: TagIdentity
) -> tuple[StructTagClash, list[Any]] | None:
    """Return how the ``(tag_field, tag)`` pairs of a union's tagged msgspec Structs clash, and the values that do.

    msgspec tells them apart only by one tag field (else ``tag-fields`` with the fields in order), with tags of one
    type (``tag-types``) that are distinct (else ``shared-tags`` with the tags more than one takes, by ``tag_key``).
    """
    if len(tag_fields := [*dict.fromkeys(tag_field for tag_field, _ in tags)]) > 1:
        return "tag-fields", tag_fields
    if len({type(tag) for _, tag in tags}) > 1:
        return "tag-types", []
    by_key: dict[object, list[Any]] = {}
    for _, tag in tags:
        by_key.setdefault(tag_key(tag, identity), []).append(tag)
    if shared := [same[0] for same in by_key.values() if len(same) > 1]:
        return "shared-tags", shared
    return None


def struct_tag_options(*, own_tag: Any, own_tag_field: Any, bases: Sequence[tuple[Any, Any]]) -> tuple[Any, Any]:
    """Return the ``tag`` and ``tag_field`` of a msgspec Struct, inheriting those of its bases as msgspec does.

    ``bases`` are the options of its generated Struct bases in base order; the first base setting an option wins, and
    the Struct's own options win over its bases'. ``tag=False`` clears both, so the Structs inheriting from it are
    untagged too.
    """
    tag = tag_field = None
    for base_tag, base_tag_field in reversed(bases):
        tag = tag if base_tag is None else base_tag
        tag_field = tag_field if base_tag_field is None else base_tag_field
    if (tag := tag if own_tag is None else own_tag) is False:
        return None, None
    return tag, tag_field if own_tag_field is None else own_tag_field


def struct_discriminator_tag(tag: Any, tag_field: Any, /, *, class_name: str) -> tuple[Any, Any] | None:
    """Return the tag field and the tag msgspec tells a Struct apart by in a union, or None if it is untagged.

    msgspec defaults the tag field to ``type`` and the tag (also for ``tag=True``) to the class name.
    """
    if tag is None and tag_field is None:
        return None
    return "type" if tag_field is None else tag_field, class_name if tag is None or tag is True else tag
