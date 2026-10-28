"""Frozen records of how the class body writes a field, built by the decisions and read by the passes.

``AssignmentFacts`` comes from ``model/_decisions/assignment.py:classify_assignment`` through the output model's
adapter ``model/base.py:DataModel._field_assignment_facts``; constructor ordering, keyword-only fixes, hidden-type
checks and the signature warning read it instead of classifying the field themselves.

``FieldRenderPlan`` is what a field of a Pydantic ``BaseModel``, ``RootModel`` or dataclass, a msgspec ``Struct``, a
stdlib dataclass, a ``TypedDict``, an enum or a type alias writes: the form of its annotation, its calls (``Field()``,
``field()``, ``Meta()``), its default, the imports the calls need and every item the field declares that it does not
write, each with the ``Why`` that put it there. The field's backend builds it (``model/pydantic_base.py``,
``model/pydantic_v2/dataclass.py``, ``model/msgspec.py``, ``model/dataclass.py``, ``model/typed_dict.py``; an enum
member is a field of the output's backend). The parser freezes one plan per field in the parse's ``RenderReady`` when
render preparation ends (B2), and the field's ``field``, ``annotated``, ``type_hint``, ``represented_default``,
``has_default_factory_in_field`` and imports read the frozen plan from then on. Before B2, and for a field that no
parse froze, they read the plan the parse's memo keeps for the field (``model/base.py:live_render_plan``) while the
revisions it read hold, and B2 freezes that plan; the field's imports are kept the same way, by the plan they read
(``model/base.py:field_imports``).
A field, model or parser of a class outside this package computes both at each read. A frozen plan keeps its field's
revisions, or, for such a class or under custom templates, the contents of what it read from its field; a field whose
revisions or contents differ when its model renders, as a model's ``render`` may change them, renders from a plan
rebuilt then. A plan holds values, not text: its text is
written when it is read, so the names it holds (the model a default factory builds, an aliased pattern expression) are
the final ones.

Named legacy row ``legacy.L-NF``: render preparation decides the default factories of optional nested models
(``NestedFactoryMetadata``) before B2, but the import snapshot reads each field as if the factory were written, as it
did when the decision came after the snapshot: the parse keeps a second plan for a field whose factory the decision
drops or rewrites, which ``RenderReady.import_view`` returns. The models that render without frozen plans (GraphQL
unions, custom output models) read the decision from the point ``Parser`` publishes it on the models. The fix
(msgspec ``UNSET`` ``NameError``) deletes the second plan.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from operator import eq, is_
from typing import TYPE_CHECKING, Any, Final, Literal, NamedTuple, NewType, Protocol, TypeAlias, cast, final

from datamodel_code_generator._records import record
from datamodel_code_generator.python_literal import represent_python_value

if TYPE_CHECKING:
    from collections.abc import Generator, Iterator, Mapping

    from datamodel_code_generator._model_keys import ModelKey
    from datamodel_code_generator._parse_memo import ParseMemo
    from datamodel_code_generator.imports import Import

__all__ = [
    "RENDER_READY",
    "AnnotationForm",
    "AssignmentFacts",
    "AssignmentRule",
    "CallArguments",
    "CallKind",
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
    "RenderInputs",
    "RenderReady",
    "RuleId",
    "Why",
    "field_call_text",
    "frozen_render_plan",
    "unchanged_render_inputs",
    "visible_nested_factories",
    "written_argument_names",
    "written_text",
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
@record
class AssignmentFacts:
    """What the class body writes for a field and what the field is in its model's constructor.

    ``assigned`` tells that an assignment follows the annotation, as constructor ordering counts it, and
    ``construction_assigned`` the same as the model's construction key counts it when the model is built (a Pydantic
    dataclass counts a ``None`` default that ``--strip-default-none`` may keep as none there). ``binds_class_attribute``
    tells that the class body binds the field's name, which can hide a type of that name. ``place_assigned`` is
    ``assigned`` as the field takes its own place in the constructor: a msgspec Struct or Pydantic dataclass field
    whose ``None`` default ``--strip-default-none`` strips counts as having none there, as in 0.86.0, unless
    ``--order-constructor-fields-by written-defaults`` is given (``AssignmentInputs.keeps_stripped_place``).
    """

    assigned: bool
    construction_assigned: bool
    binds_class_attribute: bool
    constructor_default: ConstructorDefault
    participates_in_constructor: bool
    rule: AssignmentRule
    place_assigned: bool

    @property
    def has_default(self) -> bool:
        """Return whether the constructor gives the field a default, a value or a factory."""
        return self.constructor_default != "none"

    @property
    def has_place_default(self) -> bool:
        """Return whether the field counts as having a default for its own place in the constructor."""
        return self.place_assigned and self.has_default

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
    "meta-needs-annotated",
    "inexact-float-divisor",
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
* ``backend-cannot-express``: the backend has no argument for it (``unique_items`` in Pydantic 2 and msgspec, a
  non-finite bound in msgspec ``Meta``).
* ``union-mode-inapplicable``: ``union_mode`` on a type that is no union, or beside a discriminator.
* ``alias-generator-reproduces``: the model's alias generator gives the same alias.
* ``validation-aliases``: ``AliasChoices`` replaces the single alias.
* ``serialization-alias-is-name``: the serialization alias equals the field name.
* ``compiled-pattern-metadata``: the prepared pattern expression, which the ``pattern`` argument writes.
* ``none-value``: the value is ``None``, which ``Field()`` takes as unset.
* ``not-a-property-name``: a discriminator that is neither a property name nor a mapping with ``propertyName``.
* ``superseded``: a later argument of the same name replaced it, a stronger bound of the other kind (msgspec ``gt``
  beside ``ge``), or the default factory that replaces a default.
* ``factory-not-applicable``: a declared default factory of a required field or of one with a default value, or a
  declared default of a required field (msgspec, stdlib dataclasses).
* ``meta-needs-annotated``: msgspec writes ``Meta(...)`` only inside ``Annotated`` (``--use-annotated``).
* ``inexact-float-divisor``: msgspec checks ``multiple_of`` on a float by its floating-point remainder, which rejects
  multiples of a divisor that floats only approximate; it warns.
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


@final
class CallArguments:
    """The keyword arguments of a call while the field builds them, with the declared items each writes.

    An argument removed with ``pop`` leaves its items as ``Omitted`` with the reason; ``take`` removes one whose
    items another argument writes. ``factory`` is the default factory a ``Field()`` call writes before its other
    arguments, the one the field's own facts give it; ``field()`` keeps its default factory among its arguments.
    """

    __slots__ = ("_entries", "factory", "omitted")

    def __init__(self, omitted: list[Omitted]) -> None:
        """Start with no argument and the omissions the field already knows."""
        self._entries: dict[str, Kwarg] = {}
        self.omitted = omitted
        self.factory: Kwarg | None = None

    def __contains__(self, name: str) -> bool:
        """Return whether the call has an argument ``name``."""
        return name in self._entries

    def get(self, name: str) -> Any:
        """Return the value of the argument ``name``, or None."""
        return None if (entry := self._entries.get(name)) is None else entry.value

    def entry(self, name: str) -> Kwarg | None:
        """Return the argument ``name``, or None."""
        return self._entries.get(name)

    def names(self) -> tuple[str, ...]:
        """Return the names of the arguments in the order they were added."""
        return tuple(self._entries)

    def put(self, name: str, value: Any, why: Why, items: tuple[str, ...] = ()) -> None:
        """Set the argument ``name``; one it replaces leaves its items as ``superseded``."""
        if (replaced := self._entries.get(name)) is not None:
            self.omit_entry(replaced, "superseded", why)
        self._entries[name] = Kwarg(name, value, why, items)

    def update(self, name: str, value: Any, why: Why, items: tuple[str, ...] = ()) -> None:
        """Give the argument ``name`` a new value in its place, keeping its items and adding ``items``."""
        entry = self._entries[name]
        self._entries[name] = Kwarg(
            name,
            value,
            Why(why.rule, why.keyword, why.option, (entry.why.rule,)),
            (
                *entry.items,
                *items,
            ),
        )

    def take(self, name: str) -> Kwarg | None:
        """Remove the argument ``name`` and return it; its items stay to be written or omitted by the caller."""
        return self._entries.pop(name, None)

    def pop(self, name: str, reason: OmitReason, why: Why) -> Any:
        """Remove the argument ``name``, its items omitted for ``reason``, and return its value."""
        if (entry := self._entries.pop(name, None)) is None:
            return None
        self.omit_entry(entry, reason, why)
        return entry.value

    def omit(self, item: str, value: Any, reason: OmitReason, why: Why) -> None:
        """Record a declared item the call does not write."""
        self.omitted.append(Omitted(item, value, reason, why))

    def omit_entry(self, entry: Kwarg, reason: OmitReason, why: Why) -> None:
        """Record the items of an argument the call does not write."""
        for item in entry.items:
            self.omit(item, entry.value, reason, why)

    def writes(self, item: str) -> bool:
        """Return whether an argument writes the declared item ``item``."""
        return any(item in entry.items for entry in self._entries.values())

    def data(self) -> dict[str, Any]:
        """Return the arguments as a mapping of names to values, in order."""
        return {name: entry.value for name, entry in self._entries.items()}

    def kwargs(self) -> tuple[Kwarg, ...]:
        """Return the arguments in order."""
        return tuple(self._entries.values())


PositionalKind: TypeAlias = Literal["required", "default", "none"]
"""The positional argument of a ``Field()`` call: ``...``, the field's default, or ``None``."""


