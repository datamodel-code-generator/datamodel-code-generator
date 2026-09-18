# S02 implementation record

Status: implemented; local verification completed as recorded below; remote CI and review pending. Nothing in this stack has been merged. S03 has not started.

## Baseline and review boundaries

Repository: `datamodel-code-generator/datamodel-code-generator`. The authoritative plan is [PR #4098](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4098), revision `8e6f0d4a8a64b71e5a049ad8d108507b2fe164ca`, still separate and unmerged. Main was pulled to `07ffad5c6b316871817f69edf577e0cec617f823` after S01 merged. The intervening changes since the plan's pinned baseline are S01's observation, factory, and attempt-lifetime integration. This stack uses those integration points without implementing final binding or any server/client target.

The isolated implementation checkout is `/private/tmp/dcg-generation-platform-s02`. Baseline and blinded A/A control checkouts are `/private/tmp/dcg-generation-platform-s02-baseline` and `/private/tmp/dcg-generation-platform-s02-control`, both at the fixed main revision. The original checkout's untracked `1.tmp` was preserved. No subagents were used.

The three native `gh stack` branches, bottom to top, are:

1. `generation-platform-api-declarations`: canonical reference keys and declaration identities, with an independently complete parser-factory consumer. Initial commit `cf9a882e`.
2. `generation-platform-api-traversal`: the complete explicit API scope, traversal, public selection, empty emission, and its E2E acceptance cases. Initial commit `71b08a31`.
3. `generation-platform-api-contracts`: public surface/CLI checks, ordinary-versus-capture engine comparisons, typing samples, documentation, and this record.

This adjusts PR-STACKS' default split: reference normalization has an independently testable boundary; traversal and its public selection stay together so the second PR can execute every acceptance path with 100% new-code coverage. The third PR adds contract comparisons, not an incomplete feature flag. The published native stack is #4106: [PR #4103](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4103) → [PR #4104](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4104) → [PR #4105](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4105). `gh stack submit --auto --remote origin` registered the already-created empty-body PRs without rewriting their bodies. CodeRabbit owns subsequent body updates.

## Implemented contracts

Only explicit `OpenAPIScope.Api` selects the lazy-loaded `parser.openapi_scope.ApiOpenAPIParser`. The existing default remains Schemas. Direct legacy parser selection with Api and direct API-parser selection without Api fail with the specified errors. The new parser is not re-exported at package root. Existing facade signatures and signature baselines are unchanged.

The API resolver owns strict URI-fragment decoding, raw JSON Pointer tokens, canonical registration arguments, and generated special-path preservation. Its E2E cases distinguish percent/tilde/slash spellings, reject malformed or ambiguous pointers, and delegate named IDs to the existing resolver. The legacy resolver remains unchanged. Canonical root references have the new scope's canonical root name; legacy names and goldens are unchanged.

The API walker processes component schemas, parameters, request bodies, responses, headers, paths, webhooks, standalone path items, and callbacks in the specified order. Actual declaration documents and original parameter indices survive references and overrides. Shared declarations generate once while retaining use edges; callback cycles retain edges without recursive expansion. Root filtering does not filter webhooks or standalone components. OpenAPI 3.2 `query` and `additionalOperations` participate in source order, including rejection of reserved fixed methods in `additionalOperations`.

Frames borrow actual raw/validated schema context, names, original/effective parameters, operation security, roles, and item-stream projection identity. Existing parsing leaves, reference loading, resource/ID handling, discriminator processing, model finalization, formatting, and disposal own model generation. The guarded legacy path walker and discriminator collector bodies are unchanged. API-only discriminator registration and subtype edges use the same canonical resolver keys.

The two existing version caches are established, in order, at the first root. Their respective OpenAPI and JSON Schema policies remain independent. Bare external libraries, explicit overrides, reversed multi-root lists, and directory inputs exercise that lifetime. No external borrow recomputes the caches.

Shared media parsing and applicability apply version/location before media, then encoding-header and wire-name exclusions. Ignored children are neither validated as schemas nor fetched. Schema/itemSchema precedence uses existing helpers; actual item declarations and synthetic array projections remain distinct. Schemas with `$ref` siblings use the ordinary raw-schema engine instead of discarding siblings through a Reference Object. Five-backend independent inventories preserve `x-python-import` and model type overrides.

Empty inventories are allowed only for actual OpenAPI input with explicit Api and an API-capable selected factory. The driver selects that factory once across retries. Existing output files are untouched; absent files remain absent; metadata is empty; requested info-version artifacts and normal remote-lock publication still work. Real HTTP tests cover external Path Items and updating/verifying an empty API's remote lock. API-only borrowed state is cleared during disposal.

## Type safety

Dedicated tools: mypy 2.3.1, Pyright 1.1.411, typing-extensions 4.16.0, Ruff 0.16.7. Product dependencies and lockfile are unchanged. The repository's locked ty 0.0.9 also passes with its existing error flags. The separate type environment uses locked dependencies; optional `ryaml` and Python-3.10-only `tomli` are absent on Python 3.13, matching the existing guarded-import suppressions.

Commands from the implementation checkout:

```sh
MYPYPATH=src /private/tmp/dcg-s02-tools/bin/mypy --strict --follow-imports=silent --python-executable /Users/koudai/work/dcg-generation-platform-s01/.venv/bin/python src/datamodel_code_generator/parser/_api_reference.py src/datamodel_code_generator/parser/openapi_scope.py src/datamodel_code_generator/parser/openapi_media.py tests/data/generation_platform/api_scope/typing/positive.py
/private/tmp/dcg-s02-tools/bin/pyright --project /private/tmp/dcg-s02-evidence/pyrightconfig.json
/private/tmp/dcg-s02-typing/bin/ty check src --python /private/tmp/dcg-s02-typing/bin/python --error unused-ignore-comment --error redundant-cast --error possibly-missing-attribute
```

Strict positive samples pass without diagnostics. Pyright uses Python 3.10, checks unnecessary suppressions, and disables general `type: ignore` handling. Negative samples produce exactly seven errors per checker, no extras. Lines 9–15 respectively expect mypy `dict-item`, `arg-type`, `arg-type`, `misc`, `arg-type`, `arg-type`, `call-arg`; Pyright expects `reportAssignmentType`, `reportArgumentType`, `reportArgumentType`, `reportAttributeAccessIssue`, `reportArgumentType`, `reportArgumentType`, `reportCallIssue`. The external verification script checks ordered lines, codes, and counts.

There are no new Any annotations or casts. Narrow Pyright exceptions cover inherited `super()` calls whose types are erased by the existing `snooper_to_methods()` decorator: constructor, raw/schema/file parsing, parameter identity, result postprocessing, and disposal. Mypy checks those calls. A separate constructor `arg-type` exception documents the legacy constructor annotation omitting dictionary sources that `Parser._iter_source_uncached` already supports. The new constructor exposes that real capability without changing the legacy signature. Runtime `get_type_hints(..., include_extras=True)` resolves the new public annotations, including an imported private alias. Required annotation imports occur only on the explicitly selected API path.

## Local verification

Runtime: CPython 3.13.2 on macOS arm64, existing S01 test environment, identical generated version file in implementation/baseline/control.

- First PR: 7 real parser tests, 100% reference-module line and branch coverage.
- Second PR: 113 E2E tests, 100% of the three new implementation modules and four new test modules: 917 statements, 228 branches, no missing or partial branches. Existing assertion helpers and external fixtures only; no new assert helper or assertion-policy exemption. Fault injection is limited to abnormal frame/resolver/attempt paths.
- Legacy OpenAPI plus public API baseline: 719 passed, 8 skipped.
- Full non-performance suite: 19,239 passed, 15 skipped, 72 deselected; 12 initial failures were generated documentation and the authorized additive `api` help/config choices. The affected generated-surface tests subsequently passed (12 cases), as did help/prompt checks (9 cases). No unrelated model golden was updated. The input-model configuration golden changes only the new enum literal.
- Architecture boundaries and GenerationStore usage checks pass. Ruff passes on changed implementation/tests.
- Third PR verification: 100 passed, with 100% line/branch coverage of the new contract test module. This includes five-backend CLI generation, public annotations, old signature baselines, assertion-helper policy, and ten ordinary/capture engine comparisons. The parity checks compare actual engine call counts, generated bytes, one factory selection, and zero retained parsers.

The first sandboxed full-suite attempt could not bind local HTTP sockets. Its errors were not treated as product evidence; the completed full-suite run above had localhost access. A latest, unlocked ty experiment found baseline/environment errors and was not used as the repository gate; the locked checker is the passing gate.

## Performance and memory

Raw evidence is outside the repository at `/private/tmp/dcg-s02-evidence`; `evidence-hashes.json` hashes the measurement scripts and raw reports. `measure.py` uses the repository's six startup cases plus small/large/reference/module/formatter/OpenAPI/repeated/cyclic/debug cases (17 total), fixed seed 4098, balanced AB/BA order, identical source/settings/formatter/version/interpreter, one warm-up per side, 10,000 bootstrap resamples, 99% median confidence intervals, and sign-test significance 0.01.

The full procedure ran 30 A/A and 60 A/B pairs per case. No wall/CPU case satisfied all three regression conditions (positive median beyond the A/A interval, confidence interval excluding zero, significant sign test). Cyclic generation had a statistically positive small timing difference, but within the A/A interval. This is recorded as noise-limited, not as proof of zero cost or a speed improvement.

Initial cyclic peak RSS differed by 147,456 bytes and triggered investigation. An independent second 30-pair A/A plus 60-pair A/B run measured a 32,768-byte median RSS difference, 99% interval [-24,576, 180,224], p=0.01834, within the second A/A interval [-212,992, 393,216]. The initial RSS signal did not reproduce. Repeated-generation tracemalloc checks across five processes per revision retained zero additional datamodel-code-generator objects; candidate peaks were 507,225–507,520 bytes versus baseline 507,140–507,966 bytes. No slowdown threshold was introduced.

New-scope costs are reported separately because Api intentionally visits more declarations. On three small declaration/callback/external fixtures, cold generation was 84.8–89.2 ms and 60-run warm medians were 1.05–1.80 ms. Twenty repeated generations retained zero additional package objects in each case; traced peaks were 518–820 KB. These single-host measurements are not a performance guarantee for every schema. Fresh ordinary OpenAPI generation imports none of the three new API modules. Ordinary paths allocate no API frames, declaration maps, or capture observers; their source-visible additions are scope checks and local callable selection.

## Next action

All three PRs are published, attached to the task, and registered in native stack #4106. Wait for required CI and CodeRabbit review, address findings, and record the final verification status. A final full-suite rerun is in progress at `/private/tmp/dcg-s02-evidence/full-final.log`. Do not merge without authorization and do not start S03.
