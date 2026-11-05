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
(``NamingPlan``, made by ``plan_names`` under the ``NamingRules``), which L3 reads for the name a model was numbered
from.
This module imports only the standard library, ``typing_extensions`` and the model keys, so every layer may use it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from string import digits
from typing import TYPE_CHECKING, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import Protocol, final

from datamodel_code_generator._free_names import first_free, is_numbered
from datamodel_code_generator._model_keys import ModelKey, model_key_of

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

_ResultT = TypeVar("_ResultT")

__all__ = [
    "BUILD_RECORD_KEY",
    "BuildFrame",
    "BuildRecord",
    "Claim",
    "ClaimRole",
    "Composed",
    "ComposedRole",
    "NameClaim",
    "Namer",
    "NamingFrames",
    "NamingPlan",
    "NamingRules",
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


ClaimRole: TypeAlias = Literal["definition", "root", "owned", "composed", "ordinal"]
"""What a model is to the naming rules (``NameClaim.role``), read from its records once L1 has built every model.

``composed``: its name is composed from another model's (``Composed``); ``ordinal``: a union branch named as the
union (``Ordinal``); ``owned``: built by the builder of another model (``BuildRecord.owner``); ``definition``: built by
no other model's builder from an entry of a definitions container of its document; ``root``: none of these, such as a
document root or an operation model.
"""


@final
@dataclass(frozen=True, slots=True)
class NameClaim:
    """The claim of one model to a name, with the facts the naming rules read and the name L1 gave it.

    The name is ``module`` (its dotted prefix with the dot, ``""`` for the root module) and ``cls``; ``base`` is the
    class name it asked for where L1 numbered it, else ``cls``. ``document`` is the rank of its schema document, in
    the order the parse first claimed a name in each, and ``order`` its place in that order among all claims.
    """

    key: ModelKey
    path: str
    role: ClaimRole
    owner: ModelKey | None
    composed_from: ModelKey | None
    word: str
    """The role of a composed claim (``Composed.role``), else ``""``."""
    position: int
    """The position of an ordinal claim (``Ordinal.position``), else 0."""
    document: int
    order: int
    module: str
    cls: str
    base: str
    kind: str
    """The kind of the class whose duplicate name suffix numbers it: ``model`` or ``enum``."""
    keyed: bool
    """Whether ``--model-name-map`` named the model, which no rule renames."""

    @property
    def name(self) -> str:
        """The name L1 gave the model, its module included."""
        return f"{self.module}{self.cls}"


@final
@dataclass(frozen=True, slots=True)
class NamingRules:
    """The rules that plan the names of the models after their last claim (``plan_names``).

    With no rule on (``legacy``) the plan names no model. Every model keeps the name the L1 resolver gave it, first
    come, first served (row ``legacy-first-come``), and the name ``settle_yielding_names`` settled; L3 numbers a class
    that its module holds from the name the model asked for and gives that name back where the module leaves it free
    (row ``legacy-module-restore``).

    ``definitions_first`` (``--naming-strategy definitions-first``): in each family of models that asked for one name
    in one module, the first definition in definition order (a definition of the input documents before one of a
    document they reference, in the order the parse reached them) takes the name, and a model that held it takes the
    name the definition gave up where that is a numbered name, else the first free one; a model whose name is composed
    from one of them is named again from its new name (#4825).
    """

    definitions_first: bool = False

    @property
    def legacy(self) -> bool:
        """Whether no rule is on, so the plan names no model."""
        return not self.definitions_first


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
    """The names planned after the last claim under ``rules``, by model key; a model it leaves out keeps its name."""

    rules: NamingRules = field(default_factory=NamingRules)
    names: Mapping[ModelKey, PlannedName] = field(default_factory=dict)

    def base_of(self, model: _NamedModel) -> str:
        """Return the name ``model`` was numbered from: its planned base, else the name it asked for, or ``""``.

        L3 groups the models of a module by it, the class name standing in for ``""``, and gives it back where the
        module leaves it free.
        """
        planned = self.names.get(model_key_of(model.reference)) if self.names else None
        return model.duplicate_class_name if planned is None else planned.base


class Namer(Protocol):
    """How the plan asks the parse which names are held and how L1 names a model (``parser/_passes/naming.py``)."""

    def held(self, name: str) -> int:
        """Return how many references of the parse hold ``name``, its module included."""

    def reserved(self, module: str, cls: str, path: str) -> bool:
        """Return whether a name no reference holds is not free for the model at ``path``: excluded or reserved."""

    def suffix(self, kind: str) -> str | None:
        """Return the duplicate name suffix of the classes of ``kind`` (``--duplicate-name-suffix``)."""

    def composed(self, claim: NameClaim, base: str) -> tuple[str, str]:
        """Return the module and class L1 gives the composed model of ``claim`` when its base is named ``base``."""


@final
class _Allocation:
    """The names the plan gives, against the names the models outside it hold."""

    __slots__ = ("_given", "_namer", "_released")

    def __init__(self, namer: Namer, released: Counter[str]) -> None:
        self._namer = namer
        self._released = released
        self._given: set[str] = set()

    def taken(self, module: str, cls: str, path: str) -> bool:
        """Return whether ``cls`` is not free in ``module``, at its root as the yielding names count it too."""
        return any(
            name in self._given or self._namer.held(name) > self._released[name]
            for name in dict.fromkeys((f"{module}{cls}", cls))
        ) or self._namer.reserved(module, cls, path)

    def give(self, claim: NameClaim, cls: str, base: str) -> PlannedName:
        """Give ``cls`` to ``claim``, numbered from ``base``."""
        self._given.add(f"{claim.module}{cls}")
        return PlannedName(claim.module, cls, base)

    def first_free(self, claim: NameClaim, base: str, module: str | None = None) -> str:
        """Return the first free name of ``base`` numbered as the models are (``joined``, the duplicate suffix)."""
        module = claim.module if module is None else module
        return first_free(
            base,
            lambda candidate: self.taken(module, candidate, claim.path),
            "joined",
            suffix=self._namer.suffix(claim.kind),
        )[0]


def _definitions_first(claims: Sequence[NameClaim]) -> dict[ModelKey, NameClaim]:
    """Return the claims the definitions-first rule touches, each with the first definition of its family.

    A family is the claims that asked for one class name in one module. Its first definition, in definition order,
    is touched where it does not hold that name or another claim of the family holds it too; so is each such holder.
    """
    families: dict[tuple[str, str], list[NameClaim]] = {}
    for claim in claims:
        if not claim.keyed:
            families.setdefault((claim.module, claim.base), []).append(claim)
    touched: dict[ModelKey, NameClaim] = {}
    for (_, base), members in families.items():
        if not (definitions := [claim for claim in members if claim.role == "definition"]):
            continue
        first = min(definitions, key=lambda claim: (claim.document, claim.order))
        holders = [claim for claim in members if claim is not first and claim.cls == base]
        if holders or first.cls != base:
            touched.update((claim.key, first) for claim in (first, *holders))
    return touched


def _closure(touched: Iterable[ModelKey], claims: Sequence[NameClaim], namer: Namer) -> dict[ModelKey, None]:
    """Return ``touched`` and every claim composed from one of them, recursively, bases before what they compose.

    A claim is composed from its base where L1 named it so: its name is the one L1 composes from the base's name, or a
    numbered one of it.
    """
    by_key = {claim.key: claim for claim in claims}
    composed: dict[ModelKey, list[NameClaim]] = {}
    for claim in claims:
        if claim.composed_from is not None and not claim.keyed:
            composed.setdefault(claim.composed_from, []).append(claim)
    closure = dict.fromkeys(touched)
    pending = list(closure)
    while pending:
        base = by_key[pending.pop(0)]
        for child in composed.get(base.key, ()):
            module, cls = namer.composed(child, base.name)
            if (
                child.key not in closure
                and module == child.module
                and is_numbered(child.cls, cls, "joined", namer.suffix(child.kind))
            ):
                closure[child.key] = None
                pending.append(child.key)
    return closure


def plan_names(rules: NamingRules, claims: Sequence[NameClaim], namer: Namer) -> NamingPlan:
    """Return the names ``rules``, a rule on at least, plan after the last claim, by model key.

    A rule touches some claims; every claim composed from a touched one, recursively, is named again from its base's
    new name, and every other model keeps its name, which no planned name takes. Under ``definitions_first`` the first
    definition of a family takes the family's name, or the first free one where a model outside the plan holds it;
    each claim that held it takes the name the definition gave up where that is a numbered name of it, else the first
    free one.
    """
    if not (touched := _definitions_first(claims)):
        return NamingPlan(rules)
    by_key = {claim.key: claim for claim in claims}
    closure = _closure(touched, claims, namer)
    allocation = _Allocation(namer, Counter(by_key[key].name for key in closure))
    names: dict[ModelKey, PlannedName] = {}
    for key, first in touched.items():
        if key == first.key:
            names[key] = allocation.give(first, allocation.first_free(first, first.base), first.base)
    for key, first in touched.items():
        if key != first.key:
            claim = by_key[key]
            given_up = first.cls
            name = (
                given_up
                if given_up != first.base
                and is_numbered(given_up, first.base, "joined", namer.suffix(first.kind))
                and not allocation.taken(claim.module, given_up, claim.path)
                else allocation.first_free(claim, first.base)
            )
            names[key] = allocation.give(claim, name, first.base)
    for key in closure:
        if key not in names:
            claim = by_key[key]
            base = names[cast("ModelKey", claim.composed_from)]
            _, cls = namer.composed(claim, f"{base.module}{base.cls}")
            names[key] = allocation.give(claim, allocation.first_free(claim, cls), cls)
    return NamingPlan(
        rules,
        {
            key: planned
            for key, planned in names.items()
            if (planned.cls, planned.base) != (by_key[key].cls, by_key[key].base)
        },
    )
