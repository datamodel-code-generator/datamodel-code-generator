# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://media.example.com/api") as client:
    result = client.forms.submit_form(body=...)
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://media.example.com/api") as client:
        result = await client.forms.submit_form(body=...)
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, bodies, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://media.example.com/api` for `forms.submit_form`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://media.example.com/api") as client:
    try:
        result = client.forms.submit_form(body=...)
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

### `client.forms.submit_form`

`POST /forms`, operation reference `/paths/~1forms/post`, request body as `body=` only (`application/x-www-form-urlencoded`)

```python
def submit_form(
    *,
    body: models.FormsPostRequest,
    media_type: Literal['application/x-www-form-urlencoded'] | None = None,
    response_media_type: Literal['application/x-www-form-urlencoded'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitFormResponse: ...
```

### `client.forms.submit_profile`

`POST /profiles`, operation reference `/paths/~1profiles/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_profile(
    *,
    body: models.ProfilesPostRequest,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitProfileResponse: ...
```

### `client.forms.read_profile`

`GET /profiles`, operation reference `/paths/~1profiles/get`

```python
def read_profile(
    *,
    response_media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> ReadProfileResponse: ...
```

### `client.forms.submit_anything`

`POST /anything`, operation reference `/paths/~1anything/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_anything(
    *,
    body: models.AnythingPostRequest,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitAnythingResponse: ...
```

### `client.forms.submit_parts`

`POST /attachments`, operation reference `/paths/~1attachments/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_parts(
    *,
    body: MultipartBody[str] | UNSET = UNSET,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitPartsResponse: ...
```

### `client.forms.read_parts`

`GET /attachments`, operation reference `/paths/~1attachments/get`

```python
def read_parts(
    *,
    response_media_type: Literal['multipart/mixed', 'multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> ReadPartsResponse: ...
```

`resources/forms/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.forms.submit_pairs`

`POST /pairs`, operation reference `/paths/~1pairs/post`, request body as `body=` only (`application/x-www-form-urlencoded`)

```python
def submit_pairs(
    *,
    body: FormData | UNSET = UNSET,
    media_type: Literal['application/x-www-form-urlencoded'] | None = None,
    response_media_type: Literal['application/x-www-form-urlencoded'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitPairsResponse: ...
```

### `client.forms.submit_upload`

`POST /uploads`, operation reference `/paths/~1uploads/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_upload(
    *,
    body: MultipartBody[int | str | list[str] | models.Address],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitUploadResponse: ...
```

### `client.forms.read_upload`

`GET /uploads`, operation reference `/paths/~1uploads/get`

```python
def read_upload(
    *,
    response_media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> ReadUploadResponse: ...
```

### `client.forms.submit_avatar`

`POST /avatars`, operation reference `/paths/~1avatars/post`, request body as `body=` only (`multipart/form-data`, `application/json`)

```python
def submit_avatar(
    *,
    body: MultipartBody[str | JSONValue] | models.Address | UNSET = UNSET,
    media_type: Literal['multipart/form-data', 'application/json'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitAvatarResponse: ...
```

`resources/forms/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.forms.submit_scans`

`POST /scans`, operation reference `/paths/~1scans/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_scans(
    *,
    body: MultipartBody[str] | UNSET = UNSET,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitScansResponse: ...
```

### `client.forms.submit_photos`

`POST /photos`, operation reference `/paths/~1photos/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_photos(
    *,
    body: MultipartBody[Never],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitPhotosResponse: ...
```

### `client.forms.submit_labels`

`POST /labels`, operation reference `/paths/~1labels/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_labels(
    *,
    body: MultipartBody[str],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitLabelsResponse: ...
```

### `client.files.store_file`

`POST /files`, operation reference `/paths/~1files/post`, request body as `body=` only (`application/json`, `application/*`, `image/*`, `text/*`)

```python
def store_file(
    *,
    body: models.Address | SyncBinaryBody | models.FilesPostRequest2,
    media_type: str | None = None,
    response_media_type: str | None = None,
    options: RequestOptions | None = None,
) -> StoreFileResponse: ...
```

`resources/files/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.files.replace_file`

`PUT /files`, operation reference `/paths/~1files/put`, request body as `body=` only (`*/*`)

```python
def replace_file(
    *,
    body: SyncBinaryBody | UNSET = UNSET,
    media_type: str | None = None,
    options: RequestOptions | None = None,
) -> ReplaceFileResponse: ...
```

`resources/files/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.forms.submit_search`

`POST /searches`, operation reference `/paths/~1searches/post`, request body as `body=` only (`application/x-www-form-urlencoded`)

```python
def submit_search(
    *,
    body: models.SearchesPostRequest,
    media_type: Literal['application/x-www-form-urlencoded'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitSearchResponse: ...
```

