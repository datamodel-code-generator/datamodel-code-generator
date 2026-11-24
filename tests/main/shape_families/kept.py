"""The known differences of the shape families: rules over family parameters, each with its basis and reference.

A rule covers the cells of one family, one kind (of ``KINDS`` or a relation of ``relations.py``) and the contracts it
names whose parameters lie in one of its boxes. A box maps parameters (the backend among them) to the values it covers;
a parameter it leaves out takes any value. Its basis says why the difference is known, and its reference names the
record of that:

``issue``
    the issue that tracks the difference or decided it (``#n``);
``legacy row``
    the legacy row that keeps it in the default output (``scripts/legacy_rows.py``);
``by design``
    the output cannot hold the rule by its nature (a backend that validates nothing at run time); the reference is the
    keyword the payload suite's conformance table leaves unasserted for the backend
    (``BACKEND_UNASSERTED_MUTATION_CONSTRAINTS``);
``library limit``
    the backend's library decides otherwise than JSON Schema; the reference is that keyword of the same table.

Its reason says what the cells show.

``differences`` holds the cells against the rules both ways: every cell that differs is covered, and every cell a rule
covers differs. Under the default output the set is frozen: a new difference fails, and so does a fix until its rule
is narrowed or removed, so the rules only shrink. Under the newest dated preset the rules are the to-do list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final

from .families import DATACLASSES, MSGSPEC, PYDANTIC_V2, TYPED_DICT

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .verdict import Cell

ISSUE: Final = "issue"
LEGACY_ROW: Final = "legacy row"
BY_DESIGN: Final = "by design"
LIBRARY_LIMIT: Final = "library limit"
BASES: Final = (ISSUE, LEGACY_ROW, BY_DESIGN, LIBRARY_LIMIT)
"""Every basis a rule can rest on."""

DEFAULT: Final = frozenset({"default"})
PRESET: Final = frozenset({"preset"})
BOTH: Final = DEFAULT | PRESET

Box = dict[str, tuple[object, ...]]


@dataclass(frozen=True)
class Kept:
    """One known difference: its basis and reference, the cells it covers and what they show."""

    basis: str
    reference: str
    family: str
    kind: str
    contracts: frozenset[str]
    boxes: tuple[Box, ...]
    reason: str

    def covers(self, cell: Cell) -> bool:
        """Return whether the rule covers a cell: its family, a contract of the rule, parameters in one of its boxes."""
        if cell.shape.family != self.family or cell.contract not in self.contracts:
            return False
        params = cell.params
        return any(all(params[name] in values for name, values in box.items()) for box in self.boxes)


@dataclass(frozen=True)
class Differences:
    """How the cells of a family stand against the rules: cells that differ uncovered, and rules gone stale."""

    uncovered: tuple[str, ...]
    stale: tuple[str, ...]


def differences(cells: Iterable[Cell], rules: Iterable[Kept]) -> Differences:
    """Return the lines of the differences no rule covers and of the rules that cover a cell which does not differ."""
    cells, rules = tuple(cells), tuple(rules)
    return Differences(
        tuple(
            f"{cell.params} {cell.contract}: {kind} {witness}"
            for cell in cells
            for kind, witness in cell.findings.items()
            if not any(rule.kind == kind and rule.covers(cell) for rule in rules)
        ),
        tuple(
            f"{rule.reference} {rule.kind}: {cell.params} {cell.contract} no longer differs"
            for rule in rules
            for cell in cells
            if rule.covers(cell) and rule.kind not in cell.findings
        ),
    )


_ALL_MAPS: Final = ("pattern-map", "names-map")
_NO_RUNTIME: Final = (TYPED_DICT, DATACLASSES)
_STRING_LEAVES: Final = ("str", "str_min", "enum", "nullable_str")

KEPT: Final[tuple[Kept, ...]] = (
    Kept(
        LEGACY_ROW,
        "legacy-all-of-alias-member-base",
        "extend",
        "accepts-invalid",
        DEFAULT,
        ({"via": ("alias",)},),
        "a child whose allOf names an alias of the base takes the alias as its base and drops it as a root model, so "
        "it has none of the base's fields (#5177, fixed by --intersect-combined-schema-keywords)",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        "accepts-invalid",
        DEFAULT,
        (
            {
                "via": ("ref-siblings",),
                "how": ("narrow",),
                "leaf": ("str", "int", "str_min", "int_min", "arr", "str_or_arr"),
            },
            {"via": ("ref-siblings",), "how": ("require",), "child_first": (True,)},
        ),
        "a parent that is a $ref with sibling properties types the inherited property Any where the child narrows "
        "it, and leaves a required one optional where the child is defined first",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-type-narrowing-override-deferred",
        "extend",
        "accepts-invalid",
        DEFAULT,
        ({"leaf": ("num",), "how": ("narrow",), "via": ("direct", "allof-siblings")},),
        "a child that narrows an inherited number to an integer keeps the inherited float type",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-length-keywords-across-kinds",
        "extend",
        "rejects-valid",
        DEFAULT,
        ({"leaf": ("str_or_arr",), "how": ("narrow",), "via": ("direct", "allof-siblings")},),
        "maxItems that a child adds to an inherited string-or-array union also bounds the length of the string",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        "accepts-invalid",
        DEFAULT,
        ({"shape": ("nested-ref",)},),
        "allOf over a union whose branch is a named union: the variant for that branch has none of its fields",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        "rejects-valid",
        DEFAULT,
        ({"shape": ("nested-inline",)},),
        "allOf over a union whose branch is an inline union: the variant for that branch is missing",
    ),
    Kept(
        ISSUE,
        "#6867",
        "union",
        "accepts-invalid",
        DEFAULT,
        (
            {"how": ("require",), "shape": ("flat", "nested-inline"), "form": ("member",)},
            {"how": ("require",), "shape": ("flat", "nested-inline"), "form": ("beside",), "child_first": (True,)},
        ),
        "a required name that an allOf adds to a union parent is not required in the variants",
    ),
    Kept(
        ISSUE,
        "#7171",
        "mixin",
        "accepts-invalid",
        DEFAULT,
        (
            {"first": ("int", "int_min"), "second": ("num",), "swapped": (False,)},
            {"first": ("num",), "second": ("int", "int_min"), "swapped": (True,)},
        ),
        "an allOf merge of two refs takes the shared property from the later member: number after integer accepts "
        "1.5 (row legacy-all-of-object-merge-row)",
    ),
    Kept(
        ISSUE,
        "#7171",
        "mixin",
        "accepts-invalid",
        BOTH,
        (
            {"first": _STRING_LEAVES, "second": ("str_or_arr",)},
            {"first": ("str_or_arr",), "second": _STRING_LEAVES},
        ),
        "a string beside a string-or-array union is the union in both member orders, so it accepts an array: the "
        "merge is the string type beside the union, which a type beside an anyOf writes as the whole union",
    ),
    Kept(
        ISSUE,
        "#4849",
        "container",
        "accepts-invalid",
        BOTH,
        ({"container": ("prefix-tail", "prefix-closed"), "element": ("int", "ref")},),
        "positional arrays are written as a list of the union of their item types, with a warning that the positions "
        "are not checked, so an item in the wrong position is accepted",
    ),
    Kept(
        ISSUE,
        "#6935",
        "container",
        "accepts-invalid",
        DEFAULT,
        ({"container": _ALL_MAPS, "backend": (MSGSPEC,)},),
        "msgspec writes the key of a patternProperties or propertyNames map as bare str (fixed by "
        "--use-msgspec-meta-for-map-keys)",
    ),
    Kept(
        BY_DESIGN,
        "patternProperties",
        "container",
        "accepts-invalid",
        BOTH,
        ({"container": ("pattern-map",), "backend": _NO_RUNTIME},),
        "TypedDict and standard dataclasses validate nothing at run time; the keys of a patternProperties map are "
        "typed str",
    ),
    Kept(
        BY_DESIGN,
        "propertyNames",
        "container",
        "accepts-invalid",
        BOTH,
        ({"container": ("names-map",), "backend": _NO_RUNTIME},),
        "TypedDict and standard dataclasses validate nothing at run time; the keys of a propertyNames map are typed "
        "str",
    ),
    Kept(
        ISSUE,
        "#6305",
        "container",
        "accepts-invalid",
        DEFAULT,
        ({"container": ("map",), "nullable": (True,)},),
        "a map typed [object, null] loses its value type (closed in 0.91.0 with the opt-in "
        "--use-typed-nullable-map-values; the default keeps it)",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-map-null-left-to-field",
        "container",
        "rejects-valid",
        DEFAULT,
        ({"container": _ALL_MAPS, "nullable": (True,), "required": (False,), "backend": (TYPED_DICT,)},),
        "TypedDict drops the None of an optional nullable map that has a key schema (#6934, fixed by "
        "--use-single-dict-for-object-maps)",
    ),
    Kept(
        ISSUE,
        "#7102",
        "tagged",
        "rejects-valid",
        PRESET,
        ({"nullable": (True,), "backend": (PYDANTIC_V2,)},),
        "a discriminator whose tag also admits null: the model imports and rejects every tag",
    ),
    Kept(
        ISSUE,
        "#7101",
        "tagged",
        "model-raises",
        BOTH,
        ({"tag": ("enum2-str", "enum2-int"), "backend": (MSGSPEC,)},),
        "a msgspec Struct whose variant has several tags leaves its union undecodable (the generation warns)",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "model-raises",
        DEFAULT,
        ({"tag": ("enum1-str",), "backend": (MSGSPEC,)},),
        "msgspec cannot decode a union of Structs that are not all tagged; the generation warns and names "
        "--use-msgspec-tagged-unions",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "model-raises",
        PRESET,
        ({"tag": ("enum1-str",), "nullable": (True,), "backend": (MSGSPEC,)},),
        "msgspec cannot decode an enum tag with a null member nor the untagged union of its Structs; the generation "
        "warns and names --use-msgspec-nullable-enum-alias and --use-msgspec-tagged-unions",
    ),
    Kept(
        LIBRARY_LIMIT,
        "const",
        "tagged",
        "accepts-invalid",
        BOTH,
        ({"tag": ("const-int",), "nullable": (False,), "backend": (PYDANTIC_V2, TYPED_DICT)},),
        "Pydantic's Literal equality accepts true for the integer tag Literal[1], also in strict mode",
    ),
    Kept(
        LIBRARY_LIMIT,
        "enum",
        "tagged",
        "accepts-invalid",
        BOTH,
        (
            {"tag": ("enum2-int",), "nullable": (False,), "backend": (PYDANTIC_V2, TYPED_DICT)},
            {"tag": ("enum2-int",), "nullable": (True,), "backend": (TYPED_DICT,)},
        ),
        "Pydantic's Literal equality accepts true for the integer tags Literal[1, 3], also in strict mode",
    ),
    Kept(
        LIBRARY_LIMIT,
        "enum",
        "tagged",
        "accepts-invalid",
        PRESET,
        ({"tag": ("enum2-int",), "nullable": (True,), "mapping": (True,), "backend": (PYDANTIC_V2,)},),
        "the same under the preset, where the Pydantic model of a nullable tag with a mapping imports",
    ),
    Kept(
        LIBRARY_LIMIT,
        "const",
        "tagged",
        "rejects-valid",
        BOTH,
        ({"tag": ("const-int",), "backend": (MSGSPEC,)},),
        "msgspec rejects 1.0 for the integer tag 1, which JSON Schema reads as the same number",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-all-of-alias-member-base",
        "extend",
        "alias",
        DEFAULT,
        ({"via": ("alias",)},),
        "naming the base through an alias drops the base's fields, where naming it directly keeps them",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-all-of-alias-member-base",
        "extend",
        "beside",
        DEFAULT,
        ({"how": ("require",), "via": ("alias",), "form": ("beside",)},),
        "over an alias parent, required beside the allOf keeps it required where the same as a member does not",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        "order",
        DEFAULT,
        ({"how": ("require",), "via": ("ref-siblings",), "child_first": (True,)},),
        "a child of a $ref-with-siblings parent defined before it leaves the required property optional",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        "ref-siblings",
        DEFAULT,
        (
            {"how": ("require",), "via": ("ref-siblings",), "child_first": (True,)},
            {
                "via": ("ref-siblings",),
                "how": ("narrow",),
                "leaf": ("str", "int", "str_min", "int_min", "arr", "str_or_arr"),
            },
        ),
        "a parent written as a $ref with sibling properties decides unlike allOf: [$ref] beside them: Any where the "
        "child narrows, optional where it requires and is defined first",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        "reversed-definitions",
        DEFAULT,
        ({"how": ("require",), "via": ("ref-siblings",)},),
        "whether a child of a $ref-with-siblings parent keeps the property required follows the order of the "
        "definitions",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-type-narrowing-override-deferred",
        "extend",
        "ref-siblings",
        DEFAULT,
        ({"leaf": ("num",), "how": ("narrow",), "via": ("ref-siblings",)},),
        "the allOf spelling keeps the inherited float where the child narrows it to an integer; the $ref spelling "
        "narrows it",
    ),
    Kept(
        ISSUE,
        "#6867",
        "union",
        "order",
        DEFAULT,
        (
            {
                "shape": ("flat", "nested-inline"),
                "how": ("require",),
                "form": ("beside",),
                "child_first": (True,),
            },
        ),
        "required beside an allOf over a union holds in the variants only where the child is defined after them",
    ),
    Kept(
        ISSUE,
        "#6867",
        "union",
        "reversed-definitions",
        DEFAULT,
        ({"shape": ("flat", "nested-inline"), "how": ("require",), "form": ("beside",)},),
        "whether required beside an allOf over a union holds in the variants follows the order of the definitions",
    ),
    Kept(
        ISSUE,
        "#6867",
        "union",
        "beside",
        DEFAULT,
        ({"how": ("require",), "form": ("beside",)},),
        "required beside an allOf over a union and the same as its member decide differently on the variants",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        "inline",
        DEFAULT,
        ({"shape": ("nested-ref",)},),
        "a union branch that names a union and the same union inline give different variants (none of its fields, "
        "or missing)",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        "flat",
        DEFAULT,
        ({"shape": ("nested-inline",)},),
        "an inline union branch loses its variant, where the same branches written flat keep them",
    ),
    Kept(
        ISSUE,
        "#7171",
        "mixin",
        "member-order",
        DEFAULT,
        (
            {"first": ("int", "int_min"), "second": ("num",), "swapped": (True,)},
            {"first": ("num",), "second": ("int", "int_min"), "swapped": (True,)},
        ),
        "an allOf of a number and an integer declaration of one property takes the later member's type",
    ),
    Kept(
        ISSUE,
        "#7171",
        "mixin",
        "reversed-all-of",
        DEFAULT,
        ({"first": ("int", "int_min"), "second": ("num",)}, {"first": ("num",), "second": ("int", "int_min")}),
        "reversing the allOf members of a number and an integer declaration of one property changes its type",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-length-keywords-across-kinds",
        "mixin",
        "option:field_constraints",
        DEFAULT,
        ({"first": ("str_min",), "second": ("str_or_arr",)}, {"first": ("str_or_arr",), "second": ("str_min",)}),
        "the field forms write minLength of the merged string-or-array union as a length of the array too",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-length-keywords-across-kinds",
        "mixin",
        "option:use_annotated",
        DEFAULT,
        ({"first": ("str_min",), "second": ("str_or_arr",)}, {"first": ("str_or_arr",), "second": ("str_min",)}),
        "the field forms write minLength of the merged string-or-array union as a length of the array too",
    ),
    Kept(
        BY_DESIGN,
        "patternProperties",
        "container",
        "backends",
        BOTH,
        ({"container": ("pattern-map",), "backend": _NO_RUNTIME},),
        "TypedDict and standard dataclasses check no key of a patternProperties map, the Pydantic model does",
    ),
    Kept(
        BY_DESIGN,
        "propertyNames",
        "container",
        "backends",
        BOTH,
        ({"container": ("names-map",), "backend": _NO_RUNTIME},),
        "TypedDict and standard dataclasses check no key of a propertyNames map, the Pydantic model does",
    ),
    Kept(
        ISSUE,
        "#6935",
        "container",
        "backends",
        DEFAULT,
        ({"container": _ALL_MAPS, "backend": (MSGSPEC,)},),
        "msgspec checks no key of a patternProperties or propertyNames map, the Pydantic model does",
    ),
    Kept(
        ISSUE,
        "#7348",
        "container",
        "client",
        DEFAULT,
        ({"container": _ALL_MAPS, "nullable": (True,), "required": (False,), "backend": (MSGSPEC,)},),
        "the client's field argument of a nullable map with key constraints drops the None its model field keeps",
    ),
    Kept(
        ISSUE,
        "#7101",
        "tagged",
        "backends",
        BOTH,
        ({"tag": ("enum2-str", "enum2-int"), "backend": (MSGSPEC,)},),
        "msgspec cannot decode the union whose variant has several tags, the Pydantic model can",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "backends",
        DEFAULT,
        ({"tag": ("enum1-str",), "backend": (MSGSPEC,)},),
        "msgspec cannot decode a union of Structs that are not all tagged, the Pydantic model can",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "backends",
        PRESET,
        ({"tag": ("enum1-str",), "nullable": (True,), "backend": (MSGSPEC,)},),
        "msgspec cannot decode an enum tag with a null member, the Pydantic model imports under the preset",
    ),
    Kept(
        LIBRARY_LIMIT,
        "enum",
        "tagged",
        "accepts-invalid",
        DEFAULT,
        ({"tag": ("enum2-int",), "nullable": (True,), "backend": (PYDANTIC_V2,)},),
        "a union whose integer tags also admit null is written without discriminator= (#7102), and Pydantic's Literal "
        "equality accepts true for the integer tag Literal[1, 4] that a plain union reads",
    ),
    Kept(
        LIBRARY_LIMIT,
        "enum",
        "tagged",
        "accepts-invalid",
        PRESET,
        ({"tag": ("enum2-int",), "nullable": (True,), "mapping": (False,), "backend": (PYDANTIC_V2,)},),
        "the same under the preset, where a mapping gives the union tags it reads with discriminator=",
    ),
    Kept(
        LIBRARY_LIMIT,
        "const",
        "tagged",
        "backends",
        BOTH,
        ({"tag": ("const-int",), "backend": (MSGSPEC,)},),
        "Pydantic's Literal equality accepts true for the integer tag Literal[1], msgspec does not",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "const",
        DEFAULT,
        ({"tag": ("enum1-str",), "nullable": (False,), "backend": (MSGSPEC,)},),
        "msgspec decodes the union of const tags, and cannot decode the same with one-member enum tags, which are no "
        "tags to it",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "option:enum_field_as_literal",
        DEFAULT,
        ({"tag": ("enum1-str",), "nullable": (False,), "backend": (MSGSPEC,)},),
        "with Literal tags msgspec decodes the union whose one-member enum tags it cannot decode",
    ),
)
"""Every known difference of the shape families."""
