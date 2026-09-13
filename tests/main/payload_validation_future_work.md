# Schema-Derived Payload Validation Future Work

This file tracks schemas that the payload-validation e2e test intentionally does not
run yet, plus compatibility-sensitive generator gaps identified by that coverage.
These cases are not simple fixture omissions. They need either better payload
generation, an explicit backend compatibility policy, or a careful generator
change that avoids surprising existing users.

## Nightly Runtime Fixture Compatibility

The nightly matrix also discovers regression fixtures that exercise existing output
limitations. `tests/data/payloads/runtime_compatibility/` contains deterministic
source-valid witnesses for the cases classified after issue #4061:

- msgspec cannot convert unions with multiple string-like dictionary key types or
  heterogeneous Enum values. Its assignment-based key aliases can also retain
  unresolved forward references; that exclusion does not apply to PEP 695 aliases.
- Some generated msgspec property-name aliases attach string constraints to
  non-string types, and null-only aliases retain unrelated constraint metadata.
  Numeric schemas with date-time formats can generate string aliases with numeric
  bounds. These generator gaps need focused changes with output compatibility tests.
- Bare Pydantic dataclass aliases cannot carry the model-level regex-engine config
  needed by lookaround patterns. Pydantic before 2.5 also lacks the required regex
  behavior and can warn while serializing enum dictionary keys.

These cases retain explicit backend or version-specific reasons. E2E tests generate
the modules and verify the runtime failures, so an exclusion that becomes obsolete
fails a regression test. Supported null aliases remain in the matrix, and the
array/object union fixtures use `--field-constraints` for both Pydantic backends.

## Payload Generator Limitations

The following cases have valid payloads in principle, but `hypothesis-jsonschema`
does not currently generate them reliably from the raw schema shape:

- `jsonschema/all_of_any_of_base_class_ref.json`
- `openapi/allof.yaml::components.schemas.AllOfNested3`
- `openapi/allof_with_required_inherited_complex_allof.yaml::components.schemas.ProjectedItem`
- `openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.Entity`
- `openapi/allof_with_required_inherited_comprehensive.yaml::components.schemas.ProjectedEntity`
- `openapi/allof_with_required_inherited_coverage.yaml::components.schemas.EdgeCasesCoverage`
- `openapi/allof_with_required_inherited_edge_cases.yaml::components.schemas.EdgeCases`
- `openapi/allof_with_required_inherited_edge_cases.yaml::components.schemas.ProjectedEdgeCases`

These schemas combine `allOf`, inherited required fields, nested references, and
union keywords in ways that often cause filtered strategies to become
unsatisfiable or flaky. The generator may still have bugs in this area, but the
current blocker is that the e2e test cannot reliably obtain schema-valid inputs.

Future work:

- Add schema normalization for inherited `allOf` shapes before calling
  `hypothesis-jsonschema`, while still validating generated payloads against the
  original schema.
- Keep any normalization local to payload generation. It must not weaken the
  source-schema validation step.
- Re-enable excluded cases one by one when deterministic valid payload generation
  is available.

## Compatibility-Sensitive Generator Gaps

The following cases used to produce schema-valid payloads that the generated
Pydantic v2 model did not accept, but fixing them changes existing default
output shapes:

- `openapi/allof_with_required_inherited_coverage.yaml::components.schemas.EnumInAllOf`
- `openapi/allof_with_required_inherited_coverage.yaml::components.schemas.MultipleOfBase`

For these OpenAPI `allOf` primitive cases, payloads such as `"a"` or `0` are valid
for the source schema. The previous class-oriented output emitted an intermediate
model for the property-level `allOf`, so Pydantic expected a dictionary/object
instead of the primitive value.

Status:

- Fixed by a focused generator change that keeps the intermediate model names
  but emits RootModel wrappers around the schema-faithful primitive/constrained
  payload type.
- Updated expected outputs document the remaining compatibility impact.
- Payload-validation e2e coverage is enabled for both cases, and the
  corresponding `_EXCLUDED_CASES` entries were removed.

