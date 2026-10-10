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
| `body_arguments` | `both` |
| Model backend | `pydantic_v2.BaseModel` |
| Models | `models` |

Each operation method declares its parameters, its body, and its options as keyword arguments.
A request body whose media is JSON or a URL-encoded form of an object model is given as `body=` or as the keyword
arguments of its fields, unless an operation's `body_arguments` is `body`.

## Operations

- `default.create_pet`: `POST /pets`, body arguments `both`
  - `application/json`: `body=` or the fields `name` (required), `kind` (required), `pet_tag`, `birthDate`, `owner`, `secret`
  - `application/x-www-form-urlencoded`: `body=` or the fields `name` (required), `pet_tag`
- `default.update_pet`: `PATCH /pets/{petId}`, body arguments `both`
  - `application/json`: `body=` or the fields `name`, `tag`
- `default.log_visit`: `POST /pets/{petId}/visits`, body arguments `both`
  - `application/json`: `body=` or the fields `note`, `visit_options`
  - `text/plain`: `body=` only, since only JSON and URL-encoded form bodies have field arguments
- `default.set_owner`: `PUT /pets/{petId}/owner`, `body=` only (`application/json`)
- `default.create_owner`: `POST /owners`, body arguments `both`
  - `application/json`: `body=` or the fields `email` (required), `nickName`
- `default.put_labels`: `PUT /pets/{petId}/labels`, body arguments `both`
  - `application/json`: `body=` or the fields `size`
- `default.put_photo`: `PUT /pets/{petId}/photo`, body arguments `both`
  - `multipart/form-data`: `body=` only, since only JSON and URL-encoded form bodies have field arguments
- `default.replace_pet`: `PUT /pets/{petId}/records`, body arguments `both`
  - `application/json`: `body=` or the fields `name` (required), `tag`

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
    "operation": "default.create_pet",
    "method": "POST",
    "path": "/pets",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.update_pet",
    "method": "PATCH",
    "path": "/pets/{petId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.log_visit",
    "method": "POST",
    "path": "/pets/{petId}/visits",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.set_owner",
    "method": "PUT",
    "path": "/pets/{petId}/owner",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.create_owner",
    "method": "POST",
    "path": "/owners",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.put_labels",
    "method": "PUT",
    "path": "/pets/{petId}/labels",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.put_photo",
    "method": "PUT",
    "path": "/pets/{petId}/photo",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "default.replace_pet",
    "method": "PUT",
    "path": "/pets/{petId}/records",
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
- `_runtime/client`: `__init__.py`, `bodies.py`, `body_sources.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameters.py`, `unset.py`
