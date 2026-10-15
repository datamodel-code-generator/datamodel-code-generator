from pathlib import Path

from datamodel_code_generator import (
    ClientBodyArguments,
    ClientType,
    DataModelType,
    InputFileType,
    OpenAPIScope,
    generate,
)

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    output_model_type=DataModelType.PydanticV2BaseModel,
    preset="<newest-standard-py312-preset>",
    output=Path("app/models.py"),
    generate_client=ClientType.HTTPX2,
    client_output=Path("app/client"),
    client_package="app.client",
    client_model_package="app.models",
    client_body_arguments=ClientBodyArguments.Both,
    client_operations={"/paths/~1pets/get": {"runtime": {"request_id_header": "X-Request-Id"}}},
)
