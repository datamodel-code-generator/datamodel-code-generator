"""Debug checks that watch how the passes of one parse change the model graph.

Two check areas share this module. With neither variable set, ``Parser.parse`` installs nothing, and the scopes the
pipeline of ``parser/base.py`` opens (``pass_scope``, ``fact_pass``, ``module_checkpoint``) are one shared no-op. Inside
``recording_pass_trace``, ``fact_pass`` and ``module_checkpoint`` also record each pass and module, whether an area is
on or not (``scripts/check_pass_pipeline.py``).

``DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES``: after L1, a data type, a field or a model may change a fact or
presentation attribute of ``ATTRIBUTE_FACTS`` only while a ``GenerationStore`` operation that some row names in
``store_ops`` runs. Any such operation accepts any guarded write; which pass and phase may write which attribute is the
finality check's. A write outside every store operation is reported with the line that wrote it, the parser method it
ran in and the pass scope and phase of the pipeline (``pass_scope``), once per line and parse; the construction of an
object (its ``__init__`` and what that calls) writes freely.

``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY``: the pipeline runs every pass inside
``fact_pass(DECL)`` with its declaration from ``parser/_pass_decls.py``. The parse keeps one fingerprint per model: the
values of the ``fact`` rows of ``ATTRIBUTE_FACTS`` on the model, its fields and every data type of their types and of
its bases, holding the objects it keys. When a pass ends, the fingerprints of the models it touched are compared with
the stored ones: for a module pass the models of its module and the models they reference, the models that guarded
writes and store operations reached during the pass, and the models the pass reported changed
(``GenerationStore.note_changed_models``, ``DataModelFieldBase.invalidate_semantic_caches``). With
``DATAMODEL_CODE_GENERATOR_CHECK_FACT_FINALITY_SWEEP`` on as well, a global pass that transforms or repairs compares
every model. Every model is compared whenever the pipeline enters a phase (``GenerationStore.enter_phase``), which
includes the freeze points B1 and B2. A changed row is

* ``undeclared`` when the pass declares none of the row's facts,
* ``other-module`` when a module pass changed a model of another module,
* ``frozen`` when every declared fact of the row is final at a freeze point the pipeline has passed (B1 for facts,
  which a decision may still write during ``DECIDE`` and which freeze when ``RENDER_PREPARATION`` begins, B2 for the
  rendered nullability rows, the ``RENDER_FACT_FREEZE_POINTS`` entry for render facts),
* ``after-final`` when the phase is later than the row's ``final_after``; a decision writes the facts it declares
  until ``DECIDE`` ends.

A change that no pass covers is ``unattributed``, found at the next comparison of its model: when a pass touches it,
when the next pass starts (a guarded write outside every pass), at the start and the end of each module
(``module_checkpoint``) and when the pipeline leaves a phase (``GenerationStore.enter_phase``). A pass that runs in
another phase than it declares is ``wrong-phase``. On entering ``IMPORTS`` (B2), the ordering-input check compares
the fields that a pass read from the bases of a model for constructor ordering, after the decision phase, with the
facts the ordering pass declares as reads, with those fields now (``ordering-input``). Only type aliases hold
``TYPE_ALIAS_BODY`` in their rows. In
``DECIDE``, a decision reads ``FinalFacts`` through the guarded views of ``decision_views``: reading a render fact
(an attribute, or a key of a dict attribute or of ``__dict__``, whose row holds facts none of which is a ``Fact`` final
at B1) is ``render-read``.

Once a freeze point has passed, the check also seals the graph: each guarded write to an object that a fingerprint
holds, an assignment that changes the attribute or a mutation of a guarded list (``fields``, ``base_classes``,
``data_types``, ...), is judged when it is made, inside store operations too, and is ``frozen`` by the rule above, with
all of the row's facts for a write outside every pass. This covers presentation rows as well.

A row is judged by the freeze points of the facts the pass declares: a pass that declares only ``IMPORT_ALIASES`` (final
at render) can change ``DataType.type``, ``python_type``, ``kwargs`` or ``extras`` after B1 without a report, and one
that declares only ``TYPE_ALIAS_BODY`` (final at B2) can change a type alias's ``data_type`` or ``data_types`` after B1,
although ``FIELD_TYPES`` of those rows is final at B1. ``KNOWN_WRITES`` lists the violations of today's pipeline with a
witness each; they are reported with ``report:<path>`` and never raise. Presentation rows, parser state, module imports
and rows a model class computes with a property are not fingerprinted.

Machinery shared by both areas: ``parse_checks`` opens the state of one parse when either area is on, and
``arm_parse_checks`` arms it at the end of L1: ``DataType``, ``DataModelFieldBase`` and ``DataModel`` get a
``__setattr__`` guard (they inherit theirs, so leaving deletes it again), each store operation of the parser's store
runs in a write scope, and the list attributes of the parsed graph, and the lists store operations set, are replaced by
``_GuardedList`` copies: a list keeps being shared by the objects that shared it, but not with other references to the
original list, and ``type(x) is list`` is false for it. The copies outlive the parse; once no parse is armed they act as
plain lists, and they copy and pickle as plain lists. Writes through ``object.__setattr__``, through ``__dict__``, into
dict attributes, and into lists that an object got outside a store operation after arming are not seen by the guards;
the finality check sees their effect at the next comparison.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, is_dataclass
from decimal import Decimal
from enum import Enum, unique
from functools import wraps
from inspect import getattr_static
from itertools import takewhile, zip_longest
from pathlib import Path
from threading import Lock
from traceback import walk_stack
from typing import TYPE_CHECKING, Any, Final, SupportsIndex, cast
from weakref import ref

from pydantic import BaseModel
from typing_extensions import Sentinel, final, override

from datamodel_code_generator._checks import CheckArea, CheckMode, check_mode
from datamodel_code_generator._records import record
from datamodel_code_generator.model.base import (
    DataModel,
    DataModelFieldBase,
    direct_base_models,
    linearize_data_models,
)
from datamodel_code_generator.model.type_alias import TypeAliasBase
from datamodel_code_generator.parser import _pass_decls as decls
from datamodel_code_generator.parser._check_scopes import (
    NO_SCOPE,
    PARSE_CHECKS,
    GuardedView,
    ParseChecksPort,
)
from datamodel_code_generator.parser._facts import (
    ATTRIBUTE_FACTS,
    RENDER_FACT_FREEZE_POINTS,
    Access,
    AttributeFact,
    Fact,
    FactKind,
    FreezePoint,
    Phase,
    RenderFact,
)
from datamodel_code_generator.parser._pass_decls import PassDecl, PassKind, Scope
from datamodel_code_generator.reference import Reference
from datamodel_code_generator.types import DataType

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator, Sequence
    from contextlib import AbstractContextManager
    from types import FrameType

    from datamodel_code_generator.model._views import ModelView
    from datamodel_code_generator.parser.generation import GenerationStore

__all__ = [
    "KNOWN_WRITES",
    "KnownWrite",
    "Rule",
    "parse_checks",
]

_PACKAGE_ROOT: Final = Path(__file__).parents[1]
_PACKAGE: Final = _PACKAGE_ROOT.name
_SOURCE_PREFIX: Final = f"src/{_PACKAGE}"
_CONSTRUCTORS: Final = frozenset({"__init__", "__post_init__", "model_post_init"})
_GUARDED_KINDS: Final = frozenset({FactKind.FACT, FactKind.PRESENTATION})
_GUARDED_ACCESS: Final = frozenset({Access.ATTRIBUTE, Access.CONTAINER})
_GUARDED_CLASSES: Final = (DataType, DataModelFieldBase, DataModel)
_LIST_MUTATORS: Final = (
    "__delitem__",
    "__iadd__",
    "__imul__",
    "__setitem__",
    "append",
    "clear",
    "extend",
    "insert",
    "pop",
    "remove",
    "reverse",
    "sort",
)
_KEYED_MUTATORS: Final = frozenset({"__delitem__", "__setitem__", "pop"})


def _rows_by_owner() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """Return, by owner class name, the guarded attributes and those of them that hold containers."""
    attributes: dict[str, set[str]] = {}
    containers: dict[str, set[str]] = {}
    for row in ATTRIBUTE_FACTS:
        if row.kind in _GUARDED_KINDS and row.access in _GUARDED_ACCESS:
            attributes.setdefault(row.owner, set()).add(row.attribute)
            if row.access is Access.CONTAINER:
                containers.setdefault(row.owner, set()).add(row.attribute)
    return {owner: (frozenset(names), frozenset(containers.get(owner, ()))) for owner, names in attributes.items()}


_ROWS_BY_OWNER: Final = _rows_by_owner()
_STORE_OPERATIONS: Final = frozenset(store_op for row in ATTRIBUTE_FACTS for store_op in row.store_ops)


def _source_path(filename: str) -> str:
    """Return a writer's file as the store checker names it: package files from ``src/``, others as given."""
    path = Path(filename)
    if path.is_relative_to(_PACKAGE_ROOT):
        return f"{_SOURCE_PREFIX}/{path.relative_to(_PACKAGE_ROOT).as_posix()}"
    return path.as_posix()


