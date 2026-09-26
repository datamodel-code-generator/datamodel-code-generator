# Implementation handoff

This guide identifies the design contracts and acceptance requirements for implementation. At publication, only planning documents exist; product implementation has not started. Work only on the stack named in the implementation request and protect existing working trees. All repository content, issues, pull requests, and published implementation records must be in English.

## 1. Authority and reading order

Read [PLAN](PLAN.md) and [ARCHITECTURE](ARCHITECTURE.md) first. Then identify the boundaries of all eight contracts below, regardless of the assigned stack. Read the complete contracts relevant to that stack and any sections they depend on. [COVERAGE](COVERAGE.md) maps requirement IDs; [FASTAPI-REQUIREMENTS](FASTAPI-REQUIREMENTS.md) inventories legacy workflows; [CLIENT-REQUIREMENTS](CLIENT-REQUIREMENTS.md) records client capabilities.

| Contract | Authoritative responsibility |
| --- | --- |
| [ENTRY](DECISIONS-ENTRY.md) | Distribution, new APIs/CLI, configuration, model generate/verify, single-target artifact ownership, publication/rollback, dependencies |
| [BINDING](DECISIONS-BINDING.md) | Existing phases/order, optional integration points, new api scope, final operation/field/type binding, accepted attempts, disposal |
| [MODEL-CODECS](DECISIONS-MODEL-CODECS.md) | Five backends, wire validation, presence, direction, native/envelope handling, aliases, extension adapters |
| [FASTAPI](DECISIONS-FASTAPI.md) | All 13 workflows, routes/service Protocols, sync/async, router updates, forms/uploads, security, served OpenAPI, templates/hooks |
| [CLIENT](DECISIONS-CLIENT.md) | Generation configuration, resources/methods/arguments, statuses/media, file trees, extensions, packaging/docs, diagnostics |
| [RUNTIME](DECISIONS-RUNTIME.md) | Public clients/options/responses/errors, HTTPX2, timeout/retry, authentication, ownership/close, hooks |
| [PROTOCOLS](DECISIONS-PROTOCOLS.md) | Paging/polling/streams/WS/webhooks/cache; metadata and state machines for resumable uploads, batches, queues, circuits, compression |
| [TYPING](DECISIONS-TYPING.md) | Concrete generated types, type correlations, dynamic boundaries, Python versions, positive/negative static oracles |

Preserve existing external Python class reuse through `--type-overrides`, `x-python-import`, and other supported mechanisms. Treat external type binding separately from verification of generated model artifacts.

Do not redefine defaults, types, or configuration in multiple layers. ENTRY owns entry points/distribution/artifacts; BINDING owns generation facts; MODEL-CODECS owns conversion; RUNTIME owns call send budgets/resources; PROTOCOLS owns API-specific procedures using those budgets. FASTAPI/CLIENT compose them into generated public surfaces. If a summary and a detailed contract disagree, correct and review the plan rather than silently choosing an interpretation.

## 2. Requirements that must not change

1. Maintain the legacy FastAPI generator in its existing repository/package/implementation with normal updates for current dcg APIs. Add deprecation guidance to documentation and CLI help only when the replacement is available. Do not add a legacy/new selector, separate-environment launcher, or dcg-to-legacy dependency.
2. The new server/client generators share dcg's distribution/version. Initially implement Pydantic v2 BaseModel/dataclass for the server and all five current backends for the client. Preserve all existing dcg backend support; do not add Pydantic v1 to the new target support matrix. The server rejects unsupported backend selections without conversion. One run selects one target; no combined-generation CLI/API.
3. Preserve existing dcg bytes/tree/order, public and de facto usage, exceptions/warnings/side effects, dependencies, and performance under identical conditions. Optional APIs, explicit scope additions, and internal integration points are permitted. Do not add runtime dependencies or always-on capture to existing usage.
4. New `--openapi-scopes api` is explicitly selected. Ordinary dcg with the same version and effective model settings is the oracle for server/client model artifacts. Targets never silently change scope, names, variants, or reuse/collapse settings.
5. New server output need not match legacy bytes. Carry forward its 13 practical capabilities through the new design and verify wire behavior, generated APIs, and handwritten-code protection independently.
6. Include C01–C33 in the initial client contract. Conditional API capabilities activate only with explicit metadata, but reporting a required unimplemented feature as unsupported does not count as completing it.
7. HTTPX2 is the first built-in transport with sync and native asyncio. Trio, other transports, and other languages have no initial guarantee. Embed private runtime code in generated packages; they must not depend on dcg or the legacy generator at runtime.
8. Do not mix in compatibility-changing bug fixes, hide differences by updating goldens, repair private graph state externally, replace type strings, or suppress failures through fallback.
9. Prefer official standard FastAPI patterns. Select standard parameter/body/response projections and necessary codec adapters at generation time. Pydantic v2 still has nullable/direction/style differences. Do not turn every route into Request/Response handling or generate custom multipart parsers, DI containers, or cancellation tasks. Standard FastAPI behavior and strict client/adapter wire rules are different guarantees.

