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

- `files.create_file`: `POST /files`, no request body
- `files.probe_file`: `HEAD /files/{fileId}`, no request body
- `files.append_file`: `PATCH /files/{fileId}`, `body=` only (`application/offset+octet-stream`)
- `files.put_file_chunk`: `PUT /files/{fileId}`, `body=` only (`application/octet-stream`)
- `files.get_file_status`: `GET /files/{fileId}/status`, no request body
- `files.complete_file`: `POST /files/{fileId}/complete`, no request body

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
- `pydantic>=2.13.5`

Each generation reports them; add them to your project, for example with `uv add`.

## Capabilities

| Capability | Declared |
| --- | --- |
| Security | none |
| Helpers | `uploads` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `records.py`, `references.py`, `resume.py`, `sources.py`, `uploads.py`, `values.py`, `writes.py`
