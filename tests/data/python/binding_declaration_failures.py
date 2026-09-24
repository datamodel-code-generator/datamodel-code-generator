"""Exercise inconsistent declaration contracts against actual builtin artifacts."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import (
    AnnotatedType,
    AttemptId,
    BindingCaptureError,
    BuiltinType,
    MetadataCall,
)
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model.binding import FrozenImportBindings, index_builtin_field_declarations
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from datamodel_code_generator.parser.openapi_contract_freeze import freeze_model_inventory
from tests.data.python.binding_inputs import builtin_binding_config, final_inventory_declarations

if TYPE_CHECKING:
    from pathlib import Path


def declaration_failure(source: Path, case: str) -> str:
    """Retain the real generated slots while corrupting an attempt or type expectation."""
    parser = ContractApiOpenAPIParser(
        source, attempt_id=AttemptId(1), config=builtin_binding_config(DataModelType.PydanticV2BaseModel)
    )
    try:
        body = str(parser.parse())
        inventory = freeze_model_inventory(parser, body, output=source.with_name("models.py"), model_package="models")
        expected = final_inventory_declarations(inventory)
        match case:
            case "foreign-attempt":
                expected = (replace(expected[0], attempt=AttemptId(2)), *expected[1:])
            case "duplicate-consumer-slot":
                expected = (*expected, expected[0])
            case "nested-metadata":
                metadata = MetadataCall(Import(import_="Field", from_="pydantic"), ())
                expected = (
                    replace(
                        expected[0], type=AnnotatedType(AnnotatedType(BuiltinType("int"), (metadata,)), (metadata,))
                    ),
                    *expected[1:],
                )
            case _:
                raise ValueError(case)
        index_builtin_field_declarations(
            body, expected=expected, imports=FrozenImportBindings(inventory.imports[0].values)
        )
        return "accepted\n"
    except BindingCaptureError as error:
        return str(error) + "\n"
    finally:
        parser.dispose()
        parser.source_lease.close()
