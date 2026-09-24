"""Exercise opaque backend boundaries after the real builtin renderer has completed."""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

from datamodel_code_generator import DataModelType
from datamodel_code_generator._generation_contract import AttemptId, BindingCaptureError
from datamodel_code_generator.model.binding import (
    FieldProjectionContext,
    FrozenImportBindings,
    ModelProjectionContext,
    OpaqueBackendValue,
    freeze_builtin_field_facts,
    freeze_builtin_model_facts,
    index_builtin_field_declarations,
)
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from tests.data.python.binding_inputs import (
    binding_backend_name,
    builtin_model_config,
    projected_field_expectations,
)

if TYPE_CHECKING:
    from pathlib import Path


def backend_failure(source: Path, backend: DataModelType, case: str) -> dict[str, object]:
    """Corrupt one adopted value, preserving actual generation and backend projection."""
    parser = ContractApiOpenAPIParser(
        source, attempt_id=AttemptId(1), config=builtin_model_config(backend, configured=False)
    )
    try:
        body = str(parser.parse())
        model = parser.results[0]
        name = binding_backend_name(backend)
        context = ModelProjectionContext(name, builtin_semantics=case != "custom-model")
        internal = model._internal_template_data
        match case:
            case "dataclass-invalid":
                model.dataclass_arguments = {0: True}
            case "dataclass-opaque-init":
                model.dataclass_arguments = {"init": object()}
            case "msgspec-raw":
                model.extra_template_data["base_class_kwargs"] = ()
            case "msgspec-adopted":
                internal["base_class_kwargs"] = []
            case "msgspec-keyword":
                internal["base_class_kwargs"] = {"kw_only": 0}
            case "typeddict-invalid":
                internal["typed_dict_kwargs"] = ()
            case "typeddict-keyword":
                internal["typed_dict_kwargs"] = {"closed": 0}
            case "typeddict-extra":
                internal["typed_dict_kwargs"] = {"extra_items": "str"}
            case "config-container":
                internal["config_items"] = object()
            case "config-item":
                internal["config_items"] = [["strict", "True"]]
            case "config-arity":
                internal["config_items"] = [("strict",)]
            case "config-name":
                internal["config_items"] = [(0, "True")]
            case "config-unknown":
                internal["config_items"] = [("unobserved", "True")]
            case "config-value":
                internal["config_items"] = [("strict", True)]
        facts = freeze_builtin_model_facts(model, projection=context)
        if case.startswith("field-"):
            expected = projected_field_expectations(parser, model.name, backend)
            imports = FrozenImportBindings(tuple(model.imports))
            index = index_builtin_field_declarations(body, expected=expected, imports=imports)
            projection = FieldProjectionContext(
                original_required=model.fields[0].required,
                backend=name,
                builtin_semantics=True,
                schema_default=False,
                explicit_model_default=False,
                explicit_nullable=False,
                preexisting_null=False,
                configuration_nullable=False,
                constructor_init=True,
                kw_only=False,
            )
            if case == "field-backend":
                projection = replace(projection, backend=None)
            elif case == "field-custom":
                projection = replace(projection, builtin_semantics=False)
            else:
                model.fields[0].alias = object()
            field = freeze_builtin_field_facts(model.fields[0], emitted=index.fields[0].facts, projection=projection)
            return {
                "opaque_declarations": sum(isinstance(value, OpaqueBackendValue) for _, value in field.declarations),
                "opaque_init": isinstance(field.constructor_init, OpaqueBackendValue),
            }
        return {
            "opaque_parameters": sum(isinstance(item.value, OpaqueBackendValue) for item in facts.parameters),
            "unknown_parameters": sum(item.present is None for item in facts.parameters),
            "opaque_configuration": sum(isinstance(item.value, OpaqueBackendValue) for item in facts.configuration),
            "unknown_configuration": sum(item.present is None for item in facts.configuration),
        }
    except BindingCaptureError as error:
        return {"error": str(error)}
    finally:
        parser.dispose()
        parser.source_lease.close()