This intentionally avoids deleting the intermediate models in the default
output while fixing the payload shape, and it does not introduce a new runtime
validation mechanism.

## JSON-Schema-Test-Suite Conformance Limits

The JSON-Schema-Test-Suite conformance gate currently targets the required
`draft7` and `draft2020-12` suite directories pinned at
`fe8c2f0de2041943975932b6bf4bd882625b6cfb`. It checks the generated Pydantic v2
model against both valid and invalid suite instances for schema groups that are
within the current generated-model compatibility policy.

Current scope:

- 640 suite schema groups discovered across the two target drafts.
- 59 schema groups and 301 suite instances are checked end to end.
- 581 schema groups are excluded by policy with machine-readable reasons in
  `tests/main/payload_validation/json_schema_suite.py`.

The largest exclusion categories are compatibility-sensitive or unsupported in
the current default generated model shape:

- Boolean schemas and boolean subschemas are not accepted by the generator input
  path.
- Remote `$ref` and `$dynamicRef`/`$dynamicAnchor` need resolver and dynamic-scope
  policies before they can be made deterministic.
- JSON Schema object or array keywords without an explicit matching `type` allow
  other instance types that generated `BaseModel` or collection roots reject.
- Pydantic v2 default validation is non-strict, so primitive type tests such as
  string-to-int coercion do not match JSON Schema.
- Runtime enforcement for keywords such as `contains`, `dependentRequired`,
  `dependentSchemas`, `not`, `unevaluatedItems`, `unevaluatedProperties`,
  `patternProperties`, `uniqueItems`, numeric/string bounds, `multipleOf`, and
  JSON-equality-sensitive `const`/`enum` values is incomplete in the default
  generated model.
- Combined applicators (`allOf`, `anyOf`, `oneOf`) need a dedicated compatibility
  policy before default-output changes are safe.

Future work:

- Move policy exclusions into explicit hand-classified case dictionaries as each
  class is narrowed to concrete generator behavior.
- Add deterministic local remote-ref mirroring for suite `remotes/` and enable
  non-dynamic remote `$ref` groups.
- Decide whether strict type generation or field constraint generation should be
  enabled by default, exposed as a schema-faithful mode, or only tested under
  non-default generator options.
- Convert each remaining unsupported keyword category into either a generator
  fix, a backend-specific conformance policy entry, or a documented permanent
  subset limitation.

## Backend Payload Matrix Limits

Payload validation now keeps full coverage on the default `pydantic_v2.BaseModel`
backend and adds a representative backend matrix for `pydantic_v2.dataclass`,
`msgspec.Struct`, and `dataclasses.dataclass`.

Current scope:

- `pydantic_v2.BaseModel` runs the full schema-derived accept/reject oracle.
- `pydantic_v2.dataclass` and `msgspec.Struct` run representative accept/reject
  cases using backend-specific conformance policies.
- `dataclasses.dataclass` runs representative construction-only cases because
  standard dataclasses do not provide runtime type validation.
- `pydantic.BaseModel` / pydantic v1 output is not part of the current matrix
  because pydantic-v1 output was removed from the generator in #3031.
- The scheduled runtime matrix widens runtime-validating non-default backends
  with `DCG_PAYLOAD_BACKEND_CASES=all`. That mode runs the JSON Schema fixture
  corpus minus classified backend exclusions, plus representative OpenAPI cases;
  the remaining JSON Schema and OpenAPI backend-specific import/runtime limits
  are machine-classified in
  `tests/main/payload_validation/conformance.py`. Plain dataclasses stay
  representative because they do not provide runtime validation and native
  construction does not implement JSON alias or extra-key handling.

Future work:

- Reduce the full nightly backend exclusions by fixing backend-specific
  generator issues where the generated code is invalid for that backend.
- Add a dedicated strict/schema-faithful mode if a future backend needs stronger
  JSON Schema runtime validation than the default generated output provides.

## Runtime Minimum Matrix Limits

