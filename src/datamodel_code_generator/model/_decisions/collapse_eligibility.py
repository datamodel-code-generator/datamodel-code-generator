"""Collapse eligibility: whether collapsing root models keeps a root model named at one of its holders.

A site is a data type of a holder's field that names a model of the configured root representation. Every input is
an immutable fact, so the functions read nothing else. ``keeps_root_model_named`` decides whether
``--collapse-root-models`` leaves the site naming the root model, with the rule that keeps it, or inlines it there.
``later_holders_follow`` decides whether the later holders of a root model that the ``parent`` strategy collapsed at
its first holder name its inner model too. The adapters read the facts from the live models at L5:
``parser/_passes/modules.py:collapse_site_facts`` for ``collapse_root_models_in_scope``, and
``collapse_root_models_in_scope`` (at the first holder) and ``settle_kept_parent_roots`` (once every module is
processed) for the later holders.

The row named ``legacy-*`` keeps today's output where the decision reads a fact before it is final; its fix deletes
the row.
"""

from __future__ import annotations

from typing import Literal, TypeAlias, final

from datamodel_code_generator._records import record

__all__ = [
    "CollapseSiteFacts",
    "KeepRule",
    "LaterHolderFacts",
    "keeps_root_model_named",
    "later_holders_follow",
]

KeepRule: TypeAlias = Literal[
    "no-root-field",
    "circular",
    "runtime-object-validation",
    "fieldless-site-regex-engine",
    "constraints-in-type",
    "constrained-map",
    "constraints-beside-container",
    "no-name-strategy",
    "list-item",
]
"""Why collapse keeps a root model named at a site.

* ``no-root-field``: the root model has no root field to inline.
* ``circular``: the root model is circular (L4 ``set_circular_root_model_paths``) and cannot be fully inlined.
* ``runtime-object-validation``: the root model owns runtime object rules, which would be lost with it.
* ``fieldless-site-regex-engine``: the root model is the model of a site that earlier releases wrote as a model
  without fields (``FIELDLESS_SITE_KEY``) and its pattern needs a regex engine in the config of the model that holds
  it: inlined, that engine would apply to the other patterns of its holder.
* ``constraints-in-type``: without ``--field-constraints``, the root model or a root model its root type reaches
  constrains its root field, which the type of the holder would not carry.
* ``constrained-map``: the root field constrains a dict or mapping.
* ``constraints-beside-container``: under ``--field-constraints``, the root field has constraints (as ``Field()``
  arguments or inside its type) and the holder's field type holds a dict, a union or a list.
* ``no-name-strategy``: the root type names another model and no ``--collapse-root-models-name-strategy`` says which
  name the site takes.
* ``list-item``: the root type names no model, the site is the item of a list, and the parser does not collapse the
  root models that list items name (GraphQL).
"""


@final
@record
class CollapseSiteFacts:
    """The facts ``keeps_root_model_named`` reads about one site and the root model it names.

    ``constrains_through_root_types`` is read only without ``field_constraints``, and ``holder_has_container`` only
    under it where the root field has constraints; the adapter leaves them False where no rule reads them.
    """

    has_root_field: bool = False
    """The root model has a root field; every other fact is False when it has none."""
    circular: bool = False
    runtime_object_validation: bool = False
    needs_model_regex_engine: bool = False
    """The root model is a fieldless site whose pattern needs a regex engine in its holder's config."""
    field_constraints: bool = False
    constrains_through_root_types: bool = False
    """The root model, or a root model its root type reaches, constrains its root field."""
    constrained_root_field: bool = False
    """The root field has constraints as ``Field()`` arguments."""
    constrained_root: bool = False
    """The root field has constraints as ``Field()`` arguments or written inside its type."""
    root_type_is_map: bool = False
    root_type_names_model: bool = False
    names_inner_models: bool = False
    """A ``--collapse-root-models-name-strategy`` is set."""
    holder_has_container: bool = False
    """The holder's field type holds a dict, a union or a list."""
    list_item: bool = False
    """The site is the item of a list."""
    collapses_list_items: bool = False
    """The parser collapses the root models that list items name."""


def keeps_root_model_named(facts: CollapseSiteFacts, /) -> KeepRule | None:  # noqa: PLR0911
    """Return the rule that keeps the root model named at a site, or None when collapse inlines it there."""
    if not facts.has_root_field:
        return "no-root-field"
    if facts.circular:
        return "circular"
    if facts.runtime_object_validation:
        return "runtime-object-validation"
    if facts.needs_model_regex_engine:
        return "fieldless-site-regex-engine"
    if not facts.field_constraints and facts.constrains_through_root_types:
        return "constraints-in-type"
    if facts.constrained_root_field and facts.root_type_is_map:
        return "constrained-map"
    if facts.field_constraints and facts.constrained_root and facts.holder_has_container:
        return "constraints-beside-container"
    if facts.root_type_names_model:
        return None if facts.names_inner_models else "no-name-strategy"
    return "list-item" if facts.list_item and not facts.collapses_list_items else None


@final
@record
class LaterHolderFacts:
    """The facts ``later_holders_follow`` reads about a root model that the ``parent`` strategy collapsed.

    ``renamed_apart`` is None until every module is processed, when the class names are final.
    """

    shared_parent_wrappers: bool
    """``--collapse-shared-parent-wrappers`` makes the later holders follow the first one."""
    renames_colliding_types: bool
    """The run renames a type that a field is named like."""
    same_module: bool
    """The root model and its inner model are written in one module."""
    renamed_apart: bool | None = None
    """A pass gave the root model a class name of its own beside the inner model that took its name."""


def later_holders_follow(facts: LaterHolderFacts, /) -> bool | None:
    """Return whether the later holders of a collapsed root model name its inner model, or None while it is open.

    They follow the first holder unless a later pass can still give the root model a name of its own: row
    ``legacy-predicted-rename`` asks, at the first holder, whether the run renames colliding types and both models
    are written in one module, as 0.86.0 kept the root model there; such a root model stays open (its later holders
    keep naming it) until ``renamed_apart`` is known, and stays for them where a pass renamed it apart.
    """
    if facts.shared_parent_wrappers or not facts.renames_colliding_types or not facts.same_module:
        return True
    if facts.renamed_apart is None:
        return None
    return not facts.renamed_apart
