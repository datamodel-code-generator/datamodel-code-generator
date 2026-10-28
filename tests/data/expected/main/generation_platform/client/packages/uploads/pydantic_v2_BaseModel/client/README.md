# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://api.example.com") as client:
    result = client.files.create_file(Upload_Length=1, Tus_Resumable="...")
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com") as client:
        result = await client.files.create_file(Upload_Length=1, Tus_Resumable="...")
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, bodies, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com` for `files.create_file`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://api.example.com") as client:
    try:
        result = client.files.create_file(Upload_Length=1, Tus_Resumable="...")
    except NotFoundError as error:
        print("not found:", error.body)
    except APIStatusError as error:
        print(error.status_code, error.body)
    except APIConnectionError as error:
        print(error)
```

A final status the operation does not declare as a success raises `APIStatusError`, or its subclass for the status: `BadRequestError` (400), `AuthenticationError` (401), `PermissionDeniedError` (403), `NotFoundError` (404), `ConflictError` (409), `UnprocessableEntityError` (422), `RateLimitError` (429), or `InternalServerError` (500 and above).
Its `body` is the payload decoded with the error schema the operation declares for the status, such as that of a `default` response, or else the bounded raw bytes; `status_code` and `headers` give the rest of the response.
A failure to reach the server raises `APIConnectionError`, after the retries it allows, and a timeout its subclass `APITimeoutError`.
Every exception of `client.errors` derives from `SDKError`; the runtime reference describes the others.

## Operations

`Client` and `AsyncClient` hold the operations in resource attributes, as `client.<resource>.<method>`; an `AsyncClient` method takes the same arguments.
Each method declares its parameters, its body, and its options as keyword arguments.
A request body is given as `body=`, unless an operation's `body_arguments` is `both`.
An optional argument defaults to its schema's default, or to `UNSET`, which leaves it out of the request; `options=RequestOptions(...)` sets the settings of one call.
The signatures spell types as `resources/<resource>/_sync.py` imports them: from `client.options`, the result types from `client.types.<resource>`, `JSONValue`, the type of any JSON value, from `client.types`, the `models` models, and the other public modules.
`--client-operations` takes the operation reference of each as its key.
The `with_response`, `with_raw_response`, and `with_streaming_response` attributes of a resource hold the same methods, returning the result with its response metadata, the raw response, or a block that streams the response.

### `client.files.create_file`

`POST /files`, operation reference `/paths/~1files/post`

```python
def create_file(
    *,
    Upload_Length: int,
    Tus_Resumable: str,
    X_Name: str | UNSET = UNSET,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CreateFileResponse: ...
```

### `client.files.probe_file`

`HEAD /files/{fileId}`, operation reference `/paths/~1files~1{fileId}/head`

```python
def probe_file(
    *,
    fileId: str,
    Tus_Resumable: str,
    options: RequestOptions | None = None,
) -> ProbeFileResponse: ...
```

### `client.files.append_file`

`PATCH /files/{fileId}`, operation reference `/paths/~1files~1{fileId}/patch`, request body as `body=` only (`application/offset+octet-stream`)

```python
def append_file(
    *,
    fileId: str,
    Upload_Offset: int,
    Tus_Resumable: str,
    X_Chunk_Length: int | UNSET = UNSET,
    Upload_Checksum: str | UNSET = UNSET,
    body: SyncBinaryBody,
    media_type: Literal['application/offset+octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> AppendFileResponse: ...
```

### `client.files.put_file_chunk`

`PUT /files/{fileId}`, operation reference `/paths/~1files~1{fileId}/put`, request body as `body=` only (`application/octet-stream`)

```python
def put_file_chunk(
    *,
    fileId: str,
    offset: int,
    checksum: str | UNSET = UNSET,
    body: SyncBinaryBody,
    media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> PutFileChunkResponse: ...
```

### `client.files.get_file_status`

`GET /files/{fileId}/status`, operation reference `/paths/~1files~1{fileId}~1status/get`

```python
def get_file_status(
    *,
    fileId: str,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> GetFileStatusResponse: ...
```

### `client.files.complete_file`

`POST /files/{fileId}/complete`, operation reference `/paths/~1files~1{fileId}~1complete/post`

```python
def complete_file(
    *,
    fileId: str,
    response_media_type: Literal['application/json'] | None = None,
    options: RequestOptions | None = None,
) -> CompleteFileResponse: ...
```

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
An upload helper's `start` reads its source once and creates the upload; the handle's `advance` appends one chunk and
`run` appends the rest and completes it, and `resume` continues a handle's `checkpoint()` from the offset the server
holds. `close()` or `aclose()` stops only local uploading. See the runtime reference for their limits.

```json
[
  {
    "helper": "files.upload",
    "kind": "resumable_upload",
    "operation": "files.create_file",
    "method": "POST",
    "path": "/files"
  },
  {
    "helper": "files.finish",
    "kind": "resumable_upload",
    "operation": "files.create_file",
    "method": "POST",
    "path": "/files"
  },
  {
    "helper": "files.put",
    "kind": "resumable_upload",
    "operation": "files.create_file",
    "method": "POST",
    "path": "/files"
  }
]
```

## Dependencies

The package needs these distributions at run time:

- `httpx2>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.14.0`

Add them to your project with the command each generation prints:

```console
uv add "httpx2>=2.13.0" "typing-extensions>=4.16" "pydantic>=2.14.0"
```

## Regenerating

Run the same generation again whenever the OpenAPI document changes; the `--check` option reports the files it would change without writing them.
Generation overwrites every generated file, restoring any you edited or deleted, and never deletes one: delete the modules of removed resources yourself, which `--check` lists as extra files and each generation warns about.
Each generation prints the `uv add` command that adds the runtime dependencies of the package to your project;
run it again when a new version of datamodel-code-generator changes them.

## Generation details

### Retries

The default is two retries, subject to operation safety, replayable input, delay, and the shared deadline.
The default status set is 408, 429, 500, 502, 503, and 504. Server retry delays are respected by default.
`retry=RetryOptions(respect_retry_after=False)` is an explicit application override that ignores those server hints;
give it deliberately to the client, a view, or a call. This package does not embed that override.

### Profile

| Setting | Value |
| --- | --- |
| `signature_style` | `explicit` |
| `body_arguments` | `body` |
| Model backend | `pydantic_v2.BaseModel` |
| Models | `models` |

### Selected operation contracts

These declarations come from the finalized operation selection and generation configuration. A key contract does
not guarantee exactly-once execution. An unsafe method needs a declared header and an active key to be replay-safe.
No vendor retry-header name is inferred. Security requirements preserve ordered OR
alternatives and their AND members; `[]` and `[{}]` remain distinct declared anonymous choices. Missing required
credentials fail before sending. `auth_challenge_less_401` is the explicit generation declaration at
`operations[].runtime.auth_challenge_less_401`; it is false by default and is not a client option.

```json
[
  {
    "operation": "files.create_file",
    "method": "POST",
    "path": "/files",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.probe_file",
    "method": "HEAD",
    "path": "/files/{fileId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.append_file",
    "method": "PATCH",
    "path": "/files/{fileId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.put_file_chunk",
    "method": "PUT",
    "path": "/files/{fileId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.get_file_status",
    "method": "GET",
    "path": "/files/{fileId}/status",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.complete_file",
    "method": "POST",
    "path": "/files/{fileId}/complete",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  }
]
```

### Capabilities

| Capability | Declared |
| --- | --- |
| Security | none |
| Helpers | `uploads` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameter_reads.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `resume.py`, `sources.py`, `uploads.py`, `values.py`, `writes.py`
