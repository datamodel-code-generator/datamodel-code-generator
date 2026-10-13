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
    preset="standard-py312-20260909",
    output=Path("models.py"),
    generate_client=ClientType.HTTPX2,
    client_output=Path("client"),
    client_package="client",
    client_model_package="models",
    client_body_arguments=ClientBodyArguments.Both,
    client_operations={"/paths/~1pets/get": {"runtime": {"request_id_header": "X-Request-Id"}}},
)
