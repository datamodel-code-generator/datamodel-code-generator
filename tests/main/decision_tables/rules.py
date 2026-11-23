"""The known differences of the decision tables, as rules over their row parameters (the form of ``shape_families``).

A rule (``Kept`` of ``shape_families/kept.py``) covers the cells of one table, one kind and the contracts it names
whose parameters lie in one of its boxes, and states its basis: an issue (``#n``), a legacy row, by design or a library
limit (a short name of the decision, with the reason). ``differences`` of that module holds the cells against the
rules both ways.
"""

from __future__ import annotations

from typing import Final

from tests.main.shape_families.families import DATACLASSES, MSGSPEC, PYDANTIC_V2, TYPED_DICT
from tests.main.shape_families.kept import BOTH, BY_DESIGN, DEFAULT, ISSUE, LEGACY_ROW, LIBRARY_LIMIT, PRESET, Kept

from . import constraints, nulls, tags

_ALL_BUT_UNIQUE: Final = tuple(
    dict.fromkeys(
        keyword for kind in constraints.TYPES.values() for keyword in kind.keywords if keyword != "uniqueItems"
    )
)
_FORMAT_TYPES: Final = ("date", "date-time", "ipv4", "uuid")
_PLAIN_TYPES: Final = ("string", "integer", "int64", "number", "double", "array")
_ALL_TYPES_BUT_MAP: Final = tuple(name for name in constraints.TYPES if name != "map")
_PYDANTIC_NONE: Final = (PYDANTIC_V2, DATACLASSES)
"""The outputs whose optional field is written with the default None, which the field admits."""

CONSTRAINTS: Final[tuple[Kept, ...]] = (
    Kept(
        ISSUE,
        "#7198",
        constraints.TABLE,
        "accepts-invalid",
        DEFAULT,
        ({"type": ("uri",), "backend": (PYDANTIC_V2,)},),
        "a URL is written AnyUrl and every string keyword beside it is left out without a warning (row "
        "legacy-anyurl-keeps-string-keywords, written as the plain string they check under "
        "--keep-string-keywords-beside-formats)",
    ),
    Kept(
        LIBRARY_LIMIT,
        "pydantic-format-types",
        constraints.TABLE,
        "accepts-invalid",
        BOTH,
        ({"type": _FORMAT_TYPES, "backend": (PYDANTIC_V2,)},),
        "Pydantic's date, datetime, IPv4Address and UUID check no string keyword on the text the format names",
    ),
    Kept(
        LIBRARY_LIMIT,
        "msgspec-uuid",
        constraints.TABLE,
        "accepts-invalid",
        PRESET,
        ({"type": ("uuid",), "backend": (MSGSPEC,)},),
        "with the standard primitive types of the preset msgspec decodes a UUID, which checks no pattern on its text",
    ),
    Kept(
        ISSUE,
        "#7345",
        constraints.TABLE,
        "accepts-invalid",
        DEFAULT,
        ({"type": ("byte", "email"), "backend": (PYDANTIC_V2,)},),
        "Base64Str and EmailStr are written without the string keywords beside them unless --field-constraints",
    ),
    Kept(
        ISSUE,
        "#5107",
        constraints.TABLE,
        "accepts-invalid",
        DEFAULT,
        (
            {
                "position": ("all-of-member",),
                "site": ("property",),
                "type": (*_PLAIN_TYPES, "map"),
                "keyword": _ALL_BUT_UNIQUE,
                "backend": (PYDANTIC_V2,),
            },
            {
                "position": ("all-of-member",),
                "site": ("item",),
                "type": _PLAIN_TYPES,
                "keyword": _ALL_BUT_UNIQUE,
                "backend": (PYDANTIC_V2,),
            },
            {
                "position": ("all-of-member",),
                "site": ("item",),
                "type": _ALL_TYPES_BUT_MAP,
                "keyword": _ALL_BUT_UNIQUE,
                "backend": (MSGSPEC,),
            },
        ),
        "an allOf of a $ref and a member that adds keywords is written as the referenced type, without them (closed "
        "with the opt-in --use-site-models-for-constrained-refs)",
    ),
    Kept(
        ISSUE,
        "#7181",
        constraints.TABLE,
        "accepts-invalid",
        DEFAULT,
        ({"type": _FORMAT_TYPES, "position": ("all-of-member",), "site": ("root",), "backend": (MSGSPEC,)},),
        "an allOf definition over a date, a time, a UUID or an IP address keeps no string keyword of its member, "
        "though msgspec keeps the value a str (row legacy-all-of-root-format-kinds, kept under "
        "--keep-string-keywords-beside-formats where msgspec writes the format as str)",
    ),
    Kept(
        LIBRARY_LIMIT,
        "unique-items",
        constraints.TABLE,
        "accepts-invalid",
        BOTH,
        ({"keyword": ("uniqueItems",)},),
        "a list checks no uniqueness, and the set of --use-unique-items-as-set-by-value drops a duplicate silently",
    ),
    Kept(
        ISSUE,
        "#7346",
        constraints.TABLE,
        "accepts-invalid",
        BOTH,
        ({"type": ("map",), "position": ("union-member",)},),
        "the property counts of a map that is a member of a union are left out",
    ),
    Kept(
        ISSUE,
        "#7340",
        constraints.TABLE,
        "accepts-invalid",
        DEFAULT,
        ({"type": ("map",), "position": ("all-of-member",), "site": ("root", "item")},),
        "the property counts an allOf member adds to a referenced map are left out at a definition and in array "
        "items, also with --use-site-models-for-constrained-refs (row legacy-all-of-map-counts-dropped)",
    ),
)
"""The known differences of the constraint applicability table."""

