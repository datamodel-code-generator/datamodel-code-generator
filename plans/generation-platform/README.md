# Generation platform plan

Status: **planned; product implementation has not started**. Published 2026-09-19.

Tracking issue: [#4097 — FastAPI server and OpenAPI client generation](https://github.com/datamodel-code-generator/datamodel-code-generator/issues/4097).

This directory contains the implementation contracts and acceptance criteria for optional generation targets in datamodel-code-generator. All published plans, prompts, issues, and pull requests are written in English. These are design requirements, not documentation of released APIs.

The order is shared generation infrastructure, FastAPI server generation, then OpenAPI client generation. FastAPI initially supports Pydantic v2 BaseModel and Pydantic v2 dataclass; the client supports all five current model backends. A run selects one target. OpenAPI is the first input; later inputs retain their own operation and protocol semantics.

Existing dcg output bytes, ordering, CLI/Python API and extension behavior, dependencies, and performance remain compatibility requirements. Existing Python type reuse, including `--type-overrides` and `x-python-import`, must carry through to final type binding and applicable codecs. New targets must not silently change model settings or regenerate external classes.

The existing fastapi-code-generator keeps its implementation during the transition. Deprecation guidance follows an available replacement. The new dcg implementation has no dependency on fastapi-code-generator. New server output need not match the legacy generator; business logic stays in user modules that implement generated service Protocols, and regeneration never touches it.

## Reading map

| Document | Purpose |
| --- | --- |
| [PLAN](PLAN.md) | Scope, sequence, compatibility and product choices |
| [ARCHITECTURE](ARCHITECTURE.md) | Responsibilities, ownership and lifetime |
| [IMPLEMENTER](IMPLEMENTER.md) | How to implement one bounded portion of the plan |
| [PR-STACKS](PR-STACKS.md) | Functional PR stacks, dependencies and completion gates |
| [IMPLEMENTATION-PROMPT](IMPLEMENTATION-PROMPT.md) | A prompt for an implementation agent |
| [ACCEPTANCE](ACCEPTANCE.md) | Independent compatibility, behavior and performance oracles |
| [BACKGROUND](BACKGROUND.md), [RATIONALE](RATIONALE.md) | Earlier requests, design rationale, maintenance and later inputs |
| [COVERAGE](COVERAGE.md), [CLIENT-REQUIREMENTS](CLIENT-REQUIREMENTS.md), [FASTAPI-REQUIREMENTS](FASTAPI-REQUIREMENTS.md) | Requirement-to-contract coverage |
| [REFERENCES](REFERENCES.md) | Pinned source baselines and verification status |

The eight detailed contracts are authoritative within their responsibilities:

| Contract | Responsibility |
| --- | --- |
| [ENTRY](DECISIONS-ENTRY.md) | Distribution, optional API/CLI, configuration, ownership and publication |
| [BINDING](DECISIONS-BINDING.md) | Actual model generation order, scope, type/field binding and accepted attempts |
| [MODEL-CODECS](DECISIONS-MODEL-CODECS.md) | Backend integration, wire conversion, presence and custom adapters |
| [FASTAPI](DECISIONS-FASTAPI.md) | Routes, dependency injection, service Protocols, regeneration and extensions |
| [CLIENT](DECISIONS-CLIENT.md) | Generated client structure, methods, configuration and extensions |
| [RUNTIME](DECISIONS-RUNTIME.md) | HTTP execution, retry, authentication, deadlines and resource ownership |
| [PROTOCOLS](DECISIONS-PROTOCOLS.md) | Explicit API-specific pagination, streaming and other protocol helpers |
| [TYPING](DECISIONS-TYPING.md) | Concrete public and internal types and static acceptance cases |

Read the map for every stack, then the complete contracts relevant to that stack and their stated dependencies. Do not ask an implementation agent to infer defaults or substitute a smaller feature set. If implementation reveals a counterexample, update the affected contract and independent acceptance case in the same reviewed change.

Implementation baseline: dcg `4f96e22ea403a66faae96f3949d41dd61fc1186f`; legacy fastapi-code-generator `b3b9bd66e0b0c4d8c686fda700258104615a1332`. Recheck the current main and applicable repository instructions when implementation begins. Historical source links retain their original commits.

The public plan contains dcg requirements, design contracts, and acceptance criteria, with links to relevant source, dependency documentation, and standards. Product acceptance requires implementation tests; source inspection alone does not establish compatibility or performance.

All implementation stacks are currently **not started**. The documentation PR is not S01 or product implementation. Update stack status and link PRs as work completes; passing CI on a documentation change is not an implementation result.