def _key_kind(operation: str, args: tuple[Any, ...]) -> str | None:
    """Return how a list mutator addresses its list: by ``slice``, by ``index``, or as a whole (None)."""
    if operation not in _KEYED_MUTATORS or not args:
        return None
    return "slice" if isinstance(args[0], slice) else "index"


def _qualified_name(frame: FrameType) -> str:
    return getattr(frame.f_code, "co_qualname", frame.f_code.co_name)


def _stack(frame: FrameType) -> Iterator[FrameType]:
    return (item for item, _ in walk_stack(frame))


@final
class _GuardedList(list[Any]):  # noqa: FURB189
    """A list attribute of the parsed graph whose mutators report to the checks of the parse."""

    __slots__ = ("_attribute", "_holder", "_owner")

    _attribute: str
    _holder: ref[Any]
    _owner: str

    @classmethod
    def of(cls, items: Iterable[Any], holder: object, attribute: str) -> _GuardedList:
        """Return a guarded list holding ``items``, as the attribute of ``holder``."""
        guarded = cls(items)
        guarded._owner = type(holder).__name__
        guarded._holder = ref(holder)
        guarded._attribute = attribute
        return guarded

    @override
    def __reduce_ex__(self, protocol: SupportsIndex, /) -> tuple[type[list[Any]], tuple[list[Any]]]:
        """Copy and pickle as a plain list: a copy of a guarded object is a new object that nothing guards."""
        return list, (list(self),)


def _current_checks() -> _ParseChecks | None:
    return cast("_ParseChecks | None", PARSE_CHECKS.get())


def _guarded_mutator(name: str) -> Callable[..., Any]:
    mutate = getattr(list, name)

    def mutator(self: _GuardedList, *args: Any, **kwargs: Any) -> Any:
        if (checks := _current_checks()) is None or not checks.armed:
            return mutate(self, *args, **kwargs)
        before = checks.on_list_write(self, name, args)
        result = mutate(self, *args, **kwargs)
        checks.judge_list_write(self, before)
        return result

    mutator.__name__ = name
    return mutator


for _name in _LIST_MUTATORS:
    setattr(_GuardedList, _name, _guarded_mutator(_name))


def _guarded_setattr(original: Callable[[Any, str, Any], None]) -> Callable[[Any, str, Any], None]:
    def __setattr__(self: Any, name: str, value: Any) -> None:  # noqa: N807
        if (checks := _current_checks()) is not None and checks.armed:
            value = checks.on_setattr(self, name, value, sys._getframe(1))  # noqa: SLF001
        original(self, name, value)

    return __setattr__


def _noting_invalidation(original: Callable[..., None]) -> Callable[..., None]:
    @wraps(original)
    def invalidate_semantic_caches(self: DataModelFieldBase, *, invalidate_parent: bool = True) -> None:
        if (checks := _current_checks()) is not None and (finality := checks.finality) is not None:
            finality.mark((self,))
        original(self, invalidate_parent=invalidate_parent)

    return invalidate_semantic_caches


@final
class _ClassGuards:
    """The ``__setattr__`` guards of the graph classes, installed while at least one parse is armed.

    ``DataModelFieldBase.invalidate_semantic_caches`` notes its field's models as changed for the finality check.
    """

    def __init__(self) -> None:
        self._lock = Lock()
        self._armed = 0

    def install(self) -> None:
        """Count one more armed parse; the first one installs the guards."""
        with self._lock:
            self._armed += 1
            if self._armed == 1:
                for cls in _GUARDED_CLASSES:
                    cls.__setattr__ = _guarded_setattr(cls.__setattr__)  # ty: ignore[invalid-assignment]
                DataModelFieldBase.invalidate_semantic_caches = _noting_invalidation(_INVALIDATE)  # ty: ignore[invalid-assignment]

    def uninstall(self) -> None:
        """Count one armed parse less; the last one gives the classes their inherited ``__setattr__`` back."""
        with self._lock:
            self._armed -= 1
            if not self._armed:
                for cls in _GUARDED_CLASSES:
                    del cls.__setattr__
                DataModelFieldBase.invalidate_semantic_caches = _INVALIDATE


_INVALIDATE: Final = DataModelFieldBase.invalidate_semantic_caches
_CLASS_GUARDS: Final = _ClassGuards()


@unique
class Rule(Enum):
    """Why the finality check reports a change."""

    UNDECLARED = "undeclared"
    OTHER_MODULE = "other-module"
    FROZEN = "frozen"
    AFTER_FINAL = "after-final"
    UNATTRIBUTED = "unattributed"
    WRONG_PHASE = "wrong-phase"
    ORDERING_INPUT = "ordering-input"
    RENDER_READ = "render-read"


@final
@record
class KnownWrite:
    """A finality violation of today's pipeline: reported, never raised, and made by its witness."""

    pass_name: str
    """The declaration name of the pass, or ``<PHASE>`` for an unattributed change."""
    rule: Rule
    row: str
    """The ``ATTRIBUTE_FACTS`` row as ``Owner.attribute``, or ``-`` for a pass that runs in the wrong phase."""
    witness: str
    """The ``datamodel-codegen`` arguments of one run that makes the violation, from the repository root."""
    removed_by: str
    """The roadmap item that removes the violation."""


_TAGGED_MODULES_WITNESS: Final = (
    "--input tests/data/openapi/discriminator_msgspec_tagged_unions_modules_variants_first.yaml --input-file-type "
    "openapi --output-model-type msgspec.Struct --use-msgspec-tagged-unions --target-python-version 3.10 "
    "--formatters builtin"
)
_IMPORTS_CORRECT_WITNESS: Final = (
    "--input tests/data/jsonschema/imports_correct --output-model-type pydantic_v2.BaseModel --formatters builtin"
)
_EMPTY_UNION_WITNESS: Final = (
    "--input tests/data/jsonschema/collapse_root_models_empty_union.json --input-file-type jsonschema "
    "--collapse-root-models --formatters builtin"
)

