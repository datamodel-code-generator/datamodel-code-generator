"""Frozen records of how the class body writes a field, built by the decisions and read by the passes.

``AssignmentFacts`` comes from ``model/_decisions/assignment.py:classify_assignment`` through the output model's
adapter ``model/base.py:DataModel._field_assignment_facts``; constructor ordering, keyword-only fixes, hidden-type
checks and the signature warning read it instead of classifying the field themselves.

``FieldRenderPlan`` is what a field of a Pydantic ``BaseModel``, ``RootModel`` or dataclass, or of a type alias, writes:
the form of its annotation, its ``Field()`` call, its default, the imports the call needs and every item the field
declares that it does not write, each with the ``Why`` that put it there. The backend builds it
(``model/pydantic_base.py:DataModelField``, ``model/pydantic_v2/dataclass.py:_PydanticDataclassField``). The
parser freezes one plan per field in the parse's ``RenderReady`` when render preparation ends (B2), and the field's
``field``, ``annotated``, ``type_hint``, ``represented_default`` and ``has_default_factory_in_field`` read the frozen
plan from then on; before B2, and for a field that no parse froze, they read a plan the same builder makes without
keeping it. A plan holds values, not text: its text is written when it is read, so the names it holds (the model a
default factory builds, an aliased pattern expression) are the final ones.

Named legacy row ``legacy.L-NF``: render preparation decides the default factories of optional nested models
(``NestedFactoryMetadata``) before B2, but the import snapshot reads each field as if the factory were written, as it
did when the decision came after the snapshot: the parse keeps a second plan for a field whose factory the decision
drops or rewrites, which ``RenderReady.import_view`` returns. The models of the other outputs read the decision from
the point it was published before. The fix (msgspec ``UNSET`` ``NameError``, the ``RecursionError`` under
``--strip-default-none``) deletes the second plan.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Literal, NamedTuple, NewType, Protocol, TypeAlias, final

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator.imports import Import

__all__ = [
    "RENDER_READY",
    "AnnotationForm",
    "AssignmentFacts",
    "AssignmentRule",
    "CallPlan",
    "ConstructorDefault",
    "FieldDefault",
    "FieldRenderPlan",
    "Kwarg",
    "NestedFactoryMetadata",
    "OmitReason",
    "Omitted",
    "PlannedField",
    "Positional",
    "PositionalKind",
    "RenderReady",
    "RuleId",
    "Why",
    "frozen_render_plan",
    "visible_nested_factories",
]

ConstructorDefault: TypeAlias = Literal["none", "value", "factory"]
"""The default a field has in its model's constructor: none, a value, or a default factory."""

AssignmentRule: TypeAlias = Literal[
    "unassigned",
    "field-call",
    "default",
    "dataclass-visible-metadata",
    "hook",
    "legacy-stdlib-annotated",
]
"""The rows of ``classify_assignment``: why the class body writes, or does not write, an assignment.

* ``unassigned``: nothing follows the annotation: a required field, or a ``None`` default that
  ``--strip-default-none`` strips.
* ``field-call``: the field's call (``Field(...)``, ``field(...)``) follows the annotation.
* ``default``: the default follows the annotation; a msgspec Struct writes one (``UNSET`` or ``None``) for every
  optional field, as it never strips its defaults.
* ``dataclass-visible-metadata``: a Pydantic dataclass field under ``--use-annotated`` whose metadata the dataclass
  must see is an assignment exactly when it renders one.
* ``hook``: the output model brings its own assignment hooks (``FIELD_ASSIGNMENT_CHECKER`` and the others), which
  answer instead of a row.
* ``legacy-stdlib-annotated``: a stdlib dataclass field under ``--use-annotated`` that writes ``field(...)`` counts as
  unassigned when it is required or strips its ``None``, as if ``field(...)`` went into ``Annotated``, which the
  dataclass template never writes; ordering then lets ``field(default_factory=...)`` precede a required field
  (``TypeError`` at import). The fix counts the call it writes and deletes the row.
"""


