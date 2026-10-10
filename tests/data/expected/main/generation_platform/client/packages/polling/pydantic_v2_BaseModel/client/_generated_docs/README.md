# client

This generated package exposes `Client` and `AsyncClient`. Import options, errors, and response types from
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

- `jobs.create_job`: `POST /jobs`, `body=` only (`application/json`)
- `jobs.get_job`: `GET /jobs/{jobId}`, no request body
- `jobs.cancel_job`: `DELETE /jobs/{jobId}`, no request body
- `jobs.get_report`: `GET /jobs/{jobId}/report`, no request body
- `reports.find_report`: `POST /reports`, `body=` only (`application/json`)
- `reports.latest_report`: `GET /reports/latest`, no request body
- `exports.start_export`: `POST /exports`, no request body
- `exports.cancel_exports`: `DELETE /exports`, no request body
- `exports.export_status`: `GET /exports/status`, no request body
- `exports.find_export`: `GET /exports/find`, no request body
- `reports.add_note`: `POST /notes`, `body=` only (`text/plain`)

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
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "jobs.get_job",
    "method": "GET",
    "path": "/jobs/{jobId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "jobs.cancel_job",
    "method": "DELETE",
    "path": "/jobs/{jobId}",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "jobs.get_report",
    "method": "GET",
    "path": "/jobs/{jobId}/report",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "reports.find_report",
    "method": "POST",
    "path": "/reports",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "reports.latest_report",
    "method": "GET",
    "path": "/reports/latest",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "exports.start_export",
    "method": "POST",
    "path": "/exports",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "exports.cancel_exports",
    "method": "DELETE",
    "path": "/exports",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "exports.export_status",
    "method": "GET",
    "path": "/exports/status",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "exports.find_export",
    "method": "GET",
    "path": "/exports/find",
    "retry_safety": "method_default",
    "idempotency": null,
    "retry_after_ms_header": null,
    "should_retry_header": null,
    "security": null,
    "auth_challenge_less_401": false
  },
  {
    "operation": "reports.add_note",
    "method": "POST",
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

## Protocol helpers

`client.protocols` holds the protocol helpers below by their dotted names, on `Client` and `AsyncClient` alike.
A polling helper's `start` creates the operation and returns a handle: `status` polls it once and `wait` polls until
it settles and returns its result, each poll after the wait the last response requires; `close()` or `aclose()`
stops only local polling. `resume` returns a handle continuing a handle's `checkpoint()` without creating the
operation again, and a helper that declares a remote cancellation returns a handle whose `cancel_remote` sends it. See
the runtime reference for their limits and checkpoints.

```json
[
  {
    "helper": "jobs.run",
    "kind": "polling",
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs"
  },
  {
    "helper": "jobs.inline",
    "kind": "polling",
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs"
  },
  {
    "helper": "jobs.report",
    "kind": "polling",
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs"
  },
  {
    "helper": "exports.run",
    "kind": "polling",
    "operation": "exports.start_export",
    "method": "POST",
    "path": "/exports"
  },
  {
    "helper": "exports.latest",
    "kind": "polling",
    "operation": "exports.start_export",
    "method": "POST",
    "path": "/exports"
  },
  {
    "helper": "jobs.tracked",
    "kind": "polling",
    "operation": "jobs.create_job",
    "method": "POST",
    "path": "/jobs"
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
| Helpers | `polling` |

The package copies the runtime modules its capabilities and model backend need:

- `_runtime`: `__init__.py`
- `_runtime/client`: `__init__.py`, `client.py`, `codecs.py`, `content.py`, `errors.py`, `logical.py`, `media.py`, `native.py`, `operations.py`, `options.py`, `paths.py`, `positions.py`, `raw.py`, `responses.py`, `retry.py`, `security.py`, `timing.py`, `urls.py`
- `_runtime/model_codecs`: `__init__.py`, `errors.py`, `media.py`, `native.py`, `parameter_reads.py`, `parameters.py`, `unset.py`
- `_runtime/protocols`: `__init__.py`, `caches.py`, `client.py`, `errors.py`, `links.py`, `options.py`, `origins.py`, `pagination.py`, `polling.py`, `records.py`, `references.py`, `resume.py`, `values.py`, `writes.py`
