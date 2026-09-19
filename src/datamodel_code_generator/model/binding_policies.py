"""Finite builtin binding policies, loaded only by optional contract generation."""

from __future__ import annotations

import keyword
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from datamodel_code_generator._generation_contract import LiteralScalar
from datamodel_code_generator.model import dataclass as dataclass_model
from datamodel_code_generator.model import msgspec, pydantic_v2, typed_dict
from datamodel_code_generator.model.binding import KnownBackendValue
from datamodel_code_generator.model.enum import Enum, IntEnum, StrEnum
from datamodel_code_generator.model.pydantic_v2 import dataclass as pydantic_dataclass
from datamodel_code_generator.model.type_alias import TypeAlias, TypeAliasTypeBackport, TypeStatement

if TYPE_CHECKING:
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.model.binding import BackendModelFacts, BackendName


_BACKENDS: dict[type[DataModel], BackendName] = {
    pydantic_v2.BaseModel: "pydantic",
    pydantic_dataclass.DataClass: "pydantic_dataclass",
    dataclass_model.DataClass: "dataclass",
    typed_dict.TypedDict: "typeddict",
    msgspec.Struct: "msgspec",
}


@dataclass(frozen=True, slots=True)
class BuiltinModelPolicy:
    """Keep accepted declaration form and custom origins separate from backend choice."""

    backend: BackendName | None
    kind: Literal["model", "root", "alias", "enum", "custom"]
    builtin_semantics: bool
    functional_typeddict: bool
    extra_items_present: bool


def freeze_model_policy(model: DataModel, configured_model: type[DataModel]) -> BuiltinModelPolicy:
    """Inspect stored state and exact builtin types without invoking model properties."""
    backend = _BACKENDS.get(configured_model)
    match type(model):
        case value if value in {Enum, IntEnum, StrEnum}:
            kind: Literal["model", "root", "alias", "enum", "custom"] = "enum"
        case value if value in {TypeAlias, TypeAliasTypeBackport, TypeStatement}:
            kind = "alias"
        case value if value is pydantic_v2.RootModel:
            kind = "root"
        case _:
            kind = "model" if type(model) in _BACKENDS else "custom"
    builtin = (
        backend is not None
        and kind != "custom"
        and model._custom_template_dir is None  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access] -- Model-owned raw origin.
        and not model.decorators
        and model.custom_base_class in (None, model.BASE_CLASS, [model.BASE_CLASS])
    )
    functional = (
        backend == "typeddict"
        and kind == "model"
        and any(
            (name := field.original_name if field.original_name is not None else field.name) is None
            or not name.isidentifier()
            or keyword.iskeyword(name)
            for field in model.fields
        )
    )
    arguments: object = model._internal_template_data.get("typed_dict_kwargs", {})  # pyright: ignore[reportPrivateUsage] # ruff: ignore[private-member-access] -- Already adopted by the builtin renderer.
    extra_items = (
        backend == "typeddict" and kind == "model" and isinstance(arguments, dict) and "extra_items" in arguments
    )
    return BuiltinModelPolicy(backend, kind, builtin, functional, extra_items)


def final_field_name(model: DataModel, field_name: str | None) -> str:
    """Use the builtin root template's fixed slot without renaming the live field."""
    return "root" if type(model) is pydantic_v2.RootModel else field_name or ""


def constructor_policy(facts: BackendModelFacts, name: Literal["init", "kw_only"]) -> bool | None:
    """Resolve only finite adopted settings; opaque expressions remain unknown."""
    for setting in facts.parameters:
        if setting.name != name:
            continue
        if setting.present is None:
            return None
        if setting.present:
            match setting.value:
                case KnownBackendValue(LiteralScalar("bool", bool() as value)):
                    return value
                case _:
                    return None
    return name == "init" or facts.backend == "pydantic"