class Positional(NamedTuple):
    """The positional argument of a call, written before its keyword arguments."""

    kind: PositionalKind
    why: Why


CallKind: TypeAlias = Literal["Field", "field", "Meta"]
"""The callee: Pydantic's ``Field()``, the ``field()`` of a stdlib dataclass or msgspec, or msgspec ``Meta()``."""


@final
@record
class CallPlan:
    """A call: its positional argument, its default factory and its other keyword arguments.

    The arguments are written in that order; ``Field()`` and ``Meta()`` sort their keyword arguments by their text,
    ``field()`` keeps their order and its default factory among them. ``default_keyword`` writes the positional
    argument as ``default=`` where the call follows the annotation (``--use-default-kwarg``); inside ``Annotated`` it
    stays positional.
    """

    positional: Positional | None
    factory: Kwarg | None
    kwargs: tuple[Kwarg, ...]
    default_keyword: bool
    why: Why
    callee: CallKind = "Field"

    def written_items(self) -> Iterator[str]:
        """Yield the declared items the call's arguments write."""
        arguments = (*self.kwargs, self.factory) if self.factory is not None else self.kwargs
        for argument in arguments:
            yield from argument.items


@final
@record
class FieldDefault:
    """The default a field writes: its value, or Pydantic's ``MISSING`` sentinel (``--use-missing-sentinel``).

    ``items`` are the declared items a default written alone (``FieldRenderPlan.bare_default``) writes.
    """

    value: object
    missing_sentinel: bool
    why: Why
    items: tuple[str, ...] = ()


