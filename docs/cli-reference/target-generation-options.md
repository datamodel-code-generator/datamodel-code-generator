# 🚀 Target Generation Options

## 📋 Options

| Option | Description |
|--------|-------------|
| [`--client-body-arguments`](#client-body-arguments) | Choose how operation methods take request bodies (experiment... |
| [`--client-default-base-url`](#client-default-base-url) | Set the server URL of operations that declare no servers (ex... |
| [`--client-model-package`](#client-model-package) | Name the import path of the models the client package import... |
| [`--client-operations`](#client-operations) | Set the client settings of single operations (experimental). |
| [`--client-output`](#client-output) | Write the client package to this directory (experimental). |
| [`--client-package`](#client-package) | Name the import path of the client package (experimental). |
| [`--client-protocols`](#client-protocols) | Declare the protocol helpers of the client package (experime... |
| [`--client-resource-names`](#client-resource-names) | Name the resources of tags (experimental). |
| [`--client-server-base-url`](#client-server-base-url) | Resolve relative server URLs against this base URL (experime... |
| [`--client-signature-style`](#client-signature-style) | Declare the arguments of the operation methods (experimental... |
| [`--generate-client`](#generate-client) | Generate an HTTPX2 client package for the models (experiment... |
| [`--generate-server`](#generate-server) | Generate a FastAPI server package for the models (experiment... |
| [`--server-body-mode`](#server-body-mode) | Choose how service methods receive request bodies (experimen... |
| [`--server-body-modes`](#server-body-modes) | Set the body mode of single operations (experimental). |
| [`--server-handler-mode`](#server-handler-mode) | Declare the service methods as plain or coroutine functions ... |
| [`--server-handler-modes`](#server-handler-modes) | Set the handler mode of single operations (experimental). |
| [`--server-include-request`](#server-include-request) | Pass the Starlette Request to every service method (experime... |
| [`--server-layout`](#server-layout) | Choose how the server package lays out its routes (experimen... |
| [`--server-model-package`](#server-model-package) | Name the import path of the models the server package import... |
| [`--server-operation-names`](#server-operation-names) | Name the service methods of single operations (experimental)... |
| [`--server-output`](#server-output) | Write the server package to this directory (experimental). |
| [`--server-package`](#server-package) | Name the import path of the server package (experimental). |
| [`--server-parameter-names`](#server-parameter-names) | Name the method arguments of single operations (experimental... |
| [`--server-primary-responses`](#server-primary-responses) | Choose the response a bare return value of an operation take... |
| [`--server-router-names`](#server-router-names) | Name router groups (experimental). |

---

## `--client-body-arguments` {#client-body-arguments}

Choose how operation methods take request bodies (experimental).

`body` (the default) takes the body as one `body` argument; `both` also takes the properties of an object body as
keyword arguments, so a call can pass either. The `body_arguments` of an operation in `--client-operations`
overrides it for that operation.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-body-arguments` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-body-arguments both # (1)!
    ```

    1. :material-arrow-left: `--client-body-arguments` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal, overload

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        @overload
        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            name: Unset = UNSET,
            tag: Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse: ...
        @overload
        def create_pet(
            self,
            *,
            body: Unset = UNSET,
            name: str,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse: ...
        def create_pet(
            self,
            *,
            body: _dcg_type_0 | Unset = UNSET,
            name: str | Unset = UNSET,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                fields=(name, tag),
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        @overload
        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            name: Unset = UNSET,
            tag: Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]: ...
        @overload
        def create_pet(
            self,
            *,
            body: Unset = UNSET,
            name: str,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]: ...
        def create_pet(
            self,
            *,
            body: _dcg_type_0 | Unset = UNSET,
            name: str | Unset = UNSET,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                fields=(name, tag),
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        @overload
        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            name: Unset = UNSET,
            tag: Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse: ...
        @overload
        def create_pet(
            self,
            *,
            body: Unset = UNSET,
            name: str,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse: ...
        def create_pet(
            self,
            *,
            body: _dcg_type_0 | Unset = UNSET,
            name: str | Unset = UNSET,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                fields=(name, tag),
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        @overload
        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            name: Unset = UNSET,
            tag: Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]: ...
        @overload
        def create_pet(
            self,
            *,
            body: Unset = UNSET,
            name: str,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]: ...
        def create_pet(
            self,
            *,
            body: _dcg_type_0 | Unset = UNSET,
            name: str | Unset = UNSET,
            tag: str | Unset = UNSET,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                fields=(name, tag),
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-default-base-url` {#client-default-base-url}

Set the server URL of operations that declare no servers (experimental).

An absolute `http` or `https` URL without userinfo, query, or fragment. Operations whose document and path declare
servers keep them.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-default-base-url` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-default-base-url https://api.example.com/v1 # (1)!
    ```

    1. :material-arrow-left: `--client-default-base-url` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options-unserved.yaml

    """The operation registry of this package; regenerate it instead of editing."""

    from __future__ import annotations

    from typing import Final

    from ._generated import model_bindings
    from ._runtime.client.operations import (
        BodyMedia,
        OperationPlan,
        ParameterSpec,
        RequestBody,
        ResponseDecoder,
        ServerPlan,
        model_branch,
    )
    from ._runtime.model_codecs.parameters import ParameterPlan
    from .types.pets import CreatePetResponse, ListPetsResponse

    _SERVERS_0: Final = (ServerPlan(url='https://api.example.com/v1'),)

    OPERATION_0: Final[OperationPlan[ListPetsResponse]] = OperationPlan(
        operation_id='listPets',
        method='GET',
        path='/pets',
        servers=_SERVERS_0,
        responses=ResponseDecoder(
            (model_branch('200', 'application/json', 'json', model_bindings.codec_2),),
            (),
        ),
        parameters=(
            ParameterSpec(
                plan=ParameterPlan(
                    location='query',
                    name='limit',
                    style='form',
                    explode=True,
                    kind='integer',
                    reserved_names=('cursor',),
                ),
                codec=model_bindings.codec_0,
            ),
            ParameterSpec(
                plan=ParameterPlan(
                    location='query',
                    name='cursor',
                    style='form',
                    explode=True,
                    reserved_names=('limit',),
                ),
                codec=model_bindings.codec_1,
            ),
        ),
    )

    OPERATION_1: Final[OperationPlan[CreatePetResponse]] = OperationPlan(
        operation_id='createPet',
        method='POST',
        path='/pets',
        servers=_SERVERS_0,
        responses=ResponseDecoder(
            (model_branch('201', 'application/json', 'json', model_bindings.codec_4),),
            (),
        ),
        body=RequestBody(
            media=(
                BodyMedia(
                    media_type='application/json',
                    kind='json',
                    codec=model_bindings.codec_3,
                ),
            ),
            default='application/json',
            required=True,
        ),
    )
    ```

    <!-- fmt: on -->

---

## `--client-model-package` {#client-model-package}

Name the import path of the models the client package imports (experimental).

`--client-model-package` is required with `--generate-client`, and names the module or package `--output`
generates.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-model-package` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models # (1)!
    ```

    1. :material-arrow-left: `--client-model-package` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-operations` {#client-operations}

Set the client settings of single operations (experimental).

The JSON object, inline or in a file, maps operation references to their settings. An operation reference is the
JSON pointer of the path item method, such as `/paths/~1pets/get`, optionally after a document and `#`, such as
`pets.yaml#/paths/~1pets/get`. A relative document resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table. The settings are
`resource`, `name`, `parameter_names` (keyed by location and name,
such as `{"query:limit": "page_size"}`), `request_media_type`, `response_media_type`, `description`,
`body_arguments`, which overrides `--client-body-arguments`, `body_field_names` (keyed by media type, then property,
such as `{"application/json": {"petName": "pet_name"}}`), and `runtime`: `request_id_header`, `success_statuses`,
`retry_safety`, `idempotency` (`{"header_name": "Idempotency-Key"}`), `retry_after_ms_header`,
`should_retry_header`, `auth_challenge_less_401`, and `accepted_content_encodings`. In pyproject.toml,
`client-operations` is a table, and a command-line value replaces the whole table.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-operations` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-operations '{"/paths/~1pets/get": {"name": "list_all", "parameter_names": {"query:limit": "page_size"}}}' # (1)!
    ```

    1. :material-arrow-left: `--client-operations` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListAllResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_all(
            self,
            *,
            page_size: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListAllResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (page_size, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_all(
            self,
            *,
            page_size: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListAllResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (page_size, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_all(
            self,
            *,
            page_size: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (page_size, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_all(
            self,
            *,
            page_size: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (page_size, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-output` {#client-output}

Write the client package to this directory (experimental).

`--client-output` is required with `--generate-client`. A path given on the command line is relative to the working
directory, and the `client-output` key of pyproject.toml is relative to the pyproject.toml directory, as for
`--output`.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-output` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models # (1)!
    ```

    1. :material-arrow-left: `--client-output` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-package` {#client-package}

Name the import path of the client package (experimental).

`--client-package` is required with `--generate-client`. The generated README and the dependency command a
generation prints name the package by it; the package imports its own modules relatively.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-package` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models # (1)!
    ```

    1. :material-arrow-left: `--client-package` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-protocols` {#client-protocols}

Declare the protocol helpers of the client package (experimental).

The JSON object, inline or in a file, maps each helper's name to the definition of a pagination, polling, stream,
WebSocket, cache, upload, or webhook helper, as the Python client guide describes. Relative documents that its
references name resolve against the directory of the JSON file that holds them, on the command line and for the
`client-protocols` key alike. For inline JSON they resolve against the working directory, and for a table of the
`client-protocols` key against the pyproject.toml directory.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-protocols` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-protocols protocols.json # (1)!
    ```

    1. :material-arrow-left: `--client-protocols` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous protocol helpers of this package, by their dotted names."""

    from __future__ import annotations

    from functools import cached_property

    from models import Pet as _dcg_type_0

    from .._runtime.client.client import ClientCore as ClientCore_1
    from .._runtime.protocols.client import ClientCore
    from .._runtime.protocols.pagination import (
        Page,
        Pager,
        first_page,
        following_page,
        iterate_pages,
        resume_pages,
    )
    from .._runtime.protocols.resume import ResumeState
    from ..options import UNSET, RequestOptions, SessionOptions, Unset
    from ..types.pets import ListPetsResponse
    from . import PaginationOptions, _plans


    class ProtocolHelpers:
        """The protocol helpers of this API."""

        def __init__(self, core: ClientCore_1) -> None:
            """Keep the client core its helpers send through."""
            self._core = ClientCore.from_client(core)

        @cached_property
        def pets(self) -> PetsProtocols:
            """The pets protocol helpers."""
            return PetsProtocols(self._core)


    class PetsProtocols:
        """The pets protocol helpers."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core its helpers send through."""
            self._core = core

        @cached_property
        def all(self) -> PetsAllPagination:
            """The pets.all pagination helper."""
            return PetsAllPagination(self._core)


    class PetsAllPagination:
        """The pets.all pagination helper of GET /pets."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the helper sends through."""
            self._core = core

        def page(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            pagination_options: PaginationOptions | None = None,
            options: RequestOptions | None = None,
            session_options: SessionOptions | None = None,
        ) -> Page[_dcg_type_0, ListPetsResponse]:
            """Fetch the first page of GET /pets."""
            return first_page(
                self._core,
                _plans.PLAN_0,
                (limit, cursor),
                pagination_options=pagination_options,
                options=options,
                session_options=session_options,
            )

        def iterate(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            pagination_options: PaginationOptions | None = None,
            options: RequestOptions | None = None,
            session_options: SessionOptions | None = None,
        ) -> Pager[_dcg_type_0, ListPetsResponse]:
            """Return a pager over the items of GET /pets; it sends nothing until it is iterated."""
            return iterate_pages(
                self._core,
                _plans.PLAN_0,
                (limit, cursor),
                pagination_options=pagination_options,
                options=options,
                session_options=session_options,
            )

        def next_page(
            self,
            page: Page[_dcg_type_0, ListPetsResponse],
            *,
            pagination_options: PaginationOptions | None = None,
            options: RequestOptions | None = None,
            session_options: SessionOptions | None = None,
        ) -> Page[_dcg_type_0, ListPetsResponse] | None:
            """Fetch the page after a page of this helper, or return None after the last page."""
            return following_page(
                self._core,
                _plans.PLAN_0,
                page,
                pagination_options=pagination_options,
                options=options,
                session_options=session_options,
            )

        def resume(
            self,
            state: ResumeState,
            *,
            pagination_options: PaginationOptions | None = None,
            options: RequestOptions | None = None,
            session_options: SessionOptions | None = None,
        ) -> Pager[_dcg_type_0, ListPetsResponse]:
            """Return a pager continuing a checkpoint; it sends nothing until it is iterated."""
            return resume_pages(
                self._core,
                _plans.PLAN_0,
                state,
                pagination_options=pagination_options,
                options=options,
                session_options=session_options,
            )
    ```

    <!-- fmt: on -->

---

## `--client-resource-names` {#client-resource-names}

Name the resources of tags (experimental).

The JSON object, inline or in a file, maps a tag to the dotted namespace of the resource its operations join, such
as `{"pets": "store.pets"}` for `client.store.pets`. Other operations join the resource of their first tag. In
pyproject.toml, `client-resource-names` is a table, and a command-line value replaces the whole table.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-resource-names` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-resource-names '{"pets": "animals"}' # (1)!
    ```

    1. :material-arrow-left: `--client-resource-names` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the animals resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.animals import CreatePetResponse, ListPetsResponse


    class AnimalsResource:
        """The animals operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> AnimalsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return AnimalsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> AnimalsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return AnimalsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> AnimalsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return AnimalsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class AnimalsWithResponse:
        """The animals operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class AnimalsWithRawResponse:
        """The animals operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class AnimalsWithStreamingResponse:
        """The animals operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--client-server-base-url` {#client-server-base-url}

Resolve relative server URLs against this base URL (experimental).

An absolute `http` or `https` URL without userinfo, query, or fragment. Without it, relative server URLs resolve
against the document's URL when it is read from one.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-server-base-url` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-server-base-url https://api.example.com/ # (1)!
    ```

    1. :material-arrow-left: `--client-server-base-url` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: /v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options-relative.yaml

    """The operation registry of this package; regenerate it instead of editing."""

    from __future__ import annotations

    from typing import Final

    from ._generated import model_bindings
    from ._runtime.client.operations import (
        BodyMedia,
        OperationPlan,
        ParameterSpec,
        RequestBody,
        ResponseDecoder,
        ServerPlan,
        model_branch,
    )
    from ._runtime.model_codecs.parameters import ParameterPlan
    from .types.pets import CreatePetResponse, ListPetsResponse

    _SERVERS_0: Final = (ServerPlan(url='https://api.example.com/v1'),)

    OPERATION_0: Final[OperationPlan[ListPetsResponse]] = OperationPlan(
        operation_id='listPets',
        method='GET',
        path='/pets',
        servers=_SERVERS_0,
        responses=ResponseDecoder(
            (model_branch('200', 'application/json', 'json', model_bindings.codec_2),),
            (),
        ),
        parameters=(
            ParameterSpec(
                plan=ParameterPlan(
                    location='query',
                    name='limit',
                    style='form',
                    explode=True,
                    kind='integer',
                    reserved_names=('cursor',),
                ),
                codec=model_bindings.codec_0,
            ),
            ParameterSpec(
                plan=ParameterPlan(
                    location='query',
                    name='cursor',
                    style='form',
                    explode=True,
                    reserved_names=('limit',),
                ),
                codec=model_bindings.codec_1,
            ),
        ),
    )

    OPERATION_1: Final[OperationPlan[CreatePetResponse]] = OperationPlan(
        operation_id='createPet',
        method='POST',
        path='/pets',
        servers=_SERVERS_0,
        responses=ResponseDecoder(
            (model_branch('201', 'application/json', 'json', model_bindings.codec_4),),
            (),
        ),
        body=RequestBody(
            media=(
                BodyMedia(
                    media_type='application/json',
                    kind='json',
                    codec=model_bindings.codec_3,
                ),
            ),
            default='application/json',
            required=True,
        ),
    )
    ```

    <!-- fmt: on -->

---

## `--client-signature-style` {#client-signature-style}

Declare the arguments of the operation methods (experimental).

`explicit` (the default) declares each argument as a keyword parameter; `unpack` declares one
`**kwargs: Unpack[TypedDict]` per operation, whose keys are the same arguments.

**Option relationships:**

- **Requires:** [`--generate-client`](target-generation-options.md#generate-client) - `--client-signature-style` requires `--generate-client httpx2`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models --client-signature-style unpack # (1)!
    ```

    1. :material-arrow-left: `--client-signature-style` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property

    from typing_extensions import Unpack

    from ... import _operations
    from ..._generated.client_arguments import (
        KEYWORDS_0,
        KEYWORDS_1,
        Operation0Arguments,
        Operation1Arguments,
    )
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(self, **kwargs: Unpack[Operation0Arguments]) -> ListPetsResponse:
            """List the pets."""
            KEYWORDS_0.check(kwargs)
            return self._core.execute(
                _operations.OPERATION_0,
                (kwargs.get('limit', UNSET), kwargs.get('cursor', UNSET)),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            ).data

        def create_pet(
            self,
            **kwargs: Unpack[Operation1Arguments],
        ) -> CreatePetResponse:
            """Create a pet."""
            KEYWORDS_1.check(kwargs)
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=kwargs['body'],
                media_type=kwargs.get('media_type'),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            **kwargs: Unpack[Operation0Arguments],
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            KEYWORDS_0.check(kwargs)
            return self._core.execute(
                _operations.OPERATION_0,
                (kwargs.get('limit', UNSET), kwargs.get('cursor', UNSET)),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )

        def create_pet(
            self,
            **kwargs: Unpack[Operation1Arguments],
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            KEYWORDS_1.check(kwargs)
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=kwargs['body'],
                media_type=kwargs.get('media_type'),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(self, **kwargs: Unpack[Operation0Arguments]) -> RawResponse:
            """List the pets."""
            KEYWORDS_0.check(kwargs)
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (kwargs.get('limit', UNSET), kwargs.get('cursor', UNSET)),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )

        def create_pet(self, **kwargs: Unpack[Operation1Arguments]) -> RawResponse:
            """Create a pet."""
            KEYWORDS_1.check(kwargs)
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=kwargs['body'],
                media_type=kwargs.get('media_type'),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            **kwargs: Unpack[Operation0Arguments],
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            KEYWORDS_0.check(kwargs)
            return self._core.stream(
                _operations.OPERATION_0,
                (kwargs.get('limit', UNSET), kwargs.get('cursor', UNSET)),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )

        def create_pet(
            self,
            **kwargs: Unpack[Operation1Arguments],
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            KEYWORDS_1.check(kwargs)
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=kwargs['body'],
                media_type=kwargs.get('media_type'),
                options=kwargs.get('options'),
                response_media_type=kwargs.get('response_media_type'),
            )
    ```

    <!-- fmt: on -->

---

## `--generate-client` {#generate-client}

Generate an HTTPX2 client package for the models (experimental).

`--generate-client httpx2` generates the models of an OpenAPI document as usual and, in the same run, an HTTPX2
client package at `--client-output`: a `Client` and an `AsyncClient` with a resource for each tag and a method for
each operation. The models need the `api` scope (`--openapi-scopes schemas api`) and a target Python version of 3.11
or later. Like every client setting, it can also be set in `[tool.datamodel-codegen]` of pyproject.toml, here as
`generate-client = "httpx2"`. It cannot be used with `--generate-server`.

**Option relationships:**

- **Requires:** [`--client-output`](target-generation-options.md#client-output) - `--generate-client` requires `--client-output`.
- **Requires:** [`--client-package`](target-generation-options.md#client-package) - `--generate-client` requires `--client-package`.
- **Requires:** [`--client-model-package`](target-generation-options.md#client-model-package) - `--generate-client` requires `--client-model-package`.
- **Conflicts:** [`--generate-server`](target-generation-options.md#generate-server) - `--generate-client` can not be used with `--generate-server`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-client httpx2 --client-output client --client-package client --client-model-package models # (1)!
    ```

    1. :material-arrow-left: `--generate-client` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info:
      title: Pets
      version: 1.0.0
    servers:
      - url: https://api.example.com/v1
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          summary: List the pets.
          parameters:
            - name: limit
              in: query
              schema: {type: integer}
            - name: cursor
              in: query
              schema: {type: string}
          responses:
            '200':
              description: A page of pets.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pets'}
        post:
          operationId: createPet
          tags: [pets]
          summary: Create a pet.
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: '#/components/schemas/NewPet'}
          responses:
            '201':
              description: The created pet.
              content:
                application/json:
                  schema: {$ref: '#/components/schemas/Pet'}
    components:
      schemas:
        NewPet:
          type: object
          required: [name]
          properties:
            name: {type: string}
            tag: {type: string}
        Pet:
          type: object
          required: [id, name]
          properties:
            id: {type: integer}
            name: {type: string}
            tag: {type: string}
        Pets:
          type: object
          required: [data]
          properties:
            data:
              type: array
              items: {$ref: '#/components/schemas/Pet'}
            next_cursor: {type: [string, 'null']}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """The synchronous operations of the pets resource."""

    from __future__ import annotations

    from contextlib import AbstractContextManager
    from functools import cached_property
    from typing import Literal

    from models import NewPet as _dcg_type_0

    from ... import _operations
    from ..._runtime.client.client import ClientCore
    from ...options import UNSET, RequestOptions, Unset
    from ...responses import RawResponse, Response
    from ...types.pets import CreatePetResponse, ListPetsResponse


    class PetsResource:
        """The pets operations."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        @cached_property
        def with_response(self) -> PetsWithResponse:
            """The same operations, returning each result with its response metadata."""
            return PetsWithResponse(self._core)

        @cached_property
        def with_raw_response(self) -> PetsWithRawResponse:
            """The same operations, returning each raw response with its body read into memory."""
            return PetsWithRawResponse(self._core)

        @cached_property
        def with_streaming_response(self) -> PetsWithStreamingResponse:
            """The same operations, returning blocks that send each call on entry and stream its response."""
            return PetsWithStreamingResponse(self._core)

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> ListPetsResponse:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            ).data

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> CreatePetResponse:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            ).data


    class PetsWithResponse:
        """The pets operations, returning each result with its response metadata."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[ListPetsResponse]:
            """List the pets."""
            return self._core.execute(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> Response[CreatePetResponse]:
            """Create a pet."""
            return self._core.execute(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithRawResponse:
        """The pets operations, returning each raw response with its body read into memory."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """List the pets."""
            return self._core.execute_raw(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> RawResponse:
            """Create a pet."""
            return self._core.execute_raw(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )


    class PetsWithStreamingResponse:
        """The pets operations, returning blocks that send each call on entry and stream its response."""

        def __init__(self, core: ClientCore) -> None:
            """Keep the client core the operations send through."""
            self._core = core

        def list_pets(
            self,
            *,
            limit: int | Unset = UNSET,
            cursor: str | Unset = UNSET,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """List the pets."""
            return self._core.stream(
                _operations.OPERATION_0,
                (limit, cursor),
                options=options,
                response_media_type=response_media_type,
            )

        def create_pet(
            self,
            *,
            body: _dcg_type_0,
            media_type: Literal['application/json'] | None = None,
            response_media_type: Literal['application/json'] | None = None,
            options: RequestOptions | None = None,
        ) -> AbstractContextManager[RawResponse]:
            """Create a pet."""
            return self._core.stream(
                _operations.OPERATION_1,
                (),
                body=body,
                media_type=media_type,
                options=options,
                response_media_type=response_media_type,
            )
    ```

    <!-- fmt: on -->

---

## `--generate-server` {#generate-server}

Generate a FastAPI server package for the models (experimental).

`--generate-server fastapi` generates the models of an OpenAPI document as usual and, in the same run, a FastAPI
server package at `--server-output`: routers, a service Protocol for each router group, and the application. The
models need the `api` scope (`--openapi-scopes schemas api`), a Pydantic v2 output model type, and a target Python
version of 3.11 or later. Like every server setting, it can also be set in `[tool.datamodel-codegen]` of
pyproject.toml, here as `generate-server = "fastapi"`.

**Option relationships:**

- **Requires:** [`--server-output`](target-generation-options.md#server-output) - `--generate-server` requires `--server-output`.
- **Requires:** [`--server-package`](target-generation-options.md#server-package) - `--generate-server` requires `--server-package`.
- **Requires:** [`--server-model-package`](target-generation-options.md#server-model-package) - `--generate-server` requires `--server-model-package`.
- **Conflicts:** [`--generate-client`](target-generation-options.md#generate-client) - `--generate-server` can not be used with `--generate-client`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models # (1)!
    ```

    1. :material-arrow-left: `--generate-server` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-body-mode` {#server-body-mode}

Choose how service methods receive request bodies (experimental).

`typed` (the default) passes the body validated as its model; `request` passes the raw Starlette `Request` instead,
for methods that read the body themselves. `--server-body-modes` overrides it for single operations.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-body-mode` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-body-mode request # (1)!
    ```

    1. :material-arrow-left: `--server-body-mode` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi import Request
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            request: Request,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            request: Request,
            name: str,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-body-modes` {#server-body-modes}

Set the body mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `typed` or `request`, and overrides
`--server-body-mode` for those operations. A relative document of an operation reference resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-body-modes` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-body-modes '{"/paths/~1pets~1{name}/put": "request"}' # (1)!
    ```

    1. :material-arrow-left: `--server-body-modes` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi import Request
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            request: Request,
            name: str,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-handler-mode` {#server-handler-mode}

Declare the service methods as plain or coroutine functions (experimental).

`sync` (the default) declares plain methods, which FastAPI runs in a thread pool; `async` declares coroutine
methods. `--server-handler-modes` overrides it for single operations.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-handler-mode` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-handler-mode async # (1)!
    ```

    1. :material-arrow-left: `--server-handler-mode` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        async def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        async def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        async def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-handler-modes` {#server-handler-modes}

Set the handler mode of single operations (experimental).

The JSON object, inline or in a file, maps operation references to `sync` or `async`, and overrides
`--server-handler-mode` for those operations. An operation reference is the JSON pointer of the path item method,
such as `/paths/~1pets/get`, optionally after a document and `#`, such as `pets.yaml#/paths/~1pets/get`.
A relative document of an operation reference resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table. In pyproject.toml, `server-handler-modes` is a table, and a command-line value replaces the whole
table.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-handler-modes` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-handler-modes '{"/paths/~1pets/post": "async"}' # (1)!
    ```

    1. :material-arrow-left: `--server-handler-modes` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        async def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-include-request` {#server-include-request}

Pass the Starlette Request to every service method (experimental).

Each service method takes a `request` keyword argument as well as the operation's arguments.
`--no-server-include-request` turns off a `server-include-request = true` of pyproject.toml.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-include-request` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-include-request # (1)!
    ```

    1. :material-arrow-left: `--server-include-request` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi import Request
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            request: Request,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            request: Request,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            request: Request,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-layout` {#server-layout}

Choose how the server package lays out its routes (experimental).

`routers` (the default) writes one router module per tag under `routers/`; `single` writes every route to one
`routes.py` module.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-layout` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-layout single # (1)!
    ```

    1. :material-arrow-left: `--server-layout` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Endpoints of every operation; regenerate them instead of editing."""

    from collections.abc import Sequence
    from typing import Annotated, Final

    import models
    from fastapi import APIRouter, Body, Path, Query
    from fastapi.responses import Response

    from ._generated import contract
    from ._generated.contract import OperationDependencies
    from ._runtime.server.application import Dependency, Wiring, build
    from ._runtime.server.responses import dispatch
    from .services import Service


    def _add_list_pets(router: APIRouter, wiring: Wiring) -> None:
        list_pets_handler = wiring.handlers['list_pets']

        def list_pets(*, limit: Annotated[int, Query(alias='limit')] = 20) -> object:
            return dispatch(list_pets_handler(limit=limit), contract.ListPets.RESPONSES)

        router.add_api_route(
            '/pets',
            list_pets,
            methods=['GET'],
            status_code=200,
            response_model=models.FieldPetsGetResponse,
            response_model_by_alias=True,
            response_model_exclude_unset=True,
            operation_id='listPets',
            tags=['pets'],
            response_description='The pets.',
            dependencies=wiring.dependencies.get('/paths/~1pets/get'),
        )


    def _add_create_pet(router: APIRouter, wiring: Wiring) -> None:
        create_pet_handler = wiring.handlers['create_pet']

        def create_pet(
            *,
            body: Annotated[models.Pet, Body(media_type='application/json')],
        ) -> object:
            return dispatch(create_pet_handler(body=body), contract.CreatePet.RESPONSES)

        router.add_api_route(
            '/pets',
            create_pet,
            methods=['POST'],
            status_code=200,
            response_model=models.Pet,
            response_model_by_alias=True,
            response_model_exclude_unset=True,
            operation_id='createPet',
            tags=['pets'],
            response_description='Already there.',
            responses={'201': {'model': models.Pet, 'description': 'Created.'}},
            dependencies=wiring.dependencies.get('/paths/~1pets/post'),
        )


    def _add_replace_pet(router: APIRouter, wiring: Wiring) -> None:
        replace_pet_handler = wiring.handlers['replace_pet']

        def replace_pet(
            *,
            name: Annotated[str, Path(alias='name')],
            body: Annotated[models.Pet, Body(media_type='application/json')],
        ) -> object:
            return dispatch(
                replace_pet_handler(name=name, body=body),
                contract.ReplacePet.RESPONSES,
            )

        router.add_api_route(
            '/pets/{name}',
            replace_pet,
            methods=['PUT'],
            status_code=204,
            response_model=None,
            response_class=Response,
            operation_id='replacePet',
            tags=['pets'],
            response_description='Replaced.',
            responses={'204': {'description': 'Replaced.'}},
            dependencies=wiring.dependencies.get('/paths/~1pets~1{name}/put'),
        )


    LITERAL_ROUTES: Final = (
        (contract.ListPets.OPERATION, _add_list_pets),
        (contract.CreatePet.OPERATION, _add_create_pet),
    )
    TEMPLATED_ROUTES: Final = ((contract.ReplacePet.OPERATION, _add_replace_pet),)


    def build_router(
        *,
        service: Service,
        dependencies: Sequence[Dependency] = (),
        operation_dependencies: OperationDependencies | None = None,
        prefix: str = "",
    ) -> APIRouter:
        """Check the service and settings, then register every operation on a new router, literal paths first."""
        return build(
            (*LITERAL_ROUTES, *TEMPLATED_ROUTES),
            services={'service': service},
            dependencies=dependencies,
            operation_dependencies=operation_dependencies,
            prefix=prefix,
        )
    ```

    <!-- fmt: on -->

---

## `--server-model-package` {#server-model-package}

Name the import path of the models the server package imports (experimental).

`--server-model-package` is required with `--generate-server`, and names the module or package `--output`
generates.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-model-package` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models # (1)!
    ```

    1. :material-arrow-left: `--server-model-package` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-operation-names` {#server-operation-names}

Name the service methods of single operations (experimental).

The JSON object, inline or in a file, maps operation references to method names. Other operations take the
snake_case form of their operationId, or of their method and path. A relative document of an operation reference resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-operation-names` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-operation-names '{"/paths/~1pets/get": "list_all"}' # (1)!
    ```

    1. :material-arrow-left: `--server-operation-names` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_all(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-output` {#server-output}

Write the server package to this directory (experimental).

`--server-output` is required with `--generate-server`. A path given on the command line is relative to the working
directory, and the `server-output` key of pyproject.toml is relative to the pyproject.toml directory, as for
`--output`.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-output` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models # (1)!
    ```

    1. :material-arrow-left: `--server-output` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-package` {#server-package}

Name the import path of the server package (experimental).

`--server-package` is required with `--generate-server`. The generated README and the dependency command a
generation prints name the package by it; the package imports its own modules relatively.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-package` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models # (1)!
    ```

    1. :material-arrow-left: `--server-package` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-parameter-names` {#server-parameter-names}

Name the method arguments of single operations (experimental).

The JSON object, inline or in a file, maps operation references to objects that map a parameter, written as its
location and name such as `query:limit` or `header:X-Request-Id`, to the argument name. A relative document of an operation reference resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-parameter-names` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-parameter-names '{"/paths/~1pets/get": {"query:limit": "page_size"}}' # (1)!
    ```

    1. :material-arrow-left: `--server-parameter-names` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class PetsService(Protocol):
        """Implement the pets operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            page_size: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---

## `--server-primary-responses` {#server-primary-responses}

Choose the response a bare return value of an operation takes (experimental).

The JSON object, inline or in a file, maps operation references to an object with the `status_code` of a declared
response and, when that response has several media types, its `media_type`. Without an entry, the server infers the
primary response from the declared success responses. A relative document of an operation reference resolves against the JSON file that holds the reference, or
without a file against the working directory, and against the pyproject.toml directory for a table.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-primary-responses` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-primary-responses '{"/paths/~1pets/post": {"status_code": 201}}' # (1)!
    ```

    1. :material-arrow-left: `--server-primary-responses` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Endpoints of the pets operations; regenerate them instead of editing."""

    from collections.abc import Sequence
    from typing import Annotated, Final

    import models
    from fastapi import APIRouter, Body, Path, Query
    from fastapi.responses import Response

    from .._generated import contract
    from .._generated.contract import OperationDependencies
    from .._runtime.server.application import Dependency, Wiring, build
    from .._runtime.server.responses import dispatch
    from ..services import PetsService


    def _add_list_pets(router: APIRouter, wiring: Wiring) -> None:
        list_pets_handler = wiring.handlers['list_pets']

        def list_pets(*, limit: Annotated[int, Query(alias='limit')] = 20) -> object:
            return dispatch(list_pets_handler(limit=limit), contract.ListPets.RESPONSES)

        router.add_api_route(
            '/pets',
            list_pets,
            methods=['GET'],
            status_code=200,
            response_model=models.FieldPetsGetResponse,
            response_model_by_alias=True,
            response_model_exclude_unset=True,
            operation_id='listPets',
            tags=['pets'],
            response_description='The pets.',
            dependencies=wiring.dependencies.get('/paths/~1pets/get'),
        )


    def _add_create_pet(router: APIRouter, wiring: Wiring) -> None:
        create_pet_handler = wiring.handlers['create_pet']

        def create_pet(
            *,
            body: Annotated[models.Pet, Body(media_type='application/json')],
        ) -> object:
            return dispatch(create_pet_handler(body=body), contract.CreatePet.RESPONSES)

        router.add_api_route(
            '/pets',
            create_pet,
            methods=['POST'],
            status_code=201,
            response_model=models.Pet,
            response_model_by_alias=True,
            response_model_exclude_unset=True,
            operation_id='createPet',
            tags=['pets'],
            response_description='Created.',
            responses={'200': {'model': models.Pet, 'description': 'Already there.'}},
            dependencies=wiring.dependencies.get('/paths/~1pets/post'),
        )


    def _add_replace_pet(router: APIRouter, wiring: Wiring) -> None:
        replace_pet_handler = wiring.handlers['replace_pet']

        def replace_pet(
            *,
            name: Annotated[str, Path(alias='name')],
            body: Annotated[models.Pet, Body(media_type='application/json')],
        ) -> object:
            return dispatch(
                replace_pet_handler(name=name, body=body),
                contract.ReplacePet.RESPONSES,
            )

        router.add_api_route(
            '/pets/{name}',
            replace_pet,
            methods=['PUT'],
            status_code=204,
            response_model=None,
            response_class=Response,
            operation_id='replacePet',
            tags=['pets'],
            response_description='Replaced.',
            responses={'204': {'description': 'Replaced.'}},
            dependencies=wiring.dependencies.get('/paths/~1pets~1{name}/put'),
        )


    LITERAL_ROUTES: Final = (
        (contract.ListPets.OPERATION, _add_list_pets),
        (contract.CreatePet.OPERATION, _add_create_pet),
    )
    TEMPLATED_ROUTES: Final = ((contract.ReplacePet.OPERATION, _add_replace_pet),)


    def build_router(
        *,
        pets: PetsService,
        dependencies: Sequence[Dependency] = (),
        operation_dependencies: OperationDependencies | None = None,
        prefix: str = "",
    ) -> APIRouter:
        """Check the service and settings, then register the pets operations on a new router, literal paths first."""
        return build(
            (*LITERAL_ROUTES, *TEMPLATED_ROUTES),
            services={'pets': pets},
            dependencies=dependencies,
            operation_dependencies=operation_dependencies,
            prefix=prefix,
        )
    ```

    <!-- fmt: on -->

---

## `--server-router-names` {#server-router-names}

Name router groups (experimental).

The JSON object, inline or in a file, maps group keys, such as `tag:pets` for the operations whose first tag is
`pets`, to the name of the router module, the `create_app` argument, and the `<Name>Service` Protocol.

**Option relationships:**

- **Requires:** [`--generate-server`](target-generation-options.md#generate-server) - `--server-router-names` requires `--generate-server fastapi`.

!!! tip "Usage"

    ```bash
    datamodel-codegen --input schema.json --input-file-type openapi --output models.py --target-python-version 3.11 --openapi-scopes schemas api --output-model-type pydantic_v2.BaseModel --formatters builtin --disable-timestamp --generate-server fastapi --server-output server --server-package server --server-model-package models --server-router-names '{"tag:pets": "animals"}' # (1)!
    ```

    1. :material-arrow-left: `--server-router-names` - the option documented here

??? example "Examples"

    **Input Schema:**

    ```yaml
    openapi: 3.1.0
    info: {title: Pets, version: "1.0"}
    paths:
      /pets:
        get:
          operationId: listPets
          tags: [pets]
          parameters:
            - {name: limit, in: query, schema: {type: integer, default: 20}}
          responses:
            "200":
              description: The pets.
              content:
                application/json:
                  schema: {type: array, items: {$ref: "#/components/schemas/Pet"}}
        post:
          operationId: createPet
          tags: [pets]
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "200":
              description: Already there.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
            "201":
              description: Created.
              content:
                application/json:
                  schema: {$ref: "#/components/schemas/Pet"}
      /pets/{name}:
        put:
          operationId: replacePet
          tags: [pets]
          parameters:
            - {name: name, in: path, required: true, schema: {type: string}}
          requestBody:
            required: true
            content:
              application/json:
                schema: {$ref: "#/components/schemas/Pet"}
          responses:
            "204": {description: Replaced.}
    components:
      schemas:
        Pet:
          type: object
          required: [name]
          properties:
            name: {type: string}
    ```

    **Output:**

    <!-- fmt: off -->

    ```python
    # generated by datamodel-codegen:
    #   filename:  options.yaml

    """Service interfaces of this package's router groups; regenerate them instead of editing."""

    from __future__ import annotations

    from abc import abstractmethod
    from typing import Protocol

    import models
    from fastapi.responses import Response

    from ._runtime.server.responses import HTTPResult


    class AnimalsService(Protocol):
        """Implement the animals operations: subclass this Protocol, or give an object its methods."""

        @abstractmethod
        def list_pets(
            self,
            *,
            limit: int,
        ) -> (
            models.FieldPetsGetResponse
            | HTTPResult[models.FieldPetsGetResponse]
            | Response
        ): ...

        @abstractmethod
        def create_pet(
            self,
            *,
            body: models.Pet,
        ) -> models.Pet | HTTPResult[models.Pet] | Response: ...

        @abstractmethod
        def replace_pet(
            self,
            *,
            name: str,
            body: models.Pet,
        ) -> None | HTTPResult[None] | Response: ...
    ```

    <!-- fmt: on -->

---
