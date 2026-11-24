"""Compositions of the shape families: the shape of one family placed inside the shape of another.

A composition is a chain of two to ``MAX_LEVELS`` levels, outermost first. Its last level is a plain shape of a family,
any of the shapes ``families.py`` yields. Each level before it is a family whose slot holds the checked definition of
the level after it, by the family's ``PLACEMENTS`` entry:

``container`` (24 levels)
    ``f`` is a container of it (the family's ``ref`` element): every container spelling, nullable or not, required or
    not.
``extend`` (16 levels)
    the child extends it, which is the base: named directly, through an alias, through a ``$ref`` with sibling
    ``properties`` or through an ``allOf`` with sibling ``properties``; the child's addition, a required integer ``r``,
    as a member or beside the ``allOf``; the child defined first or last.
``union`` (48 levels)
    it is the first variant of the union ``Pet``: ``oneOf`` or ``anyOf``, flat, nested through a named union or nested
    inline; the child adds an optional or a required ``owner``, as a member or beside; defined first or last.
``tagged`` (32 levels)
    the first variant ``Cat`` extends it beside its own tag: every tag form, nullable, mapping and union keyword of the
    family.
``mixin`` (18 levels)
    it is the first member of the mix; the second declares ``p`` with one of the nine leaves; both member orders.

A placed level writes the family's definitions with the parameters the placement fixes (``Placement.fixed``) and the
ones it varies, then puts the inner checked definition where the slot definition was used (or, for ``tagged``, makes
the slot extend it), and keeps only the definitions the outer checked definition still reaches. The definitions of
the level at depth ``d`` carry the prefix ``PREFIXES[d]``, so no two levels share a name. The checked definition of a
composition is that of its first level; its backends are those every level's family is generated for
(``FAMILY_BACKENDS``). A composition is left out where a property that a level declares beside the inner (``p`` of
the mix, the tag ``kind``) has no value in common with what the inner declares of it (``fits``): the realism
filter of the ``mixin`` family, across levels.

The bound: 138 placed levels and 750 plain shapes, chains of at most ``MAX_LEVELS`` levels, so at most 103,500
compositions of two levels and 14,283,000 of three, before the filter; too many to enumerate, so they are drawn
(``levels``). The strategy shrinks a chain towards fewer levels and towards the first value of each dimension.

A difference of a composition from the validator is known where a rule covers it (``covering``): a rule of
``kept.py`` that covers the part one level describes (``views``: a plain level's parameters, a placed level's own
parameters and what it holds), or a rule of ``COMPOSED``, which names what a placed level holds or, where the view
of no one level tells the cells apart, the composition's own parameters (``params``).
"""

from __future__ import annotations

import functools
import itertools
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final

from hypothesis import strategies as st

from .families import (
    DIALECT,
    FAMILIES,
    FAMILY_BACKENDS,
    LEAVES,
    MSGSPEC,
    PYDANTIC_V2,
    REF,
    Shape,
    has_common_value,
)
from .kept import BOTH, DEFAULT, ISSUE, LEGACY_ROW, PRESET, Kept

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Mapping

    from .families import Schema

MAX_LEVELS: Final = 3
"""The most levels a composition has: two placed levels around a plain shape."""
PREFIXES: Final = ("", "In", "Deep")
"""The prefix of each definition name of the level at that depth."""
COMPOSITION: Final = "composition"
"""The family name of a composed shape."""

Params = tuple[tuple[str, object], ...]


def _schema(value: object) -> Schema:
    """Return a definition of a family's document as the schema it is (every definition is a JSON object)."""
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class Level:
    """One level of a composition: a family, its parameters and the definitions they write.

    A placed level's parameters are the ones its placement varies, and its definitions those the family writes with
    the parameters the placement fixes as well; a plain level is a whole family shape.
    """

    family: str
    params: Params
    definitions: Mapping[str, Schema] = field(compare=False, repr=False)
    name: str = field(compare=False, repr=False)
    """The checked definition of the level's family."""


def _no_params(_params: Mapping[str, object]) -> Mapping[str, object]:
    """Return no fixed parameters: the placement varies every parameter it holds."""
    return {}