NULLS: Final[tuple[Kept, ...]] = (
    Kept(
        BY_DESIGN,
        "optional-defaults-to-none",
        nulls.TABLE,
        "accepts-invalid",
        BOTH,
        ({"source": ("none",), "required": (False,), "position": ("property", "root"), "backend": _PYDANTIC_NONE},),
        "a property that is not required is written with the default None, which the field admits",
    ),
    Kept(
        BY_DESIGN,
        "type-list-null-beside-enum",
        nulls.TABLE,
        "accepts-invalid",
        BOTH,
        (
            {
                "source": ("type-list",),
                "kind": ("enum",),
                "position": ("property",),
                "required": (False,),
                "backend": (*_PYDANTIC_NONE, MSGSPEC),
            },
            {
                "source": ("type-list",),
                "kind": ("enum",),
                "position": ("root",),
                "required": (False,),
                "backend": _PYDANTIC_NONE,
            },
        ),
        "a type list with null beside an enum that lists no null admits null, as the author of the type list means",
    ),
    Kept(
        BY_DESIGN,
        "type-list-null-beside-enum",
        nulls.TABLE,
        "accepts-invalid",
        DEFAULT,
        ({"source": ("type-list",), "kind": ("enum",), "position": ("property",), "required": (True,)},),
        "the same at a required property, which the preset's --strict-nullable writes without null",
    ),
    Kept(
        BY_DESIGN,
        "nullable-beside-enum",
        nulls.TABLE,
        "accepts-invalid",
        DEFAULT,
        (
            {
                "source": ("nullable",),
                "kind": ("enum",),
                "position": ("property",),
                "required": (False,),
                "backend": _PYDANTIC_NONE,
            },
            {
                "source": ("nullable",),
                "kind": ("enum",),
                "position": ("root",),
                "required": (False,),
                "backend": (*_PYDANTIC_NONE, TYPED_DICT),
            },
            {
                "source": ("nullable",),
                "kind": ("enum",),
                "position": ("root",),
                "required": (True,),
                "backend": (TYPED_DICT,),
            },
        ),
        "OpenAPI's nullable beside an enum that lists no null admits null where the output reads it without "
        "--strict-nullable, though OpenAPI 3.0.3 needs null in the enum",
    ),
    Kept(
        BY_DESIGN,
        "nullable-beside-enum",
        nulls.TABLE,
        "accepts-invalid",
        PRESET,
        ({"source": ("nullable",), "kind": ("enum",), "position": ("property", "root")},),
        "the same wherever --strict-nullable reads the nullable",
    ),
    Kept(
        BY_DESIGN,
        "nullable-beside-all-of-ref",
        nulls.TABLE,
        "accepts-invalid",
        DEFAULT,
        (
            {
                "source": ("nullable",),
                "kind": ("ref",),
                "position": ("property", "root"),
                "required": (False,),
                "backend": _PYDANTIC_NONE,
            },
        ),
        "nullable beside an allOf of one $ref, the OpenAPI 3.0 idiom of a nullable reference, admits null, though "
        "OpenAPI 3.0.3 adds null only to a type beside it",
    ),
    Kept(
        BY_DESIGN,
        "nullable-beside-all-of-ref",
        nulls.TABLE,
        "accepts-invalid",
        PRESET,
        ({"source": ("nullable",), "kind": ("ref",), "position": ("property", "root", "item", "value")},),
        "the same wherever --strict-nullable reads the nullable, an array item and a map value included under "
        "--keep-null-of-nullable-items",
    ),
    Kept(
        ISSUE,
        "#1496",
        nulls.TABLE,
        "rejects-valid",
        DEFAULT,
        (
            {
                "source": ("nullable",),
                "kind": ("string", "array", "map"),
                "position": ("property",),
                "required": (False,),
                "backend": (MSGSPEC, TYPED_DICT),
            },
            {
                "source": ("nullable",),
                "kind": ("string", "array", "map"),
                "position": ("property",),
                "required": (True,),
            },
            {"source": ("nullable",), "kind": ("string", "array", "map"), "position": ("item", "value")},
            {
                "source": ("nullable",),
                "kind": ("map",),
                "position": ("root",),
                "required": (False,),
                "backend": (MSGSPEC, TYPED_DICT),
            },
            {"source": ("nullable",), "kind": ("map",), "position": ("root",), "required": (True,)},
        ),
        "OpenAPI's nullable admits null only under --strict-nullable (decided in #1496), so without it a property the "
        "output writes without the default None, an array item and a map value reject null",
    ),
    Kept(
        ISSUE,
        "#4677",
        nulls.TABLE,
        "model-raises",
        BOTH,
        ({"input": ("jsonschema",), "source": ("enum-null",), "backend": (MSGSPEC,)},),
        "an enum that lists null is an Enum with a None member, which msgspec cannot decode, unless "
        "--use-msgspec-nullable-enum-alias (#4677)",
    ),
)
"""The known differences of the null table."""