KNOWN_WRITES: tuple[KnownWrite, ...] = (
    KnownWrite(
        "apply_discriminator_type",
        Rule.OTHER_MODULE,
        "DataModel.fields",
        "--input tests/data/openapi/external_discriminator_base/multiple --input-file-type openapi "
        "--formatters builtin",
        "RP17",
    ),
    KnownWrite(
        "apply_discriminator_type", Rule.OTHER_MODULE, "DataModelFieldBase.data_type", _IMPORTS_CORRECT_WITNESS, "RP17"
    ),
    KnownWrite(
        "apply_discriminator_type", Rule.OTHER_MODULE, "DataModelFieldBase.required", _IMPORTS_CORRECT_WITNESS, "RP17"
    ),
    KnownWrite(
        "apply_discriminator_type",
        Rule.OTHER_MODULE,
        'DataModelFieldBase.extras["is_classvar"]',
        "--input tests/data/jsonschema/discriminator_with_external_reference --output-model-type msgspec.Struct "
        "--target-python-version 3.10 --formatters builtin",
        "RP17",
    ),
    KnownWrite(
        "<MODULES>", Rule.UNATTRIBUTED, 'DataModelFieldBase.extras["is_classvar"]', _TAGGED_MODULES_WITNESS, "RP17"
    ),
    KnownWrite(
        "delete_duplicate_models",
        Rule.OTHER_MODULE,
        "DataModel.base_classes",
        "--input tests/data/jsonschema/schema_validators_property_count_cross_module --input-file-type jsonschema "
        "--generate-schema-validators --output-model-type pydantic_v2.BaseModel --formatters builtin",
        "DC11b",
    ),
    KnownWrite(
        "delete_duplicate_models",
        Rule.OTHER_MODULE,
        "DataType.reference",
        "--input tests/data/jsonschema/external_ref_slash_key/schema.json --input-file-type jsonschema "
        "--target-python-version 3.10 --formatters builtin",
        "DC11b",
    ),
    KnownWrite(
        "reuse_model",
        Rule.OTHER_MODULE,
        "DataType.reference",
        "--input tests/data/openapi/reuse_model_cross_module_users.yaml --input-file-type openapi --output-model-type "
        "pydantic_v2.BaseModel --reuse-model --target-python-version 3.10 --formatters builtin",
        "G07",
    ),
    KnownWrite(
        "fix_constructor_field_ordering",
        Rule.FROZEN,
        'DataModelFieldBase.extras["init"]',
        "--input tests/data/openapi/allof_required_inherited_dataclass_metadata.yaml --input-file-type openapi "
        "--output-model-type dataclasses.dataclass --field-extra-keys init --order-constructor-fields-by final-names "
        "--formatters builtin",
        "RP14c",
    ),
    KnownWrite(
        "prepare_type_alias_fields",
        Rule.FROZEN,
        "DataModelFieldBase.extras",
        "--input tests/data/jsonschema/type_alias_lookaround/model.json --input-file-type jsonschema --use-type-alias "
        "--use-annotated --formatters builtin",
        "RP14a",
    ),
    KnownWrite(
        "fix_constructor_field_ordering",
        Rule.ORDERING_INPUT,
        "DataType.is_optional",
        "--input tests/data/openapi/schema_intersection/inherited_override_null_oas31.yaml --input-file-type openapi "
        "--output-model-type msgspec.Struct --formatters builtin",
        "RP14",
    ),
    KnownWrite(
        "finalize_rendered_nullability",
        Rule.FROZEN,
        "DataType.import_",
        "--input tests/data/generation_platform/fastapi/unbound-body.yaml --input-file-type openapi --openapi-scopes "
        "schemas api --collapse-root-models --formatters builtin",
        "RP15",
    ),
    KnownWrite("render_modules", Rule.FROZEN, "DataType.import_", _EMPTY_UNION_WITNESS, "RP15"),
)
"""The violations of today's pipeline; ``scripts/check_fact_finality.py --witnesses`` runs their witnesses.

A module pass writes models of other modules: the discriminator pass writes the tags of variants (RP17 plans them
first), reuse redirects the users of removed duplicates (G07's reuse groups), and L3 deduplication redirects users
and bases module by module (DC11b makes it one step). Constructor ordering, after the decision phase, gives a
required field back the place in the constructor it left out while its base excluded it (RP14c's dataclass field
plan holds it), and it reads the nullability of base fields before render preparation writes rendered nullability
once (RP14's ordering reads the plans written after it). Rendering writes the import of the ``Any`` an empty union
renders as after B1, at render preparation and at L7 (RP15), and alias preparation records compiled patterns in
field extras after B1 (RP14a's field render plan holds them).
"""

_KNOWN: Final = frozenset((known.pass_name, known.rule, known.row) for known in KNOWN_WRITES)
_SWEEPING_KINDS: Final = frozenset({PassKind.TRANSFORM, PassKind.REPAIR})
_PHASE_FREEZE_POINTS: Final = {FreezePoint.B1: Phase.RENDER_PREPARATION, FreezePoint.B2: Phase.IMPORTS}
_SEALED_FROM: Final = min(_PHASE_FREEZE_POINTS.values(), key=lambda phase: phase.value)
"""The first phase in which a freeze point has passed, from which guarded writes are judged when they are made."""
_ITEM_ROW: Final = re.compile(r'(\w+)\["([^"]+)"\]')
_MISSING: Final = Sentinel("_MISSING")
_IDENTITY: Final = Sentinel("_IDENTITY")
_SEQUENCE: Final = Sentinel("_SEQUENCE")
_MAPPING: Final = Sentinel("_MAPPING")
_SET: Final = Sentinel("_SET")
_PLAIN_TYPES: Final = frozenset({str, int, bool, type(None), bytes})
_BUILTIN_VALUE_TYPES: Final = (bool, int, float, complex, Decimal, str, bytes)
_DESCENDED: Final = (DataType, DataModelFieldBase)
_GRAPH_CLASSES: Final = (DataType, DataModelFieldBase, Reference, DataModel)
_ORDERING_READS: Final = decls.FIX_CONSTRUCTOR_FIELD_ORDERING.reads
_FIELDS_ROW: Final = next(row for row in ATTRIBUTE_FACTS if (row.owner, row.attribute) == ("DataModel", "fields"))


def _freeze(value: Any, nested: list[Any]) -> Any:  # noqa: PLR0911
    """Return a comparable copy of ``value``; graph objects become their identity and are appended to ``nested``.

    Numbers, strings and bytes compare by their built-in text, so that NaN equals itself and subclasses never run their
    own hash or equality; any other object is kept as it is and compares by its own equality.
    """
    kind = type(value)
    if kind in _PLAIN_TYPES:
        return value
    if isinstance(value, _GRAPH_CLASSES):
        nested.append(value)
        return _IDENTITY, id(value)
    if isinstance(value, (list, tuple)):
        return _SEQUENCE, kind is tuple, tuple(_freeze(item, nested) for item in value)
    if isinstance(value, dict):
        return _MAPPING, tuple((_freeze(key, nested), _freeze(item, nested)) for key, item in value.items())
    if isinstance(value, (set, frozenset)):
        return _SET, frozenset(_freeze(item, nested) for item in value)
    if isinstance(value, Enum):
        return kind, value.name
    if isinstance(value, _BUILTIN_VALUE_TYPES):
        return kind, repr(next(base for base in _BUILTIN_VALUE_TYPES if isinstance(value, base))(value))
    if isinstance(value, BaseModel):
        state = value.__dict__
        return kind, tuple(_freeze(state.get(name), nested) for name in kind.model_fields)
    if is_dataclass(value):
        return kind, tuple(_freeze(getattr(value, item.name), nested) for item in fields(value))
    return value


