# client

This generated package exposes `Client` and `AsyncClient`. Import options, bodies, errors, and response types from
its public modules. Operations are grouped into the resource attributes listed below; `request_raw` accepts an
explicit URL. Close clients with `with` or `async with`, and retain streaming responses only inside their context.

```python
from client import Client

with Client(max_retries=0) as client:
    response = client.request_raw("GET", "https://api.example.com/health")
    status = response.info.status_code
```

Replace the example URL with your service. The explicit `max_retries=0` disables resends for this example.
The default is two retries, subject to operation safety, replayable input, delay, and the shared deadline.
The default status set is 408, 429, 500, 502, 503, and 504. Server retry delays are respected by default.
`retry=RetryOptions(respect_retry_after=False)` is an explicit application override that ignores those server hints;
give it deliberately to the client, a view, or a call. This package does not embed that override.

See the [runtime reference](runtime.md) for defaults, ownership, cancellation, replay, redirects, and transport costs.

## Profile

| Setting | Value |
| --- | --- |
| `signature_style` | `explicit` |
| `body_arguments` | `body` |
| Model backend | `pydantic_v2.BaseModel` |
| Models | `models` |

Each operation method declares its parameters, its body, and its options as keyword arguments.
A request body is given as `body=`, unless an operation's `body_arguments` is `both`.

## Operations

- `forms.submit_form`: `POST /forms`, `body=` only (`application/x-www-form-urlencoded`)
- `forms.submit_profile`: `POST /profiles`, `body=` only (`multipart/form-data`)
- `forms.read_profile`: `GET /profiles`, no request body
- `forms.submit_anything`: `POST /anything`, `body=` only (`multipart/form-data`)
- `forms.submit_parts`: `POST /attachments`, `body=` only (`multipart/form-data`)
- `forms.read_parts`: `GET /attachments`, no request body
- `forms.submit_pairs`: `POST /pairs`, `body=` only (`application/x-www-form-urlencoded`)
- `forms.submit_upload`: `POST /uploads`, `body=` only (`multipart/form-data`)
- `forms.read_upload`: `GET /uploads`, no request body
- `forms.submit_avatar`: `POST /avatars`, `body=` only (`multipart/form-data`, `application/json`)
- `forms.submit_scans`: `POST /scans`, `body=` only (`multipart/form-data`)
- `forms.submit_photos`: `POST /photos`, `body=` only (`multipart/form-data`)
- `forms.submit_labels`: `POST /labels`, `body=` only (`multipart/form-data`)
- `files.store_file`: `POST /files`, `body=` only (`application/json`, `application/*`, `image/*`, `text/*`)
- `files.replace_file`: `PUT /files`, `body=` only (`*/*`)
- `forms.submit_search`: `POST /searches`, `body=` only (`application/x-www-form-urlencoded`)
- `forms.submit_cover`: `POST /covers`, `body=` only (`multipart/form-data`)
- `forms.submit_card`: `POST /cards`, `body=` only (`multipart/form-data`)
- `forms.submit_stickers`: `POST /stickers`, `body=` only (`multipart/form-data`)
- `forms.submit_album`: `POST /albums`, `body=` only (`multipart/form-data`)
- `documents.store_document`: `POST /documents`, `body=` only (`application/json`, `text/plain; charset=utf-16`, `application/vnd.api+json`)
- `documents.read_document`: `GET /documents/{id}`, no request body
- `documents.store_note`: `POST /notes`, `body=` only (`application/json`, `application/vnd.note+json`)
- `documents.replace_note`: `PUT /notes`, `body=` only (`application/json`, `text/plain`)

## Selected operation contracts

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

## Dependencies

The package needs these distributions at run time:

- `httpx2>=2.13.0`
- `typing-extensions>=4.16`
- `pydantic>=2.13.5`

Each generation reports them; add them to your project, for example with `uv add`.

## Capabilities

| Capability | Declared |
| --- | --- |
| Security | none |
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `multipart_responses.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
