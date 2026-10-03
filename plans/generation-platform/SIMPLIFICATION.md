# Simplification of the generation platform

Decided 2026-10-02. **This document overrides any conflicting text in the other documents of this plan.** Sections
that it replaces carry a one-line "Superseded by" note; their text stays for history.

The server and client targets were implemented through S13 on main. They are unreleased and experimental, so
everything below can change without deprecation. A review against established generators and SDKs found that the
plan made the targets own work that generated code does not normally own. Comparisons included:
- Stainless (openai/anthropic), Speakeasy, Fern, Kiota and OpenAPI Generator;
- the Azure, AWS and Google SDKs;
- httpx, FastAPI and fastapi-code-generator.

Examples of that work:
- a second validator beside the models;
- application infrastructure inside the SDK;
- generator machinery that reconstructs facts the generator already holds.

This document keeps the targets' goals and removes that work.

## 1. Principles

### P1. Models are the runtime contract
OpenAPI is the source. dcg generates the models from it, and the generated server and client then simply use those
models. The OpenAPI document is used at generation time only, to decide:
- operation-to-model bindings;
- wire names;
- parameter locations, styles and explode;
- media types;
- status codes;
- security schemes.

At runtime, the targets validate, construct and serialize exactly as the selected backend does with the generated
model:

| Backend | Encode | Decode |
| --- | --- | --- |
| Pydantic v2 BaseModel | `model_dump_json(by_alias=True, exclude_unset=True)`; a cached `TypeAdapter` for non-model types | cached `TypeAdapter(T).validate_json` |
| Pydantic v2 dataclass | cached `TypeAdapter(T).dump_json(by_alias=True)` | cached `TypeAdapter(T).validate_json` |
| msgspec.Struct | `msgspec.json.encode` | cached `msgspec.json.Decoder(T)` |
| dataclasses / TypedDict | rename keys from a generated wire-name map, convert JSON-incompatible leaves, omit absent keys / `None` defaults | `json.loads`, then rename keys and build nested values; no validation, because the backend has none |

- There is no second validator: no JSON Schema validation, no ECMA/RE2 pattern engine, no wire IR, no presence
  envelopes, no per-operation codec facades and no structural re-validation.
- How strictly the model represents the schema (nullability, optional fields and the like) is a matter of dcg
  model-generation options. It is independent of the targets, and the targets never compensate for it.
- Data that violates the specification, such as an unknown enum value, is a conformance problem of the peer, not a
  codegen concern. Decoding fails exactly as the model does.
- Unchanged: the same model settings produce byte-identical models, and the client supports all five backends.

### P2. The client is an API client
The generated client has:
- no persistence;
- no background threads or executors of its own;
- no job queues;
- no cross-process state or locks;
- no circuit breakers;
- no client-side authorization decisions.

A capability is kept only where established SDKs have a precedent for it.

### P3. The server is plain FastAPI
- Routes take the models directly (`body: Model`, `response_model=Model`, Annotated parameters).
- Adapters remain only for what FastAPI cannot express: deepObject, label and matrix parameters, form-style cookie or
  header objects and arrays, and non-JSON media FastAPI cannot parse.
- The served OpenAPI document is FastAPI's own. Adapter routes contribute through `openapi_extra` and `responses`.

### P4. Binding records replacements; it does not reverse-engineer
- **Why some tracking is needed.** After parsing, dcg rewrites the users of a reused or collapsed model only when
  models own them. Operation type uses are owned by no model, so they do not follow model reuse/dedup or root-model
  collapse by themselves.
- **Where it is recorded.** A target-only subclass of the existing generation store records each replacement (old
  reference → new reference or inner type) at the store's mutation methods. One map per generation attempt; only the
  accepted attempt's map is used. The ordinary path never constructs it.
- **What follows automatically.** Renames and moves update the shared `Reference` in place.
- **Module and existence.** The final module of each class, and whether it exists, come from a record of each emitted
  module and its models, taken where dcg writes module output. Recomputing module grouping afterwards is wrong for
  tree-scope shared modules, single-module splits, relocated cycles and reuse-created classes.
- **Operation uses.** The Api-scope parser records each operation-located schema use (schemas and item schemas) where
  it acquires it.
- **Verification.** These five recording points (two in the store, three in the parser) reproduced the previous
  capture on every operation type use across the reuse, collapse, module-split, naming, output-type and
  read/write-variant option matrix.
- **The post-pass.** After the accepted attempt, a post-pass resolves each operation type use through the map. Field
  bindings (wire name, attribute, required, has_default) come from the final model fields.
- **Target imports.** Target modules build their own imports.
- **Unresolvable uses.** A type use the map cannot resolve fails target generation with the operation and schema
  pointer. It never produces a silently wrong name.