@final
@dataclass(frozen=True, slots=True)
class AssignmentFacts:
    """What the class body writes for a field and what the field is in its model's constructor.

    ``assigned`` tells that an assignment follows the annotation, as constructor ordering counts it, and
    ``construction_assigned`` the same as the model's construction key counts it when the model is built (a Pydantic
    dataclass counts a ``None`` default that ``--strip-default-none`` may keep as none there). ``binds_class_attribute``
    tells that the class body binds the field's name, which can hide a type of that name.
    """

    assigned: bool
    construction_assigned: bool
    binds_class_attribute: bool
    constructor_default: ConstructorDefault
    participates_in_constructor: bool
    rule: AssignmentRule

    @property
    def has_default(self) -> bool:
        """Return whether the constructor gives the field a default, a value or a factory."""
        return self.constructor_default != "none"

    @property
    def default_info(self) -> tuple[bool, bool]:
        """Return the constructor default as the ``FIELD_DEFAULT_CLASSIFIER`` pair: (has a default, it is a value)."""
        return self.constructor_default != "none", self.constructor_default == "value"


RuleId = NewType("RuleId", str)
"""The decision row that produced or dropped a plan item, e.g. ``D2.pydantic.constraint`` or ``legacy.L-NF``."""


class Why(NamedTuple):
    """Why a plan item is there, or why a declared item is not written.

    ``keyword`` is the schema keyword or field attribute the item comes from, ``option`` the option that selected
    the rule, ``via`` the earlier decisions it went through. The schema location is not stored: it is the location of
    the field's model and property.
    """

    rule: RuleId
    keyword: str | None = None
    option: str | None = None
    via: tuple[RuleId, ...] = ()


class Kwarg(NamedTuple):
    """One keyword argument of a call: its name, its value (written when the plan is read) and the items it writes.

    ``items`` name the declared items of the field the argument writes (``Omitted.item`` names them the same way);
    an argument the backend adds, such as ``strict=True`` for a strict ``Decimal``, writes none.
    """

    name: str
    value: object
    why: Why
    items: tuple[str, ...] = ()


OmitReason: TypeAlias = Literal[
    "class-var",
    "anyurl",
    "self-reference-container",
    "strict-kwargs",
    "untaken-by-type",
    "override-type-does-not-take",
    "none-type",
    "consumed-by-type",
    "use-field-description",
    "backend-cannot-express",
    "union-mode-inapplicable",
    "alias-generator-reproduces",
    "validation-aliases",
    "serialization-alias-is-name",
    "compiled-pattern-metadata",
    "none-value",
    "not-a-property-name",
    "superseded",
    "factory-not-applicable",
]
"""Why a declared item of a field is not written.

* ``class-var``: a class variable writes its default only, no ``Field()`` call.
* ``anyurl``: the type holds ``AnyUrl`` outside a container, and the field is written without constraints (row
  ``legacy-anyurl-keeps-string-keywords`` of ``_constraint_applicability``); it warns.
* ``self-reference-container``: the field names its own model outside a container, which takes no constraints.
* ``strict-kwargs``: a strict type that carries its arguments writes the constraints itself.
* ``untaken-by-type``: the type of the field, or of a part collapsing inlined into it, does not take the keyword
  (recorded at L5); it warns.
* ``override-type-does-not-take``: ``--type-overrides`` replaced the type and its arguments; it warns.
* ``none-type``: a number constraint on a ``None`` type.
* ``consumed-by-type``: the type expresses the keyword (``const`` as a ``Literal``, an integer ``multipleOf`` of 1).
* ``use-field-description``: ``--use-field-description`` writes the description as the field's docstring.
* ``backend-cannot-express``: the backend has no argument for it (``unique_items`` in Pydantic 2).
* ``union-mode-inapplicable``: ``union_mode`` on a type that is no union, or beside a discriminator.
* ``alias-generator-reproduces``: the model's alias generator gives the same alias.
* ``validation-aliases``: ``AliasChoices`` replaces the single alias.
* ``serialization-alias-is-name``: the serialization alias equals the field name.
* ``compiled-pattern-metadata``: the prepared pattern expression, which the ``pattern`` argument writes.
* ``none-value``: the value is ``None``, which ``Field()`` takes as unset.
* ``not-a-property-name``: a discriminator that is neither a property name nor a mapping with ``propertyName``.
* ``superseded``: a later argument of the same name replaced it.
* ``factory-not-applicable``: a declared default factory of a required field or of one with a default value.
"""


