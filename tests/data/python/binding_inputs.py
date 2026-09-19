"""Prepare real builtin parser inputs for binding E2E tests."""

from __future__ import annotations

from typing import Literal

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator.config import OpenAPIParserConfig
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import get_data_model_types


def builtin_binding_config(
    backend: DataModelType, *, annotated: bool = False, missing_sentinel: bool = False
) -> OpenAPIParserConfig:
    """Select the actual builtin model/field/type factories before capture starts."""
    types = get_data_model_types(backend, target_python_version=PythonVersionMin)
    return OpenAPIParserConfig(
        data_model_type=types.data_model,
        data_model_root_type=types.root_model,
        data_model_field_type=types.field_model,
        data_type_manager_type=types.data_type_manager,
        dump_resolve_reference_action=types.dump_resolve_reference_action,
        openapi_scopes=[OpenAPIScope.Api],
        field_extra_keys={"default_factory"},
        use_annotated=annotated,
        field_constraints=annotated,
        use_missing_sentinel=missing_sentinel,
        formatters=[],
    )


def binding_backend_name(
    backend: DataModelType,
) -> Literal["dataclass", "pydantic_dataclass", "pydantic", "typeddict", "msgspec"]:
    """Select the expected builtin binding policy independently of rendered names."""
    match backend:
        case DataModelType.PydanticV2BaseModel:
            return "pydantic"
        case DataModelType.PydanticV2Dataclass:
            return "pydantic_dataclass"
        case DataModelType.DataclassesDataclass:
            return "dataclass"
        case DataModelType.TypingTypedDict:
            return "typeddict"
        case DataModelType.MsgspecStruct:
            return "msgspec"


def builtin_field_imports() -> tuple[Import, ...]:
    """Supply the explicit identities that the accepted artifact must corroborate."""
    return tuple(
        Import(import_=name, from_=module)
        for module, names in (
            ("pydantic", ("Field",)),
            ("pydantic.experimental.missing_sentinel", ("MISSING",)),
            ("msgspec", ("UNSET", "UnsetType")),
            ("typing", ("Union", "Optional", "Annotated")),
            ("typing_extensions", ("NotRequired", "Required", "ReadOnly")),
        )
        for name in names
    )
