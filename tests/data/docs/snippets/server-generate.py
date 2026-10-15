from pathlib import Path

from datamodel_code_generator import (
    DataModelType,
    InputFileType,
    OpenAPIScope,
    ServerHandlerMode,
    ServerType,
    generate,
)

generate(
    Path("openapi.yaml"),
    input_file_type=InputFileType.OpenAPI,
    openapi_scopes=[OpenAPIScope.Schemas, OpenAPIScope.Api],
    output_model_type=DataModelType.PydanticV2BaseModel,
    preset="<newest-standard-py312-preset>",
    output=Path("app/models.py"),
    generate_server=ServerType.FastAPI,
    server_output=Path("app/server"),
    server_package="app.server",
    server_model_package="app.models",
    server_handler_mode=ServerHandlerMode.Async,
    server_body_modes={"/paths/~1upload/post": "request"},
)
