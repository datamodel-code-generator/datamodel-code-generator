"""Where a schema object's null comes from, and how each parse site reads it.

A schema object admits null through its own keywords: a ``type`` of null or a ``type`` list holding null, an
``anyOf`` or ``oneOf`` member typed null, ``nullable`` (OpenAPI 3.0 and many JSON Schema documents), or a null in its
``enum``. :class:`NullSources` records them as written, and :func:`resolve_null_facts` returns what one site of
``parser/jsonschema.py`` reads from them, with the row of :data:`NullRule` the answer comes from. Every input is an
immutable fact, so the function reads nothing else; the adapter ``parser/jsonschema.py:_null_sources`` reads the facts
from the live schema object and ``JsonSchemaParser._null_mode`` the options.

The sites disagree today on which sources count and whether ``nullable`` needs ``--strict-nullable``. The rows named
``legacy-*`` keep that output; the fix of each (G12, G01 decision 3) deletes its row.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, final

from datamodel_code_generator._records import record

__all__ = [
    "NO_NULL_MODE",
    "NullFacts",
    "NullMode",
    "NullRule",
    "NullSite",
    "NullSources",
    "resolve_null_facts",
]

NullSite: TypeAlias = Literal[
    "schema-type",
    "field",
    "scalar",
    "object-model",
    "root-model",
    "ref-target",
    "ref-nullable-only",
    "type-union",
    "object-map",
    "all-of-map",
    "all-of-value",
    "root-type",
    "multiple-types-root",
    "array-field",
    "enum",
    "enum-literal",
    "combined-branch",
    "inherited-child",
]
"""The parse sites that read where null comes from.

* ``schema-type``: ``JsonSchemaObject.type_has_null``, which fields and models record.
* ``field``: the ``nullable`` of a property field (``JsonSchemaParser.get_object_field``).
* ``scalar``: the null beside one scalar ``type`` (``JsonSchemaParser._get_data_type``).
* ``object-model``: the ``nullable`` of an object model (``JsonSchemaParser._object_nullable``).
* ``root-model``: the ``nullable`` of a root model (``JsonSchemaParser._create_registered_root_model``).
* ``ref-target``: the null a referenced schema gives every reference to it (``JsonSchemaParser.get_ref_data_type``).
* ``ref-nullable-only``: a ``$ref`` beside only ``nullable: true`` (``JsonSchemaParser._parse_ref_nullable_only_item``).
* ``type-union``: the union of the types of a ``type`` list (``JsonSchemaParser._parse_constrained_type_union``,
  ``JsonSchemaParser.parse_array_fields``).
* ``object-map``: the dict of an object map (``JsonSchemaParser._object_map_admits_null``).
* ``all-of-map``, ``all-of-value``: the map or the value an ``allOf`` merges its parts into
  (``JsonSchemaParser._merge_all_of_root_schema``, ``JsonSchemaParser._all_of_root_value_schema``).
* ``root-type``: the root field of a root model (``JsonSchemaParser._parse_root_type_with_context``).
* ``multiple-types-root``: the root field of a ``type`` list holding an object with properties
  (``JsonSchemaParser._parse_multiple_types_with_properties``).
* ``array-field``: the field of an array (``JsonSchemaParser._resolve_array_field_required_nullable``).
* ``enum``: a null in the ``enum`` of an Enum class (``JsonSchemaParser.parse_enum``, ``_enum_entries_skip_null``).
* ``enum-literal``: a null among the values of an enum written as a ``Literal``
  (``JsonSchemaParser._literal_values_type``).
* ``combined-branch``: an ``anyOf`` or ``oneOf`` member that is ``nullable``
  (``JsonSchemaParser._add_nullable_combined_schema_branches``).
* ``inherited-child``: a property that overrides an inherited one, which drops the null of the inherited type unless
  it admits null itself (``JsonSchemaParser._merge_inherited_type_shape_dict``,
  ``JsonSchemaParser._mark_partial_inherited_fields``).
"""

NullRule: TypeAlias = Literal[
    "type-or-member-null",
    "type-excludes-member-null",
    "field-declared",
    "field-strict-not-null",
    "field-undecided",
    "strict-nullable-wrapper",
    "legacy-nullable-as-type-list",
    "scalar-not-null",
    "object-type-or-strict-nullable",
    "legacy-root-model-type-only",
    "legacy-ref-target-null-type-only",
    "strict-nullable-ref",
    "type-union-null",
    "map-type-list",
    "map-strict-nullable",
    "legacy-map-copied-nullable",
    "all-of-own-null",
    "all-of-every-part-null",
    "all-of-not-null",
    "force-optional",
    "legacy-root-type-nullable-ungated",
    "root-type-not-nullable",
    "legacy-multiple-types-nullable-not-recorded",
    "multiple-types-root-declared",
    "array-force-optional",
    "array-strict",
    "legacy-array-nullable-ungated",
    "array-not-nullable",
    "enum-null-as-nullable",
    "enum-null-member",
    "enum-without-null",
    "legacy-enum-literal-value-only",
    "nullable-branch-adds-null",
    "branch-keeps",
    "inherited-typed-without-null",
    "inherited-declared-not-null",
    "inherited-keeps-null",
]
"""The rows of :func:`resolve_null_facts`.

