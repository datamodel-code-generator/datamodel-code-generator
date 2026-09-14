# ⚙️ General Options

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--all-exports-collision-strategy`](#all-exports-collision-strategy) | Handle name collisions when exporting recursive module hiera... |
| [`--all-exports-scope`](#all-exports-scope) | Generate __all__ exports for child modules in __init__.py fi... |
| [`--allow-private-network`](#allow-private-network) | Allow HTTP requests to private network schema endpoints. |
| [`--allow-remote-refs`](#allow-remote-refs) | Enable fetching of `$ref` targets over HTTP/HTTPS. |
| [`--check`](#check) | Verify generated code matches existing output without modify... |
| [`--diff-against`](#diff-against) | Compare generated code from a baseline input with the curren... |
| [`--disable-warnings`](#disable-warnings) | Suppress warning messages during code generation. |
| [`--fail-on-multi-module-stdout`](#fail-on-multi-module-stdout) | Fail instead of concatenating multiple modules in text stdou... |
| [`--generate-cli-command`](#generate-cli-command) | Generate CLI command from pyproject.toml configuration. |
| [`--generate-pyproject-config`](#generate-pyproject-config) | Generate pyproject.toml configuration from CLI arguments. |
| [`--http-headers`](#http-headers) | Fetch a schema from a URL with custom HTTP headers. |
| [`--http-ignore-tls`](#http-ignore-tls) | Disable TLS certificate verification for HTTPS requests. |
| [`--http-local-ref-path`](#http-local-ref-path) | Resolve HTTP references from local schema files. |
| [`--http-query-parameters`](#http-query-parameters) | Add query parameters to HTTP requests for remote schemas. |
| [`--http-timeout`](#http-timeout) | Set timeout for HTTP requests to remote hosts. |
| [`--ignore-pyproject`](#ignore-pyproject) | Ignore pyproject.toml configuration file. |
| [`--locked`](#locked) | Require an existing remote lock and validate each fetched re... |
| [`--lockfile`](#lockfile) | Select the remote reference integrity lock file (experimenta... |
| [`--module-split-mode`](#module-split-mode) | Split generated models into separate files, one per model cl... |
| [`--shared-module-name`](#shared-module-name) | Customize the name of the shared module for deduplicated mod... |
| [`--strict-refs`](#strict-refs) | Treat unresolved local `$ref` JSON pointers as errors. |
| [`--update-lock`](#update-lock) | Create or atomically update the selected remote lock after g... |
| [`--watch`](#watch) | Watch input file(s) for changes and regenerate output automa... |
| [`--watch-delay`](#watch-delay) | Set debounce delay in seconds for watch mode. |

---

## 🍳 Recipes

### Resolve remote references deliberately

Enable remote `$ref` loading and configure request metadata, timeouts, or local ref roots.

**Options:** [`--allow-remote-refs`](#allow-remote-refs), [`--http-headers`](#http-headers), [`--http-timeout`](#http-timeout), [`--http-local-ref-path`](#http-local-ref-path)

### Regenerate during schema edits

Watch input files with a short debounce while writing output to a stable target path.

**Options:** [`--watch`](#watch), [`--watch-delay`](#watch-delay), [`--output`](base-options.md#output)

---

## `--all-exports-collision-strategy` {#all-exports-collision-strategy}

Handle name collisions when exporting recursive module hierarchies.

The `--all-exports-collision-strategy` flag determines how to resolve naming conflicts
when using `--all-exports-scope=recursive`. The 'minimal-prefix' strategy adds the
minimum module path prefix needed to disambiguate colliding names, while 'full-prefix'
uses the complete module path. Requires `--all-exports-scope=recursive`.

**Related:** [`--all-exports-scope`](#all-exports-scope)

**See also:** [Module Structure and Exports](../module-exports.md)

**Option relationships:**

- **Requires:** [`--all-exports-scope`](general-options.md#all-exports-scope) = `recursive` - `--all-exports-collision-strategy` can only be used with `--all-exports-scope=recursive`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --all-exports-scope recursive --all-exports-collision-strategy minimal-prefix # (1)!
    ```

    1. :material-arrow-left: `--all-exports-collision-strategy` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    info:
      version: 1.0.0
      title: Modular Swagger Petstore
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
                    $ref: "#/components/schemas/collections.Pets"
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
                    $ref: "#/components/schemas/collections.Pets"
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
        models.Species:
          type: string
          enum:
            - dog
            - cat
            - snake
        models.Pet:
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
            species:
              $ref: '#/components/schemas/models.Species'
        models.User:
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
        collections.Pets:
          type: array
          items:
            $ref: "#/components/schemas/models.Pet"
        collections.Users:
          type: array
          items:
            $ref: "#/components/schemas/models.User"
        optional:
          type: string
        Id:
          type: string
        collections.Rules:
          type: array
          items:
            type: string
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
        collections.apis:
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
              stage:
                type: string
                enum: [
                  "test",
                  "dev",
                  "stg",
                  "prod"
                ]
        models.Event:
          type: object
          properties:
            name:
              anyOf:
                - type: string
                - type: number
                - type: integer
                - type: boolean
                - type: object
                - type: array
                  items:
                    type: string
        Result:
          type: object
          properties:
            event:
              $ref: '#/components/schemas/models.Event'
        foo.bar.Thing:
          properties:
            attributes:
              type: object
        foo.bar.Thang:
          properties:
            attributes:
              type: array
              items:
                type: object
        foo.bar.Clone:
          allOf:
            - $ref: '#/components/schemas/foo.bar.Thing'
            - type: object
              properties:
                others:
                  type: object
                  properties:
                     name:
                       type: string
    
        foo.Tea:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
        Source:
          properties:
            country:
              type: string
        foo.Cocoa:
          properties:
            quality:
              type: integer
        bar.Field:
          type: string
          example: green
        woo.boo.Chocolate:
          properties:
            flavour:
              type: string
            source:
              $ref: '#/components/schemas/Source'
            cocoa:
              $ref: '#/components/schemas/foo.Cocoa'
            field:
              $ref: '#/components/schemas/bar.Field'
        differentTea:
          type: object
          properties:
            foo:
              $ref: '#/components/schemas/foo.Tea'
            nested:
              $ref: '#/components/schemas/nested.foo.Tea'
        nested.foo.Tea:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
            self:
              $ref: '#/components/schemas/nested.foo.Tea'
            optional:
              type: array
              items:
                $ref: '#/components/schemas/optional'
        nested.foo.TeaClone:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
            self:
              $ref: '#/components/schemas/nested.foo.Tea'
            optional:
              type: array
              items:
                $ref: '#/components/schemas/optional'
        nested.foo.List:
          type: array
          items:
            $ref: '#/components/schemas/nested.foo.Tea'
    ```

    **Output:**

    ```python
    # __init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from ._internal import DifferentTea, Error, Id, Optional, Result, Source

    __all__ = ["DifferentTea", "Error", "Id", "Optional", "Result", "Source"]

    # _internal.py
    # generated by datamodel-codegen:
    #   filename:  _internal

    from __future__ import annotations

    from pydantic import BaseModel, RootModel

    from . import models


    class Optional(RootModel[str]):
        root: str


    class Id(RootModel[str]):
        root: str


    class Error(BaseModel):
        code: int
        message: str


    class Result(BaseModel):
        event: models.Event | None = None


    class Source(BaseModel):
        country: str | None = None


    class DifferentTea(BaseModel):
        foo: Tea | None = None
        nested: Tea_1 | None = None


    class Tea(BaseModel):
        flavour: str | None = None
        id: Id | None = None


    class Cocoa(BaseModel):
        quality: int | None = None


    class Tea_1(BaseModel):
        flavour: str | None = None
        id: Id | None = None
        self: Tea_1 | None = None
        optional: list[Optional] | None = None


    class TeaClone(BaseModel):
        flavour: str | None = None
        id: Id | None = None
        self: Tea_1 | None = None
        optional: list[Optional] | None = None


    class List(RootModel[list[Tea_1]]):
        root: list[Tea_1]


    Tea_1.model_rebuild()

    # bar.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from pydantic import Field, RootModel


    class FieldModel(RootModel[str]):
        root: str = Field(..., examples=["green"])


    # collections.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from enum import Enum

    from pydantic import AnyUrl, BaseModel, Field, RootModel

    from . import models


    class Pets(RootModel[list[models.Pet]]):
        root: list[models.Pet]


    class Users(RootModel[list[models.User]]):
        root: list[models.User]


    class Rules(RootModel[list[str]]):
        root: list[str]


    class Stage(Enum):
        test = "test"
        dev = "dev"
        stg = "stg"
        prod = "prod"


    class Api(BaseModel):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")
        stage: Stage | None = None


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    # foo/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from .._internal import Cocoa, Tea

    __all__ = ["Cocoa", "Tea"]

    # foo/bar.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel


    class Thing(BaseModel):
        attributes: dict[str, Any] | None = None


    class Thang(BaseModel):
        attributes: list[dict[str, Any]] | None = None


    class Others(BaseModel):
        name: str | None = None


    class Clone(Thing):
        others: Others | None = None


    # models.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from enum import Enum
    from typing import Any

    from pydantic import BaseModel


    class Species(Enum):
        dog = "dog"
        cat = "cat"
        snake = "snake"


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None = None
        species: Species | None = None


    class User(BaseModel):
        id: int
        name: str
        tag: str | None = None


    class Event(BaseModel):
        name: str | float | int | bool | dict[str, Any] | list[str] | None = None


    # nested/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    # nested/foo.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from .._internal import List
    from .._internal import Tea_1 as Tea
    from .._internal import TeaClone

    __all__ = ["List", "Tea", "TeaClone"]

    # woo/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from .boo import Chocolate

    __all__ = [
        "Chocolate",
    ]

    # woo/boo.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from pydantic import BaseModel

    from .. import bar
    from .._internal import Cocoa, Source


    class Chocolate(BaseModel):
        flavour: str | None = None
        source: Source | None = None
        cocoa: Cocoa | None = None
        field: bar.FieldModel | None = None
    ```

---

## `--all-exports-scope` {#all-exports-scope}

Generate __all__ exports for child modules in __init__.py files.

The `--all-exports-scope=children` flag adds __all__ to each __init__.py containing
exports from direct child modules. This improves IDE autocomplete and explicit exports.
Use 'recursive' to include all descendant exports with collision handling.

**Related:** [`--all-exports-collision-strategy`](#all-exports-collision-strategy)

**See also:** [Module Structure and Exports](../module-exports.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --all-exports-scope children # (1)!
    ```

    1. :material-arrow-left: `--all-exports-scope` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: "3.0.0"
    info:
      version: 1.0.0
      title: Modular Swagger Petstore
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
                    $ref: "#/components/schemas/collections.Pets"
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
                    $ref: "#/components/schemas/collections.Pets"
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
        models.Species:
          type: string
          enum:
            - dog
            - cat
            - snake
        models.Pet:
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
            species:
              $ref: '#/components/schemas/models.Species'
        models.User:
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
        collections.Pets:
          type: array
          items:
            $ref: "#/components/schemas/models.Pet"
        collections.Users:
          type: array
          items:
            $ref: "#/components/schemas/models.User"
        optional:
          type: string
        Id:
          type: string
        collections.Rules:
          type: array
          items:
            type: string
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
        collections.apis:
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
              stage:
                type: string
                enum: [
                  "test",
                  "dev",
                  "stg",
                  "prod"
                ]
        models.Event:
          type: object
          properties:
            name:
              anyOf:
                - type: string
                - type: number
                - type: integer
                - type: boolean
                - type: object
                - type: array
                  items:
                    type: string
        Result:
          type: object
          properties:
            event:
              $ref: '#/components/schemas/models.Event'
        foo.bar.Thing:
          properties:
            attributes:
              type: object
        foo.bar.Thang:
          properties:
            attributes:
              type: array
              items:
                type: object
        foo.bar.Clone:
          allOf:
            - $ref: '#/components/schemas/foo.bar.Thing'
            - type: object
              properties:
                others:
                  type: object
                  properties:
                     name:
                       type: string
    
        foo.Tea:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
        Source:
          properties:
            country:
              type: string
        foo.Cocoa:
          properties:
            quality:
              type: integer
        bar.Field:
          type: string
          example: green
        woo.boo.Chocolate:
          properties:
            flavour:
              type: string
            source:
              $ref: '#/components/schemas/Source'
            cocoa:
              $ref: '#/components/schemas/foo.Cocoa'
            field:
              $ref: '#/components/schemas/bar.Field'
        differentTea:
          type: object
          properties:
            foo:
              $ref: '#/components/schemas/foo.Tea'
            nested:
              $ref: '#/components/schemas/nested.foo.Tea'
        nested.foo.Tea:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
            self:
              $ref: '#/components/schemas/nested.foo.Tea'
            optional:
              type: array
              items:
                $ref: '#/components/schemas/optional'
        nested.foo.TeaClone:
          properties:
            flavour:
              type: string
            id:
              $ref: '#/components/schemas/Id'
            self:
              $ref: '#/components/schemas/nested.foo.Tea'
            optional:
              type: array
              items:
                $ref: '#/components/schemas/optional'
        nested.foo.List:
          type: array
          items:
            $ref: '#/components/schemas/nested.foo.Tea'
    ```

    **Output:**

    ```python
    # __init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from ._internal import DifferentTea, Error, Id, Optional, Result, Source

    __all__ = ["DifferentTea", "Error", "Id", "Optional", "Result", "Source"]

    # _internal.py
    # generated by datamodel-codegen:
    #   filename:  _internal

    from __future__ import annotations

    from pydantic import BaseModel, RootModel

    from . import models


    class Optional(RootModel[str]):
        root: str


    class Id(RootModel[str]):
        root: str


    class Error(BaseModel):
        code: int
        message: str


    class Result(BaseModel):
        event: models.Event | None = None


    class Source(BaseModel):
        country: str | None = None


    class DifferentTea(BaseModel):
        foo: Tea | None = None
        nested: Tea_1 | None = None


    class Tea(BaseModel):
        flavour: str | None = None
        id: Id | None = None


    class Cocoa(BaseModel):
        quality: int | None = None


    class Tea_1(BaseModel):
        flavour: str | None = None
        id: Id | None = None
        self: Tea_1 | None = None
        optional: list[Optional] | None = None


    class TeaClone(BaseModel):
        flavour: str | None = None
        id: Id | None = None
        self: Tea_1 | None = None
        optional: list[Optional] | None = None


    class List(RootModel[list[Tea_1]]):
        root: list[Tea_1]


    Tea_1.model_rebuild()

    # bar.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from pydantic import Field, RootModel


    class FieldModel(RootModel[str]):
        root: str = Field(..., examples=["green"])


    # collections.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from enum import Enum

    from pydantic import AnyUrl, BaseModel, Field, RootModel

    from . import models


    class Pets(RootModel[list[models.Pet]]):
        root: list[models.Pet]


    class Users(RootModel[list[models.User]]):
        root: list[models.User]


    class Rules(RootModel[list[str]]):
        root: list[str]


    class Stage(Enum):
        test = "test"
        dev = "dev"
        stg = "stg"
        prod = "prod"


    class Api(BaseModel):
        apiKey: str | None = Field(None, description="To be used as a dataset parameter value")
        apiVersionNumber: str | None = Field(None, description="To be used as a version parameter value")
        apiUrl: AnyUrl | None = Field(None, description="The URL describing the dataset's fields")
        apiDocumentationUrl: AnyUrl | None = Field(None, description="A URL to the API console for each API")
        stage: Stage | None = None


    class Apis(RootModel[list[Api]]):
        root: list[Api]


    # foo/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from .._internal import Cocoa, Tea

    __all__ = ["Cocoa", "Tea"]

    # foo/bar.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel


    class Thing(BaseModel):
        attributes: dict[str, Any] | None = None


    class Thang(BaseModel):
        attributes: list[dict[str, Any]] | None = None


    class Others(BaseModel):
        name: str | None = None


    class Clone(Thing):
        others: Others | None = None


    # models.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from enum import Enum
    from typing import Any

    from pydantic import BaseModel


    class Species(Enum):
        dog = "dog"
        cat = "cat"
        snake = "snake"


    class Pet(BaseModel):
        id: int
        name: str
        tag: str | None = None
        species: Species | None = None


    class User(BaseModel):
        id: int
        name: str
        tag: str | None = None


    class Event(BaseModel):
        name: str | float | int | bool | dict[str, Any] | list[str] | None = None


    # nested/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    # nested/foo.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from .._internal import List
    from .._internal import Tea_1 as Tea
    from .._internal import TeaClone

    __all__ = ["List", "Tea", "TeaClone"]

    # woo/__init__.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from .boo import Chocolate

    __all__ = [
        "Chocolate",
    ]

    # woo/boo.py
    # generated by datamodel-codegen:
    #   filename:  modular.yaml

    from __future__ import annotations

    from pydantic import BaseModel

    from .. import bar
    from .._internal import Cocoa, Source


    class Chocolate(BaseModel):
        flavour: str | None = None
        source: Source | None = None
        cocoa: Cocoa | None = None
        field: bar.FieldModel | None = None
    ```

---

## `--allow-private-network` {#allow-private-network}

Allow HTTP requests to private network schema endpoints.

The `--allow-private-network` flag permits trusted HTTP(S) schema requests to
private, loopback, link-local, or otherwise non-public network hosts. Without
this flag, those targets are blocked by default to reduce server-side request
forgery (SSRF) risk. If a trusted internal schema endpoint is blocked, verify
the URL and pass `--allow-private-network`; otherwise use a local schema file
or public endpoint.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url http://127.0.0.1/schema.json --allow-private-network # (1)!
    ```

    1. :material-arrow-left: `--allow-private-network` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  http://127.0.0.1/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--allow-remote-refs` {#allow-remote-refs}

