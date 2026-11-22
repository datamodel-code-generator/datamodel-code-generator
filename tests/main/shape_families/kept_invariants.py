"""The known breaks of the shape families' invariants and their ty errors, as rules over family parameters.

The rules have the form of ``kept.py``: each covers the cells of one family, one kind and the contracts it names whose
parameters lie in one of its boxes, with its basis (an issue or a legacy row) and reference. ``INVARIANTS`` holds the
kinds of ``invariants.KINDS``; ``TYPES`` holds ty's rules (the nightly static tier). Both are held to the cells both
ways by ``kept.differences``: under the default output the set is frozen, under the newest dated preset it is the
to-do list.
"""

from __future__ import annotations

from typing import Final

from .families import DATACLASSES, MSGSPEC, PYDANTIC_V2
from .invariants import ANY_TYPE, BUILD_FAILS, DUPLICATE_WIRE_KEY, IMPORT_FAILS, LOST_PROPERTY
from .kept import BOTH, DEFAULT, ISSUE, LEGACY_ROW, PRESET, Kept

_NARROWED: Final = ("str", "int", "str_min", "int_min", "arr", "str_or_arr")
_ENUM_TAGS: Final = ("enum1-str", "enum2-str", "enum2-int")
_MULTIPLE_TAGS: Final = ("enum2-str", "enum2-int")
_NARROWING_LEAVES: Final = ("arr", "enum", "int", "num", "str", "str_or_arr")
_REQUIRED_LEAVES: Final = ("str", "int", "num", "str_min", "int_min", "enum", "arr", "str_or_arr")

INVARIANTS: Final[tuple[Kept, ...]] = (
    Kept(
        LEGACY_ROW,
        "legacy-all-of-alias-member-base",
        "extend",
        LOST_PROPERTY,
        DEFAULT,
        ({"via": ("alias",), "how": ("add", "narrow")}, {"via": ("alias",), "how": ("require",), "form": ("beside",)}),
        "a child whose allOf names an alias of the base drops the alias as a root model and lacks the base's fields",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-all-of-alias-member-base",
        "extend",
        ANY_TYPE,
        DEFAULT,
        ({"via": ("alias",), "how": ("require",)}, {"via": ("alias",), "how": ("narrow",), "leaf": _NARROWED}),
        "the same child is written as a root model of Any, or types the property it narrows Any",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        ANY_TYPE,
        BOTH,
        (
            {"via": ("ref-siblings",), "how": ("narrow",), "leaf": _NARROWED},
            {"via": ("ref-siblings",), "how": ("require",), "child_first": (True,)},
        ),
        "a parent that is a $ref with sibling properties types the inherited property Any where the child narrows it "
        "or requires it before the parent is defined",
    ),
    Kept(
        ISSUE,
        "#7309",
        "extend",
        ANY_TYPE,
        PRESET,
        ({"via": ("alias",), "how": ("require",), "form": ("member",)},),
        "a child that only adds required, as an allOf member, to an alias parent is written as a root model of a dict "
        "of Any",
    ),
    Kept(
        ISSUE,
        "#6867",
        "union",
        ANY_TYPE,
        BOTH,
        ({"how": ("require",), "shape": ("flat", "nested-inline"), "form": ("beside",), "child_first": (True,)},),
        "the variants of a union parent whose allOf adds a required name inherit the name typed Any",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        ANY_TYPE,
        BOTH,
        ({"how": ("require",), "shape": ("nested-ref",)},),
        "the variant for a branch that is a named union inherits the required name typed Any",
    ),
    Kept(
        ISSUE,
        "#6305",
        "container",
        ANY_TYPE,
        DEFAULT,
        ({"container": ("map",), "nullable": (True,)},),
        "a map typed [object, null] has values of Any (the opt-in --use-typed-nullable-map-values types them)",
    ),
    Kept(
        ISSUE,
        "#7102",
        "tagged",
        IMPORT_FAILS,
        DEFAULT,
        ({"nullable": (True,), "backend": (PYDANTIC_V2,)},),
        "a discriminator whose tag also admits null fails the import of the Pydantic v2 model",
    ),
    Kept(
        ISSUE,
        "#7101",
        "tagged",
        BUILD_FAILS,
        BOTH,
        ({"tag": _MULTIPLE_TAGS, "backend": (MSGSPEC,)},),
        "msgspec has no decoder for a union whose Struct has several tags (the generation warns)",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        BUILD_FAILS,
        DEFAULT,
        ({"tag": ("enum1-str",), "backend": (MSGSPEC,)},),
        "msgspec has no decoder for a union of Structs that are not all tagged; the generation warns and names "
        "--use-msgspec-tagged-unions",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        BUILD_FAILS,
        PRESET,
        ({"tag": ("enum1-str",), "nullable": (True,), "backend": (MSGSPEC,)},),
        "msgspec has no decoder for an enum tag with a null member nor for the untagged union of its Structs; the "
        "generation warns",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged-allof",
        BUILD_FAILS,
        BOTH,
        ({"backend": (MSGSPEC,)},),
        "msgspec has no decoder for the union of the inline variants, which subclass Structs that keep the tag as a "
        "field and so are not tagged; the generation warns, also under --use-msgspec-tagged-unions",
    ),
    Kept(
        ISSUE,
        "#7103",
        "redeclare",
        DUPLICATE_WIRE_KEY,
        DEFAULT,
        ({"name": ("Pet",), "override": ("other",), "backend": (PYDANTIC_V2,)},),
        "the base's field named like its type is renamed with an alias and the child's redeclaration is not, so the "
        "child has two fields for the wire key Pet",
    ),
)
"""Every known break of an invariant of the shape families."""