- **Follow the models.** Where dcg's models represent a schema only through its `$ref` target (a circular `$ref` with
  sibling keywords) or through a placeholder (a dangling `$ref`), the binding follows the models.
- **Custom templates and formatters** are trusted to keep class and field names.
- **Scope.** Targets always enable the `api` model scope. Model output equals ordinary generation with that scope.
- **Not used.** Parser-method capture wrappers, ledgers and origin indexes, tokenizing of generated source, frozen
  backend facts and legacy-scope capture.

### P5. The runtime is proportional
A generated package copies only the runtime modules for the capabilities it declares: its security schemes, its
configured helpers and its backend.

### P6. Speed is measured, not decreed
Fast paths and lazy imports remain goals. Regressions are gated by the existing CodSpeed benchmarks with their
thresholds, and by checks that ordinary model generation loads no target modules. There are no call-count spies and
no zero-tolerance rule.

### P7. Publish the core first
The core client is published first:
- resources and methods, with the argument forms;
- API key, basic, bearer and OAuth client-credential/refresh-token authentication;
- retries, timeouts, redirects and errors;
- the declared helpers.

Further helpers are optional and come later. Acceptance matrices are targeted (§4, R8).

### P8. The foundation stays at the released baseline
- Files that exist in release 0.83.0 end up identical to 0.83.0, except for changes that close independent issues
  (bug fixes, and features unrelated to the targets such as Python API project-configuration loading), the
  typing-tool update and the minimal CLI wiring for the experimental target flags.
- Verification is done against the `0.83.0` tag:
  - the source diff of those files contains only the listed changes;
  - the model e2e expected outputs are unchanged except for the listed fixes, and the model e2e suite passes;
  - ordinary model generation imports no target module.

## 2. Keep, simplify, remove

**Kept:**
- retries with backoff, Retry-After, `x-should-retry` and `retry-after-ms`;
- idempotency keys: one per logical call, reused across retries; metadata is only the header name;
- a total timeout, passed to each attempt as the remaining time;
- redirects off by default, with credential stripping;
- rewinding seekable bodies;
- `with_options` and raw/streaming views;
- the injectable Clock;
- the client argument forms: `body_arguments` including `both`, and explicit or `Unpack` signatures;
- pagination (cursor, offset, Link, next URL) with cycle detection, resumed from a small continuation token;
- LRO polling with Retry-After, resume and remote cancel;
- SSE/NDJSON with Last-Event-ID reconnect;
- basic typed WebSocket sessions (auth, ping, close);
- webhook signature verification through presets (Standard Webhooks, Stripe-style, body HMAC), public keys and
  adapters;
- conditional requests (ETag/If-None-Match/304) over a simple get/set/delete store;
- resumable uploads: sequential, resumed from the session URL and the server offset;
- gzip request compression for operations that declare it;
- one FastAPI service Protocol per tag with a typed builder;
- hooks and templates;
- staged publication with rollback and the state-change recheck;
- `--check`.

**Removed:**
- **Helpers:**
  - offline/replay queues, with their queue and blob stores;
  - the batch helper (batch endpoints are ordinary methods);
  - circuit breakers and their stores;
  - cache tags, mutation wrappers, compare-and-swap and keyed fingerprints;
  - WebSocket resume, acknowledgements, reauthentication and SOCKS;
  - the webhook replay store and the generic signed-parts framing language.
- **Authentication:**
  - the resident OAuth refresh executor, its limits and receipts;
  - the compare-and-swap token persistence;
  - the device and authorization-code flows (out of the initial scope);
  - the client-side scope preflight.
- **Codecs and validation (P1):**
  - the validation axes and their runtime overrides, and Pydantic argument validation;
  - JSON Schema validation and its dependencies;
  - the ECMA→RE2 pattern translator;
  - the wire IR on model paths, the envelopes and the per-operation codec facades;
  - the codec adapter plugin system and its compatibility declarations;
  - the structural re-validation and the none-default provenance.
  - Empty containers in style/form parameters are omitted, not errors.
- **FastAPI (P3):**
  - strict-wire adapters for model/schema mismatches;
  - served-OpenAPI rewriting;
  - `update_groups` and the per-operation fingerprints;
  - per-operation response facades (handlers return a model, an `HTTPResult` or a `Response`);
  - the custom authentication framework (one dependency per scheme plus an authorizer callback);
  - the closed `FastAPIOptions` (`create_app` forwards its keyword arguments to `FastAPI`).
- **Binding (P4):** the capture wrappers, ledgers, origin index, frames, source tokenizing, frozen backend facts,
  legacy-scope capture and `BND_MODEL_SCOPE_REQUIRED` for targets.
