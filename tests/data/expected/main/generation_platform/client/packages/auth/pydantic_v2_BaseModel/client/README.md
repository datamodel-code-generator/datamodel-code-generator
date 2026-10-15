# client

The `client` package is an HTTPX2 client of the API the OpenAPI document describes.
datamodel-code-generator generated it from the document, together with the `models` models of the `pydantic_v2.BaseModel` backend.
Generation overwrites every file of the package, as it does the models: keep your code outside the package and regenerate the package instead of editing it; see [Regenerating](#regenerating).

HTTPX2 client generation is experimental.
The [runtime reference](_generated_docs/runtime.md) describes the settings, defaults, ownership, cancellation, replay, redirects, and transport costs of the clients.

## Quick start

```python
from client import Client

with Client(base_url="https://api.example.com", bearer="...") as client:
    result = client.auth.inherited_auth()
```

`AsyncClient` has the same resources and methods, and each method is a coroutine:

```python
from client import AsyncClient


async def main() -> None:
    async with AsyncClient(base_url="https://api.example.com", bearer="...") as client:
        result = await client.auth.inherited_auth()
```

Replace each `...` with a value of the type its argument declares; [Operations](#operations) lists the arguments of each method.
A method returns the decoded body of a success response, of the type its signature declares.
Leaving the `with` or `async with` block closes the client, as `close()` or `aclose()` does; keep a streaming response only inside its own context.
Import options, bodies, errors, and response types from the public modules of the package.
`request_raw(method, url)` of a client sends a request outside the operations to an absolute URL and returns its raw response.

`Client` and `AsyncClient` take one keyword argument per security scheme an operation requires: `bearer` (bearer token), `header_key` (API key), `basic` (HTTP Basic), `query_key` (API key), `cookie_key` (API key), `bearer_alias` (bearer token), `oauth` (bearer token), `openid` (bearer token). The example gives those of the first security alternative of `auth.inherited_auth`.
See [Authentication](_generated_docs/runtime.md#authentication) in the runtime reference.

## Base URL and servers

Without `base_url`, a call sends to the first server generation resolved for its operation, with the default of each server variable: `https://api.example.com` for `auth.inherited_auth`.
An operation's servers are the OpenAPI `servers` of the operation, its path item, or the document; one without them takes the `--client-default-base-url` of the generation.
`server=ServerSelection(index=..., variables={...})`, with `ServerSelection` from `client.options`, picks another declared server by position and supplies its variables; `base_url=` replaces the servers with one URL.
`Client`, `AsyncClient`, `with_options`, and `RequestOptions` take both, and the nearest one given wins: a call's over its view's, and a view's over the client's.
Giving both on one of them raises `ConfigurationError`.

## Errors

```python
from client import Client
from client.errors import APIConnectionError, APIStatusError, NotFoundError

with Client(base_url="https://api.example.com", bearer="...") as client:
    try:
        result = client.auth.inherited_auth()
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

### `client.auth.inherited_auth`

`GET /inherited`, operation reference `/paths/~1inherited/get`

```python
def inherited_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> InheritedAuthResponse: ...
```

### `client.auth.anonymous`

`GET /anonymous`, operation reference `/paths/~1anonymous/get`

```python
def anonymous(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> AnonymousResponse: ...
```

### `client.auth.empty_security`

`GET /empty`, operation reference `/paths/~1empty/get`

```python
def empty_security(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> EmptySecurityResponse: ...
```

### `client.auth.optional_auth`

`GET /optional`, operation reference `/paths/~1optional/get`

```python
def optional_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OptionalAuthResponse: ...
```

### `client.auth.optional_token_first`

`GET /optional-token-first`, operation reference `/paths/~1optional-token-first/get`

```python
def optional_token_first(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OptionalTokenFirstResponse: ...
```

### `client.auth.and_auth`

`GET /and`, operation reference `/paths/~1and/get`

```python
def and_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> AndAuthResponse: ...
```

### `client.auth.or_auth`

`GET /or`, operation reference `/paths/~1or/get`

```python
def or_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OrAuthResponse: ...
```

### `client.auth.authorization_or`

`GET /authorization-or`, operation reference `/paths/~1authorization-or/get`

```python
def authorization_or(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> AuthorizationOrResponse: ...
```

### `client.auth.api_key_header`

`GET /api-key/header`, operation reference `/paths/~1api-key~1header/get`

```python
def api_key_header(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> ApiKeyHeaderResponse: ...
```

### `client.auth.api_key_query`

`GET /api-key/query`, operation reference `/paths/~1api-key~1query/get`

```python
def api_key_query(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> ApiKeyQueryResponse: ...
```

### `client.auth.api_key_cookie`

`GET /api-key/cookie`, operation reference `/paths/~1api-key~1cookie/get`

```python
def api_key_cookie(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> ApiKeyCookieResponse: ...
```

### `client.auth.cookie_parameters`

`GET /api-key/cookie-parameters`, operation reference `/paths/~1api-key~1cookie-parameters/get`

```python
def cookie_parameters(
    *,
    theme: str,
    page: int,
    X_Trace: str,
    filter: models.ApiKeyCookieParametersGetQueryFilterParameter | UNSET = UNSET,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> CookieParametersResponse: ...
```

### `client.auth.basic`

`GET /basic`, operation reference `/paths/~1basic/get`

```python
def basic(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> BasicResponse: ...
```

### `client.auth.bearer`

`GET /bearer`, operation reference `/paths/~1bearer/get`

```python
def bearer(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> BearerResponse: ...
```

### `client.auth.alias_auth`

`GET /alias`, operation reference `/paths/~1alias/get`

```python
def alias_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> AliasAuthResponse: ...
```

### `client.auth.oauth_read`

`GET /oauth/read`, operation reference `/paths/~1oauth~1read/get`

```python
def oauth_read(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OauthReadResponse: ...
```

### `client.auth.oauth_scopes`

`GET /oauth/scopes`, operation reference `/paths/~1oauth~1scopes/get`

```python
def oauth_scopes(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OauthScopesResponse: ...
```

### `client.auth.oauth_empty`

`GET /oauth/empty`, operation reference `/paths/~1oauth~1empty/get`

```python
def oauth_empty(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OauthEmptyResponse: ...
```

### `client.auth.openid_read`

`GET /openid/read`, operation reference `/paths/~1openid~1read/get`

```python
def openid_read(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> OpenidReadResponse: ...
```

### `client.auth.challenge_less`

`GET /challenge-less`, operation reference `/paths/~1challenge-less/get`

```python
def challenge_less(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> ChallengeLessResponse: ...
```

### `client.auth.unsafe_auth`

`POST /unsafe`, operation reference `/paths/~1unsafe/post`, request body as `body=` only (`application/octet-stream`)

```python
def unsafe_auth(
    *,
    body: SyncBinaryBody | UNSET = UNSET,
    media_type: Literal['application/octet-stream'] | None = None,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> UnsafeAuthResponse: ...
```

### `client.auth.idempotent_auth`

`POST /idempotent`, operation reference `/paths/~1idempotent/post`, request body as `body=` only (`application/octet-stream`)

```python
def idempotent_auth(
    *,
    body: SyncBinaryBody | UNSET = UNSET,
    media_type: Literal['application/octet-stream'] | None = None,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> IdempotentAuthResponse: ...
```

### `client.auth.never_auth`

`GET /never`, operation reference `/paths/~1never/get`

```python
def never_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> NeverAuthResponse: ...
```

### `client.auth.vendor_auth`

`GET /vendor`, operation reference `/paths/~1vendor/get`

```python
def vendor_auth(
    *,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> VendorAuthResponse: ...
```

### `client.auth.signed_body`

`PUT /signed`, operation reference `/paths/~1signed/put`, request body as `body=` only (`application/octet-stream`)

```python
def signed_body(
    *,
    body: SyncBinaryBody | UNSET = UNSET,
    media_type: Literal['application/octet-stream'] | None = None,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> SignedBodyResponse: ...
```

### `client.auth.signed_multipart`

`POST /signed-multipart`, operation reference `/paths/~1signed-multipart/post`, request body as `body=` only (`multipart/form-data`)

```python
def signed_multipart(
    *,
    body: MultipartBody[str],
    media_type: Literal['multipart/form-data'] | None = None,
    response_media_type: Literal['application/octet-stream'] | None = None,
    options: RequestOptions | None = None,
) -> SignedMultipartResponse: ...
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

### Capabilities

| Capability | Declared |
| --- | --- |
| Security | `api_key`, `basic`, `bearer`, `client_credentials`, `refresh_token` |
| Helpers | none |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `auth.py`, `bodies.py`, `body_sources.py`, `client.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `multipart.py`, `native.py`, `oauth.py`, `operations.py`, `options.py`, `parts.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `parameters.py`, `pydantic_adapters.py`, `pydantic_models.py`, `unset.py`
