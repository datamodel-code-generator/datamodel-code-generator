"""Guard writes of model facts outside store operations when ``DATAMODEL_CODE_GENERATOR_CHECK_STORE_WRITES`` is on.

After L1, a data type, a field or a model may change a fact or presentation attribute of ``ATTRIBUTE_FACTS`` only
while a ``GenerationStore`` operation that some row names in ``store_ops`` runs. Any such operation accepts any guarded
write, and the phase is not checked; which operation and which phase may write which attribute is EN5's check. With
the area on, ``Parser.parse`` opens a guard (``store_write_guard``) and arms it at the end of L1
(``arm_store_write_guard``): ``DataType``, ``DataModelFieldBase`` and ``DataModel`` get a ``__setattr__`` guard (they
inherit theirs, so leaving deletes it again), and each store operation of the parser's store runs in a write scope. The
drivers of ``parser/base.py`` open pass scopes (``pass_scope``, inert unless a guard is armed). A write outside every
store operation is reported with the line that wrote it, the parser method it ran in and the pass scope and phase,
once per line and parse; the construction of an object (its ``__init__`` and what that calls) writes freely. With the
area off, nothing is installed.

In check mode the list attributes of the parsed graph, and the lists store operations set, are replaced by
``_GuardedList`` copies: a list keeps being shared by the objects that shared it, but not with other references to the
original list, and ``type(x) is list`` is false for it. The copies outlive the parse; once no guard is armed they act as
plain lists, and they copy and pickle as plain lists.

Writes through ``object.__setattr__``, through ``__dict__``, into dict attributes, and into lists that an object got
outside a store operation after arming are not seen.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import wraps
from itertools import takewhile
from pathlib import Path
from threading import Lock
from traceback import walk_stack
from typing import TYPE_CHECKING, Any, Final, SupportsIndex

from typing_extensions import final, override

from datamodel_code_generator._checks import CheckArea, CheckMode, check_mode
from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
from datamodel_code_generator.parser._facts import ATTRIBUTE_FACTS, Access, FactKind, Phase
from datamodel_code_generator.types import DataType

if TYPE_CHECKING:
    from collections.abc import Callable, Generator, Iterable, Iterator
    from contextlib import AbstractContextManager
    from types import FrameType

    from datamodel_code_generator.parser.generation import GenerationStore

__all__ = ["arm_store_write_guard", "pass_scope", "store_write_guard"]

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

_GUARD: Final[ContextVar[_StoreWriteGuard | None]] = ContextVar("store_write_guard", default=None)
_NO_SCOPE: Final = nullcontext()


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
    """A list attribute of the parsed graph whose mutators report writes outside store operations."""

    __slots__ = ("_attribute", "_owner")

    _attribute: str
    _owner: str

    @classmethod
    def of(cls, items: Iterable[Any], owner: str, attribute: str) -> _GuardedList:
        """Return a guarded list holding ``items``, as the attribute of an object of class ``owner``."""
        guarded = cls(items)
        guarded._owner = owner
        guarded._attribute = attribute
        return guarded

    @override
    def __reduce_ex__(self, protocol: SupportsIndex, /) -> tuple[type[list[Any]], tuple[list[Any]]]:
        """Copy and pickle as a plain list: a copy of a guarded object is a new object that nothing guards."""
        return list, (list(self),)


def _guarded_mutator(name: str) -> Callable[..., Any]:
    mutate = getattr(list, name)

    def mutator(self: _GuardedList, *args: Any, **kwargs: Any) -> Any:
        if (guard := _GUARD.get()) is not None and guard.checks_writes:
            guard.report_write(
                self._owner,
                self._attribute,
                name,
                _key_kind(name, args),
                sys._getframe(1),  # noqa: SLF001
            )
        return mutate(self, *args, **kwargs)

    mutator.__name__ = name
    return mutator


for _name in _LIST_MUTATORS:
    setattr(_GuardedList, _name, _guarded_mutator(_name))


def _guarded_setattr(original: Callable[[Any, str, Any], None]) -> Callable[[Any, str, Any], None]:
    def __setattr__(self: Any, name: str, value: Any) -> None:  # noqa: N807
        if (guard := _GUARD.get()) is not None and guard.armed:
            value = guard.on_setattr(self, name, value, sys._getframe(1))  # noqa: SLF001
        original(self, name, value)

    return __setattr__


@final
class _ClassGuards:
    """The ``__setattr__`` guards of the graph classes, installed while at least one parse has an armed guard."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._armed = 0

    def install(self) -> None:
        """Count one more armed guard; the first one installs the guards."""
        with self._lock:
            self._armed += 1
            if self._armed == 1:
                for cls in _GUARDED_CLASSES:
                    cls.__setattr__ = _guarded_setattr(cls.__setattr__)  # ty: ignore[invalid-assignment]

    def uninstall(self) -> None:
        """Count one armed guard less; the last one gives the classes their inherited ``__setattr__`` back."""
        with self._lock:
            self._armed -= 1
            if not self._armed:
                for cls in _GUARDED_CLASSES:
                    del cls.__setattr__


