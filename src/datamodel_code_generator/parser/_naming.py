"""The names of the models: the name a model takes from the model it is built for, and the plan of the final names.

L1 names a model it builds for another one, the parent, after the parent's name and the word of the child's role
(``ComposedRole``): the enum of a nullable enum (``Enum``), the model of a map's values (``AdditionalProperty``), the
enum of an allOf array's items (``Item``), the map branch of a type list (``Object``), the constrained branch of a type
list (the titled type), the response of a status code (the capitalized code), the request and response variants of a
model (``rw_variant_name``) and a union branch without a name of its own, which takes the parent's name as it is. Each
string is released output.

While a naming rule reads them, L1 records for every model the frames of the model builders open when the model is
recorded (``NamingFrames``, ``BuildRecord``), which name its owner, the model whose builder built it, and what the
code that names the model claims for it (``Claim``): the model its name is composed from, or its position as a union
branch named as the union.

Once every model has claimed its name, the L2 pass ``name_models`` records the names planned after the last claim
(``NamingPlan``, made by ``plan_names`` under a ``NamingPolicy``), which L3 reads for the name a model was numbered
from.
This module imports only the standard library, ``typing_extensions`` and the model keys, so every layer may use it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from string import digits
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar

from typing_extensions import Protocol, final

from datamodel_code_generator._model_keys import ModelKey, model_key_of

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

_ResultT = TypeVar("_ResultT")

__all__ = [
    "BUILD_RECORD_KEY",
    "BuildFrame",
    "BuildRecord",
    "Claim",
    "Composed",
    "ComposedRole",
    "NamingFrames",
    "NamingPlan",
    "NamingPolicy",
    "Ordinal",
    "PlannedName",
    "VariantRole",
    "build_record_of",
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


BUILD_RECORD_KEY: Final = "_build_record"
"""The key of the ``BuildRecord`` of a model on the ``Reference`` that names it."""
VariantRole: TypeAlias = Literal["Request", "Response"]
"""The role of a read or write variant of a model, the suffix ``rw_variant_name`` gives it."""


@final
@dataclass(frozen=True, slots=True, eq=False)
class Composed:
    """The claim of a model that L1 names after the name of another model, its base, and the word of its role."""

    base: object
    """The ``Reference`` of the base."""
    role: ComposedRole | VariantRole


@final
@dataclass(frozen=True, slots=True)
class Ordinal:
    """The claim of a union branch with no name of its own, which L1 names as the union: its position, from 1."""

    position: int


Claim: TypeAlias = Composed | Ordinal
"""What the code that decides how a model is named claims for it, beside its owner and its own name."""


@final
@dataclass(slots=True, eq=False)
class BuildFrame:
    """A model builder, or a naming decision with its ``claim``, that runs while L1 parses.

    ``model`` is the ``Reference`` of the model it built, once it returns one that was recorded inside it.
    """

    claim: Claim | None = None
    model: object | None = None


@final
@dataclass(frozen=True, slots=True, eq=False)
class BuildRecord:
    """The frames open when L1 records a model (``NamingFrames.record``), innermost first.

    ``model`` is the ``Reference`` the frames stand for: the model's own, or the base of a read or write variant.
    """

    model: object
    frames: tuple[BuildFrame, ...]
    definition: bool
    """Whether the model is built from an entry of a definitions container of its document."""
    claim: Claim | None = None
    """The claim made for the model where it was built, not by a frame."""

    @property
    def owner(self) -> object | None:
        """Return the model whose builder built this one: the innermost builder frame that holds another model.

        A model built outside every builder (a definition, a document root) has none; neither has a `$ref` target
        recorded inside a builder that returned another model.
        """
        key = model_key_of(self.model)
        return next(
            (
                frame.model
                for frame in self.frames
                if frame.claim is None and frame.model is not None and model_key_of(frame.model) != key
            ),
            None,
        )

    @property
    def root(self) -> bool:
        """Return whether every builder frame open when the model was recorded built a model."""
        return all(frame.model is not None for frame in self.frames if frame.claim is None)

    def claim_for(self, reference: object) -> Claim | None:
        """Return the claim made for the model of ``reference``: its own, else the innermost frame's that built it."""
        if self.claim is not None:
            return self.claim
        key = model_key_of(reference)
        return next(
            (
                frame.claim
                for frame in self.frames
                if frame.claim is not None and frame.model is not None and model_key_of(frame.model) == key
            ),
            None,
        )


@final
class NamingFrames:
    """The frames of the model builders and naming decisions open while L1 parses, and what they record.

    A model records the frames open when its schema location is recorded (``record``), so its owner and its claim are
    read once every frame has closed. A read or write variant stands for its base model in the frames, so it takes the
    base's owner. A document is parsed with no frame open (``detach``), whatever parse reached it, so its models are
    roots.
    """

    __slots__ = ("_claims", "_stack", "_variant_bases")

    def __init__(self) -> None:
        """Start with no frame open."""
        self._stack: list[BuildFrame] = []
        self._variant_bases: dict[ModelKey, object] = {}
        self._claims: dict[ModelKey, Claim] = {}

    def open(self, claim: Claim | None = None) -> BuildFrame:
        """Open a frame of a builder, or of a naming decision with ``claim``, and return it."""
        self._stack.append(frame := BuildFrame(claim))
        return frame

    def close(self) -> None:
        """Close the innermost frame."""
        self._stack.pop()

    @staticmethod
    def built(frame: BuildFrame, result: object) -> None:
        """Note that ``frame``, closed, returned ``result``: it built the model of ``result`` if it recorded it."""
        if (
            (reference := getattr(result, "reference", None)) is not None
            and (record := reference.__dict__.get(BUILD_RECORD_KEY)) is not None
            and any(open_frame is frame for open_frame in record.frames)
        ):
            frame.model = reference

    def claimed(self, claim: Claim, parse: Callable[[], _ResultT]) -> _ResultT:
        """Return what ``parse`` returns, run in a frame that makes ``claim`` for the model it builds, if any."""
        frame = self.open(claim)
        try:
            result = parse()
        finally:
            self.close()
        self.built(frame, result)
        return result

    def detach(self) -> list[BuildFrame]:
        """Close every frame for a document parse and return them, for ``attach`` once it ends."""
        stack, self._stack = self._stack, []
        return stack

    def attach(self, stack: list[BuildFrame]) -> None:
        """Open again the frames ``detach`` closed."""
        self._stack = stack

    def variant(self, reference: object, base: object, role: VariantRole) -> None:
        """Record that the model of ``reference`` is the ``role`` variant of the model of ``base``."""
        self._variant_bases[key := model_key_of(reference)] = base
        self._claims[key] = Composed(base, role)

    def record(self, reference: object, *, definition: bool) -> None:
        """Record on ``reference`` the frames open now, innermost first (``BuildRecord``)."""
        key = model_key_of(reference)
        reference.__dict__[BUILD_RECORD_KEY] = BuildRecord(
            self._variant_bases.get(key, reference),
            tuple(reversed(self._stack)),
            definition,
            self._claims.get(key),
        )


def build_record_of(reference: object) -> BuildRecord | None:
    """Return the frames recorded for the model of ``reference`` while L1 recorded frames, if it recorded them."""
    return reference.__dict__.get(BUILD_RECORD_KEY)


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
