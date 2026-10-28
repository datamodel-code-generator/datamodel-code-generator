"""Field assignments: what the class body writes after a field's annotation and what that gives its constructor.

Every input is an immutable fact of ``AssignmentInputs``, so ``classify_assignment`` reads nothing else. The output
model names its row (``AssignmentRow``) and the field's backend reports what it writes (``WrittenAssignment``); the
adapter ``model/base.py:_assignment_inputs`` reads both from the live objects, and
``model/base.py:DataModel._field_assignment_facts`` is the one entry for the passes that read the result: the L1
construction key (``DataModel.FIELD_CONSTRUCTION_ORDER_KEY``), in ``parser/_passes/render_prep.py``
``fix_constructor_field_ordering`` and ``fix_tag_opt_out_field_ordering``, the hidden-type checks and the signature
warning. The public hooks
``FIELD_ASSIGNMENT_CHECKER``, ``FIELD_DEFAULT_CLASSIFIER``, ``FIELD_PARTICIPATES_IN_CONSTRUCTOR``,
``FIELD_CONSTRUCTION_ORDER_KEY`` and ``DataModel.field_binds_class_attribute`` of the built-in outputs are views of
the same rows.

The facts are classified when a pass reads them, as the written assignment can change between passes (forced
assignments, keyword-only fixes, type overrides). The written assignment is read as the field renders it at that
point, nested-model default factories included, before L6 records the recursive paths that drop them
(``_set_nested_model_default_factory_metadata``): a nested model whose required constructor fields are being looked
up already leads back to the field's model and gives no factory there (``model/base.py:_nested_model_factory``), and
the import snapshot taken at the same point keeps ``field`` where msgspec then writes ``UNSET`` (``NameError``).
Moving that metadata before the reads fixes that (row ``legacy-nested-factory-at-read``, kept in the adapter).

``dataclass_field_items`` decides which ``field()`` arguments a stdlib dataclass field writes: its render plan fills
them with values, and the L1 construction key of ``model/dataclass.py`` reads their names without building the plan,
which the fields' binding to their model would invalidate at once.

The rows named ``legacy-*`` (``AssignmentRule``) keep today's output where a row disagrees with what the template
writes; the fix of each deletes its row.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, NamedTuple, TypeAlias, final

from datamodel_code_generator._records import record
from datamodel_code_generator.model._render_plan import AssignmentFacts

if TYPE_CHECKING:
    from collections.abc import Collection

    from datamodel_code_generator.model._render_plan import AssignmentRule, ConstructorDefault

__all__ = [
    "AssignmentInputs",
    "AssignmentRow",
    "DataclassItem",
    "DataclassItemInputs",
    "DataclassItems",
    "DataclassVisibility",
    "DataclassWritten",
    "DefaultState",
    "ModelWritten",
    "MsgspecWritten",
    "PydanticDataclassWritten",
    "WrittenAssignment",
    "WrittenData",
    "classify_assignment",
    "dataclass_field_items",
    "participates_in_constructor",
    "struct_assigned",
    "struct_construction_assigned",
    "written_data",
]

AssignmentRow: TypeAlias = Literal["model", "dataclass", "pydantic-dataclass", "msgspec"]
"""The output model whose template writes the class body.

* ``model``: Pydantic models, TypedDict and the neutral model: the field's call outside ``Annotated``, or its default
  unless it is required or strips its ``None``; every field takes part in the constructor.
* ``dataclass``: stdlib dataclasses: the ``model`` rule (row ``legacy-stdlib-annotated``); an ``init=False`` field
  takes no part in the constructor.
* ``pydantic-dataclass``: Pydantic dataclasses: the template keeps a stripped ``None`` default when the rendered type
  admits None, and a ``ClassVar`` takes no part in the constructor. Such a field still takes its own place in the
  constructor as one without a default, unless ``--order-constructor-fields-by written-defaults`` is given
  (``AssignmentFacts.place_assigned``).
