"""The names of the models: the name a model takes from the model it is built for, and the plan of the final names.

L1 names a model it builds for another one, the parent, after the parent's name and the word of the child's role
(``ComposedRole``): the enum of a nullable enum (``Enum``), the model of a map's values (``AdditionalProperty``), the
alias that holds the constraints of a patternProperties value (``PatternProperty``), the enum of an allOf array's items
(``Item``), the map branch of a type list (``Object``), the constrained branch of a type list (the titled type), the
response of a status code (the capitalized code), the request and response variants of a model (``rw_variant_name``)
and a union branch without a name of its own, which takes the parent's name as it is. Each string is released output.

While a naming rule reads them, L1 records for every model the frames of the model builders open when the model is
recorded (``NamingFrames``, ``BuildRecord``), which name its owner, the model whose builder built it, and what the
code that names the model claims for it (``Claim``): the model its name is composed from, or its position as a union
branch named as the union.

Once every model has claimed its name, the L2 pass ``name_models`` records the names planned after the last claim
(``NamingPlan``, made by ``plan_names`` under the ``NamingRules``), which L3 reads for the name a model was numbered
from.
This module imports only the standard library, ``typing_extensions``, the model keys and the record decorator, so
every layer may use it.
"""

from __future__ import annotations

from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field
from string import digits
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, cast

from typing_extensions import Protocol, final

from datamodel_code_generator._free_names import first_free, is_numbered, numbered_class
from datamodel_code_generator._model_keys import ModelKey, model_key_of
from datamodel_code_generator._records import record

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Mapping, Sequence
    from types import TracebackType

_ResultT = TypeVar("_ResultT")
_NamedModelT = TypeVar("_NamedModelT", bound="_NamedModel")

__all__ = [
    "BUILD_RECORD_KEY",
    "UNRECORDED",
    "AllOfPart",
    "BuildFrame",
    "BuildRecord",
    "Claim",
    "ClaimRole",
    "Composed",
    "ComposedRole",
    "NameClaim",
    "NameRestore",
    "Namer",
    "NamingFrames",
    "NamingPlan",
    "NamingRules",
    "NamingScope",
    "Ordinal",
    "PlannedName",
    "Tag",
    "VariantRole",
    "build_record_of",
    "composed_name",
    "plan_names",
    "rw_variant_class_name",
    "rw_variant_name",
]

