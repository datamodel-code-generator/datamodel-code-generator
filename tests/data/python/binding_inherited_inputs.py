"""Observe actual inherited fields before the session layer adopts their values."""

from __future__ import annotations

import gc
import sys
import weakref
from typing import TYPE_CHECKING

from datamodel_code_generator._generation_contract import AttemptId
from datamodel_code_generator.enums import AllOfMergeMode
from datamodel_code_generator.parser.openapi_contract import ContractApiOpenAPIParser
from datamodel_code_generator.parser.openapi_scope import ApiOpenAPIParser
from tests.data.python.binding_engine_observer import BindingEngineObserver
from tests.data.python.binding_inputs import builtin_binding_config

if TYPE_CHECKING:
    from collections.abc import Container
    from pathlib import Path

    from datamodel_code_generator import DataModelType


def inherited_fields(source: Path, backend: DataModelType, members: Container[str]) -> tuple[str, dict[str, object]]:
    """Exercise real emission and field provenance while checking schema and graph release."""
    config = builtin_binding_config(backend)
    config.allof_merge_mode = AllOfMergeMode.NoMerge
    ordinary = ApiOpenAPIParser(source, config=config)
    parser = ContractApiOpenAPIParser(source, attempt_id=AttemptId(1), config=config)
    previous = sys.getprofile()
    try:
        normal_observer = BindingEngineObserver()
        ordinary.data_model_field_type._field_imports_cache.clear()
        sys.setprofile(normal_observer.record)
        normal_body = str(ordinary.parse())
        observer = BindingEngineObserver()
        parser.data_model_field_type._field_imports_cache.clear()
        sys.setprofile(observer.record)
        body = str(parser.parse())
        sys.setprofile(previous)
        sources = parser._resolve_field_sources()
        redirected = {
            replacement.original
            for replacement in parser.binding_ledger.replacements
            if replacement.kind == "reference" and replacement.original != replacement.replacement
        }
        origins = {
            member: sorted({
                origin.location.pointer
                for identity in sources.get(
                    parser.binding_ledger.identity(field), (parser.binding_ledger.identity(field),)
                )
                if (entry := parser.field_origins.get(identity)) is not None
                for origin in entry.origins
            })
            for model in parser.results
            if parser.binding_ledger.identity(model.reference) not in redirected
            for field in model.fields
            if (member := f"{model.name}.{field.original_name if field.original_name is not None else field.name}")
            in members
        }
        handles = [weakref.ref(node) for node in parser.binding_ledger._anchors]
        handles.extend(weakref.ref(node) for node in parser.schema_origins._validated_anchors.values())
        unknown = sum(not field.origins for field in parser.field_origins.values())
    finally:
        sys.setprofile(previous)
        ordinary.dispose()
        parser.dispose()
        parser.source_lease.close()
    gc.collect()
    return body, {
        "origins": origins,
        "unknown_fields": unknown,
        "retained_graph": sum(handle() is not None for handle in handles),
        "identical_model_bytes": normal_body == body,
        "identical_engine_calls": normal_observer.calls == observer.calls,
    }
