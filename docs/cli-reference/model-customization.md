# 🏗️ Model Customization

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--alias-generator`](#alias-generator) | Use a Pydantic v2 alias generator in model_config. |
| [`--allow-extra-fields`](#allow-extra-fields) | Allow extra fields in generated Pydantic models (extra='allo... |
| [`--allow-leading-underscore-class-name`](#allow-leading-underscore-class-name) | Allow an explicitly specified root class name to start with ... |
| [`--allow-population-by-field-name`](#allow-population-by-field-name) | Allow Pydantic model population by field name (not just alia... |
| [`--base-class`](#base-class) | Specify a custom base class for generated models. |
| [`--base-class-map`](#base-class-map) | Specify different base classes for specific models via JSON ... |
| [`--class-name`](#class-name) | Override the auto-generated class name with a custom name. |
| [`--class-name-affix-scope`](#class-name-affix-scope) | Control which classes receive the prefix/suffix. |
| [`--class-name-prefix`](#class-name-prefix) | Add a prefix to all generated class names. |
| [`--class-name-suffix`](#class-name-suffix) | Add a suffix to all generated class names. |
| [`--collapse-reuse-models`](#collapse-reuse-models) | Collapse duplicate models by replacing references instead of... |
| [`--collapse-root-models`](#collapse-root-models) | Inline root model definitions instead of creating separate w... |
| [`--collapse-root-models-name-strategy`](#collapse-root-models-name-strategy) | Select which name to keep when collapsing root models with o... |
| [`--dataclass-arguments`](#dataclass-arguments) | Customize dataclass decorator arguments via JSON dictionary. |
| [`--duplicate-name-suffix`](#duplicate-name-suffix) | Customize suffix for duplicate model names. |
| [`--enable-faux-immutability`](#enable-faux-immutability) | Enable faux immutability in Pydantic models (frozen=True). |
| [`--force-optional`](#force-optional) | Force all fields to be Optional regardless of required statu... |
| [`--frozen-dataclasses`](#frozen-dataclasses) | Generate frozen dataclasses with optional keyword-only field... |
| [`--keep-model-order`](#keep-model-order) | Keep generated model order deterministic while respecting de... |
| [`--keyword-only`](#keyword-only) | Generate dataclasses with keyword-only fields (Python 3.10+)... |
| [`--model-extra-keys`](#model-extra-keys) | Add model-level schema extensions to ConfigDict json_schema_... |
| [`--model-extra-keys-without-x-prefix`](#model-extra-keys-without-x-prefix) | Strip x- prefix from model-level schema extensions and add t... |
| [`--model-name-map`](#model-name-map) | Rename generated model classes from a JSON mapping. |
| [`--naming-strategy`](#naming-strategy) | Use parent-prefixed naming strategy for duplicate model name... |
| [`--output-model-type`](#output-model-type) | Select the output model type (Pydantic v2, Pydantic v2 datac... |
| [`--parent-scoped-naming`](#parent-scoped-naming) | Namespace models by their parent scope to avoid naming confl... |
| [`--reuse-model`](#reuse-model) | Reuse identical model definitions instead of generating dupl... |
| [`--reuse-scope`](#reuse-scope) | Scope for model reuse detection (root or tree). |
| [`--skip-root-model`](#skip-root-model) | Skip generation of root model when schema contains nested de... |
| [`--strict-nullable`](#strict-nullable) | Treat default field as a non-nullable field. |
| [`--strip-default-none`](#strip-default-none) | Remove fields with None as default value from generated mode... |
| [`--target-pydantic-version`](#target-pydantic-version) | Target Pydantic version for generated code compatibility. |
| [`--target-python-version`](#target-python-version) | Target Python version for generated code syntax and imports. |
| [`--union-mode`](#union-mode) | Union mode for combining anyOf/oneOf schemas (smart or left_... |
| [`--use-default`](#use-default) | Use default values from schema in generated models. |
| [`--use-default-factory-for-optional-nested-models`](#use-default-factory-for-optional-nested-models) | Generate default_factory for optional nested model fields. |
| [`--use-default-kwarg`](#use-default-kwarg) | Use default= keyword argument instead of positional argument... |
| [`--use-frozen-field`](#use-frozen-field) | Generate frozen (immutable) field definitions for readOnly p... |
| [`--use-generic-base-class`](#use-generic-base-class) | Generate a shared base class with model configuration to avo... |
| [`--use-missing-sentinel`](#use-missing-sentinel) | Use Pydantic's MISSING sentinel for optional fields without ... |
| [`--use-one-literal-as-default`](#use-one-literal-as-default) | Use single literal value as default when enum has only one o... |
| [`--use-root-model-sequence-interface`](#use-root-model-sequence-interface) | Make non-null sequence-like Pydantic v2 RootModel classes im... |
| [`--use-serialize-as-any`](#use-serialize-as-any) | Wrap fields with subtypes in Pydantic's SerializeAsAny. |
| [`--use-subclass-enum`](#use-subclass-enum) | Generate typed Enum subclasses for enums with specific field... |

---

## 🍳 Recipes

### Target Pydantic v2 on modern Python

Set the output model family and Python/Pydantic compatibility targets together.

**Options:** [`--output-model-type`](#output-model-type), [`--target-python-version`](#target-python-version), [`--target-pydantic-version`](#target-pydantic-version)

### Deduplicate reusable schemas

Reuse equivalent models and tune the scope or root-model behavior when schemas repeat.

**Options:** [`--reuse-model`](#reuse-model), [`--reuse-scope`](#reuse-scope), [`--collapse-root-models`](#collapse-root-models)

---

## `--alias-generator` {#alias-generator}

Use a Pydantic v2 alias generator in model_config.

The `--alias-generator` option emits a per-model ConfigDict alias generator for
Pydantic v2 BaseModel output and omits matching per-field aliases.

**Related:** [`--output-model-type`](#output-model-type), [`--snake-case-field`](field-customization.md#snake-case-field)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --snake-case-field --alias-generator to_camel --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--alias-generator` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "AliasGeneratorModel",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string"
        },
        "lastName": {
          "type": "string"
        },
        "foo_bar": {
          "type": "string"
        },
        "weird-url": {
          "type": "integer"
        }
      },
      "required": ["firstName", "foo_bar", "weird-url"]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  alias_generator.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, ConfigDict, Field
    from pydantic.alias_generators import to_camel


    class AliasGeneratorModel(BaseModel):
        model_config = ConfigDict(
            populate_by_name=True,
            alias_generator=to_camel,
        )
        first_name: str
        last_name: str | None = None
        foo_bar: str = Field(..., alias="foo_bar")
        weird_url: int = Field(..., alias="weird-url")
    ```

---

## `--allow-extra-fields` {#allow-extra-fields}

Allow extra fields in generated Pydantic models (extra='allow').

The `--allow-extra-fields` flag configures the code generation behavior.

**Deprecated:** --allow-extra-fields is deprecated. Use --extra-fields=allow instead.

**Option relationships:**

- **Implies:** [`--extra-fields`](field-customization.md#extra-fields) = `allow`

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --allow-extra-fields # (1)!
    ```

    1. :material-arrow-left: `--allow-extra-fields` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  api.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel


        class Pet(BaseModel):
            model_config = ConfigDict(
                extra="allow",
            )
            id: int
            name: str
            tag: str | None = None


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class User(BaseModel):
            model_config = ConfigDict(
                extra="allow",
            )
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
            model_config = ConfigDict(
                extra="allow",
            )
            code: int
            message: str


        class Api(BaseModel):
            model_config = ConfigDict(
                extra="allow",
            )
            apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
            apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
            apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
            apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            model_config = ConfigDict(
                extra="allow",
            )
            name: str | None = None


        class Result(BaseModel):
            model_config = ConfigDict(
                extra="allow",
            )
            event: Event | None = None
        ```

---

## `--allow-leading-underscore-class-name` {#allow-leading-underscore-class-name}

Allow an explicitly specified root class name to start with an underscore.

By default, leading underscores in class names are normalized to avoid private
model names. Use --allow-leading-underscore-class-name together with an explicit
--class-name when you need the generated root model to preserve that name
exactly.

**Related:** [`--class-name`](#class-name)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --class-name __ParsedModel --allow-leading-underscore-class-name --disable-timestamp # (1)!
    ```

    1. :material-arrow-left: `--allow-leading-underscore-class-name` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "type": "object",
      "properties": {
        "status": {
          "$ref": "#/$defs/Status"
        },
        "item": {
          "$ref": "#/$defs/Item"
        }
      },
      "$defs": {
        "Status": {
          "type": "string",
          "enum": ["active", "inactive"]
        },
        "Item": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  class_name_affix.json

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel


    class Status(Enum):
        active = "active"
        inactive = "inactive"


    class Item(BaseModel):
        id: int | None = None
        name: str | None = None


    class __ParsedModel(BaseModel):
        status: Status | None = None
        item: Item | None = None
    ```

---

## `--allow-population-by-field-name` {#allow-population-by-field-name}

Allow Pydantic model population by field name (not just alias).

The `--allow-population-by-field-name` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --allow-population-by-field-name # (1)!
    ```

    1. :material-arrow-left: `--allow-population-by-field-name` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  api.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel


        class Pet(BaseModel):
            model_config = ConfigDict(
                populate_by_name=True,
            )
            id: int
            name: str
            tag: str | None = None


        class Pets(RootModel[list[Pet]]):
            root: list[Pet]


        class User(BaseModel):
            model_config = ConfigDict(
                populate_by_name=True,
            )
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
            model_config = ConfigDict(
                populate_by_name=True,
            )
            code: int
            message: str


        class Api(BaseModel):
            model_config = ConfigDict(
                populate_by_name=True,
            )
            apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
            apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
            apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
            apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


        class Apis(RootModel[list[Api]]):
            root: list[Api]


        class Event(BaseModel):
            model_config = ConfigDict(
                populate_by_name=True,
            )
            name: str | None = None


        class Result(BaseModel):
            model_config = ConfigDict(
                populate_by_name=True,
            )
            event: Event | None = None
        ```

---

## `--base-class` {#base-class}

Specify a custom base class for generated models.

The `--base-class` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --base-class custom_module.Base # (1)!
    ```

    1. :material-arrow-left: `--base-class` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  api.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, Field, RootModel

    from custom_module import Base


    class Pet(Base):
        id: int
        name: str
        tag: str | None = None


    class Pets(RootModel[list[Pet]]):
        root: list[Pet]


    class User(Base):
        id: int
        name: str
        tag: str | None = None


    class Users(RootModel[list[User]]):
        root: list[User]


    class Id(RootModel[str]):
        root: str


    class Rules(RootModel[list[str]]):
        root: list[str]


    class Error(Base):
        code: int
        message: str


    class Api(Base):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    class Event(Base):
        name: str | None = None


    class Result(Base):
        event: Event | None = None
    ```

---

## `--base-class-map` {#base-class-map}

Specify different base classes for specific models via JSON mapping.

The `--base-class-map` option allows you to assign different base classes
to specific models. This is useful when you want selective base class inheritance,
for example, applying custom base classes only to specific models while leaving
others with the default `BaseModel`.

Priority: `--base-class-map` > `customBasePath` (schema extension) > `--base-class`

You can specify either a single base class as a string, or multiple base classes
(mixins) as a list:

- Single: `{"Person": "custom.bases.PersonBase"}`
- Multiple: `{"User": ["mixins.AuditMixin", "mixins.TimestampMixin"]}`

You can pass the mapping either inline as JSON or as a path to a JSON file.

When using multiple base classes, the specified classes are used directly without
adding `BaseModel`. Ensure your mixins inherit from `BaseModel` if needed.

**Related:** [`--base-class`](#base-class)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --base-class-map '{"Person": "custom.bases.PersonBase", "Animal": "custom.bases.AnimalBase"}' # (1)!
    ```

    1. :material-arrow-left: `--base-class-map` - the option documented here

<!-- BEGIN AUTO-GENERATED DOC EXAMPLE: cli-reference.model-customization.base-class-map.example -->
??? example "Examples"

    **Input Schema:**
    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "Person": {
          "type": "object",
          "properties": {
            "name": {"type": "string"}
          }
        },
        "Animal": {
          "type": "object",
          "properties": {
            "species": {"type": "string"}
          }
        },
        "Car": {
          "type": "object",
          "properties": {
            "model": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**
    ```python
    from __future__ import annotations

    from typing import Any

    from custom.bases import AnimalBase, PersonBase
    from pydantic import BaseModel, RootModel


    class Model(RootModel[Any]):
        root: Any


    class Person(PersonBase):
        name: str | None = None


    class Animal(AnimalBase):
        species: str | None = None


    class Car(BaseModel):
        model: str | None = None
    ```
<!-- END AUTO-GENERATED DOC EXAMPLE: cli-reference.model-customization.base-class-map.example -->

---

## `--class-name` {#class-name}

Override the auto-generated class name with a custom name.

The --class-name option allows you to specify a custom class name for the
generated model. This is useful when the schema title is invalid as a Python
class name (e.g., starts with a number) or when you want to use a different
naming convention than what's in the schema.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --class-name ValidModelName # (1)!
    ```

    1. :material-arrow-left: `--class-name` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "1 xyz",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string",
          "description": "The person's first name."
        },
        "lastName": {
          "type": "string",
          "description": "The person's last name."
        },
        "age": {
          "description": "Age in years which must be equal to or greater than zero.",
          "type": "integer",
          "minimum": 0
        },
        "friends": {
          "type": "array"
        },
        "comment": {
          "type": "null"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  invalid_model_name.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, Field, conint


    class ValidModelName(BaseModel):
        firstName: str | None = Field(None, description="The person's first name.")
        lastName: str | None = Field(None, description="The person's last name.")
        age: conint(ge=0) | None = Field(None, description="Age in years which must be equal to or greater than zero.")
        friends: list[Any] | None = None
        comment: None = Field(None)
    ```

---

## `--class-name-affix-scope` {#class-name-affix-scope}

Control which classes receive the prefix/suffix.

The --class-name-affix-scope option controls which types of classes receive the
prefix or suffix specified by --class-name-prefix or --class-name-suffix:
- 'all': Apply to all classes (models and enums) - this is the default
- 'models': Apply only to model classes (BaseModel, dataclass, TypedDict, etc.)
- 'enums': Apply only to enum classes

**Related:** [`--class-name-prefix`](#class-name-prefix), [`--class-name-suffix`](#class-name-suffix)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --class-name-suffix Schema --class-name-affix-scope models # (1)!
    ```

    1. :material-arrow-left: `--class-name-affix-scope` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "type": "object",
      "properties": {
        "status": {
          "$ref": "#/$defs/Status"
        },
        "item": {
          "$ref": "#/$defs/Item"
        }
      },
      "$defs": {
        "Status": {
          "type": "string",
          "enum": ["active", "inactive"]
        },
        "Item": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  class_name_affix.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel


    class Status(Enum):
        active = "active"
        inactive = "inactive"


    class ItemSchema(BaseModel):
        id: int | None = None
        name: str | None = None


    class ModelSchemaSchema(BaseModel):
        status: Status | None = None
        item: ItemSchema | None = None
    ```

---

## `--class-name-prefix` {#class-name-prefix}

Add a prefix to all generated class names.

The --class-name-prefix option allows you to add a prefix to all generated class
names, including both models and enums. This is useful for namespacing generated
code or avoiding conflicts with existing classes.

**Related:** [`--class-name-affix-scope`](#class-name-affix-scope), [`--class-name-suffix`](#class-name-suffix)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --class-name-prefix Api # (1)!
    ```

    1. :material-arrow-left: `--class-name-prefix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "type": "object",
      "properties": {
        "status": {
          "$ref": "#/$defs/Status"
        },
        "item": {
          "$ref": "#/$defs/Item"
        }
      },
      "$defs": {
        "Status": {
          "type": "string",
          "enum": ["active", "inactive"]
        },
        "Item": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  class_name_affix.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel


    class ApiStatus(Enum):
        active = "active"
        inactive = "inactive"


    class ApiItem(BaseModel):
        id: int | None = None
        name: str | None = None


    class ApiModel(BaseModel):
        status: ApiStatus | None = None
        item: ApiItem | None = None
    ```

---

## `--class-name-suffix` {#class-name-suffix}

Add a suffix to all generated class names.

The --class-name-suffix option allows you to add a suffix to all generated class
names, including both models and enums. This is useful for distinguishing generated
classes (e.g., adding 'Schema' or 'Model' suffix).

**Related:** [`--class-name-affix-scope`](#class-name-affix-scope), [`--class-name-prefix`](#class-name-prefix)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --class-name-suffix Schema # (1)!
    ```

    1. :material-arrow-left: `--class-name-suffix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "type": "object",
      "properties": {
        "status": {
          "$ref": "#/$defs/Status"
        },
        "item": {
          "$ref": "#/$defs/Item"
        }
      },
      "$defs": {
        "Status": {
          "type": "string",
          "enum": ["active", "inactive"]
        },
        "Item": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  class_name_affix.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel


    class StatusSchema(Enum):
        active = "active"
        inactive = "inactive"


    class ItemSchema(BaseModel):
        id: int | None = None
        name: str | None = None


    class ModelSchema(BaseModel):
        status: StatusSchema | None = None
        item: ItemSchema | None = None
    ```

---

## `--collapse-reuse-models` {#collapse-reuse-models}

Collapse duplicate models by replacing references instead of inheritance.

The `--collapse-reuse-models` flag, when used with `--reuse-model`,
eliminates redundant empty subclasses (e.g., `class Foo(Bar): pass`)
by replacing all references to duplicate models with the canonical model.

**Related:** [`--reuse-model`](#reuse-model)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --reuse-model --collapse-reuse-models # (1)!
    ```

    1. :material-arrow-left: `--collapse-reuse-models` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "Arm Right": {
            "Joint 1": 5,
            "Joint 2": 3,
            "Joint 3": 66
        },
        "Arm Left": {
            "Joint 1": 55,
            "Joint 2": 13,
            "Joint 3": 6
        },
        "Head": {
            "Joint 1": 10
        }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  duplicate_models.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field


    class ArmRight(BaseModel):
        Joint_1: int = Field(..., alias="Joint 1")
        Joint_2: int = Field(..., alias="Joint 2")
        Joint_3: int = Field(..., alias="Joint 3")


    class Head(BaseModel):
        Joint_1: int = Field(..., alias="Joint 1")


    class Model(BaseModel):
        Arm_Right: ArmRight = Field(..., alias="Arm Right")
        Arm_Left: ArmRight = Field(..., alias="Arm Left")
        Head_1: Head = Field(..., alias="Head")
    ```

---

## `--collapse-root-models` {#collapse-root-models}

Inline root model definitions instead of creating separate wrapper classes.

The `--collapse-root-models` option generates simpler output by inlining root models
directly instead of creating separate wrapper types. This shows how different output
model types (Pydantic v2, dataclass, TypedDict, msgspec) handle const fields.

**See also:** [Model Reuse and Deduplication](../model-reuse.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --collapse-root-models # (1)!
    ```

    1. :material-arrow-left: `--collapse-root-models` - the option documented here

??? example "Examples"

    === "OpenAPI"

        **Input Schema:**

        ```yaml
        openapi: '3.0.2'
        components:
          schemas:
            ApiVersion:
              description: The version of this API
              type: string
              const: v1
            Api:
              type: object
              required:
                - version
              properties:
                version:
                  $ref: "#/components/schemas/ApiVersion"
        ```

        **Output:**

        === "Pydantic v2"

            ```python
            # generated by datamodel-codegen:
            #   filename:  const.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Literal

            from pydantic import BaseModel, Field


            class Api(BaseModel):
                version: Literal["v1"] = Field(..., description="The version of this API")
            ```

        === "dataclass"

            ```python
            # generated by datamodel-codegen:
            #   filename:  const.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from dataclasses import dataclass
            from typing import Literal


            @dataclass
            class Api:
                version: Literal["v1"]
            ```

        === "TypedDict"

            ```python
            # generated by datamodel-codegen:
            #   filename:  const.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Literal, TypedDict


            class Api(TypedDict):
                version: Literal["v1"]
            ```

        === "msgspec"

            ```python
            # generated by datamodel-codegen:
            #   filename:  const.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Annotated, Literal

            from msgspec import Meta, Struct


            class Api(Struct):
                version: Annotated[Literal["v1"], Meta(description="The version of this API")]
            ```

        === "Without Option (Baseline)"

            ```python
            # generated by datamodel-codegen:
            #   filename:  const.yaml
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from pydantic import BaseModel, Field


            class ApiVersion(BaseModel):
                __root__: str = Field("v1", const=True, description="The version of this API")


            class Api(BaseModel):
                version: ApiVersion
            ```

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "https://json-schema.org/draft/2020-12/schema",
          "type": "object",
          "properties": {
            "field": {
              "anyOf": [
                {"$ref": "#/$defs/NullType1"},
                {"$ref": "#/$defs/NullType2"}
              ]
            }
          },
          "$defs": {
            "NullType1": {
              "type": "null"
            },
            "NullType2": {
              "type": "null"
            }
          }
        }
        ```

        **Output:**

        ```python
        # generated by datamodel-codegen:
        #   filename:  collapse_root_models_empty_union.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel


        class Model(BaseModel):
            field: Any = None
        ```

---

## `--collapse-root-models-name-strategy` {#collapse-root-models-name-strategy}

Select which name to keep when collapsing root models with object references.

The --collapse-root-models-name-strategy option controls naming when collapsing
root models. 'child' keeps the inner model's name, 'parent' uses the wrapper's name.

**Related:** [`--collapse-root-models`](#collapse-root-models)

**Option relationships:**

- **Requires:** [`--collapse-root-models`](model-customization.md#collapse-root-models) enabled - `--collapse-root-models-name-strategy` requires `--collapse-root-models`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --collapse-root-models --collapse-root-models-name-strategy child # (1)!
    ```

    1. :material-arrow-left: `--collapse-root-models-name-strategy` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "metadata": {
          "$ref": "#/$defs/ISectionBlockMetadata"
        }
      },
      "$defs": {
        "ISectionBlockMetadata": {
          "$ref": "#/$defs/FieldType2"
        },
        "FieldType2": {
          "type": "object",
          "properties": {
            "asText": {
              "type": "string"
            }
          },
          "required": ["asText"]
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  collapse_root_models_name_strategy_child.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class FieldType2(BaseModel):
        asText: str


    class Model(BaseModel):
        metadata: FieldType2 | None = None
    ```

---

## `--dataclass-arguments` {#dataclass-arguments}

Customize dataclass decorator arguments via JSON dictionary.

The `--dataclass-arguments` flag accepts custom dataclass arguments as a JSON
dictionary (e.g., '{"frozen": true, "kw_only": true, "slots": true, "order": true}').
This overrides individual flags like --frozen-dataclasses and provides fine-grained
control over dataclass generation.

**Related:** [`--frozen-dataclasses`](#frozen-dataclasses), [`--keyword-only`](#keyword-only)

**See also:** [Output Model Types](../output-model-types.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type dataclasses.dataclass --dataclass-arguments '{"slots": true, "order": true}' # (1)!
    ```

    1. :material-arrow-left: `--dataclass-arguments` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    type Person {
        id: ID!
        name: String!
        height: Int
        mass: Int
        hair_color: String
        skin_color: String
        eye_color: String
        birth_year: String
        gender: String
    
        # Relationships
        homeworld_id: ID
        homeworld: Planet
        species: [Species!]!
        species_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
        starships: [Starship!]!
        starships_ids: [ID!]!
        vehicles: [Vehicle!]!
        vehicles_ids: [ID!]!
    }
    
    type Planet {
        id: ID!
        name: String!
        rotation_period: String
        orbital_period: String
        diameter: String
        climate: String
        gravity: String
        terrain: String
        surface_water: String
        population: String
    
        # Relationships
        residents: [Person!]!
        residents_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Species {
        id: ID!
        name: String!
        classification: String
        designation: String
        average_height: String
        skin_colors: String
        hair_colors: String
        eye_colors: String
        average_lifespan: String
        language: String
    
        # Relationships
        people: [Person!]!
        people_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Vehicle {
        id: ID!
        name: String!
        model: String
        manufacturer: String
        cost_in_credits: String
        length: String
        max_atmosphering_speed: String
        crew: String
        passengers: String
        cargo_capacity: String
        consumables: String
        vehicle_class: String
    
        # Relationships
        pilots: [Person!]!
        pilots_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Starship {
        id: ID!
        name: String!
        model: String
        manufacturer: String
        cost_in_credits: String
        length: String
        max_atmosphering_speed: String
        crew: String
        passengers: String
        cargo_capacity: String
        consumables: String
        hyperdrive_rating: String
        MGLT: String
        starship_class: String
    
        # Relationships
        pilots: [Person!]!
        pilots_ids: [ID!]!
        films: [Film!]!
        films_ids: [ID!]!
    }
    
    type Film {
      id: ID!
      title: String!
      episode_id: Int!
      opening_crawl: String!
      director: String!
      producer: String
      release_date: String!
    
      # Relationships
      characters: [Person!]!
      characters_ids: [ID!]!
      planets: [Planet!]!
      planets_ids: [ID!]!
      starships: [Starship!]!
      starships_ids: [ID!]!
      vehicles: [Vehicle!]!
      vehicles_ids: [ID!]!
      species: [Species!]!
      species_ids: [ID!]!
    }
    
    type Query {
      planet(id: ID!): Planet
      listPlanets(page: Int): [Planet!]!
      person(id: ID!): Person
      listPeople(page: Int): [Person!]!
      species(id: ID!): Species
      listSpecies(page: Int): [Species!]!
      film(id: ID!): Film
      listFilms(page: Int): [Film!]!
      starship(id: ID!): Starship
      listStarships(page: Int): [Starship!]!
      vehicle(id: ID!): Vehicle
      listVehicles(page: Int): [Vehicle!]!
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  simple-star-wars.graphql
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from dataclasses import dataclass
    from typing import Literal, TypeAlias

    Boolean: TypeAlias = bool
    """
    The `Boolean` scalar type represents `true` or `false`.
    """


    ID: TypeAlias = str
    """
    The `ID` scalar type represents a unique identifier, often used to refetch an object or as key for a cache. The ID type appears in a JSON response as a String; however, it is not intended to be human-readable. When expected as an input type, any string (such as `"4"`) or integer (such as `4`) input value will be accepted as an ID.
    """


    Int: TypeAlias = int
    """
    The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
    """


    String: TypeAlias = str
    """
    The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
    """


    @dataclass(order=True, slots=True)
    class Film:
        characters: list[Person]
        characters_ids: list[ID]
        director: String
        episode_id: Int
        id: ID
        opening_crawl: String
        planets: list[Planet]
        planets_ids: list[ID]
        release_date: String
        species: list[Species]
        species_ids: list[ID]
        starships: list[Starship]
        starships_ids: list[ID]
        title: String
        vehicles: list[Vehicle]
        vehicles_ids: list[ID]
        producer: String | None = None
        typename__: Literal["Film"] | None = "Film"


    @dataclass(order=True, slots=True)
    class Person:
        films: list[Film]
        films_ids: list[ID]
        id: ID
        name: String
        species: list[Species]
        species_ids: list[ID]
        starships: list[Starship]
        starships_ids: list[ID]
        vehicles: list[Vehicle]
        vehicles_ids: list[ID]
        birth_year: String | None = None
        eye_color: String | None = None
        gender: String | None = None
        hair_color: String | None = None
        height: Int | None = None
        homeworld: Planet | None = None
        homeworld_id: ID | None = None
        mass: Int | None = None
        skin_color: String | None = None
        typename__: Literal["Person"] | None = "Person"


    @dataclass(order=True, slots=True)
    class Planet:
        films: list[Film]
        films_ids: list[ID]
        id: ID
        name: String
        residents: list[Person]
        residents_ids: list[ID]
        climate: String | None = None
        diameter: String | None = None
        gravity: String | None = None
        orbital_period: String | None = None
        population: String | None = None
        rotation_period: String | None = None
        surface_water: String | None = None
        terrain: String | None = None
        typename__: Literal["Planet"] | None = "Planet"


    @dataclass(order=True, slots=True)
    class Species:
        films: list[Film]
        films_ids: list[ID]
        id: ID
        name: String
        people: list[Person]
        people_ids: list[ID]
        average_height: String | None = None
        average_lifespan: String | None = None
        classification: String | None = None
        designation: String | None = None
        eye_colors: String | None = None
        hair_colors: String | None = None
        language: String | None = None
        skin_colors: String | None = None
        typename__: Literal["Species"] | None = "Species"


    @dataclass(order=True, slots=True)
    class Starship:
        films: list[Film]
        films_ids: list[ID]
        id: ID
        name: String
        pilots: list[Person]
        pilots_ids: list[ID]
        MGLT: String | None = None
        cargo_capacity: String | None = None
        consumables: String | None = None
        cost_in_credits: String | None = None
        crew: String | None = None
        hyperdrive_rating: String | None = None
        length: String | None = None
        manufacturer: String | None = None
        max_atmosphering_speed: String | None = None
        model: String | None = None
        passengers: String | None = None
        starship_class: String | None = None
        typename__: Literal["Starship"] | None = "Starship"


    @dataclass(order=True, slots=True)
    class Vehicle:
        films: list[Film]
        films_ids: list[ID]
        id: ID
        name: String
        pilots: list[Person]
        pilots_ids: list[ID]
        cargo_capacity: String | None = None
        consumables: String | None = None
        cost_in_credits: String | None = None
        crew: String | None = None
        length: String | None = None
        manufacturer: String | None = None
        max_atmosphering_speed: String | None = None
        model: String | None = None
        passengers: String | None = None
        vehicle_class: String | None = None
        typename__: Literal["Vehicle"] | None = "Vehicle"
    ```

---

## `--duplicate-name-suffix` {#duplicate-name-suffix}

Customize suffix for duplicate model names.

The `--duplicate-name-suffix` flag allows specifying custom suffixes for
resolving duplicate names by type. The value is a JSON mapping where keys
are type names ('model', 'enum', 'default') and values are suffix strings.
For example, `{"model": "Schema"}` changes `Item1` to `ItemSchema`.

**Related:** [`--naming-strategy`](#naming-strategy)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --duplicate-name-suffix '{"model": "Schema"}' # (1)!
    ```

    1. :material-arrow-left: `--duplicate-name-suffix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "Order": {
          "type": "object",
          "properties": {
            "item": {
              "type": "object",
              "properties": {
                "name": {"type": "string"}
              }
            }
          }
        },
        "Cart": {
          "type": "object",
          "properties": {
            "item": {
              "type": "object",
              "properties": {
                "quantity": {"type": "integer"}
              }
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  input.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, RootModel


    class Model(RootModel[Any]):
        root: Any


    class Item(BaseModel):
        name: str | None = None


    class Order(BaseModel):
        item: Item | None = None


    class ItemSchema(BaseModel):
        quantity: int | None = None


    class Cart(BaseModel):
        item: ItemSchema | None = None
    ```

---

## `--enable-faux-immutability` {#enable-faux-immutability}

Enable faux immutability in Pydantic models (frozen=True).

The `--enable-faux-immutability` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --enable-faux-immutability # (1)!
    ```

    1. :material-arrow-left: `--enable-faux-immutability` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  api.yaml
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AnyUrl, BaseModel, ConfigDict, Field, RootModel


        class Pet(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            id: int
            name: str
            tag: str | None = None


        class Pets(RootModel[list[Pet]]):
            model_config = ConfigDict(
                frozen=True,
            )
            root: list[Pet]


        class User(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            id: int
            name: str
            tag: str | None = None


        class Users(RootModel[list[User]]):
            model_config = ConfigDict(
                frozen=True,
            )
            root: list[User]


        class Id(RootModel[str]):
            model_config = ConfigDict(
                frozen=True,
            )
            root: str


        class Rules(RootModel[list[str]]):
            model_config = ConfigDict(
                frozen=True,
            )
            root: list[str]


        class Error(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            code: int
            message: str


        class Api(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
            apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
            apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
            apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


        class Apis(RootModel[list[Api]]):
            model_config = ConfigDict(
                frozen=True,
            )
            root: list[Api]


        class Event(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            name: str | None = None


        class Result(BaseModel):
            model_config = ConfigDict(
                frozen=True,
            )
            event: Event | None = None
        ```

---

## `--force-optional` {#force-optional}

Force all fields to be Optional regardless of required status.

The `--force-optional` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --force-optional # (1)!
    ```

    1. :material-arrow-left: `--force-optional` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  api.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field, RootModel


    class Pet(BaseModel):
        id: int | None = 1
        name: str | None = None
        tag: str | None = None


    class Pets(RootModel[list[Pet] | None]):
        root: list[Pet] | None = None


    class User(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None


    class Users(RootModel[list[User] | None]):
        root: list[User] | None = None


    class Id(RootModel[str | None]):
        root: str | None = None


    class Rules(RootModel[list[str] | None]):
        root: list[str] | None = None


    class Error(BaseModel):
        code: int | None = None
        message: str | None = None


    class Api(BaseModel):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


    class Apis(RootModel[list[Api] | None]):
        root: list[Api] | None = None


    class Event(BaseModel):
        name: str | None = None


    class Result(BaseModel):
        event: Event | None = None
    ```

---

## `--frozen-dataclasses` {#frozen-dataclasses}

Generate frozen dataclasses with optional keyword-only fields.

The `--frozen-dataclasses` flag generates dataclass instances that are immutable
(frozen=True). Combined with `--keyword-only` (Python 3.10+), all fields become
keyword-only arguments.

**Related:** [`--keyword-only`](#keyword-only), [`--output-model-type`](#output-model-type)

**See also:** [Output Model Types](../output-model-types.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type dataclasses.dataclass --frozen-dataclasses # (1)!
    ```

    1. :material-arrow-left: `--frozen-dataclasses` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "title": "User",
      "properties": {
        "name": {
          "type": "string"
        },
        "age": {
          "type": "integer"
        },
        "email": {
          "type": "string",
          "format": "email"
        }
      },
      "required": ["name", "age"]
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  simple_frozen_test.json
    #   timestamp: 1985-10-26T08:21:00+00:00

    from __future__ import annotations

    from dataclasses import dataclass


    @dataclass(frozen=True)
    class User:
        name: str
        age: int
        email: str | None = None
    ```

---

## `--keep-model-order` {#keep-model-order}

Keep generated model order deterministic while respecting dependency constraints.

The `--keep-model-order` flag produces a stable, deterministic output order. The
generator starts from class-name order and only moves models when required to
satisfy dependency or runtime constraints (for example, a base class must appear
before its subclass, and cyclic groups must stay together).

This option is not equivalent to preserving the raw definition order from the
input schema, and it does not guarantee a strict unconditional alphabetical
order either. Inheritance and other runtime ordering requirements can force a
non-alphabetical arrangement. The value of the flag is that repeated runs on the
same schema produce the same ordering, which keeps diffs stable.

**Related:** [`--collapse-root-models`](#collapse-root-models)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --keep-model-order # (1)!
    ```

    1. :material-arrow-left: `--keep-model-order` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "title": "PersonsBestFriend",
        "description": "This is the main model.",
        "type": "object",
        "properties": {
          "people": {
            "title": "People",
            "type": "array",
            "items": {
              "$ref": "#/definitions/Person"
            }
          },
          "dogs": {
            "title": "Dogs",
            "type": "array",
            "items": {
              "$ref": "#/definitions/Dog"
            }
          },
          "dog_base": {
            "$ref": "#/definitions/DogBase"
          },
          "dog_relationships": {
            "$ref": "#/definitions/DogRelationships"
          },
          "person_base": {
            "$ref": "#/definitions/PersonBase"
          },
          "person_relationships": {
            "$ref": "#/definitions/PersonRelationships"
          }
        },
        "definitions": {
          "Person": {
            "title": "Person",
            "allOf": [
                {"$ref": "#/definitions/PersonBase"},
                {"$ref": "#/definitions/PersonRelationships"}
            ]
          },
          "Dog": {
            "title": "Dog",
            "allOf": [
                {"$ref": "#/definitions/DogBase"},
                {"$ref": "#/definitions/DogRelationships"}
            ]
          },
          "DogBase": {
            "title": "DogBase",
            "type": "object",
            "properties": {
              "name": {
                "title": "Name",
                "type": "string"
              },
              "woof": {
                "title": "Woof",
                "default": true,
                "type": "boolean"
              }
            }
          },
          "DogRelationships": {
            "title": "DogRelationships",
            "type": "object",
            "properties": {
              "people": {
                "title": "People",
                "type": "array",
                "items": {
                  "$ref": "#/definitions/Person"
                }
              }
            }
          },
          "PersonBase": {
            "title": "PersonBase",
            "type": "object",
            "properties": {
              "name": {
                "title": "Name",
                "type": "string"
              }
            }
          },
          "PersonRelationships": {
            "title": "PersonRelationships",
            "type": "object",
            "properties": {
              "people": {
                "title": "People",
                "type": "array",
                "items": {
                  "$ref": "#/definitions/Person"
                }
              }
            }
          }
        }
      }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  inheritance_forward_ref.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field


    class DogBase(BaseModel):
        name: str | None = Field(None, title="Name")
        woof: bool | None = Field(True, title="Woof")


    class DogRelationships(BaseModel):
        people: list[Person] | None = Field(None, title="People")


    class Dog(DogBase, DogRelationships):
        pass


    class PersonBase(BaseModel):
        name: str | None = Field(None, title="Name")


    class PersonRelationships(BaseModel):
        people: list[Person] | None = Field(None, title="People")


    class Person(PersonBase, PersonRelationships):
        pass


    class PersonsBestFriend(BaseModel):
        people: list[Person] | None = Field(None, title="People")
        dogs: list[Dog] | None = Field(None, title="Dogs")
        dog_base: DogBase | None = None
        dog_relationships: DogRelationships | None = None
        person_base: PersonBase | None = None
        person_relationships: PersonRelationships | None = None


    DogRelationships.model_rebuild()
    Dog.model_rebuild()
    PersonRelationships.model_rebuild()
    Person.model_rebuild()
    ```

---

## `--keyword-only` {#keyword-only}

Generate dataclasses with keyword-only fields (Python 3.10+).

The `--keyword-only` flag generates dataclasses where all fields must be
specified as keyword arguments (kw_only=True). This is only available for
Python 3.10+. When combined with `--frozen-dataclasses`, it creates immutable
dataclasses with keyword-only arguments, improving code clarity and preventing
positional argument errors.

**Related:** [`--frozen-dataclasses`](#frozen-dataclasses), [`--output-model-type`](#output-model-type), [`--target-python-version`](#target-python-version)

**See also:** [Output Model Types](../output-model-types.md)

**Option relationships:**

- **Requires:** [`--target-python-version`](model-customization.md#target-python-version) = `3.10+` - `--keyword-only` requires `--target-python-version` 3.10 or higher for dataclasses.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type dataclasses.dataclass --frozen-dataclasses --keyword-only --target-python-version 3.10 # (1)!
    ```

    1. :material-arrow-left: `--keyword-only` - the option documented here

??? example "Examples"

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
          "$schema": "http://json-schema.org/draft-07/schema#",
          "title": "Person",
          "type": "object",
          "properties": {
            "firstName": {
              "type": "string",
              "description": "The person's first name."
            },
            "lastName": {
              "type": ["string", "null"],
              "description": "The person's last name."
            },
            "age": {
              "description": "Age in years which must be equal to or greater than zero.",
              "type": "integer",
              "minimum": 0
            },
            "friends": {
              "type": "array"
            },
            "comment": {
              "type": "null"
            }
          }
        }
        ```

        **Output:**

        ```python
        # generated by datamodel-codegen:
        #   filename:  person.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from dataclasses import dataclass
        from typing import Any


        @dataclass(frozen=True, kw_only=True)
        class Person:
            firstName: str | None = None
            lastName: str | None = None
            age: int | None = None
            friends: list[Any] | None = None
            comment: None = None
        ```

    === "GraphQL"

        **Input Schema:**

        ```graphql
        type Person {
            id: ID!
            name: String!
            height: Int
            mass: Int
            hair_color: String
            skin_color: String
            eye_color: String
            birth_year: String
            gender: String
        
            # Relationships
            homeworld_id: ID
            homeworld: Planet
            species: [Species!]!
            species_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
            starships: [Starship!]!
            starships_ids: [ID!]!
            vehicles: [Vehicle!]!
            vehicles_ids: [ID!]!
        }
        
        type Planet {
            id: ID!
            name: String!
            rotation_period: String
            orbital_period: String
            diameter: String
            climate: String
            gravity: String
            terrain: String
            surface_water: String
            population: String
        
            # Relationships
            residents: [Person!]!
            residents_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Species {
            id: ID!
            name: String!
            classification: String
            designation: String
            average_height: String
            skin_colors: String
            hair_colors: String
            eye_colors: String
            average_lifespan: String
            language: String
        
            # Relationships
            people: [Person!]!
            people_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Vehicle {
            id: ID!
            name: String!
            model: String
            manufacturer: String
            cost_in_credits: String
            length: String
            max_atmosphering_speed: String
            crew: String
            passengers: String
            cargo_capacity: String
            consumables: String
            vehicle_class: String
        
            # Relationships
            pilots: [Person!]!
            pilots_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Starship {
            id: ID!
            name: String!
            model: String
            manufacturer: String
            cost_in_credits: String
            length: String
            max_atmosphering_speed: String
            crew: String
            passengers: String
            cargo_capacity: String
            consumables: String
            hyperdrive_rating: String
            MGLT: String
            starship_class: String
        
            # Relationships
            pilots: [Person!]!
            pilots_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Film {
          id: ID!
          title: String!
          episode_id: Int!
          opening_crawl: String!
          director: String!
          producer: String
          release_date: String!
        
          # Relationships
          characters: [Person!]!
          characters_ids: [ID!]!
          planets: [Planet!]!
          planets_ids: [ID!]!
          starships: [Starship!]!
          starships_ids: [ID!]!
          vehicles: [Vehicle!]!
          vehicles_ids: [ID!]!
          species: [Species!]!
          species_ids: [ID!]!
        }
        
        type Query {
          planet(id: ID!): Planet
          listPlanets(page: Int): [Planet!]!
          person(id: ID!): Person
          listPeople(page: Int): [Person!]!
          species(id: ID!): Species
          listSpecies(page: Int): [Species!]!
          film(id: ID!): Film
          listFilms(page: Int): [Film!]!
          starship(id: ID!): Starship
          listStarships(page: Int): [Starship!]!
          vehicle(id: ID!): Vehicle
          listVehicles(page: Int): [Vehicle!]!
        }
        ```

        **Output:**

        ```python
        # generated by datamodel-codegen:
        #   filename:  simple-star-wars.graphql
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from dataclasses import dataclass
        from typing import Literal, TypeAlias

        Boolean: TypeAlias = bool
        """
        The `Boolean` scalar type represents `true` or `false`.
        """


        ID: TypeAlias = str
        """
        The `ID` scalar type represents a unique identifier, often used to refetch an object or as key for a cache. The ID type appears in a JSON response as a String; however, it is not intended to be human-readable. When expected as an input type, any string (such as `"4"`) or integer (such as `4`) input value will be accepted as an ID.
        """


        Int: TypeAlias = int
        """
        The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
        """


        String: TypeAlias = str
        """
        The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
        """


        @dataclass(frozen=True, kw_only=True)
        class Film:
            characters: list[Person]
            characters_ids: list[ID]
            director: String
            episode_id: Int
            id: ID
            opening_crawl: String
            planets: list[Planet]
            planets_ids: list[ID]
            release_date: String
            species: list[Species]
            species_ids: list[ID]
            starships: list[Starship]
            starships_ids: list[ID]
            title: String
            vehicles: list[Vehicle]
            vehicles_ids: list[ID]
            producer: String | None = None
            typename__: Literal["Film"] | None = "Film"


        @dataclass(frozen=True, kw_only=True)
        class Person:
            films: list[Film]
            films_ids: list[ID]
            id: ID
            name: String
            species: list[Species]
            species_ids: list[ID]
            starships: list[Starship]
            starships_ids: list[ID]
            vehicles: list[Vehicle]
            vehicles_ids: list[ID]
            birth_year: String | None = None
            eye_color: String | None = None
            gender: String | None = None
            hair_color: String | None = None
            height: Int | None = None
            homeworld: Planet | None = None
            homeworld_id: ID | None = None
            mass: Int | None = None
            skin_color: String | None = None
            typename__: Literal["Person"] | None = "Person"


        @dataclass(frozen=True, kw_only=True)
        class Planet:
            films: list[Film]
            films_ids: list[ID]
            id: ID
            name: String
            residents: list[Person]
            residents_ids: list[ID]
            climate: String | None = None
            diameter: String | None = None
            gravity: String | None = None
            orbital_period: String | None = None
            population: String | None = None
            rotation_period: String | None = None
            surface_water: String | None = None
            terrain: String | None = None
            typename__: Literal["Planet"] | None = "Planet"


        @dataclass(frozen=True, kw_only=True)
        class Species:
            films: list[Film]
            films_ids: list[ID]
            id: ID
            name: String
            people: list[Person]
            people_ids: list[ID]
            average_height: String | None = None
            average_lifespan: String | None = None
            classification: String | None = None
            designation: String | None = None
            eye_colors: String | None = None
            hair_colors: String | None = None
            language: String | None = None
            skin_colors: String | None = None
            typename__: Literal["Species"] | None = "Species"


        @dataclass(frozen=True, kw_only=True)
        class Starship:
            films: list[Film]
            films_ids: list[ID]
            id: ID
            name: String
            pilots: list[Person]
            pilots_ids: list[ID]
            MGLT: String | None = None
            cargo_capacity: String | None = None
            consumables: String | None = None
            cost_in_credits: String | None = None
            crew: String | None = None
            hyperdrive_rating: String | None = None
            length: String | None = None
            manufacturer: String | None = None
            max_atmosphering_speed: String | None = None
            model: String | None = None
            passengers: String | None = None
            starship_class: String | None = None
            typename__: Literal["Starship"] | None = "Starship"


        @dataclass(frozen=True, kw_only=True)
        class Vehicle:
            films: list[Film]
            films_ids: list[ID]
            id: ID
            name: String
            pilots: list[Person]
            pilots_ids: list[ID]
            cargo_capacity: String | None = None
            consumables: String | None = None
            cost_in_credits: String | None = None
            crew: String | None = None
            length: String | None = None
            manufacturer: String | None = None
            max_atmosphering_speed: String | None = None
            model: String | None = None
            passengers: String | None = None
            vehicle_class: String | None = None
            typename__: Literal["Vehicle"] | None = "Vehicle"
        ```

---

## `--model-extra-keys` {#model-extra-keys}

Add model-level schema extensions to ConfigDict json_schema_extra.

The `--model-extra-keys` flag adds specified x-* extensions from the schema
to the model's ConfigDict json_schema_extra.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --model-extra-keys x-custom-metadata # (1)!
    ```

    1. :material-arrow-left: `--model-extra-keys` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "ModelExtras",
      "type": "object",
      "x-custom-metadata": {"key1": "value1"},
      "x-version": 1,
      "properties": {
        "name": {"type": "string"}
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  model_extras.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, ConfigDict


        class ModelExtras(BaseModel):
            model_config = ConfigDict(
                json_schema_extra={"x-custom-metadata": {"key1": "value1"}},
            )
            name: str | None = None
        ```

---

## `--model-extra-keys-without-x-prefix` {#model-extra-keys-without-x-prefix}

Strip x- prefix from model-level schema extensions and add to ConfigDict json_schema_extra.

The `--model-extra-keys-without-x-prefix` flag adds specified x-* extensions
from the schema to the model's ConfigDict json_schema_extra with the x- prefix stripped.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --model-extra-keys-without-x-prefix x-custom-metadata x-version # (1)!
    ```

    1. :material-arrow-left: `--model-extra-keys-without-x-prefix` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "ModelExtras",
      "type": "object",
      "x-custom-metadata": {"key1": "value1"},
      "x-version": 1,
      "properties": {
        "name": {"type": "string"}
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  model_extras.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, ConfigDict


        class ModelExtras(BaseModel):
            model_config = ConfigDict(
                json_schema_extra={"custom-metadata": {"key1": "value1"}, "version": 1},
            )
            name: str | None = None
        ```

---

## `--model-name-map` {#model-name-map}

Rename generated model classes from a JSON mapping.

The `--model-name-map` option applies explicit class names to generated models.
Mapping keys can be canonical schema refs such as `#/definitions/Foo` or the
current generated class name such as `Foo1`. Values are final Python class names.

This is useful when a schema cannot be edited but generated model names must be
stable for public APIs or downstream code. Colliding mapped names fail instead
of being silently suffixed.

**Related:** [`--naming-strategy`](#naming-strategy), [`--use-title-as-name`](field-customization.md#use-title-as-name)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --model-name-map '{"#/definitions/Foo": "RenamedFoo", "Bar": "RenamedBar", "model-name": "OriginalMapped"}' # (1)!
    ```

    1. :material-arrow-left: `--model-name-map` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "item": {
          "$ref": "#/definitions/Foo"
        },
        "related": {
          "$ref": "#/definitions/Bar"
        },
        "original": {
          "$ref": "#/definitions/model-name"
        }
      },
      "definitions": {
        "Foo": {
          "type": "object",
          "properties": {
            "value": {
              "type": "string"
            }
          }
        },
        "Bar": {
          "type": "object",
          "properties": {
            "foo": {
              "$ref": "#/definitions/Foo"
            }
          }
        },
        "model-name": {
          "type": "object",
          "properties": {
            "bar": {
              "$ref": "#/definitions/Bar"
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  model_name_map.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class RenamedFoo(BaseModel):
        value: str | None = None


    class RenamedBar(BaseModel):
        foo: RenamedFoo | None = None


    class OriginalMapped(BaseModel):
        bar: RenamedBar | None = None


    class Model(BaseModel):
        item: RenamedFoo | None = None
        related: RenamedBar | None = None
        original: OriginalMapped | None = None
    ```

---

## `--naming-strategy` {#naming-strategy}

Use parent-prefixed naming strategy for duplicate model names.

The `--naming-strategy parent-prefixed` flag prefixes model names with their
parent model name when duplicates occur. For example, if both `Order` and
`Cart` have an inline `Item` definition, they become `OrderItem` and `CartItem`.

**Related:** [`--duplicate-name-suffix`](#duplicate-name-suffix), [`--parent-scoped-naming`](#parent-scoped-naming)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --naming-strategy parent-prefixed # (1)!
    ```

    1. :material-arrow-left: `--naming-strategy` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "Order": {
          "type": "object",
          "properties": {
            "item": {
              "type": "object",
              "properties": {
                "name": {"type": "string"}
              }
            }
          }
        },
        "Cart": {
          "type": "object",
          "properties": {
            "item": {
              "type": "object",
              "properties": {
                "quantity": {"type": "integer"}
              }
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  input.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, RootModel


    class Model(RootModel[Any]):
        root: Any


    class ModelOrderItem(BaseModel):
        name: str | None = None


    class ModelOrder(BaseModel):
        item: ModelOrderItem | None = None


    class ModelCartItem(BaseModel):
        quantity: int | None = None


    class ModelCart(BaseModel):
        item: ModelCartItem | None = None
    ```

---

## `--output-model-type` {#output-model-type}

Select the output model type (Pydantic v2, Pydantic v2 dataclass,
dataclasses, TypedDict, msgspec).

The `--output-model-type` flag specifies which Python data model framework to use
for the generated code. Supported values include `pydantic_v2.BaseModel`,
`pydantic_v2.dataclass`, `dataclasses.dataclass`, `typing.TypedDict`, and `msgspec.Struct`.

**See also:** [Output Model Types](../output-model-types.md)

**Option relationships:**

- **Implies:** When `--output-model-type=msgspec.Struct`, [`--use-annotated`](typing-customization.md#use-annotated) enabled

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--output-model-type` - the option documented here

??? example "Examples"

    === "JSON Schema"

        **Input Schema:**

        ```json
        {
            "$schema": "http://json-schema.org/schema#",
            "type": "object",
            "properties": {
                "my_obj": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "items": {
                                "type": [
                                    "array",
                                    "null"
                                ]
                            }
                        },
                        "required": [
                            "items"
                        ]
                    }
                }
            },
            "required": [
                "my_obj"
            ]
        }
        ```

        **Output:**

        === "Pydantic v2"

            ```python
            # generated by datamodel-codegen:
            #   filename:  null_and_array.json
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Any

            from pydantic import BaseModel


            class MyObjItem(BaseModel):
                items: list[Any] | None


            class Model(BaseModel):
                my_obj: list[MyObjItem]
            ```

    === "GraphQL"

        **Input Schema:**

        ```graphql
        type Person {
            id: ID!
            name: String!
            height: Int
            mass: Int
            hair_color: String
            skin_color: String
            eye_color: String
            birth_year: String
            gender: String
        
            # Relationships
            homeworld_id: ID
            homeworld: Planet
            species: [Species!]!
            species_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
            starships: [Starship!]!
            starships_ids: [ID!]!
            vehicles: [Vehicle!]!
            vehicles_ids: [ID!]!
        }
        
        type Planet {
            id: ID!
            name: String!
            rotation_period: String
            orbital_period: String
            diameter: String
            climate: String
            gravity: String
            terrain: String
            surface_water: String
            population: String
        
            # Relationships
            residents: [Person!]!
            residents_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Species {
            id: ID!
            name: String!
            classification: String
            designation: String
            average_height: String
            skin_colors: String
            hair_colors: String
            eye_colors: String
            average_lifespan: String
            language: String
        
            # Relationships
            people: [Person!]!
            people_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Vehicle {
            id: ID!
            name: String!
            model: String
            manufacturer: String
            cost_in_credits: String
            length: String
            max_atmosphering_speed: String
            crew: String
            passengers: String
            cargo_capacity: String
            consumables: String
            vehicle_class: String
        
            # Relationships
            pilots: [Person!]!
            pilots_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Starship {
            id: ID!
            name: String!
            model: String
            manufacturer: String
            cost_in_credits: String
            length: String
            max_atmosphering_speed: String
            crew: String
            passengers: String
            cargo_capacity: String
            consumables: String
            hyperdrive_rating: String
            MGLT: String
            starship_class: String
        
            # Relationships
            pilots: [Person!]!
            pilots_ids: [ID!]!
            films: [Film!]!
            films_ids: [ID!]!
        }
        
        type Film {
          id: ID!
          title: String!
          episode_id: Int!
          opening_crawl: String!
          director: String!
          producer: String
          release_date: String!
        
          # Relationships
          characters: [Person!]!
          characters_ids: [ID!]!
          planets: [Planet!]!
          planets_ids: [ID!]!
          starships: [Starship!]!
          starships_ids: [ID!]!
          vehicles: [Vehicle!]!
          vehicles_ids: [ID!]!
          species: [Species!]!
          species_ids: [ID!]!
        }
        
        type Query {
          planet(id: ID!): Planet
          listPlanets(page: Int): [Planet!]!
          person(id: ID!): Person
          listPeople(page: Int): [Person!]!
          species(id: ID!): Species
          listSpecies(page: Int): [Species!]!
          film(id: ID!): Film
          listFilms(page: Int): [Film!]!
          starship(id: ID!): Starship
          listStarships(page: Int): [Starship!]!
          vehicle(id: ID!): Vehicle
          listVehicles(page: Int): [Vehicle!]!
        }
        ```

        **Output:**

        === "Pydantic v2"

            ```python
            # generated by datamodel-codegen:
            #   filename:  simple-star-wars.graphql
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from typing import Literal

            from pydantic import BaseModel, Field
            from typing_extensions import TypeAliasType

            Boolean = TypeAliasType("Boolean", bool)
            """
            The `Boolean` scalar type represents `true` or `false`.
            """


            ID = TypeAliasType("ID", str)
            """
            The `ID` scalar type represents a unique identifier, often used to refetch an object or as key for a cache. The ID type appears in a JSON response as a String; however, it is not intended to be human-readable. When expected as an input type, any string (such as `"4"`) or integer (such as `4`) input value will be accepted as an ID.
            """


            Int = TypeAliasType("Int", int)
            """
            The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
            """


            String = TypeAliasType("String", str)
            """
            The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
            """


            class Film(BaseModel):
                characters: list[Person]
                characters_ids: list[ID]
                director: String
                episode_id: Int
                id: ID
                opening_crawl: String
                planets: list[Planet]
                planets_ids: list[ID]
                producer: String | None = None
                release_date: String
                species: list[Species]
                species_ids: list[ID]
                starships: list[Starship]
                starships_ids: list[ID]
                title: String
                vehicles: list[Vehicle]
                vehicles_ids: list[ID]
                typename__: Literal["Film"] | None = Field("Film", alias="__typename")


            class Person(BaseModel):
                birth_year: String | None = None
                eye_color: String | None = None
                films: list[Film]
                films_ids: list[ID]
                gender: String | None = None
                hair_color: String | None = None
                height: Int | None = None
                homeworld: Planet | None = None
                homeworld_id: ID | None = None
                id: ID
                mass: Int | None = None
                name: String
                skin_color: String | None = None
                species: list[Species]
                species_ids: list[ID]
                starships: list[Starship]
                starships_ids: list[ID]
                vehicles: list[Vehicle]
                vehicles_ids: list[ID]
                typename__: Literal["Person"] | None = Field("Person", alias="__typename")


            class Planet(BaseModel):
                climate: String | None = None
                diameter: String | None = None
                films: list[Film]
                films_ids: list[ID]
                gravity: String | None = None
                id: ID
                name: String
                orbital_period: String | None = None
                population: String | None = None
                residents: list[Person]
                residents_ids: list[ID]
                rotation_period: String | None = None
                surface_water: String | None = None
                terrain: String | None = None
                typename__: Literal["Planet"] | None = Field("Planet", alias="__typename")


            class Species(BaseModel):
                average_height: String | None = None
                average_lifespan: String | None = None
                classification: String | None = None
                designation: String | None = None
                eye_colors: String | None = None
                films: list[Film]
                films_ids: list[ID]
                hair_colors: String | None = None
                id: ID
                language: String | None = None
                name: String
                people: list[Person]
                people_ids: list[ID]
                skin_colors: String | None = None
                typename__: Literal["Species"] | None = Field("Species", alias="__typename")


            class Starship(BaseModel):
                MGLT: String | None = None
                cargo_capacity: String | None = None
                consumables: String | None = None
                cost_in_credits: String | None = None
                crew: String | None = None
                films: list[Film]
                films_ids: list[ID]
                hyperdrive_rating: String | None = None
                id: ID
                length: String | None = None
                manufacturer: String | None = None
                max_atmosphering_speed: String | None = None
                model: String | None = None
                name: String
                passengers: String | None = None
                pilots: list[Person]
                pilots_ids: list[ID]
                starship_class: String | None = None
                typename__: Literal["Starship"] | None = Field("Starship", alias="__typename")


            class Vehicle(BaseModel):
                cargo_capacity: String | None = None
                consumables: String | None = None
                cost_in_credits: String | None = None
                crew: String | None = None
                films: list[Film]
                films_ids: list[ID]
                id: ID
                length: String | None = None
                manufacturer: String | None = None
                max_atmosphering_speed: String | None = None
                model: String | None = None
                name: String
                passengers: String | None = None
                pilots: list[Person]
                pilots_ids: list[ID]
                vehicle_class: String | None = None
                typename__: Literal["Vehicle"] | None = Field("Vehicle", alias="__typename")


            Film.model_rebuild()
            Person.model_rebuild()
            ```

        === "dataclass"

            ```python
            # generated by datamodel-codegen:
            #   filename:  simple-star-wars.graphql
            #   timestamp: 2019-07-26T00:00:00+00:00

            from __future__ import annotations

            from dataclasses import dataclass
            from typing import Literal, TypeAlias

            Boolean: TypeAlias = bool
            """
            The `Boolean` scalar type represents `true` or `false`.
            """


            ID: TypeAlias = str
            """
            The `ID` scalar type represents a unique identifier, often used to refetch an object or as key for a cache. The ID type appears in a JSON response as a String; however, it is not intended to be human-readable. When expected as an input type, any string (such as `"4"`) or integer (such as `4`) input value will be accepted as an ID.
            """


            Int: TypeAlias = int
            """
            The `Int` scalar type represents non-fractional signed whole numeric values. Int can represent values between -(2^31) and 2^31 - 1.
            """


            String: TypeAlias = str
            """
            The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
            """


            @dataclass
            class Film:
                characters: list[Person]
                characters_ids: list[ID]
                director: String
                episode_id: Int
                id: ID
                opening_crawl: String
                planets: list[Planet]
                planets_ids: list[ID]
                release_date: String
                species: list[Species]
                species_ids: list[ID]
                starships: list[Starship]
                starships_ids: list[ID]
                title: String
                vehicles: list[Vehicle]
                vehicles_ids: list[ID]
                producer: String | None = None
                typename__: Literal["Film"] | None = "Film"


            @dataclass
            class Person:
                films: list[Film]
                films_ids: list[ID]
                id: ID
                name: String
                species: list[Species]
                species_ids: list[ID]
                starships: list[Starship]
                starships_ids: list[ID]
                vehicles: list[Vehicle]
                vehicles_ids: list[ID]
                birth_year: String | None = None
                eye_color: String | None = None
                gender: String | None = None
                hair_color: String | None = None
                height: Int | None = None
                homeworld: Planet | None = None
                homeworld_id: ID | None = None
                mass: Int | None = None
                skin_color: String | None = None
                typename__: Literal["Person"] | None = "Person"


            @dataclass
            class Planet:
                films: list[Film]
                films_ids: list[ID]
                id: ID
                name: String
                residents: list[Person]
                residents_ids: list[ID]
                climate: String | None = None
                diameter: String | None = None
                gravity: String | None = None
                orbital_period: String | None = None
                population: String | None = None
                rotation_period: String | None = None
                surface_water: String | None = None
                terrain: String | None = None
                typename__: Literal["Planet"] | None = "Planet"


            @dataclass
            class Species:
                films: list[Film]
                films_ids: list[ID]
                id: ID
                name: String
                people: list[Person]
                people_ids: list[ID]
                average_height: String | None = None
                average_lifespan: String | None = None
                classification: String | None = None
                designation: String | None = None
                eye_colors: String | None = None
                hair_colors: String | None = None
                language: String | None = None
                skin_colors: String | None = None
                typename__: Literal["Species"] | None = "Species"


            @dataclass
            class Starship:
                films: list[Film]
                films_ids: list[ID]
                id: ID
                name: String
                pilots: list[Person]
                pilots_ids: list[ID]
                MGLT: String | None = None
                cargo_capacity: String | None = None
                consumables: String | None = None
                cost_in_credits: String | None = None
                crew: String | None = None
                hyperdrive_rating: String | None = None
                length: String | None = None
                manufacturer: String | None = None
                max_atmosphering_speed: String | None = None
                model: String | None = None
                passengers: String | None = None
                starship_class: String | None = None
                typename__: Literal["Starship"] | None = "Starship"


            @dataclass
            class Vehicle:
                films: list[Film]
                films_ids: list[ID]
                id: ID
                name: String
                pilots: list[Person]
                pilots_ids: list[ID]
                cargo_capacity: String | None = None
                consumables: String | None = None
                cost_in_credits: String | None = None
                crew: String | None = None
                length: String | None = None
                manufacturer: String | None = None
                max_atmosphering_speed: String | None = None
                model: String | None = None
                passengers: String | None = None
                vehicle_class: String | None = None
                typename__: Literal["Vehicle"] | None = "Vehicle"
            ```

---

## `--parent-scoped-naming` {#parent-scoped-naming}

Namespace models by their parent scope to avoid naming conflicts.

The `--parent-scoped-naming` flag prefixes model names with their parent scope
(operation/path/parameter) to prevent name collisions when the same model name
appears in different contexts within an OpenAPI specification.

**Deprecated:** --parent-scoped-naming is deprecated. Use --naming-strategy parent-prefixed instead.

**Option relationships:**

- **Implies:** [`--naming-strategy`](model-customization.md#naming-strategy) = `parent-prefixed`

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --parent-scoped-naming --use-operation-id-as-name --openapi-scopes paths schemas parameters # (1)!
    ```

    1. :material-arrow-left: `--parent-scoped-naming` - the option documented here

??? example "Examples"

    **Input Schema:**

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
          summary: Get pet
          operationId: getPets
          responses:
            '200':
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Pet"
      /cars:
        get:
          summary: Get car
          operationId: getCar
          responses:
            '200':
              content:
                application/json:
                  schema:
                    $ref: "#/components/schemas/Cars"
    
    components:
      schemas:
        Pet:
          required:
            - id
            - name
            - type
          properties:
            id:
              type: integer
              format: int64
            name:
              type: string
            tag:
              type: string
            type:
              type: string
              enum: [ 'pet' ]
            details:
              type: object
              properties:
                race: { type: string }
        Car:
          required:
            - id
            - name
            - type
          properties:
            id:
              type: integer
              format: int64
            name:
              type: string
            tag:
              type: string
            type:
              type: string
              enum: [ 'car' ]
            details:
              type: object
              properties:
                brand: { type: string }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  duplicate_models2.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Any

    from pydantic import BaseModel, RootModel


    class PetType(Enum):
        pet = "pet"


    class PetDetails(BaseModel):
        race: str | None = None


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None = None
        type: PetType
        details: PetDetails | None = None


    class CarType(Enum):
        car = "car"


    class CarDetails(BaseModel):
        brand: str | None = None


    class Car(BaseModel):
        id: int
        name: str
        tag: str | None = None
        type: CarType
        details: CarDetails | None = None


    class Cars(RootModel[Any]):
        root: Any
    ```

---

## `--reuse-model` {#reuse-model}

Reuse identical model definitions instead of generating duplicates.

The `--reuse-model` flag detects identical enum or model definitions
across the schema and generates a single shared definition, reducing
code duplication in the output.

**Related:** [`--collapse-root-models`](#collapse-root-models)

**See also:** [Model Reuse and Deduplication](../model-reuse.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --reuse-model # (1)!
    ```

    1. :material-arrow-left: `--reuse-model` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema",
      "title": "User",
      "type": "object",
      "properties": {
        "name": {
          "type": "string"
        },
        "animal": {
          "type": "string",
          "enum": [
            "dog",
            "cat",
            "snake"
          ],
          "default": "dog"
        },
        "pet": {
          "type": "string",
          "enum": [
            "dog",
            "cat",
            "snake"
          ],
          "default": "cat"
        },
        "redistribute": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "static",
              "connected"
            ]
          }
        }
      },
      "definitions": {
        "redistribute": {
          "type": "array",
          "items": {
            "type": "string",
            "enum": [
              "static",
              "connected"
            ]
          },
          "description": "Redistribute type for routes."
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  duplicate_enum.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from pydantic import BaseModel, Field, RootModel


    class Animal(Enum):
        dog = "dog"
        cat = "cat"
        snake = "snake"


    class RedistributeEnum(Enum):
        static = "static"
        connected = "connected"


    class User(BaseModel):
        name: str | None = None
        animal: Animal | None = "dog"
        pet: Animal | None = "cat"
        redistribute: list[RedistributeEnum] | None = None


    class Redistribute(RootModel[list[RedistributeEnum]]):
        root: list[RedistributeEnum] = Field(..., description="Redistribute type for routes.")
    ```

---

## `--reuse-scope` {#reuse-scope}

Scope for model reuse detection (root or tree).

The `--reuse-scope` flag configures the code generation behavior.

**See also:** [Model Reuse and Deduplication](../model-reuse.md)

**Option relationships:**

- **Requires:** When `--reuse-scope=tree`, [`--reuse-model`](model-customization.md#reuse-model) enabled - `--reuse-scope=tree` has no effect without `--reuse-model`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --reuse-model --reuse-scope tree # (1)!
    ```

    1. :material-arrow-left: `--reuse-scope` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    # schema_a.json
    {
      "type": "object",
      "properties": {
        "data": { "$ref": "#/$defs/SharedModel" }
      },
      "$defs": {
        "SharedModel": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "name": { "type": "string" }
          }
        }
      }
    }
    
    # schema_b.json
    {
      "type": "object",
      "properties": {
        "info": { "$ref": "#/$defs/SharedModel" }
      },
      "$defs": {
        "SharedModel": {
          "type": "object",
          "properties": {
            "id": { "type": "integer" },
            "name": { "type": "string" }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # __init__.py
    # generated by datamodel-codegen:
    #   filename:  reuse_scope_tree
    #   timestamp: 2019-07-26T00:00:00+00:00

    # schema_a.py
    # generated by datamodel-codegen:
    #   filename:  reuse_scope_tree
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel

    from .shared import SharedModel as SharedModel_1


    class SharedModel(SharedModel_1):
        pass


    class Model(BaseModel):
        data: SharedModel | None = None


    # schema_b.py
    # generated by datamodel-codegen:
    #   filename:  schema_b.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel

    from . import shared


    class Model(BaseModel):
        info: shared.SharedModel | None = None


    # shared.py
    # generated by datamodel-codegen:
    #   filename:  shared.py
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class SharedModel(BaseModel):
        id: int | None = None
        name: str | None = None
    ```

---

## `--skip-root-model` {#skip-root-model}

Skip generation of root model when schema contains nested definitions.

The `--skip-root-model` flag prevents generating a model for the root schema object
when the schema primarily contains reusable definitions. This is useful when the root
object is just a container for $defs and not a meaningful model itself.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --skip-root-model # (1)!
    ```

    1. :material-arrow-left: `--skip-root-model` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "_Placeholder",
      "type": "null",
      "$defs": {
        "Person": {
          "type": "object",
          "properties": {
            "name": {"type": "string"},
            "age": {"type": "integer"}
          },
          "required": ["name"]
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  skip_root_model_test.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Person(BaseModel):
        name: str
        age: int | None = None
    ```

---

## `--strict-nullable` {#strict-nullable}

Treat default field as a non-nullable field.

The `--strict-nullable` flag ensures that fields with default values are generated
with their exact schema type (non-nullable), rather than being made nullable.

This is particularly useful when combined with `--use-default` to generate models
where optional fields have defaults but cannot accept `None` values.

**Related:** [`--use-default`](#use-default)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --strict-nullable # (1)!
    ```

    1. :material-arrow-left: `--strict-nullable` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.0.3
    info:
      version: 1.0.0
      title: testapi
      license:
        name: proprietary
    servers: []
    paths: {}
    components:
      schemas:
        TopLevel:
          type: object
          properties:
            cursors:
              type: object
              properties:
                prev:
                  type: string
                  nullable: true
                next:
                  type: string
                  default: last
                index:
                  type: number
                tag:
                  type: string
              required:
              - prev
              - index
          required:
          - cursors
        User:
          type: object
          properties:
            info:
              type: object
              properties:
                name:
                  type: string
              required:
                - name
          required:
            - info
        apis:
          type: array
          nullable: true
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
                nullable: true
              apiDocumentationUrl:
                type: string
                format: uri
                description: A URL to the API console for each API
                nullable: true
        email:
          type: array
          items:
            type: object
            properties:
              author:
                type: string
              address:
                type: string
                description: email address
              description:
                type: string
                default: empty
              tag:
                type: string
            required:
              - author
              - address
        id:
          type: integer
          default: 1
        description:
          type: string
          nullable: true
          default: example
        name:
          type: string
          nullable: true
        tag:
          type: string
        notes:
          type: object
          properties:
            comments:
              type: array
              items:
                  type: string
              default_factory: list
              nullable: false
        options:
          type: object
          properties:
            comments:
              type: array
              items:
                  type: string
                  nullable: true
            oneOfComments:
               type: array
               items:
                   oneOf:
                    - type: string
                    - type: number
                   nullable: true
            simpleUnion:
              oneOf:
                - type: string
                - type: number
          required:
            - comments
            - oneOfComments
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  nullable.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field, RootModel


    class Cursors(BaseModel):
        prev: str | None = Field(...)
        next: str = "last"
        index: float
        tag: str | None = None


    class TopLevel(BaseModel):
        cursors: Cursors


    class Info(BaseModel):
        name: str


    class User(BaseModel):
        info: Info


    class Api(BaseModel):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")


    class Apis(RootModel[list[Api] | None]):
        root: list[Api] | None = Field(...)


    class EmailItem(BaseModel):
        author: str
        address: str = Field(..., description="email address")
        description: str = "empty"
        tag: str | None = None


    class Email(RootModel[list[EmailItem]]):
        root: list[EmailItem]


    class Id(RootModel[int]):
        root: int = 1


    class Description(RootModel[str | None]):
        root: str | None = "example"


    class Name(RootModel[str | None]):
        root: str | None = None


    class Tag(RootModel[str]):
        root: str


    class Notes(BaseModel):
        comments: list[str] = Field(default_factory=list)


    class Options(BaseModel):
        comments: list[str | None]
        oneOfComments: list[str | float | None]
        simpleUnion: str | float | None = None
    ```

---

## `--strip-default-none` {#strip-default-none}

Remove fields with None as default value from generated models.

The `--strip-default-none` option removes fields that have None as their default value from the
generated models. This results in cleaner model definitions by excluding optional fields that
default to None.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --strip-default-none # (1)!
    ```

    1. :material-arrow-left: `--strip-default-none` - the option documented here

??? example "Examples"

    **Input Schema:**

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

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  api.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import AnyUrl, BaseModel, Field, RootModel


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None


    class Pets(RootModel[list[Pet]]):
        root: list[Pet]


    class User(BaseModel):
        id: int
        name: str
        tag: str | None


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
        apiKey: str | None = Field(description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(description="A URL to the API console for each API")


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    class Event(BaseModel):
        name: str | None


    class Result(BaseModel):
        event: Event | None
    ```

---

## `--target-pydantic-version` {#target-pydantic-version}

Target Pydantic version for generated code compatibility.

The `--target-pydantic-version` flag controls Pydantic version-specific config:

- **2**: Uses `populate_by_name=True` (compatible with Pydantic 2.0-2.10)
- **2.11**: Uses `validate_by_name=True` (for Pydantic 2.11+)
- **2.12**: Uses `validate_by_name=True` and allows features that require Pydantic 2.12+

This prevents breaking changes when generated code is used on older Pydantic versions.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --target-pydantic-version 2.11 --allow-population-by-field-name --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--target-pydantic-version` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string",
          "description": "The person's first name."
        },
        "lastName": {
          "type": ["string", "null"],
          "description": "The person's last name."
        },
        "age": {
          "description": "Age in years which must be equal to or greater than zero.",
          "type": "integer",
          "minimum": 0
        },
        "friends": {
          "type": "array"
        },
        "comment": {
          "type": "null"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  person.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, ConfigDict, Field, conint


    class Person(BaseModel):
        model_config = ConfigDict(
            validate_by_name=True,
        )
        firstName: str | None = Field(None, description="The person's first name.")
        lastName: str | None = Field(None, description="The person's last name.")
        age: conint(ge=0) | None = Field(None, description="Age in years which must be equal to or greater than zero.")
        friends: list[Any] | None = None
        comment: None = Field(None)
    ```

---

## `--target-python-version` {#target-python-version}

Target Python version for generated code syntax and imports.

The `--target-python-version` flag controls Python version-specific syntax:

- **Python 3.10-3.11**: Uses `X | None` union operator, `TypeAlias` annotation
- **Python 3.12+**: Uses `type` statement for type aliases

This affects import statements and type annotation syntax in generated code.

**See also:** [CI/CD Integration](../ci-cd.md), [Output Model Types](../output-model-types.md), [Python Version Compatibility](../python-version-compatibility.md), [Root Models and Type Aliases](../root-model-and-type-alias.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --target-python-version 3.10 --use-standard-collections # (1)!
    ```

    1. :material-arrow-left: `--target-python-version` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Person",
      "type": "object",
      "properties": {
        "firstName": {
          "type": "string",
          "description": "The person's first name."
        },
        "lastName": {
          "type": ["string", "null"],
          "description": "The person's last name."
        },
        "age": {
          "description": "Age in years which must be equal to or greater than zero.",
          "type": "integer",
          "minimum": 0
        },
        "friends": {
          "type": "array"
        },
        "comment": {
          "type": "null"
        }
      }
    }
    ```

    **Output:**

    === "Python 3.10"

        ```python
        # generated by datamodel-codegen:
        #   filename:  person.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from typing import Any

        from pydantic import BaseModel, Field, conint


        class Person(BaseModel):
            firstName: str | None = Field(None, description="The person's first name.")
            lastName: str | None = Field(None, description="The person's last name.")
            age: conint(ge=0) | None = Field(None, description="Age in years which must be equal to or greater than zero.")
            friends: list[Any] | None = None
            comment: None = Field(None)
        ```

---

## `--union-mode` {#union-mode}

Union mode for combining anyOf/oneOf schemas (smart or left_to_right).

The `--union-mode` flag configures the code generation behavior.

**Option relationships:**

- **Requires:** [`--output-model-type`](model-customization.md#output-model-type) = `pydantic_v2.BaseModel` - `--union-mode` is only supported for `--output-model-type pydantic_v2.BaseModel`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --union-mode left_to_right --output-model-type pydantic_v2.BaseModel # (1)!
    ```

    1. :material-arrow-left: `--union-mode` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "$schema": "http://json-schema.org/draft-04/schema#",
        "type": "object",
        "title": "My schema",
        "additionalProperties": true,
        "properties": {
            "AddressLine1": { "type": "string" },
            "AddressLine2": { "type": "string" },
            "City":         { "type": "string" }
        },
        "required": [ "AddressLine1" ],
        "anyOf": [
            {
                "type": "object",
                "properties": {
                    "State":   { "type": "string" },
                    "ZipCode": { "type": "string" }
                },
                "required": [ "ZipCode" ]
            },
            {
                "type": "object",
                "properties": {
                    "County":   { "type": "string" },
                    "PostCode": { "type": "string" }
                },
                "required": [ "PostCode" ]
            },
            { "$ref": "#/definitions/US" }
        ],
        "definitions": {
            "US":  {
                "type": "object",
                "properties": {
                    "County":   { "type": "string" },
                    "PostCode": { "type": "string" }
                },
                "required": [ "PostCode" ]
            }
        }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  combine_any_of_object.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, ConfigDict, Field, RootModel


    class MySchema1(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        AddressLine1: str
        AddressLine2: str | None = None
        City: str | None = None
        State: str | None = None
        ZipCode: str


    class MySchema2(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        AddressLine1: str
        AddressLine2: str | None = None
        City: str | None = None
        County: str | None = None
        PostCode: str


    class US(BaseModel):
        County: str | None = None
        PostCode: str


    class MySchema3(US):
        model_config = ConfigDict(
            extra="allow",
        )
        AddressLine1: str
        AddressLine2: str | None = None
        City: str | None = None


    class MySchema(RootModel[MySchema1 | MySchema2 | MySchema3]):
        root: MySchema1 | MySchema2 | MySchema3 = Field(..., title="My schema", union_mode="left_to_right")
    ```

---

## `--use-default` {#use-default}

Use default values from schema in generated models.

The `--use-default` flag allows required fields with default values to be generated
with their defaults, making them optional to provide when instantiating the model.

When `--strict-nullable` is enabled, the field type still follows the schema's
nullability. For example, a required string field with a default is generated
as `str = 'value'`, not `str | None = 'value'`, unless the schema allows null.

**Related:** [`--strict-nullable`](#strict-nullable)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-default # (1)!
    ```

    1. :material-arrow-left: `--use-default` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "title": "Use default with const",
        "properties": {
            "foo": {
                "const": "foo"
            },
            "bool_default": {
                "type": "boolean",
                "const": true,
                "default": false
            },
            "int_default": {
                "type": "integer",
                "const": 3,
                "default": 0
            },
            "str_default": {
                "type": "string",
                "const": "fast",
                "default": ""
            }
        }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  use_default_with_const.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Literal

    from pydantic import BaseModel


    class UseDefaultWithConst(BaseModel):
        foo: Literal["foo"] | None = None
        bool_default: Literal[True] = False
        int_default: Literal[3] = 0
        str_default: Literal["fast"] = ""
    ```

---

## `--use-default-factory-for-optional-nested-models` {#use-default-factory-for-optional-nested-models}

Generate default_factory for optional nested model fields.

The `--use-default-factory-for-optional-nested-models` flag generates default_factory
for optional nested model fields instead of None default:
- Dataclasses: `field: Model | None = field(default_factory=Model)`
- Pydantic: `field: Model | None = Field(default_factory=Model)`
- msgspec: `field: Model | UnsetType = field(default_factory=Model)`

When a built-in generated child has required constructor fields, including inherited fields,
the field keeps its usual `None` or `UNSET` default. The child's required fields
remain required. With `--strip-default-none`, the usual omission behavior can instead
require the parent field to be supplied explicitly. Existing defaults enabled by
`--use-default` and fields excluded
from the constructor are respected. Custom root templates, decorators, and opaque
base classes keep their existing factory behavior. Constructors and validators are
not executed to determine whether a factory is usable.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-default-factory-for-optional-nested-models # (1)!
    ```

    1. :material-arrow-left: `--use-default-factory-for-optional-nested-models` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "name": {"type": "string"},
        "address": {"$ref": "#/$defs/Address"},
        "contact": {"$ref": "#/$defs/Contact"}
      },
      "required": ["name"],
      "$defs": {
        "Address": {
          "type": "object",
          "properties": {
            "street": {"type": "string"},
            "city": {"type": "string"}
          }
        },
        "Contact": {
          "type": "object",
          "properties": {
            "email": {"type": "string"},
            "phone": {"type": "string"}
          }
        }
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  default_factory_nested_model.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Address(BaseModel):
            street: str | None = None
            city: str | None = None


        class Contact(BaseModel):
            email: str | None = None
            phone: str | None = None


        class Model(BaseModel):
            name: str
            address: Address | None = Field(default_factory=Address)
            contact: Contact | None = Field(default_factory=Contact)
        ```

    === "dataclass"

        ```python
        # generated by datamodel-codegen:
        #   filename:  default_factory_nested_model.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from dataclasses import dataclass, field


        @dataclass
        class Address:
            street: str | None = None
            city: str | None = None


        @dataclass
        class Contact:
            email: str | None = None
            phone: str | None = None


        @dataclass
        class Model:
            name: str
            address: Address | None = field(default_factory=Address)
            contact: Contact | None = field(default_factory=Contact)
        ```

    === "msgspec"

        ```python
        # generated by datamodel-codegen:
        #   filename:  default_factory_nested_model.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from msgspec import UNSET, Struct, UnsetType, field


        class Address(Struct):
            street: str | UnsetType = UNSET
            city: str | UnsetType = UNSET


        class Contact(Struct):
            email: str | UnsetType = UNSET
            phone: str | UnsetType = UNSET


        class Model(Struct):
            name: str
            address: Address | UnsetType = field(default_factory=Address)
            contact: Contact | UnsetType = field(default_factory=Contact)
        ```

---

## `--use-default-kwarg` {#use-default-kwarg}

Use default= keyword argument instead of positional argument for fields with defaults.

The `--use-default-kwarg` flag generates Field() declarations using `default=`
as a keyword argument instead of a positional argument for fields that have
default values.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-default-kwarg # (1)!
    ```

    1. :material-arrow-left: `--use-default-kwarg` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    type A {
        field: String!
        optionalField: String
        listField: [String!]!
        listOptionalField: [String]!
        optionalListField: [String!]
        optionalListOptionalField: [String]
        listListField:[[String!]!]!
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  annotated.graphql
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Literal

    from pydantic import BaseModel, Field
    from typing_extensions import TypeAliasType

    Boolean = TypeAliasType("Boolean", bool)
    """
    The `Boolean` scalar type represents `true` or `false`.
    """


    String = TypeAliasType("String", str)
    """
    The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
    """


    class A(BaseModel):
        field: String
        listField: list[String]
        listListField: list[list[String]]
        listOptionalField: list[String | None]
        optionalField: String | None = None
        optionalListField: list[String] | None = None
        optionalListOptionalField: list[String | None] | None = None
        typename__: Literal["A"] | None = Field(default="A", alias="__typename")
    ```

---

## `--use-frozen-field` {#use-frozen-field}

Generate frozen (immutable) field definitions for readOnly properties.

The `--use-frozen-field` flag generates frozen field definitions:
- Pydantic v2: `Field(frozen=True)`
- Dataclasses: silently ignored (no frozen fields generated)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-frozen-field # (1)!
    ```

    1. :material-arrow-left: `--use-frozen-field` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "User",
      "type": "object",
      "required": ["id", "name", "password"],
      "properties": {
        "id": {
          "type": "integer",
          "description": "Server-generated ID",
          "readOnly": true
        },
        "name": {
          "type": "string"
        },
        "password": {
          "type": "string",
          "description": "User password",
          "writeOnly": true
        },
        "created_at": {
          "type": "string",
          "format": "date-time",
          "readOnly": true
        }
      }
    }
    ```

    **Output:**

    === "Pydantic v2"

        ```python
        # generated by datamodel-codegen:
        #   filename:  use_frozen_field.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from pydantic import AwareDatetime, BaseModel, Field


        class User(BaseModel):
            id: int = Field(..., description="Server-generated ID", frozen=True)
            name: str
            password: str = Field(..., description="User password")
            created_at: AwareDatetime | None = Field(None, frozen=True)
        ```

    === "dataclass"

        ```python
        # generated by datamodel-codegen:
        #   filename:  use_frozen_field.json
        #   timestamp: 2019-07-26T00:00:00+00:00

        from __future__ import annotations

        from dataclasses import dataclass


        @dataclass
        class User:
            id: int
            name: str
            password: str
            created_at: str | None = None
        ```

---

## `--use-generic-base-class` {#use-generic-base-class}

Generate a shared base class with model configuration to avoid repetition (DRY).

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --extra-fields forbid --output-model-type pydantic_v2.BaseModel --use-generic-base-class # (1)!
    ```

    1. :material-arrow-left: `--use-generic-base-class` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "title": "Test",
      "type": "object",
      "required": [
        "foo"
      ],
      "properties": {
        "foo": {
          "type": "object",
          "properties": {
            "x": {
              "type": "integer"
            }
          },
          "additionalProperties": true
        },
        "bar": {
          "type": "object",
          "properties": {
            "y": {
              "type": "integer"
            }
          },
          "additionalProperties": false
        },
        "baz": {
          "type": "object",
          "properties": {
            "z": {
              "type": "integer"
            }
          }
        }
      },
      "additionalProperties": false
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  extra_fields.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel as _BaseModel
    from pydantic import ConfigDict


    class BaseModel(_BaseModel):
        model_config = ConfigDict(
            extra="forbid",
        )


    class Foo(BaseModel):
        model_config = ConfigDict(
            extra="allow",
        )
        x: int | None = None


    class Bar(BaseModel):
        model_config = ConfigDict(
            extra="forbid",
        )
        y: int | None = None


    class Baz(BaseModel):
        z: int | None = None


    class Test(BaseModel):
        model_config = ConfigDict(
            extra="forbid",
        )
        foo: Foo
        bar: Bar | None = None
        baz: Baz | None = None
    ```

---

## `--use-missing-sentinel` {#use-missing-sentinel}

Use Pydantic's MISSING sentinel for optional fields without defaults.

The `--use-missing-sentinel` flag generates `MISSING` as the default for optional
Pydantic v2 fields that do not define a schema default. This preserves the
difference between an omitted field and a nullable field set to `None`.

**Related:** [`--strict-nullable`](#strict-nullable), [`--target-pydantic-version`](#target-pydantic-version)

**Option relationships:**

- **Implies:** [`--target-pydantic-version`](model-customization.md#target-pydantic-version) = `2.12`
- **Requires:** [`--output-model-type`](model-customization.md#output-model-type) = `pydantic_v2.BaseModel` - `--use-missing-sentinel` is only supported for `--output-model-type pydantic_v2.BaseModel`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-missing-sentinel # (1)!
    ```

    1. :material-arrow-left: `--use-missing-sentinel` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "MissingSentinel",
      "type": "object",
      "required": ["required", "requiredNullable"],
      "properties": {
        "required": {
          "type": "integer"
        },
        "requiredNullable": {
          "type": ["integer", "null"]
        },
        "unrequired": {
          "type": "integer"
        },
        "nullableUnrequired": {
          "type": ["integer", "null"]
        },
        "withDefault": {
          "type": "integer",
          "default": 1
        },
        "nullDefault": {
          "type": ["integer", "null"],
          "default": null
        },
        "aliased-field": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  missing_sentinel.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field
    from pydantic.experimental.missing_sentinel import MISSING


    class MissingSentinel(BaseModel):
        required: int
        requiredNullable: int | None
        unrequired: int | MISSING = MISSING
        nullableUnrequired: int | None | MISSING = MISSING
        withDefault: int | None = 1
        nullDefault: int | None = None
        aliased_field: str | MISSING = Field(MISSING, alias="aliased-field")
    ```

---

## `--use-one-literal-as-default` {#use-one-literal-as-default}

Use single literal value as default when enum has only one option.

The `--use-one-literal-as-default` flag configures the code generation behavior.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-one-literal-as-default --enum-field-as-literal one # (1)!
    ```

    1. :material-arrow-left: `--use-one-literal-as-default` - the option documented here

??? example "Examples"

    **Input Schema:**

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
    components:
      schemas:
        Pet:
          required:
            - id
            - name
            - number
            - boolean
          properties:
            id:
              type: integer
              format: int64
            name:
              type: string
            tag:
              type: string
            kind:
              type: string
              enum: ['dog', 'cat']
            type:
              type: string
              enum: [ 'animal' ]
            number:
              type: integer
              enum: [ 1 ]
            boolean:
              type: boolean
              enum: [ true ]
    
        Pets:
          type: array
          items:
            $ref: "#/components/schemas/Pet"
        animal:
          type: object
          properties:
            kind:
              type: string
              enum: ['snake', 'rabbit']
        Error:
          required:
            - code
            - message
          properties:
            code:
              type: integer
              format: int32
            message:
              type: string
        EnumObject:
          type: object
          properties:
            type:
              enum: ['a', 'b']
              type: string
        EnumRoot:
          enum: ['a', 'b']
          type: string
        IntEnum:
          enum: [1,2]
          type: number
        AliasEnum:
          enum: [1,2,3]
          type: number
          x-enum-varnames: ['a', 'b', 'c']
        MultipleTypeEnum:
          enum: [ "red", "amber", "green", null, 42 ]
        singleEnum:
          enum: [ "pet" ]
          type: string
        arrayEnum:
          type: array
          items: [
            { enum: [ "cat" ] },
            { enum: [ "dog"]}
          ]
        nestedNullableEnum:
          type: object
          properties:
            nested_version:
              type: string
              nullable: true
              default: RC1
              description: nullable enum
              example: RC2
              enum:
                - RC1
                - RC1N
                - RC2
                - RC2N
                - RC3
                - RC4
                - null
        version:
          type: string
          nullable: true
          default: RC1
          description: nullable enum
          example: RC2
          enum:
          - RC1
          - RC1N
          - RC2
          - RC2N
          - RC3
          - RC4
          - null
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  enum_models.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum
    from typing import Literal

    from pydantic import BaseModel, Field, RootModel


    class Kind(Enum):
        dog = "dog"
        cat = "cat"


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None = None
        kind: Kind | None = None
        type: Literal["animal"] | None = None
        number: Literal[1] = 1
        boolean: Literal[True] = True


    class Pets(RootModel[list[Pet]]):
        root: list[Pet]


    class Kind1(Enum):
        snake = "snake"
        rabbit = "rabbit"


    class Animal(BaseModel):
        kind: Kind1 | None = None


    class Error(BaseModel):
        code: int
        message: str


    class Type(Enum):
        a = "a"
        b = "b"


    class EnumObject(BaseModel):
        type: Type | None = None


    class EnumRoot(Enum):
        a = "a"
        b = "b"


    class IntEnum(Enum):
        number_1 = 1
        number_2 = 2


    class AliasEnum(Enum):
        a = 1
        b = 2
        c = 3


    class MultipleTypeEnum(Enum):
        red = "red"
        amber = "amber"
        green = "green"
        NoneType_None = None
        int_42 = 42


    class SingleEnum(RootModel[Literal["pet"]]):
        root: Literal["pet"] = "pet"


    class ArrayEnum(RootModel[list[Literal["cat"] | Literal["dog"]]]):
        root: list[Literal["cat"] | Literal["dog"]]


    class NestedVersionEnum(Enum):
        RC1 = "RC1"
        RC1N = "RC1N"
        RC2 = "RC2"
        RC2N = "RC2N"
        RC3 = "RC3"
        RC4 = "RC4"


    class NestedVersion(RootModel[NestedVersionEnum | None]):
        root: NestedVersionEnum | None = Field("RC1", description="nullable enum", examples=["RC2"])


    class NestedNullableEnum(BaseModel):
        nested_version: NestedVersion | None = Field(
            "RC1", description="nullable enum", examples=["RC2"], validate_default=True
        )


    class VersionEnum(Enum):
        RC1 = "RC1"
        RC1N = "RC1N"
        RC2 = "RC2"
        RC2N = "RC2N"
        RC3 = "RC3"
        RC4 = "RC4"


    class Version(RootModel[VersionEnum | None]):
        root: VersionEnum | None = Field("RC1", description="nullable enum", examples=["RC2"])
    ```

---

## `--use-root-model-sequence-interface` {#use-root-model-sequence-interface}

Make non-null sequence-like Pydantic v2 RootModel classes implement collections.abc.Sequence.

When enabled, a RootModel whose root is list[T] or Sequence[T] inherits from Sequence[T]
and includes __iter__, __getitem__, and __len__ methods that delegate to the wrapped root value.
Scalar, optional, nullable, and RootModel type-alias outputs are unchanged.

This makes generated RootModel wrappers convenient to use anywhere a typed sequence is expected,
without unpacking `.root` at every call site:

```python
from collections.abc import Sequence

pets = Pets(root=[Pet(name="dog")])

first_pet: Pet = pets[0]
selected_pets: list[Pet] = pets[:1]
pet_names = [pet.name for pet in pets]


def render_pet_names(pets: Sequence[Pet]) -> list[str]:
    return [pet.name for pet in pets]


render_pet_names(pets)
```

When used with `--custom-template-dir`, a custom `pydantic_v2/RootModel.jinja2` template must render both
`sequence_base_class` and the helper methods using `sequence_item_type` and `sequence_slice_type`. If the custom
RootModel template does not render the sequence base class and helper methods, generation fails with an error
instead of silently ignoring this option.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --output-model-type pydantic_v2.BaseModel --use-root-model-sequence-interface --class-name Pets # (1)!
    ```

    1. :material-arrow-left: `--use-root-model-sequence-interface` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "array",
      "items": {
        "$ref": "#/definitions/Pet"
      },
      "definitions": {
        "Pet": {
          "type": "object",
          "required": [
            "name"
          ],
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_model_sequence_interface.json

    from __future__ import annotations

    from collections.abc import Iterator, Sequence
    from typing import SupportsIndex, overload

    from pydantic import BaseModel, RootModel


    class Pet(BaseModel):
        name: str


    class Pets(RootModel[list[Pet]], Sequence[Pet]):
        root: list[Pet]

        def __iter__(self) -> Iterator[Pet]:
            return iter(self.root)

        @overload
        def __getitem__(self, index: SupportsIndex) -> Pet:
            pass

        @overload
        def __getitem__(self, index: slice) -> list[Pet]:
            pass

        def __getitem__(self, index: SupportsIndex | slice) -> Pet | list[Pet]:
            return self.root[index]

        def __len__(self) -> int:
            return len(self.root)
    ```

---

## `--use-serialize-as-any` {#use-serialize-as-any}

Wrap fields with subtypes in Pydantic's SerializeAsAny.

The `--use-serialize-as-any` flag applies Pydantic v2's SerializeAsAny wrapper
to fields that have subtype relationships, ensuring proper serialization of
polymorphic types and inheritance hierarchies.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-serialize-as-any # (1)!
    ```

    1. :material-arrow-left: `--use-serialize-as-any` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    info:
      version: 1.0.0
      title: SerializeAsAny Test
      description: Test schema for SerializeAsAny annotation on types with subtypes
    paths: {}
    components:
      schemas:
        User:
          type: object
          description: Base user model
          properties:
            name:
              type: string
              description: User's name
          required:
            - name
    
        AdminUser:
          allOf:
            - $ref: '#/components/schemas/User'
            - type: object
              description: Admin user with additional permissions
              properties:
                admin_level:
                  type: integer
                  description: Admin permission level
              required:
                - admin_level
    
        Container:
          type: object
          description: Container that holds user references
          properties:
            admin_user_field:
              $ref: '#/components/schemas/AdminUser'
              description: Field that should not use SerializeAsAny
            user_field:
              $ref: '#/components/schemas/User'
              description: Field that should use SerializeAsAny
            user_list:
              type: array
              description: List of users that should use SerializeAsAny
              items:
                $ref: '#/components/schemas/User'
          required:
            - user_field
            - user_list
            - admin_user_field
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  serialize_as_any.yaml
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, Field, SerializeAsAny


    class User(BaseModel):
        name: str = Field(..., description="User's name")


    class AdminUser(User):
        admin_level: int = Field(..., description="Admin permission level")


    class Container(BaseModel):
        admin_user_field: AdminUser = Field(..., description="Field that should not use SerializeAsAny")
        user_field: SerializeAsAny[User] = Field(..., description="Field that should use SerializeAsAny")
        user_list: list[SerializeAsAny[User]] = Field(..., description="List of users that should use SerializeAsAny")
    ```

---

## `--use-subclass-enum` {#use-subclass-enum}

Generate typed Enum subclasses for enums with specific field types.

The `--use-subclass-enum` flag generates Enum classes as subclasses of the
appropriate field type (int, float, bytes, str) when an enum has a specific
type, providing better type safety and IDE support.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --use-subclass-enum # (1)!
    ```

    1. :material-arrow-left: `--use-subclass-enum` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```graphql
    "Employee shift status"
    enum EmployeeShiftStatus {
      "not on shift"
      NOT_ON_SHIFT
      "on shift"
      ON_SHIFT
    }
    
    enum Color {
      RED
      GREEN
      BLUE
    }
    
    enum EnumWithOneField {
        FIELD
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  enums.graphql
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from enum import Enum

    from typing_extensions import TypeAliasType

    Boolean = TypeAliasType("Boolean", bool)
    """
    The `Boolean` scalar type represents `true` or `false`.
    """


    String = TypeAliasType("String", str)
    """
    The `String` scalar type represents textual data, represented as UTF-8 character sequences. The String type is most often used by GraphQL to represent free-form human-readable text.
    """


    class Color(str, Enum):
        BLUE = "BLUE"
        GREEN = "GREEN"
        RED = "RED"


    class EmployeeShiftStatus(str, Enum):
        """
        Employee shift status
        """

        NOT_ON_SHIFT = "NOT_ON_SHIFT"
        ON_SHIFT = "ON_SHIFT"


    class EnumWithOneField(str, Enum):
        FIELD = "FIELD"
    ```

---
