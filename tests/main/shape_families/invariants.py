"""Payload-free invariants of the shape families' generated modules, beside the static tier that ty is.

Each check reads only the files of a generated module, as a type checker does, and reports diagnostics as
``(file, line, rule)`` (``strict_typing.Found``, the form of ty's errors): ``invariant_diagnostics`` imports each module
and reads the document it was generated from (``schema.json`` beside it), and gives each diagnostic one of ``KINDS``
followed by its detail (which names the class) as the rule and line 0; ty, the static tier, names its own rules.

``import-fails``
    the module does not import;
``build-fails``
    a class the module defines, or a definition it binds (an alias among them), has no validator: a
    ``msgspec.json.Decoder`` for msgspec output, a Pydantic ``TypeAdapter`` for every other output (it validates
    TypedDict and standard dataclasses too);
``duplicate-wire-key``
    two fields of the class of a definition read the same wire key: the field's alias, or its name where it has none;
``lost-property``
    the class of a definition reads no wire key for a property the definition declares, directly, through its ``allOf``
    members or through references;
``unknown-wire-key``
    the class of a definition reads a wire key that none of those properties names;
``any-type``
    a value of a definition meets ``Any``: the definition's alias, or a field of a class that a definition's value can
    be (the definition's class, the classes its fields and members name, and so on), is typed ``Any`` where it inherits
    or declares it, although every schema of the families types its values. A class that is only a base of others is
    no value and is not read; its fields count in the classes that inherit them.

The class of a definition is the class the module defines under the definition's name. A definition that reaches a
union (``anyOf``, ``oneOf``) through its ``allOf`` members or references has variants instead, and a root model holds
one value instead of fields: neither is compared by its wire keys, nor is a definition the module writes as an alias or
an enum.
"""

from __future__ import annotations

import contextlib
import dataclasses
import hashlib
import json
from typing import TYPE_CHECKING, Any, Final, Literal, get_args, get_origin, get_type_hints, is_typeddict

import msgspec
from pydantic import BaseModel, PydanticUserError, RootModel, TypeAdapter

from tests.main.conftest import _generated_model

from .families import REF

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator, Mapping
    from pathlib import Path

    from .families import Schema

IMPORT_FAILS: Final = "import-fails"
BUILD_FAILS: Final = "build-fails"
DUPLICATE_WIRE_KEY: Final = "duplicate-wire-key"
LOST_PROPERTY: Final = "lost-property"
UNKNOWN_WIRE_KEY: Final = "unknown-wire-key"
ANY_TYPE: Final = "any-type"
KINDS: Final = (IMPORT_FAILS, BUILD_FAILS, DUPLICATE_WIRE_KEY, LOST_PROPERTY, UNKNOWN_WIRE_KEY, ANY_TYPE)
"""Every way a generated module can break an invariant."""

BUILD_ERRORS: Final = (PydanticUserError, NameError, TypeError, ValueError)
"""What building a validator of a broken type raises: Pydantic's user errors (an undefined annotation is a NameError),
msgspec's TypeErrors."""
IMPORT_ERRORS: Final = (ImportError, SyntaxError, *BUILD_ERRORS)
"""What importing a broken module raises: its own errors, or those of building a model's validator at class creation."""
SOURCE: Final = "schema.json"
"""The name of the document beside each generated module."""

Diagnostic = tuple[str, int, str]
"""A file relative to the checked root, a line of it (0 where the rule's detail names the class), and a rule."""


def declared_properties(definitions: Mapping[str, Schema], schema: Schema) -> frozenset[str] | None:
    """Return the property names an object of a schema declares through ``allOf`` and references, or None at a union."""
    if "anyOf" in schema or "oneOf" in schema:
        return None
    parts = [*schema.get("allOf", ())]
    if isinstance(reference := schema.get("$ref"), str):
        parts.append(definitions[reference.removeprefix(REF)])
    names = set(schema.get("properties", ()))
    for part in parts:
        if (more := declared_properties(definitions, part)) is None:
            return None
        names |= more
    return frozenset(names)


def fields(value: object) -> dict[str, object] | None:
    """Return the type of each field of a generated class by its wire key, or None for an enum or an alias.

    A field's wire key is its alias, or its name where it has none. A root model has the one field ``root``.
    """
    if not isinstance(value, type):
        return None
    if issubclass(value, BaseModel):
        return {info.alias or name: info.annotation for name, info in value.model_fields.items()}
    if issubclass(value, msgspec.Struct):
        return {field.encode_name: field.type for field in msgspec.structs.fields(value)}
    if is_typeddict(value) or dataclasses.is_dataclass(value):
        return get_type_hints(value)
    return None