* ``msgspec``: msgspec Structs: a default for every optional field, as a Struct never strips its defaults. The
  construction key still counts a field whose ``None`` default ``--strip-default-none`` strips as having none, so it
  keeps its schema place among the required fields, unless ``--order-constructor-fields-by written-defaults`` is given
  (``AssignmentInputs.keeps_stripped_place``), and takes its own place in the constructor as one without a default
  (``AssignmentFacts.place_assigned``).
"""

DefaultState: TypeAlias = Literal["undefined", "none", "value"]
"""The field's default: none given, ``None``, or another value."""


@final
@record
class WrittenData:
    """The ``field()`` arguments a stdlib dataclass or msgspec field writes: any, a default, a default factory."""

    any: bool
    default: bool
    factory: bool


@final
@record
class ModelWritten:
    """The written assignment of a neutral or Pydantic model field.

    ``text`` tells that the field's call renders (``bool(field.field)``), ``declared_factory`` that the field declares
    a ``default_factory``.
    """

    text: bool
    declared_factory: bool
    default: DefaultState


@final
@record
class DataclassWritten:
    """The written assignment of a stdlib dataclass field: its ``field()`` arguments and a forced ``field()``.

    ``text`` tells that the field's call renders (``bool(field.field)``, ``_render_plan.written_text``); the arguments
    and the forced call give its constructor default.
    """

    data: WrittenData
    forced: bool
    text: bool


@final
@record
class PydanticDataclassWritten:
    """The written assignment of a Pydantic dataclass field: its rendered call and the default factory it writes."""

    text: bool
    factory: bool


@final
@record
class MsgspecWritten:
    """The written assignment of a msgspec Struct field: its ``field()`` arguments and whether its call renders.

    ``text`` tells that the field's call renders (``bool(field.field)``, ``_render_plan.written_text``).
    """

    data: WrittenData
    text: bool


WrittenAssignment: TypeAlias = ModelWritten | DataclassWritten | PydanticDataclassWritten | MsgspecWritten


@final
@record
class DataclassVisibility:
    """What the Pydantic dataclass template needs to see as an assignment.

    ``metadata_assignment`` tells that a field under ``--use-annotated`` has metadata the dataclass must see,
    ``factory_in_field`` that its ``Field()`` writes a default factory and ``admits_none`` that its rendered type
    admits None.
    """

    metadata_assignment: bool
    factory_in_field: bool
    admits_none: bool


@final
@record
class AssignmentInputs:
    """The facts ``classify_assignment`` reads for one field.

    ``required`` tells that the field is required without ``--use-default-with-required``, ``strips_none`` that
    ``--strip-default-none`` strips its ``None`` default, ``annotated`` that it is written under ``--use-annotated``,
    ``init_excluded`` that it declares ``init=False``. ``visibility`` is given for the ``pydantic-dataclass`` row and
    for a ``PydanticDataclassWritten`` assignment, which read it. ``keeps_stripped_place`` tells, for the ``msgspec``
    and ``pydantic-dataclass`` rows, that the field takes its place as one without a default where the class body
    assigns only its default, the construction key of a Struct too: ``--strip-default-none`` strips its ``None``
    default, and neither ``--order-constructor-fields-by written-defaults`` nor ``--use-default-with-required`` is
    given.
    """

    row: AssignmentRow
    written: WrittenAssignment
    required: bool
    strips_none: bool
    annotated: bool
    class_var: bool = False
    init_excluded: bool = False
    visibility: DataclassVisibility | None = None
    keeps_stripped_place: bool = False


DataclassItem: TypeAlias = Literal[
    "extra", "nested-model-factory", "kept-none-default", "default", "value-factory", "unbound-factory"
]
"""What gives a stdlib dataclass ``field()`` argument its value.

* ``extra``: the field's extra of that name.
* ``nested-model-factory``: the factory of the nested model the optional field holds.
* ``kept-none-default``: the ``None`` default a call with other arguments keeps.
* ``default``: the field's default.
* ``value-factory``: a factory building the field's list, dict or set default.
* ``unbound-factory``: a factory returning the field's default, as ``dataclasses`` unbinds the field.
"""