Enable fetching of `$ref` targets over HTTP/HTTPS.

When enabled, the generator will resolve `$ref` references that point to remote URLs,
including relative refs resolved against a schema's `$id` base URL. This is required
for schemas that reference definitions hosted on external servers.

Automatically enabled when using `--url` input.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --allow-remote-refs # (1)!
    ```

    1. :material-arrow-left: `--allow-remote-refs` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$id": "https://example.com/root_id.json",
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "Person": {
          "$ref": "person.json"
        },
        "OriginalPerson": {
          "$ref": "person.json"
        },
        "Pet": {
          "type": "object",
          "properties": {
            "name": {
              "type": "string",
              "examples": ["dog", "cat"]
            },
            "owner": {
               "$ref": "person.json"
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  root_id.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from typing import Any

    from pydantic import BaseModel, Field, RootModel, conint


    class Model(RootModel[Any]):
        root: Any


    class Person(BaseModel):
        firstName: str | None = Field(None, description="The person's first name.")
        lastName: str | None = Field(None, description="The person's last name.")
        age: conint(ge=0) | None = Field(None, description="Age in years which must be equal to or greater than zero.")
        friends: list[Any] | None = None
        comment: None = Field(None)


    class OriginalPerson(RootModel[Person]):
        root: Person


    class Pet(BaseModel):
        name: str | None = Field(None, examples=["dog", "cat"])
        owner: Person | None = None
    ```