_ENUM2: Final = ("enum2-str", "enum2-int")
_SHARED_PAIR: Final = {"count": (2,), "shared": (True,)}
"""The rows where the earlier union lists every variant of ``Pet``, so the Structs of ``Pet`` are tagged by it."""

_SEVERAL_TAGS_RAISE: Final = (
    {"form": ("enum2-int",), "count": (3,), "backend": (MSGSPEC,)},
    {"form": _ENUM2, "shared": (False,), "backend": (MSGSPEC,)},
    {"form": _ENUM2, **_SHARED_PAIR, "mapping": (True,), "backend": (MSGSPEC,)},
    {"form": ("enum2-int",), **_SHARED_PAIR, "backend": (MSGSPEC,)},
)
"""The rows whose msgspec union of a variant with several tags raises under both contracts."""

TAGS: Final[tuple[Kept, ...]] = (
    Kept(
        ISSUE,
        "#7102",
        tags.TABLE,
        "rejects-valid",
        PRESET,
        (
            {
                "form": ("enum1-str", *_ENUM2),
                "count": (3,),
                "nullable": (True,),
                "mapping": (False,),
                "backend": (PYDANTIC_V2,),
            },
            {"nullable": (True,), "kind": ("anyOf",), "backend": (PYDANTIC_V2,)},
        ),
        "a discriminator whose tag also admits null: the model imports and rejects a tag or the null tag",
    ),
    Kept(
        ISSUE,
        "#7101",
        tags.TABLE,
        "model-raises",
        DEFAULT,
        (*_SEVERAL_TAGS_RAISE, {"form": ("enum2-str",), "count": (3,), "shared": (True,), "backend": (MSGSPEC,)}),
        "a msgspec Struct whose variant has several tags leaves its union undecodable (the generation warns)",
    ),
    Kept(
        ISSUE,
        "#7101",
        tags.TABLE,
        "model-raises",
        PRESET,
        (
            *_SEVERAL_TAGS_RAISE,
            {"form": ("enum2-str",), "count": (3,), "shared": (True,), "mapping": (True,), "backend": (MSGSPEC,)},
            {"form": ("enum2-str",), "count": (3,), "shared": (True,), "nullable": (True,), "backend": (MSGSPEC,)},
        ),
        "the same under the preset, but where the earlier union of two variants tags Cat by its first tag alone",
    ),
    Kept(
        ISSUE,
        "#7101",
        tags.TABLE,
        "rejects-valid",
        BOTH,
        (
            {"form": ("enum2-str",), **_SHARED_PAIR, "nullable": (True,), "mapping": (False,), "backend": (MSGSPEC,)},
            {
                "form": ("enum2-int",),
                **_SHARED_PAIR,
                "nullable": (True,),
                "mapping": (False,),
                "kind": ("anyOf",),
                "backend": (MSGSPEC,),
            },
            {
                "form": ("enum1-str", "schema-name"),
                **_SHARED_PAIR,
                "nullable": (True,),
                "kind": ("anyOf",),
                "backend": (MSGSPEC,),
            },
            {"form": ("enum2-str",), **_SHARED_PAIR, "nullable": (False,), "mapping": (False,), "backend": (MSGSPEC,)},
        ),
        "a Struct that an earlier union tags takes that union's one tag, so the later union refuses its other tags and "
        "its null tag",
    ),
    Kept(
        ISSUE,
        "#7101",
        tags.TABLE,
        "rejects-valid",
        PRESET,
        (
            {
                "form": ("enum2-str",),
                "count": (3,),
                "nullable": (False,),
                "mapping": (False,),
                "shared": (True,),
                "backend": (MSGSPEC,),
            },
        ),
        "the same where the earlier union lists two of three variants",
    ),
    Kept(
        ISSUE,
        "#4713",
        tags.TABLE,
        "model-raises",
        DEFAULT,
        (
            {"form": ("enum1-str", "schema-name"), "count": (3,), "backend": (MSGSPEC,)},
            {"form": ("enum1-str", "schema-name"), "shared": (False,), "backend": (MSGSPEC,)},
            {"form": ("inherited",), "backend": (MSGSPEC,)},
        ),
        "msgspec cannot decode a union of Structs that are not all tagged; the generation warns and names "
        "--use-msgspec-tagged-unions",
    ),
    Kept(
        ISSUE,
        "#4713",
        tags.TABLE,
        "model-raises",
        PRESET,
        (
            {"form": ("enum1-str", "schema-name"), "count": (3,), "nullable": (True,), "backend": (MSGSPEC,)},
            {"form": ("enum1-str", "schema-name"), "shared": (False,), "nullable": (True,), "backend": (MSGSPEC,)},
            {"form": ("schema-name", "inherited"), "count": (3,), "nullable": (False,), "backend": (MSGSPEC,)},
            {"form": ("schema-name",), "shared": (False,), "nullable": (False,), "backend": (MSGSPEC,)},
            {"form": ("inherited",), "count": (2,), "backend": (MSGSPEC,)},
        ),
        "the same under the preset, which does not turn on --use-msgspec-tagged-unions",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-tag-from-earlier-union",
        tags.TABLE,
        "rejects-valid",
        DEFAULT,
        ({"form": _ENUM2, "mapping": (True,), "shared": (True,), "backend": (PYDANTIC_V2,)},),
        "a variant keeps the tags of the first union that tags it, so a later union's own tag of it is refused "
        "(fixed by --merge-shared-variant-tags)",
    ),
    Kept(
        ISSUE,
        "#7344",
        tags.TABLE,
        "accepts-invalid",
        BOTH,
        ({"form": ("schema-name",), "backend": (TYPED_DICT,)},),
        "TypedDict writes a tag property the schema leaves a plain string as str, where Pydantic and msgspec write "
        "the Literal of the variant's tags, so any tag is accepted",
    ),
)
"""The known differences of the tag plan table."""

RULES: Final[dict[str, tuple[Kept, ...]]] = {
    constraints.TABLE: CONSTRAINTS,
    nulls.TABLE: NULLS,
    tags.TABLE: TAGS,
}
"""Every table's rules by table name."""