class DataclassItemInputs(NamedTuple):
    """The facts ``dataclass_field_items`` reads for one stdlib dataclass field.

    ``extra_keys`` are the ``field()`` arguments its extras declare, in their order, ``init_excluded`` tells that
    they give ``init=False``, ``nested_model_factory`` that it writes its nested model's factory. ``default`` is its
    default and ``container_default`` tells that the default is a list, a dict or a set. ``required`` tells that it
    is required without ``--use-default-with-required``, ``strip_default_none`` that ``--strip-default-none`` is
    given and ``unbound`` that ``dataclasses`` unbinds the field.
    """

    extra_keys: tuple[str, ...]
    init_excluded: bool
    nested_model_factory: bool
    default: DefaultState
    container_default: bool
    strip_default_none: bool
    required: bool
    unbound: bool


class DataclassItems(NamedTuple):
    """The ``field()`` arguments a stdlib dataclass field writes, in order, and the extras a required field drops.

    ``written`` pairs each argument's name with what gives its value; ``dropped`` names the declared extras the
    call leaves out, as a required field takes no default factory.
    """

    written: tuple[tuple[str, DataclassItem], ...]
    dropped: tuple[str, ...]

    def names(self) -> tuple[str, ...]:
        """Return the names of the arguments, in order."""
        return tuple(name for name, _ in self.written)


def dataclass_field_items(inputs: DataclassItemInputs) -> DataclassItems:
    """Return which ``field()`` arguments a stdlib dataclass field writes, the one decision its plan and key read.

    A call with other arguments keeps a ``None`` default unless ``--strip-default-none`` strips it, or the field is
    left out of ``__init__``. A required field writes no default and no default factory. A list, dict or set
    default is built by a factory, and so is any default of a field that ``dataclasses`` unbinds, or the ``None``
    of one that writes nothing else.
    """
    written: list[tuple[str, DataclassItem]] = [(key, "extra") for key in inputs.extra_keys]
    if inputs.nested_model_factory:
        written.append(("default_factory", "nested-model-factory"))
    default: DataclassItem | None = None
    if not (inputs.nested_model_factory or "default_factory" in inputs.extra_keys):
        if inputs.default == "none":
            if written and (not inputs.strip_default_none or inputs.init_excluded):
                default = "kept-none-default"
        elif inputs.default == "value":
            default = "default"
    dropped: tuple[str, ...] = ()
    if inputs.required:
        default = None
        dropped = tuple(name for name, item in written if name == "default_factory" and item == "extra")
        written = [(name, item) for name, item in written if name != "default_factory"]
    strips_none = inputs.strip_default_none and inputs.default == "none"
    if default == "default" and inputs.container_default:
        written.append(("default_factory", "value-factory"))
    elif inputs.unbound and (default is not None or not (written or inputs.required or strips_none)):
        written.append(("default_factory", "unbound-factory"))
    elif default is not None:
        written.append(("default", default))
    return DataclassItems(tuple(written), dropped)


def written_data(keys: Collection[str]) -> WrittenData:
    """Return what classification reads of the ``field()`` arguments a field writes, from their names."""
    return WrittenData(any=bool(keys), default="default" in keys, factory="default_factory" in keys)


def participates_in_constructor(row: AssignmentRow, *, init_excluded: bool, class_var: bool) -> bool:
    """Return whether a field takes part in its model's constructor."""
    if row == "dataclass":
        return not init_excluded
    return not class_var if row == "pydantic-dataclass" else True


def struct_assigned(*, required: bool) -> bool:
    """Return whether a msgspec Struct writes an assignment for a field: a default for every optional one.

    The row reads requiredness alone, so the Struct's hooks answer without rendering the field.
    """
    return not required


def struct_construction_assigned(*, required: bool, keeps_stripped_place: bool) -> bool:
    """Return whether a msgspec Struct's construction key counts a field as having a default.

    A field that keeps its stripped place counts as having none, the order the Struct had before it ordered fields by
    the defaults it writes; a required field after it then makes the constructor ordering move the fields.
    """
    return struct_assigned(required=required) and not keeps_stripped_place


