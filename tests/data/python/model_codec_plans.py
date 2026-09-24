"""Render generation-time wire plans from real accepted generation, without the model engine."""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import TYPE_CHECKING

from datamodel_code_generator import GenerateConfig, InputFileType, OpenAPIScope
from datamodel_code_generator._openapi_wire_plan import plan_wire
from datamodel_code_generator._runtime.model_codecs.errors import CodecError
from datamodel_code_generator._runtime.model_codecs.media import decode_json
from datamodel_code_generator._runtime.model_codecs.schema import SchemaBundle
from datamodel_code_generator._runtime.model_codecs.wire import thaw_wire
from tests.data.python.generation_session_inputs import generate_product

if TYPE_CHECKING:
    from pathlib import Path


def wire_plan_report(source: Path, instances: Path | None = None, *, legacy: bool = False) -> str:
    """Plan every schema-bearing use of one accepted attempt and validate sample wire values offline."""
    scopes = [OpenAPIScope.Schemas, OpenAPIScope.Paths] if legacy else [OpenAPIScope.Api]
    product, _ = generate_product(
        source, GenerateConfig(input_file_type=InputFileType.OpenAPI, openapi_scopes=scopes, formatters=[])
    )
    try:
        plan = plan_wire(product.batch, product.source_lease)
        documents = {document.id: document.uri for document in product.batch.documents}
    finally:
        product.close()
    report: dict[str, object] = {
        "resources": [
            {"uri": resource.uri, "roots": list(resource.roots), "contents": thaw_wire(resource.contents)}
            for resource in plan.resources
        ],
        "schemas": sorted(
            {f"{use.role} {use.location or '-'} {use.name or '-'} {use.status or '-'} {use.media or '-'} {schema_id}" for use, schema_id in plan.schema_ids}
        ),
        "parameters": {
            operation.use_site.pointer: [asdict(parameter) for parameter in parameters]
            for operation, parameters in plan.parameters
        },
        "views": {
            view.direction: {
                "patches": [f"{patch.uri}#{patch.pointer} {patch.keyword}={json.dumps(thaw_wire(patch.value))}" for patch in view.patches],
                "flagged": list(view.flagged),
            }
            for view in plan.views
        },
        "diagnostics": [
            f"{item.code} {documents[item.source.document]}#{item.source.pointer}"
            f"{f' [{item.operation.use_site.pointer}]' if item.operation else ''}: {item.message}"
            for item in plan.diagnostics
        ],
    }
    if instances is not None:
        bundle = SchemaBundle(plan.resources)
        report["validation"] = [
            f"{case['schema']} {json.dumps(thaw_wire(case['value']), default=str)} -> "
            f"{','.join(f'{issue.code}@{issue.instance_pointer}' for issue in bundle.validator(case['schema']).validate(case['value'])) or 'valid'}"
            for case in decode_json(instances.read_bytes())
        ]
    return json.dumps(report, indent=2, default=str) + "\n"