def _dict_keys() -> dict[str, str]:
    """Return the ``__dict__`` key that each constant a dict-key row names stands for, from the loaded modules.

    A module that is not loaded puts no key into any object, so its constants are not needed.
    """
    names = {row.attribute for row in ATTRIBUTE_FACTS if row.access is Access.DICT_KEY}
    keys: dict[str, str] = {}
    for module_name, module in sorted(sys.modules.items()):
        if module_name.startswith(f"{_PACKAGE}.") and module is not None:
            keys.update(
                (name, value) for name in names - keys.keys() if isinstance(value := getattr(module, name, None), str)
            )
    return keys


def _getter(row: AttributeFact, dict_keys: dict[str, str], item_keys: frozenset[str]) -> Callable[[Any], Any]:
    """Return how to read the value of ``row`` on an object.

    A dict-key row whose constant no loaded module defines reads its name, which no object holds.
    """
    match row.access:
        case Access.ITEM:
            match = _ITEM_ROW.fullmatch(row.attribute)
            container, key = match.groups() if match else (row.attribute, "")
            return lambda owner: getattr(owner, container, {}).get(key, _MISSING)
        case Access.DICT_KEY:
            key = dict_keys.get(row.attribute, row.attribute)
            return lambda owner: owner.__dict__.get(key, _MISSING)
        case _ if row.access is Access.CONTAINER and item_keys:
            name = row.attribute
            return lambda owner: {key: item for key, item in getattr(owner, name, {}).items() if key not in item_keys}
        case _:
            name = row.attribute
            return lambda owner: getattr(owner, name, _MISSING)


@final
@record
class _ClassRows:
    """The fact rows of one class and how to read them."""

    rows: tuple[AttributeFact, ...]
    getters: tuple[Callable[[Any], Any], ...]

    @classmethod
    def of(cls, owner_class: type, dict_keys: dict[str, str]) -> _ClassRows:
        """Collect the fact rows that ``owner_class`` stores, from the rows of its package classes.

        A row that the class computes with a property holds no state of its own, so it is left out; a dict attribute
        leaves out the keys that have rows of their own.
        """
        names = {base.__name__ for base in owner_class.__mro__ if base.__module__.startswith(f"{_PACKAGE}.")}
        rows = [row for row in ATTRIBUTE_FACTS if row.owner in names]
        item_keys: dict[str, set[str]] = {}
        for row in rows:
            if row.access is Access.ITEM and (match := _ITEM_ROW.fullmatch(row.attribute)):
                item_keys.setdefault(match.group(1), set()).add(match.group(2))
        readable = tuple(
            row
            for row in rows
            if row.kind is FactKind.FACT and not isinstance(getattr_static(owner_class, row.attribute, None), property)
        )
        return cls(
            readable,
            tuple(_getter(row, dict_keys, frozenset(item_keys.get(row.attribute, ()))) for row in readable),
        )


@final
@record
class _Entry:
    """The fact values of one object of a model's graph."""

    owner: object
    rows: tuple[AttributeFact, ...]
    values: tuple[Any, ...]
    base: bool
    """Whether the object belongs to a base class of the model, so its type facts are ``BASES``."""
    alias: bool
    """Whether the object belongs to a type alias, the only model whose rows hold ``TYPE_ALIAS_BODY``."""


@final
@record
class _ModelPrint:
    """The fingerprint of one model: its entries by object id, holding the objects."""

    model: DataModel
    entries: dict[int, _Entry]
    referenced: tuple[object, ...]
    """The references and models the fingerprint names, through which new models are found."""


def _row_name(row: AttributeFact) -> str:
    return f"{row.owner}.{row.attribute}"


def _row_facts(entry: _Entry, row: AttributeFact) -> frozenset[Fact | RenderFact]:
    facts = (fact for fact in row.facts if entry.alias or fact is not RenderFact.TYPE_ALIAS_BODY)
    if not entry.base:
        return frozenset(facts)
    return frozenset(Fact.BASES if isinstance(fact, Fact) else fact for fact in facts)


def _freeze_point(row: AttributeFact, fact: Fact | RenderFact) -> FreezePoint:
    """Return where ``fact`` of ``row`` is final: a render fact at its point, a fact at B1 or its row's later point."""
    if isinstance(fact, RenderFact):
        return RENDER_FACT_FREEZE_POINTS[fact]
    if row.frozen_at is not None and all(isinstance(item, Fact) for item in row.facts):
        return row.frozen_at
    return FreezePoint.B1


def _final_phase(row: AttributeFact, decl: PassDecl) -> Phase:
    """Return the last phase in which ``decl`` may write ``row``: for a decision, at the earliest the end of DECIDE."""
    if decl.phase is Phase.DECIDE and row.final_after.value < Phase.DECIDE.value:
        return Phase.DECIDE
    return row.final_after


def _passed(point: FreezePoint, phase: Phase) -> bool:
    return (entered := _PHASE_FREEZE_POINTS.get(point)) is not None and phase.value >= entered.value


@final
@dataclass(slots=True)
class _PassRun:
    """One pass running inside ``fact_pass``."""

    decl: PassDecl
    own: set[int]
    """The ids of the models of the module a module pass runs for."""
    touched: dict[int, DataModel] = field(default_factory=dict)


@final
@record
class _OrderingField:
    """A field of a base that constructor ordering reads, with the values of the facts it reads."""

    field: DataModelFieldBase
    base: DataModel
    values: tuple[tuple[int, AttributeFact, Any], ...]
    held: tuple[object, ...]
    """The objects whose ids ``values`` names, kept alive so that no id is reused while the read is recorded."""


@final
@record
class _OrderingRead:
    """The constructor fields that ordering read from the bases of ``model``."""

    pass_name: str
    model: DataModel
    bases: tuple[DataModel, ...]
    """The linearized bases ordering read, a base a type override replaced as the generated model it stood for."""
    fields: tuple[_OrderingField, ...]
    stand_ins: Mapping[int, Reference | None]
    """The generated models that the bases a type override replaced stand for, at every level of the chain."""


