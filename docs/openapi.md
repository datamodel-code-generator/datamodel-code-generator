# 📘 Generate from OpenAPI

Generate Pydantic models from OpenAPI 3 schema definitions.

This page covers OpenAPI input behavior and examples. For task-oriented OpenAPI options, see
[OpenAPI Options](openapi-options.md). For the generated option reference, see
[CLI Reference: OpenAPI-only Options](cli-reference/openapi-only-options.md).

## 🚀 Quick Start

```bash
datamodel-codegen --input api.yaml --input-file-type openapi --output model.py
```

## 📝 Example

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: openapi.quick-start.schema -->
<details>
<summary>api.yaml</summary>

<!-- fmt: off -->

```yaml
openapi: "3.0.0"
info:
  version: 1.0.0
  title: Swagger Petstore
  license:
    name: MIT
servers:
  - url: http://petstore.swagger.io/v1
paths:
  /pets:
    get:
      summary: List all pets
      operationId: listPets
      tags:
        - pets
      parameters:
        - name: limit
          in: query
          description: How many items to return at one time (max 100)
          required: false
          schema:
            type: integer
            format: int32
      responses:
        '200':
          description: A paged array of pets
          headers:
            x-next:
              description: A link to the next page of responses
              schema:
                type: string
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pets"
        default:
          description: unexpected error
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
                x-amazon-apigateway-integration:
                  uri:
                    Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                  passthroughBehavior: when_no_templates
                  httpMethod: POST
                  type: aws_proxy
    post:
      summary: Create a pet
      operationId: createPets
      tags:
        - pets
      responses:
        '201':
          description: Null response
        default:
          description: unexpected error
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
                x-amazon-apigateway-integration:
                  uri:
                    Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
                  passthroughBehavior: when_no_templates
                  httpMethod: POST
                  type: aws_proxy
  /pets/{petId}:
    get:
      summary: Info for a specific pet
      operationId: showPetById
      tags:
        - pets
      parameters:
        - name: petId
          in: path
          required: true
          description: The id of the pet to retrieve
          schema:
            type: string
      responses:
        '200':
          description: Expected response to a valid request
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Pets"
        default:
          description: unexpected error
          content:
            application/json:
              schema:
                $ref: "#/components/schemas/Error"
    x-amazon-apigateway-integration:
      uri:
        Fn::Sub: arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${PythonVersionFunction.Arn}/invocations
      passthroughBehavior: when_no_templates
      httpMethod: POST
      type: aws_proxy
components:
  schemas:
    Pet:
      required:
        - id
        - name
      properties:
        id:
          type: integer
          format: int64
          default: 1
        name:
          type: string
        tag:
          type: string
    Pets:
      type: array
      items:
        $ref: "#/components/schemas/Pet"
    Users:
      type: array
      items:
        required:
          - id
          - name
        properties:
          id:
            type: integer
            format: int64
          name:
            type: string
          tag:
            type: string
    Id:
      type: string
    Rules:
      type: array
      items:
        type: string
    Error:
      description: error result
      required:
        - code
        - message
      properties:
        code:
          type: integer
          format: int32
        message:
          type: string
    apis:
      type: array
      items:
        type: object
        properties:
          apiKey:
            type: string
            description: To be used as a dataset parameter value
          apiVersionNumber:
            type: string
            description: To be used as a version parameter value
          apiUrl:
            type: string
            format: uri
            description: "The URL describing the dataset's fields"
          apiDocumentationUrl:
            type: string
            format: uri
            description: A URL to the API console for each API
    Event:
      type: object
      description: Event object
      properties:
        name:
          type: string
    Result:
        type: object
        properties:
          event:
            $ref: '#/components/schemas/Event'
```

<!-- fmt: on -->
</details>
<!-- END AUTO-GENERATED DOC EXAMPLE: openapi.quick-start.schema -->

**✨ Generated model.py:**

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: openapi.quick-start.output -->
<!-- fmt: off -->

```python
from __future__ import annotations

from pydantic import AnyUrl, BaseModel, Field, RootModel


class Pet(BaseModel):
    id: int
    name: str
    tag: str | None = None


class Pets(RootModel[list[Pet]]):
    root: list[Pet]


class User(BaseModel):
    id: int
    name: str
    tag: str | None = None


class Users(RootModel[list[User]]):
    root: list[User]


class Id(RootModel[str]):
    root: str


class Rules(RootModel[list[str]]):
    root: list[str]


class Error(BaseModel):
    code: int
    message: str


class Api(BaseModel):
    apiKey: str | None = Field(
        None, description='To be used as a dataset parameter value'
    )
    apiVersionNumber: str | None = Field(
        None, description='To be used as a version parameter value'
    )
    apiUrl: AnyUrl | None = Field(
        None, description="The URL describing the dataset's fields"
    )
    apiDocumentationUrl: AnyUrl | None = Field(
        None, description='A URL to the API console for each API'
    )


class Apis(RootModel[list[Api]]):
    root: list[Api]


class Event(BaseModel):
    name: str | None = None


class Result(BaseModel):
    event: Event | None = None
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: openapi.quick-start.output -->

---

## 📖 readOnly / writeOnly Properties

OpenAPI 3.x supports `readOnly` and `writeOnly` property annotations:

- 📤 **readOnly**: Property is only returned in responses (e.g., `id`, `created_at`)
- 📥 **writeOnly**: Property is only sent in requests (e.g., `password`)

### ⚙️ Option: `--read-only-write-only-model-type`

This option generates separate Request/Response models based on these annotations.

| Value | Description |
|-------|-------------|
| (not set) | Default. No special handling (backward compatible) |
| `request-response` | Generate only Request/Response models (no base model) |
| `all` | Generate base model + Request + Response models |

### 📋 Example Schema

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only.schema -->
<!-- fmt: off -->

```yaml
openapi: "3.0.0"
info:
  title: Read Only Write Only Test API
  version: "1.0"