@dataclass(frozen=True)
class Placement:
    """How a family holds an inner definition: its slot, the parameters it fixes and the dimensions it varies."""

    family: str
    slot: str
    """The definition whose place the inner checked definition takes, or which extends it (``wraps``)."""
    dimensions: tuple[tuple[str, tuple[object, ...]], ...]
    fixed: Callable[[Mapping[str, object]], Mapping[str, object]] = field(default_factory=lambda: _no_params)
    """The family parameters the placement holds, from the ones it varies: the slot's own, which the inner replaces."""
    shown: Mapping[str, object] = field(default_factory=dict)
    """The fixed parameters that still describe the composed shape (the container element stays a reference)."""
    wraps: bool = False
    """Whether the slot keeps its own schema and extends the inner beside it, instead of being replaced by it."""
    watch: tuple[str, str] | None = None
    """The definition and property the level declares beside the inner, which the inner may declare too."""

    def levels(self) -> Iterator[Level]:
        """Yield a level for every combination of the varied parameters whose family shape exists."""
        names = [name for name, _ in self.dimensions]
        for values in itertools.product(*(values for _, values in self.dimensions)):
            params = dict(zip(names, values, strict=True))
            if (built := FAMILIES[self.family].build(**self.fixed(params), **params)) is not None:
                definitions, name, _ = built
                yield Level(self.family, tuple(params.items()), _definitions(definitions), name)


_BOOLS: Final = (False, True)


def _dimension(family: str, name: str) -> tuple[object, ...]:
    return dict(FAMILIES[family].dimensions)[name]


PLACEMENTS: Final[Mapping[str, Placement]] = {
    placement.family: placement
    for placement in (
        Placement(
            "container",
            "Item",
            (
                ("container", _dimension("container", "container")),
                ("nullable", _BOOLS),
                ("required", _BOOLS),
            ),
            lambda _params: {"element": "ref"},
            {"element": "ref"},
        ),
        Placement(
            "extend",
            "Base",
            (
                ("via", _dimension("extend", "via")),
                ("form", _dimension("extend", "form")),
                ("child_first", _BOOLS),
            ),
            lambda _params: {"leaf": "str", "how": "add"},
            {"how": "add"},
        ),
        Placement(
            "union",
            "Cat",
            (
                ("kind", _dimension("union", "kind")),
                ("shape", _dimension("union", "shape")),
                ("how", ("add", "require-added")),
                ("form", _dimension("union", "form")),
                ("child_first", _BOOLS),
            ),
        ),
        Placement(
            "tagged",
            "Cat",
            (
                ("tag", _dimension("tagged", "tag")),
                ("nullable", _BOOLS),
                ("mapping", _BOOLS),
                ("kind", _dimension("tagged", "kind")),
            ),
            wraps=True,
            watch=("Cat", "kind"),
        ),
        Placement(
            "mixin",
            "A",
            (("second", tuple(LEAVES)), ("swapped", _BOOLS)),
            lambda params: {"first": params["second"]},
            watch=("B", "p"),
        ),
    )
}
"""Every family's placement, by family name."""


@functools.cache
def placed_levels() -> tuple[Level, ...]:
    """Return every level that holds another, family by family in the order of ``PLACEMENTS``."""
    return tuple(level for placement in PLACEMENTS.values() for level in placement.levels())


@functools.cache
def plain_levels() -> tuple[Level, ...]:
    """Return every plain shape as a level, family by family in the order of ``FAMILIES``."""
    return tuple(
        Level(shape.family, shape.params, _definitions(_schema(shape.document["$defs"])), shape.name)
        for family in FAMILIES.values()
        for shape in family.shapes()
    )


def levels() -> st.SearchStrategy[tuple[Level, ...]]:
    """Return a strategy of compositions: one to ``MAX_LEVELS - 1`` placed levels around a plain shape."""
    return (
        st
        .tuples(
            st.lists(st.sampled_from(placed_levels()), min_size=1, max_size=MAX_LEVELS - 1),
            st.sampled_from(plain_levels()),
        )
        .map(lambda drawn: (*drawn[0], drawn[1]))
        .filter(fits)
    )


def _references(value: object) -> Iterator[str]:
    """Yield the definition names a schema value refers to: every string that starts with ``REF``.

    That is each ``$ref`` and each discriminator mapping value.
    """
    if isinstance(value, dict):
        for item in value.values():
            yield from _references(item)
    elif isinstance(value, list):
        for item in value:
            yield from _references(item)
    elif isinstance(value, str) and value.startswith(REF):
        yield value.removeprefix(REF)


def _renamed(value: object, names: Mapping[str, str]) -> object:
    """Return a schema value with every reference to a definition of ``names`` pointing at its new name."""
    if isinstance(value, dict):
        return {key: _renamed(item, names) for key, item in value.items()}
    if isinstance(value, list):
        return [_renamed(item, names) for item in value]
    if isinstance(value, str) and value.startswith(REF) and value.removeprefix(REF) in names:
        return REF + names[value.removeprefix(REF)]
    return value


