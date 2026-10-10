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

- `auth.inherited_auth`: `GET /inherited`, no request body
- `auth.anonymous`: `GET /anonymous`, no request body
- `auth.empty_security`: `GET /empty`, no request body
- `auth.optional_auth`: `GET /optional`, no request body
- `auth.optional_token_first`: `GET /optional-token-first`, no request body
- `auth.and_auth`: `GET /and`, no request body
- `auth.or_auth`: `GET /or`, no request body
- `auth.authorization_or`: `GET /authorization-or`, no request body
- `auth.api_key_header`: `GET /api-key/header`, no request body
- `auth.api_key_query`: `GET /api-key/query`, no request body
- `auth.api_key_cookie`: `GET /api-key/cookie`, no request body
- `auth.cookie_parameters`: `GET /api-key/cookie-parameters`, no request body
- `auth.basic`: `GET /basic`, no request body
- `auth.bearer`: `GET /bearer`, no request body
- `auth.alias_auth`: `GET /alias`, no request body
- `auth.oauth_read`: `GET /oauth/read`, no request body
- `auth.oauth_scopes`: `GET /oauth/scopes`, no request body
- `auth.oauth_empty`: `GET /oauth/empty`, no request body
- `auth.openid_read`: `GET /openid/read`, no request body
- `auth.challenge_less`: `GET /challenge-less`, no request body
- `auth.unsafe_auth`: `POST /unsafe`, `body=` only (`application/octet-stream`)
- `auth.idempotent_auth`: `POST /idempotent`, `body=` only (`application/octet-stream`)
- `auth.never_auth`: `GET /never`, no request body
- `auth.vendor_auth`: `GET /vendor`, no request body
- `auth.signed_body`: `PUT /signed`, `body=` only (`application/octet-stream`)
- `auth.signed_multipart`: `POST /signed-multipart`, `body=` only (`multipart/form-data`)

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
    "operation": "auth.inherited_auth",
    "method": "GET",
    "path": "/inherited",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.anonymous",
    "method": "GET",
    "path": "/anonymous",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {}
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.empty_security",
    "method": "GET",
    "path": "/empty",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.optional_auth",
    "method": "GET",
    "path": "/optional",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {},
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.optional_token_first",
    "method": "GET",
    "path": "/optional-token-first",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      },
      {}
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.and_auth",
    "method": "GET",
    "path": "/and",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "header_key": [],
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.or_auth",
    "method": "GET",
    "path": "/or",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "header_key": []
      },
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.authorization_or",
    "method": "GET",
    "path": "/authorization-or",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "basic": []
      },
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.api_key_header",
    "method": "GET",
    "path": "/api-key/header",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "header_key": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.api_key_query",
    "method": "GET",
    "path": "/api-key/query",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "query_key": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.api_key_cookie",
    "method": "GET",
    "path": "/api-key/cookie",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "cookie_key": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.cookie_parameters",
    "method": "GET",
    "path": "/api-key/cookie-parameters",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "cookie_key": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.basic",
    "method": "GET",
    "path": "/basic",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "basic": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.bearer",
    "method": "GET",
    "path": "/bearer",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.alias_auth",
    "method": "GET",
    "path": "/alias",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer_alias": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.oauth_read",
    "method": "GET",
    "path": "/oauth/read",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "oauth": [
          "read"
        ]
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.oauth_scopes",
    "method": "GET",
    "path": "/oauth/scopes",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "oauth": [
          "read",
          "write"
        ]
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.oauth_empty",
    "method": "GET",
    "path": "/oauth/empty",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "oauth": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.openid_read",
    "method": "GET",
    "path": "/openid/read",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "openid": [
          "read"
        ]
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.challenge_less",
    "method": "GET",
    "path": "/challenge-less",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": true
  },
  {
    "operation": "auth.unsafe_auth",
    "method": "POST",
    "path": "/unsafe",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.idempotent_auth",
    "method": "POST",
    "path": "/idempotent",
    "retry_safety": "idempotent",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.never_auth",
    "method": "GET",
    "path": "/never",
    "retry_safety": "never",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.vendor_auth",
    "method": "GET",
    "path": "/vendor",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": "X-Retry-In-Ms",
    "should_retry_header": "X-Retry-Permitted",
    "security": [
      {
        "bearer": []
      }
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.signed_body",
    "method": "PUT",
    "path": "/signed",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {}
    ],
    "auth_challenge_less_401": false
  },
  {
    "operation": "auth.signed_multipart",
    "method": "POST",
    "path": "/signed-multipart",
    "retry_safety": "idempotent",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": [
      {}
    ],
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
| Security | `api_key`, `basic`, `bearer`, `client_credentials`, `refresh_token` |
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `bodies.py`, `body_sources.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `native.py`, `oauth.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameters.py`, `unset.py`
