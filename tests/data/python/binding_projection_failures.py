"""Reject corrupted type producers against a real generated builtin declaration."""

from __future__ import annotations

import gc
import weakref
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError, FieldSlot, SymbolId
from datamodel_code_generator.imports import Import
from datamodel_code_generator.model.binding import ExpectedFieldDeclaration, FrozenImportBindings, index_builtin_field_declarations
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from datamodel_code_generator.parser.openapi_contract_store import _type_recipe
from datamodel_code_generator.parser.openapi_contract_types import FinalTypeProjector, ReferenceTypeBinding
from tests.data.python.binding_inputs import builtin_model_config, builtin_field_imports
from tests.data.python.binding_type_corruptions import corrupt_type

if TYPE_CHECKING:
    from pathlib import Path


def rejected_projection(source: Path, case: str) -> tuple[str, dict[str, object]]:
    """Generate real input/output before damaging one final type and verifying cleanup."""
    parser = ContractApiOpenAPIParser(source, attempt_id=AttemptId(1), config=builtin_model_config(DataModelType.PydanticV2BaseModel, configured=False))
    handles = ()
    error = None
    try:
        body = str(parser.parse())
        model = parser.results[0]
        field = model.fields[0]
        data_type = field.data_type
        if case.startswith('argument-') or case.endswith('import-name') or case == 'qualified-import':
            data_type.type = 'conint'
            data_type.import_ = Import(import_='conint', from_='pydantic')
            data_type.is_func = True
        corrupt_type(parser, data_type, case)
        ledger = parser.binding_ledger
        recipe = _type_recipe(data_type, ledger, set())
        projector = FinalTypeProjector({ledger.identity(model.reference): ReferenceTypeBinding(SymbolId(0), False, False, False)}, {})
        projection = projector.project(recipe)
        if projection.value is None:
            error = projection.reason
        else:
            declaration = ExpectedFieldDeclaration(AttemptId(1), SymbolId(0), FieldSlot(AttemptId(1), SymbolId(0), ledger.identity(field), 0, field.name), model.name, field.name, 'pydantic', projection.value)
            try:
                index_builtin_field_declarations(body, expected=(declaration,), imports=FrozenImportBindings(builtin_field_imports()))
            except BindingCaptureError as failure:
                error = str(failure)
        handles = tuple(weakref.ref(node) for node in ledger._anchors)
    finally:
        parser.dispose()
        parser.source_lease.close()
    del model, field, data_type, parser
    gc.collect()
    return body, {'rejection': error, 'retained_graph': sum(handle() is not None for handle in handles)}
