"""Run the existing discriminator input through public generation."""

from __future__ import annotations

import sys
from pathlib import Path

from datamodel_code_generator import DataModelType, InputFileType, OpenAPIScope, generate

if __name__ == "__main__":
    generate(
        Path(sys.argv[1]),
        input_file_type=InputFileType.OpenAPI,
        output=Path(sys.argv[2]),
        output_model_type=DataModelType.PydanticV2BaseModel,
        special_field_name_prefix="x",
        remove_special_field_name_prefix=True,
        disable_timestamp=True,
        formatters=[],
        **({"openapi_scopes": [OpenAPIScope.Schemas, OpenAPIScope.Api]} if len(sys.argv) > 3 else {}),
    )