@final
class _Finality:
    """The finality check of one armed parse."""

    def __init__(
        self,
        mode: CheckMode,
        error: type[Exception],
        store: GenerationStore,
        guard_lists: Callable[[object, dict[int, _GuardedList]], None],
        *,
        sweep: bool,
    ) -> None:
        self.mode = mode
        self._guard_lists = guard_lists
        """Puts the plain lists of an object it holds into guarded lists, sharing those it has guarded already."""
        self.sweep = sweep
        """Whether a global pass that transforms or repairs compares every model (``FACT_FINALITY_SWEEP``)."""
        self.error = error
        self.store = store
        self.prints: dict[int, _ModelPrint] = {}
        self.owners: dict[int, dict[int, DataModel]] = {}
        self.held: dict[int, object] = {}
        self.run: _PassRun | None = None
        self.module: dict[int, DataModel] | None = None
        """The models of the module that ``module_checkpoint`` processes now, by id, as they were at its start."""
        self.done: set[int] = set()
        self.loose: dict[int, DataModel] = {}
        """Models that guarded writes outside every pass reached, to compare when the next pass starts."""
        self.ordering_reads: list[_OrderingRead] = []
        self._classes: dict[type, _ClassRows] = {}
        self._guarded_rows: dict[type, dict[str, tuple[AttributeFact, ...]]] = {}
        self._dict_keys = _dict_keys()
        self._reported: set[tuple[str, Rule, str]] = set()
        self._settle(store.models)

    def _rows(self, cls: type) -> _ClassRows:
        if (rows := self._classes.get(cls)) is None:
            rows = self._classes[cls] = _ClassRows.of(cls, self._dict_keys)
        return rows

    def _entries(
        self, owner: object, entries: dict[int, _Entry], referenced: list[object], *, alias: bool = False
    ) -> None:
        """Add the entries of ``owner`` and of the fields and data types it holds; data types of bases are ``base``.

        With ``alias``, ``owner`` is a type alias and the entries outside its bases are ``alias``.
        """
        pending: list[tuple[object, bool]] = [(owner, False)]
        while pending:
            item, base = pending.pop()
            if id(item) in entries:
                continue
            rows = self._rows(type(item))
            nested: list[Any] = []
            values = tuple(
                value if type(value := getter(item)) in _PLAIN_TYPES else _freeze(value, nested)
                for getter in rows.getters
            )
            entries[id(item)] = _Entry(item, rows.rows, values, base, alias and not base)
            base_ids = {id(base_class) for base_class in getattr(item, "base_classes", ())}
            for child in nested:
                if isinstance(child, _DESCENDED):
                    pending.append((child, base or id(child) in base_ids))
                else:
                    referenced.append(child)

    def _print(self, model: DataModel) -> _ModelPrint:
        """Return the fingerprint of ``model``: the model, its fields and their types, and its bases."""
        entries: dict[int, _Entry] = {}
        referenced: list[object] = []
        self._entries(model, entries, referenced, alias=isinstance(model, TypeAliasBase))
        model_id = id(model)
        lists: dict[int, _GuardedList] = {}
        for object_id, entry in entries.items():
            self.owners.setdefault(object_id, {})[model_id] = model
            self.held[object_id] = entry.owner
            self._guard_lists(entry.owner, lists)
        return _ModelPrint(model, entries, tuple(referenced))

    def _settle(self, models: Iterable[DataModel], run: _PassRun | None = None, *, reach: bool = False) -> None:
        """Compare ``models`` with their stored fingerprints, report what changed, and store the new ones.

        Models that a fingerprint names and that have none yet get their first one; with ``reach``, the models that
        the new fingerprints name are compared as well.
        """
        pending = list(models)
        settled: set[int] = set()
        reached: list[DataModel] = []
        while pending:
            if (model_id := id(current := pending.pop())) in settled:
                continue
            settled.add(model_id)
            self.loose.pop(model_id, None)
            new = self._print(current)
            old = self.prints.get(model_id)
            self.prints[model_id] = new
            if old is not None:
                self._compare(old, new, run)
            for item in new.referenced:
                if not isinstance(source := item.source if isinstance(item, Reference) else item, DataModel):
                    continue
                if id(source) not in self.prints:
                    pending.append(source)
                elif reach:
                    reached.append(source)
        if reached:
            self._settle((model for model in reached if id(model) not in settled), run)

    def _compare(self, old: _ModelPrint, new: _ModelPrint, run: _PassRun | None) -> None:
        for object_id, before in old.entries.items():
            if (after := new.entries.get(object_id)) is None or after.values == before.values:
                continue
            for row, was, now in zip(after.rows, before.values, after.values, strict=True):
                if was != now:
                    self._judge(new.model, after, row, run)

    def _judge(self, model: DataModel, entry: _Entry, row: AttributeFact, run: _PassRun | None) -> None:
        """Report a change of ``row`` of ``model`` when the rules forbid it."""
        facts = _row_facts(entry, row)
        phase = self.store.phase
        if run is None:
            self._violation(f"<{phase.name}>", Rule.UNATTRIBUTED, row, model, facts)
            return
        decl = run.decl
        if not (declared := facts & decl.writes):
            self._violation(decl.name, Rule.UNDECLARED, row, model, facts)
        elif decl.scope is Scope.MODULE and id(model) not in run.own:
            self._violation(decl.name, Rule.OTHER_MODULE, row, model, facts, module_done=id(model) in self.done)
        elif all(_passed(_freeze_point(row, fact), phase) for fact in declared):
            self._violation(decl.name, Rule.FROZEN, row, model, declared)
        elif phase.value > (final_after := _final_phase(row, decl)).value:
            self._violation(decl.name, Rule.AFTER_FINAL, row, model, facts, final_after=final_after.name)

    def _violation(
        self,
        pass_name: str,
        rule: Rule,
        row: AttributeFact | None,
        model: DataModel | None,
        facts: Iterable[Fact | RenderFact],
        **details: object,
    ) -> None:
        """Raise or report a violation once per pass, rule and row; a known one is only reported."""
        row_name = "-" if row is None else _row_name(row)
        if (key := (pass_name, rule, row_name)) in self._reported:
            return
        self._reported.add(key)
        known = key in _KNOWN
        if known and self.mode.report_path is None:
            return
        fact_names = sorted(fact.name for fact in facts)
        class_name = None if model is None else model.class_name
        phase = self.store.phase.name
        self.mode.violation(
            self.error,
            f"{pass_name} {rule.value}: {row_name} ({', '.join(fact_names)}) of {class_name} during {phase}",
            rule=rule.value,
            pass_name=pass_name,
            row=row_name,
            facts=fact_names,
            model=class_name,
            phase=phase,
            known=known,
            **details,
        )

    def models_of(self, owner: object) -> Iterable[DataModel]:
        """Return the models whose fingerprint holds ``owner``, or the model its parents lead to."""
        if isinstance(owner, DataModel):
            return (owner,)
        if models := self.owners.get(id(owner)):
            return models.values()
        node = owner
        while isinstance(node, DataType):
            node = node.parent
        if isinstance(node, DataModelFieldBase):
            node = node.parent
        return (node,) if isinstance(node, DataModel) else ()

    def touch(self, owner: object) -> None:
        """Note that the running pass writes ``owner``; a model of another module is compared before the write.

        A write outside every pass is compared when the next pass starts.
        """
        if (run := self.run) is None:
            self._note_loose(owner)
            return
        for model in tuple(self.models_of(owner)):
            if (model_id := id(model)) in run.touched:
                continue
            run.touched[model_id] = model
            if model_id not in run.own and model_id in self.prints:
                self._settle((model,))

    def mark(self, owners: Iterable[object]) -> None:
        """Note that the models of ``owners`` changed, as the store or a field reports, to compare when the pass ends.

        Unlike ``touch``, nothing is compared first: the change is already made. Outside every pass the models are
        compared when the next pass starts.
        """
        if (run := self.run) is None:
            for owner in owners:
                self._note_loose(owner)
            return
        for owner in owners:
            for model in self.models_of(owner):
                run.touched.setdefault(id(model), model)

    def _note_loose(self, owner: object) -> None:
        """Note the fingerprinted models of ``owner`` changed outside every pass, to compare when the next starts."""
        self.loose.update((id(model), model) for model in self.models_of(owner) if id(model) in self.prints)

    @contextmanager
    def pass_scope(self, decl: PassDecl, models: Sequence[DataModel] | None) -> Generator[None, None, None]:
        """Run one pass and compare the models it touched when it ends; a pass inside another is a pass of its own."""
        if self.loose:
            self._settle(tuple(self.loose.values()))
        if decl.phase is not self.store.phase:
            self._violation(decl.name, Rule.WRONG_PHASE, None, None, (), declared_phase=decl.phase.name)
        own = {id(model) for model in models or ()}
        if decl.scope is Scope.MODULE and (module := self.module) is not None:
            own.update(module)
        outer, run = self.run, _PassRun(decl, own)
        self.run = run
        try:
            yield
            checked: dict[int, DataModel] = {}
            if models is not None:
                run.own.update(id(model) for model in models)
                checked.update((id(model), model) for model in models)
            checked.update(run.touched)
            if self.sweep and decl.scope is Scope.GLOBAL and decl.kind in _SWEEPING_KINDS:
                checked.update((model_id, model_print.model) for model_id, model_print in self.prints.items())
            self._settle(checked.values(), run, reach=decl.scope is Scope.MODULE)
        finally:
            self.run = outer

    @contextmanager
    def module_scope(self, models: list[DataModel]) -> Generator[None, None, None]:
        """Compare the models of one module before and after it is processed; afterwards the module is done."""
        start = {id(model): model for model in models}
        self._settle(start.values(), reach=True)
        self.module = start
        try:
            yield
            done = {**start, **{id(model): model for model in models}}
            self._settle(done.values())
            self.done.update(done)
        finally:
            self.module = None

    def leave_phase(self) -> None:
        """Compare every model before the pipeline leaves the current phase."""
        self._settle(tuple(model_print.model for model_print in self.prints.values()))

    def check_ordering_reads(self) -> None:
        """Check, at B2, that the constructor fields the passes read from bases did not change since."""
        for read in self.ordering_reads:
            self._check_ordering_read(read)

    def _ordering_fields(self, bases: Iterable[DataModel]) -> tuple[_OrderingField, ...]:
        """Return the named fields of ``bases`` in constructor order, with the values of the facts ordering reads."""
        result: list[_OrderingField] = []
        for base in reversed(list(bases)):
            for base_field in (item for item in base.fields if item.name is not None):
                entries: dict[int, _Entry] = {}
                referenced: list[object] = []
                self._entries(base_field, entries, referenced)
                values = tuple(
                    (id(entry.owner), row, value)
                    for entry in entries.values()
                    for row, value in zip(entry.rows, entry.values, strict=True)
                    if not _ORDERING_READS.isdisjoint(row.facts)
                )
                held = (*(entry.owner for entry in entries.values()), *referenced)
                result.append(_OrderingField(base_field, base, values, held))
        return tuple(result)

    def record_ordering(
        self, model: DataModel, bases: Iterable[DataModel], stand_ins: Mapping[int, Reference | None]
    ) -> None:
        """Record the constructor fields that the running pass, or the phase, reads from the bases of ``model``."""
        pass_name = f"<{self.store.phase.name}>" if (run := self.run) is None else run.decl.name
        read = tuple(bases)
        self.ordering_reads.append(_OrderingRead(pass_name, model, read, self._ordering_fields(read), stand_ins))

    def judge_write(self, owner: object, name: str, value: Any = _MISSING) -> None:
        """Report a guarded write of ``name`` of ``owner`` that comes after the facts it writes froze.

        Only objects a fingerprint holds now are judged, and an assignment of ``value`` that ``name`` holds already
        changes nothing, also a list holding the same items as the one it holds. The running pass writes the facts it
        declares among the row's facts; a write outside every pass, any of them.
        """
        phase = self.store.phase
        if (
            phase.value < _SEALED_FROM.value
            or (value is not _MISSING and _same_value(owner.__dict__.get(name, _MISSING), value))
            or (held := self._held_entry(owner)) is None
        ):
            return
        model, entry = held
        run = self.run
        for row in self._write_rows(type(owner)).get(name, ()):
            facts = _row_facts(entry, row)
            declared = facts if run is None else facts & run.decl.writes
            if declared and all(_passed(_freeze_point(row, fact), phase) for fact in declared):
                self._violation(f"<{phase.name}>" if run is None else run.decl.name, Rule.FROZEN, row, model, declared)

    def _held_entry(self, owner: object) -> tuple[DataModel, _Entry] | None:
        """Return a model whose fingerprint holds ``owner`` now, with the entry of ``owner``, or None."""
        return next(
            (
                (model, entry)
                for model in self.owners.get(id(owner), {}).values()
                if (entry := self.prints[id(model)].entries.get(id(owner))) is not None
            ),
            None,
        )

    def _write_rows(self, cls: type) -> dict[str, tuple[AttributeFact, ...]]:
        """Return the rows the guards watch on ``cls``, by attribute, from the rows of its package classes."""
        if (rows := self._guarded_rows.get(cls)) is None:
            names = {base.__name__ for base in cls.__mro__ if base.__module__.startswith(f"{_PACKAGE}.")}
            found: dict[str, list[AttributeFact]] = {}
            for row in ATTRIBUTE_FACTS:
                if row.owner in names and row.kind in _GUARDED_KINDS and row.access in _GUARDED_ACCESS:
                    found.setdefault(row.attribute, []).append(row)
            rows = self._guarded_rows[cls] = {attribute: tuple(items) for attribute, items in found.items()}
        return rows

    def read_render_fact(self, owner: object, row: AttributeFact) -> None:
        """Report that the running pass reads ``row`` of ``owner``, a render fact, through a view of ``FinalFacts``."""
        name = f"<{self.store.phase.name}>" if (run := self.run) is None else run.decl.name
        source = owner.source if isinstance(owner, Reference) else owner
        self._violation(name, Rule.RENDER_READ, row, next(iter(self.models_of(source)), None), row.facts)

    def _check_ordering_read(self, read: _OrderingRead) -> None:
        """Report a field of a base that ordering read and that changed before B2, or a changed set of fields."""
        now = self._ordering_fields(
            linearize_data_models(direct_base_models(read.model, read.stand_ins), read.stand_ins)
        )
        name = read.pass_name
        if [id(item.field) for item in now] != [id(item.field) for item in read.fields]:
            self._violation(name, Rule.ORDERING_INPUT, _FIELDS_ROW, read.model, _FIELDS_ROW.facts)
            return
        for before, after in zip(read.fields, now, strict=True):
            changed = next((new or old for old, new in zip_longest(before.values, after.values) if old != new), None)
            if changed is not None:
                row = changed[1]
                self._violation(name, Rule.ORDERING_INPUT, row, read.model, row.facts, base=before.base.class_name)


