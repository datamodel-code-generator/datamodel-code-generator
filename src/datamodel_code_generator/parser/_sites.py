"""Reference occurrences ("sites"): every place a model names another one, with the store operation that put it there.

A site is one reference inside a model: in a field's type tree (``type``, or ``alias_body`` for the right-hand side of
a type-alias model) or in a base class (``base``). The store records the sites of every model it records, keyed by the
id of the occurrence (`SiteKey`), and keeps each site's `Provenance` while the occurrence stays: a refresh carries the
provenance of a site whose owner, field, position, node, target and kind did not change, and gives a new site the
effect the store operation that made it stated (``parsed`` for the models the parsers built).

Names are not stored on sites: a rename leaves every site as it was. Under the ``MODEL_GRAPH`` check, the sites a
refresh records for the models store operations noted equal those a full rebuild computes from the live trees.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, NewType, TypeAlias, final

from datamodel_code_generator.parser._holders import FieldKey, NodeKey

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

SiteKey = NewType("SiteKey", int)
"""One occurrence: the id of the data type occurrence the store records it under, stable while the store holds it."""
RefKind: TypeAlias = Literal[
    "type", "base", "default_factory", "default_value", "discriminator_mapping", "runtime_validation", "alias_body"
]
"""Where an occurrence is. The store records ``type``, ``base`` and ``alias_body`` sites from the type trees."""
PositionStep: TypeAlias = Literal["member", "item", "key", "value", "tuple"]
Position: TypeAlias = tuple[PositionStep, int]
"""One step down a type tree: a union member, a list or set item, a dict key or value, or a tuple element."""
ProvenanceHow: TypeAlias = Literal["parsed", "inherited", "collapsed", "overridden", "redirected", "reused", "copied"]


@final
@dataclass(frozen=True, slots=True)
class Provenance:
    """How a site came to be: the store operation that made it, the site it was derived from and the model via which."""

    how: ProvenanceHow
    source: SiteKey | None = None
    """The occurrence this one was derived from: the one at the same place before a redirect, the collapsed root's."""
    via: ModelKey | None = None
    """The model the occurrence named before a redirect, or the root model collapsed."""


PARSED: Final = Provenance("parsed")
COPIED: Final = Provenance("copied")


class SiteOccurrence(NamedTuple):
    """What a site is, without its key and provenance: what a recomputation from the live trees gives."""

    owner: ModelKey
    """The model whose field or base class holds the occurrence."""
    field: FieldKey | None
    """The field whose type tree holds the occurrence; None for a base class."""
    path: tuple[Position, ...]
    """The position inside the field's type tree or the base class; () for the field type or the base itself."""
    node: NodeKey | None
    """The data type node that holds the reference."""
    target: ModelKey
    kind: RefKind
    eager: bool
    """Whether the reference is evaluated when the class is created: a base class."""


@final
@dataclass(frozen=True, slots=True)
class RefSite:
    """One reference occurrence the store records, with how it came to be."""

    key: SiteKey
    owner: ModelKey
    field: FieldKey | None
    path: tuple[Position, ...]
    node: NodeKey | None
    target: ModelKey
    kind: RefKind
    eager: bool
    provenance: Provenance

    @property
    def occurrence(self) -> SiteOccurrence:
        """The site without its key and provenance."""
        return SiteOccurrence(self.owner, self.field, self.path, self.node, self.target, self.kind, self.eager)


def site_of(key: SiteKey, occurrence: SiteOccurrence, provenance: Provenance) -> RefSite:
    """Return the site ``key`` of ``occurrence``."""
    return RefSite(key, *occurrence, provenance)


def tree_sites(
    model: DataModel, owner: ModelKey, target_of: Callable[[Reference], ModelKey]
) -> list[tuple[int, SiteOccurrence]]:
    """Return the sites of the type trees of ``model``, each with its position among the nodes the store records.

    The nodes are walked in the store's order: each field's tree, then each base class's, every tree in preorder with
    the items of a node before its dict key.
    """
    sites: list[tuple[int, SiteOccurrence]] = []
    kind: RefKind = "alias_body" if model.IS_ALIAS else "type"
    index = 0
    for field_ in model.fields:
        index = _collect(field_.data_type, FieldKey(id(field_)), owner, kind, target_of, index, sites)
    for base_class in model.base_classes:
        index = _collect(base_class, None, owner, "base", target_of, index, sites)
    return sites


def _collect(  # noqa: PLR0913, PLR0917
    root: DataType,
    field_key: FieldKey | None,
    owner: ModelKey,
    kind: RefKind,
    target_of: Callable[[Reference], ModelKey],
    index: int,
    sites: list[tuple[int, SiteOccurrence]],
) -> int:
    """Append the sites of the tree of ``root``, whose first node is the ``index``-th, and return the next index."""
    stack: list[tuple[DataType, tuple[Position, ...]]] = [(root, ())]
    while stack:
        data_type, path = stack.pop()
        if (reference := data_type.reference) is not None:
            sites.append((
                index,
                SiteOccurrence(
                    owner, field_key, path, NodeKey(id(data_type)), target_of(reference), kind, eager=kind == "base"
                ),
            ))
        index += 1
        if dict_key := data_type.dict_key:
            stack.append((dict_key, (*path, ("key", 0))))
        if nested := data_type.data_types:
            step = _step(data_type)
            stack.extend((nested[position], (*path, (step, position))) for position in reversed(range(len(nested))))
    return index


def _step(data_type: DataType) -> PositionStep:
    """Return what the items of ``data_type`` are to it."""
    if data_type.is_tuple:
        return "tuple"
    if len(data_type.data_types) > 1:
        return "member"
    if data_type.is_dict or data_type.is_mapping:
        return "value"
    if data_type.is_list or data_type.is_set or data_type.is_frozen_set or data_type.is_sequence:
        return "item"
    return "member"
