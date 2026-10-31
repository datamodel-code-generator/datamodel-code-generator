"""Back links of the generation graph: the children of a `Reference` and the holders of a node, for every layer.

A data type that names a reference, and a model whose base names one, is a child of that reference
(``Reference.children``), in the order the children were added. A data type is the child that names its reference
through ``replace_reference``; every other child is a model, which ``Reference.iter_data_model_children`` yields.

A data type node is held through its ``parent``: by the data type it is a member of, or by the field whose type it is,
which its model holds. A field is the holder with ``invalidate_semantic_caches``; a model has no ``parent``. A base
class node has no ``parent``: the models that list it in their ``base_classes`` hold it.

A type built only to read facts from, or a tree a holder lets go of, leaves the graph through its tree: every node
of a data type's tree (``all_data_types``) drops its record as a child (``drop_tree``), or lets go of its ``parent``
(``clear_parents``).

This module adds, drops and reads children and walks and clears the holders without importing `reference`, `types` or
`model`, which import it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "add_child",
    "base_owners",
    "chain_root",
    "clear_children",
    "clear_parents",
    "data_type_children",
    "drop_child",
    "drop_child_and_detached",
    "drop_tree",
    "nearest_field",
]


def add_child(reference: Any, child: object) -> None:
    """Record that ``child`` names ``reference``, after its other children."""
    reference.children.append(child)


def drop_child(reference: Any, child: object) -> None:
    """Forget every record of ``child`` naming ``reference``; the other children keep their order."""
    reference.children = [held for held in reference.children if held is not child]


def drop_child_and_detached(reference: Any, child: object) -> None:
    """Forget ``child`` and every child that nothing holds: a model, or a data type without a parent."""
    reference.children = [held for held in reference.children if held is not child and getattr(held, "parent", None)]


def clear_children(reference: Any) -> None:
    """Forget every child of ``reference``, so that a disposed graph keeps no cycle through it."""
    reference.children.clear()


def drop_tree(root: Any | None) -> None:
    """Forget every node of the data-type tree of ``root`` (none for None) as a child of the reference it names."""
    for node in () if root is None else root.all_data_types:
        node.unregister_reference()


def data_type_children(reference: Any) -> list[Any]:
    """Return the data types that name ``reference``, in the order they were added, as a list of their own."""
    return [child for child in reference.children if hasattr(child, "replace_reference")]


def nearest_field(node: object) -> Any | None:
    """Return the field that holds ``node`` through the data types above it, or None when no field holds it."""
    current = node
    while (parent := getattr(current, "parent", None)) is not None:
        if hasattr(parent, "invalidate_semantic_caches"):
            return parent
        current = parent
    return None


def chain_root(node: object) -> Any:
    """Return the outermost holder of ``node``: the model of its field, else the top of its chain (``node`` itself)."""
    current = node
    while (parent := getattr(current, "parent", None)) is not None:
        current = parent
    return current


def base_owners(node: object, models: Iterable[Any]) -> tuple[Any, ...]:
    """Return the models of ``models`` that list ``node`` among their base classes, in their order."""
    return tuple(model for model in models if any(base_class is node for base_class in model.base_classes))


def clear_parents(root: Any) -> None:
    """Let go of the ``parent`` of every node of the data-type tree of ``root``, ``root`` included."""
    for node in root.all_data_types:
        node.parent = None
