"""Back links of the generation graph: the children of a `Reference`, readable and writable by every layer.

A data type that names a reference, and a model whose base names one, is a child of that reference
(``Reference.children``), in the order the children were added. A data type is the child that names its reference
through ``replace_reference``; every other child is a model, which ``Reference.iter_data_model_children`` yields. This
module adds, drops and reads children without importing `reference`, `types` or `model`, which import it.
"""

from __future__ import annotations

from typing import Any

__all__ = ["add_child", "clear_children", "data_type_children", "drop_child", "drop_child_and_detached"]


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


def data_type_children(reference: Any) -> list[Any]:
    """Return the data types that name ``reference``, in the order they were added, as a list of their own."""
    return [child for child in reference.children if hasattr(child, "replace_reference")]