## 3. Implementation dependency order

[PR-STACKS](PR-STACKS.md) defines concrete PR boundaries and restart records. S01, containing three PRs, is the first independent implementation scope. PR counts describe review units, not effort or delivery estimates.

1. **Establish comparisons with existing dcg.** Fix current main and align dependencies/settings/time conditions for complete byte, API, side-effect, and performance comparisons. Use existing checkers/benchmarks and G01–12 in [ACCEPTANCE](ACCEPTANCE.md).
2. **Shared facts and type binding.** Implement BINDING's bounded integration points, explicit scope, field lineage, accepted-attempt handling, freeze, and disposal. Do not capture on model-only paths. Meet B oracles and G10 through a client dry consumer; working FastAPI examples do not justify postponing missing client facts.
3. **Shared codecs and the two-backend FastAPI target.** Meet common and Pydantic-specific MC oracles, F01–13, and FA oracles. Independently verify standard integration versus adapters, arguments, responses, DI, and served docs; do not apply shared codecs twice on all routes. Regenerate from two successive specifications while the same business implementations stay in user modules. Reject the other three backends at the new server entry point. Align legacy migration guidance with actual published entry points and feature coverage.
4. **Five-backend client.** Implement C01–33 using the same facts and authoritative RUNTIME/PROTOCOLS contracts. Complete standard dataclass/TypedDict/msgspec converters and their MC acceptance in the client stage. Pass RT/P/CG oracles, X01–17, and five-backend × sync/asyncio × dependency/OS matrices. Generated server/client interoperability alone is insufficient.
5. **Distribution and operation.** Implement mutually exclusive server/client choices on the existing CLI, model-preserving independent verification, single-target advisory locks, and manifests exactly as ENTRY specifies. Do not add cross-target registries, retirement APIs, or multi-target execution. Verify embedded/standalone layouts, real wheels/sdists, generated typing/docs/examples, manifest ownership/rollback, and runtime updates through regeneration. A release with incomplete mandatory requirements is not complete.

Do not charge client implementation again for shared semantic facts, wire codecs, or ownership infrastructure already implemented for the server. HTTP execution, retry/auth/paging/stream policy, and runtime distribution remain separate continuing costs. [RATIONALE](RATIONALE.md) explains the rationale.

## 4. Distinguish selected design from unexecuted tests

The authoritative contracts select API names, signatures, defaults, scope, ownership/lifetime, capabilities, diagnostics, and compatibility/performance decision procedures. They do not delegate those choices to implementation. If a demonstrated counterexample requires a change, record the evidence and update the affected contract and independent oracle. Do not relax existing dcg compatibility to force a pass.

The new generators/runtimes are unimplemented; complete binding/wire/performance/installation tests have not run. That is outstanding implementation evidence, not an open design choice. No-findings reviews neither establish passing implementation tests nor guarantee no future defect. Record implementation commits, plan revisions, and actual review scope; do not reuse an older review result as evidence for changed content.

Baselines are dcg `4f96e22ea403a66faae96f3949d41dd61fc1186f` and legacy FastAPI `b3b9bd66e0b0c4d8c686fda700258104615a1332`. Earlier dcg observations at `3452dfb` remain historical; source links retain the commit actually inspected. See [REFERENCES](REFERENCES.md).

## 5. Superseded alternatives

The selected contracts supersede earlier alternatives. The following are not implementation requirements:

- A legacy backend selector, separate-environment delegation, permanent old-dcg pin, or `fastapi-code-generator-next`.
- Mandatory Protocol inheritance, async-only handlers, a fixed seven-file output, JSON-only/flat-type coverage, or no-reuse/single-module restrictions presented as a complete replacement.
- A compatibility shim transparently reproducing arbitrary live visitor/private graph mutation.
- Pydantic-only client support, postponed client runtime design, or five-backend server support. The server has the two selected Pydantic v2 backends.
- response_model=None on every route or custom multipart readers/MultipartRoute/cancellation management for all uploads. Do not claim discarded guarantees are equivalently provided by standard FastAPI.
- Treating historical no-findings reviews as proof of the current design or implementation.

Do not start implementation by mechanically renaming packages in an old proposal. This directory's current contracts govern.

## 6. CLI, typing, and regeneration boundaries

`--generate-server fastapi` and `--generate-client httpx` are mutually exclusive options on existing datamodel-codegen. The `api` scope value controls model collection, not target selection. Target selection must not silently change existing model settings. Include the cost of added argument definitions themselves in existing-path performance checks.

Keep business logic in user modules during server regeneration. Type-check the actual implementations against the regenerated service Protocols. Independently test new required arguments, type changes, sync/async changes, operation deletion, and explicit renaming; generated `.pyi` files must not conceal implementation checking. Pass `<Group>Service[Principal]` implementations and the authorizer to the same builder for static checking. Do not infer handwritten types or treat startup shape checks as proof of static compatibility. Apply all relevant TY oracles to new generated artifacts.
