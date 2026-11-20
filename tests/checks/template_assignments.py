"""``TEMPLATE_ASSIGNMENTS``: the field lines a built-in template writes have ``= value`` where Python decides they do.

A built-in model template decides in its own conditions whether a field's line binds the field's name at class scope
(``name: hint = value``, ``name = value``) or only annotates it (``name: hint``), which ``classify_assignment``
(``model/_decisions/assignment.py``) also decides for constructor ordering, keyword-only fixes and the names a class
body binds. Every model whose root template is a built-in one is checked when it renders after B2 (``render_ready``
of ``parse.py``), where its facts and its fields' render plans are final, by the compiled renderer or by Jinja: every
class-scope line that writes a field of the model, annotated or assigned, binds the name exactly where the model's
assignment facts say that the class body binds the field (``binds_class_attribute``: the field's call, or
``assigned``). The renders before B2 (deduplication keys, for one) read facts that are not final yet and are not
checked. A field the rendered text writes no line for (a ``TypedDict`` of the functional form, a tag ``ClassVar``) is
not compared. A model whose class brings its own assignment hooks (rule ``hook``) is decided by them, not by
``classify_assignment``, and a rendered text that the running Python cannot parse, such as identifiers of a newer
target or a string annotation holding a lone surrogate, is not checked; the lines are read through
``scripts/_source_model.py``. The check reads the facts with ``RENDER_MEMOS`` off, which would otherwise compute anew
each memo value it reads, as the parse never does there.

The shapes of ``LEGACY_ROWS`` are known to differ: their fields are recorded with a report file and never raise.
"""

from __future__ import annotations

import ast
from functools import wraps
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Protocol

from tests.checks.areas import MODES, Area, Mode

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from scripts._source_model import Binding


LEGACY_ROWS: Final[Mapping[str, str]] = MappingProxyType({
    "legacy-annotated-factory-binds": (
        "a Pydantic model field under --use-annotated whose Field() in Annotated writes a default factory: the "
        "template writes no value, while the model row counts its default as an assignment (rule default)"
    ),
    "legacy-typed-dict-binds": (
        "a TypedDict field with a default: the class form writes an annotation only, while the model row counts "
        "its default as an assignment (rule default)"
    ),
    "legacy-enum-member-unbound": (
        "an enum member: the template writes the member's value, while the model row decides that the class body "
        "binds nothing (rule unassigned)"
    ),
})
"""The shapes whose line differs from the assignment facts today, each with what differs."""


class AssignmentFacts(Protocol):
    """The assignment facts of a field that the check reads (``AssignmentFacts`` of ``model/_render_plan.py``)."""

    @property
    def assigned(self) -> bool:
        """Whether an assignment follows the annotation, as constructor ordering counts it."""

    @property
    def binds_class_attribute(self) -> bool:
        """Whether the class body binds the field's name."""

    @property
    def rule(self) -> str:
        """The rule of the row that classified the field."""


def field_lines(rendered: str) -> list[Binding] | None:
    """Return the field lines of the module-level classes of the rendered text, in order, or None if it does not parse.

    A field line annotates one name (``name: hint``) or binds it to a value, with an annotation or without one
    (``name: hint = value``, ``name = value``); the value of a binding is None exactly for an annotation alone. Only the
    direct body of a class at module level holds field lines: a nested class (``class Config:`` in a model body, a
    class in a method) binds its own names, which are not the model's fields.
    """
    from scripts._source_model import SourceModule

    try:
        source = SourceModule(rendered)
    except (SyntaxError, UnicodeEncodeError):
        return None
    return [
        binding
        for binding in source.bindings()
        if binding.scope.kind == "class"
        and binding.scope.parent is not None
        and binding.scope.parent.kind == "module"
        and (
            binding.kind == "annotation-only" or (binding.kind in {"assign", "annassign"} and binding.value is not None)
        )
    ]


def line_name(model: DataModel, field: DataModelFieldBase) -> str:
    """Return the name with which the class body of ``model`` writes ``field``: ``root`` for a root model's field."""
    return "root" if model.IS_ROOT_MODEL else field.name or ""


def assignment_facts(model: DataModel, field: DataModelFieldBase) -> AssignmentFacts:
    """Return the assignment facts the parse reads for ``field`` of ``model``, which its line is compared with."""
    return model._field_assignment_facts(field)


def _legacy_row(model: DataModel, field: DataModelFieldBase, facts: AssignmentFacts, *, binds: bool) -> str | None:
    """Return the legacy row whose shape the field's line has, or None.

    A row is keyed on the facts and on the built-in template the shape is about, which says what the line writes, not
    on a class object the check imports itself.
    """
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.model.typed_dict import TypedDict

    template, unbound_default = type(model).TEMPLATE_FILE_PATH, not binds and facts.rule == "default"
    if unbound_default and field.use_annotated and getattr(field, "has_default_factory_in_field", False):
        return "legacy-annotated-factory-binds"
    if unbound_default and template == TypedDict.TEMPLATE_FILE_PATH:
        return "legacy-typed-dict-binds"
    if binds and facts.rule == "unassigned" and template == Enum.TEMPLATE_FILE_PATH:
        return "legacy-enum-member-unbound"
    return None


def _check(checks_mode: Mode, model: DataModel, rendered: str) -> None:
    """Report a field whose lines bind its name where its facts bind none, or none where they bind one.

    The class body binds the name when any line of the field assigns it, so every line written under the field's name
    counts, not only the first.
    """
    if (written := field_lines(rendered)) is None:
        return
    lines: dict[str, list[Binding]] = {}
    for binding in written:
        lines.setdefault(binding.name, []).append(binding)
    template = type(model).TEMPLATE_FILE_PATH
    for field in model.fields:
        if not (named := lines.get(line_name(model, field))):
            continue
        bound = next((line.value for line in named if line.value is not None), None)
        facts = assignment_facts(model, field)
        if facts.rule == "hook" or (bound is not None) == facts.binds_class_attribute:
            continue
        wrote = "no value" if bound is None else f"= {ast.unparse(bound)}"
        decided = (
            f"classify_assignment decides binds_class_attribute={facts.binds_class_attribute} "
            f"(assigned={facts.assigned}, rule {facts.rule})"
        )
        details = {"template": template, "model": model.class_name, "field": field.name, "written": wrote}
        if (row := _legacy_row(model, field, facts, binds=bound is not None)) is None:
            checks_mode.violation(
                f"{template} writes {model.class_name}.{field.name} with {wrote}, where {decided}",
                **details,
            )
        elif checks_mode.report_path is not None:
            checks_mode.record(
                f"{checks_mode.area.value}: {template} writes {model.class_name}.{field.name} with {wrote} under row "
                f"{row}, where {decided}",
                row=row,
                **details,
            )


def render_template(original: Callable[..., str]) -> Callable[..., str]:
    """Wrap ``DataModel._render_template``: a built-in template's field lines must bind what the facts bind."""

    @wraps(original)
    def render_template(self: DataModel, *args: object, **kwargs: object) -> str:
        rendered = original(self, *args, **kwargs)
        if (modes := MODES.get()) is None or (checks_mode := modes.get(Area.TEMPLATE_ASSIGNMENTS)) is None:
            return rendered
        from tests.checks.parse import render_ready

        if render_ready() and not self._uses_custom_root_template:
            token = MODES.set({area: other for area, other in modes.items() if area is not Area.RENDER_MEMOS})
            try:
                _check(checks_mode, self, rendered)
            finally:
                MODES.reset(token)
        return rendered

    return render_template
