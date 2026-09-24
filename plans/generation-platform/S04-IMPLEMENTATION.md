# S04 implementation record

Status: S04-1 (shared wire rules) is implemented and verified locally, and is published as the bottom PR of the S04 native stack. S04-2 and S04-3 have not started. Nothing in this stack has been merged.

## Baseline and review boundaries

Repository: `datamodel-code-generator/datamodel-code-generator`. The authoritative plan is [PR #4098](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4098), revision `8e6f0d4a8a64b71e5a049ad8d108507b2fe164ca`, still separate and unmerged; contracts are read from its separate checkout, and their hashes below are unchanged since S03. Fixed main baseline: `e62b1e03d0952eac9c9b075ac5cd862cf6550b0d`. S01–S03 are merged, together with the ty 0.0.84 update (#4141) and the two ordinary-generator fixes observed during S03 (#4135, #4136).

The isolated implementation checkout is `/private/tmp/dcg-s04`; `/private/tmp/dcg-main-e62b` is a detached checkout of the baseline for comparisons. The original checkout and its untracked `1.tmp` are untouched. No subagents were used. Raw investigation output and measurement scripts stay in the session scratchpad, outside the working tree.

The three native `gh stack` branches, bottom to top, follow PR-STACKS:

1. `generation-platform-codecs-wire`: schema, parameter and media wire rules, [PR #4143](https://github.com/datamodel-code-generator/datamodel-code-generator/pull/4143).
2. `generation-platform-codecs-pydantic`: the two Pydantic v2 backends, presence, direction, native and envelope values.
3. `generation-platform-codecs-adapters`: custom adapters and typed generated surfaces.

## Contract hashes

- `ACCEPTANCE.md`: `ab8138656f8013a23da95089abe110216270386f405be793c527254fa782276e`
- `ARCHITECTURE.md`: `b5021f0649466928c4ca7e7520536380accc23ba09a00d26a650b5226f8022ad`
- `BACKGROUND.md`: `77cadae0ab8b153e5e8c89e4be7d1d013ab181cac81b3623b5e469aa60470414`
- `CLIENT-REQUIREMENTS.md`: `b8891572c0b224c20286f4d30507aa2c7eb6503e26ec295fc17c78d9972fda08`
- `COVERAGE.md`: `af5941254e33aeb6713aaa74832f95d0f76881cf57c4d6658fb34bbc2be60b71`
- `DECISIONS-BINDING.md`: `ede700ae13a284fdca2928b075f6b0d0432b0f845284238ee9732e5d58cf867d`
- `DECISIONS-CLIENT.md`: `7a73bdb829f33bf6e9a888c3d2f206c2099789f44e5975d5a352feffd31858bd`
- `DECISIONS-ENTRY.md`: `3a49c001d03c2ac4ef9abaa145f665f88b46e6cdfd8a49780c9f9c5598b5a8c0`
- `DECISIONS-FASTAPI.md`: `ceec759e4d6c553858c57d35660a938e94708b94b2aaaa7afd1aa2dff5a2b688`
- `DECISIONS-MODEL-CODECS.md`: `fc4d804065f11c31e27a3174a2436db6b8cdbb7ae9454641110b8e3a144e1678`
- `DECISIONS-PROTOCOLS.md`: `a751d01cbbce08a40cfabaa5fb1f42a29d6b49dc79f8b63b6d30e7034fb330f1`
- `DECISIONS-RUNTIME.md`: `4d3841b1f4359cf384e924c58ff2155981f03e312f86ec94f90c3aed1bad7c66`
- `DECISIONS-TYPING.md`: `e96bcce1ce1d82f21fac898c3e89ecd164961d1397cc2d85cd52c940a7c8c965`
- `FASTAPI-REQUIREMENTS.md`: `e403e5497ae680eba5faa78d8e79935943f8493313518459815bd230ccecb85f`
- `IMPLEMENTATION-PROMPT.md`: `3990a0c8a9d86132b42c77040f08c2d89dc2de7da81ca847514f9190dffa7f61`
- `IMPLEMENTER.md`: `be5062538bc961655cf3bcf0f62b06034b872674b7d78503764d2841535f8a94`
- `PLAN.md`: `e7661f6f6e65eba283bc6cba4f7be7264321ebe0e4c402b6dbe91dd0b779a48e`
- `PR-STACKS.md`: `8a9fca2aa7b4dda07d856b8657be6c957eca1b6da1abd44fe0db16d9fb6dbfa0`
- `RATIONALE.md`: `9f81875b6c5fa3a0946157b05f12ec79b45c39336f4f9f02e7ab15a3349c5cd2`
- `README.md`: `4b0650cb9297ce568990f4f502b22c53229010343af25b819e2f874bba7f9b08`
- `REFERENCES.md`: `840615466e0107c7e50168dea9091359d6cf3f45b30c616a0bbca6c0f399dcf2`

## S04-1: shared wire rules

The runtime package `datamodel_code_generator._runtime.model_codecs` is the embeddable common codec that generated packages will vendor as `<package>._runtime.model_codecs`. It uses relative imports only and never imports the generator; `runtime-imports.txt` pins every module's import list. jsonschema, referencing and RE2 are imported lazily, when a schema bundle is built or a pattern is compiled. Generation-time planning is separate, in `datamodel_code_generator._openapi_wire_plan`. No existing module changes, and ordinary generation imports neither package, RE2, jsonschema nor referencing.

- Wire values (`wire.py`): recursive `JSONScalar`/`JSONValue`/`WireValue` aliases. `freeze_wire` copies into ordered `MappingProxyType`/tuple snapshots, `thaw_wire` into independent lists and dicts, and `presence_of` records every present RFC 6901 pointer in document order. Only exact JSON scalar types pass (subclasses such as `IntEnum` or `str` subclasses are rejected), numbers must be finite, keys must be strings, strings must not contain lone surrogates, and cycles are rejected.
- Media (`media.py`): JSON must be UTF-8; duplicate member names, NaN/Infinity, syntax errors and lone surrogates become value-free `WireValidationError` issues. Non-integers decode to `Decimal`, so number lexemes survive a round trip exactly; integer digit and nesting limits raise `CodecResourceLimitError`. `encode_json` writes compact UTF-8 (or ASCII) JSON in one checked pass. Media types are normalized and classified (JSON including `+json`, form, multipart, text, binary); flat URL-encoded forms follow WHATWG encoding and reject duplicate scalars and undeclared members. Lexical kinds (string, integer, number, boolean) are strict.
- Patterns (`patterns.py`): the ECMA-262 Unicode-mode subset in MODEL-CODECS §9 is parsed in pure Python and translated to RE2: ASCII `\d\w\b`, the explicit ECMA `\s` set, a dot excluding LF/CR/U+2028/U+2029, anchors only at the ends of top-level branches, `[\b]` as U+0008, `[\B]` rejected, surrogate pairs combined and lone surrogates rejected. Lookaround, backreferences, named groups, property escapes and other engine-specific syntax raise `PatternDialectError` (an adapter is required). Limits: 4 KiB source, depth 32, repeat bound 1000 (static); program size 4096, `max_mem` 1 MiB, `never_capture`, `log_errors=False`, a 128-entry compiled cache, 1 MiB per subject and 32 MiB per codec call (runtime). There is no Python `re` fallback.
- Schema validation (`schema.py`): `SchemaBundle` copies normalized 2020-12 resources into validator-owned containers, indexes every schema object, checks every `$ref` against an offline `referencing` registry, and plans every pattern before any value is validated. A `$ref` that cannot be resolved offline, or `$dynamicRef` (which needs an explicit schema adapter), raises `CodecConfigurationError` at construction. `Draft202012Validator` is extended so that `pattern`, `patternProperties`, `additionalProperties` and `unevaluatedProperties` share the RE2 matcher, `number`/`integer`/`multipleOf` use exact decimal arithmetic (bool is not a number), and `required`/`dependentRequired` report one issue per missing member. The 18 standard formats come from the installed jsonschema checker set; `regex` uses the builtin grammar, and `byte`, `int32`, `int64`, `date-time-local` and `time-local` are added. A missing standard checker stops construction. Issues carry a `schema.<keyword>` code, a value-free message, and instance and schema pointers; nested `false` schemas report `schema.false` at their own location.
- Parameters (`parameters.py`): `ParameterPlan` accepts only reversible builtin combinations. Encoding and decoding cover path `simple`/`label`/`matrix`, query `form`/`spaceDelimited`/`pipeDelimited`/`deepObject`, header `simple`, cookie `form` and OAS 3.2 `style: cookie`, JSON/text content parameters, and OAS 3.2 `querystring` (form, JSON and text). `allowReserved`, empty values, null, empty containers and duplicates follow explicit rules. Omission is the `UNSET` singleton; raw values are never replaced by defaults. `decode_parameters` reports every missing or malformed parameter together.
- Planning (`_openapi_wire_plan.py`): `plan_wire` derives a `WirePlan` from an accepted binding batch and its source lease. Each source document becomes an offline logical resource (`https://dcg.invalid/inputs/root` and `.../documents/<i>`), pruned to the referenced schema roots while keeping the source's list and dict structure. `$ref` values become absolute canonical references; OAS 3.0 `nullable`, boolean exclusive bounds and `$ref` siblings are normalized. Unsupported dialects or vocabularies, patterns outside the grammar or its static limits, parameter forms without a builtin codec, and unresolved references become `MC_SCHEMA_DIALECT`, `MC_PATTERN_DIALECT`, `MC_PATTERN_RESOURCE_LIMIT`, `MC_PARAMETER_ENCODING` and `BND_UNRESOLVED_REFERENCE` diagnostics. Parameter plans derive lexical kinds from `type`/`enum`/`const` and combinators, reserve apiKey credential names from effective security, and report expanded-name collisions (case-insensitive for query and header). The planner never imports RE2. It has no production caller yet: S06 consumes it.

### Dependencies

Product dependencies are unchanged. The test group adds `google-re2>=1.1.20251105,<2` and `jsonschema[format-nongpl]>=4.26,<5`, which are the latest releases. MODEL-CODECS also names `referencing>=0.37,<1` for generated runtimes, but the lock keeps `referencing` 0.36.2, because `jsonschema-path` 0.3.4, required by the existing `openapi-spec-validator`, pins `referencing<0.37`. The runtime uses only registry APIs present in both versions. The codec tests and the JSON Schema suite comparison also pass in a scratch environment with `referencing` 0.37.0. Generated-package dependency closures are declared when targets are published (S05/S07), not by the generator.

### Typing

Tools: mypy 2.3.1, Pyright 1.1.414 and the plan's Pyright 1.1.411 baseline, ty 0.0.84 (latest; the repository gate), Ruff 0.16.8 (pre-commit pin), typing-extensions 4.16.0, types-jsonschema 4.26.0.20260518 in the checking environment only. Strict mypy and both Pyright versions (Python 3.10, `reportUnnecessaryTypeIgnoreComment`, type-ignore comments disabled) report zero diagnostics for every new module. ty with the repository flags reports only the pre-existing `util.py:24` unused `tomli` suppression, which appears because the local environment installs `tomli`.

TY03 samples in `tests/data/generation_platform/codecs/typing/`: positive cases (`positive.py`, `annotations.py`, `first_use.py`) have zero diagnostics on both checkers. Lines 8–15 of `negative.py` produce: mypy `assignment`, `dict-item`, `list-item`, `assignment`, `dict-item`, `call-overload`, `call-overload`, `arg-type`; Pyright `reportAssignmentType` for lines 8–12, `reportCallIssue` plus `reportArgumentType` for each of lines 13–14, and `reportArgumentType` for line 15. They cover `object()` and non-string keys for `JSONValue`, a list for `WireValue`, and invalid arguments to `freeze_wire`, `encode_json` and `ParameterPlan`. A scratchpad script checks the ordered lines, codes and counts with both Pyright versions. The E2E alias test resolves `get_type_hints(..., include_extras=True)` through private import aliases in another module, on Python 3.10, 3.13 and 3.14.

Counterexamples to TYPING §2, reproduced in isolated probes:

- mypy 2.3.1 rejects its `Union[...]` spelling with quoted inner names: `Cannot resolve name "JSONValue" (possible cyclic definition) [misc]` for each alias.
- Pyright 1.1.411 and 1.1.414 accept every `TypeAliasType` spelling (the §2 one, a whole-string value, and a stringified `Union[...]`) inside the defining module, but when another module's first reference is a function such as `presence_of`, the alias's recursive members become Unknown (`Type of "presence_of" is partially unknown`). A classic recursive `TypeAlias` does not have this problem.
- With a `JSONValue | WireValue` parameter, mypy infers a nested dict literal without the union context and reports `arg-type`, so the MODEL-CODECS example `presence_of({'name': 'A', 'tag': None})` fails.

The runtime aliases therefore stay `TypeAliasType` objects with one string value each, which `get_type_hints` and Pydantic 2.12.5's `TypeAdapter` resolve (the alias's `__module__` is the wire module), while type checkers see structurally identical classic `TypeAlias` definitions under `TYPE_CHECKING`. `freeze_wire`, `presence_of` and `encode_json` have one overload for each alias, keeping the documented `JSONValue | WireValue` implementation signature. `first_use.py` pins both the Pyright ordering case and the nested-literal case.

Exception ledger (TYPING §8):

| Symbol | Location | Origin | Scope and validation | Tests |
| --- | --- | --- | --- | --- |
| `re2` module | `patterns.py` `compile_pattern` | google-re2 ships no type information (untyped upstream) | `import_module("re2")` keeps the boundary to `Options`, `compile` and `error`; the compiled object is held as the `CompiledPattern` Protocol, and its program size is checked before use | `patterns.txt` |
| `jsonschema.validators.extend` | `schema.py` `_validator_factory` | the jsonschema stubs leave `extend` unannotated, so strict Pyright reports its result as Unknown (upstream stub defect) | reached through `import_module`; every created validator is returned as the `_KeywordValidator` Protocol, whose four methods are the only ones called | `schema-validation.txt` |
| `_resolver` argument | `schema.py` `create`, `_referenced_keys` | private jsonschema field, the only way to set a validator's base URI without `$id`; jsonschema's own `descend` uses it | constructing a validator on a reference target, with that target's registry resolver; pinned by `jsonschema<5` | cross-resource and root-`$id` cases in `schema-validation.txt` |

There are no `Any` annotations, casts, or type-ignore comments. The only suppressions are `# noqa: PLC0415` on the lazy jsonschema/referencing imports.

## Verification

Runtime: CPython 3.13.2 on macOS arm64, locked environment (`--extra all --group test --group type`).

- 16 E2E tests (plus the environment-gated suite comparison) give 100% line and branch coverage of the 13 new implementation and test modules: 1,933 statements, 740 branches. Inputs are external fixtures under `tests/data/generation_platform/codecs/`, expected outputs are handwritten or reviewed `.txt` files compared with the existing `assert_output`. No assertion helper was added. The only mock removes one format checker to exercise the missing-dependency failure.
- Plan fixtures: OAS 3.1 with an external document, security names and instances validated through the planned bundle; OAS 3.0 normalization with instances; OAS 3.2 `querystring`/`style: cookie`; dialect and vocabulary diagnostics for 3.1 and 3.2; and the legacy scope.
- JSON Schema Test Suite `fe8c2f0` (2020-12, with 35 remotes): 1,927 pass. The 86 configuration results are all deliberate: `$dynamicRef` requires an adapter; references to unbundled remotes, metaschemas or non-schema keyword values do not resolve offline; Unicode property and `\c` escapes are outside the builtin grammar. The 84 mismatches are also accounted for: formats are asserted, not annotation-only (19); draft-07 `dependencies` is not a 2020-12 keyword (14); a custom metaschema dialect is rejected by the planner (`MC_SCHEMA_DIALECT`), so the runtime always applies the full 2020-12 vocabulary (1); and 50 optional format cases follow the selected jsonschema 4.26 checkers (leap seconds, year 0000, duration grammar, e-mail syntax, IDNA hostnames).
- Full suite (8 workers): 21,559 passed, 16 skipped, no failures. An earlier run in an environment synced with `--all-extras`, which adds `ryaml`, `httpx2` and `truststore`, had 13 failures; the sampled failures fail identically on main in that environment, and all 13 pass on both revisions after syncing with `--extra all`.
- Ruff 0.16.8 check and format with the pre-commit arguments, codespell 2.4.3 and pyproject-fmt 2.29.4 pass on the changed files.
- PR #4143 CI: every required check passes. CodeQL's first run reported 13 `py/uninitialized-local-variable` errors on exhaustive `match` statements, because it does not treat `case _` as exhaustive; those cases now return directly. The remaining CodeQL notes (mixed returns after exhaustive matches, Protocol `...` bodies, the exported `UNSET`) are the same kinds main already carries. CodSpeed walltime reports a uniform slowdown of about 33% across all 39 compared benchmarks, together with its own warnings about unknown hosted-runner and differing runtime environments; no existing module changed, and the local comparison below shows no difference.
- CodeRabbit review of PR #4143: all three findings are fixed and covered by fixtures. First, alternative security requirements that repeat an `apiKey` scheme, or name the same header with different case, no longer report a false header collision; with no header declaration, that false collision had also crashed planning with `StopIteration`. The planner now deduplicates the auth names, case-insensitively for headers. Second, `ParameterPlan` now rejects content media without a builtin codec (cookie content, form content outside `querystring`, and media other than JSON, text or form) through the same `builtin_content` rule the planner uses. Third, the suite helper re-raises a bundle error that names no remote instead of retrying forever.

## Performance and memory

Ordinary generation is unchanged: in 40 randomized, balanced runs per side, OpenAPI `api.yaml` generation had a median of 125.9 ms on main and 125.8 ms on this branch. The new modules are not imported by ordinary generation. Codec hot paths on the same host (best of five): decoding a 7.9 KB, 100-object JSON body 253 µs; encoding 282 µs; freezing 203 µs; validating it against a 2020-12 schema with a pattern, `multipleOf` and `additionalProperties: false` 1.84 ms; encoding or decoding an exploded form array parameter 3.1 µs and 2.0 µs. Compiled patterns are cached (at most 128), validators are cached per bundle, and per-call state lives in a context variable, not on shared validators. These single-host measurements are not a throughput guarantee.

## Deferred and known limitations

- `$id` and `$anchor` embedded inside OpenAPI documents are reported as `MC_SCHEMA_DIALECT`; standalone JSON Schema roots keep their `$id` as an alias.
- `$dynamicRef` requires an explicit schema adapter (S04-3 owns adapter registration).
- The Python pattern dialect for msgspec `Meta` belongs to S08.
- Directional request/response schema views, presence decisions from models and native versus envelope values are S04-2. `SchemaView`, `OfflineSchemaRegistry` and adapter capability records are S04-3.
- Parameter defaults are applied by the server integration (S06). Multipart and binary media layers are not part of S04-1. `allowEmptyValue` is informational only.

## Next action

Publish S04-1 as the bottom PR of the native stack (empty body, CodeRabbit fills it) and bring its CI to green, answering review comments. Then implement S04-2 on `generation-platform-codecs-pydantic`, stacked above it. Do not merge; the maintainer merges. Stop after S04.