AnnotationForm: TypeAlias = Literal["plain", "annotated", "classvar"]
"""How the annotation is written: the type, ``Annotated[type, Field(...)]`` or ``ClassVar[type]``."""


@final
@record
class FieldRenderPlan:
    """What the class body writes for one field, with the reason of every item; text is written when it is read.

    ``has_default_factory_in_field`` is the template condition of the same name: the call writes the default factory
    that the field's own facts give it (a Pydantic dataclass's factory for a mutable default does not count).
    ``symbol_imports`` are the imports the call needs; the type hint's imports come from its data type.
    A Pydantic dataclass field under ``--use-annotated`` whose call the dataclass must see has a ``plain`` annotation
    and writes ``call`` after it, its default moved into the call where the dataclass needs it.

    A msgspec field writes ``annotated_call`` (``Meta(...)``) inside ``Annotated`` and ``call`` (``field(...)``) after
    the annotation; a msgspec or stdlib dataclass field with ``bare_default`` writes its default alone after the
    annotation. ``written_type`` is the type a msgspec annotation writes when it writes a copy of the field's type: its
    ``Annotated`` type, or its union with ``UnsetType``; the type writes its text when it is read. It is a ``DataType``,
    typed ``object`` since importing ``types`` here would close an import cycle. ``qualifiers`` wrap the type hint,
    innermost first (``TypedDict``'s ``ReadOnly[``, ``NotRequired[``).
    """

    annotation_form: AnnotationForm
    call: CallPlan | None
    default: FieldDefault
    has_default_factory_in_field: bool
    omitted: tuple[Omitted, ...]
    symbol_imports: tuple[tuple[Import, Why], ...]
    why: Why
    annotated_call: CallPlan | None = None
    bare_default: bool = False
    qualifiers: tuple[tuple[str, Why], ...] = ()
    written_type: object = None

    def written_items(self) -> frozenset[str]:
        """Return the declared items the calls, or the default written alone, write."""
        items = {item for call in (self.call, self.annotated_call) if call is not None for item in call.written_items()}
        if self.bare_default:
            items.update(self.default.items)
        return frozenset(items)

    def unaccounted(self, declared: frozenset[str]) -> tuple[frozenset[str], frozenset[str]]:
        """Return the declared items neither written nor omitted, and the written or omitted items not declared."""
        accounted = self.written_items() | {omitted.item for omitted in self.omitted}
        return declared - accounted, accounted - declared


