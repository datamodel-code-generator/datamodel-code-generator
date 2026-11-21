"""``RENDER_PLANS``: the field render plans frozen at B2 account for their fields and hold when modules are written.

Every plan frozen at B2, and every plan rebuilt when its model renders, writes or omits each item its field declares,
and writes or omits no other. When the modules are written, each frozen plan renders as the plan the field's facts give
then, and the type expression of the field's type hint, kept with the plan, renders as the type hint then.
"""

from __future__ import annotations

from functools import wraps
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from tests.checks.areas import Area, Mode, mode

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.model._render_plan import FieldRenderPlan


@runtime_checkable
class _CheckedPlannedField(Protocol):
    """A field that renders from a render plan: the members ``PlannedField`` names that the check calls."""

    def _build_frozen_render_plan(self) -> FieldRenderPlan | None:
        """Build the plan the parse freezes for the field."""

    def _render_plan_text(self, plan: FieldRenderPlan) -> tuple[object, ...]:
        """Return what the field writes from ``plan``."""


def _violation(plans_mode: Mode, message: str) -> None:

    plans_mode.violation(message)


def _check_accounting(plans_mode: Mode, model: Any, field: Any, plan: Any) -> None:
    """Report a frozen or rebuilt plan that drops a declared item without a reason or writes an undeclared one."""
    declared = field._declared_render_items()
    accounted = plan.written_items() | {omitted.item for omitted in plan.omitted}
    if (missing := declared - accounted) | (extra := accounted - declared):
        _violation(
            plans_mode,
            f"the render plan of {model.class_name}.{field.name} leaves {sorted(missing)} unaccounted and accounts for "
            f"undeclared {sorted(extra)}",
        )


def freeze_field_render_plans(original: Callable[[Any], None]) -> Callable[[Any], None]:
    """Wrap ``render_prep.freeze_field_render_plans``: each plan it froze must account for its field."""

    @wraps(original)
    def freeze_field_render_plans(state: Any) -> None:
        original(state)
        if (plans_mode := mode(Area.RENDER_PLANS)) is None:
            return
        from datamodel_code_generator.model._render_plan import RENDER_READY

        ready = RENDER_READY.get()
        assert ready is not None, "a parse that freezes plans has installed its RenderReady"
        plans = ready._plans
        for model in state.all_models:
            if not model._FREEZES_FIELD_RENDER_PLANS:
                continue
            for field in model.fields:
                if (entry := plans.get(id(field))) is not None:
                    _check_accounting(plans_mode, model, field, entry.plan)

    return freeze_field_render_plans


def refresh_field_render_plans(original: Callable[[Any], None]) -> Callable[[Any], None]:
    """Wrap ``DataModel._refresh_field_render_plans``: each plan it rebuilt must account for its field."""

    @wraps(original)
    def refresh_field_render_plans(self: Any) -> None:
        from datamodel_code_generator.model._render_plan import RENDER_READY

        if (plans_mode := mode(Area.RENDER_PLANS)) is None or (ready := RENDER_READY.get()) is None:
            return original(self)
        plans = ready._plans
        before = {id(field): entry.plan for field in self.fields if (entry := plans.get(id(field))) is not None}
        original(self)
        for field in self.fields:
            if (entry := plans.get(id(field))) is not None and entry.plan is not before.pop(id(field), entry.plan):
                _check_accounting(plans_mode, self, field, entry.plan)
        return None

    return refresh_field_render_plans


def check_frozen_render_plans() -> None:
    """Report a frozen render plan, or the type expression kept with it, that renders other text than now.

    The plan is compared with its field's plan rebuilt now, the type expression with the field's type hint now,
    both rendered with a memo of their own, so the check leaves the parse's memo as it was.
    """
    if (plans_mode := mode(Area.RENDER_PLANS)) is None:
        return
    from datamodel_code_generator._parse_memo import rendering_memo
    from datamodel_code_generator.model._render_plan import RENDER_READY
    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.types import TypeExpr, render_type_expr

    ready = RENDER_READY.get()
    assert ready is not None, "the checks run inside a parse, which has installed its RenderReady"
    for entry in ready._plans.values():
        field = entry.field
        assert isinstance(field, DataModelFieldBase), "the plans are frozen for the fields of the parse's models"
        assert isinstance(entry.hint, TypeExpr), "the hint is the field's type expression, frozen with its plan"
        with rendering_memo():
            hint = field.data_type.type_hint
            frozen_hint = render_type_expr(entry.hint)
        if frozen_hint != hint:
            _violation(
                plans_mode, f"the type expression frozen for field {field.name} renders {frozen_hint}, now {hint}"
            )
        assert isinstance(field, _CheckedPlannedField), "every field the parse freezes a plan for renders from it"
        with ready.rebuilding():
            rebuilt_plan = field._build_frozen_render_plan()
            assert rebuilt_plan is not None, "the field froze a plan, so its facts still give one"
            rebuilt_text = field._render_plan_text(rebuilt_plan)
        if (frozen_text := field._render_plan_text(entry.plan)) != rebuilt_text:
            _violation(
                plans_mode,
                f"the render plan frozen for field {field.name} renders {frozen_text}, rebuilt {rebuilt_text}",
            )