def _same_value(old: Any, new: Any) -> bool:
    """Return whether assigning ``new`` over ``old`` changes nothing: the same object, or lists of the same items."""
    if old is new:
        return True
    return (
        isinstance(old, list)
        and isinstance(new, list)
        and len(old) == len(new)
        and all(before is after for before, after in zip(old, new, strict=True))
    )


def _reads_render_fact(row: AttributeFact) -> bool:
    """Return whether ``row`` holds facts and none of them is a ``Fact`` final at B1: a render fact for decisions."""
    return bool(row.facts) and not any(
        isinstance(fact, Fact) and _freeze_point(row, fact) is FreezePoint.B1 for fact in row.facts
    )


@final
@record
class _RenderRows:
    """The render-fact rows of one class: by attribute, and by key of a dict attribute or of ``__dict__``."""

    attributes: dict[str, AttributeFact]
    items: dict[str, dict[str, AttributeFact]]

    @classmethod
    def of(cls, owner_class: type, dict_keys: dict[str, str]) -> _RenderRows:
        """Collect the render-fact rows of ``owner_class`` from the rows of its package classes."""
        names = {base.__name__ for base in owner_class.__mro__ if base.__module__.startswith(f"{_PACKAGE}.")}
        attributes: dict[str, AttributeFact] = {}
        items: dict[str, dict[str, AttributeFact]] = {}
        for row in ATTRIBUTE_FACTS:
            if row.owner not in names or not _reads_render_fact(row):
                continue
            match row.access:
                case Access.ITEM:
                    container, _, key = row.attribute.removesuffix('"]').partition('["')
                    items.setdefault(container, {})[key] = row
                case Access.DICT_KEY:
                    items.setdefault("__dict__", {})[dict_keys.get(row.attribute, row.attribute)] = row
                case _:
                    attributes[row.attribute] = row
        return cls(attributes, items)