paths: {}
components:
  schemas:
    User:
      type: object
      required:
        - id
        - name
        - password
      properties:
        id:
          type: integer
          readOnly: true
        name:
          type: string
        password:
          type: string
          writeOnly: true
        created_at:
          type: string
          format: date-time
          readOnly: true
        secret_token:
          type: string
          writeOnly: true
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only.schema -->

### ✨ Generated Output

```bash
datamodel-codegen --input user.yaml --input-file-type openapi \
    --output-model-type pydantic_v2.BaseModel \
    --read-only-write-only-model-type all
```

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only.output -->
<!-- fmt: off -->

```python
from __future__ import annotations

from pydantic import AwareDatetime, BaseModel


class UserRequest(BaseModel):
    name: str
    password: str
    secret_token: str | None = None


class UserResponse(BaseModel):
    id: int
    name: str
    created_at: AwareDatetime | None = None


class User(BaseModel):
    id: int
    name: str
    password: str
    created_at: AwareDatetime | None = None
    secret_token: str | None = None
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only.output -->

### 🎯 Usage Patterns

| Use Case | Recommended Option | Generated Models |
|----------|-------------------|------------------|
| API client validation | `request-response` | `UserRequest`, `UserResponse` |
| Database ORM mapping | (not set) | `User` |
| Both client & ORM | `all` | `User`, `UserRequest`, `UserResponse` |

### 🔗 Behavior with allOf Inheritance

When using `allOf` with `$ref`, fields from all referenced schemas are flattened into Request/Response models:

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only-allof.schema -->
<!-- fmt: off -->

```yaml
openapi: "3.0.0"
info:
  title: Read Only Write Only AllOf Test API
  version: "1.0"
paths: {}
components:
  schemas:
    Timestamps:
      type: object
      properties:
        created_at:
          type: string
          format: date-time
          readOnly: true
        updated_at:
          type: string
          format: date-time
          readOnly: true

    Credentials:
      type: object
      properties:
        password:
          type: string
          writeOnly: true
        api_key:
          type: string
          writeOnly: true

    User:
      allOf:
        - $ref: "#/components/schemas/Timestamps"
        - $ref: "#/components/schemas/Credentials"
        - type: object
          required:
            - id
            - name
          properties:
            id:
              type: integer
              readOnly: true
            name:
              type: string
            email:
              type: string
```

<!-- fmt: on -->
<!-- END AUTO-GENERATED DOC EXAMPLE: openapi.read-only-write-only-allof.schema -->

Generated `UserRequest` will exclude `created_at`, `updated_at`, and `id` because they are readOnly fields from the flattened Timestamps/User schemas. Generated `UserResponse` will exclude `password` and `api_key` because they are writeOnly fields from Credentials.

### ⚠️ Collision Handling

If a schema named `UserRequest` or `UserResponse` already exists, the generated model will be named `UserRequestModel` or `UserResponseModel` to avoid conflicts.

### 📤 Supported Output Formats

This option works with all output formats:

- `pydantic_v2.BaseModel`
- `pydantic_v2.dataclass`
- `dataclasses.dataclass`
- `typing.TypedDict`
- `msgspec.Struct`

### 🔗 Supported $ref Types

readOnly/writeOnly resolution works with local and file reference types:

| Reference Type | Example | Support |
|---------------|---------|---------|
| Local | `#/components/schemas/User` | ✅ Supported |
| File | `./common.yaml#/User` | ✅ Supported |

## Supported OpenAPI Features

| Feature | Generation behavior |
|---------|---------------------|
| `components.schemas` | Default model generation scope |
| `components.parameters` | Resolved when referenced by selected operations; no standalone models for unreferenced entries |
| `paths` operation schemas | Generated with `--openapi-scopes paths` |
| Operation query parameters | Included with `--openapi-scopes paths parameters`; add `--include-path-parameters` for URL path parameters |
| `readOnly` / `writeOnly` | Request/response variants with `--read-only-write-only-model-type` |
| Discriminators and combined schemas | Converted into Python unions and inheritance-aware models where possible |
| Local and file `$ref` | Resolved before model generation |

## Limitations

OpenAPI input is used for model generation. It does not generate HTTP clients, server handlers, route definitions, or
runtime request dispatch logic. Full OpenAPI document validation is outside the generation path unless you explicitly
enable validation-related options.

---

## 📖 See Also

- 🚀 [Getting Started](getting-started.md) - Installation and first model
- 🖥️ [CLI Reference: OpenAPI-only Options](cli-reference/openapi-only-options.md) - All OpenAPI-specific CLI options
- ⚙️ [CLI Reference: Base Options](cli-reference/base-options.md) - Input/output configuration options
