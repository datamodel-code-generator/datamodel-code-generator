"""Prepare real builtin parser inputs for binding E2E tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from datamodel_code_generator import DataModelType, OpenAPIScope, PythonVersionMin
from datamodel_code_generator._generation_contract import AttemptId, BuiltinType, FieldSlot, SymbolId
from datamodel_code_generator.config import OpenAPIParserConfig
from datamodel_code_generator.format import PythonVersion
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model import get_data_model_types
from datamodel_code_generator.model.binding import ExpectedFieldDeclaration

if TYPE_CHECKING:
    from collections.abc import Iterator

    from datamodel_code_generator.model.base import DataModelFieldBase
    from datamodel_code_generator.model.binding import FieldArtifactDeclaration, FieldProjectionContext
    from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
    from datamodel_code_generator.parser.openapi_contract_freeze import FinalModelInventory
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector


def builtin_binding_config(
    backend: DataModelType,
    *,
    annotated: bool = False,
    missing_sentinel: bool = False,
    type_statement: bool = False,
) -> OpenAPIParserConfig:
    """Select the actual builtin model/field/type factories before capture starts."""
    version = PythonVersion.PY_312 if type_statement else PythonVersionMin
    types = get_data_model_types(backend, target_python_version=version, use_type_alias=type_statement)
    return OpenAPIParserConfig(
        data_model_type=types.data_model,
        data_model_root_type=types.root_model,
        data_model_field_type=types.field_model,
        data_type_manager_type=types.data_type_manager,
        dump_resolve_reference_action=types.dump_resolve_reference_action,
        target_python_version=version,
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
                ("typing_extensions", ("NotRequired", "Required", "ReadOnly", "TypeAliasType")),
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


def final_reference_projector(parser: ContractApiOpenAPIParser) -> FinalTypeProjector:
    """Bind emitted references and discriminator members to their final symbols and policies."""
    from datamodel_code_generator._generation_contract import GeneratedEnumMember
    from datamodel_code_generator.model.base import DataModel
    from datamodel_code_generator.model.pydantic_v2.types import PydanticV2DataType
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding

    ledger = parser.binding_ledger
    manager = parser.data_type_manager
    serialize_as_any = manager.use_serialize_as_any and issubclass(manager.data_type, PydanticV2DataType)
    symbols = {ledger.identity(model): SymbolId(index) for index, model in enumerate(parser.results)}
    collapsed = {id(collapse.reference) for collapse in ledger.collapses if collapse.completed}
    return FinalTypeProjector(
        {
            ledger.identity(model.reference): ReferenceTypeBinding(
                symbols[ledger.identity(model)],
                model.nullable,
                model.is_alias,
                serialize_as_any
                and any(isinstance(child, DataModel) and child.fields for child in model.reference.children),
            )
            for model in parser.results
            if id(model.reference) not in collapsed
        },
        {
            ledger.identity(observation.data_type): tuple(
                GeneratedEnumMember(symbols[observation.enum], fields[name], name)
                for _, name in observation.data_type.enum_member_literals
            )
            for observation in parser.discriminator_types
            if observation.enum in symbols
            and observation.data_type.enum_member_literals
            and (fields := {name: field for name, field in observation.members if name is not None})
        },
    )


def projected_field_expectations(
    parser: ContractApiOpenAPIParser,
    model_name: str,
    backend: DataModelType = DataModelType.MsgspecStruct,
    projector: FinalTypeProjector | None = None,
) -> tuple[ExpectedFieldDeclaration, ...]:
    """Use actual final references and pure recipes for the selected consumer."""
    from datamodel_code_generator.parser.openapi_contract_store import _type_recipe
    from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding

    if projector is None:
        projector = FinalTypeProjector(
            {
                parser.binding_ledger.identity(model.reference): ReferenceTypeBinding(
                    SymbolId(index), False, False, False
                )
                for index, model in enumerate(parser.results)
            },
            {},
        )
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


def accepted_field_facts(
    parser: ContractApiOpenAPIParser,
    body: str,
    backend: DataModelType,
    model_name: str,
    imports: tuple[Import, ...] = (),
) -> Iterator[tuple[FieldArtifactDeclaration, DataModelFieldBase, FieldProjectionContext]]:
    """Join accepted emitted facts with actual producer observations, as the final field batch does."""
    from datamodel_code_generator.model.binding import (
        FieldProjectionContext,
        FrozenImportBindings,
        index_builtin_field_declarations,
    )

    projector = final_reference_projector(parser)
    index = index_builtin_field_declarations(
        body,
        expected=projected_field_expectations(parser, model_name, backend, projector),
        imports=FrozenImportBindings(
            (*builtin_field_imports(), *imports),
            tuple((SymbolId(index), model.name) for index, model in enumerate(parser.results)),
        ),
    )
    fields = {parser.binding_ledger.identity(field): field for model in parser.results for field in model.fields}
    for declaration in sorted(index.fields, key=lambda item: item.expected.native_name):
        identity = declaration.expected.slot.field
        construction = parser.field_constructions[identity]
        policy = construction.default_policy
        producers = tuple(
            value.resolution.producer
            for value in (parser.inherited_defaults.get(identity), policy)
            if value is not None
        )
        origin = parser.field_origins.get(identity)
        yield (
            declaration,
            fields[identity],
            FieldProjectionContext(
                origin.required_by_node if origin is not None else None,
                policy.has_default if policy is not None else None,
                None if not producers or "opaque" in producers else "override" in producers,
                construction.schema is not None and construction.schema.nullable is True,
                projector.preexisting_null(construction.preexisting_null, alias_nullable={}),
                parser.force_optional_for_required_fields,
                builtin_semantics=True,
                backend=binding_backend_name(backend),
            ),
        )


def default_provenance_table(
    parser: ContractApiOpenAPIParser,
    body: str,
    backend: DataModelType,
    model_name: str,
    imports: tuple[Import, ...] = (),
) -> str:
    """Render final None-default provenance for each accepted field of one consumer."""
    from datamodel_code_generator.model.binding import freeze_none_default_provenance

    return "".join(
        f"{declaration.expected.native_name}: {value.emitted_default} / {value.origin} / "
        f"{value.annotation_null_origin}\n"
        for declaration, field, projection in accepted_field_facts(parser, body, backend, model_name, imports)
        for value in (freeze_none_default_provenance(field, emitted=declaration.facts, projection=projection),)
    )


def constructor_fact_table(
    parser: ContractApiOpenAPIParser,
    body: str,
    backend: DataModelType,
    model_name: str,
    imports: tuple[Import, ...] = (),
) -> str:
    """Render actual per-field constructor policies read from accepted declarations."""
    import json

    from datamodel_code_generator.model.binding import freeze_builtin_field_facts
    from tests.data.python.binding_type_snapshot import type_snapshot

    return (
        json.dumps(
            {
                declaration.expected.native_name: {
                    "constructor_init": type_snapshot(facts.constructor_init),
                    "kw_only": type_snapshot(facts.kw_only),
                }
                for declaration, field, projection in accepted_field_facts(parser, body, backend, model_name, imports)
                for facts in (freeze_builtin_field_facts(field, emitted=declaration.facts, projection=projection),)
            },
            indent=2,
        )
        + "\n"
    )


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