@final
class _ReadGuard:
    """The views that decisions read while the finality check is armed: reads of render facts are reported.

    A view stands for one model, field, data type or reference: it reads through to the object, gives the graph
    objects it reaches as views and their lists as tuples, and has no attribute to write. Reading a render fact
    (``_reads_render_fact``) is reported as ``render-read``, also when it is a key of a dict attribute or of
    ``__dict__``. What the methods and properties of the classes read inside is theirs, not the decision's.
    """

    __slots__ = ("_dict_keys", "_finality", "_rows", "_views")

    def __init__(self, finality: _Finality) -> None:
        self._finality = finality
        self._dict_keys = _dict_keys()
        self._rows: dict[type, _RenderRows] = {}
        self._views: dict[int, _GuardedView] = {}

    def view(self, owner: Any) -> Any:
        """Return the one view of ``owner``."""
        if (view := self._views.get(id(owner))) is None:
            view = self._views[id(owner)] = _GuardedView(self, owner)
        return view

    def read(self, owner: object, name: str) -> Any:
        """Return attribute ``name`` of ``owner`` as a view reads it, reporting a render fact."""
        if (rows := self._rows.get(type(owner))) is None:
            rows = self._rows[type(owner)] = _RenderRows.of(type(owner), self._dict_keys)
        if (row := rows.attributes.get(name)) is not None:
            self._finality.read_render_fact(owner, row)
        value = getattr(owner, name)
        if (keys := rows.items.get(name)) is not None:
            return _GuardedItems(self, owner, value, keys)
        return self.wrap(value)

    def read_item(self, owner: object, row: AttributeFact | None) -> None:
        """Report reading a key of a dict attribute of ``owner`` that ``row`` names as a render fact."""
        if row is not None:
            self._finality.read_render_fact(owner, row)

    def wrap(self, value: Any) -> Any:
        """Return ``value`` as a view reads it: graph objects as views, lists and tuples as tuples of what they hold."""
        if isinstance(value, _GRAPH_CLASSES):
            return self.view(value)
        if isinstance(value, (list, tuple)):
            return tuple(map(self.wrap, value))
        return value


@final
class _GuardedView(GuardedView):
    """A view of one graph object that ``_ReadGuard`` gives a decision."""

    __slots__ = ("_guard",)

    def __init__(self, guard: _ReadGuard, owner: object) -> None:
        self._guard = guard
        self._object = owner

    def __getattr__(self, name: str) -> Any:
        """Read attribute ``name`` of the object."""
        return self._guard.read(self._object, name)

    @override
    def __eq__(self, other: object) -> bool:
        """Compare the object with ``other``, or with the object ``other`` views."""
        return self._object == (other._object if isinstance(other, GuardedView) else other)

    @override
    def __hash__(self) -> int:
        """Hash as the object."""
        return hash(self._object)


@final
class _GuardedItems(Mapping[str, Any]):
    """A dict attribute of a graph object, or its ``__dict__``, as a view reads it."""

    __slots__ = ("_guard", "_items", "_owner", "_rows")

    def __init__(
        self, guard: _ReadGuard, owner: object, items: Mapping[str, Any], rows: dict[str, AttributeFact]
    ) -> None:
        self._guard = guard
        self._owner = owner
        self._items = items
        self._rows = rows

    @override
    def __getitem__(self, key: str) -> Any:
        self._guard.read_item(self._owner, self._rows.get(key))
        return self._guard.wrap(self._items[key])

    def _report_keys(self) -> None:
        """Report every key present that ``self._rows`` names as a render fact, as reading it does."""
        for key in self._items:
            if (row := self._rows.get(key)) is not None:
                self._guard.read_item(self._owner, row)

    @override
    def __iter__(self) -> Iterator[str]:
        self._report_keys()
        return iter(self._items)

    @override
    def __len__(self) -> int:
        self._report_keys()
        return len(self._items)


