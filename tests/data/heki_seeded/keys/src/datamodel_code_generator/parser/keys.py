"""Writes of ``__dict__`` keys by a pass, one form per function: keys no row names on typed, untyped and ``Any``
values, a key a fact row names and a bookkeeping key, then every way to reach the namespace (``vars``, ``getattr``, an
alias, a walrus, an annotated and a rebound alias), every write (``del``, ``pop``, ``+=``, a tuple target), the
namespace of each owner and of a subclass, a key constant read from its module, an annotated and a starred target, a
pop through an alias, an alias of an alias, aliases of ``vars`` and ``getattr``, a write in a closure, a schema-object
name rebound by a loop, a call result, a computed key and calls that write no key, mutations of an entry, and the
namespaces of a callable, a schema object and a class no row describes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from datamodel_code_generator import types
from datamodel_code_generator.types import _BOOKKEEPING_KEY, _LISTED_KEY

if TYPE_CHECKING:
    from collections.abc import Callable

    from datamodel_code_generator.model.base import DataModel, DataModelFieldBase
    from datamodel_code_generator.model.enum import Enum
    from datamodel_code_generator.parser.schema import JsonSchemaObject
    from datamodel_code_generator.reference import Reference
    from datamodel_code_generator.types import DataType

_NEW_KEY = "_new_constant"


def literal(data_type: DataType) -> None:
    data_type.__dict__["_new_literal"] = 1


def constant(data_type: DataType) -> None:
    data_type.__dict__.setdefault(_NEW_KEY, 1)


def untyped(data_type) -> None:
    data_type.__dict__["_new_untyped"] = 1


def loop(data_types: list) -> None:
    for data_type in data_types:
        data_type.__dict__["_new_loop"] = 1


def any_value(data_type: Any) -> None:
    data_type.__dict__["_new_any"] = 1


def listed(data_type: DataType) -> None:
    data_type.__dict__[_LISTED_KEY] = 1


def bookkeeping(data_type: DataType) -> None:
    data_type.__dict__[_BOOKKEEPING_KEY] = 1


def through_vars(data_type: DataType) -> None:
    vars(data_type)["_new_vars"] = 1


def through_getattr(data_type: DataType) -> None:
    getattr(data_type, "__dict__")["_new_getattr"] = 1


def through_an_alias(data_type: DataType) -> None:
    namespace = data_type.__dict__
    namespace["_new_alias"] = 1


def through_a_walrus(data_type: DataType) -> None:
    (namespace := data_type.__dict__)["_new_walrus"] = 1
    del namespace


def deleted(data_type: DataType) -> None:
    del data_type.__dict__["_new_deleted"]


def popped(data_type: DataType) -> None:
    data_type.__dict__.pop("_new_popped", None)


def augmented(data_type: DataType) -> None:
    data_type.__dict__["_new_augmented"] += 1


def in_a_tuple(data_type: DataType) -> None:
    first, data_type.__dict__["_new_tuple"] = 1, 2
    del first


def through_an_annotated_alias(data_type: DataType) -> None:
    namespace: dict[str, Any] = data_type.__dict__
    namespace["_new_annotated_alias"] = 1


def through_a_rebound_alias(data_type: DataType, other: DataType) -> None:
    namespace = other.__dict__
    namespace = data_type.__dict__
    namespace["_new_rebound"] = 1


def optional(data_type: DataType | None) -> None:
    if data_type is not None:
        data_type.__dict__["_new_optional"] = 1


def field(field: DataModelFieldBase) -> None:
    field.__dict__["_new_field"] = 1


def model(model: DataModel) -> None:
    model.__dict__["_new_model"] = 1


def subclass(model: Enum) -> None:
    model.__dict__["_new_subclass"] = 1


def model_vars(model: DataModel) -> None:
    vars(model).setdefault("_new_model_vars", 1)


def reference(reference: Reference) -> None:
    reference.__dict__["_new_reference"] = 1


def module_constant(data_type: DataType) -> None:
    data_type.__dict__[types._MODULE_KEY] = 1


def annotated_target(data_type: DataType) -> None:
    data_type.__dict__["_new_annotated"]: int = 1


def starred_target(data_type: DataType, pair: list) -> None:
    [*data_type.__dict__["_new_starred"]] = pair


def alias_popped(data_type: DataType) -> None:
    namespace = data_type.__dict__
    namespace.pop("_new_alias_popped")


def alias_of_an_alias(data_type: DataType) -> None:
    namespace = data_type.__dict__
    copied = namespace
    copied["_new_copied"] = 1


def vars_alias(data_type: DataType) -> None:
    state = vars(data_type)
    state["_new_vars_alias"] = 1


def getattr_alias(data_type: DataType) -> None:
    got = getattr(data_type, "__dict__")
    got["_new_getattr_alias"] = 1


def closure_direct(data_type: DataType):
    def inner() -> None:
        data_type.__dict__["_new_closure_direct"] = 1

    return inner


def rebound_in_a_loop(schema: JsonSchemaObject, items: list) -> None:
    for schema in items:
        schema.__dict__["_new_rebound_loop"] = 1


def built(build) -> None:
    build().__dict__["_new_built"] = 1


def computed(data_type: DataType, name: str) -> None:
    data_type.__dict__[name] = 1


def no_key(data_type: DataType) -> None:
    data_type.__dict__.update({})
    data_type.__dict__.get("_new_read")


def callable_namespace(action: Callable[[], str]) -> None:
    action.__dict__["_marker"] = True


def entry_append(data_type: DataType) -> None:
    data_type.__dict__["_listed"].append(1)


def entry_item(data_type: DataType) -> None:
    data_type.__dict__["_listed"]["x"] = 1


def entry_alias(data_type: DataType) -> None:
    entry = data_type.__dict__["_listed"]
    entry["x"] = 1


def entry_augmented(data_type: DataType) -> None:
    entry = data_type.__dict__["_listed"]
    entry += [1]


def schema_object(schema: JsonSchemaObject) -> None:
    schema.__dict__["_schema_key"] = 1


class ParserState:
    def own(self) -> None:
        self.__dict__["_state_key"] = 1
