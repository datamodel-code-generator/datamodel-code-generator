"""Frozen records of how the class body writes a field, built by the decisions and read by the passes.

``AssignmentFacts`` comes from ``model/_decisions/assignment.py:classify_assignment`` through the output model's
adapter ``model/base.py:DataModel._field_assignment_facts``; constructor ordering, keyword-only fixes, hidden-type
checks and the signature warning read it instead of classifying the field themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, final

__all__ = ["AssignmentFacts", "AssignmentRule", "ConstructorDefault"]

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
