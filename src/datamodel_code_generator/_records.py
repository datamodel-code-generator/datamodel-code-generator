"""The decorator of the generator's internal records: frozen for the type checker, not guarded at run time.

A record is immutable by a convention that ``ty`` enforces (every assignment to a field is an error), so the run time
does not pay the ``__setattr__`` guard that ``@dataclass(frozen=True)`` adds to each ``__init__``. Equality, hashing,
``replace``, ``fields`` and ``__post_init__`` behave as for a frozen dataclass.

``dataclass`` generates and compiles the source of every method of every class. A record of plain fields is built
without it: declared under ``from __future__ import annotations``, each field with a hashable default, only a
``field(default_factory=...)`` or neither, and no base record, ``ClassVar``, ``__post_init__``, own ``__init__``,
``__eq__`` or ``__hash__`` or method that calls ``super()``. It gets one ``__init__`` from a small template, compiled
once per sequence of required, defaulted and factory fields and renamed to the record's fields, and
``__repr__``, ``__eq__`` and ``__hash__`` from closures over the names of its fields, which compare and hash the tuple
of fields that ``dataclass`` hashes, so hash values stay the same; its fields are ``dataclasses.Field`` objects marked
as fields (the private ``dataclasses._FIELD``), which ``fields``, ``replace`` and ``asdict`` read, and its
``__dataclass_params__`` are those ``dataclass`` gives, which a ``dataclass`` that extends it reads. ``dataclass``
builds any other record. Only internal classes are records: a class of the public API keeps ``dataclass``.
"""

from __future__ import annotations

import dataclasses
import re
from dataclasses import MISSING, Field, dataclass, field
from functools import cache
from operator import attrgetter
from reprlib import recursive_repr
from types import CodeType, FunctionType
from typing import TYPE_CHECKING, Any, Final, Literal, TypeAlias, TypeVar, overload

from typing_extensions import dataclass_transform

if TYPE_CHECKING:
    from collections.abc import Callable

_T = TypeVar("_T")
_FieldKind: TypeAlias = Literal["required", "default", "factory"]

_FIELD: Final = dataclasses._FIELD  # noqa: SLF001 # ty: ignore[unresolved-attribute]
_PSEUDO_FIELD: Final = re.compile(r"\s*(?:typing\.|dataclasses\.)?(?:ClassVar|InitVar|KW_ONLY)\b")
_OWN_METHODS: Final = frozenset({"__init__", "__eq__", "__hash__"})
_FACTORY_ONLY: Final = (MISSING, True, True, None, True, MISSING)
"""``default``, ``init``, ``repr``, ``hash``, ``compare`` and ``kw_only`` of a ``field()`` that sets a factory only."""


_HAS_FACTORY: Final = dataclasses._HAS_DEFAULT_FACTORY  # noqa: SLF001 # ty: ignore[unresolved-attribute]
"""The default of a parameter whose field has a default factory: the one ``dataclass`` writes, shown ``<factory>``."""
_PARAMETERS: Final = {
    (eq, slots): dataclass(type("Parameters", (), {}), slots=slots, eq=eq, unsafe_hash=eq).__dataclass_params__
    for eq in (True, False)
    for slots in (True, False)
}
"""The ``__dataclass_params__`` of a record by ``eq`` and ``slots``, which ``dataclass`` reads from a base record."""


@overload
def record(cls: type[_T], /) -> type[_T]:  # noqa: D418 - CodeQL flags an ellipsis body.
    """Declare ``cls`` a record."""


@overload
def record(*, eq: bool = True, slots: bool = True, public: bool = False) -> Callable[[type[_T]], type[_T]]:  # noqa: D418
    """Return the decorator that declares a class a record with these options."""


@dataclass_transform(frozen_default=True, field_specifiers=(field,))
def record(cls: type[Any] | None = None, /, *, eq: bool = True, slots: bool = True, public: bool = False) -> Any:
    """Declare ``cls`` a record: a dataclass the type checker treats as frozen, with value-based hash when ``eq``.

    A ``public`` record, a class of the public API, keeps the methods ``dataclass`` generates.
    """

    def wrap(target: type[_T]) -> type[_T]:
        plain = not public and _plain(target)
        return _build(target, eq=eq, slots=slots) if plain else dataclass(target, slots=slots, eq=eq, unsafe_hash=eq)

    return wrap if cls is None else wrap(cls)


def _names_class(member: object) -> bool:
    """Return whether a method names the class it is defined in (``super()``), which a rebuilt class must rebind."""
    function = getattr(member, "fget", None) or getattr(member, "__func__", member)
    return "__class__" in getattr(getattr(function, "__code__", None), "co_freevars", ())


def _plain_default(default: object) -> bool:
    """Return whether a field's default is one the template writes: none, a hashable value or only a factory."""
    if isinstance(default, Field):
        settings = (default.default, default.init, default.repr, default.hash, default.compare, default.kw_only)
        return settings == _FACTORY_ONLY and default.default_factory is not MISSING
    return default.__class__.__hash__ is not None


def _plain(cls: type[Any]) -> bool:
    """Return whether ``cls`` is a record of plain fields, which the template builds (see the module docstring)."""
    namespace = cls.__dict__
    annotations = namespace.get("__annotations__", {})
    return (
        bool(annotations)
        and all(isinstance(text, str) and not _PSEUDO_FIELD.match(text) for text in annotations.values())
        and all(_plain_default(namespace.get(name, MISSING)) for name in annotations)
        and not any("__dataclass_fields__" in base.__dict__ for base in cls.__mro__[1:])
        and _OWN_METHODS.isdisjoint(namespace)
        and not hasattr(cls, "__post_init__")
        and not any(map(_names_class, namespace.values()))
    )