ComposedRole: TypeAlias = Literal[
    "enum", "additional-property", "pattern-property", "item", "object", "type", "status", "variant-parent"
]
"""The role of a model that L1 names after the model it is built for (see the module docstring)."""
_ROLE_WORDS: Final[Mapping[ComposedRole, str | Callable[[str], str]]] = {
    "enum": "Enum",
    "additional-property": "AdditionalProperty",
    "pattern-property": "PatternProperty",
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


class _ClassName(Protocol):
    """What ``rw_variant_class_name`` reads of the class name a resolver gives."""

    @property
    def name(self) -> str:
        """The class name."""


class _ClassNameResolver(Protocol):
    """What ``rw_variant_class_name`` reads of the model resolver."""

    @property
    def class_name_suffix(self) -> str:
        """The suffix every class name takes."""

    def get_class_name(self, name: str, *args: Any, **kwargs: Any) -> _ClassName:
        """Return the class name the resolver gives ``name``."""


def rw_variant_class_name(
    resolver: _ClassNameResolver,
    base_name: str,
    suffix: Literal["Request", "Response"],
    *,
    affixed: bool = True,
    **kwargs: Any,
) -> str:
    """Return the class name ``resolver`` gives the ``suffix`` variant of the model named ``base_name``.

    Unless ``affixed``, the variant is named before ``--class-name-suffix``.
    """
    class_name_suffix = resolver.class_name_suffix if affixed else ""
    return resolver.get_class_name(
        rw_variant_name(base_name, suffix, class_name_suffix), skip_affix=True, preserve_name=True, **kwargs
    ).name


BUILD_RECORD_KEY: Final = "_build_record"
"""The key of the ``BuildRecord`` of a model on the ``Reference`` that names it."""
VariantRole: TypeAlias = Literal["Request", "Response"]
"""The role of a read or write variant of a model, the suffix ``rw_variant_name`` gives it."""


@final
@record(eq=False)
class Composed:
    """The claim of a model that L1 names after the name of another model, its base, and the word of its role."""

    base: object
    """The ``Reference`` of the base."""
    role: ComposedRole | VariantRole


@final
@record
class Ordinal:
    """The claim of a union branch with no name of its own, which L1 names as the union: its position, from 1."""

    position: int


@final
@record
class Tag:
    """The claim of a union branch named after its tag: the part of its name its tag gives, after the union's name."""

    text: str


@final
@record(eq=False)
class AllOfPart:
    """The claim of a class an allOf builds for a oneOf or anyOf it holds: a union member, or the shared part.

    A member has its ``position`` among the combinations of branches, from 1, and the ``branches`` (``Reference``) it
    combines with the shared part; the shared part has position 0 and no branches.
    """

    position: int
    branches: tuple[object, ...] = ()


Claim: TypeAlias = Composed | Ordinal | Tag | AllOfPart
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
@record(eq=False)
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
    base's owner. A document is parsed with no frame open (``detached``), whatever parse reached it, so its models are
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

    @contextmanager
    def detached(self) -> Generator[None]:
        """Close every frame for the ``with`` block of a document parse, and open them again once it ends."""
        stack, self._stack = self._stack, []
        try:
            yield
        finally:
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


@final
class NamingScope:
    """The frame of a model builder, or of a naming decision with its claim, open while its ``with`` block runs.

    ``built`` passes on what the block returns, noted as the model the frame built (``NamingFrames.built``). A block
    is no call, so the frames add no level to the stack of a nested parse. ``UNRECORDED`` opens no frame, for a parse
    that records none.
    """

    __slots__ = ("_claim", "_frame", "_frames")

    def __init__(self, frames: NamingFrames | None = None, claim: Claim | None = None) -> None:
        """Hold the frames to open a frame in, if any, and the claim the frame makes."""
        self._frames = frames
        self._claim = claim
        self._frame: BuildFrame | None = None

    def __enter__(self) -> NamingScope:
        """Open the frame."""
        if self._frames is not None:
            self._frame = self._frames.open(self._claim)
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        """Close the frame."""
        if self._frames is not None:
            self._frames.close()

    def built(self, result: _ResultT) -> _ResultT:
        """Return ``result``, noted as what the frame returned."""
        if self._frame is not None:
            NamingFrames.built(self._frame, result)
        return result


UNRECORDED: Final = NamingScope()
"""The scope of a builder or a naming decision while L1 records no naming frames: it opens none."""


def build_record_of(reference: object) -> BuildRecord | None:
    """Return the frames recorded for the model of ``reference`` while L1 recorded frames, if it recorded them."""
    return reference.__dict__.get(BUILD_RECORD_KEY)


ClaimRole: TypeAlias = Literal[
    "definition", "root", "owned", "composed", "ordinal", "tagged", "allof-member", "allof-shared"
]
"""What a model is to the naming rules (``NameClaim.role``), read from its records once L1 has built every model.

``composed``: its name is composed from another model's (``Composed``); ``ordinal``: a union branch named as the
union (``Ordinal``); ``tagged``: a union branch named after its tag (``Tag``); ``allof-member`` and ``allof-shared``:
a union member and the shared part an allOf builds for a oneOf or anyOf it holds (``AllOfPart``); ``owned``: built by
the builder of another model (``BuildRecord.owner``); ``definition``: built by
no other model's builder from an entry of a definitions container of its document; ``root``: none of these, such as a
document root or an operation model.
"""


@final
@record
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
    local: str
    """The part of a tagged claim's name its tag gives (``Tag.text``), else ``""``."""
    branches: tuple[ModelKey, ...]
    """The branches an allOf member combines (``AllOfPart.branches``), else none."""
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
@record
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

    ``enclosing_model`` (``--naming-strategy enclosing-model``): every model is named again, owners first and
    definitions before the other models: a model built for another one, its owner, takes the owner's final class
    joined once with its own part (the name it asked for, its position as a union branch named as the union, or the
    word of a composed role), and a definition, a document root or an operation model takes its own name (#4855,
    #4845, #4826, #5666); a name another model takes first is numbered, with ``_`` where it ends in a digit. Where L3
    gives a numbered model its name back in its module, a model whose name was composed from the numbered one takes
    the name composed from the name given back, where the module leaves it free (#6299).

    ``allof_union_members`` (``--name-allof-union-members-after-branches``): the classes an allOf gives the branches
    of a oneOf or anyOf it holds take the holder's name and the branch's part (its tag, the class of a ``$ref`` branch,
    else its position), a branch-only base the class's name and ``Base``, and the shared part the holder's name and
    ``Base`` (#4847); with ``definitions_first`` or alone it touches these classes only.
    """

    definitions_first: bool = False
    enclosing_model: bool = False
    allof_union_members: bool = False

    @property
    def strategy(self) -> bool:
        """Whether a ``--naming-strategy`` value plans the names."""
        return self.definitions_first or self.enclosing_model

    @property
    def legacy(self) -> bool:
        """Whether no rule is on, so the plan names no model."""
        return not (self.strategy or self.allof_union_members)


@final
@record
class PlannedName:
    """The name the plan gives a model: its module (dotted, ``""`` for the root module), its class, and its base.

    The base is the name the class was numbered from, which L3 groups and restores the model by.
    """

    module: str
    cls: str
    base: str

    @property
    def name(self) -> str:
        """The name the plan gives the model, its module included."""
        return f"{self.module}{self.cls}"


@final
@record
class NameRestore:
    """The class a model takes in its module where L3 gives back their bases to the models its planned name reads.

    ``reads`` holds each model whose numbered name the planned name was composed from, with the class it holds once
    L3 gave it back; ``order`` puts a model after the models it reads.
    """

    cls: str
    reads: tuple[tuple[ModelKey, str], ...]
    order: int


class _NamedReference(Protocol):
    """What ``NamingPlan.reference_base_of`` reads of a reference."""

    @property
    def duplicate_name(self) -> str | None:
        """The name the resolver numbered the reference from, else None."""


class _NamedModel(Protocol):
    """What ``NamingPlan.base_of`` and ``NamingPlan.restores_in`` read of a model."""

    @property
    def reference(self) -> object:
        """The reference that names the model."""

    @property
    def duplicate_class_name(self) -> str:
        """The class name the model asked for where the resolver numbered it, else ``""``."""


@final
@record
class NamingPlan:
    """The names planned after the last claim under ``rules``, by model key; a model it leaves out keeps its name.

    ``restores`` holds, under ``enclosing_model``, the class each model whose name reads a numbered name takes where L3
    gives the numbered models back their bases (``restores_in``).
    """

    rules: NamingRules = field(default_factory=NamingRules)
    names: Mapping[ModelKey, PlannedName] = field(default_factory=dict)
    restores: Mapping[ModelKey, NameRestore] = field(default_factory=dict)

    def base_of(self, model: _NamedModel) -> str:
        """Return the name ``model`` was numbered from: its planned base, else the name it asked for, or ``""``.

        L3 groups the models of a module by it, the class name standing in for ``""``, and gives it back where the
        module leaves it free.
        """
        planned = self.names.get(model_key_of(model.reference)) if self.names else None
        return model.duplicate_class_name if planned is None else planned.base

    def reference_base_of(self, reference: _NamedReference) -> str:
        """Return the name ``reference`` was numbered from, as ``base_of`` does for its model, also without one."""
        planned = self.names.get(model_key_of(reference)) if self.names else None
        return numbered_class(reference.duplicate_name) if planned is None else planned.base

    def restores_in(self, models: Iterable[_NamedModelT]) -> list[tuple[_NamedModelT, NameRestore]]:
        """Return the models of ``models`` that have a restore, with it, each after the models it reads."""
        if not self.restores:
            return []
        return sorted(
            (
                (model, restore)
                for model in models
                if (restore := self.restores.get(model_key_of(model.reference))) is not None
            ),
            key=lambda item: item[1].order,
        )


class Namer(Protocol):
    """How the plan asks the parse which names are held and how L1 names a model (``parser/_passes/naming.py``)."""

    def held(self, name: str) -> int:
        """Return how many references of the parse hold ``name``, its module included."""

    def reserved(self, module: str, cls: str, path: str) -> bool:
        """Return whether a name no reference holds is not free for the model at ``path``: excluded or reserved."""

    def suffix(self, kind: str) -> str | None:
        """Return the duplicate name suffix of the classes of ``kind`` (``--duplicate-name-suffix``)."""

    def composed(self, claim: NameClaim, base: str, *, affixed: bool = True) -> tuple[str, str]:
        """Return the module and class L1 gives the composed model of ``claim`` when its base is named ``base``.

        Without ``affixed`` the class takes no ``--class-name-prefix`` or suffix.
        """

    def own(self, claim: NameClaim) -> tuple[str, str]:
        """Return the module and class, without affixes, of the name ``claim`` asked for."""

    def prefixed(self, claim: NameClaim, owner: str) -> str:
        """Return the class, without affixes, of ``owner`` joined with the claim's own part, as full-path joins them.

        The own part is the name the claim asked for, or the part of it its tag gives (``NameClaim.local``).
        """

    def affixed(self, claim: NameClaim, cls: str) -> str:
        """Return ``cls`` with the class name affixes the model of ``claim`` takes."""

    def stem(self, claim: NameClaim, cls: str) -> str:
        """Return ``cls`` without the class name affixes the model of ``claim`` takes, where it is their form of one.

        Any other class, such as one numbered after its affixes or one ``--model-name-map`` gives, which takes no
        affixes, is its own stem.
        """

    def camel(self, text: str) -> str:
        """Return ``text`` in UpperCamelCase, as a full-path name joins a part."""

    def prefixed_as_written(self, claim: NameClaim, owner: str) -> tuple[str, str] | None:
        """Return the module and class L1 gives ``claim`` with ``owner`` as its parent's name, where L1 prefixes.

        None where L1 prefixes no name or the claim asked for none.
        """


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

    def give(self, module: str, cls: str, base: str) -> PlannedName:
        """Give ``cls`` of ``module``, numbered from ``base``."""
        self._given.add(f"{module}{cls}")
        return PlannedName(module, cls, base)

    def first_free(self, claim: NameClaim, base: str, module: str | None = None, *, affixed: bool = False) -> str:
        """Return the first free name of ``base`` numbered as the models are, with the duplicate suffix.

        A base that ends in a digit is numbered with ``_`` (``StreamEvent1_1``), else joined (``Event1``). With
        ``affixed`` a candidate is free where its class with the affixes of ``claim`` is (``Namer.affixed``).
        """
        module = claim.module if module is None else module
        namer = self._namer
        return first_free(
            base,
            lambda candidate: self.taken(module, namer.affixed(claim, candidate) if affixed else candidate, claim.path),
            "underscore" if base[-1:].isdigit() else "joined",
            suffix=namer.suffix(claim.kind),
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


_Via: TypeAlias = tuple[Literal["composed", "prefixed"], ModelKey] | None
"""How a claim of the closure is named from another one: composed from its base, prefixed with its owner's name."""
_PREFIXED_ROLES: Final = frozenset({"owned", "tagged", "ordinal"})
"""The roles whose name L1 prefixes with the name of the model above them, where it prefixes names."""


def _closure(
    touched: Iterable[ModelKey], claims: Sequence[NameClaim], by_key: Mapping[ModelKey, NameClaim], namer: Namer
) -> dict[ModelKey, _Via]:
    """Return ``touched`` and every claim named from one of them, recursively, before what is named from them.

    A claim is named from another where L1 named it so: composed from its base, or prefixed with its owner's name
    under ``full-path`` or ``parent-prefixed``; its name is the one L1 makes of the other's, or a numbered one of it.
    Each claim maps to how it is named from the other one (``_Via``), a touched claim to None.
    """
    children: dict[ModelKey, list[tuple[NameClaim, Literal["composed", "prefixed"]]]] = {}
    for claim in claims:
        if claim.keyed:
            continue
        if claim.composed_from is not None:
            children.setdefault(claim.composed_from, []).append((claim, "composed"))
        elif claim.owner is not None and claim.role in _PREFIXED_ROLES:
            children.setdefault(claim.owner, []).append((claim, "prefixed"))
    closure: dict[ModelKey, _Via] = dict.fromkeys(touched)
    pending = list(closure)
    while pending:
        base = by_key[pending.pop(0)]
        for child, via in children.get(base.key, ()):
            if (
                child.key not in closure
                and (name := _named_from(child, via, base.name, namer)) is not None
                and name[0] == child.module
                and is_numbered(child.cls, name[1], "joined", namer.suffix(child.kind))
            ):
                closure[child.key] = via, base.key
                pending.append(child.key)
    return closure


def _named_from(
    claim: NameClaim, via: Literal["composed", "prefixed"], name: str, namer: Namer
) -> tuple[str, str] | None:
    """Return the module and class L1 makes for ``claim`` from ``name``, its base's or its owner's (``_Via``)."""
    return namer.composed(claim, name) if via == "composed" else namer.prefixed_as_written(claim, name)


_AllOfParts: TypeAlias = dict[ModelKey, tuple[Literal["member", "base", "shared"], ModelKey]]
"""The allOf classes the allOf rule names, in the order it names them, each with the claim it is named after."""


def _allof_parts(claims: Sequence[NameClaim], by_key: Mapping[ModelKey, NameClaim]) -> _AllOfParts:
    """Return the classes an allOf builds for the oneOf or anyOf it holds: members, branch-only bases, shared parts.

    A member and a shared part are named after their holder, the allOf's model and their owner; a branch-only base,
    an inline branch the holder built that one member alone combines, after that member. A member that
    ``--model-name-map`` names keeps its name but combines its branches too. Each part comes after the part it is
    named after, such as a branch-only base that is itself the holder of an allOf before the members it holds.
    """
    members = [claim for claim in claims if claim.role == "allof-member" and claim.owner is not None]
    uses = Counter(branch for member in members for branch in member.branches)
    members = [member for member in members if not member.keyed]
    parts: _AllOfParts = {member.key: ("member", cast("ModelKey", member.owner)) for member in members}
    for member in members:
        for branch in member.branches:
            if (
                (claim := by_key.get(branch)) is not None
                and not claim.keyed
                and claim.owner == member.owner
                and uses[branch] == 1
                and branch not in parts
            ):
                parts[branch] = ("base", member.key)
    parts.update(
        (claim.key, ("shared", claim.owner))
        for claim in claims
        if claim.role == "allof-shared" and not claim.keyed and claim.owner is not None
    )
    ordered: _AllOfParts = {}
    for key in parts:
        chain: list[ModelKey] = []
        at = key
        while at in parts and at not in ordered:
            chain.append(at)
            at = parts[at][1]
        ordered.update((part, parts[part]) for part in reversed(chain))
    return ordered


def _allof_name(
    claim: NameClaim,
    part: tuple[Literal["member", "base", "shared"], ModelKey],
    by_key: Mapping[ModelKey, NameClaim],
    final: Callable[[NameClaim], tuple[str, str]],
    namer: Namer,
) -> tuple[str, str]:
    """Return the module and the name, before numbering, the allOf rule gives the class of ``claim``.

    A member takes its holder's name and, of each branch, its tag, the class of a branch its holder did not build (a
    ``$ref`` target), else its own position; a base and a shared part take the name they are named after and ``Base``.
    ``final`` gives the name a claim ends with, which the names compose from.
    """
    kind, of = part
    module, name = final(by_key[of])
    if kind != "member":
        return module, f"{name}Base"
    branch_parts = []
    for branch in claim.branches:
        branch_claim = by_key.get(branch, claim)
        if branch_claim.owner != claim.owner:
            branch_parts.append(final(branch_claim)[1])
        else:
            branch_parts.append(namer.camel(branch_claim.local) if branch_claim.role == "tagged" else "")
    own = "".join(branch_parts) if branch_parts and all(branch_parts) else str(claim.position)
    return module, f"{name}{own}"


def _enclosing_model(
    claims: Sequence[NameClaim], by_key: Mapping[ModelKey, NameClaim], namer: Namer, parts: _AllOfParts
) -> tuple[dict[ModelKey, PlannedName], dict[ModelKey, NameRestore]]:
    """Return the names the enclosing-model rule gives: every claim, owners first, definitions before the others.

    A keyed claim keeps its name, which children compose from as it is. The allOf ``parts`` take the names of the
    allOf rule, made of the names before affixes. A claim whose name is composed from a numbered name, recursively,
    also gets the restore of the name composed from the base of each (``NameRestore``), which L3 gives it in its
    module once it gave them their bases back (#6299).
    """
    stems: dict[ModelKey, tuple[str, str]] = {claim.key: (claim.module, claim.cls) for claim in claims if claim.keyed}
    allocation = _Allocation(namer, Counter(claim.name for claim in claims if claim.key not in stems))
    names: dict[ModelKey, PlannedName] = {}

    def wanted(claim: NameClaim, name_of: Callable[[NameClaim], tuple[str, str]]) -> tuple[str, str]:
        if (part := parts.get(claim.key)) is not None:
            return _allof_name(claim, part, by_key, name_of, namer)
        if claim.role == "composed" and (base := by_key.get(cast("ModelKey", claim.composed_from))) is not None:
            module, stem = name_of(base)
            return module, namer.composed(claim, stem, affixed=False)[1]
        if claim.role in {"owned", "ordinal", "tagged"} and (owner := by_key.get(cast("ModelKey", claim.owner))):
            module, stem = name_of(owner)
            return module, f"{stem}{claim.position}" if claim.role == "ordinal" else namer.prefixed(claim, stem)
        return namer.own(claim)

    def final(claim: NameClaim) -> tuple[str, str]:
        if (stem := stems.get(claim.key)) is not None:
            return stem
        stems[claim.key] = (claim.module, claim.cls)
        module, stem = wanted(claim, final)
        numbered = allocation.first_free(claim, stem, module, affixed=True)
        names[claim.key] = allocation.give(module, namer.affixed(claim, numbered), namer.affixed(claim, stem))
        stems[claim.key] = module, numbered
        return stems[claim.key]

    restored_stems = {claim.key: stems[claim.key] for claim in claims if claim.keyed}
    restores: dict[ModelKey, NameRestore] = {}

    def restored(claim: NameClaim) -> tuple[str, str]:
        if (stem := restored_stems.get(claim.key)) is not None:
            return stem
        restored_stems[claim.key] = final(claim)
        reads: list[tuple[ModelKey, str]] = []

        def read(other: NameClaim) -> tuple[str, str]:
            if (stem := restored(other)) != final(other):
                reads.append((other.key, namer.affixed(other, stem[1])))
            return stem

        restored_stems[claim.key] = module, stem = wanted(claim, read)
        if reads:
            restores[claim.key] = NameRestore(namer.affixed(claim, stem), tuple(reads), len(restores))
        return module, stem

    ordered = sorted(claims, key=lambda claim: (claim.role != "definition", claim.document, claim.order))
    for claim in ordered:
        final(claim)
    for claim in ordered:
        restored(claim)
    return names, restores


def plan_names(rules: NamingRules, claims: Sequence[NameClaim], namer: Namer) -> NamingPlan:
    """Return the names ``rules``, a rule on at least, plan after the last claim, by model key.

    A rule touches some claims; every claim composed from a touched one, recursively, is named again from its base's
    new name, and every other model keeps its name, which no planned name takes. Under ``definitions_first`` the first
    definition of a family takes the family's name, or the first free one where a model outside the plan holds it;
    each claim that held it takes the name the definition gave up where that is a numbered name of it, else the first
    free one; then the allOf parts, which the allOf rule names alone, take theirs, made of the names before affixes
    (``Namer.stem``) and given the affixes of their own classes. Under ``enclosing_model`` every
    claim is named again (``_enclosing_model``).
    """
    by_key = {claim.key: claim for claim in claims}
    parts = _allof_parts(claims, by_key) if rules.allof_union_members else {}
    if rules.enclosing_model:
        return _planned(rules, *_enclosing_model(claims, by_key, namer, parts), by_key)
    touched = {
        key: first
        for key, first in (_definitions_first(claims) if rules.definitions_first else {}).items()
        if key not in parts
    }
    if not (touched or parts):
        return NamingPlan(rules)
    closure = _closure([*touched, *parts], claims, by_key, namer)
    allocation = _Allocation(namer, Counter(by_key[key].name for key in closure))
    names: dict[ModelKey, PlannedName] = {}
    for key, first in touched.items():
        if key == first.key:
            names[key] = allocation.give(first.module, allocation.first_free(first, first.base), first.base)
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
            names[key] = allocation.give(claim.module, name, first.base)

    def final(claim: NameClaim) -> tuple[str, str]:
        planned = names.get(claim.key)
        module, cls = (claim.module, claim.cls) if planned is None else (planned.module, planned.cls)
        return module, namer.stem(claim, cls)

    for key, part in parts.items():
        module, name = _allof_name(by_key[key], part, by_key, final, namer)
        name = namer.affixed(by_key[key], name)
        names[key] = allocation.give(module, allocation.first_free(by_key[key], name, module), name)
    for key, via in closure.items():
        if key not in names:
            claim = by_key[key]
            kind, other = cast("tuple[Literal['composed', 'prefixed'], ModelKey]", via)
            base = names[other]
            _, cls = cast("tuple[str, str]", _named_from(claim, kind, base.name, namer))
            names[key] = allocation.give(claim.module, allocation.first_free(claim, cls), cls)
    return _planned(rules, names, {}, by_key)


def _planned(
    rules: NamingRules,
    names: Mapping[ModelKey, PlannedName],
    restores: Mapping[ModelKey, NameRestore],
    claims: Mapping[ModelKey, NameClaim],
) -> NamingPlan:
    """Return the plan of the names that differ from those L1 gave, in module, class or base, and the restores."""
    return NamingPlan(
        rules,
        {
            key: planned
            for key, planned in names.items()
            if (planned.module, planned.cls, planned.base) != (claims[key].module, claims[key].cls, claims[key].base)
        },
        restores,
    )