def _definitions(definitions: Mapping[str, object], names: Mapping[str, str] | None = None) -> dict[str, Schema]:
    """Return definitions as schemas, renamed by ``names`` (keys and references) where given."""
    names = names or {}
    return {names.get(key, key): _schema(_renamed(value, names)) for key, value in definitions.items()}


def _reachable(definitions: dict[str, Schema], name: str) -> dict[str, Schema]:
    """Return the definitions that ``name`` reaches, itself included, in their order."""
    reached = {name}
    pending = [name]
    while pending:
        for target in _references(definitions[pending.pop()]):
            if target not in reached:
                reached.add(target)
                pending.append(target)
    return {key: value for key, value in definitions.items() if key in reached}


def _placed(level: Level, inner: str, prefix: str) -> dict[str, Schema]:
    """Return a placed level's definitions, prefixed, around the inner checked definition ``inner``."""
    placement = PLACEMENTS[level.family]
    names = {name: prefix + name for name in level.definitions}
    if placement.wraps:
        slot = level.definitions[placement.slot]
        return _definitions({**level.definitions, placement.slot: {"allOf": [{"$ref": REF + inner}], **slot}}, names)
    kept = {name: value for name, value in level.definitions.items() if name != placement.slot}
    return _definitions(kept, {**names, placement.slot: inner})


def compose(chain: tuple[Level, ...]) -> Shape:
    """Return the shape of a composition: its definitions, the first level's checked definition and its parameters."""
    *placed, plain = chain
    prefix = PREFIXES[len(placed)]
    definitions = _definitions(plain.definitions, {name: prefix + name for name in plain.definitions})
    name = prefix + plain.name
    for depth, level in reversed(list(enumerate(placed))):
        definitions = {**_placed(level, name, PREFIXES[depth]), **definitions}
        name = PREFIXES[depth] + level.name
        definitions = _reachable(definitions, name)
    return Shape(COMPOSITION, params(chain), {"$schema": DIALECT, "$defs": definitions}, name, (COMPOSITION, *chain))


def params(chain: tuple[Level, ...]) -> Params:
    """Return a composition's parameters: each level's family and parameters under its depth (``0.family``, ...)."""
    return tuple(
        (f"{depth}.{name}", value)
        for depth, level in enumerate(chain)
        for name, value in (("family", level.family), *level.params)
    )


@dataclass(frozen=True)
class Facts:
    """What a definition of a composed document is, read through its ``$ref`` and ``allOf`` members."""

    union: bool
    """Whether it is a union (``oneOf`` or ``anyOf``) or extends one."""
    declared: frozenset[str]
    """The properties it or one of its branches declares."""
    required: frozenset[str]
    """The properties it requires, not counting those only a branch requires."""


def _items(value: object) -> list[object]:
    """Return a list a schema holds (``allOf``, ``required``, ...), or an empty one where it holds none."""
    return value if isinstance(value, list) else []


def facts(definitions: Mapping[str, Schema], schema: Schema, seen: frozenset[str] = frozenset()) -> Facts:
    """Return the facts of a schema of a composed document (``definitions`` are its definitions)."""
    members = _items(schema.get("allOf"))
    if isinstance(target := schema.get("$ref"), str) and (name := target.removeprefix(REF)) not in seen:
        seen |= {name}
        members = [*members, definitions[name]]
    found = [facts(definitions, _schema(member), seen) for member in members]
    branches = [
        facts(definitions, _schema(branch), seen)
        for branch in (*_items(schema.get("oneOf")), *_items(schema.get("anyOf")))
    ]
    return Facts(
        bool(branches) or any(fact.union for fact in found),
        frozenset(_schema(schema.get("properties"))).union(*(fact.declared for fact in (*found, *branches))),
        frozenset(map(str, _items(schema.get("required")))).union(*(fact.required for fact in found)),
    )


def _declared(definitions: Mapping[str, Schema], schema: Schema, prop: str, seen: frozenset[str]) -> Schema | None:
    """Return the schema that one instance of ``schema`` gives the property ``prop``, or None where nothing declares it.

    It reads ``schema`` as ``facts`` does: its own declaration, its ``allOf`` members and its ``$ref`` hold at once,
    and the branches of its ``oneOf`` or ``anyOf`` are alternatives, which bound ``prop`` only where every branch
    declares it (a branch that does not leaves it free). A definition held by a property, container item or
    container value describes another object.
    """
    members = _items(schema.get("allOf"))
    if isinstance(target := schema.get("$ref"), str) and (name := target.removeprefix(REF)) not in seen:
        seen |= {name}
        members = [*members, definitions[name]]
    parts = [_schema(declaration)] if (declaration := _schema(schema.get("properties")).get(prop)) is not None else []
    parts.extend(found for member in members if (found := _declared(definitions, _schema(member), prop, seen)))
    for key in ("oneOf", "anyOf"):
        branches = [_declared(definitions, _schema(branch), prop, seen) for branch in _items(schema.get(key))]
        parts.extend([{"anyOf": branches}] if branches and None not in branches else [])
    return {"allOf": parts} if parts else None