- **Entry:**
  - OS advisory locks on output;
  - the duplicate model inventory and the write-only manifest sections. The manifest keeps the format, the
    generator version, the target, the model output hash and the owned-file hashes;
  - `api-diff.json`, the public-contract snapshots and the contract digests;
  - template taint tracking;
  - generation plan hooks.
- **Runtime:**
  - delivery evidence from transport traces and errno graphs. Classify by exception type instead: connect/pool
    failures are not sent, everything else may have been sent and is never resent automatically;
  - the cancellation token, the guard and the stop-reason precedence;
  - the transport-adapter layer (`httpx2` clients are accepted directly);
  - the draining close, closable views and cleanup errors;
  - send budgets and per-result counters. About 12–15 exception classes replace the per-operation classes;
  - the default response-size cap. `trust_env` defaults to true;
  - the default total-timeout and item caps for pagination and uploads.

## 3. Superseded sections

| Section | Replacement |
| --- | --- |
| PLAN §4, RATIONALE §1–§2, COVERAGE §1–§2, CLIENT-REQUIREMENTS §1–§3 | P1, P2, P7 and §2 |
| ARCHITECTURE §6 | §2 Entry (no output locks) |
| ACCEPTANCE §3, §4; TYPING §9 (performance) | P6, P7 and §4 R8 |
| ENTRY §4, §4.1, §5 (locks), §5.1, §6, §7 | P5, P8 and §2 Entry |
| BINDING §1, §3, §5.1, §5.4 (names and resolver paths), §6, §7, §7.1–§7.3, §9, §10, §11, §12 | P4 and P6 |
| MODEL-CODECS §1 (dependencies), §3, §4, §5, §6, §8, §9, §10 (empty containers), §11, §12 | P1 and P3 |
| FASTAPI §2.1, §4 (create_app options), §5, §6, §6.0, §7, §8, §9.1, §9.3, §10, §10.1, §11.1; FASTAPI-REQUIREMENTS §2–§3; TYPING §5.1 | P3 and §2 FastAPI |
| CLIENT §3 (public-name drift), §4.5–§4.8, §6, §7.1 (idempotency), §7.2, §8, §9, §9.1, §9.2, §9.3.1, §10, §11 (plan hooks) | P1, P5 and §2 |
| RUNTIME §1, §2, §4, §5, §7, §8, §9, §13 | §2 Runtime and P5 |
| PROTOCOLS §1.2, §2, §3 (defaults, resume), §6, §7, §7.1, §8, §9, §10, §11, §12, §12.1, §12.2 | P2 and §2 Helpers |

## 4. Implementation stacks
Each stack follows the existing stack workflow, and each verifies P8 and that model output is unchanged.

| Stack | Content |
| --- | --- |
| R0 Foundation baseline | Verification against 0.83.0; revert released-file changes that the targets do not need |
| R1 Defaults | `trust_env`, no default caps, no scope preflight, idempotency metadata |
| R2 Out-of-scope helpers | Remove queues and circuit breakers; webhook presets; simplified cache, WebSocket and compression |
| R3 Native FastAPI | P3: native boundaries, FastAPI's OpenAPI, no partial updates, simple responses, security and `create_app` |
| R4 Backend codecs | P1: backend encode/decode, no schema validation, no adapter plugins, no envelopes or facades |
| R5 Binding | P4: the replacement map and post-pass, validated against the previous capture on every fixture before removing it; released files return to 0.83.0 |
| R6 Publication | No output locks, a slim manifest, no contract digests |
| R7 Runtime | Capability-based runtime copies, inline OAuth refresh, exception-type delivery, simple close, consolidated errors, simple upload and helper resume |
| R8 S14 replan | The core client publication with targeted matrices: backend × sync/async only for codec cases; OS matrix only for file I/O and publication; lowest and highest Python on Linux otherwise; one wheel per layout |

### Parallel work and dependencies

R0, R1, R2, R3, output-lock removal (R6a), and client-contract-digest removal (R6c) proceed in parallel, alongside
the R5 binding design. R4 and R5 implementation start once that design is written. Manifest slimming (R6b) waits
for partial-group-update removal (R3c) to merge. R7 starts after R1 and R2 merge.

Each feature owns its dedicated supporting files, functions and hunks, including its generators, references,
tests, fixtures, documentation and registrations. Shared code waits for the preceding change to merge, then
incorporates main. Dead references to a feature's removed symbols may be deleted when that file has no active
changes from another feature. Shared generated reports are regenerated by the changing feature and again after
integrating main; they are never reconciled by hand.

Implementation PRs form one stack rooted on main for batch merging. The living plan PR stays open independently
and is excluded from that implementation stack. Independent review, CI and the model/foundation gates remain
required for every implementation change.