def _model_assignment(inputs: AssignmentInputs, *, text: bool) -> tuple[bool, AssignmentRule]:
    if text and not inputs.annotated:
        return True, "field-call"
    if not (inputs.required or inputs.strips_none):
        return True, "default"
    return False, "unassigned"


def _pydantic_dataclass_assignment(
    inputs: AssignmentInputs, visibility: DataclassVisibility, *, text: bool
) -> tuple[bool, AssignmentRule]:
    if visibility.metadata_assignment:
        return text, "dataclass-visible-metadata"
    if text and not inputs.annotated:
        return True, "field-call"
    if (
        not inputs.required
        and not visibility.factory_in_field
        and not (inputs.strips_none and not visibility.admits_none)
    ):
        return True, "default"
    return False, "unassigned"


def _visibility(inputs: AssignmentInputs) -> DataclassVisibility:
    visibility = inputs.visibility
    assert visibility is not None, "the pydantic-dataclass row and assignment read DataclassVisibility"
    return visibility


def _constructor_default(inputs: AssignmentInputs, *, text: bool, model_assigned: bool) -> ConstructorDefault:
    """Return the constructor default the field's written assignment gives it; a required field has none.

    A stdlib dataclass field's comes from the ``field()`` arguments it writes, whatever its call renders.
    """
    if inputs.required:
        return "none"
    written = inputs.written
    if isinstance(written, ModelWritten):
        return _declared_default(written, strips_none=inputs.strips_none) if model_assigned else "none"
    if isinstance(written, DataclassWritten):
        data = written.data
        writes = (data.any or not written.forced) and (
            ((data.any or written.forced) and not inputs.annotated) or not inputs.strips_none
        )
        return _data_default(data) if writes else "none"
    if isinstance(written, PydanticDataclassWritten):
        writes = not inputs.class_var and _pydantic_dataclass_assignment(inputs, _visibility(inputs), text=text)[0]
        return ("factory" if written.factory else "value") if writes else "none"
    return _data_default(written.data)


def _declared_default(written: ModelWritten, *, strips_none: bool) -> ConstructorDefault:
    if written.declared_factory:
        return "factory"
    return "none" if written.default == "undefined" or (written.default == "none" and strips_none) else "value"


def _data_default(data: WrittenData) -> ConstructorDefault:
    if data.factory:
        return "factory"
    return "none" if data.any and not data.default else "value"


def classify_assignment(inputs: AssignmentInputs) -> AssignmentFacts:
    """Return what the class body writes for a field and what the field is in its model's constructor.

    The row decides the assignment, the class attribute and the constructor's participants; the field's written
    assignment decides its constructor default.
    """
    text = inputs.written.text
    model_assigned, model_rule = _model_assignment(inputs, text=text)
    row = inputs.row
    if row == "pydantic-dataclass":
        visibility = _visibility(inputs)
        assigned, rule = _pydantic_dataclass_assignment(inputs, visibility, text=text)
        construction_assigned = text if visibility.metadata_assignment else model_assigned
        binds = assigned
    else:
        if row == "msgspec":
            assigned = struct_assigned(required=inputs.required)
            rule: AssignmentRule = "default" if assigned else "unassigned"
            construction_assigned = struct_construction_assigned(
                required=inputs.required, keeps_stripped_place=inputs.keeps_stripped_place
            )
        else:
            assigned, rule = model_assigned, model_rule
            if row == "dataclass" and text and not assigned:
                rule = "legacy-stdlib-annotated"
            construction_assigned = assigned
        binds = (text or assigned) if row in {"msgspec", "dataclass"} else assigned
    return AssignmentFacts(
        assigned=assigned,
        construction_assigned=construction_assigned,
        binds_class_attribute=binds,
        constructor_default=_constructor_default(inputs, text=text, model_assigned=model_assigned),
        participates_in_constructor=participates_in_constructor(
            row, init_excluded=inputs.init_excluded, class_var=inputs.class_var
        ),
        rule=rule,
        place_assigned=assigned and not (inputs.keeps_stripped_place and rule == "default"),
    )