RenderInputs: TypeAlias = tuple[Any, tuple[object, ...] | None, tuple[object, ...], tuple["RenderInputs", ...]]
"""What a render plan read from a field when it was built: the mapping or the collection it read (the field's
attributes, a dict, a list), its keys then (None for a collection), its values then, and the inputs of the values that
hold more (a field's types and constraints, containers), which ``unchanged_render_inputs`` compares with them now."""


_EQUAL_BY_VALUE: Final = frozenset({str, bool, int, float, bytes, type(None)})
"""The immutable types whose values are the same when they are equal and of the same type."""


def unchanged_render_inputs(inputs: RenderInputs) -> bool:
    """Return whether what a plan read holds the same keys and values now, and the same inside values that hold more.

    A value is the same object, or an equal immutable value of the same type (``_same_value``), such as a cache that
    was computed again.
    """
    live, keys, values, nested = inputs
    if len(live) != len(values):
        return False
    if keys is None:
        same = all(map(is_, live, values)) or all(map(_same_value, live, values))
    else:
        current = live.values()
        same = all(map(eq, live, keys)) and (all(map(is_, current, values)) or all(map(_same_value, current, values)))
    return same and all(map(unchanged_render_inputs, nested))


def _same_value(new: Any, old: Any) -> bool:
    """Return whether ``new`` is ``old``, or equals it as an immutable value of the same type."""
    return new is old or (type(new) is type(old) and type(new) in _EQUAL_BY_VALUE and new == old)


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

    def _render_inputs(self) -> RenderInputs:
        """Return what the field's plan reads from the field, as ``RenderInputs``."""


@final
@record
class NestedFactoryMetadata:
    """What decides the default factory of an optional nested model, final when render preparation ends.

    ``order`` gives each model (by ``id``) its module and its position there; ``recursive`` gives the models of each
    reference cycle the models of that cycle, whose nested factories would build each other forever.
    """

    order: Mapping[int, tuple[int, int]]
    recursive: Mapping[ModelKey, frozenset[ModelKey]]


class _FrozenPlan(NamedTuple):
    """A field's frozen plan, the plan the import snapshot reads if different, and what the plan read from the field.

    ``inputs`` are the field's revisions (`ParseMemo.revision`) when the plan was frozen, or, for a class outside this
    package or under custom templates, its ``_render_inputs``. ``type_expr`` is the expression of the field's type
    hint then (``types.py:type_expr_of``), kept under ``DATAMODEL_CODE_GENERATOR_CHECK_RENDER_PLANS``.
    """

    field: object
    plan: FieldRenderPlan
    import_plan: FieldRenderPlan | None
    inputs: _FieldRevision | RenderInputs
    type_expr: object = None