@final
@dataclass(slots=True)
class _ParseChecks(ParseChecksPort):
    """The debug checks of one parse: the store write guard and the finality check, whichever areas are on."""

    parser: object
    error: type[Exception]
    """The error a violation raises when the mode raises."""
    writes: CheckMode | None
    """How the store write guard handles a write outside store operations, or None when the area is off."""
    finality_mode: CheckMode | None
    finality: _Finality | None = None
    scopes: list[tuple[str, Phase]] = field(default_factory=list)
    """The pass scopes of the pipeline open now, innermost last."""
    operations: list[str] = field(default_factory=list)
    """The store operations running now, innermost last."""
    _store: GenerationStore | None = None
    _reported: set[tuple[str, int, str, str, str | None]] = field(default_factory=set)
    _attributes: dict[type, tuple[frozenset[str], frozenset[str]]] = field(default_factory=dict)

    @property
    @override
    def armed(self) -> bool:
        """Whether L1 is over, so that writes are watched."""
        return self._store is not None

    @override
    def scope(self, name: str, phase: Phase) -> AbstractContextManager[None]:
        """Name the scope and phase that guarded writes inside are reported with."""
        return _scope(self, name, phase)

    @override
    def fact_pass(self, decl: PassDecl, models: Sequence[DataModel] | None) -> AbstractContextManager[None]:
        """Run the pass ``decl`` declares inside the finality check; a no-op when that area is off."""
        return NO_SCOPE if (finality := self.finality) is None else finality.pass_scope(decl, models)

    @override
    def module_checkpoint(self, models: list[DataModel]) -> AbstractContextManager[None]:
        """Process the module whose models are ``models`` inside the finality check; a no-op when that area is off."""
        return NO_SCOPE if (finality := self.finality) is None else finality.module_scope(models)

    @override
    def decision_views(self) -> Callable[[DataModel], ModelView] | None:
        """Return how ``FinalFacts`` gives a decision each model while the finality check is armed, else None.

        The views report the render facts a decision reads (``_ReadGuard``).
        """
        return None if (finality := self.finality) is None else _ReadGuard(finality).view

    @override
    def record_ordering_inputs(
        self, model: DataModel, bases: Iterable[DataModel], stand_ins: Mapping[int, Reference | None]
    ) -> None:
        """Record the fields constructor ordering reads from ``bases`` for ``model``, when the finality check is on."""
        if (finality := self.finality) is not None:
            finality.record_ordering(model, bases, stand_ins)

    @override
    def arm(self, store: GenerationStore, scope: str) -> None:
        """Guard the classes, the lists of the parsed graph and the store operations, in ``scope`` of SETTLE."""
        self._store = store
        self.scopes.append((scope, Phase.SETTLE))
        _CLASS_GUARDS.install()
        for name in sorted(_STORE_OPERATIONS):
            setattr(store, name, _store_operation(self, name, getattr(store, name)))
        lists: dict[int, _GuardedList] = {}
        for owner in _graph_objects(store.models):
            self._guard_lists(owner, lists)
        if (mode := self.finality_mode) is not None:
            finality = self.finality = _Finality(
                mode,
                self.error,
                store,
                self._guard_lists,
                sweep=check_mode(CheckArea.FACT_FINALITY_SWEEP) is not None,
            )
            enter_phase = store.enter_phase
            note_changed_models = store.note_changed_models  # ty: ignore[deprecated]

            def note_checked_models(*models: object) -> None:
                finality.mark(models)
                note_changed_models(*models)  # ty: ignore[deprecated]

            def enter_checked_phase(phase: Phase) -> None:
                finality.leave_phase()
                enter_phase(phase)
                if phase is Phase.IMPORTS:
                    finality.check_ordering_reads()

            store.__dict__["enter_phase"] = enter_checked_phase
            store.__dict__["note_changed_models"] = note_checked_models

    def disarm(self) -> None:
        """Give the classes and the store back their own behaviour."""
        if (store := self._store) is None:
            return
        self._store = None
        self.finality = None
        for name in (*_STORE_OPERATIONS, "enter_phase", "note_changed_models"):
            store.__dict__.pop(name, None)
        _CLASS_GUARDS.uninstall()

    def _guarded(self, cls: type) -> tuple[frozenset[str], frozenset[str]]:
        """Return the guarded attributes of ``cls`` and its container attributes, from the rows of its classes."""
        if (guarded := self._attributes.get(cls)) is None:
            rows = [
                _ROWS_BY_OWNER[base.__name__]
                for base in cls.__mro__
                if base.__name__ in _ROWS_BY_OWNER and base.__module__.startswith(f"{_PACKAGE}.")
            ]
            guarded = self._attributes[cls] = (
                frozenset().union(*(names for names, _ in rows)),
                frozenset().union(*(containers for _, containers in rows)),
            )
        return guarded

    def _guard_lists(self, owner: object, lists: dict[int, _GuardedList]) -> None:
        """Put the container attributes of ``owner`` that hold plain lists into guarded lists, keeping them shared."""
        values = owner.__dict__
        for name in self._guarded(type(owner))[1]:
            if type(value := values.get(name)) is list:
                if (guarded := lists.get(id(value))) is None:
                    guarded = lists[id(value)] = _GuardedList.of(value, owner, name)
                values[name] = guarded

    def on_setattr(self, owner: object, name: str, value: Any, frame: FrameType) -> Any:
        """Watch a guarded write; a list a store operation sets becomes a guarded list."""
        attributes, containers = self._guarded(type(owner))
        if name not in attributes:
            return value
        if (finality := self.finality) is not None:
            finality.touch(owner)
            finality.judge_write(owner, name, value)
        if self.operations:
            if name in containers and type(value) is list:
                return _GuardedList.of(value, owner, name)
            return value
        if (mode := self.writes) is not None and not _constructing(owner, frame):
            self.report_write(mode, type(owner).__name__, name, "=", None, frame)
        return value

    def on_list_write(self, guarded: _GuardedList, operation: str, args: tuple[Any, ...]) -> tuple[Any, ...] | None:
        """Watch a mutation of a guarded list by the caller of its mutator; return the items to judge it against."""
        before = None
        if (finality := self.finality) is not None and (holder := guarded._holder()) is not None:  # noqa: SLF001
            finality.touch(holder)
            before = tuple(guarded)
        if (mode := self.writes) is not None and not self.operations:
            self.report_write(
                mode,
                guarded._owner,  # noqa: SLF001
                guarded._attribute,  # noqa: SLF001
                operation,
                _key_kind(operation, args),
                sys._getframe(2),  # noqa: SLF001
            )
        return before

    def judge_list_write(self, guarded: _GuardedList, before: tuple[Any, ...] | None) -> None:
        """Judge a mutation of a guarded list that changed its items; one that left them as they were writes nothing."""
        if (
            before is not None
            and (finality := self.finality) is not None
            and (holder := guarded._holder()) is not None  # noqa: SLF001
            and (len(before) != len(guarded) or any(old is not new for old, new in zip(before, guarded, strict=True)))
        ):
            finality.judge_write(holder, guarded._attribute)  # noqa: SLF001

    def report_write(  # noqa: PLR0913, PLR0917
        self, mode: CheckMode, owner: str, attribute: str, operation: str, key: str | None, frame: FrameType
    ) -> None:
        """Raise for a write outside store operations, or report it once per writing line and parse."""
        filename, line = frame.f_code.co_filename, frame.f_lineno
        if (seen := (filename, line, attribute, operation, key)) in self._reported:
            return
        self._reported.add(seen)
        scope, phase = self.scopes[-1]
        path = _source_path(filename)
        function = _qualified_name(frame)
        caller = next(
            (_qualified_name(item) for item in _stack(frame) if item.f_locals.get("self") is self.parser), None
        )
        written = f"{owner}.{attribute}" if operation == "=" else f"{owner}.{attribute}.{operation}()"
        mode.violation(
            self.error,
            f"{written} is written outside store operations by {function} ({path}:{line}), "
            f"in {caller or scope} during {phase.name}",
            owner=owner,
            attribute=attribute,
            operation=operation,
            key=key,
            path=path,
            line=line,
            function=function,
            caller=caller,
            scope=scope,
            phase=phase.name,
        )


def _constructing(owner: object, frame: FrameType) -> bool:
    """Return whether the write comes from the construction of ``owner``: its ``__init__`` and what that calls."""
    frames = takewhile(lambda item: item.f_locals.get("self") is owner, _stack(frame))
    return any(item.f_code.co_name in _CONSTRUCTORS for item in frames)


def _graph_objects(models: Iterable[Any]) -> Iterator[object]:
    """Yield every model, field and data type that the parsed models hold, each once."""
    seen: set[int] = set()
    pending: list[Any] = list(models)
    while pending:
        if id(item := pending.pop()) in seen:
            continue
        seen.add(id(item))
        yield item
        if (fields_ := getattr(item, "fields", None)) is not None:
            pending.extend(fields_)
            pending.extend(item.base_classes)
        elif (data_type := getattr(item, "data_type", None)) is not None:
            pending.append(data_type)
        else:
            pending.extend(item.data_types)
            if (dict_key := item.dict_key) is not None:
                pending.append(dict_key)


def _store_operation(checks: _ParseChecks, name: str, operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def run(*args: Any, **kwargs: Any) -> Any:
        checks.operations.append(name)
        try:
            return operation(*args, **kwargs)
        finally:
            checks.operations.pop()

    return run


@contextmanager
def parse_checks(parser: object, error: type[Exception]) -> Generator[None, None, None]:
    """Open the checks of one parse of ``parser``; ``Parser.parse`` calls it when an area that watches writes is on.

    A violation raises ``error`` when the mode raises.
    """
    checks = _ParseChecks(parser, error, check_mode(CheckArea.STORE_WRITES), check_mode(CheckArea.FACT_FINALITY))
    token = PARSE_CHECKS.set(checks)
    try:
        yield
    finally:
        PARSE_CHECKS.reset(token)
        checks.disarm()


@contextmanager
def _scope(checks: _ParseChecks, name: str, phase: Phase) -> Generator[None, None, None]:
    checks.scopes.append((name, phase))
    try:
        yield
    finally:
        checks.scopes.pop()