The `py311-payload-runtime-min` tox environment pins runtime validation
dependencies to the declared lower bounds where possible. Pydantic v2 is checked
with `pydantic==2.0.3` without raising the package lower bound.

Current Pydantic 2.0 compatibility fixes:

- Pydantic v2 dataclass fields with non-identifier aliases move the JSON alias out
  of `alias` only before Pydantic 2.4, so generated dataclass signatures stay
  importable under Pydantic 2.0 without changing newer-runtime output.
- Pydantic v2 `Field(deprecated=True)` is emitted through `json_schema_extra`
  only before Pydantic 2.7, where `deprecated` was still treated as extra field
  metadata.
- Pydantic v2 RootModels whose dictionary keys reference generated enum classes
  include those key references in dependency sorting only before Pydantic 2.8,
  where dict-key forward references could not be resolved when adapting the
  model.

Current version-specific exclusions:

- Pydantic before 2.5.0 does not support generated `regex_engine="python-re"` for
  lookaround pattern validators, so the Pydantic v2 BaseModel and dataclass
  payload runtime matrices skip only the classified lookaround pattern cases
  under older runtimes.
- Pydantic before 2.5.0 can reject schema-valid Decimal `multipleOf` values near
  float-originated boundaries, so only the classified Decimal `multipleOf` cases
  are skipped under older runtimes.
- Pydantic before 2.5.0 can emit JSON-mode serializer warnings for enum
  dictionary keys during dump, so only the affected round-trip cases are skipped
  under older runtimes; acceptance coverage remains enabled for those schemas.
- Pydantic 2.0.x can emit JSON-mode serializer warnings for object unions
  distinguished by boolean `Literal` fields during dump, so only the affected
  round-trip case is skipped under that older runtime; acceptance coverage
  remains enabled for that schema.
- Pydantic before 2.10.0 cannot fill generated nested `TypeAliasType` aliases
  while constructing dataclass schemas, so only the affected dataclass payload
  runtime case is skipped under older runtimes.

Future work:

- Revisit the lookaround skips if support for older Pydantic runtimes can be
  improved without changing the declared dependency lower bound or newer runtime
  behavior.
- Revisit the Decimal `multipleOf` and enum-key dump skips if older Pydantic
  runtimes can be supported without changing newer runtime behavior.
- Revisit the nested `TypeAliasType` dataclass skip if support for Pydantic
  versions before 2.10.0 is required.

## Round-Trip Dump Limits

The pydantic v2 payload test now validates accepted payloads, dumps them with
`mode="json"`, `by_alias=True`, and `exclude_unset=True`, then checks that the
dumped value is still valid for the source schema. `exclude_unset=True` keeps
unset optional fields from being materialized as `null` when the source schema
does not accept `null`.

Current exclusions are machine-readable in
`tests/main/payload_validation/constants.py`:

- Schemas that list required names absent from `properties` cannot round-trip
  those names because the generated model has no field to dump.
- One `oneOf` case normalizes into a shape that matches multiple branches after
  dumping.
- One decimal case serializes a JSON numeric `Decimal` value as a string, while
  the source schema still requires `type: number`.

Future work:

- Decide whether required names absent from `properties` should be represented
  by generated fields or remain a documented compatibility boundary.
- Decide whether decimal JSON mode should preserve JSON numbers for
  schema-faithful dumps or keep Pydantic's string serialization.
- Strengthen round-trip from schema validity to payload equivalence modulo
  defaults once these compatibility categories are resolved.

## OpenAPI Discriminator Compatibility Policy

The following cases expose a mismatch between OpenAPI discriminator semantics,
JSON Schema validation semantics, and backend-specific runtime validators:

- `openapi/discriminator.yaml::components.schemas.Demo`
- `openapi/discriminator_float_mapping.yaml::components.schemas.Base`
- `openapi/discriminator_in_array_oneof.yaml::components.schemas.Demo`
- `openapi/discriminator_without_mapping.yaml::components.schemas.Demo`