### `client.forms.submit_cover`

`POST /covers`, operation reference `/paths/~1covers/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_cover(
    *,
    body: MultipartBody[str | int | models.Address | Any | JSONValue],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitCoverResponse: ...
```

### `client.forms.submit_card`

`POST /cards`, operation reference `/paths/~1cards/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_card(
    *,
    body: models.CardsPostRequest,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitCardResponse: ...
```

### `client.forms.submit_stickers`

`POST /stickers`, operation reference `/paths/~1stickers/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_stickers(
    *,
    body: models.StickersPostRequest,
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitStickersResponse: ...
```

### `client.forms.submit_album`

`POST /albums`, operation reference `/paths/~1albums/post`, request body as `body=` only (`multipart/form-data`)

```python
def submit_album(
    *,
    body: MultipartBody[models.Bounds | str | list[str] | JSONValue],
    media_type: Literal['multipart/form-data'] | None = None,
    options: RequestOptions | None = None,
) -> SubmitAlbumResponse: ...
```

### `client.documents.store_document`

`POST /documents`, operation reference `/paths/~1documents/post`, request body as `body=` only (`application/json`, `text/plain; charset=utf-16`, `application/vnd.api+json`)

```python
def store_document(
    *,
    body: JSONValue | str | models.Draft,
    media_type: Literal['application/json', 'text/plain; charset=utf-16', 'application/vnd.api+json'] | None = None,
    response_media_type: Literal['application/json', 'text/plain'] | None = None,
    options: RequestOptions | None = None,
) -> StoreDocumentResponse: ...
```

`resources/documents/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

### `client.documents.read_document`

`GET /documents/{id}`, operation reference `/paths/~1documents~1{id}/get`

```python
def read_document(
    *,
    id: models.DocumentsIdGetPathIdParameter,
    filter: JSONValue | UNSET = UNSET,
    X_Mode: str | UNSET = UNSET,
    response_media_type: Literal['application/vnd.api+json'] | None = None,
    options: RequestOptions | None = None,
) -> ReadDocumentResponse: ...
```

### `client.documents.store_note`

`POST /notes`, operation reference `/paths/~1notes/post`, request body as `body=` only (`application/json`, `application/vnd.note+json`)

```python
def store_note(
    *,
    body: JSONValue,
    media_type: Literal['application/json', 'application/vnd.note+json'],
    options: RequestOptions | None = None,
) -> StoreNoteResponse: ...
```

### `client.documents.replace_note`

`PUT /notes`, operation reference `/paths/~1notes/put`, request body as `body=` only (`application/json`, `text/plain`)

```python
def replace_note(
    *,
    body: JSONValue | str | UNSET = UNSET,
    media_type: Literal['application/json', 'text/plain'] | None = None,
    options: RequestOptions | None = None,
) -> ReplaceNoteResponse: ...
```

`resources/documents/_sync.py` also declares overloads of the method, which narrow its body or its result by the media types a call chooses.

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
    "operation": "forms.submit_form",
    "method": "POST",
    "path": "/forms",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_profile",
    "method": "POST",
    "path": "/profiles",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.read_profile",
    "method": "GET",
    "path": "/profiles",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_anything",
    "method": "POST",
    "path": "/anything",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_parts",
    "method": "POST",
    "path": "/attachments",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.read_parts",
    "method": "GET",
    "path": "/attachments",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_pairs",
    "method": "POST",
    "path": "/pairs",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_upload",
    "method": "POST",
    "path": "/uploads",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.read_upload",
    "method": "GET",
    "path": "/uploads",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_avatar",
    "method": "POST",
    "path": "/avatars",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_scans",
    "method": "POST",
    "path": "/scans",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_photos",
    "method": "POST",
    "path": "/photos",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_labels",
    "method": "POST",
    "path": "/labels",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "files.store_file",
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
    "operation": "files.replace_file",
    "method": "PUT",
    "path": "/files",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_search",
    "method": "POST",
    "path": "/searches",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_cover",
    "method": "POST",
    "path": "/covers",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_card",
    "method": "POST",
    "path": "/cards",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_stickers",
    "method": "POST",
    "path": "/stickers",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "forms.submit_album",
    "method": "POST",
    "path": "/albums",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "documents.store_document",
    "method": "POST",
    "path": "/documents",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "documents.read_document",
    "method": "GET",
    "path": "/documents/{id}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "documents.store_note",
    "method": "POST",
    "path": "/notes",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "documents.replace_note",
    "method": "PUT",
    "path": "/notes",
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
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `multipart_responses.py`, `native.py`, `operations.py`, `options.py`, `parts.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameter_reads.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