* ``type-or-member-null``: a ``type`` list holding null, or a direct ``anyOf``/``oneOf`` member typed null or with a
  ``type`` list holding null; ``nullable`` and ``enum`` do not count (``schema-type``).
* ``type-excludes-member-null``: with ``--intersect-allof-keywords`` (``NullMode.type_excludes_member_null``), except
  a member beside a ``type`` that is neither null nor a list holding null, without ``nullable: true``: that ``type``
  excludes null, so the combined schema drops its members typed only null (``null`` or ``["null"]``) and, when every
  member is one, is rejected as unsatisfiable (``JsonSchemaParser.parse_combined_schema``).
* ``field-declared``: under ``--strict-nullable`` or ``--use-missing-sentinel`` a field writes the ``nullable`` its
  schema declares.
* ``field-strict-not-null``: under ``--strict-nullable`` a required or defaulted field without ``nullable`` is not
  nullable.
* ``field-undecided``: otherwise the field leaves its nullability to its type and required state.
* ``strict-nullable-wrapper``: ``nullable`` under ``--strict-nullable`` adds null beside a scalar type.
* ``legacy-nullable-as-type-list``: OpenAPI reads the type as a ``type`` list holding null instead, from the moment
  ``JsonSchemaParser._get_data_type`` first builds the type of the schema object: the sites that read the schema
  object afterwards see the null of a ``type`` list, those before see none, and ``JsonSchemaObject.type_has_null``
  keeps the answer of its first read. RP13 deletes the row with rows ``legacy-root-model-type-only`` and
  ``legacy-ref-target-null-type-only``: a use writes the null of its target once, at render preparation.
* ``scalar-not-null``: a scalar type adds no null.
* ``object-type-or-strict-nullable``: an object model is nullable by ``type-or-member-null`` or by ``nullable: true``
  under ``--strict-nullable``.
* ``legacy-root-model-type-only``: a root model is nullable by ``type-or-member-null`` only; ``nullable`` does not
  count (O3). RP13 deletes the row (see ``legacy-nullable-as-type-list``).
* ``legacy-ref-target-null-type-only``: a referenced schema makes its references nullable when its ``type`` is the one
  string null, or by ``nullable: true`` under ``--strict-nullable``; a ``type`` list holding null does not count.
  RP13 deletes the row (see ``legacy-nullable-as-type-list``).
* ``strict-nullable-ref``: a ``$ref`` beside only ``nullable: true`` admits null under ``--strict-nullable``.
* ``type-union-null``: the union of a ``type`` list admits null by ``type-or-member-null``.
* ``map-type-list``: an object map whose ``type`` list holds object and null admits null.
* ``map-strict-nullable``: an object map without a ``type`` list admits null by ``nullable`` under
  ``--strict-nullable``.
* ``legacy-map-copied-nullable``: except a ``nullable`` that a combined schema copied into its branch, whose union
  admits it.
* ``all-of-own-null``: the schema an ``allOf`` merges admits null by its own ``nullable`` or its own ``type`` list
  holding null, also without ``--strict-nullable``, as root types read it (``legacy-root-type-nullable-ungated``).
* ``all-of-every-part-null``: a merged map also admits null when every typed part lists null (G01 decision 3).
* ``all-of-not-null``: neither holds.
* ``force-optional``: ``--force-optional`` makes a root field optional and leaves its nullability undecided.
* ``legacy-root-type-nullable-ungated``: a root field is optional and nullable by its ``nullable``, also without
  ``--strict-nullable``; ``type`` lists do not count.
* ``root-type-not-nullable``: otherwise its default decides.
* ``legacy-multiple-types-nullable-not-recorded``: the root field of a ``type`` list with an object is optional by
  ``nullable`` or ``type-or-member-null``, but records only the latter, under ``--strict-nullable`` or
  ``--use-missing-sentinel``.
* ``multiple-types-root-declared``: with ``--read-nullable-beside-type-list``
  (``NullMode.read_nullable_beside_type_list``), it writes that null under ``--strict-nullable`` or
  ``--use-missing-sentinel`` instead, as ``field-declared`` does.