_CONSTRAINED_CALLS: Final = "#625"
_MUTABLE_NARROWING: Final = "#7355"
_UNASSIGNABLE_NARROWING: Final = "#7356"
_TAG_MARKER: Final = "#7357"
_FIELD_NAMED_LIKE_TYPE: Final = "#7358"

INVALID_TYPE_FORM: Final = "invalid-type-form"
INVALID_MUTABLE_OVERRIDE: Final = "invalid-mutable-override"
INVALID_ATTRIBUTE_OVERRIDE: Final = "invalid-attribute-override"

TYPES: Final[tuple[Kept, ...]] = (
    Kept(
        ISSUE,
        _CONSTRAINED_CALLS,
        "extend",
        INVALID_TYPE_FORM,
        DEFAULT,
        (
            {"how": ("narrow",), "leaf": ("int", "str", "str_or_arr"), "via": ("direct", "allof-siblings")},
            {"leaf": ("int_min", "str_min")},
        ),
        "the default writes Pydantic's con* calls, which no type checker reads in an annotation; the owner keeps them "
        "for the users who rely on them, and --field-constraints writes plain types",
    ),
    Kept(
        ISSUE,
        _CONSTRAINED_CALLS,
        "mixin",
        INVALID_TYPE_FORM,
        DEFAULT,
        (
            {"first": ("int_min",), "second": ("int", "num")},
            {"first": ("int", "int_min", "num"), "second": ("int_min",)},
            {"first": ("nullable_str", "str", "str_or_arr"), "second": ("str_min",)},
            {"first": ("str_min",), "second": ("nullable_str", "str", "str_min", "str_or_arr")},
        ),
        "the same con* calls for a bounded property of a mixin",
    ),
    Kept(
        ISSUE,
        _CONSTRAINED_CALLS,
        "container",
        INVALID_TYPE_FORM,
        DEFAULT,
        ({"container": ("pattern-map", "names-map"), "backend": (PYDANTIC_V2,)},),
        "the same con* call for the keys of a patternProperties or propertyNames map",
    ),
    Kept(
        ISSUE,
        _MUTABLE_NARROWING,
        "extend",
        INVALID_MUTABLE_OVERRIDE,
        DEFAULT,
        (
            {"how": ("narrow",), "leaf": ("num",), "via": ("ref-siblings",)},
            {"how": ("require",), "leaf": _NARROWING_LEAVES, "via": ("ref-siblings",), "child_first": (False,)},
            {"how": ("require",), "leaf": _NARROWING_LEAVES, "via": ("direct", "allof-siblings")},
        ),
        "a child class redeclares an inherited mutable field with a narrower type (required, or integer for number)",
    ),
    Kept(
        ISSUE,
        _MUTABLE_NARROWING,
        "extend",
        INVALID_MUTABLE_OVERRIDE,
        PRESET,
        (
            {"how": ("narrow",), "leaf": ("num",)},
            {"how": ("require",), "leaf": _REQUIRED_LEAVES, "form": ("beside",), "child_first": (False,)},
            {
                "how": ("require",),
                "leaf": _REQUIRED_LEAVES,
                "form": ("member",),
                "child_first": (False,),
                "via": ("direct", "allof-siblings", "ref-siblings"),
            },
            {
                "how": ("require",),
                "leaf": _REQUIRED_LEAVES,
                "form": ("beside",),
                "child_first": (True,),
                "via": ("direct", "alias", "allof-siblings"),
            },
            {
                "how": ("require",),
                "leaf": _REQUIRED_LEAVES,
                "form": ("member",),
                "child_first": (True,),
                "via": ("direct", "allof-siblings"),
            },
        ),
        "the same under the preset, where bounded scalars are typed too",
    ),
    Kept(
        ISSUE,
        _MUTABLE_NARROWING,
        "union",
        INVALID_MUTABLE_OVERRIDE,
        BOTH,
        ({"how": ("require",), "form": ("beside",), "child_first": (False,)},),
        "each variant redeclares the name its union member declares optional as a required string",
    ),
    Kept(
        ISSUE,
        _MUTABLE_NARROWING,
        "redeclare",
        INVALID_MUTABLE_OVERRIDE,
        DEFAULT,
        (
            {
                "inherited": ("model",),
                "name": ("pet",),
                "override": ("other",),
                "backend": (PYDANTIC_V2, DATACLASSES, MSGSPEC),
            },
        ),
        "the child redeclares the inherited field with the subclass Dog of its type Pet",
    ),
    Kept(
        ISSUE,
        _MUTABLE_NARROWING,
        "redeclare",
        INVALID_MUTABLE_OVERRIDE,
        PRESET,
        (
            {"inherited": ("model",), "override": ("other",), "backend": (PYDANTIC_V2, MSGSPEC)},
            {"name": ("pet",), "override": ("other",), "backend": (DATACLASSES,)},
        ),
        "the same under the preset, which renames the field Pet to pet with an alias where the output has aliases",
    ),
    Kept(
        ISSUE,
        _UNASSIGNABLE_NARROWING,
        "extend",
        INVALID_ATTRIBUTE_OVERRIDE,
        DEFAULT,
        (
            {"how": ("narrow",), "leaf": ("enum",), "via": ("direct", "allof-siblings", "ref-siblings")},
            {"how": ("narrow",), "leaf": ("str_or_arr",), "via": ("direct", "allof-siblings")},
        ),
        "a child narrows an inherited enum to a new Enum class, or an inherited string-or-array union to root model "
        "classes, neither of which the inherited type admits",
    ),
    Kept(
        ISSUE,
        _UNASSIGNABLE_NARROWING,
        "extend",
        INVALID_ATTRIBUTE_OVERRIDE,
        PRESET,
        (
            {"how": ("narrow",), "leaf": ("enum",)},
            {"how": ("narrow",), "leaf": ("str_or_arr",), "via": ("direct", "alias", "allof-siblings")},
        ),
        "the same under the preset, where the narrowed enum is a Literal of a member's value",
    ),
    Kept(
        ISSUE,
        _UNASSIGNABLE_NARROWING,
        "redeclare",
        INVALID_ATTRIBUTE_OVERRIDE,
        DEFAULT,
        (
            {
                "inherited": ("enum",),
                "name": ("pet",),
                "override": ("other",),
                "backend": (PYDANTIC_V2, DATACLASSES, MSGSPEC),
            },
        ),
        "the child redeclares the inherited enum field with the Enum class of a narrower enum",
    ),
    Kept(
        ISSUE,
        _UNASSIGNABLE_NARROWING,
        "redeclare",
        INVALID_ATTRIBUTE_OVERRIDE,
        PRESET,
        ({"inherited": ("enum",), "override": ("other",), "backend": (PYDANTIC_V2, MSGSPEC)},),
        "the same under the preset, where the narrower enum is a Literal of its one member's value",
    ),
    Kept(
        ISSUE,
        "#6281",
        "tagged-allof",
        INVALID_ATTRIBUTE_OVERRIDE,
        DEFAULT,
        (
            {"tag": _ENUM_TAGS, "mapping": (True,), "backend": (PYDANTIC_V2, MSGSPEC)},
            {"tag": _ENUM_TAGS, "nullable": (False,), "mapping": (False,), "backend": (PYDANTIC_V2,)},
            {"tag": ("enum1-str",), "nullable": (False,), "mapping": (False,), "backend": (MSGSPEC,)},
        ),
        "the inline variants redeclare the tag that their base types with an Enum class as a Literal of its values",
    ),
    Kept(
        ISSUE,
        "#6281",
        "tagged-allof",
        INVALID_ATTRIBUTE_OVERRIDE,
        PRESET,
        (
            {"tag": _ENUM_TAGS, "nullable": (True,), "mapping": (True,), "backend": (PYDANTIC_V2, MSGSPEC)},
            {"tag": _MULTIPLE_TAGS, "nullable": (False,), "mapping": (True,), "backend": (PYDANTIC_V2, MSGSPEC)},
            {"tag": _MULTIPLE_TAGS, "nullable": (False,), "mapping": (False,), "backend": (PYDANTIC_V2,)},
        ),
        "the same under the preset, which writes a one-member tag enum as a Literal in the base too",
    ),
    Kept(
        ISSUE,
        _TAG_MARKER,
        "tagged-allof",
        INVALID_ATTRIBUTE_OVERRIDE,
        BOTH,
        (
            {"nullable": (True,), "mapping": (False,), "backend": (PYDANTIC_V2, MSGSPEC)},
            {"tag": _MULTIPLE_TAGS, "nullable": (False,), "mapping": (False,), "backend": (MSGSPEC,)},
        ),
        "without a mapping, an inline variant whose tag admits null, or has several values under msgspec, gets the "
        "internal path marker as its tag Literal",
    ),
    Kept(
        ISSUE,
        _FIELD_NAMED_LIKE_TYPE,
        "redeclare",
        INVALID_TYPE_FORM,
        DEFAULT,
        ({"name": ("Pet",), "backend": (DATACLASSES, MSGSPEC)},),
        "a field named like the class of its type keeps the name, and in the class body the name is the field",
    ),
    Kept(
        ISSUE,
        _FIELD_NAMED_LIKE_TYPE,
        "redeclare",
        INVALID_TYPE_FORM,
        PRESET,
        ({"name": ("Pet",), "backend": (DATACLASSES,)},),
        "the same under the preset for dataclasses, which have no alias to rename the field with",
    ),
)
"""Every known ty error of the shape families' modules (the nightly static tier)."""
