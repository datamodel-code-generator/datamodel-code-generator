"""Prepare real builtin parser inputs for binding E2E tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator._generation_contract import AttemptId, BuiltinType, FieldSlot, SymbolId
from datamodel_code_generator.config import OpenAPIParserConfig
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.binding import ExpectedFieldDeclaration

if TYPE_CHECKING:
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_freeze import FinalModelInventory


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
    return (
        Import(import_="argparse"),
        *tuple(
            Import(import_=name, from_=module)
            for module, names in (
                ("pydantic", ("Field", "conint", "constr", "AwareDatetime")),
                ("pydantic.experimental.missing_sentinel", ("MISSING",)),
                ("msgspec", ("UNSET", "UnsetType", "Meta")),
                ("typing", ("Union", "Optional", "Annotated", "List", "Dict", "Set", "Literal")),
                ("typing_extensions", ("NotRequired", "Required", "ReadOnly")),
                ("uuid", ("UUID",)),
            )
            for name in names
        ),
    )


def functional_field_expectations(parser: ContractApiOpenAPIParser) -> tuple[ExpectedFieldDeclaration, ...]:
    """Prepare known scalar keys from the functional-fields source fixture."""
    model = parser.results[0]
    return tuple(
        ExpectedFieldDeclaration(
            AttemptId(1),
            SymbolId(0),
            FieldSlot(AttemptId(1), SymbolId(0), parser.binding_ledger.identity(field), index, str(field.name)),
            model.name,
            str(field.name),
            "typeddict",
            BuiltinType("str"),
            form="typeddict_entry",
            entry_key=field.original_name if field.original_name is not None else field.name,
            entry_ordinal=index,
        )
        for index, field in enumerate(model.fields)
    )


def projected_field_expectations(
    parser: ContractApiOpenAPIParser, model_name: str, backend: DataModelType = DataModelType.MsgspecStruct
) -> tuple[ExpectedFieldDeclaration, ...]:
    """Use actual final references and pure recipes for the selected consumer."""
    from datamodel_code_generator.parser.openapi_contract_store import _type_recipe
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding

    references = {
        parser.binding_ledger.identity(model.reference): ReferenceTypeBinding(SymbolId(index), False, False, False)
        for index, model in enumerate(parser.results)
    }
    projector = FinalTypeProjector(references, {})
    declarations: list[ExpectedFieldDeclaration] = []
    for index, model in enumerate(parser.results):
        if model.name != model_name:
            continue
        for ordinal, field in enumerate(model.fields):
            projection = projector.project(_type_recipe(field.data_type, parser.binding_ledger, set()))
            projected = next(value for value in (projection.value,) if value is not None)
            declarations.append(
                ExpectedFieldDeclaration(
                    AttemptId(1),
                    SymbolId(index),
                    FieldSlot(
                        AttemptId(1), SymbolId(index), parser.binding_ledger.identity(field), ordinal, str(field.name)
                    ),
                    model.name,
                    field.name,
                    binding_backend_name(backend),
                    projected,
                    excluded_by_tag=backend == DataModelType.MsgspecStruct and bool(field.extras.get("is_classvar")),
                )
            )
    return tuple(declarations)


def builtin_model_config(backend: DataModelType, *, configured: bool) -> OpenAPIParserConfig:
    """Exercise adopted model options, including existing override and omission rules."""
    from collections import defaultdict

    config = builtin_binding_config(backend)
    if not configured:
        return config
    match backend:
        case DataModelType.DataclassesDataclass | DataModelType.PydanticV2Dataclass:
            config.dataclass_arguments = {"init": False, "frozen": True, "slots": True, "kw_only": True}
        case DataModelType.TypingTypedDict:
            config.use_total_false_for_typed_dict = True
        case DataModelType.MsgspecStruct:
            config.keyword_only = True
            config.extra_template_data = defaultdict(
                dict,
                {
                    "#all#": {
                        "base_class_kwargs": {
                            "tag": "record",
                            "tag_field": "kind",
                            "array_like": True,
                            "forbid_unknown_fields": True,
                            "omit_defaults": True,
                            "kw_only": False,
                            "frozen": True,
                            "rename": {"wireName": "wire_name"},
                        }
                    }
                },
            )
        case DataModelType.PydanticV2BaseModel:
            pass
    if backend in {DataModelType.PydanticV2BaseModel, DataModelType.PydanticV2Dataclass}:
        config.extra_template_data = defaultdict(
            dict,
            {
                "#all#": {
                    "config": {
                        "strict": True,
                        "extra": "forbid",
                        "populate_by_name": True,
                    }
                }
            },
        )
    return config


def final_inventory_config(case: str) -> OpenAPIParserConfig:
    """Select explicit settings for independent final-import and reuse regression inputs."""
    from datamodel_code_generator.enums import StrictTypes

    config = builtin_binding_config(DataModelType.PydanticV2BaseModel)
    match case:
        case "final-imports":
            config.strict_types = [StrictTypes.str]
            config.import_overrides = {"StrictStr": "pydantic.types", "Color": "palette"}
        case "final-reused-inheritance":
            config.use_serialize_as_any = True
            config.reuse_model = True
    return config


def final_inventory_declarations(inventory: FinalModelInventory) -> tuple[ExpectedFieldDeclaration, ...]:
    """Use actual frozen slots and types to prepare class-field corroboration inputs."""
    return tuple(
        ExpectedFieldDeclaration(
            inventory.attempt,
            model.symbol,
            field.slot,
            model.name,
            field.slot.name,
            "pydantic",
            next(value for value in (field.projection.value,) if value is not None),
        )
        for model in inventory.models
        for field in model.fields
    )


def quoted_constraints_config(formatter: str) -> OpenAPIParserConfig:
    """Exercise real formatter quote changes on source and imported expression producers."""
    from datamodel_code_generator.enums import DefaultValueType
    from datamodel_code_generator.format import Formatter

    config = builtin_binding_config(DataModelType.PydanticV2BaseModel)
    config.formatters = [Formatter(formatter)]
    config.use_double_quotes = True
    config.wrap_string_literal = True
    config.use_decimal_for_multiple_of = True
    config.deserialize_default_values = [DefaultValueType.Decimal]
    return config