class Omitted(NamedTuple):
    """A declared item of a field that is not written, its reason and whether the warning pass reports it.

    ``detail`` holds what the warning text names besides the item: the target version from which the type takes an
    untaken keyword, or the type hint ``--type-overrides`` replaced.
    """

    item: str
    value: object
    reason: OmitReason
    why: Why
    warns: bool = False
    detail: str | None = None


PositionalKind: TypeAlias = Literal["required", "default", "none"]
"""The positional argument of a ``Field()`` call: ``...``, the field's default, or ``None``."""


class Positional(NamedTuple):
    """The positional argument of a call, written before its keyword arguments."""

    kind: PositionalKind
    why: Why


@final
@dataclass(frozen=True, slots=True)
class CallPlan:
    """A ``Field()`` call: its positional argument, its default factory and its other keyword arguments.

    The arguments are written in that order, the keyword arguments sorted by their text. ``default_keyword`` writes
    the positional argument as ``default=`` where the call follows the annotation (``--use-default-kwarg``); inside
    ``Annotated`` it stays positional.
    """

    positional: Positional | None
    factory: Kwarg | None
    kwargs: tuple[Kwarg, ...]
    default_keyword: bool
    why: Why


@final
@dataclass(frozen=True, slots=True)
class FieldDefault:
    """The default a field writes: its value, or Pydantic's ``MISSING`` sentinel (``--use-missing-sentinel``)."""

    value: object
    missing_sentinel: bool
    why: Why


AnnotationForm: TypeAlias = Literal["plain", "annotated", "classvar"]
"""How the annotation is written: the type, ``Annotated[type, Field(...)]`` or ``ClassVar[type]``."""