def wire_keys(value: object) -> list[str] | None:
    """Return the wire key of each field of the class of a definition, repeats kept, or None where none is compared.

    A msgspec Struct's tag field is a wire key too.
    """
    if not isinstance(value, type) or issubclass(value, RootModel):
        return None
    if issubclass(value, BaseModel):
        return [info.alias or name for name, info in value.model_fields.items()]
    if issubclass(value, msgspec.Struct) and (tag := value.__struct_config__.tag_field):
        return [tag, *(field.encode_name for field in msgspec.structs.fields(value))]
    return None if (found := fields(value)) is None else list(found)


def _named(annotation: object) -> Iterator[object]:
    """Yield an annotation and every type it names through its arguments, a ``Literal``'s values aside."""
    yield annotation
    if get_origin(annotation) is not Literal:
        for argument in get_args(annotation):
            yield from _named(argument)


def _any_diagnostics(
    file: str, namespace: Mapping[str, object], definitions: Mapping[str, Schema]
) -> Iterator[Diagnostic]:
    """Yield a diagnostic for each ``Any`` that a value of a definition meets (see ``any-type``)."""
    module = namespace["__name__"]
    seen: set[int] = set()
    queue = [(name, namespace[name]) for name in definitions if name in namespace]
    while queue:
        name, value = queue.pop(0)
        if id(value) in seen:
            continue
        seen.add(id(value))
        typed = fields(value)
        annotations = {name: value} if typed is None else {f"{name}.{key}": item for key, item in typed.items()}
        for place, annotation in annotations.items():
            named = list(_named(annotation))
            if any(item is Any for item in named):
                yield file, 0, f"{ANY_TYPE}: {place}"
            queue += [
                (item.__name__, item)
                for item in named
                if isinstance(item, type) and item is not value and item.__module__ == module
            ]


def _defined(namespace: Mapping[str, object], definitions: Mapping[str, Schema]) -> list[str]:
    """Return the names of the classes a module defines and of the definitions it binds, aliases among them."""
    module = namespace["__name__"]
    return [
        name
        for name, value in namespace.items()
        if name in definitions or (isinstance(value, type) and value.__module__ == module)
    ]


def _builder(namespace: Mapping[str, object]) -> Callable[[object], object]:
    """Return what builds a validator of a generated type: msgspec's decoder for msgspec output, else Pydantic's."""
    return msgspec.json.Decoder if namespace.get("Struct") is msgspec.Struct else TypeAdapter


def _build_error(build: Callable[[object], object], value: object) -> str | None:
    """Return the name of the error that building a validator of a value raises, or None where it builds."""
    try:
        build(value)
    except BUILD_ERRORS as error:
        return type(error).__name__
    return None


def _build_diagnostics(
    file: str, namespace: Mapping[str, object], definitions: Mapping[str, Schema]
) -> Iterator[Diagnostic]:
    """Yield a diagnostic for each class of the module, or definition it binds, whose validator cannot be built."""
    build = _builder(namespace)
    for name in _defined(namespace, definitions):
        if (error := _build_error(build, namespace[name])) is not None:
            yield file, 0, f"{BUILD_FAILS}: {name}: {error}"


def _wire_key_diagnostics(
    file: str, namespace: Mapping[str, object], definitions: Mapping[str, Schema]
) -> Iterator[Diagnostic]:
    """Yield a diagnostic for each duplicate, lost or unknown wire key of the class of a definition."""
    for name, schema in definitions.items():
        keys = wire_keys(namespace.get(name))
        declared = declared_properties(definitions, schema)
        if keys is None or declared is None:
            continue
        breaks = {
            DUPLICATE_WIRE_KEY: {key for key in keys if keys.count(key) > 1},
            LOST_PROPERTY: declared - set(keys),
            UNKNOWN_WIRE_KEY: set(keys) - declared,
        }
        yield from ((file, 0, f"{kind}: {name}: {', '.join(sorted(found))}") for kind, found in breaks.items() if found)


def module_diagnostics(root: Path, target: str) -> list[Diagnostic]:
    """Return the diagnostics of one generated module, a path relative to ``root`` beside its ``schema.json``."""
    path = root / target
    definitions = json.loads(path.with_name(SOURCE).read_text(encoding="utf-8"))["$defs"]
    module_name = "shape_family_invariants_" + hashlib.sha256(str(path).encode()).hexdigest()[:16]
    with contextlib.ExitStack() as stack:
        try:
            namespace = stack.enter_context(_generated_model(path, module_name, "__dict__"))
        except IMPORT_ERRORS as error:
            return [(target, 0, f"{IMPORT_FAILS}: {type(error).__name__}")]
        found = [*_build_diagnostics(target, namespace, definitions)]
        found += _wire_key_diagnostics(target, namespace, definitions)
        found += _any_diagnostics(target, namespace, definitions)
    return found


def invariant_diagnostics(root: Path, targets: list[str]) -> list[Diagnostic]:
    """Return the diagnostics of the generated modules ``targets`` (paths relative to ``root``), like a checker."""
    return [diagnostic for target in targets for diagnostic in module_diagnostics(root, target)]