_CLASS_GUARDS: Final = _ClassGuards()


@final
@dataclass(slots=True)
class _StoreWriteGuard:
    """The write guard of one parse."""

    mode: CheckMode
    parser: object
    error: type[Exception]
    """The error a violation raises when the mode raises."""
    scopes: list[tuple[str, Phase]] = field(default_factory=list)
    """The pass scopes open now, innermost last."""
    operations: list[str] = field(default_factory=list)
    """The store operations running now, innermost last."""
    _store: GenerationStore | None = None
    _reported: set[tuple[str, int, str, str, str | None]] = field(default_factory=set)
    _attributes: dict[type, tuple[frozenset[str], frozenset[str]]] = field(default_factory=dict)

    @property
    def armed(self) -> bool:
        """Return whether L1 is over, so that writes are checked."""
        return self._store is not None

    @property
    def checks_writes(self) -> bool:
        """Return whether a write now happens after L1 and outside every store operation."""
        return self._store is not None and not self.operations

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

    def disarm(self) -> None:
        """Give the classes and the store back their own behaviour."""
        if (store := self._store) is None:
            return
        self._store = None
        for name in _STORE_OPERATIONS:
            del store.__dict__[name]
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
                    guarded = lists[id(value)] = _GuardedList.of(value, type(owner).__name__, name)
                values[name] = guarded

    def on_setattr(self, owner: object, name: str, value: Any, frame: FrameType) -> Any:
        """Report a guarded write outside store operations; a list a store operation sets becomes a guarded list."""
        attributes, containers = self._guarded(type(owner))
        if name not in attributes:
            return value
        if self.operations:
            if name in containers and type(value) is list:
                return _GuardedList.of(value, type(owner).__name__, name)
            return value
        if not _constructing(owner, frame):
            self.report_write(type(owner).__name__, name, "=", None, frame)
        return value

    def report_write(self, owner: str, attribute: str, operation: str, key: str | None, frame: FrameType) -> None:
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
        self.mode.violation(
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
        if (fields := getattr(item, "fields", None)) is not None:
            pending.extend(fields)
            pending.extend(item.base_classes)
        elif (data_type := getattr(item, "data_type", None)) is not None:
            pending.append(data_type)
        else:
            pending.extend(item.data_types)
            if (dict_key := item.dict_key) is not None:
                pending.append(dict_key)


def _store_operation(guard: _StoreWriteGuard, name: str, operation: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(operation)
    def run(*args: Any, **kwargs: Any) -> Any:
        guard.operations.append(name)
        try:
            return operation(*args, **kwargs)
        finally:
            guard.operations.pop()

    return run


@contextmanager
def store_write_guard(parser: object, error: type[Exception]) -> Generator[None, None, None]:
    """Open the write guard of one parse of ``parser`` when ``CHECK_STORE_WRITES`` is on; install nothing when off.

    A violation raises ``error`` when the mode raises.
    """
    if (mode := check_mode(CheckArea.STORE_WRITES)) is None:
        yield
        return
    guard = _StoreWriteGuard(mode, parser, error)
    token = _GUARD.set(guard)
    try:
        yield
    finally:
        _GUARD.reset(token)
        guard.disarm()


def arm_store_write_guard(store: GenerationStore, scope: str) -> None:
    """Arm the guard of the current parse at the end of L1, if one is open; ``scope`` names the SETTLE driver."""
    if (guard := _GUARD.get()) is not None:
        guard.arm(store, scope)


def pass_scope(name: str, phase: Phase) -> AbstractContextManager[None]:
    """Name the pass and phase that writes inside are reported with; a shared no-op without an armed guard."""
    if (guard := _GUARD.get()) is None or not guard.armed:
        return _NO_SCOPE
    return _scope(guard, name, phase)


@contextmanager
def _scope(guard: _StoreWriteGuard, name: str, phase: Phase) -> Generator[None, None, None]:
    guard.scopes.append((name, phase))
    try:
        yield
    finally:
        guard.scopes.pop()