@cache
def _init_template(kinds: tuple[_FieldKind, ...]) -> CodeType:
    """Return the code of the ``__init__`` of fields of ``kinds``, its parameters and attributes named ``_p<index>``.

    Records share the compiled code of one shape; ``_init`` gives each its own names with ``CodeType.replace``.
    """
    namespace: dict[str, Any] = {"_HAS_FACTORY": _HAS_FACTORY}
    parameters: list[str] = []
    body: list[str] = []
    for index, kind in enumerate(kinds):
        name, default = f"_p{index}", f"_d{index}"
        match kind:
            case "default":
                namespace[default] = None
                parameters.append(f"{name}={default}")
                body.append(f" self.{name} = {name}")
            case "factory":
                parameters.append(f"{name}=_HAS_FACTORY")
                body.append(f" self.{name} = {default}() if {name} is _HAS_FACTORY else {name}")
            case _:
                parameters.append(name)
                body.append(f" self.{name} = {name}")
    exec(f"def __init__(self, {', '.join(parameters)}):\n" + "\n".join(body), namespace)  # noqa: S102
    return namespace["__init__"].__code__


def _kind(spec: Field[Any]) -> _FieldKind:
    """Return whether the field ``spec`` is required, has a default or has a default factory."""
    if spec.default is not MISSING:
        return "default"
    return "factory" if spec.default_factory is not MISSING else "required"


def _init(qualname: str, specs: tuple[Field[Any], ...]) -> Callable[..., None]:
    """Return the ``__init__`` of the fields ``specs``, as ``dataclass`` writes it for a class that is not frozen."""
    kinds = tuple(map(_kind, specs))
    template = _init_template(kinds)
    names = {f"_p{index}": spec.name for index, spec in enumerate(specs)}
    namespace: dict[str, Any] = {"_HAS_FACTORY": _HAS_FACTORY}
    defaults: list[object] = []
    for index, (spec, kind) in enumerate(zip(specs, kinds, strict=True)):
        match kind:
            case "default":
                defaults.append(spec.default)
            case "factory":
                namespace[f"_d{index}"] = spec.default_factory
                defaults.append(_HAS_FACTORY)
    code = template.replace(
        co_varnames=("self", *names.values()), co_names=tuple(names.get(name, name) for name in template.co_names)
    )
    init = FunctionType(code, namespace, "__init__", tuple(defaults) or None)
    init.__qualname__ = f"{qualname}.__init__"
    init.__annotations__ = {**{spec.name: spec.type for spec in specs}, "return": None}
    return init


def _methods(qualname: str, names: tuple[str, ...], *, eq: bool) -> dict[str, Callable[..., Any]]:
    """Return ``__repr__`` and, with ``eq``, ``__eq__`` and ``__hash__`` over the tuple of the fields ``names``."""
    getter = attrgetter(*names)
    values = (lambda instance: (getter(instance),)) if len(names) == 1 else getter
    shown = "{}={!r}".format
    __repr__ = recursive_repr()(
        lambda self: f"{self.__class__.__qualname__}({', '.join(map(shown, names, values(self)))})"
    )

    def __eq__(self: Any, other: object) -> Any:  # noqa: N807
        return values(self) == values(other) if other.__class__ is self.__class__ else NotImplemented

    def __hash__(self: Any) -> int:  # noqa: N807
        return hash(values(self))

    methods: dict[str, Any] = {"__repr__": __repr__, "__eq__": __eq__, "__hash__": __hash__}
    methods = methods if eq else {"__repr__": __repr__}
    for name, method in methods.items():
        method.__qualname__ = f"{qualname}.{name}"
    return methods


def _build(cls: type[Any], *, eq: bool, slots: bool) -> type[Any]:
    """Return the record class of ``cls``, a class of plain fields."""
    fields_by_name: dict[str, Field[Any]] = {}
    for name, annotation in cls.__dict__["__annotations__"].items():  # noqa: RUF063 - the strings the class declares
        default = cls.__dict__.get(name, MISSING)
        spec = default if isinstance(default, Field) else field(default=default)
        spec.name, spec.type, spec.kw_only, spec._field_type = name, annotation, False, _FIELD  # noqa: SLF001
        fields_by_name[name] = spec
        if spec is default:
            delattr(cls, name)
    qualname = cls.__qualname__
    generated = {
        "__init__": _init(qualname, tuple(fields_by_name.values())),
        "__match_args__": tuple(fields_by_name),
        **_methods(qualname, tuple(fields_by_name), eq=eq),
    }
    attributes = {
        **{name: value for name, value in generated.items() if name not in cls.__dict__},
        "__dataclass_fields__": fields_by_name,
        "__dataclass_params__": _PARAMETERS[eq, slots],
    }
    if not slots:
        for name, value in attributes.items():
            setattr(cls, name, value)
        return cls
    inherited_slots = {slot for base in cls.__mro__[1:-1] for slot in base.__dict__.get("__slots__", ())}
    namespace = {
        **{name: value for name, value in cls.__dict__.items() if name not in fields_by_name},
        **attributes,
        "__slots__": tuple(name for name in fields_by_name if name not in inherited_slots),
    }
    namespace.pop("__dict__", None)
    namespace.pop("__weakref__", None)
    built = type(cls)(cls.__name__, cls.__bases__, namespace)
    built.__qualname__ = qualname
    return built