* ``array-force-optional``: ``--force-optional`` makes an array field optional with undecided nullability.
* ``array-strict``: under ``--strict-nullable`` an array field is required without a default and writes the
  ``nullable`` its schema declares.
* ``legacy-array-nullable-ungated``: without it, ``nullable`` makes an array field optional and nullable.
* ``array-not-nullable``: otherwise an array field with a default is not nullable, one without is undecided.
* ``enum-null-as-nullable``: the null of a string or ``nullable`` enum makes its class nullable instead of a member.
* ``enum-null-member``: the null of another enum is a member unless the class decides otherwise.
* ``enum-without-null``: the enum lists no null.
* ``legacy-enum-literal-value-only``: an enum written as a ``Literal`` admits null by a null value alone, where an
  Enum class also needs a string type or ``nullable`` (``enum-null-as-nullable``).
* ``nullable-branch-adds-null``: a ``nullable`` member of a combined schema that does not admit null by its type adds a
  null member beside it, whatever ``--strict-nullable`` says.
* ``branch-keeps``: otherwise the member stays as written.
* ``inherited-typed-without-null``: a ``type`` without null, unless ``nullable: true``, drops the inherited null.
* ``inherited-declared-not-null``: so does ``nullable: false`` beside no such ``type``.
* ``inherited-keeps-null``: otherwise the inherited type keeps its null.
"""


@final
@record
class NullSources:
    """The keywords of one schema object that can admit null, as written.

    ``type_has_null``: the answer of the ``schema-type`` row that the schema object recorded when it was first asked
    (``JsonSchemaObject.type_has_null``), which the sites reading it keep. ``type_null``: ``type`` is the string
    null. ``type_list``: ``type`` is a list; ``type_lists_null``: one holding null; ``type_lists_object``: one holding
    object. ``typed``: ``type`` is set; ``type_string``: it is the string string. ``member_null``: a direct ``anyOf``
    or ``oneOf`` member is typed null or has a ``type`` list holding null. ``nullable``: the keyword's value, None
    when unset; ``copied_nullable``: a combined schema copied it into this branch. ``enum_null``: the ``enum`` lists
    null. The ``type`` facts are those of the ``type`` list OpenAPI reads once ``legacy-nullable-as-type-list``
    applied.
    """

    type_has_null: bool = False
    type_null: bool = False
    type_list: bool = False
    type_lists_null: bool = False
    type_lists_object: bool = False
    typed: bool = False
    type_string: bool = False
    member_null: bool = False
    nullable: bool | None = None
    copied_nullable: bool = False
    enum_null: bool = False


@final
@record
class NullMode:
    """The options that change how sites read null.

    ``nullable_as_type_list``: OpenAPI reads ``nullable`` beside one scalar ``type`` under ``--strict-nullable`` as a
    ``type`` list holding null. ``type_excludes_member_null``: ``--intersect-allof-keywords``, under which a ``type``
    that excludes null drops the null of the ``anyOf`` or ``oneOf`` members beside it (row
    ``type-excludes-member-null``). ``read_nullable_beside_type_list``: ``--read-nullable-beside-type-list``, under
    which the root of a ``type`` list with an object writes the null its ``nullable`` declares (row
    ``multiple-types-root-declared``).
    """

    strict_nullable: bool = False
    use_missing_sentinel: bool = False
    force_optional: bool = False
    nullable_as_type_list: bool = False
    type_excludes_member_null: bool = False
    read_nullable_beside_type_list: bool = False


NO_NULL_MODE: NullMode = NullMode()
"""The mode of sites that no option changes."""


@final
@record
class NullFacts:
    """What a site reads, and the row of :data:`NullRule` the answer comes from.

    ``admits_null`` is None when the site leaves it undecided, ``required`` when the site does not decide it.
    """

    rule: NullRule
    admits_null: bool | None = None
    required: bool | None = None


def resolve_null_facts(  # noqa: PLR0911, PLR0912, PLR0913
    sources: NullSources,
    site: NullSite,
    mode: NullMode = NO_NULL_MODE,
    *,
    required: bool = False,
    has_default: bool = False,
    parts_null: bool = False,
) -> NullFacts:
    """Return what ``site`` reads from the null ``sources`` of one schema object under ``mode``.

    The match returns for every site but ``inherited-child``, whose rows follow it. ``required`` and ``has_default``
    are the required state and default of the field a site builds (``field``, ``array-field``); ``parts_null`` tells
    that every typed part of an ``allOf`` lists null (``all-of-map``).
    """
    type_null = sources.type_has_null
    match site:
        case "schema-type":
            if mode.type_excludes_member_null and sources.member_null and _type_excludes_null(sources):
                return NullFacts("type-excludes-member-null", admits_null=False)
            return NullFacts("type-or-member-null", admits_null=sources.type_lists_null or sources.member_null)
        case "field":
            if (mode.strict_nullable or mode.use_missing_sentinel) and sources.nullable is not None:
                return NullFacts("field-declared", admits_null=sources.nullable)
            if mode.strict_nullable and (has_default or required):
                return NullFacts("field-strict-not-null", admits_null=False)
            return NullFacts("field-undecided")
        case "scalar":
            if not (mode.strict_nullable and sources.nullable):
                return NullFacts("scalar-not-null", admits_null=False)
            if mode.nullable_as_type_list and sources.typed and not sources.type_list:
                return NullFacts("legacy-nullable-as-type-list", admits_null=True)
            return NullFacts("strict-nullable-wrapper", admits_null=True)
        case "object-model":
            return NullFacts(
                "object-type-or-strict-nullable",
                admits_null=type_null or (mode.strict_nullable and sources.nullable is True),
            )
        case "root-model":
            return NullFacts("legacy-root-model-type-only", admits_null=type_null)
        case "ref-target":
            return NullFacts(
                "legacy-ref-target-null-type-only",
                admits_null=sources.type_null or (mode.strict_nullable and sources.nullable is True),
            )
        case "ref-nullable-only":
            return NullFacts("strict-nullable-ref", admits_null=mode.strict_nullable)
        case "type-union":
            return NullFacts("type-union-null", admits_null=sources.type_list and type_null)
        case "object-map":
            if sources.type_list:
                return NullFacts("map-type-list", admits_null=sources.type_lists_object and sources.type_lists_null)
            if sources.copied_nullable:
                return NullFacts("legacy-map-copied-nullable", admits_null=False)
            return NullFacts("map-strict-nullable", admits_null=bool(mode.strict_nullable and sources.nullable))
        case "all-of-map" | "all-of-value":
            if sources.nullable or sources.type_lists_null:
                return NullFacts("all-of-own-null", admits_null=True)
            if site == "all-of-map" and parts_null:
                return NullFacts("all-of-every-part-null", admits_null=True)
            return NullFacts("all-of-not-null", admits_null=False)
        case "root-type":
            if mode.force_optional:
                return NullFacts("force-optional", admits_null=None, required=False)
            if sources.nullable:
                return NullFacts("legacy-root-type-nullable-ungated", admits_null=True, required=False)
            return NullFacts("root-type-not-nullable")
        case "multiple-types-root":
            declared = bool(sources.nullable) or type_null
            reads_nullable = mode.read_nullable_beside_type_list
            return NullFacts(
                "multiple-types-root-declared" if reads_nullable else "legacy-multiple-types-nullable-not-recorded",
                admits_null=(declared if reads_nullable else type_null)
                if (mode.strict_nullable or mode.use_missing_sentinel)
                else None,
                required=not (mode.force_optional or declared),
            )
        case "array-field":
            if mode.force_optional:
                return NullFacts("array-force-optional", admits_null=None, required=False)
            if mode.strict_nullable:
                return NullFacts("array-strict", admits_null=sources.nullable, required=not has_default)
            if sources.nullable:
                return NullFacts("legacy-array-nullable-ungated", admits_null=True, required=False)
            return NullFacts("array-not-nullable", admits_null=False if has_default else None, required=not has_default)
        case "enum":
            if not sources.enum_null:
                return NullFacts("enum-without-null", admits_null=False)
            if sources.type_string or sources.nullable:
                return NullFacts("enum-null-as-nullable", admits_null=True)
            return NullFacts("enum-null-member")
        case "enum-literal":
            return NullFacts("legacy-enum-literal-value-only", admits_null=sources.enum_null)
        case "combined-branch":
            if sources.nullable and not type_null:
                return NullFacts("nullable-branch-adds-null", admits_null=True)
            return NullFacts("branch-keeps")
    if sources.typed and not type_null and sources.nullable is not True:
        return NullFacts("inherited-typed-without-null", admits_null=False)
    if sources.nullable is False:
        return NullFacts("inherited-declared-not-null", admits_null=False)
    return NullFacts("inherited-keeps-null")


def _type_excludes_null(sources: NullSources) -> bool:
    """Return whether the schema object's own ``type`` excludes null: set, without null, and not ``nullable: true``."""
    return sources.typed and not (sources.type_null or sources.type_lists_null) and sources.nullable is not True
