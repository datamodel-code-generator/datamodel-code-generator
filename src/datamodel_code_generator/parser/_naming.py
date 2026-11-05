"""The names of the models: the one place for the name a model takes from the model it is built for.

L1 names a model it builds for another one, the parent, after the parent's name and the word of the child's role
(``ComposedRole``): the enum of a nullable enum (``Enum``), the model of a map's values (``AdditionalProperty``), the
enum of an allOf array's items (``Item``), the map branch of a type list (``Object``), the constrained branch of a type
list (the titled type), the response of a status code (the capitalized code), the request and response variants of a
model (``rw_variant_name``) and a union branch without a name of its own, which takes the parent's name as it is. Each
string is released output. This module imports only the standard library, so every layer may use it.
"""

from __future__ import annotations

from string import digits
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = ["ComposedRole", "composed_name", "rw_variant_name"]

ComposedRole: TypeAlias = Literal["enum", "additional-property", "item", "object", "type", "status", "variant-parent"]
"""The role of a model that L1 names after the model it is built for (see the module docstring)."""
_ROLE_WORDS: Final[Mapping[ComposedRole, str | Callable[[str], str]]] = {
    "enum": "Enum",
    "additional-property": "AdditionalProperty",
    "item": "Item",
    "object": "Object",
    "type": str.title,
    "status": str.capitalize,
    "variant-parent": "",
}
"""The word each role adds to the parent's name, or how it spells the ``word`` its caller gives."""


def composed_name(base: str, role: ComposedRole, *, word: str = "") -> str:
    """Return the name of the model of ``role`` built for the model named ``base``.

    The roles ``type`` and ``status`` spell ``word``, the type or the status code of the model; the other roles add
    their own word. Row ``legacy-variant-takes-parent-name``: a union branch without a name of its own takes ``base``
    (role ``variant-parent``), so the model of a branch is named like the union's (#4826).
    """
    role_word = _ROLE_WORDS[role]
    return f"{base}{role_word if isinstance(role_word, str) else role_word(word)}"


def rw_variant_name(base_name: str, suffix: str, class_name_suffix: str) -> str:
    """Return the name of the ``Request`` or ``Response`` variant (``suffix``) of the model named ``base_name``.

    The suffix goes after the name, or, when the name ends in ``--class-name-suffix`` and digits, before them both, so
    that ``PetModel1`` gives ``PetRequestModel1``.
    """
    base_name_without_index = base_name.rstrip(digits)
    if class_name_suffix and base_name_without_index.endswith(class_name_suffix):
        numeric_index = base_name.removeprefix(base_name_without_index)
        return f"{base_name_without_index[: -len(class_name_suffix)]}{suffix}{class_name_suffix}{numeric_index}"
    return f"{base_name}{suffix}"