@final
@dataclass(frozen=True, slots=True)
class FieldRenderPlan:
    """What the class body writes for one field, with the reason of every item; text is written when it is read.

    ``has_default_factory_in_field`` is the template condition of the same name: the call writes the default factory
    that the field's own facts give it (a Pydantic dataclass's factory for a mutable default does not count).
    ``symbol_imports`` are the imports the call needs; the type hint's imports come from its data type.
    A Pydantic dataclass field under ``--use-annotated`` whose call the dataclass must see has a ``plain`` annotation
    and writes ``call`` after it, its default moved into the call where the dataclass needs it.
    """

    annotation_form: AnnotationForm
    call: CallPlan | None
    default: FieldDefault
    has_default_factory_in_field: bool
    omitted: tuple[Omitted, ...]
    symbol_imports: tuple[tuple[Import, Why], ...]
    why: Why

    def written_items(self) -> frozenset[str]:
        """Return the declared items the call writes."""
        if (call := self.call) is None:
            return frozenset()
        arguments = (*call.kwargs, call.factory) if call.factory is not None else call.kwargs
        return frozenset(item for argument in arguments for item in argument.items)

    def unaccounted(self, declared: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
        """Return the declared items neither written nor omitted, and the written or omitted items not declared."""
        accounted = self.written_items() | {omitted.item for omitted in self.omitted}
        return declared - accounted, accounted - declared


class PlannedField(Protocol):
    """A field that renders from a render plan, as the parse reads it to freeze and check its plan."""

    @property
    def name(self) -> str | None:
        """The field's name."""

    def _build_frozen_render_plan(self) -> FieldRenderPlan | None:
        """Build the plan the parse freezes for the field."""

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``."""

    def _declared_render_items(self) -> frozenset[str]:
        """Return the items the field declares that its plan must write or omit."""


@final
@dataclass(frozen=True, slots=True)
class NestedFactoryMetadata:
    """What decides the default factory of an optional nested model, final when render preparation ends.

    ``order`` gives each model (by ``id``) its module and its position there; ``recursive`` gives the models of each
    reference cycle the models of that cycle, whose nested factories would build each other forever.
    """

    order: Mapping[int, tuple[int, int]]
    recursive: Mapping[ModelKey, frozenset[ModelKey]]


@final
class RenderReady:
    """The field render plans one parse froze at B2, and the nested-factory decision render preparation made.

    A plan is looked up by its field object. ``import_view`` returns, inside it, the second plan of a field that row
    ``legacy.L-NF`` keeps.
    """

    __slots__ = ("_deciding", "_import_view", "_plans", "_rebuilding", "nested_factories")

    def __init__(self) -> None:
        """Start a parse with no plan frozen."""
        self._plans: dict[int, tuple[object, FieldRenderPlan, FieldRenderPlan | None]] = {}
        self._import_view = False
        self._deciding = False
        self._rebuilding = False
        self.nested_factories: NestedFactoryMetadata | None = None

    def freeze(self, field: object, plan: FieldRenderPlan, import_plan: FieldRenderPlan | None = None) -> None:
        """Keep ``plan`` as the field's plan, and ``import_plan`` as the one the import snapshot reads, if different."""
        self._plans[id(field)] = (field, plan, import_plan)

    def plan_of(self, field: object) -> FieldRenderPlan | None:
        """Return the plan frozen for ``field`` in the current view, or None when the parse froze none for it."""
        if self._rebuilding or (entry := self._plans.get(id(field))) is None or entry[0] is not field:
            return None
        if self._import_view and entry[2] is not None:
            return entry[2]
        return entry[1]

    def frozen(self) -> Iterator[tuple[object, FieldRenderPlan]]:
        """Yield every field with the plan frozen for it."""
        for field, plan, _ in self._plans.values():
            yield field, plan

    def deciding_nested_factories(self) -> NestedFactoryMetadata | None:
        """Return the nested-factory decision while ``nested_factories_decided`` runs, else None."""
        return self.nested_factories if self._deciding else None

    @contextmanager
    def nested_factories_decided(self) -> Generator[None, None, None]:
        """Let the nested-factory decision answer the fields' factories while the block builds the plans to freeze."""
        self._deciding = True
        try:
            yield
        finally:
            self._deciding = False

    @contextmanager
    def rebuilding(self) -> Generator[None, None, None]:
        """Answer no frozen plan while the block rebuilds plans from the fields' facts, to compare them."""
        self._rebuilding = True
        try:
            yield
        finally:
            self._rebuilding = False

    @contextmanager
    def import_view(self) -> Generator[None, None, None]:
        """Return the plans the import snapshot reads (row ``legacy.L-NF``) while the block runs."""
        self._import_view = True
        try:
            yield
        finally:
            self._import_view = False


RENDER_READY: Final[ContextVar[RenderReady | None]] = ContextVar("render_ready", default=None)
"""The ``RenderReady`` of the parse that runs, or None outside a parse."""


def visible_nested_factories() -> NestedFactoryMetadata | None:
    """Return the nested-factory decision while the running parse builds the plans it freezes, else None."""
    if (ready := RENDER_READY.get()) is None:
        return None
    return ready.deciding_nested_factories()


def frozen_render_plan(field: object) -> FieldRenderPlan | None:
    """Return the plan the running parse froze for ``field``, or None before B2, outside a parse or for no plan."""
    if (ready := RENDER_READY.get()) is None:
        return None
    return ready.plan_of(field)