@final
class RenderReady:
    """The field render plans one parse froze at B2, and the nested-factory decision render preparation made.

    A plan is looked up by its field object. ``import_view`` returns, inside it, the second plan of a field that row
    ``legacy.L-NF`` keeps. Each plan keeps its field's revisions or the contents of what it read from its field, and
    ``refreshed`` rebuilds it when the field's model renders with them changed, as a model's ``render`` that changes its
    fields first does.
    """

    __slots__ = ("_deciding", "_import_view", "_plans", "_rebuilding", "memo", "nested_factories")

    def __init__(self, parser_class: type | None = None, memo: ParseMemo | None = None) -> None:
        """Start a parse with no plan frozen.

        ``memo``, the parse's memo, keeps the derived values of its fields only when ``parser_class`` is one of this
        package's parsers: a parser extension may write fields outside the store at any point.
        """
        self._plans: dict[int, _FrozenPlan] = {}
        self._import_view = False
        self._deciding = False
        self._rebuilding = False
        self.memo = memo if _package_class(parser_class) else None
        self.nested_factories: NestedFactoryMetadata | None = None

    def freeze(
        self,
        field: PlannedField,
        plan: FieldRenderPlan,
        import_plan: FieldRenderPlan | None = None,
        type_expr: object = None,
    ) -> None:
        """Keep the field's plan, the plan the import snapshot reads if different, and the expression of its hint."""
        self._plans[id(field)] = _FrozenPlan(field, plan, import_plan, self._frozen_inputs(field), type_expr)

    def plan_of(self, field: object) -> FieldRenderPlan | None:
        """Return the plan frozen for ``field`` in the current view, or None when the parse froze none for it.

        An entry holds its field, so no other object takes the field's ``id`` while the parse runs.
        """
        if self._rebuilding or (entry := self._plans.get(id(field))) is None:
            return None
        if self._import_view and entry.import_plan is not None:
            return entry.import_plan
        return entry.plan

    def frozen(self) -> Iterator[tuple[object, FieldRenderPlan, object]]:
        """Yield every field with the plan frozen for it and the expression of its type hint, if kept."""
        for entry in self._plans.values():
            yield entry.field, entry.plan, entry.type_expr

    def refreshed(self, field: PlannedField) -> FieldRenderPlan | None:
        """Rebuild the plan frozen for ``field`` if what it read from the field changed since; return the rebuilt plan.

        The plan is rebuilt from the field's facts as the frozen plans' check rebuilds it, and kept with what it read
        now; the import snapshot's plan stays. Return None when the parse froze no plan for the field or its revisions,
        or the contents it read, are the same.
        """
        if (entry := self._plans.get(id(field))) is None or self._unchanged(field, entry.inputs):
            return None
        with self.rebuilding():
            plan = cast("FieldRenderPlan", field._build_frozen_render_plan())  # noqa: SLF001
        self._plans[id(field)] = entry._replace(plan=plan, inputs=self._frozen_inputs(field))
        return plan

    def derived_from(self, field: Any) -> ParseMemo | None:
        """Return the memo that keeps the derived values of ``field`` now (`memo`, `_derives`), or None.

        A plan rebuilt to compare with a frozen one is built anew.
        """
        return self.memo if self.memo is not None and not self._rebuilding and _derives(field) else None

    def _frozen_inputs(self, field: Any) -> _FieldRevision | RenderInputs:
        """Return what a frozen plan compares when its model renders: the field's revisions when the memo keeps them.

        A field, model or parser of a class outside this package, or a model under custom templates, which see the raw
        fields, keeps the contents of what the plan read (``_render_inputs``).
        """
        if (memo := self.derived_from(field)) is None or (
            (parent := field.parent) is not None and parent._custom_template_dir is not None  # noqa: SLF001
        ):
            return field._render_inputs()  # noqa: SLF001
        return _FieldRevision(*memo.revision(field))

    def _unchanged(self, field: Any, inputs: _FieldRevision | RenderInputs) -> bool:
        """Return whether what a frozen plan read from its field holds: its revisions, or its contents."""
        if type(inputs) is _FieldRevision:
            return self.memo is not None and self.memo.revision(field) == inputs
        return unchanged_render_inputs(cast("RenderInputs", inputs))

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


_PACKAGE_CLASSES: Final[dict[type | None, bool]] = {None: True, type(None): True}


def _package_class(cls: type | None) -> bool:
    """Return whether ``cls`` is a class of this package, the class of None, or None."""
    if (known := _PACKAGE_CLASSES.get(cls)) is None:
        known = _PACKAGE_CLASSES[cls] = cast("type", cls).__module__.startswith("datamodel_code_generator.")
    return known


def written_text(field: Any, *, writes: bool) -> bool:
    """Return whether the field's call renders (``bool(field.field)``), ``writes`` for a field class of this package.

    A field class outside the package may write anything through ``field`` or ``__str__``, which ordering and the
    class attribute then read, as they did in 0.86.0.
    """
    return writes if _package_class(type(field)) else bool(field.field)


_DERIVING_CLASSES: Final[dict[tuple[type, type], bool]] = {}


def _derives(field: Any) -> bool:
    """Return whether the derived values of ``field`` may be kept: its class and its model's are this package's.

    A class outside the package may compute them from anything, so no revision covers what they read. The answer is
    kept for each pair of classes.
    """
    classes = type(field), type(field.parent)
    if (known := _DERIVING_CLASSES.get(classes)) is None:
        known = _DERIVING_CLASSES[classes] = _package_class(classes[0]) and _package_class(classes[1])
    return known


class _FieldRevision(NamedTuple):
    """The revisions of a field and of its model's own attributes when its plan was frozen (`ParseMemo.revision`)."""

    field: int
    model: int


def _field_argument_text(argument: Kwarg) -> str:
    """Write one ``field()`` argument: the default factory as code, any other value as its literal."""
    value = argument.value if argument.name == "default_factory" else represent_python_value(argument.value)
    return f"{argument.name}={value}"


def field_call_text(call: CallPlan) -> str:
    """Write a ``field(...)`` call of a stdlib dataclass or msgspec: its arguments in order, the factory as code."""
    return f"field({', '.join(_field_argument_text(argument) for argument in call.kwargs)})"


def written_argument_names(plan: FieldRenderPlan) -> tuple[str, ...]:
    """Return the names of the ``field()`` arguments ``plan`` writes, ``default`` for a default written alone."""
    if plan.bare_default:
        return ("default",)
    return () if plan.call is None else tuple(argument.name for argument in plan.call.kwargs)
