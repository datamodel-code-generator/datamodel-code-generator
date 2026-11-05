"""The names of the models: the name a model takes from the model it is built for, and the plan of the final names.

L1 names a model it builds for another one, the parent, after the parent's name and the word of the child's role
(``ComposedRole``): the enum of a nullable enum (``Enum``), the model of a map's values (``AdditionalProperty``), the
enum of an allOf array's items (``Item``), the map branch of a type list (``Object``), the constrained branch of a type
list (the titled type), the response of a status code (the capitalized code), the request and response variants of a
model (``rw_variant_name``) and a union branch without a name of its own, which takes the parent's name as it is. Each
string is released output.

Once every model has claimed its name, the L2 pass ``name_models`` records the names planned after the last claim
(``NamingPlan``, made by ``plan_names`` under a ``NamingPolicy``), which L3 reads for the name a model was numbered
from.
This module imports only the standard library, ``typing_extensions`` and the model keys, so every layer may use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import digits
from typing import TYPE_CHECKING, Final, Literal, TypeAlias

from typing_extensions import Protocol, final

from datamodel_code_generator._model_keys import ModelKey, model_key_of

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "ComposedRole",
    "NamingPlan",
    "NamingPolicy",
    "PlannedName",
    "composed_name",
    "plan_names",
    "rw_variant_name",
]

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


NamingPolicy: TypeAlias = Literal["legacy"]
"""The rules that plan the names of the models after their last claim (``plan_names``).

``legacy`` plans no name. Every model keeps the name the L1 resolver gave it, first come, first served (row
``legacy-first-come``), and the name ``settle_yielding_names`` settled; L3 numbers a class that its module holds from
the name the model asked for and gives that name back where the module leaves it free (row ``legacy-module-restore``).
"""


@final
@dataclass(frozen=True, slots=True)
class PlannedName:
    """The name the plan gives a model: its module (dotted, ``""`` for the root module), its class, and its base.

    The base is the name the class was numbered from, which L3 groups and restores the model by.
    """

    module: str
    cls: str
    base: str


class _NamedModel(Protocol):
    """What ``NamingPlan.base_of`` reads of a model."""

    @property
    def reference(self) -> object:
        """The reference that names the model."""

    @property
    def duplicate_class_name(self) -> str:
        """The class name the model asked for where the resolver numbered it, else ``""``."""


@final
@dataclass(frozen=True, slots=True)
class NamingPlan:
    """The names planned after the last claim under ``policy``, by model key; a model it leaves out keeps its name."""

    policy: NamingPolicy = "legacy"
    names: Mapping[ModelKey, PlannedName] = field(default_factory=dict)

    def base_of(self, model: _NamedModel) -> str:
        """Return the name ``model`` was numbered from: its planned base, else the name it asked for, or ``""``.

        L3 groups the models of a module by it, the class name standing in for ``""``, and gives it back where the
        module leaves it free.
        """
        planned = self.names.get(model_key_of(model.reference)) if self.names else None
        return model.duplicate_class_name if planned is None else planned.base


def plan_names(policy: NamingPolicy) -> NamingPlan:
    """Return the names ``policy`` plans after the last claim; ``legacy`` plans none."""
    return NamingPlan(policy)