These are not safe to change as incidental fixes in the payload-validation test
work. Existing users may rely on the current generated type shape, especially for
Pydantic discriminated unions.

Important distinctions:

- OpenAPI discriminator mapping keys are strings, but the discriminator property
  schema can be numeric or otherwise constrained. For example, a mapping key
  `"1.5"` can point to a schema whose discriminator field is `type: number`.
- Without an explicit mapping, OpenAPI uses implicit schema-name-based mapping.
  That can conflict with a JSON Schema `enum` on the discriminator property.
- For `allOf` inheritance and array item unions, mapping tags can be applied at a
  different semantic level than the inherited JSON Schema constraints.
- Pydantic v2 discriminated unions require literal discriminator values. Other
  output backends may need a different representation or may not support the
  same semantics.

Future work:

- Decide whether existing default output should continue to prioritize OpenAPI
  discriminator semantics, or whether a schema-faithful mode should fall back to
  plain unions when discriminator tags conflict with field constraints.
- Consider using an existing strict/schema-version mode or adding a dedicated
  compatibility option before changing default behavior.
- If a generated Pydantic model is runtime-invalid, fix it directly. If the model
  is runtime-valid but chooses OpenAPI discriminator semantics over JSON Schema
  validation semantics, treat behavior changes as compatibility-sensitive.
- Add backend-specific tests when enabling any excluded discriminator case. A
  change that is correct for Pydantic may not be correct for dataclasses,
  TypedDict, msgspec, or other output types.

## Top-Level Nullable Object Components

The following case is excluded:

- `openapi/ref_nullable.yaml::components.schemas.NullableChild`

The component schema is a nullable object, so `null` is schema-valid for the
component itself. The current class-based output represents the object component
as a model class:

```python
class NullableChild(BaseModel):
    name: str | None = None
```

That class does not validate `None` when used directly. Nullable references to
the component can still be represented in parent fields, and the payload e2e test
covers that through `openapi/ref_nullable.yaml::components.schemas.Parent`.

Future work:

- Decide whether top-level nullable object components should produce an
  additional wrapper/root type for direct validation of `null`.
- Treat a default output change as compatibility-sensitive, because it can change
  class names, exported types, annotations, and user imports.
- Consider limiting any new behavior to a schema-faithful or strict nullable mode
  if preserving existing class-based output is more important for default usage.

## Exact JSON Numeric Oracle and Native Float Contract

The payload oracle interprets the canonical JSON serialization of the original
schema and payload as exact decimal numbers. Fractions stay inside this oracle;
actual generated models receive the original Python values. This avoids binary
`multipleOf` false positives and false negatives in jsonschema without changing
source constraints, annotations, instance values, or the example budget.

Only `MultipleOfBase.decimal_multiple_field` has a native float contract. Its
original source fragment must exactly match the external contract fixture. The
intersection of multiples of 0.2 and 0.3 is independently specified as multiples
of 0.6. A minimal native Pydantic float validator supplies expected runtime
errors, and the generated model must match the complete error records, including
location and constraint context. Source-valid values accepted by the native
validator must be accepted and retain source validity after JSON-mode dumping.
Deterministic external witnesses detect weaker, stronger, missing, wrong-type,
wrong-path, and wrong-context generated constraints. No native or generated
acceptance result filters sampled payloads. The registered case unions fresh
copies of external source-valid witnesses with its existing strategy, so the
ordinary acceptance and round-trip tests also exercise native rejections.

Root numeric `const` and wholly numeric `enum` strategies sample their original
immutable declared values before exact source filtering. In particular, `1e100`
must remain a float: converting it to either its binary integer expansion or the
canonical decimal integer changes native enum lookup behavior. Other shapes keep
the existing hypothesis-jsonschema strategy. This does not establish general
sampling completeness: bounded singleton decimal-multiple intervals such as
10066330.8 can still be unsatisfiable in hypothesis-jsonschema despite exact
source validity. Deterministic conformance witnesses cover these values directly;
no additional fixture exclusion or generalized structural normalization is added.