def fits(chain: tuple[Level, ...]) -> bool:
    """Return whether a composition is one an author writes, as far as the properties declared on two levels go.

    A property a level declares beside the inner, which the inner declares too, needs a value that the level's
    declaration and the inner's admit: every declaration the inner's instance holds at once (its own, its ``allOf``
    members and what it references) and one branch of each ``oneOf`` or ``anyOf`` whose branches all declare it
    (``_declared``).

    This is the filter of the ``mixin`` family, applied across levels: a pair without a common value has no valid value
    of that property at all.
    """
    shape = compose(chain)
    definitions = _definitions(_schema(shape.document["$defs"]))
    for depth, level in enumerate(chain[:-1]):
        if (watch := PLACEMENTS[level.family].watch) is None:
            continue
        own = _schema(_schema(level.definitions[watch[0]].get("properties")).get(watch[1]))
        inner = PREFIXES[depth + 1] + chain[depth + 1].name
        declared = _declared(definitions, definitions[inner], watch[1], frozenset({inner}))
        if declared is not None and not has_common_value(own, declared):
            return False
    return True


def views(chain: tuple[Level, ...]) -> tuple[tuple[str, dict[str, object]], ...]:
    """Return each level's family with the parameters that describe its own part of the composed shape.

    A plain level is a whole family shape. A placed level shows the parameters it varies and those it fixes that stay
    true (``Placement.shown``), not the ones whose definition the inner replaced, and what it holds: ``inner`` (the
    next level's family), ``inner union`` (whether the inner checked definition is a union, ``facts``), and whether
    the inner declares and requires the property the level declares beside it (``inner declares``, ``inner
    requires``; false where the placement declares none).
    """
    *placed, plain = chain
    definitions = _definitions(_schema(compose(chain).document["$defs"]))
    found = []
    for depth, level in enumerate(placed):
        inner = facts(definitions, definitions[PREFIXES[depth + 1] + chain[depth + 1].name])
        watched = (PLACEMENTS[level.family].watch or ("", ""))[1]
        found.append((
            level.family,
            {
                **PLACEMENTS[level.family].shown,
                **dict(level.params),
                "inner": chain[depth + 1].family,
                "inner union": inner.union,
                "inner declares": watched in inner.declared,
                "inner requires": watched in inner.required,
            },
        ))
    return (*found, (plain.family, dict(plain.params)))


def backends(chain: tuple[Level, ...]) -> tuple[str, ...]:
    """Return the backends every level's family is generated for, in the order of the first level's family."""
    return tuple(
        backend
        for backend in FAMILY_BACKENDS[chain[0].family]
        if all(backend in FAMILY_BACKENDS[level.family] for level in chain)
    )


def describe(chain: tuple[Level, ...]) -> str:
    """Return a composition as one line per level, outermost first."""
    return "\n".join(
        f"{'  ' * depth}{level.family}"
        f"{' holds' if depth < len(chain) - 1 else ''} "
        f"({', '.join(f'{name}={value}' for name, value in level.params)})"
        for depth, level in enumerate(chain)
    )


def _boxed(boxes: Iterable[Mapping[str, tuple[object, ...]]], view: Mapping[str, object]) -> bool:
    """Return whether a view lies in one of the boxes; a parameter a box names that the view lacks is no match."""
    return any(all(name in view and view[name] in values for name, values in box.items()) for box in boxes)


def covering(rules: Iterable[Kept], chain: tuple[Level, ...], backend: str, contract: str, kind: str) -> Kept | None:
    """Return the first rule that covers a composition's difference of one kind under one contract, or None.

    A rule of a family covers it where it covers the view of one of the composition's levels of that family
    (``views``); a rule of ``COMPOSITION`` where it covers the composition's own parameters (``params``). Either way
    the backend is one more parameter.
    """
    candidates = (*views(chain), (COMPOSITION, dict(params(chain))))
    return next(
        (
            rule
            for rule in rules
            if rule.kind == kind and contract in rule.contracts
            for family, view in candidates
            if rule.family == family and _boxed(rule.boxes, {**view, "backend": backend})
        ),
        None,
    )