---

## `--check` {#check}

Verify generated code matches existing output without modifying files.

The `--check` flag compares the generated output with existing files and exits with
a non-zero status if they differ. Useful for CI/CD validation to ensure schemas
and generated code stay in sync. Works with both single files and directory outputs.

**See also:** [CI/CD Integration](../ci-cd.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --disable-timestamp --check # (1)!
    ```

    1. :material-arrow-left: `--check` - the option documented here

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

## `--diff-against` {#diff-against}

Compare generated code from a baseline input with the current schema without writing files.

Pass the baseline input with `--diff-against` and the current schema with `--input`.
The command generates both, then shows the generated-code diff from baseline to current.
The required `--output` value is a virtual destination: it selects single-file or
directory layout and formatter settings, but datamodel-code-generator never writes it.
The command exits non-zero when the formatted generated outputs differ, making it useful
for reviewing schema migrations in CI.

**Related:** [`--check`](#check), [`--input`](base-options.md#input), [`--output`](base-options.md#output), `--output-format`

**Option relationships:**

- **Requires:** [`--input`](base-options.md#input) - `--diff-against` requires --input with the current local schema path.
- **Requires:** [`--output`](base-options.md#output) - `--diff-against` requires --output to select file or directory output layout.
- **Conflicts:** [`--check`](general-options.md#check)
- **Conflicts:** [`--watch`](general-options.md#watch)
- **Conflicts:** [`--url`](base-options.md#url)
- **Conflicts:** [`--input-model`](base-options.md#input-model)
- **Conflicts:** [`--emit-model-metadata`](base-options.md#emit-model-metadata)
- **Conflicts:** [`--fail-on-multi-module-stdout`](general-options.md#fail-on-multi-module-stdout)
- **Conflicts:** `--job` - `--diff-against` compares one profile or input and cannot run named jobs.
- **Conflicts:** `--all-jobs` - `--diff-against` compares one profile or input and cannot run named jobs.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input tests/data/openapi/input_diff/new.yaml --diff-against tests/data/openapi/input_diff/old.yaml --output models --input-file-type openapi --disable-timestamp # (1)!
    ```

    1. :material-arrow-left: `--diff-against` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.0.0
    info:
      title: Current models
      version: "1.0"
    paths: {}
    components:
      schemas:
        collections.Current:
          type: object
          properties:
            name:
              type: string
    ```

    **Output:**

    ```
    ADDED: collections.py (generated only from current input)
    REMOVED: models.py (generated only from baseline input)
    ```

---

## `--disable-warnings` {#disable-warnings}

Suppress warning messages during code generation.

The --disable-warnings option silences all warning messages that the generator
might emit during processing (e.g., about unsupported features, ambiguous schemas,
or potential issues). Useful for clean output in CI/CD pipelines.

**See also:** [Model Reuse and Deduplication](../model-reuse.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --disable-warnings # (1)!
    ```

    1. :material-arrow-left: `--disable-warnings` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "allOf": [
        {
          "$ref": "#/definitions/Home"
        },
        {
          "$ref": "#/definitions/Kind"
        },
        {
          "$ref": "#/definitions/Id"
        },
        {
          "type": "object",
          "properties": {
            "name": {
              "type": "string"
            }
          }
        }
      ],
      "type": [
        "object"
      ],
      "properties": {
        "name": {
          "type": "string"
        },
        "age": {
          "type": "integer"
        }
      },
      "definitions": {
        "Home": {
          "type": "object",
          "properties": {
            "address": {
              "type": "string"
            },
            "zip": {
              "type": "string"
            }
          }
        },
        "Kind": {
          "type": "object",
          "properties": {
            "description": {
              "type": "string"
            }
          }
        },
        "Id": {
          "type": "object",
          "properties": {
            "id": {
              "type": "integer"
            }
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  all_of_with_object.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Home(BaseModel):
        address: str | None = None
        zip: str | None = None


    class Kind(BaseModel):
        description: str | None = None


    class Id(BaseModel):
        id: int | None = None


    class Pet(Home, Kind, Id):
        name: str | None = None
        age: int | None = None
    ```

---

## `--fail-on-multi-module-stdout` {#fail-on-multi-module-stdout}

Fail instead of concatenating multiple modules in text stdout.

The `--fail-on-multi-module-stdout` flag detects modular results while preserving
the legacy default, single-module output, JSON output, and file output. It takes
precedence over automatic unusable-stdout repair, rejecting the modular result
instead of coalescing it.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --fail-on-multi-module-stdout --disable-timestamp # (1)!
    ```

    1. :material-arrow-left: `--fail-on-multi-module-stdout` - the option documented here

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

## `--generate-cli-command` {#generate-cli-command}

Generate CLI command from pyproject.toml configuration.

The `--generate-cli-command` flag reads your pyproject.toml configuration
and outputs the equivalent CLI command. This is useful for debugging
configuration issues or sharing commands with others.

**See also:** [pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --generate-cli-command # (1)!
    ```

    1. :material-arrow-left: `--generate-cli-command` - the option documented here

??? example "Examples"

    **Configuration (pyproject.toml):**

    ```toml
    [tool.datamodel-codegen]
    input = "schema.yaml"
    output = "model.py"
    ```

    **Output:**

    ```
    datamodel-codegen --input schema.yaml --output model.py
    ```

---

## `--generate-pyproject-config` {#generate-pyproject-config}

Generate pyproject.toml configuration from CLI arguments.

The `--generate-pyproject-config` flag outputs a pyproject.toml configuration
snippet based on the provided CLI arguments. This is useful for converting
a working CLI command into a reusable configuration file.

**See also:** [pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --generate-pyproject-config --input schema.yaml --output model.py # (1)!
    ```

    1. :material-arrow-left: `--generate-pyproject-config` - the option documented here

??? example "Examples"

    **Output:**

    ```
    [tool.datamodel-codegen]
    input = "schema.yaml"
    output = "model.py"
    ```

---

## `--http-headers` {#http-headers}

Fetch a schema from a URL with custom HTTP headers.

The `--url` flag specifies a remote URL to fetch the schema from instead of
a local file. The `--http-headers` flag adds request headers in
`HeaderName:HeaderValue` format.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --http-headers "Authorization:Bearer token" # (1)!
    ```

    1. :material-arrow-left: `--http-headers` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--http-ignore-tls` {#http-ignore-tls}

Disable TLS certificate verification for HTTPS requests.

The `--http-ignore-tls` flag disables SSL/TLS certificate verification
when fetching schemas from HTTPS URLs. This is useful for development
environments with self-signed certificates. Not recommended for production.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --http-ignore-tls # (1)!
    ```

    1. :material-arrow-left: `--http-ignore-tls` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--http-local-ref-path` {#http-local-ref-path}

Resolve HTTP references from local schema files.

The `--http-local-ref-path` flag maps HTTP(S) `$ref` URLs to files under
a local schema store instead of fetching them from the network. The host and
URL path are used as the relative path under the schema store. For example,
`https://api.example.com/schemas/pet.json` is read from
`schemas/api.example.com/schemas/pet.json`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --http-local-ref-path schemas # (1)!
    ```

    1. :material-arrow-left: `--http-local-ref-path` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "$ref": "https://api.example.com/schemas/pet.json"
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel, RootModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None


    class Model(RootModel[Pet]):
        root: Pet
    ```

---

## `--http-query-parameters` {#http-query-parameters}

Add query parameters to HTTP requests for remote schemas.

The `--http-query-parameters` flag adds query parameters to HTTP requests
when fetching schemas from URLs. Useful for APIs that require version
or format parameters. Format: `key=value`. Multiple parameters can be
specified: `--http-query-parameters version=v2 format=json`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --http-query-parameters version=v2 format=json # (1)!
    ```

    1. :material-arrow-left: `--http-query-parameters` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--http-timeout` {#http-timeout}

Set timeout for HTTP requests to remote hosts.

The `--http-timeout` flag sets the timeout in seconds for HTTP requests
when fetching schemas from URLs. Useful for slow servers or large schemas.
Default is 30 seconds.

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --http-timeout 60 # (1)!
    ```

    1. :material-arrow-left: `--http-timeout` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--ignore-pyproject` {#ignore-pyproject}

Ignore pyproject.toml configuration file.

The `--ignore-pyproject` flag tells datamodel-codegen to ignore any
[tool.datamodel-codegen] configuration in pyproject.toml. This is useful
when you want to override project defaults with CLI arguments, or when
testing without project configuration.

**See also:** [pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --ignore-pyproject # (1)!
    ```

    1. :material-arrow-left: `--ignore-pyproject` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "type": "object",
      "properties": {
        "firstName": {"type": "string"},
        "lastName": {"type": "string"}
      }
    }
    ```

    **Output:**

    === "With Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  schema.json

        from __future__ import annotations

        from pydantic import BaseModel


        class Model(BaseModel):
            firstName: str | None = None
            lastName: str | None = None
        ```

    === "Without Option"

        ```python
        # generated by datamodel-codegen:
        #   filename:  schema.json

        from __future__ import annotations

        from pydantic import BaseModel, Field


        class Model(BaseModel):
            first_name: str | None = Field(None, alias="firstName")
            last_name: str | None = Field(None, alias="lastName")
        ```

---

## `--locked` {#locked}

Require an existing remote lock and validate each fetched resource against it (experimental).

`--locked` fails if the selected lock is missing, a resource is unrecorded, or its body differs. It conflicts with
`--update-lock`.

Remote reference lock support is experimental: its lock document schema and request-identity compatibility may evolve. It remains fail-closed on integrity mismatches, and it never persists credentials.

The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests,
never response bodies or request values directly. Each saved display origin contains only the scheme, host, and
explicit port—never a path, query, or request headers. A request identity includes its scheme, host, explicit port,
path, header names, and ordered query parameter names only. If one generation receives different bodies for the same
path and query-name identity, it fails closed rather than sharing a lock entry.

**Related:** [`--http-local-ref-path`](#http-local-ref-path), [`--lockfile`](#lockfile), [`--url`](base-options.md#url)

**Option relationships:**

- **Conflicts:** [`--update-lock`](general-options.md#update-lock)

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --locked --lockfile datamodel-codegen.lock # (1)!
    ```

    1. :material-arrow-left: `--locked` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--lockfile` {#lockfile}

Select the remote reference integrity lock file (experimental).

An existing selected lock is verified automatically; a missing selected lock is ignored unless `--locked` requires it.
Without `--lockfile`, the CLI uses `datamodel-codegen.lock` beside the discovered `pyproject.toml`, or in the invocation
working directory when no project is found. Explicit relative `--lockfile` paths resolve from the invocation working
directory, not the project root or output directory. The public API uses the caller's working directory for both its
default lock and relative `lockfile` paths.

Remote reference lock support is experimental: its lock document schema and request-identity compatibility may evolve. It remains fail-closed on integrity mismatches, and it never persists credentials.

The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests,
never response bodies or request values directly. Each saved display origin contains only the scheme, host, and
explicit port—never a path, query, or request headers. A request identity includes its scheme, host, explicit port,
path, header names, and ordered query parameter names only. If one generation receives different bodies for the same
path and query-name identity, it fails closed rather than sharing a lock entry.

**Related:** [`--http-local-ref-path`](#http-local-ref-path), [`--locked`](#locked), [`--update-lock`](#update-lock), [`--url`](base-options.md#url)

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --update-lock --lockfile datamodel-codegen.lock # (1)!
    ```

    1. :material-arrow-left: `--lockfile` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--module-split-mode` {#module-split-mode}

Split generated models into separate files, one per model class.

The `--module-split-mode=single` flag generates each model class in its own file,
named after the class in snake_case. Use with `--all-exports-scope=recursive` to
create an __init__.py that re-exports all models for convenient imports.

**Related:** [`--all-exports-scope`](#all-exports-scope), [`--use-exact-imports`](template-customization.md#use-exact-imports)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --module-split-mode single --all-exports-scope recursive --use-exact-imports # (1)!
    ```

    1. :material-arrow-left: `--module-split-mode` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "definitions": {
        "User": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "name": {"type": "string"}
          }
        },
        "Order": {
          "type": "object",
          "properties": {
            "id": {"type": "integer"},
            "user": {"$ref": "#/definitions/User"}
          }
        }
      }
    }
    ```

    **Output:**

    ```python
    # __init__.py
    # generated by datamodel-codegen:
    #   filename:  input.json

    from __future__ import annotations

    from .model import Model
    from .order import Order
    from .user import User

    __all__ = [
        "Model",
        "Order",
        "User",
    ]

    # model.py
    # generated by datamodel-codegen:
    #   filename:  input.json

    from __future__ import annotations

    from typing import Any

    from pydantic import RootModel


    class Model(RootModel[Any]):
        root: Any


    # order.py
    # generated by datamodel-codegen:
    #   filename:  input.json

    from __future__ import annotations

    from pydantic import BaseModel

    from .user import User


    class Order(BaseModel):
        id: int | None = None
        user: User | None = None


    # user.py
    # generated by datamodel-codegen:
    #   filename:  input.json

    from __future__ import annotations

    from pydantic import BaseModel


    class User(BaseModel):
        id: int | None = None
        name: str | None = None
    ```

---

## `--shared-module-name` {#shared-module-name}

Customize the name of the shared module for deduplicated models.

The `--shared-module-name` flag sets the name of the shared module created
when using `--reuse-model` with `--reuse-scope=tree`. This module contains
deduplicated models that are referenced from multiple files. Default is
`shared`. Use this if your schema already has a file named `shared`.

Note: This option only affects modular output with tree-level model reuse.

**See also:** [Model Reuse and Deduplication](../model-reuse.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --shared-module-name my_shared # (1)!
    ```

    1. :material-arrow-left: `--shared-module-name` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  pet_simple.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--strict-refs` {#strict-refs}

Treat unresolved local `$ref` JSON pointers as errors.

By default, an unresolved local pointer emits a warning and retains the generated
fallback `Any` model. Enable this option in validation-sensitive workflows to stop
generation instead. Existing empty schemas remain valid references.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --strict-refs # (1)!
    ```

    1. :material-arrow-left: `--strict-refs` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$defs": {
        "Empty": {}
      },
      "$ref": "#/$defs/Empty"
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  empty_local_ref.json

    from __future__ import annotations

    from typing import Any

    from pydantic import RootModel


    class Empty(RootModel[Any]):
        root: Any


    class Model(RootModel[Empty]):
        root: Empty
    ```

---

## `--update-lock` {#update-lock}

Create or atomically update the selected remote lock after generation (experimental).

`--update-lock` creates or refreshes the selected `--lockfile` from every remote resource reached during this run.
It conflicts with `--locked`.

Remote reference lock support is experimental: its lock document schema and request-identity compatibility may evolve. It remains fail-closed on integrity mismatches, and it never persists credentials.

The lock stores opaque SHA-256 request-identity digests and SHA-256 body digests,
never response bodies or request values directly. Each saved display origin contains only the scheme, host, and
explicit port—never a path, query, or request headers. A request identity includes its scheme, host, explicit port,
path, header names, and ordered query parameter names only. If one generation receives different bodies for the same
path and query-name identity, it fails closed rather than sharing a lock entry.

**Related:** [`--http-local-ref-path`](#http-local-ref-path), [`--lockfile`](#lockfile), [`--url`](base-options.md#url)

**Option relationships:**

- **Conflicts:** [`--locked`](general-options.md#locked)

!!! tip "Usage"

    ```bash
    datamodel-codegen --url https://api.example.com/schema.json --update-lock --lockfile datamodel-codegen.lock # (1)!
    ```

    1. :material-arrow-left: `--update-lock` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```json
    {
      "$schema": "http://json-schema.org/draft-07/schema#",
      "title": "Pet",
      "type": "object",
      "properties": {
        "id": {
          "type": "integer"
        },
        "name": {
          "type": "string"
        },
        "tag": {
          "type": "string"
        }
      }
    }
    ```

    **Output:**

    ```python
    # generated by datamodel-codegen:
    #   filename:  https://api.example.com/schema.json
    #   timestamp: 2019-07-26T00:00:00+00:00

    from __future__ import annotations

    from pydantic import BaseModel


    class Pet(BaseModel):
        id: int | None = None
        name: str | None = None
        tag: str | None = None
    ```

---

## `--watch` {#watch}

Watch input file(s) for changes and regenerate output automatically.

The `--watch` flag enables continuous file monitoring mode. When enabled,
datamodel-codegen watches the input file or directory for changes and
automatically regenerates the output whenever changes are detected.
Press Ctrl+C to stop watching.

**See also:** [pyproject.toml Configuration](../pyproject_toml.md)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --watch --check # (1)!
    ```

    1. :material-arrow-left: `--watch` - the option documented here

!!! warning "Requires extra dependency"

    The watch feature requires the `watch` extra:

    ```bash
    pip install 'datamodel-code-generator[watch]'
    ```

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

        ```
        Error: --watch and --check cannot be used together
        ```

    === "unknown"

        **Output:**

        ```
        Error: --watch requires --input file path
        ```

---

## `--watch-delay` {#watch-delay}

Set debounce delay in seconds for watch mode.

The `--watch-delay` option configures the debounce interval (default: 0.5 seconds)
for watch mode. This prevents multiple regenerations when files are rapidly
modified in succession. The delay ensures that after the last file change,
the generator waits the specified time before regenerating.

**Related:** [`--watch`](general-options.md#watch)

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --watch --watch-delay 1.5 # (1)!
    ```

    1. :material-arrow-left: `--watch-delay` - the option documented here

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

    ```
    Watching
    ```

---