_STRING_SECONDS: Final = ("str", "str_min", "nullable_str", "str_or_arr")
"""The leaves of a mix's second member that admit a string longer than two characters."""

COMPOSED: Final[tuple[Kept, ...]] = (
    Kept(
        LEGACY_ROW,
        "legacy-ref-member-union-dropped",
        "extend",
        "accepts-invalid",
        DEFAULT,
        ({"via": ("direct", "allof-siblings"), "inner union": (True,)},),
        "a child whose allOf names an allOf over a union, directly or through a parent with sibling properties, drops "
        "it as a root model, so it has none of the union's fields (fixed by --distribute-allof-over-unions)",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-ref-member-union-dropped",
        "mixin",
        "accepts-invalid",
        DEFAULT,
        ({"inner union": (True,)},),
        "a mix whose first member is an allOf over a union drops that member as a root model, so it has none of the "
        "union's fields (fixed by --distribute-allof-over-unions)",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-ref-member-union-dropped",
        "tagged",
        "accepts-invalid",
        DEFAULT,
        ({"inner union": (True,)},),
        "a variant that extends an allOf over a union drops it as a root model, so it has none of the union's fields "
        "(fixed by --distribute-allof-over-unions)",
    ),
    Kept(
        ISSUE,
        "#6868",
        "extend",
        "accepts-invalid",
        BOTH,
        ({"via": ("ref-siblings",)},),
        "a child whose parent is a $ref with sibling properties loses what the referenced definition holds: an "
        "inherited property typed Any, a required one made optional, or a union's fields",
    ),
    Kept(
        ISSUE,
        "#6869",
        "union",
        "accepts-invalid",
        BOTH,
        ({"inner union": (True,)}, {"shape": ("nested-inline",)}),
        "allOf over a union whose first variant is itself a union (a tagged union, or a model that extends one): the "
        "variant for that branch has none of its fields; and a union with an inline union branch, whose variant is "
        "missing, held by another level accepts what that variant would reject",
    ),
    Kept(
        ISSUE,
        "#7102",
        "tagged",
        "accepts-invalid",
        PRESET,
        ({"nullable": (True,), "inner union": (True,), "backend": (PYDANTIC_V2,)},),
        "a variant that extends a union, in a discriminated union whose tag also admits null: under the preset the "
        "Pydantic model imports and accepts a tag no variant admits (true for an integer tag, a variant's tag beside "
        "fields of another)",
    ),
    Kept(
        ISSUE,
        "#4713",
        "tagged",
        "model-raises",
        BOTH,
        ({"inner union": (True,), "backend": (MSGSPEC,)},),
        "a variant that extends a union is a union of Structs that are not all tagged, which msgspec cannot decode; "
        "the generation warns and names --use-msgspec-tagged-unions",
    ),
    Kept(
        ISSUE,
        "#7331",
        "mixin",
        "accepts-invalid",
        BOTH,
        ({"swapped": (True,), "inner requires": (True,)},),
        "the second member requires p, which the first member declares too: p stays optional in the mix",
    ),
    Kept(
        ISSUE,
        "#7171",
        "mixin",
        "accepts-invalid",
        BOTH,
        ({"inner declares": (True,), "inner requires": (False,)}, {"inner declares": (True,), "swapped": (False,)}),
        "an allOf merge of two refs that both declare p takes p from one member: the other member's type or bounds "
        "are lost",
    ),
    Kept(
        LEGACY_ROW,
        "legacy-length-keywords-across-kinds",
        COMPOSITION,
        "rejects-valid",
        DEFAULT,
        tuple(
            {
                f"{depth}.family": ("mixin",),
                f"{depth}.second": _STRING_SECONDS,
                f"{depth + 1}.family": ("extend",),
                f"{depth + 1}.leaf": ("str_or_arr",),
                f"{depth + 1}.how": ("narrow",),
                f"{depth + 1}.via": ("alias", "ref-siblings"),
                f"{depth + 1}.form": ("beside",),
            }
            for depth in range(MAX_LEVELS - 1)
        ),
        "maxItems that a child adds beside an alias or a $ref with sibling properties to an inherited string-or-array "
        "p bounds the length of the string a mix merges it with (the extend rule of the row covers the other parents)",
    ),
)
"""The known differences of compositions that no family rule covers on one level.

Most are rules over a placed level's view: they name what a placed level holds (``views``), so a family rule of the
plain shape never covers them by accident. A rule of ``COMPOSITION`` is keyed by the composition's parameters
(``params``: ``<depth>.family`` and ``<depth>.<parameter>``) where a placed level's view cannot tell the cells apart,
such as a difference that needs one parameter of the inner plain shape.
"""
